from __future__ import annotations

import bz2
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path, PureWindowsPath

PACKAGE_VERSION = "1.5.4"
DEFAULT_ASP_VERSION = "3.3.0"

REQUIRED_TOOLS = (
    "bundle_adjust",
    "parallel_stereo",
    "point2dem",
    "pc_align",
    "mapproject",
    "cam_test",
    "pc_merge",
    "dem_geoid",
)

# Executables that cover the ASP processing chain used by parallel_stereo
# and the other public tools. These are scanned with ldd on Linux.
RUNTIME_PROBES = (
    "stereo_parse",
    "stereo_pprc",
    "stereo_corr",
    "stereo_rfne",
    "stereo_fltr",
    "stereo_tri",
    "bundle_adjust",
    "point2dem",
    "pc_align",
    "mapproject",
    "cam_test",
)

# Common Linux runtime libraries used by older ASP binary distributions.
# Packages are Debian/Ubuntu runtime package names.
DEBIAN_LIBRARY_PACKAGES = {
    "libXxf86vm.so.1": "libxxf86vm1",
    "libX11.so.6": "libx11-6",
    "libXext.so.6": "libxext6",
    "libXrender.so.1": "libxrender1",
    "libXi.so.6": "libxi6",
    "libXrandr.so.2": "libxrandr2",
    "libXinerama.so.1": "libxinerama1",
    "libXcursor.so.1": "libxcursor1",
    "libXfixes.so.3": "libxfixes3",
    "libSM.so.6": "libsm6",
    "libICE.so.6": "libice6",
    "libGL.so.1": "libgl1",
    "libGLU.so.1": "libglu1-mesa",
    "libxcb.so.1": "libxcb1",
    "libXau.so.6": "libxau6",
    "libXdmcp.so.6": "libxdmcp6",
    "libfontconfig.so.1": "libfontconfig1",
    "libfreetype.so.6": "libfreetype6",
    "libdrm.so.2": "libdrm2",
    "libgbm.so.1": "libgbm1",
    "libexpat.so.1": "libexpat1",
    "libuuid.so.1": "libuuid1",
    "libz.so.1": "zlib1g",
}

_GITHUB_API_TEMPLATE = (
    "https://api.github.com/repos/"
    "NeoGeographyToolkit/StereoPipeline/releases/tags/{version}"
)
_USER_AGENT = f"pleiades-asp-runner/{PACKAGE_VERSION}"
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_MISSING_LIB_RE = re.compile(r"^\s*(\S+)\s+=>\s+not found\s*$", re.MULTILINE)
_ARCH_TOKENS = ("x86_64", "amd64", "aarch64", "arm64")


def is_windows() -> bool:
    return platform.system().lower() == "windows"


def is_linux() -> bool:
    return platform.system().lower() == "linux"


def is_macos() -> bool:
    return platform.system().lower() == "darwin"


def _cache_root() -> Path:
    custom = os.environ.get("PLEIADES_ASP_HOME")
    if custom:
        return Path(custom).expanduser().resolve()
    return Path.home() / ".pleiades-asp-runner"


def _config_path() -> Path:
    return _cache_root() / "config.json"


def _read_config() -> dict:
    path = _config_path()
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Could not read configuration: {path}") from exc


def _write_config(data: dict) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def get_asp_version() -> str:
    env_version = os.environ.get("PLEIADES_ASP_VERSION", "").strip()
    if env_version:
        if not _VERSION_RE.match(env_version):
            raise RuntimeError(
                f"PLEIADES_ASP_VERSION must look like X.Y.Z, got {env_version!r}."
            )
        return env_version

    configured = str(_read_config().get("asp_version", "")).strip()
    if configured:
        if not _VERSION_RE.match(configured):
            raise RuntimeError(f"Invalid configured ASP version: {configured!r}")
        return configured

    return DEFAULT_ASP_VERSION


