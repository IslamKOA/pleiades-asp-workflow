from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import warnings

import numpy as np
import rasterio
from pyproj import Transformer, datadir, network
from pyproj.aoi import AreaOfInterest
from pyproj.transformer import TransformerGroup
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window
from scipy.interpolate import RegularGridInterpolator

MODEL_LABELS = {
    "ellipsoid": "Ellipsoidal height",
    "raf20": "NGF-IGN69 / RAF20",
    "egm96": "EGM96",
    "egm2008": "EGM2008",
    "custom": "Custom geoid/quasi-geoid",
}

PROJ_MODELS = {
    "egm96": {
        "compound_crs": "EPSG:4326+5773",
        "label": "EGM96",
        "asp_name": "EGM96",
    },
    "egm2008": {
        "compound_crs": "EPSG:4326+3855",
        "label": "EGM2008",
        "asp_name": "EGM2008",
    },
}


def _windows(width, height, tile_size=512):
    for row_off in range(0, height, tile_size):
        h = min(tile_size, height - row_off)
        for col_off in range(0, width, tile_size):
            w = min(tile_size, width - col_off)
            yield Window(col_off, row_off, w, h)


def _window_xy(transform, window):
    rows, cols = np.indices((int(window.height), int(window.width)))
    rows = rows + int(window.row_off)
    cols = cols + int(window.col_off)

    xs = (
        transform.c
        + (cols + 0.5) * transform.a
        + (rows + 0.5) * transform.b
    )
    ys = (
        transform.f
        + (cols + 0.5) * transform.d
        + (rows + 0.5) * transform.e
    )
    return xs, ys


def _float_profile(src):
    profile = src.profile.copy()
    profile.pop("blockxsize", None)
    profile.pop("blockysize", None)
    use_tiles = src.width >= 256 and src.height >= 256
    profile.update(
        driver="GTiff",
        dtype="float32",
        count=1,
        nodata=np.nan,
        compress="deflate",
        tiled=use_tiles,
        BIGTIFF="IF_SAFER",
    )
    if use_tiles:
        profile.update(blockxsize=256, blockysize=256)
    return profile


def _validate_model_raster(path, label="geoid separation model"):
    """
    Ensure a generated N raster contains finite values.

    A failed vertical-grid transformation must never propagate as an all-NoData
    final DEM while still being reported as successful.
    """
    path = Path(path)

    with rasterio.open(path) as src:
        data = src.read(1, masked=True)
        valid = np.asarray(data.compressed(), dtype=np.float64)

    valid = valid[np.isfinite(valid)]

    if valid.size == 0:
        raise RuntimeError(
            f"The generated {label} contains no finite values: {path}"
        )

    return {
        "count": int(valid.size),
        "min": float(np.min(valid)),
        "max": float(np.max(valid)),
        "mean": float(np.mean(valid)),
    }


# ---------------------------------------------------------------------
# RAF20
# ---------------------------------------------------------------------

def load_raf20(raf_path):
    """Load the supplied IGN RAF20 TAC grid as N = h - H."""
    raf_path = Path(raf_path)

    with raf_path.open("r", encoding="utf-8", errors="replace") as f:
        header = f.readline().strip().split()
        min_lon = float(header[0])
        max_lon = float(header[1])
        min_lat = float(header[2])
        max_lat = float(header[3])
        step_lon = float(header[4])
        step_lat = float(header[5])

        height = int(round((max_lat - min_lat) / step_lat)) + 1
        width = int(round((max_lon - min_lon) / step_lon)) + 1
        values = np.full((height, width), np.nan, dtype=np.float64)

        i = 0
        j = height - 1
        for line in f:
            separation_values = line.split()[::2]
            for raw in separation_values:
                if j < 0:
                    break
                values[j, i] = float(raw)
                i += 1
                if i == width:
                    i = 0
                    j -= 1
            if j < 0:
                break

    lon_grid = np.linspace(min_lon, max_lon, width)
    lat_grid = np.linspace(min_lat, max_lat, height)

    interpolator = RegularGridInterpolator(
        (lat_grid, lon_grid),
        values,
        method="linear",
        bounds_error=False,
        fill_value=np.nan,
    )

    return {
        "interpolator": interpolator,
        "bounds": (min_lon, min_lat, max_lon, max_lat),
    }


