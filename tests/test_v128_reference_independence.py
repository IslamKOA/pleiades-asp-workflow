from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "src" / "pleiades_asp_workflow" / "interface_bundle"
if str(BUNDLE) not in sys.path:
    sys.path.insert(0, str(BUNDLE))

import pleiades_reference_dem as refdem


def _install_fakes(monkeypatch, calls):
    def fake_global(source, aoi_path, output_dir, buffer_deg):
        calls.setdefault("download", []).append(source)
        out = Path(output_dir) / f"{source}.tif"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(source, encoding="utf-8")
        return out

    def fake_reproject(src_path, dst_path, dst_crs, resolution_m, **kwargs):
        calls.setdefault("reproject", []).append((Path(dst_path).name, float(resolution_m)))
        out = Path(dst_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(str(resolution_m), encoding="utf-8")
        return out

    def fake_convert(working_dem, output_path, geoid_model, custom_n_raster,
                     model_dir, grid_cache_dir, model_tag):
        calls.setdefault("convert", []).append((model_tag, geoid_model))
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(geoid_model, encoding="utf-8")
        return out, None

    monkeypatch.setattr(refdem, "_download_global", fake_global)
    monkeypatch.setattr(refdem, "_reproject_resample", fake_reproject)
    monkeypatch.setattr(refdem, "_convert_to_ellipsoid", fake_convert)
    monkeypatch.setattr(refdem, "_validate_reference_coverage", lambda path, role: 100.0)


def _aoi(tmp_path):
    p = tmp_path / "aoi.geojson"
    p.write_text("{}", encoding="utf-8")
    return p


def test_same_source_different_resolution_runs_separately(tmp_path, monkeypatch):
    calls = {}
    _install_fakes(monkeypatch, calls)
    settings = refdem.IntegratedReferenceDEMSettings(
        project_dir=tmp_path / "p",
        target_epsg=32632,
        region="global",
        aoi_path=str(_aoi(tmp_path)),
        alignment_source="copernicus",
        alignment_resolution_m=20.0,
        map_source="copernicus",
        map_resolution_m=40.0,
        alignment_geoid_model="egm2008",
        map_geoid_model="egm2008",
    )
    result = refdem.prepare_integrated_reference_dems(settings)
    assert result["shared_alignment_map_reference"] is False
    assert result["alignment_dem"] != result["mapproject_dem"]
    assert [x[1] for x in calls["reproject"]] == [20.0, 40.0]
    assert calls["convert"] == [("alignment", "egm2008"), ("mapprojection", "egm2008")]


def test_different_sources_run_separately_and_keep_role_geoids(tmp_path, monkeypatch):
    calls = {}
    _install_fakes(monkeypatch, calls)
    settings = refdem.IntegratedReferenceDEMSettings(
        project_dir=tmp_path / "p",
        target_epsg=32632,
        region="global",
        aoi_path=str(_aoi(tmp_path)),
        alignment_source="srtm",
        alignment_resolution_m=30.0,
        map_source="copernicus",
        map_resolution_m=30.0,
        alignment_geoid_model="egm96",
        map_geoid_model="egm2008",
    )
    result = refdem.prepare_integrated_reference_dems(settings)
    assert result["shared_alignment_map_reference"] is False
    assert result["alignment_dem"] != result["mapproject_dem"]
    assert calls["download"] == ["srtm", "copernicus"]
    assert calls["convert"] == [("alignment", "egm96"), ("mapprojection", "egm2008")]
    import json
    config = json.loads(Path(result["config_path"]).read_text(encoding="utf-8"))
    assert config["alignment_geoid_model"] == "egm96"
    assert config["map_geoid_model"] == "egm2008"


def test_ui_keeps_reference_options_resolution_and_qc_table_figure():
    text = (BUNDLE / "asp_utils2.py").read_text(encoding="utf-8")
    # Existing options are retained.
    for token in ["IGN LiDAR HD", "Copernicus DEM GLO-30", "SRTM1 30 m", "Existing DEM"]:
        assert token in text
    # Both resolutions remain editable and map selection remains visible.
    assert "self.reference_alignment_resolution.disabled = False" in text
    assert "self.reference_map_resolution.disabled = False" in text
    assert "self.reference_map_source" in text
    # France and global vertical defaults.
    assert 'return "raf20"' in text
    assert 'return "egm96"' in text
    assert 'return "egm2008"' in text
    # QC keeps both figure and a visibly bordered table.
    assert 'display(payload["figure"])' in text
    assert "border-collapse:collapse" in text
    assert "Requested working resolution" in text
    assert "Vertical reference model" in text


def test_same_source_same_resolution_but_different_vertical_models_runs_separately(tmp_path, monkeypatch):
    calls = {}
    _install_fakes(monkeypatch, calls)
    settings = refdem.IntegratedReferenceDEMSettings(
        project_dir=tmp_path / "p_vertical",
        target_epsg=32632,
        region="global",
        aoi_path=str(_aoi(tmp_path)),
        alignment_source="copernicus",
        alignment_resolution_m=30.0,
        map_source="copernicus",
        map_resolution_m=30.0,
        alignment_geoid_model="egm2008",
        map_geoid_model="egm96",
    )
    result = refdem.prepare_integrated_reference_dems(settings)
    assert result["shared_alignment_map_reference"] is False
    assert result["alignment_dem"] != result["mapproject_dem"]
    assert calls["convert"] == [("alignment", "egm2008"), ("mapprojection", "egm96")]
