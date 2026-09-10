def test_public_packages_import():
    import pleiades_asp_runner
    import asp_utils
    import pleiades_asp_workflow

    assert pleiades_asp_runner.__version__ == "1.5.4"
    assert asp_utils.__version__ == "1.5.4"
    assert pleiades_asp_workflow.__version__ == "1.5.4"
