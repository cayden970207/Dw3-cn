#!/usr/bin/env python3
"""Replay paging, a post-album save, and a fresh-process EU prototype reload.

Uses only copied private memory cards and normal controller inputs. All raw
screenshots and card data remain local; a passed report contains hashes only.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

from tools.album_regression import index_one, make_traces
from tools.collect_batch01_save_regression import light_mask
from tools.collect_batch03_runtime import FIXTURE_SHA
from tools.eu_font_prototype import EU_IMAGE_SHA
from tools.replay_card_album import BIOS_SHA1, EMULATOR_SHA256, REFERENCE_COMMIT, STACK_COMMIT, git_head
from tools.scan_text_tables import hash_file


def validate_listener_resets(log: str) -> int:
    """Recognize only the pinned replay's explicitly documented stack reset."""
    cursor, count = 0, 0
    for match in re.finditer('Error in event listener:', log):
        preceding = log[cursor:match.start()]
        if preceding.count('replay: Lua stack reset at frame ') != 1 or 'run.lua:391' not in preceding:
            raise ValueError('An unexpected emulator listener error occurred')
        count += 1
        cursor = match.end()
    if 'replay: Lua stack reset at frame ' in log[cursor:]:
        raise ValueError('Replay listener reset without the expected diagnostic')
    return count


def playtime(folder: Path, checkpoint: dict) -> int:
    raw = (folder / checkpoint['gamestate_file']).read_bytes()
    if len(raw) != 0x275c:
        raise ValueError('Unexpected private game-state size')
    hours, minutes, seconds = struct.unpack_from('<hhh', raw, 0x4c)
    if not (0 <= hours <= 999 and 0 <= minutes < 60 and 0 <= seconds < 60):
        raise ValueError('Unexpected game-state playtime')
    return hours*3600 + minutes*60 + seconds


