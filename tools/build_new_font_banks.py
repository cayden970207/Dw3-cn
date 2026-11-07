#!/usr/bin/env python3
"""Build a private, new Chinese font from pinned, separately licensed sources.

Produces original 12x12 4bpp and compact 1bpp banks plus illustrative 48px
atlas PNGs. It does not touch a game image or make these banks game-loadable.
The detailed index and all font data remain under local/.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import unicodedata

from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTFont

from tools.scan_text_tables import hash_file

BANK_SIZE, CELL, HD_CELL = 256, 12, 48
SOURCES = {
    'ark': {'sha256': 'add3145c4372eca6c386b2adf90e98fea21e848809da56ceed90f917a9caeb4e',
            'name': 'Ark Pixel 12px Monospaced zh-Hans 2026.10.25',
            'url': 'https://github.com/TakWolf/ark-pixel-font', 'license': 'OFL-1.1'},
    'fusion': {'sha256': '2e63342fd51ba29831406c8ad8fc919868de0c9dc9277d3a11614a5bc7a26907',
               'name': 'Fusion Pixel 12px Monospaced zh-Hans 2026.10.25',
               'url': 'https://github.com/TakWolf/fusion-pixel-font', 'license': 'OFL-1.1'},
    'noto': {'sha256': '2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b',
             'name': 'Noto Sans CJK SC Regular', 'url': 'https://github.com/notofonts/noto-cjk',
             'license': 'OFL-1.1'},
    'unifont': {'sha256': '88d0a14d4aa9a96419720b39ba5da921a59560d76d4ec7d523acc06ed85cd3c2',
                'name': 'GNU Unifont 18.0.01', 'url': 'https://unifoundry.com/unifont/index.html',
                'license': 'OFL-1.1 (dual-licensed)'}
}


def gb2312_repertoire() -> set[int]:
    """Character coverage target only; never decode original game text here."""
    out = set()
    for lead in range(0xA1, 0xF8):
        for trail in range(0xA1, 0xFF):
            try:
                text = bytes((lead, trail)).decode('gb2312')
            except UnicodeDecodeError:
                continue
            if len(text) != 1:
                raise ValueError('Unexpected GB2312 mapping')
            out.add(ord(text))
    if len(out) != 7445:
        raise ValueError('Unexpected built-in GB2312 repertoire')
    return out


def repertoire(cmaps: dict[str, dict]) -> tuple[list[int], set[int]]:
    all_supported = set().union(*(set(x) for x in cmaps.values()))
    gb = gb2312_repertoire()
    target = set(range(0x3400, 0x4DC0)) | set(range(0x4E00, 0xA000)) | gb
    target.update(range(0x20, 0x7F))
    target.update(range(0xFF01, 0xFF5F))
    target.update(n for n in range(0x3000, 0x3040) if unicodedata.category(chr(n)) != 'Cn')
    # Keep supplementary Han only where a source font actually has a glyph.
    target.update(n for n in all_supported if 0x20000 <= n <= 0x3347F)
    return sorted(target), gb


def raster(font, char: str, cell: int, mode: str):
    """Raster actual visible bounds, fit to the square, and report downsizing."""
    bounds = font.getbbox(char, anchor='lt')
    if bounds is None:
        return Image.new(mode, (cell, cell)), False
    left, top, right, bottom = bounds
    width, height = max(1, right - left), max(1, bottom - top)
    if width > 512 or height > 512:
        raise ValueError('Unexpected font glyph size')
    image = Image.new(mode, (width + 8, height + 8))
    ImageDraw.Draw(image).text((4-left, 4-top), char, font=font, fill=255 if mode == 'L' else 1, anchor='lt')
    ink = image.getbbox()
    if ink is None:
        return Image.new(mode, (cell, cell)), False
    cropped = image.crop(ink)
    w, h = cropped.size
    shrunk = w > cell or h > cell
    if shrunk:
        factor = min(cell / w, cell / h)
        target = (max(1, math.floor(w * factor)), max(1, math.floor(h * factor)))
        cropped = cropped.resize(target, Image.Resampling.NEAREST if mode == '1' else Image.Resampling.LANCZOS)
        w, h = cropped.size
    result = Image.new(mode, (cell, cell))
    result.paste(cropped, ((cell - w) // 2, (cell - h) // 2))
    return result, shrunk


def pixel_values(image):
    """Read pixels on both current and older supported Pillow releases."""
    if hasattr(image, 'get_flattened_data'):
        return image.get_flattened_data()
    return image.getdata()


def encode_native(image):
    if image.mode != '1' or image.size != (12, 12):
        raise ValueError('Native glyph must be a 12x12 monochrome image')
    pixels = [bool(x) for x in pixel_values(image)]
    four = bytearray(72)
    one = bytearray(18)
    for i, value in enumerate(pixels):
        if value:
            four[i // 2] |= 4 << (4 * (i % 2))
            one[i // 8] |= 1 << (7 - i % 8)
    for i, value in enumerate(pixels):
        if bool((four[i // 2] >> (4 * (i % 2))) & 15) != value or \
                bool((one[i // 8] >> (7 - i % 8)) & 1) != value:
            raise ValueError('Native 1bpp/4bpp packing failed roundtrip')
    return bytes(four), bytes(one)


def write_json(path: Path, data):
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2); f.write('\n')


def build(paths: dict[str, Path], output: Path) -> dict:
    root, output = Path(__file__).resolve().parents[1], output.resolve()
    if not output.is_relative_to((root / 'local').resolve()) or output == (root / 'local').resolve():
        raise ValueError('Font artifacts must be generated in a new local/ directory')
    for name, path in paths.items():
        if hash_file(path) != SOURCES[name]['sha256'] or path.resolve().is_relative_to(output):
            raise ValueError('A pinned font source differs or is inside the output')
    cmaps = {name: TTFont(path, lazy=True).getBestCmap() for name, path in paths.items()}
    targets, gb = repertoire(cmaps)
    union = set().union(*(set(c) for c in cmaps.values()))
    missing_sources = [n for n in targets if n not in union]
    if missing_sources:
        raise ValueError('Missing source glyphs: ' + ', '.join(f'U+{n:04X}' for n in missing_sources[:20]))
    native_fonts = {n: ImageFont.truetype(str(p), CELL) for n,p in paths.items()}
    hd_fonts = {n: ImageFont.truetype(str(p), HD_CELL) for n,p in paths.items()}
    native_order, hd_order = ('ark', 'fusion', 'noto', 'unifont'), ('noto', 'ark', 'fusion', 'unifont')
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    atlas_dir = output / 'PRIVATE_hd48_atlases'
    atlas_dir.mkdir(mode=0o700)
    native_counts, hd_counts = Counter(), Counter()
    downsized = Counter()
    entries, failed, atlas_hashes = [], [], []
    binary4, binary1 = output / 'PRIVATE_native12_4bpp.bin', output / 'PRIVATE_native12_1bpp_banked.bin'
    atlas = Image.new('L', (16 * HD_CELL, 16 * HD_CELL))
    with binary4.open('xb') as f4, binary1.open('xb') as f1:
        for index, cp in enumerate(targets):
            char = chr(cp)
            white_space = char.isspace()
            chosen = {}
            for mode, order, fontset, size, image_mode in (
                    ('native', native_order, native_fonts, CELL, '1'), ('hd', hd_order, hd_fonts, HD_CELL, 'L')):
                for name in order:
                    if cp not in cmaps[name] or cmaps[name][cp] == '.notdef':
                        continue
                    rendered, shrunk = raster(fontset[name], char, size, image_mode)
                    if rendered.getbbox() is not None or white_space:
                        chosen[mode] = (name, rendered, shrunk)
                        break
                if mode not in chosen:
                    failed.append({'codepoint': f'U+{cp:04X}', 'mode': mode, 'reason': 'no nonblank glyph in source fonts'})
                    chosen[mode] = ('unresolved', Image.new(image_mode, (size, size)), False)
            native_name, native, native_shrunk = chosen['native']
            hd_name, hd, hd_shrunk = chosen['hd']
            native_counts[native_name] += 1; hd_counts[hd_name] += 1
            downsized['native'] += bool(native_shrunk); downsized['hd'] += bool(hd_shrunk)
            four, one = encode_native(native)
            f4.write(four); f1.write(one)
            slot, bank = index % BANK_SIZE, index // BANK_SIZE
            atlas.paste(hd, ((slot % 16) * HD_CELL, (slot // 16) * HD_CELL))
            entries.append({'codepoint': f'U+{cp:04X}', 'index': index, 'bank': bank, 'slot': slot,
                            'native_source': native_name, 'hd_source': hd_name,
                            'native_ink_pixels': sum(bool(x) for x in pixel_values(native)),
                            'status': 'unresolved' if 'unresolved' in (native_name, hd_name) else 'generated_not_gameplay_verified'})
            if slot == BANK_SIZE - 1 or index == len(targets) - 1:
                # 256 1bpp glyphs occupy 4,608 bytes; pad each bank to 3 CD sectors.
                unused = BANK_SIZE - slot - 1
                f4.write(bytes(unused * 72))
                f1.write(bytes(unused * 18 + (6144 - BANK_SIZE * 18)))
                path = atlas_dir / f'bank-{bank:04d}.png'
                with path.open('xb') as out:
                    atlas.save(out, format='PNG', compress_level=6)
                atlas_hashes.append(hash_file(path))
                atlas = Image.new('L', (16 * HD_CELL, 16 * HD_CELL))
        f4.flush(); os.fsync(f4.fileno()); f1.flush(); os.fsync(f1.fileno())
    count = len(targets)
    banks = (count + BANK_SIZE - 1) // BANK_SIZE
    if binary4.stat().st_size != banks * 18432 or binary1.stat().st_size != banks * 6144:
        raise ValueError('Generated bank sizes or alignment are incorrect')
    unresolved = {int(x['codepoint'][2:], 16) for x in failed}
    successful = set(targets) - unresolved
    manifest = {'schema_version': 1, 'glyph_count': count, 'bank_size': BANK_SIZE,
                'native_4bpp_sha256': hash_file(binary4), 'native_1bpp_sha256': hash_file(binary1),
                'source_sha256': {n: SOURCES[n]['sha256'] for n in paths}, 'glyphs': entries,
                'failures': failed}
    write_json(output / 'PRIVATE_glyph_index.json', manifest)
    report = {'schema_version': 1, 'status': 'generated_not_integrated_or_gameplay_verified' if not failed else 'generated_with_unresolved_glyphs',
              'sources': {name: SOURCES[name] for name in paths},
              'coverage': {'glyph_slots_generated': count, 'source_missing': len(missing_sources),
                  'unresolved_rasterizations': len(unresolved),
                  'gb2312_repertoire_count': len(gb), 'gb2312_covered': len(gb & successful),
                  'cjk_unified_bmp_total': 0xA000 - 0x4E00,
                  'cjk_unified_bmp_covered': sum(n in successful for n in range(0x4E00,0xA000)),
                  'cjk_extension_a_total': 0x4DC0 - 0x3400,
                  'cjk_extension_a_covered': sum(n in successful for n in range(0x3400,0x4DC0)),
                  'supplementary_han_included_from_sources': sum(0x20000 <= n <= 0x3347F for n in successful),
                  'all_supplementary_han_complete': False},
              'native12': {'cell_px': [12,12], 'bank_size': BANK_SIZE, 'banks': banks,
                  'four_bpp_bank_bytes': 18432, 'one_bpp_bank_bytes_with_sector_padding': 6144,
                  'four_bpp_sha256': manifest['native_4bpp_sha256'],
                  'one_bpp_sha256': manifest['native_1bpp_sha256'],
                  'source_counts': dict(native_counts), 'downsized_glyphs': downsized['native'],
                  'all_packing_roundtrips_verified': True},
              'hd48': {'cell_px': [48,48], 'atlas_columns': 16, 'atlas_rows': 16,
                  'atlas_count': len(atlas_hashes), 'source_counts': dict(hd_counts),
                  'downsized_glyphs': downsized['hd'], 'atlas_hashes_sha256': atlas_hashes,
                  'texture_replacement_pack_ready': False},
              'game_integration': {'full_font_usable_in_game': False, 'full_font_runtime_verified': False,
                  'status': 'runtime_pending',
                  'reason': 'The original EU decoder and renderer do not yet load this banked font or its mapping.'},
              'limits': ['Generated bitmaps come from licensed third-party font sources and have not all been visually reviewed.',
                  'Some fallback glyphs use different source styles; downsampling can affect legibility.',
                  'Supplementary Unicode Han coverage contains only glyphs present in the pinned source fonts.',
                  'Generated native banks and HD atlases are local only; no game resources were used in their generation.',
                  'Character repertoire selection is not a claim about the original game encoding.']}
    write_json(output / 'SHARE_THIS_font_generation_report.json', report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for n in ('ark', 'fusion', 'noto', 'unifont', 'output'):
        p.add_argument(n, type=Path)
    a = p.parse_args()
    result = build({n:getattr(a,n) for n in SOURCES}, a.output)
    print(json.dumps({'status': result['status'], 'coverage': result['coverage'],
                      'native_source_counts': result['native12']['source_counts']}))


if __name__ == '__main__':
    main()
