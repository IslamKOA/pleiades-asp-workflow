from pathlib import Path

UI = Path('src/pleiades_asp_workflow/interface_bundle/asp_utils2.py')

def src():
    return UI.read_text(encoding='utf-8')

def comparison_block(t):
    a=t.index('def run_camera_model_comparison(')
    b=t.index('# BUNDLE-ADJUSTMENT RESIDUALS', a)
    return t[a:b]

def preflight_block(t):
    a=t.index('def _preflight_exact_pleiades_cameras(')
    b=t.index('PREPROCESS_STAGE_ORDER', a)
    return t[a:b]

def test_camtest_default_command_matches_supplied_script_principle():
    b=comparison_block(src())
    for token in ['"--image"', '"--cam1"', '"--cam2"', '"--session1"', '"--session2"']:
        assert token in b
    assert '"--height-above-datum"' not in b
    assert '"--sample-rate"' not in b

def test_dim_preflight_does_not_force_camtest_sampling_or_height():
    b=preflight_block(src())
    assert '"--height-above-datum"' not in b
    assert '"--sample-rate"' not in b

def test_camtest_saved_columns_match_external_analysis_code():
    b=comparison_block(src())
    expected = [
        '"Dataset"', '"Sensor"', '"View"', '"Status"', '"Samples"',
        '"Direction_Min"', '"Direction_Median"', '"Direction_Max"',
        '"DIM_to_RPC_Min_px"', '"DIM_to_RPC_Median_px"', '"DIM_to_RPC_Max_px"',
        '"RPC_to_DIM_Min_px"', '"RPC_to_DIM_Median_px"', '"RPC_to_DIM_Max_px"',
        '"Elapsed_ms_per_sample"',
    ]
    pos=-1
    for col in expected:
        nxt=b.find(col, pos+1)
        assert nxt > pos, col
        pos=nxt

def test_old_camtest_height_and_sampling_widgets_removed():
    t=src()
    assert 'self.cam_test_height' not in t
    assert 'self.cam_test_sample_rate' not in t
    assert 'Advanced DIM ↔ RPC comparison (cam_test only)' not in t

def test_comparison_table_and_figure_remain_visible():
    t=src()
    assert 'Comparison results — numerical table + figure' in t
    assert '_plot_camera_model_comparison(settings, table)' in t
    assert '_styled_dataframe(display_table, precision=6)' in t

def test_runtime_camtest_command_has_only_reference_script_arguments(tmp_path, monkeypatch):
    import asp_utils2

    settings = asp_utils2.ProjectSettings(
        project_name='CamTest',
        output_base=str(tmp_path),
        platform='PHR1A',
        acquisition_mode='stereo',
        acquisition_A=str(tmp_path/'inputA'),
        acquisition_B=str(tmp_path/'inputB'),
    )
    settings.merged_dir.mkdir(parents=True, exist_ok=True)
    for view in settings.image_names:
        (settings.merged_dir/f'{view}.tif').write_bytes(b'x')
        (settings.merged_dir/f'DIM_{view}.XML').write_text('<xml/>')
        (settings.merged_dir/f'RPC_{view}.XML').write_text('<xml/>')

    calls=[]
    fake='''cam1 to cam2 camera direction diff norm\nMin: 0.001 Median: 0.002 Max: 0.003\ncam1 to cam2 pixel diff\nMin: 0.01 Median: 0.02 Max: 0.03\ncam2 to cam1 pixel diff\nMin: 0.01 Median: 0.02 Max: 0.03\nNumber of samples used: 42\nElapsed time per sample: 0.5 milliseconds\n'''
    def fake_run(command, args, log_path, cwd, *a, **k):
        calls.append((command, [str(x) for x in args]))
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text(fake)

    monkeypatch.setattr(asp_utils2, '_run_asp_command', fake_run)
    monkeypatch.setattr(asp_utils2, '_plot_camera_model_comparison', lambda *a, **k: None)

    result=asp_utils2.run_camera_model_comparison(settings)
    assert len(calls) == 2
    for command,args in calls:
        assert command == 'cam_test'
        assert '--height-above-datum' not in args
        assert '--sample-rate' not in args
        assert args[args.index('--session1')+1] == 'pleiades'
        assert args[args.index('--session2')+1] == 'rpc'
    assert list(result['table'].columns) == [
        'Dataset','Sensor','View','Status','Samples',
        'Direction_Min','Direction_Median','Direction_Max',
        'DIM_to_RPC_Min_px','DIM_to_RPC_Median_px','DIM_to_RPC_Max_px',
        'RPC_to_DIM_Min_px','RPC_to_DIM_Median_px','RPC_to_DIM_Max_px',
        'Elapsed_ms_per_sample'
    ]
