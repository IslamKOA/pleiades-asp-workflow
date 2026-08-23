from pathlib import Path
import re
ROOT = Path(__file__).resolve().parents[1]

def test_rpcm_is_core_dependency():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    core = text.split("[project.optional-dependencies]", 1)[0]
    assert '"rpcm==1.4.10"' in core

def test_rpcm_is_not_optional_extra():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    optional = text.split("[project.optional-dependencies]", 1)[1]
    assert not re.search(r"^rpc\s*=", optional, flags=re.MULTILINE)

def test_interface_requirements_include_pinned_rpcm():
    p = ROOT / "src/pleiades_asp_workflow/interface_bundle/requirements_interface.txt"
    assert "rpcm==1.4.10" in p.read_text(encoding="utf-8").splitlines()
