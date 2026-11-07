#!/usr/bin/env python3
"""Audit the actual batch-04 replays and emit a sanitized, local report.

Screenshots, memory cards and original bytes are inputs only. The explicit
visual flag is meaningful only after inspecting all target screenshots.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import struct

from tools.byte_patch import make_patch
from tools.collect_batch03_runtime import FIXTURE_SHA, replay as booster_replay
from tools.collect_card_batch import compare_images, inspect_replay
from tools.collect_runtime_evidence import ppm
from tools.font_recovery_probe import ARCHIVE_SHA, FONT_LBA, FONT_SECTORS
from tools.layout_cn import Font1, measure, neutral_ink_bbox
from tools.make_card_album_sample import FONT_RAW_SHA, digest_extent
from tools.make_card_menu_batch02 import SCREEN_SHA, TARGETS, check_diff
from tools.make_card_menu_batch04 import SOURCE_SHA, prepare_return
from tools.psx_sector import USER_BEGIN, check_form1
from tools.replay_card_album import BIOS_SHA1, EMULATOR_SHA256
from tools.scan_text_tables import hash_file
from tools.text_codec import CN_C713, CN_IMAGE_SHA256, cn_canonical_code, load_reviewed_glyphs, tokenize

PREVIOUS_SHA = '389de62b2455a30431ea3acb340843060550f9f1cd58fbf645af9f824ac31c6f'
FINAL_SHA = '632d62e803a31fe9887c8733925663493205512dd9f1a332336c6ae1fd4849bb'
SELECTION_SOURCE_SHA = '985dc5e2da00139cfdf13c13e2d16403b51988bc0395f79c082281cd015ce3cf'


def state(directory: Path, name: str) -> dict:
    report = json.loads((directory / 'result.json').read_text())
    record = next(c for c in report['checkpoints'] if c['name'] == name)
    data = (directory / record['gamestate_file']).read_bytes()
    if len(data) != 0x275c:
        raise ValueError('Recorded game state has unexpected size')
    return {'frame': record['frame'], 'money': struct.unpack_from('<i', data, 0x6c)[0],
            'card_59_count': data[0x3a2 + 59]}


def buy_replay(directory: Path, fixture: Path) -> dict:
    if hash_file(fixture) != FIXTURE_SHA or hash_file(directory / 'memcard1.mcd') != FIXTURE_SHA:
        raise ValueError('Fixture or independent memory card unexpectedly changed')
    expected = {'cold_loaded': 2, 'card_shop_open': 19, 'batch04_shop_main': 19,
                'batch04_buy_list': 19, 'batch04_affordable_card': 19, 'batch04_buy_prompt': 19,
                'batch04_no_selected': 19, 'batch04_buy_cancelled': 19, 'batch04_buy_reopened': 19,
                'batch04_buy_completed': 19, 'batch04_buy_return_to_field': 2}
    checkpoints = inspect_replay(directory, expected)
    if [c['frame'] for c in checkpoints.values()] != sorted(c['frame'] for c in checkpoints.values()):
        raise ValueError('Purchase checkpoints are not in order')
    initial = state(directory, 'batch04_buy_prompt')
    no = state(directory, 'batch04_buy_cancelled')
    bought = state(directory, 'batch04_buy_completed')
    if ((initial['money'], initial['card_59_count']) != (10000, 1) or
            (no['money'], no['card_59_count']) != (10000, 1) or
            (bought['money'], bought['card_59_count']) != (9000, 2)):
        raise ValueError('No/purchase game state did not match expected outcome')
    log = (directory / 'emulator.log').read_text(errors='replace')
    if not all(s in log for s in ('batch04_question_cursor=0', 'batch04_question_cursor=1',
                                   'batch04_capture=buy_prompt', 'batch04_capture=buy_no_selected')):
        raise ValueError('Purchase confirmation and both selections were not observed')
    if not re.search(r'batch04_buy state=1 step=7 substep=0 frame=\d+ money=10000 card59_count=1 chosen=59 price=1000', log):
        raise ValueError('The purchased card and price are not evidenced by the actual game')
    return {'checkpoints': checkpoints, 'prompt': initial, 'after_no': no, 'after_buy': bought,
            'normal_input_only': True, 'fixture_saved': False,
            'pad_warning_count': log.count('Unknown command for pad: 43'),
            'listener_stack_reset_count': log.count('replay: Lua stack reset at frame 8192')}


def path_replay(directory: Path, fixture: Path, checkpoints: dict[str, int], marker: str) -> dict:
    if hash_file(fixture) != FIXTURE_SHA or hash_file(directory / 'memcard1.mcd') != FIXTURE_SHA:
        raise ValueError('Fixture or independent memory card unexpectedly changed')
    checked = inspect_replay(directory, checkpoints)
    log = (directory / 'emulator.log').read_text(errors='replace')
    if marker not in log:
        raise ValueError('Window capture state was not observed')
    return {'checkpoints': checked, 'normal_input_only': True, 'fixture_saved': False,
            'pad_warning_count': log.count('Unknown command for pad: 43'),
            'listener_stack_reset_count': log.count('replay: Lua stack reset at frame 8192')}


def icon_unchanged(before: Path, after: Path, x: int, y: int) -> bool:
    a, b = ppm(before), ppm(after)
    return all(a[(yy * 320 + xx) * 3:(yy * 320 + xx) * 3 + 3] ==
               b[(yy * 320 + xx) * 3:(yy * 320 + xx) * 3 + 3]
               for yy in range(y, y + 12) for xx in range(x, x + 16))


def run(root: Path, original_zip: Path, visual_checked: bool) -> dict:
    if not visual_checked:
        raise ValueError('Inspect the five local target screenshots before collecting visual evidence')
    local = root / 'local'
    recover = local / 'font-recovery'
    previous = recover / 'p1-batch03-v2/DW3-p1-batch02.bin'
    output = recover / 'p1-batch04-v2'
    image = output / 'DW3-p1-batch02.bin'
    creation = json.loads((output / 'SHARE_THIS_batch04_creation_report.json').read_text())
    idx = json.loads((local / 'dw3-probe/20261107T092435Z-03a090/PRIVATE_DO_NOT_UPLOAD.json').read_text())
    glyph_review = load_reviewed_glyphs(root / 'data/baselines/c7130c53-reviewed-glyphs.json')
    if (hash_file(previous) != PREVIOUS_SHA or hash_file(image) != FINAL_SHA or
            creation.get('test_image_sha256') != FINAL_SHA or creation.get('source_image_sha256') != CN_IMAGE_SHA256 or
            idx.get('image_sha256') != CN_IMAGE_SHA256 or hash_file(original_zip) != ARCHIVE_SHA or
            not all(creation.get(k) is True for k in ('previous_batch_changes_preserved',
                'all_differences_within_reviewed_text_or_modified_sector_checksums',
                'modified_sector_edc_ecc_before_and_after_valid'))):
        raise ValueError('Image, archive or creation evidence differs')
    f = next(x for x in idx['text_files'] if x['file_id'] == 52)
    if f['lba'] != 2313:
        raise ValueError('Resource address differs')
    rows = {e['index']: e for e in f['entries'] if e['table'] is None}
    ranges = []
    for n in (4, 13, 14, 16):
        row = rows[n]
        if row['raw_sha256'] != (SOURCE_SHA if n == 4 else TARGETS[n][4]):
            raise ValueError('Target preimage differs')
        begin = 2313 * 2352 + USER_BEGIN + row['offset_bytes']
        ranges.append((begin, begin + row['encoded_bytes']))
    delta = make_patch(previous, image)
    check_diff(delta, ranges, {2313})
    for disc in (previous, image):
        with disc.open('rb') as stream:
            stream.seek(2313 * 2352)
            if not check_form1(stream.read(2352)):
                raise ValueError('Previous or final modified sector fails EDC/ECC')
        if digest_extent(disc, FONT_LBA * 2352, FONT_SECTORS * 2352) != FONT_RAW_SHA:
            raise ValueError('Protected font resource changed')
    font = Font1((recover / 'run-20261107/PRIVATE_executable.bin').read_bytes(),
                 (recover / 'run-20261107/PRIVATE_647_userdata.bin').read_bytes())
    fixture = recover / 'p1-batch03-fixture-v1/memcard1.mcd'
    dirs = {name: recover / folder for name, folder in {
        'source_buy': 'p1-batch04-source-buy-v1', 'source_shop': 'p1-batch04-source-shop-v1',
        'source_deck': 'p1-batch04-source-deck-v1', 'source_booster': 'p1-batch03-test-booster-v2',
        'test_buy': 'p1-batch04-final-buy-v1', 'test_shop': 'p1-batch04-final-shop-v1',
        'test_deck': 'p1-batch04-final-deck-v1', 'test_booster': 'p1-batch04-final-booster-v1'}.items()}
    shop_cp = {'cold_loaded': 2, 'batch04_shop_main': 19, 'batch04_shop_return_to_field': 2}
    deck_cp = {'cold_loaded': 2, 'batch04_editor_visible': 4, 'batch04_editor_closed': 4,
               'batch04_editor_return_to_party': 16}
    runtime = {
        'source_buy': buy_replay(dirs['source_buy'], fixture), 'test_buy': buy_replay(dirs['test_buy'], fixture),
        'source_booster': booster_replay(dirs['source_booster'], fixture, 'booster', require_next=True),
        'test_booster': booster_replay(dirs['test_booster'], fixture, 'booster', require_next=True),
        'source_shop': path_replay(dirs['source_shop'], fixture, shop_cp, 'batch04_capture=shop_main'),
        'test_shop': path_replay(dirs['test_shop'], fixture, shop_cp, 'batch04_capture=shop_main'),
        'source_deck': path_replay(dirs['source_deck'], fixture, deck_cp, 'batch04_capture=deck_editor'),
        'test_deck': path_replay(dirs['test_deck'], fixture, deck_cp, 'batch04_capture=deck_editor'),
    }
    codes = {ch: cn_canonical_code(index) for index, ch in glyph_review.items()}
    created_rows = {e['entry_index']: e for e in creation['new_entries']}
    entries = []
    for n in (13, 14, 16):
        target, x, y, context, digest = TARGETS[n]
        before_dir, after_dir, filename, rect = (
            ('source_buy', 'test_buy', 'PRIVATE_buy_prompt.ppm', (196, y, 240, y + 13)) if n in (13, 14) else
            ('source_booster', 'test_booster', 'PRIVATE_booster_opened.ppm', (19, 194, 163, 207)))
        before, after = dirs[before_dir] / filename, dirs[after_dir] / filename
        if hash_file(before) != SCREEN_SHA[context] or created_rows[n]['translation'] != target:
            raise ValueError('Source screen or reviewed target differs')
        original = bytes.fromhex(rows[n]['raw_hex'])
        replacement = b''.join(codes[c] for c in target) + b'\0'
        if (len(replacement) > len(original) or any(t.kind == 'control' for t in
                tokenize(original, variant=CN_C713) + tokenize(replacement, variant=CN_C713))):
            raise ValueError('Text capacity or control sequence differs')
        old, new = measure(font, original), measure(font, replacement)
        def absolute(box):
            return [box[0]+x, box[1]+y, box[2]+x, box[3]+y]
        source_box = absolute(old['ink_bbox_relative_exclusive'])
        target_box = absolute(new['ink_bbox_relative_exclusive'])
        if neutral_ink_bbox(before, rect) != source_box or neutral_ink_bbox(after, rect) != target_box:
            raise ValueError('Observed text pixels do not match inspected font metrics')
        entries.append({'file_id': 52, 'entry_index': n, 'source_sha256': digest, 'translation': target,
                        'source_capacity_bytes': len(original), 'replacement_bytes_including_terminator': len(replacement),
                        'source_control_count': 0, 'replacement_control_count': 0,
                        'source_advance_px': old['draw_max_advance_px'], 'replacement_advance_px': new['draw_max_advance_px'],
                        'legacy_game_measure_static_px': new['game_measure_max_px'],
                        'game_measure_result_used_by_this_inspected_window': False,
                        'source_ink_bbox_px': source_box, 'runtime_ink_bbox_px': target_box,
                        'source_screenshot_sha256': hash_file(before), 'runtime_screenshot_sha256': hash_file(after),
                        'status': 'runtime_visual_and_pixel_bounds_verified_with_artificial_fixture'})
    comparisons = {
        'buy': compare_images(dirs['source_buy'] / 'PRIVATE_buy_prompt.ppm', dirs['test_buy'] / 'PRIVATE_buy_prompt.ppm',
                              [(196,86,240,99), (196,102,240,115)]),
        'shop': compare_images(dirs['source_shop'] / 'PRIVATE_shop_main.ppm', dirs['test_shop'] / 'PRIVATE_shop_main.ppm',
                               [(227,204,300,217)]),
        'deck': compare_images(dirs['source_deck'] / 'PRIVATE_deck_editor.ppm', dirs['test_deck'] / 'PRIVATE_deck_editor.ppm',
                               [(209,23,283,36)]),
        'booster_selection': compare_images(dirs['source_booster'] / 'PRIVATE_booster_selection.ppm',
                                            dirs['test_booster'] / 'PRIVATE_booster_selection.ppm', [(36,208,110,221)]),
        'booster_opened': compare_images(dirs['source_booster'] / 'PRIVATE_booster_opened.ppm',
                                         dirs['test_booster'] / 'PRIVATE_booster_opened.ppm',
                                         [(19,194,163,207), (36,208,110,221)]),
    }
    if hash_file(dirs['source_booster'] / 'PRIVATE_booster_selection.ppm') != SELECTION_SOURCE_SHA:
        raise ValueError('Original booster selection screenshot differs')
    shared = creation['shared_return_entry']
    replacement = prepare_return(bytes.fromhex(rows[4]['raw_hex']), glyph_review)
    suffix = b''.join(t.raw for t in tokenize(replacement, variant=CN_C713)[2:])
    metric = measure(font, suffix)
    return_paths = [('shop', 'source_shop', 'test_shop', 'PRIVATE_shop_main.ppm'),
                    ('deck_editor', 'source_deck', 'test_deck', 'PRIVATE_deck_editor.ppm'),
                    ('booster', 'source_booster', 'test_booster', 'PRIVATE_booster_opened.ppm'),
                    ('booster_selection', 'source_booster', 'test_booster', 'PRIVATE_booster_selection.ppm')]
    contexts = []
    for name, before_dir, after_dir, filename in return_paths:
        context = next(c for c in shared['contexts'] if c['context'] == ('booster' if name == 'booster_selection' else name))
        x, y = context['origin_px']
        before, after = dirs[before_dir] / filename, dirs[after_dir] / filename
        box = metric['ink_bbox_relative_exclusive']
        predicted = [box[0]+x+18, box[1]+y, box[2]+x+18, box[3]+y]
        actual = neutral_ink_bbox(after, (x+16, y, min(x+90, 320), y+13))
        if actual != predicted or not icon_unchanged(before, after, x, y):
            raise ValueError('Return text bounds or the original button icon changed in ' + name)
        contexts.append({'context': name, 'origin_px': [x, y], 'runtime_text_ink_bbox_px': actual,
                         'original_button_icon_pixels_unchanged': True,
                         'source_screenshot_sha256': hash_file(before), 'runtime_screenshot_sha256': hash_file(after),
                         'status': 'runtime_visual_and_pixel_bounds_verified_with_artificial_fixture'})
    entries.append({'file_id': 52, 'entry_index': 4, 'source_sha256': SOURCE_SHA, 'translation': '返回',
                    'source_capacity_bytes': shared['slot_bytes'],
                    'replacement_bytes_including_terminator': shared['replacement_bytes_including_terminator'],
                    'source_control_count': 0, 'replacement_control_count': 0,
                    'source_advance_px': shared['source_advance_px'],
                    'replacement_advance_px': shared['replacement_advance_px'],
                    'legacy_game_measure_static_px': shared['legacy_game_measure_static_px'],
                    'game_measure_result_used_by_these_inspected_windows': False,
                    'original_icon_and_spacing_tokens_preserved': True, 'contexts': contexts,
                    'status': 'runtime_visual_and_pixel_bounds_verified_with_artificial_fixture'})
    return {'schema_version': 1, 'status': 'four_new_entries_runtime_verified_with_artificial_fixture',
            'source_image_sha256': CN_IMAGE_SHA256, 'previous_test_image_sha256': PREVIOUS_SHA,
            'test_image_sha256': FINAL_SHA, 'input_cue': 'local/font-recovery/p1-batch04-v2/DW3-p1-batch02.cue',
            'new_translated_entries': [4,13,14,16], 'entries': entries,
            'fixture': {'kind': 'artificial_condition_fixture_not_normal_play', 'sha256': FIXTURE_SHA,
                        'original_fixture_and_independent_cards_unchanged': True, 'seed_money': 10000,
                        'seed_booster_item_91': 1, 'seed_card_59_count': 1},
            'emulator': {'name': 'PCSX-Redux', 'version': 'a74a0587', 'build_id': 433,
                         'executable_sha256': EMULATOR_SHA256, 'bios': 'OpenBIOS', 'bios_sha1': BIOS_SHA1,
                         'mode': 'headless, unthrottled, separate process and copied card for each path',
                         'capture_size_px': [320,240]},
            'integrity': {'all_other_raw_bytes_unchanged_from_previous': True,
                          'incremental_changed_bytes': delta['changed_bytes'], 'changed_sectors': [2313],
                          'all_differences_from_original_within_approved_text_or_sector_checksums': True,
                          'original_and_final_modified_sectors_edc_ecc_valid': True,
                          'font_id_647_unchanged': True, 'source_archive_sha256': ARCHIVE_SHA,
                          'generic_writer_unblocked': False},
            'runtime': runtime, 'before_after_pixel_comparisons': comparisons,
            'private_evidence_directories': {k: str(v.relative_to(root)) for k,v in dirs.items()},
            'limits': ['Only the named source and target windows were observed; other callers and fonts remain unresolved.',
                       'The exact clipping bounds remain unresolved; each translation fits the observed source band.',
                       'Game measurement values are static simulations; the generic writer remains blocked.',
                       'This batch did not perform an after-menu save/cold-load regression.',
                       'No retail BIOS, hardware, or whole-game regression was performed.']}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original_zip', type=Path)
    parser.add_argument('report', type=Path)
    parser.add_argument('--visual-checked', action='store_true')
    args = parser.parse_args()
    try:
        result = run(Path(__file__).resolve().parents[1], args.original_zip, args.visual_checked)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        result = {'schema_version': 1, 'status': 'blocked', 'error_type': type(exc).__name__,
                  'error': str(exc)[:500], 'new_entries_verified': 0}
    with os.fdopen(os.open(args.report, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2); stream.write('\n')
    print(json.dumps({'status': result['status'], 'report': str(args.report)}))
    return 2 if result['status'] == 'blocked' else 0


if __name__ == '__main__':
    raise SystemExit(main())