def set_asp_version(version: str) -> None:
    version = version.strip()
    if not _VERSION_RE.match(version):
        raise ValueError("ASP version must have the form X.Y.Z, e.g. 3.3.0.")

    config = _read_config()
    config["asp_version"] = version
    _write_config(config)


def reset_asp_version() -> None:
    config = _read_config()
    config.pop("asp_version", None)

    if config:
        _write_config(config)
    elif _config_path().exists():
        _config_path().unlink()


def _selected_wsl_distro() -> str | None:
    value = os.environ.get("PLEIADES_ASP_WSL_DISTRO", "").strip()
    return value or None


def _wsl_base(user: str | None = None) -> list[str]:
    cmd = ["wsl.exe"]

    distro = _selected_wsl_distro()
    if distro:
        cmd += ["-d", distro]

    if user:
        cmd += ["-u", user]

    return cmd


def _wsl_prefix(user: str | None = None) -> list[str]:
    return [*_wsl_base(user=user), "--"]


def _run_wsl(
    args: list[str],
    *,
    capture_output: bool = False,
    check: bool = True,
    user: str | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*_wsl_prefix(user=user), *args],
        check=check,
        capture_output=capture_output,
        text=True,
    )


def wsl_label() -> str:
    return _selected_wsl_distro() or "default WSL distribution"


def verify_wsl() -> str:
    try:
        result = _run_wsl(
            ["uname", "-m"],
            capture_output=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "Windows was detected, but a working WSL Linux environment "
            "could not be started. ASP has no native Windows build."
        ) from exc

    arch = result.stdout.strip().lower()

    if arch not in {"x86_64", "amd64", "aarch64", "arm64"}:
        raise RuntimeError(f"Unsupported WSL architecture: {arch}")

    return arch


def windows_to_wsl_path(path: str | Path) -> str:
    raw = str(path)
    win = PureWindowsPath(raw)

    if not win.is_absolute() or not win.drive:
        raise ValueError(f"Expected an absolute Windows drive path: {raw}")

    drive = win.drive

    if drive.startswith("\\\\"):
        raise ValueError(
            "UNC/network paths are not supported yet. "
            "Use a mounted drive such as C:, D:, or G:."
        )

    letter = drive.rstrip(":").lower()

    if len(letter) != 1 or not letter.isalpha():
        raise ValueError(f"Could not determine drive letter from: {raw}")

    parts = list(win.parts)[1:]
    tail = "/".join(
        p.replace("\\", "/").strip("/")
        for p in parts
        if p
    )

    return f"/mnt/{letter}/{tail}" if tail else f"/mnt/{letter}"


def _wsl_home() -> str:
    result = _run_wsl(
        ["sh", "-lc", 'printf "%s" "$HOME"'],
        capture_output=True,
        check=True,
    )
    home = result.stdout.strip()

    if not home.startswith("/"):
        raise RuntimeError(f"Could not determine WSL home: {home!r}")

    return home


def _wsl_path() -> str:
    result = _run_wsl(
        ["sh", "-lc", 'printf "%s" "$PATH"'],
        capture_output=True,
        check=True,
    )
    value = result.stdout.strip()

    return value or (
        "/usr/local/sbin:/usr/local/bin:"
        "/usr/sbin:/usr/bin:/sbin:/bin"
    )


