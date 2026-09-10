# Changelog

## 1.5.4 — Publication metadata and documentation preparation

- Kept the top-level `LICENSE` intentionally empty while the project license is being selected.
- Removed the provisional MIT declaration from package metadata so the archive does not claim a license prematurely.
- Reworked `README.md` into a publication-ready project overview covering installation, workflow stages, resume behavior, reference DEMs, ASP processing, optional co-registration, data restrictions, and citation status.
- Clarified that `asp-install` automatically downloads/configures ASP as a separate managed dependency from the official NeoGeographyToolkit/StereoPipeline release source; ASP is not bundled in this workflow archive.
- Added direct links to the official ASP documentation, source repository, and Pléiades example.
- Added `THIRD_PARTY_NOTICES.md` for ASP attribution, third-party dependencies, satellite-data restrictions, and the pre-release RAF20 redistribution check.
- Added a provisional machine-readable `CITATION.cff` for the software release.
- Added `PUBLICATION_CHECKLIST.md` for the planned Recherche Data Gouv deposit and post-publication DOI/citation updates.
- No scientific processing logic or interface behavior was changed in this release.

## 1.5.3

- Fixed existing-project restoration for preliminary DSMs saved under older/non-current point2dem paths.
- Resume now prefers exact preprocessing paths recorded in `processing_state.json` before filename discovery.
- Added recursive case-insensitive fallback discovery for preliminary `*-DEM.tif` products.
- No ASP processing is rerun during restoration.

## 1.5.2

- Existing-project restore now discovers legacy preliminary DSM, pc_align transform/log, and map-projected image filenames before rebuilding result tabs.
- Preliminary DSM, Reference alignment, and Map projection tabs are restored from products already on disk without rerunning ASP.
- Reference alignment restoration still shows the saved 4 x 4 transform matrix when an older pc_align log is unavailable.
- Start new project now resets all reference-DEM source, resolution, geoid, path, progress, and QC controls to the default new-project state without deleting files.


## 1.5.1

- Existing-project resume now reconstructs the full preprocessing result panels from products already on disk, including bundle-adjustment residual/adjustment tables, preliminary stereo summary, preliminary DSM table and preview, pc_align diagnostics and 4x4 transform matrix, camera-transform table, and map-projection table/preview. No ASP command is rerun during restoration.
- Start new project / clear form now resets the complete Reference DEM interface state, resolved DEM paths, progress, and QC tabs while retaining files on disk.
- The public workflow header and interface now list optional co-registration as workflow step 6.

## 1.5.0 — Clean resume interface and persistent results

- Tile warnings are now conditional: no warning for valid R1C1-only input, a red warning when a later tile is selected without R1C1, and the same red warning when merging is enabled but R1C1 is omitted.
- New-project mode detects an already existing output project and directs the user to Load / resume existing project instead of silently treating it as new.
- Removed every project-deletion control. Start new project / clear form resets only notebook state and never deletes project files.
- Existing projects restore prepared-image previews, reference-DEM QC, completed pre-processing result tabs, point-cloud tables, and final-DSM tables/previews without rerunning ASP.
- Single-stage preprocessing continues to reuse compatible upstream files already present on disk.
- The latest corrected co-registration notebook is packaged without the `07_` filename prefix.
- The original scientific notebooks used during development are retained under `original_notebooks/`.

## 1.4.3 — Independent stage resume and clean-project controls

- Later preprocessing stages now reuse compatible upstream products already present on disk after reopening a project, without requiring the immediately preceding step to be rerun in the current session.
- Added saved-state and unique-product fallback discovery for single-stage preprocessing resumes.
- Added a top-level **Start clean / new project** action. Interface state is cleared safely by default; deleting a loaded project output folder requires an explicit checkbox and a valid `project_settings.json`. Source imagery is never deleted.
- New-project **Run prepare data** button is blue; an existing prepared project uses a warning-style **Re-run prepare data** button.
- Renamed the optional co-registration notebook to `Coregistration_Process-Final-withPlannimetric.ipynb`.

## 1.4.2 — Restore prepared-image previews when resuming a project

