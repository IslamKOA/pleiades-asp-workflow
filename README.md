# Pléiades ASP Workflow

**GitHub candidate release: v0.9.0**

A notebook-first, end-to-end workflow for generating and evaluating DSMs from
**Pléiades, Pléiades Neo and SPOT 6/7** imagery with **NASA Ames Stereo
Pipeline (ASP)**.

The repository combines two layers:

1. **ASP runtime layer** — installs/selects an official ASP release and exposes
   the required ASP commands on Linux or through the Windows → WSL bridge.
2. **Scientific notebook interface** — `Pleiades_ASP_Workflow.ipynb`, which
   provides data preparation, metadata/geometry inspection, pre-processing,
   integrated reference-DEM preparation, point-cloud reconstruction and final
   DSM generation.

The recommended reproducibility target is **ASP 3.3.0**, matching the tested
workflow methodology.

> The manuscript/software citation will be added after publication. Do not use
> a provisional citation from this README.

---

## 1. Install the workflow package and ASP

### 1.1 Create a clean Conda environment

```bash
conda create -n pleiades_asp python=3.11 pip -y
conda activate pleiades_asp
```

### 1.2 Install this repository from GitHub

After the repository is published, install it with:

```bash
python -m pip install git+https://github.com/IslamKOA/pleiades-asp-workflow.git
```

This installs the Python dependencies, ASP runner/bridge, and the packaged
Jupyter interface.

### 1.3 Install the tested ASP release

```bash
asp-install 3.3.0
```

Check it with:

```bash
asp-version
asp-doctor
```

The public ASP commands include:

```text
bundle_adjust
parallel_stereo
point2dem
pc_align
mapproject
pc_merge
dem_geoid
```

**Windows:** ASP itself runs in Linux through WSL; the package handles the
Windows-path → WSL-path bridge. A working WSL installation is therefore
required.

---

## 2. Create/download the notebook interface

After the package is installed, run:

```bash
pleiades-workflow-init
```

This creates:

```text
./Pleiades_ASP_Workflow/
```

containing the complete working interface:

```text
Pleiades_ASP_Workflow.ipynb
asp_utils2.py
prepare_stereo_metadata_and_geometry.py
pleiades_reference_dem.py
global_dem_downloader.py
ign_lidarhd_downloader.py
vertical_reference.py
requirements_interface.txt
figures/
data/
```

The command refuses to overwrite an existing workspace by default. To refresh
package-managed files intentionally:

```bash
pleiades-workflow-init Pleiades_ASP_Workflow --force
```

For the developer notebook as well:

```bash
pleiades-workflow-init Pleiades_ASP_Workflow --developer
```

### Start the interface

```bash
cd Pleiades_ASP_Workflow
jupyter lab Pleiades_ASP_Workflow.ipynb
```

Then click:

```text
▶ Start Pléiades ASP Workflow
```

---

## 3. One-line initial setup

Once the GitHub repository is public, the normal first installation can be:

```bash
python -m pip install git+https://github.com/IslamKOA/pleiades-asp-workflow.git && asp-install 3.3.0 && pleiades-workflow-init
```

Then:

```bash
cd Pleiades_ASP_Workflow
jupyter lab Pleiades_ASP_Workflow.ipynb
```

---

## 4. Test this candidate ZIP before publishing to GitHub

For the ZIP supplied with this candidate release:

```bash
unzip pleiades-asp-workflow-v0.9.0-github-candidate.zip
cd pleiades-asp-workflow-v0.9.0-github-candidate
```

Install the local repository in the test environment:

```bash
python -m pip install -e .
```

If you already have ASP 3.3.0 installed by the runner, check it:

```bash
asp-version
asp-doctor
```

Otherwise install it:

```bash
asp-install 3.3.0
```

Create a fresh interface workspace:

```bash
pleiades-workflow-init ./Pleiades_ASP_Workflow_Test
```

Launch:

```bash
jupyter lab ./Pleiades_ASP_Workflow_Test/Pleiades_ASP_Workflow.ipynb
```

This local test is the recommended final check before pushing the repository to
GitHub.

---

## 5. Scientific workflow

The notebook follows the processing sequence:

```text
Prepare data
→ Metadata and geometry
→ Pre-processing
   ├─ Reference DEM preparation
   ├─ Bundle adjustment
   ├─ Preliminary stereo
   ├─ Preliminary DSM
   ├─ High-resolution alignment
   ├─ Camera transform
   └─ Map projection
→ Point-cloud reconstruction
→ Final DSM generation
```

The existing ASP processing sequence and the tested stereo/DSM defaults are
retained in this candidate.

---

## 6. Integrated Reference DEM settings

Reference topography is configured directly inside **Pre-processing**.