def run(image: Path, creation: Path, fixture: Path, reviewed: Path, output: Path,
        reference: Path, emulator_path: Path, bios: Path, version_path: Path, heap_probe: bool = False):
    root = Path(__file__).resolve().parents[1]
    local = (root / 'local').resolve()
    image, creation, fixture, reviewed, output, reference = (p.resolve() for p in
        (image, creation, fixture, reviewed, output, reference))
    if output == local or any(not p.is_relative_to(local) for p in (image, creation, fixture, reviewed, output)):
        raise ValueError('Inputs and new output must stay under local/')
    creation_data = json.loads(creation.read_text())
    image_sha = hash_file(image)
    if (creation_data.get('status') != 'prototype_copy_created_runtime_pending' or
            creation_data.get('source_image_sha256') != EU_IMAGE_SHA or
            creation_data.get('test_image_sha256') != image_sha):
        raise ValueError('Image differs from its EU prototype creation report')
    if hash_file(fixture) != FIXTURE_SHA:
        raise ValueError('Independent fixture differs')
    cue = image.with_suffix('.cue')
    if cue.read_text() != f'FILE "{image.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n':
        raise ValueError('CUE differs from test image')
    if git_head(reference) != REFERENCE_COMMIT or git_head(reference / 'psxstack') != STACK_COMMIT:
        raise ValueError('Reference commits differ')
    if any(subprocess.run(['git', '-C', str(p), 'diff', '--quiet', 'HEAD']).returncode
           for p in (reference, reference / 'psxstack')):
        raise ValueError('Reference checkout has tracked changes')
    if hash_file(emulator_path) != EMULATOR_SHA256 or hashlib.sha1(bios.read_bytes()).hexdigest() != BIOS_SHA1:
        raise ValueError('Emulator or OpenBIOS differs')
    version = json.loads(version_path.read_text())
    if (version.get('version'), version.get('buildId')) != ('a74a0587', 433):
        raise ValueError('Emulator version differs')
    full, cold = make_traces(reference)
    album_at = index_one(full['steps'], lambda s: s.get('name') == 'album_open')
    first = {'name': 'eu_font_paging_and_save', 'max_frames': 80000, 'default_timeout': 3000,
             'steps': copy.deepcopy(cold['steps'] + full['steps'][album_at + 1:])}
    cold = copy.deepcopy(cold)
    for trace in (first, cold):
        if any(s.get('type') in ('write_mem', 'reset') for s in trace['steps']):
            raise ValueError('Replay must use a fresh process and no game-state injection')
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    sys.path.insert(0, str(reference / 'psxstack/tools/replay'))
    import emulator
    cfg = emulator.configure(root=reference, game_json=reference / 'port/game/game.json', redux_dir=output,
        iso=cue, scripts_dir=reference / 'tests/replay/scripts', expected_dir=output,
        probes=reference / 'tests/replay/probes.lua')
    cfg.redux, cfg.redux_version, cfg.openbios = emulator_path.resolve(), version_path.resolve(), bios.resolve()
    os.environ['DW3_REPLAY_RUN_LUA'] = str(reference / 'psxstack/tools/replay/run.lua')

    def phase(name, trace, source):
        folder = output / name
        folder.mkdir(mode=0o700)
        card = folder / 'memcard1.mcd'
        with source.open('rb') as src, card.open('xb') as dst:
            shutil.copyfileobj(src, dst)
        before = hash_file(card)
        if before != hash_file(source):
            raise ValueError('Independent card copy differs')
        script = folder / 'PRIVATE_trace.lua'
        script.write_text('return ' + emulator.lua_literal(emulator.parse_ints(trace)) + '\n')
        (folder / 'PRIVATE_trace.json').write_text(json.dumps(trace, indent=2) + '\n')
        env = emulator.replay_env(script, folder, speed=0)
        capture = root / 'tools/album_regression_capture.lua'
        if heap_probe:
            env['DW3_HEAP_NEXT_LUA'] = str(capture)
            capture = root / 'tools/eu_heap_album_capture.lua'
        cmd = [str(cfg.redux), '-no-ui', '-stdout', '-testmode', '-run', '-iso', str(cue),
               '-bios', str(cfg.openbios), '-memcard1', str(card), '-memcard2', str(folder / 'memcard2.mcd'),
               '-dofile', str(capture)]
        with (folder / 'emulator.log').open('w') as log:
            process = subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=600)
        result = json.loads((folder / 'result.json').read_text())
        if process.returncode or result.get('status') != 'ok':
            raise RuntimeError(f'{name} replay did not complete: {result.get("message")}')
        if hash_file(source) != before or hash_file(image) != image_sha:
            raise ValueError('Source card or test image changed')
        return folder, card, before, result

    first_dir, saved_card, old_sha, first_result = phase('first', first, fixture)
    if hash_file(saved_card) == old_sha or hash_file(fixture) != FIXTURE_SHA:
        raise ValueError('The independent post-album card was not changed')
    print('post_album_save_process_completed', flush=True)
    cold_dir, cold_card, cold_sha, cold_result = phase('cold', cold, saved_card)
    if cold_sha != hash_file(saved_card) or hash_file(cold_card) != cold_sha:
        raise ValueError('Cold phase did not use an unchanged copy of the saved card')
    first_cp = {c['name']: c for c in first_result['checkpoints']}
    cold_cp = {c['name']: c for c in cold_result['checkpoints']}
    for cp, required in ((first_cp, {'cold_loaded': 2, 'cold_album_visible': 18,
                                      'page_last_clamp': 18, 'page_first_return': 18,
                                      'post_album_saved': 12, 'post_album_save_returned': 2}),
                         (cold_cp, {'cold_loaded': 2, 'cold_album_visible': 18})):
        if any(name not in cp or cp[name]['stage'] != stage for name, stage in required.items()):
            raise ValueError('A required observed checkpoint is missing')
    log = (first_dir / 'emulator.log').read_text(errors='replace')
    cold_log = (cold_dir / 'emulator.log').read_text(errors='replace')
    # The pinned replay deliberately resets a known listener stack leak at
    # fixed frame intervals. Every listener error must match that diagnostic.
    listener_resets = {'first': validate_listener_resets(log),
                       'cold': validate_listener_resets(cold_log)}
    pages = [(int(page), int(frame)) for page, frame in re.findall(r'regression_page=(\d+) frame=(\d+)', log)]
    page_numbers = [p for p, _ in pages]
    if not page_numbers or page_numbers[0] != 1 or 2 not in page_numbers or 27 not in page_numbers or \
            1 not in page_numbers[page_numbers.index(27) + 1:]:
        raise ValueError('First, next, last and return-to-first page were not observed')
    start = re.search(r'regression_save_write_started_frame=(\d+)', log)
    finish = re.search(r'regression_save_done_frame=(\d+)', log)
    if not start or not finish or not (first_cp['page_first_return']['frame'] < int(start.group(1)) <
                                        int(finish.group(1)) <= first_cp['post_album_saved']['frame']):
        raise ValueError('A new write after album paging was not observed')
    if not (first_dir / 'PRIVATE_post_album_save.ppm').is_file():
        raise ValueError('Post-album save screen was not captured')
    title_region = (15, 24, 80, 39)
    expected = light_mask(reviewed, title_region)
    for folder in (first_dir, cold_dir):
        if light_mask(folder / 'PRIVATE_page_first.ppm', title_region) != expected:
            raise ValueError('Revisited album title differs from reviewed pixels')
    old_time = playtime(first_dir, first_cp['cold_loaded'])
    saved_time = playtime(first_dir, first_cp['post_album_saved'])
    loaded_time = playtime(cold_dir, cold_cp['cold_loaded'])
    if not old_time < loaded_time <= saved_time:
        raise ValueError('Cold-loaded game state does not reflect the later save')
    result = {'schema_version': 1, 'status': 'eu_font_prototype_post_album_save_cold_reload_verified',
              'source_image_sha256': EU_IMAGE_SHA, 'test_image_sha256': image_sha,
              'emulator': {'name': 'PCSX-Redux', 'version': 'a74a0587', 'build_id': 433,
                           'executable_sha256': EMULATOR_SHA256, 'bios': 'OpenBIOS', 'bios_sha1': BIOS_SHA1},
              'independent_card': {'fixture_sha256': FIXTURE_SHA, 'fixture_unchanged': True,
                                   'save_changed_card': True, 'fresh_process_loaded_saved_copy': True,
                                   'playtime_seconds': {'first_load': old_time, 'after_save': saved_time,
                                                        'cold_reload': loaded_time}},
              'paging': {'observed_page_min': min(page_numbers), 'observed_page_max': max(page_numbers),
                         'first_next_last_and_return_observed': True},
              'fresh_write_started_frame': int(start.group(1)), 'fresh_write_completed_frame': int(finish.group(1)),
              'first_album_before_save_frame': first_cp['cold_album_visible']['frame'],
              'cold_loaded_frame': cold_cp['cold_loaded']['frame'],
              'cold_album_reopened_frame': cold_cp['cold_album_visible']['frame'],
              'first_and_cold_title_pixels_match_reviewed': True,
              'pad_warning_count': {'first': log.count('Unknown command for pad: 43'),
                                    'cold': cold_log.count('Unknown command for pad: 43')},
              'known_replay_listener_stack_resets': listener_resets,
              'heap_probe_enabled': heap_probe,
              'limits': ['Only this four-glyph prototype and the listed replay route were exercised.',
                         'This does not validate a full banked font, the proposed codec, or other languages.']}
    with (output / 'SHARE_THIS_report.json').open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2); f.write('\n')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('image', 'creation', 'fixture', 'reviewed', 'output', 'reference', 'emulator', 'bios', 'version'):
        p.add_argument(name, type=Path)
    p.add_argument('--heap-probe', action='store_true')
    a = p.parse_args()
    result = run(a.image, a.creation, a.fixture, a.reviewed, a.output, a.reference, a.emulator, a.bios, a.version,
                 a.heap_probe)
    print(json.dumps({'status': result['status']}))


if __name__ == '__main__':
    main()
