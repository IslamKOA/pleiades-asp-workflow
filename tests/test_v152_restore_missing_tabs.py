from pathlib import Path
from types import SimpleNamespace
import asp_utils2


def test_v152_legacy_restore_discovers_prelim_align_and_mapproject(tmp_path):
    asp_out = tmp_path / "full_data" / "asp_out"
    log_dir = tmp_path / "asp_logs"
    (asp_out / "dems" / "stereo_preliminary_AC").mkdir(parents=True)
    (asp_out / "dems" / "align").mkdir(parents=True)
    (asp_out / "mapproject").mkdir(parents=True)
    log_dir.mkdir(parents=True)

    pc = asp_out / "dems" / "stereo_preliminary_AC" / "preliminary-PC.tif"
    dem = asp_out / "dems" / "preliminary_AC-DEM.tif"
    transform = asp_out / "dems" / "align" / "preliminary_AC_to_LiDAR-transform.txt"
    pc.touch(); dem.touch(); transform.write_text("1 0 0 0\n0 1 0 0\n0 0 1 0\n0 0 0 1\n")

    map_outputs = {}
    for view in ("A", "B", "C"):
        path = asp_out / "mapproject" / f"{view}_Merdaret_Aug24_0.5m_baL50.tif"
        path.touch()
        map_outputs[view] = asp_out / "mapproject" / f"{view}_Merdaret_Aug24_0.5m_baDEM.tif"

    settings = SimpleNamespace(project_name="Merdaret_Aug24", image_names=["A", "B", "C"])
    processing = SimpleNamespace(preliminary_pair="AC", raw_resolution_m=0.5, mapproject_dem="/missing/LiDAR50m.tif")
    paths = {
        "asp_out": asp_out,
        "log_dir": log_dir,
        "pair": "AC",
        "model_suffix": "",
        "prelim_point_cloud": asp_out / "missing-PC.tif",
        "prelim_prefix": asp_out / "missing",
        "prelim_stereo_dir": asp_out / "missing-dir",
        "prelim_dem": asp_out / "missing-DEM.tif",
        "prelim_dem_prefix": asp_out / "missing-dem",
        "prelim_dem_log": log_dir / "missing-prelim.log",
        "align_transform": asp_out / "dems" / "align" / "missing-transform.txt",
        "align_prefix": asp_out / "dems" / "align" / "missing",
        "align_log": log_dir / "missing-align.log",
        "mapproject_dir": asp_out / "mapproject",
        "mapprojected": map_outputs,
        "mapproject_logs": {v: log_dir / f"missing-{v}.log" for v in settings.image_names},
    }

    resolved = asp_utils2._restore_legacy_preprocess_paths(settings, processing, paths)
    assert resolved["prelim_point_cloud"] == pc
    assert resolved["prelim_dem"] == dem
    assert resolved["align_transform"] == transform
    for view in settings.image_names:
        assert resolved["mapprojected"][view].name == f"{view}_Merdaret_Aug24_0.5m_baL50.tif"


def test_v152_reset_contract_is_complete():
    source = Path(asp_utils2.__file__).read_text(encoding="utf-8")
    assert 'self.reference_alignment_source.value = "ign"' in source
    assert 'self.reference_alignment_resolution.value = 1.0' in source
    assert 'self.reference_map_source.value = "ign"' in source
    assert 'self.reference_map_resolution.value = 50.0' in source
    assert 'self.reference_geoid_model.value = "raf20"' in source
    assert 'self.reference_map_geoid_model.value = "raf20"' in source
    assert '_restore_legacy_preprocess_paths(settings, processing, paths)' in source
