"""
IGN LiDAR HD MNS/DSM download and processing utilities.

This module reproduces the supplied R workflow in Python:

1. Read and validate an AOI.
2. transform the AOI to Lambert-93 (EPSG:2154).
3. Query the IGN Géoplateforme WFS tile index, with direct WMS-raster fallback.
4. Select MNS/DSM tiles intersecting the AOI or a user-defined buffer.
5. Download and cache the native 0.5 m GeoTIFF tiles.
6. Check missing and unreadable files.
7. Aggregate each native tile to a target resolution using the mean.
8. Mosaic the aggregated tiles.
9. Save an AOI-bounding-box raster and an AOI-masked raster in EPSG:2154.
10. Save NoData diagnostic rasters and tile statistics.
11. Optionally export EPSG:4326 copies and display the results.

The workflow downloads the derived IGN LiDAR HD MNS product. It does not
download or rasterize the raw LiDAR point clouds.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import csv
import json
import tempfile
import math
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterable
from xml.etree import ElementTree as ET

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.transform import from_origin
from rasterio.warp import calculate_default_transform, reproject
from rasterio.windows import Window, from_bounds
from shapely.geometry import mapping, box


LAMBERT93 = "EPSG:2154"
DEFAULT_OUTPUT_CRS = "EPSG:4326"

# Official IGN Géoplateforme WFS endpoint.
#
# Keep this as a single official route.  The previously tested
# ``/wfs/geoserver/ows`` fallback returns HTTP 404 and is intentionally
# not used.
WFS_ENDPOINTS = (
    "https://data.geopf.fr/wfs/ows",
)

# Official/current IGN MNS LiDAR-HD tile-index identifier.
MNS_LAYER = "IGNF_MNS-LIDAR-HD:dalle"

# Compatibility spelling observed in catalog metadata.  It is only tried if
# the official identifier is rejected by the server.
MNS_LAYER_CANDIDATES = (
    MNS_LAYER,
    "IGNF_MNS_LIDAR-HD:dalle",
)

# Direct WMS-raster fallback. The WFS service above is only used as a tile
# index; its records ultimately point to this raster service. If IGN
# temporarily depublishes or breaks the WFS namespace, the workflow can
# reconstruct the regular 1-km LiDAR-HD grid from the Lambert-93 AOI and
# request the same native 0.5-m Float32 GeoTIFF tiles directly.
WMS_RASTER_ENDPOINT = "https://data.geopf.fr/wms-r"
MNS_WMS_LAYER = "IGNF_LIDAR-HD_MNS_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93"
IGN_TILE_SIZE_M = 1000.0
IGN_TILE_WIDTH_PX = 2000
IGN_TILE_HEIGHT_PX = 2000

FLOAT_NODATA = np.nan


@dataclass(frozen=True)
class TileRecord:
    """One IGN LiDAR HD MNS tile returned by the WFS index."""

    name_download: str
    url: str


def _ensure_path(path: str | Path) -> Path:
    """Return a resolved Path object without requiring the path to exist."""
    return Path(path).expanduser()


def _read_aoi(aoi_path: str | Path) -> gpd.GeoDataFrame:
    """Read, validate, dissolve, and transform an AOI to EPSG:2154."""
    aoi_path = _ensure_path(aoi_path)

    if not aoi_path.exists():
        raise FileNotFoundError(f"AOI file does not exist: {aoi_path}")

    aoi = gpd.read_file(aoi_path)

    if aoi.empty:
        raise ValueError("AOI vector file is empty.")

    if aoi.crs is None:
        raise ValueError(
            "AOI vector file has no CRS. Define its CRS before running the workflow."
        )

    aoi = aoi.loc[~aoi.geometry.is_empty & aoi.geometry.notna()].copy()

    if aoi.empty:
        raise ValueError("AOI contains no valid geometries.")

    # buffer(0) repairs many common polygon validity problems.
    invalid = ~aoi.geometry.is_valid
    if invalid.any():
        aoi.loc[invalid, "geometry"] = aoi.loc[invalid, "geometry"].buffer(0)

    aoi_l93 = aoi.to_crs(LAMBERT93)
    dissolved = (
        aoi_l93.geometry.union_all()
        if hasattr(aoi_l93.geometry, "union_all")
        else aoi_l93.geometry.unary_union
    )

    if dissolved.is_empty:
        raise ValueError("The dissolved AOI geometry is empty.")

    return gpd.GeoDataFrame(
        {"geometry": [dissolved]},
        crs=LAMBERT93,
    )


def _write_aoi(aoi_l93: gpd.GeoDataFrame, out_path: Path) -> Path:
    """Save the dissolved Lambert-93 AOI as a GeoPackage."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists():
        out_path.unlink()

    aoi_l93.to_file(
        out_path,
        layer="aoi",
        driver="GPKG",
    )

    return out_path


def _selection_geometry(
    aoi_l93: gpd.GeoDataFrame,
    download_buffer: bool,
    buffer_distance_m: float,
) -> gpd.GeoDataFrame:
    """
    Build the IGN tile-selection geometry.

    Buffered mode uses the AOI bounding box expanded on all four sides.
    This includes the corner tiles needed by the rectangular ASP reference.
    """
    if buffer_distance_m < 0:
        raise ValueError("buffer_distance_m must be zero or positive.")

    minx, miny, maxx, maxy = [float(v) for v in aoi_l93.total_bounds]

    if download_buffer:
        d = float(buffer_distance_m)
        geometry = box(minx - d, miny - d, maxx + d, maxy + d)
    else:
        geometry = box(minx, miny, maxx, maxy)

    return gpd.GeoDataFrame(
        {"geometry": [geometry]},
        crs=LAMBERT93,
    )


