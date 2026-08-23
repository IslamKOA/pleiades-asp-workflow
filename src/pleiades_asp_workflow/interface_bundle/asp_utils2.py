
"""
asp_utils2.py
Development interface for the first stages of the Pléiades ASP workflow.

Version 0.5
- renamed sections to user-friendly titles
- compact summaries + detailed logs saved to disk
- no external subprocess for metadata/geometry step
- added optional overview figure display for stereo / tri-stereo concept
- keeps the scientific logic of the original notebooks
"""
from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import sys
import traceback
import warnings
from dataclasses import dataclass, asdict
from datetime import datetime
from itertools import combinations
from math import radians, degrees, sin, cos, acos, tan
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import NotGeoreferencedWarning
from rasterio.windows import Window

DEV_VERSION = "0.9.0"


# ============================================================
# SETTINGS
# ============================================================

@dataclass
class ProjectSettings:
    project_name: str
    output_base: str
    platform: str
    acquisition_mode: str

    acquisition_A: str
    acquisition_B: str
    acquisition_C: str = ""

    merge_tiles: bool = False
    tile_ids: Tuple[str, ...] = ("R1C1",)

    crop_enabled: bool = False
    aoi_vector: str = ""
    rpc_height: float = 2500.0
    buffer_px: int = 500
    spacing_deg: float = 0.00005

    overwrite: bool = False

    @property
    def project_dir(self) -> Path:
        return Path(self.output_base).expanduser() / self.project_name

    @property
    def merged_dir(self) -> Path:
        return self.project_dir / "merged_tiles"

    @property
    def cropped_dir(self) -> Path:
        return self.merged_dir / "cropped_images"

    @property
    def figure_dir(self) -> Path:
        return self.project_dir / "Figure"

    @property
    def log_dir(self) -> Path:
        return self.project_dir / "logs"

    @property
    def metadata_dir(self) -> Path:
        return self.project_dir / "metadata"

    @property
    def asp_logs_dir(self) -> Path:
        return self.project_dir / "asp_logs"

    @property
    def asp_out_dir(self) -> Path:
        return self.project_dir / "asp_out"

    @property
    def image_names(self) -> List[str]:
        return ["A", "B", "C"] if self.acquisition_mode == "tri_stereo" else ["A", "B"]

    @property
    def acquisition_folders(self) -> Dict[str, Path]:
        result = {
            "A": Path(self.acquisition_A).expanduser(),
            "B": Path(self.acquisition_B).expanduser(),
        }
        if self.acquisition_mode == "tri_stereo":
            result["C"] = Path(self.acquisition_C).expanduser()
        return result


class WorkflowLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("w", encoding="utf-8")
        self.write(f"Created: {datetime.now().isoformat(timespec='seconds')}")
        self.write("=" * 80)

    def write(self, msg=""):
        self._stream.write(str(msg) + "\n")
        self._stream.flush()

    def close(self):
        if not self._stream.closed:
            self._stream.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


# ============================================================
# FOLDERS / CONFIG
# ============================================================

def create_project_folders(settings: ProjectSettings) -> Dict[str, Path]:
    folders = {
        "project": settings.project_dir,
        "merged_tiles": settings.merged_dir,
        "cropped_images": settings.cropped_dir,
        "figures": settings.figure_dir,
        "logs": settings.log_dir,
        "metadata": settings.metadata_dir,
        "asp_logs": settings.asp_logs_dir,
        "asp_out": settings.asp_out_dir,
    }
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    return folders


def save_project_config(settings: ProjectSettings) -> Path:
    config_path = settings.project_dir / "project_settings.json"
    payload = asdict(settings)
    payload["tile_ids"] = list(settings.tile_ids)
    payload["derived_paths"] = {
        "project_dir": str(settings.project_dir),
        "merged_tiles": str(settings.merged_dir),
        "cropped_images": str(settings.cropped_dir),
        "figure_dir": str(settings.figure_dir),
        "log_dir": str(settings.log_dir),
        "metadata_dir": str(settings.metadata_dir),
        "asp_logs": str(settings.asp_logs_dir),
        "asp_out": str(settings.asp_out_dir),
    }
    with config_path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2)
    return config_path


# ============================================================
# PREPARE DATA
# ============================================================

IMAGE_EXTENSIONS = ("TIF", "tif", "TIFF", "tiff", "JP2", "jp2")


def _first(paths):
    paths = sorted(paths)
    return paths[0] if paths else None


def _find_rpc_file(folder: Path, platform: str) -> Path:
    rpc = _first(list(folder.glob(f"RPC_{platform}*.XML")) + list(folder.glob(f"RPC_{platform}*.xml")))
    if rpc is None:
        raise FileNotFoundError(f"No RPC file found in:\n{folder}\nExpected pattern: RPC_{platform}*.XML")
    return rpc


def _find_dim_file(folder: Path, platform: str) -> Optional[Path]:
    return _first(list(folder.glob(f"DIM_{platform}*.XML")) + list(folder.glob(f"DIM_{platform}*.xml")))


def _find_tile_files(folder: Path, tile_ids: Sequence[str], log: WorkflowLog) -> List[Path]:
    selected = []
    for tile in tile_ids:
        match = None
        for ext in IMAGE_EXTENSIONS:
            candidates = sorted(folder.glob(f"*{tile}.{ext}"))
            if candidates:
                match = candidates[0]
                break
        if match is None:
            log.write(f"WARNING: no image found for tile {tile}")
        else:
            selected.append(match)
    if not selected:
        raise FileNotFoundError(f"No selected image tiles found in:\n{folder}\nRequested: {', '.join(tile_ids)}")
    return selected


def validate_project_inputs(settings: ProjectSettings, log: WorkflowLog) -> Dict[str, Dict[str, object]]:
    if not settings.project_name.strip():
        raise ValueError("Project name cannot be empty.")
    if not settings.output_base.strip():
        raise ValueError("Output base folder cannot be empty.")
    if not settings.platform.strip():
        raise ValueError("Platform cannot be empty.")
    if not settings.tile_ids:
        raise ValueError("At least one Tile ID is required.")
    if (not settings.merge_tiles) and len(settings.tile_ids) != 1:
        raise ValueError("Merge tiles is OFF. Enter exactly one Tile ID, for example R1C1.")
    if settings.crop_enabled:
        if not settings.aoi_vector.strip():
            raise ValueError("Crop is ON, but no AOI vector was selected.")
        if not Path(settings.aoi_vector).expanduser().is_file():
            raise FileNotFoundError(f"AOI vector does not exist:\n{settings.aoi_vector}")

    inspected = {}
    for name, folder in settings.acquisition_folders.items():
        folder = Path(folder)
        if not folder.is_dir():
            raise FileNotFoundError(
                f"Acquisition {name} folder does not exist:\n{folder}\n\nSelect the IMG_* folder containing image tile(s), RPC XML, and DIM XML."
            )
        rpc = _find_rpc_file(folder, settings.platform)
        dim = _find_dim_file(folder, settings.platform)
        tiles = _find_tile_files(folder, settings.tile_ids, log)
        log.write(f"Acquisition {name}: {folder}")
        log.write(f"  RPC: {rpc}")
        log.write(f"  DIM: {dim if dim else 'not found'}")
        for tile in tiles:
            log.write(f"  image: {tile}")
        inspected[name] = {"folder": folder, "rpc": rpc, "dim": dim, "tiles": tiles}
    return inspected


def _clean_gdal_env():
    env = os.environ.copy()
    env.pop("GDAL_DRIVER_PATH", None)
    env.pop("LD_LIBRARY_PATH", None)
    return env


def _find_cmd(command: str) -> Optional[str]:
    fixed = Path("/usr/bin") / command
    if fixed.is_file():
        return str(fixed)
    return shutil.which(command)


def _remove_output(path: Path, overwrite: bool):
    if not path.exists():
        return
    if not overwrite:
        raise FileExistsError(f"Output already exists:\n{path}\nEnable overwrite only if you want to replace it.")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _prepare_single_tile(source: Path, destination: Path, overwrite: bool, log: WorkflowLog):
    _remove_output(destination, overwrite)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() in {".tif", ".tiff"}:
        shutil.copy2(source, destination)
        return
    if source.suffix.lower() == ".jp2":
        gdal_translate = _find_cmd("gdal_translate")
        if gdal_translate:
            subprocess.run([gdal_translate, str(source), str(destination), "-co", "TILED=YES"], check=True, env=_clean_gdal_env(), stdout=log._stream, stderr=subprocess.STDOUT)
            return
        with rasterio.open(source) as src:
            profile = src.profile.copy()
            profile.update(driver="GTiff", tiled=True)
            with rasterio.open(destination, "w", **profile) as dst:
                for band in range(1, src.count + 1):
                    dst.write(src.read(band), band)
            return
    raise ValueError(f"Unsupported image extension: {source.suffix}")


def _merge_tiles(tile_files: Sequence[Path], out_tif: Path, out_vrt: Path, overwrite: bool, log: WorkflowLog):
    _remove_output(out_tif, overwrite)
    if out_vrt.exists():
        out_vrt.unlink()
    gdalbuildvrt = _find_cmd("gdalbuildvrt")
    gdal_translate = _find_cmd("gdal_translate")
    if not gdalbuildvrt or not gdal_translate:
        raise RuntimeError("Multi-tile preparation requires gdalbuildvrt and gdal_translate.")
    subprocess.run([gdalbuildvrt, str(out_vrt), *[str(p) for p in tile_files]], check=True, env=_clean_gdal_env(), stdout=log._stream, stderr=subprocess.STDOUT)
    subprocess.run([gdal_translate, str(out_vrt), str(out_tif), "-co", "TILED=YES"], check=True, env=_clean_gdal_env(), stdout=log._stream, stderr=subprocess.STDOUT)


def prepare_one_acquisition(name: str, inspected: Dict[str, object], settings: ProjectSettings, log: WorkflowLog):
    tile_files = list(inspected["tiles"])
    rpc_file = Path(inspected["rpc"])
    dim_file = inspected["dim"]
    out_tif = settings.merged_dir / f"{name}.tif"
    out_vrt = settings.merged_dir / f"{name}.vrt"
    out_rpc = settings.merged_dir / f"{name}.XML"
    out_dim = settings.merged_dir / f"DIM_{name}.XML"
    log.write(f"Preparing acquisition {name}")
    if settings.merge_tiles and len(tile_files) > 1:
        _merge_tiles(tile_files, out_tif, out_vrt, settings.overwrite, log)
    else:
        _prepare_single_tile(tile_files[0], out_tif, settings.overwrite, log)
    _remove_output(out_rpc, settings.overwrite)
    shutil.copy2(rpc_file, out_rpc)
    if dim_file is not None:
        _remove_output(out_dim, settings.overwrite)
        shutil.copy2(Path(dim_file), out_dim)
    return {"image": out_tif, "rpc": out_rpc, "dim": out_dim if dim_file is not None else None}


# ============================================================
# OPTIONAL AOI CROP
# ============================================================

def _require_rpcm():
    try:
        from rpcm.rpc_model import RPCModel
        return RPCModel
    except ImportError as exc:
        raise ImportError("AOI cropping uses rpcm RPCModel. Install rpcm in this environment.") from exc


def load_rpc_from_xml(xml_path):
    RPCModel = _require_rpcm()
    tree = ET.parse(xml_path)
    root = tree.getroot()
    rfm = root.find(".//Rational_Function_Model/Global_RFM")
    if rfm is None:
        raise ValueError(f"Cannot find Rational_Function_Model/Global_RFM in:\n{xml_path}")
    def get(tag):
        elem = rfm.find(f".//{tag}")
        if elem is None:
            raise ValueError(f"Missing RPC tag {tag} in:\n{xml_path}")
        return elem.text.strip()
    def coeffs(prefix):
        return " ".join(get(f"{prefix}_{i}") for i in range(1, 21))
    rpc_dict = {
        "LINE_OFF": get("LINE_OFF"),
        "SAMP_OFF": get("SAMP_OFF"),
        "LAT_OFF": get("LAT_OFF"),
        "LONG_OFF": get("LONG_OFF"),
        "HEIGHT_OFF": get("HEIGHT_OFF"),
        "LINE_SCALE": get("LINE_SCALE"),
        "SAMP_SCALE": get("SAMP_SCALE"),
        "LAT_SCALE": get("LAT_SCALE"),
        "LONG_SCALE": get("LONG_SCALE"),
        "HEIGHT_SCALE": get("HEIGHT_SCALE"),
        "LINE_NUM_COEFF": coeffs("LINE_NUM_COEFF"),
        "LINE_DEN_COEFF": coeffs("LINE_DEN_COEFF"),
        "SAMP_NUM_COEFF": coeffs("SAMP_NUM_COEFF"),
        "SAMP_DEN_COEFF": coeffs("SAMP_DEN_COEFF"),
    }
    return RPCModel(rpc_dict, dict_format="geotiff")


def update_rpc_offsets(in_xml, out_xml, col0, row0):
    tree = ET.parse(in_xml)
    root = tree.getroot()
    samp = root.find(".//SAMP_OFF")
    line = root.find(".//LINE_OFF")
    if samp is None or line is None:
        raise ValueError(f"Missing LINE_OFF/SAMP_OFF in:\n{in_xml}")
    samp.text = str(float(samp.text) - col0)
    line.text = str(float(line.text) - row0)
    out_xml = Path(out_xml)
    out_xml.parent.mkdir(parents=True, exist_ok=True)
    tree.write(out_xml, encoding="UTF-8", xml_declaration=True)


def densify_linestring(line, spacing_deg):
    n_points = max(2, int(line.length / spacing_deg))
    return [line.interpolate(i / (n_points - 1), normalized=True).coords[0] for i in range(n_points)]


def extract_aoi_lonlat_points(vector_path, spacing_deg):
    import geopandas as gpd
    gdf = gpd.read_file(vector_path)
    if gdf.empty:
        raise ValueError(f"AOI vector is empty:\n{vector_path}")
    if gdf.crs is None:
        raise ValueError("AOI vector has no CRS.")
    gdf = gdf.to_crs("EPSG:4326")
    try:
        geom = gdf.geometry.union_all()
    except AttributeError:
        geom = gdf.geometry.unary_union
    points = []
    def add_geom(g):
        if g.geom_type == "Polygon":
            points.extend(densify_linestring(g.exterior, spacing_deg))
        elif g.geom_type == "MultiPolygon":
            for part in g.geoms:
                points.extend(densify_linestring(part.exterior, spacing_deg))
        elif g.geom_type == "LineString":
            points.extend(densify_linestring(g, spacing_deg))
        elif g.geom_type == "MultiLineString":
            for part in g.geoms:
                points.extend(densify_linestring(part, spacing_deg))
        else:
            raise ValueError(f"Unsupported AOI geometry type: {g.geom_type}")
    add_geom(geom)
    if not points:
        raise ValueError("No AOI points were extracted.")
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return lons, lats


def get_rpc_window(rpc_path, lons, lats, height, buffer_px):
    rpc = load_rpc_from_xml(rpc_path)
    heights = [height] * len(lons)
    cols, rows = rpc.projection(lons, lats, heights)
    col_min = int(np.floor(np.min(cols))) - buffer_px
    col_max = int(np.ceil(np.max(cols))) + buffer_px
    row_min = int(np.floor(np.min(rows))) - buffer_px
    row_max = int(np.ceil(np.max(rows))) + buffer_px
    return col_min, col_max, row_min, row_max


def crop_one_rpc_image(img_path, rpc_path, out_img_path, out_rpc_path, rpc_window, overwrite):
    _remove_output(Path(out_img_path), overwrite)
    _remove_output(Path(out_rpc_path), overwrite)
    col_min, col_max, row_min, row_max = rpc_window
    with rasterio.open(img_path) as src:
        col0 = max(0, col_min)
        row0 = max(0, row_min)
        col1 = min(src.width, col_max)
        row1 = min(src.height, row_max)
        width = col1 - col0
        height_crop = row1 - row0
        if width <= 0 or height_crop <= 0:
            raise ValueError(f"No overlap between AOI and image:\n{img_path}")
        window = Window(col0, row0, width, height_crop)
        data = src.read(window=window)
        transform = src.window_transform(window)
        meta = src.meta.copy()
        meta.update({"height": data.shape[1], "width": data.shape[2], "transform": transform})
    Path(out_img_path).parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_img_path, "w", **meta) as dst:
        dst.write(data)
    update_rpc_offsets(in_xml=rpc_path, out_xml=out_rpc_path, col0=col0, row0=row0)
    return col0, row0, width, height_crop


def crop_prepared_images(settings: ProjectSettings, log: WorkflowLog):
    lons, lats = extract_aoi_lonlat_points(settings.aoi_vector, settings.spacing_deg)
    results = {}
    for name in settings.image_names:
        img_path = settings.merged_dir / f"{name}.tif"
        rpc_path = settings.merged_dir / f"{name}.XML"
        out_name = f"{name}_crop"
        out_img = settings.cropped_dir / f"{out_name}.tif"
        out_rpc = settings.cropped_dir / f"{out_name}.XML"
        rpc_window = get_rpc_window(rpc_path, lons, lats, settings.rpc_height, settings.buffer_px)
        final_window = crop_one_rpc_image(img_path, rpc_path, out_img, out_rpc, rpc_window, settings.overwrite)
        src_dim = settings.merged_dir / f"DIM_{name}.XML"
        dst_dim = settings.cropped_dir / f"DIM_{out_name}.XML"
        if src_dim.exists():
            _remove_output(dst_dim, settings.overwrite)
            shutil.copy2(src_dim, dst_dim)
        else:
            dst_dim = None
        log.write(f"Cropped {name}: {out_img}")
        results[name] = {"image": out_img, "rpc": out_rpc, "dim": dst_dim, "window": final_window}
    return results


def _read_preview(path: Path, max_display_size=1200, percentiles=(2, 98)):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(path) as src:
            scale = max(src.width / max_display_size, src.height / max_display_size, 1.0)
            out_width = max(1, int(round(src.width / scale)))
            out_height = max(1, int(round(src.height / scale)))
            image = src.read(1, out_shape=(out_height, out_width), resampling=Resampling.bilinear).astype("float32")
            try:
                mask = src.dataset_mask(out_shape=(out_height, out_width), resampling=Resampling.nearest) == 0
            except TypeError:
                mask = src.dataset_mask() == 0
                if mask.shape != image.shape:
                    mask = np.zeros_like(image, dtype=bool)
            if src.nodata is not None:
                mask |= np.isclose(image, src.nodata)
            mask |= np.isclose(image, 0)
    valid = (~mask) & np.isfinite(image)
    if valid.any():
        low, high = np.nanpercentile(image[valid], percentiles)
        if high <= low:
            high = low + 1.0
        stretched = np.clip((image - low) / (high - low), 0, 1)
    else:
        stretched = np.zeros_like(image, dtype="float32")
    return np.ma.array(stretched, mask=mask)


def make_preview(settings: ProjectSettings, cropped: bool):
    stage = "cropped" if cropped else "prepared"
    paths = [settings.cropped_dir / f"{name}_crop.tif" for name in settings.image_names] if cropped else [settings.merged_dir / f"{name}.tif" for name in settings.image_names]
    arrays = [_read_preview(path) for path in paths]
    fig, axes = plt.subplots(1, len(arrays), figsize=(5.0 * len(arrays), 5.5), squeeze=False)
    axes = axes.ravel()
    cmap = plt.get_cmap("gray").copy()
    cmap.set_bad("white")
    for ax, name, array in zip(axes, settings.image_names, arrays):
        ax.imshow(array, cmap=cmap)
        ax.set_title(f"{name} — {stage}")
        ax.set_xlabel("Column")
        ax.set_ylabel("Row")
        ax.grid(True, alpha=0.22, linewidth=0.5, linestyle="--")
    fig.suptitle(f"{settings.project_name} — {stage} image preview")
    fig.tight_layout()
    settings.figure_dir.mkdir(parents=True, exist_ok=True)
    png = settings.figure_dir / f"{stage}_images_preview.png"
    pdf = settings.figure_dir / f"{stage}_images_preview.pdf"
    fig.savefig(png, dpi=200, bbox_inches="tight")
    fig.savefig(pdf, dpi=300, bbox_inches="tight")
    return {"figure": fig, "png": png, "pdf": pdf, "images": paths, "stage": stage}


def run_prepare_data(settings: ProjectSettings, progress_callback=None):
    create_project_folders(settings)
    log_path = settings.log_dir / "prepare_data.log"
    with WorkflowLog(log_path) as log:
        try:
            if progress_callback: progress_callback(5, "Validating inputs")
            inspected = validate_project_inputs(settings, log)
            if progress_callback: progress_callback(10, "Saving project configuration")
            config_path = save_project_config(settings)
            preparation = {}
            names = settings.image_names
            for index, name in enumerate(names, start=1):
                if progress_callback:
                    value = 20 + int((index - 1) / len(names) * 35)
                    progress_callback(value, f"Preparing acquisition {name}")
                preparation[name] = prepare_one_acquisition(name, inspected[name], settings, log)
            crop = {}
            if settings.crop_enabled:
                if progress_callback: progress_callback(65, "Cropping prepared images")
                crop = crop_prepared_images(settings, log)
                preview_is_cropped = True
            else:
                preview_is_cropped = False
            if progress_callback: progress_callback(88, "Creating image preview")
            preview = make_preview(settings, cropped=preview_is_cropped)
            active_images = {name: settings.cropped_dir / f"{name}_crop.tif" for name in settings.image_names} if preview_is_cropped else {name: settings.merged_dir / f"{name}.tif" for name in settings.image_names}
            if progress_callback: progress_callback(100, "Completed")
            log.write(f"Active image stage: {preview['stage']}")
            for name, path in active_images.items():
                log.write(f"Active image {name}: {path}")
            return {"config_path": config_path, "preparation": preparation, "crop": crop, "active_images": active_images, "preview": preview, "log_path": log_path}
        except Exception:
            log.write(traceback.format_exc())
            raise


# ============================================================
# METADATA AND GEOMETRY
# Internal implementation adapted from user's script.
# ============================================================

def clean_tag(tag):
    return tag.split("}")[-1]


