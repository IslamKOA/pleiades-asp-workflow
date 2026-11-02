#!/usr/bin/env bash
# Linux update: refresh package and managed workspace files; preserve user data and ASP.
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
workspace="${WASA_DEM_WORKSPACE:-$HOME/Pleiades_ASP_Workflow}"
initializer="$(dirname "$python_exe")/pleiades-workflow-init"
[[ -x "$initializer" ]] || { echo "[FAIL] Workspace initializer missing: $initializer" >&2; exit 1; }
if [[ -d "$workspace" ]]; then
  backup="$workspace/notebook_backups/before-update-$(date +%Y%m%d-%H%M%S)"
  for relative in "Pleiades_ASP_Workflow.ipynb" "coregistration/Coregistration_Process-Final-withPlannimetric.ipynb" "point_density/08_Optional_LAZ_Export_and_Point_Density.ipynb"; do
    if [[ -f "$workspace/$relative" ]]; then
      mkdir -p "$(dirname "$backup/$relative")"
      cp -p "$workspace/$relative" "$backup/$relative"
    fi
  done
  echo "[OK] Previous notebooks backed up under: $backup"
fi
echo "WASA-DEM: refreshing package-managed notebooks and Python resources..."
"$initializer" "$workspace"
[[ -f "$workspace/Pleiades_ASP_Workflow.ipynb" ]] || { echo "[FAIL] Main notebook missing after workspace refresh." >&2; exit 1; }
echo "[OK] Package-managed notebooks and interface resources refreshed."

launcher_helper="$(mktemp)"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL https://raw.githubusercontent.com/IslamKOA/pleiades-asp-workflow/main/setup_linux_command.sh -o "$launcher_helper"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$launcher_helper" https://raw.githubusercontent.com/IslamKOA/pleiades-asp-workflow/main/setup_linux_command.sh
else
  echo "WASA-DEM: curl or wget needed for launcher" >&2
fi
if [[ -s "$launcher_helper" ]]; then
  WASA_DEM_PYTHON="$python_exe" bash "$launcher_helper"
fi
rm -f "$launcher_helper"
[[ -x "$HOME/.local/bin/wasa-dem" ]] || { echo "[FAIL] WASA-DEM launcher was not created." >&2; exit 1; }
echo "[OK] WASA-DEM update complete; package, notebooks, and launcher refreshed."
echo "Launch WASA-DEM with: $HOME/.local/bin/wasa-dem"