def _decode_wfs_payload(raw: bytes, output_format: str) -> dict:
    """Decode an IGN WFS response into a GeoJSON-like feature collection.

    IGN normally supports ``application/json`` for WFS GetFeature, while the
    current LiDAR-HD guidance also demonstrates ``GML2``.  Supporting both
    makes the downloader tolerant of server-side output-format changes.
    """
    if output_format == "application/json":
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "The IGN WFS JSON response could not be decoded."
            ) from error
    else:
        # GeoPandas/Fiona can parse the GML2 feature response reliably.  Write
        # it to a temporary file because support for arbitrary in-memory file
        # objects differs across Fiona / pyogrio versions.
        try:
            with tempfile.NamedTemporaryFile(suffix=".gml") as tmp:
                tmp.write(raw)
                tmp.flush()
                gdf = gpd.read_file(tmp.name)
            data = json.loads(gdf.to_json())
        except Exception as error:
            raise RuntimeError(
                "The IGN WFS GML2 response could not be decoded."
            ) from error

    if "features" not in data:
        raise RuntimeError(
            "The IGN WFS response does not contain a feature collection."
        )
    return data


def _discover_mns_layers(endpoint: str, timeout: int = 60) -> list[str]:
    """Discover MNS LiDAR-HD tile-index layer names from WFS GetCapabilities.

    IGN has changed technical layer identifiers over time.  This helper is used
    only as a fallback after the known current/legacy identifiers fail.  It
    keeps the workflow robust to future publication-name changes.
    """
    params = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetCapabilities",
    }
    url = f"{endpoint}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "IGN-LiDARHD-Python-workflow/1.2",
            "Accept": "application/xml, text/xml, */*",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        root = ET.fromstring(raw)
    except Exception:
        return []

    names: list[str] = []
    for element in root.iter():
        if element.tag.split("}")[-1] != "Name":
            continue
        value = (element.text or "").strip()
        lower = value.lower()
        if (
            value
            and "mns" in lower
            and "lidar-hd" in lower
            and ("dalle" in lower or "tile" in lower)
        ):
            names.append(value)

    # Stable order, no duplicates.
    return list(dict.fromkeys(names))


def _wfs_geojson(
    selection_l93: gpd.GeoDataFrame,
    layer: str = MNS_LAYER,
    count: int = 10000,
) -> dict:
    """Query the IGN LiDAR-HD MNS WFS tile index with resilient fallbacks.

    The function first tries the official IGN technical layer name, then the
    known catalog spelling variant, on the official Géoplateforme WFS route.
    If those fail, it asks GetCapabilities for any currently published MNS
    LiDAR-HD ``dalle`` layer and retries with the discovered identifier.

    No ASP processing is started by this function.
    """
    minx, miny, maxx, maxy = [float(v) for v in selection_l93.total_bounds]

    crs_variants = [
        LAMBERT93,
        "urn:ogc:def:crs:EPSG::2154",
    ]
    output_formats = [
        "application/json",
        "GML2",
    ]

    # Keep caller-supplied layer first, then the known IGN current/legacy names.
    layer_variants = list(dict.fromkeys([layer, *MNS_LAYER_CANDIDATES]))
    errors: list[str] = []

    def _try_layer(endpoint: str, layer_name: str):
        for crs_name in crs_variants:
            for output_format in output_formats:
                params = {
                    "SERVICE": "WFS",
                    "VERSION": "2.0.0",
                    "REQUEST": "GetFeature",
                    "TYPENAMES": layer_name,
                    "SRSNAME": crs_name,
                    "BBOX": f"{minx},{miny},{maxx},{maxy},{crs_name}",
                    "OUTPUTFORMAT": output_format,
                    "COUNT": str(count),
                }
                url = f"{endpoint}?{urllib.parse.urlencode(params)}"
                request = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "IGN-LiDARHD-Python-workflow/1.2",
                        "Accept": "application/json, application/gml+xml, text/xml, */*",
                    },
                )

                try:
                    with urllib.request.urlopen(request, timeout=120) as response:
                        raw = response.read()
                    data = _decode_wfs_payload(raw, output_format)
                    if data.get("features") is not None:
                        return data
                except urllib.error.HTTPError as error:
                    try:
                        body = error.read().decode("utf-8", errors="replace")
                    except Exception:
                        body = ""
                    # Keep the diagnostic concise.  The interface should not be
                    # flooded with an entire XML exception document.
                    body = " ".join(body.split())[:500]
                    errors.append(
                        f"HTTP {error.code} | endpoint={endpoint} | "
                        f"layer={layer_name} | CRS={crs_name} | "
                        f"format={output_format} | {body or 'no server message'}"
                    )
                except urllib.error.URLError as error:
                    errors.append(
                        f"Network error | endpoint={endpoint} | "
                        f"layer={layer_name} | CRS={crs_name} | "
                        f"format={output_format} | {error}"
                    )
                    time.sleep(0.5)
                except RuntimeError as error:
                    errors.append(
                        f"Decode error | endpoint={endpoint} | "
                        f"layer={layer_name} | CRS={crs_name} | "
                        f"format={output_format} | {error}"
                    )
        return None

    # 1) Try the official and compatibility identifiers on the official route.
    for endpoint in WFS_ENDPOINTS:
        for layer_name in layer_variants:
            data = _try_layer(endpoint, layer_name)
            if data is not None:
                return data

    # 2) Last-resort discovery from GetCapabilities.  This protects against a
    # future IGN technical-name change without silently selecting an unrelated
    # elevation layer.
    for endpoint in WFS_ENDPOINTS:
        discovered = _discover_mns_layers(endpoint)
        for layer_name in discovered:
            if layer_name in layer_variants:
                continue
            data = _try_layer(endpoint, layer_name)
            if data is not None:
                return data

    detail = "\n".join(f"  - {item}" for item in errors[-6:])
    raise RuntimeError(
        "IGN WFS tile-index query failed. The workflow tried the official MNS "
        "LiDAR-HD layer identifier, the known catalog spelling variant, and "
        "GetCapabilities discovery on the official IGN WFS endpoint. "
        "No ASP processing has started.\n"
        f"Endpoint: {WFS_ENDPOINTS[0]}\n"
        f"Requested layer: {layer}\n"
        "The request reached the IGN service, but no usable MNS LiDAR-HD tile "
        "index could be obtained. This can indicate a temporary IGN "
        "service/publication problem or a server-side layer-name change.\n"
        f"Recent IGN responses:\n{detail}"
    )

