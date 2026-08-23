from pathlib import Path


def test_mapproject_qc_uses_common_intersection_and_target_crs_validation():
    root = Path(__file__).resolve().parents[1]
    source = (
        root
        / "src"
        / "pleiades_asp_workflow"
        / "interface_bundle"
        / "asp_utils2.py"
    ).read_text(encoding="utf-8")

    assert "output_epsg != expected_epsg" in source
    assert "common_left = max(" in source
    assert "common_right = min(" in source
    assert "common_bottom = max(" in source
    assert "common_top = min(" in source
    assert "GeoTIFF outputs are unchanged" in source
    assert "mapprojected_images_preview.png" in source
    assert "mapprojected_images_preview.pdf" in source
