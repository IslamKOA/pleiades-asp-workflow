
"""
pleiades_reference_dem.py

Reference-DEM preparation used only by the Pléiades ASP pre-processing UI.

The ASP workflow needs two reference surfaces:

1. High-resolution alignment DSM
   Used by pc_align.
   Tested default in France: IGN LiDAR HD at 1 m.

2. Generalized map-projection DEM
   Used by mapproject.
   Tested LiDAR default: 50 m.
   Copernicus/SRTM default: 30 m.

Downloaded reference DEMs are converted to ellipsoidal heights before ASP.
For existing DEMs, the user can explicitly choose whether conversion is needed.

France workflow default:
    RAF20
but the user can override it with EGM96, EGM2008 or a custom N raster.

Other/global defaults:
    SRTM       -> EGM96
    Copernicus -> EGM2008

Horizontal CRS preparation and vertical height conversion are independent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import shutil

import numpy as np
import rasterio
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject

import global_dem_downloader as global_dem
import ign_lidarhd_downloader as ign_lidar
from vertical_reference import build_n_raster, convert_between_models


MODULE_DIR = Path(__file__).resolve().parent
RAF20_PATH = MODULE_DIR / "data" / "RAF20.tac"


@dataclass
class IntegratedReferenceDEMSettings:
    project_dir: Path
    target_epsg: int

    region: str
    aoi_path: str

    map_source: str
    map_resolution_m: float
    map_existing_path: str = ""
    map_existing_convert_to_ellipsoid: bool = False

    alignment_source: str = "existing"
    alignment_resolution_m: float = 1.0
    alignment_existing_path: str = ""
    alignment_existing_convert_to_ellipsoid: bool = False

    geoid_model: str = "raf20"
    custom_n_raster: str = ""

    global_buffer_deg: float = 0.05
    ign_buffer_m: float = 1000.0
    ign_workers: int = 2


def _require_file(path, label):
    path = Path(path).expanduser()

    if not path.is_file():
        raise FileNotFoundError(
            f"{label} not found:\n{path}"
        )

    return path


def _valid_raster(path, label):
    path = Path(path)

    with rasterio.open(path) as src:
        if src.crs is None:
            raise ValueError(
                f"{label} has no CRS:\n{path}"
            )

        data = src.read(
            1,
            masked=True,
        )

        values = np.asarray(
            data.compressed(),
            dtype=np.float64,
        )

    values = values[
        np.isfinite(values)
    ]

    if values.size == 0:
        raise RuntimeError(
            f"{label} contains no finite values:\n{path}"
        )

    return path


def _resampling(name):
    return Resampling[
        str(name)
    ]


def _reproject_resample(
    src_path,
    dst_path,
    dst_crs,
    resolution_m,
    resampling="bilinear",
):
    src_path = Path(
        src_path
    )

    dst_path = Path(
        dst_path
    )

    dst_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    dst_crs = CRS.from_user_input(
        dst_crs
    )

    with rasterio.open(
        src_path
    ) as src:
        if src.crs is None:
            raise ValueError(
                f"Reference DEM has no CRS:\n{src_path}"
            )

        kwargs = {}

        if dst_crs.is_projected:
            kwargs[
                "resolution"
            ] = float(
                resolution_m
            )

        transform, width, height = (
            calculate_default_transform(
                src.crs,
                dst_crs,
                src.width,
                src.height,
                *src.bounds,
                **kwargs,
            )
        )

        profile = src.profile.copy()
        profile.pop(
            "blockxsize",
            None,
        )
        profile.pop(
            "blockysize",
            None,
        )

        tiled = (
            width >= 256
            and height >= 256
        )

        profile.update(
            driver="GTiff",
            crs=dst_crs,
            transform=transform,
            width=width,
            height=height,
            count=1,
            dtype="float32",
            nodata=np.nan,
            compress="deflate",
            tiled=tiled,
            BIGTIFF="IF_SAFER",
        )

        if tiled:
            profile.update(
                blockxsize=256,
                blockysize=256,
            )

        with rasterio.open(
            dst_path,
            "w",
            **profile,
        ) as dst:
            reproject(
                source=rasterio.band(
                    src,
                    1,
                ),
                destination=rasterio.band(
                    dst,
                    1,
                ),
                src_transform=src.transform,
                src_crs=src.crs,
                src_nodata=src.nodata,
                dst_transform=transform,
                dst_crs=dst_crs,
                dst_nodata=np.nan,
                resampling=_resampling(
                    resampling
                ),
            )

    return _valid_raster(
        dst_path,
        "Prepared reference DEM",
    )


def _convert_to_ellipsoid(
    working_dem,
    output_path,
    geoid_model,
    custom_n_raster,
    model_dir,
    grid_cache_dir,
    model_tag,
):
    working_dem = Path(
        working_dem
    )

    output_path = Path(
        output_path
    )

    model_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    n_path = (
        model_dir
        / f"N_{model_tag}_{geoid_model}.tif"
    )

    custom_path = (
        Path(
            custom_n_raster
        ).expanduser()
        if geoid_model == "custom"
        else None
    )

    build_n_raster(
        template_dem=working_dem,
        output_path=n_path,
        model=geoid_model,
        raf20_path=RAF20_PATH,
        custom_n_raster=custom_path,
        grid_cache_dir=grid_cache_dir,
    )

    convert_between_models(
        dem_path=working_dem,
        output_path=output_path,
        source_model=geoid_model,
        target_model="ellipsoid",
        source_n_path=n_path,
        target_n_path=None,
    )

    _valid_raster(
        output_path,
        "Ellipsoidal reference DEM",
    )

    return (
        output_path,
        n_path,
    )


def _download_global(
    source,
    aoi_path,
    output_dir,
    buffer_deg,
):
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    if source == "copernicus":
        out = (
            output_dir
            / "Copernicus_GLO30_WGS84.tif"
        )

        global_dem.download_copernicus(
            aoi_path=aoi_path,
            out_tif=out,
            buffer_deg=buffer_deg,
            plot=False,
        )

    elif source == "srtm":
        out = (
            output_dir
            / "SRTM1_30m_WGS84.tif"
        )

        global_dem.download_srtm(
            aoi_path=aoi_path,
            out_tif=out,
            buffer_deg=buffer_deg,
            plot=False,
        )

    else:
        raise ValueError(
            f"Unsupported global DEM source: {source}"
        )

    return _valid_raster(
        out,
        f"Downloaded {source} DEM",
    )


def _download_ign(
    aoi_path,
    output_dir,
    resolution_m,
    buffer_m,
    workers,
):
    outputs = (
        ign_lidar.download_ign_lidarhd_dsm(
            aoi_path=aoi_path,
            out_dir=output_dir,
            target_res=float(
                resolution_m
            ),
            download_buffer=True,
            buffer_distance_m=float(
                buffer_m
            ),
            n_workers=int(
                workers
            ),
            aggregation_fun="mean",
            export_to_wgs84=False,
            plot_results=False,
        )
    )

    return _valid_raster(
        outputs[
            "bbox_epsg2154"
        ],
        "IGN LiDAR HD reference DEM",
    )


def _prepare_existing(
    source_path,
    output_path,
    target_crs,
    resolution_m,
    convert_to_ellipsoid,
    geoid_model,
    custom_n_raster,
    model_dir,
    grid_cache_dir,
    role,
):
    source_path = _require_file(
        source_path,
        f"Existing {role} DEM",
    )

    horizontal = (
        output_path.parent
        / (
            output_path.stem
            + "_horizontal_tmp.tif"
        )
    )

    _reproject_resample(
        source_path,
        horizontal,
        target_crs,
        resolution_m,
    )

    n_path = None

    if convert_to_ellipsoid:
        final, n_path = (
            _convert_to_ellipsoid(
                horizontal,
                output_path,
                geoid_model,
                custom_n_raster,
                model_dir,
                grid_cache_dir,
                role,
            )
        )

        horizontal.unlink(
            missing_ok=True
        )

    else:
        shutil.move(
            str(
                horizontal
            ),
            str(
                output_path
            ),
        )

        final = _valid_raster(
            output_path,
            f"Existing {role} DEM",
        )

    return (
        final,
        n_path,
    )


def prepare_integrated_reference_dems(
    settings: IntegratedReferenceDEMSettings,
    progress_callback=None,
):
    project_dir = Path(
        settings.project_dir
    )

    ref_root = (
        project_dir
        / "reference_dems"
    )

    source_dir = (
        ref_root
        / "source_cache"
    )

    model_dir = (
        ref_root
        / "vertical_models"
    )

    grid_cache_dir = (
        ref_root
        / "vertical_grids"
    )

    final_dir = (
        ref_root
        / "prepared"
    )

    for folder in (
        ref_root,
        source_dir,
        model_dir,
        grid_cache_dir,
        final_dir,
    ):
        folder.mkdir(
            parents=True,
            exist_ok=True,
        )

    def progress(value, text):
        if progress_callback:
            progress_callback(
                int(value),
                text,
            )

    needs_aoi = (
        settings.map_source
        in {
            "ign",
            "copernicus",
            "srtm",
        }
        or settings.alignment_source
        == "ign"
    )

    aoi_path = (
        settings.aoi_path.strip()
    )

    if needs_aoi:
        if not aoi_path:
            raise ValueError(
                "Reference DEM preparation needs an AOI vector. "
                "Enter Reference DEM AOI, or provide the AOI "
                "in the Prepare data section so it can be reused."
            )

        _require_file(
            aoi_path,
            "Reference DEM AOI vector",
        )

    target_crs = (
        f"EPSG:{int(settings.target_epsg)}"
    )

    if (
        settings.region != "france"
        and (
            settings.map_source
            == "ign"
            or settings.alignment_source
            == "ign"
        )
    ):
        raise ValueError(
            "IGN LiDAR HD automatic reference preparation "
            "is available only in France — mainland mode."
        )

    progress(
        1,
        "Preparing high-resolution alignment reference",
    )

    alignment_n = None
    alignment_source_cache = None

    if (
        settings.alignment_source
        == "ign"
    ):
        alignment_source_cache = (
            _download_ign(
                aoi_path=aoi_path,
                output_dir=(
                    source_dir
                    / "IGN_alignment"
                ),
                resolution_m=(
                    settings.alignment_resolution_m
                ),
                buffer_m=(
                    settings.ign_buffer_m
                ),
                workers=(
                    settings.ign_workers
                ),
            )
        )

        alignment_horizontal = (
            final_dir
            / "Alignment_horizontal_tmp.tif"
        )

        _reproject_resample(
            alignment_source_cache,
            alignment_horizontal,
            target_crs,
            settings.alignment_resolution_m,
        )

        alignment_dem = (
            final_dir
            / (
                "Alignment_IGN_"
                f"{settings.alignment_resolution_m:g}m_"
                "Ellipsoidal_"
                f"EPSG{settings.target_epsg}.tif"
            )
        )

        alignment_dem, alignment_n = (
            _convert_to_ellipsoid(
                alignment_horizontal,
                alignment_dem,
                settings.geoid_model,
                settings.custom_n_raster,
                model_dir,
                grid_cache_dir,
                "alignment",
            )
        )

        alignment_horizontal.unlink(
            missing_ok=True
        )

    elif (
        settings.alignment_source
        == "existing"
    ):
        alignment_dem = (
            final_dir
            / (
                "Alignment_Existing_"
                f"{settings.alignment_resolution_m:g}m_"
                f"EPSG{settings.target_epsg}.tif"
            )
        )

        alignment_dem, alignment_n = (
            _prepare_existing(
                source_path=(
                    settings.alignment_existing_path
                ),
                output_path=(
                    alignment_dem
                ),
                target_crs=(
                    target_crs
                ),
                resolution_m=(
                    settings.alignment_resolution_m
                ),
                convert_to_ellipsoid=(
                    settings.alignment_existing_convert_to_ellipsoid
                ),
                geoid_model=(
                    settings.geoid_model
                ),
                custom_n_raster=(
                    settings.custom_n_raster
                ),
                model_dir=(
                    model_dir
                ),
                grid_cache_dir=(
                    grid_cache_dir
                ),
                role=(
                    "alignment"
                ),
            )
        )

    else:
        raise ValueError(
            "Choose a high-resolution alignment reference."
        )

    progress(
        3,
        "Preparing generalized map-projection reference",
    )

    map_n = None

    if (
        settings.map_source
        == "ign"
        and settings.alignment_source
        == "ign"
    ):
        # Reuse the already prepared ellipsoidal LiDAR reference.
        # This preserves the tested 1 m alignment / 50 m map-projection logic
        # without downloading the same IGN tiles twice.
        mapproject_dem = (
            final_dir
            / (
                "MapProjection_IGN_"
                f"{settings.map_resolution_m:g}m_"
                "Ellipsoidal_"
                f"EPSG{settings.target_epsg}.tif"
            )
        )

        _reproject_resample(
            alignment_dem,
            mapproject_dem,
            target_crs,
            settings.map_resolution_m,
        )

        map_n = alignment_n

    elif (
        settings.map_source
        == "ign"
    ):
        ign_map_source = (
            _download_ign(
                aoi_path=aoi_path,
                output_dir=(
                    source_dir
                    / "IGN_mapprojection"
                ),
                resolution_m=(
                    settings.map_resolution_m
                ),
                buffer_m=(
                    settings.ign_buffer_m
                ),
                workers=(
                    settings.ign_workers
                ),
            )
        )

        map_horizontal = (
            final_dir
            / "MapProjection_horizontal_tmp.tif"
        )

        _reproject_resample(
            ign_map_source,
            map_horizontal,
            target_crs,
            settings.map_resolution_m,
        )

        mapproject_dem = (
            final_dir
            / (
                "MapProjection_IGN_"
                f"{settings.map_resolution_m:g}m_"
                "Ellipsoidal_"
                f"EPSG{settings.target_epsg}.tif"
            )
        )

        mapproject_dem, map_n = (
            _convert_to_ellipsoid(
                map_horizontal,
                mapproject_dem,
                settings.geoid_model,
                settings.custom_n_raster,
                model_dir,
                grid_cache_dir,
                "mapprojection",
            )
        )

        map_horizontal.unlink(
            missing_ok=True
        )

    elif (
        settings.map_source
        in {
            "copernicus",
            "srtm",
        }
    ):
        global_source = (
            _download_global(
                source=(
                    settings.map_source
                ),
                aoi_path=(
                    aoi_path
                ),
                output_dir=(
                    source_dir
                    / settings.map_source
                ),
                buffer_deg=(
                    settings.global_buffer_deg
                ),
            )
        )

        map_horizontal = (
            final_dir
            / "MapProjection_horizontal_tmp.tif"
        )

        _reproject_resample(
            global_source,
            map_horizontal,
            target_crs,
            settings.map_resolution_m,
        )

        mapproject_dem = (
            final_dir
            / (
                "MapProjection_"
                f"{settings.map_source}_"
                f"{settings.map_resolution_m:g}m_"
                "Ellipsoidal_"
                f"EPSG{settings.target_epsg}.tif"
            )
        )

        # In the integrated Pléiades workflow ellipsoidal reference
        # topography is mandatory for downloaded DEMs. The model shown
        # in the UI is therefore always applied here.
        mapproject_dem, map_n = (
            _convert_to_ellipsoid(
                map_horizontal,
                mapproject_dem,
                settings.geoid_model,
                settings.custom_n_raster,
                model_dir,
                grid_cache_dir,
                "mapprojection",
            )
        )

        map_horizontal.unlink(
            missing_ok=True
        )

    elif (
        settings.map_source
        == "existing"
    ):
        mapproject_dem = (
            final_dir
            / (
                "MapProjection_Existing_"
                f"{settings.map_resolution_m:g}m_"
                f"EPSG{settings.target_epsg}.tif"
            )
        )

        mapproject_dem, map_n = (
            _prepare_existing(
                source_path=(
                    settings.map_existing_path
                ),
                output_path=(
                    mapproject_dem
                ),
                target_crs=(
                    target_crs
                ),
                resolution_m=(
                    settings.map_resolution_m
                ),
                convert_to_ellipsoid=(
                    settings.map_existing_convert_to_ellipsoid
                ),
                geoid_model=(
                    settings.geoid_model
                ),
                custom_n_raster=(
                    settings.custom_n_raster
                ),
                model_dir=(
                    model_dir
                ),
                grid_cache_dir=(
                    grid_cache_dir
                ),
                role=(
                    "mapprojection"
                ),
            )
        )

    else:
        raise ValueError(
            "Choose a map-projection reference DEM source."
        )

    progress(
        4,
        "Reference DEMs ready",
    )

    summary = {
        "region": (
            settings.region
        ),
        "target_epsg": (
            settings.target_epsg
        ),
        "geoid_model": (
            settings.geoid_model
        ),
        "alignment_source": (
            settings.alignment_source
        ),
        "alignment_resolution_m": (
            settings.alignment_resolution_m
        ),
        "alignment_dem": str(
            alignment_dem
        ),
        "map_source": (
            settings.map_source
        ),
        "map_resolution_m": (
            settings.map_resolution_m
        ),
        "mapproject_dem": str(
            mapproject_dem
        ),
        "alignment_geoid_model_raster": (
            str(
                alignment_n
            )
            if alignment_n
            else None
        ),
        "map_geoid_model_raster": (
            str(
                map_n
            )
            if map_n
            else None
        ),
    }

    config_path = (
        ref_root
        / "reference_dem_config.json"
    )

    config_path.write_text(
        json.dumps(
            summary,
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "alignment_dem": (
            Path(
                alignment_dem
            )
        ),
        "mapproject_dem": (
            Path(
                mapproject_dem
            )
        ),
        "alignment_n": (
            Path(
                alignment_n
            )
            if alignment_n
            else None
        ),
        "map_n": (
            Path(
                map_n
            )
            if map_n
            else None
        ),
        "config_path": (
            config_path
        ),
    }
