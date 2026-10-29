#!/usr/bin/env bash
# Create/refresh a terminal command using the installed environment directly.
set -euo pipefail
python_exe="${WASA_DEM_PYTHON:?Set WASA_DEM_PYTHON to the installed environment Python executable}"
[[ -x "$python_exe" ]] || { echo "WASA-DEM Python missing: $python_exe" >&2; exit 1; }
jupyter="$(dirname "$python_exe")/jupyter"
[[ -x "$jupyter" ]] || { echo "JupyterLab missing: $jupyter" >&2; exit 1; }
mkdir -p "$HOME/.local/bin"
printf -v exe_q '%q' "$jupyter"
workspace="${WASA_DEM_WORKSPACE:-$HOME/Pleiades_ASP_Workflow}"
notebook="$workspace/Pleiades_ASP_Workflow.ipynb"
[[ -f "$notebook" ]] || { echo "WASA-DEM notebook missing: $notebook" >&2; exit 1; }
printf -v workspace_q '%q' "$workspace"
printf -v bin_q '%q' "$(dirname "$python_exe")"
cat > "$HOME/.local/bin/wasa-dem" <<EOF
#!/usr/bin/env bash
export PATH=$bin_q:"\$PATH"
exec $exe_q lab --ServerApp.root_dir=$workspace_q --ServerApp.default_url=/lab/tree/Pleiades_ASP_Workflow.ipynb "\$@"
EOF
chmod 755 "$HOME/.local/bin/wasa-dem"
echo "[OK] WASA-DEM terminal launcher: $HOME/.local/bin/wasa-dem"
