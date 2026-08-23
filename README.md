# Pléiades ASP Workflow

**Release v1.0.6**

A notebook-first research-software interface for generating DSMs from
**Pléiades, Pléiades Neo, and SPOT 6/7** stereo/tri-stereo imagery with
**NASA Ames Stereo Pipeline (ASP)**.

The software integrates image preparation, metadata/geometry inspection,
reference-DEM preparation, ASP pre-processing, point-cloud reconstruction,
and final DSM generation in one Jupyter interface.

The reproducibility default is **NASA Ames Stereo Pipeline 3.3.0**.

> The associated paper/software citation will be added after publication.
> For the complete scientific methodology, parameter interpretation,
> validation, and recommendations, follow the associated paper once the final
> citation is available.

---

## Installation

Use Python **3.10 or newer**. Python 3.11 is recommended.

Create and activate a clean environment:

```bash
conda create -n pleiades_asp python=3.11 pip -y
conda activate pleiades_asp
```

Install the interface, install/select ASP 3.3.0, and create the notebook
workspace with one command:

```bash
python -m pip install git+https://github.com/IslamKOA/pleiades-asp-workflow.git && asp-install 3.3.0 && pleiades-workflow-init
```

The complete interface is created at:

```text
~/Pleiades_ASP_Workflow/
```

Launch it with:

```bash
jupyter lab ~/Pleiades_ASP_Workflow/Pleiades_ASP_Workflow.ipynb
```

Then click:

```text
▶ Start Pléiades ASP Workflow
```

---

## Workflow

```text
Prepare data
→ Metadata and geometry
→ Reference DEM preparation and QC
→ ASP pre-processing
   ├─ Bundle adjustment
   ├─ Preliminary stereo
   ├─ Preliminary DSM
   ├─ High-resolution reference alignment
   ├─ Apply alignment transform to cameras
   └─ Map projection
→ Point-cloud reconstruction
→ Final DSM generation
```

![Stereo and tri-stereo acquisition geometry](src/pleiades_asp_workflow/interface_bundle/figures/overview_mountain_notebook.png)

The interface preserves the tested scientific processing sequence while
allowing the intended parameters to remain user-configurable.

---

## Reference DEM preparation

The software keeps two reference roles separate:

1. **High-resolution alignment reference** for `pc_align`.
2. **Generalized map-projection reference** for `mapproject`.

For **France — mainland**, the default starting configuration is:

```text
Alignment reference:      IGN LiDAR HD
Alignment resolution:     1 m
Map-projection reference: IGN LiDAR HD
Map DEM resolution:       50 m
Vertical model:           RAF20
```

Reference DEMs are prepared **before bundle adjustment**. The user first runs:

```text
1. Prepare & check reference DEMs
```

The actual alignment and map-projection DEMs are then displayed with CRS,
resolution, extent, elevation range, and coverage information. ASP remains
blocked if reference preparation fails.

The Reference DEM AOI may be left blank to reuse the AOI entered under
**Prepare data**, or a separate reference AOI may be supplied.

For global use, Copernicus GLO-30, SRTM1, and existing DEM inputs are supported
for the map-projection reference. An existing high-resolution DSM can be used
for alignment.

Automatically downloaded references can be converted to ellipsoidal heights.
Existing DEM vertical conversion remains optional.

```text
N = h - H
h = H + N
H = h - N
```

![Vertical-reference concept](src/pleiades_asp_workflow/interface_bundle/figures/Geoid_concept.png)

---

## ASP pre-processing

The tested pre-processing sequence is:

```text
Reference DEM QC
→ bundle_adjust
→ preliminary parallel_stereo
→ preliminary point2dem
→ pc_align
→ apply transform to adjusted cameras
→ mapproject
```

Tested starting values include:

```text
Bundle-adjust robust threshold     2
Bundle-adjust max iterations       500
Preliminary algorithm              BM — asp_bm
Preliminary CK:SK                  35:45
Preliminary cost mode              2
Preliminary xcorr threshold        2
Correlation memory                 10240 MB
Correlation tile size              3200
Subpixel mode                      2
pc_align max displacement          250 m
pc_align iterations                100
Map/camera threads                 18
Map-projected image resolution     0.5 m
```

BM, SGM, MGM, and custom algorithm/kernel controls are available.

---

## Point-cloud reconstruction and final DSM

The interface supports stereo AB, tri-stereo single-pair tests, ordered
three-image configurations, and full tri-stereo reconstruction using
AB + AC + BC followed by `pc_merge`.

Tested final DSM starting values:

```text
Final DSM resolution                  1 m
Maximum valid triangulation error     1 m
```

---

## Output figures

Project QC figures are written under:

```text
<Project>/Figure/
```

The workflow saves graphical outputs in **both PNG and PDF**, including:

- prepared/cropped image previews;
- alignment reference DEM QC;
- map-projection reference DEM QC;
- preliminary DSM visualization;
- map-projected image QC;
- final DSM visualization.

Static software figures are kept together under:

```text
figures/
```

The installed workspace also includes:

```text
examples/
```

for real representative PNG/PDF products generated by the workflow. The public
README can display those examples after validated outputs are copied there.

---

## Map-projection CRS and QC

The **Target CRS (EPSG)** selected under Advanced pre-processing is reused by
reference-DEM preparation and ASP `mapproject`.

The map-projection QC verifies the raster CRS and displays the common spatial
intersection of all map-projected views. This avoids non-overlapping NoData
borders in the QC figure without modifying the actual GeoTIFF outputs.

For the La Bérarde test case, the tested target CRS is:

```text
EPSG:32632 — WGS 84 / UTM zone 32N
```

---

## Supported ASP commands

```text
bundle_adjust
parallel_stereo
point2dem
pc_align
mapproject
pc_merge
dem_geoid
```

---

## Additional information

Check the installation with:

```bash
asp-version
asp-doctor
pleiades-workflow-info
```

Refresh an existing installed workspace after a software update with:

```bash
pleiades-workflow-init --force
```

If an existing ASP installation places its bundled Python before the active
Conda Python, check:

```bash
which python
```

If it points inside `StereoPipeline-.../bin`, restore the active environment:

```bash
export PATH="$CONDA_PREFIX/bin:$PATH"
hash -r
```

Then confirm:

```bash
which python
python --version
```

On Windows, NASA ASP itself runs in Linux; use the included Windows → WSL
bridge with a working WSL installation.

The optional RPC crop step uses `rpcm`. Install that optional dependency only
when needed:

```bash
python -m pip install "pleiades-asp-workflow[rpc] @ git+https://github.com/IslamKOA/pleiades-asp-workflow.git"
```

---

## Installed workspace

```text
~/Pleiades_ASP_Workflow/
├── Pleiades_ASP_Workflow.ipynb
├── asp_utils2.py
├── prepare_stereo_metadata_and_geometry.py
├── pleiades_reference_dem.py
├── global_dem_downloader.py
├── ign_lidarhd_downloader.py
├── vertical_reference.py
├── requirements_interface.txt
├── figures/
├── examples/
└── data/
```

---

## Citation

The associated paper/software citation will be added after publication.

## License

See `LICENSE`.
