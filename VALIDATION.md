# Validation — Pléiades ASP Workflow v1.3.7

This v1.3.7 package is a focused correction of the DIM↔RPC comparison while retaining the validated v1.2.9 workflow behavior elsewhere.

## v1.3.7 camera-comparison verification

- Runtime regression captures the actual `cam_test` argument list for multiple views.
- The default DIM↔RPC command contains `--image`, `--cam1`, `--cam2`, `--session1`, and `--session2` only.
- No automatic `--height-above-datum` argument is injected.
- No automatic `--sample-rate` argument is injected.
- The DIM exact session is `pleiades` for Pléiades/Pléiades Neo; RPC remains `rpc`.
- The DIM preflight also does not force height or sampling overrides.
- Saved CSV columns match the supplied external analysis code: Dataset, Sensor, View, Status, Samples, direction Min/Median/Max, DIM→RPC Min/Median/Max, RPC→DIM Min/Median/Max, and elapsed milliseconds per sample.
- Metadata → Compared geometry retains both the bordered numerical table and the saved/displayed comparison figure.
- Reference DEM figures/tables, independent alignment/mapprojection choices, vertical-reference controls, exact DIM/RPC preprocessing, and Pause/Resume/Stop are unchanged from v1.2.9.
- Regression suite: 70 tests pass.
- Python compilation and notebook JSON/version validation pass.

## v1.2.6 execution-control verification

- Pause/Resume/Stop report their action result instead of swallowing signal failures.
- The notebook status line exposes the active external command, PID, and POSIX PGID.
- Managed ASP execution bypasses the package console-script wrapper when the real installed ASP executable is available.
- Stop is asynchronous from the ipywidget callback and uses process-group termination plus recursive descendant termination when `psutil` is available.
- Live regression test: a shell parent and child both write continuously; Pause freezes the file, Resume restarts writes, and Stop terminates the run.
- `psutil>=5.9` is included as a core dependency for robust descendant control, with a POSIX process-group fallback if import is unavailable.

## Retained DIM and table verification

- Exact Pléiades camera session resolves to `pleiades`; camera-dependent commands receive `-t pleiades`.
- Full-image exact-DIM workflows use `<project>/full_data/asp_out`.
- DIM adjustment discovery accepts `ABC-DIM_A*`, `ABC-DIM_B*`, and `ABC-DIM_C*`.
- New RPC files use `RPC_A.XML`, `RPC_B.XML`, `RPC_C.XML`, with legacy `A.XML/B.XML/C.XML` fallback.
- Bundle adjustment and the remaining preprocessing tabs render bordered direct-HTML analysis tables.
- Both notebooks require v1.3.7 and load the helper beside the notebook.
- Regression suite: 64 tests pass.
- Python source compilation and notebook JSON/version validation pass.

Live ASP processing still requires the user's installed ASP distribution and real imagery; the live control test verifies the operating-system process-control mechanism independently of ASP data.

## Notebook figure fix

The Metadata and geometry concept figure now uses responsive HTML instead of
`ipywidgets.Image`.

Original PNG: `6116 × 2433 px`

Notebook PNG: `1800 × 716 px`

The full-quality PDF remains:

`figures/Overview_mountain.pdf`

## Example outputs

The provisional reconstructed example images were removed. `examples/` contains
only a README placeholder until real saved workflow outputs are copied there.

## Checks performed

- Python source compilation
- Notebook JSON/version checks
- Existing package tests
- Reference DEM contract tests
- Workspace materialization test
- Responsive figure-rendering contract test

Live ASP processing and live IGN/global DEM downloads still require validation
on the target server/workstation.


## v1.1.0 concept-figure verification

Dedicated runtime asset:

`figures/overview_mountain_notebook.png`

Dimensions: `1743 × 716 px`

Detected non-white content bbox: `(19, 17, 1725, 698)`

Lower-third non-white fraction: `0.462`

The v1.1.0 interface only loads this new filename, which prevents an older
workspace copy of `overview_mountain_page1.png` from being displayed.

## Public notebook startup-cell presentation

- Cover badge displays `Notebook software v1.2.3`.
- The startup code cell remains executable but its input strip is fully suppressed in the public notebook.
- Startup-cell outputs remain visible so the interactive workflow UI is unaffected.
- The developer notebook version guard is updated to v1.2.3; its scientific workflow remains unchanged.
- No scientific ASP, Reference DEM, stereo, alignment, map-projection, or DSM logic was modified.

## Required RPC dependency verification

