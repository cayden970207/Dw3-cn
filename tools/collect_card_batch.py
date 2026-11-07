#!/usr/bin/env python3
"""Check private runtime evidence and publish sanitized card-batch results.

Pixel comparisons do not perform OCR. The --visual-checked flag is only for a
person who inspected every local screenshot and the exact proposed text.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path

try:
    from tools.collect_runtime_evidence import ppm
    from tools.layout_cn import actual_assets, measure, white_bbox
    from tools.make_card_menu_batch import EXPECTED
    from tools.replay_card_album import BIOS_SHA1, EMULATOR_SHA256
    from tools.scan_text_tables import hash_file
    from tools.text_codec import CN_IMAGE_SHA256, cn_canonical_code, load_reviewed_glyphs
except ModuleNotFoundError:
    from collect_runtime_evidence import ppm
    from layout_cn import actual_assets, measure, white_bbox
    from make_card_menu_batch import EXPECTED
    from replay_card_album import BIOS_SHA1, EMULATOR_SHA256
    from scan_text_tables import hash_file
    from text_codec import CN_IMAGE_SHA256, cn_canonical_code, load_reviewed_glyphs

CANDIDATES = {
    5: ("购买卡片菜单", "shop_main", [167, 53], "开放卡片商店，第一行"),
    6: ("打开卡包菜单", "shop_main", [167, 67], "开放卡片商店，第二行"),
    7: ("编辑卡组菜单", "shop_main", [167, 81], "开放卡片商店，第三行"),
    21: ("没有卡包提示", "shop_empty", [154, 113], "无卡包时选择商店第二行"),
    26: ("编辑卡组子菜单", "deck_menu", [167, 35], "打开卡组页面并选择一组"),
    15: ("选择卡包提示", "booster_screen", [20, 194], "持有卡包时选择商店第二行"),
    19: ("余额不足提示", "buy_message", [154, 57], "尝试购买价格高于余额的卡片"),
    20: ("卡片数量上限提示", "buy_message", [154, 57], "尝试购买已持有九张的卡片"),
    25: ("选择卡组提示", "deck_help", [151, 34], "进入卡组页面的选择列表"),
    27: ("更改名称菜单", "deck_menu", [167, 49], "打开卡组页面并选择一组"),
}


def inspect_replay(directory: Path, checkpoints: dict[str, int]) -> dict:
    result = json.loads((directory / 'result.json').read_text())
    trace = json.loads((directory / 'PRIVATE_trace.json').read_text())
    if result.get('status') != 'ok' or any(s.get('type') in ('reset', 'write_mem') for s in trace['steps']):
        raise ValueError('A replay failed or injected state')
    cp = {x['name']: x for x in result['checkpoints']}
    if any(name not in cp or cp[name]['stage'] != stage for name, stage in checkpoints.items()):
        raise ValueError('A required gameplay checkpoint was not reached')
    return {name: {'frame': cp[name]['frame'], 'stage': stage} for name, stage in checkpoints.items()}


def compare_images(before: Path, after: Path, allowed: list[tuple[int, int, int, int]]) -> dict:
    a, b = ppm(before), ppm(after)
    changed = [(i % 320, i // 320) for i in range(320 * 240) if a[i*3:i*3+3] != b[i*3:i*3+3]]
    if not changed:
        raise ValueError('Before and after screenshots are identical')
    outside = sum(not any(x0 <= x < x1 and y0 <= y < y1 for x0, y0, x1, y1 in allowed) for x, y in changed)
    if outside:
        raise ValueError('Before/after screenshot changed outside the expected text bands')
    return {'changed_pixels': len(changed), 'changes_outside_expected_text_bands': outside,
            'bbox_exclusive': [min(x for x, _ in changed), min(y for _, y in changed),
                               max(x for x, _ in changed)+1, max(y for _, y in changed)+1]}


def run(image: Path, original_zip: Path, creation_report: Path, index_path: Path, audit_path: Path,
        glyph_path: Path, before_shop: Path, after_shop: Path, before_deck: Path, after_deck: Path,
        after_album: Path, emulator: Path, bios: Path, visual_checked: bool) -> dict:
    creation = json.loads(creation_report.read_text())
    if creation.get('source_image_sha256') != CN_IMAGE_SHA256 or hash_file(image) != creation.get('test_image_sha256'):
        raise ValueError('Batch image does not match its verified creation report')
    if (len(creation.get('entries', [])) != len(EXPECTED) or
            {(e['file_id'], e['entry_index']) for e in creation['entries']} != set(EXPECTED) or
            any(e['translation'] != EXPECTED[(e['file_id'], e['entry_index'])][0] for e in creation['entries']) or
            not creation.get('modified_sector_edc_ecc_before_and_after_valid') or
            not creation.get('all_differences_within_reviewed_text_or_modified_sector_checksums')):
        raise ValueError('Batch creation evidence does not cover the fixed reviewed entries')
    if hash_file(emulator) != EMULATOR_SHA256 or hashlib.sha1(bios.read_bytes()).hexdigest() != BIOS_SHA1:
        raise ValueError('Emulator or BIOS differs from the inspected build')
    if not visual_checked:
        raise ValueError('Manual inspection of all local screenshots is required')
    shop_cp = inspect_replay(after_shop, {'shop_main_stable': 19, 'shop_after_open_booster': 19, 'shop_return_to_field': 2})
    deck_cp = inspect_replay(after_deck, {'deck_submenu': 4, 'deck_return_to_party': 16})
    album_cp = inspect_replay(after_album, {'cold_loaded': 2, 'cold_album_visible': 18})
    inspect_replay(before_shop, {'shop_main_stable': 19, 'shop_after_open_booster': 19})
    inspect_replay(before_deck, {'deck_submenu': 4})
    shop_log = (after_shop / 'emulator.log').read_text(errors='replace')
    deck_log = (after_deck / 'emulator.log').read_text(errors='replace')
    if 'shop_step=22 cursor=1' not in shop_log or 'deck_submenu_ready' not in deck_log:
        raise ValueError('The empty-booster or deck submenu state was not actually reached')
    areas = [(166, 53, 310, 66), (166, 67, 310, 80), (166, 81, 310, 94)]
    comparisons = {
        'shop_main': compare_images(before_shop / 'PRIVATE_shop_main.ppm', after_shop / 'PRIVATE_shop_main.ppm', areas),
        'shop_empty': compare_images(before_shop / 'PRIVATE_shop_no_boosters.ppm', after_shop / 'PRIVATE_shop_no_boosters.ppm', areas + [(153, 113, 310, 126)]),
        'deck_menu': compare_images(before_deck / 'PRIVATE_deck_menu.ppm', after_deck / 'PRIVATE_deck_menu.ppm', [(166, 35, 310, 48)]),
    }
    images = {'shop': after_shop / 'PRIVATE_shop_main.ppm', 'empty': after_shop / 'PRIVATE_shop_no_boosters.ppm',
              'deck': after_deck / 'PRIVATE_deck_menu.ppm', 'album': after_album / 'PRIVATE_card_album.ppm'}
    audit = json.loads(audit_path.read_text())
    font = actual_assets(original_zip, audit)
    reviewed = load_reviewed_glyphs(glyph_path)
    codes = {ch: cn_canonical_code(i) for i, ch in reviewed.items()}
    entries = creation['entries']
    for entry in entries:
        target = entry['translation']
        if any(ch not in codes for ch in target):
            raise ValueError('Runtime sample contains an unreviewed glyph')
        encoded = b''.join(codes[ch] for ch in target) + b'\0'
        layout = measure(font, encoded)
        x, y = entry['window_origin_px']
        actual = white_bbox(images[entry['context']], (x-1, y, min(x+143, 320), y+13))
        if actual != entry['replacement_ink_bbox_px'] or layout['draw_max_advance_px'] != entry['replacement_advance_px']:
            raise ValueError('Actual runtime glyph ink differs from the predicted sample')
        entry['runtime_ink_bbox_px'] = actual
        entry['validated_source_text_band_px'] = [x, y, x + entry['source_advance_px'], y + 12]
        entry['visible_screen_width_from_origin_px'] = 320 - x
        entry['exact_window_clipping_bounds'] = 'unresolved; target fits the observed source text band and visible screen'
        entry['legacy_game_measure_static_px'] = layout['game_measure_max_px']
        entry['game_measure_result_used_by_window'] = False
        entry['status'] = 'runtime_visual_and_pixel_bounds_verified'
    idx = json.loads(index_path.read_text())
    f = next(f for f in idx['text_files'] if f['file_id'] == 52)
    candidates = []
    by_index = {e['entry_index']: e for e in entries if e['file_id'] == 52}
    for number, (meaning, context, origin, trigger) in CANDIDATES.items():
        row = next(e for e in f['entries'] if e['index'] == number and e['table'] is None)
        candidates.append({'resource_id': 52, 'entry_index': number, 'source_sha256': row['raw_sha256'],
                           'meaning_zh': meaning, 'window_context': context, 'font': 1,
                           'window_origin_px': origin, 'trigger': trigger,
                           'status': 'runtime_verified' if number in by_index else 'unresolved_not_modified'})
    return {'schema_version': 1, 'status': 'five_new_entries_runtime_verified',
            'source_image_sha256': CN_IMAGE_SHA256, 'test_image_sha256': creation['test_image_sha256'],
            'new_entries_verified': 5, 'previous_title_reverified': True,
            'entries': entries, 'candidate_shortlist': candidates,
            'disc_integrity': {k: creation[k] for k in ('expected_changed_sectors', 'changed_raw_bytes',
                'changed_raw_sectors', 'unchanged_raw_sectors', 'all_differences_within_reviewed_text_or_modified_sector_checksums',
                'modified_sector_edc_ecc_before_and_after_valid', 'font_id_647_raw_sha256_before_and_after')},
            'runtime': {'emulator': 'PCSX-Redux a74a0587 build 433', 'emulator_sha256': EMULATOR_SHA256,
                        'bios': 'OpenBIOS', 'bios_sha1': BIOS_SHA1,
                        'configuration': 'Headless, unthrottled, separate process per path, independent memory-card copies.',
                        'input_cue': 'local/font-recovery/p1-batch01/DW3-p1-batch01.cue',
                        'shop_checkpoints': shop_cp, 'deck_checkpoints': deck_cp, 'album_checkpoints': album_cp,
                        'before_after_pixel_comparisons': comparisons,
                        'screenshots_sha256': {name: hash_file(path) for name, path in images.items()},
                        'visual_review': 'Analyst inspected each local screenshot and confirmed the displayed translations and no visible overlap.',
                        'actual_ocr_used': False},
            'limits': ['Only the listed windows and recorded paths were verified; general Chinese writing remains blocked.',
                       'The unchanged candidate entries require glyph review, context bounds and runtime checks.',
                       'The batch was not tested across the complete game or on retail BIOS/hardware.',
                       'Game measurement values are static simulations of inspected instructions, not live function calls.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('image', 'original_zip', 'creation_report', 'private_index', 'prior_audit', 'glyph_review',
                 'before_shop', 'after_shop', 'before_deck', 'after_deck', 'after_album', 'emulator', 'bios', 'report'):
        p.add_argument(name, type=Path)
    p.add_argument('--visual-checked', action='store_true')
    a = p.parse_args()
    try:
        result = run(a.image, a.original_zip, a.creation_report, a.private_index, a.prior_audit, a.glyph_review,
                     a.before_shop, a.after_shop, a.before_deck, a.after_deck, a.after_album, a.emulator, a.bios, a.visual_checked)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        result = {'schema_version': 1, 'status': 'blocked', 'source_image_sha256': CN_IMAGE_SHA256,
                  'error_type': type(exc).__name__, 'error': str(exc)[:500], 'new_entries_verified': 0}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(a.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps({'status': result['status'], 'report': str(a.report)}))
    return 2 if result['status'] == 'blocked' else 0


if __name__ == '__main__':
    raise SystemExit(main())
