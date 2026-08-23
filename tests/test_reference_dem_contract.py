from pathlib import Path
REF=Path("src/pleiades_asp_workflow/interface_bundle/pleiades_reference_dem.py")
IGN=Path("src/pleiades_asp_workflow/interface_bundle/ign_lidarhd_downloader.py")

def test_rectangular_selection():
    s=IGN.read_text()
    for x in ["minx - d","miny - d","maxx + d","maxy + d"]: assert x in s

def test_full_mosaic():
    assert 'outputs["selected_tiles_mosaic_epsg2154"]' in REF.read_text()
    assert "SELECTED_TILES_EPSG2154.tif" in IGN.read_text()

def test_direct_native_aggregation():
    s=REF.read_text()
    assert "aggregate the native IGN" in s
    assert "dst_bounds=map_target_bounds" in s
    assert "dst_bounds=alignment_target_bounds" in s

def test_coverage_gate():
    s=REF.read_text()
    assert "def _validate_reference_coverage" in s
    assert "incomplete rectangular coverage" in s
    assert "mapproject_coverage_percent" in s

def test_retries():
    s=IGN.read_text()
    assert "attempts: int = 3" in s
    assert "time.sleep(2 * attempt)" in s
