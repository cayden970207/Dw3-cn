#!/usr/bin/env python3
"""Summarize verified post-menu save, cold load and re-entry; keep cards private."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import struct

try:
    from tools.collect_batch01_save_regression import light_mask
    from tools.collect_batch03_runtime import FIXTURE_SHA, checkpoint_state
    from tools.collect_card_batch import inspect_replay
    from tools.replay_card_album import BIOS_SHA1, EMULATOR_SHA256
    from tools.scan_text_tables import hash_file
    from tools.text_codec import CN_IMAGE_SHA256
except ModuleNotFoundError:
    from collect_batch01_save_regression import light_mask
    from collect_batch03_runtime import FIXTURE_SHA, checkpoint_state
    from collect_card_batch import inspect_replay
    from replay_card_album import BIOS_SHA1, EMULATOR_SHA256
    from scan_text_tables import hash_file
    from text_codec import CN_IMAGE_SHA256


def playtime(folder: Path, checkpoint: str) -> int:
    result = json.loads((folder / 'result.json').read_text())
    row = next(x for x in result['checkpoints'] if x['name'] == checkpoint)
    raw = (folder / row['gamestate_file']).read_bytes()
    if len(raw) != 0x275c:
        raise ValueError('Checkpoint size differs')
    h, m, s = struct.unpack_from('<hhh', raw, 0x4c)
    if not (0 <= h <= 999 and 0 <= m < 60 and 0 <= s < 60):
        raise ValueError('Invalid playtime')
    return h * 3600 + m * 60 + s


def run(directory: Path, image: Path, fixture: Path, runtime_path: Path,
        reference_booster: Path, reference_limit: Path, visual_checked: bool) -> dict:
    if not visual_checked:
        raise ValueError('Inspect the actual save and cold-load screenshots first')
    root = Path(__file__).resolve().parents[1]
    local = (root / 'local').resolve()
    if any(not p.resolve().is_relative_to(local) for p in (directory, image, fixture, reference_booster, reference_limit)):
        raise ValueError('Memory cards, screenshots and test image must remain private')
    runtime = json.loads(runtime_path.read_text())
    if (runtime.get('status') != 'recorded_actual_shop_paths_with_artificial_fixture' or
        runtime.get('source_image_sha256') != CN_IMAGE_SHA256 or hash_file(image) != runtime.get('test_image_sha256') or
        runtime.get('new_translated_entries') != [15, 20] or
        any(e.get('translation_status') != 'runtime_visual_and_pixel_bounds_verified_with_artificial_fixture'
            for e in runtime['entries'])):
        raise ValueError('New translations lack matching runtime evidence')
    if hash_file(fixture) != FIXTURE_SHA:
        raise ValueError('Fixture changed')
    first, cold = directory / 'first', directory / 'cold'
    saved_sha = hash_file(first / 'memcard1.mcd')
    if saved_sha == FIXTURE_SHA or hash_file(cold / 'memcard1.mcd') != saved_sha:
        raise ValueError('Post-visit card was not changed or cold copy differs')
    rows = {e['entry_index']: e for e in runtime['entries']}
    references = {15: (reference_booster, (19, 194, 163, 207)),
                  20: (reference_limit, (153, 57, 297, 70))}
    for n, (path, _) in references.items():
        if hash_file(path) != rows[n]['runtime_screenshot_sha256']:
            raise ValueError('Reference screenshot differs from verified runtime evidence')
    phases = {}
    for label, folder in (('first', first), ('cold', cold)):
        required = {'cold_loaded': 2, 'batch03_buy_open': 19, 'batch03_nine_card_limit': 19,
                    'batch03_limit_dismissed': 19, 'batch03_back_to_shop': 19,
                    'batch03_save_booster_selection': 19, 'batch03_save_booster_cancelled': 19,
                    'batch03_both_menus_left': 2}
        if label == 'first':
            required.update({'batch03_post_menu_saved': 12, 'batch03_post_menu_save_returned': 2})
        cp = inspect_replay(folder, required)
        if [v['frame'] for v in cp.values()] != sorted(v['frame'] for v in cp.values()):
            raise ValueError('Save or menu checkpoints are out of order')
        log = (folder / 'emulator.log').read_text(errors='replace')
        if not re.search(r'batch03_buy state=1 step=12 substep=20 frame=\d+ money=10000 booster_count=1 card65_count=9 chosen=65 price=10000', log):
            raise ValueError('Nine-card state and sufficient balance were not observed')
        if not re.search(r'batch03_booster state=1 step=4 substep=0 frame=\d+ money=10000 booster_count=1 card65_count=9 chosen=0 price=0 list_count=1', log):
            raise ValueError('Booster selection was not observed')
        captures = {}
        for n, filename in ((15, 'PRIVATE_booster_selection.ppm'), (20, 'PRIVATE_nine_card_limit.ppm')):
            path, rect = references[n]
            actual = folder / filename
            if light_mask(actual, rect) != light_mask(path, rect):
                raise ValueError('Revisited translated pixels differ')
            captures[str(n)] = {'sha256': hash_file(actual), 'text_mask_matches_runtime': True}
        state = checkpoint_state(folder, 'batch03_nine_card_limit')
        if state['money'] != 10000 or state['booster_item_91'] != 1 or state['card_65'] != 9:
            raise ValueError('Checkpoint state differs')
        phase = {'checkpoints': cp, 'captures': captures, 'game_state': state,
                 'pad_warning_count': log.count('Unknown command for pad: 43'),
                 'listener_stack_reset_count': log.count('replay: Lua stack reset at frame 8192')}
        if label == 'first':
            start = re.search(r'batch03_save_write_started_frame=(\d+)', log)
            finish = re.search(r'batch03_save_write_finished_frame=(\d+)', log)
            saved_screen = folder / 'PRIVATE_saved.ppm'
            if (not start or not finish or not saved_screen.is_file() or
                not (cp['batch03_both_menus_left']['frame'] < int(start.group(1)) < int(finish.group(1)) ==
                     cp['batch03_post_menu_saved']['frame'] < cp['batch03_post_menu_save_returned']['frame'])):
                raise ValueError('The save did not follow both menu visits')
            phase['save'] = {'started_frame': int(start.group(1)), 'finished_frame': int(finish.group(1)),
                             'screen_sha256': hash_file(saved_screen), 'screen_visually_reviewed': True}
        elif 'batch03_save_write_started_frame=' in log:
            raise ValueError('The cold revisit unexpectedly saved')
        phases[label] = phase
    old_time, new_time, after_time = (playtime(first, 'cold_loaded'), playtime(cold, 'cold_loaded'),
                                      playtime(first, 'batch03_post_menu_saved'))
    if not old_time < new_time <= after_time:
        raise ValueError('Cold process did not load the later save')
    return {'schema_version': 1, 'status': 'two_menu_visit_post_save_cold_load_revisit_verified',
            'source_image_sha256': CN_IMAGE_SHA256, 'test_image_sha256': runtime['test_image_sha256'],
            'input_cue': str(image.resolve().with_suffix('.cue').relative_to(root)),
            'private_evidence_directory': str(directory.resolve().relative_to(root)),
            'fixture_kind': 'artificial_condition_fixture_not_normal_play',
            'emulator': {'name': 'PCSX-Redux', 'version': 'a74a0587', 'build_id': 433,
                         'executable_sha256': EMULATOR_SHA256, 'bios': 'OpenBIOS', 'bios_sha1': BIOS_SHA1,
                         'mode': 'two headless -no-ui -testmode processes, unthrottled; arm64 dynarec; dummy audio',
                         'capture_size_px': [320, 240], 'gpu_backend': 'not identified in the captured log'},
            'separate_processes': True, 'cold_process_loaded_post_menu_save': True,
            'playtime_seconds': {'initial_load': old_time, 'cold_reloaded': new_time, 'post_write': after_time},
            'phases': phases,
            'limitations': ['The initial memory card was a deliberately edited local fixture, not natural progress.',
                'These two PCSX-Redux/OpenBIOS menu paths were exercised; other emulators and paths are not implied.',
                'Pad 43 warnings and a periodic listener stack-reset message remain in the logs; state and pixels support only the recorded actions.',
                'Private cards, local screenshots and game resources are excluded.']}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('directory', 'image', 'fixture', 'runtime', 'reference_booster', 'reference_limit', 'report'):
        p.add_argument(name, type=Path)
    p.add_argument('--visual-checked', action='store_true')
    a = p.parse_args()
    try:
        result = run(a.directory, a.image, a.fixture, a.runtime, a.reference_booster, a.reference_limit, a.visual_checked)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        result = {'schema_version': 1, 'status': 'blocked', 'error_type': type(exc).__name__, 'error': str(exc)[:500]}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(a.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as out:
        json.dump(result, out, ensure_ascii=False, indent=2); out.write('\n')
    print(json.dumps({'status': result['status'], 'report': str(a.report)}))
    return 2 if result['status'] == 'blocked' else 0


if __name__ == '__main__':
    raise SystemExit(main())
