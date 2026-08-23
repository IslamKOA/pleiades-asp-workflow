"""
Global DEM download utilities for AOI-based SRTM1 and Copernicus GLO-30 extraction.

The module preserves the original workflows:
- Read an AOI vector file.
- Convert the AOI bounds to WGS84.
- Determine the required 1° x 1° DEM tiles.
- Download and cache the source tiles.
- Mosaic and crop the tiles to the AOI bounding box.
- Save the result as a compressed GeoTIFF.
- Optionally display the downloaded DEM.

Public functions
----------------
download_srtm(...)
download_copernicus(...)
"""

from pathlib import Path
import gzip
import math
import urllib.error
import urllib.request

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.merge import merge
from rasterio.plot import plotting_extent
from rasterio.transform import from_origin


# ============================================================
# SRTM CONSTANTS
# ============================================================

SRTM_RES_DEG = 1.0 / 3600.0
SRTM_N_SAMPLES = 3601
SRTM_NODATA = -32768


# ============================================================
# COPERNICUS CONSTANTS
# ============================================================

COPERNICUS_BUCKET = "copernicus-dem-30m"
COPERNICUS_RESOLUTION_CODE = "10"


# ============================================================
# SHARED FUNCTIONS
# ============================================================

def _read_aoi_and_bounds(aoi_path, buffer_deg):
    """
    Read the AOI, convert it to EPSG:4326, and return buffered bounds.
    """
    aoi_path = Path(aoi_path)
    aoi = gpd.read_file(aoi_path)

    if aoi.empty:
        raise ValueError("AOI shapefile is empty.")

    if aoi.crs is None:
        raise ValueError(
            "AOI shapefile has no CRS. Please define the CRS before downloading the DEM."
        )

    aoi_wgs84 = aoi.to_crs("EPSG:4326")

    min_lon, min_lat, max_lon, max_lat = aoi_wgs84.total_bounds

    bounds = (
        float(min_lon - buffer_deg),
        float(min_lat - buffer_deg),
        float(max_lon + buffer_deg),
        float(max_lat + buffer_deg),
    )

    return aoi, aoi_wgs84, bounds


def _required_tile_coordinates(bounds):
    """
    Return the integer lower-left coordinates of all required 1-degree tiles.
    """
    left, bottom, right, top = bounds

    lon_min_tile = math.floor(left)
    lon_max_tile = math.floor(right - 1e-12)

    lat_min_tile = math.floor(bottom)
    lat_max_tile = math.floor(top - 1e-12)

    coordinates = []

    for lat_deg in range(lat_min_tile, lat_max_tile + 1):
        for lon_deg in range(lon_min_tile, lon_max_tile + 1):
            coordinates.append((lat_deg, lon_deg))

    return coordinates


def _print_bounds(bounds, label="AOI bounds in WGS84"):
    """
    Print AOI bounds in a consistent format.
    """
    print(label + ":")
    print(f"min_lon = {bounds[0]:.6f}")
    print(f"min_lat = {bounds[1]:.6f}")
    print(f"max_lon = {bounds[2]:.6f}")
    print(f"max_lat = {bounds[3]:.6f}")


def _print_dem_information(out_tif):
    """
    Print output DEM metadata and elevation statistics.
    """
    out_tif = Path(out_tif)

    with rasterio.open(out_tif) as src:
        print("\nOutput DEM information:")
        print(f"CRS: {src.crs}")
        print(f"Width: {src.width}")
        print(f"Height: {src.height}")
        print(f"Resolution: {src.res}")
        print(f"Bounds: {src.bounds}")
        print(f"NoData: {src.nodata}")
        print(f"Data type: {src.dtypes[0]}")

        data = src.read(1, masked=True)

        if data.count() == 0:
            print("The output DEM contains no valid elevation values.")
            return

        print(f"Min elevation: {float(data.min()):.2f} m")
        print(f"Max elevation: {float(data.max()):.2f} m")
        print(f"Mean elevation: {float(data.mean()):.2f} m")


def _plot_dem(out_tif, aoi, title):
    """
    Plot the downloaded DEM and the AOI boundary.
    """
    out_tif = Path(out_tif)

    with rasterio.open(out_tif) as src:
        dem = src.read(1, masked=True)
        extent = plotting_extent(src)
        aoi_plot = aoi.to_crs(src.crs)

    fig, ax = plt.subplots(figsize=(9, 7))

    image = ax.imshow(
        dem,
        extent=extent,
        origin="upper",
        cmap="terrain",
    )

    aoi_plot.boundary.plot(
        ax=ax,
        linewidth=1.5,
    )

    colorbar = fig.colorbar(
        image,
        ax=ax,
        shrink=0.85,
        pad=0.02,
    )
    colorbar.set_label("Elevation (m)")

    ax.set_title(title)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_aspect("equal")

    fig.tight_layout()
    plt.show()


# ============================================================
# SRTM FUNCTIONS
# ============================================================