def build_raf20_n_raster(template_dem, output_path, raf_path):
    """Create RAF20 N = h - H on the exact template DEM grid."""
    template_dem = Path(template_dem)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    raf = load_raf20(raf_path)
    interpolator = raf["interpolator"]
    outside = 0

    with rasterio.open(template_dem) as src:
        if src.crs is None:
            raise ValueError("RAF20 evaluation requires a defined horizontal CRS.")

        to_rgf93_geo = Transformer.from_crs(
            src.crs,
            "EPSG:4171",
            always_xy=True,
        )
        profile = _float_profile(src)

        with rasterio.open(output_path, "w", **profile) as dst:
            for window in _windows(src.width, src.height):
                dem = src.read(1, window=window, masked=True)
                valid = ~np.ma.getmaskarray(dem) & np.isfinite(dem.data)
                out = np.full(dem.shape, np.nan, dtype=np.float32)

                if valid.any():
                    xs, ys = _window_xy(src.transform, window)
                    lon, lat = to_rgf93_geo.transform(xs[valid], ys[valid])
                    n = interpolator(np.column_stack((lat, lon)))
                    missing = ~np.isfinite(n)
                    outside += int(missing.sum())
                    values = np.full(n.shape, np.nan, dtype=np.float32)
                    values[~missing] = n[~missing].astype(np.float32)
                    out[valid] = values

                dst.write(out, 1, window=window)

            dst.update_tags(
                VERTICAL_MODEL="RAF20",
                MODEL_QUANTITY="N = h - H",
                MODEL_UNITS="m",
                MODEL_TARGET="NGF-IGN69",
                MODEL_ELLIPSOID="RGF93 / GRS80",
            )

    if outside:
        output_path.unlink(missing_ok=True)
        raise ValueError(
            f"{outside} valid DEM pixels lie outside RAF20 coverage. "
            "RAF20 must only be used for mainland France."
        )

    _validate_model_raster(
        output_path,
        label="RAF20 geoid separation model",
    )

    return output_path


# ---------------------------------------------------------------------
# EGM96 / EGM2008 via PROJ with ASP fallback
# ---------------------------------------------------------------------

def _raster_wgs84_aoi(src):
    to_wgs84 = Transformer.from_crs(src.crs, "EPSG:4326", always_xy=True)
    xs = np.array([
        src.bounds.left,
        src.bounds.right,
        src.bounds.right,
        src.bounds.left,
    ])
    ys = np.array([
        src.bounds.bottom,
        src.bounds.bottom,
        src.bounds.top,
        src.bounds.top,
    ])
    lon, lat = to_wgs84.transform(xs, ys)
    return AreaOfInterest(
        west_lon_degree=float(np.nanmin(lon)),
        south_lat_degree=float(np.nanmin(lat)),
        east_lon_degree=float(np.nanmax(lon)),
        north_lat_degree=float(np.nanmax(lat)),
    )


def _proj_geoid_to_ellipsoid_transformer(model, src, grid_cache_dir):
    info = PROJ_MODELS[model]
    grid_cache_dir = Path(grid_cache_dir)
    grid_cache_dir.mkdir(parents=True, exist_ok=True)
    datadir.append_data_dir(str(grid_cache_dir))
    area = _raster_wgs84_aoi(src)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        group = TransformerGroup(
            info["compound_crs"],
            "EPSG:4979",
            always_xy=True,
            area_of_interest=area,
            allow_ballpark=False,
        )

    if not group.best_available or not group.transformers:
        try:
            network.set_network_enabled(True)
            group.download_grids(
                directory=str(grid_cache_dir),
                open_license=True,
                verbose=False,
            )
            datadir.append_data_dir(str(grid_cache_dir))
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                group = TransformerGroup(
                    info["compound_crs"],
                    "EPSG:4979",
                    always_xy=True,
                    area_of_interest=area,
                    allow_ballpark=False,
                )
        except Exception:
            pass

    if not group.best_available or not group.transformers:
        missing = []
        for operation in group.unavailable_operations:
            for grid in operation.grids:
                missing.append(grid.short_name or grid.url or operation.name)
        missing_text = ", ".join(sorted(set(missing))) if missing else "unknown grid"
        raise RuntimeError(
            "Required PROJ vertical grid is unavailable: " + missing_text
        )

    return group.transformers[0]


