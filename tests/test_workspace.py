from pathlib import Path


def test_workspace_materialization(tmp_path):
    from pleiades_asp_workflow.workspace import initialize_workspace

    target = initialize_workspace(tmp_path / "workflow")

    assert (target / "Pleiades_ASP_Workflow.ipynb").is_file()
    assert (target / "asp_utils2.py").is_file()
    assert (target / "pleiades_reference_dem.py").is_file()

    assert (
        target
        / "figures"
        / "overview_mountain_page1.png"
    ).is_file()

    assert (
        target
        / "figures"
        / "Overview_mountain.pdf"
    ).is_file()

    assert (
        target
        / "figures"
        / "Geoid_concept.png"
    ).is_file()

    assert (
        target
        / "data"
        / "RAF20.tac"
    ).is_file()

    assert (
        target
        / "examples"
        / "README.md"
    ).is_file()
