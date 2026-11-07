#!/usr/bin/env python3
"""Rebuild the restricted batch and preserve the shared return-button icon.

This is deliberately restricted to one image and one reviewed entry. It is
not a general writer, and runtime verification remains a separate step.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile

from tools import make_card_menu_batch as p1
from tools import make_card_menu_batch02 as p2
from tools.byte_patch import make_patch
from tools.font_recovery_probe import ARCHIVE_SHA, FONT_LBA, FONT_SECTORS
from tools.layout_cn import actual_assets, measure, neutral_ink_bbox
from tools.make_card_album_sample import FONT_RAW_SHA, digest_extent, exclusive_write
from tools.psx_sector import USER_BEGIN, check_form1, patch_form1_user_bytes
from tools.roundtrip_cn import inspect_resource
from tools.scan_text_tables import Disc, hash_file, local_image
from tools.text_codec import CN_C713, CN_IMAGE_SHA256, cn_canonical_code, load_reviewed_glyphs, tokenize

PRIOR_REBUILD_SHA = 'b39832c756de3ba52b7e1795c24e077fd1413725430fc88d3444304be17b99a8'
SOURCE_SHA = '7752b3c97ca1a29729d02c898acfc9179c3181c58a8e119f3ac88157a33a5040'
SCREENSHOTS = {
    'shop': ('fa73b3a435e8993a49c03578408b5e8b28b1964bfb793cd4634b7f215581f6bb', (211, 204)),
    'deck_editor': ('a6dd67fda34658783eb9ae4d7e102d0c7546f7da7dc4735e9d97b06595f83c95', (193, 23)),
    'booster': ('4a61fae7a72921af2a2893c757b3c9b65863f2f1328be6ceae8d40f8838d4898', (20, 208)),
}


def prepare_return(original: bytes, reviewed: dict[int, str]) -> bytes:
    """Keep the two inspected extended tokens exactly; replace only the label."""
    import hashlib
    if hashlib.sha256(original).hexdigest() != SOURCE_SHA:
        raise ValueError('Return hint source hash differs')
    tokens = tokenize(original, variant=CN_C713)
    if (len(tokens) < 4 or [(t.kind, t.glyph_index) for t in tokens[:2]] !=
            [('extended_glyph', 52), ('extended_glyph', 1)] or
            tokens[-1].kind != 'terminator' or any(t.kind == 'control' for t in tokens) or
            any(t.kind not in ('base_glyph', 'extended_glyph') for t in tokens[2:-1])):
        raise ValueError('Return hint prefix, controls or terminator differ')
    if reviewed.get(985) != '返' or reviewed.get(96) != '回':
        raise ValueError('Return glyph identities were not reviewed')
    out = b''.join(t.raw for t in tokens[:2]) + cn_canonical_code(985) + cn_canonical_code(96) + b'\0'
    result = tokenize(out, variant=CN_C713)
    if (len(out) > len(original) or result[:2] != tokens[:2] or
            [(t.kind, t.glyph_index) for t in result[2:-1]] != [('cn_glyph', 985), ('cn_glyph', 96)] or
            result[-1].kind != 'terminator' or any(t.kind == 'control' for t in result)):
        raise ValueError('Return hint prefix, layout tokens or capacity failed')
    return out


def run(image, index_path, audit_path, glyph_path, p1_review, selected, return_review,
        output, p1_pictures, new_pictures, return_pictures):
    root = Path(__file__).resolve().parents[1]
    local, output = (root / 'local').resolve(), output.resolve()
    if output == local or not output.is_relative_to(local) or image.resolve().is_relative_to(output):
        raise ValueError('Output must be a new local directory separate from the source')
    review = json.loads(return_review.read_text())
    if review != {'schema_version': 1, 'source_image_sha256': CN_IMAGE_SHA256,
                  'file_id': 52, 'table': None, 'entry_index': 4,
                  'original_sha256': SOURCE_SHA, 'translation': '返回',
                  'translation_reviewed': True, 'preserve_original_button_icon_and_spacing': True,
                  'runtime_evidence_report': 'reports/SHARE_THIS_p1_batch04_report.json'}:
        raise ValueError('Return review is incomplete or is not for this source')
    if hash_file(image) != ARCHIVE_SHA:
        raise ValueError('Original archive changed')
    index, audit = json.loads(index_path.read_text()), json.loads(audit_path.read_text())
    reviewed = load_reviewed_glyphs(glyph_path)
    f = next(x for x in index['text_files'] if x['file_id'] == 52)
    row = next(x for x in f['entries'] if x['index'] == 4 and x['table'] is None)
    original = bytes.fromhex(row['raw_hex'])
    if f['lba'] != 2313 or row['raw_sha256'] != SOURCE_SHA or len(original) != row['encoded_bytes']:
        raise ValueError('Private index does not match the inspected entry')
    replacement = prepare_return(original, reviewed)
    font = actual_assets(image, audit)
    old, new = measure(font, original), measure(font, replacement)
    suffix = b''.join(t.raw for t in tokenize(original, variant=CN_C713)[2:])
    old_suffix = measure(font, suffix)
    new_suffix = measure(font, b''.join(t.raw for t in tokenize(replacement, variant=CN_C713)[2:]))
    if (old['draw_max_advance_px'] != 86 or new['draw_max_advance_px'] != 42 or
            new['draw_max_advance_px'] > old['draw_max_advance_px']):
        raise ValueError('Return hint advances differ from the inspected paths')
    contexts = []
    for name, (digest, (x, y)) in SCREENSHOTS.items():
        screenshot = return_pictures[name]
        if hash_file(screenshot) != digest:
            raise ValueError('Return hint source screenshot differs')
        predicted = [old_suffix['ink_bbox_relative_exclusive'][i] + (x + 18 if i % 2 == 0 else y) for i in range(4)]
        actual = neutral_ink_bbox(screenshot, (x + 16, y, min(320, x + 90), y + 13))
        if actual != predicted or x + new['draw_max_advance_px'] > 320 or y + 12 > 240:
            raise ValueError('Source screenshot or target bounds differ in ' + name)
        target_box = [new_suffix['ink_bbox_relative_exclusive'][i] + (x + 18 if i % 2 == 0 else y) for i in range(4)]
        if target_box[2] > x + old['draw_max_advance_px']:
            raise ValueError('Return hint exceeds observed source text band')
        contexts.append({'context': name, 'origin_px': [x, y], 'source_ink_bbox_px': actual,
                         'replacement_text_ink_bbox_px': target_box, 'source_screenshot_sha256': digest,
                         'status': 'runtime_pending'})
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='.batch04-rebuild-', dir=output) as temp_name:
        temp = Path(temp_name)
        rebuilt = temp / 'approved-rebuild'
        built = p2.run(image, index_path, audit_path, glyph_path, p1_review, selected,
                       rebuilt, p1_pictures, new_pictures)
        base = rebuilt / p2.DISC_NAME
        if built['test_image_sha256'] != PRIOR_REBUILD_SHA or hash_file(base) != PRIOR_REBUILD_SHA:
            raise ValueError('Rebuilt preceding entries do not match the approved batch')
        with local_image(image) as raw:
            if hash_file(raw) != CN_IMAGE_SHA256:
                raise ValueError('Original unpacked image differs')
            disc = Disc(raw)
            source = disc.read_user(f['lba'], f['file_size_bytes'])
            verified = inspect_resource(source, f)
            offset = row['offset_bytes']
            if verified['errors'] or not verified['file_template_equal'] or source[offset:offset + len(original)] != original:
                raise ValueError('Original resource does not match its index')
            lba, user_offset = f['lba'] + offset // 2048, offset % 2048
            if lba != 2313 or user_offset + len(original) > 2048:
                raise ValueError('Return text crossed an unsupported sector')
            with base.open('rb') as stream:
                stream.seek(lba * 2352)
                before = stream.read(2352)
            if not check_form1(before):
                raise ValueError('Pre-patch sector failed EDC/ECC')
            after = patch_form1_user_bytes(before, user_offset, original,
                                           replacement + bytes(len(original) - len(replacement)))
            if not check_form1(after):
                raise ValueError('Modified sector failed EDC/ECC')
            result_path = temp / p2.DISC_NAME
            with base.open('rb') as src, result_path.open('xb') as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            with result_path.open('r+b') as dst:
                dst.seek(lba * 2352); dst.write(after); dst.flush(); os.fsync(dst.fileno())
            start = lba * 2352 + USER_BEGIN + user_offset
            delta = make_patch(base, result_path)
            p2.check_diff(delta, [(start, start + len(original))], {lba})
            approved = list(p1.EXPECTED) + [(52, n) for n in p2.TARGETS if n in
                                            {e['entry_index'] for e in built['new_entries']}] + [(52, 4)]
            ranges = []
            for fid, number in approved:
                file = next(x for x in index['text_files'] if x['file_id'] == fid)
                item = next(x for x in file['entries'] if x['index'] == number and x['table'] is None)
                sector = file['lba'] + item['offset_bytes'] // 2048
                pos = sector * 2352 + USER_BEGIN + item['offset_bytes'] % 2048
                ranges.append((pos, pos + item['encoded_bytes']))
            whole = make_patch(raw, result_path)
            p2.check_diff(whole, ranges, {2310, 2313})
            for sector in (2310, 2313):
                for disk in (raw, result_path):
                    with disk.open('rb') as stream:
                        stream.seek(sector * 2352)
                        if not check_form1(stream.read(2352)):
                            raise ValueError('Original or final sector failed EDC/ECC')
            if any(digest_extent(p, FONT_LBA * 2352, FONT_SECTORS * 2352) != FONT_RAW_SHA
                   for p in (raw, base, result_path)) or hash_file(image) != ARCHIVE_SHA:
                raise ValueError('Protected font extent or original archive changed')
            built.update({'status': 'batch04_copy_created_runtime_pending', 'test_image_sha256': whole['target_sha256'],
                          'changed_raw_bytes': whole['changed_bytes'], 'runtime_verified': False,
                          'shared_return_entry': {'file_id': 52, 'entry_index': 4, 'original_sha256': SOURCE_SHA,
                              'translation': '返回', 'slot_bytes': len(original),
                              'replacement_bytes_including_terminator': len(replacement),
                              'original_prefix_tokens_preserved': True, 'original_control_count': 0,
                              'replacement_control_count': 0, 'source_advance_px': old['draw_max_advance_px'],
                              'replacement_advance_px': new['draw_max_advance_px'],
                              'legacy_game_measure_static_px': new['game_measure_max_px'],
                              'contexts': contexts, 'status': 'runtime_pending'}})
            os.link(result_path, output / p2.DISC_NAME)
            exclusive_write(output / 'DW3-p1-batch02.cue',
                            f'FILE "{p2.DISC_NAME}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'.encode())
            exclusive_write(output / 'PRIVATE_batch04_whole_delta.json', (json.dumps(whole, indent=2) + '\n').encode())
            exclusive_write(output / 'PRIVATE_batch04_return_delta.json', (json.dumps(delta, indent=2) + '\n').encode())
            exclusive_write(output / 'SHARE_THIS_batch04_creation_report.json',
                            (json.dumps(built, ensure_ascii=False, indent=2) + '\n').encode())
            return built


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('image', 'private_index', 'prior_audit', 'glyph_review', 'p1_review', 'selected', 'return_review', 'output'):
        parser.add_argument(name, type=Path)
    for name in p1.PICTURES:
        parser.add_argument('--p1-' + name, type=Path, required=True)
    for name in p2.SCREEN_SHA:
        parser.add_argument('--' + name.replace('_', '-'), type=Path, required=True)
    for name in SCREENSHOTS:
        parser.add_argument('--return-' + name.replace('_', '-'), type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.image, args.private_index, args.prior_audit, args.glyph_review, args.p1_review,
                     args.selected, args.return_review, args.output,
                     {n: getattr(args, 'p1_' + n) for n in p1.PICTURES},
                     {n: getattr(args, n) for n in p2.SCREEN_SHA},
                     {n: getattr(args, 'return_' + n) for n in SCREENSHOTS})
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        print(json.dumps({'status': 'blocked', 'error_type': type(exc).__name__, 'error': str(exc)[:500]}))
        return 2
    print(json.dumps({'status': result['status'], 'test_image_sha256': result['test_image_sha256']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