- Loading or auto-restoring an existing project now re-displays the saved prepared/cropped image preview immediately.
- Existing acquisition paths and prepared A/B(/C) rasters continue to be reused without rerunning Prepare data.
- For older projects that contain the prepared rasters but no saved preview PNG, the interface recreates only the lightweight preview from those existing rasters; no merge, crop, metadata, or ASP processing is repeated.

## 1.4.1 — Persistent resume, retry cleanup, preliminary-DSM display, and co-registration handoff

- Added **Load / resume existing project** using the saved `project_settings.json`; original acquisition paths, tile choices, AOI/crop settings, and output location are restored without rerunning Prepare data.
- Added a workspace last-project pointer so the previous project can be restored automatically after the notebook/kernel is reopened.
- Added reusable-output detection for prepared imagery, metadata/geometry tables, reference DEMs, processing state, and final DSM products.
- Restores existing reference DEM paths from `reference_dems/reference_dem_config.json` or `processing_state.json`, allowing full preprocessing to continue in a later notebook session without preparing the references again.
- Clarified that **Run one step** reuses prerequisite products already on disk and does not require earlier steps to be rerun in the current session.
- Added a prominent caution below Image tile preparation: later DIMAP tiles should be merged with `R1C1` when they belong to the same image product.
- Clears previous failure messages immediately when preprocessing, point-cloud reconstruction, or final DSM generation is retried.
- Preliminary DSM QC now embeds the saved PNG directly in the stage tab, avoiding blank figure output from background-thread rendering.
- Added an optional **Co-registration** section after Final DSM. The supplied xDEM notebook opens separately and a `coregistration_handoff.json` file records the active project/reference/final-DSM paths.
- Added `interface_bundle/original_notebooks/` as a developer-only location for preserving original scientific notebooks.
- Replaced workflow helper modules with the latest user-provided versions, including updated reference-DEM coverage handling and publication-friendly PDF font settings.

## 1.3.7
- Added resilient IGN LiDAR-HD WFS querying with JSON/GML2 and EPSG/URN fallbacks plus server-response diagnostics.
- Matched the ASP pre-processing execution heading size/style to the Reference DEM settings heading.

## 1.3.7

- Removed the long tri-stereo normalization explanation from **Input acquisitions**; that section now only asks for two inputs in Stereo mode or three inputs in Tri-stereo mode.
- Made the **Metadata and geometry** description acquisition-mode aware: Stereo explains only A/B, while Tri-stereo explains the DIM-time normalization to A = Forward, B = Near-nadir/Middle, C = Backward.
- Added a persistent `metadata/runtime_summary.csv` with one predefined row per workflow stage. Wall-clock time is written automatically on successful completion.
- Rerunning any stage updates that same runtime row rather than appending duplicates; the interface also shows a live Runtime summary table.
- Runtime tracking covers Prepare data, Metadata and geometry, DIM↔RPC comparison, Reference DEM preparation, all six preprocessing stages, Point-cloud reconstruction, and Final DSM.
- Runtime bookkeeping is diagnostic only and cannot make a successful scientific processing step fail.
- Retains the previously corrected inline Map-projection and Final-DSM figure rendering, rerunnable Point-cloud/Final-DSM controls, persistent preprocessing result tabs, DIM/RPC handling, reference DEM QC, and Pause/Resume/Stop behavior.

## 1.2.9 — UI clarity and vertical-reference simplification

- Clarified that `cam_test` ground height and sampling rate belong only to the DIM↔RPC camera-comparison diagnostic and do not affect bundle adjustment, stereo, `pc_align`, mapprojection, or DSM generation.
- Preserved the DIM↔RPC numerical table and comparison figure and added an explicit results heading.
- Simplified vertical-reference controls: one shared selector is shown by default; two role-specific selectors appear automatically when alignment and map-projection DEM sources differ, with an optional manual split when the same source needs different vertical models.
- Preserved France → RAF20 defaults and source-dependent global defaults.
- Kept reference DEM QC figures and bordered tables for Alignment DEM, Map-projection DEM, and vertical conversion.
- Strengthened the camera-model warning in Advanced pre-processing.
- Restyled major workflow section headings and internal reference/advanced-processing subsection headings for clearer visual separation.