def safe_float(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", ".").strip())
    except Exception:
        return None


def find_image_ids(img_dir):
    image_ids = []
    for filename in sorted(os.listdir(img_dir)):
        if filename.lower().endswith(".tif"):
            image_id = os.path.splitext(filename)[0]
            tif_path = os.path.join(img_dir, f"{image_id}.tif")
            rpc_path = os.path.join(img_dir, f"{image_id}.XML")
            if os.path.isfile(tif_path) and os.path.isfile(rpc_path):
                image_ids.append(image_id)
    return image_ids


def parse_pairs_argument(pairs_arg):
    if not pairs_arg:
        return None
    pairs = []
    for item in pairs_arg:
        if ":" not in item:
            raise ValueError(f"Invalid pair format: {item}. Use LEFT:RIGHT, for example A:B.")
        left, right = item.split(":", 1)
        pairs.append((left.strip(), right.strip()))
    return pairs


def get_rpc_params(xml_path):
    tree = ET.parse(xml_path)
    root = tree.getroot()
    tags = ["SAMP_OFF", "SAMP_SCALE", "LINE_OFF", "LINE_SCALE", "LAT_OFF", "LAT_SCALE", "LONG_OFF", "LONG_SCALE"]
    params = {}
    for tag in tags:
        elem = root.find(f".//{tag}")
        params[tag] = float(elem.text) if elem is not None and elem.text is not None else (1.0 if tag.endswith("SCALE") else 0.0)
    return params


def approximate_footprint(img_path, rpc):
    from shapely.geometry import box
    with rasterio.open(img_path) as src:
        width, height = src.width, src.height
    x_min = -rpc["SAMP_OFF"] / rpc["SAMP_SCALE"]
    x_max = (width - rpc["SAMP_OFF"]) / rpc["SAMP_SCALE"]
    y_min = -rpc["LINE_OFF"] / rpc["LINE_SCALE"]
    y_max = (height - rpc["LINE_OFF"]) / rpc["LINE_SCALE"]
    lon_min = rpc["LONG_OFF"] + x_min * rpc["LONG_SCALE"]
    lon_max = rpc["LONG_OFF"] + x_max * rpc["LONG_SCALE"]
    lat_min = rpc["LAT_OFF"] + y_min * rpc["LAT_SCALE"]
    lat_max = rpc["LAT_OFF"] + y_max * rpc["LAT_SCALE"]
    return box(lon_min, lat_min, lon_max, lat_max)


def build_footprints(img_dir, image_ids):
    footprints = {}
    for image_id in image_ids:
        img_path = os.path.join(img_dir, f"{image_id}.tif")
        rpc_path = os.path.join(img_dir, f"{image_id}.XML")
        rpc = get_rpc_params(rpc_path)
        footprints[image_id] = approximate_footprint(img_path, rpc)
    return footprints


def compute_overlap_table(footprints, pairs, iou_thresh):
    records = []
    for left, right in pairs:
        if left not in footprints or right not in footprints:
            records.append({"pair": f"{left}{right}", "left_image": left, "right_image": right, "iou": None, "is_valid": False, "note": "missing footprint"})
            continue
        poly_left = footprints[left]
        poly_right = footprints[right]
        inter_area = poly_left.intersection(poly_right).area
        union_area = poly_left.union(poly_right).area
        iou = inter_area / union_area if union_area > 0 else 0.0
        records.append({"pair": f"{left}{right}", "left_image": left, "right_image": right, "iou": round(iou, 4), "is_valid": iou >= iou_thresh, "note": ""})
    return pd.DataFrame(records)


def extract_image_id_from_dim(dim_filename):
    name = os.path.basename(dim_filename)
    name = os.path.splitext(name)[0]
    return name.replace("DIM_", "", 1) if name.startswith("DIM_") else name


def parse_dim_xml(xml_path):
    try:
        tree = ET.parse(xml_path)
        return tree.getroot()
    except ET.ParseError:
        return None


def extract_dim_metadata(root, xml_path):
    image_id = extract_image_id_from_dim(xml_path)
    metadata = {"image_id": image_id, "dim_file": os.path.basename(xml_path), "tif_file": f"{image_id}.tif", "rpc_file": f"{image_id}.XML"}
    target_tags = {"NBANDS", "NBITS", "FOCAL_LENGTH", "AZIMUTH_ANGLE", "VIEWING_ANGLE_ACROSS_TRACK", "VIEWING_ANGLE_ALONG_TRACK", "VIEWING_ANGLE", "INCIDENCE_ANGLE_ALONG_TRACK", "INCIDENCE_ANGLE_ACROSS_TRACK", "INCIDENCE_ANGLE", "SUN_AZIMUTH", "SUN_ELEVATION", "IMAGING_DATE", "IMAGING_TIME"}
    for elem in root.iter():
        tag = clean_tag(elem.tag)
        text = elem.text.strip() if elem.text and elem.text.strip() else None
        if tag in target_tags:
            metadata[tag] = text
        if tag == "Planimetric_Accuracy":
            for sub_elem in elem:
                sub_tag = clean_tag(sub_elem.tag)
                sub_text = sub_elem.text.strip() if sub_elem.text and sub_elem.text.strip() else None
                if sub_tag in ["MEAN", "STDV", "CE90"]:
                    metadata[f"Planimetric_Accuracy_{sub_tag}"] = sub_text
    return metadata


def build_image_metadata_table(img_dir, image_ids):
    records = []
    for image_id in image_ids:
        dim_path = os.path.join(img_dir, f"DIM_{image_id}.XML")
        if not os.path.isfile(dim_path):
            continue
        root = parse_dim_xml(dim_path)
        if root is None:
            continue
        records.append(extract_dim_metadata(root, dim_path))
    metadata_df = pd.DataFrame(records)
    if metadata_df.empty:
        return metadata_df
    if "IMAGING_DATE" in metadata_df.columns and "IMAGING_TIME" in metadata_df.columns:
        metadata_df = metadata_df.sort_values(by=["IMAGING_DATE", "IMAGING_TIME"]).reset_index(drop=True)
    if len(metadata_df) == 3:
        metadata_df.insert(1, "view_order", ["Forward", "Middle", "Backward"])
    elif len(metadata_df) == 2:
        metadata_df.insert(1, "view_order", ["Image_1", "Image_2"])
    else:
        metadata_df.insert(1, "view_order", [f"Image_{i+1}" for i in range(len(metadata_df))])
    return metadata_df


class ImageGeometry:
    def __init__(self, image_id, along, across, azimuth):
        self.image_id = image_id
        self.scan = radians(float(along))
        self.ortho = radians(float(across))
        self.azimuth = radians(float(azimuth))
        self.ortho = -self.ortho
        self.azimuth = -self.azimuth
        self.compute_components()

    def compute_components(self):
        self.s_comp = cos(self.ortho) * sin(self.scan)
        self.o_comp = cos(self.scan) * sin(self.ortho)
        self.z_comp = cos(self.ortho) * cos(self.scan)


def compute_bh_and_stereo_angle(im1, im2):
    delta_az = im2.azimuth - im1.azimuth
    rot_z = [[cos(delta_az), -sin(delta_az), 0], [sin(delta_az), cos(delta_az), 0], [0, 0, 1]]
    p1 = [im1.s_comp, im1.o_comp, im1.z_comp]
    p1_rot = [sum(a * b for a, b in zip(row, p1)) for row in rot_z]
    p2 = [im2.s_comp, im2.o_comp, im2.z_comp]
    dot = sum(a * b for a, b in zip(p1_rot, p2))
    dot = max(min(dot, 1.0), -1.0)
    stereo_angle_rad = acos(dot)
    stereo_angle_deg = degrees(stereo_angle_rad)
    bh = 2.0 * tan(stereo_angle_rad / 2.0)
    return stereo_angle_deg, bh


def build_stereo_geometry_table(metadata_df, pairs):
    required_cols = ["image_id", "INCIDENCE_ANGLE_ALONG_TRACK", "INCIDENCE_ANGLE_ACROSS_TRACK", "AZIMUTH_ANGLE"]
    missing_cols = [c for c in required_cols if c not in metadata_df.columns]
    if missing_cols:
        return pd.DataFrame(columns=["pair", "left_image", "right_image", "stereo_angle_deg", "B_over_H", "geometry_source"])
    metadata_index = metadata_df.set_index("image_id")
    records = []
    for left, right in pairs:
        if left not in metadata_index.index or right not in metadata_index.index:
            records.append({"pair": f"{left}{right}", "left_image": left, "right_image": right, "stereo_angle_deg": None, "B_over_H": None, "geometry_source": "missing metadata"})
            continue
        row_left = metadata_index.loc[left]
        row_right = metadata_index.loc[right]
        values = [row_left["INCIDENCE_ANGLE_ALONG_TRACK"], row_left["INCIDENCE_ANGLE_ACROSS_TRACK"], row_left["AZIMUTH_ANGLE"], row_right["INCIDENCE_ANGLE_ALONG_TRACK"], row_right["INCIDENCE_ANGLE_ACROSS_TRACK"], row_right["AZIMUTH_ANGLE"]]
        if any(safe_float(v) is None for v in values):
            records.append({"pair": f"{left}{right}", "left_image": left, "right_image": right, "stereo_angle_deg": None, "B_over_H": None, "geometry_source": "missing angle value"})
            continue
        im1 = ImageGeometry(left, row_left["INCIDENCE_ANGLE_ALONG_TRACK"], row_left["INCIDENCE_ANGLE_ACROSS_TRACK"], row_left["AZIMUTH_ANGLE"])
        im2 = ImageGeometry(right, row_right["INCIDENCE_ANGLE_ALONG_TRACK"], row_right["INCIDENCE_ANGLE_ACROSS_TRACK"], row_right["AZIMUTH_ANGLE"])
        stereo_angle_deg, bh = compute_bh_and_stereo_angle(im1, im2)
        records.append({"pair": f"{left}{right}", "left_image": left, "right_image": right, "stereo_angle_deg": round(stereo_angle_deg, 3), "B_over_H": round(bh, 3), "geometry_source": "incidence_angles_and_azimuth"})
    return pd.DataFrame(records)


PREFERRED_METADATA_ORDER = ["view_order", "tif_file", "rpc_file", "dim_file", "IMAGING_DATE", "IMAGING_TIME", "NBANDS", "NBITS", "FOCAL_LENGTH", "AZIMUTH_ANGLE", "VIEWING_ANGLE_ACROSS_TRACK", "VIEWING_ANGLE_ALONG_TRACK", "VIEWING_ANGLE", "INCIDENCE_ANGLE_ALONG_TRACK", "INCIDENCE_ANGLE_ACROSS_TRACK", "INCIDENCE_ANGLE", "SUN_AZIMUTH", "SUN_ELEVATION"]


def run_metadata_geometry(settings: ProjectSettings, iou_threshold: float = 0.05, custom_pairs_text: str = ""):
    create_project_folders(settings)
    work_dir = settings.merged_dir
    log_path = settings.log_dir / "metadata_geometry.log"
    with WorkflowLog(log_path) as log:
        try:
            image_ids = find_image_ids(str(work_dir))
            if len(image_ids) < 2:
                raise RuntimeError(f"At least two prepared image/RPC pairs are required in {work_dir}. Found: {image_ids}")
            pairs_arg = [x for x in custom_pairs_text.replace(',', ' ').split() if x.strip()]
            user_pairs = parse_pairs_argument(pairs_arg)
            pairs = list(combinations(image_ids, 2)) if user_pairs is None else user_pairs
            log.write(f"Detected prepared image IDs: {image_ids}")
            log.write(f"Pairs: {pairs}")
            footprints = build_footprints(str(work_dir), image_ids)
            overlap_df = compute_overlap_table(footprints, pairs, iou_threshold)
            metadata_df = build_image_metadata_table(str(work_dir), image_ids)
            geometry_df = build_stereo_geometry_table(metadata_df, pairs)
            overlap_csv = settings.metadata_dir / f"{settings.project_name}_overlap_pairs.csv"
            metadata_csv = settings.metadata_dir / f"{settings.project_name}_image_metadata.csv"
            geometry_csv = settings.metadata_dir / f"{settings.project_name}_stereo_geometry.csv"
            overlap_df.to_csv(overlap_csv, index=False)
            metadata_df.to_csv(metadata_csv, index=False)
            geometry_df.to_csv(geometry_csv, index=False)
            if 'image_id' not in metadata_df.columns:
                metadata_vertical_df = pd.DataFrame()
            else:
                metadata_vertical_df = metadata_df.set_index('image_id').T.reset_index().rename(columns={'index': 'Parameter'})
                existing_preferred = [p for p in PREFERRED_METADATA_ORDER if p in metadata_vertical_df['Parameter'].values]
                remaining = [p for p in metadata_vertical_df['Parameter'].values if p not in existing_preferred]
                metadata_vertical_df = metadata_vertical_df.set_index('Parameter').loc[existing_preferred + remaining].reset_index()
            return {
                'work_dir': work_dir,
                'overlap': overlap_df,
                'metadata': metadata_vertical_df,
                'geometry': geometry_df,
                'overlap_csv': overlap_csv,
                'metadata_csv': metadata_csv,
                'geometry_csv': geometry_csv,
                'log_path': log_path,
            }
        except Exception:
            log.write(traceback.format_exc())
            raise


# ============================================================
# UI
# ============================================================

class ProjectSetupUI:
    def __init__(self):
        import ipywidgets as widgets
        from IPython.display import display, Image
        self.widgets = widgets
        self.display_fn = display
        self.ImageClass = Image
        self.last_prepare = None
        self.last_metadata = None
        style = {"description_width": "185px"}
        wide = widgets.Layout(width="780px")
        # Publication/software cover is provided by the first Markdown cell
        # of the user-facing notebook, so it is visible before Python runs.
        self.title = widgets.HTML("")
        self.project_name = widgets.Text(value="Berarde_Aug24", description="Project name:", style=style, layout=wide)
        self.output_base = widgets.Text(value=str(Path.home()), description="Output base folder:", style=style, layout=wide)
        self.platform = widgets.Combobox(
            options=[
                "PHR1A",
                "PHR1B",
                "PNEO3",
                "PNEO4",
                "SPOT6",
                "SPOT7",
            ],
            value="PHR1A",
            ensure_option=False,
            description="Platform:",
            placeholder="Select or type platform code",
            style=style,
            layout=wide,
        )
        self.acquisition_mode = widgets.ToggleButtons(options=[("Stereo — A/B", "stereo"), ("Tri-stereo — A/B/C", "tri_stereo")], value="tri_stereo", description="Acquisition:", style=style)
        self.acquisition_A = widgets.Text(description="Acquisition A folder:", placeholder="/path/to/Pleiades_1/IMG_PHR1A_P_001", style=style, layout=wide)
        self.acquisition_B = widgets.Text(description="Acquisition B folder:", placeholder="/path/to/Pleiades_2/IMG_PHR1A_P_001", style=style, layout=wide)
        self.acquisition_C = widgets.Text(description="Acquisition C folder:", placeholder="/path/to/Pleiades_3/IMG_PHR1A_P_001", style=style, layout=wide)
        self.merge_tiles = widgets.Checkbox(value=False, description="Merge selected image tiles", indent=False)
        self.tile_ids = widgets.Text(value="R1C1", description="Tile ID(s):", style=style, layout=wide)
        self.crop_enabled = widgets.Checkbox(value=False, description="Crop prepared images to an AOI", indent=False)
        self.aoi_vector = widgets.Text(description="AOI vector:", placeholder="/path/to/aoi.shp", style=style, layout=wide)
        self.rpc_height = widgets.FloatText(value=2500.0, description="RPC height (m):", style=style)
        self.buffer_px = widgets.IntText(value=500, description="Crop buffer (px):", style=style)
        self.spacing_deg = widgets.FloatText(value=0.00005, description="AOI spacing (deg):", style=style)
        self.overwrite = widgets.Checkbox(value=False, description="Overwrite existing outputs", indent=False)
        self.run_stage1 = widgets.Button(description="Run prepare data", button_style="success", icon="play", layout=widgets.Layout(width="240px", height="42px"))
        self.stage1_progress = widgets.IntProgress(value=0, min=0, max=100, description="Progress:", style={"description_width": "80px"}, layout=widgets.Layout(width="720px"))
        self.stage1_progress_text = widgets.HTML("<span style='color:#666;'>Waiting.</span>")
        self.stage1_summary = widgets.HTML()
        self.stage1_preview = widgets.Output(layout=widgets.Layout(border="1px solid #ddd", padding="6px", width="100%"))
        self.iou_threshold = widgets.FloatText(value=0.05, description="IoU threshold:", style=style)
        self.custom_pairs = widgets.Text(value="", description="Custom pairs:", placeholder="Leave blank for automatic pairs; e.g. A:B A:C B:C", style=style, layout=wide)
        self.run_stage2 = widgets.Button(description="Run metadata and geometry", button_style="info", icon="table", layout=widgets.Layout(width="280px", height="42px"))
        self.stage2_summary = widgets.HTML()
        self.stage2_tables_box = widgets.VBox()
        # Persistent concept-figure container.
        #
        # A widgets.Image keeps the PNG bytes in widget state. This is more
        # stable than rendering the image through widgets.Output during
        # interface construction.
        self.concept_figure = widgets.VBox(
            layout=widgets.Layout(
                border="1px solid #ddd",
                padding="6px",
                width="100%",
                margin="4px 0 12px 0",
                align_items="center",
            )
        )

        self.common_rows = [
            self._row(self.project_name, "Short project name. A folder with this name is created inside the Output base folder. Example: Berarde_Aug24."),
            self._row(self.output_base, "Parent folder only. Do not add the project name or merged_tiles. Example: /mnt/summer/USERS/KOAI/Software."),
            self._row(self.platform, "Sensor/platform code used to find RPC_<Platform>*.XML and DIM_<Platform>*.XML. Presets include Pléiades (PHR1A/PHR1B), Pléiades Neo (PNEO3/PNEO4), and SPOT 6/7; another compatible code may also be typed."),
            self._row(self.acquisition_mode, "Stereo uses A/B. Tri-stereo uses A/B/C."),
            self._row(self.acquisition_A, "IMG_* folder for the first acquisition. It should contain the selected image tile(s), RPC XML and DIM XML."),
            self._row(self.acquisition_B, "IMG_* folder for the second acquisition."),
            self._row(self.acquisition_C, "IMG_* folder for the third acquisition. Used only for tri-stereo."),
            self._row(self.merge_tiles, "OFF: use one selected tile only. ON: merge several DIMAP tiles into one A/B/C image. Example ON: R1C1,R1C2,R2C1,R2C2."),
            self._row(self.tile_ids, "Tile ID(s) searched in every acquisition. When merge is OFF, use exactly one, e.g. R1C1. When ON, separate IDs by commas."),
            self._row(self.crop_enabled, "OFF: use full prepared A/B(/C). ON: additionally create A_crop/B_crop/C_crop in merged_tiles/cropped_images. Raw data are never modified."),
        ]
        self.crop_rows = [
            self._row(self.aoi_vector, "AOI vector used by the original RPC crop logic. It must have a valid CRS."),
            self._row(self.rpc_height, "Representative terrain height used by RPC projection. Original default: 2500 m."),
            self._row(self.buffer_px, "Extra pixels around the projected AOI crop. Original default: 500 px."),
            self._row(self.spacing_deg, "AOI boundary densification spacing. Original default: 0.00005 degrees."),
        ]
        self.overwrite_row = self._row(self.overwrite, "Enable only when you intentionally want to replace existing outputs.")
        self.block2_rows = [
            self._row(self.iou_threshold, "Minimum IoU used to mark an overlap pair as valid. Original default: 0.05."),
            self._row(self.custom_pairs, "Optional. Leave blank to use all automatic image pairs. For custom cases use LEFT:RIGHT, e.g. A_1:B_1 A_2:B_2."),
        ]
        self.crop_box = widgets.VBox(self.crop_rows)
        self.acquisition_mode.observe(self._update_visibility, names="value")
        self.crop_enabled.observe(self._update_visibility, names="value")
        self.run_stage1.on_click(self._on_stage1)
        self.run_stage2.on_click(self._on_stage2)
        self._update_visibility()
        self.container = widgets.VBox([
            self.title,
            widgets.HTML("<hr><h4>Prepare data</h4><div style='color:#666;margin-bottom:8px;'>Organize the project folders, prepare A/B(/C), optionally crop to an AOI, and save a compact image preview.</div>"),
            widgets.HTML("<b>1. Project</b>"), *self.common_rows[:3],
            widgets.HTML("<br><b>2. Input acquisitions</b>"), *self.common_rows[3:7],
            widgets.HTML("<br><b>3. Image tile preparation</b>"), *self.common_rows[7:9],
            widgets.HTML("<br><b>4. Optional AOI crop</b>"), self.common_rows[9], self.crop_box,
            widgets.HTML("<br><b>5. Output protection</b>"), self.overwrite_row,
            widgets.HTML("<br>"), self.run_stage1, self.stage1_progress, self.stage1_progress_text, self.stage1_summary, self.stage1_preview,
            widgets.HTML("<hr><h4>Metadata and geometry</h4><div style='color:#666;margin-bottom:8px;'>This step uses the full prepared A/B(/C) files in <code>merged_tiles</code>, matching the original metadata workflow even when an AOI crop was also created.</div>"),
            self.concept_figure,
            *self.block2_rows, widgets.HTML("<br>"), self.run_stage2, self.stage2_summary, self.stage2_tables_box
        ], layout=widgets.Layout(width="100%"))
        self._display_concept_figure()

    def _help_icon(self, text):
        safe = html.escape(text, quote=True)
        return self.widgets.HTML(value=(f"<span title=\"{safe}\" style='cursor:help; font-size:18px; color:#336699; padding-left:5px;'>ⓘ</span>"), layout=self.widgets.Layout(width="32px"))

    def _row(self, widget, help_text):
        return self.widgets.HBox([widget, self._help_icon(help_text)], layout=self.widgets.Layout(width="100%", align_items="center"))

    def _update_visibility(self, change=None):
        is_tri = self.acquisition_mode.value == "tri_stereo"
        self.common_rows[6].layout.display = "" if is_tri else "none"
        self.crop_box.layout.display = "" if self.crop_enabled.value else "none"

    def _display_concept_figure(self):
        """
        Display the stereo/tri-stereo concept figure persistently.

        The PNG bytes are stored directly in an ipywidgets.Image so the figure
        remains visible after the full interface finishes rendering.
        """
        module_dir = Path(__file__).resolve().parent

        candidates = [
            module_dir / "figures" / "overview_mountain_page1.png",
            Path.cwd() / "figures" / "overview_mountain_page1.png",
        ]

        img_path = next(
            (path for path in candidates if path.is_file()),
            None,
        )

        if img_path is not None:
            image_widget = self.widgets.Image(
                value=img_path.read_bytes(),
                format="png",
                layout=self.widgets.Layout(
                    width="100%",
                    max_width="1500px",
                    height="auto",
                ),
            )

            self.concept_figure.children = (
                image_widget,
            )

        else:
            searched = "<br>".join(
                f"<code>{html.escape(str(path))}</code>"
                for path in candidates
            )

            warning = self.widgets.HTML(
                value=(
                    "<div style='padding:10px 12px;"
                    "border-left:4px solid #d58b00;"
                    "background:#fffaf0;color:#555;width:100%;'>"
                    "<b>Concept figure not found.</b><br>"
                    "Expected software asset: "
                    "<code>overview_mountain_page1.png</code>.<br>"
                    "Keep all software figures inside the <code>figures/</code> folder.<br><br>"
                    "<b>Searched:</b><br>"
                    f"{searched}"
                    "</div>"
                )
            )

            self.concept_figure.children = (
                warning,
            )

    def _build_settings(self):
        project_name = self.project_name.value.strip()
        output_base = self.output_base.value.strip()
        platform = self.platform.value.strip()
        if not project_name: raise ValueError("Project name is required.")
        if not output_base: raise ValueError("Output base folder is required.")
        if not platform: raise ValueError("Platform is required.")
        a = self.acquisition_A.value.strip()
        b = self.acquisition_B.value.strip()
        c = self.acquisition_C.value.strip()
        if not a or not b: raise ValueError("Acquisition A and B folders are required.")
        if self.acquisition_mode.value == "tri_stereo" and not c: raise ValueError("Acquisition C is required for tri-stereo.")
        tile_ids = tuple(value.strip() for value in self.tile_ids.value.replace(";", ",").split(",") if value.strip())
        if not tile_ids: raise ValueError("At least one Tile ID is required.")
        return ProjectSettings(project_name=project_name, output_base=output_base, platform=platform, acquisition_mode=self.acquisition_mode.value, acquisition_A=a, acquisition_B=b, acquisition_C=c, merge_tiles=bool(self.merge_tiles.value), tile_ids=tile_ids, crop_enabled=bool(self.crop_enabled.value), aoi_vector=self.aoi_vector.value.strip(), rpc_height=float(self.rpc_height.value), buffer_px=int(self.buffer_px.value), spacing_deg=float(self.spacing_deg.value), overwrite=bool(self.overwrite.value))

    def _set_progress(self, value, message):
        self.stage1_progress.value = int(value)
        self.stage1_progress_text.value = f"<span style='color:#555;'>{html.escape(message)}</span>"

    def _on_stage1(self, _):
        self.stage1_summary.value = ""
        self.stage1_preview.clear_output()
        self.stage1_progress.bar_style = ""
        self.run_stage1.disabled = True
        try:
            settings = self._build_settings()
            result = run_prepare_data(settings, progress_callback=self._set_progress)
            self.last_prepare = result
            active_lines = "<br>".join(f"<code>{name}: {html.escape(str(path))}</code>" for name, path in result['active_images'].items())
            crop_text = "AOI crop created; cropped images are active." if settings.crop_enabled else "AOI crop not requested; full prepared images are active."
            self.stage1_summary.value = ("<div style='margin:10px 0;padding:10px;border-left:4px solid #2e7d32;background:#f4fbf4;'>"
                                         "<b>✓ Prepare data completed.</b><br>" + html.escape(crop_text) + "<br><br><b>Active images:</b><br>" + active_lines + "<br><br><b>Preview:</b> <code>" + html.escape(str(result['preview']['png'])) + "</code><br><b>Detailed log:</b> <code>" + html.escape(str(result['log_path'])) + "</code></div>")
            with self.stage1_preview:
                self.display_fn(result['preview']['figure'])
            plt.close(result['preview']['figure'])
            self.stage1_progress.bar_style = "success"
        except Exception as exc:
            self.stage1_progress.bar_style = "danger"
            try:
                settings = self._build_settings()
                log_path = settings.log_dir / 'prepare_data.log'
            except Exception:
                log_path = Path('(log path unavailable)')
            self.stage1_summary.value = ("<div style='margin:10px 0;padding:10px;border-left:4px solid #b00020;background:#fff4f4;'><b>✗ Prepare data stopped.</b><br>" + html.escape(type(exc).__name__ + ': ' + str(exc)) + "<br><br><b>Detailed log:</b> <code>" + html.escape(str(log_path)) + "</code></div>")
        finally:
            self.run_stage1.disabled = False

    def _dataframe_html(self, df):
        if df is None or len(df) == 0:
            return "<div style='padding:10px;color:#666;'>No rows to display.</div>"
        return "<div style='max-height:430px;overflow:auto;border:1px solid #ddd;padding:4px;'>" + df.to_html(index=False, border=0) + "</div>"

    def _on_stage2(self, _):
        self.stage2_summary.value = ""
        self.stage2_tables_box.children = ()
        self.run_stage2.disabled = True
        try:
            settings = self._build_settings()
            result = run_metadata_geometry(settings=settings, iou_threshold=float(self.iou_threshold.value), custom_pairs_text=self.custom_pairs.value)
            self.last_metadata = result
            tabs = self.widgets.Tab(children=[self.widgets.HTML(self._dataframe_html(result['overlap'])), self.widgets.HTML(self._dataframe_html(result['metadata'])), self.widgets.HTML(self._dataframe_html(result['geometry']))])
            tabs.set_title(0, 'Overlap pairs')
            tabs.set_title(1, 'Image metadata')
            tabs.set_title(2, 'Stereo geometry')
            self.stage2_tables_box.children = (tabs,)
            self.stage2_summary.value = ("<div style='margin:10px 0;padding:10px;border-left:4px solid #2e7d32;background:#f4fbf4;'><b>✓ Metadata and geometry completed.</b><br><b>Prepared images analyzed:</b> <code>" + html.escape(str(result['work_dir'])) + "</code><br><b>Saved tables:</b> <code>" + html.escape(str(settings.metadata_dir)) + "</code><br><b>Detailed log:</b> <code>" + html.escape(str(result['log_path'])) + "</code></div>")
        except Exception as exc:
            try:
                settings = self._build_settings()
                log_path = settings.log_dir / 'metadata_geometry.log'
            except Exception:
                log_path = Path('(log path unavailable)')
            self.stage2_summary.value = ("<div style='margin:10px 0;padding:10px;border-left:4px solid #b00020;background:#fff4f4;'><b>✗ Metadata and geometry stopped.</b><br>" + html.escape(type(exc).__name__ + ': ' + str(exc)) + "<br><br><b>Detailed log:</b> <code>" + html.escape(str(log_path)) + "</code></div>")
        finally:
            self.run_stage2.disabled = False

    def display(self):
        self.display_fn(self.container)
        return self


def project_setup():
    return ProjectSetupUI().display()


# =====================================================================
# v0.5.1 CONTINUATION
# PRE-PROCESSING -> POINT CLOUD -> FINAL DSM
#
# IMPORTANT:
# The v0.5 Prepare data + Metadata and geometry implementation above
# is intentionally left unchanged.  The code below only appends the
# remaining tested workflow stages.
# =====================================================================

import shlex
import re
from dataclasses import dataclass
from matplotlib.colors import LightSource


# ============================================================
# PROCESSING SETTINGS
# ============================================================

@dataclass
class PreProcessingSettings:
    alignment_dem: str
    mapproject_dem: str

    target_epsg: int = 32632
    raw_resolution_m: float = 0.5
    preliminary_pair: str = "AC"

    # Exact defaults from the tested preprocessing notebook.
    ba_robust_threshold: float = 2.0
    ba_max_iterations: int = 500

    prelim_stereo_algorithm: str = "asp_bm"
    prelim_xcorr_threshold: float = 2.0
    prelim_cost_mode: int = 2
    prelim_corr_kernel: int = 35
    prelim_subpixel_kernel: int = 45
    prelim_subpixel_mode: int = 2

    corr_memory_limit_mb: int = 10240
    corr_tile_size: int = 3200

    prelim_dem_resolution_m: float = 1.0
    prelim_dem_nodata: float = -9999.0

    pc_align_max_displacement_m: float = 250.0
    pc_align_iterations: int = 100

    aligned_ba_threads: int = 18
    mapproject_threads: int = 18


@dataclass
class FinalProcessingSettings:
    stereo_mode: str = "dual"

    # Selected internal ASP view tags.
    single_pairs: Tuple[str, ...] = ("AC",)
    dual_configurations: Tuple[str, ...] = ("CAB",)

    # Presets: BM / SGM / MGM.
    # A custom ASP --stereo-algorithm value can also be entered.
    algorithm_tag: str = "MGM"
    custom_cost_mode: int = 4

    # Preset and/or user-entered linked CK:SK pairs.
    kernel_pairs: Tuple[Tuple[int, int], ...] = ((9, 21),)

    # Exact defaults used by the final reconstruction notebook.
    xcorr_threshold: float = 2.0
    corr_memory_limit_mb: int = 10240
    corr_tile_size: int = 3200
    subpixel_mode: int = 2

    pc_merge_threads: int = 18

    final_dsm_resolution_m: float = 1.0
    max_valid_triangulation_error_m: float = 1.0
    final_dsm_threads: int = 0
    final_dsm_nodata: float = -9999.0
    final_dsm_compression: str = "Deflate"
    create_error_image: bool = True


FINAL_ALGORITHMS = {
    "BM": {
        "asp_algorithm": "asp_bm",
        "cost_mode": 2,
        "kernel_pairs": (
            (5, 9),
            (7, 15),
            (9, 21),
            (15, 25),
            (25, 35),
            (35, 45),
        ),
    },
    "SGM": {
        "asp_algorithm": "asp_sgm",
        "cost_mode": 4,
        "kernel_pairs": (
            (5, 9),
            (7, 15),
            (9, 21),
        ),
    },
    "MGM": {
        "asp_algorithm": "asp_mgm",
        "cost_mode": 4,
        "kernel_pairs": (
            (5, 9),
            (7, 15),
            (9, 21),
        ),
    },
}


def _safe_algorithm_filename(value: str) -> str:
    """Create a filesystem-safe algorithm tag without changing the ASP value."""
    value = str(value).strip()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return safe or "custom_algorithm"


def _resolve_final_algorithm(final: FinalProcessingSettings):
    """
    Resolve tested algorithm presets or an explicitly entered custom
    ASP --stereo-algorithm value.

    Preset cost modes stay automatic:
        BM  -> 2
        SGM -> 4
        MGM -> 4

    For CUSTOM, the user-entered algorithm and cost mode are used.
    """
    raw = str(final.algorithm_tag).strip()

    if not raw:
        raise ValueError("Stereo algorithm cannot be empty.")

    preset = FINAL_ALGORITHMS.get(raw.upper())

    if preset is not None:
        return {
            "display_name": raw.upper(),
            "asp_algorithm": preset["asp_algorithm"],
            "cost_mode": preset["cost_mode"],
            "preset": True,
        }

    return {
        "display_name": raw,
        "asp_algorithm": raw,
        "cost_mode": int(final.custom_cost_mode),
        "preset": False,
    }


def _parse_custom_kernel_pairs(text: str) -> Tuple[Tuple[int, int], ...]:
    """
    Parse additional linked CK:SK pairs.

    Accepted examples:
        11:23
        11:23,13:27
        11:23 13:27
    """
    text = str(text or "").strip()
    if not text:
        return ()

    pairs = []
    for token in re.split(r"[,\s;]+", text):
        token = token.strip()
        if not token:
            continue

        if ":" not in token:
            raise ValueError(
                f"Invalid custom kernel pair '{token}'. "
                "Use CK:SK, for example 11:23."
            )

        ck_text, sk_text = token.split(":", 1)

        try:
            ck = int(ck_text)
            sk = int(sk_text)
        except Exception as exc:
            raise ValueError(
                f"Invalid custom kernel pair '{token}'. "
                "Both CK and SK must be integers."
            ) from exc

        if ck <= 0 or sk <= 0:
            raise ValueError(
                f"Invalid custom kernel pair '{token}'. "
                "CK and SK must be positive."
            )

        pairs.append((ck, sk))

    # Remove duplicates while preserving order.
    unique = []
    for pair in pairs:
        if pair not in unique:
            unique.append(pair)

    return tuple(unique)


def _raster_basic_summary(path: Path) -> dict:
    path = Path(path)
    with rasterio.open(path) as src:
        return {
            "File": str(path),
            "Width": int(src.width),
            "Height": int(src.height),
            "Bands": int(src.count),
            "CRS": src.crs.to_string() if src.crs else "",
            "Pixel X": abs(float(src.transform.a)),
            "Pixel Y": abs(float(src.transform.e)),
            "NoData": src.nodata,
        }


def _read_transform_matrix(transform_path: Path):
    transform_path = Path(transform_path)
    try:
        matrix = np.loadtxt(transform_path, dtype=float)
        if matrix.shape == (4, 4):
            return matrix
    except Exception:
        pass
    return None


def _parse_pc_align_original_results(
    log_path: Path,
    transform_path: Path,
):
    """
    Extract the important lines that the original pc_align notebook printed.
    The complete console output remains in the ASP log.
    """
    log_path = Path(log_path)
    text = log_path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    labels = [
        "Centroid of source points (Cartesian, meters)",
        "Centroid of source points (lat,lon,z)",
        "Translation vector (Cartesian, meters)",
        "Translation vector (North-East-Down, meters)",
        "Translation vector magnitude (meters)",
        (
            "Maximum displacement of points between the source cloud "
            "with any initial transform applied to it and the source "
            "cloud after alignment to the reference"
        ),
        "Translation vector (lat,lon,z)",
        "Transform scale - 1",
        "Euler angles (degrees)",
        "Euler angles (North-East-Down, degrees)",
        "Axis of rotation and angle (degrees)",
    ]

    extracted = []
    lines = text.splitlines()

    for label in labels:
        for line in reversed(lines):
            stripped = line.strip()
            if stripped.startswith(label):
                extracted.append(stripped)
                break

    matrix = _read_transform_matrix(
        transform_path
    )

    return {
        "important_lines": extracted,
        "matrix": matrix,
        "transform_path": Path(transform_path),
        "log_path": log_path,
    }


def _camera_adjustment_table(
    settings: ProjectSettings,
    paths: dict,
):
    rows = []

    for view in settings.image_names:
        stem = paths["images"][view].stem
        adjustment = Path(
            f"{paths['aligned_ba_prefix']}-{stem}.adjust"
        )

        rows.append(
            {
                "Image": view,
                "Status": "Created" if adjustment.is_file() else "Missing",
                "Aligned adjustment": str(adjustment),
            }
        )

    return pd.DataFrame(rows)


def _mapproject_output_table(
    settings: ProjectSettings,
    paths: dict,
):
    rows = []

    for view in settings.image_names:
        path = paths["mapprojected"][view]
        info = _raster_basic_summary(path)

        rows.append(
            {
                "Image": view,
                "Output": str(path),
                "Size": f"{info['Width']} × {info['Height']}",
                "CRS": info["CRS"],
                "Pixel size": (
                    f"{info['Pixel X']:g} × {info['Pixel Y']:g}"
                ),
            }
        )

    return pd.DataFrame(rows)


GEOMETRY_LABELS = {
    "AB": "FM — Forward–Middle",
    "AC": "FB — Forward–Backward",
    "BC": "MB — Middle–Backward",
    "ABC": "FMB — Forward–Middle–Backward",
    "BAC": "MFB — Middle–Forward–Backward",
    "CAB": "BFM — Backward–Forward–Middle",
    "ABACBC": "FMFBMB — merged AB + AC + BC",
}


# ============================================================
# PROCESSING PATHS
# ============================================================

def _processing_root(settings: ProjectSettings) -> Path:
    return settings.project_dir / (
        "cropped_data"
        if settings.crop_enabled
        else "full_data"
    )


def _processing_log_dir(settings: ProjectSettings) -> Path:
    return _processing_root(settings) / "asp_logs"


def _processing_output_dir(settings: ProjectSettings) -> Path:
    return _processing_root(settings) / "asp_out"


def _active_image_inputs(settings: ProjectSettings):
    """
    Reuse exactly the image selection already made by Prepare data.

    Crop OFF:
        merged_tiles/A.tif, B.tif, C.tif
        merged_tiles/A.XML, B.XML, C.XML

    Crop ON:
        merged_tiles/cropped_images/A_crop.tif, ...
        merged_tiles/cropped_images/A_crop.XML, ...
    """
    if settings.crop_enabled:
        images = {
            view: settings.cropped_dir / f"{view}_crop.tif"
            for view in settings.image_names
        }
        rpcs = {
            view: settings.cropped_dir / f"{view}_crop.XML"
            for view in settings.image_names
        }
    else:
        images = {
            view: settings.merged_dir / f"{view}.tif"
            for view in settings.image_names
        }
        rpcs = {
            view: settings.merged_dir / f"{view}.XML"
            for view in settings.image_names
        }

    _require_existing_files(*images.values(), *rpcs.values())
    return images, rpcs


def _dem_resolution_label(dem_path: Path) -> str:
    """
    Reproduce names such as baL50 from the tested map-projection workflow.
    """
    try:
        with rasterio.open(dem_path) as src:
            resolution = abs(float(src.transform.a))

        if resolution >= 1:
            text = str(int(round(resolution)))
        else:
            text = f"{resolution:g}".replace(".", "p")

        return f"L{text}"

    except Exception:
        return "DEM"


def _preprocessing_paths(
    settings: ProjectSettings,
    processing: PreProcessingSettings,
):
    images, rpcs = _active_image_inputs(settings)

    processing_root = _processing_root(settings)
    log_dir = _processing_log_dir(settings)
    asp_out = _processing_output_dir(settings)

    log_dir.mkdir(parents=True, exist_ok=True)
    asp_out.mkdir(parents=True, exist_ok=True)

    view_tag = "".join(settings.image_names)

    pair = processing.preliminary_pair.upper()

    if len(pair) != 2:
        raise ValueError("The preliminary pair must contain two views.")

    if any(view not in settings.image_names for view in pair):
        raise ValueError(
            f"Preliminary pair {pair} is not valid for "
            f"{settings.image_names}."
        )

    ba_prefix = (
        asp_out
        / f"ba_{view_tag}"
        / view_tag
    )

    prelim_stereo_dir = (
        asp_out
        / "dems"
        / f"stereo_preliminary_{pair}"
    )

    prelim_prefix = prelim_stereo_dir / "preliminary"
    prelim_point_cloud = Path(f"{prelim_prefix}-PC.tif")

    prelim_dem_prefix = (
        asp_out
        / "dems"
        / f"preliminary_{pair}"
    )
    prelim_dem = Path(f"{prelim_dem_prefix}-DEM.tif")

    align_prefix = (
        asp_out
        / "dems"
        / "align"
        / f"preliminary_{pair}_to_LiDAR"
    )
    align_transform = Path(f"{align_prefix}-transform.txt")

    aligned_ba_prefix = (
        asp_out
        / f"ba_aligned_{view_tag}"
        / view_tag
    )

    mapproject_dir = asp_out / "mapproject"

    map_label = _dem_resolution_label(
        Path(processing.mapproject_dem)
    )

    mapprojected = {
        view: (
            mapproject_dir
            / (
                f"{view}_{settings.project_name}_"
                f"{processing.raw_resolution_m:g}m_"
                f"ba{map_label}.tif"
            )
        )
        for view in settings.image_names
    }

    return {
        "processing_root": processing_root,
        "log_dir": log_dir,
        "asp_out": asp_out,
        "images": images,
        "rpcs": rpcs,
        "view_tag": view_tag,
        "pair": pair,
        "ba_prefix": ba_prefix,
        "ba_log": log_dir / f"bundle_adjust.{view_tag}.log",
        "prelim_stereo_dir": prelim_stereo_dir,
        "prelim_prefix": prelim_prefix,
        "prelim_point_cloud": prelim_point_cloud,
        "prelim_log": log_dir / f"stereo_preliminary.{pair}.log",
        "prelim_dem_prefix": prelim_dem_prefix,
        "prelim_dem": prelim_dem,
        "prelim_dem_log": log_dir / f"point2dem.preliminary_{pair}.log",
        "align_prefix": align_prefix,
        "align_transform": align_transform,
        "align_log": log_dir / f"pc_align.preliminary_{pair}_to_LiDAR.log",
        "aligned_ba_prefix": aligned_ba_prefix,
        "aligned_ba_log": log_dir / f"bundle_adjust.aligned_{view_tag}.log",
        "mapproject_dir": mapproject_dir,
        "mapprojected": mapprojected,
        "mapproject_logs": {
            view: log_dir / f"mapproject.{view}.log"
            for view in settings.image_names
        },
    }


def _save_processing_state(
    settings: ProjectSettings,
    section_name: str,
    payload: dict,
):
    """
    Save interface choices/results without changing the original v0.5
    project_settings.json format.
    """
    path = settings.project_dir / "processing_state.json"

    if path.exists():
        try:
            state = json.loads(
                path.read_text(encoding="utf-8")
            )
        except Exception:
            state = {}
    else:
        state = {}

    state[section_name] = payload

    path.write_text(
        json.dumps(state, indent=2),
        encoding="utf-8",
    )

    return path


# ============================================================
# COMMAND EXECUTION
# ============================================================

def _require_existing_files(*paths):
    missing = [
        str(Path(path))
        for path in paths
        if not Path(path).is_file()
    ]

    if missing:
        raise FileNotFoundError(
            "Required file(s) not found:\n"
            + "\n".join(missing)
        )


def _remove_path(path: Path):
    path = Path(path)

    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _prepare_output_prefix(
    prefix: Path,
    log_file: Path,
    overwrite: bool,
):
    """
    Python equivalent of the original prepare_output_prefix helper.
    """
    prefix = Path(prefix)
    log_file = Path(log_file)

    prefix.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # ASP prefixes may legitimately contain dots, for example the final
    # DSM prefix ending in "-1.0m".  Therefore do not use Path.suffix to
    # decide whether this is a file or a prefix.  Always remove everything
    # beginning with the requested ASP prefix, matching the original Bash
    # prepare_output_prefix behavior.
    matches = list(
        prefix.parent.glob(prefix.name + "*")
    )

    conflict = bool(matches) or log_file.exists()

    if not conflict:
        return

    if not overwrite:
        raise FileExistsError(
            "Outputs already exist for:\n"
            f"{prefix}\n\n"
            "Enable 'Overwrite existing outputs' to replace them."
        )

    for item in matches:
        _remove_path(item)

    if log_file.exists():
        log_file.unlink()


def _prepare_run_directory(
    run_directory: Path,
    log_file: Path,
    overwrite: bool,
):
    """
    Python equivalent of the original prepare_stereo_run helper.
    """
    run_directory = Path(run_directory)
    log_file = Path(log_file)

    if run_directory.exists() or log_file.exists():
        if not overwrite:
            raise FileExistsError(
                "Outputs already exist:\n"
                f"{run_directory}\n\n"
                "Enable 'Overwrite existing outputs' to replace them."
            )

        _remove_path(run_directory)

        if log_file.exists():
            log_file.unlink()

    run_directory.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)


