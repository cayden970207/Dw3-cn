#!/usr/bin/env python3
"""Replay inspected shop, deck, album or lab paths with a copied private card."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

try:
    from tools.album_regression import index_one, make_traces, checkpoint
    from tools.replay_card_album import (BIOS_SHA1, EMULATOR_SHA256, REFERENCE_COMMIT, STACK_COMMIT, git_head)
    from tools.scan_text_tables import hash_file
    from tools.text_codec import CN_IMAGE_SHA256
except ModuleNotFoundError:
    from album_regression import index_one, make_traces, checkpoint
    from replay_card_album import (BIOS_SHA1, EMULATOR_SHA256, REFERENCE_COMMIT, STACK_COMMIT, git_head)
    from scan_text_tables import hash_file
    from text_codec import CN_IMAGE_SHA256


def trace(reference: Path, scenario: str = 'shop'):
    original = json.loads((reference / 'tests/replay/scripts/first_battle_save.json').read_text())['steps']
    if scenario == 'lab':
        reset = index_one(original, lambda s: s.get('type') == 'reset')
        loaded = index_one(original, lambda s: s.get('name') == 'loaded', reset)
        leave_inn = index_one(original, lambda s: s.get('type') == 'press' and
                              s.get('until', {}).get('map') == '0x200', loaded)
        lab_route = index_one(original, lambda s: s.get('type') == 'walk' and
                              s.get('x') == 665 and s.get('y') == 378, leave_inn)
        lab_open = index_one(original, lambda s: s.get('name') == 'lab_open', lab_route)
        steps = (original[reset + 1:leave_inn + 1] + original[lab_route:lab_open + 1] +
                 [{'type': 'wait_frames', 'frames': 650},
                  {'type': 'press', 'buttons': ['DOWN'], 'frames': 3, 'release': 100},
                  {'type': 'press', 'buttons': ['DOWN'], 'frames': 3, 'release': 100},
                  {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 100},
                  {'type': 'wait_frames', 'frames': 500},
                  {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 100},
                  {'type': 'wait_frames', 'frames': 850}, checkpoint('lab_chart_observed')])
        if any(s.get('type') in ('write_mem', 'reset') for s in steps):
            raise ValueError('Lab replay must use only natural inputs')
        return {'name': 'cn_lab_chart_check', 'max_frames': 20000, 'default_timeout': 3000, 'steps': steps}
    _, cold = make_traces(reference)
    if scenario == 'album':
        return cold
    if scenario == 'deck02':
        status = index_one(cold['steps'], lambda s: s.get('name') == 'status_open')
        album = index_one(original, lambda s: s.get('name') == 'album_open')
        deck = index_one(original, lambda s: s.get('type') == 'press' and s.get('until', {}).get('stage') == 4, album)
        steps = cold['steps'][:status + 1] + [original[deck],
            {'type': 'wait_frames', 'frames': 650}, checkpoint('batch02_deck_selection'),
            {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
            {'type': 'wait_frames', 'frames': 200}, checkpoint('batch02_deck_menu'),
            {'type': 'press', 'buttons': ['TRIANGLE'], 'frames': 3, 'release': 120},
            {'type': 'wait_frames', 'frames': 100}, checkpoint('batch02_menu_cancelled'),
            {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
            {'type': 'wait_frames', 'frames': 200}, checkpoint('batch02_menu_reopened'),
            {'type': 'press', 'buttons': ['DOWN'], 'frames': 3, 'release': 90},
            {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
            {'type': 'wait_frames', 'frames': 500}, checkpoint('batch02_name_entry'),
            # Choose one character through the game's own name-entry grid.
            # This changes only this replay's in-memory deck name; no save step follows.
            {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
            {'type': 'press', 'buttons': ['START'], 'frames': 3, 'release': 90},
            {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
            {'type': 'wait_frames', 'frames': 500}, checkpoint('batch02_name_return'),
            {'type': 'press', 'buttons': ['TRIANGLE'], 'frames': 2, 'release': 10,
             'until': {'type': 'wait_stage', 'stage': 16}, 'timeout': 1500},
            checkpoint('batch02_deck_return_to_party')]
        if any(s.get('type') in ('write_mem', 'reset') for s in steps):
            raise ValueError('Deck-02 replay must use only natural inputs')
        return {'name': 'cn_batch02_deck_check', 'max_frames': 20000, 'default_timeout': 3000, 'steps': steps}
    if scenario == 'deck':
        status = index_one(cold['steps'], lambda s: s.get('name') == 'status_open')
        album = index_one(original, lambda s: s.get('name') == 'album_open')
        deck = index_one(original, lambda s: s.get('type') == 'press' and s.get('until', {}).get('stage') == 4, album)
        steps = cold['steps'][:status + 1] + [original[deck], {'type': 'wait_frames', 'frames': 650},
                 {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
                 {'type': 'wait_frames', 'frames': 200}, checkpoint('deck_submenu'),
                 {'type': 'press', 'buttons': ['TRIANGLE'], 'frames': 3, 'release': 120},
                 {'type': 'press', 'buttons': ['TRIANGLE'], 'frames': 2, 'release': 10,
                  'until': {'type': 'wait_stage', 'stage': 16}, 'timeout': 1500}, checkpoint('deck_return_to_party')]
        if any(s.get('type') in ('write_mem', 'reset') for s in steps):
            raise ValueError('Deck replay must use only natural inputs')
        return {'name': 'cn_card_deck_check', 'max_frames': 20000, 'default_timeout': 3000, 'steps': steps}
    arena = index_one(cold['steps'], lambda s: s.get('type') == 'press' and s.get('until', {}).get('map') == '0x211')
    opened = index_one(original, lambda s: s.get('name') == 'card_shop_open')
    previous_arena = max(i for i in range(opened) if original[i].get('type') == 'press' and
                         original[i].get('until', {}).get('map') == '0x211')
    steps = cold['steps'][:arena + 1] + original[previous_arena + 1:opened + 1]
    if scenario == 'booster03':
        steps += [{'type': 'wait_frames', 'frames': 650}, checkpoint('batch03_shop_main'),
                  {'type': 'press', 'buttons': ['DOWN'], 'frames': 3, 'release': 90},
                  {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
                  {'type': 'wait_frames', 'frames': 650}, checkpoint('batch03_booster_selection'),
                  {'type': 'press', 'buttons': ['TRIANGLE'], 'frames': 3, 'release': 120},
                  {'type': 'wait_frames', 'frames': 200}, checkpoint('batch03_booster_cancelled'),
                  {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
                  {'type': 'wait_frames', 'frames': 650}, checkpoint('batch03_booster_reopened'),
                  {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
                  {'type': 'wait_frames', 'frames': 850}, checkpoint('batch03_booster_opened'),
                  {'type': 'press', 'buttons': ['RIGHT'], 'frames': 3, 'release': 90},
                  {'type': 'wait_frames', 'frames': 150}, checkpoint('batch03_booster_next_card'),
                  {'type': 'press', 'buttons': ['TRIANGLE'], 'frames': 3, 'release': 120},
                  {'type': 'wait_frames', 'frames': 650}, checkpoint('batch03_booster_returned'),
                  {'type': 'press', 'buttons': ['TRIANGLE'], 'frames': 2, 'release': 10,
                   'until': {'type': 'wait_stage', 'stage': 2}, 'timeout': 2000},
                  checkpoint('batch03_booster_return_to_field')]
        if any(s.get('type') in ('write_mem', 'reset') for s in steps):
            raise ValueError('Booster replay must use only natural inputs')
        return {'name': 'cn_batch03_booster_check', 'max_frames': 20000, 'default_timeout': 3000, 'steps': steps}
    if scenario == 'limit03':
        steps += [{'type': 'wait_frames', 'frames': 650}, checkpoint('batch03_shop_main'),
                  {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
                  {'type': 'wait_frames', 'frames': 650}, checkpoint('batch03_buy_open'),
                  {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
                  {'type': 'wait_frames', 'frames': 250}, checkpoint('batch03_nine_card_limit'),
                  {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
                  {'type': 'wait_frames', 'frames': 200}, checkpoint('batch03_limit_dismissed'),
                  {'type': 'press', 'buttons': ['TRIANGLE'], 'frames': 2, 'release': 10,
                   'until': {'type': 'wait_stage', 'stage': 2}, 'timeout': 2000},
                  checkpoint('batch03_limit_return_to_field')]
        if any(s.get('type') in ('write_mem', 'reset') for s in steps):
            raise ValueError('Limit replay must use only natural inputs')
        return {'name': 'cn_batch03_limit_check', 'max_frames': 20000, 'default_timeout': 3000, 'steps': steps}
    if scenario == 'buy02':
        steps += [{'type': 'wait_frames', 'frames': 650}, checkpoint('batch02_shop_main'),
                  {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
                  {'type': 'wait_frames', 'frames': 650}, checkpoint('batch02_buy_open'),
                  {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
                  {'type': 'wait_frames', 'frames': 250}, checkpoint('batch02_insufficient_funds'),
                  {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
                  {'type': 'press', 'buttons': ['TRIANGLE'], 'frames': 2, 'release': 10,
                   'until': {'type': 'wait_stage', 'stage': 2}, 'timeout': 2000},
                  checkpoint('batch02_buy_return_to_field')]
        if any(s.get('type') in ('write_mem', 'reset') for s in steps):
            raise ValueError('Buy-02 replay must use only natural inputs')
        return {'name': 'cn_batch02_buy_check', 'max_frames': 20000, 'default_timeout': 3000, 'steps': steps}
    steps += [{'type': 'wait_frames', 'frames': 650}, checkpoint('shop_main_stable'),
              {'type': 'press', 'buttons': ['DOWN'], 'frames': 3, 'release': 90},
              {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
              {'type': 'wait_frames', 'frames': 200}, checkpoint('shop_after_open_booster'),
              {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
              {'type': 'press', 'buttons': ['TRIANGLE'], 'frames': 2, 'release': 10,
               'until': {'type': 'wait_stage', 'stage': 2}, 'timeout': 2000},
              checkpoint('shop_return_to_field')]
    if any(s.get('type') in ('write_mem', 'reset') for s in steps):
        raise ValueError('Shop replay must use only natural inputs and a fresh process')
    return {'name': 'cn_card_shop_check', 'max_frames': 20000, 'default_timeout': 3000, 'steps': steps}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('image', type=Path)
    p.add_argument('image_report', type=Path)
    p.add_argument('seed_card', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--reference', type=Path, required=True)
    p.add_argument('--emulator', type=Path, required=True)
    p.add_argument('--bios', type=Path, required=True)
    p.add_argument('--version-json', type=Path, required=True)
    p.add_argument('--scenario', choices=('shop', 'deck', 'album', 'lab', 'deck02', 'buy02',
                                          'booster03', 'limit03'), default='shop')
    a = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    local, out, seed, ref = (root / 'local').resolve(), a.output.resolve(), a.seed_card.resolve(), a.reference.resolve()
    if out == local or not out.is_relative_to(local) or not seed.is_relative_to(local) or seed.stat().st_size != 131072:
        raise ValueError('Output and independent seed card must be under local/')
    if git_head(ref) != REFERENCE_COMMIT or git_head(ref / 'psxstack') != STACK_COMMIT:
        raise ValueError('Reference commits differ from inspected source')
    if any(subprocess.run(['git', '-C', str(path), 'diff', '--quiet', 'HEAD']).returncode for path in (ref, ref / 'psxstack')):
        raise ValueError('Reference checkout has tracked changes')
    if hashlib.sha1(a.bios.read_bytes()).hexdigest() != BIOS_SHA1 or hash_file(a.emulator) != EMULATOR_SHA256:
        raise ValueError('Emulator or OpenBIOS does not match the recorded build')
    version = json.loads(a.version_json.read_text())
    if version.get('version') != 'a74a0587' or version.get('buildId') != 433:
        raise ValueError('Emulator version metadata differs')
    image = a.image.resolve()
    report = json.loads(a.image_report.read_text())
    if report.get('source_image_sha256') != CN_IMAGE_SHA256 or hash_file(image) != report.get('test_image_sha256'):
        raise ValueError('Test image does not match its baseline-bound report')
    cue = image.with_suffix('.cue')
    if cue.read_text() != f'FILE "{image.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n':
        raise ValueError('Test CUE does not bind the selected test image')
    spec = trace(ref, a.scenario)
    out.mkdir(mode=0o700, parents=False, exist_ok=False)
    card = out / 'memcard1.mcd'
    before_sha = hash_file(seed)
    with seed.open('rb') as source, card.open('xb') as target:
        shutil.copyfileobj(source, target)
    if hash_file(card) != before_sha:
        raise ValueError('Memory-card copy failed')
    sys.path.insert(0, str(ref / 'psxstack/tools/replay'))
    import emulator
    cfg = emulator.configure(root=ref, game_json=ref / 'port/game/game.json', redux_dir=out, iso=cue,
                             scripts_dir=ref / 'tests/replay/scripts', expected_dir=out,
                             probes=ref / 'tests/replay/probes.lua')
    cfg.redux, cfg.redux_version, cfg.openbios = a.emulator.resolve(), a.version_json.resolve(), a.bios.resolve()
    os.environ['DW3_REPLAY_RUN_LUA'] = str(ref / 'psxstack/tools/replay/run.lua')
    trace_lua = out / 'PRIVATE_trace.lua'
    trace_lua.write_text('return ' + emulator.lua_literal(emulator.parse_ints(spec)) + '\n')
    (out / 'PRIVATE_trace.json').write_text(json.dumps(spec, indent=2) + '\n')
    env = emulator.replay_env(trace_lua, out, speed=0)
    env['DW3_CAPTURE_COMBINED'] = '0'
    capture = {'deck': 'tools/card_deck_capture.lua', 'shop': 'tools/card_shop_capture.lua',
               'lab': 'tools/lab_dynamic_capture.lua',
               'deck02': 'tools/batch02_runtime_capture.lua', 'buy02': 'tools/batch02_runtime_capture.lua',
               'booster03': 'tools/batch03_runtime_capture.lua', 'limit03': 'tools/batch03_runtime_capture.lua',
               'album': 'tools/card_album_capture.lua'}[a.scenario]
    cmd = [str(cfg.redux), '-no-ui', '-stdout', '-testmode', '-run', '-iso', str(cue), '-bios', str(cfg.openbios),
           '-memcard1', str(card), '-memcard2', str(out / 'memcard2.mcd'), '-dofile', str(root / capture)]
    with (out / 'emulator.log').open('w') as log:
        proc = subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=600)
    result = json.loads((out / 'result.json').read_text())
    if proc.returncode or result.get('status') != 'ok':
        raise RuntimeError(f"Shop replay failed: {result.get('message')}")
    if a.scenario == 'lab' and not all((out / name).is_file() for name in
            ('PRIVATE_lab_window.json', 'PRIVATE_lab_line0.bin', 'PRIVATE_lab_line1.bin', 'PRIVATE_lab_chart.ppm')):
        raise RuntimeError('Lab replay completed without reaching the observable chart title')
    if hash_file(seed) != before_sha:
        raise ValueError('Source memory card changed')
    print(json.dumps({'status': result['status'], 'frames': result['frames'],
                      'checkpoints': [x['name'] for x in result['checkpoints']]}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