## 1.2.8 — Independent alignment/map references, editable resolutions, and restored QC tables

- Restored the full map-projection reference selector even when Copernicus GLO-30 or SRTM1 is selected for `pc_align`; no source choice is hidden or locked.
- Changing the alignment source now only initializes the map-projection source to the same family (`IGN → IGN`, `Copernicus → Copernicus`, `SRTM → SRTM`). The user can then choose a different map source independently.
- Alignment and map-projection working resolutions are both editable for all sources. Copernicus/SRTM still default to ~30 m, while the UI warns that finer resampling does not add native topographic detail.
- Backend reuse now occurs only when automatic source, requested resolution, and vertical model are identical. Different sources, different resolutions, or different vertical models produce independent prepared reference DEMs.
- Restored source-specific vertical defaults by role: mainland France defaults both alignment and map references to RAF20 for every source; outside France, SRTM defaults to EGM96 and Copernicus defaults to EGM2008 independently for each role. All vertical-model selectors remain editable, including Custom N raster.
- Restored/expanded reference QC so both Alignment DEM and Map-projection DEM tabs show the saved figure plus a bordered table with selected source, requested resolution, vertical model, actual pixel size, CRS, extent, elevation range, coverage, and output paths. The Geoid/Vertical tab also shows a bordered numerical table.
- Retains all v1.2.6 verified Run / Pause / Resume / Stop behavior and all exact-DIM/RPC naming/session fixes.

## 1.2.7 — Flexible alignment references and shared global DEM reuse

- Added Copernicus DEM GLO-30 and SRTM1 as selectable `pc_align` alignment references in addition to IGN LiDAR HD and user-supplied DEMs.
- Global alignment references are prepared at their native ~30 m working resolution and the resolution field is locked accordingly.
- Added a prominent UI warning that coarse global DEMs improve transferability but a higher-resolution reference is recommended for steep terrain, narrow valleys, and accuracy-sensitive alignment.
- When Copernicus or SRTM is selected for alignment, mapprojection is linked automatically to the same source and the exact same prepared ellipsoidal DEM is reused. No second download, reprojection, vertical conversion, or duplicate raster is created.
- Other/global projects now default to Copernicus GLO-30 for both alignment and mapprojection, while France retains IGN LiDAR HD as the preferred default.
- Reference-preparation status reports explicitly when one shared global DEM is being reused for both ASP stages.
- Retains all v1.2.6 exact-DIM processing and verified Run / Pause / Resume / Stop controls.

## 1.2.6 — Verified Run / Pause / Resume / Stop controls

- Reworked the execution controller so Pause/Resume/Stop no longer silently swallow failed signals. Every control action now reports success/failure in the notebook.
- The active ASP command now displays its PID and, on POSIX/Linux, its process-group ID (PGID), making it clear which process is being controlled.
- Pause sends `SIGSTOP` to the real ASP process group and Resume sends `SIGCONT`; optional `psutil` fallback also reaches descendants that create separate groups.
- Stop is now asynchronous from the widget callback: the button responds immediately while the controller terminates the ASP process tree with TERM followed by KILL if needed.
- When PATH resolves to this package's Python console wrapper, the managed runner now prefers the installed real ASP executable directly, so controls target ASP rather than only the launcher.
- Added a live regression test that verifies a parent+child process actually stops writing while paused, resumes writing after Resume, and is terminated by Stop.
- Retains the v1.2.5 reliable bordered preprocessing tables and all v1.2.4 exact-DIM path/session/naming fixes.

## 1.2.4 — Exact-DIM output naming and full-data path fix