- `rpcm==1.4.10` is a core package dependency, not an optional extra.
- A normal `pip install` of `pleiades-asp-workflow` therefore installs RPC support automatically.
- `requirements_interface.txt` is kept consistent with the package metadata.
- AOI cropping no longer instructs users to manually install `rpcm`; a missing import now indicates an incomplete environment/install.

## Geoid / vertical-correction QC

- Reference preparation now exposes three QC tabs: Alignment DEM, Map-projection DEM, and Geoid / Vertical.
- The Geoid / Vertical tab plots the actual `N` raster(s) used in `h = H + N`.
- The QC reports model, CRS, pixel size, extent, `N` range, and mean `N`.
- The geoid preview is saved as `Figure/reference_geoid_preview.png` and `.pdf`.
- If no vertical conversion is requested, the tab explicitly reports that the correction is not applied.
- ASP and Reference DEM generation logic are unchanged; this is a QC/visualization addition only.

## Geoid QC result-key correction

- The Geoid / Vertical QC tab now reads the actual prepared-reference result keys: `alignment_n` and `map_n`.
- This fixes the false `Vertical conversion: Not applied` message when RAF20/EGM conversion was in fact performed.
- Vertical conversion, DEM generation, and saved reference products are unchanged.


## v1.1.0 camera-model selection contract

- Default: `rpc` with `A.XML/B.XML/C.XML`.
- Exact Pléiades: `pleiades` with `DIM_A.XML/DIM_B.XML/DIM_C.XML`.
- The selected session/camera set is propagated through both bundle-adjustment calls, preliminary stereo, mapprojection, and final stereo.
- Final-processing selection is inherited/read-only.
- Legacy RPC output paths are unchanged; exact outputs use `_pleiades`.
- Exact DIM mode rejects AOI-cropped image inputs because the crop implementation only updates RPC offsets.
- ASP 3.3.0 supports Pléiades 1A/1B and Pléiades Neo exact models; SPOT 6/7 is kept on RPC in this release.

## v1.3.7 packaging checks

- Python source compilation passed.
- Both notebooks parse as valid JSON and require v1.3.7.
- Wheel build (`--no-build-isolation`) passed in the offline packaging environment.
- Final ZIP integrity was checked after packaging.

## v1.4.3
- Existing-project resume restores saved prepared/cropped image previews without rerunning preparation.
- Dependency contract aligned with supplied interface requirements (`rpcm==1.4.10`).


## v1.4.3

- Verified Python syntax for the updated interface.
- Added regression checks for clean/new-project controls, blue Prepare-data state, co-registration notebook rename, and single-stage saved-prerequisite reuse.
- Full automated test suite passes for the packaged release.


## v1.5.0

Validation includes conditional R1C1 tile rules, no-delete new-project reset, existing-project detection, persistent pre/post/reference result restoration, original-notebook packaging, and the renamed current co-registration notebook.


### Final v1.5.0 package validation

- Automated test suite: **88 passed**.
- Python source compilation: passed.
- Wheel build (no build isolation): passed.
- Wheel package-data inspection: current co-registration notebook present, 13 original scientific notebooks present, obsolete `07_` co-registration filename absent.
- Final ZIP integrity: checked after archive creation.

## v1.5.1

- Existing-project resume reconstructs full preprocessing result displays from products on disk: bundle-adjustment residual and adjustment tables, preliminary stereo summary, preliminary DSM preview, pc_align diagnostics and 4 × 4 transform matrix, camera-transform table, and map-projection output table/preview.
- New-project reset clears all Reference DEM project-specific controls, resolved paths, progress, and QC outputs without deleting any project files.
- Workflow header/interface lists optional co-registration as step 6.
- Automated test suite: **91 passed**.
- Python source compilation and notebook JSON validation: passed.


## v1.5.2

Validated legacy/current preprocessing-product discovery for existing-project display restoration, including preliminary DSM, pc_align transform, and map-projected images. Verified full reference-DEM UI reset to new-project defaults.


## v1.5.3

- Regression coverage added for exact saved preliminary-DSM path restoration, recursive legacy fallback, and project-wide cropped/full-data recovery.
- Automated test suite: 95 passed.


## v1.5.4

- Documentation/publication-preparation release only; no scientific processing logic changed.
- Version metadata synchronized to 1.5.4 across package modules and public notebook.
- Top-level `LICENSE` intentionally left empty and provisional MIT metadata removed.
- Added `THIRD_PARTY_NOTICES.md`, `CITATION.cff`, and `PUBLICATION_CHECKLIST.md`.
- README now documents automated separate ASP installation via `asp-install` and links to official ASP documentation.
- Automated regression suite: **95 passed**.
- Python source compilation: passed.
- Notebook JSON validation: passed (16 notebooks).
- `CITATION.cff` YAML parse check: passed.
