from __future__ import annotations

import argparse
from importlib.resources import files
from pathlib import Path
import shutil

from . import __version__


DEFAULT_DIRECTORY = Path.home() / "Pleiades_ASP_Workflow"


# ---------------------------------------------------------------------
# Package-managed workflow files
#
# These files belong to the software and are refreshed automatically
# whenever pleiades-workflow-init is run.
# ---------------------------------------------------------------------

MANAGED_ITEMS = (
    "Pleiades_ASP_Workflow.ipynb",
    "asp_utils2.py",
    "prepare_stereo_metadata_and_geometry.py",
    "pleiades_reference_dem.py",
    "global_dem_downloader.py",
    "ign_lidarhd_downloader.py",
    "vertical_reference.py",
    "requirements_interface.txt",
    "figures",
    "examples",
    "coregistration",
)


# ---------------------------------------------------------------------
# User-data locations
#
# These are copied/created on the first installation.
# During subsequent updates, existing files inside these locations are
# preserved so that user data are never overwritten accidentally.
# ---------------------------------------------------------------------

PRESERVED_ITEMS = (
    "data",
)


# ---------------------------------------------------------------------
# Optional developer/reference material
# ---------------------------------------------------------------------

DEVELOPER_ITEMS = (
    "01_Pleiades_ASP_Workflow_Developer.ipynb",
    "original_notebooks",
)


def _copy_resource(
    src,
    dst: Path,
    *,
    overwrite: bool,
) -> None:
    """
    Recursively copy a package resource into the workspace.

    Parameters
    ----------
    src
        Source package resource.

    dst
        Destination path in the user workspace.

    overwrite
        If True, existing files are replaced.
        If False, existing files are preserved.
    """

    # --------------------------------------------------------------
    # Directory
    # --------------------------------------------------------------

    if src.is_dir():

        dst.mkdir(
            parents=True,
            exist_ok=True,
        )

        for child in src.iterdir():

            _copy_resource(
                child,
                dst / child.name,
                overwrite=overwrite,
            )

        return


    # --------------------------------------------------------------
    # File
    # --------------------------------------------------------------

    dst.parent.mkdir(
        parents=True,
        exist_ok=True,
    )


    # Preserve an existing file when overwrite=False.
    if dst.exists() and not overwrite:
        return


    with src.open("rb") as source, dst.open("wb") as target:
        shutil.copyfileobj(
            source,
            target,
        )


def initialize_workspace(
    directory: str | Path = DEFAULT_DIRECTORY,
    *,
    force: bool = False,
    developer: bool = False,
) -> Path:
    """
    Create or update the Pléiades ASP Workflow workspace.

    Normal behaviour
    ----------------
    - If the workspace does not exist, it is created.
    - Package-managed workflow files are copied or refreshed.
    - Existing user data are preserved.

    With --force
    ------------
    Package-provided files inside preserved locations such as ``data/``
    may also be refreshed.

    Files created by the user that are not part of the package are never
    deleted by this function.
    """

    target = Path(directory).expanduser().resolve()

    target.mkdir(
        parents=True,
        exist_ok=True,
    )


    bundle = files(
        "pleiades_asp_workflow"
    ).joinpath(
        "interface_bundle"
    )


    # -----------------------------------------------------------------
    # 1. Refresh software-managed files
    # -----------------------------------------------------------------

    managed_items = list(MANAGED_ITEMS)


    if developer:
        managed_items.extend(
            DEVELOPER_ITEMS
        )


    for item in managed_items:

        src = bundle.joinpath(item)
        dst = target / item

        _copy_resource(
            src,
            dst,
            overwrite=True,
        )


    # -----------------------------------------------------------------
    # 2. Preserve user-data locations
    #
    # Missing package-provided files are added.
    # Existing files are preserved unless --force is explicitly used.
    # -----------------------------------------------------------------

    for item in PRESERVED_ITEMS:

        src = bundle.joinpath(item)
        dst = target / item

        _copy_resource(
            src,
            dst,
            overwrite=force,
        )


    return target


def init_cli() -> None:
    """
    Command-line entry point for ``pleiades-workflow-init``.
    """

    parser = argparse.ArgumentParser(
        prog="pleiades-workflow-init",
        description=(
            "Create or update the Pléiades ASP Workflow workspace. "
            "Package-managed workflow files are refreshed automatically "
            "while existing user data are preserved."
        ),
    )


    parser.add_argument(
        "directory",
        nargs="?",
        default=DEFAULT_DIRECTORY,
        help=(
            f"Destination directory "
            f"(default: {DEFAULT_DIRECTORY})"
        ),
    )


    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Also refresh package-provided files inside preserved "
            "locations such as data/. Existing unrelated user files "
            "are not deleted."
        ),
    )


    parser.add_argument(
        "--developer",
        action="store_true",
        help=(
            "Also copy/update developer and reference material."
        ),
    )


    args = parser.parse_args()


    target_path = Path(
        args.directory
    ).expanduser().resolve()


    workspace_existed = target_path.exists()


    try:

        target = initialize_workspace(
            args.directory,
            force=args.force,
            developer=args.developer,
        )

    except Exception as exc:

        parser.exit(
            2,
            f"pleiades-workflow-init: {exc}\n",
        )


    notebook = (
        target
        / "Pleiades_ASP_Workflow.ipynb"
    )


    print("")


    if workspace_existed:

        print(
            f"Pléiades ASP Workflow {__version__} "
            "workspace updated:"
        )

        print(
            f"  {target}"
        )

        print("")

        print(
            "Package-managed workflow files were refreshed."
        )

        print(
            "Existing user data were preserved."
        )

    else:

        print(
            f"Pléiades ASP Workflow {__version__} "
            "workspace created:"
        )

        print(
            f"  {target}"
        )


    print("")

    print(
        "Open the interface with:"
    )

    print(
        f'  jupyter lab "{notebook}"'
    )


def info_cli() -> None:
    """
    Display basic package/workspace information.
    """

    print(
        f"pleiades-asp-workflow {__version__}"
    )

    print(
        "Interface notebook: "
        "Pleiades_ASP_Workflow.ipynb"
    )

    print(
        "Create or update a workspace: "
        "pleiades-workflow-init"
    )
