from pathlib import Path


def test_resume_preview_contract():
    root = Path(__file__).resolve().parents[1]
    text = (root / "src/pleiades_asp_workflow/interface_bundle/asp_utils2.py").read_text(encoding="utf-8")
    assert 'def _restore_prepared_image_preview' in text
    assert 'self._restore_prepared_image_preview(settings)' in text
    assert 'Existing prepared images recovered.' in text
    assert 'Prepare data was not rerun.' in text
