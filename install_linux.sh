#!/usr/bin/env bash
# WASA-DEM Linux installer (no system Python or Git needed).
# Reuses valid environments; never deletes projects or installed environments.
set -euo pipefail
# Conda's optional libmamba solver can be incompatible with the installed
# libmambapy. Use the classic solver only for this installer, without changing
# the user's persistent Conda configuration.
export CONDA_NO_PLUGINS=true
export CONDA_SOLVER=classic
ENV_NAME="${WASA_DEM_ENV_NAME:-pleiades_asp}"
ASP_VERSION="${PLEIADES_ASP_VERSION:-3.3.0}"
WORKSPACE="${WASA_DEM_WORKSPACE:-$HOME/Pleiades_ASP_Workflow}"
SOURCE_URL="https://github.com/IslamKOA/pleiades-asp-workflow/archive/refs/heads/main.zip"
MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-}"
export MAMBA_ROOT_PREFIX

say() { printf '\n[WASA-DEM] %s\n' "$*"; }
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  GREEN="$(printf '\033[32m')"
  YELLOW="$(printf '\033[33m')"
  RESET="$(printf '\033[0m')"
else GREEN=""; YELLOW=""; RESET=""; fi
ok() { printf '%s[OK]%s %s\n' "$GREEN" "$RESET" "$*"; }
skip() { printf '%s[SKIP]%s %s\n' "$YELLOW" "$RESET" "$*"; }
fail() { printf 'WASA-DEM installation error: %s\n' "$*" >&2; exit 1; }
[[ "$(uname -s)" == "Linux" ]] || fail "This installer is for Linux. Use install_windows.ps1 on Windows."

download() {
    if command -v curl >/dev/null 2>&1; then
        curl -fLSs --retry 2 "$1" --output "$2"
    elif command -v wget >/dev/null 2>&1; then
        wget -q --tries=3 -O "$2" "$1"
    else
        fail "Neither curl nor wget is available; install one or download the archive manually."
    fi
}

manager_paths=()
manager_types=()
add_manager() {
    local type="$1" path="$2" item
    [[ -n "$path" && -x "$path" && -f "$path" ]] || return 0
    for item in "${manager_paths[@]}"; do [[ "$item" == "$path" ]] && return 0; done
    manager_paths+=("$path")
    manager_types+=("$type")
}
for type in conda micromamba mamba; do
    if command -v "$type" >/dev/null 2>&1; then add_manager "$type" "$(command -v "$type")"; fi
done
[[ -n "${CONDA_EXE:-}" ]] && add_manager conda "$CONDA_EXE"
[[ -n "${MAMBA_EXE:-}" ]] && add_manager mamba "$MAMBA_EXE"
for file in "$HOME/.local/bin/micromamba" "$HOME/micromamba/bin/micromamba" \
    "$HOME/micromamba/micromamba" "$HOME/.local/share/mamba/bin/micromamba"; do
    add_manager micromamba "$file"
done
for prefix in "$HOME/miniforge3" "$HOME/miniconda3" "$HOME/anaconda3" \
    "$HOME/Miniforge3" "$HOME/Miniconda3" "$HOME/Anaconda3" \
    "/opt/conda" "/opt/miniforge3" "/opt/miniconda3"; do
    add_manager conda "$prefix/bin/conda"
    add_manager mamba "$prefix/bin/mamba"
done

# Recover Micromamba root from an existing environment, when available.
if [[ -z "$MAMBA_ROOT_PREFIX" ]]; then
    for root in "$HOME/micromamba" "$HOME/.local/share/mamba" "$HOME/.local/share/micromamba"; do
        if [[ -d "$root/envs/$ENV_NAME" ]]; then MAMBA_ROOT_PREFIX="$root"; break; fi
    done
    MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-$HOME/micromamba}"
    export MAMBA_ROOT_PREFIX
fi

selected=""
selected_type=""
first_usable=""
first_usable_type=""
for i in "${!manager_paths[@]}"; do
    path="${manager_paths[$i]}"
    type="${manager_types[$i]}"
    if listing="$("$path" env list --json 2>/dev/null)"; then
        if [[ -z "$first_usable" ]]; then first_usable="$path"; first_usable_type="$type"; fi
        # Detect a named environment from paths in manager JSON, not system Python.
        if printf '%s' "$listing" | grep -Eq "[/]$ENV_NAME\""; then
            selected="$path"; selected_type="$type"
            say "Found existing $ENV_NAME environment using $type ($path)."
            break
        fi
    else
        printf 'Warning: unusable environment manager: %s\n' "$path" >&2
    fi
done
if [[ -z "$selected" ]]; then selected="$first_usable"; selected_type="$first_usable_type"; fi

