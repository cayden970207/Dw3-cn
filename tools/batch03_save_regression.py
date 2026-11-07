#!/usr/bin/env python3
"""Visit both new shop paths, save a copied fixture, cold-load and revisit.

The fixture was deliberately prepared, not earned by normal play. The game
performs the save; only private, independently copied cards are written.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

try:
    from tools.album_regression import checkpoint, index_one, make_traces
    from tools.collect_batch03_runtime import FIXTURE_SHA
    from tools.replay_card_album import BIOS_SHA1, EMULATOR_SHA256, REFERENCE_COMMIT, STACK_COMMIT, git_head
    from tools.replay_card_shop import trace
    from tools.scan_text_tables import hash_file
    from tools.text_codec import CN_IMAGE_SHA256
except ModuleNotFoundError:
    from album_regression import checkpoint, index_one, make_traces
    from collect_batch03_runtime import FIXTURE_SHA
    from replay_card_album import BIOS_SHA1, EMULATOR_SHA256, REFERENCE_COMMIT, STACK_COMMIT, git_head
    from replay_card_shop import trace
    from scan_text_tables import hash_file
    from text_codec import CN_IMAGE_SHA256


def traces(reference: Path):
    buy = trace(reference, 'limit03')['steps']
    at = index_one(buy, lambda s: s.get('name') == 'batch03_limit_dismissed')
    visits = copy.deepcopy(buy[:at + 1]) + [
        {'type': 'press', 'buttons': ['TRIANGLE'], 'frames': 3, 'release': 120},
        {'type': 'wait_frames', 'frames': 250}, checkpoint('batch03_back_to_shop'),
        {'type': 'press', 'buttons': ['DOWN'], 'frames': 3, 'release': 90},
        {'type': 'press', 'buttons': ['CROSS'], 'frames': 3, 'release': 90},
        {'type': 'wait_frames', 'frames': 650}, checkpoint('batch03_save_booster_selection'),
        {'type': 'press', 'buttons': ['TRIANGLE'], 'frames': 3, 'release': 120},
        {'type': 'wait_frames', 'frames': 200}, checkpoint('batch03_save_booster_cancelled'),
        {'type': 'press', 'buttons': ['TRIANGLE'], 'frames': 2, 'release': 10,
         'until': {'type': 'wait_stage', 'stage': 2}, 'timeout': 2000},
        checkpoint('batch03_both_menus_left')]
    old_first, _ = make_traces(reference)
    field = index_one(old_first['steps'], lambda s: s.get('name') == 'field_return')
    tail = copy.deepcopy(old_first['steps'][field + 1:])
    for step in tail:
        if step.get('name') == 'post_album_saved':
            step['name'] = 'batch03_post_menu_saved'
        elif step.get('name') == 'post_album_save_returned':
            step['name'] = 'batch03_post_menu_save_returned'
    first, cold = visits + tail, copy.deepcopy(visits)
    if any(s.get('type') in ('reset', 'write_mem') for s in first + cold):
        raise ValueError('Replay may not inject RAM or reset the emulator')
    return ({'name': 'batch03_controlled_visit_then_save', 'max_frames': 30000, 'default_timeout': 3000, 'steps': first},
            {'name': 'batch03_controlled_cold_load_revisit', 'max_frames': 20000, 'default_timeout': 3000, 'steps': cold})


def run(image: Path, creation_path: Path, fixture: Path, output: Path, reference: Path,
        emulator_path: Path, bios: Path, version_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    local = (root / 'local').resolve()
    image, fixture, output, reference = (p.resolve() for p in (image, fixture, output, reference))
    if output == local or not output.is_relative_to(local) or not fixture.is_relative_to(local):
        raise ValueError('Fixture and new output must be private local paths')
    if hash_file(fixture) != FIXTURE_SHA:
        raise ValueError('Controlled fixture differs')
    creation = json.loads(creation_path.read_text())
    if (creation.get('source_image_sha256') != CN_IMAGE_SHA256 or
        hash_file(image) != creation.get('test_image_sha256') or
        {e['entry_index'] for e in creation['new_entries']} != {15, 19, 20, 25, 27}):
        raise ValueError('Expected the baseline-bound two-entry test copy')
    cue = image.with_suffix('.cue')
    if cue.read_text() != f'FILE "{image.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n':
        raise ValueError('CUE does not bind the selected test image')
    if git_head(reference) != REFERENCE_COMMIT or git_head(reference / 'psxstack') != STACK_COMMIT:
        raise ValueError('Reference revisions differ')
    if any(subprocess.run(['git', '-C', str(p), 'diff', '--quiet', 'HEAD']).returncode
           for p in (reference, reference / 'psxstack')):
        raise ValueError('Reference checkout has tracked modifications')
    if hashlib.sha1(bios.read_bytes()).hexdigest() != BIOS_SHA1 or hash_file(emulator_path) != EMULATOR_SHA256:
        raise ValueError('Emulator or OpenBIOS differs')
    version = json.loads(version_path.read_text())
    if version.get('version') != 'a74a0587' or version.get('buildId') != 433:
        raise ValueError('Unexpected emulator version')
    first, cold = traces(reference)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    sys.path.insert(0, str(reference / 'psxstack/tools/replay'))
    import emulator
    cfg = emulator.configure(root=reference, game_json=reference / 'port/game/game.json', redux_dir=output, iso=cue,
                             scripts_dir=reference / 'tests/replay/scripts', expected_dir=output,
                             probes=reference / 'tests/replay/probes.lua')
    cfg.redux, cfg.redux_version, cfg.openbios = emulator_path.resolve(), version_path.resolve(), bios.resolve()
    os.environ['DW3_REPLAY_RUN_LUA'] = str(reference / 'psxstack/tools/replay/run.lua')

    def phase(name, spec, source):
        folder = output / name
        folder.mkdir(mode=0o700)
        card = folder / 'memcard1.mcd'
        before = hash_file(source)
        with source.open('rb') as src, card.open('xb') as dst:
            shutil.copyfileobj(src, dst)
        if hash_file(card) != before:
            raise ValueError('Independent card copy differs')
        script = folder / 'PRIVATE_trace.lua'
        script.write_text('return ' + emulator.lua_literal(emulator.parse_ints(spec)) + '\n')
        (folder / 'PRIVATE_trace.json').write_text(json.dumps(spec, indent=2) + '\n')
        env = emulator.replay_env(script, folder, speed=0)
        env['DW3_CAPTURE_COMBINED'] = '1'
        env['DW3_CAPTURE_BATCH03'] = str(root / 'tools/batch03_runtime_capture.lua')
        command = [str(cfg.redux), '-no-ui', '-stdout', '-testmode', '-run', '-iso', str(cue),
                   '-bios', str(cfg.openbios), '-memcard1', str(card), '-memcard2', str(folder / 'memcard2.mcd'),
                   '-dofile', str(root / 'tools/batch03_save_regression_capture.lua')]
        with (folder / 'emulator.log').open('w') as log:
            proc = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=600)
        result = json.loads((folder / 'result.json').read_text())
        if proc.returncode or result.get('status') != 'ok' or hash_file(source) != before:
            raise RuntimeError(f'{name} failed or the source card changed')
        return hash_file(card), result

    saved, result = phase('first', first, fixture)
    if saved == FIXTURE_SHA:
        raise ValueError('The game did not modify its independent copied card')
    print('first_process_finished frames=' + str(result['frames']), flush=True)
    cold_sha, result = phase('cold', cold, output / 'first/memcard1.mcd')
    if cold_sha != saved or hash_file(image) != creation['test_image_sha256']:
        raise ValueError('Cold replay modified the card or the input image changed')
    print(json.dumps({'cold_status': result['status'], 'cold_frames': result['frames']}), flush=True)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('image', 'creation', 'fixture', 'output'):
        p.add_argument(name, type=Path)
    for name in ('reference', 'emulator', 'bios', 'version-json'):
        p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args()
    run(a.image, a.creation, a.fixture, a.output, a.reference, a.emulator, a.bios, a.version_json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
