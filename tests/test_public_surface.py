def test_public_packages_import():
    import pleiades_asp_runner
    import asp_utils
    import pleiades_asp_workflow

    assert pleiades_asp_runner.__version__ == "1.0.6"
    assert asp_utils.__version__ == "1.0.6"
    assert pleiades_asp_workflow.__version__ == "1.0.6"
