"""Static and syntax checks for robust WASA-DEM installation.

These checks deliberately do not install Conda, ASP, or modify any system.
Windows and Linux installation still need live machine validation.
"""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def read(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_linux_installer_is_git_free_and_checks_managed_python():
    script = read("install_linux.sh")
    assert script.startswith("#!/usr/bin/env bash")
    assert "git clone" not in script
    assert "git+https" not in script
    assert "archive/refs/heads/main.zip" in script
    assert 'python -m pip install --upgrade "$SOURCE_URL"' in script
    assert 'python=3.11 pip' in script
    assert "micro.mamba.pm/api/micromamba/" in script
    assert "env list --json" in script
    assert "pleiades-workflow-init" in script


def test_linux_installer_parses_with_bash():
    bash = shutil.which("bash")
    if bash:
        subprocess.run([bash, "-n", str(ROOT / "install_linux.sh")], check=True)


def test_windows_standard_installer_detects_existing_and_missing_managers():
    script = read("install_windows.ps1")
    assert "Find-EnvironmentManager" in script
    assert "Install-MicromambaFallback" in script
    assert "env list --json" in script
    assert "CONDA_EXE" in script
    assert "MAMBA_ROOT_PREFIX" in script
    assert "sys.executable" in script
    assert "micro.mamba.pm/api/micromamba/win-64/latest" in script
    assert "github.com/IslamKOA/pleiades-asp-workflow/archive/refs/heads/main.zip" in script


def test_windows_first_time_only_delegates_to_standard_installer():
    script = read("install_windows_first_time.ps1")
    assert "install_windows.ps1" in script
    assert "powershell.exe" in script
    assert "micromamba.tar.bz2" not in script


def test_legacy_workspace_and_package_names_unchanged():
    pyproject = read("pyproject.toml")
    workspace = read("src/pleiades_asp_workflow/workspace.py")
    assert 'name = "pleiades-asp-workflow"' in pyproject
    assert 'version = "1.5.4"' in pyproject
    assert 'DEFAULT_DIRECTORY = Path.home() / "Pleiades_ASP_Workflow"' in workspace


def test_windows_conda_python_checks_avoid_quoted_inline_code():
    # Conda run on Windows can strip inner Python -c string quotes. Both
    # checks must be passed as filenames rather than quoted Python snippets.
    script = read("install_windows.ps1")
    assert "wasa-dem-python-check-" in script
    assert "verify_wasa_dem_imports.py" in script
    assert "'python', $pythonCheck" in script
    assert "'python', $importCheck" in script
    assert "'python', '-c'" not in script
    assert "Remove-Item -LiteralPath $pythonCheck" in script
    assert "Remove-Item -LiteralPath $tempRoot -Recurse" in script
