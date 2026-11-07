#!/usr/bin/env python3
"""Verify all generated native/HD glyph cells against their pinned fonts.

All source files and generated bitmap assets stay local. This does not verify
game-engine integration, glyph aesthetics, or translation completeness.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path

from PIL import Image, ImageFont
from fontTools.ttLib import TTFont

from tools.build_new_font_banks import BANK_SIZE, CELL, HD_CELL, SOURCES, gb2312_repertoire, raster
from tools.scan_text_tables import hash_file


def verify(directory: Path, paths: dict[str, Path]) -> dict:
    root = Path(__file__).resolve().parents[1]
    local = (root / 'local').resolve()
    if not directory.resolve().is_relative_to(local) or any(not p.resolve().is_relative_to(local) for p in paths.values()):
        raise ValueError('Font artifacts and sources must remain under local/')
    metadata = json.loads((directory / 'PRIVATE_glyph_index.json').read_text())
    report = json.loads((directory / 'SHARE_THIS_font_generation_report.json').read_text())
    if report.get('status') != 'generated_not_integrated_or_gameplay_verified' or metadata.get('failures'):
        raise ValueError('Font generation did not resolve every requested glyph')
    if any(hash_file(p) != SOURCES[n]['sha256'] or metadata['source_sha256'].get(n) != SOURCES[n]['sha256']
           for n,p in paths.items()):
        raise ValueError('Pinned font source changed')
    records = metadata['glyphs']
    if len(records) != metadata['glyph_count'] or metadata['bank_size'] != BANK_SIZE:
        raise ValueError('Font index count or bank size differs')
    codepoints = [int(row['codepoint'][2:],16) for row in records]
    if codepoints != sorted(set(codepoints)):
        raise ValueError('Codepoints are duplicated or not sorted')
    present = set(codepoints)
    if not gb2312_repertoire() <= present or not set(range(0x3400,0x4DC0)) <= present or \
            not set(range(0x4E00,0xA000)) <= present:
        raise ValueError('Promised character repertoire is incomplete')
    four_path = directory / 'PRIVATE_native12_4bpp.bin'
    one_path = directory / 'PRIVATE_native12_1bpp_banked.bin'
    if hash_file(four_path) != metadata['native_4bpp_sha256'] or hash_file(one_path) != metadata['native_1bpp_sha256']:
        raise ValueError('Generated native font bank hash differs')
    four, one = four_path.read_bytes(), one_path.read_bytes()
    banks = (len(records) + BANK_SIZE - 1) // BANK_SIZE
    if len(four) != banks * 18432 or len(one) != banks * 6144 or report['hd48']['atlas_count'] != banks:
        raise ValueError('Generated bank geometry differs')
    cmaps = {n: TTFont(p, lazy=True).getBestCmap() for n,p in paths.items()}
    native_fonts = {n: ImageFont.truetype(str(p), CELL) for n,p in paths.items()}
    hd_fonts = {n: ImageFont.truetype(str(p), HD_CELL) for n,p in paths.items()}
    counts, hd_counts = Counter(), Counter()
    atlas_dir = directory / 'PRIVATE_hd48_atlases'
    for bank in range(banks):
        image_path = atlas_dir / f'bank-{bank:04d}.png'
        if hash_file(image_path) != report['hd48']['atlas_hashes_sha256'][bank]:
            raise ValueError('HD atlas hash differs')
        with Image.open(image_path) as loaded:
            atlas = loaded.copy()
        if atlas.mode != 'L' or atlas.size != (16 * HD_CELL, 16 * HD_CELL):
            raise ValueError('HD atlas dimensions or colour mode differ')
        start = bank * BANK_SIZE
        for i in range(start, min(start + BANK_SIZE, len(records))):
            row, cp, slot = records[i], codepoints[i], i - start
            if (row['index'], row['bank'], row['slot'], row['status']) != \
                    (i, bank, slot, 'generated_not_gameplay_verified'):
                raise ValueError('Glyph mapping/index differs')
            native_name, hd_name = row['native_source'], row['hd_source']
            if cp not in cmaps[native_name] or cp not in cmaps[hd_name]:
                raise ValueError('A selected font lacks its indexed glyph')
            native, _ = raster(native_fonts[native_name], chr(cp), CELL, '1')
            offset4, offset1 = bank * 18432 + slot * 72, bank * 6144 + slot * 18
            ink_count = 0
            # Decode both packed formats separately instead of reusing the
            # writer's encoder: a shared packing error must not pass audit.
            for pixel in range(CELL * CELL):
                expected = bool(native.getpixel((pixel % CELL, pixel // CELL)))
                nibble = (four[offset4 + pixel//2] >> (4 * (pixel % 2))) & 15
                bit = (one[offset1 + pixel//8] >> (7 - pixel % 8)) & 1
                if nibble != (4 if expected else 0) or bit != int(expected):
                    raise ValueError(f'Native glyph packing differs for U+{cp:04X}')
                ink_count += expected
            if row['native_ink_pixels'] != ink_count:
                raise ValueError('Native ink count differs')
            hd, _ = raster(hd_fonts[hd_name], chr(cp), HD_CELL, 'L')
            x, y = (slot % 16) * HD_CELL, (slot // 16) * HD_CELL
            if atlas.crop((x,y,x+HD_CELL,y+HD_CELL)).tobytes() != hd.tobytes():
                raise ValueError(f'HD glyph cell differs for U+{cp:04X}')
            counts[native_name] += 1; hd_counts[hd_name] += 1
        used = min(BANK_SIZE, len(records) - start)
        if any(four[bank*18432+used*72:(bank+1)*18432]) or \
                any(one[bank*6144+used*18:(bank+1)*6144]):
            raise ValueError('Unused bank or sector padding is not zero')
    if dict(counts) != report['native12']['source_counts'] or dict(hd_counts) != report['hd48']['source_counts']:
        raise ValueError('Font source counts differ')
    return {'schema_version': 1, 'status': 'all_generated_cells_match_pinned_sources',
            'glyph_count': len(records), 'banks': banks,
            'native_4bpp_sha256': metadata['native_4bpp_sha256'],
            'native_1bpp_sha256': metadata['native_1bpp_sha256'],
            'checked_native_cells': len(records), 'checked_hd_cells': len(records),
            'gb2312_characters_present': len(gb2312_repertoire()),
            'bmp_unified_ideographs_present': 0xA000-0x4E00, 'extension_a_present': 0x4DC0-0x3400,
            'native_source_counts': dict(counts), 'hd_source_counts': dict(hd_counts),
            'full_game_integration_verified': False,
            'limits': ['Raster fidelity matches source fonts; the appearance of every character has not been reviewed by a person.',
                       'The game cannot yet load the full bank or HD atlases.',
                       'Only supplementary Han present in the pinned sources were included.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('directory','ark','fusion','noto','unifont','report'):
        p.add_argument(name,type=Path)
    a=p.parse_args()
    result=verify(a.directory,{n:getattr(a,n) for n in SOURCES})
    with os.fdopen(os.open(a.report,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w',encoding='utf-8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({'status':result['status'],'glyph_count':result['glyph_count'],'banks':result['banks']}))


if __name__ == '__main__':
    main()
