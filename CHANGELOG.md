# Changelog

## 1.0.6 — Final notebook presentation and release cleanup

- Fixes the incomplete Metadata and geometry concept figure by rendering it as responsive HTML instead of `ipywidgets.Image`.
- Optimizes the notebook PNG while retaining the full-quality PDF in `figures/`.
- Preserves all reference-DEM and ASP scientific processing logic from v1.0.4.
- Consolidates the README around one clear installation workflow.
- Removes provisional/screenshot-derived example images.
- Keeps `examples/` as a placeholder for real saved workflow outputs only.


## 1.0.4 — Full rectangular IGN reference coverage

- IGN source-tile selection uses the Lambert-93 AOI bounding box expanded by the requested buffer on all four sides.
- Corner tiles are explicitly included for the rectangular ASP reference grid.
- Final 1 m and 50 m references must have 100% valid target-grid coverage.
- ASP remains blocked if reference coverage is incomplete.


## 1.0.3 — Restore original buffered-AOI IGN selection

- Restores the original tile-selection rule: actual AOI plus the requested 1000 m Lambert-93 buffer.
- Removes the enlarged rectangular source-download request introduced in v1.0.2 testing.
- Retains the full mosaic of all selected buffered IGN tiles for reference reprojection.
- 1 m alignment and 50 m map-projection DEMs remain direct native LiDAR aggregations.
- Final references remain rectangular in the selected ASP target CRS.
- Adds three attempts per IGN WMS tile request for transient failures.
- Reference DEM QC remains before bundle adjustment.


## 1.0.2 — Restore original IGN map-reference construction

- 50 m IGN map DEM is generated directly from native LiDAR HD tiles using the original mean aggregation logic.
- It is no longer downsampled from the reprojected 1 m alignment DEM.
- IGN buffer now defines actual surrounding reference coverage.
- Final IGN references use rectangular grids in the target CRS.
- Reference DEM QC remains before bundle adjustment.


## 1.0.1 — Reference DEM QC gate test

- Reference DEM preparation is a separate first action before ASP.
- Alignment and map-projection reference DEMs are displayed immediately after preparation.
- Both QC figures are saved as PNG and PDF.
- ASP pre-processing remains disabled until reference DEM preparation succeeds.
- Changing any reference DEM setting invalidates the prepared references.
- Scientific ASP processing commands/settings are unchanged.


## 1.0.0 — Final GitHub package

- Consolidated the notebook interface, ASP installer/runner, and Reference DEM preparation in one installable package.
- `pleiades-workflow-init` now creates the default workspace at `~/Pleiades_ASP_Workflow` regardless of the shell working directory.
- Restored the tested original map-projection QC display: the figure uses the common spatial intersection of all map-projected views rather than their union.
- Added strict map-projection CRS validation against the selected Target CRS (EPSG) before the QC figure is drawn.
- Kept the underlying ASP `mapproject` command and GeoTIFF products unchanged; the change is in QC validation/display only.
- Map-projection panel now reports the validated target EPSG and both PNG/PDF figure paths.
- Prepared/cropped, preliminary DSM, map-projection, and final DSM figures are saved in both PNG and PDF.
- Public legacy plotting helpers now also write a paired PNG when producing a PDF.
- Added packaged representative examples (PNG + PDF) from the tested La Bérarde workflow.
- Reorganized README around one primary installation route, a short workflow description, example figures, and a later troubleshooting section.
- Preserved the tested ASP stereo, bundle-adjustment, reference-DEM, point-cloud, and final-DSM scientific settings.
- Citation remains a placeholder until the associated paper reference is finalized.

## 0.9.0 — GitHub candidate

- Integrated the notebook-first Pléiades ASP interface into the installable repository.
- Added `pleiades-workflow-init` to materialize the complete Jupyter workspace after installation.
- Integrated Reference DEM preparation into Pre-processing.
- Reordered Reference DEM controls: general location → high-resolution alignment → map-projection reference → vertical conversion → download settings.
- France resets to the tested defaults: IGN LiDAR 1 m alignment, IGN LiDAR 50 m map reference, RAF20.
- Other/global requires an existing high-resolution alignment DSM and defaults global map sources/models appropriately.
- Reference DEM AOI reuses the Prepare-data AOI when left blank.
- Consolidated static interface figures under one `figures/` folder.
- Exposed `pc_merge` and `dem_geoid` through the ASP runner.
