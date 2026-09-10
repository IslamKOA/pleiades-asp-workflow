from pathlib import Path

UI = Path("src/pleiades_asp_workflow/interface_bundle/asp_utils2.py")

def src(): return UI.read_text(encoding="utf-8")

def test_rpc_default_and_exact_option():
    t=src()
    assert 'camera_model: str = "rpc"' in t
    assert '("RPC — RPC XML (default)", "rpc")' in t
    assert '("Pléiades exact linescan — DIM XML", "pleiades")' in t

def test_exact_dim_file_pattern():
    t=src()
    assert 'DIM_A.XML / DIM_B.XML / DIM_C.XML' in t
    assert 'return "pleiades"' in t

def test_preprocessing_uses_selected_session_and_cameras():
    t=src()
    assert 'session_type = paths["session_type"]' in t
    assert t.count('session_type,') >= 4
    assert 'cameras[left]' in t and 'cameras[right]' in t
    assert 'images[view]' in t and 'cameras[view]' in t

def test_final_stereo_uses_same_selected_model():
    t=src()
    assert 'pre_paths["session_type"]' in t
    assert 'pre_paths["cameras"][view]' in t
    assert 'self.final_camera_model.value = self.camera_model.value' in t
    assert 'disabled=True' in t

def test_exact_crop_guard():
    t=src()
    assert 'Pléiades exact linescan (DIM XML) mode requires full prepared' in t
    assert 'does not rewrite the exact DIM linescan camera model' in t

def test_rpc_names_preserved_exact_namespaced():
    t=src()
    assert 'return "" if _normalize_camera_model(processing.camera_model) == "rpc" else "_pleiades"' in t

def test_spot_guard_for_asp_330():
    assert 'keep SPOT 6/7 on the RPC' in src()
