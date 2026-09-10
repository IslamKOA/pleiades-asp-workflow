# v1.4.1 interface update

This release is based on the v1.3.7 workflow package and incorporates the latest user-provided workflow modules plus the requested interface/resume improvements.

## Interface changes

- Added a caution directly below **Image tile preparation**: when an acquisition contains tiles beyond `R1C1`, enable tile merging and include `R1C1` together with the required later tile(s).
- Added **Load / resume existing project**. Existing `project_settings.json`, prepared A/B/C files, metadata, reference-DEM configuration, processing state, and final-product tables are detected and reused where available.
- Added automatic restoration of the most recently used project when the notebook is reopened from the same workflow workspace. Restoring a project does not rerun processing.
- Prepared data no longer need to be recreated simply because the notebook/kernel was closed. When valid prepared files are detected, the UI explicitly reports that the user can continue without rerunning Prepare data.
- Pre-processing remains restartable stage by stage. Prerequisite outputs only need to exist on disk; they do not need to have been run in the current notebook session.
- Previous red error summaries are cleared immediately when a new corrected run is started for camera comparison, reference-DEM preparation, ASP pre-processing, point-cloud reconstruction, or final DSM generation.
- Preliminary DSM output is now rendered persistently in the processing-stage tab using the saved preview image.

## Optional co-registration

- Added a separate **Optional co-registration (outside ASP)** section after Final DSM.
- The button opens the bundled `Coregistration_Process-Final-withPlannimetric.ipynb` in a separate notebook tab.
- A `coregistration_handoff.json` file is written in the project directory with the project path, final DSM table/list, and currently resolved reference-DEM paths.
- The supplied co-registration notebook is intentionally kept as a separate optional scientific workflow. Review its INPUTS cell before running it.

## Original scientific notebooks

- Added `interface_bundle/original_notebooks/` as a dedicated location for archived/original scientific notebooks.
- Normal end-user workspace creation does not copy these files. Use `pleiades-workflow-init --developer` to include the developer notebook and the original-notebook archive.

## Latest supplied modules

The package uses the latest supplied versions of the vertical-reference, global-DEM, IGN LiDAR-HD, reference-DEM, stereo metadata/geometry, and requirements files. `asp_utils2.py` uses the latest supplied file as its base and adds the v1.4.1 interface/resume features above.

## Validation

- Python syntax validation completed for the updated interface module.
- Notebook JSON validation completed.
- Automated test suite: **79 passed**.
