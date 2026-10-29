#!/usr/bin/env bash
# WASA-DEM Linux installer (no system Python or Git needed).
# Reuses valid environments; never deletes projects or installed environments.
set -euo pipefail
ENV_NAME="${WASA_DEM_ENV_NAME:-pleiades_asp}"
ASP_VERSION="${PLEIADES_ASP_VERSION:-3.3.0}"
WORKSPACE="${WASA_DEM_WORKSPACE:-$HOME/Pleiades_ASP_Workflow}"
SOURCE_URL="https://github.com/IslamKOA/pleiades-asp-workflow/archive/refs/heads/main.zip"
MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-}"
export MAMBA_ROOT_PREFIX

say() { printf '\n[WASA-DEM] %s\n' "$*"; }
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
for type in micromamba mamba conda; do
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

say "Using $selected_type: $selected"
listing="$("$selected" env list --json)" || fail "Could not list environments using $selected."
if ! printf '%s' "$listing" | grep -Eq "[/]$ENV_NAME\""; then
    say "Creating isolated Python 3.11 environment $ENV_NAME."
    "$selected" create -y -n "$ENV_NAME" -c conda-forge python=3.11 pip
else
    say "Reusing existing $ENV_NAME environment."
fi

"$selected" run -n "$ENV_NAME" python -c \
  'import sys; print("WASA-DEM Python:", sys.executable, sys.version); assert sys.version_info >= (3, 10)' \
  || fail "Managed Python >=3.10 was not found; inspect the environment before updating."

say "Installing WASA-DEM from a ZIP archive; Git is not required."
"$selected" run -n "$ENV_NAME" python -m pip install --upgrade "$SOURCE_URL"
say "Installing/verifying NASA Ames Stereo Pipeline $ASP_VERSION."
"$selected" run -n "$ENV_NAME" asp-install "$ASP_VERSION"
say "Refreshing the existing Jupyter workspace."
"$selected" run -n "$ENV_NAME" pleiades-workflow-init "$WORKSPACE"
[[ -f "$WORKSPACE/Pleiades_ASP_Workflow.ipynb" ]] || fail "Notebook not found in $WORKSPACE."


# Optional graphical Linux shortcut; never required on headless/SSH installations.
if [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]; then
    app_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    bin_dir="$HOME/.local/bin"
    icon_dir="${XDG_DATA_HOME:-$HOME/.local/share}/icons"
    mkdir -p "$app_dir" "$bin_dir" "$icon_dir"
    icon_file="$icon_dir/wasa-dem.jpg"
    if [[ ! -s "$icon_file" ]]; then
        download "https://raw.githubusercontent.com/IslamKOA/pleiades-asp-workflow/main/assets/WASA-DEM-official-logo.jpg" "$icon_file" \
          || { rm -f "$icon_file"; say "Icon download failed; using a generic icon."; }
    fi
    launcher="$bin_dir/wasa-dem-launch"
    printf -v manager_q '%q' "$selected"
    printf -v env_q '%q' "$ENV_NAME"
    printf -v notebook_q '%q' "$WORKSPACE/Pleiades_ASP_Workflow.ipynb"
    printf -v root_q '%q' "$MAMBA_ROOT_PREFIX"
    cat > "$launcher" <<EOF
#!/usr/bin/env bash
set -e
export MAMBA_ROOT_PREFIX=$root_q
exec $manager_q run -n $env_q jupyter lab $notebook_q
EOF
    chmod 755 "$launcher"
    desktop_file="$app_dir/wasa-dem.desktop"
    desktop_exec="${launcher//\\/\\\\}"
    desktop_exec="${desktop_exec//\"/\\\"}"
    icon_value="applications-science"
    [[ -s "$icon_file" ]] && icon_value="$icon_file"
    cat > "$desktop_file" <<EOF
[Desktop Entry]
Type=Application
Name=WASA-DEM
Comment=Satellite stereo DSM workflow with NASA ASP
Exec="$desktop_exec"
Icon=$icon_value
Terminal=true
Categories=Education;Science;Geography;
StartupNotify=true
EOF
    chmod 644 "$desktop_file"
    desktop_dir=""
    if command -v xdg-user-dir >/dev/null 2>&1; then
        desktop_dir="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
    fi
    if [[ -z "$desktop_dir" || "$desktop_dir" == "$HOME" ]]; then
        [[ -d "$HOME/Desktop" ]] && desktop_dir="$HOME/Desktop"
    fi
    if [[ -n "$desktop_dir" && -d "$desktop_dir" ]]; then
        cp "$desktop_file" "$desktop_dir/WASA-DEM.desktop"
        chmod +x "$desktop_dir/WASA-DEM.desktop"
        say "Desktop shortcut: $desktop_dir/WASA-DEM.desktop"
    fi
    say "Linux application launcher: $desktop_file"
    say "Some desktops require right-click > Allow Launching before first use."
fi

say "Installation complete. Launch the interface using:"
printf '  %q run -n %q jupyter lab %q\n' "$selected" "$ENV_NAME" "$WORKSPACE/Pleiades_ASP_Workflow.ipynb"