def _apt_get_available_wsl() -> bool:
    """
    Detect the actual package manager instead of relying on /etc/os-release.
    This is intentionally capability-based because WSL images/customizations
    may have incomplete or unusual distro metadata.
    """
    result = _run_wsl(
        ["sh", "-lc", "command -v apt-get >/dev/null 2>&1"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _apt_get_available_native() -> bool:
    return shutil.which("apt-get") is not None


def managed_install_dir(version: str | None = None) -> str:
    version = version or get_asp_version()

    if is_windows():
        return f"{_wsl_home()}/.pleiades-asp-runner/asp/{version}"

    return str(_cache_root() / "asp" / version)


def asp_bin_location(version: str | None = None) -> str:
    return f"{managed_install_dir(version)}/bin"


def _tool_exists(tool: str, version: str | None = None) -> bool:
    executable = f"{asp_bin_location(version)}/{tool}"

    if is_windows():
        result = _run_wsl(
            ["test", "-x", executable],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0

    path = Path(executable)
    return path.exists() and os.access(path, os.X_OK)


def asp_files_present(version: str | None = None) -> bool:
    return all(_tool_exists(tool, version) for tool in REQUIRED_TOOLS)


def _request_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )

    try:
        with urllib.request.urlopen(request) as response:
            return json.load(response)
    except Exception as exc:
        raise RuntimeError(
            f"Could not query the official ASP GitHub release: {url}"
        ) from exc


def _execution_platform() -> tuple[str, str]:
    if is_windows():
        return "linux", verify_wsl()

    machine = platform.machine().lower()

    if is_linux():
        return "linux", machine

    if is_macos():
        return "macos", machine

    raise RuntimeError(
        f"Unsupported host platform: {platform.system()} {platform.machine()}"
    )


def _asset_matches_os(name: str, exec_os: str) -> bool:
    lower = name.lower()

    if exec_os == "linux":
        return "linux" in lower

    if exec_os == "macos":
        return "osx" in lower or "mac" in lower

    return False


def _asset_has_arch_token(name: str) -> bool:
    lower = name.lower()
    return any(token in lower for token in _ARCH_TOKENS)


def _asset_matches_arch(name: str, arch: str) -> bool:
    lower = name.lower()

    if arch in {"x86_64", "amd64"}:
        return "x86_64" in lower or "amd64" in lower

    if arch in {"aarch64", "arm64"}:
        return "aarch64" in lower or "arm64" in lower

    return False


def _target_asset(version: str) -> tuple[str, str]:
    exec_os, arch = _execution_platform()

    api_url = _GITHUB_API_TEMPLATE.format(version=version)
    release = _request_json(api_url)

    assets = [
        asset
        for asset in release.get("assets", [])
        if asset.get("name", "").lower().endswith(".tar.bz2")
    ]

    os_assets = [
        asset
        for asset in assets
        if _asset_matches_os(asset.get("name", ""), exec_os)
    ]

    explicit = [
        asset
        for asset in os_assets
        if _asset_matches_arch(asset.get("name", ""), arch)
    ]

    if len(explicit) == 1:
        asset = explicit[0]
        return asset["name"], asset["browser_download_url"]

    if len(explicit) > 1:
        raise RuntimeError(
            f"Multiple ASP {version} archives matched {exec_os} {arch}: "
            f"{[a.get('name') for a in explicit]}"
        )

    # Legacy ASP releases such as 3.3.0 use one generic Linux/OSX
    # archive without an architecture token. Treat this as x86_64 only.
    generic = [
        asset
        for asset in os_assets
        if not _asset_has_arch_token(asset.get("name", ""))
    ]

    if arch in {"x86_64", "amd64"} and len(generic) == 1:
        asset = generic[0]
        return asset["name"], asset["browser_download_url"]

    available = [a.get("name", "") for a in release.get("assets", [])]

    if arch in {"aarch64", "arm64"} and generic:
        raise RuntimeError(
            f"ASP {version} has only a legacy generic {exec_os} archive, "
            "not an ARM-specific build. Choose an ASP release with ARM support.\n"
            f"Available assets: {available}"
        )

    raise RuntimeError(
        f"No official ASP {version} archive matches {exec_os} {arch}.\n"
        f"Available assets: {available}"
    )


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)

    request = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT},
    )

    with urllib.request.urlopen(request) as response, destination.open("wb") as out:
        total_header = response.headers.get("Content-Length")
        total = (
            int(total_header)
            if total_header and total_header.isdigit()
            else None
        )

        copied = 0
        block = 1024 * 1024

        while True:
            chunk = response.read(block)

            if not chunk:
                break

            out.write(chunk)
            copied += len(chunk)

            if total:
                pct = copied * 100 / total
                print(
                    f"\rDownloading ASP: {pct:5.1f}% "
                    f"({copied / 1024**2:.1f}/{total / 1024**2:.1f} MiB)",
                    end="",
                    flush=True,
                )
            else:
                print(
                    f"\rDownloading ASP: {copied / 1024**2:.1f} MiB",
                    end="",
                    flush=True,
                )

    print()


