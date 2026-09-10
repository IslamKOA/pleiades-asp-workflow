from pathlib import Path

import pandas as pd

import asp_utils2


def test_v121_version_and_default_cost_function():
    assert asp_utils2.DEV_VERSION == "1.5.4"
    settings = asp_utils2.PreProcessingSettings(
        alignment_dem="",
        mapproject_dem="",
    )
    assert settings.ba_cost_function == "Cauchy"


def test_cam_test_parser_matches_external_analysis_columns(tmp_path):
    log = tmp_path / "cam_test.log"
    log.write_text(
        """
cam1 to cam2 camera direction diff norm
Min: 0.0001 Median: 0.0002 Max: 0.0003
cam1 to cam2 pixel diff
Min: 0.01 Median: 0.02 Max: 0.03
cam2 to cam1 pixel diff
Min: 0.011 Median: 0.021 Max: 0.031
Number of samples used: 1234
Elapsed time per sample: 0.456 milliseconds
""".strip(),
        encoding="utf-8",
    )

    result = asp_utils2._parse_cam_test_metrics(log)
    assert result["Samples"] == 1234
    assert result["Direction_Median"] == 0.0002
    assert result["DIM_to_RPC_Median_px"] == 0.02
    assert result["DIM_to_RPC_Max_px"] == 0.03
    assert result["RPC_to_DIM_Median_px"] == 0.021
    assert result["Elapsed_ms_per_sample"] == 0.456


def test_metadata_html_tables_have_cell_borders():
    df = pd.DataFrame({"A": [1], "B": [2]})
    html = asp_utils2.ProjectSetupUI._dataframe_html(None, df)
    assert "border:1px solid #c9cdd2" in html
    assert "asp-analysis-table" in html


def test_source_contains_v121_ui_contracts():
    source = Path(asp_utils2.__file__).read_text(encoding="utf-8")
    assert "Compared geometry — exact DIM vs RPC" in source
    assert "widgets.Combobox" in source
    assert "Cauchy" in source
    assert "PseudoHuber" in source
    assert "L2 — least squares / non-robust" in source
    assert "No tuning parameter is required here." in source
    assert "_on_preprocess_single_stage_change" in source
    assert "display_table = result[\"table\"].rename" in source