def _direct_wms_tile_records(
    selection_l93: gpd.GeoDataFrame,
) -> tuple[gpd.GeoDataFrame, list[TileRecord]]:
    """Build native IGN MNS tile requests directly from the Lambert-93 grid.

    This is a fallback for periods when the IGN WFS tile-index namespace is
    unavailable. It requests the same native 0.5 m Float32 GeoTIFF raster
    layer referenced by the WFS tile records, so the downstream scientific
    processing remains unchanged.
    """
    if selection_l93.crs is None:
        raise ValueError("Selection geometry has no CRS.")

    selection = selection_l93.to_crs(LAMBERT93)
    minx, miny, maxx, maxy = [float(v) for v in selection.total_bounds]

    # IGN native 0.5-m LiDAR-HD rasters are published on a 1-km grid with
    # outer bounds offset by 0.25 m from integer-kilometre lines.
    x_left = math.floor((minx + 0.25) / IGN_TILE_SIZE_M) * IGN_TILE_SIZE_M - 0.25
    y_bottom = math.floor((miny - 0.25) / IGN_TILE_SIZE_M) * IGN_TILE_SIZE_M + 0.25

    selection_union = (
        selection.geometry.union_all()
        if hasattr(selection.geometry, "union_all")
        else selection.geometry.unary_union
    )

    rows: list[dict] = []
    records: list[TileRecord] = []
    eps = 1e-7

    x = x_left
    while x < maxx - eps:
        y = y_bottom
        while y < maxy - eps:
            right = x + IGN_TILE_SIZE_M
            top = y + IGN_TILE_SIZE_M
            geom = box(x, y, right, top)

            if geom.intersection(selection_union).area > 0:
                x_code = int(round((x + 0.25) / 1000.0))
                y_code = int(round((top - 0.25) / 1000.0))
                stem = (
                    f"LHD_FXX_{x_code:04d}_{y_code:04d}_"
                    "MNS_O_0M50_LAMB93_IGN69"
                )
                name_download = f"{stem}.tif"

                params = {
                    "SERVICE": "WMS",
                    "VERSION": "1.3.0",
                    "EXCEPTIONS": "text/xml",
                    "REQUEST": "GetMap",
                    "LAYERS": MNS_WMS_LAYER,
                    "FORMAT": "image/geotiff",
                    "STYLES": "",
                    "CRS": LAMBERT93,
                    "BBOX": f"{x:.2f},{y:.2f},{right:.2f},{top:.2f}",
                    "WIDTH": str(IGN_TILE_WIDTH_PX),
                    "HEIGHT": str(IGN_TILE_HEIGHT_PX),
                    "FILENAME": name_download,
                }
                url = f"{WMS_RASTER_ENDPOINT}?{urllib.parse.urlencode(params)}"

                rows.append({
                    "name_download": name_download,
                    "url": url,
                    "source": "direct_wms_fallback",
                    "geometry": geom,
                })
                records.append(TileRecord(name_download=name_download, url=url))

            y += IGN_TILE_SIZE_M
        x += IGN_TILE_SIZE_M

    if not records:
        raise RuntimeError(
            "Could not construct any direct IGN MNS WMS tile request for the AOI."
        )

    tile_index = gpd.GeoDataFrame(rows, geometry="geometry", crs=LAMBERT93)
    tile_index = tile_index.sort_values("name_download").reset_index(drop=True)
    records = sorted(records, key=lambda r: r.name_download)
    return tile_index, records


