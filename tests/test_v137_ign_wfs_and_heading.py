from pathlib import Path

ROOT = Path('src/pleiades_asp_workflow/interface_bundle')
IGN = ROOT / 'ign_lidarhd_downloader.py'
UI = ROOT / 'asp_utils2.py'


def test_ign_wfs_has_current_format_and_crs_fallbacks():
    s = IGN.read_text()
    assert '"application/json"' in s
    assert '"GML2"' in s
    assert 'urn:ogc:def:crs:EPSG::2154' in s
    assert 'error.read().decode' in s
    assert 'IGNF_MNS-LIDAR-HD:dalle' in s


def test_asp_preprocessing_execution_heading_matches_reference_heading_size():
    s = UI.read_text()
    assert "font-size:18px;font-weight:700;color:#17324d;" in s
    assert "ASP pre-processing execution</div>" in s
