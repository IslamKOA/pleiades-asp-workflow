"""
Pléiades ASP scientific workflow utilities.

Import directly with::

    import asp_utils

The scientific functions originate from the validated notebook helper module
and are packaged here so users do not need to copy a local .py file.
"""

# ============================================================
# ASP WORKFLOW PYTHON UTILITIES
# ============================================================

from pathlib import Path
import re
import os
import shlex
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.colors import LightSource
from rasterio.enums import Resampling
from rasterio.windows import from_bounds
import matplotlib as mpl


from pleiades_asp_runner import (
    run_bash,
    host_to_runtime_path,
    runtime_to_host_path,
)

__version__ = "1.0.6"


def _runtime_source_path(path):
    """Return a safely quoted runtime path for Bash `source`."""
    return shlex.quote(host_to_runtime_path(path))


def _path_from_shell(value, base_directory=None):
    """Convert a path emitted by the Bash configuration to a host Path."""
    raw = runtime_to_host_path(str(value).strip())
    path = Path(raw)

    if path.is_absolute():
        return path

    if base_directory is None:
        return path

    return (Path(base_directory) / path).resolve()

# ============================================================
# 1. READ WORKFLOW SETTINGS FROM THE .SH FILE
# ============================================================

def read_asp_configuration(metadata_sh):
    """
    Read the ASP output prefix and active image views from
    the central Bash configuration file.
    """
    metadata_sh = Path(metadata_sh).resolve()

    if not metadata_sh.is_file():
        raise FileNotFoundError(
            f"Configuration file not found:\n{metadata_sh}"
        )

    command = f'''
        source {_runtime_source_path(metadata_sh)} >/dev/null

        printf "BA_PREFIX=%s\\n" "${{BA_PREFIX}}"
        printf "VIEW_IDS=%s\\n" "${{VIEW_IDS[*]}}"
    '''

    result = run_bash(
        command,
        cwd=metadata_sh.parent,
        capture_output=True,
        text=True,
        check=True,
    )

    config = {}

    for line in result.stdout.splitlines():
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        config[key.strip()] = value.strip()

    if "BA_PREFIX" not in config:
        raise ValueError(
            "BA_PREFIX is not defined in the Bash configuration."
        )

    if "VIEW_IDS" not in config:
        raise ValueError(
            "VIEW_IDS is not defined in the Bash configuration."
        )

    config["VIEW_IDS"] = config["VIEW_IDS"].split()
    config["BA_PREFIX"] = str(
        _path_from_shell(config["BA_PREFIX"], metadata_sh.parent)
    )

    return config


# ============================================================
# 2. PARSE ASP RESIDUAL-STATISTICS FILE
# ============================================================

def parse_residuals_stats(filepath, active_views):
    """
    Read the first section of an ASP residual-statistics file.

    Expected ASP format:

    Mean and median norm of residual error and point count for cameras:
    /path/A_crop.XML, mean, median, count
    /path/B_crop.XML, mean, median, count

    Camera weight position and orientation residual errors:
    ...
    """
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
        errors="replace"
    ) as file:

        for raw_line in file:
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
                camera_name
            )

            if match is None:
                continue

            image_id = match.group(1).upper()

            if image_id not in active_views:
                continue

            records.append({
                "Image": image_id,
                "Mean": float(parts[1]),
                "Median": float(parts[2]),
                "Count": int(float(parts[3]))
            })

    if not records:
        raise ValueError(
            f"No camera residual rows were found in:\n{filepath}"
        )

    return (
        pd.DataFrame(records)
        .set_index("Image")
        .reindex(active_views)
    )


# ============================================================
# 3. BUNDLE-ADJUSTMENT RESIDUAL SUMMARY
# ============================================================

def get_bundle_adjustment_residuals(metadata_sh):
    """
    Read the initial and final bundle-adjustment residual files
    and return a combined DataFrame.
    """
    config = read_asp_configuration(metadata_sh)

    ba_prefix = Path(config["BA_PREFIX"])
    active_views = config["VIEW_IDS"]

    initial_path = Path(
        f"{ba_prefix}-initial_residuals_stats.txt"
    )

    final_path = Path(
        f"{ba_prefix}-final_residuals_stats.txt"
    )

    initial_df = parse_residuals_stats(
        initial_path,
        active_views
    )

    final_df = parse_residuals_stats(
        final_path,
        active_views
    )

    summary = pd.DataFrame({
        "Initial mean (px)": initial_df["Mean"],
        "Final mean (px)": final_df["Mean"],
        "Initial median (px)": initial_df["Median"],
        "Final median (px)": final_df["Median"],
        "Point count": final_df["Count"]
    })

    summary.index.name = "Image"

    return summary.round({
        "Initial mean (px)": 4,
        "Final mean (px)": 4,
        "Initial median (px)": 4,
        "Final median (px)": 4,
        "Point count": 0
    })

# ============================================================
# READ SELECTED VARIABLES FROM THE BASH CONFIGURATION
# ============================================================

def read_shell_variables(metadata_sh, variable_names):
    """
    Read selected variables from the central Bash configuration.
    """
    metadata_sh = Path(metadata_sh).resolve()

    if not metadata_sh.is_file():
        raise FileNotFoundError(
            f"Configuration file not found:\n{metadata_sh}"
        )

    print_commands = "\n".join(
        f'printf "{name}=%s\\n" "${{{name}}}"'
        for name in variable_names
    )

    command = f'''
        source {_runtime_source_path(metadata_sh)} >/dev/null
        {print_commands}
    '''

    result = run_bash(
        command,
        cwd=metadata_sh.parent,
        capture_output=True,
        text=True,
        check=True,
    )

    values = {}

    for line in result.stdout.splitlines():
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    missing = [
        name for name in variable_names
        if name not in values
    ]

    if missing:
        raise ValueError(
            f"Variables not found in {metadata_sh.name}: {missing}"
        )

    return values


# ============================================================
# PLOT PRELIMINARY ALIGNMENT DEM
# ============================================================

