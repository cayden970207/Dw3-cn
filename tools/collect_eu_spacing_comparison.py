#!/usr/bin/env python3
"""Compare 11px and 12px advances on two real EU font-demo screenshots."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from tools.collect_card_batch import compare_images
from tools.collect_runtime_evidence import ppm
from tools.eu_font_prototype import EU_IMAGE_SHA
from tools.scan_text_tables import hash_file


def compare(creation_paths, runtime_paths, frames):
    root = Path(__file__).resolve().parents[1]
    local = (root / 'local').resolve()
    if any(not p.resolve().is_relative_to(local) for p in (*creation_paths, *runtime_paths, *frames)):
        raise ValueError('Runtime evidence and screenshots must remain local')
    creations = [json.loads(p.read_text()) for p in creation_paths]
    runtimes = [json.loads(p.read_text()) for p in runtime_paths]
    for advance, creation, runtime, frame in zip((11, 12), creations, runtimes, frames):
        if (creation.get('source_image_sha256') != EU_IMAGE_SHA or
                runtime.get('source_image_sha256') != EU_IMAGE_SHA or
                runtime.get('status') != 'four_bank_glyphs_rendered_in_eu_game' or
                creation.get('test_image_sha256') != runtime.get('test_image_sha256') or
                creation['test_title']['translation'] != '记忆插槽' or
                creation['test_title']['font_1_advance_per_glyph_px'] != advance or
                runtime['title']['advance_px'] != advance * 4 or
                runtime['title']['white_pixel_count'] != 200 or
                not runtime['title']['expected_and_actual_white_pixels_identical'] or
                hash_file(frame) != runtime['runtime']['prototype_screenshot_sha256']):
            raise ValueError('An input is not the validated memory-slot font demo')
    visual_diff = compare_images(frames[0], frames[1], [(15, 25, 100, 38)])
    positions = []
    for advance, frame in zip((11, 12), frames):
        pixels = ppm(frame)
        def bright(x, y):
            rgb = pixels[(y * 320 + x) * 3:(y * 320 + x) * 3 + 3]
            return min(rgb) >= 160 and max(rgb) - min(rgb) < 40
        masks = []
        for i in range(4):
            masks.append({(x, y) for y in range(11) for x in range(11)
                          if bright(17 + advance * i + x, 25 + y)})
        positions.append((masks, bright))
    if positions[0][0] != positions[1][0] or any(not x for x in positions[0][0]):
        raise ValueError('Character pixel masks differ between the spacing variants')
    blank_columns = [17 + 12*i + 11 for i in range(3)]
    if any(positions[1][1](x, y) for x in blank_columns for y in range(25, 36)):
        raise ValueError('The 12-pixel variant lacks an empty column between glyph cells')
    return {'schema_version': 1, 'status': 'two_spacing_variants_observed_in_one_game_window',
            'source_image_sha256': EU_IMAGE_SHA, 'translation_for_glyph_preview': '记忆插槽',
            'scope': 'Artificial wording in the card-album title; the actual memory-card screen is unchanged.',
            'variants': [{'advance_per_glyph_px': advance, 'advance_total_px': runtime['title']['advance_px'],
                          'ink_bbox_px': runtime['title']['runtime_ink_bbox_px'],
                          'white_pixel_count': runtime['title']['white_pixel_count'],
                          'test_image_sha256': runtime['test_image_sha256'],
                          'modified_sectors': len(creation['integrity']['changed_sectors']),
                          'modified_sectors_edc_ecc_valid': creation['integrity']['all_modified_sectors_edc_ecc_before_and_after_valid']}
                         for advance, creation, runtime in zip((11,12), creations, runtimes)],
            'same_four_glyph_pixel_masks': True, 'one_empty_pixel_column_between_cells_at_12px': True,
            'differences_outside_title_region': visual_diff['changes_outside_expected_text_bands'],
            'limits': ['The comparison uses a single font and window; no general spacing setting is proven safe.',
                       '12px spacing does not add missing strokes inside a 12x12 glyph.',
                       'Save/cold-reload for these separate visibility demos was not performed.',
                       'A full banked font and HD texture replacements remain unimplemented.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('creation11', 'runtime11', 'frame11', 'creation12', 'runtime12', 'frame12', 'report'):
        p.add_argument(name, type=Path)
    a = p.parse_args()
    result = compare((a.creation11,a.creation12), (a.runtime11,a.runtime12), (a.frame11,a.frame12))
    with os.fdopen(os.open(a.report,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w',encoding='utf-8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2); f.write('\n')
    print(json.dumps({'status': result['status']}))


if __name__ == '__main__':
    main()
