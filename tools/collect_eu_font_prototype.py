#!/usr/bin/env python3
"""Verify the EU font prototype using real replays and exact bank pixels."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from tools.collect_batch03_runtime import FIXTURE_SHA
from tools.collect_card_batch import compare_images, inspect_replay
from tools.collect_runtime_evidence import ppm
from tools.eu_font_prototype import DEMO_SLOTS, EU_ARCHIVE_SHA, EU_IMAGE_SHA, masks_from_bank
from tools.replay_card_album import BIOS_SHA1, EMULATOR_SHA256
from tools.scan_text_tables import hash_file


def run(image, creation_path, bank, fixture, before, after, visual_checked):
    root = Path(__file__).resolve().parents[1]
    local = (root / 'local').resolve()
    if not visual_checked:
        raise ValueError('Visually inspect the actual source and target screenshots first')
    if any(not p.resolve().is_relative_to(local) for p in (image, bank, fixture, before, after)):
        raise ValueError('Game artifacts and screenshots must remain private')
    creation = json.loads(creation_path.read_text())
    if (creation.get('status') != 'prototype_copy_created_runtime_pending' or
            creation.get('source_image_sha256') != EU_IMAGE_SHA or hash_file(image) != creation.get('test_image_sha256')):
        raise ValueError('Prototype image or creation evidence differs')
    candidates = [slots for slots in DEMO_SLOTS.values()
                  if creation.get('experimental_mapping') == [{'slot': n, 'unicode': char} for n, char, _, _ in slots]]
    if len(candidates) != 1:
        raise ValueError('Creation evidence is not an inspected font demo')
    slots = candidates[0]
    masks, bank_sha = masks_from_bank(bank, slots)
    advance = creation['test_title']['font_1_advance_per_glyph_px']
    if advance not in (11, 12) or (advance == 12 and slots != DEMO_SLOTS['memory-slot']):
        raise ValueError('Unrecognized experimental glyph advance')
    if creation['source_font']['generated_4bpp_bank_sha256'] != bank_sha:
        raise ValueError('Prototype was not built from the verified full bank')
    if hash_file(fixture) != FIXTURE_SHA:
        raise ValueError('Independent artificial fixture changed')
    required = {'cold_loaded': 2, 'status_open': 16, 'cold_album_open': 18, 'cold_album_visible': 18,
                'eu_album_closed': 16, 'eu_album_reopened': 18, 'eu_album_returned': 16}
    evidence = {}
    for name, directory in (('baseline', before), ('prototype', after)):
        if hash_file(directory / 'memcard1.mcd') != FIXTURE_SHA:
            raise ValueError('A copied test card was modified')
        checkpoints = inspect_replay(directory, required)
        if [v['frame'] for v in checkpoints.values()] != sorted(v['frame'] for v in checkpoints.values()):
            raise ValueError('Album checkpoints are not in order')
        log = (directory / 'emulator.log').read_text(errors='replace')
        if 'private_card_album_screenshot_frame=' not in log:
            raise ValueError('Album screenshot capture marker missing')
        evidence[name] = {'checkpoints': checkpoints, 'normal_input_only': True, 'memory_card_saved': False,
                          'pad_warning_count': log.count('Unknown command for pad: 43'),
                          'listener_stack_reset_count': log.count('replay: Lua stack reset at frame 8192')}
    old, new = before / 'PRIVATE_card_album.ppm', after / 'PRIVATE_card_album.ppm'
    comparison = compare_images(old, new, [(15,25,100,38)])
    predicted = set()
    for i, (number, _, _, _) in enumerate(slots):
        for pos, ink in enumerate(masks[number]):
            if ink:
                predicted.add((16 + 1 + advance * i + pos % 12, 25 + pos // 12))
    pixels = ppm(new)
    actual = set()
    for y in range(25, 38):
        for x in range(16, 85):
            rgb = pixels[(y * 320 + x) * 3:(y * 320 + x) * 3 + 3]
            if min(rgb) >= 160 and max(rgb) - min(rgb) < 40:
                actual.add((x,y))
    if not predicted or actual != predicted:
        raise ValueError('Actual title ink differs from generated bank cells')
    bbox = [min(x for x,_ in actual), min(y for _,y in actual),
            max(x for x,_ in actual)+1, max(y for _,y in actual)+1]
    result = {'schema_version': 1, 'status': 'four_bank_glyphs_rendered_in_eu_game',
            'source_image_sha256': EU_IMAGE_SHA, 'source_archive_sha256': EU_ARCHIVE_SHA,
            'test_image_sha256': creation['test_image_sha256'],
            'full_bank_native_4bpp_sha256': bank_sha,
            'title': {'file_id': 38, 'entry_index': 1, 'translation': ''.join(char for _, char, _, _ in slots),
                      'native_glyphs': 4, 'advance_px': advance * len(slots), 'runtime_ink_bbox_px': bbox,
                      'expected_and_actual_white_pixels_identical': True,
                      'white_pixel_count': len(actual), 'visual_inspection_completed': True},
            'source_font': creation['source_font'], 'disc_integrity': creation['integrity'],
            'runtime': {'emulator': 'PCSX-Redux a74a0587 build 433', 'emulator_sha256': EMULATOR_SHA256,
                        'bios': 'OpenBIOS', 'bios_sha1': BIOS_SHA1,
                        'input_fixture': 'artificial independent memory-card copy',
                        'baseline_replay': evidence['baseline'], 'prototype_replay': evidence['prototype'],
                        'baseline_screenshot_sha256': hash_file(old), 'prototype_screenshot_sha256': hash_file(new),
                        'before_after_pixel_comparison': comparison},
            'full_font_ingame_integration': 'runtime_pending',
            'limits': ['The prototype temporarily repurposes four EU Japanese glyph slots, only in this new test copy.',
                       'Four glyphs in one window do not validate full bank loading or a general Chinese codec.',
                       'Only the stated album opening/closing paths were run; no save/cold-reload regression was performed.',
                       'The original European image, game screenshots, fonts and memory cards remain local.']}
    if slots != DEMO_SLOTS['album']:
        result['limits'].append('This is an artificial wording in the album title, not a change to the memory-card screen.')
    if advance == 12:
        result['limits'].append('The wider advance was tested only in this album-title window, not other fonts or contexts.')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('image','creation','bank','fixture','before','after','report'):
        p.add_argument(name,type=Path)
    p.add_argument('--visual-checked',action='store_true')
    a=p.parse_args()
    result=run(a.image,a.creation,a.bank,a.fixture,a.before,a.after,a.visual_checked)
    with os.fdopen(os.open(a.report,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w',encoding='utf-8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({'status':result['status'],'test_image_sha256':result['test_image_sha256']}))


if __name__ == '__main__':
    main()
