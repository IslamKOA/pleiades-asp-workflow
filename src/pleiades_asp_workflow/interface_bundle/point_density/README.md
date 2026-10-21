# Optional LAZ export and point-cloud density

This folder contains an **independent, optional** notebook:

`08_Optional_LAZ_Export_and_Point_Density.ipynb`

It is a copy of the research notebook in `original_notebooks/`. The
main stereo reconstruction, DSM rasterization, and co-registration workflow
are **not modified** by adding this notebook.

## Before running

1. Open the notebook separately from the Pléiades ASP Workflow interface
   (or from Jupyter's file browser).
2. Set `METADATA_FILE` in the **USER SETTINGS** cell to the Bash metadata
   configuration for **your own dataset**. The default value
   `Metadata-Copy1.sh` is an example and that file is not automatically
   installed in this folder. Select the original configuration file explicitly.
3. Run in an environment with **ASP 3.5.0 or later**, providing `point2las`
   and the `--save-triangulation-error` option, and with **PDAL** available.
   The notebook checks these requirements before processing. If your main
   processing used ASP 3.3, you can keep that installation unchanged and
   choose an ASP 3.5+ environment for this optional notebook.
4. Review its input paths, triangulation-error threshold, and overwrite
   settings before exporting anything.

The notebook exports existing `*-PC.tif` point clouds to LAZ and saves
`laz_export_manifest.csv` and `point_density_summary.csv`. Its reported
density is the retained LAZ point count divided by the rectangular
horizontal bounding-box area (average points per square metre). It does
**not** compute one value for every 1 x 1 m cell.

Opening the notebook does not automatically start an ASP job or alter the
main workflow inputs.