def _archive_top_folder(archive: Path) -> str:
    with tarfile.open(archive, "r:bz2") as tar:
        while True:
            member = tar.next()

            if member is None:
                break

            name = member.name.strip("/")

            if not name:
                continue

            root = Path(name).parts[0]

            if root:
                return root

    raise RuntimeError("Could not determine ASP archive top-level folder.")


def _stream_bz2_to_tar(archive: Path, command: list[str]) -> None:
    proc = subprocess.Popen(command, stdin=subprocess.PIPE)

    if proc.stdin is None:
        proc.kill()
        raise RuntimeError("Could not open ASP extraction pipe.")

    try:
        with bz2.open(archive, "rb") as source:
            while True:
                chunk = source.read(8 * 1024 * 1024)

                if not chunk:
                    break

                proc.stdin.write(chunk)

        proc.stdin.close()
        code = proc.wait()

    except Exception:
        try:
            proc.stdin.close()
        except Exception:
            pass

        proc.kill()
        proc.wait()
        raise

    if code != 0:
        raise RuntimeError(f"ASP extraction failed with exit code {code}.")


def _validate_files(version: str) -> None:
    missing = [
        tool
        for tool in REQUIRED_TOOLS
        if not _tool_exists(tool, version)
    ]

    if missing:
        raise RuntimeError(
            "ASP extraction finished, but required tools are missing: "
            + ", ".join(missing)
        )


def _find_probe_path_wsl(name: str, version: str) -> str | None:
    root = managed_install_dir(version)

    for candidate in (
        f"{root}/libexec/{name}",
        f"{root}/bin/{name}",
    ):
        result = _run_wsl(
            ["test", "-e", candidate],
            capture_output=True,
            check=False,
        )

        if result.returncode == 0:
            return candidate

    return None


def _find_probe_path_native(name: str, version: str) -> Path | None:
    root = Path(managed_install_dir(version))

    for candidate in (
        root / "libexec" / name,
        root / "bin" / name,
    ):
        if candidate.exists():
            return candidate

    return None


