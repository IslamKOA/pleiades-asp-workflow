#!/usr/bin/env bash
# Refresh Linux desktop integration without replacing user data, Conda, or ASP.
set -euo pipefail
workspace="$HOME/Pleiades_ASP_Workflow"
if [[ -n "${WASA_DEM_WORKSPACE:-}" ]]; then workspace="$WASA_DEM_WORKSPACE"; fi
notebook="$workspace/Pleiades_ASP_Workflow.ipynb"
python_exe="${WASA_DEM_PYTHON:-}"
if [[ -z "$python_exe" && "${CONDA_DEFAULT_ENV:-}" == "pleiades_asp" ]]; then
  python_exe="$CONDA_PREFIX/bin/python"
fi
if [[ -z "$python_exe" ]]; then
  for p in "$HOME/miniconda3/envs/pleiades_asp/bin/python" "$HOME/miniforge3/envs/pleiades_asp/bin/python" "$HOME/micromamba/envs/pleiades_asp/bin/python" "$HOME/.local/share/mamba/envs/pleiades_asp/bin/python" "$HOME/anaconda3/envs/pleiades_asp/bin/python"; do
    if [[ -x "$p" ]]; then python_exe="$p"; break; fi
  done
fi
[[ -n "$python_exe" && -x "$python_exe" ]] || { echo "WASA-DEM: pleiades_asp Python not found; set WASA_DEM_PYTHON=/path/to/python" >&2; exit 1; }
jupyter="$(dirname "$python_exe")/jupyter"
[[ -x "$jupyter" ]] || { echo "WASA-DEM: missing Jupyter: $jupyter" >&2; exit 1; }
[[ -f "$notebook" ]] || { echo "WASA-DEM: notebook missing: $notebook; initialize workspace after backing up custom notebooks." >&2; exit 1; }
app_dir="$HOME/.local/share/applications"
icon_dir="$HOME/.local/share/icons"
bin_dir="$HOME/.local/bin"
mkdir -p "$app_dir" "$icon_dir" "$bin_dir"
icon="$icon_dir/wasa-dem.jpg"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
url="https://raw.githubusercontent.com/IslamKOA/pleiades-asp-workflow/main/assets/WASA-DEM-official-logo.jpg"
if command -v curl >/dev/null 2>&1 && curl -fLsS --retry 2 "$url" -o "$tmp"; then
  cp -f "$tmp" "$icon"
elif command -v wget >/dev/null 2>&1 && wget -q -O "$tmp" "$url"; then
  cp -f "$tmp" "$icon"
else
  echo "WASA-DEM: Could not refresh logo; keeping existing icon or using a fallback." >&2
fi
launcher="$bin_dir/wasa-dem-launch"
printf -v jupyter_q '%q' "$jupyter"
printf -v notebook_q '%q' "$notebook"
cat > "$launcher" <<EOF
#!/usr/bin/env bash
set -e
exec $jupyter_q lab $notebook_q
EOF
chmod 755 "$launcher"
icon_value="applications-science"
[[ -s "$icon" ]] && icon_value="$icon"
entry="$app_dir/wasa-dem.desktop"
cat > "$entry" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=WASA-DEM
Comment=Satellite stereo DSM workflow
Exec="$launcher"
Icon=$icon_value
Terminal=true
Categories=Science;Education;Geography;
StartupNotify=false
EOF
chmod 644 "$entry"
desktop_dir=""
if command -v xdg-user-dir >/dev/null 2>&1; then desktop_dir="$(xdg-user-dir DESKTOP 2>/dev/null || true)"; fi
if [[ -z "$desktop_dir" || "$desktop_dir" == "$HOME" ]]; then
  if [[ -d "$HOME/Desktop" ]]; then desktop_dir="$HOME/Desktop";
  elif [[ -d "$HOME/Bureau" ]]; then desktop_dir="$HOME/Bureau"; fi
fi
if [[ -n "$desktop_dir" && -d "$desktop_dir" ]]; then
  cp -f "$entry" "$desktop_dir/WASA-DEM.desktop"
  chmod 755 "$desktop_dir/WASA-DEM.desktop"
  echo "WASA-DEM: Desktop icon refreshed at $desktop_dir/WASA-DEM.desktop"
else
  echo "WASA-DEM: Desktop folder not detected; application-menu shortcut installed."
fi
echo "WASA-DEM: Application menu entry refreshed at $entry"
echo "WASA-DEM: XFCE startup notification disabled to avoid launcher timeouts during Jupyter startup."
echo "On some desktops: right-click icon > Allow Launching."