- Fixed exact Pléiades/Neo adjustment discovery to prefer ASP's camera-derived DIM names such as `ABC-DIM_A.adjust`, `ABC-DIM_B.adjust`, and `ABC-DIM_C.adjust` (or corresponding `.adjusted_state.json` files).
- Removed the remaining final-processing prerequisite that hard-coded RPC-style `ABC-A.adjust` / `ABC-B.adjust` / `ABC-C.adjust` names. Final stereo now resolves the aligned adjustment/state products from the active camera files.
- Ensured full-image workflows, including exact DIM, use `<project>/full_data/asp_out` as the canonical ASP output directory. The misleading empty project-root `asp_out` directory is no longer the configured ASP-output path.
- New prepared RPC cameras are named `RPC_A.XML`, `RPC_B.XML`, and `RPC_C.XML` (and `RPC_A_crop.XML`, etc. for legacy RPC crop mode) to distinguish them unambiguously from `DIM_A.XML`, `DIM_B.XML`, and `DIM_C.XML`. Existing projects using `A.XML/B.XML/C.XML` remain backward-compatible.
- Metadata, overlap, `cam_test`, crop, and preprocessing camera selection now prefer `RPC_*` names with automatic fallback to legacy names.
- Fixed both packaged notebooks to require v1.2.4 and to load the `asp_utils2.py` shipped beside the notebook, preventing a stale helper elsewhere on `sys.path` from silently controlling a newer interface.
- Retains exact-camera `-t pleiades` propagation through bundle adjustment, preliminary stereo, camera-transform bundle adjustment, mapprojection, and final stereo.
- Retains v1.2.3 Run / Pause / Resume / Stop process controls.

## 1.2.3 — Responsive Run / Pause / Resume / Stop execution

- Long ASP tasks now run in a background Python thread so Jupyter widget controls remain responsive while ASP is executing.
- Added section-level **Pause**, **Resume**, and **Stop** controls for DIM-vs-RPC `cam_test`, ASP pre-processing, point-cloud reconstruction, and final DSM generation.
- External commands are launched in their own POSIX process group; Stop terminates the complete active process tree, including ASP child processes.
- Pause/Resume uses POSIX `SIGSTOP` / `SIGCONT`, continuing the same ASP process rather than restarting the stage.
- On non-POSIX hosts, Pause/Resume is disabled rather than pretending to suspend ASP; Stop remains available through process-tree termination.
- Stop from a paused state resumes the process only long enough to terminate it cleanly.
- User cancellation is reported separately from a scientific/ASP failure and no longer appears as a generic red processing error.
- Completed upstream stage outputs are preserved after cancellation. Interrupted partial outputs are left for inspection and can be replaced by enabling **Overwrite existing outputs** before rerunning.
- Run buttons are locked while another long task is active, preventing concurrent commands from writing to the same output prefixes. They are restored after Stop/completion so the same stage can be run again without restarting the notebook.
- Retains the v1.2.2 exact-DIM preflight, Pléiades `--camera-weight 0 --tri-weight 0.1` bundle-adjustment configuration, flexible adjustment/model-state discovery, and editable cost-function field.

## 1.2.2 — Pléiades exact DIM bundle-adjustment compatibility

- Fixed exact Pléiades/Pléiades Neo DIM preprocessing so bundle adjustment follows the documented `-t pleiades --camera-weight 0 --tri-weight 0.1` recipe.
- Added automatic `cam_test` preflight for every DIM/image pair before exact-camera bundle adjustment.
- Kept `DIM_A.XML`, `DIM_B.XML`, and `DIM_C.XML` distinct from RPC `A.XML`, `B.XML`, and `C.XML`; ASP pairing is positional and does not require matching basenames.
- Made output validation accept image-stem, camera-stem, `.adjust`, and CSM `.adjusted_state.json` products, with an explicit listing of files actually produced.
- Fixed bundle-adjustment residual parsing for `DIM_A.XML` / `DIM_B.XML` / `DIM_C.XML` camera labels.
- ASP command failures now show the last log lines directly in the notebook in addition to saving the full log.
- The bundle-adjustment cost-function field is now editable: documented values are suggested while custom ASP values can be typed.

## 1.2.1 — Analysis-table cleanup and geometry-comparison refinement

