#!/usr/bin/env python3
"""Read-only ZIP image fingerprinting. Never extracts copyrighted game data.

Only one selected ISO/BIN/IMG member is streamed. Format/serial detection is
heuristic; no ISO9660 filesystem, boot, EDC/ECC or gameplay validation occurs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

SERIAL = re.compile(rb"\b(S[CL][A-Z]{2})[_-]([0-9]{3})\.([0-9]{2})\b")
IMAGE_EXTS = {".iso", ".bin", ".img"}
MAX_IMAGE_BYTES = 1500 * 1024 * 1024
SAMPLE_BYTES = 8 * 1024 * 1024


def _hash_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def inspect_zip(zip_path: Path, member: str | None = None,
                max_image_bytes: int = MAX_IMAGE_BYTES) -> dict:
    """Fingerprint selected game-image member, without writing any image bytes."""
    zip_path = Path(zip_path)
    if type(max_image_bytes) is not int or max_image_bytes <= 0:
        raise ValueError("max_image_bytes must be positive")
    with zipfile.ZipFile(zip_path) as archive:
        images = [x for x in archive.infolist()
                  if not x.is_dir() and Path(x.filename).suffix.lower() in IMAGE_EXTS]
        choices = [x for x in images if x.filename == member] if member else images
        if not choices:
            raise ValueError("No ISO/BIN/IMG image found; use --member for a specific member")
        if len(choices) != 1:
            raise ValueError("Multiple game image members; choose one with --member: " +
                             ", ".join(x.filename for x in choices[:12]))
        item = choices[0]
        if item.flag_bits & 1:
            raise ValueError("Password-protected ZIP member is unsupported")
        if not 0 < item.file_size <= max_image_bytes:
            raise ValueError("Member exceeds configured size limit or is empty")
        sha = hashlib.sha256()
        head = bytearray()
        bytes_read = 0
        with archive.open(item, "r") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                bytes_read += len(chunk)
                if bytes_read > max_image_bytes:
                    raise ValueError("Member exceeds size limit during extraction")
                sha.update(chunk)
                if len(head) < SAMPLE_BYTES:
                    head.extend(chunk[:SAMPLE_BYTES - len(head)])
        if bytes_read != item.file_size:
            raise ValueError("Uncompressed member size mismatch")
    candidates = []
    for sector_size, data_offset in ((2048, 0), (2352, 16), (2352, 24)):
        p = 16 * sector_size + data_offset
        if bytes_read % sector_size == 0 and len(head) >= p + 7 and head[p:p + 7] == b"\x01CD001\x01":
            candidates.append({"sector_size": sector_size, "user_data_offset": data_offset})
    serials = sorted({(a + b"-" + b + c).decode("ascii")
                      for a, b, c in SERIAL.findall(head)})
    return {
        "archive_filename": zip_path.name,
        "archive_sha256": _hash_file(zip_path),
        "member_name": item.filename,
        "uncompressed_size_bytes": bytes_read,
        "uncompressed_sha256": sha.hexdigest(),
        "member_zip_crc32": f"{item.CRC:08x}",
        "layout_candidates": candidates,
        "serial_candidates": serials,
        "boot_file_verified": False,
        "patch_compatible": False,
        "note": "ZIP CRC + SHA and layout candidates only. Never assume patch compatibility by serial.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--member", help="Exact ZIP member name when multiple images exist")
    parser.add_argument("--expect", help="Expected SHA-256 of the uncompressed member")
    args = parser.parse_args(argv)
    if args.expect is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", args.expect):
        parser.error("--expect must contain 64 hexadecimal characters")
    try:
        result = inspect_zip(args.archive, member=args.member)
        if args.expect is not None:
            result["expected_member_sha256_matches"] = result["uncompressed_sha256"] == args.expect.lower()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if result.get("expected_member_sha256_matches") is False else 0
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, EOFError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
