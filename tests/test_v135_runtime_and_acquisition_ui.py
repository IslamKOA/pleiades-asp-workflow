from pathlib import Path

import asp_utils2


def test_input_section_no_long_tri_stereo_normalization_sentence():
    source = Path(asp_utils2.__file__).read_text(encoding="utf-8")
    assert "For tri-stereo, these are temporary input slots" not in source
    assert 'widgets.HTML("<br><b>2. Input acquisitions</b>"), self.common_rows[3], *self.common_rows[4:7]' in source


def test_metadata_header_is_mode_dependent():
    source = Path(asp_utils2.__file__).read_text(encoding="utf-8")
    assert "For stereo, the two prepared images are handled simply as <b>A</b> and <b>B</b>" in source
    assert "For tri-stereo, DIM acquisition times determine the viewing order" in source


def test_runtime_summary_rewrites_stage_row(tmp_path):
    settings = asp_utils2.ProjectSettings(
        project_name="runtime_test",
        output_base=str(tmp_path),
        platform="PHR1A",
        acquisition_mode="stereo",
        acquisition_A="A",
        acquisition_B="B",
    )
    first = asp_utils2._record_runtime(settings, "prepare_data", 12.0)
    second = asp_utils2._record_runtime(settings, "prepare_data", 5.25)
    row = second.loc[second["stage_key"] == "prepare_data"].iloc[0]
    assert len(second) == len(asp_utils2.RUNTIME_STAGE_DEFINITIONS)
    assert float(row["last_runtime_seconds"]) == 5.25
    assert int(row["run_count"]) == 2
    assert (settings.metadata_dir / "runtime_summary.csv").is_file()


def test_runtime_summary_covers_full_workflow():
    keys = [item[1] for item in asp_utils2.RUNTIME_STAGE_DEFINITIONS]
    assert keys == [
        "prepare_data",
        "metadata_geometry",
        "camera_comparison",
        "reference_dem",
        "bundle_adjustment",
        "preliminary_stereo",
        "preliminary_dem",
        "lidar_alignment",
        "camera_transform",
        "map_projection",
        "point_cloud",
        "final_dsm",
    ]
