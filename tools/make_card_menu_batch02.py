#!/usr/bin/env python3
"""Rebuild batch 01 from its original ZIP, then add narrowly approved menu labels.

Every selected entry, glyph, source image, menu overlay, text band, and raw
disc difference must pass its own check. This is not a general-purpose writer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

try:
    from tools import make_card_menu_batch as p1
    from tools.byte_patch import make_patch
    from tools.font_recovery_probe import ARCHIVE_SHA, EXE_SHA, FONT_LBA, FONT_SECTORS, filetable
    from tools.layout_cn import actual_assets, measure, neutral_ink_bbox
    from tools.make_card_album_sample import FONT_RAW_SHA, digest_extent, exclusive_write
    from tools.prepare_translation_patch import prepare
    from tools.psx_sector import EDC_BEGIN, USER_BEGIN, check_form1, patch_form1_user_bytes
    from tools.roundtrip_cn import inspect_resource
    from tools.scan_text_tables import Disc, hash_file, local_image
    from tools.text_codec import CN_C713, CN_IMAGE_SHA256, cn_canonical_code, load_reviewed_glyphs, tokenize
except ModuleNotFoundError:
    import make_card_menu_batch as p1
    from byte_patch import make_patch
    from font_recovery_probe import ARCHIVE_SHA, EXE_SHA, FONT_LBA, FONT_SECTORS, filetable
    from layout_cn import actual_assets, measure, neutral_ink_bbox
    from make_card_album_sample import FONT_RAW_SHA, digest_extent, exclusive_write
    from prepare_translation_patch import prepare
    from psx_sector import EDC_BEGIN, USER_BEGIN, check_form1, patch_form1_user_bytes
    from roundtrip_cn import inspect_resource
    from scan_text_tables import Disc, hash_file, local_image
    from text_codec import CN_C713, CN_IMAGE_SHA256, cn_canonical_code, load_reviewed_glyphs, tokenize

P1_SHA = '03fa8316801fd610fec4dfdf895753d25121f8c955f3835f0ad4446d12886193'
DISC_NAME = 'DW3-p1-batch02.bin'
TARGETS = {
    13: ('买', 197, 86, 'buy_prompt', '598128e4fde4363b2fa0f28af6b1ed39ec50316d33559ea0fd2b0692e1be0162'),
    14: ('不', 197, 102, 'buy_prompt', '42ab71a2a7058da8ffa2eb77eac6092996d062f96a749f49454ff8e40d8fbe86'),
    15: ('选择卡包', 20, 194, 'booster', '4fc2ad8f756b55ddcbca5076b15076465222b2094a45e60557fc12e5763c0608'),
    16: ('卡包已打开', 20, 194, 'booster_opened', '6fdede1328d511028b738d820b76703bcd4b79bd4d60074ad3fc46b6201f91a6'),
    19: ('金钱不足', 154, 57, 'buy', '2a81ed939dbcdf7f2b3de39f07239c28e50f282e499f30ec1fe84e3bfe25c793'),
    20: ('已持有九张', 154, 57, 'limit', '506519d000e80369e62d91eb609eb97e56fa8d0b9cb2a384de73eb5c03166a18'),
    25: ('选择卡组', 151, 34, 'selection', '796cf96018087f19cfdffd3da937f6a39e90a779e024f5871d8da56cf6fadc6c'),
    27: ('改名', 167, 49, 'rename', 'f980d46324d23b3a0939f54269e794a498524ce25a0a7cb9c7fdef4873fab7b9'),
}
SCREEN_SHA = {'buy_prompt': '2a6f8f76855d95675d6e5fb8d30c2ec8191e813f7b62ea77c63d2179b119ef04',
              'booster_opened': '4a61fae7a72921af2a2893c757b3c9b65863f2f1328be6ceae8d40f8838d4898',
              'booster': 'ea5afe23bcb3fd475c718f922844b60503ad512d2d436a10930112f6dff1bd92',
              'limit': '3adab047562e54bcbc0d82c36f030775b612a2194af0e1aeca7066b8cdde6b39',
              'buy': 'b1992a9c07564b3d523caaadb7f00b86cf7c9cf36f0b138dce5cd65ce9549340',
              'selection': '40dcdeb334702a009aa18644a45e861805fe1c9e199392f1b55f4cd2c5bbfcb4',
              'rename': '2aa56962940c016722c5311e0f73b315e7dfa27c1bc3f82043958bc1853cd13b'}


def check_diff(delta, ranges, sectors):
    allowed = ranges + [(lba * 2352 + EDC_BEGIN, (lba + 1) * 2352) for lba in sectors]
    changed = set()
    for span in delta['spans']:
        start, end = span['offset'], span['offset'] + len(span['after_hex']) // 2
        if not any(a <= start < end <= b for a, b in allowed):
            raise ValueError('Unexpected raw-disc difference')
        changed.add(start // 2352)
    if changed != set(sectors):
        raise ValueError('Changed sectors differ from the approved set')


def run(image: Path, index_path: Path, audit_path: Path, glyph_path: Path, p1_review_path: Path,
        selected_path: Path, output: Path, p1_pictures: dict[str, Path], new_pictures: dict[str, Path]):
    root = Path(__file__).resolve().parents[1]
    local, output = (root / 'local').resolve(), output.resolve()
    if output == local or not output.is_relative_to(local) or image.resolve().is_relative_to(output):
        raise ValueError('Output must be a new local directory separate from the source')
    if hash_file(image) != ARCHIVE_SHA:
        raise ValueError('Source ZIP differs from the inspected archive')
    index, audit = json.loads(index_path.read_text()), json.loads(audit_path.read_text())
    selected = json.loads(selected_path.read_text())
    if selected.get('schema_version') != 1 or selected.get('source_image_sha256') != CN_IMAGE_SHA256:
        raise ValueError('Translation review is not bound to the original image')
    entries = selected.get('entries', [])
    keys = [e.get('entry_index') for e in entries]
    if not keys or len(set(keys)) != len(keys) or not set(keys) <= set(TARGETS):
        raise ValueError('Unsupported or duplicate batch-02 targets')
    reviewed = load_reviewed_glyphs(glyph_path)
    for e in entries:
        target, _, _, context, digest = TARGETS[e['entry_index']]
        if (e.get('file_id') != 52 or e.get('table') is not None or e.get('target') != target or
            e.get('original_sha256') != digest or e.get('translation_reviewed') is not True or
            any(ch not in reviewed.values() for ch in target)):
            raise ValueError('Translation, preimage, or user-reviewed glyph identity is incomplete')
        if not new_pictures.get(context) or hash_file(new_pictures[context]) != SCREEN_SHA[context]:
            raise ValueError('A source screenshot differs from its real replay')
    font = actual_assets(image, audit)
    profile = {'schema_version': 1, 'verified_for_image_sha256': CN_IMAGE_SHA256,
               'mapping_reviewed': True, 'glyphs': {ch: cn_canonical_code(i).hex() for i, ch in reviewed.items()}}
    review = {'schema_version': 1, 'source_image_sha256': CN_IMAGE_SHA256,
              'entries': [{'file_id': 52, 'table': None, 'index': e['entry_index'],
                           'original_sha256': e['original_sha256'], 'reviewed': True,
                           'segments': [e['target']]} for e in entries]}
    prepared = prepare(index, profile, review)
    replacements = {e['index']: bytes.fromhex(e['replacement_hex']) for e in prepared['changes']}
    if set(replacements) != set(keys):
        raise ValueError('Prepared targets differ from the reviewed selection')
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='.batch02-build-', dir=output) as temp:
        temp = Path(temp)
        p1_dir = temp / 'batch01'
        prior = p1.run(image, index_path, audit_path, glyph_path, p1_review_path, p1_dir, p1_pictures)
        p1_image = p1_dir / p1.DISC_NAME
        if prior['test_image_sha256'] != P1_SHA or hash_file(p1_image) != P1_SHA:
            raise ValueError('Rebuilt first batch is not the previously verified sample')
        with local_image(image) as raw:
            if hash_file(raw) != CN_IMAGE_SHA256:
                raise ValueError('Unpacked original image differs')
            disc = Disc(raw)
            if (disc.sector_bytes, disc.user_offset) != (2352, 24):
                raise ValueError('Unsupported disc sector layout')
            boot = next(f for f in audit['iso_directory_files'] if f['path'].upper() == 'SLES_039.36')
            exe = disc.read_user(boot['lba'], boot['size_bytes'])
            if hashlib.sha256(exe).hexdigest() != EXE_SHA or filetable(exe, 52) != (2313, 1):
                raise ValueError('Executable or resource file table differs')
            files = {f['file_id']: f for f in index['text_files'] if f['file_id'] in (38, 52)}
            source = disc.read_user(files[52]['lba'], files[52]['file_size_bytes'])
            checked = inspect_resource(source, files[52])
            if checked['errors'] or not checked['file_template_equal']:
                raise ValueError('Source resource differs from its private index')
            all_ranges, new_ranges, sectors, results = [], [], {}, []
            for fid, idx in p1.EXPECTED:
                f = files[fid]
                row = next(r for r in f['entries'] if r['table'] is None and r['index'] == idx)
                lba = f['lba'] + row['offset_bytes'] // 2048
                begin = lba * 2352 + USER_BEGIN + row['offset_bytes'] % 2048
                all_ranges.append((begin, begin + row['encoded_bytes']))
            with p1_image.open('rb') as stream:
                for e in entries:
                    number = e['entry_index']
                    target, x, y, context, digest = TARGETS[number]
                    row = next(r for r in files[52]['entries'] if r['table'] is None and r['index'] == number)
                    original, replacement = bytes.fromhex(row['raw_hex']), replacements[number]
                    if row['raw_sha256'] != digest or source[row['offset_bytes']:row['offset_bytes'] + len(original)] != original:
                        raise ValueError('New target source bytes do not match the baseline')
                    old_tokens, new_tokens = tokenize(original, variant=CN_C713), tokenize(replacement, variant=CN_C713)
                    if any(t.kind == 'control' for t in old_tokens + new_tokens):
                        raise ValueError('These fixed labels may not contain control codes')
                    if any(t.kind not in ('cn_glyph', 'terminator') for t in new_tokens) or len(replacement) > len(original):
                        raise ValueError('Unreviewed target bytes or insufficient capacity')
                    old, new = measure(font, original), measure(font, replacement)
                    ob, nb = old['ink_bbox_relative_exclusive'], new['ink_bbox_relative_exclusive']
                    predicted = [ob[0] + x, ob[1] + y, ob[2] + x, ob[3] + y]
                    actual = neutral_ink_bbox(new_pictures[context], (x-1, y, min(x+143, 320), y+13))
                    if actual != predicted or new['draw_max_advance_px'] > old['draw_max_advance_px']:
                        raise ValueError('Source ink or replacement advance fails its observed window')
                    if not (nb[0] >= 0 and nb[1] >= 0 and nb[2] <= old['draw_max_advance_px'] and
                            nb[3] <= 12 and x + nb[2] <= 320 and y + nb[3] <= 240):
                        raise ValueError('Replacement ink leaves the observed source text band')
                    offset = row['offset_bytes']
                    lba, user_offset = files[52]['lba'] + offset // 2048, offset % 2048
                    if user_offset + len(original) > 2048:
                        raise ValueError('Target crosses an unsupported sector boundary')
                    if lba not in sectors:
                        stream.seek(lba * 2352)
                        sectors[lba] = stream.read(2352)
                        if not check_form1(sectors[lba]):
                            raise ValueError('First-batch source sector has invalid EDC/ECC')
                    sectors[lba] = patch_form1_user_bytes(sectors[lba], user_offset, original,
                                                          replacement + bytes(len(original) - len(replacement)))
                    begin = lba * 2352 + USER_BEGIN + user_offset
                    new_ranges.append((begin, begin + len(original)))
                    all_ranges.append(new_ranges[-1])
                    results.append({'file_id': 52, 'entry_index': number, 'original_sha256': digest,
                        'translation': target, 'slot_bytes': len(original), 'replacement_bytes_including_terminator': len(replacement),
                        'original_control_count': 0, 'replacement_control_count': 0, 'window_origin_px': [x, y],
                        'source_advance_px': old['draw_max_advance_px'], 'replacement_advance_px': new['draw_max_advance_px'],
                        'source_ink_bbox_px': predicted, 'replacement_ink_bbox_px': [nb[0]+x, nb[1]+y, nb[2]+x, nb[3]+y],
                        'legacy_game_measure_static_px': new['game_measure_max_px'], 'context': context,
                        'source_screenshot_sha256': SCREEN_SHA[context], 'status': 'runtime_pending'})
            final_temp = temp / DISC_NAME
            with p1_image.open('rb') as src, final_temp.open('xb') as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            with final_temp.open('r+b') as dst:
                for lba, sector in sectors.items():
                    dst.seek(lba * 2352); dst.write(sector)
                dst.flush(); os.fsync(dst.fileno())
            with final_temp.open('rb') as stream:
                for lba, sector in sectors.items():
                    stream.seek(lba * 2352)
                    if stream.read(2352) != sector or not check_form1(sector):
                        raise ValueError('Final modified sector failed EDC/ECC')
            incremental = make_patch(p1_image, final_temp)
            check_diff(incremental, new_ranges, sectors)
            whole = make_patch(raw, final_temp)
            check_diff(whole, all_ranges, {2310, 2313})
            extent = (FONT_LBA * 2352, FONT_SECTORS * 2352)
            if any(digest_extent(p, *extent) != FONT_RAW_SHA for p in (raw, p1_image, final_temp)):
                raise ValueError('Protected ID 647 differs')
            if hash_file(image) != ARCHIVE_SHA:
                raise ValueError('Original archive changed')
            result = {'schema_version': 1, 'status': 'batch02_copy_created_runtime_pending',
                'source_image_sha256': CN_IMAGE_SHA256, 'previous_batch_image_sha256': P1_SHA,
                'test_image_sha256': whole['target_sha256'], 'source_archive_sha256': ARCHIVE_SHA,
                'previous_entries': prior['entries'], 'new_entries': results,
                'expected_changed_sectors': [2310, 2313], 'changed_raw_bytes': whole['changed_bytes'],
                'changed_raw_sectors': 2, 'unchanged_raw_sectors': disc.sector_count - 2,
                'all_differences_within_reviewed_text_or_modified_sector_checksums': True,
                'previous_batch_changes_preserved': True, 'modified_sector_edc_ecc_before_and_after_valid': True,
                'font_id_647_raw_sha256_before_and_after': FONT_RAW_SHA, 'generic_writer_unblocked': False,
                'runtime_verified': False}
            os.link(final_temp, output / DISC_NAME)
            exclusive_write(output / 'DW3-p1-batch02.cue',
                            f'FILE "{DISC_NAME}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'.encode())
            exclusive_write(output / 'PRIVATE_batch02_whole_delta.json', (json.dumps(whole, indent=2) + '\n').encode())
            exclusive_write(output / 'PRIVATE_batch02_incremental_delta.json', (json.dumps(incremental, indent=2) + '\n').encode())
            exclusive_write(output / 'SHARE_THIS_batch02_creation_report.json',
                            (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode())
            return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('image', 'private_index', 'prior_audit', 'glyph_review', 'p1_review', 'selected', 'output'):
        p.add_argument(name, type=Path)
    for name in p1.PICTURES:
        p.add_argument('--p1-' + name, type=Path, required=True)
    for name in SCREEN_SHA:
        p.add_argument('--' + name.replace('_', '-'), type=Path,
                       required=name not in ('booster', 'limit', 'buy_prompt', 'booster_opened'))
    a = p.parse_args()
    try:
        result = run(a.image, a.private_index, a.prior_audit, a.glyph_review, a.p1_review, a.selected, a.output,
                     {n: getattr(a, 'p1_' + n) for n in p1.PICTURES}, {n: getattr(a, n) for n in SCREEN_SHA})
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        print(json.dumps({'status': 'blocked', 'error_type': type(exc).__name__, 'error': str(exc)[:500]}))
        return 2
    print(json.dumps({'status': result['status'], 'test_image_sha256': result['test_image_sha256']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