- Added clear cell borders and column separators to metadata and analysis tables.
- Moved the exact-DIM vs RPC `cam_test` diagnostic into a new **Compared geometry** tab under Metadata and geometry.
- Replaced raw `cam_test` excerpts and long file-path columns with parsed numerical diagnostics: direction difference, DIM→RPC and RPC→DIM pixel differences, sample count, and elapsed time.
- Added a saved DIM-vs-RPC comparison plot plus a highlighted interpretation note; detailed stdout remains in per-view log files.
- Added a bundle-adjustment cost-function selector with all ASP-supported choices (`Cauchy`, `PseudoHuber`, `Huber`, `L1`, `L2`); Cauchy remains selected by default.
- Advanced preprocessing controls now follow the run mode: the full six-stage panel remains in **Run all**, while **Run one step** shows only the selected stage and required shared controls.
- Simplified the Camera transform UI/output to report that the `pc_align` transform was applied, without exposing low-value fixed command details.

## 1.2.0 — View normalization, restartable preprocessing, and RPC/DIM diagnostic

- Tri-stereo folder slots are now normalized after DIM metadata inspection to `A = Forward (F)`, `B = Middle / near-nadir (M)`, and `C = Backward (B)` using acquisition time.
- Prepared TIFF/RPC/DIM/VRT and AOI-cropped copies are relabeled together, with the assignment saved as `metadata/*_view_assignment.csv`.
- Added `Run all pre-processing` (default) and `Run one step` execution modes.
- Each single-stage rerun checks and reuses only the prerequisite outputs required by that stage, allowing recovery from a failed ASP step without restarting the full chain.
- Reorganized advanced preprocessing controls into six clearly labeled parameter groups: bundle adjustment, preliminary stereo, preliminary DSM, reference alignment, camera transform, and map projection.
- Split the previous shared camera/map thread setting into separate camera-transform and mapproject thread controls; exposed preliminary DSM resolution and nodata controls.
- Added a temporary `cam_test` RPC-vs-DIM diagnostic for Pléiades / Pléiades Neo, using `DIM_*.XML` with the `pleiades` session and the corresponding RPC XML with the `rpc` session.
- Added `cam_test` to the packaged ASP runner surface and installation validation.


## 1.1.0 — Selectable RPC / Pléiades exact camera model

- Added a camera-model selector under Advanced pre-processing settings.
- RPC remains the default (`-t rpc` with `A.XML/B.XML/C.XML`).
- Added optional Pléiades exact linescan mode (`-t pleiades` with `DIM_A.XML/DIM_B.XML/DIM_C.XML`).
- Propagates the selected model through bundle adjustment, preliminary stereo, camera-transform bundle adjustment, mapprojection, and final stereo.
- Shows the inherited selection read-only under Advanced final-processing settings.
- Preserves legacy RPC output names and namespaces exact-camera outputs with `_pleiades`.
- Exact DIM mode requires full prepared images; AOI-cropped exact-camera processing is blocked to avoid a silent pixel-coordinate mismatch.
- SPOT 6/7 remains RPC-only with the packaged ASP 3.3.0 reproducibility default.


## 1.1.0 — Final notebook presentation and release cleanup

### Geoid QC wiring fix

- Fixed the Geoid / Vertical QC tab to use the prepared result keys `alignment_n` and `map_n`.
- Prevents a false `Not applied` status after successful vertical conversion.
- No vertical-reference or DEM-processing logic changed.

### Reference QC enhancement — geoid / vertical correction

- Added a third `Geoid / Vertical` tab to the pre-ASP reference QC gate.
- Displays the actual geoid/quasi-geoid undulation raster(s) `N` used for vertical conversion.
- Saves `reference_geoid_preview.png` and `reference_geoid_preview.pdf`.
- Clearly reports when no vertical conversion/geoid raster is required.
- No scientific DEM-generation or ASP processing logic was changed.

### Packaging fix — automatic RPC support

- Promoted `rpcm==1.4.10` from an optional extra to a required dependency.
- Fresh GitHub/pip installations now include AOI-cropping RPC support automatically.
- Updated runtime guidance and installation documentation accordingly.

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
