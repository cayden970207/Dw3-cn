#!/usr/bin/env python3
"""Visit batch-01 menus, save to a copied card, then cold-load in a new process.

The specified seed card, original ZIP, and test disc are read-only. Everything
written by the emulator is confined to a new ignored local directory.
"""
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
    from tools.album_regression import checkpoint, index_one, make_traces
    from tools.replay_card_album import BIOS_SHA1, EMULATOR_SHA256, REFERENCE_COMMIT, STACK_COMMIT, git_head
    from tools.scan_text_tables import hash_file
    from tools.text_codec import CN_IMAGE_SHA256
except ModuleNotFoundError:
    from album_regression import checkpoint, index_one, make_traces
    from replay_card_album import BIOS_SHA1, EMULATOR_SHA256, REFERENCE_COMMIT, STACK_COMMIT, git_head
    from scan_text_tables import hash_file
    from text_codec import CN_IMAGE_SHA256

BATCH_SHA = '03fa8316801fd610fec4dfdf895753d25121f8c955f3835f0ad4446d12886193'


def traces(reference: Path):
    original = json.loads((reference / 'tests/replay/scripts/first_battle_save.json').read_text())['steps']
    old_first, cold_album = make_traces(reference)
    album = index_one(original, lambda s: s.get('name') == 'album_open')
    deck = index_one(original, lambda s: s.get('type') == 'press' and s.get('until', {}).get('stage') == 4, album)
    opened = index_one(original, lambda s: s.get('name') == 'card_shop_open')
    reenter = max(i for i in range(opened) if original[i].get('type') == 'walk' and
                  original[i].get('x') == 545 and original[i].get('y') == 234)
    if original[album + 1].get('until', {}).get('stage') != 16:
        raise ValueError('Pinned album exit no longer matches')
    menu_steps = [original[album + 1], original[deck], {'type': 'wait_frames', 'frames': 650},
        {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
        {'type': 'wait_frames', 'frames': 200}, checkpoint('batch_deck_submenu'),
        {'type': 'press', 'buttons': ['TRIANGLE'], 'frames': 3, 'release': 120},
        original[deck + 2], original[deck + 3], original[deck + 4]]
    menu_steps += original[reenter:opened + 1] + [
        {'type': 'wait_frames', 'frames': 650}, checkpoint('batch_shop_main'),
        {'type': 'press', 'buttons': ['DOWN'], 'frames': 3, 'release': 90},
        {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
        {'type': 'wait_frames', 'frames': 200}, checkpoint('batch_shop_empty'),
        {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
        {'type': 'press', 'buttons': ['TRIANGLE'], 'frames': 2, 'release': 10,
         'until': {'type': 'wait_stage', 'stage': 2}, 'timeout': 2000},
        checkpoint('batch_shop_return')]
    field = index_one(old_first['steps'], lambda s: s.get('name') == 'field_return')
    first_steps = cold_album['steps'] + menu_steps + old_first['steps'][field + 1:]
    cold_steps = cold_album['steps'] + menu_steps
    for steps in (first_steps, cold_steps):
        if any(s.get('type') in ('reset', 'write_mem') for s in steps):
            raise ValueError('Replay may not reset or inject state')
    return ({'name': 'batch01_visit_then_save', 'max_frames': 40000, 'default_timeout': 3000, 'steps': first_steps},
            {'name': 'batch01_post_save_cold_load_and_revisit', 'max_frames': 30000,
             'default_timeout': 3000, 'steps': cold_steps})


def run(image: Path, creation_report: Path, seed: Path, output: Path, ref: Path,
        emulator_path: Path, bios: Path, version_path: Path, batch02_runtime_report: Path | None = None):
    root = Path(__file__).resolve().parents[1]
    local = (root / 'local').resolve()
    image, seed, output, ref = (p.resolve() for p in (image, seed, output, ref))
    if output == local or not output.is_relative_to(local) or not seed.is_relative_to(local) or seed.stat().st_size != 131072:
        raise ValueError('Use a new local output and a private 128 KiB seed card')
    report = json.loads(creation_report.read_text())
    expected_sha = BATCH_SHA
    if batch02_runtime_report is not None:
        verified = json.loads(batch02_runtime_report.read_text())
        expected_sha = report.get('test_image_sha256')
        if (verified.get('status') != 'batch02_new_entries_runtime_verified' or
            verified.get('source_image_sha256') != CN_IMAGE_SHA256 or
            verified.get('previous_batch_image_sha256') != BATCH_SHA or
            verified.get('test_image_sha256') != expected_sha or
            not any(e.get('file_id') == 52 and e.get('entry_index') == 19 and
                    e.get('status') == 'runtime_visual_and_pixel_bounds_verified' for e in verified.get('new_entries', []))):
            raise ValueError('Batch-02 must have verified the insufficient-funds window first')
    if report.get('source_image_sha256') != CN_IMAGE_SHA256 or report.get('test_image_sha256') != expected_sha or hash_file(image) != expected_sha:
        raise ValueError('Test image or baseline differs')
    cue = image.with_suffix('.cue')
    if cue.read_text() != f'FILE "{image.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n':
        raise ValueError('Batch CUE does not match its image')
    if git_head(ref) != REFERENCE_COMMIT or git_head(ref / 'psxstack') != STACK_COMMIT:
        raise ValueError('Reference revisions differ')
    if any(subprocess.run(['git', '-C', str(p), 'diff', '--quiet', 'HEAD']).returncode for p in (ref, ref / 'psxstack')):
        raise ValueError('Reference checkout has tracked changes')
    if hashlib.sha1(bios.read_bytes()).hexdigest() != BIOS_SHA1 or hash_file(emulator_path) != EMULATOR_SHA256:
        raise ValueError('Emulator or OpenBIOS differs')
    version = json.loads(version_path.read_text())
    if version.get('version') != 'a74a0587' or version.get('buildId') != 433:
        raise ValueError('Unexpected emulator version')
    first, cold = traces(ref)
    if batch02_runtime_report is not None:
        try:
            from tools.replay_card_shop import trace as card_trace
        except ModuleNotFoundError:
            from replay_card_shop import trace as card_trace
        original = json.loads((ref / 'tests/replay/scripts/first_battle_save.json').read_text())['steps']
        shop_open = index_one(original, lambda s: s.get('name') == 'card_shop_open')
        arena = max(i for i in range(shop_open) if original[i].get('type') == 'press' and
                    original[i].get('until', {}).get('map') == '0x211')
        buy = card_trace(ref, 'buy02')['steps']
        buy_open = index_one(buy, lambda s: s.get('name') == 'card_shop_open')
        add = original[arena + 1:shop_open + 1] + buy[buy_open + 1:]
        for spec in (first, cold):
            where = index_one(spec['steps'], lambda s: s.get('name') == 'batch_shop_return') + 1
            spec['steps'][where:where] = add
            spec['name'] = spec['name'].replace('batch01', 'batch02')
            if any(s.get('type') in ('reset', 'write_mem') for s in spec['steps']):
                raise ValueError('Batch-02 regression may not reset or inject state')
    seed_hash = hash_file(seed)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    sys.path.insert(0, str(ref / 'psxstack/tools/replay'))
    import emulator
    cfg = emulator.configure(root=ref, game_json=ref / 'port/game/game.json', redux_dir=output, iso=cue,
                             scripts_dir=ref / 'tests/replay/scripts', expected_dir=output,
                             probes=ref / 'tests/replay/probes.lua')
    cfg.redux, cfg.redux_version, cfg.openbios = emulator_path.resolve(), version_path.resolve(), bios.resolve()
    os.environ['DW3_REPLAY_RUN_LUA'] = str(ref / 'psxstack/tools/replay/run.lua')

    def phase(name, trace, input_card):
        dest = output / name
        dest.mkdir(mode=0o700)
        card = dest / 'memcard1.mcd'
        source_sha = hash_file(input_card)
        with input_card.open('rb') as src, card.open('xb') as dst:
            shutil.copyfileobj(src, dst)
        if hash_file(card) != source_sha:
            raise ValueError('Card copy mismatch')
        script = dest / 'PRIVATE_trace.lua'
        script.write_text('return ' + emulator.lua_literal(emulator.parse_ints(trace)) + '\n')
        (dest / 'PRIVATE_trace.json').write_text(json.dumps(trace, indent=2) + '\n')
        env = emulator.replay_env(script, dest, speed=0)
        env['DW3_CAPTURE_COMBINED'] = '0'
        capture = root / 'tools/batch01_regression_capture.lua'
        if batch02_runtime_report is not None:
            env['DW3_CAPTURE_COMBINED'] = '1'
            env['DW3_CAPTURE_BATCH01'] = str(capture)
            env['DW3_CAPTURE_BATCH02'] = str(root / 'tools/batch02_runtime_capture.lua')
            capture = root / 'tools/batch02_save_regression_capture.lua'
        cmd = [str(cfg.redux), '-no-ui', '-stdout', '-testmode', '-run', '-iso', str(cue), '-bios', str(cfg.openbios),
               '-memcard1', str(card), '-memcard2', str(dest / 'memcard2.mcd'), '-dofile',
               str(capture)]
        with (dest / 'emulator.log').open('w') as log:
            process = subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=600)
        result_path = dest / 'result.json'
        if process.returncode or not result_path.is_file():
            raise RuntimeError(f'{name} process failed or did not finish')
        result = json.loads(result_path.read_text())
        if result.get('status') != 'ok':
            raise RuntimeError(f"{name} replay failed: {result.get('message')}")
        if hash_file(input_card) != source_sha:
            raise ValueError('Seed/source card changed')
        return source_sha, hash_file(card), result

    _, saved_sha, result = phase('first', first, seed)
    if hash_file(seed) != seed_hash or saved_sha == seed_hash:
        raise ValueError('Original seed changed or save did not change copied card')
    print('first_process_finished frames=' + str(result['frames']), flush=True)
    copied_sha, _, result = phase('cold', cold, output / 'first/memcard1.mcd')
    if copied_sha != saved_sha or hash_file(image) != expected_sha:
        raise ValueError('Saved card or input disc changed')
    print(json.dumps({'cold_status': result['status'], 'cold_frames': result['frames']}), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('image', 'creation_report', 'seed', 'output'):
        p.add_argument(name, type=Path)
    for name in ('reference', 'emulator', 'bios', 'version-json'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--batch02-runtime-report', type=Path,
                   help='Require a verified batch-02 insufficient-funds replay and include it before and after saving')
    a = p.parse_args()
    run(a.image, a.creation_report, a.seed, a.output, a.reference, a.emulator, a.bios, a.version_json,
        a.batch02_runtime_report)


if __name__ == '__main__':
    main()
