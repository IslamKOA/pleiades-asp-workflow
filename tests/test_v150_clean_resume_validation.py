from pathlib import Path
import pytest
import asp_utils2


def _settings(tmp_path, *, merge, tiles):
    return asp_utils2.ProjectSettings(
        project_name='Demo',
        output_base=str(tmp_path),
        platform='PHR1A',
        acquisition_mode='stereo',
        acquisition_A='/does/not/matter/A',
        acquisition_B='/does/not/matter/B',
        merge_tiles=merge,
        tile_ids=tiles,
    )


def test_v150_version_and_clean_ui_contract():
    assert asp_utils2.DEV_VERSION == '1.5.4'
    source = Path(asp_utils2.__file__).read_text(encoding='utf-8')
    assert 'Start new project / clear form' in source
    assert 'Also delete loaded project outputs from disk' not in source
    assert 'R1C1 is missing' in source
    assert '<b>Project already exists.</b>' in source
    assert '_restore_existing_processing_result_panels' in source


def test_merge_on_requires_r1c1(tmp_path):
    s = _settings(tmp_path, merge=True, tiles=('R1C2',))
    log_path = tmp_path / 'log.txt'
    with asp_utils2.WorkflowLog(log_path) as log:
        with pytest.raises(ValueError, match='R1C1 is missing'):
            asp_utils2.validate_project_inputs(s, log)


def test_merge_off_rejects_non_r1c1(tmp_path):
    s = _settings(tmp_path, merge=False, tiles=('R1C2',))
    log_path = tmp_path / 'log.txt'
    with asp_utils2.WorkflowLog(log_path) as log:
        with pytest.raises(ValueError, match='selected without R1C1'):
            asp_utils2.validate_project_inputs(s, log)


def test_original_notebooks_and_current_coreg_are_packaged():
    root = Path(asp_utils2.__file__).resolve().parent
    originals = root / 'original_notebooks'
    assert (originals / '05_ASP_Pre-Processing_and_mapProjection.ipynb').is_file()
    assert (originals / '06_Generate_Point_Cloud_and_Final_DSM.ipynb').is_file()
    assert (originals / 'Coregistration_Process-Final-withPlannimetric.ipynb').is_file()
    assert (root / 'coregistration' / 'Coregistration_Process-Final-withPlannimetric.ipynb').is_file()
