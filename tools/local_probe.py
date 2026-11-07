#!/usr/bin/env python3
"""One-command, local-only baseline probe for a user-owned DW2003 Chinese ZIP.

Creates two artifacts in a Git-ignored directory:
  SHARE_THIS_report.json  -- no original game text or glyph bytes
  PRIVATE_DO_NOT_UPLOAD.json -- original text bytes, for subsequent local work

No game bytes are sent to a network service. The original archive is read-only.
A partial public report is written even when the strict text scan fails.
This cannot prove translation coverage or game-renderer correctness.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sys
import zipfile

try:
    from tools.inspect_zip import inspect_zip
    from tools.verify_base import inspect_image
    from tools.scan_text_tables import scan
except ModuleNotFoundError:
    from inspect_zip import inspect_zip
    from verify_base import inspect_image
    from scan_text_tables import scan


REPORT_FILENAME = "SHARE_THIS_report.json"
PRIVATE_FILENAME = "PRIVATE_DO_NOT_UPLOAD.json"


def _write_exclusive(path: Path, obj: dict):
    """Create a new local JSON file with private permissions; never overwrite."""
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def public_summary(fingerprint: dict, actual_scan: dict) -> dict:
    """Restrict report to structure metadata, IDs, counts and checksums."""
    hash_from_archive = fingerprint.get("uncompressed_sha256", fingerprint.get("sha256"))
    if hash_from_archive != actual_scan.get("image_sha256"):
        raise ValueError("Scan image SHA-256 differs from independently fingerprinted image")
    groups = []
    for item in actual_scan.get("text_files", []):
        groups.append({
            "file_id": item["file_id"],
            "file_name": str(item.get("path", "")).split("/")[-1],
            "table_count": item.get("table_count"),
            "entry_count": item.get("entry_count"),
            "file_size_bytes": item.get("file_size_bytes"),
            "file_sha256": item.get("file_sha256"),
        })
    groups.sort(key=lambda row: row["file_id"])
    return {
        "source": {
            "member_name": fingerprint.get("member_name"),
            "archive_sha256": fingerprint.get("archive_sha256"),
            "image_sha256": hash_from_archive,
            "image_size_bytes": fingerprint.get("uncompressed_size_bytes", fingerprint.get("size_bytes")),
            "layout_candidates": fingerprint.get("layout_candidates"),
            "serial_candidates": fingerprint.get("serial_candidates"),
        },
        "structure": {
            "sector_bytes": actual_scan.get("sector_bytes"),
            "user_data_offset": actual_scan.get("user_data_offset"),
            "reference_exe": actual_scan.get("boot"),
            "reference_inventory_complete": bool(actual_scan.get("inventory_complete")),
            "identified_text_files": actual_scan.get("total_text_files"),
            "indexed_entries": actual_scan.get("total_entries"),
            "issues": actual_scan.get("issues", []),
            "files": groups,
        },
        "limitations": [
            "This is a reference-format structural scan, not a gameplay test.",
            "No Chinese glyph mapping, translation accuracy or visible text was verified.",
            "No original game text or raw byte sequences are included in this report.",
            "Do NOT upload the PRIVATE_DO_NOT_UPLOAD.json file or the ISO to a public GitHub repository.",
        ],
    }


def _fingerprint(input_path: Path, member: str | None = None) -> dict:
    if zipfile.is_zipfile(input_path):
        return inspect_zip(input_path, member=member)
    if member is not None:
        raise ValueError("--member is valid only for ZIP inputs")
    return inspect_image(input_path)


def run_probe(input_path: Path, output_root: Path, member: str | None = None) -> tuple[int, Path]:
    """Generate shareable and private reports; return (exit_code, shareable_path).

    All game text is kept in the private file. No data is uploaded anywhere.
    """
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = output_root / f"{stamp}-{secrets.token_hex(3)}"
    folder.mkdir(mode=0o700)
    public_path = folder / REPORT_FILENAME
    private_path = folder / PRIVATE_FILENAME
    result = {
        "schema_version": 1,
        "probe_version": "baseline-local-v1",
        "status": "blocked",
        "name": input_path.name,
        "source": None,
        "structure": None,
        "error": None,
        "private_dump_saved_locally": False,
        "gameplay_verified": False,
    }
    try:
        try:
            fingerprint = _fingerprint(input_path, member=member)
            result["source"] = {
                "archive_sha256": fingerprint.get("archive_sha256"),
                "member_name": fingerprint.get("member_name"),
                "image_sha256": fingerprint.get("uncompressed_sha256", fingerprint.get("sha256")),
                "image_size_bytes": fingerprint.get("uncompressed_size_bytes", fingerprint.get("size_bytes")),
                "layout_candidates": fingerprint.get("layout_candidates"),
                "serial_candidates": fingerprint.get("serial_candidates"),
            }
        except (OSError, ValueError, RuntimeError, EOFError, zipfile.BadZipFile) as exc:
            result["error"] = {"phase": "fingerprint", "type": type(exc).__name__,
                               "message": str(exc).replace(str(input_path), "<input>")[:900]}
            return 2, public_path

        try:
            actual = scan(input_path, member=member, private=True)
            summary = public_summary(fingerprint, actual)
            result["source"] = summary["source"]
            result["structure"] = summary["structure"]
            result["limitations"] = summary["limitations"]
            _write_exclusive(private_path, actual)
            result["private_dump_saved_locally"] = True
            result["status"] = (
                "structure_passed_not_gameplay_verified"
                if actual.get("inventory_complete") else "structure_incomplete"
            )
            return (0 if actual.get("inventory_complete") else 1), public_path
        except (OSError, ValueError, RuntimeError, EOFError, zipfile.BadZipFile) as exc:
            result["error"] = {"phase": "text_scan", "type": type(exc).__name__,
                               "message": str(exc).replace(str(input_path), "<input>")[:900]}
            return 2, public_path
    finally:
        _write_exclusive(public_path, result)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="Your own Chinese DW2003 ZIP, BIN, IMG or ISO")
    parser.add_argument("--member", help="Select an exact image member from a multi-image ZIP")
    parser.add_argument("--output-root", type=Path, default=Path("local/dw3-probe"),
                        help="Local Git-ignored output folder (default: local/dw3-probe)")
    args = parser.parse_args(argv)
    try:
        status, report = run_probe(args.image, args.output_root, args.member)
        print(f"\nSHARE THIS SMALL REPORT ONLY: {report}\n")
        print(f"Status code: {status}")
        print("All extracted entry bytes (if generated) remain in the sibling")
        print(f"{PRIVATE_FILENAME} file. Keep that file local; do not upload it.")
        print("This is not an in-game test, and your original image was not modified.")
        return status
    except (OSError, ValueError) as exc:
        print(f"Could not create local report directory: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