def _run_asp_command(
    command: str,
    arguments: Sequence,
    log_file: Path,
    cwd: Path,
):
    """
    Execute ASP from Python and write the long console output to a log.

    The five installed workflow wrappers are used when available.
    For ASP tools such as pc_merge that are not currently exposed as a
    wrapper, the function falls back to the ASP installation managed by
    pleiades_asp_runner.
    """
    log_file = Path(log_file)
    cwd = Path(cwd)

    log_file.parent.mkdir(parents=True, exist_ok=True)
    cwd.mkdir(parents=True, exist_ok=True)

    arguments = list(arguments)

    with log_file.open("w", encoding="utf-8") as stream:
        stream.write(f"Command: {command}\n")
        stream.write("Arguments:\n")
        for value in arguments:
            stream.write(f"  {value}\n")
        stream.write("\n" + "=" * 80 + "\n")
        stream.flush()

        executable = shutil.which(command)

        if executable is not None:
            result = subprocess.run(
                [
                    executable,
                    *[str(value) for value in arguments],
                ],
                cwd=str(cwd),
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
            )

        else:
            try:
                from pleiades_asp_runner.installer import (
                    asp_bin_location,
                    is_windows,
                    _wsl_base,
                    wsl_execution_environment,
                )
                from pleiades_asp_runner.bridge import (
                    host_to_runtime_path,
                )

            except Exception as exc:
                raise RuntimeError(
                    f"ASP command '{command}' was not found on PATH "
                    "and the managed ASP installation could not be accessed."
                ) from exc

            asp_bin = asp_bin_location()

            if is_windows():
                runtime_cwd = host_to_runtime_path(cwd)

                runtime_args = []

                for value in arguments:
                    if isinstance(value, Path):
                        runtime_args.append(
                            host_to_runtime_path(value)
                        )
                    else:
                        runtime_args.append(str(value))

                env_args = [
                    f"{key}={value}"
                    for key, value in
                    wsl_execution_environment().items()
                ]

                cmd = [
                    *_wsl_base(),
                    "--cd",
                    runtime_cwd,
                    "--",
                    "env",
                    *env_args,
                    f"{asp_bin}/{command}",
                    *runtime_args,
                ]

                result = subprocess.run(
                    cmd,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                )

            else:
                managed_executable = (
                    Path(asp_bin) / command
                )

                if not managed_executable.is_file():
                    raise FileNotFoundError(
                        f"ASP executable not found:\n"
                        f"{managed_executable}"
                    )

                env = os.environ.copy()
                env["PATH"] = (
                    str(Path(asp_bin))
                    + os.pathsep
                    + env.get("PATH", "")
                )

                result = subprocess.run(
                    [
                        str(managed_executable),
                        *[str(value) for value in arguments],
                    ],
                    cwd=str(cwd),
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                )

        if result.returncode != 0:
            raise RuntimeError(
                f"{command} stopped with exit code "
                f"{result.returncode}.\n"
                f"See the detailed log:\n{log_file}"
            )


# ============================================================
# BUNDLE-ADJUSTMENT RESIDUALS
# Copied from the existing workflow logic, but receives paths
# directly instead of reading Metadata-Copy1.sh.
# ============================================================

def _parse_residual_statistics(filepath, active_views):
    filepath = Path(filepath)

    if not filepath.is_file():
        raise FileNotFoundError(
            f"Residual-statistics file not found:\n{filepath}"
        )

    records = []
    reading_camera_residuals = False

    with filepath.open(
        "r",
        encoding="utf-8",
        errors="replace",
    ) as stream:

        for raw_line in stream:
            line = raw_line.strip()

            if not line:
                continue

            if line.startswith(
                "Mean and median norm of residual error"
            ):
                reading_camera_residuals = True
                continue

            if line.startswith(
                "Camera weight position and orientation"
            ):
                break

            if not reading_camera_residuals:
                continue

            parts = [
                part.strip()
                for part in line.split(",")
            ]

            if len(parts) < 4:
                continue

            camera_name = Path(parts[0]).name

            match = re.fullmatch(
                r"([ABC])(?:_crop)?\.(?:XML|xml|tif|tiff)",
                camera_name,
            )

            if match is None:
                continue

            image_id = match.group(1).upper()

            if image_id not in active_views:
                continue

            records.append(
                {
                    "Image": image_id,
                    "Mean": float(parts[1]),
                    "Median": float(parts[2]),
                    "Count": int(float(parts[3])),
                }
            )

    if not records:
        raise ValueError(
            f"No camera residual rows were found in:\n{filepath}"
        )

    return (
        pd.DataFrame(records)
        .set_index("Image")
        .reindex(active_views)
    )


def _bundle_adjustment_residual_summary(
    ba_prefix: Path,
    active_views,
):
    initial = _parse_residual_statistics(
        f"{ba_prefix}-initial_residuals_stats.txt",
        active_views,
    )

    final = _parse_residual_statistics(
        f"{ba_prefix}-final_residuals_stats.txt",
        active_views,
    )

    summary = pd.DataFrame(
        {
            "Initial mean (px)": initial["Mean"],
            "Final mean (px)": final["Mean"],
            "Initial median (px)": initial["Median"],
            "Final median (px)": final["Median"],
            "Point count": final["Count"],
        }
    )

    summary.index.name = "Image"

    return summary.round(
        {
            "Initial mean (px)": 4,
            "Final mean (px)": 4,
            "Initial median (px)": 4,
            "Final median (px)": 4,
            "Point count": 0,
        }
    )


# ============================================================
# PRE-PROCESSING FIGURES
# ============================================================

def _read_dem_preview(
    raster_path: Path,
    max_display_size=1800,
):
    raster_path = Path(raster_path)

    with rasterio.open(raster_path) as src:
        scale = min(
            1.0,
            max_display_size
            / max(src.width, src.height),
        )

        width = max(
            1,
            int(round(src.width * scale)),
        )
        height = max(
            1,
            int(round(src.height * scale)),
        )

        array = src.read(
            1,
            out_shape=(height, width),
            resampling=Resampling.bilinear,
            masked=True,
        ).astype(np.float32)

        data = np.asarray(
            array.data,
            dtype=np.float32,
        )

        mask = (
            np.ma.getmaskarray(array)
            | ~np.isfinite(data)
        )

        if src.nodata is not None:
            mask |= np.isclose(data, src.nodata)

        array = np.ma.array(
            data,
            mask=mask,
        )

        bounds = src.bounds
        crs = src.crs

    return array, bounds, crs


