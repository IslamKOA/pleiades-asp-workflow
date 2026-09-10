from pathlib import Path

import asp_utils2
from asp_utils2 import ProjectSettings, PreProcessingSettings


def _settings(tmp_path, camera_model="pleiades"):
    settings = ProjectSettings(
        project_name="Demo",
        output_base=str(tmp_path),
        platform="PHR1A",
        acquisition_mode="tri_stereo",
        acquisition_A="/tmp/A",
        acquisition_B="/tmp/B",
        acquisition_C="/tmp/C",
        crop_enabled=False,
    )
    settings.merged_dir.mkdir(parents=True, exist_ok=True)
    for view in "ABC":
        (settings.merged_dir / f"{view}.tif").touch()
        (settings.merged_dir / f"RPC_{view}.XML").touch()
        (settings.merged_dir / f"DIM_{view}.XML").touch()
    processing = PreProcessingSettings(
        alignment_dem=str(tmp_path / "lidar.tif"),
        mapproject_dem=str(tmp_path / "map_dem.tif"),
        camera_model=camera_model,
    )
    return settings, processing


def test_v124_exact_session_and_full_data_output(tmp_path):
    settings, processing = _settings(tmp_path)
    paths = asp_utils2._preprocessing_paths(settings, processing)

    assert asp_utils2.DEV_VERSION == "1.5.4"
    assert paths["session_type"] == "pleiades"
    assert paths["processing_root"] == settings.project_dir / "full_data"
    assert paths["asp_out"] == settings.project_dir / "full_data" / "asp_out"
    assert settings.asp_out_dir == settings.project_dir / "full_data" / "asp_out"
    assert paths["cameras"]["A"].name == "DIM_A.XML"
    assert paths["rpcs"]["A"].name == "RPC_A.XML"


def test_dim_adjustment_names_prefer_camera_stem(tmp_path):
    settings, processing = _settings(tmp_path)
    paths = asp_utils2._preprocessing_paths(settings, processing)
    prefix = paths["ba_prefix"]
    prefix.parent.mkdir(parents=True, exist_ok=True)

    for view in "ABC":
        Path(f"{prefix}-DIM_{view}.adjust").touch()

    resolved = asp_utils2._require_adjustments(
        prefix, paths["images"], settings.image_names, paths["cameras"]
    )
    assert [p.name for p in resolved] == [
        "ABC-DIM_A.adjust",
        "ABC-DIM_B.adjust",
        "ABC-DIM_C.adjust",
    ]


def test_final_prerequisites_accept_dim_camera_named_adjustments(tmp_path):
    settings, processing = _settings(tmp_path)
    paths = asp_utils2._preprocessing_paths(settings, processing)

    Path(processing.mapproject_dem).touch()
    for p in paths["mapprojected"].values():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()

    prefix = paths["aligned_ba_prefix"]
    prefix.parent.mkdir(parents=True, exist_ok=True)
    for view in "ABC":
        Path(f"{prefix}-DIM_{view}.adjust").touch()

    returned = asp_utils2._final_common_paths(settings, processing)
    assert returned["aligned_ba_prefix"] == prefix


def test_legacy_rpc_names_remain_readable(tmp_path):
    settings = ProjectSettings(
        project_name="Legacy",
        output_base=str(tmp_path),
        platform="PHR1A",
        acquisition_mode="tri_stereo",
        acquisition_A="/tmp/A",
        acquisition_B="/tmp/B",
        acquisition_C="/tmp/C",
    )
    settings.merged_dir.mkdir(parents=True, exist_ok=True)
    for view in "ABC":
        (settings.merged_dir / f"{view}.tif").touch()
        (settings.merged_dir / f"{view}.XML").touch()

    images, rpcs = asp_utils2._active_image_inputs(settings)
    assert rpcs["A"].name == "A.XML"
    assert images["A"].name == "A.tif"


def test_bundle_adjust_command_uses_pleiades_and_dim_output_names(tmp_path, monkeypatch):
    settings, processing = _settings(tmp_path)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    settings.metadata_dir.mkdir(parents=True, exist_ok=True)
    (settings.project_dir / "project_settings.json").write_text(
        '{"normalized_view_assignment":{"A":{},"B":{},"C":{}}}'
    )

    calls = []

    def fake_run(command, args, log_path, cwd):
        calls.append((command, [str(x) for x in args], Path(log_path), Path(cwd)))
        if command == "bundle_adjust":
            oi = args.index("-o")
            prefix = Path(args[oi + 1])
            prefix.parent.mkdir(parents=True, exist_ok=True)
            for view in "ABC":
                Path(f"{prefix}-DIM_{view}.adjust").touch()
            body = (
                "Mean and median norm of residual error\n"
                "DIM_A.XML, 1.0, 0.5, 10\n"
                "DIM_B.XML, 1.0, 0.5, 10\n"
                "DIM_C.XML, 1.0, 0.5, 10\n"
                "Camera weight position and orientation\n"
            )
            Path(f"{prefix}-initial_residuals_stats.txt").write_text(body)
            Path(f"{prefix}-final_residuals_stats.txt").write_text(body)
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).touch()

    monkeypatch.setattr(asp_utils2, "_run_asp_command", fake_run)

    result = asp_utils2.run_pre_processing(
        settings, processing, stages=("bundle_adjustment",)
    )

    ba = [item for item in calls if item[0] == "bundle_adjust"]
    assert len(ba) == 1
    args = ba[0][1]
    assert args[:2] == ["-t", "pleiades"]
    assert any(x.endswith("DIM_A.XML") for x in args)
    assert any(x.endswith("DIM_B.XML") for x in args)
    assert any(x.endswith("DIM_C.XML") for x in args)
    prefix = Path(args[args.index("-o") + 1])
    assert prefix == settings.project_dir / "full_data" / "asp_out" / "ba_pleiades_ABC" / "ABC"
    assert Path(f"{prefix}-DIM_A.adjust").is_file()
    assert result["paths"]["session_type"] == "pleiades"


def test_residual_parser_accepts_new_rpc_prefix(tmp_path):
    f = tmp_path / "stats.txt"
    f.write_text(
        "Mean and median norm of residual error\n"
        "RPC_A.XML, 1.2, 0.8, 11\n"
        "RPC_B.XML, 1.3, 0.9, 12\n"
        "RPC_C.XML, 1.4, 1.0, 13\n"
        "Camera weight position and orientation\n"
    )
    table = asp_utils2._parse_residual_statistics(f, ["A", "B", "C"])
    assert list(table.index) == ["A", "B", "C"]
    assert table.loc["A", "Count"] == 11
