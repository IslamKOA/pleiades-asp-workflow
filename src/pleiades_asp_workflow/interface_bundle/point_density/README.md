# Optional LAZ export and point-cloud density

This folder contains the independent, optional notebook:

`08_Optional_LAZ_Export_and_Point_Density.ipynb`

This is a **standalone interface/testing copy** of the original research notebook
kept under `original_notebooks/`. No Bash metadata file or `.sh` configuration
is needed for this copy.

## Configure in the first settings cell

Set these three values directly:

- `POINT_CLOUD_DIR`: the folder containing existing ASP `*-PC.tif` files.
  The notebook searches recursively, so you can select your project's
  `full_data/asp_out` or `cropped_data/asp_out` and include both
  `point_clouds/` and `point_clouds_merged/`.
- `TARGET_SRS`: the actual projected CRS of your analysis, such as
  `"EPSG:32632"` or `"EPSG:32631"` (metre units).
- `MAX_VALID_TRIANGULATION_ERROR`: the point export filtering threshold
  in metres, e.g. `1.0`, matching your experimental configuration.

If ASP is not available from the Jupyter kernel environment, set
`ASP_BIN_DIR` to the bin folder of an **ASP 3.5.0 or later** installation,
or use the `USE_MICROMAMBA` option. The notebook uses `point2las` with
`--save-triangulation-error`, which is unavailable in ASP 3.3.
Set `PDAL_BIN_DIR` only if PDAL is also outside the kernel's PATH.

`LAZ_OUTPUT_DIR_OVERRIDE=None` saves the exported LAZ files and CSV summaries
in `<POINT_CLOUD_DIR>/laz`. Review the discovery preview before exporting.

## Result and scope

The notebook writes `laz_export_manifest.csv` and
`point_density_summary.csv`. Reported point density is the retained LAZ
point count divided by its rectangular horizontal bounding-box area
(average points/m²), **not** the number of points counted individually
in each 1 m × 1 m raster cell.

Opening the notebook through the main Jupyter workflow interface **does not**
run processing automatically. The ASP stereo reconstruction and DSM rasterization
steps remain independent and unchanged.
