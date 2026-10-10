"""Windows-only native WSL workspace management for ASP.

The workflow interface and scientific processing parameters remain unchanged.
Linux execution does not use this module.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from .installer import (
    _wsl_base,
    is_windows,
    windows_to_wsl_path,
)


def wsl_workspace(project_dir: str | Path) -> str:
    """Return a stable, project-specific native WSL workspace path."""
    if not is_windows():
        raise RuntimeError("WSL staging is only available on Windows.")

    project = Path(project_dir).expanduser().resolve()
    project_id = hashlib.sha256(
        str(project).lower().encode("utf-8")
    ).hexdigest()[:16]

    result = subprocess.run(
        [*_wsl_base(), "--", "sh", "-lc", 'printf "%s" "$HOME"'],
        capture_output=True,
        text=True,
        check=True,
    )
    home = result.stdout.strip()

    if not home.startswith("/"):
        raise RuntimeError("Could not determine WSL home directory.")

    return f"{home}/.pleiades-asp-runner/workspaces/{project_id}"


def ensure_wsl_workspace(project_dir: str | Path) -> str:
    """Create a native Linux workspace without changing Windows folders."""
    workspace = wsl_workspace(project_dir)

    subprocess.run(
        [*_wsl_base(), "--", "mkdir", "-p", workspace],
        check=True,
    )

    return workspace
