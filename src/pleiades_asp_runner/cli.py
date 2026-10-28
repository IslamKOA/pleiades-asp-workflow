from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from .installer import (
    REQUIRED_TOOLS,
    _wsl_base,
    _tool_exists,
    asp_bin_location,
    get_asp_version,
    install_asp,
    is_windows,
    windows_to_wsl_path,
    wsl_execution_environment,
)

_ALLOWED = set(REQUIRED_TOOLS) | {"point2las", "asp-pdal"}
MIN_LAS_ASP_VERSION = (3, 5, 0)
LAS_COMPANION_VERSION = "3.5.0"


def asp_version_for_command(command: str) -> str:
    """Return the selected ASP release, except for newer LAS/PDAL tools."""
    selected = get_asp_version()
    if command in {"point2las", "asp-pdal"}:
        parsed = tuple(int(x) for x in selected.split("."))
        if parsed < MIN_LAS_ASP_VERSION:
            return LAS_COMPANION_VERSION
    return selected


def command_executable(command: str) -> tuple[str, str]:
    """Choose the executable and lazily install a separate ASP 3.5 companion.

    This does not call set_asp_version(), so bundle_adjust/stereo/point2dem
    keep the user's existing ASP release and science defaults intact.
    """
    # Keep all existing core ASP commands on their previously validated
    # install_asp() pathway. Only LAZ utilities use a separate companion.
    if command not in {"point2las", "asp-pdal"}:
        return install_asp(), command
    version = asp_version_for_command(command)
    binary = "pdal" if command == "asp-pdal" else command
    if not _tool_exists(binary, version):
        install_asp(version=version)
    if not _tool_exists(binary, version):
        raise RuntimeError(
            f"{binary} is not available in ASP {version}. "
            "For PDAL, install the PDAL command-line tool separately if it is "
            "not bundled in the selected ASP distribution."
        )
    return asp_bin_location(version), binary

_WINDOWS_ABS = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_EQUALS_PATH = re.compile(
    r"^(?P<key>--?[^=]+)=(?P<path>[A-Za-z]:[\\/].+)$"
)


def _command_name() -> str:
    command = Path(sys.argv[0]).name

    if command.lower().endswith(".exe"):
        command = command[:-4]

    if command not in _ALLOWED:
        raise SystemExit(
            f"Unsupported ASP wrapper: {command}. "
            f"Allowed: {', '.join(sorted(_ALLOWED))}"
        )

    return command


def _convert_windows_argument(arg: str) -> str:
    if _WINDOWS_ABS.match(arg):
        return windows_to_wsl_path(arg)

    match = _WINDOWS_EQUALS_PATH.match(arg)

    if match:
        converted = windows_to_wsl_path(match.group("path"))
        return f"{match.group('key')}={converted}"

    return arg


def _run_windows(command: str) -> int:
    asp_bin, binary = command_executable(command)
    executable = f"{asp_bin}/{binary}"
    wsl_cwd = windows_to_wsl_path(Path.cwd())

    converted_args = [
        _convert_windows_argument(arg)
        for arg in sys.argv[1:]
    ]

    env_args = [
        f"{key}={value}"
        for key, value in wsl_execution_environment(asp_version_for_command(command)).items()
    ]

    cmd = [
        *_wsl_base(),
        "--cd",
        wsl_cwd,
        "--",
        "env",
        *env_args,
        executable,
        *converted_args,
    ]

    completed = subprocess.run(cmd)
    return completed.returncode


def _run_native(command: str) -> int:
    asp_bin_string, binary = command_executable(command)
    asp_bin = Path(asp_bin_string)
    executable = asp_bin / binary

    if not executable.exists():
        raise SystemExit(
            f"ASP executable '{command}' was not found in {asp_bin}"
        )

    env = os.environ.copy()
    env["PATH"] = str(asp_bin) + os.pathsep + env.get("PATH", "")

    completed = subprocess.run(
        [str(executable), *sys.argv[1:]],
        env=env,
    )

    return completed.returncode


def main() -> None:
    command = _command_name()

    code = (
        _run_windows(command)
        if is_windows()
        else _run_native(command)
    )

    raise SystemExit(code)
