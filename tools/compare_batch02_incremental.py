#!/usr/bin/env python3
"""Compare two locally built, separately verified batch-02 copies read-only."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path

try:
    from tools.byte_patch import make_patch
    from tools.font_recovery_probe import FONT_LBA, FONT_SECTORS
    from tools.make_card_album_sample import FONT_RAW_SHA, digest_extent
    from tools.make_card_menu_batch02 import check_diff
    from tools.psx_sector import USER_BEGIN, check_form1
    from tools.scan_text_tables import hash_file
    from tools.text_codec import CN_IMAGE_SHA256
except ModuleNotFoundError:
    from byte_patch import make_patch
    from font_recovery_probe import FONT_LBA, FONT_SECTORS
    from make_card_album_sample import FONT_RAW_SHA, digest_extent
    from make_card_menu_batch02 import check_diff
    from psx_sector import USER_BEGIN, check_form1
    from scan_text_tables import hash_file
    from text_codec import CN_IMAGE_SHA256


def run(previous: Path, expanded: Path, previous_report: Path, expanded_report: Path, private_index: Path):
    old, new, index = (json.loads(p.read_text()) for p in (previous_report, expanded_report, private_index))
    if (old.get('source_image_sha256') != CN_IMAGE_SHA256 or new.get('source_image_sha256') != CN_IMAGE_SHA256 or
        index.get('image_sha256') != CN_IMAGE_SHA256 or
        old.get('status') != 'batch02_new_entries_runtime_verified' or
        new.get('status') != 'batch02_new_entries_runtime_verified' or
        hash_file(previous) != old.get('test_image_sha256') or hash_file(expanded) != new.get('test_image_sha256')):
        raise ValueError('A batch image or its runtime evidence differs')
    old_entries = {e['entry_index']: e for e in old['new_entries']}
    new_entries = {e['entry_index']: e for e in new['new_entries']}
    if set(old_entries) != {19} or set(new_entries) != {19, 25, 27}:
        raise ValueError('The expected prior and newly added entries differ')
    if any(old_entries[19][k] != new_entries[19][k] for k in ('translation', 'original_sha256')):
        raise ValueError('The previously verified translation changed')
    f = next(x for x in index['text_files'] if x['file_id'] == 52)
    if f['lba'] != 2313:
        raise ValueError('Resource extent differs from the inspected baseline')
    ranges = []
    for number in (25, 27):
        row = next(e for e in f['entries'] if e['table'] is None and e['index'] == number)
        if row['raw_sha256'] != new_entries[number]['original_sha256']:
            raise ValueError('Entry hash differs from the real index')
        start = 2313 * 2352 + USER_BEGIN + row['offset_bytes']
        ranges.append((start, start + row['encoded_bytes']))
    delta = make_patch(previous, expanded)
    if delta['source_sha256'] != old['test_image_sha256'] or delta['target_sha256'] != new['test_image_sha256']:
        raise ValueError('Whole-disc comparison hash differs')
    check_diff(delta, ranges, {2313})
    for path in (previous, expanded):
        with path.open('rb') as stream:
            stream.seek(2313 * 2352)
            if not check_form1(stream.read(2352)):
                raise ValueError('Changed sector has invalid EDC/ECC')
        if digest_extent(path, FONT_LBA * 2352, FONT_SECTORS * 2352) != FONT_RAW_SHA:
            raise ValueError('Protected ID 647 differs')
    return {'schema_version': 1, 'status': 'previous_batch02_preserved_new_entries_only',
            'source_image_sha256': CN_IMAGE_SHA256, 'previous_test_image_sha256': old['test_image_sha256'],
            'expanded_test_image_sha256': new['test_image_sha256'], 'new_entries': [25, 27],
            'changed_raw_bytes': delta['changed_bytes'], 'changed_sectors': [2313],
            'all_other_bytes_identical': True, 'changed_sector_edc_ecc_valid_before_and_after': True,
            'font_id_647_unchanged': True}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('previous_image', 'expanded_image', 'previous_report', 'expanded_report', 'private_index', 'report'):
        p.add_argument(name, type=Path)
    a = p.parse_args()
    try:
        result = run(a.previous_image, a.expanded_image, a.previous_report, a.expanded_report, a.private_index)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        result = {'schema_version': 1, 'status': 'blocked', 'error_type': type(exc).__name__, 'error': str(exc)[:500]}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(a.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as out:
        json.dump(result, out, ensure_ascii=False, indent=2); out.write('\n')
    print(json.dumps({'status': result['status'], 'report': str(a.report)}))
    return 2 if result['status'] == 'blocked' else 0


if __name__ == '__main__':
    raise SystemExit(main())