def _load_and_select_tiles(
    selection_l93: gpd.GeoDataFrame,
    layer: str = MNS_LAYER,
) -> tuple[gpd.GeoDataFrame, list[TileRecord]]:
    """Load/select MNS tiles, with direct official WMS-raster fallback.

    Normal route: use the IGN WFS tile index and the URLs it publishes.
    Fallback route: if the WFS namespace is unavailable or returns no features,
    reconstruct the regular 1-km grid and request the same native MNS raster
    layer directly through IGN WMS-raster.
    """
    try:
        data = _wfs_geojson(
            selection_l93=selection_l93,
            layer=layer,
        )
        features = data.get("features", [])

        if not features:
            raise RuntimeError(
                "The IGN WFS returned no MNS/DSM tiles for the requested area."
            )

        tile_index = gpd.GeoDataFrame.from_features(
            features,
            crs=LAMBERT93,
        )
    except Exception as wfs_error:
        print(
            "IGN WFS tile index is unavailable; switching automatically to "
            "the official IGN MNS WMS-raster fallback."
        )
        print(f"WFS diagnostic: {str(wfs_error).splitlines()[0]}")
        try:
            return _direct_wms_tile_records(selection_l93)
        except Exception as wms_error:
            raise RuntimeError(
                "IGN MNS tile discovery failed through both routes. "
                "The WFS tile index was unavailable and the direct WMS-raster "
                "fallback could not construct usable tile requests. No ASP "
                "processing has started.\n"
                f"WFS: {wfs_error}\n"
                f"WMS fallback: {wms_error}"
            ) from wms_error

    if tile_index.crs is None:
        tile_index = tile_index.set_crs(LAMBERT93)
    elif tile_index.crs.to_string() != LAMBERT93:
        tile_index = tile_index.to_crs(LAMBERT93)

    required_fields = {"name_download", "url"}
    missing_fields = required_fields.difference(tile_index.columns)

    if missing_fields:
        available = ", ".join(map(str, tile_index.columns))
        raise KeyError(
            "The IGN tile index does not contain the expected field(s): "
            f"{sorted(missing_fields)}. Available fields: {available}"
        )

    selection_union = (
        selection_l93.geometry.union_all()
        if hasattr(selection_l93.geometry, "union_all")
        else selection_l93.geometry.unary_union
    )
    selected = tile_index.loc[
        tile_index.geometry.intersects(selection_union)
    ].copy()

    if selected.empty:
        raise RuntimeError(
            "No IGN MNS/DSM tile intersects the AOI selection geometry."
        )

    selected = (
        selected.dropna(subset=["name_download", "url"])
        .drop_duplicates(subset=["name_download"])
        .sort_values("name_download")
        .reset_index(drop=True)
    )

    records = [
        TileRecord(
            name_download=str(row["name_download"]),
            url=str(row["url"]),
        )
        for _, row in selected.iterrows()
    ]

    if not records:
        raise RuntimeError("No valid downloadable MNS/DSM tile records were found.")

    return selected, records


def _download_one_tile(
    record: TileRecord,
    tile_dir: Path,
    overwrite: bool = False,
    attempts: int = 3,
) -> Path:
    """Download one tile atomically, retrying transient IGN WMS failures."""
    tile_dir.mkdir(parents=True, exist_ok=True)
    out_path = tile_dir / record.name_download

    if out_path.exists() and out_path.stat().st_size > 0 and not overwrite:
        print(f"Using cached tile: {out_path.name}")
        return out_path

    temporary_path = out_path.with_suffix(out_path.suffix + ".part")
    last_error = None

    for attempt in range(1, max(1, int(attempts)) + 1):
        if temporary_path.exists():
            temporary_path.unlink()

        suffix = "" if attempt == 1 else f" (retry {attempt}/{attempts})"
        print(f"Downloading: {record.name_download}{suffix}")

        request = urllib.request.Request(
            record.url,
            headers={"User-Agent": "IGN-LiDARHD-Python-workflow/1.0"},
        )

        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                with temporary_path.open("wb") as destination:
                    shutil.copyfileobj(response, destination)

            if not temporary_path.exists() or temporary_path.stat().st_size == 0:
                raise RuntimeError(
                    f"Downloaded tile is empty: {record.name_download}"
                )

            temporary_path.replace(out_path)
            return out_path

        except Exception as error:
            last_error = error
            if temporary_path.exists():
                temporary_path.unlink()

            if attempt < attempts:
                time.sleep(2 * attempt)

    raise RuntimeError(
        f"download failed after {attempts} attempts: {last_error}"
    )


def _download_tiles(
    records: Iterable[TileRecord],
    tile_dir: Path,
    n_workers: int,
    overwrite_names: set[str] | None = None,
) -> list[Path]:
    """Download tiles concurrently while retaining deterministic output ordering."""
    records = list(records)

    if n_workers < 1:
        raise ValueError("n_workers must be at least 1.")

    overwrite_names = overwrite_names or set()
    results: dict[str, Path] = {}
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        futures = {
            executor.submit(
                _download_one_tile,
                record,
                tile_dir,
                record.name_download in overwrite_names,
            ): record
            for record in records
        }

        for future in as_completed(futures):
            record = futures[future]

            try:
                results[record.name_download] = future.result()
            except Exception as error:
                errors.append(f"{record.name_download}: {error}")

    if errors:
        details = "\n".join(errors)
        raise RuntimeError(f"One or more MNS/DSM downloads failed:\n{details}")

    return [results[record.name_download] for record in records]


def _missing_expected_tiles(
    records: Iterable[TileRecord],
    tile_dir: Path,
) -> list[str]:
    """Return expected tile names that are absent or empty."""
    missing = []

    for record in records:
        path = tile_dir / record.name_download

        if not path.exists() or path.stat().st_size == 0:
            missing.append(record.name_download)

    return missing


def _validate_raster(path: Path, minimum_size_bytes: int = 10000) -> bool:
    """Check that a downloaded GeoTIFF is present, sufficiently large, and readable."""
    if not path.exists():
        return False

    if path.stat().st_size < minimum_size_bytes:
        return False

    try:
        with rasterio.open(path) as src:
            if src.count < 1 or src.width < 1 or src.height < 1:
                return False

            _ = src.res
            _ = src.crs
            src.read(
                1,
                window=Window(
                    0,
                    0,
                    min(src.width, 16),
                    min(src.height, 16),
                ),
            )
    except Exception:
        return False

    return True


