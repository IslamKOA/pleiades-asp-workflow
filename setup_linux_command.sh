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
# Matches the verified remote-server launch: separate port, no browser on server,
# and the notebook URL explicitly configured for Jupyter Server and JupyterLab.
# Override the port if required: WASA_DEM_PORT=8891 ~/.local/bin/wasa-dem
port="\${WASA_DEM_PORT:-8890}"
case "\$port" in
  *[!0-9]*|"") echo "WASA-DEM: invalid WASA_DEM_PORT: \$port" >&2; exit 2 ;;
esac
exec $exe_q lab --no-browser --port="\$port" --ServerApp.root_dir=$workspace_q --ServerApp.default_url=/lab/tree/Pleiades_ASP_Workflow.ipynb --LabApp.default_url=/lab/tree/Pleiades_ASP_Workflow.ipynb "\$@"
EOF
chmod 755 "$HOME/.local/bin/wasa-dem"

# Ensure the short command is available in future Bash sessions.
# Idempotent: do not append duplicate PATH entries.
path_line='export PATH="$HOME/.local/bin:$PATH"'
for rc in "$HOME/.bashrc" "$HOME/.profile"; do
  [[ -f "$rc" ]] || touch "$rc"
  if ! grep -Fqx "$path_line" "$rc"; then
    printf '\n# WASA-DEM command\n%s\n' "$path_line" >> "$rc"
  fi
done
if ! grep -Fq -- "--LabApp.default_url=/lab/tree/Pleiades_ASP_Workflow.ipynb" "$HOME/.local/bin/wasa-dem"; then
  echo "[FAIL] WASA-DEM launcher did not configure direct notebook opening." >&2
  exit 1
fi
echo "[OK] WASA-DEM terminal launcher: $HOME/.local/bin/wasa-dem"
echo "[OK] Launcher will open Pleiades_ASP_Workflow.ipynb directly."
echo "[OK] wasa-dem command configured for new Bash sessions."
echo "[INFO] In this existing terminal, run: export PATH=\"\$HOME/.local/bin:\$PATH\""