The panel is deliberately organized around the two distinct ASP roles.

### General location

At the top of the panel the user selects:

```text
Country / region
Reference DEM AOI
```

The **Reference DEM AOI may normally be left blank**. In that case the software
reuses the AOI already supplied under **Prepare data**. A separate AOI is only
needed when no Prepare-data AOI exists or when a different reference-download
extent is required.

### A. High-resolution alignment reference

This reference is used by `pc_align`.

**France — mainland default:**

```text
IGN LiDAR HD
1 m
```

An existing high-resolution DSM can always be selected instead.

For **Other / global**, an existing high-resolution alignment DSM is required;
Copernicus/SRTM 30 m surfaces are not substituted for the high-resolution
alignment reference.

### B. Map-projection reference

This reference is used by `mapproject`.

France defaults to:

```text
IGN LiDAR HD
50 m
```

The user may instead select:

```text
Copernicus DEM GLO-30
SRTM1 30 m
Existing DEM
```

Copernicus/SRTM default to 30 m. All map-reference resolutions remain editable.

If Copernicus or SRTM is selected in France, the high-resolution alignment
reference remains IGN LiDAR HD by default.

### C. Vertical reference conversion

Automatically downloaded reference DEMs are prepared as **ellipsoidal heights**
before ASP.

France defaults to:

```text
RAF20
```

but the selector remains editable:

```text
RAF20
EGM96
EGM2008
Custom N raster
```

Other/global defaults are:

```text
SRTM       → EGM96
Copernicus → EGM2008
```

For an **existing DEM**, conversion is optional. If conversion is not selected,
the software assumes that the existing reference already contains the vertical
heights intended for ASP.

The vertical relationship is:

```text
h = H + N
H = h - N
```

where `N` is the spatial geoid/quasi-geoid separation.

### Target horizontal CRS

The prepared alignment and map-projection DEMs use the **Target CRS (EPSG)**
already defined under **Advanced pre-processing**. The control is intentionally
not duplicated in the Reference DEM panel.

---

## 7. Pre-processing defaults retained

The tested defaults remain user-editable:

```text
BA robust threshold          2
BA max iterations            500
Preliminary algorithm        BM — asp_bm
Preliminary CK:SK            35:45
Preliminary cost mode        2
Preliminary xcorr threshold  2
Correlation memory           10240 MB
Correlation tile size        3200
Subpixel mode                2
Max displacement             250 m
pc_align iterations          100
Map/camera threads           18
Map image resolution         0.5 m
```

BM/SGM/MGM and custom algorithm/kernel controls remain available exactly as in
the tested notebook workflow.

---

## 8. Point-cloud and final DSM modes

The interface supports:

- stereo AB;
- tri-stereo single-pair tests AB / AC / BC;
- ordered three-image modes ABC / BAC / CAB;
- full Tri mode: AB + AC + BC followed by `pc_merge`.

The final DSM defaults remain:

```text
DSM resolution                    1 m
Maximum valid triangulation error 1 m
```

Intersection-error output remains optional.

---

## 9. Figures and software assets

All interface/publication figures are stored in **one folder**:

```text
figures/
├── overview_mountain_page1.png
├── Overview_mountain.pdf
└── Geoid_concept.png
```

This keeps the runtime workspace clean and makes later README figure insertion
straightforward.

---

## 10. Optional RPC crop dependency

The optional RPC-based crop step uses `rpcm`.

Because `rpcm`/`srtm4` may require additional native build support on some
Windows installations, it remains an optional dependency:

```bash
python -m pip install "pleiades-asp-workflow[rpc]"
```

When installing directly from GitHub with the optional dependency:

```bash
python -m pip install "pleiades-asp-workflow[rpc] @ git+https://github.com/IslamKOA/pleiades-asp-workflow.git"
```

The rest of the workflow does not require `rpcm` unless the optional RPC crop is
used.

---

## 11. Repository structure

```text
pleiades-asp-workflow/
├── README.md
├── LICENSE
├── CHANGELOG.md
├── pyproject.toml
├── src/
│   ├── pleiades_asp_runner/       # ASP installer + Linux/WSL command bridge
│   ├── asp_utils/                 # existing public utility surface
│   └── pleiades_asp_workflow/
│       ├── workspace.py           # pleiades-workflow-init
│       └── interface_bundle/      # notebook + runtime modules/assets
└── tests/
```

---

## 12. Citation and publication

The associated paper/software citation is intentionally left as a placeholder
in this candidate package and will be added once the final publication reference
is available.

The notebook cover retains the author and affiliation information used by the
research software.

---

## 13. License

This candidate preserves the existing repository `LICENSE` file. Review the
license/citation wording before the public GitHub release if you want to change
the software-protection terms.