def _write_float_raster(
    out_path: Path,
    array: np.ndarray,
    transform: Affine,
    crs,
) -> Path:
    """Write one floating-point GeoTIFF using the R workflow's compression intent."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    profile = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": crs,
        "transform": transform,
        "nodata": FLOAT_NODATA,
        "compress": "LZW",
        "predictor": 3,
        "tiled": True,
        "BIGTIFF": "IF_SAFER",
    }

    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(array.astype(np.float32), 1)

    return out_path


def _write_uint8_raster(
    out_path: Path,
    array: np.ndarray,
    transform: Affine,
    crs,
) -> Path:
    """Write one unsigned 8-bit diagnostic GeoTIFF."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    profile = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": "uint8",
        "crs": crs,
        "transform": transform,
        "nodata": None,
        "compress": "LZW",
        "tiled": True,
        "BIGTIFF": "IF_SAFER",
    }

    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(array.astype(np.uint8), 1)

    return out_path


def _aggregate_tile_to_resolution(
    native_path: Path,
    out_path: Path,
    target_res: float,
) -> dict:
    """Aggregate one native tile to the target resolution using the mean."""
    with rasterio.open(native_path) as src:
        if src.crs is None:
            source_crs = rasterio.crs.CRS.from_string(LAMBERT93)
        else:
            source_crs = src.crs

        native_res_x = abs(float(src.res[0]))
        native_res_y = abs(float(src.res[1]))

        if not math.isclose(native_res_x, native_res_y, abs_tol=1e-9):
            raise ValueError(
                f"Input tile has non-square pixels: {native_path}"
            )

        factor = target_res / native_res_x
        rounded_factor = round(factor)

        if not math.isclose(factor, rounded_factor, abs_tol=1e-6):
            raise ValueError(
                "target_res is not an integer multiple of the native resolution "
                f"for tile: {native_path}"
            )

        factor = int(rounded_factor)

        if factor < 1:
            raise ValueError(
                f"target_res must be at least the native resolution: {native_path}"
            )

        if src.width % factor != 0 or src.height % factor != 0:
            raise ValueError(
                "Native raster dimensions are not divisible by the aggregation "
                f"factor for tile: {native_path}"
            )

        out_width = src.width // factor
        out_height = src.height // factor

        aggregated = src.read(
            1,
            out_shape=(out_height, out_width),
            masked=True,
            resampling=Resampling.average,
        )

        array = aggregated.astype(np.float32).filled(np.nan)

        transform = src.transform * Affine.scale(
            src.width / out_width,
            src.height / out_height,
        )

    _write_float_raster(
        out_path=out_path,
        array=array,
        transform=transform,
        crs=source_crs,
    )

    n_total = int(array.size)
    n_na = int(np.isnan(array).sum())

    return {
        "file": native_path.name,
        "ncell": n_total,
        "n_na": n_na,
        "pct_na": 100.0 * n_na / n_total if n_total else np.nan,
        "coarse_path": out_path,
    }


def _mosaic_mean(
    raster_paths: Iterable[Path],
) -> tuple[np.ndarray, Affine, rasterio.crs.CRS]:
    """Mosaic aligned coarse tiles and average any overlapping valid cells."""
    raster_paths = list(raster_paths)

    if not raster_paths:
        raise ValueError("No coarse raster tiles were provided for mosaicking.")

    metadata = []

    for path in raster_paths:
        with rasterio.open(path) as src:
            metadata.append(
                {
                    "path": path,
                    "bounds": src.bounds,
                    "res": src.res,
                    "crs": src.crs,
                    "width": src.width,
                    "height": src.height,
                }
            )

    crs = metadata[0]["crs"]
    res_x = abs(float(metadata[0]["res"][0]))
    res_y = abs(float(metadata[0]["res"][1]))

    for item in metadata[1:]:
        if item["crs"] != crs:
            raise ValueError("All coarse raster tiles must use the same CRS.")

        if not (
            math.isclose(abs(float(item["res"][0])), res_x, abs_tol=1e-8)
            and math.isclose(abs(float(item["res"][1])), res_y, abs_tol=1e-8)
        ):
            raise ValueError("All coarse raster tiles must use the same resolution.")

    left = min(item["bounds"].left for item in metadata)
    bottom = min(item["bounds"].bottom for item in metadata)
    right = max(item["bounds"].right for item in metadata)
    top = max(item["bounds"].top for item in metadata)

    width = int(round((right - left) / res_x))
    height = int(round((top - bottom) / res_y))
    transform = from_origin(left, top, res_x, res_y)

    sum_array = np.zeros((height, width), dtype=np.float64)
    count_array = np.zeros((height, width), dtype=np.uint16)

    for item in metadata:
        with rasterio.open(item["path"]) as src:
            tile = src.read(1, masked=True).astype(np.float64)
            values = tile.filled(np.nan)
            valid = np.isfinite(values)

            col_offset = int(round((src.bounds.left - left) / res_x))
            row_offset = int(round((top - src.bounds.top) / res_y))

            row_slice = slice(row_offset, row_offset + src.height)
            col_slice = slice(col_offset, col_offset + src.width)

            target_sum = sum_array[row_slice, col_slice]
            target_count = count_array[row_slice, col_slice]

            target_sum[valid] += values[valid]
            target_count[valid] += 1

    mosaic = np.full((height, width), np.nan, dtype=np.float32)
    valid_mosaic = count_array > 0
    mosaic[valid_mosaic] = (
        sum_array[valid_mosaic] / count_array[valid_mosaic]
    ).astype(np.float32)

    return mosaic, transform, crs


