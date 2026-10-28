# Contributing to WASA-DEM

Thank you for your interest in **WASA-DEM**, the reproducible satellite stereo/tri-stereo DSM workflow built around NASA Ames Stereo Pipeline (ASP).

## Before opening an issue

1. Check the [README](README.md) for supported sensors, installation/update guidance, and scientific workflow details.
2. Search the [existing issues](https://github.com/IslamKOA/pleiades-asp-workflow/issues).
3. Identify the software version and your ASP version. The reference scientific workflow was validated with ASP 3.3.0.

## Bug reports

Please include:
- Operating system (Windows/WSL or Linux), Python environment, WASA-DEM and ASP versions.
- Satellite sensor (Pléiades, Pléiades NEO, or SPOT 6/7), acquisition configuration, image format, and relevant metadata type.
- The specific step that failed (e.g., `bundle_adjust`, `parallel_stereo`, `point2dem`, `pc_align`, or co-registration).
- Expected versus observed result, relevant error/log excerpt, and steps to reproduce.
- Coordinate reference system, vertical reference, resolution, and reference DEM information when relevant.

Do not publish licensed satellite imagery, proprietary input data, access tokens, personal information, or sensitive locations without permission. Prefer minimal synthetic or explicitly shareable examples.

## Feature requests and pull requests

Explain the scientific use case, affected sensors, validation data, and expected workflow behavior. Keep code and documentation changes focused, test against current installation procedures, and describe any effect on scientific reproducibility or archived outputs.

Do not silently change validated processing defaults, directory paths, installer behavior, or archived release identity.

## Citation and license

See [CITATION.cff](CITATION.cff), [LICENSE](LICENSE), and the [research software archive](https://doi.org/10.57745/4OQDHQ). The original archived title is **Pléiades ASP Workflow**; the current user-facing brand is **WASA-DEM**.
