#!/usr/bin/env python3
"""Cold-load an independent card and observe the EU album font prototype."""
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

from tools.album_regression import checkpoint
from tools.collect_batch03_runtime import FIXTURE_SHA
from tools.eu_font_prototype import EU_IMAGE_SHA
from tools.replay_card_album import BIOS_SHA1, EMULATOR_SHA256, REFERENCE_COMMIT, STACK_COMMIT, git_head
from tools.replay_card_shop import trace
from tools.scan_text_tables import hash_file


def run(image, fixture, output, reference, emulator_path, bios, version_path, creation=None):
    root = Path(__file__).resolve().parents[1]
    local = (root / 'local').resolve()
    image, fixture, output, reference = (p.resolve() for p in (image, fixture, output, reference))
    if output == local or not output.is_relative_to(local) or not image.is_relative_to(local) or not fixture.is_relative_to(local):
        raise ValueError('Image, fixture and new output must remain in local/')
    source_sha, target_sha = EU_IMAGE_SHA, hash_file(image)
    if target_sha != source_sha:
        if creation is None:
            raise ValueError('A test image requires its creation report')
        report = json.loads(creation.read_text())
        if (report.get('source_image_sha256') != source_sha or report.get('test_image_sha256') != target_sha or
                report.get('status') != 'prototype_copy_created_runtime_pending'):
            raise ValueError('Test image and creation report differ')
    elif creation is not None:
        raise ValueError('Original image must not use a test creation report')
    if hash_file(fixture) != FIXTURE_SHA:
        raise ValueError('Independent artificial fixture differs')
    cue = image.with_suffix('.cue')
    if cue.read_text() != f'FILE "{image.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n':
        raise ValueError('CUE does not bind the selected image')
    if git_head(reference) != REFERENCE_COMMIT or git_head(reference / 'psxstack') != STACK_COMMIT:
        raise ValueError('Reference commits differ from the inspected source')
    if any(subprocess.run(['git', '-C', str(p), 'diff', '--quiet', 'HEAD']).returncode
           for p in (reference, reference / 'psxstack')):
        raise ValueError('Reference checkout has tracked changes')
    if hash_file(emulator_path) != EMULATOR_SHA256 or hashlib.sha1(bios.read_bytes()).hexdigest() != BIOS_SHA1:
        raise ValueError('Emulator or OpenBIOS differs')
    version = json.loads(version_path.read_text())
    if version.get('version') != 'a74a0587' or version.get('buildId') != 433:
        raise ValueError('Emulator version metadata differs')
    spec = copy.deepcopy(trace(reference, 'album'))
    spec['name'] = 'eu_font_prototype_album'
    spec['steps'] += [
        {'type': 'press', 'buttons': ['TRIANGLE'], 'frames': 2, 'release': 10,
         'until': {'type': 'wait_stage', 'stage': 16}, 'timeout': 1500}, checkpoint('eu_album_closed'),
        {'type': 'wait_frames', 'frames': 120},
        {'type': 'press', 'buttons': ['CROSS'], 'frames': 2, 'release': 10,
         'until': {'type': 'wait_stage', 'stage': 18}, 'timeout': 1500},
        {'type': 'wait_frames', 'frames': 600}, checkpoint('eu_album_reopened'),
        {'type': 'press', 'buttons': ['TRIANGLE'], 'frames': 2, 'release': 10,
         'until': {'type': 'wait_stage', 'stage': 16}, 'timeout': 1500}, checkpoint('eu_album_returned')]
    if any(x.get('type') in ('write_mem', 'reset') for x in spec['steps']):
        raise ValueError('Replay must not inject RAM or reset')
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    card = output / 'memcard1.mcd'
    with fixture.open('rb') as src, card.open('xb') as dst:
        shutil.copyfileobj(src, dst)
    if hash_file(card) != FIXTURE_SHA:
        raise ValueError('Independent card copy differs')
    sys.path.insert(0, str(reference / 'psxstack/tools/replay'))
    import emulator
    cfg = emulator.configure(root=reference, game_json=reference / 'port/game/game.json', redux_dir=output, iso=cue,
                             scripts_dir=reference / 'tests/replay/scripts', expected_dir=output,
                             probes=reference / 'tests/replay/probes.lua')
    cfg.redux, cfg.redux_version, cfg.openbios = emulator_path.resolve(), version_path.resolve(), bios.resolve()
    os.environ['DW3_REPLAY_RUN_LUA'] = str(reference / 'psxstack/tools/replay/run.lua')
    script = output / 'PRIVATE_trace.lua'
    script.write_text('return ' + emulator.lua_literal(emulator.parse_ints(spec)) + '\n')
    (output / 'PRIVATE_trace.json').write_text(json.dumps(spec, indent=2) + '\n')
    env = emulator.replay_env(script, output, speed=0)
    command = [str(cfg.redux), '-no-ui', '-stdout', '-testmode', '-run', '-iso', str(cue),
               '-bios', str(cfg.openbios), '-memcard1', str(card), '-memcard2', str(output / 'memcard2.mcd'),
               '-dofile', str(root / 'tools/card_album_capture.lua')]
    with (output / 'emulator.log').open('w') as log:
        process = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=600)
    result = json.loads((output / 'result.json').read_text())
    if (process.returncode or result.get('status') != 'ok' or
            hash_file(fixture) != FIXTURE_SHA or hash_file(card) != FIXTURE_SHA or hash_file(image) != target_sha):
        raise RuntimeError('Replay failed or an input/independent card changed')
    if not (output / 'PRIVATE_card_album.ppm').is_file():
        raise RuntimeError('Replay did not produce the observed album screenshot')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('image', 'fixture', 'output'):
        p.add_argument(name, type=Path)
    for name in ('reference', 'emulator', 'bios', 'version-json'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--creation', type=Path)
    a = p.parse_args()
    result = run(a.image, a.fixture, a.output, a.reference, a.emulator, a.bios, a.version_json, a.creation)
    print(json.dumps({'status': result['status'], 'frames': result['frames'],
                      'checkpoints': [x['name'] for x in result['checkpoints']]}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