def _crop_array_to_aoi_bbox(
    array: np.ndarray,
    transform: Affine,
    aoi_l93: gpd.GeoDataFrame,
) -> tuple[np.ndarray, Affine]:
    """Crop a raster array to the AOI bounding box on the existing grid."""
    minx, miny, maxx, maxy = aoi_l93.total_bounds

    requested = from_bounds(
        minx,
        miny,
        maxx,
        maxy,
        transform=transform,
    ).round_offsets().round_lengths()

    full = Window(
        col_off=0,
        row_off=0,
        width=array.shape[1],
        height=array.shape[0],
    )

    window = requested.intersection(full)

    row_start = int(window.row_off)
    row_stop = row_start + int(window.height)
    col_start = int(window.col_off)
    col_stop = col_start + int(window.width)

    cropped = array[row_start:row_stop, col_start:col_stop].copy()
    cropped_transform = rasterio.windows.transform(window, transform)

    return cropped, cropped_transform


def _mask_to_aoi(
    bbox_array: np.ndarray,
    bbox_transform: Affine,
    aoi_l93: gpd.GeoDataFrame,
) -> np.ndarray:
    """Set raster cells outside the exact AOI polygon to NoData."""
    inside = geometry_mask(
        geometries=[mapping(geometry) for geometry in aoi_l93.geometry],
        out_shape=bbox_array.shape,
        transform=bbox_transform,
        invert=True,
        all_touched=False,
    )

    masked = bbox_array.copy()
    masked[~inside] = np.nan

    return masked


def _reproject_raster(
    src_path: Path,
    dst_path: Path,
    dst_crs: str,
    resampling: Resampling,
) -> Path:
    """Reproject a saved raster to a new CRS."""
    with rasterio.open(src_path) as src:
        transform, width, height = calculate_default_transform(
            src.crs,
            dst_crs,
            src.width,
            src.height,
            *src.bounds,
        )

        profile = src.profile.copy()
        profile.update(
            {
                "crs": dst_crs,
                "transform": transform,
                "width": width,
                "height": height,
                "dtype": "float32",
                "nodata": FLOAT_NODATA,
                "compress": "LZW",
                "predictor": 3,
                "tiled": True,
                "BIGTIFF": "IF_SAFER",
            }
        )

        with rasterio.open(dst_path, "w", **profile) as dst:
            reproject(
                source=rasterio.band(src, 1),
                destination=rasterio.band(dst, 1),
                src_transform=src.transform,
                src_crs=src.crs,
                src_nodata=src.nodata,
                dst_transform=transform,
                dst_crs=dst_crs,
                dst_nodata=FLOAT_NODATA,
                resampling=resampling,
            )

    return dst_path


def _plot_raster(path: Path, title: str, colorbar_label: str | None = None) -> None:
    """Display one raster in a separate Matplotlib figure."""
    with rasterio.open(path) as src:
        data = src.read(1, masked=True)
        bounds = src.bounds

    fig, ax = plt.subplots(figsize=(9, 7))

    image = ax.imshow(
        data,
        extent=(bounds.left, bounds.right, bounds.bottom, bounds.top),
        origin="upper",
    )

    if colorbar_label is not None:
        colorbar = fig.colorbar(image, ax=ax, shrink=0.85, pad=0.02)
        colorbar.set_label(colorbar_label)

    ax.set_title(title)
    ax.set_xlabel("Easting")
    ax.set_ylabel("Northing")
    ax.set_aspect("equal")
    fig.tight_layout()
    plt.show()


