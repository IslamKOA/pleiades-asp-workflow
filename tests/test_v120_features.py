from pathlib import Path

import pandas as pd

from pleiades_asp_workflow.interface_bundle.asp_utils2 import (
    PREPROCESS_STAGE_ORDER,
    ProjectSettings,
    WorkflowLog,
    _normalize_tri_stereo_prepared_files,
    _normalized_preprocess_stages,
    _tri_stereo_time_assignment,
)
from pleiades_asp_runner.installer import REQUIRED_TOOLS


def _metadata():
    # Input folder labels intentionally do not match chronological F/M/B order.
    return pd.DataFrame(
        [
            {"image_id": "A", "IMAGING_DATE": "2024-07-04", "IMAGING_TIME": "10:00:03Z"},
            {"image_id": "B", "IMAGING_DATE": "2024-07-04", "IMAGING_TIME": "10:00:01Z"},
            {"image_id": "C", "IMAGING_DATE": "2024-07-04", "IMAGING_TIME": "10:00:02Z"},
        ]
    )


def test_time_assignment_normalizes_to_figure_convention():
    mapping, table = _tri_stereo_time_assignment(_metadata())
    assert mapping == {"B": "A", "C": "B", "A": "C"}
    assert table["assigned_workflow_id"].tolist() == ["A", "B", "C"]
    assert table["view_role"].tolist() == [
        "Forward",
        "Middle / near-nadir",
        "Backward",
    ]
    assert table["figure_symbol"].tolist() == ["F", "M", "B"]


def test_prepared_products_are_renamed_together(tmp_path):
    settings = ProjectSettings(
        project_name="demo",
        output_base=str(tmp_path),
        platform="PHR1A",
        acquisition_mode="tri_stereo",
        acquisition_A="input-a",
        acquisition_B="input-b",
        acquisition_C="input-c",
        crop_enabled=True,
    )
    settings.merged_dir.mkdir(parents=True)
    settings.cropped_dir.mkdir(parents=True)
    settings.metadata_dir.mkdir(parents=True)
    settings.log_dir.mkdir(parents=True)
    (settings.project_dir / "project_settings.json").write_text("{}")

    for old in ("A", "B", "C"):
        files = {
            settings.merged_dir / f"{old}.tif": f"{old}-image",
            settings.merged_dir / f"{old}.XML": f"{old}-rpc",
            settings.merged_dir / f"DIM_{old}.XML": f"{old}-dim",
            settings.merged_dir / f"{old}.vrt": f"{old}-vrt",
            settings.cropped_dir / f"{old}_crop.tif": f"{old}-crop-image",
            settings.cropped_dir / f"{old}_crop.XML": f"{old}-crop-rpc",
            settings.cropped_dir / f"DIM_{old}_crop.XML": f"{old}-crop-dim",
        }
        for path, content in files.items():
            path.write_text(content)

    with WorkflowLog(settings.log_dir / "test.log") as log:
        mapping, table, csv_path = _normalize_tri_stereo_prepared_files(
            settings, _metadata(), log
        )

    assert mapping == {"B": "A", "C": "B", "A": "C"}
    assert (settings.merged_dir / "A.tif").read_text() == "B-image"
    assert (settings.merged_dir / "B.tif").read_text() == "C-image"
    assert (settings.merged_dir / "C.tif").read_text() == "A-image"
    assert (settings.merged_dir / "DIM_A.XML").read_text() == "B-dim"
    assert (settings.merged_dir / "B.XML").read_text() == "C-rpc"
    assert (settings.cropped_dir / "C_crop.tif").read_text() == "A-crop-image"
    assert csv_path.is_file()
    assert table["assigned_workflow_id"].tolist() == ["A", "B", "C"]


def test_restart_stage_selection_is_ordered_and_validated():
    assert _normalized_preprocess_stages(None) == PREPROCESS_STAGE_ORDER
    assert _normalized_preprocess_stages("preliminary_dem") == ("preliminary_dem",)
    assert _normalized_preprocess_stages(
        ["map_projection", "bundle_adjustment"]
    ) == ("bundle_adjustment", "map_projection")


def test_cam_test_is_packaged_as_required_asp_tool():
    assert "cam_test" in REQUIRED_TOOLS
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'cam_test = "pleiades_asp_runner.cli:main"' in pyproject


def test_ui_exposes_restart_and_grouped_stage_controls():
    source = Path(
        "src/pleiades_asp_workflow/interface_bundle/asp_utils2.py"
    ).read_text(encoding="utf-8")
    assert "Run all pre-processing" in source
    assert "Run one step" in source
    for title in (
        "1. Bundle adjustment",
        "2. Preliminary stereo",
        "3. Preliminary DSM",
        "4. LiDAR / reference alignment",
        "5. Camera transform",
        "6. Map projection",
    ):
        assert title in source
    assert "Compare RPC vs DIM (cam_test)" in source
