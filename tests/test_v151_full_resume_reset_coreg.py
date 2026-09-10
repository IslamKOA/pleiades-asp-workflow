from pathlib import Path
import json
import asp_utils2


def test_v151_full_preprocess_restore_contract():
    source = Path(asp_utils2.__file__).read_text(encoding='utf-8')
    assert 'restored=True' in source
    assert '_bundle_adjustment_residual_summary' in source
    assert '_parse_pc_align_original_results' in source
    assert '_camera_adjustment_table(settings, paths)' in source
    assert '_mapproject_output_table(settings, paths)' in source
    assert 'Restored from the existing project.' in source


def test_v151_reference_reset_contract():
    source = Path(asp_utils2.__file__).read_text(encoding='utf-8')
    assert 'self.reference_aoi.value = ""' in source
    assert 'self.reference_existing_map.value = ""' in source
    assert 'self.reference_existing_alignment.value = ""' in source
    assert 'self.reference_dem_qc_tabs.layout.display = "none"' in source
    assert 'self.reference_dem_progress.value = 0' in source


def test_v151_workflow_lists_coregistration_as_step_6():
    root = Path(asp_utils2.__file__).resolve().parent
    nb = json.loads((root / 'Pleiades_ASP_Workflow.ipynb').read_text(encoding='utf-8'))
    cover = ''.join(nb['cells'][0]['source'])
    assert '<b>6.</b> Optional co-registration' in cover
    assert 'Notebook software v1.5.4' in cover
