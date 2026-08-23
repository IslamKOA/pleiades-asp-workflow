from pathlib import Path

UI = Path(
    "src/pleiades_asp_workflow/interface_bundle/asp_utils2.py"
)


def test_geoid_qc_tab_exists():
    text = UI.read_text(encoding="utf-8")
    assert '"geoid": widgets.Output' in text
    assert 'set_title(2, "Geoid / Vertical")' in text


def test_geoid_qc_uses_actual_prepared_rasters():
    text = UI.read_text(encoding="utf-8")
    assert 'result.get("alignment_geoid_model_raster")' in text
    assert 'result.get("map_geoid_model_raster")' in text
    assert "def _plot_reference_geoid_qc" in text


def test_geoid_qc_saves_png_and_pdf():
    text = UI.read_text(encoding="utf-8")
    assert '"reference_geoid_preview.png"' in text
    assert '"reference_geoid_preview.pdf"' in text


def test_geoid_qc_reports_no_conversion():
    text = UI.read_text(encoding="utf-8")
    assert "Vertical conversion: Not applied" in text
    assert "No geoid/quasi-geoid undulation raster was required" in text


def test_geoid_qc_reports_n_statistics():
    text = UI.read_text(encoding="utf-8")
    assert "Geoid undulation N (m)" in text
    assert '"N min"' in text
    assert '"N max"' in text
    assert '"N mean"' in text