def _plot_preliminary_dem_from_path(
    settings: ProjectSettings,
    dem_path: Path,
    pair: str,
):
    dem, bounds, crs = _read_dem_preview(dem_path)

    valid = dem.compressed()

    if valid.size == 0:
        raise ValueError(
            f"The preliminary DEM has no valid values:\n{dem_path}"
        )

    vmin, vmax = np.percentile(valid, [2, 98])

    filled = dem.filled(
        float(np.median(valid))
    )

    hillshade = LightSource(
        azdeg=315,
        altdeg=45,
    ).hillshade(
        filled,
        vert_exag=1.0,
    )

    hillshade = np.ma.array(
        hillshade,
        mask=np.ma.getmaskarray(dem),
    )

    extent = [
        bounds.left,
        bounds.right,
        bounds.bottom,
        bounds.top,
    ]

    fig, ax = plt.subplots(
        figsize=(10, 8)
    )

    plot = ax.imshow(
        dem,
        extent=extent,
        origin="upper",
        cmap="terrain",
        vmin=vmin,
        vmax=vmax,
    )

    ax.imshow(
        hillshade,
        extent=extent,
        origin="upper",
        cmap="gray",
        alpha=0.30,
    )

    colorbar = fig.colorbar(
        plot,
        ax=ax,
        shrink=0.82,
        pad=0.025,
    )
    colorbar.set_label("Elevation (m)")

    ax.set_title(
        f"{settings.project_name} — "
        f"Preliminary {pair} alignment DEM"
    )

    if crs is not None and crs.is_geographic:
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
    else:
        ax.set_xlabel("Easting (m)")
        ax.set_ylabel("Northing (m)")

    ax.set_aspect("equal")
    ax.grid(
        True,
        color="white",
        alpha=0.22,
        linewidth=0.5,
        linestyle="--",
    )

    fig.tight_layout()

    settings.figure_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    png = (
        settings.figure_dir
        / f"preliminary_{pair}_alignment_DEM.png"
    )
    pdf = (
        settings.figure_dir
        / f"preliminary_{pair}_alignment_DEM.pdf"
    )

    fig.savefig(
        png,
        dpi=200,
        bbox_inches="tight",
    )
    fig.savefig(
        pdf,
        dpi=300,
        bbox_inches="tight",
    )

    return {
        "figure": fig,
        "png": png,
        "pdf": pdf,
    }


def _read_map_image_preview(
    image_path: Path,
    max_display_size=1600,
):
    image_path = Path(image_path)

    with rasterio.open(image_path) as src:
        scale = min(
            1.0,
            max_display_size
            / max(src.width, src.height),
        )

        width = max(
            1,
            int(round(src.width * scale)),
        )
        height = max(
            1,
            int(round(src.height * scale)),
        )

        image = src.read(
            1,
            out_shape=(height, width),
            resampling=Resampling.bilinear,
            masked=True,
        ).astype(np.float32)

        data = np.asarray(
            image.data,
            dtype=np.float32,
        )

        mask = (
            np.ma.getmaskarray(image)
            | ~np.isfinite(data)
            | np.isclose(data, 0)
        )

        if src.nodata is not None:
            mask |= np.isclose(
                data,
                src.nodata,
            )

        valid = data[~mask]

        if valid.size:
            low, high = np.percentile(
                valid,
                [2, 98],
            )

            if high <= low:
                high = low + 1.0

            data = np.clip(
                (data - low) / (high - low),
                0,
                1,
            )

        array = np.ma.array(
            data,
            mask=mask,
        )

        bounds = src.bounds
        crs = src.crs

    return {
        "array": array,
        "bounds": bounds,
        "crs": crs,
    }


def _plot_mapprojected_from_paths(
    settings: ProjectSettings,
    mapprojected: Dict[str, Path],
):
    """
    Plot all map-projected images using:
    - one common spatial extent,
    - a shared y-axis,
    - plain coordinate labels (no 1e6 scientific offset),
    - fewer, readable x ticks.
    """
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    prepared = [
        (
            view,
            _read_map_image_preview(
                mapprojected[view]
            ),
        )
        for view in settings.image_names
    ]

    all_left = [
        item["bounds"].left
        for _, item in prepared
    ]
    all_right = [
        item["bounds"].right
        for _, item in prepared
    ]
    all_bottom = [
        item["bounds"].bottom
        for _, item in prepared
    ]
    all_top = [
        item["bounds"].top
        for _, item in prepared
    ]

    common_extent = [
        min(all_left),
        max(all_right),
        min(all_bottom),
        max(all_top),
    ]

    fig, axes = plt.subplots(
        1,
        len(prepared),
        figsize=(5.2 * len(prepared), 6.4),
        squeeze=False,
        sharey=True,
    )
    axes = axes.ravel()

    cmap = plt.get_cmap("gray").copy()
    cmap.set_bad("white")

    first_crs = prepared[0][1]["crs"]
    is_geographic = (
        first_crs is not None
        and first_crs.is_geographic
    )

    if is_geographic:
        coordinate_formatter = FuncFormatter(
            lambda value, position: f"{value:.4f}"
        )
        common_x_label = "Longitude"
        common_y_label = "Latitude"
    else:
        coordinate_formatter = FuncFormatter(
            lambda value, position: f"{value:,.0f}"
        )
        common_x_label = "Easting (m)"
        common_y_label = "Northing (m)"

    for ax, (view, item) in zip(
        axes,
        prepared,
    ):
        bounds = item["bounds"]

        extent = [
            bounds.left,
            bounds.right,
            bounds.bottom,
            bounds.top,
        ]

        ax.imshow(
            item["array"],
            extent=extent,
            origin="upper",
            cmap=cmap,
        )

        ax.set_xlim(
            common_extent[0],
            common_extent[1],
        )
        ax.set_ylim(
            common_extent[2],
            common_extent[3],
        )

        ax.set_title(
            f"{view} — map-projected"
        )

        ax.grid(
            True,
            color="white",
            alpha=0.22,
            linewidth=0.5,
            linestyle="--",
        )

        # Keep only a few coordinate labels so they do not overlap.
        ax.xaxis.set_major_locator(
            MaxNLocator(nbins=5)
        )
        ax.yaxis.set_major_locator(
            MaxNLocator(nbins=6)
        )

        ax.xaxis.set_major_formatter(
            coordinate_formatter
        )
        ax.yaxis.set_major_formatter(
            coordinate_formatter
        )

        ax.tick_params(
            axis="x",
            labelrotation=30,
            labelsize=8.5,
        )
        ax.tick_params(
            axis="y",
            labelsize=8.5,
        )

        # Shared labels are added once below.
        ax.set_xlabel("")
        ax.set_ylabel("")

    fig.supxlabel(
        common_x_label,
        y=0.035,
    )
    fig.supylabel(
        common_y_label,
        x=0.015,
    )

    fig.suptitle(
        f"{settings.project_name} — "
        "map-projected image alignment"
    )

    fig.tight_layout(
        rect=(0.035, 0.065, 1, 0.96)
    )

    png = (
        settings.figure_dir
        / "mapprojected_images_preview.png"
    )
    pdf = (
        settings.figure_dir
        / "mapprojected_images_preview.pdf"
    )

    fig.savefig(
        png,
        dpi=200,
        bbox_inches="tight",
    )
    fig.savefig(
        pdf,
        dpi=300,
        bbox_inches="tight",
    )

    return {
        "figure": fig,
        "png": png,
        "pdf": pdf,
    }


# ============================================================
# RUN PRE-PROCESSING
# Exact command sequence from notebook 05.
# ============================================================

def run_pre_processing(
    settings: ProjectSettings,
    processing: PreProcessingSettings,
    progress_callback=None,
    result_callback=None,
):
    """
    Run the original pre-processing sequence and emit a compact result
    after every stage so the Jupyter tabs can be inspected while the
    next ASP command is running.
    """
    create_project_folders(settings)

    alignment_dem = Path(
        processing.alignment_dem
    ).expanduser()

    mapproject_dem = Path(
        processing.mapproject_dem
    ).expanduser()

    _require_existing_files(
        alignment_dem,
        mapproject_dem,
    )

    paths = _preprocessing_paths(
        settings,
        processing,
    )

    summary_log = (
        settings.log_dir
        / "pre_processing.log"
    )

    current_stage = None
    current_log = None

    def emit(stage, status, payload=None):
        if result_callback is not None:
            result_callback(
                stage,
                status,
                payload or {},
            )

    with WorkflowLog(summary_log) as summary:
        try:
            images = paths["images"]
            rpcs = paths["rpcs"]

            # ------------------------------------------------
            # 1. BUNDLE ADJUSTMENT
            # ------------------------------------------------
            current_stage = "bundle_adjustment"
            current_log = paths["ba_log"]

            if progress_callback:
                progress_callback(
                    5,
                    "Bundle adjustment",
                )

            emit(
                current_stage,
                "running",
                {
                    "log": paths["ba_log"],
                },
            )

            _prepare_output_prefix(
                paths["ba_prefix"],
                paths["ba_log"],
                settings.overwrite,
            )

            _run_asp_command(
                "bundle_adjust",
                [
                    "-t",
                    "rpc",
                    *[
                        images[view]
                        for view in settings.image_names
                    ],
                    *[
                        rpcs[view]
                        for view in settings.image_names
                    ],
                    "--cost-function",
                    "Cauchy",
                    "--robust-threshold",
                    processing.ba_robust_threshold,
                    "--max-iterations",
                    processing.ba_max_iterations,
                    "--datum",
                    "WGS84",
                    "--threads",
                    0,
                    "--tif-compress",
                    "Deflate",
                    "-o",
                    paths["ba_prefix"],
                ],
                paths["ba_log"],
                paths["processing_root"],
            )

            residual_summary = (
                _bundle_adjustment_residual_summary(
                    paths["ba_prefix"],
                    settings.image_names,
                )
            )

            residual_csv = (
                settings.metadata_dir
                / "bundle_adjustment_residuals.csv"
            )

            residual_summary.to_csv(
                residual_csv
            )

            emit(
                current_stage,
                "completed",
                {
                    "table": residual_summary,
                    "csv": residual_csv,
                    "prefix": paths["ba_prefix"],
                    "log": paths["ba_log"],
                },
            )

            summary.write(
                f"Bundle adjustment completed: "
                f"{paths['ba_prefix']}"
            )

            # ------------------------------------------------
            # 2. INITIAL STEREO CORRELATION
            # ------------------------------------------------
            current_stage = "preliminary_stereo"
            current_log = paths["prelim_log"]

            if progress_callback:
                progress_callback(
                    25,
                    (
                        "Preliminary stereo "
                        f"{paths['pair']}"
                    ),
                )

            left = paths["pair"][0]
            right = paths["pair"][1]

            emit(
                current_stage,
                "running",
                {
                    "pair": paths["pair"],
                    "left": images[left],
                    "right": images[right],
                    "prefix": paths["prelim_prefix"],
                    "algorithm": processing.prelim_stereo_algorithm,
                    "cost_mode": processing.prelim_cost_mode,
                    "corr_kernel": processing.prelim_corr_kernel,
                    "subpixel_kernel": processing.prelim_subpixel_kernel,
                    "xcorr_threshold": processing.prelim_xcorr_threshold,
                    "subpixel_mode": processing.prelim_subpixel_mode,
                    "log": paths["prelim_log"],
                },
            )

            _prepare_run_directory(
                paths["prelim_stereo_dir"],
                paths["prelim_log"],
                settings.overwrite,
            )

            _run_asp_command(
                "parallel_stereo",
                [
                    "-t",
                    "rpc",
                    "--stereo-algorithm",
                    processing.prelim_stereo_algorithm,
                    "--xcorr-threshold",
                    processing.prelim_xcorr_threshold,
                    "--cost-mode",
                    processing.prelim_cost_mode,
                    "--corr-kernel",
                    processing.prelim_corr_kernel,
                    processing.prelim_corr_kernel,
                    "--subpixel-kernel",
                    processing.prelim_subpixel_kernel,
                    processing.prelim_subpixel_kernel,
                    "--corr-tile-size",
                    processing.corr_tile_size,
                    "--corr-memory-limit-mb",
                    processing.corr_memory_limit_mb,
                    "--subpixel-mode",
                    processing.prelim_subpixel_mode,
                    "--bundle-adjust-prefix",
                    paths["ba_prefix"],
                    images[left],
                    images[right],
                    rpcs[left],
                    rpcs[right],
                    paths["prelim_prefix"],
                ],
                paths["prelim_log"],
                paths["processing_root"],
            )

            _require_existing_files(
                paths["prelim_point_cloud"]
            )

            point_cloud_info = _raster_basic_summary(
                paths["prelim_point_cloud"]
            )

            emit(
                current_stage,
                "completed",
                {
                    "pair": paths["pair"],
                    "left": images[left],
                    "right": images[right],
                    "prefix": paths["prelim_prefix"],
                    "algorithm": processing.prelim_stereo_algorithm,
                    "cost_mode": processing.prelim_cost_mode,
                    "corr_kernel": processing.prelim_corr_kernel,
                    "subpixel_kernel": processing.prelim_subpixel_kernel,
                    "xcorr_threshold": processing.prelim_xcorr_threshold,
                    "subpixel_mode": processing.prelim_subpixel_mode,
                    "point_cloud": paths["prelim_point_cloud"],
                    "point_cloud_info": point_cloud_info,
                    "log": paths["prelim_log"],
                },
            )

            summary.write(
                f"Preliminary point cloud: "
                f"{paths['prelim_point_cloud']}"
            )

            # ------------------------------------------------
            # 3. PRELIMINARY ALIGNMENT DEM
            # ------------------------------------------------
            current_stage = "preliminary_dem"
            current_log = paths["prelim_dem_log"]

            if progress_callback:
                progress_callback(
                    45,
                    "Preliminary alignment DEM",
                )

            emit(
                current_stage,
                "running",
                {
                    "point_cloud": paths["prelim_point_cloud"],
                    "dem": paths["prelim_dem"],
                    "log": paths["prelim_dem_log"],
                },
            )

            _prepare_output_prefix(
                paths["prelim_dem_prefix"],
                paths["prelim_dem_log"],
                settings.overwrite,
            )

            _run_asp_command(
                "point2dem",
                [
                    "--t_srs",
                    f"EPSG:{processing.target_epsg}",
                    "--tr",
                    processing.prelim_dem_resolution_m,
                    "--threads",
                    0,
                    "--nodata-value",
                    processing.prelim_dem_nodata,
                    paths["prelim_point_cloud"],
                    "-o",
                    paths["prelim_dem_prefix"],
                ],
                paths["prelim_dem_log"],
                paths["processing_root"],
            )

            _require_existing_files(
                paths["prelim_dem"]
            )

            prelim_plot = (
                _plot_preliminary_dem_from_path(
                    settings,
                    paths["prelim_dem"],
                    paths["pair"],
                )
            )

            dem_info = _raster_basic_summary(
                paths["prelim_dem"]
            )

            emit(
                current_stage,
                "completed",
                {
                    "point_cloud": paths["prelim_point_cloud"],
                    "dem": paths["prelim_dem"],
                    "dem_info": dem_info,
                    "plot": prelim_plot,
                    "log": paths["prelim_dem_log"],
                },
            )

            summary.write(
                f"Preliminary DEM: "
                f"{paths['prelim_dem']}"
            )

            # ------------------------------------------------
            # 4. pc_align TO HIGH-RESOLUTION LiDAR
            # ------------------------------------------------
            current_stage = "lidar_alignment"
            current_log = paths["align_log"]

            if progress_callback:
                progress_callback(
                    60,
                    "Aligning preliminary DEM to LiDAR",
                )

            emit(
                current_stage,
                "running",
                {
                    "reference": alignment_dem,
                    "source": paths["prelim_dem"],
                    "transform": paths["align_transform"],
                    "log": paths["align_log"],
                },
            )

            _prepare_output_prefix(
                paths["align_prefix"],
                paths["align_log"],
                settings.overwrite,
            )

            _run_asp_command(
                "pc_align",
                [
                    "--max-displacement",
                    processing.pc_align_max_displacement_m,
                    "--num-iterations",
                    processing.pc_align_iterations,
                    alignment_dem,
                    paths["prelim_dem"],
                    "-o",
                    paths["align_prefix"],
                ],
                paths["align_log"],
                paths["processing_root"],
            )

            _require_existing_files(
                paths["align_transform"]
            )

            alignment_results = (
                _parse_pc_align_original_results(
                    paths["align_log"],
                    paths["align_transform"],
                )
            )

            emit(
                current_stage,
                "completed",
                {
                    "reference": alignment_dem,
                    "source": paths["prelim_dem"],
                    "transform": paths["align_transform"],
                    "alignment_results": alignment_results,
                    "log": paths["align_log"],
                },
            )

            summary.write(
                f"Alignment transform: "
                f"{paths['align_transform']}"
            )

            # ------------------------------------------------
            # 5. APPLY TRANSFORM TO ALL CAMERA MODELS
            # ------------------------------------------------
            current_stage = "camera_transform"
            current_log = paths["aligned_ba_log"]

            if progress_callback:
                progress_callback(
                    72,
                    "Applying alignment transform to cameras",
                )

            emit(
                current_stage,
                "running",
                {
                    "transform": paths["align_transform"],
                    "prefix": paths["aligned_ba_prefix"],
                    "log": paths["aligned_ba_log"],
                },
            )

            _prepare_output_prefix(
                paths["aligned_ba_prefix"],
                paths["aligned_ba_log"],
                settings.overwrite,
            )

            _run_asp_command(
                "bundle_adjust",
                [
                    "-t",
                    "rpc",
                    *[
                        images[view]
                        for view in settings.image_names
                    ],
                    *[
                        rpcs[view]
                        for view in settings.image_names
                    ],
                    "--initial-transform",
                    paths["align_transform"],
                    "--input-adjustments-prefix",
                    paths["ba_prefix"],
                    "--apply-initial-transform-only",
                    "--datum",
                    "WGS84",
                    "--threads",
                    processing.aligned_ba_threads,
                    "--tif-compress",
                    "Deflate",
                    "-o",
                    paths["aligned_ba_prefix"],
                ],
                paths["aligned_ba_log"],
                paths["processing_root"],
            )

            for view in settings.image_names:
                stem = images[view].stem

                _require_existing_files(
                    Path(
                        f"{paths['aligned_ba_prefix']}-"
                        f"{stem}.adjust"
                    )
                )

            camera_table = _camera_adjustment_table(
                settings,
                paths,
            )

            emit(
                current_stage,
                "completed",
                {
                    "table": camera_table,
                    "transform": paths["align_transform"],
                    "prefix": paths["aligned_ba_prefix"],
                    "log": paths["aligned_ba_log"],
                },
            )

            # ------------------------------------------------
            # 6. MAP PROJECT ALL ACTIVE IMAGES
            # ------------------------------------------------
            current_stage = "map_projection"
            current_log = None

            if progress_callback:
                progress_callback(
                    82,
                    "Map-projecting active images",
                )

            emit(
                current_stage,
                "running",
                {
                    "mapproject_dem": mapproject_dem,
                    "outputs": paths["mapprojected"],
                    "logs": paths["mapproject_logs"],
                },
            )

            paths["mapproject_dir"].mkdir(
                parents=True,
                exist_ok=True,
            )

            for index, view in enumerate(
                settings.image_names,
                start=1,
            ):
                output_image = (
                    paths["mapprojected"][view]
                )

                current_log = (
                    paths["mapproject_logs"][view]
                )

                _prepare_output_prefix(
                    output_image,
                    current_log,
                    settings.overwrite,
                )

                _run_asp_command(
                    "mapproject",
                    [
                        "-t",
                        "rpc",
                        "--threads",
                        processing.mapproject_threads,
                        "--tr",
                        processing.raw_resolution_m,
                        "--t_srs",
                        f"EPSG:{processing.target_epsg}",
                        "--bundle-adjust-prefix",
                        paths["aligned_ba_prefix"],
                        "--tif-compress",
                        "Deflate",
                        mapproject_dem,
                        images[view],
                        rpcs[view],
                        output_image,
                    ],
                    current_log,
                    paths["processing_root"],
                )

                _require_existing_files(
                    output_image
                )

                if progress_callback:
                    progress_callback(
                        82
                        + int(
                            index
                            / len(settings.image_names)
                            * 14
                        ),
                        f"Map-projected {view}",
                    )

            map_plot = (
                _plot_mapprojected_from_paths(
                    settings,
                    paths["mapprojected"],
                )
            )

            map_table = _mapproject_output_table(
                settings,
                paths,
            )

            emit(
                current_stage,
                "completed",
                {
                    "table": map_table,
                    "plot": map_plot,
                    "mapproject_dem": mapproject_dem,
                    "outputs": paths["mapprojected"],
                    "logs": paths["mapproject_logs"],
                },
            )

            state_path = _save_processing_state(
                settings,
                "pre_processing",
                {
                    "alignment_dem": str(
                        alignment_dem
                    ),
                    "mapproject_dem": str(
                        mapproject_dem
                    ),
                    "target_epsg": (
                        processing.target_epsg
                    ),
                    "raw_resolution_m": (
                        processing.raw_resolution_m
                    ),
                    "preliminary_pair": (
                        paths["pair"]
                    ),
                    "preliminary_stereo_algorithm": (
                        processing.prelim_stereo_algorithm
                    ),
                    "preliminary_cost_mode": (
                        processing.prelim_cost_mode
                    ),
                    "preliminary_corr_kernel": (
                        processing.prelim_corr_kernel
                    ),
                    "preliminary_subpixel_kernel": (
                        processing.prelim_subpixel_kernel
                    ),
                    "preliminary_xcorr_threshold": (
                        processing.prelim_xcorr_threshold
                    ),
                    "preliminary_subpixel_mode": (
                        processing.prelim_subpixel_mode
                    ),
                    "ba_prefix": str(
                        paths["ba_prefix"]
                    ),
                    "preliminary_point_cloud": str(
                        paths["prelim_point_cloud"]
                    ),
                    "preliminary_dem": str(
                        paths["prelim_dem"]
                    ),
                    "alignment_transform": str(
                        paths["align_transform"]
                    ),
                    "aligned_ba_prefix": str(
                        paths["aligned_ba_prefix"]
                    ),
                    "mapprojected_images": {
                        view: str(
                            paths["mapprojected"][view]
                        )
                        for view
                        in settings.image_names
                    },
                },
            )

            if progress_callback:
                progress_callback(
                    100,
                    "Pre-processing completed",
                )

            return {
                "paths": paths,
                "residual_summary": residual_summary,
                "residual_csv": residual_csv,
                "preliminary_plot": prelim_plot,
                "alignment_results": alignment_results,
                "camera_table": camera_table,
                "map_table": map_table,
                "map_plot": map_plot,
                "summary_log": summary_log,
                "state_path": state_path,
            }

        except Exception as exc:
            summary.write("")
            summary.write("ERROR")
            summary.write(
                traceback.format_exc()
            )

            if current_stage is not None:
                emit(
                    current_stage,
                    "failed",
                    {
                        "error": (
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        ),
                        "log": current_log,
                    },
                )

            raise


# ============================================================
# FINAL POINT-CLOUD PATHS AND SELECTION
# ============================================================

def _normalize_internal_tag(tag: str) -> str:
    return (
        str(tag)
        .replace(" ", "")
        .replace(",", "")
        .upper()
    )


def _validate_final_selection(
    settings: ProjectSettings,
    final: FinalProcessingSettings,
):
    # Resolve the algorithm here so invalid empty custom values fail early.
    _resolve_final_algorithm(final)

    if not final.kernel_pairs:
        raise ValueError(
            "Select or enter at least one linked CK:SK kernel pair."
        )

    for pair in final.kernel_pairs:
        if (
            len(pair) != 2
            or int(pair[0]) <= 0
            or int(pair[1]) <= 0
        ):
            raise ValueError(
                f"Invalid CK:SK pair: {pair}. "
                "Both values must be positive integers."
            )

    if final.stereo_mode == "single":
        pairs = tuple(
            _normalize_internal_tag(tag)
            for tag in final.single_pairs
        )

        if not pairs:
            raise ValueError(
                "Select at least one stereo pair."
            )

        for pair in pairs:
            if (
                len(pair) != 2
                or pair[0] == pair[1]
                or any(
                    view not in settings.image_names
                    for view in pair
                )
            ):
                raise ValueError(
                    f"Invalid stereo pair: {pair}"
                )

        return {
            "single_pairs": pairs,
            "dual_configurations": (),
            "tri_merge_required": False,
        }

    if final.stereo_mode == "dual":
        if settings.acquisition_mode != "tri_stereo":
            raise ValueError(
                "Ordered three-image configurations "
                "require tri-stereo A/B/C."
            )

        configurations = tuple(
            _normalize_internal_tag(tag)
            for tag in final.dual_configurations
        )

        if not configurations:
            raise ValueError(
                "Select at least one ordered "
                "three-image configuration."
            )

        for config in configurations:
            if (
                len(config) != 3
                or set(config) != {"A", "B", "C"}
            ):
                raise ValueError(
                    f"Invalid ordered configuration: "
                    f"{config}"
                )

        return {
            "single_pairs": (),
            "dual_configurations": configurations,
            "tri_merge_required": False,
        }

    if final.stereo_mode == "tri":
        if settings.acquisition_mode != "tri_stereo":
            raise ValueError(
                "Tri merge requires A/B/C."
            )

        return {
            "single_pairs": ("AB", "AC", "BC"),
            "dual_configurations": (),
            "tri_merge_required": True,
        }

    raise ValueError(
        f"Unsupported stereo mode: "
        f"{final.stereo_mode}"
    )