def _srtm_tile_name(lat_deg, lon_deg):
    """
    Convert an integer tile lower-left corner to an SRTM HGT tile name.

    Example
    -------
    lat=44, lon=6 -> N44E006
    """
    ns = "N" if lat_deg >= 0 else "S"
    ew = "E" if lon_deg >= 0 else "W"

    return f"{ns}{abs(lat_deg):02d}{ew}{abs(lon_deg):03d}"


def _srtm_skadi_url(tile):
    """
    Build the public AWS Terrain Tiles / Skadi HGT URL.
    """
    lat_prefix = tile[:3]

    return (
        "https://s3.amazonaws.com/elevation-tiles-prod/"
        f"skadi/{lat_prefix}/{tile}.hgt.gz"
    )


def _download_srtm_hgt(tile, download_dir):
    """
    Download and read one SRTM1 .hgt.gz tile as a 3601 x 3601 array.
    """
    download_dir = Path(download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)

    url = _srtm_skadi_url(tile)
    gz_path = download_dir / f"{tile}.hgt.gz"

    if not gz_path.exists():
        print(f"Downloading {tile}:")
        print(url)
        urllib.request.urlretrieve(url, gz_path)
    else:
        print(f"Using cached tile: {gz_path}")

    with gzip.open(gz_path, "rb") as file:
        data = file.read()

    array = np.frombuffer(
        data,
        dtype=">i2",
    ).reshape(
        (SRTM_N_SAMPLES, SRTM_N_SAMPLES)
    )

    return array.astype(np.int16)


def _srtm_hgt_to_memory_dataset(array, lat_deg, lon_deg):
    """
    Convert an HGT array to an in-memory rasterio dataset.

    HGT samples are located on a 1 arc-second geographic grid.
    The half-pixel shift centers the GeoTIFF cells on the HGT samples.
    """
    west = lon_deg - SRTM_RES_DEG / 2.0
    north = lat_deg + 1.0 + SRTM_RES_DEG / 2.0

    transform = from_origin(
        west,
        north,
        SRTM_RES_DEG,
        SRTM_RES_DEG,
    )

    memfile = MemoryFile()

    dataset = memfile.open(
        driver="GTiff",
        height=array.shape[0],
        width=array.shape[1],
        count=1,
        dtype=array.dtype,
        crs="EPSG:4326",
        transform=transform,
        nodata=SRTM_NODATA,
    )

    dataset.write(array, 1)

    return memfile, dataset


def download_srtm(
    aoi_path,
    out_tif,
    buffer_deg=0.00,
    plot=False,
):
    """
    Download, mosaic, crop, and save an SRTM1 30 m DEM.

    Parameters
    ----------
    aoi_path : str or pathlib.Path
        AOI vector file.
    out_tif : str or pathlib.Path
        Output GeoTIFF path.
    buffer_deg : float, default=0.00
        Buffer applied to the AOI bounding box in decimal degrees.
    plot : bool, default=False
        Display the output DEM when True.

    Returns
    -------
    pathlib.Path
        Saved SRTM GeoTIFF path.
    """
    out_tif = Path(out_tif)
    out_dir = out_tif.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    aoi, _, bounds = _read_aoi_and_bounds(
        aoi_path=aoi_path,
        buffer_deg=buffer_deg,
    )

    _print_bounds(
        bounds,
        label="AOI bounds in WGS84",
    )

    tile_coordinates = _required_tile_coordinates(bounds)

    tiles = [
        (
            _srtm_tile_name(lat_deg, lon_deg),
            lat_deg,
            lon_deg,
        )
        for lat_deg, lon_deg in tile_coordinates
    ]

    print("\nRequired SRTM tiles:")
    for tile, lat_deg, lon_deg in tiles:
        print(
            f"{tile}  lower-left: "
            f"lat={lat_deg}, lon={lon_deg}"
        )

    tile_cache_dir = out_dir / "hgt_tiles"
    tile_cache_dir.mkdir(parents=True, exist_ok=True)

    memfiles = []
    datasets = []

    try:
        for tile, lat_deg, lon_deg in tiles:
            array = _download_srtm_hgt(
                tile=tile,
                download_dir=tile_cache_dir,
            )

            memfile, dataset = _srtm_hgt_to_memory_dataset(
                array=array,
                lat_deg=lat_deg,
                lon_deg=lon_deg,
            )

            memfiles.append(memfile)
            datasets.append(dataset)

        print("\nMosaicking and cropping to AOI bounds...")

        mosaic, out_transform = merge(
            datasets,
            bounds=bounds,
            res=(SRTM_RES_DEG, SRTM_RES_DEG),
            nodata=SRTM_NODATA,
        )

        out_meta = {
            "driver": "GTiff",
            "height": mosaic.shape[1],
            "width": mosaic.shape[2],
            "count": 1,
            "dtype": mosaic.dtype,
            "crs": "EPSG:4326",
            "transform": out_transform,
            "nodata": SRTM_NODATA,
            "compress": "deflate",
            "tiled": True,
        }

        with rasterio.open(out_tif, "w", **out_meta) as dst:
            dst.write(mosaic)

    finally:
        for dataset in datasets:
            dataset.close()

        for memfile in memfiles:
            memfile.close()

    print("\nSaved SRTM 30 m DEM:")
    print(out_tif)

    _print_dem_information(out_tif)

    if plot:
        _plot_dem(
            out_tif=out_tif,
            aoi=aoi,
            title="SRTM1 30 m DEM",
        )

    return out_tif


