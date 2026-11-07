#!/usr/bin/env python3
"""Update reviewed-glyph coverage using the already validated private index.

This does not scan the image again or imply that additional glyphs are decoded.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path

try:
    from tools.text_codec import CN_C713, CN_IMAGE_SHA256, load_reviewed_glyphs, tokenize
except ModuleNotFoundError:
    from text_codec import CN_C713, CN_IMAGE_SHA256, load_reviewed_glyphs, tokenize


def run(prior: Path, private_index: Path, glyph_review: Path) -> dict:
    report, index = json.loads(prior.read_text()), json.loads(private_index.read_text())
    if report.get('source_image_sha256') != CN_IMAGE_SHA256 or index.get('image_sha256') != CN_IMAGE_SHA256:
        raise ValueError('Prior roundtrip and index are not from the inspected image')
    if report.get('failed_entries') != 0 or report.get('token_lossless_entries') != report.get('indexed_entries'):
        raise ValueError('Prior real-image roundtrip was incomplete')
    if not report.get('file_reconstruction', {}).get('matching_resources') == report.get('resource_count'):
        raise ValueError('Prior file-template reconstruction was incomplete')
    previous_files = {row['file_id']: row for row in report['files']}
    files = index['text_files']
    if len(files) != report['resource_count'] or len(previous_files) != len(files):
        raise ValueError('Private resource list differs from the earlier roundtrip')
    counts, indices, total = Counter(), Counter(), 0
    for f in files:
        old = previous_files.get(f['file_id'])
        if old is None or old['source_file_sha256'] != f['file_sha256'] or old['entry_count'] != len(f['entries']):
            raise ValueError('A private resource does not match the earlier real-image evidence')
        for row in f['entries']:
            raw = bytes.fromhex(row['raw_hex'])
            if len(raw) != row['encoded_bytes'] or hashlib.sha256(raw).hexdigest() != row['raw_sha256']:
                raise ValueError('An indexed text entry changed')
            tokens = tokenize(raw, variant=CN_C713)
            if b''.join(t.raw for t in tokens) != raw:
                raise ValueError('Indexed token roundtrip changed')
            counts.update(t.kind for t in tokens)
            indices.update(t.glyph_index for t in tokens if t.kind == 'cn_glyph')
            total += 1
    if total != report['indexed_entries'] or any(counts[k] != v for k, v in report['token_counts'].items()):
        raise ValueError('Recounted tokens differ from the earlier real-image roundtrip')
    reviewed = load_reviewed_glyphs(glyph_review)
    covered = sum(indices[i] for i in reviewed)
    report['unicode_mapping'].update({'reviewed_chinese_characters': len(reviewed),
                                      'reviewed_chinese_glyph_tokens': covered,
                                      'unresolved_chinese_glyph_tokens': counts['cn_glyph'] - covered,
                                      'coverage_provenance': 'Recounted the hash-checked prior private index using the expanded user-reviewed subset; no new image discovery scan.'})
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('prior_roundtrip', 'private_index', 'glyph_review', 'output'):
        p.add_argument(name, type=Path)
    a = p.parse_args()
    result = run(a.prior_roundtrip, a.private_index, a.glyph_review)
    with os.fdopen(os.open(a.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps(result['unicode_mapping'], ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
