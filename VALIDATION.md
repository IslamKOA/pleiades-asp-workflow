# Validation — Pléiades ASP Workflow v1.0.6

This final package is based on v1.0.4 and keeps its scientific ASP/reference
processing logic unchanged.

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
- Wheel build
- Wheel package-data verification

Live ASP processing and live IGN/global DEM downloads still require validation
on the target server/workstation.


## v1.0.6 concept-figure verification

Dedicated runtime asset:

`figures/overview_mountain_notebook.png`

Dimensions: `1743 × 716 px`

Detected non-white content bbox: `(19, 17, 1725, 698)`

Lower-third non-white fraction: `0.462`

The v1.0.6 interface only loads this new filename, which prevents an older
workspace copy of `overview_mountain_page1.png` from being displayed.

## Public notebook startup-cell presentation

- Cover badge displays `Notebook software v1.0.6`.
- The startup code cell remains executable but its input strip is fully suppressed in the public notebook.
- Startup-cell outputs remain visible so the interactive workflow UI is unaffected.
- The developer notebook is unchanged.
- No scientific ASP, Reference DEM, stereo, alignment, map-projection, or DSM logic was modified.

## Required RPC dependency verification

- `rpcm==1.4.10` is a core package dependency, not an optional extra.
- A normal `pip install` of `pleiades-asp-workflow` therefore installs RPC support automatically.
- `requirements_interface.txt` is kept consistent with the package metadata.
- AOI cropping no longer instructs users to manually install `rpcm`; a missing import now indicates an incomplete environment/install.
