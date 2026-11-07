#!/usr/bin/env python3
"""Read-only image fingerprinting. Heuristic detection is NOT patch compatibility."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

SERIAL = re.compile(rb'\b(S[CL][A-Z]{2})[_-]([0-9]{3})\.([0-9]{2})\b')
SAMPLE_BYTES = 8 * 1024 * 1024


def inspect_image(path: Path) -> dict:
    """Hash a local, uncompressed file and report conservative layout candidates."""
    path = Path(path)
    with path.open('rb') as stream:
        before = path.stat()
        magic = stream.read(8)
        if magic.startswith((b'PK\x03\x04', b'7z\xbc\xaf\x27\x1c', b'Rar!', b'MComprHD')):
            raise ValueError('Use an extracted, uncompressed image, not ZIP/7z/RAR/CHD.')
        if before.st_size == 0:
            raise ValueError('The image is empty.')
        stream.seek(0)
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
        layouts = []
        for sector_size, data_offset in ((2048, 0), (2352, 16), (2352, 24)):
            if before.st_size % sector_size:
                continue
            stream.seek(16 * sector_size + data_offset)
            if stream.read(7) == b'\x01CD001\x01':
                layouts.append({'sector_size': sector_size, 'user_data_offset': data_offset})
        stream.seek(0)
        sample = stream.read(SAMPLE_BYTES)
        serials = sorted({(a + b'-' + b + c).decode('ascii') for a, b, c in SERIAL.findall(sample)})
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError('Input changed while it was being inspected; retry on a stable copy.')
    return {
        'filename': path.name,
        'size_bytes': before.st_size,
        'sha256': digest.hexdigest(),
        'layout_candidates': layouts,
        'serial_candidates': serials,
        'serial_scan_bytes': min(before.st_size, SAMPLE_BYTES),
        'boot_file_verified': False,
        'patch_compatible': False,
        'note': 'Descriptor/signature candidates only. No filesystem, EDC/ECC or game execution verification.',
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--expect', help='Expected SHA-256 of the exact input file')
    args = parser.parse_args(argv)
    if args.expect is not None and not re.fullmatch(r'[0-9a-fA-F]{64}', args.expect):
        parser.error('--expect must be exactly 64 hexadecimal characters')
    try:
        report = inspect_image(args.image)
    except (OSError, ValueError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    if args.expect is not None:
        report['expected_sha256_matches'] = report['sha256'] == args.expect.lower()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report.get('expected_sha256_matches') is False else 0


if __name__ == '__main__':
    raise SystemExit(main())
