#!/usr/bin/env python3
"""Summarize the private batch-01 save/cold-load regression without game data."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import struct

try:
    from tools.batch01_save_regression import BATCH_SHA
    from tools.collect_runtime_evidence import ppm
    from tools.replay_card_album import BIOS_SHA1, EMULATOR_SHA256
    from tools.scan_text_tables import hash_file
    from tools.text_codec import CN_IMAGE_SHA256
except ModuleNotFoundError:
    from batch01_save_regression import BATCH_SHA
    from collect_runtime_evidence import ppm
    from replay_card_album import BIOS_SHA1, EMULATOR_SHA256
    from scan_text_tables import hash_file
    from text_codec import CN_IMAGE_SHA256

SEED_SHA = '656340a4d62ef6e3a2779dc9f616f4c840c12e5659b923d80cbf78a7adfb91c4'
BATCH02_SEED_SHA = '4c0497c96447801d4623e4412e5ae2b38fae85b33ba1c343ce53604327a44400'
REGIONS = {'album': (15, 25, 159, 38), 'shop': (166, 53, 310, 94),
           'empty': (153, 113, 300, 126), 'deck': (166, 35, 310, 48)}


def light_mask(path: Path, rect):
    data = ppm(path)
    pixels = set()
    for y in range(rect[1], rect[3]):
        for x in range(rect[0], rect[2]):
            rgb = data[(y * 320 + x) * 3:(y * 320 + x) * 3 + 3]
            if min(rgb) >= 160 and max(rgb) - min(rgb) < 40:
                pixels.add((x, y))
    if not pixels:
        raise ValueError('A screenshot region contains no text')
    return pixels


def run(directory: Path, image: Path, seed: Path, prior: dict, visual_checked: bool, batch02: dict | None = None):
    if not visual_checked:
        raise ValueError('Inspect the saved screen and the reloaded menu captures first')
    expected_image = BATCH_SHA
    expected_seed = SEED_SHA
    if batch02 is not None:
        expected_image = batch02.get('test_image_sha256')
        expected_seed = BATCH02_SEED_SHA
        if (batch02.get('status') != 'batch02_new_entries_runtime_verified' or
            batch02.get('source_image_sha256') != CN_IMAGE_SHA256 or
            batch02.get('previous_batch_image_sha256') != BATCH_SHA or
            not any(e.get('file_id') == 52 and e.get('entry_index') == 19 and
                    e.get('status') == 'runtime_visual_and_pixel_bounds_verified' for e in batch02.get('new_entries', []))):
            raise ValueError('Expected a runtime-verified batch-02 insufficient-funds entry')
    if hash_file(image) != expected_image or prior.get('test_image_sha256') != BATCH_SHA:
        raise ValueError('Image or prior evidence is not the verified batch')
    root = Path(__file__).resolve().parents[1]
    image = image.resolve()
    if not image.is_relative_to((root / 'local').resolve()):
        raise ValueError('Test image must remain under local/')
    cue = image.with_suffix('.cue')
    if cue.read_text() != f'FILE "{image.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n':
        raise ValueError('Test CUE does not bind the inspected image')
    if hash_file(seed) != expected_seed:
        raise ValueError('Independent seed differs from the recorded previous save')
    first_card, cold_card = directory / 'first/memcard1.mcd', directory / 'cold/memcard1.mcd'
    if hash_file(first_card) == expected_seed or hash_file(cold_card) != hash_file(first_card):
        raise ValueError('No changed post-menu save or cold card differs from it')
    previous = {'album': root / 'local/font-recovery/p1-batch01-album-runtime/PRIVATE_card_album.ppm',
                'deck': root / 'local/font-recovery/p1-batch01-deck-runtime/PRIVATE_deck_menu.ppm',
                'shop': root / 'local/font-recovery/p1-batch01-shop-runtime/PRIVATE_shop_main.ppm',
                'empty': root / 'local/font-recovery/p1-batch01-shop-runtime/PRIVATE_shop_no_boosters.ppm'}
    screenshots = prior['runtime']['screenshots_sha256']
    if any(hash_file(previous[k]) != screenshots[k] for k in previous):
        raise ValueError('Earlier manually reviewed menu screenshots differ')
    phases = {}
    checkpoint_data = {}
    for name in ('first', 'cold'):
        folder = directory / name
        result = json.loads((folder / 'result.json').read_text())
        trace = json.loads((folder / 'PRIVATE_trace.json').read_text())
        if result.get('status') != 'ok' or any(s.get('type') in ('write_mem', 'reset') for s in trace['steps']):
            raise ValueError('Replay failed or contains state injection')
        if not any(s.get('type') == 'wait_mem' and s.get('addr') == '0x80048F42' and s.get('value') == 1 for s in trace['steps']):
            raise ValueError('Replay did not verify the saved card-case item')
        cp = {c['name']: c for c in result['checkpoints']}
        checkpoint_data[name] = cp
        required = {'cold_loaded': 2, 'cold_album_visible': 18, 'batch_deck_submenu': 4,
                    'batch_shop_main': 19, 'batch_shop_empty': 19, 'batch_shop_return': 2}
        if batch02 is not None:
            required.update({'batch02_buy_open': 19, 'batch02_insufficient_funds': 19,
                             'batch02_buy_return_to_field': 2})
        if any(k not in cp or cp[k]['stage'] != v for k, v in required.items()):
            raise ValueError('Missing menu or load checkpoint')
        frames = [cp[k]['frame'] for k in required]
        if frames != sorted(frames) or len(set(frames)) != len(frames):
            raise ValueError('Menu checkpoints are out of order')
        log = (folder / 'emulator.log').read_text(errors='replace')
        if 'batch01_deck_submenu_frame=' not in log or 'batch01_shop_step=22 cursor=1' not in log:
            raise ValueError('Deck submenu or no-booster game state was not observed')
        captures = {}
        for key, rect in REGIONS.items():
            screenshot = folder / f'PRIVATE_{key}.ppm'
            if len(light_mask(screenshot, rect) ^ light_mask(previous[key], rect)):
                raise ValueError(f'{name} {key} text pixels differ from the reviewed batch')
            captures[key] = {'sha256': hash_file(screenshot), 'light_pixel_mask_matches_prior': True}
        if batch02 is not None:
            if 'batch02_buy state=1 step=12 substep=19' not in log:
                raise ValueError('The new insufficient-funds window was not actually reached')
            screenshot = folder / 'PRIVATE_insufficient_funds.ppm'
            verified_screen = root / 'local/font-recovery/p1-batch02-money-buy-runtime-v1/PRIVATE_insufficient_funds.ppm'
            entry = next(e for e in batch02['new_entries'] if e['entry_index'] == 19)
            if (hash_file(verified_screen) != entry['runtime_screenshot_sha256'] or
                light_mask(screenshot, (153, 57, 297, 70)) != light_mask(verified_screen, (153, 57, 297, 70))):
                raise ValueError('Post-visit insufficient-funds text differs from the reviewed screen')
            captures['insufficient_funds'] = {'sha256': hash_file(screenshot), 'light_pixel_mask_matches_prior': True}
        phases[name] = {'checkpoints': {k: {'frame': cp[k]['frame'], 'stage': v} for k, v in required.items()},
                        'captures': captures, 'pad_warning_count': log.count('Unknown command for pad: 43'),
                        'listener_stack_reset_count': log.count('replay: Lua stack reset at frame 8192')}
        if name == 'first':
            start = re.search(r'batch01_save_write_started_frame=(\d+)', log)
            finish = re.search(r'batch01_save_write_finished_frame=(\d+)', log)
            if (not start or not finish or cp.get('post_album_saved', {}).get('stage') != 12 or
                cp.get('post_album_save_returned', {}).get('stage') != 2 or
                not (cp['batch02_buy_return_to_field' if batch02 is not None else 'batch_shop_return']['frame'] < int(start.group(1)) < int(finish.group(1)) ==
                     cp['post_album_saved']['frame'] < cp['post_album_save_returned']['frame'])):
                raise ValueError('Fresh save write after menu visits is not proved')
            saved = folder / 'PRIVATE_saved.ppm'
            if not saved.is_file():
                raise ValueError('Saved-screen capture missing')
            phases[name]['save'] = {'started_frame': int(start.group(1)), 'finished_frame': int(finish.group(1)),
                                    'returned_to_field_frame': cp['post_album_save_returned']['frame'],
                                    'screen_sha256': hash_file(saved), 'screen_visually_reviewed': True,
                                    'independent_card_changed_after_visits': True}
    def playtime_seconds(phase, checkpoint):
        filename = checkpoint_data[phase][checkpoint]['gamestate_file']
        raw = (directory / phase / filename).read_bytes()
        if len(raw) != 0x275c:
            raise ValueError('Unexpected private game-state checkpoint length')
        h, m, s = struct.unpack_from('<hhh', raw, 0x4c)
        if not (0 <= h <= 999 and 0 <= m < 60 and 0 <= s < 60):
            raise ValueError('Invalid game-state playtime')
        return h * 3600 + m * 60 + s
    old_time = playtime_seconds('first', 'cold_loaded')
    loaded_time = playtime_seconds('cold', 'cold_loaded')
    after_time = playtime_seconds('first', 'post_album_saved')
    if not old_time < loaded_time <= after_time:
        raise ValueError('Cold-loaded playtime does not reflect the later post-menu save')
    return {'schema_version': 1, 'status': ('batch02_post_menu_save_cold_load_revisit_verified' if batch02 is not None else
                                          'batch01_post_menu_save_cold_load_revisit_verified'),
            'source_image_sha256': CN_IMAGE_SHA256, 'batch_image_sha256': expected_image,
            'input_cue': str(cue.relative_to(root)),
            'emulator': {'name': 'PCSX-Redux', 'version': 'a74a0587', 'build_id': 433,
                         'executable_sha256': EMULATOR_SHA256, 'bios': 'OpenBIOS', 'bios_sha1': BIOS_SHA1,
                         'mode': 'headless, unthrottled, two separate processes with independent private card copies'},
            'initial_seed': 'private copy of the independently generated post-album save',
            'cold_process_loaded_post_menu_save': True, 'cold_card_case_item': 1,
            'playtime_seconds': {'initial_load': old_time, 'cold_reloaded': loaded_time,
                                 'first_process_after_write': after_time},
            'phases': phases,
            'limitations': ['Only the recorded PCSX-Redux/OpenBIOS setup and listed menu paths were exercised.',
                'Pad 43 warnings and the upstream periodic Lua-listener stack-reset message remain in local logs; '
                'observed state changes and captures support only the reported actions.',
                'Private memory cards and screenshots are not included in this report.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('directory', 'image', 'seed', 'prior_report', 'report'):
        p.add_argument(name, type=Path)
    p.add_argument('--visual-checked', action='store_true')
    p.add_argument('--batch02-report', type=Path)
    a = p.parse_args()
    try:
        result = run(a.directory, a.image, a.seed, json.loads(a.prior_report.read_text()), a.visual_checked,
                     json.loads(a.batch02_report.read_text()) if a.batch02_report else None)
    except (OSError, ValueError, KeyError) as exc:
        result = {'schema_version': 1, 'status': 'blocked', 'error_type': type(exc).__name__,
                  'error': str(exc)[:500], 'cold_process_loaded_post_menu_save': False}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(a.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps({'status': result['status'], 'report': str(a.report)}))
    return 2 if result['status'] == 'blocked' else 0


if __name__ == '__main__':
    raise SystemExit(main())
