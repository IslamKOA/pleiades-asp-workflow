"""Windows-only native WSL staging for ASP stereo output.

ASP imagery, RPCs, bundle-adjustment inputs and processing options stay
unchanged. Stereo's output prefix alone is redirected to native WSL storage.
Completed artifacts are copied into the existing Windows output folder.
Linux runs do not import or use this module.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .installer import (
    _wsl_base,
    asp_bin_location,
    is_windows,
    windows_to_wsl_path,
    wsl_execution_environment,
)

_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


def _wsl_home() -> str:
    result = subprocess.run(
        [*_wsl_base(), "--", "sh", "-lc", 'printf "%s" "$HOME"'],
        check=True,
        capture_output=True,
        text=True,
    )
    home = result.stdout.strip()
    if not home.startswith("/"):
        raise RuntimeError("Could not determine native WSL home directory")
    return home


def wsl_workspace(project_dir: str | Path) -> str:
    """Return a stable native-WSL workspace for this Windows project."""
    if not is_windows():
        raise RuntimeError("WSL staging is available only on Windows")
    project = Path(project_dir).expanduser().resolve()
    token = hashlib.sha256(str(project).casefold().encode("utf-8")).hexdigest()[:16]
    return f"{_wsl_home()}/.pleiades-asp-runner/workspaces/{token}"


def ensure_wsl_workspace(project_dir: str | Path) -> str:
    workspace = wsl_workspace(project_dir)
    subprocess.run([*_wsl_base(), "--", "mkdir", "-p", workspace], check=True)
    return workspace


def _runtime_argument(value: object) -> str:
    """Convert an absolute Windows path, leaving ASP options unchanged."""
    raw = str(value)
    if _WINDOWS_ABSOLUTE.match(raw):
        return windows_to_wsl_path(raw)
    return raw


@dataclass
class StereoStage:
    command: list[str]
    native_directory: str
    destination: Path

    def copy_results(self) -> None:
        """Copy all completed ASP stereo artifacts to the designed host folder."""
        self.destination.mkdir(parents=True, exist_ok=True)
        destination_wsl = windows_to_wsl_path(self.destination.resolve())
        subprocess.run(
            [*_wsl_base(), "--", "cp", "-a", f"{self.native_directory}/.", destination_wsl],
            check=True,
        )


def stage_stereo_command(arguments: list[object], project_dir: str | Path) -> StereoStage:
    """Build a parallel_stereo command using native-WSL output storage.

    The final positional argument is the ASP output prefix. All other
    arguments (including numerical settings and external camera/BA paths)
    are retained in the same order. A new run clears only its private native
    stereo staging directory so old partial files cannot contaminate results.
    """
    if not is_windows():
        raise RuntimeError("Stereo staging must only be called on Windows")
    if not arguments:
        raise ValueError("parallel_stereo needs an output prefix")

    output_prefix = Path(arguments[-1]).expanduser().resolve()
    if not output_prefix.is_absolute() or not output_prefix.name:
        raise ValueError(f"An absolute stereo output prefix is required: {output_prefix}")

    workspace = ensure_wsl_workspace(project_dir)
    token = hashlib.sha256(str(output_prefix).casefold().encode("utf-8")).hexdigest()[:16]
    native_dir = f"{workspace}/stereo-{token}"

    # This folder is exclusively owned by this staging helper. Do not touch
    # any Windows project folder or any other native WSL workspace.
    subprocess.run([*_wsl_base(), "--", "rm", "-rf", "--", native_dir], check=True)
    subprocess.run([*_wsl_base(), "--", "mkdir", "-p", native_dir], check=True)

    runtime_args = [_runtime_argument(value) for value in arguments[:-1]]
    runtime_args.append(f"{native_dir}/{output_prefix.name}")
    environment = [f"{key}={value}" for key, value in wsl_execution_environment().items()]
    command = [
        *_wsl_base(), "--cd", native_dir, "--", "env", *environment,
        f"{asp_bin_location()}/parallel_stereo", *runtime_args,
    ]
    return StereoStage(command=command, native_directory=native_dir, destination=output_prefix.parent)