def _asp_n_raster(template_dem, output_path, model):
    """Build N using ASP dem_geoid by reverse-adjusting a zero geoid DEM."""
    executable = shutil.which("dem_geoid")
    if executable is None:
        raise RuntimeError("ASP dem_geoid is not installed.")

    template_dem = Path(template_dem)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    zero_path = output_path.parent / f"{output_path.stem}_zero_tmp.tif"
    prefix = output_path.parent / f"{output_path.stem}_asp_tmp"

    with rasterio.open(template_dem) as src:
        profile = _float_profile(src)
        with rasterio.open(zero_path, "w", **profile) as dst:
            for window in _windows(src.width, src.height):
                dem = src.read(1, window=window, masked=True)
                valid = ~np.ma.getmaskarray(dem) & np.isfinite(dem.data)
                zero = np.full(dem.shape, np.nan, dtype=np.float32)
                zero[valid] = 0.0
                dst.write(zero, 1, window=window)

    cmd = [
        executable,
        "--geoid",
        PROJ_MODELS[model]["asp_name"],
        "--reverse-adjustment",
        str(zero_path),
        "-o",
        str(prefix),
    ]

    subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    generated = Path(str(prefix) + "-adj.tif")
    if not generated.is_file():
        candidates = sorted(output_path.parent.glob(prefix.name + "*.tif"))
        if not candidates:
            raise RuntimeError("ASP dem_geoid did not create the expected N raster.")
        generated = candidates[0]

    shutil.move(str(generated), str(output_path))
    zero_path.unlink(missing_ok=True)

    for temp in output_path.parent.glob(prefix.name + "*"):
        if temp != output_path:
            try:
                temp.unlink()
            except OSError:
                pass

    with rasterio.open(output_path, "r+") as dst:
        dst.update_tags(
            VERTICAL_MODEL=PROJ_MODELS[model]["label"],
            MODEL_QUANTITY="N = h - H",
            MODEL_UNITS="m",
            MODEL_ENGINE="ASP dem_geoid",
        )

    _validate_model_raster(
        output_path,
        label=f"{PROJ_MODELS[model]['label']} geoid separation model",
    )

    return output_path


def build_global_n_raster(template_dem, output_path, model, grid_cache_dir):
    """Create EGM96 or EGM2008 N = h - H on the template DEM grid."""
    if model not in PROJ_MODELS:
        raise ValueError(f"Unsupported global model: {model}")

    template_dem = Path(template_dem)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with rasterio.open(template_dem) as src:
            if src.crs is None:
                raise ValueError("Global geoid evaluation requires a defined CRS.")

            transformer = _proj_geoid_to_ellipsoid_transformer(
                model,
                src,
                grid_cache_dir,
            )
            to_wgs84 = Transformer.from_crs(src.crs, "EPSG:4326", always_xy=True)
            profile = _float_profile(src)

            with rasterio.open(output_path, "w", **profile) as dst:
                for window in _windows(src.width, src.height):
                    dem = src.read(1, window=window, masked=True)
                    valid = ~np.ma.getmaskarray(dem) & np.isfinite(dem.data)
                    out = np.full(dem.shape, np.nan, dtype=np.float32)

                    if valid.any():
                        xs, ys = _window_xy(src.transform, window)
                        lon, lat = to_wgs84.transform(xs[valid], ys[valid])
                        zero_h = np.zeros(lon.shape, dtype=np.float64)
                        _, _, n = transformer.transform(lon, lat, zero_h)
                        out[valid] = np.asarray(n, dtype=np.float32)

                    dst.write(out, 1, window=window)

                dst.update_tags(
                    VERTICAL_MODEL=PROJ_MODELS[model]["label"],
                    MODEL_QUANTITY="N = h - H",
                    MODEL_UNITS="m",
                    MODEL_ENGINE="PROJ/pyproj",
                )

        _validate_model_raster(
            output_path,
            label=f"{PROJ_MODELS[model]['label']} geoid separation model",
        )

        return output_path

    except Exception as proj_error:
        output_path.unlink(missing_ok=True)
        if shutil.which("dem_geoid"):
            return _asp_n_raster(template_dem, output_path, model)
        raise RuntimeError(
            f"Could not build {PROJ_MODELS[model]['label']} separation model "
            "with PROJ, and ASP dem_geoid is not available as fallback. "
            f"PROJ error: {proj_error}"
        ) from proj_error


# ---------------------------------------------------------------------
# Custom N raster
# ---------------------------------------------------------------------

