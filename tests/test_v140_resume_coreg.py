from pathlib import Path
import json
import tempfile

import asp_utils2


def test_v140_version():
    assert asp_utils2.DEV_VERSION == "1.5.4"


def test_project_config_roundtrip_and_existing_status(tmp_path):
    settings = asp_utils2.ProjectSettings(
        project_name="Demo",
        output_base=str(tmp_path),
        platform="PHR1A",
        acquisition_mode="stereo",
        acquisition_A="/source/A",
        acquisition_B="/source/B",
        tile_ids=("R1C1",),
    )
    asp_utils2.create_project_folders(settings)
    asp_utils2.save_project_config(settings)

    loaded = asp_utils2.load_project_config(settings.project_dir)
    assert loaded.project_name == "Demo"
    assert loaded.acquisition_A == "/source/A"
    assert loaded.tile_ids == ("R1C1",)

    for view in ("A", "B"):
        (loaded.merged_dir / f"{view}.tif").write_bytes(b"x")
        (loaded.merged_dir / f"RPC_{view}.XML").write_text("<RPC/>")

    status = asp_utils2.inspect_existing_project(loaded)
    assert status["prepared_ready"] is True


def test_v140_ui_contract_text():
    source = Path(asp_utils2.__file__).read_text(encoding="utf-8")
    assert "Load / resume existing project" in source
    assert "Caution — tiled DIMAP products" in source
    assert "Starting new run; previous error cleared." in source
    assert "Preliminary DSM preview" in source
    assert "Open optional co-registration notebook" in source
    assert "coregistration_handoff.json" in source
