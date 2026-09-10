from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "src" / "pleiades_asp_workflow" / "interface_bundle"
if str(BUNDLE) not in sys.path:
    sys.path.insert(0, str(BUNDLE))

import pleiades_reference_dem as refdem


def _fake_backend(monkeypatch, calls):
    def fake_download_global(source, aoi_path, output_dir, buffer_deg):
        calls.setdefault("downloads", []).append(source)
        out = Path(output_dir) / f"{source}.tif"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("source", encoding="utf-8")
        return out

    def fake_reproject(src_path, dst_path, dst_crs, resolution_m, **kwargs):
        calls.setdefault("resolutions", []).append(float(resolution_m))
        Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
        Path(dst_path).write_text(str(resolution_m), encoding="utf-8")
        return Path(dst_path)

    def fake_convert(working_dem, output_path, geoid_model, custom_n_raster,
                     model_dir, grid_cache_dir, model_tag):
        calls.setdefault("models", []).append((model_tag, geoid_model))
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("ellipsoid", encoding="utf-8")
        n = Path(model_dir) / f"N_{model_tag}_{geoid_model}.tif"
        n.parent.mkdir(parents=True, exist_ok=True)
        n.write_text("N", encoding="utf-8")
        return out, n

    monkeypatch.setattr(refdem, "_download_global", fake_download_global)
    monkeypatch.setattr(refdem, "_reproject_resample", fake_reproject)
    monkeypatch.setattr(refdem, "_convert_to_ellipsoid", fake_convert)
    monkeypatch.setattr(refdem, "_validate_reference_coverage", lambda path, role: 100.0)


def test_ui_exposes_global_alignment_sources_and_warning():
    text = (BUNDLE / "asp_utils2.py").read_text(encoding="utf-8")
    assert "Copernicus DEM GLO-30 — ~30 m global reference" in text
    assert "SRTM1 — ~30 m global reference" in text
    assert "higher-resolution reference is recommended" in text
    assert "self.reference_alignment_resolution.disabled = False" in text
    assert "self.reference_map_resolution.disabled = False" in text
    assert "Mapprojection defaults to the alignment source" in text


def test_same_global_source_same_resolution_can_be_reused(tmp_path, monkeypatch):
    aoi = tmp_path / "aoi.geojson"
    aoi.write_text("{}", encoding="utf-8")
    calls = {}
    _fake_backend(monkeypatch, calls)

    settings = refdem.IntegratedReferenceDEMSettings(
        project_dir=tmp_path / "project",
        target_epsg=32632,
        region="global",
        aoi_path=str(aoi),
        map_source="copernicus",
        map_resolution_m=30.0,
        alignment_source="copernicus",
        alignment_resolution_m=30.0,
        geoid_model="egm2008",
    )
    result = refdem.prepare_integrated_reference_dems(settings)
    assert result["shared_alignment_map_reference"] is True
    assert result["alignment_dem"] == result["mapproject_dem"]
    assert calls["resolutions"] == [30.0]
    assert calls["models"] == [("alignment", "egm2008")]


def test_srtm_can_be_shared_the_same_way(tmp_path, monkeypatch):
    aoi = tmp_path / "aoi.geojson"
    aoi.write_text("{}", encoding="utf-8")
    calls = {}
    _fake_backend(monkeypatch, calls)
    settings = refdem.IntegratedReferenceDEMSettings(
        project_dir=tmp_path / "project",
        target_epsg=32632,
        region="global",
        aoi_path=str(aoi),
        map_source="srtm",
        map_resolution_m=30.0,
        alignment_source="srtm",
        alignment_resolution_m=30.0,
        geoid_model="egm96",
    )
    result = refdem.prepare_integrated_reference_dems(settings)
    assert result["shared_alignment_map_reference"] is True
    assert result["alignment_dem"] == result["mapproject_dem"]