def build_custom_n_raster(template_dem, output_path, custom_n_raster):
    """Align a user-provided N = h - H raster to the template DEM grid."""
    template_dem = Path(template_dem)
    custom_n_raster = Path(custom_n_raster)
    output_path = Path(output_path)

    if not custom_n_raster.is_file():
        raise FileNotFoundError(f"Custom N raster not found:\n{custom_n_raster}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(template_dem) as template, rasterio.open(custom_n_raster) as n_src:
        if template.crs is None:
            raise ValueError("Template DEM has no CRS.")
        if n_src.crs is None:
            raise ValueError("Custom N raster has no CRS.")

        profile = _float_profile(template)
        vrt_options = {
            "crs": template.crs,
            "transform": template.transform,
            "width": template.width,
            "height": template.height,
            "resampling": Resampling.bilinear,
            "nodata": np.nan,
        }

        with WarpedVRT(n_src, **vrt_options) as vrt:
            with rasterio.open(output_path, "w", **profile) as dst:
                for window in _windows(template.width, template.height):
                    dem = template.read(1, window=window, masked=True)
                    n = vrt.read(1, window=window, masked=True)
                    valid = (
                        ~np.ma.getmaskarray(dem)
                        & ~np.ma.getmaskarray(n)
                        & np.isfinite(dem.data)
                        & np.isfinite(n.data)
                    )
                    out = np.full(dem.shape, np.nan, dtype=np.float32)
                    out[valid] = n.data[valid].astype(np.float32)
                    dst.write(out, 1, window=window)

                dst.update_tags(
                    VERTICAL_MODEL="Custom N raster",
                    MODEL_QUANTITY="N = h - H",
                    MODEL_UNITS="m",
                    MODEL_SOURCE=str(custom_n_raster),
                )

    _validate_model_raster(
        output_path,
        label="custom geoid separation model",
    )

    return output_path


def build_n_raster(
    template_dem,
    output_path,
    model,
    *,
    raf20_path=None,
    custom_n_raster=None,
    grid_cache_dir=None,
):
    if model == "ellipsoid":
        return None
    if model == "raf20":
        if raf20_path is None:
            raise ValueError("RAF20 model file is required.")
        return build_raf20_n_raster(template_dem, output_path, raf20_path)
    if model in {"egm96", "egm2008"}:
        if grid_cache_dir is None:
            grid_cache_dir = Path(output_path).parent / "grid_cache"
        return build_global_n_raster(template_dem, output_path, model, grid_cache_dir)
    if model == "custom":
        if custom_n_raster is None:
            raise ValueError("Custom N raster is required.")
        return build_custom_n_raster(template_dem, output_path, custom_n_raster)
    raise ValueError(f"Unknown vertical model: {model}")


# ---------------------------------------------------------------------
# Any source vertical reference -> any target vertical reference
# ---------------------------------------------------------------------

def convert_between_models(
    dem_path,
    output_path,
    source_model,
    target_model,
    *,
    source_n_path=None,
    target_n_path=None,
):
    """
    Apply z_target = z_source + N_source - N_target.

    N = 0 for ellipsoidal heights.
    """
    dem_path = Path(dem_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if source_model != "ellipsoid" and source_n_path is None:
        raise ValueError("Source N raster is required.")
    if target_model != "ellipsoid" and target_n_path is None:
        raise ValueError("Target N raster is required.")

    with rasterio.open(dem_path) as dem:
        profile = _float_profile(dem)
        source_ctx = rasterio.open(source_n_path) if source_n_path else None
        target_ctx = rasterio.open(target_n_path) if target_n_path else None

        try:
            with rasterio.open(output_path, "w", **profile) as dst:
                for window in _windows(dem.width, dem.height):
                    z = dem.read(1, window=window, masked=True)
                    valid = ~np.ma.getmaskarray(z) & np.isfinite(z.data)

                    n_source = np.zeros(z.shape, dtype=np.float32)
                    n_target = np.zeros(z.shape, dtype=np.float32)

                    if source_ctx:
                        src_n = source_ctx.read(1, window=window, masked=True)
                        valid &= ~np.ma.getmaskarray(src_n) & np.isfinite(src_n.data)
                        n_source = np.asarray(src_n.data, dtype=np.float32)

                    if target_ctx:
                        dst_n = target_ctx.read(1, window=window, masked=True)
                        valid &= ~np.ma.getmaskarray(dst_n) & np.isfinite(dst_n.data)
                        n_target = np.asarray(dst_n.data, dtype=np.float32)

                    out = np.full(z.shape, np.nan, dtype=np.float32)
                    out[valid] = (
                        np.asarray(z.data[valid], dtype=np.float32)
                        + n_source[valid]
                        - n_target[valid]
                    )
                    dst.write(out, 1, window=window)

                dst.update_tags(
                    SOURCE_VERTICAL_MODEL=MODEL_LABELS.get(source_model, source_model),
                    TARGET_VERTICAL_MODEL=MODEL_LABELS.get(target_model, target_model),
                    VERTICAL_EQUATION="z_target = z_source + N_source - N_target",
                    N_DEFINITION="N = h - H",
                )
        finally:
            if source_ctx:
                source_ctx.close()
            if target_ctx:
                target_ctx.close()

    return output_path
