#!/usr/bin/env python3
"""Check both controlled shop paths and publish only sanitized evidence.

The source card is an explicitly artificial fixture. The emulator executes
normal input, and these checks never equate the fixture with earned progress.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import struct

try:
    from tools.byte_patch import make_patch
    from tools.collect_card_batch import compare_images, inspect_replay
    from tools.font_recovery_probe import FONT_LBA, FONT_SECTORS
    from tools.layout_cn import Font1, measure, neutral_ink_bbox
    from tools.make_card_album_sample import FONT_RAW_SHA, digest_extent
    from tools.make_card_menu_batch02 import SCREEN_SHA, TARGETS, check_diff
    from tools.psx_sector import USER_BEGIN, check_form1
    from tools.replay_card_album import BIOS_SHA1, EMULATOR_SHA256
    from tools.scan_text_tables import hash_file
    from tools.text_codec import CN_C713, CN_IMAGE_SHA256, cn_canonical_code, load_reviewed_glyphs, tokenize
except ModuleNotFoundError:
    from byte_patch import make_patch
    from collect_card_batch import compare_images, inspect_replay
    from font_recovery_probe import FONT_LBA, FONT_SECTORS
    from layout_cn import Font1, measure, neutral_ink_bbox
    from make_card_album_sample import FONT_RAW_SHA, digest_extent
    from make_card_menu_batch02 import SCREEN_SHA, TARGETS, check_diff
    from psx_sector import USER_BEGIN, check_form1
    from replay_card_album import BIOS_SHA1, EMULATOR_SHA256
    from scan_text_tables import hash_file
    from text_codec import CN_C713, CN_IMAGE_SHA256, cn_canonical_code, load_reviewed_glyphs, tokenize

FIXTURE_SHA = 'd8f4fefbe2186ae3c8fae382f3573cdaf3403cd676800535aab7f5f63dca4624'
PREVIOUS_SHA = '98ba88ad5c27c1fa60c04ac9645ccd85f9d23ea7296de46b40471fdd47361353'
PATHS = {
    15: ('booster', 'PRIVATE_booster_selection.ppm', (19, 194, 163, 207)),
    20: ('limit', 'PRIVATE_nine_card_limit.ppm', (153, 57, 297, 70)),
}


def checkpoint_state(folder: Path, name: str) -> dict:
    result = json.loads((folder / 'result.json').read_text())
    row = next(x for x in result['checkpoints'] if x['name'] == name)
    data = (folder / row['gamestate_file']).read_bytes()
    if len(data) != 0x275c:
        raise ValueError('Checkpoint game-state size differs')
    return {'frame': row['frame'], 'money': struct.unpack_from('<i', data, 0x6c)[0],
            'booster_item_91': data[0x7c + 91], 'card_65': data[0x3a2 + 65]}


def replay(folder: Path, fixture: Path, kind: str, require_next: bool = False) -> dict:
    if hash_file(fixture) != FIXTURE_SHA or hash_file(folder / 'memcard1.mcd') != FIXTURE_SHA:
        raise ValueError('Independent fixture changed or was unexpectedly saved')
    if kind == 'booster':
        required = {'cold_loaded': 2, 'batch03_shop_main': 19, 'batch03_booster_selection': 19,
                    'batch03_booster_cancelled': 19, 'batch03_booster_reopened': 19,
                    'batch03_booster_opened': 19, 'batch03_booster_next_card': 19,
                    'batch03_booster_returned': 19, 'batch03_booster_return_to_field': 2}
    else:
        required = {'cold_loaded': 2, 'batch03_shop_main': 19, 'batch03_buy_open': 19,
                    'batch03_nine_card_limit': 19, 'batch03_limit_dismissed': 19,
                    'batch03_limit_return_to_field': 2}
    cp = inspect_replay(folder, required)
    if [v['frame'] for v in cp.values()] != sorted(v['frame'] for v in cp.values()):
        raise ValueError('Checkpoints are not in the expected order')
    log = (folder / 'emulator.log').read_text(errors='replace')
    if kind == 'booster':
        selection = checkpoint_state(folder, 'batch03_booster_selection')
        reopened = checkpoint_state(folder, 'batch03_booster_reopened')
        opened = checkpoint_state(folder, 'batch03_booster_opened')
        if (selection['booster_item_91'] != 1 or reopened['booster_item_91'] != 1 or
            opened['booster_item_91'] != 0 or log.count('batch03_booster state=1 step=4') < 2 or
            'batch03_booster state=1 step=50 substep=0' not in log or
            'batch03_booster state=1 step=13' not in log or
            'batch03_capture=booster_selection' not in log):
            raise ValueError('Selection, cancellation, reopening or actual opening was not observed')
        if require_next and not all(s in log for s in ('batch03_booster_card_shown=0',
                                                       'batch03_booster_card_shown=1',
                                                       'batch03_capture=booster_next_card')):
            raise ValueError('The next-card input was not observed to change selection')
        state = {'selection': selection, 'reopened': reopened, 'opened': opened,
                 'next_card_change_observed': require_next}
    else:
        state = checkpoint_state(folder, 'batch03_nine_card_limit')
        pattern = r'batch03_buy state=1 step=12 substep=20 frame=\d+ money=10000 booster_count=1 card65_count=9 chosen=65 price=10000'
        if (state['money'] != 10000 or state['card_65'] != 9 or not re.search(pattern, log) or
            'batch03_buy state=1 step=13 substep=20' not in log or
            'batch03_capture=nine_card_limit' not in log):
            raise ValueError('Limit state, sufficient money, actual selection or dismissal missing')
    return {'checkpoints': cp, 'state_evidence': state,
            'pad_warning_count': log.count('Unknown command for pad: 43'),
            'listener_stack_reset_count': log.count('replay: Lua stack reset at frame 8192'),
            'normal_input_only': True, 'fixture_saved': False}


def run(previous: Path, image: Path, creation_path: Path, index_path: Path, glyph_path: Path,
        exe_path: Path, font_path: Path, fixture: Path, source_booster: Path, source_limit: Path,
        after_booster: Path, after_limit: Path, visual_checked: bool) -> dict:
    if not visual_checked:
        raise ValueError('Inspect the actual screenshots before asserting visual review')
    root = Path(__file__).resolve().parents[1]
    local = (root / 'local').resolve()
    private_paths = [image, fixture, source_booster, source_limit, after_booster, after_limit]
    if any(not p.resolve().is_relative_to(local) for p in private_paths):
        raise ValueError('Runtime image, fixture and observations must remain private')
    creation, index = json.loads(creation_path.read_text()), json.loads(index_path.read_text())
    if (hash_file(previous) != PREVIOUS_SHA or hash_file(image) != creation.get('test_image_sha256') or
        creation.get('source_image_sha256') != CN_IMAGE_SHA256 or index.get('image_sha256') != CN_IMAGE_SHA256 or
        not all(creation.get(k) is True for k in ('previous_batch_changes_preserved',
            'all_differences_within_reviewed_text_or_modified_sector_checksums',
            'modified_sector_edc_ecc_before_and_after_valid'))):
        raise ValueError('Image or baseline-bound integrity report differs')
    rows = {e['entry_index']: e for e in creation['new_entries']}
    if not {15, 19, 25, 27} <= set(rows) or not set(rows) <= {15, 19, 20, 25, 27}:
        raise ValueError('Unexpected translated entry set')
    added = set(rows) - {19, 25, 27}
    f = next(x for x in index['text_files'] if x['file_id'] == 52)
    if f['lba'] != 2313:
        raise ValueError('Unexpected source resource location')
    ranges = []
    for n in sorted(added):
        row = next(r for r in f['entries'] if r['table'] is None and r['index'] == n)
        if row['raw_sha256'] != TARGETS[n][4]:
            raise ValueError('Original entry hash differs')
        start = 2313 * 2352 + USER_BEGIN + row['offset_bytes']
        ranges.append((start, start + row['encoded_bytes']))
    delta = make_patch(previous, image)
    check_diff(delta, ranges, {2313})
    for disc in (previous, image):
        with disc.open('rb') as stream:
            stream.seek(2313 * 2352)
            if not check_form1(stream.read(2352)):
                raise ValueError('Changed sector EDC/ECC is invalid')
        if digest_extent(disc, FONT_LBA * 2352, FONT_SECTORS * 2352) != FONT_RAW_SHA:
            raise ValueError('Protected font extent differs')
    font = Font1(exe_path.read_bytes(), font_path.read_bytes())
    review = load_reviewed_glyphs(glyph_path)
    by_char = {v: k for k, v in review.items()}
    evidence = {
        'source_booster': replay(source_booster, fixture, 'booster'),
        'source_limit': replay(source_limit, fixture, 'limit'),
        'test_booster': replay(after_booster, fixture, 'booster', require_next=True),
        'test_limit': replay(after_limit, fixture, 'limit'),
    }
    entries = []
    for n, (kind, filename, rect) in PATHS.items():
        before, after = (source_booster, after_booster) if n == 15 else (source_limit, after_limit)
        source_picture, target_picture = before / filename, after / filename
        if hash_file(source_picture) != SCREEN_SHA[kind]:
            raise ValueError('Original screenshot differs from its inspected source')
        target, x, y, _, digest = TARGETS[n]
        row = next(r for r in f['entries'] if r['table'] is None and r['index'] == n)
        raw = bytes.fromhex(row['raw_hex'])
        if row['raw_sha256'] != digest or any(t.kind == 'control' for t in tokenize(raw, variant=CN_C713)):
            raise ValueError('Source entry or control sequence differs')
        old = measure(font, raw)
        b = old['ink_bbox_relative_exclusive']
        predicted = [b[0] + x, b[1] + y, b[2] + x, b[3] + y]
        if neutral_ink_bbox(source_picture, rect) != predicted:
            raise ValueError('Source screen and source font metrics differ')
        item = {'file_id': 52, 'entry_index': n, 'source_sha256': digest,
                'source_bytes_including_terminator': len(raw), 'source_control_count': 0,
                'source_advance_px': old['draw_max_advance_px'], 'source_ink_bbox_px': predicted,
                'source_screenshot_sha256': hash_file(source_picture),
                'source_runtime_status': 'verified_with_artificial_fixture',
                'translation_status': 'unmodified_runtime_pending',
                'game_measure_result_used_for_this_window': False}
        if n in rows:
            if rows[n]['translation'] != target or any(ch not in by_char for ch in target):
                raise ValueError('Target or user-reviewed glyphs differ')
            encoded = b''.join(cn_canonical_code(by_char[ch]) for ch in target) + b'\0'
            metric = measure(font, encoded)
            bounds = metric['ink_bbox_relative_exclusive']
            bounds = [bounds[0]+x, bounds[1]+y, bounds[2]+x, bounds[3]+y]
            actual = neutral_ink_bbox(target_picture, rect)
            if (actual != bounds or actual != rows[n]['replacement_ink_bbox_px'] or
                metric['draw_max_advance_px'] != rows[n]['replacement_advance_px']):
                raise ValueError('Translated screenshot does not match actual font pixels')
            comparison = compare_images(source_picture, target_picture, [rect])
            item.update({'translation': target, 'replacement_bytes_including_terminator': len(encoded),
                'replacement_control_count': 0, 'replacement_advance_px': metric['draw_max_advance_px'],
                'game_legacy_measure_px': metric['game_measure_max_px'], 'runtime_ink_bbox_px': actual,
                'runtime_screenshot_sha256': hash_file(target_picture), 'pixel_comparison': comparison,
                'translation_status': 'runtime_visual_and_pixel_bounds_verified_with_artificial_fixture'})
        entries.append(item)
    return {'schema_version': 1, 'status': 'recorded_actual_shop_paths_with_artificial_fixture',
            'source_image_sha256': CN_IMAGE_SHA256, 'previous_test_image_sha256': PREVIOUS_SHA,
            'test_image_sha256': creation['test_image_sha256'], 'new_translated_entries': sorted(added),
            'input_cue': str(image.resolve().with_suffix('.cue').relative_to(root)),
            'private_evidence_directories': {name: str(path.resolve().relative_to(root)) for name, path in
                (('source_booster', source_booster), ('source_limit', source_limit),
                 ('test_booster', after_booster), ('test_limit', after_limit))},
            'fixture': {'kind': 'artificial_condition_fixture_not_normal_play', 'sha256': FIXTURE_SHA,
                        'seed_unchanged': True, 'money': 10000, 'booster_item_id': 91,
                        'booster_count': 1, 'shop_card_id': 65, 'shop_card_count': 9},
            'emulator': {'name': 'PCSX-Redux', 'version': 'a74a0587', 'build_id': 433,
                         'executable_sha256': EMULATOR_SHA256, 'bios': 'OpenBIOS', 'bios_sha1': BIOS_SHA1,
                         'mode': 'headless -no-ui -testmode, unthrottled; arm64 dynarec; dummy audio',
                         'capture_size_px': [320, 240], 'gpu_backend': 'not identified in the captured log'},
            'integrity': {'all_other_raw_bytes_unchanged_from_previous': True,
                          'incremental_changed_bytes': delta['changed_bytes'], 'changed_sectors': [2313],
                          'changed_sector_edc_ecc_valid': True, 'font_id_647_unchanged': True,
                          'generic_writer_unblocked': False},
            'entries': entries, 'runtime': evidence,
            'limitations': ['Fixture fields were deliberately edited on an isolated memory-card copy; no natural acquisition is claimed.',
                'Only the recorded PCSX-Redux/OpenBIOS paths and these specific windows were exercised.',
                'Pad 43 warnings and a periodic listener stack-reset message remain in the local logs; recorded state and pixels verify only the reported actions.',
                'Exact clipping bounds remain unresolved; each translation fits within the observed source text band.',
                'Unmodified entries remain runtime_pending as translations.',
                'Private screenshots, memory cards, original bytes and game assets are not included.']}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('previous', 'image', 'creation', 'index', 'glyphs', 'exe', 'font', 'fixture',
                 'source_booster', 'source_limit', 'after_booster', 'after_limit', 'report'):
        p.add_argument(name, type=Path)
    p.add_argument('--visual-checked', action='store_true')
    a = p.parse_args()
    try:
        result = run(a.previous, a.image, a.creation, a.index, a.glyphs, a.exe, a.font, a.fixture,
                     a.source_booster, a.source_limit, a.after_booster, a.after_limit, a.visual_checked)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        result = {'schema_version': 1, 'status': 'blocked', 'error_type': type(exc).__name__,
                  'error': str(exc)[:500]}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(a.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as out:
        json.dump(result, out, ensure_ascii=False, indent=2); out.write('\n')
    print(json.dumps({'status': result['status'], 'report': str(a.report)}))
    return 2 if result['status'] == 'blocked' else 0


if __name__ == '__main__':
    raise SystemExit(main())
