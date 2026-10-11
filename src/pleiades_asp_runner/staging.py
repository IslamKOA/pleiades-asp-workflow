
"""Windows-only native WSL staging for ASP stereo processing.

Keep the scientific parameters unchanged. Run stereo in native WSL
storage and copy completed results to the original Windows folder.
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
    """Return a project-specific native WSL workspace."""
    if not is_windows():
        raise RuntimeError("WSL staging is available only on Windows")

    project = Path(project_dir).expanduser().resolve()
    token = hashlib.sha256(
        str(project).casefold().encode("utf-8")
    ).hexdigest()[:16]

    return f"{_wsl_home()}/.pleiades-asp-runner/workspaces/{token}"


def ensure_wsl_workspace(project_dir: str | Path) -> str:
    workspace = wsl_workspace(project_dir)

    subprocess.run(
        [*_wsl_base(), "--", "mkdir", "-p", workspace],
        check=True,
    )
    return workspace


def _runtime_argument(value: object) -> str:
    """Convert Windows paths while preserving command options."""
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
        """Copy completed WSL stereo artifacts into the Windows project folder."""
        self.destination.mkdir(parents=True, exist_ok=True)

        destination_wsl = windows_to_wsl_path(
            self.destination.resolve()
        )

        # Windows filesystems may not permit Linux symbolic links.
        # Dereference them and copy their actual contents instead.
        subprocess.run(
            [
                *_wsl_base(),
                "--",
                "cp",
                "-rL",
                f"{self.native_directory}/.",
                destination_wsl,
            ],
            check=True,
        )



def stage_stereo_command(
    arguments: list[object],
    project_dir: str | Path,
) -> StereoStage:
    """Prepare the Windows ASP stereo command with native WSL output."""

    if not is_windows():
        raise RuntimeError("Stereo staging is Windows-only")

    if not arguments:
        raise ValueError("parallel_stereo needs an output prefix")

    # For map-projected stereo, ASP receives a reference DEM
    # after the output prefix. The DEM must stay as an input.
    has_reference_dem = (
        len(arguments) >= 2
        and "--alignment-method" in [str(v) for v in arguments]
        and str(arguments[-1]).lower().endswith((".tif", ".tiff", ".vrt"))
    )

    prefix_index = -2 if has_reference_dem else -1

    output_prefix = Path(
        arguments[prefix_index]
    ).expanduser().resolve()

    if not output_prefix.is_absolute() or not output_prefix.name:
        raise ValueError(
            f"An absolute stereo output prefix is required: {output_prefix}"
        )

    workspace = ensure_wsl_workspace(project_dir)

    token = hashlib.sha256(
        str(output_prefix).casefold().encode("utf-8")
    ).hexdigest()[:16]

    native_dir = f"{workspace}/stereo-{token}"

    subprocess.run(
        [*_wsl_base(), "--", "mkdir", "-p", native_dir],
        check=True,
    )

    runtime_args = [
        _runtime_argument(value)
        for value in arguments
    ]

    # Replace only the output prefix, not the reference DEM.
    runtime_args[prefix_index] = (
        f"{native_dir}/{output_prefix.name}"
    )

    environment = [
        f"{key}={value}"
        for key, value in wsl_execution_environment().items()
    ]

    command = [
        *_wsl_base(),
        "--cd",
        native_dir,
        "--",
        "env",
        *environment,
        f"{asp_bin_location()}/parallel_stereo",
        *runtime_args,
    ]

    return StereoStage(
        command=command,
        native_directory=native_dir,
        destination=output_prefix.parent,
    )
