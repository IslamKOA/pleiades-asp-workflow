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
# Detect environments installed outside standard home locations, including
# shared/workdir Micromamba roots. Prefer active interpreter, then manager listing.
if [[ -z "$python_exe" || ! -x "$python_exe" ]]; then
  if [[ "${CONDA_DEFAULT_ENV:-}" == "pleiades_asp" ]] && command -v python >/dev/null 2>&1; then
    active_python="$(command -v python)"
    [[ -x "$active_python" ]] && python_exe="$active_python"
  fi
fi
if [[ -z "$python_exe" || ! -x "$python_exe" ]]; then
  for manager in micromamba mamba conda; do
    if command -v "$manager" >/dev/null 2>&1; then
      listing="$("$manager" env list --json 2>/dev/null || true)"
      if [[ -n "$listing" ]]; then
        python_found="$(printf '%s' "$listing" | grep -oE '"[^"]*/envs/pleiades_asp"' | head -n 1 | tr -d '"' || true)"
        if [[ -n "$python_found" && -x "$python_found/bin/python" ]]; then
          python_exe="$python_found/bin/python"
          break
        fi
      fi
    fi
  done
fi
# When environments are stored in workdir or configured through an external
# Mamba root, ask the environment manager itself for the active interpreter.
# This is how install_linux.sh successfully runs the environment as well.
if [[ -z "$python_exe" || ! -x "$python_exe" ]]; then
  for manager in "$HOME/miniconda3/bin/mamba" "$HOME/miniconda3/bin/conda" \
    "$HOME/.local/bin/micromamba" "$HOME/miniforge3/bin/mamba" \
    "$HOME/miniforge3/bin/conda" micromamba mamba conda; do
    if [[ "$manager" == */* ]]; then
      [[ -x "$manager" ]] || continue
    else
      command -v "$manager" >/dev/null 2>&1 || continue
    fi
    candidate="$("$manager" run -n pleiades_asp python -c 'import sys; print(sys.executable)' 2>/dev/null | tail -n 1 || true)"
    if [[ -n "$candidate" && -x "$candidate" && "$candidate" == */envs/pleiades_asp/bin/python ]]; then
      python_exe="$candidate"
      echo "WASA-DEM: existing environment located via $manager: $python_exe"
      break
    fi
  done
fi
[[ -n "$python_exe" && -x "$python_exe" ]] || { echo "WASA-DEM: no usable pleiades_asp environment found. To create it automatically, run the README Linux Install command; to use an external prefix set WASA_DEM_PYTHON=/full/path/bin/python." >&2; exit 1; }
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
