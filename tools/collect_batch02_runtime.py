#!/usr/bin/env python3
"""Verify and summarize actual batch-02 menu replays, without sharing assets."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path

try:
    from tools.collect_batch01_save_regression import light_mask
    from tools.collect_card_batch import compare_images, inspect_replay
    from tools.layout_cn import actual_assets, measure, neutral_ink_bbox
    from tools.make_card_menu_batch02 import P1_SHA, TARGETS, SCREEN_SHA
    from tools.replay_card_album import BIOS_SHA1, EMULATOR_SHA256
    from tools.scan_text_tables import hash_file
    from tools.text_codec import CN_IMAGE_SHA256, cn_canonical_code, load_reviewed_glyphs
except ModuleNotFoundError:
    from collect_batch01_save_regression import light_mask
    from collect_card_batch import compare_images, inspect_replay
    from layout_cn import actual_assets, measure, neutral_ink_bbox
    from make_card_menu_batch02 import P1_SHA, TARGETS, SCREEN_SHA
    from replay_card_album import BIOS_SHA1, EMULATOR_SHA256
    from scan_text_tables import hash_file
    from text_codec import CN_IMAGE_SHA256, cn_canonical_code, load_reviewed_glyphs


def run(image: Path, original: Path, audit: Path, glyphs: Path, creation_path: Path, seed: Path,
        before_deck: Path, before_buy: Path, after_deck: Path, after_buy: Path,
        after_album: Path, visual_checked: bool):
    if not visual_checked:
        raise ValueError('Inspect each translated screen before marking runtime visual review')
    creation = json.loads(creation_path.read_text())
    if (creation.get('source_image_sha256') != CN_IMAGE_SHA256 or creation.get('previous_batch_image_sha256') != P1_SHA or
        creation.get('test_image_sha256') != hash_file(image) or
        not all(creation.get(k) is True for k in ('previous_batch_changes_preserved',
                'all_differences_within_reviewed_text_or_modified_sector_checksums',
                'modified_sector_edc_ecc_before_and_after_valid'))):
        raise ValueError('Creation report or test image failed integrity binding')
    new_entries = creation['new_entries']
    selected = {e['entry_index'] for e in new_entries}
    if not selected or len(selected) != len(new_entries) or not selected <= set(TARGETS):
        raise ValueError('Unexpected batch-02 entries')
    review = load_reviewed_glyphs(glyphs)
    codes = {ch: cn_canonical_code(i) for i, ch in review.items()}
    font = actual_assets(original, json.loads(audit.read_text()))
    cases = {25: ('selection', before_deck, after_deck, 'PRIVATE_deck_selection.ppm', (150,34,294,47)),
             27: ('rename', before_deck, after_deck, 'PRIVATE_deck_menu.ppm', (166,49,310,62)),
             19: ('buy', before_buy, after_buy, 'PRIVATE_insufficient_funds.ppm', (153,57,297,70))}
    comparisons, screens = {}, {}
    for entry in new_entries:
        n = entry['entry_index']
        target, x, y, context, digest = TARGETS[n]
        if (entry['translation'] != target or entry['original_sha256'] != digest or
                entry['original_control_count'] != 0 or entry['replacement_control_count'] != 0 or
                any(ch not in codes for ch in target)):
            raise ValueError('Entry or reviewed glyph mapping differs from the inspected target')
        encoded = b''.join(codes[ch] for ch in target) + b'\0'
        metric = measure(font, encoded)
        name, before, after, filename, rect = cases[n]
        if name != context or hash_file(before / filename) != SCREEN_SHA[context]:
            raise ValueError('Before screenshot does not match its verified source window')
        actual = neutral_ink_bbox(after / filename, rect)
        if actual != entry['replacement_ink_bbox_px'] or metric['draw_max_advance_px'] != entry['replacement_advance_px']:
            raise ValueError('Translated screen ink differs from the actual font prediction')
        comparisons[str(n)] = compare_images(before / filename, after / filename, [rect])
        entry['runtime_ink_bbox_px'] = actual
        entry['runtime_screenshot_sha256'] = hash_file(after / filename)
        entry['game_measure_result_used_by_window'] = False
        entry['validated_source_text_band_px'] = [x, y, x + entry['source_advance_px'], y + 12]
        entry['exact_window_clipping_bounds'] = 'unresolved; target fits the observed source band and screen'
        entry['status'] = 'runtime_visual_and_pixel_bounds_verified'
        screens[str(n)] = entry['runtime_screenshot_sha256']
    deck_cp = inspect_replay(after_deck, {'batch02_deck_selection': 4, 'batch02_deck_menu': 4,
        'batch02_menu_cancelled': 4, 'batch02_menu_reopened': 4, 'batch02_name_entry': 4,
        'batch02_name_return': 4, 'batch02_deck_return_to_party': 16})
    buy_cp = inspect_replay(after_buy, {'batch02_buy_open': 19, 'batch02_insufficient_funds': 19,
                                        'batch02_buy_return_to_field': 2})
    album_cp = inspect_replay(after_album, {'cold_loaded': 2, 'cold_album_visible': 18})
    deck_log = (after_deck / 'emulator.log').read_text(errors='replace')
    buy_log = (after_buy / 'emulator.log').read_text(errors='replace')
    album_log = (after_album / 'emulator.log').read_text(errors='replace')
    if not all(s in deck_log for s in ('batch02_deck state=1 step=15', 'batch02_deck state=1 step=13',
             'batch02_name state=1 step=2', 'batch02_name state=1 step=100')):
        raise ValueError('Cancel, reopen, name entry or confirmation state missing')
    if 'batch02_buy state=1 step=12 substep=19' not in buy_log:
        raise ValueError('Insufficient-funds game state was not reached')
    dresult = json.loads((after_deck / 'result.json').read_text())
    dcp = {c['name']: c for c in dresult['checkpoints']}
    a = (after_deck / dcp['batch02_name_entry']['gamestate_file']).read_bytes()
    b = (after_deck / dcp['batch02_name_return']['gamestate_file']).read_bytes()
    if len(a) != 0x275c or len(b) != 0x275c or a[0x628:0x63e] == b[0x628:0x63e]:
        raise ValueError('The name entry did not actually change the first deck name')
    if any(hash_file(folder / 'memcard1.mcd') != hash_file(seed) for folder in (after_deck, after_buy, after_album)):
        raise ValueError('A replay unexpectedly modified its independent card')
    old_album = Path(__file__).resolve().parents[1] / 'local/font-recovery/p1-batch01-album-runtime/PRIVATE_card_album.ppm'
    new_album = after_album / 'PRIVATE_card_album.ppm'
    if light_mask(old_album, (15,25,159,38)) != light_mask(new_album, (15,25,159,38)):
        raise ValueError('Previously verified album title differs')
    integrity = {k: creation[k] for k in ('expected_changed_sectors', 'changed_raw_bytes', 'changed_raw_sectors',
        'unchanged_raw_sectors', 'all_differences_within_reviewed_text_or_modified_sector_checksums',
        'previous_batch_changes_preserved', 'modified_sector_edc_ecc_before_and_after_valid',
        'font_id_647_raw_sha256_before_and_after')}
    return {'schema_version': 1, 'status': 'batch02_new_entries_runtime_verified',
        'source_image_sha256': CN_IMAGE_SHA256, 'previous_batch_image_sha256': P1_SHA,
        'test_image_sha256': creation['test_image_sha256'], 'new_entries_verified': len(new_entries),
        'new_entries': new_entries, 'previous_batch_entries_preserved': 6, 'prior_album_screen_reverified': True,
        'disc_integrity': integrity,
        'runtime': {'emulator': 'PCSX-Redux a74a0587 build 433', 'emulator_sha256': EMULATOR_SHA256,
            'bios': 'OpenBIOS', 'bios_sha1': BIOS_SHA1, 'mode': 'headless, independent private card copies',
            'deck_checkpoints': deck_cp, 'buy_checkpoints': buy_cp, 'album_checkpoints': album_cp,
            'name_entry_changed_then_returned': True, 'menu_cancelled_and_reopened': True,
            'no_game_save_in_these_replays': True, 'before_after_pixel_comparisons': comparisons,
            'screenshots_sha256': screens, 'visual_review': 'Analyst inspected all translated windows.',
            'pad_warning_counts': {k: log.count('Unknown command for pad: 43') for k, log in
                                   (('deck', deck_log), ('buy', buy_log), ('album', album_log))},
            'listener_stack_reset_counts': {k: log.count('replay: Lua stack reset at frame 8192') for k, log in
                                            (('deck', deck_log), ('buy', buy_log), ('album', album_log))}},
        'limits': ['Only selected labels and the recorded menu paths are verified.',
            'The general writer remains blocked; exact arbitrary window clipping is unresolved.',
            'Booster and nine-card-limit scenarios were not triggered.',
            'Batch-02 post-visit saving, other emulators, hardware and whole-game regression remain pending.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('image', 'original_zip', 'prior_audit', 'glyph_review', 'creation_report', 'seed',
                 'before_deck', 'before_buy', 'after_deck', 'after_buy', 'after_album', 'report'):
        p.add_argument(name, type=Path)
    p.add_argument('--visual-checked', action='store_true')
    a = p.parse_args()
    try:
        result = run(a.image, a.original_zip, a.prior_audit, a.glyph_review, a.creation_report, a.seed,
                     a.before_deck, a.before_buy, a.after_deck, a.after_buy, a.after_album, a.visual_checked)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        result = {'schema_version': 1, 'status': 'blocked', 'error_type': type(exc).__name__, 'error': str(exc)[:500]}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(a.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as out:
        json.dump(result, out, ensure_ascii=False, indent=2); out.write('\n')
    print(json.dumps({'status': result['status'], 'report': str(a.report)}))
    return 2 if result['status'] == 'blocked' else 0


if __name__ == '__main__':
    raise SystemExit(main())