def download_ign_lidarhd_dsm(
    aoi_path: str | Path,
    out_dir: str | Path,
    target_res: float = 50,
    download_buffer: bool = True,
    buffer_distance_m: float = 1000,
    n_workers: int = 2,
    aggregation_fun: str = "mean",
    export_to_wgs84: bool = True,
    output_crs: str = DEFAULT_OUTPUT_CRS,
    wgs84_project_method: str = "nearest",
    plot_results: bool = True,
    max_download_attempts: int = 3,
) -> dict[str, Path]:
    """
    Download and process IGN LiDAR HD MNS/DSM data for an AOI.

    Parameters
    ----------
    aoi_path
        Input AOI vector file.
    out_dir
        Clean output directory for downloaded tiles and final products.
    target_res
        Target output resolution in metres. The default is 50 m.
    download_buffer
        Apply a selection buffer around the AOI when True.
    buffer_distance_m
        Selection-buffer distance in metres.
    n_workers
        Number of concurrent tile downloads.
    aggregation_fun
        Aggregation function. The R workflow uses ``"mean"`` and this Python
        implementation currently supports only that value.
    export_to_wgs84
        Export EPSG:4326 copies when True.
    output_crs
        CRS used for optional reprojected copies.
    wgs84_project_method
        Rasterio resampling method name. The R workflow uses nearest-neighbour.
    plot_results
        Display the bbox DSM, bbox NoData map, masked DSM, and masked NoData map.
    max_download_attempts
        Number of automatic retries when expected files remain missing.

    Returns
    -------
    dict
        Paths to the principal outputs.
    """
    if target_res <= 0:
        raise ValueError("target_res must be positive.")

    if aggregation_fun.lower() != "mean":
        raise NotImplementedError(
            "This workflow currently supports aggregation_fun='mean' only."
        )

    if max_download_attempts < 0:
        raise ValueError("max_download_attempts must be zero or positive.")

    try:
        output_resampling = Resampling[wgs84_project_method]
    except KeyError as error:
        available = ", ".join(item.name for item in Resampling)
        raise ValueError(
            f"Unknown resampling method '{wgs84_project_method}'. "
            f"Available methods include: {available}"
        ) from error

    out_dir = _ensure_path(out_dir)
    tile_dir = out_dir / "01_downloaded_MNS_tiles_native_0p5m"
    coarse_tile_dir = out_dir / f"02_resampled_MNS_tiles_{target_res:g}m"
    final_dir = out_dir / f"03_final_DSM_{target_res:g}m"
    bad_tile_dir = out_dir / "00_bad_or_corrupted_MNS_tiles"

    for directory in (tile_dir, coarse_tile_dir, final_dir, bad_tile_dir):
        directory.mkdir(parents=True, exist_ok=True)

    print("\nReading AOI...")
    aoi_l93 = _read_aoi(aoi_path)

    aoi_out = _write_aoi(
        aoi_l93,
        out_dir / "AOI_Lambert93_EPSG2154.gpkg",
    )
    print(f"AOI saved here:\n{aoi_out}")

    selection_l93 = _selection_geometry(
        aoi_l93=aoi_l93,
        download_buffer=download_buffer,
        buffer_distance_m=buffer_distance_m,
    )

    print("\nLoading IGN MNS/DSM tile index...")
    selected_index, records = _load_and_select_tiles(
        selection_l93=selection_l93,
        layer=MNS_LAYER,
    )

    print(f"MNS/DSM tiles returned and selected: {len(records)}")
    print("\nExpected selected MNS tiles:")
    for record in records:
        print(record.name_download)

    selected_index_out = out_dir / "selected_MNS_tile_index.gpkg"
    if selected_index_out.exists():
        selected_index_out.unlink()
    selected_index.to_file(
        selected_index_out,
        layer="selected_mns_tiles",
        driver="GPKG",
    )

    print("\nDownloading native MNS/DSM tiles...")
    _download_tiles(
        records=records,
        tile_dir=tile_dir,
        n_workers=n_workers,
    )

    missing = _missing_expected_tiles(records, tile_dir)
    attempt = 1

    while missing and attempt <= max_download_attempts:
        print(
            f"\nRe-download attempt {attempt}: "
            f"{len(missing)} expected tile(s) are missing."
        )

        missing_set = set(missing)
        retry_records = [
            record
            for record in records
            if record.name_download in missing_set
        ]

        _download_tiles(
            records=retry_records,
            tile_dir=tile_dir,
            n_workers=n_workers,
            overwrite_names=missing_set,
        )

        missing = _missing_expected_tiles(records, tile_dir)
        attempt += 1

    missing_report = out_dir / "missing_expected_MNS_tiles.txt"

    if missing:
        missing_report.write_text(
            "\n".join(missing) + "\n",
            encoding="utf-8",
        )
        raise RuntimeError(
            "Some expected MNS tiles remain missing. Check:\n"
            f"{missing_report}"
        )
    elif missing_report.exists():
        missing_report.unlink()

    native_paths = [
        tile_dir / record.name_download
        for record in records
    ]

    print("\nValidating native MNS tiles...")
    bad_native = [
        path
        for path in native_paths
        if not _validate_raster(path)
    ]

    if bad_native:
        report = bad_tile_dir / "bad_native_tiles.txt"
        report.write_text(
            "\n".join(str(path) for path in bad_native) + "\n",
            encoding="utf-8",
        )

        for path in bad_native:
            if path.exists():
                destination = bad_tile_dir / path.name
                if destination.exists():
                    destination.unlink()
                path.replace(destination)

        raise RuntimeError(
            "Some native MNS tiles are unreadable. They were moved to:\n"
            f"{bad_tile_dir}\nReport: {report}"
        )

    print(f"Valid native MNS tiles: {len(native_paths)}")

    print(f"\nAggregating each native MNS tile to {target_res:g} m...")
    statistics = []
    coarse_paths = []

    for index, native_path in enumerate(native_paths, start=1):
        print(f"Processing tile {index} of {len(native_paths)}: {native_path.name}")

        coarse_path = (
            coarse_tile_dir
            / f"{native_path.stem}_{target_res:g}m.tif"
        )

        stats = _aggregate_tile_to_resolution(
            native_path=native_path,
            out_path=coarse_path,
            target_res=target_res,
        )

        statistics.append(stats)
        coarse_paths.append(coarse_path)

    statistics_csv = (
        final_dir
        / f"tile_NA_statistics_{target_res:g}m.csv"
    )

    with statistics_csv.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["file", "ncell", "n_na", "pct_na"],
        )
        writer.writeheader()

        for stats in statistics:
            writer.writerow(
                {
                    "file": stats["file"],
                    "ncell": stats["ncell"],
                    "n_na": stats["n_na"],
                    "pct_na": stats["pct_na"],
                }
            )

    print(f"Tile NoData statistics saved here:\n{statistics_csv}")

    print(f"\nMosaicking {len(coarse_paths)} coarse MNS tiles...")
    mosaic, mosaic_transform, mosaic_crs = _mosaic_mean(coarse_paths)

    # Keep the complete mosaic of all selected buffered IGN tiles.
    # This is the preferred source for the integrated ASP reference DEMs.
    selected_tiles_mosaic_out = (
        final_dir
        / f"DSM_MNS_LiDARHD_{target_res:g}m_SELECTED_TILES_EPSG2154.tif"
    )
    _write_float_raster(
        out_path=selected_tiles_mosaic_out,
        array=mosaic,
        transform=mosaic_transform,
        crs=mosaic_crs,
    )

    bbox_array, bbox_transform = _crop_array_to_aoi_bbox(
        array=mosaic,
        transform=mosaic_transform,
        aoi_l93=aoi_l93,
    )

    masked_array = _mask_to_aoi(
        bbox_array=bbox_array,
        bbox_transform=bbox_transform,
        aoi_l93=aoi_l93,
    )

    bbox_out = (
        final_dir
        / f"DSM_MNS_LiDARHD_{target_res:g}m_AOI_BBOX_EPSG2154.tif"
    )
    masked_out = (
        final_dir
        / f"DSM_MNS_LiDARHD_{target_res:g}m_AOI_MASKED_EPSG2154.tif"
    )

    _write_float_raster(
        out_path=bbox_out,
        array=bbox_array,
        transform=bbox_transform,
        crs=mosaic_crs,
    )

    _write_float_raster(
        out_path=masked_out,
        array=masked_array,
        transform=bbox_transform,
        crs=mosaic_crs,
    )

    na_bbox = np.isnan(bbox_array).astype(np.uint8)
    na_masked = np.isnan(masked_array).astype(np.uint8)

    na_bbox_out = (
        final_dir
        / f"NA_map_{target_res:g}m_AOI_BBOX_EPSG2154.tif"
    )
    na_masked_out = (
        final_dir
        / f"NA_map_{target_res:g}m_AOI_MASKED_EPSG2154.tif"
    )

    _write_uint8_raster(
        out_path=na_bbox_out,
        array=na_bbox,
        transform=bbox_transform,
        crs=mosaic_crs,
    )

    _write_uint8_raster(
        out_path=na_masked_out,
        array=na_masked,
        transform=bbox_transform,
        crs=mosaic_crs,
    )

    bbox_na_cells = int(na_bbox.sum())
    masked_na_cells = int(na_masked.sum())

    print("\nNoData diagnostic:")
    print(f"BBOX total cells: {bbox_array.size}")
    print(f"BBOX NoData cells before AOI mask: {bbox_na_cells}")
    print(
        "BBOX NoData percentage: "
        f"{100 * bbox_na_cells / bbox_array.size:.6f}%"
    )
    print(f"MASKED total cells: {masked_array.size}")
    print(f"MASKED NoData cells after AOI mask: {masked_na_cells}")
    print(
        "MASKED NoData percentage: "
        f"{100 * masked_na_cells / masked_array.size:.6f}%"
    )

    outputs: dict[str, Path] = {
        "aoi_lambert93": aoi_out,
        "selected_tile_index": selected_index_out,
        "selected_tiles_mosaic_epsg2154": selected_tiles_mosaic_out,
        "tile_statistics": statistics_csv,
        "bbox_epsg2154": bbox_out,
        "masked_epsg2154": masked_out,
        "na_bbox_epsg2154": na_bbox_out,
        "na_masked_epsg2154": na_masked_out,
    }

    if export_to_wgs84:
        print(f"\nExporting {output_crs} copies...")

        bbox_wgs84_out = (
            final_dir
            / f"DSM_MNS_LiDARHD_{target_res:g}m_AOI_BBOX_EPSG4326.tif"
        )
        masked_wgs84_out = (
            final_dir
            / f"DSM_MNS_LiDARHD_{target_res:g}m_AOI_MASKED_EPSG4326.tif"
        )

        _reproject_raster(
            src_path=bbox_out,
            dst_path=bbox_wgs84_out,
            dst_crs=output_crs,
            resampling=output_resampling,
        )

        _reproject_raster(
            src_path=masked_out,
            dst_path=masked_wgs84_out,
            dst_crs=output_crs,
            resampling=output_resampling,
        )

        outputs["bbox_epsg4326"] = bbox_wgs84_out
        outputs["masked_epsg4326"] = masked_wgs84_out

    if plot_results:
        _plot_raster(
            bbox_out,
            title=f"DSM / MNS {target_res:g} m – EPSG:2154 – AOI BBOX",
            colorbar_label="Elevation (m)",
        )
        _plot_raster(
            na_bbox_out,
            title=f"NoData map before AOI mask – {target_res:g} m",
        )
        _plot_raster(
            masked_out,
            title=f"DSM / MNS {target_res:g} m – EPSG:2154 – AOI MASKED",
            colorbar_label="Elevation (m)",
        )
        _plot_raster(
            na_masked_out,
            title=f"NoData map after AOI mask – {target_res:g} m",
        )

    print("\nProcessing finished successfully.")
    print("\nMain outputs:")
    print(f"EPSG:2154 BBOX DSM:\n{bbox_out}")
    print(f"EPSG:2154 MASKED DSM:\n{masked_out}")

    if export_to_wgs84:
        print(f"EPSG:4326 BBOX DSM:\n{outputs['bbox_epsg4326']}")
        print(f"EPSG:4326 MASKED DSM:\n{outputs['masked_epsg4326']}")

    return outputs


__all__ = ["download_ign_lidarhd_dsm"]
