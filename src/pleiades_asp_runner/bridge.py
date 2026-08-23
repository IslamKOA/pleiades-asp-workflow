"""Public cross-platform shell/path bridge for workflow packages.

The runner owns the Windows -> WSL execution boundary. Scientific workflow
packages can use these functions without duplicating WSL handling.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .installer import is_windows, windows_to_wsl_path, _wsl_base

_WSL_MOUNT_RE = re.compile(r"^/mnt/(?P<drive>[A-Za-z])(?:/(?P<tail>.*))?$")


def host_to_runtime_path(path: str | Path) -> str:
    """Convert a host path to the path syntax used by the runtime shell.

    On Windows this converts an absolute drive path to its WSL /mnt path.
    On Linux/macOS it returns an absolute native path.
    """
    host = Path(path).expanduser()
    if not host.is_absolute():
        host = (Path.cwd() / host).resolve()
    else:
        host = host.resolve()

    if is_windows():
        return windows_to_wsl_path(host)
    return str(host)


def runtime_to_host_path(path: str | Path) -> str:
    """Convert a WSL-mounted drive path back to a Windows host path.

    Relative paths and already-native paths are returned unchanged. WSL paths
    under /mnt/<drive>/ are converted to <DRIVE>:\\... on Windows.
    """
    raw = str(path).strip()
    if not is_windows():
        return raw

    match = _WSL_MOUNT_RE.match(raw.replace('\\\\', '/'))
    if not match:
        return raw

    drive = match.group('drive').upper()
    tail = (match.group('tail') or '').replace('/', '\\\\')
    return f"{drive}:\\\\{tail}" if tail else f"{drive}:\\\\"


def run_bash(
    command: str,
    *,
    cwd: str | Path | None = None,
    capture_output: bool = False,
    text: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run a Bash command in the runner's execution environment.

    Windows: execute through the selected/default WSL distribution.
    Linux/macOS: execute with native Bash.
    """
    workdir = Path.cwd() if cwd is None else Path(cwd)

    if is_windows():
        runtime_cwd = host_to_runtime_path(workdir)
        cmd = [
            *_wsl_base(),
            '--cd', runtime_cwd,
            '--',
            'bash', '-lc', command,
        ]
        return subprocess.run(
            cmd,
            capture_output=capture_output,
            text=text,
            check=check,
        )

    return subprocess.run(
        ['bash', '-lc', command],
        cwd=str(workdir),
        capture_output=capture_output,
        text=text,
        check=check,
    )