def _final_common_paths(
    settings: ProjectSettings,
    processing: PreProcessingSettings,
):
    pre_paths = _preprocessing_paths(
        settings,
        processing,
    )

    _require_existing_files(
        Path(processing.mapproject_dem),
        *[
            pre_paths["mapprojected"][view]
            for view in settings.image_names
        ],
    )

    for view in settings.image_names:
        stem = pre_paths["images"][view].stem

        _require_existing_files(
            Path(
                f"{pre_paths['aligned_ba_prefix']}-"
                f"{stem}.adjust"
            )
        )

    return pre_paths


def _configuration_views(tag: str):
    return list(tag)


def _point_cloud_output(
    settings: ProjectSettings,
    final: FinalProcessingSettings,
    config_tag: str,
    ck: int,
    sk: int,
):
    asp_out = _processing_output_dir(settings)

    algorithm_file_tag = _safe_algorithm_filename(
        _resolve_final_algorithm(final)["display_name"]
    )

    outname = (
        f"{settings.project_name}_"
        f"{algorithm_file_tag}_"
        f"ck{ck}_sk{sk}"
    )

    run_directory = (
        asp_out
        / "point_clouds"
        / f"stereo_{config_tag}_{outname}"
    )

    run_prefix = (
        run_directory
        / f"{config_tag}_{outname}"
    )

    point_cloud = Path(
        f"{run_prefix}-PC.tif"
    )

    log_file = (
        _processing_log_dir(settings)
        / f"stereo.{config_tag}_{outname}.log"
    )

    return {
        "outname": outname,
        "run_directory": run_directory,
        "run_prefix": run_prefix,
        "point_cloud": point_cloud,
        "log_file": log_file,
    }


def _run_one_final_configuration(
    settings: ProjectSettings,
    processing: PreProcessingSettings,
    final: FinalProcessingSettings,
    pre_paths: dict,
    config_tag: str,
    ck: int,
    sk: int,
):
    config_tag = _normalize_internal_tag(
        config_tag
    )

    views = _configuration_views(
        config_tag
    )

    if len(views) not in (2, 3):
        raise ValueError(
            f"Invalid stereo configuration: "
            f"{config_tag}"
        )

    map_images = [
        pre_paths["mapprojected"][view]
        for view in views
    ]

    rpc_cameras = [
        pre_paths["rpcs"][view]
        for view in views
    ]

    _require_existing_files(
        Path(processing.mapproject_dem),
        *map_images,
        *rpc_cameras,
    )

    product_paths = _point_cloud_output(
        settings,
        final,
        config_tag,
        ck,
        sk,
    )

    _prepare_run_directory(
        product_paths["run_directory"],
        product_paths["log_file"],
        settings.overwrite,
    )

    algorithm = _resolve_final_algorithm(final)

    _run_asp_command(
        "parallel_stereo",
        [
            "-t",
            "rpc",
            "--alignment-method",
            "none",
            "--stereo-algorithm",
            algorithm["asp_algorithm"],
            "--xcorr-threshold",
            final.xcorr_threshold,
            "--cost-mode",
            algorithm["cost_mode"],
            "--corr-kernel",
            ck,
            ck,
            "--subpixel-kernel",
            sk,
            sk,
            "--corr-tile-size",
            final.corr_tile_size,
            "--corr-memory-limit-mb",
            final.corr_memory_limit_mb,
            "--subpixel-mode",
            final.subpixel_mode,
            "--bundle-adjust-prefix",
            pre_paths["aligned_ba_prefix"],
            *map_images,
            *rpc_cameras,
            product_paths["run_prefix"],
            Path(processing.mapproject_dem),
        ],
        product_paths["log_file"],
        _processing_root(settings),
    )

    _require_existing_files(
        product_paths["point_cloud"]
    )

    return {
        "product_tag": config_tag,
        "geometry": GEOMETRY_LABELS.get(
            config_tag,
            config_tag,
        ),
        "algorithm": _resolve_final_algorithm(final)["display_name"],
        "correlation_kernel": ck,
        "subpixel_kernel": sk,
        "point_cloud": (
            product_paths["point_cloud"]
        ),
        "log": product_paths["log_file"],
    }


def _merge_tri_point_clouds(
    settings: ProjectSettings,
    final: FinalProcessingSettings,
    pair_products: Sequence[dict],
    ck: int,
    sk: int,
):
    selected = {
        item["product_tag"]: item["point_cloud"]
        for item in pair_products
        if (
            item["correlation_kernel"] == ck
            and item["subpixel_kernel"] == sk
        )
    }

    for tag in ("AB", "AC", "BC"):
        if tag not in selected:
            raise RuntimeError(
                f"Cannot merge tri-stereo point "
                f"clouds because {tag} is missing."
            )

    algorithm_file_tag = _safe_algorithm_filename(
        _resolve_final_algorithm(final)["display_name"]
    )

    outname = (
        f"{settings.project_name}_"
        f"{algorithm_file_tag}_"
        f"ck{ck}_sk{sk}"
    )

    merge_tag = "ABACBC"

    merged_directory = (
        _processing_output_dir(settings)
        / "point_clouds_merged"
        / f"merged_{merge_tag}_{outname}"
    )

    merged_point_cloud = (
        merged_directory
        / f"{merge_tag}_{outname}-PC.tif"
    )

    merge_log = (
        _processing_log_dir(settings)
        / f"pc_merge.{merge_tag}_{outname}.log"
    )

    _prepare_run_directory(
        merged_directory,
        merge_log,
        settings.overwrite,
    )

    _run_asp_command(
        "pc_merge",
        [
            selected["AB"],
            selected["AC"],
            selected["BC"],
            "-o",
            merged_point_cloud,
            "--threads",
            final.pc_merge_threads,
            "--tif-compress",
            "LZW",
        ],
        merge_log,
        _processing_root(settings),
    )

    _require_existing_files(
        merged_point_cloud
    )

    return {
        "product_tag": merge_tag,
        "geometry": GEOMETRY_LABELS[
            merge_tag
        ],
        "algorithm": _resolve_final_algorithm(final)["display_name"],
        "correlation_kernel": ck,
        "subpixel_kernel": sk,
        "point_cloud": merged_point_cloud,
        "log": merge_log,
    }


# ============================================================
# RUN FINAL POINT-CLOUD RECONSTRUCTION
# Exact logic from notebook 06:
# single / dual / tri and pc_merge for AB+AC+BC.
# ============================================================

def run_point_cloud_reconstruction(
    settings: ProjectSettings,
    processing: PreProcessingSettings,
    final: FinalProcessingSettings,
    progress_callback=None,
):
    selection = _validate_final_selection(
        settings,
        final,
    )

    pre_paths = _final_common_paths(
        settings,
        processing,
    )

    summary_log = (
        settings.log_dir
        / "point_cloud_reconstruction.log"
    )

    products = []

    with WorkflowLog(summary_log) as summary:
        try:
            configs = []

            for pair in selection["single_pairs"]:
                configs.append(pair)

            for config in selection[
                "dual_configurations"
            ]:
                configs.append(config)

            total = (
                len(configs)
                * len(final.kernel_pairs)
            )

            completed = 0

            for config_tag in configs:
                for ck, sk in final.kernel_pairs:
                    if progress_callback:
                        progress_callback(
                            5
                            + int(
                                completed
                                / max(total, 1)
                                * 75
                            ),
                            (
                                f"Stereo {config_tag} — "
                                f"{final.algorithm_tag} "
                                f"{ck}:{sk}"
                            ),
                        )

                    products.append(
                        _run_one_final_configuration(
                            settings,
                            processing,
                            final,
                            pre_paths,
                            config_tag,
                            ck,
                            sk,
                        )
                    )

                    completed += 1

            if selection[
                "tri_merge_required"
            ]:
                merged_products = []

                for index, (ck, sk) in enumerate(
                    final.kernel_pairs,
                    start=1,
                ):
                    if progress_callback:
                        progress_callback(
                            82
                            + int(
                                index
                                / len(
                                    final.kernel_pairs
                                )
                                * 14
                            ),
                            (
                                "Merging AB + AC + BC "
                                f"for {ck}:{sk}"
                            ),
                        )

                    merged_products.append(
                        _merge_tri_point_clouds(
                            settings,
                            final,
                            products,
                            ck,
                            sk,
                        )
                    )

                selected_products = (
                    merged_products
                )
            else:
                selected_products = products

            products_df = pd.DataFrame(
                [
                    {
                        "Product": item[
                            "product_tag"
                        ],
                        "Geometry": item[
                            "geometry"
                        ],
                        "Algorithm": item[
                            "algorithm"
                        ],
                        "CK": item[
                            "correlation_kernel"
                        ],
                        "SK": item[
                            "subpixel_kernel"
                        ],
                        "Point cloud": str(
                            item["point_cloud"]
                        ),
                    }
                    for item
                    in selected_products
                ]
            )

            csv_path = (
                settings.metadata_dir
                / "point_cloud_products.csv"
            )

            products_df.to_csv(
                csv_path,
                index=False,
            )

            state_path = _save_processing_state(
                settings,
                "point_cloud_reconstruction",
                {
                    "stereo_mode": (
                        final.stereo_mode
                    ),
                    "algorithm": _resolve_final_algorithm(final)[
                        "display_name"
                    ],
                    "kernel_pairs": [
                        list(pair)
                        for pair
                        in final.kernel_pairs
                    ],
                    "selected_products": [
                        {
                            "product_tag": item[
                                "product_tag"
                            ],
                            "point_cloud": str(
                                item[
                                    "point_cloud"
                                ]
                            ),
                            "ck": item[
                                "correlation_kernel"
                            ],
                            "sk": item[
                                "subpixel_kernel"
                            ],
                        }
                        for item
                        in selected_products
                    ],
                },
            )

            if progress_callback:
                progress_callback(
                    100,
                    "Point-cloud reconstruction completed",
                )

            return {
                "all_pair_products": products,
                "selected_products": selected_products,
                "products_df": products_df,
                "products_csv": csv_path,
                "summary_log": summary_log,
                "state_path": state_path,
            }

        except Exception:
            summary.write("")
            summary.write("ERROR")
            summary.write(
                traceback.format_exc()
            )
            raise


# ============================================================
# FINAL DSM GENERATION
# Kept separate from expensive stereo correlation, exactly as
# in the original workflow design.
# ============================================================

def _selected_point_clouds_for_dsm(
    settings: ProjectSettings,
    final: FinalProcessingSettings,
):
    """
    Reconstruct the expected selected point-cloud paths from the
    current interface selection.  This allows point2dem to be rerun
    without rerunning parallel_stereo.
    """
    selection = _validate_final_selection(
        settings,
        final,
    )

    products = []

    for ck, sk in final.kernel_pairs:
        algorithm_file_tag = _safe_algorithm_filename(
            _resolve_final_algorithm(final)["display_name"]
        )

        outname = (
            f"{settings.project_name}_"
            f"{algorithm_file_tag}_"
            f"ck{ck}_sk{sk}"
        )

        if final.stereo_mode == "single":
            tags = selection["single_pairs"]

            for tag in tags:
                point_cloud = (
                    _processing_output_dir(settings)
                    / "point_clouds"
                    / f"stereo_{tag}_{outname}"
                    / f"{tag}_{outname}-PC.tif"
                )

                products.append(
                    {
                        "product_tag": tag,
                        "geometry": (
                            GEOMETRY_LABELS.get(
                                tag,
                                tag,
                            )
                        ),
                        "algorithm": _resolve_final_algorithm(final)[
                            "display_name"
                        ],
                        "correlation_kernel": ck,
                        "subpixel_kernel": sk,
                        "point_cloud": point_cloud,
                    }
                )

        elif final.stereo_mode == "dual":
            tags = selection[
                "dual_configurations"
            ]

            for tag in tags:
                point_cloud = (
                    _processing_output_dir(settings)
                    / "point_clouds"
                    / f"stereo_{tag}_{outname}"
                    / f"{tag}_{outname}-PC.tif"
                )

                products.append(
                    {
                        "product_tag": tag,
                        "geometry": (
                            GEOMETRY_LABELS.get(
                                tag,
                                tag,
                            )
                        ),
                        "algorithm": _resolve_final_algorithm(final)[
                            "display_name"
                        ],
                        "correlation_kernel": ck,
                        "subpixel_kernel": sk,
                        "point_cloud": point_cloud,
                    }
                )

        elif final.stereo_mode == "tri":
            tag = "ABACBC"

            point_cloud = (
                _processing_output_dir(settings)
                / "point_clouds_merged"
                / f"merged_{tag}_{outname}"
                / f"{tag}_{outname}-PC.tif"
            )

            products.append(
                {
                    "product_tag": tag,
                    "geometry": (
                        GEOMETRY_LABELS[tag]
                    ),
                    "algorithm": _resolve_final_algorithm(final)[
                        "display_name"
                    ],
                    "correlation_kernel": ck,
                    "subpixel_kernel": sk,
                    "point_cloud": point_cloud,
                }
            )

    _require_existing_files(
        *[
            item["point_cloud"]
            for item in products
        ]
    )

    return products


def _plot_one_final_dsm(
    settings: ProjectSettings,
    product: dict,
):
    dem, bounds, crs = _read_dem_preview(
        product["dem"]
    )

    valid = dem.compressed()

    if valid.size == 0:
        raise ValueError(
            f"Final DSM has no valid values:\n"
            f"{product['dem']}"
        )

    vmin, vmax = np.percentile(
        valid,
        [2, 98],
    )

    filled = dem.filled(
        float(np.median(valid))
    )

    hillshade = LightSource(
        azdeg=315,
        altdeg=45,
    ).hillshade(
        filled,
        vert_exag=1.0,
    )

    hillshade = np.ma.array(
        hillshade,
        mask=np.ma.getmaskarray(dem),
    )

    extent = [
        bounds.left,
        bounds.right,
        bounds.bottom,
        bounds.top,
    ]

    has_error = (
        product.get("error_image")
        is not None
        and Path(
            product["error_image"]
        ).is_file()
    )

    ncols = 2 if has_error else 1

    fig, axes = plt.subplots(
        1,
        ncols,
        figsize=(7.4 * ncols, 6.1),
        squeeze=False,
    )

    ax = axes[0, 0]

    dem_plot = ax.imshow(
        dem,
        extent=extent,
        origin="upper",
        cmap="terrain",
        vmin=vmin,
        vmax=vmax,
    )

    ax.imshow(
        hillshade,
        extent=extent,
        origin="upper",
        cmap="gray",
        alpha=0.25,
    )

    cb = fig.colorbar(
        dem_plot,
        ax=ax,
        shrink=0.82,
    )
    cb.set_label("Elevation (m)")

    ax.set_title(
        f"{product['product_tag']} — "
        f"{product['geometry']}\nFinal DSM"
    )

    ax.grid(
        True,
        color="white",
        alpha=0.22,
        linewidth=0.5,
        linestyle="--",
    )

    if has_error:
        error, ebounds, _ = (
            _read_dem_preview(
                product["error_image"]
            )
        )

        eextent = [
            ebounds.left,
            ebounds.right,
            ebounds.bottom,
            ebounds.top,
        ]

        eax = axes[0, 1]

        error_plot = eax.imshow(
            error,
            extent=eextent,
            origin="upper",
            cmap="Reds",
            vmin=0,
            vmax=1,
        )

        ecb = fig.colorbar(
            error_plot,
            ax=eax,
            shrink=0.82,
        )
        ecb.set_label(
            "Intersection error (m)"
        )

        eax.set_title(
            "Intersection error"
        )

        eax.grid(
            True,
            color="white",
            alpha=0.22,
            linewidth=0.5,
            linestyle="--",
        )

    fig.suptitle(
        f"{settings.project_name} — "
        f"{product['algorithm']} "
        f"CK{product['correlation_kernel']} "
        f"SK{product['subpixel_kernel']}"
    )

    fig.tight_layout()

    safe_name = (
        f"{product['product_tag']}_"
        f"{settings.project_name}_"
        f"{_safe_algorithm_filename(product['algorithm'])}_"
        f"ck{product['correlation_kernel']}_"
        f"sk{product['subpixel_kernel']}_"
        "final_DSM"
    )

    png = (
        settings.figure_dir
        / f"{safe_name}.png"
    )
    pdf = (
        settings.figure_dir
        / f"{safe_name}.pdf"
    )

    fig.savefig(
        png,
        dpi=200,
        bbox_inches="tight",
    )
    fig.savefig(
        pdf,
        dpi=300,
        bbox_inches="tight",
    )

    return {
        "figure": fig,
        "png": png,
        "pdf": pdf,
    }


def generate_final_dsms(
    settings: ProjectSettings,
    processing: PreProcessingSettings,
    final: FinalProcessingSettings,
    progress_callback=None,
):
    """
    Rasterize every currently selected final point cloud with point2dem.
    """
    # Validate CRS/reference inputs and aligned processing state.
    _final_common_paths(
        settings,
        processing,
    )

    point_clouds = (
        _selected_point_clouds_for_dsm(
            settings,
            final,
        )
    )

    final_dsm_dir = (
        _processing_output_dir(settings)
        / "final_dsms"
    )

    final_dsm_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    summary_log = (
        settings.log_dir
        / "final_dsm_generation.log"
    )

    generated = []

    with WorkflowLog(summary_log) as summary:
        try:
            for index, item in enumerate(
                point_clouds,
                start=1,
            ):
                ck = item[
                    "correlation_kernel"
                ]
                sk = item[
                    "subpixel_kernel"
                ]
                product_tag = item[
                    "product_tag"
                ]

                if progress_callback:
                    progress_callback(
                        5
                        + int(
                            (index - 1)
                            / len(point_clouds)
                            * 82
                        ),
                        (
                            f"DSM {product_tag} — "
                            f"{ck}:{sk}"
                        ),
                    )

                algorithm_file_tag = _safe_algorithm_filename(
                    _resolve_final_algorithm(final)["display_name"]
                )

                outname = (
                    f"{settings.project_name}_"
                    f"{algorithm_file_tag}_"
                    f"ck{ck}_sk{sk}"
                )

                dem_name = (
                    f"{product_tag}_"
                    f"{outname}-"
                    f"{final.final_dsm_resolution_m}m"
                )

                dem_prefix = (
                    final_dsm_dir
                    / dem_name
                )

                expected_dem = Path(
                    f"{dem_prefix}-DEM.tif"
                )

                expected_error = Path(
                    f"{dem_prefix}"
                    "-IntersectionErr.tif"
                )

                log_file = (
                    _processing_log_dir(settings)
                    / f"point2dem.{dem_name}.log"
                )

                _prepare_output_prefix(
                    dem_prefix,
                    log_file,
                    settings.overwrite,
                )

                arguments = [
                    "--max-valid-triangulation-error",
                    (
                        final
                        .max_valid_triangulation_error_m
                    ),
                    "--t_srs",
                    f"EPSG:{processing.target_epsg}",
                    "--tr",
                    final.final_dsm_resolution_m,
                    "--threads",
                    final.final_dsm_threads,
                    "--nodata-value",
                    final.final_dsm_nodata,
                    "--tif-compress",
                    final.final_dsm_compression,
                ]

                if final.create_error_image:
                    arguments.append(
                        "--errorimage"
                    )

                arguments.extend(
                    [
                        item["point_cloud"],
                        "-o",
                        dem_prefix,
                    ]
                )

                _run_asp_command(
                    "point2dem",
                    arguments,
                    log_file,
                    _processing_root(settings),
                )

                _require_existing_files(
                    expected_dem
                )

                error_image = None

                if final.create_error_image:
                    _require_existing_files(
                        expected_error
                    )
                    error_image = (
                        expected_error
                    )

                product = {
                    **item,
                    "dem": expected_dem,
                    "error_image": error_image,
                    "log": log_file,
                }

                product["plot"] = (
                    _plot_one_final_dsm(
                        settings,
                        product,
                    )
                )

                generated.append(product)

                summary.write(
                    f"Final DSM: {expected_dem}"
                )

            products_df = pd.DataFrame(
                [
                    {
                        "Product": item[
                            "product_tag"
                        ],
                        "Geometry": item[
                            "geometry"
                        ],
                        "Algorithm": item[
                            "algorithm"
                        ],
                        "CK": item[
                            "correlation_kernel"
                        ],
                        "SK": item[
                            "subpixel_kernel"
                        ],
                        "Point cloud": str(
                            item["point_cloud"]
                        ),
                        "DSM": str(
                            item["dem"]
                        ),
                        "Intersection error": (
                            str(
                                item["error_image"]
                            )
                            if item[
                                "error_image"
                            ] is not None
                            else ""
                        ),
                    }
                    for item in generated
                ]
            )

            csv_path = (
                settings.metadata_dir
                / "final_dsm_products.csv"
            )

            products_df.to_csv(
                csv_path,
                index=False,
            )

            state_path = _save_processing_state(
                settings,
                "final_dsm",
                {
                    "resolution_m": (
                        final.final_dsm_resolution_m
                    ),
                    "max_valid_triangulation_error_m": (
                        final
                        .max_valid_triangulation_error_m
                    ),
                    "create_error_image": (
                        final.create_error_image
                    ),
                    "products": [
                        {
                            "product_tag": item[
                                "product_tag"
                            ],
                            "dsm": str(
                                item["dem"]
                            ),
                            "error_image": (
                                str(
                                    item[
                                        "error_image"
                                    ]
                                )
                                if item[
                                    "error_image"
                                ] is not None
                                else None
                            ),
                        }
                        for item in generated
                    ],
                },
            )

            if progress_callback:
                progress_callback(
                    100,
                    "Final DSM generation completed",
                )

            return {
                "products": generated,
                "products_df": products_df,
                "products_csv": csv_path,
                "summary_log": summary_log,
                "state_path": state_path,
            }

        except Exception:
            summary.write("")
            summary.write("ERROR")
            summary.write(
                traceback.format_exc()
            )
            raise


# ============================================================
# EXTENDED v0.5 INTERFACE
# ============================================================