# ============================================================
# COPERNICUS FUNCTIONS
# ============================================================

def _copernicus_tile_id(
    lat_deg,
    lon_deg,
    resolution_code=COPERNICUS_RESOLUTION_CODE,
):
    """
    Build a Copernicus DEM COG tile ID.

    Example
    -------
    lat=44, lon=6
    -> Copernicus_DSM_COG_10_N44_00_E006_00_DEM
    """
    ns = "N" if lat_deg >= 0 else "S"
    ew = "E" if lon_deg >= 0 else "W"

    return (
        f"Copernicus_DSM_COG_{resolution_code}_"
        f"{ns}{abs(lat_deg):02d}_00_"
        f"{ew}{abs(lon_deg):03d}_00_DEM"
    )


def _copernicus_tile_url(
    tile_id,
    bucket=COPERNICUS_BUCKET,
):
    """
    Build the public HTTPS URL for one Copernicus DEM COG tile.
    """
    return (
        f"https://{bucket}.s3.amazonaws.com/"
        f"{tile_id}/{tile_id}.tif"
    )


def _download_file(url, out_path):
    """
    Download a file only when it does not already exist.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"Already exists: {out_path.name}")
        return out_path

    print(f"Downloading:\n{url}")

    try:
        urllib.request.urlretrieve(url, out_path)

    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise FileNotFoundError(
                f"Tile not found online:\n{url}"
            ) from error

        raise

    return out_path


def download_copernicus(
    aoi_path,
    out_tif,
    buffer_deg=0.05,
    plot=False,
    bucket=COPERNICUS_BUCKET,
    resolution_code=COPERNICUS_RESOLUTION_CODE,
):
    """
    Download, mosaic, crop, and save a Copernicus DEM GLO-30 DEM.

    Parameters
    ----------
    aoi_path : str or pathlib.Path
        AOI vector file.
    out_tif : str or pathlib.Path
        Output GeoTIFF path.
    buffer_deg : float, default=0.05
        Buffer applied to the AOI bounding box in decimal degrees.
    plot : bool, default=False
        Display the output DEM when True.
    bucket : str
        Copernicus DEM public S3 bucket.
    resolution_code : str
        Resolution code used in the Copernicus tile name.

    Returns
    -------
    pathlib.Path
        Saved Copernicus GeoTIFF path.
    """
    out_tif = Path(out_tif)
    out_dir = out_tif.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    aoi, _, bounds = _read_aoi_and_bounds(
        aoi_path=aoi_path,
        buffer_deg=buffer_deg,
    )

    _print_bounds(
        bounds,
        label="Buffered AOI bounds in WGS84",
    )

    tile_coordinates = _required_tile_coordinates(bounds)

    tile_ids = [
        _copernicus_tile_id(
            lat_deg=lat_deg,
            lon_deg=lon_deg,
            resolution_code=resolution_code,
        )
        for lat_deg, lon_deg in tile_coordinates
    ]

    print("\nRequired Copernicus DEM GLO-30 tiles:")
    for tile_id in tile_ids:
        print(tile_id)

    tile_dir = out_dir / "tiles"
    tile_dir.mkdir(parents=True, exist_ok=True)

    tile_paths = []

    for tile_id in tile_ids:
        url = _copernicus_tile_url(
            tile_id=tile_id,
            bucket=bucket,
        )

        tile_path = tile_dir / f"{tile_id}.tif"

        _download_file(
            url=url,
            out_path=tile_path,
        )

        tile_paths.append(tile_path)

    print("\nMosaicking and cropping Copernicus DEM...")

    src_files = []

    try:
        for tile_path in tile_paths:
            src_files.append(
                rasterio.open(tile_path)
            )

        mosaic, out_transform = merge(
            src_files,
            bounds=bounds,
        )

        out_meta = src_files[0].meta.copy()

        out_meta.update({
            "driver": "GTiff",
            "height": mosaic.shape[1],
            "width": mosaic.shape[2],
            "transform": out_transform,
            "count": 1,
            "compress": "deflate",
            "tiled": True,
            "nodata": src_files[0].nodata,
        })

        with rasterio.open(out_tif, "w", **out_meta) as dst:
            dst.write(mosaic)

    finally:
        for src in src_files:
            src.close()

    print("\nSaved Copernicus DEM:")
    print(out_tif)

    _print_dem_information(out_tif)

    if plot:
        _plot_dem(
            out_tif=out_tif,
            aoi=aoi,
            title="Copernicus DEM GLO-30",
        )

    return out_tif


__all__ = [
    "download_srtm",
    "download_copernicus",
]
