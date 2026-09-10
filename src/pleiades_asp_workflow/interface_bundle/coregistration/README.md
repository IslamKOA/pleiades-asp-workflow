# Optional co-registration

Co-registration is intentionally outside the NASA Ames Stereo Pipeline workflow.

The main interface provides an **Open optional co-registration notebook** action after final DSM generation. It writes `coregistration_handoff.json` in the active project folder with the detected final DSM products and current reference-DEM paths, then opens:

`Coregistration_Process-Final-withPlannimetric.ipynb`

The supplied notebook is based on the author's latest xDEM/Nuth & Kääb workflow. Review and edit its **INPUTS** cell before running it, especially the external LiDAR/reference DEM and stable-area polygon paths. The handoff JSON is informational and does not silently change the scientific inputs in the notebook.


`xdem` is declared as a workflow package dependency and is installed with the normal Python package installation; a separate manual xDEM installation is not required when the environment was created from this package.
