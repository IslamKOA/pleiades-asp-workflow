from pathlib import Path

UI = Path('src/pleiades_asp_workflow/interface_bundle/asp_utils2.py')

def src():
    return UI.read_text(encoding='utf-8')

def test_cam_test_results_table_and_figure_are_preserved():
    t=src()
    assert 'Compared geometry — exact DIM vs RPC' in t
    assert 'Comparison results — numerical table + figure' in t
    assert 'display(self._styled_dataframe(display_table, precision=6))' in t
    assert 'display(result["plot"]["figure"])' in t

def test_vertical_reference_is_shared_by_default_and_can_split():
    t=src()
    assert 'Use separate vertical references' in t
    assert 'Separate vertical references activated automatically' in t
    assert 'def _reference_vertical_models_are_split' in t
    assert 'self.reference_alignment_source.value != self.reference_map_source.value' in t
    assert 'self.reference_geoid_model.description = "Vertical reference:"' in t
    assert 'self.reference_geoid_model.description = "Alignment vertical ref:"' in t
    assert 'self.reference_map_geoid_model.description = "Map vertical ref:"' in t

def test_qc_table_and_figure_are_preserved():
    t=src()
    assert 'display(payload["figure"])' in t
    assert 'border-collapse:collapse' in t
    assert 'QC figure + bordered numerical table' in t

def test_major_titles_are_visually_prominent():
    t=src()
    for title in ['Prepare data','Metadata and geometry','Point cloud','Final DSM']:
        assert f"font-size:20px;font-weight:700;color:#17324d;'>{title}</div>" in t
    assert "font-size:24px;font-weight:700;color:#17324d;'>Pre-processing</div>" in t
    assert 'font-size:15px;font-weight:700;color:#29465b;' in t

def test_camera_model_note_is_emphasized():
    assert 'Important — camera model controls the complete ASP camera path.' in src()