def plot_preliminary_dem(
    metadata_sh,
    max_display_size=1800,
    hillshade_alpha=0.30,
    percentile_stretch=(2, 98),
    figsize=(10, 8),
):
    """
    Read and plot the preliminary alignment DEM defined in the
    central Bash configuration.

    The DEM is displayed with an elevation colour map and a
    semi-transparent hillshade.
    """

    config = read_shell_variables(
        metadata_sh,
        [
            "PRELIM_DEM",
            "area_name",
            "PRELIM_PAIR_TAG",
        ]
    )

    dem_path = _path_from_shell(
        config["PRELIM_DEM"],
        Path(metadata_sh).resolve().parent,
    )

    if not dem_path.is_file():
        raise FileNotFoundError(
            f"Preliminary DEM not found:\n{dem_path}"
        )

    with rasterio.open(dem_path) as src:

        scale = max(
            1,
            int(
                np.ceil(
                    max(src.width, src.height)
                    / max_display_size
                )
            )
        )

        display_height = max(1, src.height // scale)
        display_width = max(1, src.width // scale)

        dem = src.read(
            1,
            out_shape=(display_height, display_width),
            resampling=Resampling.bilinear,
            masked=True
        ).astype(np.float32)

        bounds = src.bounds
        crs = src.crs
        nodata = src.nodata

    valid_values = dem.compressed()

    if valid_values.size == 0:
        raise ValueError(
            f"The DEM contains no valid elevation values:\n{dem_path}"
        )

    vmin, vmax = np.percentile(
        valid_values,
        percentile_stretch
    )

    # Fill masked cells only for hillshade calculation
    fill_value = float(np.median(valid_values))
    dem_filled = dem.filled(fill_value)

    light_source = LightSource(
        azdeg=315,
        altdeg=45
    )

    hillshade = light_source.hillshade(
        dem_filled,
        vert_exag=1.0
    )

    hillshade = np.ma.array(
        hillshade,
        mask=np.ma.getmaskarray(dem)
    )

    extent = [
        bounds.left,
        bounds.right,
        bounds.bottom,
        bounds.top
    ]

    fig, ax = plt.subplots(
        figsize=figsize
    )

    elevation_plot = ax.imshow(
        dem,
        extent=extent,
        origin="upper",
        cmap="terrain",
        vmin=vmin,
        vmax=vmax
    )

    ax.imshow(
        hillshade,
        extent=extent,
        origin="upper",
        cmap="gray",
        alpha=hillshade_alpha
    )

    colorbar = fig.colorbar(
        elevation_plot,
        ax=ax,
        shrink=0.82,
        pad=0.025
    )

    colorbar.set_label(
        "Elevation (m)"
    )

    ax.set_title(
        f"{config['area_name']} — Preliminary "
        f"{config['PRELIM_PAIR_TAG']} alignment DEM"
    )

    if crs is not None and crs.is_geographic:
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
    else:
        ax.set_xlabel("Easting (m)")
        ax.set_ylabel("Northing (m)")

    ax.set_aspect("equal")
    ax.ticklabel_format(
        style="plain",
        axis="both",
        useOffset=False
    )

    fig.tight_layout()
    plt.show()

    return dem_path

# ============================================================
# IMAGE PLOTTING UTILITIES
#
# Public notebook functions:
#     plot_cropped_images(...)
#     plot_mapprojected_images(...)
#
# Both functions read all paths and the target CRS from the
# central Bash configuration file. Figures are saved
# automatically under:
#
#     ASP_OUTPUT_DIR/<output_folder_name>/
# ============================================================

from xml.etree import ElementTree as ET

from matplotlib.ticker import MultipleLocator
from pyproj import CRS, Transformer


# ============================================================
# READ IMAGE-PLOTTING SETTINGS FROM THE BASH CONFIGURATION
# ============================================================

def _read_image_plot_configuration(metadata_sh):
    """
    Read the active images, RPC files, map-projected images,
    target CRS, reference DEM, area name, and ASP output folder
    from the central Bash configuration.
    """
    metadata_sh = Path(metadata_sh).resolve()

    if not metadata_sh.is_file():
        raise FileNotFoundError(
            f"Configuration file not found:\n{metadata_sh}"
        )

    command = f"""
        source {_runtime_source_path(metadata_sh)} >/dev/null

        printf "AREA\\t%s\\n" "${{area_name}}"
        printf "TARGET_EPSG\\t%s\\n" "${{TARGET_EPSG}}"
        printf "ASP_OUTPUT_DIR\\t%s\\n" "${{ASP_OUTPUT_DIR}}"
        printf "MAPPROJECT_DEM\\t%s\\n" "${{MAPPROJECT_DEM}}"

        for index in "${{!VIEW_IDS[@]}}"; do
            printf "VIEW\\t%s\\t%s\\t%s\\t%s\\n" \
                "${{VIEW_IDS[$index]}}" \
                "${{IMAGE_FILES[$index]}}" \
                "${{RPC_FILES[$index]}}" \
                "${{MAPPROJECTED_IMAGES[$index]}}"
        done
    """

    result = run_bash(
        command,
        cwd=metadata_sh.parent,
        capture_output=True,
        text=True,
        check=True,
    )

    area_name = None
    target_epsg = None
    asp_output_dir = None
    mapproject_dem = None

    views = []
    cropped_images = []
    rpc_files = []
    mapprojected_images = []

    for line in result.stdout.splitlines():
        parts = line.split("\t")

        if not parts:
            continue

        key = parts[0]

        if key == "AREA" and len(parts) >= 2:
            area_name = parts[1]

        elif key == "TARGET_EPSG" and len(parts) >= 2:
            target_epsg = parts[1]

        elif key == "ASP_OUTPUT_DIR" and len(parts) >= 2:
            asp_output_dir = parts[1]

        elif key == "MAPPROJECT_DEM" and len(parts) >= 2:
            mapproject_dem = parts[1]

        elif key == "VIEW" and len(parts) >= 5:
            views.append(parts[1])
            cropped_images.append(parts[2])
            rpc_files.append(parts[3])
            mapprojected_images.append(parts[4])

    if area_name is None:
        raise ValueError(
            "area_name was not read from the Bash configuration."
        )

    if target_epsg is None:
        raise ValueError(
            "TARGET_EPSG was not read from the Bash configuration."
        )

    if asp_output_dir is None:
        raise ValueError(
            "ASP_OUTPUT_DIR was not read from the Bash configuration."
        )

    if mapproject_dem is None:
        raise ValueError(
            "MAPPROJECT_DEM was not read from the Bash configuration."
        )

    if not views:
        raise ValueError(
            "No active views were read from the Bash configuration."
        )

    def resolve_path(path):
        return _path_from_shell(
            path,
            metadata_sh.parent,
        )

    return {
        "metadata_sh": metadata_sh,
        "area_name": area_name,
        "target_crs": CRS.from_epsg(
            int(target_epsg)
        ),
        "asp_output_dir": resolve_path(
            asp_output_dir
        ),
        "mapproject_dem": resolve_path(
            mapproject_dem
        ),
        "views": views,
        "cropped_images": [
            resolve_path(path)
            for path in cropped_images
        ],
        "rpc_files": [
            resolve_path(path)
            for path in rpc_files
        ],
        "mapprojected_images": [
            resolve_path(path)
            for path in mapprojected_images
        ],
    }


# ============================================================
# GENERAL PLOTTING HELPERS
# ============================================================

def _stretch_image(
    image,
    percentiles=(2, 98),
):
    """
    Apply a percentile contrast stretch to one masked image.
    """
    image = np.ma.asarray(
        image,
        dtype=np.float32,
    )

    valid_values = image.compressed()

    if valid_values.size == 0:
        raise ValueError(
            "The image contains no valid pixels."
        )

    lower, upper = np.percentile(
        valid_values,
        percentiles,
    )

    if upper <= lower:
        return np.ma.zeros_like(
            image
        )

    stretched = (
        image - lower
    ) / (
        upper - lower
    )

    return np.ma.clip(
        stretched,
        0,
        1,
    )


def _read_image_preview(
    image_path,
    max_display_size,
    percentiles,
):
    """
    Read a reduced-resolution preview while retaining the
    original raster metadata.
    """
    image_path = Path(image_path)

    if not image_path.is_file():
        raise FileNotFoundError(
            f"Image not found:\n{image_path}"
        )

    with rasterio.open(image_path) as source:
        scale = min(
            1.0,
            max_display_size
            / max(
                source.width,
                source.height,
            ),
        )

        display_width = max(
            2,
            int(round(source.width * scale)),
        )

        display_height = max(
            2,
            int(round(source.height * scale)),
        )

        image = source.read(
            1,
            out_shape=(
                display_height,
                display_width,
            ),
            resampling=Resampling.bilinear,
            masked=True,
        )

        metadata = {
            "original_width": source.width,
            "original_height": source.height,
            "display_width": display_width,
            "display_height": display_height,
            "crs": source.crs,
            "transform": source.transform,
            "bounds": source.bounds,
        }

    image = _stretch_image(
        image,
        percentiles=percentiles,
    )

    return image, metadata


def _safe_filename(value):
    """
    Convert a label to a filesystem-safe filename component.
    """
    return re.sub(
        r"[^A-Za-z0-9_.-]+",
        "_",
        str(value),
    ).strip("_")


def _create_figure_output_path(
    config,
    output_folder_name,
    output_filename,
    default_suffix,
):
    """
    Create the requested figure folder inside ASP_OUTPUT_DIR
    and return the final PDF path.
    """
    figure_directory = (
        config["asp_output_dir"]
        / output_folder_name
    )

    figure_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    if output_filename is None:
        output_filename = (
            f"{_safe_filename(config['area_name'])}"
            f"_{default_suffix}.pdf"
        )
    else:
        output_filename = Path(
            output_filename
        ).name

        if Path(output_filename).suffix.lower() != ".pdf":
            output_filename = (
                f"{output_filename}.pdf"
            )

    return (
        figure_directory
        / output_filename
    )


# ============================================================
# RPC READING AND CROPPED-IMAGE GEOLOCATION
# ============================================================

def load_rpc_from_xml(xml_path):
    """
    Load an RPC model from a Pléiades, Pléiades Neo, or SPOT
    XML file.

    XML namespaces are ignored.
    """
    try:
        from rpcm.rpc_model import RPCModel
    except ImportError:
        try:
            from rpcm import RPCModel
        except ImportError as error:
            raise ImportError(
                "The 'rpcm' package is required to plot cropped "
                "RPC images in map coordinates."
            ) from error

    xml_path = Path(xml_path)

    if not xml_path.is_file():
        raise FileNotFoundError(
            f"RPC XML file not found:\n{xml_path}"
        )

    root = ET.parse(
        xml_path
    ).getroot()

    xml_values = {}

    for element in root.iter():
        local_name = (
            element.tag
            .split("}")[-1]
            .strip()
        )

        if element.text is None:
            continue

        value = element.text.strip()

        if value:
            xml_values.setdefault(
                local_name,
                value,
            )

    def get_value(*possible_names):
        for name in possible_names:
            if name in xml_values:
                return xml_values[name]

        raise ValueError(
            "Required RPC tag not found.\n\n"
            f"Expected one of: {possible_names}\n"
            f"RPC file: {xml_path}"
        )

    def get_coefficients(prefix):
        return " ".join(
            get_value(
                f"{prefix}_{index}",
                f"{prefix}{index}",
            )
            for index in range(1, 21)
        )

    rpc_dictionary = {
        "LINE_OFF": get_value(
            "LINE_OFF"
        ),
        "SAMP_OFF": get_value(
            "SAMP_OFF"
        ),
        "LAT_OFF": get_value(
            "LAT_OFF"
        ),
        "LONG_OFF": get_value(
            "LONG_OFF",
            "LON_OFF",
        ),
        "HEIGHT_OFF": get_value(
            "HEIGHT_OFF"
        ),
        "LINE_SCALE": get_value(
            "LINE_SCALE"
        ),
        "SAMP_SCALE": get_value(
            "SAMP_SCALE"
        ),
        "LAT_SCALE": get_value(
            "LAT_SCALE"
        ),
        "LONG_SCALE": get_value(
            "LONG_SCALE",
            "LON_SCALE",
        ),
        "HEIGHT_SCALE": get_value(
            "HEIGHT_SCALE"
        ),
        "LINE_NUM_COEFF": get_coefficients(
            "LINE_NUM_COEFF"
        ),
        "LINE_DEN_COEFF": get_coefficients(
            "LINE_DEN_COEFF"
        ),
        "SAMP_NUM_COEFF": get_coefficients(
            "SAMP_NUM_COEFF"
        ),
        "SAMP_DEN_COEFF": get_coefficients(
            "SAMP_DEN_COEFF"
        ),
    }

    return RPCModel(
        rpc_dictionary,
        dict_format="geotiff",
    )


def _get_representative_rpc_height(
    dem_path,
    max_display_size=1000,
):
    """
    Calculate the median valid elevation of the mapproject DEM.

    The value is used only to geolocate the cropped-image
    preview. The actual ASP mapprojection remains terrain-aware.
    """
    dem_path = Path(dem_path)

    if not dem_path.is_file():
        raise FileNotFoundError(
            f"Mapproject DEM not found:\n{dem_path}"
        )

    with rasterio.open(dem_path) as source:
        scale = min(
            1.0,
            max_display_size
            / max(
                source.width,
                source.height,
            ),
        )

        display_width = max(
            1,
            int(round(source.width * scale)),
        )

        display_height = max(
            1,
            int(round(source.height * scale)),
        )

        dem = source.read(
            1,
            out_shape=(
                display_height,
                display_width,
            ),
            resampling=Resampling.bilinear,
            masked=True,
        ).astype(np.float32)

    valid_values = dem.compressed()

    if valid_values.size == 0:
        raise ValueError(
            "The mapproject DEM contains no valid elevations:\n"
            f"{dem_path}"
        )

    return float(
        np.median(valid_values)
    )


def _prepare_cropped_rpc_image(
    image_path,
    rpc_path,
    target_crs,
    rpc_height,
    max_display_size,
    percentiles,
):
    """
    Read and geolocate one cropped RPC image in the target CRS.
    """
    image, metadata = _read_image_preview(
        image_path=image_path,
        max_display_size=max_display_size,
        percentiles=percentiles,
    )

    rpc = load_rpc_from_xml(
        rpc_path
    )

    display_height = metadata[
        "display_height"
    ]

    display_width = metadata[
        "display_width"
    ]

    original_height = metadata[
        "original_height"
    ]

    original_width = metadata[
        "original_width"
    ]

    # Pixel-edge coordinates are used by pcolormesh.
    column_edges = np.linspace(
        -0.5,
        original_width - 0.5,
        display_width + 1,
        dtype=np.float64,
    )

    row_edges = np.linspace(
        -0.5,
        original_height - 0.5,
        display_height + 1,
        dtype=np.float64,
    )

    columns, rows = np.meshgrid(
        column_edges,
        row_edges,
    )

    flat_columns = columns.ravel()
    flat_rows = rows.ravel()

    heights = np.full(
        flat_columns.shape,
        float(rpc_height),
        dtype=np.float64,
    )

    longitudes, latitudes = rpc.localization(
        flat_columns,
        flat_rows,
        heights,
    )

    longitudes = np.asarray(
        longitudes,
        dtype=np.float64,
    )

    latitudes = np.asarray(
        latitudes,
        dtype=np.float64,
    )

    if (
        not np.all(np.isfinite(longitudes))
        or not np.all(np.isfinite(latitudes))
    ):
        raise ValueError(
            "RPC localization returned invalid coordinates for:\n"
            f"{image_path}"
        )

    transformer = Transformer.from_crs(
        CRS.from_epsg(4326),
        target_crs,
        always_xy=True,
    )

    eastings, northings = transformer.transform(
        longitudes,
        latitudes,
    )

    eastings = np.asarray(
        eastings,
        dtype=np.float64,
    ).reshape(
        display_height + 1,
        display_width + 1,
    )

    northings = np.asarray(
        northings,
        dtype=np.float64,
    ).reshape(
        display_height + 1,
        display_width + 1,
    )

    if (
        not np.all(np.isfinite(eastings))
        or not np.all(np.isfinite(northings))
    ):
        raise ValueError(
            "Projected RPC coordinates are invalid for:\n"
            f"{image_path}"
        )

    bounds = [
        float(np.nanmin(eastings)),
        float(np.nanmax(eastings)),
        float(np.nanmin(northings)),
        float(np.nanmax(northings)),
    ]

    return {
        "image": image,
        "plot_method": "pcolormesh",
        "eastings": eastings,
        "northings": northings,
        "extent": None,
        "bounds": bounds,
        "path": Path(image_path),
    }


# ============================================================
# MAP-PROJECTED IMAGE PREPARATION
# ============================================================

def _prepare_mapprojected_image(
    image_path,
    target_crs,
    max_display_size,
    percentiles,
):
    """
    Read one already map-projected image.
    """
    image, metadata = _read_image_preview(
        image_path=image_path,
        max_display_size=max_display_size,
        percentiles=percentiles,
    )

    source_crs = metadata[
        "crs"
    ]

    if source_crs is None:
        raise ValueError(
            "No CRS is defined for the map-projected image:\n"
            f"{image_path}"
        )

    source_crs = CRS.from_user_input(
        source_crs
    )

    if source_crs != target_crs:
        raise ValueError(
            "The map-projected image CRS does not match "
            "TARGET_EPSG in the Bash configuration.\n\n"
            f"Image: {image_path}\n"
            f"Image CRS: {source_crs.to_string()}\n"
            f"Target CRS: {target_crs.to_string()}"
        )

    bounds = metadata[
        "bounds"
    ]

    extent = [
        bounds.left,
        bounds.right,
        bounds.bottom,
        bounds.top,
    ]

    return {
        "image": image,
        "plot_method": "imshow",
        "eastings": None,
        "northings": None,
        "extent": extent,
        "bounds": extent,
        "path": Path(image_path),
    }


# ============================================================
# COMMON PUBLICATION PLOTTER
# ============================================================

def _plot_georeferenced_image_panels(
    prepared_images,
    views,
    target_crs,
    area_name,
    figure_description,
    output_pdf,

    use_common_extent=True,

    cmap="gray",
    interpolation="nearest",

    show_grid=True,
    grid_color="white",
    grid_alpha=0.22,
    grid_linewidth=0.5,
    grid_linestyle="--",
    grid_spacing=None,

    figsize_per_image=5.0,
    figure_height=6.3,

    panel_title_fontsize=12,
    axis_label_fontsize=10,
    coordinate_fontsize=8,
    suptitle_fontsize=14,
    show_suptitle=True,

    wspace=0.01,
    left_margin=0.07,
    right_margin=0.995,
    bottom_margin=0.12,
    top_margin=0.88,

    dpi=300,
    pdf_pad_inches=0.03,

    show=True,
    return_images=False,
):
    """
    Plot prepared images with shared projected-coordinate axes
    and save the result as a Matplotlib PDF.
    """
    if len(prepared_images) != len(views):
        raise ValueError(
            "The number of prepared images and views differs."
        )

    if not prepared_images:
        raise ValueError(
            "No images were provided for plotting."
        )

    view_labels = {
        "A": "A — Forward",
        "B": "B — Near-nadir",
        "C": "C — Backward",
    }

    if use_common_extent:
        plot_left = max(
            item["bounds"][0]
            for item in prepared_images
        )

        plot_right = min(
            item["bounds"][1]
            for item in prepared_images
        )

        plot_bottom = max(
            item["bounds"][2]
            for item in prepared_images
        )

        plot_top = min(
            item["bounds"][3]
            for item in prepared_images
        )

        if (
            plot_left >= plot_right
            or plot_bottom >= plot_top
        ):
            raise ValueError(
                "The images do not have a common projected extent."
            )

    else:
        plot_left = min(
            item["bounds"][0]
            for item in prepared_images
        )

        plot_right = max(
            item["bounds"][1]
            for item in prepared_images
        )

        plot_bottom = min(
            item["bounds"][2]
            for item in prepared_images
        )

        plot_top = max(
            item["bounds"][3]
            for item in prepared_images
        )

    if target_crs.is_geographic:
        x_label = "Longitude"
        y_label = "Latitude"
    else:
        x_label = "Easting (m)"
        y_label = "Northing (m)"

    pdf_settings = {
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "savefig.dpi": dpi,
    }

    with mpl.rc_context(
        pdf_settings
    ):
        n_images = len(
            prepared_images
        )

        fig, axes = plt.subplots(
            1,
            n_images,
            figsize=(
                figsize_per_image * n_images,
                figure_height,
            ),
            sharex=True,
            sharey=True,
            squeeze=False,
            gridspec_kw={
                "wspace": wspace,
            },
        )

        for index, (
            view,
            prepared,
        ) in enumerate(
            zip(
                views,
                prepared_images,
            )
        ):
            ax = axes[0, index]

            if prepared["plot_method"] == "imshow":
                ax.imshow(
                    prepared["image"],
                    cmap=cmap,
                    extent=prepared["extent"],
                    origin="upper",
                    interpolation=interpolation,
                    rasterized=True,
                    zorder=1,
                )

            elif prepared["plot_method"] == "pcolormesh":
                ax.pcolormesh(
                    prepared["eastings"],
                    prepared["northings"],
                    prepared["image"],
                    cmap=cmap,
                    shading="auto",
                    antialiased=False,
                    rasterized=True,
                    zorder=1,
                )

            else:
                raise ValueError(
                    "Unsupported plotting method:\n"
                    f"{prepared['plot_method']}"
                )

            ax.set_title(
                view_labels.get(
                    view,
                    str(view),
                ),
                fontsize=panel_title_fontsize,
                pad=7,
            )

            ax.set_xlabel(
                x_label,
                fontsize=axis_label_fontsize,
            )

            if index == 0:
                ax.set_ylabel(
                    y_label,
                    fontsize=axis_label_fontsize,
                )
            else:
                ax.tick_params(
                    axis="y",
                    labelleft=False,
                    left=False,
                )

            ax.ticklabel_format(
                style="plain",
                axis="both",
                useOffset=False,
            )

            ax.tick_params(
                axis="both",
                labelsize=coordinate_fontsize,
                direction="out",
                length=3.5,
                width=0.7,
            )

            if grid_spacing is not None:
                ax.xaxis.set_major_locator(
                    MultipleLocator(
                        grid_spacing
                    )
                )

                if index == 0:
                    ax.yaxis.set_major_locator(
                        MultipleLocator(
                            grid_spacing
                        )
                    )

            if show_grid:
                ax.grid(
                    True,
                    which="major",
                    color=grid_color,
                    alpha=grid_alpha,
                    linewidth=grid_linewidth,
                    linestyle=grid_linestyle,
                    zorder=3,
                )
            else:
                ax.grid(
                    False
                )

            ax.set_xlim(
                plot_left,
                plot_right,
            )

            ax.set_ylim(
                plot_bottom,
                plot_top,
            )

            ax.set_aspect(
                "equal"
            )

            for spine in ax.spines.values():
                spine.set_linewidth(
                    0.7
                )

        if show_suptitle:
            fig.suptitle(
                f"{area_name}: {figure_description}",
                fontsize=suptitle_fontsize,
                y=0.965,
            )
        else:
            top_margin = 0.97

        fig.subplots_adjust(
            left=left_margin,
            right=right_margin,
            bottom=bottom_margin,
            top=top_margin,
            wspace=wspace,
        )

        output_pdf = Path(
            output_pdf
        )

        output_pdf.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        fig.savefig(
            output_pdf,
            format="pdf",
            dpi=dpi,
            bbox_inches="tight",
            pad_inches=pdf_pad_inches,
            facecolor="white",
            edgecolor="none",
            transparent=False,
        )

        output_png = Path(output_pdf).with_suffix(".png")
        fig.savefig(
            output_png,
            format="png",
            dpi=max(200, int(dpi)),
            bbox_inches="tight",
            pad_inches=pdf_pad_inches,
            facecolor="white",
            edgecolor="none",
            transparent=False,
        )

        if show:
            plt.show()
        else:
            plt.close(
                fig
            )

    if return_images:
        return prepared_images

    return None


# ============================================================
# PUBLIC FUNCTION: CROPPED RPC IMAGES
# ============================================================

def plot_cropped_images(
    metadata_sh,

    output_folder_name="Figure",
    output_filename=None,

    rpc_height=None,
    use_common_extent=True,

    max_display_size=800,
    percentiles=(2, 98),

    show_grid=True,
    grid_color="white",
    grid_alpha=0.22,
    grid_linewidth=0.5,
    grid_linestyle="--",
    grid_spacing=None,

    figsize_per_image=5.0,
    figure_height=6.3,

    panel_title_fontsize=12,
    axis_label_fontsize=10,
    coordinate_fontsize=8,
    suptitle_fontsize=14,
    show_suptitle=True,

    wspace=0.01,
    left_margin=0.07,
    right_margin=0.995,
    bottom_margin=0.12,
    top_margin=0.88,

    dpi=300,
    pdf_pad_inches=0.03,

    show=True,
    return_images=False,
):
    """
    Plot cropped RPC images using true projected coordinates.

    All image paths, RPC paths, CRS information, and output
    directories are read from the Bash configuration.

    The PDF is saved under:
        ASP_OUTPUT_DIR/output_folder_name/
    """
    config = _read_image_plot_configuration(
        metadata_sh
    )

    for image_path, rpc_path in zip(
        config["cropped_images"],
        config["rpc_files"],
    ):
        if not image_path.is_file():
            raise FileNotFoundError(
                f"Cropped image not found:\n{image_path}"
            )

        if not rpc_path.is_file():
            raise FileNotFoundError(
                f"Cropped RPC file not found:\n{rpc_path}"
            )

    if rpc_height is None:
        rpc_height = _get_representative_rpc_height(
            config["mapproject_dem"]
        )

    prepared_images = [
        _prepare_cropped_rpc_image(
            image_path=image_path,
            rpc_path=rpc_path,
            target_crs=config["target_crs"],
            rpc_height=rpc_height,
            max_display_size=max_display_size,
            percentiles=percentiles,
        )
        for image_path, rpc_path in zip(
            config["cropped_images"],
            config["rpc_files"],
        )
    ]

    output_pdf = _create_figure_output_path(
        config=config,
        output_folder_name=output_folder_name,
        output_filename=output_filename,
        default_suffix="cropped_images",
    )

    return _plot_georeferenced_image_panels(
        prepared_images=prepared_images,
        views=config["views"],
        target_crs=config["target_crs"],
        area_name=config["area_name"],
        figure_description="cropped image alignment",
        output_pdf=output_pdf,

        use_common_extent=use_common_extent,

        show_grid=show_grid,
        grid_color=grid_color,
        grid_alpha=grid_alpha,
        grid_linewidth=grid_linewidth,
        grid_linestyle=grid_linestyle,
        grid_spacing=grid_spacing,

        figsize_per_image=figsize_per_image,
        figure_height=figure_height,

        panel_title_fontsize=panel_title_fontsize,
        axis_label_fontsize=axis_label_fontsize,
        coordinate_fontsize=coordinate_fontsize,
        suptitle_fontsize=suptitle_fontsize,
        show_suptitle=show_suptitle,

        wspace=wspace,
        left_margin=left_margin,
        right_margin=right_margin,
        bottom_margin=bottom_margin,
        top_margin=top_margin,

        dpi=dpi,
        pdf_pad_inches=pdf_pad_inches,

        show=show,
        return_images=return_images,
    )


# ============================================================
# PUBLIC FUNCTION: MAP-PROJECTED IMAGES
# ============================================================

def plot_mapprojected_images(
    metadata_sh,

    output_folder_name="Figure",
    output_filename=None,

    use_common_extent=True,

    max_display_size=1600,
    percentiles=(2, 98),

    show_grid=True,
    grid_color="white",
    grid_alpha=0.22,
    grid_linewidth=0.5,
    grid_linestyle="--",
    grid_spacing=None,

    figsize_per_image=5.0,
    figure_height=6.3,

    panel_title_fontsize=12,
    axis_label_fontsize=10,
    coordinate_fontsize=8,
    suptitle_fontsize=14,
    show_suptitle=True,

    wspace=0.01,
    left_margin=0.07,
    right_margin=0.995,
    bottom_margin=0.12,
    top_margin=0.88,

    dpi=300,
    pdf_pad_inches=0.03,

    show=True,
    return_images=False,
):
    """
    Plot map-projected images with shared map-coordinate axes.

    All paths, CRS information, and output directories are read
    from the Bash configuration.

    The PDF is saved under:
        ASP_OUTPUT_DIR/output_folder_name/
    """
    config = _read_image_plot_configuration(
        metadata_sh
    )

    for image_path in config[
        "mapprojected_images"
    ]:
        if not image_path.is_file():
            raise FileNotFoundError(
                "Map-projected image not found:\n"
                f"{image_path}"
            )

    prepared_images = [
        _prepare_mapprojected_image(
            image_path=image_path,
            target_crs=config["target_crs"],
            max_display_size=max_display_size,
            percentiles=percentiles,
        )
        for image_path in config[
            "mapprojected_images"
        ]
    ]

    output_pdf = _create_figure_output_path(
        config=config,
        output_folder_name=output_folder_name,
        output_filename=output_filename,
        default_suffix="mapprojected_images",
    )

    return _plot_georeferenced_image_panels(
        prepared_images=prepared_images,
        views=config["views"],
        target_crs=config["target_crs"],
        area_name=config["area_name"],
        figure_description="map-projected image alignment",
        output_pdf=output_pdf,

        use_common_extent=use_common_extent,

        show_grid=show_grid,
        grid_color=grid_color,
        grid_alpha=grid_alpha,
        grid_linewidth=grid_linewidth,
        grid_linestyle=grid_linestyle,
        grid_spacing=grid_spacing,

        figsize_per_image=figsize_per_image,
        figure_height=figure_height,

        panel_title_fontsize=panel_title_fontsize,
        axis_label_fontsize=axis_label_fontsize,
        coordinate_fontsize=coordinate_fontsize,
        suptitle_fontsize=suptitle_fontsize,
        show_suptitle=show_suptitle,

        wspace=wspace,
        left_margin=left_margin,
        right_margin=right_margin,
        bottom_margin=bottom_margin,
        top_margin=top_margin,

        dpi=dpi,
        pdf_pad_inches=pdf_pad_inches,

        show=show,
        return_images=return_images,
    )


# ============================================================
# PLOT SELECTED FINAL DSM AND INTERSECTION ERROR
# ============================================================
def _read_final_raster_preview(
    raster_path,
    max_display_size=1800,
):
    """
    Read a reduced-resolution preview of a final DSM or
    intersection-error raster while preserving its map extent,
    CRS, and mask.

    Parameters
    ----------
    raster_path : str or pathlib.Path
        Path to the raster file.

    max_display_size : int
        Maximum number of preview pixels along the largest
        raster dimension.

    Returns
    -------
    dict
        Dictionary containing:

        array
            Reduced-resolution masked raster array.

        extent
            Matplotlib extent:
            [left, right, bottom, top].

        bounds
            Original rasterio bounds object.

        crs
            Raster coordinate reference system.

        transform
            Original affine transform.

        path
            Resolved raster path.
    """
    raster_path = Path(
        raster_path
    ).resolve()

    if not raster_path.is_file():
        raise FileNotFoundError(
            f"Raster file not found:\n{raster_path}"
        )

    if max_display_size <= 0:
        raise ValueError(
            "max_display_size must be greater than zero."
        )

    with rasterio.open(
        raster_path
    ) as source:

        if source.count < 1:
            raise ValueError(
                "The raster contains no bands:\n"
                f"{raster_path}"
            )

        # ----------------------------------------------------
        # Reduced preview dimensions
        # ----------------------------------------------------
        scale = min(
            1.0,
            max_display_size
            / max(
                source.width,
                source.height,
            ),
        )

        display_width = max(
            1,
            int(
                round(
                    source.width * scale
                )
            ),
        )

        display_height = max(
            1,
            int(
                round(
                    source.height * scale
                )
            ),
        )

        # ----------------------------------------------------
        # Read the raster as a masked array
        # ----------------------------------------------------
        raster_array = source.read(
            1,
            out_shape=(
                display_height,
                display_width,
            ),
            resampling=Resampling.bilinear,
            masked=True,
        ).astype(
            np.float32
        )

        # Preserve the rasterio mask and additionally mask
        # non-finite values.
        combined_mask = (
            np.ma.getmaskarray(
                raster_array
            )
            |
            ~np.isfinite(
                np.asarray(
                    raster_array.data
                )
            )
        )

        raster_array = np.ma.array(
            raster_array.data,
            mask=combined_mask,
            dtype=np.float32,
        )

        bounds = source.bounds
        crs = source.crs
        transform = source.transform

    extent = [
        bounds.left,
        bounds.right,
        bounds.bottom,
        bounds.top,
    ]

    return {
        "array": raster_array,
        "extent": extent,
        "bounds": bounds,
        "crs": crs,
        "transform": transform,
        "path": raster_path,
    }
    
def _read_selected_final_dsm_products(metadata_sh):
    """
    Reconstruct the exact final DSM products selected in the
    Bash configuration.

    Selection follows:
        FINAL_STEREO_MODE
        ACTIVE_SINGLE_PAIR_TAGS
        ACTIVE_DUAL_CONFIGURATION_TAGS
        TRI_MERGE_TAG
        FINAL_CKERNELS
        FINAL_SKERNELS
        FINAL_ALGORITHM_TAG
        FINAL_DSM_RESOLUTION
    """
    metadata_sh = Path(
        metadata_sh
    ).resolve()

    if not metadata_sh.is_file():
        raise FileNotFoundError(
            f"Configuration file not found:\n{metadata_sh}"
        )

    command = f'''
        source {_runtime_source_path(metadata_sh)} >/dev/null

        printf "AREA\\t%s\\n" "${{area_name}}"
        printf "ASP_OUTPUT_DIR\\t%s\\n" "${{ASP_OUTPUT_DIR}}"
        printf "FINAL_DSM_OUTPUT_DIR\\t%s\\n" "${{FINAL_DSM_OUTPUT_DIR}}"
        printf "FINAL_STEREO_MODE\\t%s\\n" "${{FINAL_STEREO_MODE}}"
        printf "CREATE_ERROR\\t%s\\n" "${{FINAL_DSM_CREATE_ERROR_IMAGE}}"

        for kernel_index in "${{!FINAL_CKERNELS[@]}}"; do

            ck="${{FINAL_CKERNELS[$kernel_index]}}"
            sk="${{FINAL_SKERNELS[$kernel_index]}}"

            outname="${{area_name}}_${{FINAL_ALGORITHM_TAG}}_ck${{ck}}_sk${{sk}}"

            case "${{FINAL_STEREO_MODE}}" in

                single)

                    for product_tag in \
                        "${{ACTIVE_SINGLE_PAIR_TAGS[@]}}"
                    do
                        dem_name="${{product_tag}}_${{outname}}-${{FINAL_DSM_RESOLUTION}}m"

                        printf "PRODUCT\\t%s\\t%s\\t%s\\n" \
                            "${{dem_name}}" \
                            "${{FINAL_DSM_OUTPUT_DIR}}/${{dem_name}}-DEM.tif" \
                            "${{FINAL_DSM_OUTPUT_DIR}}/${{dem_name}}-IntersectionErr.tif"
                    done
                    ;;

                dual)

                    for product_tag in \
                        "${{ACTIVE_DUAL_CONFIGURATION_TAGS[@]}}"
                    do
                        dem_name="${{product_tag}}_${{outname}}-${{FINAL_DSM_RESOLUTION}}m"

                        printf "PRODUCT\\t%s\\t%s\\t%s\\n" \
                            "${{dem_name}}" \
                            "${{FINAL_DSM_OUTPUT_DIR}}/${{dem_name}}-DEM.tif" \
                            "${{FINAL_DSM_OUTPUT_DIR}}/${{dem_name}}-IntersectionErr.tif"
                    done
                    ;;

                tri)

                    product_tag="${{TRI_MERGE_TAG}}"

                    dem_name="${{product_tag}}_${{outname}}-${{FINAL_DSM_RESOLUTION}}m"

                    printf "PRODUCT\\t%s\\t%s\\t%s\\n" \
                        "${{dem_name}}" \
                        "${{FINAL_DSM_OUTPUT_DIR}}/${{dem_name}}-DEM.tif" \
                        "${{FINAL_DSM_OUTPUT_DIR}}/${{dem_name}}-IntersectionErr.tif"
                    ;;

                *)

                    echo "Unsupported FINAL_STEREO_MODE: ${{FINAL_STEREO_MODE}}" >&2
                    exit 1
                    ;;

            esac

        done
    '''

    result = run_bash(
        command,
        cwd=metadata_sh.parent,
        capture_output=True,
        text=True,
        check=True,
    )

    area_name = None
    asp_output_directory = None
    final_dsm_directory = None
    final_stereo_mode = None
    create_error_image = None

    products = []

    def resolve_path(path_value):
        return _path_from_shell(
            path_value,
            metadata_sh.parent,
        )

    for line in result.stdout.splitlines():

        parts = line.split(
            "\t"
        )

        if not parts:
            continue

        key = parts[0]

        if key == "AREA" and len(parts) >= 2:

            area_name = parts[1]

        elif (
            key == "ASP_OUTPUT_DIR"
            and len(parts) >= 2
        ):

            asp_output_directory = resolve_path(
                parts[1]
            )

        elif (
            key == "FINAL_DSM_OUTPUT_DIR"
            and len(parts) >= 2
        ):

            final_dsm_directory = resolve_path(
                parts[1]
            )

        elif (
            key == "FINAL_STEREO_MODE"
            and len(parts) >= 2
        ):

            final_stereo_mode = parts[1]

        elif (
            key == "CREATE_ERROR"
            and len(parts) >= 2
        ):

            create_error_image = (
                parts[1].lower()
            )

        elif (
            key == "PRODUCT"
            and len(parts) >= 4
        ):

            products.append({
                "name": parts[1],

                "dem_path": resolve_path(
                    parts[2]
                ),

                "error_path": resolve_path(
                    parts[3]
                ),
            })

    if area_name is None:
        raise ValueError(
            "area_name was not read from the Bash configuration."
        )

    if asp_output_directory is None:
        raise ValueError(
            "ASP_OUTPUT_DIR was not read from the Bash "
            "configuration."
        )

    if final_dsm_directory is None:
        raise ValueError(
            "FINAL_DSM_OUTPUT_DIR was not read from the Bash "
            "configuration."
        )

    if not products:
        raise ValueError(
            "No final DSM products are selected in the Bash "
            "configuration."
        )

    if create_error_image != "true":
        raise ValueError(
            "FINAL_DSM_CREATE_ERROR_IMAGE must be true to plot "
            "the intersection-error raster."
        )

    # Validate only products selected in the metadata
    for product in products:

        if not product["dem_path"].is_file():
            raise FileNotFoundError(
                "Selected final DSM not found:\n"
                f"{product['dem_path']}"
            )

        if not product["error_path"].is_file():
            raise FileNotFoundError(
                "Selected intersection-error raster not found:\n"
                f"{product['error_path']}"
            )

    return {
        "area_name": area_name,
        "asp_output_directory": asp_output_directory,
        "final_dsm_directory": final_dsm_directory,
        "final_stereo_mode": final_stereo_mode,
        "products": products,
    }

    
def plot_final_dsm(
    metadata_sh,

    # Output controls
    output_folder_name="Figure",
    output_filename=None,

    # Raster controls
    max_display_size=1800,
    dem_percentiles=(2, 98),

    # DSM appearance
    dem_cmap="terrain",
    hillshade_alpha=0.25,
    hillshade_azimuth=315,
    hillshade_altitude=45,

    # Intersection-error appearance
    error_cmap="Reds",
    error_vmin=0.0,
    error_vmax=1.0,

    # Grid controls
    show_grid=True,
    grid_color="white",
    grid_alpha=0.22,
    grid_linewidth=0.5,
    grid_linestyle="--",
    grid_spacing=None,

    # Figure controls
    figure_width=7.8,
    figure_height_per_product=5.8,
    panel_title_fontsize=12,
    axis_label_fontsize=10,
    coordinate_fontsize=8,
    colorbar_label_fontsize=10,
    suptitle_fontsize=14,
    show_suptitle=False,

    # Layout controls
    wspace=0.10,
    hspace=0.50,
    left_margin=0.09,
    right_margin=0.99,
    bottom_margin=0.10,
    top_margin=0.93,

    # Horizontal color-bar controls
    colorbar_size="4%",
    colorbar_pad=0.45,

    # PDF controls
    dpi=300,
    pdf_pad_inches=0.03,

    # Display and return controls
    show=True,
    return_images=False,
):
    """
    Plot only the final DSM configurations currently selected
    in the Bash metadata, together with their corresponding
    intersection-error rasters.

    Acquisition-geometry labels are converted as follows:

        A -> F -> Forward
        B -> M -> Middle
        C -> B -> Backward

    Examples:

        AB      -> FM
        AC      -> FB
        BC      -> MB
        ABC     -> FMB
        BAC     -> MFB
        ABACBC  -> FMFBMB

    The intersection-error range is fixed between 0 and 1 m.
    Values above 1 m are displayed using the maximum color
    without modifying the original raster.

    Horizontal color bars have exactly the same width as their
    corresponding map panels.

    The PDF is saved automatically under:

        ASP_OUTPUT_DIR/output_folder_name/
    """
    from mpl_toolkits.axes_grid1 import make_axes_locatable

    # --------------------------------------------------------
    # Acquisition-geometry naming
    # --------------------------------------------------------
    abbreviation_mapping = {
        "A": "F",
        "B": "M",
        "C": "B",
    }

    full_name_mapping = {
        "A": "Forward",
        "B": "Middle",
        "C": "Backward",
    }

    def format_acquisition_geometry(asp_view_tag):
        """
        Convert an ASP view tag to its acquisition-geometry
        abbreviation and full description.
        """
        geometry_tag = "".join(
            abbreviation_mapping.get(
                character,
                character,
            )
            for character in asp_view_tag
        )

        geometry_description = "–".join(
            full_name_mapping.get(
                character,
                character,
            )
            for character in asp_view_tag
        )

        return (
            geometry_tag,
            geometry_description,
        )

    # --------------------------------------------------------
    # Read only the configuration selected in the metadata
    # --------------------------------------------------------
    configuration = _read_selected_final_dsm_products(
        metadata_sh
    )

    products = configuration["products"]
    area_name = configuration["area_name"]

    # --------------------------------------------------------
    # Prepare output PDF
    # --------------------------------------------------------
    figure_directory = (
        configuration["asp_output_directory"]
        / output_folder_name
    )

    figure_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    if output_filename is None:
        output_filename = (
            f"{_safe_filename(area_name)}"
            "_final_DSM_and_intersection_error.pdf"
        )

    else:
        output_filename = Path(
            output_filename
        ).name

        if Path(output_filename).suffix.lower() != ".pdf":
            output_filename = (
                f"{output_filename}.pdf"
            )

    output_pdf = (
        figure_directory
        / output_filename
    )

    # --------------------------------------------------------
    # Read selected final products
    # --------------------------------------------------------
    plotted_products = []

    for product in products:

        dem_data = _read_final_raster_preview(
            product["dem_path"],
            max_display_size=max_display_size,
        )

        error_data = _read_final_raster_preview(
            product["error_path"],
            max_display_size=max_display_size,
        )

        # ----------------------------------------------------
        # Validate CRS
        # ----------------------------------------------------
        if dem_data["crs"] is None:
            raise ValueError(
                "No CRS is defined for:\n"
                f"{product['dem_path']}"
            )

        if error_data["crs"] is None:
            raise ValueError(
                "No CRS is defined for:\n"
                f"{product['error_path']}"
            )

        if dem_data["crs"] != error_data["crs"]:
            raise ValueError(
                "The DSM and intersection-error raster do not "
                "have the same CRS.\n\n"
                f"DSM CRS: {dem_data['crs']}\n"
                f"Error CRS: {error_data['crs']}"
            )

        # ----------------------------------------------------
        # Common spatial extent
        # ----------------------------------------------------
        common_left = max(
            dem_data["bounds"].left,
            error_data["bounds"].left,
        )

        common_right = min(
            dem_data["bounds"].right,
            error_data["bounds"].right,
        )

        common_bottom = max(
            dem_data["bounds"].bottom,
            error_data["bounds"].bottom,
        )

        common_top = min(
            dem_data["bounds"].top,
            error_data["bounds"].top,
        )

        if (
            common_left >= common_right
            or common_bottom >= common_top
        ):
            raise ValueError(
                "The selected DSM and intersection-error "
                "raster do not have a common spatial extent.\n\n"
                f"DSM:\n{product['dem_path']}\n\n"
                f"Intersection error:\n{product['error_path']}"
            )

        # ----------------------------------------------------
        # DSM display range
        # ----------------------------------------------------
        valid_dem = dem_data[
            "array"
        ].compressed()

        if valid_dem.size == 0:
            raise ValueError(
                "The final DSM contains no valid values:\n"
                f"{product['dem_path']}"
            )

        dem_vmin, dem_vmax = np.percentile(
            valid_dem,
            dem_percentiles,
        )

        # ----------------------------------------------------
        # DSM hillshade
        # ----------------------------------------------------
        dem_fill_value = float(
            np.median(valid_dem)
        )

        dem_filled = dem_data[
            "array"
        ].filled(
            dem_fill_value
        )

        light_source = LightSource(
            azdeg=hillshade_azimuth,
            altdeg=hillshade_altitude,
        )

        hillshade = light_source.hillshade(
            dem_filled,
            vert_exag=1.0,
        )

        hillshade = np.ma.array(
            hillshade,
            mask=np.ma.getmaskarray(
                dem_data["array"]
            ),
        )

        # ----------------------------------------------------
        # Intersection error
        #
        # Display only:
        #   0 m -> white
        #   1 m or greater -> dark red
        # ----------------------------------------------------
        error_array = np.ma.abs(
            error_data["array"]
        )

        error_display = np.ma.clip(
            error_array,
            error_vmin,
            error_vmax,
        )

        # ----------------------------------------------------
        # Extract and convert the selected geometry tag
        #
        # Example:
        # BAC_Berared_Aug24_MGM_ck9_sk21-1.0m
        #
        # ASP tag      = BAC
        # Geometry tag = MFB
        # ----------------------------------------------------
        asp_view_tag = product[
            "name"
        ].split(
            "_",
            1,
        )[0]

        (
            geometry_tag,
            geometry_description,
        ) = format_acquisition_geometry(
            asp_view_tag
        )

        plotted_products.append({
            "name": product["name"],

            "asp_view_tag": asp_view_tag,
            "geometry_tag": geometry_tag,
            "geometry_description": geometry_description,

            "dem": dem_data,
            "hillshade": hillshade,
            "dem_vmin": float(dem_vmin),
            "dem_vmax": float(dem_vmax),

            "error": error_data,
            "error_display": error_display,

            "extent": [
                common_left,
                common_right,
                common_bottom,
                common_top,
            ],
        })

    # --------------------------------------------------------
    # Validate CRS across selected configurations
    # --------------------------------------------------------
    figure_crs = plotted_products[0][
        "dem"
    ]["crs"]

    for product in plotted_products[1:]:

        if product["dem"]["crs"] != figure_crs:
            raise ValueError(
                "The selected final DSM products do not all "
                "use the same CRS."
            )

    if figure_crs.is_geographic:
        x_label = "Longitude"
        y_label = "Latitude"

    else:
        x_label = "Easting (m)"
        y_label = "Northing (m)"

    number_of_products = len(
        plotted_products
    )

    # --------------------------------------------------------
    # PDF settings for Inkscape
    # --------------------------------------------------------
    pdf_settings = {
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "savefig.dpi": dpi,
    }

    with mpl.rc_context(
        pdf_settings
    ):

        fig, axes = plt.subplots(
            number_of_products,
            2,
            figsize=(
                figure_width,
                figure_height_per_product
                * number_of_products,
            ),
            sharex="row",
            sharey="row",
            squeeze=False,
            gridspec_kw={
                "wspace": wspace,
                "hspace": hspace,
                "width_ratios": [1, 1],
            },
        )

        for row_index, product in enumerate(
            plotted_products
        ):

            dem_ax = axes[
                row_index,
                0,
            ]

            error_ax = axes[
                row_index,
                1,
            ]

            # Pull both panels toward the centre.
            dem_ax.set_anchor(
                "E"
            )

            error_ax.set_anchor(
                "W"
            )

            extent = product[
                "extent"
            ]

            # ------------------------------------------------
            # Publication panel letters
            # ------------------------------------------------
            dem_panel_index = (
                row_index * 2
            )

            error_panel_index = (
                dem_panel_index + 1
            )

            dem_panel_letter = chr(
                ord("a") + dem_panel_index
            )

            error_panel_letter = chr(
                ord("a") + error_panel_index
            )

            dem_title = (
                f"({dem_panel_letter}) "
                f"{product['geometry_tag']} — Final DSM"
            )

            error_title = (
                f"({error_panel_letter}) "
                f"{product['geometry_tag']} — "
                "Intersection error"
            )

            # ------------------------------------------------
            # Final DSM
            # ------------------------------------------------
            dem_plot = dem_ax.imshow(
                product["dem"]["array"],
                extent=product["dem"]["extent"],
                origin="upper",
                cmap=dem_cmap,
                vmin=product["dem_vmin"],
                vmax=product["dem_vmax"],
                interpolation="nearest",
                rasterized=True,
                zorder=1,
            )

            dem_ax.imshow(
                product["hillshade"],
                extent=product["dem"]["extent"],
                origin="upper",
                cmap="gray",
                alpha=hillshade_alpha,
                interpolation="nearest",
                rasterized=True,
                zorder=2,
            )

            dem_ax.set_title(
                dem_title,
                fontsize=panel_title_fontsize,
                pad=8,
            )

            # ------------------------------------------------
            # DSM horizontal color bar
            #
            # Its width is exactly equal to the DSM axis width.
            # ------------------------------------------------
            dem_divider = make_axes_locatable(
                dem_ax
            )

            dem_colorbar_ax = dem_divider.append_axes(
                "bottom",
                size=colorbar_size,
                pad=colorbar_pad,
            )

            dem_colorbar = fig.colorbar(
                dem_plot,
                cax=dem_colorbar_ax,
                orientation="horizontal",
            )

            dem_colorbar.set_label(
                "Elevation (m)",
                fontsize=colorbar_label_fontsize,
                labelpad=5,
            )

            dem_colorbar.ax.tick_params(
                labelsize=coordinate_fontsize,
                direction="out",
                length=3,
            )

            # ------------------------------------------------
            # Intersection-error map
            # ------------------------------------------------
            error_plot = error_ax.imshow(
                product["error_display"],
                extent=product["error"]["extent"],
                origin="upper",
                cmap=error_cmap,
                vmin=error_vmin,
                vmax=error_vmax,
                interpolation="nearest",
                rasterized=True,
                zorder=1,
            )

            error_ax.set_title(
                error_title,
                fontsize=panel_title_fontsize,
                pad=8,
            )

            # ------------------------------------------------
            # Error horizontal color bar
            #
            # Its width is exactly equal to the error-axis width.
            # ------------------------------------------------
            error_divider = make_axes_locatable(
                error_ax
            )

            error_colorbar_ax = error_divider.append_axes(
                "bottom",
                size=colorbar_size,
                pad=colorbar_pad,
            )

            error_colorbar = fig.colorbar(
                error_plot,
                cax=error_colorbar_ax,
                orientation="horizontal",
                ticks=np.linspace(
                    error_vmin,
                    error_vmax,
                    6,
                ),
            )

            error_colorbar.set_label(
                "Intersection error (m)",
                fontsize=colorbar_label_fontsize,
                labelpad=5,
            )

            error_colorbar.ax.tick_params(
                labelsize=coordinate_fontsize,
                direction="out",
                length=3,
            )

            # ------------------------------------------------
            # Shared map-coordinate settings
            # ------------------------------------------------
            for column_index, ax in enumerate(
                (
                    dem_ax,
                    error_ax,
                )
            ):

                ax.set_xlim(
                    extent[0],
                    extent[1],
                )

                ax.set_ylim(
                    extent[2],
                    extent[3],
                )

                ax.set_aspect(
                    "equal",
                    adjustable="box",
                )

                if column_index == 0:
                    ax.set_anchor(
                        "E"
                    )

                else:
                    ax.set_anchor(
                        "W"
                    )

                ax.set_xlabel(
                    x_label,
                    fontsize=axis_label_fontsize,
                )

                if column_index == 0:
                    ax.set_ylabel(
                        y_label,
                        fontsize=axis_label_fontsize,
                    )

                else:
                    ax.tick_params(
                        axis="y",
                        labelleft=False,
                        left=False,
                    )

                ax.ticklabel_format(
                    style="plain",
                    axis="both",
                    useOffset=False,
                )

                ax.tick_params(
                    axis="both",
                    labelsize=coordinate_fontsize,
                    direction="out",
                    length=3.5,
                    width=0.7,
                )

                if grid_spacing is not None:

                    ax.xaxis.set_major_locator(
                        MultipleLocator(
                            grid_spacing
                        )
                    )

                    if column_index == 0:

                        ax.yaxis.set_major_locator(
                            MultipleLocator(
                                grid_spacing
                            )
                        )

                if show_grid:

                    ax.grid(
                        True,
                        which="major",
                        color=grid_color,
                        alpha=grid_alpha,
                        linewidth=grid_linewidth,
                        linestyle=grid_linestyle,
                        zorder=3,
                    )

                else:

                    ax.grid(
                        False
                    )

                for spine in ax.spines.values():
                    spine.set_linewidth(
                        0.7
                    )

        # ----------------------------------------------------
        # Optional main title
        # ----------------------------------------------------
        if show_suptitle:

            if number_of_products == 1:

                main_title = (
                    f"{area_name} — "
                    f"{plotted_products[0]['geometry_tag']} "
                    f"({plotted_products[0]['geometry_description']})"
                )

            else:

                main_title = (
                    f"{area_name}: selected final DSM products"
                )

            fig.suptitle(
                main_title,
                fontsize=suptitle_fontsize,
                y=0.97,
            )

        else:

            top_margin = 0.96

        # ----------------------------------------------------
        # Final figure spacing
        # ----------------------------------------------------
        fig.subplots_adjust(
            left=left_margin,
            right=right_margin,
            bottom=bottom_margin,
            top=top_margin,
            wspace=wspace,
            hspace=hspace,
        )

        # ----------------------------------------------------
        # Save Matplotlib PDF
        # ----------------------------------------------------
        fig.savefig(
            output_pdf,
            format="pdf",
            dpi=dpi,
            bbox_inches="tight",
            pad_inches=pdf_pad_inches,
            facecolor="white",
            edgecolor="none",
            transparent=False,
        )

        output_png = Path(output_pdf).with_suffix(".png")
        fig.savefig(
            output_png,
            format="png",
            dpi=max(200, int(dpi)),
            bbox_inches="tight",
            pad_inches=pdf_pad_inches,
            facecolor="white",
            edgecolor="none",
            transparent=False,
        )

        print(
            "\nSelected configuration:"
        )

        for product in plotted_products:

            print(
                f"  {product['name']}"
            )

            print(
                "    Acquisition geometry: "
                f"{product['geometry_tag']} "
                f"({product['geometry_description']})"
            )

        print(
            "\nPDF figure saved to:"
        )

        print(
            output_pdf
        )

        if show:

            plt.show()

        else:

            plt.close(
                fig
            )

    if return_images:

        return (
            output_pdf,
            plotted_products,
        )

    return output_pdf

# ============================================================
# PUBLICATION FIGURE:
# FINAL DSM + INTERSECTION ERROR + PLEIADES ACCURACY
# ============================================================

def _read_coreg_accuracy_table(file_path):
    """
    Read a co-registration pixel-residual table from CSV or
    Excel.

    Supported formats
    -----------------
    .csv
    .xlsx
    .xls
    """
    file_path = Path(
        file_path
    ).resolve()

    if not file_path.is_file():
        raise FileNotFoundError(
            f"Accuracy file not found:\n{file_path}"
        )

    suffix = file_path.suffix.lower()

    if suffix == ".csv":
        dataframe = pd.read_csv(
            file_path
        )

    elif suffix in {
        ".xlsx",
        ".xls",
    }:
        dataframe = pd.read_excel(
            file_path
        )

    else:
        raise ValueError(
            "Unsupported accuracy-file format.\n"
            "Use a CSV or Excel file:\n"
            "  .csv, .xlsx, or .xls"
        )

    return dataframe


def load_pleiades_coreg_accuracy(
    accuracy_file,
    dem_name="Pleiades_2024",
    before_stage="Before",
    after_stage=None,
    dem_column="DEM_name",
    stage_column="Stage",
    residual_column="dh",
):
    """
    Read before/after stable-area residuals for one DEM product.

    The function automatically detects the final available
    co-registration stage when after_stage is None.

    Preferred final-stage order
    ---------------------------
    1. After_Final
    2. After
    3. After_Nuth_Deramp
    4. After_Nuth

    Returns
    -------
    dict
        Before/after residual arrays, selected stage names,
        and calculated accuracy statistics.
    """
    dataframe = _read_coreg_accuracy_table(
        accuracy_file
    )

    required_columns = {
        dem_column,
        stage_column,
        residual_column,
    }

    missing_columns = (
        required_columns
        - set(dataframe.columns)
    )

    if missing_columns:
        raise ValueError(
            "The accuracy file is missing required columns:\n"
            f"{sorted(missing_columns)}\n\n"
            f"Available columns:\n{list(dataframe.columns)}"
        )

    filtered = dataframe.loc[
        dataframe[dem_column].astype(str)
        == str(dem_name)
    ].copy()

    if filtered.empty:
        available_products = sorted(
            dataframe[dem_column]
            .dropna()
            .astype(str)
            .unique()
        )

        raise ValueError(
            f"No rows were found for DEM product:\n{dem_name}\n\n"
            f"Available products:\n{available_products}"
        )

    # --------------------------------------------------------
    # Resolve stage names without case sensitivity
    # --------------------------------------------------------
    available_stages = [
        str(stage)
        for stage in (
            filtered[stage_column]
            .dropna()
            .unique()
        )
    ]

    stage_lookup = {
        stage.lower(): stage
        for stage in available_stages
    }

    before_key = str(
        before_stage
    ).lower()

    if before_key not in stage_lookup:
        raise ValueError(
            f"Before stage '{before_stage}' was not found.\n\n"
            f"Available stages:\n{available_stages}"
        )

    selected_before_stage = stage_lookup[
        before_key
    ]

    if after_stage is None:

        preferred_after_stages = [
            "After_Final",
            "After",
            "After_Nuth_Deramp",
            "After_Nuth",
        ]

        selected_after_stage = None

        for candidate in preferred_after_stages:

            candidate_key = candidate.lower()

            if candidate_key in stage_lookup:
                selected_after_stage = (
                    stage_lookup[candidate_key]
                )

                break

        if selected_after_stage is None:
            raise ValueError(
                "No recognized final co-registration stage "
                "was found.\n\n"
                f"Available stages:\n{available_stages}"
            )

    else:

        after_key = str(
            after_stage
        ).lower()

        if after_key not in stage_lookup:
            raise ValueError(
                f"After stage '{after_stage}' was not found.\n\n"
                f"Available stages:\n{available_stages}"
            )

        selected_after_stage = stage_lookup[
            after_key
        ]

    # --------------------------------------------------------
    # Extract finite residual values
    # --------------------------------------------------------
    before_values = pd.to_numeric(
        filtered.loc[
            filtered[stage_column].astype(str)
            == selected_before_stage,
            residual_column,
        ],
        errors="coerce",
    ).to_numpy(
        dtype=float
    )

    after_values = pd.to_numeric(
        filtered.loc[
            filtered[stage_column].astype(str)
            == selected_after_stage,
            residual_column,
        ],
        errors="coerce",
    ).to_numpy(
        dtype=float
    )

    before_values = before_values[
        np.isfinite(before_values)
    ]

    after_values = after_values[
        np.isfinite(after_values)
    ]

    if before_values.size == 0:
        raise ValueError(
            "No finite residual values were found for:\n"
            f"{dem_name} — {selected_before_stage}"
        )

    if after_values.size == 0:
        raise ValueError(
            "No finite residual values were found for:\n"
            f"{dem_name} — {selected_after_stage}"
        )

    # --------------------------------------------------------
    # Calculate robust accuracy statistics
    # --------------------------------------------------------
    def calculate_statistics(values):

        median = float(
            np.median(values)
        )

        nmad = float(
            1.4826
            * np.median(
                np.abs(
                    values - median
                )
            )
        )

        return {
            "count": int(
                values.size
            ),
            "mean": float(
                np.mean(values)
            ),
            "median": median,
            "nmad": nmad,
            "rmse": float(
                np.sqrt(
                    np.mean(
                        values ** 2
                    )
                )
            ),
            "mae": float(
                np.mean(
                    np.abs(values)
                )
            ),
        }

    before_statistics = calculate_statistics(
        before_values
    )

    after_statistics = calculate_statistics(
        after_values
    )

    return {
        "file_path": Path(
            accuracy_file
        ).resolve(),
        "dem_name": dem_name,

        "before_stage": selected_before_stage,
        "after_stage": selected_after_stage,

        "before_values": before_values,
        "after_values": after_values,

        "before_statistics": before_statistics,
        "after_statistics": after_statistics,
    }

# ============================================================
# PUBLICATION FIGURE:
# FINAL DSM + INTERSECTION ERROR + VERTICAL RESIDUALS
# ============================================================

def plot_final_dsm_accuracy_figure(
    metadata_sh,
    accuracy_file,

    # Accuracy-data controls
    dem_name="Pleiades_2024",
    before_stage="Before",
    after_stage=None,

    # Output controls
    output_folder_name="Figure",
    output_filename=None,

    # Raster controls
    max_display_size=1800,
    dem_percentiles=(2, 98),

    # DSM appearance
    dem_cmap="terrain",
    hillshade_alpha=0.25,
    hillshade_azimuth=315,
    hillshade_altitude=45,

    # Intersection-error appearance
    error_cmap="Reds",
    error_vmin=0.0,
    error_vmax=1.0,

    # Accuracy-boxplot appearance
    before_fill="#D98C8C",
    after_fill="#8FB9D9",
    before_edge="#A64B4B",
    after_edge="#3E7BAA",
    mean_color="#222222",

    boxplot_width=0.55,
    boxplot_whiskers=(10, 90),
    show_boxplot_outliers=False,

    # Accuracy-axis controls
    residual_ylim=None,
    accuracy_grid_spacing=0.5,
    show_accuracy_grid=True,
    accuracy_grid_color="#707070",
    accuracy_grid_alpha=0.65,
    accuracy_grid_linewidth=0.9,
    accuracy_grid_linestyle=":",

    # Map-grid controls
    show_grid=True,
    grid_color="white",
    grid_alpha=0.22,
    grid_linewidth=0.5,
    grid_linestyle="--",
    grid_spacing=None,

    # Figure controls
    figure_width=11.5,
    figure_height=5.8,
    width_ratios=(1.0, 1.0, 0.72),

    panel_title_fontsize=12,
    axis_label_fontsize=10,
    coordinate_fontsize=8,
    annotation_fontsize=8,
    colorbar_label_fontsize=10,

    # Layout controls
    wspace=0.10,
    colorbar_hspace=0.2,

    left_margin=0.065,
    right_margin=0.985,
    bottom_margin=0.10,
    top_margin=0.94,

    # PDF controls
    dpi=300,
    pdf_pad_inches=0.03,

    # Display and return controls
    show=True,
    return_data=False,
):
    """
    Create a publication figure containing:

        (a) selected final DSM;
        (b) corresponding intersection-error map;
        (c) Pléiades vertical residuals before and after
            co-registration.

    Panel (c) includes horizontal grid lines at a fixed interval,
    controlled by accuracy_grid_spacing.

    The existing plot_final_dsm() function is not modified.

    The output PDF is saved automatically under:

        ASP_OUTPUT_DIR/output_folder_name/
    """
    # --------------------------------------------------------
    # Read the selected final DSM configuration
    # --------------------------------------------------------
    configuration = _read_selected_final_dsm_products(
        metadata_sh
    )

    products = configuration["products"]

    if len(products) != 1:
        selected_names = [
            product["name"]
            for product in products
        ]

        raise ValueError(
            "This publication figure requires exactly one "
            "selected final DSM configuration.\n\n"
            f"Currently selected products:\n{selected_names}\n\n"
            "Select one pair/configuration and one kernel "
            "combination in the metadata."
        )

    product = products[0]
    area_name = configuration["area_name"]

    # --------------------------------------------------------
    # Read Pléiades vertical residuals
    # --------------------------------------------------------
    accuracy = load_pleiades_coreg_accuracy(
        accuracy_file=accuracy_file,
        dem_name=dem_name,
        before_stage=before_stage,
        after_stage=after_stage,
    )

    before_values = accuracy["before_values"]
    after_values = accuracy["after_values"]

    before_statistics = accuracy["before_statistics"]
    after_statistics = accuracy["after_statistics"]

    # --------------------------------------------------------
    # Read final DSM and intersection-error rasters
    # --------------------------------------------------------
    dem_data = _read_final_raster_preview(
        product["dem_path"],
        max_display_size=max_display_size,
    )

    error_data = _read_final_raster_preview(
        product["error_path"],
        max_display_size=max_display_size,
    )

    if dem_data["crs"] is None:
        raise ValueError(
            "No CRS is defined for:\n"
            f"{product['dem_path']}"
        )

    if error_data["crs"] is None:
        raise ValueError(
            "No CRS is defined for:\n"
            f"{product['error_path']}"
        )

    if dem_data["crs"] != error_data["crs"]:
        raise ValueError(
            "The DSM and intersection-error raster do not "
            "have the same CRS.\n\n"
            f"DSM CRS: {dem_data['crs']}\n"
            f"Error CRS: {error_data['crs']}"
        )

    # --------------------------------------------------------
    # Common DSM/error-map extent
    # --------------------------------------------------------
    common_left = max(
        dem_data["bounds"].left,
        error_data["bounds"].left,
    )

    common_right = min(
        dem_data["bounds"].right,
        error_data["bounds"].right,
    )

    common_bottom = max(
        dem_data["bounds"].bottom,
        error_data["bounds"].bottom,
    )

    common_top = min(
        dem_data["bounds"].top,
        error_data["bounds"].top,
    )

    if (
        common_left >= common_right
        or common_bottom >= common_top
    ):
        raise ValueError(
            "The DSM and intersection-error raster do not "
            "have a common spatial extent."
        )

    common_extent = [
        common_left,
        common_right,
        common_bottom,
        common_top,
    ]

    # --------------------------------------------------------
    # DSM display range
    # --------------------------------------------------------
    valid_dem = dem_data["array"].compressed()

    if valid_dem.size == 0:
        raise ValueError(
            "The final DSM contains no valid elevation values."
        )

    dem_vmin, dem_vmax = np.percentile(
        valid_dem,
        dem_percentiles,
    )

    # --------------------------------------------------------
    # DSM hillshade
    # --------------------------------------------------------
    dem_fill_value = float(
        np.median(valid_dem)
    )

    dem_filled = dem_data["array"].filled(
        dem_fill_value
    )

    light_source = LightSource(
        azdeg=hillshade_azimuth,
        altdeg=hillshade_altitude,
    )

    hillshade = light_source.hillshade(
        dem_filled,
        vert_exag=1.0,
    )

    hillshade = np.ma.array(
        hillshade,
        mask=np.ma.getmaskarray(
            dem_data["array"]
        ),
    )

    # --------------------------------------------------------
    # Intersection-error display
    # --------------------------------------------------------
    error_array = np.ma.abs(
        error_data["array"]
    )

    error_display = np.ma.clip(
        error_array,
        error_vmin,
        error_vmax,
    )

    # --------------------------------------------------------
    # Convert ASP image labels to acquisition geometry
    #
    # A -> F = Forward
    # B -> M = Middle
    # C -> B = Backward
    # --------------------------------------------------------
    geometry_mapping = {
        "A": "F",
        "B": "M",
        "C": "B",
    }

    asp_view_tag = product["name"].split(
        "_",
        1,
    )[0]

    geometry_tag = "".join(
        geometry_mapping.get(
            character,
            character,
        )
        for character in asp_view_tag
    )

    # --------------------------------------------------------
    # Output PDF path
    # --------------------------------------------------------
    figure_directory = (
        configuration["asp_output_directory"]
        / output_folder_name
    )

    figure_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    if output_filename is None:
        output_filename = (
            f"{_safe_filename(area_name)}"
            f"_{geometry_tag}"
            "_DSM_intersection_error_"
            "Pleiades_vertical_residuals.pdf"
        )

    else:
        output_filename = Path(
            output_filename
        ).name

        if Path(output_filename).suffix.lower() != ".pdf":
            output_filename = (
                f"{output_filename}.pdf"
            )

    output_pdf = (
        figure_directory
        / output_filename
    )

    # --------------------------------------------------------
    # Coordinate labels
    # --------------------------------------------------------
    if dem_data["crs"].is_geographic:
        x_label = "Longitude"
        y_label = "Latitude"

    else:
        x_label = "Easting (m)"
        y_label = "Northing (m)"

    # --------------------------------------------------------
    # Determine the residual-axis limits
    # --------------------------------------------------------
    if residual_ylim is None:

        combined_values = np.concatenate(
            [
                before_values,
                after_values,
            ]
        )

        lower_value, upper_value = np.percentile(
            combined_values,
            [2, 98],
        )

        residual_range = (
            upper_value - lower_value
        )

        if residual_range <= 0:
            residual_range = 1.0

        padding = max(
            0.25,
            0.08 * residual_range,
        )

        lower_limit = min(
            lower_value - padding,
            0.0,
        )

        upper_limit = max(
            upper_value + padding,
            0.0,
        )

        # Snap limits to the requested grid interval
        lower_limit = (
            np.floor(
                lower_limit
                / accuracy_grid_spacing
            )
            * accuracy_grid_spacing
        )

        upper_limit = (
            np.ceil(
                upper_limit
                / accuracy_grid_spacing
            )
            * accuracy_grid_spacing
        )

        residual_ylim = (
            lower_limit,
            upper_limit,
        )

    # --------------------------------------------------------
    # Matplotlib PDF settings for Inkscape
    # --------------------------------------------------------
    pdf_settings = {
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "savefig.dpi": dpi,
    }

    with mpl.rc_context(
        pdf_settings
    ):
        fig = plt.figure(
            figsize=(
                figure_width,
                figure_height,
            )
        )

        # ----------------------------------------------------
        # Row 1: three panels
        # Row 2: color bars for panels a and b
        # ----------------------------------------------------
        grid_spec = fig.add_gridspec(
            nrows=2,
            ncols=3,

            height_ratios=[
                1.0,
                0.055,
            ],

            width_ratios=list(
                width_ratios
            ),

            wspace=wspace,
            hspace=colorbar_hspace,
        )

        dem_ax = fig.add_subplot(
            grid_spec[0, 0]
        )

        error_ax = fig.add_subplot(
            grid_spec[0, 1],
            sharex=dem_ax,
            sharey=dem_ax,
        )

        accuracy_ax = fig.add_subplot(
            grid_spec[0, 2]
        )

        dem_colorbar_ax = fig.add_subplot(
            grid_spec[1, 0]
        )

        error_colorbar_ax = fig.add_subplot(
            grid_spec[1, 1]
        )

        empty_ax = fig.add_subplot(
            grid_spec[1, 2]
        )

        empty_ax.axis(
            "off"
        )

        # Pull the two map panels toward the centre
        dem_ax.set_anchor(
            "E"
        )

        error_ax.set_anchor(
            "W"
        )

        # ====================================================
        # (a) FINAL DSM
        # ====================================================
        dem_plot = dem_ax.imshow(
            dem_data["array"],
            extent=dem_data["extent"],
            origin="upper",
            cmap=dem_cmap,
            vmin=dem_vmin,
            vmax=dem_vmax,
            interpolation="nearest",
            rasterized=True,
            zorder=1,
        )

        dem_ax.imshow(
            hillshade,
            extent=dem_data["extent"],
            origin="upper",
            cmap="gray",
            alpha=hillshade_alpha,
            interpolation="nearest",
            rasterized=True,
            zorder=2,
        )

        dem_ax.set_title(
            f"(a) {geometry_tag} — Final DSM",
            fontsize=panel_title_fontsize,
            pad=8,
        )

        dem_colorbar = fig.colorbar(
            dem_plot,
            cax=dem_colorbar_ax,
            orientation="horizontal",
        )

        dem_colorbar.set_label(
            "Elevation (m)",
            fontsize=colorbar_label_fontsize,
            labelpad=5,
        )

        dem_colorbar.ax.tick_params(
            labelsize=coordinate_fontsize,
            direction="out",
            length=3,
        )

        # ====================================================
        # (b) INTERSECTION ERROR
        # ====================================================
        error_plot = error_ax.imshow(
            error_display,
            extent=error_data["extent"],
            origin="upper",
            cmap=error_cmap,
            vmin=error_vmin,
            vmax=error_vmax,
            interpolation="nearest",
            rasterized=True,
            zorder=1,
        )

        error_ax.set_title(
            (
                f"(b) {geometry_tag} — "
                "Intersection error"
            ),
            fontsize=panel_title_fontsize,
            pad=8,
        )

        error_colorbar = fig.colorbar(
            error_plot,
            cax=error_colorbar_ax,
            orientation="horizontal",
            ticks=np.linspace(
                error_vmin,
                error_vmax,
                6,
            ),
        )

        error_colorbar.set_label(
            "Intersection error (m)",
            fontsize=colorbar_label_fontsize,
            labelpad=5,
        )

        error_colorbar.ax.tick_params(
            labelsize=coordinate_fontsize,
            direction="out",
            length=3,
        )

        # ====================================================
        # SHARED MAP COORDINATES
        # ====================================================
        for column_index, axis in enumerate(
            (
                dem_ax,
                error_ax,
            )
        ):
            axis.set_xlim(
                common_extent[0],
                common_extent[1],
            )

            axis.set_ylim(
                common_extent[2],
                common_extent[3],
            )

            axis.set_aspect(
                "equal",
                adjustable="box",
            )

            if column_index == 0:
                axis.set_anchor(
                    "E"
                )

            else:
                axis.set_anchor(
                    "W"
                )

            axis.set_xlabel(
                x_label,
                fontsize=axis_label_fontsize,
            )

            if column_index == 0:
                axis.set_ylabel(
                    y_label,
                    fontsize=axis_label_fontsize,
                )

            else:
                axis.tick_params(
                    axis="y",
                    labelleft=False,
                    left=False,
                )

            axis.ticklabel_format(
                style="plain",
                axis="both",
                useOffset=False,
            )

            axis.tick_params(
                axis="both",
                labelsize=coordinate_fontsize,
                direction="out",
                length=3.5,
                width=0.7,
            )

            if grid_spacing is not None:

                axis.xaxis.set_major_locator(
                    MultipleLocator(
                        grid_spacing
                    )
                )

                if column_index == 0:
                    axis.yaxis.set_major_locator(
                        MultipleLocator(
                            grid_spacing
                        )
                    )

            if show_grid:
                axis.grid(
                    True,
                    which="major",
                    color=grid_color,
                    alpha=grid_alpha,
                    linewidth=grid_linewidth,
                    linestyle=grid_linestyle,
                    zorder=3,
                )

            else:
                axis.grid(
                    False
                )

            for spine in axis.spines.values():
                spine.set_linewidth(
                    0.7
                )

        # ====================================================
        # (c) VERTICAL RESIDUALS
        # ====================================================
        before_boxplot = accuracy_ax.boxplot(
            [before_values],
            positions=[1],
            widths=boxplot_width,
            patch_artist=True,
            showfliers=show_boxplot_outliers,
            whis=boxplot_whiskers,

            medianprops={
                "color": before_edge,
                "linewidth": 1.8,
            },

            whiskerprops={
                "color": "#666666",
                "linewidth": 1.0,
            },

            capprops={
                "color": "#666666",
                "linewidth": 1.0,
            },

            boxprops={
                "edgecolor": before_edge,
                "linewidth": 1.2,
            },
        )

        after_boxplot = accuracy_ax.boxplot(
            [after_values],
            positions=[2],
            widths=boxplot_width,
            patch_artist=True,
            showfliers=show_boxplot_outliers,
            whis=boxplot_whiskers,

            medianprops={
                "color": after_edge,
                "linewidth": 1.8,
            },

            whiskerprops={
                "color": "#666666",
                "linewidth": 1.0,
            },

            capprops={
                "color": "#666666",
                "linewidth": 1.0,
            },

            boxprops={
                "edgecolor": after_edge,
                "linewidth": 1.2,
            },
        )

        before_boxplot[
            "boxes"
        ][0].set_facecolor(
            before_fill
        )

        before_boxplot[
            "boxes"
        ][0].set_alpha(
            0.65
        )

        after_boxplot[
            "boxes"
        ][0].set_facecolor(
            after_fill
        )

        after_boxplot[
            "boxes"
        ][0].set_alpha(
            0.65
        )

        # Mean markers
        accuracy_ax.scatter(
            [1],
            [before_statistics["mean"]],
            s=28,
            color=mean_color,
            zorder=5,
        )

        accuracy_ax.scatter(
            [2],
            [after_statistics["mean"]],
            s=28,
            color=mean_color,
            zorder=5,
        )

        # Zero-reference line
        accuracy_ax.axhline(
            0,
            color="#555555",
            linestyle="--",
            linewidth=1.0,
            alpha=0.9,
            zorder=2,
        )

        # ----------------------------------------------------
        # Accuracy values beside the corresponding boxes
        # ----------------------------------------------------
        accuracy_ax.text(
            1.32,
            before_statistics["median"],
            (
                f"RMSE = {before_statistics['rmse']:.2f} m\n"
                f"NMAD = {before_statistics['nmad']:.2f} m"
            ),
            ha="left",
            va="center",
            fontsize=annotation_fontsize,
        )

        accuracy_ax.text(
            1.68,
            after_statistics["median"],
            (
                f"RMSE = {after_statistics['rmse']:.2f} m\n"
                f"NMAD = {after_statistics['nmad']:.2f} m"
            ),
            ha="right",
            va="center",
            fontsize=annotation_fontsize,
        )

        accuracy_ax.set_title(
            "(c) Vertical residuals",
            fontsize=panel_title_fontsize,
            pad=8,
        )

        accuracy_ax.set_xticks(
            [1, 2]
        )

        accuracy_ax.set_xticklabels(
            [
                "Before\nCo-registration",
                "After\nCo-registration",
            ],
            fontsize=coordinate_fontsize,
        )

        accuracy_ax.set_xlim(
            0.45,
            2.55,
        )

        accuracy_ax.set_ylim(
            residual_ylim
        )

        # Show ticks and label on the right, as in your example
        accuracy_ax.yaxis.tick_right()
        accuracy_ax.yaxis.set_label_position(
            "right"
        )

        accuracy_ax.set_ylabel(
            "Elevation difference (m)",
            fontsize=axis_label_fontsize,
        )

        accuracy_ax.tick_params(
            axis="y",
            labelsize=coordinate_fontsize,
            direction="out",
            length=3.5,
            width=0.7,
            left=False,
            labelleft=False,
            right=True,
            labelright=True,
        )

        accuracy_ax.tick_params(
            axis="x",
            labelsize=coordinate_fontsize,
            direction="out",
            length=3.5,
            width=0.7,
        )

        # ----------------------------------------------------
        # Horizontal grid every 0.5 m
        # ----------------------------------------------------
        accuracy_ax.yaxis.set_major_locator(
            MultipleLocator(
                accuracy_grid_spacing
            )
        )

        accuracy_ax.set_axisbelow(
            True
        )

        if show_accuracy_grid:
            accuracy_ax.grid(
                axis="y",
                which="major",
                color=accuracy_grid_color,
                alpha=accuracy_grid_alpha,
                linewidth=accuracy_grid_linewidth,
                linestyle=accuracy_grid_linestyle,
                zorder=0,
            )

        else:
            accuracy_ax.grid(
                False
            )

        for spine in accuracy_ax.spines.values():
            spine.set_linewidth(
                0.7
            )

        # ----------------------------------------------------
        # Final layout
        # ----------------------------------------------------
        fig.subplots_adjust(
            left=left_margin,
            right=right_margin,
            bottom=bottom_margin,
            top=top_margin,
        )

        # ----------------------------------------------------
        # Save publication PDF
        # ----------------------------------------------------
        fig.savefig(
            output_pdf,
            format="pdf",
            dpi=dpi,
            bbox_inches="tight",
            pad_inches=pdf_pad_inches,
            facecolor="white",
            edgecolor="none",
            transparent=False,
        )

        output_png = Path(output_pdf).with_suffix(".png")
        fig.savefig(
            output_png,
            format="png",
            dpi=max(200, int(dpi)),
            bbox_inches="tight",
            pad_inches=pdf_pad_inches,
            facecolor="white",
            edgecolor="none",
            transparent=False,
        )

        print(
            "\nSelected final DSM configuration:"
        )

        print(
            f"  {product['name']}"
        )

        print(
            f"  Acquisition geometry: {geometry_tag}"
        )

        print(
            "\nAccuracy data:"
        )

        print(
            f"  File: {accuracy['file_path']}"
        )

        print(
            f"  Product: {accuracy['dem_name']}"
        )

        print(
            f"  Before stage: {accuracy['before_stage']}"
        )

        print(
            f"  After stage: {accuracy['after_stage']}"
        )

        print(
            "\nBefore co-registration:"
        )

        print(
            f"  RMSE: {before_statistics['rmse']:.3f} m"
        )

        print(
            f"  NMAD: {before_statistics['nmad']:.3f} m"
        )

        print(
            "\nAfter co-registration:"
        )

        print(
            f"  RMSE: {after_statistics['rmse']:.3f} m"
        )

        print(
            f"  NMAD: {after_statistics['nmad']:.3f} m"
        )

        print(
            "\nPDF figure saved to:"
        )

        print(
            output_pdf
        )

        if show:
            plt.show()

        else:
            plt.close(
                fig
            )

    if return_data:
        return (
            output_pdf,
            {
                "product": product,
                "geometry_tag": geometry_tag,
                "dem": dem_data,
                "intersection_error": error_data,
                "accuracy": accuracy,
            },
        )

    return output_pdf