def _missing_linux_libraries(version: str) -> set[str]:
    missing: set[str] = set()

    if is_windows():
        for probe in RUNTIME_PROBES:
            path = _find_probe_path_wsl(probe, version)

            if not path:
                continue

            result = _run_wsl(
                ["ldd", path],
                capture_output=True,
                check=False,
            )

            text = (result.stdout or "") + "\n" + (result.stderr or "")
            missing.update(_MISSING_LIB_RE.findall(text))

        return missing

    if is_linux():
        for probe in RUNTIME_PROBES:
            path = _find_probe_path_native(probe, version)

            if not path:
                continue

            result = subprocess.run(
                ["ldd", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )

            text = (result.stdout or "") + "\n" + (result.stderr or "")
            missing.update(_MISSING_LIB_RE.findall(text))

    return missing


def _packages_for_missing_libraries(missing: set[str]) -> tuple[list[str], list[str]]:
    packages = []
    unknown = []

    for library in sorted(missing):
        package = DEBIAN_LIBRARY_PACKAGES.get(library)

        if package:
            packages.append(package)
        else:
            unknown.append(library)

    return sorted(set(packages)), unknown


def _apt_install_wsl(packages: list[str]) -> None:
    if not packages:
        return

    if not _apt_get_available_wsl():
        raise RuntimeError(
            "ASP is missing Linux runtime libraries, but the selected WSL "
            "distribution does not provide `apt-get`. Automatic dependency "
            "installation currently supports APT-based WSL environments. "
            "Install the required libraries with your distribution's package "
            "manager, or use an Ubuntu/Debian-based WSL distribution."
        )

    if os.environ.get("PLEIADES_ASP_SKIP_SYSTEM_DEPS", "").strip() == "1":
        raise RuntimeError(
            "ASP needs Linux runtime packages, but automatic system dependency "
            "installation was disabled by PLEIADES_ASP_SKIP_SYSTEM_DEPS=1."
        )

    print("Installing required WSL runtime packages:")
    for package in packages:
        print(f"  - {package}")

    # WSL supports running the selected distribution as root. This avoids a
    # sudo password prompt while modifying only the Linux distro's packages.
    _run_wsl(
        ["apt-get", "update"],
        check=True,
        user="root",
    )

    _run_wsl(
        [
            "env",
            "DEBIAN_FRONTEND=noninteractive",
            "apt-get",
            "install",
            "-y",
            "--no-install-recommends",
            *packages,
        ],
        check=True,
        user="root",
    )


def _apt_install_native(packages: list[str]) -> None:
    if not packages:
        return

    if not _apt_get_available_native():
        raise RuntimeError(
            "ASP is missing Linux runtime libraries, but `apt-get` was not "
            "found. Automatic dependency installation currently supports "
            "APT-based Linux environments."
        )

    if os.environ.get("PLEIADES_ASP_SKIP_SYSTEM_DEPS", "").strip() == "1":
        raise RuntimeError(
            "ASP needs Linux runtime packages, but automatic system dependency "
            "installation was disabled by PLEIADES_ASP_SKIP_SYSTEM_DEPS=1."
        )

    print("Installing required Linux runtime packages:")
    for package in packages:
        print(f"  - {package}")

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        prefix: list[str] = []
    else:
        sudo = shutil.which("sudo")

        if not sudo:
            raise RuntimeError(
                "Root access is required to install missing Ubuntu/Debian "
                "runtime packages, and `sudo` was not found."
            )

        prefix = [sudo]

    subprocess.run(
        [*prefix, "apt-get", "update"],
        check=True,
    )

    subprocess.run(
        [
            *prefix,
            "env",
            "DEBIAN_FRONTEND=noninteractive",
            "apt-get",
            "install",
            "-y",
            "--no-install-recommends",
            *packages,
        ],
        check=True,
    )


def _ensure_linux_runtime_dependencies(version: str) -> None:
    if not (is_windows() or is_linux()):
        return

    missing = _missing_linux_libraries(version)

    if not missing:
        return

    packages, unknown = _packages_for_missing_libraries(missing)

    if unknown:
        raise RuntimeError(
            "ASP needs Linux shared libraries that this installer does not yet "
            "know how to install automatically: "
            + ", ".join(unknown)
        )

    if is_windows():
        _apt_install_wsl(packages)
    else:
        _apt_install_native(packages)

    remaining = _missing_linux_libraries(version)

    if remaining:
        raise RuntimeError(
            "Linux runtime dependency installation completed, but these "
            "libraries are still missing: "
            + ", ".join(sorted(remaining))
        )


def _runtime_self_test(version: str) -> None:
    """
    Test the actual ASP Python/C++ chain without requiring the user to run
    parallel_stereo --version manually.
    """
    asp_bin = asp_bin_location(version)
    executable = f"{asp_bin}/parallel_stereo"

    if is_windows():
        path_value = f"{asp_bin}:{_wsl_path()}"

        result = _run_wsl(
            [
                "env",
                f"PATH={path_value}",
                executable,
                "--version",
            ],
            capture_output=True,
            check=False,
        )
    else:
        env = os.environ.copy()
        env["PATH"] = asp_bin + os.pathsep + env.get("PATH", "")

        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    if result.returncode != 0:
        output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()

        raise RuntimeError(
            "ASP files are present, but the ASP runtime self-test failed.\n"
            + output
        )


def _install_in_wsl(
    archive: Path,
    version: str,
    force: bool = False,
) -> str:
    verify_wsl()

    tar_check = _run_wsl(
        ["tar", "--version"],
        capture_output=True,
        check=False,
    )

    if tar_check.returncode != 0:
        raise RuntimeError("The selected WSL environment does not provide `tar`.")

    home = _wsl_home()
    parent = f"{home}/.pleiades-asp-runner/asp"
    target = f"{parent}/{version}"

    extracted_name = _archive_top_folder(archive)
    extracted = f"{parent}/{extracted_name}"

    _run_wsl(["mkdir", "-p", parent])

    if force or not asp_files_present(version):
        _run_wsl(["rm", "-rf", target])

        if extracted != target:
            _run_wsl(["rm", "-rf", extracted])

    print("Extracting ASP inside WSL...")
    print("Python handles bzip2 decompression; Linux bzip2 is not required.")

    _stream_bz2_to_tar(
        archive,
        [
            *_wsl_prefix(),
            "tar",
            "-xf",
            "-",
            "-C",
            parent,
        ],
    )

    if extracted != target:
        _run_wsl(["rm", "-rf", target])
        _run_wsl(["mv", extracted, target])

    _validate_files(version)
    return f"{target}/bin"


def _install_native(
    archive: Path,
    version: str,
    force: bool = False,
) -> str:
    tar_program = shutil.which("tar")

    if not tar_program:
        raise RuntimeError("A system `tar` executable is required on Linux/macOS.")

    parent = _cache_root() / "asp"
    target = parent / version
    parent.mkdir(parents=True, exist_ok=True)

    extracted_name = _archive_top_folder(archive)
    extracted = parent / extracted_name

    if force or not asp_files_present(version):
        if target.exists():
            shutil.rmtree(target)

        if extracted.exists() and extracted != target:
            shutil.rmtree(extracted)

    print("Extracting ASP...")
    print("Python handles bzip2 decompression; external bzip2 is not required.")

    _stream_bz2_to_tar(
        archive,
        [
            tar_program,
            "-xf",
            "-",
            "-C",
            str(parent),
        ],
    )

    if extracted != target:
        if target.exists():
            shutil.rmtree(target)

        extracted.rename(target)

    _validate_files(version)
    return str(target / "bin")


def install_asp(
    force: bool = False,
    version: str | None = None,
) -> str:
    """
    Install and validate one ASP version.

    If version is None, use the currently active ASP version.
    Passing an explicit version does not itself change the active
    configuration; the CLI persists it only after a successful install.
    """
    version = version or get_asp_version()

    if not _VERSION_RE.match(version):
        raise ValueError(
            "ASP version must have the form X.Y.Z, for example 3.3.0."
        )

    if not asp_files_present(version) or force:
        asset_name, url = _target_asset(version)

        print("=" * 60)
        print("Pléiades ASP Runner")
        print("=" * 60)
        print(f"Package     : {PACKAGE_VERSION}")
        print(f"ASP version : {version}")
        print(f"Host        : {platform.system()} {platform.machine()}")

        if is_windows():
            print(f"Execution   : WSL / {wsl_label()}")
            print(f"WSL arch    : {verify_wsl()}")
        else:
            print("Execution   : native")

        print(f"Asset       : {asset_name}")
        print()
        print("Installing the official Ames Stereo Pipeline distribution.")
        print("No imagery, RPCs, DEMs, or project data are downloaded.")
        print()

        archive = _cache_root() / "downloads" / version / asset_name

        if archive.exists():
            print(f"Using cached ASP archive:\n  {archive}")
        else:
            print("Downloading official ASP release...")
            _download(url, archive)

        if is_windows():
            bin_dir = _install_in_wsl(
                archive,
                version=version,
                force=force,
            )
        else:
            bin_dir = _install_native(
                archive,
                version=version,
                force=force,
            )
    else:
        bin_dir = asp_bin_location(version)

    # This is deliberately done even when ASP files already exist, so a
    # partially configured system can repair itself on the next invocation.
    _ensure_linux_runtime_dependencies(version)
    _validate_files(version)
    _runtime_self_test(version)

    print()
    print(f"ASP {version} is ready.")
    print("Verified workflow tools:")
    for tool in REQUIRED_TOOLS:
        print(f"  [OK] {tool}")

    return bin_dir


def wsl_execution_environment() -> dict[str, str]:
    version = get_asp_version()
    asp_bin = asp_bin_location(version)
    existing = _wsl_path()

    return {
        "PATH": f"{asp_bin}:{existing}",
    }


def _install_usage() -> str:
    return (
        "Usage:\n"
        "  asp-install\n"
        "  asp-install X.Y.Z\n\n"
        f"Default ASP version: {DEFAULT_ASP_VERSION}\n\n"
        "Examples:\n"
        "  asp-install\n"
        "  asp-install 3.3.0\n"
        "  asp-install 3.7.0"
    )


def install_cli() -> None:
    """
    Public version-selection and installation interface.

    - asp-install
        Install/verify the currently active ASP version.
        On a fresh setup this is DEFAULT_ASP_VERSION (3.3.0).

    - asp-install X.Y.Z
        Install/verify that official ASP release and, only after the
        installation succeeds, make it the active version used by all
        wrapped ASP commands.
    """
    args = sys.argv[1:]

    if args and args[0] in {"-h", "--help"}:
        print(_install_usage())
        return

    if len(args) > 1:
        raise SystemExit(_install_usage())

    if not args:
        version = get_asp_version()

        if version == DEFAULT_ASP_VERSION:
            print(
                f"Using ASP {version} "
                "(package default unless previously selected)."
            )
        else:
            print(f"Using active ASP version: {version}")

        install_asp(version=version)
        return

    requested_version = args[0].strip()

    if not _VERSION_RE.match(requested_version):
        raise SystemExit(
            "ASP version must have the form X.Y.Z, "
            "for example:\n"
            "  asp-install 3.3.0\n"
            "  asp-install 3.7.0"
        )

    env_version = os.environ.get("PLEIADES_ASP_VERSION", "").strip()

    if env_version and env_version != requested_version:
        raise SystemExit(
            "PLEIADES_ASP_VERSION is currently set to "
            f"{env_version}, which would override the selected version.\n"
            "Unset that environment variable first, then run:\n"
            f"  asp-install {requested_version}"
        )

    previous_version = get_asp_version()

    print(f"Requested ASP version: {requested_version}")

    try:
        install_asp(version=requested_version)
    except Exception:
        print()
        print(
            "ASP installation did not complete successfully. "
            f"The active version remains {previous_version}."
        )
        raise

    # Persist only after the requested release has installed and passed
    # dependency checks plus the ASP runtime self-test.
    set_asp_version(requested_version)

    print()
    print(f"Active ASP version is now {requested_version}.")


def version_cli() -> None:
    """
    Report version state only. Version changes are intentionally handled
    by asp-install so installation and selection cannot get out of sync.
    """
    args = sys.argv[1:]

    if args:
        raise SystemExit(
            "`asp-version` is informational only.\n\n"
            "To select and install an ASP release, use:\n"
            "  asp-install X.Y.Z\n\n"
            "Examples:\n"
            "  asp-install 3.3.0\n"
            "  asp-install 3.7.0"
        )

    current = get_asp_version()

    print(f"Active ASP version : {current}")
    print(f"Package default    : {DEFAULT_ASP_VERSION}")

    if current == DEFAULT_ASP_VERSION:
        print("Status             : using the reproducible package default")
    else:
        print("Status             : user-selected ASP release")


def info_cli() -> None:
    version = get_asp_version()

    print("=" * 60)
    print("Pléiades ASP Runner")
    print("=" * 60)
    print(f"Package version : {PACKAGE_VERSION}")
    print(f"ASP selected    : {version}")
    print(f"ASP default     : {DEFAULT_ASP_VERSION}")
    print(f"Host            : {platform.system()} {platform.machine()}")

    if is_windows():
        try:
            print(f"WSL target      : {wsl_label()}")
            print(f"WSL architecture: {verify_wsl()}")
            print("Execution mode  : Windows Python -> WSL -> Linux ASP")
        except Exception as exc:
            print(f"WSL status      : ERROR: {exc}")
            return
    elif is_linux():
        print("Execution mode  : native Linux")
    elif is_macos():
        print("Execution mode  : native macOS")
    else:
        print("Execution mode  : unsupported")

    print(f"Managed ASP bin : {asp_bin_location(version)}")
    print(
        "ASP files       : present"
        if asp_files_present(version)
        else "ASP files       : not installed"
    )

    if is_windows() or is_linux():
        missing = _missing_linux_libraries(version) if asp_files_present(version) else set()

        if missing:
            print("Runtime status  : missing Linux libraries")
            for library in sorted(missing):
                print(f"  - {library}")
        elif asp_files_present(version):
            print("Runtime status  : shared-library scan OK")


def doctor_cli() -> None:
    version = get_asp_version()

    print("=" * 60)
    print("Pléiades ASP Runner - diagnostics")
    print("=" * 60)
    print(f"Package version : {PACKAGE_VERSION}")
    print(f"ASP selected    : {version}")
    print(f"Host            : {platform.system()} {platform.machine()}")

    if is_windows():
        try:
            print(f"[OK] WSL: {wsl_label()} ({verify_wsl()})")
        except Exception as exc:
            print(f"[FAIL] WSL: {exc}")
            return

        tar_check = _run_wsl(
            ["tar", "--version"],
            capture_output=True,
            check=False,
        )

        if tar_check.returncode != 0:
            print("[FAIL] WSL tar is unavailable.")
            return

        print("[OK] WSL tar available.")

        mapped = windows_to_wsl_path(Path.cwd())
        visible = _run_wsl(
            ["test", "-d", mapped],
            capture_output=True,
            check=False,
        )

        if visible.returncode != 0:
            print(f"[FAIL] Current folder not visible in WSL: {mapped}")
            return

        print(f"[OK] Current folder visible in WSL: {mapped}")

    elif is_linux() or is_macos():
        if not shutil.which("tar"):
            print("[FAIL] system tar not found.")
            return

        print("[OK] system tar available.")

    else:
        print("[FAIL] unsupported operating system.")
        return

    print("[OK] Python bz2 support available.")

    if not asp_files_present(version):
        print(f"[INFO] ASP {version} is not installed yet.")
        return

    if is_windows() or is_linux():
        missing = _missing_linux_libraries(version)

        if missing:
            print("[INFO] Missing Linux libraries detected:")
            for library in sorted(missing):
                package = DEBIAN_LIBRARY_PACKAGES.get(library, "?")
                print(f"  - {library} -> {package}")
            return

    try:
        _runtime_self_test(version)
        print(f"[OK] ASP {version} runtime self-test passed.")
    except Exception as exc:
        print(f"[FAIL] ASP runtime: {exc}")


def clean_managed_data() -> None:
    host_root = _cache_root()

    if host_root.exists():
        shutil.rmtree(host_root)
        print(f"Removed host managed data: {host_root}")
    else:
        print(f"Host managed data already absent: {host_root}")

    if is_windows():
        try:
            home = _wsl_home()
            wsl_root = f"{home}/.pleiades-asp-runner"

            _run_wsl(["rm", "-rf", wsl_root])
            print(f"Removed WSL managed ASP data: {wsl_root}")
        except Exception as exc:
            print(f"Could not remove WSL managed ASP data: {exc}")

    print(
        "System runtime libraries installed through apt are not removed, "
        "because they may be used by other Linux applications."
    )
    print(
        "Project imagery, DEMs, RPC/XML files, notebooks, and outputs "
        "were not touched."
    )


def clean_cli() -> None:
    clean_managed_data()
