from pathlib import Path

import asp_utils2


def test_v125_version_and_direct_preprocess_html_contract():
    assert asp_utils2.DEV_VERSION == "1.5.4"
    source = Path(asp_utils2.__file__).read_text()
    assert "self._preprocess_stage_html" in source
    assert "widgets.VBox([" in source
    assert "Camera / adjustment products" in source
    assert '"Adjustment / state"' in source
    assert "self._dataframe_html(residual_table)" in source
    assert "Output widgets can otherwise appear blank" in source


def test_session_helper_returns_value_not_flag(tmp_path):
    settings = asp_utils2.ProjectSettings(
        project_name="Demo",
        output_base=str(tmp_path),
        platform="PHR1A",
        acquisition_mode="tri_stereo",
        acquisition_A="/tmp/A",
        acquisition_B="/tmp/B",
        acquisition_C="/tmp/C",
    )
    processing = asp_utils2.PreProcessingSettings(alignment_dem="/tmp/lidar.tif", mapproject_dem="/tmp/map.tif", camera_model="pleiades")
    assert asp_utils2._camera_model_session(settings, processing) == "pleiades"
    assert not asp_utils2._camera_model_session(settings, processing).startswith("-t")
