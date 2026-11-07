#!/usr/bin/env python3
"""Create a hash-locked, four-glyph font experiment on the supplied EU disc.

The bitmap is generated from a separately supplied OFL font, never from the
previous Chinese game font. Four original EU glyph slots are repurposed ONLY
in the new test copy. This is not a scalable Chinese codec or general writer.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct

from tools.byte_patch import make_patch
from tools.font_recovery_probe import EXE_BASE, filetable, font_images, unpack_rlen
from tools.layout_cn import FONT1_BASE, Font1
from tools.make_card_album_sample import exclusive_write
from tools.psx_sector import EDC_BEGIN, USER_BEGIN, check_form1, patch_form1_user_bytes
from tools.roundtrip_cn import inspect_resource
from tools.scan_text_tables import Disc, get_filetable, hash_file, iso9660_files
from tools.text_codec import REFERENCE, tokenize

EU_IMAGE_SHA = 'fb70dc9a995aed628cf515cabc87c7b14e5142559076ec394dfe793ec3e26a04'
EU_ARCHIVE_SHA = '7e68a2caab41d3946a7ca4cf6697ae2c8e3ff990563da07c3e9e64be33d24837'
EU_EXE_SHA = '15f37849a81b9f00e7f20d00de7a465b663a9e86b9d08b05d32dd386be95c546'
EU_FONT_SHA = '9db2bc926b0d5227dd253f7b6b2488198cf52a95bc45894ff6cfa09c8170651a'
TITLE_SHA = '870746459242413a79eb73d41ba6f1e73b30c25dc6b073622f6c2f6a1d681fa3'
SOURCE_FONT_SHA = 'add3145c4372eca6c386b2adf90e98fea21e848809da56ceed90f917a9caeb4e'
SLOTS = ((76, '卡', 168, 77), (77, '片', 72, 78), (83, '图', 144, 86), (87, '鉴', 216, 86))
SLOT_REVIEW_SLOTS = ((76, '记', 168, 77), (77, '忆', 72, 78), (83, '插', 144, 86), (87, '槽', 216, 86))
DEMO_SLOTS = {'album': SLOTS, 'memory-slot': SLOT_REVIEW_SLOTS}
METRIC_TABLES = ((0x8004E5A4, 230, 4), (0x8004EF88, 114, 1),
                 (0x8004F470, 230, 4), (0x8004FE54, 114, 1),
                 (0x8005033C, 230, 4), (0x80050D20, 114, 1))


def merge_ranges(ranges):
    """Join adjacent approved intervals without admitting any gap."""
    merged = []
    for start, end in sorted(ranges):
        if start < 0 or end <= start:
            raise ValueError('Invalid allowed range')
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def pack_rlen(data: bytes) -> bytes:
    """Choose minimum-length RLEN literal/repeat packets, with a terminator."""
    if not data or len(data) > 4_000_000:
        raise ValueError('Unsupported RLEN input size')
    size = len(data)
    cost, choice = [0] * (size + 1), [0] * size
    for i in range(size - 1, -1, -1):
        cap, best, pick = min(127, size - i), 10**9, 0
        for length in range(1, cap + 1):
            score = 1 + length + cost[i + length]
            if score < best:
                best, pick = score, length
        length = 1
        while length < cap and data[i + length] == data[i]:
            length += 1
            score = 2 + cost[i + length]
            if score < best:
                best, pick = score, -length
        cost[i], choice[i] = best, pick
    out = bytearray(b'RLEN' + struct.pack('<I', size))
    i = 0
    while i < size:
        pick = choice[i]
        if pick < 0:
            out.extend((0x80 | -pick, data[i])); i -= pick
        else:
            out.append(pick); out.extend(data[i:i + pick]); i += pick
    out.append(0)
    packed = bytes(out)
    decoded, consumed = unpack_rlen(packed)
    if decoded != data or consumed != len(packed):
        raise ValueError('RLEN roundtrip failed')
    return packed


def validate_slots(exe: bytes, indexed: dict, disc: Disc, font: Font1, slots=SLOTS) -> None:
    if len(indexed['text_files']) != 50 or indexed.get('inventory_complete') is not True:
        raise ValueError('Complete prior English resource index required')
    counts = Counter()
    for f in indexed['text_files']:
        data = disc.read_user(f['lba'], f['file_size_bytes'])
        if hashlib.sha256(data).hexdigest() != f['file_sha256']:
            raise ValueError('An indexed English resource differs')
        checked = inspect_resource(data, f, variant=REFERENCE)
        if checked['errors'] or not checked['file_template_equal']:
            raise ValueError('An indexed English resource failed byte verification')
        for row in f['entries']:
            original = bytes.fromhex(row['raw_hex'])
            if hashlib.sha256(original).hexdigest() != row['raw_sha256']:
                raise ValueError('An indexed entry differs')
            counts.update(t.glyph_index for t in tokenize(original, variant=REFERENCE) if t.kind == 'base_glyph')
    if any(counts[n] for n, _, _, _ in slots):
        raise ValueError('A proposed slot occurs in indexed English text')
    records = []
    for address, count, first_code in METRIC_TABLES:
        for i in range(count):
            record = struct.unpack_from('<BBBBBBBbbBB', exe, address - EXE_BASE + i * 11)
            page, u, v, _, _, width, height, _, _, _, _ = record
            if page in (0, 1) and width and height:
                records.append((address, i + first_code, page, u, v, width, height))
    for number, _, x, y in slots:
        record = font.record(FONT1_BASE, number - 4)
        if (record[0], record[1], record[2], record[5], record[6], record[7], record[8], record[9]) != \
                (0, x, y, 12, 12, 1, 0, 10):
            raise ValueError('A proposed source glyph metric differs')
        palette = font.palette(record)
        if palette[0] != 0 or palette[4] != 0x7FFF:
            raise ValueError('The inspected transparent/white palette changed')
        for table, other, page, ox, oy, width, height in records:
            if table == FONT1_BASE and other == number:
                continue
            if page == 0 and x < ox + width and ox < x + 12 and y < oy + height and oy < y + 12:
                raise ValueError('A proposed atlas slot overlaps another game glyph')


def make_masks(font_path: Path, slots=SLOTS):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise ValueError('Pillow is required to rasterize the separately licensed font') from exc
    if hash_file(font_path) != SOURCE_FONT_SHA:
        raise ValueError('The input font differs from the pinned OFL release')
    font = ImageFont.truetype(str(font_path), 12)
    masks = {}
    for number, char, _, _ in slots:
        canvas = Image.new('1', (12, 12))
        ImageDraw.Draw(canvas).text((0, 0), char, fill=1, font=font, anchor='lt')
        mask = tuple(bool(canvas.getpixel((x, y))) for y in range(12) for x in range(12))
        if not any(mask) or any(mask[y * 12 + 11] for y in range(12)):
            raise ValueError('Glyph absent or wider than the observed 11-pixel advance')
        masks[number] = mask
    if len(set(masks.values())) != len(slots):
        raise ValueError('Font appears to have missing or duplicate prototype glyphs')
    return masks


def masks_from_bank(directory: Path, slots=SLOTS) -> tuple[dict, str]:
    """Read four cells from the generated bank; validate its pinned index."""
    manifest = json.loads((directory / 'PRIVATE_glyph_index.json').read_text())
    path = directory / 'PRIVATE_native12_4bpp.bin'
    digest = hash_file(path)
    if (manifest.get('schema_version') != 1 or manifest.get('bank_size') != 256 or
            manifest.get('native_4bpp_sha256') != digest or
            manifest.get('source_sha256', {}).get('ark') != SOURCE_FONT_SHA):
        raise ValueError('Generated font bank or source binding differs')
    entries = manifest.get('glyphs', [])
    if len(entries) != manifest.get('glyph_count'):
        raise ValueError('Generated font bank index is incomplete')
    by_codepoint = {e['codepoint']: e for e in entries}
    if len(by_codepoint) != len(entries):
        raise ValueError('Duplicate Unicode identity in the generated font bank')
    result = {}
    with path.open('rb') as stream:
        for number, char, _, _ in slots:
            item = by_codepoint[f'U+{ord(char):04X}']
            if (item['index'] // 256, item['index'] % 256, item['native_source'], item['status']) != \
                    (item['bank'], item['slot'], 'ark', 'generated_not_gameplay_verified'):
                raise ValueError('Prototype glyph is not an Ark cell in the bank')
            stream.seek(item['bank'] * 18432 + item['slot'] * 72)
            tile = stream.read(72)
            if len(tile) != 72:
                raise ValueError('Truncated generated font bank')
            values = [(tile[i // 2] >> (4 * (i % 2))) & 15 for i in range(144)]
            if any(v not in (0, 4) for v in values):
                raise ValueError('Unexpected generated font palette index')
            mask = tuple(v == 4 for v in values)
            if not any(mask) or any(mask[y * 12 + 11] for y in range(12)):
                raise ValueError('Generated bank glyph fails the prototype width check')
            result[number] = mask
    return result, digest


def patch_font_resource(original: bytes, masks: dict, slots=SLOTS) -> tuple[bytes, dict]:
    info, images, end = font_images(original)
    if len(images) != 2 or end > len(original) or any(original[end:]):
        raise ValueError('Unexpected original font container or nonzero tail')
    page = bytearray(images[0])
    block = info[0]['blocks'][-1]
    if (block['width_words'], block['height']) != (64, 256):
        raise ValueError('Unexpected font image dimensions')
    allowed = set()
    for number, _, left, top in slots:
        for y in range(12):
            for x in range(12):
                pos = block['data_offset'] + (top + y) * 128 + (left + x) // 2
                shift = 4 * ((left + x) % 2)
                value = 4 if masks[number][y * 12 + x] else 0
                page[pos] = (page[pos] & (255 ^ (15 << shift))) | (value << shift)
                allowed.add(pos)
    if any(a != b and i not in allowed for i, (a, b) in enumerate(zip(images[0], page))):
        raise ValueError('Font texture changed outside the four selected slots')
    packed = pack_rlen(bytes(page))
    sub = struct.unpack_from('<I', original, 0)[0]
    first = info[0]['resource_offset']
    second = info[1]['resource_offset']
    header = bytearray(original[:first])
    struct.pack_into('<I', header, sub + 4, first - sub + len(packed))
    second_packed = original[second:second + info[1]['compressed_bytes']]
    out = bytes(header) + packed + second_packed
    if len(out) > len(original):
        raise ValueError('New font resource exceeds its fixed original size')
    out += bytes(len(original) - len(out))
    new_info, new_images, _ = font_images(out)
    if new_images[0] != bytes(page) or new_images[1] != images[1]:
        raise ValueError('Rebuilt font resource failed decoded-image comparison')
    return out, {'old_first_page_rlen_bytes': info[0]['compressed_bytes'],
                 'new_first_page_rlen_bytes': new_info[0]['compressed_bytes'],
                 'original_second_page_unchanged': True, 'outside_four_atlas_slots_unchanged': True,
                 'resource_size_bytes': len(original)}


def run(image: Path, archive: Path, private_index: Path, font_path: Path, output: Path,
        bank: Path | None = None, slots=SLOTS, advance: int = 11) -> dict:
    root = Path(__file__).resolve().parents[1]
    local, output = (root / 'local').resolve(), output.resolve()
    if output == local or not output.is_relative_to(local) or any(p.resolve().is_relative_to(output)
            for p in (image, archive, private_index, font_path)):
        raise ValueError('Use a new private local output separate from every input')
    if hash_file(image) != EU_IMAGE_SHA or hash_file(archive) != EU_ARCHIVE_SHA:
        raise ValueError('Original EU image or archive differs')
    index = json.loads(private_index.read_text())
    if index.get('image_sha256') != EU_IMAGE_SHA:
        raise ValueError('Private index belongs to a different image')
    disc = Disc(image)
    if (disc.sector_bytes, disc.user_offset, disc.sector_count) != (2352, 24, 294280):
        raise ValueError('EU image geometry differs')
    iso = iso9660_files(disc)
    table = get_filetable(disc, iso)
    if table['exe_sha256'] != EU_EXE_SHA or table['filetable_match_ratio'] != 1.0:
        raise ValueError('EU executable or file table differs')
    exe_lba, exe_info = next((lba, x) for lba, x in iso.items() if x['path'].upper() == 'SLES_039.36')
    exe = disc.read_user(exe_lba, exe_info['size_bytes'])
    font_lba, sectors = filetable(exe, 647)
    if (font_lba, sectors) != (2268, 22) or iso[font_lba]['size_bytes'] != 43660:
        raise ValueError('Original EU font resource extent differs')
    original_font = disc.read_user(font_lba, iso[font_lba]['size_bytes'])
    if hashlib.sha256(original_font).hexdigest() != EU_FONT_SHA:
        raise ValueError('Original EU font resource differs')
    font = Font1.__new__(Font1)
    font.exe = exe
    font.info, font.images, _ = font_images(original_font)
    if slots not in DEMO_SLOTS.values():
        raise ValueError('Only the two explicitly inspected font demos are permitted')
    if advance not in (11, 12) or (advance == 12 and slots != SLOT_REVIEW_SLOTS):
        raise ValueError('The 12-pixel spacing is restricted to the memory-slot demo')
    validate_slots(exe, index, disc, font, slots)
    direct_masks = make_masks(font_path, slots)
    masks, bank_sha = masks_from_bank(bank, slots) if bank is not None else (direct_masks, None)
    if masks != direct_masks:
        raise ValueError('Bank glyphs differ from direct Ark font rendering')
    font_replacement, font_summary = patch_font_resource(original_font, masks, slots)
    resource = next(x for x in index['text_files'] if x['file_id'] == 38)
    row = next(x for x in resource['entries'] if x['table'] is None and x['index'] == 1)
    source = disc.read_user(resource['lba'], resource['file_size_bytes'])
    original = bytes.fromhex(row['raw_hex'])
    if (row['raw_sha256'] != TITLE_SHA or hashlib.sha256(original).hexdigest() != TITLE_SHA or
            source[row['offset_bytes']:row['offset_bytes'] + len(original)] != original or
            any(t.kind == 'control' for t in tokenize(original, variant=REFERENCE))):
        raise ValueError('Original title or its control codes differ')
    replacement = bytes(n for n, _, _, _ in slots) + b'\0'
    if len(replacement) > len(original) or resource['lba'] != 2310:
        raise ValueError('Prototype title does not fit its original fixed slot')
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    final = output / 'DW2003-EU-font-prototype.bin'
    with image.open('rb') as src, final.open('xb') as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    changed = set()
    allowed = []
    with final.open('r+b') as stream:
        if advance == 12:
            for number, _, _, _ in slots:
                exe_offset = FONT1_BASE - EXE_BASE + (number - 4) * 11 + 9
                if exe[exe_offset] != 10:
                    raise ValueError('Original glyph advance metric differs')
                lba, user_offset = exe_lba + exe_offset // 2048, exe_offset % 2048
                stream.seek(lba * 2352); sector = stream.read(2352)
                revised = patch_form1_user_bytes(sector, user_offset, b'\x0a', b'\x0b')
                stream.seek(lba * 2352); stream.write(revised)
                changed.add(lba)
                raw_offset = lba * 2352 + USER_BEGIN + user_offset
                allowed.append((raw_offset, raw_offset + 1))
        for i in range(sectors):
            lba, start = font_lba + i, i * 2048
            old, new = original_font[start:start+2048], font_replacement[start:start+2048]
            if old == new:
                continue
            stream.seek(lba * 2352); sector = stream.read(2352)
            revised = patch_form1_user_bytes(sector, 0, old, new)
            stream.seek(lba * 2352); stream.write(revised)
            changed.add(lba); allowed.append((lba * 2352 + USER_BEGIN, lba * 2352 + USER_BEGIN + len(old)))
        offset = row['offset_bytes']
        lba, user_offset = resource['lba'] + offset // 2048, offset % 2048
        if user_offset + len(original) > 2048:
            raise ValueError('Title unexpectedly crosses a sector boundary')
        stream.seek(lba * 2352); sector = stream.read(2352)
        revised = patch_form1_user_bytes(sector, user_offset, original,
                                         replacement + bytes(len(original) - len(replacement)))
        stream.seek(lba * 2352); stream.write(revised)
        changed.add(lba)
        start = lba * 2352 + USER_BEGIN + user_offset
        allowed.append((start, start + len(original)))
        stream.flush(); os.fsync(stream.fileno())
    delta = make_patch(image, final)
    for lba in changed:
        allowed.append((lba * 2352 + EDC_BEGIN, (lba + 1) * 2352))
    # A diff span can run continuously across the end of the font payload
    # and the immediately adjacent EDC. Their union is approved; the sector
    # header and every gap remain excluded.
    allowed = merge_ranges(allowed)
    seen = set()
    for span in delta['spans']:
        start, end = span['offset'], span['offset'] + len(span['after_hex']) // 2
        if not any(a <= start < end <= b for a, b in allowed):
            raise ValueError('Unexpected difference outside the allowed font/title sectors')
        seen.add(start // 2352)
    if seen != changed:
        raise ValueError('Unexpected changed-sector set')
    for disc_path in (image, final):
        with disc_path.open('rb') as stream:
            for lba in changed:
                stream.seek(lba * 2352)
                if not check_form1(stream.read(2352)):
                    raise ValueError('Original or modified sector failed EDC/ECC')
    target_disc = Disc(final)
    if (target_disc.read_user(font_lba, len(original_font)) != font_replacement or
            hash_file(image) != EU_IMAGE_SHA or hash_file(archive) != EU_ARCHIVE_SHA):
        raise ValueError('Font readback or original source hash differs')
    if advance == 12:
        new_exe = target_disc.read_user(exe_lba, exe_info['size_bytes'])
        for number, _, _, _ in slots:
            if new_exe[FONT1_BASE - EXE_BASE + (number - 4) * 11 + 9] != 11:
                raise ValueError('Experimental glyph advance readback differs')
    report = {'schema_version': 1, 'status': 'prototype_copy_created_runtime_pending',
              'source_image_sha256': EU_IMAGE_SHA, 'source_archive_sha256': EU_ARCHIVE_SHA,
              'test_image_sha256': delta['target_sha256'], 'source_executable_sha256': EU_EXE_SHA,
              'test_title': {'file_id': 38, 'entry_index': 1, 'source_sha256': TITLE_SHA,
                             'translation': ''.join(char for _, char, _, _ in slots), 'original_capacity_bytes': len(original),
                             'replacement_bytes_including_terminator': len(replacement),
                             'font_1_advance_per_glyph_px': advance, 'runtime_verified': False},
              'experimental_mapping': [{'slot': n, 'unicode': char} for n, char, _, _ in slots],
              'source_font': {'name': 'Ark Pixel 12px Monospaced Simplified Chinese', 'version': '2026.10.25',
                              'source_url': 'https://github.com/TakWolf/ark-pixel-font', 'license': 'OFL-1.1',
                              'source_font_sha256': SOURCE_FONT_SHA, 'font_file_distributed': False,
                              'generated_4bpp_bank_sha256': bank_sha},
              'font_resource': font_summary, 'integrity': {'original_image_unchanged': True,
                  'original_archive_unchanged': True, 'changed_sectors': sorted(changed),
                  'unchanged_sector_count': disc.sector_count - len(changed), 'changed_raw_bytes': delta['changed_bytes'],
                  'all_changes_within_font_title_metrics_or_sector_checksums': True,
                  'all_modified_sectors_edc_ecc_before_and_after_valid': True},
              'limitations': ['Four original Japanese glyph slots were repurposed in the experimental copy only.',
                  'Those slots do not occur in the 50 indexed English resources, but other languages and dynamic paths are not covered.',
                  'This does not implement a scalable Chinese codec or a complete font.',
                  'Runtime rendering, controller behavior and save compatibility require separate tests.']}
    if slots == SLOT_REVIEW_SLOTS:
        report['limitations'].append('This memory-slot wording is an artificial glyph preview in the album title; the actual memory-card screen is unchanged.')
    if advance == 12:
        report['limitations'].append('Four font-1 advance metrics were changed only for this spacing experiment; other windows and fonts are not verified.')
    exclusive_write(output / 'DW2003-EU-font-prototype.cue',
                    b'FILE "DW2003-EU-font-prototype.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
    exclusive_write(output / 'PRIVATE_delta.json', (json.dumps(delta, indent=2) + '\n').encode())
    exclusive_write(output / 'SHARE_THIS_creation_report.json',
                    (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode())
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('image', 'archive', 'private_index', 'font_path', 'output'):
        p.add_argument(name, type=Path)
    p.add_argument('--bank', type=Path, help='Hash-checked local generated font-bank directory')
    p.add_argument('--demo', choices=tuple(DEMO_SLOTS), default='album',
                   help='Choose the album title or isolated memory-slot glyph-visibility demo')
    p.add_argument('--advance-12', action='store_true',
                   help='Experiment with 12-pixel spacing in the memory-slot demo only')
    a = p.parse_args()
    try:
        result = run(a.image, a.archive, a.private_index, a.font_path, a.output, a.bank,
                     DEMO_SLOTS[a.demo], 12 if a.advance_12 else 11)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        print(json.dumps({'status': 'blocked', 'error_type': type(exc).__name__, 'error': str(exc)[:500]}))
        return 2
    print(json.dumps({'status': result['status'], 'test_image_sha256': result['test_image_sha256']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
