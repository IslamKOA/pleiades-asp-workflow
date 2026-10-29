#!/usr/bin/env bash
# Lightweight Linux update: package + shortcut only. ASP, dependencies, notebooks unchanged by default.
set -euo pipefail
python_exe="${WASA_DEM_PYTHON:-}"
if [[ -z "$python_exe" && "${CONDA_DEFAULT_ENV:-}" == "pleiades_asp" ]]; then
    python_exe="$CONDA_PREFIX/bin/python"
fi
if [[ -z "$python_exe" ]]; then
  for p in "$HOME/miniconda3/envs/pleiades_asp/bin/python" "$HOME/miniforge3/envs/pleiades_asp/bin/python" "$HOME/micromamba/envs/pleiades_asp/bin/python" "$HOME/.local/share/mamba/envs/pleiades_asp/bin/python"; do
    if [[ -x "$p" ]]; then python_exe="$p"; break; fi
  done
fi
[[ -n "$python_exe" && -x "$python_exe" ]] || { echo "WASA-DEM: pleiades_asp Python not found. Activate the environment or set WASA_DEM_PYTHON." >&2; exit 1; }
echo "WASA-DEM: updating Python package without reinstalling dependencies or ASP."
"$python_exe" -m pip install --upgrade --force-reinstall --no-deps   "https://github.com/IslamKOA/pleiades-asp-workflow/archive/refs/heads/main.zip"
if [[ "${1:-}" == "--refresh-notebooks" ]]; then
    workspace="${WASA_DEM_WORKSPACE:-$HOME/Pleiades_ASP_Workflow}"
    echo "WASA-DEM: refreshing managed notebooks (back up custom edits first): $workspace"
    "$(dirname "$python_exe")/pleiades-workflow-init" "$workspace"
fi
helper="$(mktemp)"
trap 'rm -f "$helper"' EXIT
url="https://raw.githubusercontent.com/IslamKOA/pleiades-asp-workflow/main/setup_linux_desktop.sh"
if command -v curl >/dev/null 2>&1; then curl -fLsS "$url" -o "$helper"
elif command -v wget >/dev/null 2>&1; then wget -q -O "$helper" "$url"
else echo "WASA-DEM: curl or wget needed for shortcut setup" >&2; exit 1; fi
WASA_DEM_PYTHON="$python_exe" bash "$helper"
echo "WASA-DEM: lightweight update complete."
