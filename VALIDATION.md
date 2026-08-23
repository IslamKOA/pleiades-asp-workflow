# Candidate validation report

Release: v0.9.0

Validated locally without downloading ASP or external DEM data:

- Python syntax/compile validation for all notebook runtime modules.
- Notebook JSON and version-guard validation.
- Reference DEM panel ordering and France/default behavior validated statically.
- Original pre-processing, point-cloud and final DSM default settings preserved.
- ASP runner exposes `pc_merge` and `dem_geoid` in addition to the original commands.
- A wheel was built successfully from `pyproject.toml`.
- The wheel was installed with `--no-deps` in a clean virtual environment.
- `pleiades-workflow-init` successfully materialized the complete notebook workspace.
- All materialized runtime Python modules compiled successfully.
- `pleiades-workflow-info` executed successfully.
- `asp-version` executed successfully and reported the reproducible default ASP 3.3.0.
- All runtime/publication figures are stored under one `figures/` directory in the materialized interface.

Not executed in this offline candidate test:

- ASP binary download/install.
- Live IGN LiDAR HD download.
- Live Copernicus/SRTM download.
- Live PROJ geoid-grid download.
- A complete stereo/DSM run.

These network/runtime operations should be tested on the target workstation before the public GitHub release.
