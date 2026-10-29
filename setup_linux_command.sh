#!/usr/bin/env bash
# Create/refresh a terminal command using the installed environment directly.
set -euo pipefail
python_exe="${WASA_DEM_PYTHON:?Set WASA_DEM_PYTHON to the installed environment Python executable}"
[[ -x "$python_exe" ]] || { echo "WASA-DEM Python missing: $python_exe" >&2; exit 1; }
jupyter="$(dirname "$python_exe")/jupyter"
[[ -x "$jupyter" ]] || { echo "JupyterLab missing: $jupyter" >&2; exit 1; }
mkdir -p "$HOME/.local/bin"
printf -v exe_q '%q' "$jupyter"
cat > "$HOME/.local/bin/wasa-dem" <<EOF
#!/usr/bin/env bash
exec $exe_q lab "\$@"
EOF
chmod 755 "$HOME/.local/bin/wasa-dem"
echo "[OK] WASA-DEM terminal launcher: $HOME/.local/bin/wasa-dem"