if [[ -z "$selected" ]]; then
    say "No usable manager found. Installing standalone Micromamba (no system Python or Git)."
    case "$(uname -m)" in
        x86_64|amd64) platform="linux-64" ;;
        aarch64|arm64) platform="linux-aarch64" ;;
        ppc64le) platform="linux-ppc64le" ;;
        *) fail "Unsupported processor architecture: $(uname -m)" ;;
    esac
    command -v tar >/dev/null 2>&1 || fail "tar is needed to extract Micromamba."
    stage="$(mktemp -d)"
    trap 'rm -rf "$stage"' EXIT
    download "https://micro.mamba.pm/api/micromamba/$platform/latest" "$stage/micromamba.tar.bz2"
    (cd "$stage" && tar -xf micromamba.tar.bz2 bin/micromamba) || fail "Install tar with bzip2 support."
    mkdir -p "$HOME/.local/bin"
    install -m 755 "$stage/bin/micromamba" "$HOME/.local/bin/micromamba"
    selected="$HOME/.local/bin/micromamba"
    selected_type="micromamba"
    "$selected" --version || fail "Micromamba cannot run on this Linux system."
fi

# Derive the environment path from the selected manager's environment listing.
# Avoid parsing the noisy stdout of 'conda run' (broken solver plugins can print warnings).
resolve_prefix() {
  local result path
  result="$("$selected" env list --json 2>/dev/null)" || return 1
  path="$(printf '%s' "$result" | grep -oE '"[^"]*/envs/'"$ENV_NAME"'"' | head -n 1 | tr -d '"' || true)"
  [[ -n "$path" && -x "$path/bin/python" ]] || return 1
  printf '%s' "$path"
}

ok "Environment manager: $selected_type ($selected)"
listing="$("$selected" env list --json)" || fail "Could not list environments using $selected."
if ! printf '%s' "$listing" | grep -Eq "[/]$ENV_NAME\""; then
    say "Creating isolated Python 3.11 environment $ENV_NAME."
    "$selected" create -y -n "$ENV_NAME" -c conda-forge python=3.11 pip
else
    ok "Existing $ENV_NAME environment found; skipping Python environment download."
fi

"$selected" run -n "$ENV_NAME" python -c \
  'import sys; print("WASA-DEM Python:", sys.executable, sys.version); assert sys.version_info >= (3, 10)' \
  || fail "Managed Python >=3.10 was not found; inspect the environment before updating."
ok "Python environment verified."

say "Installing WASA-DEM from GitHub ZIP and checking dependencies (download progress shown below)."
"$selected" run -n "$ENV_NAME" python -m pip install --upgrade "$SOURCE_URL"
ok "WASA-DEM Python package installed."
say "Installing/verifying NASA Ames Stereo Pipeline $ASP_VERSION (cached binaries may be reused)."
"$selected" run -n "$ENV_NAME" asp-install "$ASP_VERSION"
ok "ASP $ASP_VERSION installer check completed."
if [[ ! -f "$WORKSPACE/Pleiades_ASP_Workflow.ipynb" ]]; then
    say "Initializing the new Jupyter workspace."
    "$selected" run -n "$ENV_NAME" pleiades-workflow-init "$WORKSPACE"
elif [[ "${WASA_DEM_REFRESH_NOTEBOOKS:-0}" == "1" ]]; then
    say "Explicitly refreshing package-managed notebooks; user edits may be overwritten."
    "$selected" run -n "$ENV_NAME" pleiades-workflow-init "$WORKSPACE"
else
    skip "Existing notebooks preserved; no overwrite."
fi
[[ -f "$WORKSPACE/Pleiades_ASP_Workflow.ipynb" ]] || fail "Notebook not found in $WORKSPACE."
ok "WASA-DEM notebook available."


# Desktop integration is refreshed for both new installations and reinstalls.
# The helper also works via update_linux.sh without re-running ASP installation.
if [[ -d "$HOME/Desktop" || -d "$HOME/Bureau" || -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]; then
    desktop_helper="$(mktemp)"
    if download "https://raw.githubusercontent.com/IslamKOA/pleiades-asp-workflow/main/setup_linux_desktop.sh" "$desktop_helper"; then
        # Resolve the Python interpreter from the existing managed environment.
        python_path="$(resolve_prefix)/bin/python"
        WASA_DEM_PYTHON="$python_path" WASA_DEM_WORKSPACE="$WORKSPACE" bash "$desktop_helper" \
            || say "Desktop shortcut setup failed; WASA-DEM itself is installed and usable."
    else
        say "Desktop shortcut helper unavailable; WASA-DEM itself is installed."
    fi
    rm -f "$desktop_helper"
fi

# Use the verified environment prefix instead of parsing 'mamba run' output.
resolved_prefix="$(resolve_prefix)" || fail "Could not locate the newly installed environment using $selected. Inspect environment list rather than deleting it."
resolved_python="$resolved_prefix/bin/python"
resolved_jupyter="$resolved_prefix/bin/jupyter"
[[ -x "$resolved_jupyter" ]] || fail "JupyterLab executable missing: $resolved_jupyter"

ok "WASA-DEM installation complete. All required checks passed."
say "To use WASA-DEM in this terminal, run:"
printf '  conda activate %q\n' "$resolved_prefix"
printf '  jupyter lab\n'
say "Activation must be run in your terminal after the installer exits."
say "Or launch directly without activating:"
printf '  %q lab\n' "$resolved_jupyter"
say "On Linux with a graphical browser, double-click WASA-DEM if a shortcut was created."
say "If no local browser exists, Jupyter still starts; open its URL in a browser on a reachable computer."
