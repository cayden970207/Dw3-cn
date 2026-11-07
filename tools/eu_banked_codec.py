#!/usr/bin/env python3
"""Offline-only prototype of a three-byte banked Chinese text encoding.

This is a proposed NEW encoding, not a claim about original game data. The
EU executable cannot currently parse or draw it. No disc writes are offered.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path

from tools.eu_font_prototype import EU_IMAGE_SHA, TITLE_SHA
from tools.roundtrip_cn import inspect_resource
from tools.scan_text_tables import Disc, hash_file
from tools.text_codec import CONTROL_LENGTHS, REFERENCE, CodecError, Token, tokenize

MARKER = 0xEA
MAX_GLYPHS = 255 * 255


def encode_index(index: int, count: int) -> bytes:
    if type(index) is not int or type(count) is not int or not 0 <= index < count <= MAX_GLYPHS:
        raise CodecError('Glyph index or bank size is out of range')
    return bytes((MARKER, index // 255 + 1, index % 255 + 1))


def tokenize_proposed(data: bytes, count: int) -> list[Token]:
    if type(count) is not int or not 1 <= count <= MAX_GLYPHS:
        raise CodecError('Invalid generated bank size')
    if not isinstance(data, bytes) or not data or data[-1] != 0 or 0 in data[:-1]:
        raise CodecError('A single final NUL terminator is required')
    pos, end, tokens = 0, len(data) - 1, []
    while pos < end:
        first = data[pos]
        if first == MARKER:
            if pos + 2 >= end:
                raise CodecError('Truncated proposed glyph token')
            high, low = data[pos + 1], data[pos + 2]
            index = (high - 1) * 255 + low - 1
            if not high or not low or index >= count:
                raise CodecError('Proposed glyph index is outside the bank')
            tokens.append(Token('bank_glyph', pos, data[pos:pos+3], glyph_index=index)); pos += 3
        elif first == 1:
            if pos + 1 >= end:
                raise CodecError('Truncated extended glyph')
            raw = data[pos:pos+2]
            kind = 'extended_glyph' if 1 <= raw[1] <= 0x72 else 'unknown_extended_glyph'
            tokens.append(Token(kind, pos, raw, glyph_index=raw[1])); pos += 2
        elif first == 2:
            if pos + 1 >= len(data) or data[pos+1] >= len(CONTROL_LENGTHS):
                raise CodecError('Unknown or truncated control')
            control = data[pos+1]
            size = CONTROL_LENGTHS[control]
            if pos + size > end:
                raise CodecError('Truncated control arguments')
            tokens.append(Token('control', pos, data[pos:pos+size], control_id=control)); pos += size
        elif first == 3 or first >= 0xEA:
            tokens.append(Token('unknown_fallback', pos, data[pos:pos+1])); pos += 1
        else:
            tokens.append(Token('base_glyph', pos, data[pos:pos+1], glyph_index=first)); pos += 1
    tokens.append(Token('terminator', end, b'\0'))
    if b''.join(t.raw for t in tokens) != data:
        raise CodecError('Proposed token roundtrip changed bytes')
    return tokens


def bank_index(directory: Path) -> tuple[dict[str, int], list[str], str]:
    data = json.loads((directory / 'PRIVATE_glyph_index.json').read_text())
    report = json.loads((directory / 'SHARE_THIS_font_generation_report.json').read_text())
    binary = directory / 'PRIVATE_native12_4bpp.bin'
    if (report.get('status') != 'generated_not_integrated_or_gameplay_verified' or
            hash_file(binary) != data.get('native_4bpp_sha256') or data.get('failures')):
        raise ValueError('Font bank has unresolved entries or an incorrect hash')
    values = data['glyphs']
    if len(values) != data['glyph_count'] or len(values) > MAX_GLYPHS:
        raise ValueError('Font-bank size is invalid')
    codepoints = []
    for i, row in enumerate(values):
        cp = int(row['codepoint'][2:], 16)
        if (row['index'],row['bank'],row['slot'],row['status']) != \
                (i,i//256,i%256,'generated_not_gameplay_verified'):
            raise ValueError('Font-bank mapping differs')
        codepoints.append(chr(cp))
    if len(set(codepoints)) != len(codepoints) or codepoints != sorted(codepoints):
        raise ValueError('Duplicate or unsorted font-bank mappings')
    return {ch:i for i,ch in enumerate(codepoints)}, codepoints, data['native_4bpp_sha256']


def encode_text(text: str, lookup: dict[str,int]) -> bytes:
    try:
        data = b''.join(encode_index(lookup[ch],len(lookup)) for ch in text) + b'\0'
    except KeyError as exc:
        raise CodecError(f'Unicode character absent from the generated bank: U+{ord(exc.args[0]):04X}') from exc
    result = tokenize_proposed(data,len(lookup))
    if len(result) != len(text) + 1 or any(t.kind != 'bank_glyph' for t in result[:-1]):
        raise CodecError('Encoded text failed the proposed token roundtrip')
    return data


def inspect(image: Path, private_index: Path, bank: Path) -> dict:
    root = Path(__file__).resolve().parents[1]
    local = (root / 'local').resolve()
    if any(not p.resolve().is_relative_to(local) for p in (image,private_index,bank)):
        raise ValueError('Image, text index and font bank must remain under local/')
    if hash_file(image) != EU_IMAGE_SHA:
        raise ValueError('EU original image differs')
    idx = json.loads(private_index.read_text())
    if idx.get('image_sha256') != EU_IMAGE_SHA or idx.get('inventory_complete') is not True or len(idx['text_files']) != 50:
        raise ValueError('Complete, matching private EU index required')
    lookup, codepoints, bank_sha = bank_index(bank)
    disc, counts, marker_locations = Disc(image), Counter(), []
    for f in idx['text_files']:
        data = disc.read_user(f['lba'],f['file_size_bytes'])
        verified = inspect_resource(data,f,variant=REFERENCE)
        if verified['errors'] or not verified['file_template_equal']:
            raise ValueError('EU resource failed indexed roundtrip')
        for row in f['entries']:
            raw = bytes.fromhex(row['raw_hex'])
            standard = tokenize(raw,variant=REFERENCE)
            if any(t.raw == bytes((MARKER,)) and t.kind == 'unknown_fallback' for t in standard):
                marker_locations.append((f['file_id'],row['table'],row['index']))
                continue
            proposed = tokenize_proposed(raw,len(codepoints))
            if [(t.kind,t.raw,t.control_id) for t in standard] != [(t.kind,t.raw,t.control_id) for t in proposed]:
                raise ValueError('Proposed parser changed an EU token boundary')
            counts.update(t.kind for t in proposed)
            counts['entries_roundtripped'] += 1
    if marker_locations:
        raise ValueError('Proposed marker collides with indexed EU fallback tokens')
    if counts['entries_roundtripped'] != 13084:
        raise ValueError('Not all expected EU entries roundtripped')
    # Check the complete generated index in both directions, not just samples.
    for i,ch in enumerate(codepoints):
        token = tokenize_proposed(encode_index(i,len(codepoints)) + b'\0',len(codepoints))[0]
        if token.glyph_index != i or lookup[ch] != i:
            raise ValueError('A generated Unicode identity failed encoding roundtrip')
    sample = encode_text('卡片图鉴',lookup)
    f = next(x for x in idx['text_files'] if x['file_id'] == 38)
    title = next(x for x in f['entries'] if x['table'] is None and x['index'] == 1)
    if title['raw_sha256'] != TITLE_SHA:
        raise ValueError('Album title source differs')
    return {'schema_version':1, 'status':'offline_codec_roundtrip_passed_game_integration_pending',
            'source_image_sha256':EU_IMAGE_SHA, 'font_bank_sha256':bank_sha,
            'proposal':{'stored_non_sjis_text_only':True,'marker_hex':f'{MARKER:02x}',
                        'glyph_token_bytes':3,'maximum_glyphs':MAX_GLYPHS,
                        'generated_glyphs':len(codepoints), 'runtime_decoder_modified':False,
                        'runtime_renderer_modified':False,'general_writer_enabled':False},
            'real_eu_resources':{'resources':50,'entries_roundtripped':counts['entries_roundtripped'],
                                 'control_tokens_preserved':counts['control'],
                                 'proposed_marker_collisions_at_token_boundaries':len(marker_locations)},
            'font_bank_mapping_roundtrip_count':len(codepoints),
            'album_title_example':{'file_id':38,'entry_index':1,'translation':'卡片图鉴',
                                   'source_capacity_bytes':title['encoded_bytes'],
                                   'proposed_bytes_including_terminator':len(sample),
                                   'fits_existing_slot':len(sample)<=title['encoded_bytes'],
                                   'written_to_game':False},
            'limits':['No game-side code recognizes this proposal yet.',
                      'The real-corpus check covers 50 indexed English resources, not dynamic or other-language text.',
                      'Three-byte glyphs can exceed fixed source slots; resource rebuild/relocation requires separate safety validation.',
                      'SJIS paths, width measurement, controls during gameplay, VRAM cache and save/load remain unverified.']}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('image','private_index','bank','report'):
        p.add_argument(name,type=Path)
    a=p.parse_args()
    result=inspect(a.image,a.private_index,a.bank)
    with os.fdopen(os.open(a.report,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w',encoding='utf-8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({'status':result['status'],'entries':result['real_eu_resources']['entries_roundtripped'],
                      'glyphs':result['font_bank_mapping_roundtrip_count']}))


if __name__ == '__main__':
    main()
