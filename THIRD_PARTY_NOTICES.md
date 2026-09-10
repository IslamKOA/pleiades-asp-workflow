# Third-party software and data notices

This file records external software/data components that are used by the Pléiades ASP Workflow but are not authored as part of the workflow itself.

## NASA Ames Stereo Pipeline (ASP)

The workflow uses the **NASA Ames Stereo Pipeline (ASP)** as its photogrammetric processing engine.

- Project: Ames Stereo Pipeline
- Source: https://github.com/NeoGeographyToolkit/StereoPipeline
- Documentation: https://stereopipeline.readthedocs.io/en/latest/
- License: Apache License, Version 2.0
- ASP copyright notice: Copyright (c) 2009-2026, United States Government as represented by the Administrator of the National Aeronautics and Space Administration. All rights reserved.

ASP is **not bundled in this workflow archive**. The command `asp-install` supplied by this project automatically retrieves and configures the requested ASP release from the official NeoGeographyToolkit/StereoPipeline release source in a separate managed installation directory.

ASP distributions may themselves contain or depend on third-party software with separate licenses. The ASP project documents those components in its own `THIRDPARTYLICENSES.rst`. Those licenses remain applicable to the ASP distribution obtained by `asp-install`.

This workflow is an independent research-software project. References to NASA and the Ames Stereo Pipeline describe the external processing software used by the workflow and do not imply NASA endorsement of this project.

## Python dependencies

The workflow uses third-party Python packages including NumPy, pandas, Matplotlib, Rasterio, PyProj, GeoPandas, Shapely, SciPy, xmltodict, JupyterLab, ipywidgets, psutil, xDEM, and rpcm. They are installed as package dependencies and remain subject to their respective licenses.

The authoritative dependency list is maintained in `pyproject.toml` and `src/pleiades_asp_workflow/interface_bundle/requirements_interface.txt`.

## Satellite imagery

Original Pléiades, Pléiades NEO, and SPOT 6/7 imagery is not distributed with this software archive. The original imagery and associated protected products remain subject to the applicable Airbus DS / DINAMIS licensing and access conditions.

## IGN / vertical-reference resources

The workflow includes reference-DEM and vertical-reference functionality that can use IGN resources for mainland France. The packaged file `src/pleiades_asp_workflow/interface_bundle/data/RAF20.tac` is an external geodetic resource rather than original workflow code.

**Before the first public archival deposit, verify and document the applicable IGN redistribution/attribution terms for the bundled RAF20 resource.** If redistribution is not permitted or requires additional terms, replace the bundled resource with a documented external acquisition step before release.

## Workflow license

No license has yet been selected for the original Pléiades ASP Workflow code. The top-level `LICENSE` file is intentionally empty at this stage. Selection of the workflow license does not alter the licenses or access conditions of the third-party software/data described above.
