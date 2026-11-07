#!/usr/bin/env python3
"""Rebuild one EU text table in memory, without modifying any disc.

This is a narrow proof that a proposed longer banked token can be represented
with adjusted table pointers. It is not a disc writer and does not demonstrate
that the unmodified game can decode the new bytes. The rebuilt resource stays
private because it still contains original game data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct

from tools.eu_banked_codec import bank_index, encode_text, tokenize_proposed
from tools.eu_font_prototype import EU_IMAGE_SHA, TITLE_SHA
from tools.roundtrip_cn import inspect_resource
from tools.scan_text_tables import Disc, hash_file
from tools.text_codec import REFERENCE, tokenize


def rebuild_flat(data: bytes, resource: dict, replacements: dict[int, bytes], count: int):
    """Preserve each original non-text tail and align relocated strings to 4.

    Accept only a complete, validated flat table. Every control token must be
    identical and ordered; unknown tokens in a changed entry block the rebuild.
    The returned bytes are an offline artifact and have not been game-tested.
    """
    if resource.get('container') or resource.get('table_count') != 1:
        raise ValueError('Only a single flat table is supported')
    checked = inspect_resource(data, resource, variant=REFERENCE)
    if checked['errors'] or not checked['file_template_equal']:
        raise ValueError('Source text resource failed validation')
    entries = sorted(resource['entries'], key=lambda row: row['index'])
    if not replacements or any(type(i) is not int or not 0 <= i < len(entries) for i in replacements):
        raise ValueError('Replacement entry is absent')
    header_size = 4 + 4 * len(entries)
    out = bytearray(data[:header_size])
    new_offsets, parts, changes = [], [], []
    for i, row in enumerate(entries):
        old = bytes.fromhex(row['raw_hex'])
        begin = row['offset_bytes']
        end = entries[i+1]['offset_bytes'] if i+1 < len(entries) else len(data)
        tail = data[begin + len(old):end]
        new = replacements.get(i, old)
        if i in replacements:
            old_tokens = tokenize(old, variant=REFERENCE)
            new_tokens = tokenize_proposed(new, count)
            if any(t.kind.startswith('unknown_') for t in old_tokens + new_tokens):
                raise ValueError('Unknown token in changed entry')
            if [t.raw for t in old_tokens if t.kind == 'control'] != \
                    [t.raw for t in new_tokens if t.kind == 'control']:
                raise ValueError('Control tokens changed')
            changes.append({'entry_index': i, 'source_bytes_including_terminator': len(old),
                            'replacement_bytes_including_terminator': len(new),
                            'original_slot_bytes': end - begin,
                            'source_sha256': hashlib.sha256(old).hexdigest()})
        new_offsets.append(len(out))
        out.extend(new)
        out.extend(tail)
        if i + 1 < len(entries):
            out.extend(b'\0' * (-len(out) % 4))
        parts.append((new, tail))
    for i, offset in enumerate(new_offsets):
        struct.pack_into('<I', out, 4 + i*4, offset)
    for i, (raw, tail) in enumerate(parts):
        offset = struct.unpack_from('<I', out, 4 + i*4)[0]
        limit = new_offsets[i+1] if i+1 < len(entries) else len(out)
        if offset != new_offsets[i] or out[offset:offset+len(raw)] != raw or \
                out[offset+len(raw):offset+len(raw)+len(tail)] != tail or \
                any(out[offset+len(raw)+len(tail):limit]):
            raise ValueError('Rebuilt table did not preserve entry data and non-text tails')
        if i + 1 < len(entries) and (limit <= offset or limit % 4):
            raise ValueError('Rebuilt pointer is not increasing and aligned')
    return bytes(out), changes


def run(image: Path, private_index: Path, bank: Path, output: Path):
    root = Path(__file__).resolve().parents[1]
    local = (root / 'local').resolve()
    if output.resolve() == local or any(not p.resolve().is_relative_to(local)
                                        for p in (image, private_index, bank, output)):
        raise ValueError('Image, index, font bank and output must be under local/')
    if hash_file(image) != EU_IMAGE_SHA:
        raise ValueError('Unexpected source image')
    idx = json.loads(private_index.read_text())
    if idx.get('image_sha256') != EU_IMAGE_SHA or idx.get('inventory_complete') is not True:
        raise ValueError('A complete matching EU index is required')
    matches = [r for r in idx['text_files'] if r['file_id'] == 38]
    if len(matches) != 1:
        raise ValueError('Expected exactly one album resource')
    resource = matches[0]
    if resource['entries'][1]['raw_sha256'] != TITLE_SHA:
        raise ValueError('Album title source differs')
    lookup, glyphs, bank_sha = bank_index(bank)
    proposed = encode_text('卡片图鉴', lookup)
    source = Disc(image).read_user(resource['lba'], resource['file_size_bytes'])
    rebuilt, changes = rebuild_flat(source, resource, {1: proposed}, len(glyphs))
    if hash_file(image) != EU_IMAGE_SHA:
        raise ValueError('Source image changed during the audit')
    result = {'schema_version': 1, 'status': 'offline_rebuilt_game_integration_pending',
              'source_image_sha256': EU_IMAGE_SHA, 'source_resource_sha256': resource['file_sha256'],
              'font_bank_sha256': bank_sha, 'resource_id': 38, 'entries': len(resource['entries']),
              'source_size_bytes': len(source), 'rebuilt_size_bytes': len(rebuilt),
              'rebuilt_resource_sha256': hashlib.sha256(rebuilt).hexdigest(),
              'translation_example': '卡片图鉴', 'changes': changes,
              'source_entries_and_tails_preserved_except_selected_text': True,
              'relative_pointers_rebuilt_and_aligned': True,
              'game_image_modified': False, 'runtime_verified': False,
              'limits': ['The rebuilt resource is private and has not been written to a disc.',
                         'The original game does not recognize the proposed bank tokens.',
                         'Disc extent, ISO metadata, downstream pointer assumptions and game layout still need validation.']}
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    with (output / 'PRIVATE_rebuilt_resource.bin').open('xb') as f:
        f.write(rebuilt)
    with os.fdopen(os.open(output / 'SHARE_THIS_report.json', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600),
                   'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2); f.write('\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('image', 'private_index', 'bank', 'output'):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    result = run(args.image, args.private_index, args.bank, args.output)
    print(json.dumps({'status': result['status'], 'source_bytes': result['source_size_bytes'],
                      'rebuilt_bytes': result['rebuilt_size_bytes']}))


if __name__ == '__main__':
    main()
