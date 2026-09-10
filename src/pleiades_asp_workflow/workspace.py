from __future__ import annotations

import argparse
from importlib.resources import files
from pathlib import Path
import shutil

from . import __version__

DEFAULT_DIRECTORY = Path.home() / "Pleiades_ASP_Workflow"

# End-user runtime files. Developer/reference material remains available in the
# installed package but is not copied into a normal workspace unless requested.
RUNTIME_ITEMS = (
    "Pleiades_ASP_Workflow.ipynb",
    "asp_utils2.py",
    "prepare_stereo_metadata_and_geometry.py",
    "pleiades_reference_dem.py",
    "global_dem_downloader.py",
    "ign_lidarhd_downloader.py",
    "vertical_reference.py",
    "requirements_interface.txt",
    "figures",
    "data",
    "examples",
    "coregistration",
)

DEVELOPER_ITEMS = (
    "01_Pleiades_ASP_Workflow_Developer.ipynb",
    "original_notebooks",
)


def _copy_resource(src, dst: Path, *, force: bool) -> None:
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            _copy_resource(child, dst / child.name, force=force)
        return

    if dst.exists() and not force:
        raise FileExistsError(
            f"Refusing to overwrite existing workflow file: {dst}\n"
            "Use --force only when you intentionally want to refresh "
            "package-managed workflow files."
        )

    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as source, dst.open("wb") as target:
        shutil.copyfileobj(source, target)


def initialize_workspace(
    directory: str | Path = DEFAULT_DIRECTORY,
    *,
    force: bool = False,
    developer: bool = False,
) -> Path:
    """Materialize the notebook interface and its runtime files."""
    target = Path(directory).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)

    bundle = files("pleiades_asp_workflow").joinpath("interface_bundle")
    items = list(RUNTIME_ITEMS)

    if developer:
        items.extend(DEVELOPER_ITEMS)

    # Preflight first so a partial workspace is not created because one file
    # already exists.
    if not force:
        conflicts = [target / item for item in items if (target / item).exists()]
        if conflicts:
            joined = "\n".join(f"  - {path}" for path in conflicts)
            raise FileExistsError(
                "The target already contains package-managed workflow files:\n"
                f"{joined}\n"
                "Choose another directory or rerun with --force."
            )

    for item in items:
        _copy_resource(bundle.joinpath(item), target / item, force=force)

    return target


def init_cli() -> None:
    parser = argparse.ArgumentParser(
        prog="pleiades-workflow-init",
        description=(
            "Create a working directory containing the Pléiades ASP "
            "Jupyter interface and all required local workflow modules."
        ),
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=DEFAULT_DIRECTORY,
        help=f"Destination directory (default: {DEFAULT_DIRECTORY})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite package-managed workflow files in the destination.",
    )
    parser.add_argument(
        "--developer",
        action="store_true",
        help="Also copy the developer notebook.",
    )
    args = parser.parse_args()

    try:
        target = initialize_workspace(
            args.directory,
            force=args.force,
            developer=args.developer,
        )
    except Exception as exc:
        parser.exit(2, f"pleiades-workflow-init: {exc}\n")

    notebook = target / "Pleiades_ASP_Workflow.ipynb"
    print(f"Pléiades ASP Workflow {__version__} workspace created:")
    print(f"  {target}")
    print("\nOpen the interface with:")
    print(f'  jupyter lab "{notebook}"')


def info_cli() -> None:
    print(f"pleiades-asp-workflow {__version__}")
    print("Interface notebook: Pleiades_ASP_Workflow.ipynb")
    print("Create a workspace: pleiades-workflow-init")
