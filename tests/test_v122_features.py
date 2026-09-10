from pathlib import Path
import asp_utils2


def test_version():
    assert asp_utils2.DEV_VERSION == "1.5.4"


def test_dim_adjustment_discovery_accepts_camera_stem(tmp_path):
    prefix = tmp_path / "ba" / "ABC"
    prefix.parent.mkdir(parents=True)
    images = {v: tmp_path / f"{v}.tif" for v in "ABC"}
    cameras = {v: tmp_path / f"DIM_{v}.XML" for v in "ABC"}
    for v in "ABC":
        Path(f"{prefix}-DIM_{v}.adjust").write_text("0 0 0\n1 0 0 0\n")
    found = asp_utils2._require_adjustments(prefix, images, tuple("ABC"), cameras)
    assert all("DIM_" in p.name for p in found)


def test_dim_adjustment_discovery_accepts_model_state(tmp_path):
    prefix = tmp_path / "ba" / "ABC"
    prefix.parent.mkdir(parents=True)
    images = {v: tmp_path / f"{v}.tif" for v in "ABC"}
    cameras = {v: tmp_path / f"DIM_{v}.XML" for v in "ABC"}
    for v in "ABC":
        Path(f"{prefix}-{v}.adjusted_state.json").write_text("{}")
    found = asp_utils2._require_adjustments(prefix, images, tuple("ABC"), cameras)
    assert all(p.name.endswith(".adjusted_state.json") for p in found)


def test_source_has_exact_pleiades_constraints_and_preflight():
    source = Path(asp_utils2.__file__).read_text()
    assert '"--camera-weight", 0' in source
    assert '"--tri-weight", 0.1' in source
    assert '_preflight_exact_pleiades_cameras' in source
    assert 'widgets.Combobox' in source
