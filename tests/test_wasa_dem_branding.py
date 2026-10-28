"""Regression checks for the WASA-DEM software identity and launcher contract.

These are cross-platform, static checks. Running the desktop shortcut itself
still requires Windows, a configured Python environment, and JupyterLab.
"""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "src" / "pleiades_asp_workflow" / "interface_bundle"


def test_wasa_dem_notebook_brand_and_existing_kernel_import():
    nb = json.loads((BUNDLE / "Pleiades_ASP_Workflow.ipynb").read_text(encoding="utf-8"))
    markdown = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "markdown")
    code = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    assert "WASA-DEM" in markdown
    assert "Workflow for Ames-based Satellite Analysis and Digital Elevation Model Generation" in markdown
    assert "Start WASA-DEM" in markdown
    assert "from pleiades_asp_workflow.interface_bundle import asp_utils2" in code
    assert 'EXPECTED_VERSION = "1.5.4"' in code


def test_shortcut_generator_and_installer_integration():
    creator = (ROOT / "create_wasa_dem_shortcut.ps1").read_text(encoding="utf-8")
    installer = (ROOT / "install_windows.ps1").read_text(encoding="utf-8")
    for token in (
        "Pleiades_ASP_Workflow.ipynb",
        "Launch_WASA-DEM.ps1",
        "WASA-DEM.lnk",
        "DesktopDirectory",
        "MAMBA_ROOT_PREFIX",
        "jupyter lab",
    ):
        assert token in creator
    assert "create_wasa_dem_shortcut.ps1" in installer
    assert "Creating/updating WASA-DEM desktop shortcut" in installer


def test_backward_compatible_names_remain_stable():
    from pleiades_asp_workflow.workspace import DEFAULT_DIRECTORY, MANAGED_ITEMS
    assert DEFAULT_DIRECTORY.name == "Pleiades_ASP_Workflow"
    assert "Pleiades_ASP_Workflow.ipynb" in MANAGED_ITEMS
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "pleiades-asp-workflow"' in metadata
    assert 'version = "1.5.4"' in metadata


def test_official_icon_is_bundled_and_installed_automatically():
    import struct

    icon_file = ROOT / "assets" / "WASA-DEM.ico"
    content = icon_file.read_bytes()
    assert len(content) > 1024
    reserved, image_type, image_count = struct.unpack("<HHH", content[:6])
    assert (reserved, image_type) == (0, 1)
    assert image_count >= 1

    creator = (ROOT / "create_wasa_dem_shortcut.ps1").read_text(encoding="utf-8")
    for marker in (
        "assets\\WASA-DEM.ico",
        "raw.githubusercontent.com/IslamKOA/pleiades-asp-workflow/main/assets/WASA-DEM.ico",
        "Invoke-WebRequest -Uri $iconUrl",
        "Get-FileHash -LiteralPath $sourceIcon",
        "$shortcut.IconLocation",
        "$shortcut.Save()",
        "SHChangeNotify",
    ):
        assert marker in creator

    installer = (ROOT / "install_windows.ps1").read_text(encoding="utf-8")
    assert "create_wasa_dem_shortcut.ps1" in installer
