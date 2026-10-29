"""WASA-DEM companion tool routing without altering selected ASP version."""

from pleiades_asp_runner import cli


def test_point2las_selects_companion_version_for_older_asp(monkeypatch):
    monkeypatch.setattr(cli, "get_asp_version", lambda: "3.3.0")
    assert cli.asp_version_for_command("point2las") == "3.5.0"
    assert cli.asp_version_for_command("asp-pdal") == "3.5.0"
    assert cli.asp_version_for_command("bundle_adjust") == "3.3.0"
    monkeypatch.setattr(cli, "get_asp_version", lambda: "3.4.0")
    assert cli.asp_version_for_command("point2las") == "3.5.0"
    monkeypatch.setattr(cli, "get_asp_version", lambda: "3.5.0")
    assert cli.asp_version_for_command("point2las") == "3.5.0"
    monkeypatch.setattr(cli, "get_asp_version", lambda: "3.7.0")
    assert cli.asp_version_for_command("point2las") == "3.7.0"


def test_companion_installs_without_mutating_main_version(monkeypatch):
    monkeypatch.setattr(cli, "get_asp_version", lambda: "3.3.0")
    calls = []
    present = {"value": False}

    def tool_exists(name, version):
        calls.append(("exists", name, version))
        return present["value"]

    def install(*, version=None):
        calls.append(("install", version))
        present["value"] = True
        return "/some/managed/asp/bin"

    monkeypatch.setattr(cli, "_tool_exists", tool_exists)
    monkeypatch.setattr(cli, "install_asp", install)
    monkeypatch.setattr(cli, "asp_bin_location", lambda version: f"/managed/asp/{version}/bin")
    asp_bin, actual_binary = cli.command_executable("point2las")
    assert (asp_bin, actual_binary) == ("/managed/asp/3.5.0/bin", "point2las")
    assert ("install", "3.5.0") in calls
    assert cli.get_asp_version() == "3.3.0"
    assert cli.command_executable("bundle_adjust") == ("/some/managed/asp/bin", "bundle_adjust")


def test_companion_is_reused_when_already_present(monkeypatch):
    monkeypatch.setattr(cli, "get_asp_version", lambda: "3.4.0")
    monkeypatch.setattr(cli, "_tool_exists", lambda cmd, ver: True)
    monkeypatch.setattr(cli, "install_asp", lambda **kw: (_ for _ in ()).throw(AssertionError("Unexpected download")))
    monkeypatch.setattr(cli, "asp_bin_location", lambda version: f"/managed/{version}/bin")
    assert cli.command_executable("point2las") == ("/managed/3.5.0/bin", "point2las")
    assert cli.command_executable("asp-pdal") == ("/managed/3.5.0/bin", "pdal")


def test_cli_scripts_and_notebook_use_companion():
    from pathlib import Path
    import json
    root = Path(__file__).resolve().parents[1]
    project = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'point2las = "pleiades_asp_runner.cli:main"' in project
    assert 'asp-pdal = "pleiades_asp_runner.cli:main"' in project
    nb = json.loads((root / "src/pleiades_asp_workflow/interface_bundle/point_density/08_Optional_LAZ_Export_and_Point_Density.ipynb").read_text(encoding="utf-8"))
    sources = "\n".join("".join(c["source"]) for c in nb["cells"])
    assert "asp-pdal" in sources
    assert "point2las" in sources
    assert "USE_MICROMAMBA = False" in sources


def test_laz_export_notebook_explains_asp_35_companion():
    """Explain why LAZ export can run ASP 3.5 while stereo stays on 3.3/3.4."""
    from pathlib import Path
    import json

    root = Path(__file__).resolve().parents[1]
    notebook_path = root / "src/pleiades_asp_workflow/interface_bundle/point_density/08_Optional_LAZ_Export_and_Point_Density.ipynb"
    notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
    source = "\n".join("".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "markdown")
    assert "## 5. Export point clouds to LAZ" in source
    assert "ASP 3.3.0 or 3.4.0" in source
    assert "automatically uses ASP 3.5.0" in source
    assert "--save-triangulation-error" in source
    assert "main ASP version" in source

    instructions = (root / "src/pleiades_asp_workflow/interface_bundle/point_density/README.md").read_text(encoding="utf-8")
    assert "separate ASP 3.5.0 companion" in instructions
    assert "parallel_stereo" in instructions
