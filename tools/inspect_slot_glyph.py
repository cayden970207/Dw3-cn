#!/usr/bin/env python3
"""Read-only, hash-bound investigation of the memory-card slot label.

This checks the existing index, the actual matching overlay and one original
12x12 tile. It does not claim a Unicode identity or create font patches.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path

from tools.layout_cn import GLYPH_OFFSET, GLYPH_STRIDE, GLYPH_SHA
from tools.scan_text_tables import Disc, hash_file
from tools.text_codec import CN_C713, CN_IMAGE_SHA256, tokenize

TEST_IMAGE_SHA = '632d62e803a31fe9887c8733925663493205512dd9f1a332336c6ae1fd4849bb'
OVERLAY_SHA1 = '925dcb2835f6ac18c3c460f2c99c72d87fcd7db2'
ENTRY_SHA256 = '97b025b8901599a06cf970ac2b14175ca84511631d10fc5501deaa562f1d4e45'
FONT_USER_SHA256 = '3583f16b76882114e369fb07fb686271fa62c12cd107aca05281d257f08fa5f0'


def inspect(image: Path, index_path: Path, font_path: Path, screenshot: Path) -> dict:
    index = json.loads(index_path.read_text())
    if hash_file(image) != TEST_IMAGE_SHA or index.get('image_sha256') != CN_IMAGE_SHA256:
        raise ValueError('Image or prior private index differs')
    disc = Disc(image)
    overlay = disc.read_user(835, 23712)
    if hashlib.sha1(overlay).hexdigest() != OVERLAY_SHA1:
        raise ValueError('Memory-card overlay differs from the inspected reference')
    file = next(x for x in index['text_files'] if x['file_id'] == 122)
    if file['lba'] != 2427:
        raise ValueError('Memory-card text resource location differs')
    source = disc.read_user(file['lba'], file['file_size_bytes'])
    if hashlib.sha256(source).hexdigest() != file['file_sha256']:
        raise ValueError('Memory-card text resource differs from the indexed baseline')
    row = next(x for x in file['entries'] if x['table'] is None and x['index'] == 29)
    raw = bytes.fromhex(row['raw_hex'])
    if (hashlib.sha256(raw).hexdigest() != ENTRY_SHA256 or row['raw_sha256'] != ENTRY_SHA256 or
            source[row['offset_bytes']:row['offset_bytes'] + len(raw)] != raw):
        raise ValueError('Slot label does not match the source entry')
    tokens = tokenize(raw, variant=CN_C713)
    chars = [t for t in tokens if t.kind == 'cn_glyph']
    if len(chars) != 7 or [chars[i].glyph_index for i in (0, 1, 4, 6)] != [523, 756, 48, 1213]:
        raise ValueError('Known surrounding glyphs or final glyph differ')
    data = font_path.read_bytes()
    tiles = data[GLYPH_OFFSET:GLYPH_OFFSET + 1997 * GLYPH_STRIDE]
    if hashlib.sha256(data).hexdigest() != FONT_USER_SHA256 or hashlib.sha256(tiles).hexdigest() != GLYPH_SHA:
        raise ValueError('Private font data differs from the original resource')
    tile = tiles[1213 * GLYPH_STRIDE:1214 * GLYPH_STRIDE]
    palette_counts = Counter((tile[y*6 + x//2] >> (4*(x%2))) & 15 for y in range(12) for x in range(12))
    if set(palette_counts) != {0, 2, 15}:
        raise ValueError('Unexpected native glyph palette')
    return {'schema_version': 1, 'status': 'source_glyph_and_runtime_issue_identified_not_repaired',
            'source_image_sha256': CN_IMAGE_SHA256, 'inspected_test_image_sha256': TEST_IMAGE_SHA,
            'report_reason': 'The user reported that the final character of the memory-card slot prompt is unreadable.',
            'overlay': {'path': 'AAA/PRO/STGMCARD.PRO', 'sha1': OVERLAY_SHA1,
                        'reference_commit': '841521ab2071096038316b93b8f778255b85730a',
                        'source_call': 'records_language + 0x78, entry 0x1D', 'language_state': 2},
            'text_resource_id': 122, 'entry_index': 29, 'source_entry_sha256': ENTRY_SHA256,
            'final_glyph_index': 1213, 'contextual_identity_candidate': '槽',
            'unicode_identity_status': 'unresolved_not_added_to_reviewed_mapping',
            'native_tile_size_px': [12,12], 'native_tile_sha256': hashlib.sha256(tile).hexdigest(),
            'palette_index_counts': {str(k): v for k,v in sorted(palette_counts.items())},
            'user_screenshot_sha256': hash_file(screenshot),
            'visual_observation': 'The supplied screenshot and an enlarged rendering of the native tile were inspected locally; both show densely connected strokes.',
            'conclusion': 'Higher emulator resolution sharpens the sampled edges but cannot recover detail absent from this source bitmap.',
            'modified_image_or_font': False, 'repair_status': 'not_attempted_source_font_preserved',
            'limits': ['The candidate identity is inferred from the phrase and remains unreviewed.',
                       'No alternative glyph, modified font, or runtime font repair was validated.',
                       'The original screenshot, source tile and other game assets remain local.']}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('image', 'private_index', 'font_user_data', 'user_screenshot', 'report'):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    try:
        result = inspect(args.image, args.private_index, args.font_user_data, args.user_screenshot)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        result = {'schema_version': 1, 'status': 'blocked', 'error_type': type(exc).__name__,
                  'error': str(exc)[:500]}
    with os.fdopen(os.open(args.report, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2); f.write('\n')
    print(json.dumps({'status': result['status'], 'report': str(args.report)}))
    return 2 if result['status'] == 'blocked' else 0


if __name__ == '__main__':
    raise SystemExit(main())
