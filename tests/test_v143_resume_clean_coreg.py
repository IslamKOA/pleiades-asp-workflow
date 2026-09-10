from pathlib import Path
import json

import asp_utils2


def _make_prepared_tri(settings):
    settings.merged_dir.mkdir(parents=True, exist_ok=True)
    for view in ("A", "B", "C"):
        (settings.merged_dir / f"{view}.tif").write_bytes(b"dummy")
        (settings.merged_dir / f"RPC_{view}.XML").write_text("<RPC/>", encoding="utf-8")
        (settings.merged_dir / f"DIM_{view}.XML").write_text("<DIM/>", encoding="utf-8")


def test_v143_version_and_ui_contract():
    assert asp_utils2.DEV_VERSION == "1.5.4"
    source = Path(asp_utils2.__file__).read_text(encoding="utf-8")
    assert "Start new project / clear form" in source
    assert "Also delete loaded project outputs from disk" not in source
    assert "R1C1 is missing" in source
    assert "Project already exists" in source
    assert 'button_style="info"' in source
    assert 'self.run_stage1.button_style = "warning"' in source
    assert "_resolve_single_stage_resume_context" in source
    assert "saved project state" in source


def test_coregistration_notebook_renamed():
    root = Path(asp_utils2.__file__).resolve().parent
    new_name = root / "coregistration" / "Coregistration_Process-Final-withPlannimetric.ipynb"
    old_name = root / "coregistration" / "07_Coregistration_Process-Final-withPlannimetric.ipynb"
    assert new_name.is_file()
    assert not old_name.exists()
    source = Path(asp_utils2.__file__).read_text(encoding="utf-8")
    assert "Coregistration_Process-Final-withPlannimetric.ipynb" in source
    assert "07_Coregistration_Process-Final-withPlannimetric.ipynb" not in source


def test_single_stage_resume_reuses_saved_preliminary_pair(tmp_path):
    settings = asp_utils2.ProjectSettings(
        project_name="Demo",
        output_base=str(tmp_path),
        platform="PHR1A",
        acquisition_mode="tri_stereo",
        acquisition_A="/src/A",
        acquisition_B="/src/B",
        acquisition_C="/src/C",
        tile_ids=("R1C1",),
    )
    asp_utils2.create_project_folders(settings)
    _make_prepared_tri(settings)

    alignment = settings.project_dir / "reference.tif"
    alignment.write_bytes(b"dummy")

    # Saved project used AC, while the reopened/current UI is deliberately AB.
    saved_processing = asp_utils2.PreProcessingSettings(
        alignment_dem=str(alignment),
        mapproject_dem="",
        camera_model="rpc",
        preliminary_pair="AC",
    )
    saved_paths = asp_utils2._preprocessing_paths(settings, saved_processing)
    Path(saved_paths["prelim_dem"]).parent.mkdir(parents=True, exist_ok=True)
    Path(saved_paths["prelim_dem"]).write_bytes(b"dummy")

    state = {
        "pre_processing": {
            "camera_model": "rpc",
            "target_epsg": saved_processing.target_epsg,
            "raw_resolution_m": saved_processing.raw_resolution_m,
            "preliminary_pair": "AC",
            "alignment_dem": str(alignment),
            "mapproject_dem": "",
            "preliminary_dem": str(saved_paths["prelim_dem"]),
        }
    }
    (settings.project_dir / "processing_state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )

    current_processing = asp_utils2.PreProcessingSettings(
        alignment_dem=str(alignment),
        mapproject_dem="",
        camera_model="rpc",
        preliminary_pair="AB",
    )

    resolved_processing, resolved_paths, context = asp_utils2._resolve_single_stage_resume_context(
        settings, current_processing, ("lidar_alignment",)
    )

    assert context == "saved project state"
    assert resolved_processing.preliminary_pair == "AC"
    assert Path(resolved_paths["prelim_dem"]) == Path(saved_paths["prelim_dem"])


def test_tri_stereo_normalization_reuses_existing_assignment_csv(tmp_path):
    settings = asp_utils2.ProjectSettings(
        project_name="DemoNorm",
        output_base=str(tmp_path),
        platform="PHR1A",
        acquisition_mode="tri_stereo",
        acquisition_A="/src/A",
        acquisition_B="/src/B",
        acquisition_C="/src/C",
        tile_ids=("R1C1",),
    )
    asp_utils2.create_project_folders(settings)
    asp_utils2.save_project_config(settings)
    _make_prepared_tri(settings)

    assignment = settings.metadata_dir / f"{settings.project_name}_view_assignment.csv"
    assignment.write_text(
        "assigned_workflow_id,view_role\nA,Forward\nB,Middle\nC,Backward\n",
        encoding="utf-8",
    )

    assert asp_utils2._tri_stereo_normalization_ready(settings) is True
