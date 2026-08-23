# Changelog

## 0.9.0 — GitHub candidate

- Integrated the notebook-first Pléiades ASP interface into the installable repository.
- Added `pleiades-workflow-init` to materialize the complete Jupyter workspace after installation.
- Integrated Reference DEM preparation into Pre-processing.
- Reordered Reference DEM controls: general location → high-resolution alignment → map-projection reference → vertical conversion → download settings.
- France resets to the tested defaults: IGN LiDAR 1 m alignment, IGN LiDAR 50 m map reference, RAF20.
- Other/global requires an existing high-resolution alignment DSM and defaults global map sources/models appropriately.
- Reference DEM AOI reuses the Prepare-data AOI when left blank.
- Consolidated all figures under one `figures/` folder.
- Exposed `pc_merge` and `dem_geoid` through the ASP runner in addition to the existing public ASP commands.
- Preserved all existing ASP stereo, pre-processing, point-cloud and final DSM settings.
- Citation remains a placeholder until the publication reference is finalized.
