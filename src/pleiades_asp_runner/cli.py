from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from .installer import (
    REQUIRED_TOOLS,
    _wsl_base,
    install_asp,
    is_windows,
    windows_to_wsl_path,
    wsl_execution_environment,
)

_ALLOWED = set(REQUIRED_TOOLS)

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
    asp_bin = install_asp()
    executable = f"{asp_bin}/{command}"
    wsl_cwd = windows_to_wsl_path(Path.cwd())

    converted_args = [
        _convert_windows_argument(arg)
        for arg in sys.argv[1:]
    ]

    env_args = [
        f"{key}={value}"
        for key, value in wsl_execution_environment().items()
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
    asp_bin = Path(install_asp())
    executable = asp_bin / command

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
