from pathlib import Path
from PIL import Image
import numpy as np


ASP = Path(
    "src/pleiades_asp_workflow/interface_bundle/asp_utils2.py"
)

FIG = Path(
    "src/pleiades_asp_workflow/interface_bundle/figures/"
    "overview_mountain_notebook.png"
)


def test_concept_figure_uses_unique_v106_asset():
    source = ASP.read_text(encoding="utf-8")

    assert "overview_mountain_notebook.png" in source
    assert (
        'module_dir / "figures" / "overview_mountain_page1.png"'
        not in source
    )
    assert "data:image/png;base64" in source
    assert "height:auto" in source
    assert "max-height:none" in source
    assert "object-fit:contain" in source


def test_notebook_asset_contains_lower_figure_content():
    with Image.open(FIG) as image:
        image = image.convert("RGB")
        arr = np.asarray(image)

    nonwhite = np.any(arr < 245, axis=2)
    lower = nonwhite[int(nonwhite.shape[0] * 2 / 3):, :]

    assert image.width <= 1800
    assert lower.mean() > 0.08
