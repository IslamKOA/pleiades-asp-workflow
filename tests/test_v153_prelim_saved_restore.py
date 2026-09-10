from pathlib import Path
from types import SimpleNamespace
import asp_utils2


def _base_paths(tmp_path):
    asp_out = tmp_path / 'full_data' / 'asp_out'
    log_dir = tmp_path / 'asp_logs'
    map_dir = asp_out / 'mapproject'
    map_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    return asp_out, log_dir, {
        'asp_out': asp_out,
        'log_dir': log_dir,
        'pair': 'AC',
        'model_suffix': '',
        'prelim_point_cloud': asp_out / 'missing-PC.tif',
        'prelim_prefix': asp_out / 'missing',
        'prelim_stereo_dir': asp_out / 'missing-dir',
        'prelim_dem': asp_out / 'missing-DEM.tif',
        'prelim_dem_prefix': asp_out / 'missing-dem',
        'prelim_dem_log': log_dir / 'missing-prelim.log',
        'align_transform': asp_out / 'missing-transform.txt',
        'align_prefix': asp_out / 'missing-transform',
        'align_log': log_dir / 'missing-align.log',
        'mapproject_dir': map_dir,
        'mapprojected': {v: map_dir / f'missing-{v}.tif' for v in ('A','B','C')},
        'mapproject_logs': {v: log_dir / f'missing-{v}.log' for v in ('A','B','C')},
        'ba_prefix': asp_out / 'missing-ba' / 'ABC',
        'aligned_ba_prefix': asp_out / 'missing-aligned-ba' / 'ABC',
    }


def test_v153_saved_preliminary_dem_path_is_preferred(tmp_path):
    asp_out, _, paths = _base_paths(tmp_path)
    legacy = asp_out / 'old_notebook_products' / 'LowQuality_Preliminary_Surface-DEM.tif'
    legacy.parent.mkdir(parents=True)
    legacy.touch()

    saved = {'preliminary_dem': str(legacy)}
    resolved = asp_utils2._restore_saved_preprocess_paths(saved, paths, ['A','B','C'])

    assert resolved['prelim_dem'] == legacy
    assert resolved['prelim_dem_prefix'] == legacy.with_name('LowQuality_Preliminary_Surface')


def test_v153_recursive_case_insensitive_preliminary_dem_fallback(tmp_path):
    asp_out, _, paths = _base_paths(tmp_path)
    legacy = asp_out / 'legacy' / 'Preliminary_AC_CUSTOM-DEM.tif'
    legacy.parent.mkdir(parents=True)
    legacy.touch()

    settings = SimpleNamespace(project_name='Merdaret_Aug24', image_names=['A','B','C'])
    processing = SimpleNamespace(preliminary_pair='AC', raw_resolution_m=0.5, mapproject_dem='')
    resolved = asp_utils2._restore_legacy_preprocess_paths(settings, processing, paths)

    assert resolved['prelim_dem'] == legacy