class FullProjectSetupUI(ProjectSetupUI):
    """
    Keep the v0.5 interface exactly as it was and append the remaining
    processing sections underneath it.
    """

    def __init__(self):
        super().__init__()

        widgets = self.widgets
        style = {"description_width": "205px"}
        wide = widgets.Layout(width="810px")

        self.last_pre_processing = None
        self.last_point_cloud = None
        self.last_final_dsm = None

        # ----------------------------------------------------
        # PRE-PROCESSING INPUTS
        # ----------------------------------------------------
        self.alignment_dem = widgets.Text(
            description="Alignment LiDAR DSM:",
            placeholder="/path/to/LiDAR_DSM_1m.tif",
            style=style,
            layout=wide,
        )

        self.mapproject_dem = widgets.Text(
            description="Map-projection DEM:",
            placeholder="/path/to/existing_reference_DEM.tif",
            style=style,
            layout=wide,
        )

        # ----------------------------------------------------
        # INTEGRATED REFERENCE DEM SETTINGS
        # ----------------------------------------------------
        # The two widgets above are retained as internal resolved paths so the
        # tested ASP processing functions remain unchanged. Users configure
        # reference topography through the compact section below.
        self.reference_region = widgets.Dropdown(
            options=[
                ("France — mainland", "france"),
                ("Other / global", "global"),
            ],
            value="france",
            description="Country / region:",
            style=style,
            layout=widgets.Layout(width="620px"),
        )

        self.reference_aoi = widgets.Text(
            description="Reference DEM AOI:",
            placeholder="Leave blank to reuse Prepare data AOI",
            style=style,
            layout=wide,
        )

        self.reference_map_source = widgets.Dropdown(
            options=[],
            description="Map DEM source:",
            style=style,
            layout=widgets.Layout(width="680px"),
        )

        self.reference_map_resolution = widgets.FloatText(
            value=50.0,
            description="Map DEM resolution (m):",
            style=style,
        )

        self.reference_existing_map = widgets.Text(
            description="Existing map DEM:",
            placeholder="/path/to/map_projection_DEM.tif",
            style=style,
            layout=wide,
        )

        self.reference_existing_map_convert = widgets.Checkbox(
            value=False,
            description="Convert existing map DEM to ellipsoidal heights",
            indent=False,
        )

        self.reference_alignment_source = widgets.Dropdown(
            options=[],
            description="Alignment reference:",
            style=style,
            layout=widgets.Layout(width="720px"),
        )

        self.reference_alignment_resolution = widgets.FloatText(
            value=1.0,
            description="Alignment DEM res. (m):",
            style=style,
        )

        self.reference_existing_alignment = widgets.Text(
            description="Existing high-res DSM:",
            placeholder="/path/to/high_resolution_alignment_DSM.tif",
            style=style,
            layout=wide,
        )

        self.reference_existing_alignment_convert = widgets.Checkbox(
            value=False,
            description="Convert existing alignment DSM to ellipsoidal heights",
            indent=False,
        )

        self.reference_geoid_model = widgets.Dropdown(
            options=[],
            description="Geoid model:",
            style=style,
            layout=widgets.Layout(width="690px"),
        )

        self.reference_custom_n = widgets.Text(
            description="Custom N raster:",
            placeholder="/path/to/N_h_minus_H.tif",
            style=style,
            layout=wide,
        )

        self.reference_global_buffer = widgets.FloatText(
            value=0.05,
            description="Global DEM buffer (deg):",
            style=style,
        )

        self.reference_ign_buffer = widgets.FloatText(
            value=1000.0,
            description="IGN buffer (m):",
            style=style,
        )

        self.reference_ign_workers = widgets.IntText(
            value=2,
            description="IGN download workers:",
            style=style,
        )

        self.reference_dem_box = widgets.VBox()

        self.reference_dem_settings = widgets.Accordion(
            children=[self.reference_dem_box]
        )
        self.reference_dem_settings.set_title(
            0,
            "Reference DEM settings",
        )
        self.reference_dem_settings.selected_index = 0

        self.reference_dem_status = widgets.HTML(
            value=(
                "<div style='margin:5px 0 8px 0;color:#666;'>"
                "Reference DEMs will be prepared when pre-processing starts."
                "</div>"
            )
        )

        self.target_epsg = widgets.IntText(
            value=32632,
            description="Target CRS (EPSG):",
            style=style,
        )

        self.map_resolution = widgets.FloatText(
            value=0.5,
            description="Map image resolution:",
            style=style,
        )

        self.preliminary_pair = widgets.Dropdown(
            options=[
                (
                    "AC — Forward–Backward",
                    "AC",
                ),
                (
                    "AB — Forward–Middle",
                    "AB",
                ),
                (
                    "BC — Middle–Backward",
                    "BC",
                ),
            ],
            value="AC",
            description="Preliminary pair:",
            style=style,
        )

        self.ba_robust_threshold = widgets.FloatText(
            value=2.0,
            description="BA robust threshold:",
            style=style,
        )

        self.ba_max_iterations = widgets.IntText(
            value=500,
            description="BA max iterations:",
            style=style,
        )

        # ----------------------------------------------------
        # PRELIMINARY STEREO CONTROLS
        # ----------------------------------------------------
        # The defaults reproduce the original tested preprocessing:
        # asp_bm with CK 35 / SK 45, cost mode 2.
        self.prelim_algorithm = widgets.Dropdown(
            options=[
                ("BM — asp_bm", "BM"),
                ("SGM — asp_sgm", "SGM"),
                ("MGM — asp_mgm", "MGM"),
                ("Other / custom algorithm", "CUSTOM"),
            ],
            value="BM",
            description="Prelim algorithm:",
            style=style,
            layout=widgets.Layout(width="520px"),
        )

        self.prelim_custom_algorithm = widgets.Text(
            value="",
            description="Custom prelim alg.:",
            placeholder="Enter ASP --stereo-algorithm value",
            style=style,
            layout=wide,
        )

        self.prelim_kernel_selector = widgets.Dropdown(
            options=[],
            description="Prelim CK : SK:",
            style=style,
            layout=widgets.Layout(width="520px"),
        )

        self.prelim_custom_kernel = widgets.Text(
            value="",
            description="Custom prelim CK:SK:",
            placeholder="Example: 11:23",
            style=style,
            layout=wide,
        )

        self.prelim_cost_mode = widgets.IntText(
            value=2,
            description="Prelim cost mode:",
            style=style,
            disabled=True,
        )

        self.prelim_xcorr_threshold = widgets.FloatText(
            value=2.0,
            description="Prelim xcorr threshold:",
            style=style,
        )

        self.prelim_corr_memory = widgets.IntText(
            value=10240,
            description="Prelim corr memory:",
            style=style,
        )

        self.prelim_corr_tile_size = widgets.IntText(
            value=3200,
            description="Prelim corr tile size:",
            style=style,
        )

        self.prelim_subpixel_mode = widgets.IntText(
            value=2,
            description="Prelim subpixel mode:",
            style=style,
        )

        self.pc_align_max_displacement = (
            widgets.FloatText(
                value=250.0,
                description="Max displacement (m):",
                style=style,
            )
        )

        self.pc_align_iterations = (
            widgets.IntText(
                value=100,
                description="pc_align iterations:",
                style=style,
            )
        )

        self.processing_threads = widgets.IntText(
            value=18,
            description="Map/camera threads:",
            style=style,
        )

        self.target_epsg_row = self._row(
            self.target_epsg,
            "Target projected CRS used by the ASP outputs. "
            "This value is site-dependent; change it for the study area. "
            "The supplied reference DEMs should be prepared/reprojected "
            "consistently with this target CRS before processing.",
        )

        self.crs_information_note = widgets.HTML(
            value=(
                "<div style='margin:3px 0 12px 205px;"
                "padding:9px 11px;border-left:3px solid #336699;"
                "background:#f7f9fc;color:#555;max-width:760px;"
                "font-size:12px;line-height:1.45;'>"
                "<b>CRS / UTM zone:</b> the appropriate projected CRS "
                "depends on the geographic location of the study area. "
                "Confirm the EPSG code before processing. "
                "<a href='https://epsg.io/' target='_blank' "
                "rel='noopener noreferrer'>Check CRS / UTM information on EPSG.io ↗</a>"
                "<br>"
                "Use the same target CRS for the stereo-derived products "
                "and the prepared reference DEMs used for alignment and map projection."
                "</div>"
            )
        )

        self.prelim_algorithm_row = self._row(
            self.prelim_algorithm,
            "Preliminary stereo algorithm. The tested workflow default is "
            "BM / asp_bm.",
        )

        self.prelim_custom_algorithm_row = self._row(
            self.prelim_custom_algorithm,
            "Shown only for Other/custom. Enter the exact ASP "
            "--stereo-algorithm value.",
        )

        self.prelim_kernel_row = self._row(
            self.prelim_kernel_selector,
            "Preliminary linked correlation/subpixel kernel pair. "
            "The original tested BM default is CK 35 / SK 45.",
        )

        self.prelim_custom_kernel_row = self._row(
            self.prelim_custom_kernel,
            "Shown only for Other/custom CK:SK. Enter one linked pair, "
            "for example 11:23.",
        )

        self.prelim_cost_mode_row = self._row(
            self.prelim_cost_mode,
            "Automatic preset: BM = 2; SGM/MGM = 4. Editable only for "
            "a custom preliminary algorithm.",
        )

        self.preprocess_advanced_box = widgets.VBox()

        self.preprocess_advanced = (
            widgets.Accordion(
                children=[
                    self.preprocess_advanced_box
                ]
            )
        )

        self.preprocess_advanced.set_title(
            0,
            "Advanced pre-processing settings",
        )

        self.run_preprocessing = widgets.Button(
            description="Run pre-processing",
            button_style="warning",
            icon="cogs",
            layout=widgets.Layout(
                width="260px",
                height="42px",
            ),
        )

        self.preprocess_progress = (
            widgets.IntProgress(
                value=0,
                min=0,
                max=100,
                description="Progress:",
                style={
                    "description_width": "80px"
                },
                layout=widgets.Layout(
                    width="720px"
                ),
            )
        )

        self.preprocess_progress_text = (
            widgets.HTML(
                "<span style='color:#666;'>"
                "Waiting.</span>"
            )
        )

        self.preprocess_summary = widgets.HTML()

        self._preprocess_stage_order = [
            "bundle_adjustment",
            "preliminary_stereo",
            "preliminary_dem",
            "lidar_alignment",
            "camera_transform",
            "map_projection",
        ]

        self._preprocess_stage_labels = {
            "bundle_adjustment": "Bundle adjustment",
            "preliminary_stereo": "Prelim stereo",
            "preliminary_dem": "Prelim DSM",
            "lidar_alignment": "LiDAR alignment",
            "camera_transform": "Camera transform",
            "map_projection": "Map projection",
        }

        self._preprocess_stage_outputs = {
            stage: widgets.Output(
                layout=widgets.Layout(
                    width="100%",
                    min_height="120px",
                )
            )
            for stage in self._preprocess_stage_order
        }

        self.preprocess_stage_tabs = widgets.Tab(
            children=[
                self._preprocess_stage_outputs[stage]
                for stage in self._preprocess_stage_order
            ]
        )

        for index, stage in enumerate(
            self._preprocess_stage_order
        ):
            self.preprocess_stage_tabs.set_title(
                index,
                self._preprocess_stage_labels[stage],
            )

        self.preprocess_results = widgets.VBox(
            [self.preprocess_stage_tabs]
        )

        # ----------------------------------------------------
        # POINT CLOUD INPUTS
        # ----------------------------------------------------
        self.final_mode = widgets.Dropdown(
            options=[
                (
                    "Single pair(s)",
                    "single",
                ),
                (
                    "Ordered three-image configuration(s)",
                    "dual",
                ),
                (
                    "Tri — AB + AC + BC then merge",
                    "tri",
                ),
            ],
            value="dual",
            description="Stereo mode:",
            style=style,
        )

        self.single_pair_selector = (
            widgets.SelectMultiple(
                options=[
                    (
                        "AB — FM",
                        "AB",
                    ),
                    (
                        "AC — FB",
                        "AC",
                    ),
                    (
                        "BC — MB",
                        "BC",
                    ),
                ],
                value=("AC",),
                description="Stereo pair(s):",
                style=style,
                layout=widgets.Layout(
                    width="600px",
                    height="95px",
                ),
            )
        )

        self.dual_selector = (
            widgets.SelectMultiple(
                options=[
                    (
                        "ABC — FMB",
                        "ABC",
                    ),
                    (
                        "BAC — MFB",
                        "BAC",
                    ),
                    (
                        "CAB — BFM",
                        "CAB",
                    ),
                ],
                value=("CAB",),
                description="3-image config(s):",
                style=style,
                layout=widgets.Layout(
                    width="600px",
                    height="95px",
                ),
            )
        )

        self.tri_mode_info = widgets.HTML(
            "<div style='margin-left:205px;padding:8px 10px;"
            "border-left:3px solid #1976d2;background:#f5f9ff;"
            "max-width:720px;'>"
            "<b>Tri mode:</b> AB (FM), AC (FB), and BC (MB) are run "
            "automatically, then their point clouds are merged with "
            "<code>pc_merge</code>. No manual pair selection is required."
            "</div>"
        )


        self.final_algorithm = widgets.Dropdown(
            options=[
                ("BM — asp_bm", "BM"),
                ("SGM — asp_sgm", "SGM"),
                ("MGM — asp_mgm", "MGM"),
                ("Other / custom algorithm", "CUSTOM"),
            ],
            value="MGM",
            description="Algorithm:",
            style=style,
            layout=widgets.Layout(width="520px"),
        )

        self.custom_algorithm_name = widgets.Text(
            value="",
            description="Custom algorithm:",
            placeholder="Enter ASP --stereo-algorithm value",
            style=style,
            layout=wide,
        )

        # Preset algorithms set this automatically.
        # It becomes editable only for the CUSTOM option.
        self.custom_cost_mode = widgets.IntText(
            value=4,
            description="Cost mode:",
            style=style,
            disabled=True,
        )

        self.final_xcorr_threshold = widgets.FloatText(
            value=2.0,
            description="Xcorr threshold:",
            style=style,
        )

        self.final_corr_memory = widgets.IntText(
            value=10240,
            description="Corr memory (MB):",
            style=style,
        )

        self.final_corr_tile_size = widgets.IntText(
            value=3200,
            description="Corr tile size:",
            style=style,
        )

        self.final_subpixel_mode = widgets.IntText(
            value=2,
            description="Subpixel mode:",
            style=style,
        )

        self.kernel_selector = (
            widgets.SelectMultiple(
                options=[
                    (
                        "5 : 9",
                        (5, 9),
                    ),
                    (
                        "7 : 15",
                        (7, 15),
                    ),
                    (
                        "9 : 21",
                        (9, 21),
                    ),
                ],
                value=((9, 21),),
                description="CK : SK pair(s):",
                style=style,
                layout=widgets.Layout(
                    width="520px",
                    height="135px",
                ),
            )
        )

        self.custom_kernel_pairs = widgets.Text(
            value="",
            description="Additional CK:SK:",
            placeholder="Example: 11:23,13:27",
            style=style,
            layout=wide,
        )

        self.auto_generate_dsm = (
            widgets.Checkbox(
                value=True,
                description=(
                    "Generate final DSM automatically "
                    "after point-cloud reconstruction"
                ),
                indent=False,
            )
        )

        self.run_point_cloud = widgets.Button(
            description="Run point-cloud reconstruction",
            button_style="info",
            icon="cube",
            layout=widgets.Layout(
                width="310px",
                height="42px",
            ),
        )

        self.point_cloud_progress = (
            widgets.IntProgress(
                value=0,
                min=0,
                max=100,
                description="Progress:",
                style={
                    "description_width": "80px"
                },
                layout=widgets.Layout(
                    width="720px"
                ),
            )
        )

        self.point_cloud_progress_text = (
            widgets.HTML(
                "<span style='color:#666;'>"
                "Waiting.</span>"
            )
        )

        self.point_cloud_summary = widgets.HTML()
        self.point_cloud_results = widgets.VBox()

        # ----------------------------------------------------
        # FINAL DSM INPUTS
        # ----------------------------------------------------
        self.final_dsm_resolution = (
            widgets.FloatText(
                value=1.0,
                description="DSM resolution (m):",
                style=style,
            )
        )

        self.max_triangulation_error = (
            widgets.FloatText(
                value=1.0,
                description="Max triangulation error:",
                style=style,
            )
        )

        self.create_error_image = (
            widgets.Checkbox(
                value=True,
                description=(
                    "Create intersection-error image"
                ),
                indent=False,
            )
        )

        self.final_dsm_threads = widgets.IntText(
            value=0,
            description="point2dem threads:",
            style=style,
        )

        self.pc_merge_threads = widgets.IntText(
            value=18,
            description="pc_merge threads:",
            style=style,
        )

        self.final_advanced = (
            widgets.Accordion(
                children=[
                    widgets.VBox(
                        [
                            self._row(
                                self.custom_cost_mode,
                                "Automatically set to 2 for BM and 4 for "
                                "SGM/MGM. It becomes editable only when "
                                "'Other / custom algorithm' is selected.",
                            ),
                            self._row(
                                self.final_xcorr_threshold,
                                "Final parallel_stereo --xcorr-threshold. "
                                "Tested workflow default: 2.0.",
                            ),
                            self._row(
                                self.final_corr_memory,
                                "Final correlation memory limit in MB. "
                                "Tested workflow default: 10240.",
                            ),
                            self._row(
                                self.final_corr_tile_size,
                                "Final correlation tile size. "
                                "Tested workflow default: 3200.",
                            ),
                            self._row(
                                self.final_subpixel_mode,
                                "Final ASP subpixel mode. "
                                "Tested workflow default: 2.",
                            ),
                            self._row(
                                self.pc_merge_threads,
                                "Used only in Tri mode. "
                                "The original workflow passes this value "
                                "to pc_merge.",
                            ),
                            self._row(
                                self.final_dsm_threads,
                                "The final point2dem thread setting. "
                                "0 lets ASP choose.",
                            ),
                        ]
                    )
                ]
            )
        )

        self.final_advanced.set_title(
            0,
            "Advanced final-processing settings",
        )

        self.run_final_dsm = widgets.Button(
            description="Generate final DSM only",
            button_style="success",
            icon="map",
            layout=widgets.Layout(
                width="270px",
                height="42px",
            ),
        )

        self.final_dsm_progress = (
            widgets.IntProgress(
                value=0,
                min=0,
                max=100,
                description="Progress:",
                style={
                    "description_width": "80px"
                },
                layout=widgets.Layout(
                    width="720px"
                ),
            )
        )

        self.final_dsm_progress_text = (
            widgets.HTML(
                "<span style='color:#666;'>"
                "Waiting.</span>"
            )
        )

        self.final_dsm_summary = widgets.HTML()
        self.final_dsm_results = widgets.VBox()

        # ----------------------------------------------------
        # HELP ROWS
        # ----------------------------------------------------
        preprocessing_rows = [
            self.reference_dem_settings,
            self.reference_dem_status,
            self._row(
                self.map_resolution,
                "Mapproject image resolution. "
                "The tested Pléiades value is 0.5 m. "
                "This is the image projection resolution, not the "
                "reference DEM resolution configured above.",
            ),
            self._row(
                self.preliminary_pair,
                "Pair used to build the preliminary DSM "
                "for absolute high-resolution reference alignment. "
                "The tested tri-stereo workflow uses AC.",
            ),
        ]

        self.final_mode_row = self._row(
            self.final_mode,
            "Single runs selected 2-image pairs. "
            "Ordered three-image runs selected ordered A/B/C configurations. "
            "Tri automatically runs AB, AC and BC and then pc_merge.",
        )

        self.single_pair_row = self._row(
            self.single_pair_selector,
            "For a tri-stereo acquisition, Single mode can run one or more "
            "of AB (FM), AC (FB), and BC (MB). For a true two-image stereo "
            "acquisition, only AB exists and it is selected automatically.",
        )

        self.dual_selector_row = self._row(
            self.dual_selector,
            "Used only for an A/B/C tri-stereo acquisition in ordered "
            "three-image mode. Available tested configurations are "
            "ABC (FMB), BAC (MFB), and CAB (BFM).",
        )

        self.final_algorithm_row = self._row(
            self.final_algorithm,
            "Choose BM, SGM, MGM, or Other/custom. "
            "BM automatically uses cost mode 2. "
            "SGM and MGM automatically use cost mode 4.",
        )

        self.custom_algorithm_row = self._row(
            self.custom_algorithm_name,
            "Shown only when 'Other / custom algorithm' is selected. "
            "Enter the exact ASP --stereo-algorithm value here. "
            "The cost mode and other advanced parameters then become "
            "user-controlled.",
        )

        self.kernel_selector_row = self._row(
            self.kernel_selector,
            "Tested CK:SK presets for the selected algorithm. "
            "You can select one or several. Select "
            "'Other / custom CK:SK → enter below' for a value that "
            "is not listed.",
        )

        self.custom_kernel_row = self._row(
            self.custom_kernel_pairs,
            "This field appears only when "
            "'Other / custom CK:SK' is selected above. "
            "Enter custom linked kernel pairs here. "
            "Examples: 11:23 or 11:23,13:27. "
            "They are passed as --corr-kernel CK CK and "
            "--subpixel-kernel SK SK.",
        )

        self.auto_dsm_row = self._row(
            self.auto_generate_dsm,
            "Keeps point-cloud reconstruction and DSM rasterization as "
            "separate stages while allowing automatic continuation.",
        )

        # Dynamic container: this is rebuilt whenever Stereo mode,
        # acquisition type, or algorithm changes.
        self.point_cloud_controls_box = widgets.VBox()

        # Kept as named rows rather than positional indexes to avoid
        # the visibility bug seen in v0.5.3.
        point_cloud_rows = [
            self.final_mode_row,
            self.single_pair_row,
            self.dual_selector_row,
            self.final_algorithm_row,
            self.custom_algorithm_row,
            self.kernel_selector_row,
            self.custom_kernel_row,
            self.auto_dsm_row,
        ]

        final_dsm_rows = [
            self._row(
                self.final_dsm_resolution,
                "Final point2dem resolution. "
                "The uploaded tested run uses 1.0 m.",
            ),
            self._row(
                self.max_triangulation_error,
                "Maximum valid triangulation error. "
                "The uploaded tested final run uses 1.0 m.",
            ),
            self._row(
                self.create_error_image,
                "Equivalent to point2dem --errorimage.",
            ),
        ]

        self._processing_rows = (
            preprocessing_rows
        )
        self._point_cloud_rows = (
            point_cloud_rows
        )
        self._final_dsm_rows = (
            final_dsm_rows
        )

        # ----------------------------------------------------
        # EVENTS
        # ----------------------------------------------------
        self.run_preprocessing.on_click(
            self._on_pre_processing
        )

        self.run_point_cloud.on_click(
            self._on_point_cloud
        )

        self.run_final_dsm.on_click(
            self._on_final_dsm
        )

        # Preliminary-stereo controls are independent from the final stereo
        # controls. They only affect the preliminary alignment stereo run.
        self.prelim_algorithm.observe(
            self._on_prelim_algorithm_change,
            names="value",
        )

        self.prelim_kernel_selector.observe(
            self._on_prelim_kernel_change,
            names="value",
        )

        # Dedicated callbacks keep acquisition, geometry mode, and algorithm
        # updates independent. This avoids nested observer calls when dropdown
        # option lists are refreshed.
        self.final_mode.observe(
            self._on_final_mode_change,
            names="value",
        )

        self.final_algorithm.observe(
            self._on_final_algorithm_change,
            names="value",
        )

        self.kernel_selector.observe(
            self._on_kernel_selection_change,
            names="value",
        )

        self.acquisition_mode.observe(
            self._on_acquisition_mode_change,
            names="value",
        )

        # Reference-DEM controls only affect reference preparation.
        # They do not modify ASP stereo / point-cloud configuration.
        #
        # Guard widget initialization/update transactions so changing Dropdown
        # options/values cannot recursively fire nested Reference DEM callbacks.
        self._updating_reference_controls = False

        self.reference_region.observe(
            self._on_reference_region_change,
            names="value",
        )
        self.reference_map_source.observe(
            self._on_reference_map_source_change,
            names="value",
        )
        self.reference_alignment_source.observe(
            self._rebuild_reference_dem_controls,
            names="value",
        )
        self.reference_geoid_model.observe(
            self._rebuild_reference_dem_controls,
            names="value",
        )
        self.reference_existing_map_convert.observe(
            self._rebuild_reference_dem_controls,
            names="value",
        )
        self.reference_existing_alignment_convert.observe(
            self._rebuild_reference_dem_controls,
            names="value",
        )

        self._configure_reference_dem_controls()

        # Initialize advanced preliminary-stereo controls first.
        self._apply_prelim_algorithm_selection()
        self._rebuild_preprocess_advanced_controls()

        # Initialize the point-cloud section once, in a deterministic order.
        self._configure_geometry_controls()
        self._apply_algorithm_selection()
        self._rebuild_point_cloud_controls()

        # ----------------------------------------------------
        # APPEND TO THE ORIGINAL v0.5 CONTAINER
        # ----------------------------------------------------
        extra_children = [
            widgets.HTML(
                "<hr><h4>Pre-processing</h4>"
                "<div style='color:#666;margin-bottom:8px;'>"
                "Bundle adjustment → preliminary stereo → "
                "preliminary DSM → LiDAR alignment → "
                "apply transform to cameras → map projection."
                "</div>"
            ),
            *preprocessing_rows,
            self.preprocess_advanced,
            widgets.HTML("<br>"),
            self.run_preprocessing,
            self.preprocess_progress,
            self.preprocess_progress_text,
            self.preprocess_summary,
            self.preprocess_results,

            widgets.HTML(
                "<hr><h4>Point cloud</h4>"
                "<div style='color:#666;margin-bottom:8px;'>"
                "Run the selected final stereo geometry with "
                "parallel_stereo. In Tri mode, AB, AC and BC "
                "are merged with pc_merge."
                "</div>"
            ),
            self.point_cloud_controls_box,
            self.final_advanced,
            widgets.HTML("<br>"),
            self.run_point_cloud,
            self.point_cloud_progress,
            self.point_cloud_progress_text,
            self.point_cloud_summary,
            self.point_cloud_results,

            widgets.HTML(
                "<hr><h4>Final DSM</h4>"
                "<div style='color:#666;margin-bottom:8px;'>"
                "Rasterize the selected point cloud(s) with "
                "point2dem. This can be rerun without repeating "
                "the expensive stereo correlation."
                "</div>"
            ),
            *final_dsm_rows,
            widgets.HTML("<br>"),
            self.run_final_dsm,
            self.final_dsm_progress,
            self.final_dsm_progress_text,
            self.final_dsm_summary,
            self.final_dsm_results,
        ]

        self.container.children = tuple(
            list(self.container.children)
            + extra_children
        )

    # --------------------------------------------------------
    # REFERENCE DEM CONTROLS
    # --------------------------------------------------------
    def _reference_map_source_options(self):
        options = [
            ("Copernicus DEM GLO-30", "copernicus"),
            ("SRTM1 30 m", "srtm"),
        ]

        if self.reference_region.value == "france":
            options.insert(
                0,
                ("IGN LiDAR HD", "ign"),
            )

        options.append(
            ("Existing DEM", "existing")
        )
        return options

    def _reference_alignment_source_options(self):
        if self.reference_region.value == "france":
            return [
                (
                    "IGN LiDAR HD — automatic high-resolution reference",
                    "ign",
                ),
                (
                    "Existing high-resolution DSM",
                    "existing",
                ),
            ]

        return [
            (
                "Existing high-resolution DSM",
                "existing",
            ),
        ]

    def _reference_geoid_options(self):
        options = []

        if self.reference_region.value == "france":
            options.append(
                ("RAF20 — mainland France", "raf20")
            )

        options.extend(
            [
                ("EGM96 — global", "egm96"),
                ("EGM2008 — global", "egm2008"),
                ("Custom N raster", "custom"),
            ]
        )

        return options

    def _reference_default_geoid_model(self):
        # Integrated workflow rule requested for the Pléiades software:
        # France defaults to RAF20 for all automatic reference preparation.
        if self.reference_region.value == "france":
            return "raf20"

        if self.reference_map_source.value == "srtm":
            return "egm96"

        if self.reference_map_source.value == "copernicus":
            return "egm2008"

        return "egm96"

    def _configure_reference_dem_controls(self):
        if getattr(
            self,
            "_updating_reference_controls",
            False,
        ):
            return

        self._updating_reference_controls = True

        try:
            current_map = self.reference_map_source.value
            map_options = self._reference_map_source_options()
            self.reference_map_source.options = map_options

            valid_map = {
                value
                for _, value
                in map_options
            }

            desired_map = (
                current_map
                if current_map in valid_map
                else (
                    "ign"
                    if self.reference_region.value == "france"
                    else "copernicus"
                )
            )

            if (
                self.reference_map_source.value
                != desired_map
            ):
                self.reference_map_source.value = desired_map

            current_alignment = (
                self.reference_alignment_source.value
            )

            alignment_options = (
                self._reference_alignment_source_options()
            )

            self.reference_alignment_source.options = (
                alignment_options
            )

            valid_alignment = {
                value
                for _, value
                in alignment_options
            }

            desired_alignment = (
                current_alignment
                if current_alignment in valid_alignment
                else (
                    "ign"
                    if self.reference_region.value == "france"
                    else "existing"
                )
            )

            if (
                self.reference_alignment_source.value
                != desired_alignment
            ):
                self.reference_alignment_source.value = (
                    desired_alignment
                )

            model_options = self._reference_geoid_options()
            self.reference_geoid_model.options = model_options

            default_model = self._reference_default_geoid_model()

            valid_models = {
                value
                for _, value
                in model_options
            }

            desired_model = (
                default_model
                if default_model in valid_models
                else model_options[0][1]
            )

            if (
                self.reference_geoid_model.value
                != desired_model
            ):
                self.reference_geoid_model.value = (
                    desired_model
                )

            self._apply_reference_map_resolution_default()

        finally:
            self._updating_reference_controls = False

        # Rebuild exactly once after the widget transaction is complete.
        self._rebuild_reference_dem_controls()

    def _apply_reference_map_resolution_default(self):
        source = self.reference_map_source.value

        if source == "ign":
            # Tested workflow:
            # 1 m LiDAR for pc_align, generalized 50 m LiDAR for mapproject.
            self.reference_map_resolution.value = 50.0

        elif source in {
            "copernicus",
            "srtm",
        }:
            self.reference_map_resolution.value = 30.0

        elif source == "existing":
            # Keep the tested generalized-reference value as a neutral default.
            self.reference_map_resolution.value = 50.0

    def _on_reference_region_change(self, change=None):
        if getattr(
            self,
            "_updating_reference_controls",
            False,
        ):
            return

        self._updating_reference_controls = True

        try:
            map_options = self._reference_map_source_options()
            self.reference_map_source.options = map_options

            alignment_options = self._reference_alignment_source_options()
            self.reference_alignment_source.options = alignment_options

            model_options = self._reference_geoid_options()
            self.reference_geoid_model.options = model_options

            if self.reference_region.value == "france":
                # France starts from the tested LiDAR configuration.
                self.reference_map_source.value = "ign"
                self.reference_alignment_source.value = "ign"
                self.reference_geoid_model.value = "raf20"
                self.reference_alignment_resolution.value = 1.0
                self.reference_map_resolution.value = 50.0
            else:
                self.reference_map_source.value = "copernicus"
                self.reference_alignment_source.value = "existing"
                self.reference_geoid_model.value = "egm2008"
                self.reference_map_resolution.value = 30.0

        finally:
            self._updating_reference_controls = False

        self._rebuild_reference_dem_controls()

    def _on_reference_map_source_change(self, change=None):
        if getattr(
            self,
            "_updating_reference_controls",
            False,
        ):
            return

        self._updating_reference_controls = True

        try:
            self._apply_reference_map_resolution_default()

            # Changing the map source updates the global default,
            # while France remains RAF20 by default.
            model_options = self._reference_geoid_options()
            self.reference_geoid_model.options = model_options

            default_model = self._reference_default_geoid_model()

            valid_models = {
                value
                for _, value
                in model_options
            }

            if (
                default_model in valid_models
                and (
                    self.reference_geoid_model.value
                    != default_model
                )
            ):
                self.reference_geoid_model.value = (
                    default_model
                )

        finally:
            self._updating_reference_controls = False

        self._rebuild_reference_dem_controls()

    def _rebuild_reference_dem_controls(self, change=None):
        if getattr(
            self,
            "_updating_reference_controls",
            False,
        ):
            return

        children = [
            self.widgets.HTML(
                value=(
                    "<div style='color:#555;margin-bottom:8px;line-height:1.5;'>"
                    "<b>ASP reference-height requirement:</b> automatically "
                    "downloaded reference DEMs are converted to "
                    "<b>ellipsoidal heights</b> before ASP.<br>"
                    "The prepared alignment and map-projection DEMs are also "
                    "reprojected to the <b>Target CRS (EPSG)</b> selected under "
                    "Advanced pre-processing."
                    "</div>"
                )
            ),
            self.widgets.HTML("<b>Reference DEM location and defaults</b>"),
            self._row(
                self.reference_region,
                "France starts from the tested IGN LiDAR + RAF20 configuration. "
                "Other/global starts from Copernicus for map projection and "
                "requires an existing high-resolution alignment DSM.",
            ),
            self._row(
                self.reference_aoi,
                "Normally leave this blank: the software reuses the AOI vector "
                "already entered under Prepare data. Enter a separate AOI only "
                "when no Prepare-data AOI exists or when a different reference "
                "DEM download extent is required.",
            ),
            self.widgets.HTML(
                value=(
                    "<div style='margin:2px 0 7px 205px;color:#666;"
                    "max-width:760px;font-size:12px;line-height:1.45;'>"
                    "<b>AOI behavior:</b> blank = reuse <b>Prepare data AOI</b>. "
                    "If both are blank and a DEM download is requested, "
                    "Pre-processing will ask for an AOI."
                    "</div>"
                )
            ),
            self.widgets.HTML(
                "<br><b>A. High-resolution alignment reference</b>"
            ),
            self._row(
                self.reference_alignment_source,
                "pc_align requires a high-resolution external reference. "
                "France defaults to IGN LiDAR HD. Outside France, provide "
                "an existing high-resolution DSM.",
            ),
            self._row(
                self.reference_alignment_resolution,
                "High-resolution alignment DSM resolution. "
                "Tested IGN LiDAR default: 1 m.",
            ),
        ]

        if self.reference_alignment_source.value == "existing":
            children.extend(
                [
                    self._row(
                        self.reference_existing_alignment,
                        "Required high-resolution DSM used by pc_align.",
                    ),
                    self.reference_existing_alignment_convert,
                    self.widgets.HTML(
                        value=(
                            "<div style='margin:4px 0 5px 205px;color:#8a5a00;"
                            "max-width:760px;font-size:12px;line-height:1.45;'>"
                            "<b>Note:</b> if conversion is not selected, the "
                            "existing high-resolution DSM is assumed to already "
                            "contain ellipsoidal heights."
                            "</div>"
                        )
                    ),
                ]
            )
        else:
            children.append(
                self.widgets.HTML(
                    value=(
                        "<div style='margin:4px 0 5px 205px;color:#555;"
                        "max-width:760px;font-size:12px;line-height:1.45;'>"
                        "IGN LiDAR HD is downloaded automatically and prepared "
                        "as the high-resolution ellipsoidal alignment reference."
                        "</div>"
                    )
                )
            )

        children.extend(
            [
                self.widgets.HTML(
                    "<br><b>B. Map-projection reference</b>"
                ),
                self._row(
                    self.reference_map_source,
                    "Reference surface used by ASP mapproject. In France the "
                    "default is IGN LiDAR HD; Copernicus, SRTM and an existing "
                    "DEM remain selectable.",
                ),
                self._row(
                    self.reference_map_resolution,
                    "Resolution of the generalized map-projection DEM. "
                    "LiDAR default: 50 m; Copernicus/SRTM default: 30 m. "
                    "The value is user-editable.",
                ),
            ]
        )

        if self.reference_map_source.value == "existing":
            children.extend(
                [
                    self._row(
                        self.reference_existing_map,
                        "Existing map-projection DEM. It will be reprojected/"
                        "resampled to the selected Target CRS and resolution.",
                    ),
                    self.reference_existing_map_convert,
                    self.widgets.HTML(
                        value=(
                            "<div style='margin:4px 0 5px 205px;color:#8a5a00;"
                            "max-width:760px;font-size:12px;line-height:1.45;'>"
                            "<b>Note:</b> if conversion is not selected, the "
                            "existing map-projection DEM is assumed to already "
                            "contain the vertical heights intended for ASP."
                            "</div>"
                        )
                    ),
                ]
            )

        conversion_needed = (
            self.reference_map_source.value
            in {
                "ign",
                "copernicus",
                "srtm",
            }
            or self.reference_alignment_source.value
            == "ign"
            or (
                self.reference_map_source.value == "existing"
                and self.reference_existing_map_convert.value
            )
            or (
                self.reference_alignment_source.value == "existing"
                and self.reference_existing_alignment_convert.value
            )
        )

        if conversion_needed:
            children.extend(
                [
                    self.widgets.HTML(
                        "<br><b>C. Vertical reference conversion</b>"
                    ),
                    self._row(
                        self.reference_geoid_model,
                        "France default: RAF20. Other/global defaults follow "
                        "the selected global source. The user can always "
                        "change the model.",
                    ),
                ]
            )

            if self.reference_geoid_model.value == "custom":
                children.append(
                    self._row(
                        self.reference_custom_n,
                        "Custom geoid/quasi-geoid separation raster "
                        "containing N = h − H in metres.",
                    )
                )

            if self.reference_region.value == "france":
                children.append(
                    self.widgets.HTML(
                        value=(
                            "<div style='margin:4px 0 5px 205px;"
                            "padding:8px 10px;border-left:3px solid #336699;"
                            "background:#f7f9fc;color:#555;max-width:760px;"
                            "font-size:12px;line-height:1.45;'>"
                            "<b>France default:</b> RAF20. If your reference "
                            "dataset uses another vertical model, change the "
                            "selector above."
                            "</div>"
                        )
                    )
                )

        children.extend(
            [
                self.widgets.HTML(
                    "<br><b>D. Download settings</b>"
                ),
                self._row(
                    self.reference_global_buffer,
                    "Buffer around the AOI for Copernicus/SRTM tile selection.",
                ),
            ]
        )

        if (
            self.reference_region.value == "france"
            and (
                self.reference_map_source.value == "ign"
                or self.reference_alignment_source.value == "ign"
            )
        ):
            children.extend(
                [
                    self._row(
                        self.reference_ign_buffer,
                        "Safety buffer around the AOI for IGN LiDAR HD tiles.",
                    ),
                    self._row(
                        self.reference_ign_workers,
                        "Concurrent IGN LiDAR HD tile downloads.",
                    ),
                ]
            )

        self.reference_dem_box.children = tuple(
            children
        )

    def _resolved_reference_aoi(self):
        value = self.reference_aoi.value.strip()

        if value:
            return value

        return self.aoi_vector.value.strip()

    def _prepare_reference_dem_inputs(self, settings):
        from pleiades_reference_dem import (
            IntegratedReferenceDEMSettings,
            prepare_integrated_reference_dems,
        )

        ref_settings = IntegratedReferenceDEMSettings(
            project_dir=settings.project_dir,
            target_epsg=int(
                self.target_epsg.value
            ),
            region=self.reference_region.value,
            aoi_path=self._resolved_reference_aoi(),
            map_source=self.reference_map_source.value,
            map_resolution_m=float(
                self.reference_map_resolution.value
            ),
            map_existing_path=(
                self.reference_existing_map.value.strip()
            ),
            map_existing_convert_to_ellipsoid=bool(
                self.reference_existing_map_convert.value
            ),
            alignment_source=self.reference_alignment_source.value,
            alignment_resolution_m=float(
                self.reference_alignment_resolution.value
            ),
            alignment_existing_path=(
                self.reference_existing_alignment.value.strip()
            ),
            alignment_existing_convert_to_ellipsoid=bool(
                self.reference_existing_alignment_convert.value
            ),
            geoid_model=self.reference_geoid_model.value,
            custom_n_raster=(
                self.reference_custom_n.value.strip()
            ),
            global_buffer_deg=float(
                self.reference_global_buffer.value
            ),
            ign_buffer_m=float(
                self.reference_ign_buffer.value
            ),
            ign_workers=int(
                self.reference_ign_workers.value
            ),
        )

        result = prepare_integrated_reference_dems(
            ref_settings,
            progress_callback=self._set_preprocess_progress,
        )

        # Feed the prepared paths into the original, unchanged ASP pipeline.
        self.alignment_dem.value = str(
            result["alignment_dem"]
        )
        self.mapproject_dem.value = str(
            result["mapproject_dem"]
        )

        model_text = html.escape(
            self.reference_geoid_model.label
            if hasattr(
                self.reference_geoid_model,
                "label",
            )
            else str(
                self.reference_geoid_model.value
            )
        )

        self.reference_dem_status.value = (
            "<div style='margin:6px 0 9px 0;padding:9px 11px;"
            "border-left:4px solid #2e7d32;background:#f4fbf4;"
            "color:#444;font-size:12px;line-height:1.5;'>"
            "<b>✓ Reference DEMs prepared.</b><br>"
            "<b>Alignment DSM:</b> "
            f"<code>{html.escape(str(result['alignment_dem']))}</code><br>"
            "<b>Map-projection DEM:</b> "
            f"<code>{html.escape(str(result['mapproject_dem']))}</code><br>"
            "<b>Configuration:</b> "
            f"<code>{html.escape(str(result['config_path']))}</code>"
            "</div>"
        )

        return result

    # --------------------------------------------------------
    # SETTINGS BUILDERS
    # --------------------------------------------------------
    def _build_pre_processing_settings(self):
        alignment_dem = (
            self.alignment_dem.value.strip()
        )
        mapproject_dem = (
            self.mapproject_dem.value.strip()
        )

        if not alignment_dem:
            raise ValueError(
                "Prepare the reference DEMs first. "
                "A high-resolution alignment DSM is required by pc_align."
            )

        if not mapproject_dem:
            raise ValueError(
                "Prepare the reference DEMs first. "
                "A generalized map-projection DEM is required by mapproject."
            )

        return PreProcessingSettings(
            alignment_dem=alignment_dem,
            mapproject_dem=mapproject_dem,
            target_epsg=int(
                self.target_epsg.value
            ),
            raw_resolution_m=float(
                self.map_resolution.value
            ),
            preliminary_pair=(
                self.preliminary_pair.value
            ),
            ba_robust_threshold=float(
                self.ba_robust_threshold.value
            ),
            ba_max_iterations=int(
                self.ba_max_iterations.value
            ),
            prelim_stereo_algorithm=(
                self._resolved_prelim_algorithm()
            ),
            prelim_xcorr_threshold=float(
                self.prelim_xcorr_threshold.value
            ),
            prelim_cost_mode=int(
                self.prelim_cost_mode.value
            ),
            prelim_corr_kernel=int(
                self._resolved_prelim_kernel_pair()[0]
            ),
            prelim_subpixel_kernel=int(
                self._resolved_prelim_kernel_pair()[1]
            ),
            prelim_subpixel_mode=int(
                self.prelim_subpixel_mode.value
            ),
            corr_memory_limit_mb=int(
                self.prelim_corr_memory.value
            ),
            corr_tile_size=int(
                self.prelim_corr_tile_size.value
            ),
            pc_align_max_displacement_m=float(
                self.pc_align_max_displacement.value
            ),
            pc_align_iterations=int(
                self.pc_align_iterations.value
            ),
            aligned_ba_threads=int(
                self.processing_threads.value
            ),
            mapproject_threads=int(
                self.processing_threads.value
            ),
        )

    def _build_final_settings(self):
        kernel_selection = tuple(
            self.kernel_selector.value
        )

        custom_kernel_selected = (
            "CUSTOM_KERNEL" in kernel_selection
        )

        selected_pairs = [
            tuple(value)
            for value in kernel_selection
            if value != "CUSTOM_KERNEL"
        ]

        if custom_kernel_selected:
            custom_pairs = _parse_custom_kernel_pairs(
                self.custom_kernel_pairs.value
            )

            if not custom_pairs:
                raise ValueError(
                    "You selected 'Other / custom CK:SK'. "
                    "Enter at least one pair in the 'Additional CK:SK' field, "
                    "for example 11:23."
                )

            selected_pairs.extend(
                custom_pairs
            )

        unique_pairs = []
        for pair in selected_pairs:
            if pair not in unique_pairs:
                unique_pairs.append(pair)

        algorithm_choice = self.final_algorithm.value

        if algorithm_choice == "CUSTOM":
            algorithm_tag = (
                self.custom_algorithm_name.value.strip()
            )

            if not algorithm_tag:
                raise ValueError(
                    "Select an algorithm preset or enter a custom "
                    "ASP stereo algorithm."
                )
        else:
            algorithm_tag = algorithm_choice

        return FinalProcessingSettings(
            stereo_mode=self.final_mode.value,
            single_pairs=tuple(
                self.single_pair_selector.value
            ),
            dual_configurations=tuple(
                self.dual_selector.value
            ),
            algorithm_tag=algorithm_tag,
            custom_cost_mode=int(
                self.custom_cost_mode.value
            ),
            kernel_pairs=tuple(
                unique_pairs
            ),
            xcorr_threshold=float(
                self.final_xcorr_threshold.value
            ),
            corr_memory_limit_mb=int(
                self.final_corr_memory.value
            ),
            corr_tile_size=int(
                self.final_corr_tile_size.value
            ),
            subpixel_mode=int(
                self.final_subpixel_mode.value
            ),
            pc_merge_threads=int(
                self.pc_merge_threads.value
            ),
            final_dsm_resolution_m=float(
                self.final_dsm_resolution.value
            ),
            max_valid_triangulation_error_m=float(
                self.max_triangulation_error.value
            ),
            final_dsm_threads=int(
                self.final_dsm_threads.value
            ),
            create_error_image=bool(
                self.create_error_image.value
            ),
        )

    def _prelim_kernel_presets(self):
        choice = self.prelim_algorithm.value

        if choice == "BM":
            return (
                (5, 9),
                (7, 15),
                (9, 21),
                (15, 25),
                (25, 35),
                (35, 45),
            )

        if choice in {"SGM", "MGM"}:
            return (
                (5, 9),
                (7, 15),
                (9, 21),
            )

        # Custom algorithm: provide the common starting presets but allow
        # the user to select Other/custom CK:SK.
        return (
            (5, 9),
            (7, 15),
            (9, 21),
        )

    def _resolved_prelim_algorithm(self):
        choice = self.prelim_algorithm.value

        if choice in FINAL_ALGORITHMS:
            return FINAL_ALGORITHMS[choice]["asp_algorithm"]

        value = self.prelim_custom_algorithm.value.strip()
        if not value:
            raise ValueError(
                "Select a preliminary algorithm preset or enter the custom "
                "ASP --stereo-algorithm value."
            )
        return value

    def _resolved_prelim_kernel_pair(self):
        value = self.prelim_kernel_selector.value

        if value == "CUSTOM_KERNEL":
            custom = self.prelim_custom_kernel.value.strip()
            pairs = _parse_custom_kernel_pairs(custom)

            if len(pairs) != 1:
                raise ValueError(
                    "Enter exactly one custom preliminary CK:SK pair, "
                    "for example 11:23."
                )
            return tuple(pairs[0])

        if not (
            isinstance(value, tuple)
            and len(value) == 2
        ):
            raise ValueError(
                "Select a preliminary CK:SK pair."
            )

        return tuple(value)

    def _apply_prelim_algorithm_selection(self):
        choice = self.prelim_algorithm.value

        if choice in FINAL_ALGORITHMS:
            preset = FINAL_ALGORITHMS[choice]
            self.prelim_cost_mode.value = int(
                preset["cost_mode"]
            )
            self.prelim_cost_mode.disabled = True
        else:
            self.prelim_cost_mode.disabled = False

        pairs = self._prelim_kernel_presets()
        previous = self.prelim_kernel_selector.value

        options = [
            (f"{ck} : {sk}", (ck, sk))
            for ck, sk in pairs
        ]
        options.append(
            (
                "Other / custom CK:SK → enter below",
                "CUSTOM_KERNEL",
            )
        )
        self.prelim_kernel_selector.options = options

        # Preserve a compatible user choice where possible.
        valid_values = set(pairs) | {"CUSTOM_KERNEL"}
        if previous in valid_values:
            self.prelim_kernel_selector.value = previous
        elif choice == "BM":
            # Exact tested preliminary BM default.
            self.prelim_kernel_selector.value = (35, 45)
        else:
            self.prelim_kernel_selector.value = (9, 21)

        self._rebuild_preprocess_advanced_controls()

    def _rebuild_preprocess_advanced_controls(self):
        children = [
            self.target_epsg_row,
            self.crs_information_note,
            self._row(
                self.ba_robust_threshold,
                "Exact tested bundle-adjustment default: 2.0.",
            ),
            self._row(
                self.ba_max_iterations,
                "Exact tested bundle-adjustment default: 500.",
            ),
            self.prelim_algorithm_row,
        ]

        if self.prelim_algorithm.value == "CUSTOM":
            children.append(
                self.prelim_custom_algorithm_row
            )

        children.append(
            self.prelim_kernel_row
        )

        if self.prelim_kernel_selector.value == "CUSTOM_KERNEL":
            children.append(
                self.prelim_custom_kernel_row
            )

        children.extend(
            [
                self.prelim_cost_mode_row,
                self._row(
                    self.prelim_xcorr_threshold,
                    "Preliminary parallel_stereo --xcorr-threshold. "
                    "Tested default: 2.0.",
                ),
                self._row(
                    self.prelim_corr_memory,
                    "Preliminary correlation memory limit in MB. "
                    "Tested default: 10240.",
                ),
                self._row(
                    self.prelim_corr_tile_size,
                    "Preliminary correlation tile size. "
                    "Tested default: 3200.",
                ),
                self._row(
                    self.prelim_subpixel_mode,
                    "Preliminary ASP subpixel mode. Tested default: 2.",
                ),
                self._row(
                    self.pc_align_max_displacement,
                    "Exact tested pc_align maximum displacement: 250 m.",
                ),
                self._row(
                    self.pc_align_iterations,
                    "Exact tested pc_align iterations: 100.",
                ),
                self._row(
                    self.processing_threads,
                    "Exact tested value for camera-transform BA and "
                    "mapproject: 18 threads.",
                ),
            ]
        )

        self.preprocess_advanced_box.children = tuple(children)

    def _on_prelim_algorithm_change(self, change=None):
        self._apply_prelim_algorithm_selection()

    def _on_prelim_kernel_change(self, change=None):
        self._rebuild_preprocess_advanced_controls()

    # --------------------------------------------------------
    # VISIBILITY
    # --------------------------------------------------------
    def _rebuild_point_cloud_controls(self):
        """Render only the controls relevant to the current point-cloud mode."""
        children = [self.final_mode_row]

        is_tri_acquisition = (
            self.acquisition_mode.value == "tri_stereo"
        )
        mode = self.final_mode.value

        # Geometry controls.
        if not is_tri_acquisition:
            # A true two-image stereo acquisition contains only A/B.
            children.append(self.single_pair_row)
        elif mode == "single":
            children.append(self.single_pair_row)
        elif mode == "dual":
            children.append(self.dual_selector_row)
        elif mode == "tri":
            children.append(self.tri_mode_info)

        # Algorithm controls.
        children.append(self.final_algorithm_row)

        if self.final_algorithm.value == "CUSTOM":
            children.append(self.custom_algorithm_row)

        children.append(
            self.kernel_selector_row
        )

        if (
            "CUSTOM_KERNEL"
            in tuple(self.kernel_selector.value)
        ):
            children.append(
                self.custom_kernel_row
            )

        children.append(
            self.auto_dsm_row
        )

        self.point_cloud_controls_box.children = tuple(children)

    def _configure_geometry_controls(self):
        """
        Configure the available final-stereo modes from the acquisition type.

        This method is called only when the acquisition type changes (or once
        during initialization). It does not run when the user merely changes
        Single / Ordered three-image / Tri mode.
        """
        is_tri = self.acquisition_mode.value == "tri_stereo"

        if not is_tri:
            # True stereo acquisition: only A/B exists.
            self.preliminary_pair.options = [
                ("AB — Stereo pair", "AB")
            ]
            self.preliminary_pair.value = "AB"

            self.final_mode.options = [
                ("Single pair(s)", "single")
            ]
            self.final_mode.value = "single"

            self.single_pair_selector.options = [
                ("AB — stereo pair", "AB")
            ]
            self.single_pair_selector.value = ("AB",)
            return

        # Tri-stereo acquisition A/B/C.
        self.preliminary_pair.options = [
            ("AC — Forward–Backward", "AC"),
            ("AB — Forward–Middle", "AB"),
            ("BC — Middle–Backward", "BC"),
        ]

        # Keep the current geometry mode if it is still valid.
        current_mode = self.final_mode.value
        valid_modes = {"single", "dual", "tri"}

        self.final_mode.options = [
            ("Single pair(s)", "single"),
            ("Ordered three-image configuration(s)", "dual"),
            ("Tri — AB + AC + BC then merge", "tri"),
        ]

        if current_mode in valid_modes:
            self.final_mode.value = current_mode
        else:
            self.final_mode.value = "single"

        self.single_pair_selector.options = [
            ("AB — FM", "AB"),
            ("AC — FB", "AC"),
            ("BC — MB", "BC"),
        ]

        current_pairs = tuple(
            value
            for value in self.single_pair_selector.value
            if value in {"AB", "AC", "BC"}
        )
        self.single_pair_selector.value = current_pairs or ("AC",)

        self.dual_selector.options = [
            ("ABC — FMB", "ABC"),
            ("BAC — MFB", "BAC"),
            ("CAB — BFM", "CAB"),
        ]

        current_dual = tuple(
            value
            for value in self.dual_selector.value
            if value in {"ABC", "BAC", "CAB"}
        )
        self.dual_selector.value = current_dual or ("CAB",)

    def _apply_algorithm_selection(self):
        """
        Apply the selected algorithm preset to cost mode and CK:SK choices.

        BM  -> cost mode 2, BM kernel presets
        SGM -> cost mode 4, SGM kernel presets
        MGM -> cost mode 4, MGM kernel presets
        CUSTOM -> user controls the algorithm name, cost mode and may add any
                  CK:SK pair in the Additional CK:SK field.
        """
        choice = self.final_algorithm.value

        if choice in FINAL_ALGORITHMS:
            preset = FINAL_ALGORITHMS[choice]
            pairs = tuple(preset["kernel_pairs"])

            # Cost mode is a locked, algorithm-dependent preset.
            self.custom_cost_mode.value = int(preset["cost_mode"])
            self.custom_cost_mode.disabled = True
        else:
            # Custom algorithm: expose editable parameters. Keep the common
            # starter kernel list, while Additional CK:SK accepts any pair.
            pairs = (
                (5, 9),
                (7, 15),
                (9, 21),
            )
            self.custom_cost_mode.disabled = False

        previous = tuple(self.kernel_selector.value)

        kernel_options = [
            (f"{ck} : {sk}", (ck, sk))
            for ck, sk in pairs
        ]

        # Explicit visual option for users who want a kernel pair that is
        # not included in the tested presets. The actual custom CK:SK value
        # is entered in the "Additional CK:SK" field directly below.
        kernel_options.append(
            (
                "Other / custom CK:SK → enter below",
                "CUSTOM_KERNEL",
            )
        )

        self.kernel_selector.options = kernel_options

        retained = tuple(
            value
            for value in previous
            if (
                value == "CUSTOM_KERNEL"
                or value in pairs
            )
        )

        if retained:
            self.kernel_selector.value = retained
        elif (9, 21) in pairs:
            self.kernel_selector.value = ((9, 21),)
        else:
            self.kernel_selector.value = (pairs[0],)

    def _on_acquisition_mode_change(self, change=None):
        self._configure_geometry_controls()
        self._rebuild_point_cloud_controls()

    def _on_final_mode_change(self, change=None):
        # Geometry mode changes should only redraw the appropriate selector;
        # they must not rewrite the mode options themselves.
        self._rebuild_point_cloud_controls()

    def _on_final_algorithm_change(self, change=None):
        # Algorithm changes update cost mode, kernel presets, and the optional
        # custom-algorithm field in one deterministic callback.
        self._apply_algorithm_selection()
        self._rebuild_point_cloud_controls()

    def _on_kernel_selection_change(self, change=None):
        # Selecting/deselecting the custom-kernel option only controls
        # visibility of the Additional CK:SK input.
        self._rebuild_point_cloud_controls()

    # --------------------------------------------------------
    # PROGRESS
    # --------------------------------------------------------
    def _set_preprocess_progress(
        self,
        value,
        message,
    ):
        self.preprocess_progress.value = int(
            value
        )
        self.preprocess_progress_text.value = (
            "<span style='color:#555;'>"
            + html.escape(message)
            + "</span>"
        )

    def _set_point_cloud_progress(
        self,
        value,
        message,
    ):
        self.point_cloud_progress.value = int(
            value
        )
        self.point_cloud_progress_text.value = (
            "<span style='color:#555;'>"
            + html.escape(message)
            + "</span>"
        )

    def _set_final_dsm_progress(
        self,
        value,
        message,
    ):
        self.final_dsm_progress.value = int(
            value
        )
        self.final_dsm_progress_text.value = (
            "<span style='color:#555;'>"
            + html.escape(message)
            + "</span>"
        )

    def _reset_preprocess_stage_tabs(self):
        from IPython.display import clear_output, display

        for index, stage in enumerate(
            self._preprocess_stage_order
        ):
            output = self._preprocess_stage_outputs[
                stage
            ]

            with output:
                clear_output(wait=False)
                display(
                    self.widgets.HTML(
                        "<div style='padding:12px;color:#777;'>"
                        "Waiting for this processing step."
                        "</div>"
                    )
                )

            self.preprocess_stage_tabs.set_title(
                index,
                self._preprocess_stage_labels[stage],
            )

        self.preprocess_stage_tabs.selected_index = 0
        self.preprocess_results.children = (
            self.preprocess_stage_tabs,
        )

    def _display_original_style_block(
        self,
        title,
        lines,
    ):
        safe_lines = "\n".join(
            html.escape(str(line))
            for line in lines
        )

        return self.widgets.HTML(
            "<div style='padding:8px 10px;'>"
            f"<b>{html.escape(title)}</b>"
            "<pre style='margin-top:8px;white-space:pre-wrap;"
            "font-family:monospace;background:#fafafa;"
            "border:1px solid #e3e3e3;padding:10px;'>"
            f"{safe_lines}"
            "</pre></div>"
        )

    def _update_preprocess_stage_result(
        self,
        stage,
        status,
        payload,
    ):
        from IPython.display import clear_output, display

        if stage not in self._preprocess_stage_outputs:
            return

        index = self._preprocess_stage_order.index(
            stage
        )

        label = self._preprocess_stage_labels[
            stage
        ]

        prefixes = {
            "running": "⏳ ",
            "completed": "✓ ",
            "failed": "✗ ",
        }

        self.preprocess_stage_tabs.set_title(
            index,
            prefixes.get(status, "") + label,
        )

        output = self._preprocess_stage_outputs[
            stage
        ]

        with output:
            clear_output(wait=True)

            if status == "running":
                display(
                    self.widgets.HTML(
                        "<div style='padding:12px;"
                        "border-left:4px solid #1976d2;"
                        "background:#f4f8ff;'>"
                        f"<b>{html.escape(label)}</b><br>"
                        "Processing is running. "
                        "The detailed ASP output is being written "
                        "to the log file."
                        "</div>"
                    )
                )
                self.preprocess_stage_tabs.selected_index = (
                    index
                )
                return

            if status == "failed":
                display(
                    self.widgets.HTML(
                        "<div style='padding:12px;"
                        "border-left:4px solid #b00020;"
                        "background:#fff4f4;'>"
                        f"<b>✗ {html.escape(label)} stopped.</b><br>"
                        f"{html.escape(str(payload.get('error', '')))}"
                        "<br><br><b>Detailed log:</b> "
                        f"<code>{html.escape(str(payload.get('log', '')))}</code>"
                        "</div>"
                    )
                )
                self.preprocess_stage_tabs.selected_index = (
                    index
                )
                return

            # ------------------------------------------------
            # Exact meaningful outputs from the original code
            # ------------------------------------------------
            if stage == "bundle_adjustment":
                display(
                    self.widgets.HTML(
                        "<b>Bundle-adjustment residuals</b>"
                        "<div style='color:#666;margin:4px 0 8px;'>"
                        "Initial and final camera residual statistics "
                        "from the original ASP residual files."
                        "</div>"
                    )
                )
                display(
                    payload["table"]
                )
                display(
                    self.widgets.HTML(
                        "<b>Saved table:</b> "
                        f"<code>{html.escape(str(payload['csv']))}</code>"
                        "<br><b>Detailed log:</b> "
                        f"<code>{html.escape(str(payload['log']))}</code>"
                    )
                )

            elif stage == "preliminary_stereo":
                lines = [
                    "============================================================",
                    "INITIAL STEREO CORRELATION",
                    "============================================================",
                    f"Pair          : {payload['pair']}",
                    f"Algorithm     : {payload.get('algorithm', '')}",
                    f"Cost mode     : {payload.get('cost_mode', '')}",
                    f"CK : SK       : {payload.get('corr_kernel', '')} : {payload.get('subpixel_kernel', '')}",
                    f"Xcorr thresh. : {payload.get('xcorr_threshold', '')}",
                    f"Subpixel mode : {payload.get('subpixel_mode', '')}",
                    f"Left image    : {payload['left']}",
                    f"Right image   : {payload['right']}",
                    f"Output prefix : {payload['prefix']}",
                    "============================================================",
                    "",
                    "Preliminary stereo point cloud completed:",
                    f"  {payload['point_cloud']}",
                ]

                display(
                    self._display_original_style_block(
                        "Original-style preliminary stereo result",
                        lines,
                    )
                )

                info = payload[
                    "point_cloud_info"
                ]

                display(
                    pd.DataFrame(
                        [
                            {
                                "Raster size": (
                                    f"{info['Width']} × "
                                    f"{info['Height']}"
                                ),
                                "Bands": info["Bands"],
                                "Point cloud": str(
                                    payload[
                                        "point_cloud"
                                    ]
                                ),
                            }
                        ]
                    )
                )

                display(
                    self.widgets.HTML(
                        "<b>Detailed log:</b> "
                        f"<code>{html.escape(str(payload['log']))}</code>"
                    )
                )

            elif stage == "preliminary_dem":
                info = payload["dem_info"]

                lines = [
                    "============================================================",
                    "GENERATE PRELIMINARY ALIGNMENT DEM",
                    "============================================================",
                    f"Input point cloud : {payload.get('point_cloud', '')}",
                    f"Target CRS        : {info['CRS']}",
                    f"Resolution        : {info['Pixel X']:g} m",
                    f"Output DEM        : {payload['dem']}",
                    "============================================================",
                ]

                display(
                    self._display_original_style_block(
                        "Original-style DEM summary",
                        lines,
                    )
                )

                display(
                    payload["plot"]["figure"]
                )

                display(
                    self.widgets.HTML(
                        "<b>Saved figure:</b> "
                        f"<code>{html.escape(str(payload['plot']['pdf']))}</code>"
                        "<br><b>Detailed log:</b> "
                        f"<code>{html.escape(str(payload['log']))}</code>"
                    )
                )

            elif stage == "lidar_alignment":
                result = payload[
                    "alignment_results"
                ]

                lines = [
                    "============================================================",
                    "PRELIMINARY DEM ALIGNMENT",
                    "============================================================",
                    f"Reference LiDAR DSM : {payload['reference']}",
                    f"Preliminary DSM     : {payload['source']}",
                    f"Output transform    : {payload['transform']}",
                    "============================================================",
                    "",
                ]

                lines.extend(
                    result[
                        "important_lines"
                    ]
                )

                display(
                    self._display_original_style_block(
                        "Important pc_align output",
                        lines,
                    )
                )

                matrix = result["matrix"]

                if matrix is not None:
                    matrix_df = pd.DataFrame(
                        matrix,
                        index=[
                            "row 1",
                            "row 2",
                            "row 3",
                            "row 4",
                        ],
                        columns=[
                            "col 1",
                            "col 2",
                            "col 3",
                            "col 4",
                        ],
                    )

                    display(
                        self.widgets.HTML(
                            "<b>Saved 4 × 4 transform matrix</b>"
                        )
                    )
                    display(matrix_df)

                display(
                    self.widgets.HTML(
                        "<b>Transform file:</b> "
                        f"<code>{html.escape(str(payload['transform']))}</code>"
                        "<br><b>Detailed log:</b> "
                        f"<code>{html.escape(str(payload['log']))}</code>"
                    )
                )

            elif stage == "camera_transform":
                lines = [
                    "============================================================",
                    "APPLY ALIGNMENT TRANSFORM TO CAMERAS",
                    "============================================================",
                    f"Input transform : {payload['transform']}",
                    f"Output prefix   : {payload['prefix']}",
                ]

                display(
                    self._display_original_style_block(
                        "Camera-transform result",
                        lines,
                    )
                )

                display(
                    payload["table"]
                )

                display(
                    self.widgets.HTML(
                        "<b>Detailed log:</b> "
                        f"<code>{html.escape(str(payload['log']))}</code>"
                    )
                )

            elif stage == "map_projection":
                display(
                    self.widgets.HTML(
                        "<b>Map-projected image outputs</b>"
                    )
                )
                display(
                    payload["table"]
                )
                display(
                    payload["plot"]["figure"]
                )
                display(
                    self.widgets.HTML(
                        "<b>Saved figure:</b> "
                        f"<code>{html.escape(str(payload['plot']['pdf']))}</code>"
                        "<br><b>Projection DEM:</b> "
                        f"<code>{html.escape(str(payload['mapproject_dem']))}</code>"
                    )
                )

        # Do not force the user away from an earlier tab after it has
        # completed. Only a newly running step automatically opens.

    # --------------------------------------------------------
    # PRE-PROCESSING ACTION
    # --------------------------------------------------------
    def _on_pre_processing(self, _):
        self.preprocess_summary.value = ""
        self.preprocess_progress.bar_style = ""
        self.run_preprocessing.disabled = True

        self._reset_preprocess_stage_tabs()

        try:
            settings = self._build_settings()

            self._set_preprocess_progress(
                1,
                "Preparing reference DEMs",
            )

            reference_result = (
                self._prepare_reference_dem_inputs(
                    settings
                )
            )

            processing = (
                self._build_pre_processing_settings()
            )

            result = run_pre_processing(
                settings,
                processing,
                progress_callback=(
                    self._set_preprocess_progress
                ),
                result_callback=(
                    self._update_preprocess_stage_result
                ),
            )

            self.last_pre_processing = result

            paths = result["paths"]

            map_lines = "<br>".join(
                (
                    f"<code>{view}: "
                    f"{html.escape(str(paths['mapprojected'][view]))}"
                    "</code>"
                )
                for view in settings.image_names
            )

            self.preprocess_summary.value = (
                "<div style='margin:10px 0;"
                "padding:10px;border-left:4px "
                "solid #2e7d32;background:#f4fbf4;'>"
                "<b>✓ Pre-processing completed.</b><br>"
                "Use the six tabs below to inspect the result "
                "of every original pre-processing step.<br><br>"
                "<b>Alignment reference:</b> "
                f"<code>{html.escape(processing.alignment_dem)}</code><br>"
                "<b>Map-projection reference:</b> "
                f"<code>{html.escape(processing.mapproject_dem)}</code><br><br>"
                f"<b>Preliminary pair:</b> "
                f"{html.escape(paths['pair'])}<br>"
                f"<b>Preliminary algorithm:</b> "
                f"{html.escape(processing.prelim_stereo_algorithm)}<br>"
                f"<b>Preliminary CK:SK:</b> "
                f"{processing.prelim_corr_kernel}:{processing.prelim_subpixel_kernel}<br>"
                "<b>Preliminary DSM:</b> "
                f"<code>{html.escape(str(paths['prelim_dem']))}</code><br>"
                "<b>Alignment transform:</b> "
                f"<code>{html.escape(str(paths['align_transform']))}</code>"
                "<br><br><b>Map-projected images:</b><br>"
                f"{map_lines}<br><br>"
                "<b>Detailed ASP logs:</b> "
                f"<code>{html.escape(str(paths['log_dir']))}</code>"
                "</div>"
            )

            # The tab outputs already contain rendered static figures.
            plt.close(
                result[
                    "preliminary_plot"
                ]["figure"]
            )
            plt.close(
                result[
                    "map_plot"
                ]["figure"]
            )

            self.preprocess_progress.bar_style = (
                "success"
            )

        except Exception as exc:
            self.preprocess_progress.bar_style = (
                "danger"
            )

            try:
                settings = self._build_settings()
                log_dir = (
                    _processing_log_dir(
                        settings
                    )
                )
            except Exception:
                log_dir = Path(
                    "(log path unavailable)"
                )

            self.preprocess_summary.value = (
                "<div style='margin:10px 0;"
                "padding:10px;border-left:4px "
                "solid #b00020;background:#fff4f4;'>"
                "<b>✗ Pre-processing stopped.</b><br>"
                f"{html.escape(type(exc).__name__ + ': ' + str(exc))}"
                "<br><br>"
                "Any tabs completed before the error remain available "
                "for inspection."
                "<br><b>Detailed ASP logs:</b> "
                f"<code>{html.escape(str(log_dir))}</code>"
                "</div>"
            )

        finally:
            self.run_preprocessing.disabled = False

    # --------------------------------------------------------
    # POINT-CLOUD ACTION
    # --------------------------------------------------------
    def _on_point_cloud(self, _):
        self.point_cloud_summary.value = ""
        self.point_cloud_results.children = ()
        self.point_cloud_progress.bar_style = ""
        self.run_point_cloud.disabled = True

        try:
            settings = self._build_settings()
            processing = (
                self._build_pre_processing_settings()
            )
            final = self._build_final_settings()

            result = run_point_cloud_reconstruction(
                settings,
                processing,
                final,
                progress_callback=(
                    self._set_point_cloud_progress
                ),
            )

            self.last_point_cloud = result

            self.point_cloud_results.children = (
                self.widgets.HTML(
                    self._dataframe_html(
                        result[
                            "products_df"
                        ]
                    )
                ),
            )

            self.point_cloud_summary.value = (
                "<div style='margin:10px 0;"
                "padding:10px;border-left:4px "
                "solid #2e7d32;background:#f4fbf4;'>"
                "<b>✓ Point-cloud reconstruction completed.</b><br>"
                f"<b>Mode:</b> "
                f"{html.escape(final.stereo_mode)}<br>"
                f"<b>Algorithm:</b> "
                f"{html.escape(final.algorithm_tag)}<br>"
                "<b>Saved product table:</b> "
                f"<code>{html.escape(str(result['products_csv']))}</code>"
                "<br><b>Detailed ASP logs:</b> "
                f"<code>{html.escape(str(_processing_log_dir(settings)))}</code>"
                "</div>"
            )

            self.point_cloud_progress.bar_style = (
                "success"
            )

            if self.auto_generate_dsm.value:
                self._on_final_dsm(None)

        except Exception as exc:
            self.point_cloud_progress.bar_style = (
                "danger"
            )

            try:
                settings = self._build_settings()
                log_dir = (
                    _processing_log_dir(
                        settings
                    )
                )
            except Exception:
                log_dir = Path(
                    "(log path unavailable)"
                )

            self.point_cloud_summary.value = (
                "<div style='margin:10px 0;"
                "padding:10px;border-left:4px "
                "solid #b00020;background:#fff4f4;'>"
                "<b>✗ Point-cloud reconstruction stopped.</b><br>"
                f"{html.escape(type(exc).__name__ + ': ' + str(exc))}"
                "<br><br><b>Detailed ASP logs:</b> "
                f"<code>{html.escape(str(log_dir))}</code>"
                "</div>"
            )

        finally:
            self.run_point_cloud.disabled = False

    # --------------------------------------------------------
    # FINAL DSM ACTION
    # --------------------------------------------------------
    def _on_final_dsm(self, _):
        self.final_dsm_summary.value = ""
        self.final_dsm_results.children = ()
        self.final_dsm_progress.bar_style = ""
        self.run_final_dsm.disabled = True

        try:
            settings = self._build_settings()
            processing = (
                self._build_pre_processing_settings()
            )
            final = self._build_final_settings()

            result = generate_final_dsms(
                settings,
                processing,
                final,
                progress_callback=(
                    self._set_final_dsm_progress
                ),
            )

            self.last_final_dsm = result

            table_tab = self.widgets.HTML(
                self._dataframe_html(
                    result["products_df"]
                )
            )

            children = [table_tab]
            titles = ["Generated DSMs"]

            for product in result[
                "products"
            ]:
                output = self.widgets.Output()

                with output:
                    self.display_fn(
                        product["plot"][
                            "figure"
                        ]
                    )

                children.append(output)
                titles.append(
                    product["product_tag"]
                )

            tabs = self.widgets.Tab(
                children=children
            )

            for index, title in enumerate(
                titles
            ):
                tabs.set_title(
                    index,
                    title,
                )

            self.final_dsm_results.children = (
                tabs,
            )

            self.final_dsm_summary.value = (
                "<div style='margin:10px 0;"
                "padding:10px;border-left:4px "
                "solid #2e7d32;background:#f4fbf4;'>"
                "<b>✓ Final DSM generation completed.</b><br>"
                f"<b>DSM resolution:</b> "
                f"{final.final_dsm_resolution_m} m<br>"
                "<b>Triangulation-error limit:</b> "
                f"{final.max_valid_triangulation_error_m} m<br>"
                "<b>Saved product table:</b> "
                f"<code>{html.escape(str(result['products_csv']))}</code>"
                "<br><b>Figures:</b> "
                f"<code>{html.escape(str(settings.figure_dir))}</code>"
                "<br><b>Detailed ASP logs:</b> "
                f"<code>{html.escape(str(_processing_log_dir(settings)))}</code>"
                "</div>"
            )

            for product in result[
                "products"
            ]:
                plt.close(
                    product["plot"][
                        "figure"
                    ]
                )

            self.final_dsm_progress.bar_style = (
                "success"
            )

        except Exception as exc:
            self.final_dsm_progress.bar_style = (
                "danger"
            )

            try:
                settings = self._build_settings()
                log_dir = (
                    _processing_log_dir(
                        settings
                    )
                )
            except Exception:
                log_dir = Path(
                    "(log path unavailable)"
                )

            self.final_dsm_summary.value = (
                "<div style='margin:10px 0;"
                "padding:10px;border-left:4px "
                "solid #b00020;background:#fff4f4;'>"
                "<b>✗ Final DSM generation stopped.</b><br>"
                f"{html.escape(type(exc).__name__ + ': ' + str(exc))}"
                "<br><br><b>Detailed ASP logs:</b> "
                f"<code>{html.escape(str(log_dir))}</code>"
                "</div>"
            )

        finally:
            self.run_final_dsm.disabled = False


# Override only the entry point so the original v0.5 interface becomes
# the base of the extended full workflow.
def project_setup():
    return FullProjectSetupUI().display()
