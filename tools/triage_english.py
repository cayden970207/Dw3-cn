#!/usr/bin/env python3
"""Conservative candidate queue for possible English leftovers in a PRIVATE
DW2003 text scan. Not a Chinese decoder or proof of untranslated strings.

The vanilla EU one-byte font codes encode numbers at 04..0D, A-Z at 0E..27,
a-z at 28..41, and spaces as 01 01. The custom Chinese font may use
overlapping codes, so ALL results need human review.

Input comes from tools/scan_text_tables.py --private-dump, never from the
public metadata report. Only redacted counts go to stdout by default.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

# Total control-byte length including the 02 prefix and code.
# DW2003 base game message_code_lengths, with invalid 1 clamped to 2.
CTRL_LEN = (2, 2, 3, 2, 5, 3, 3, 2, 3, 2)
HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class TriageError(ValueError):
    pass


def roman_runs(raw: bytes, min_letters=7) -> list[str]:
    """Extract *candidates*, ignoring encoded controls and extended glyphs.

    It intentionally stops at unexpected bytes, so unknown Chinese encodings
    cannot silently turn into trusted decoded English.
    """
    if type(min_letters) is not int or not 2 <= min_letters <= 100:
        raise TriageError("min_letters must be an integer between 2 and 100")
    if not raw or raw[-1] != 0:
        raise TriageError("String must be terminated by NUL")
    result, run = [], []

    def flush():
        if run:
            value = "".join(run).strip()
            if sum(c.isalpha() for c in value) >= min_letters:
                result.append(value)
            run.clear()

    i = 0
    while i < len(raw) - 1:
        c = raw[i]
        if c == 0:
            raise TriageError("Unexpected interior NUL")
        if c == 1:
            if i + 1 >= len(raw) - 1:
                raise TriageError("Truncated extended glyph")
            if raw[i + 1] == 1 and run:
                run.append(" ")
            else:
                flush()
            i += 2
        elif c == 2:
            if i + 1 >= len(raw) - 1 or raw[i + 1] >= len(CTRL_LEN):
                raise TriageError("Unknown or truncated control code")
            length = CTRL_LEN[raw[i + 1]]
            if i + length > len(raw) - 1:
                raise TriageError("Truncated control arguments")
            flush()
            i += length
        elif 4 <= c <= 13:
            run.append(chr(ord("0") + c - 4))
            i += 1
        elif 14 <= c <= 39:
            run.append(chr(ord("A") + c - 14))
            i += 1
        elif 40 <= c <= 65:
            run.append(chr(ord("a") + c - 40))
            i += 1
        else:
            flush()
            i += 1
    flush()
    return result


def triage(data: dict, min_letters=7, previews=False) -> dict:
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise TriageError("Expected scan schema_version 1")
    image_hash = data.get("image_sha256")
    if not isinstance(image_hash, str) or not HEX_SHA256.fullmatch(image_hash):
        raise TriageError("Input must include the exact image SHA-256")
    files = data.get("text_files")
    if not isinstance(files, list):
        raise TriageError("text_files must be an array")
    candidates, error_entries, total = [], [], 0
    seen = set()
    for file in files:
        if not isinstance(file, dict) or type(file.get("file_id")) is not int:
            raise TriageError("Invalid text_file record")
        fid = file["file_id"]
        records = file.get("entries")
        if not isinstance(records, list):
            raise TriageError(f"File {fid:#x} has no entry records")
        for row in records:
            total += 1
            if not isinstance(row, dict) or type(row.get("index")) is not int:
                raise TriageError(f"Invalid entry in file {fid:#x}")
            key = (fid, row.get("table"), row["index"])
            if key in seen:
                raise TriageError(f"Duplicate entry {key}")
            seen.add(key)
            value = row.get("raw_hex")
            if not isinstance(value, str) or not value:
                raise TriageError("Input needs PRIVATE raw_hex dump; run scan_text_tables.py --private-dump")
            try:
                raw = bytes.fromhex(value)
            except ValueError as exc:
                raise TriageError(f"Bad hex for entry {key}") from exc
            if row.get("encoded_bytes") != len(raw):
                raise TriageError(f"Byte length mismatch for entry {key}")
            if row.get("raw_sha256") != hashlib.sha256(raw).hexdigest():
                raise TriageError(f"Raw entry hash mismatch for entry {key}")
            try:
                runs = roman_runs(raw, min_letters=min_letters)
            except TriageError as exc:
                error_entries.append({"file_id": fid, "table": row.get("table"),
                                      "index": row["index"], "reason": str(exc)})
                continue
            if runs:
                candidate = {
                    "file_id": fid, "path": file.get("path", ""),
                    "table": row.get("table"), "index": row["index"],
                    "raw_sha256": row["raw_sha256"],
                    "english_like_runs": len(runs),
                    "status": "needs_human_review",
                }
                if previews:
                    candidate["preview"] = runs
                candidates.append(candidate)
    by_path = {}
    for row in candidates:
        name = row["path"]
        by_path[name] = by_path.get(name, 0) + 1
    return {
        "schema_version": 1,
        "image_sha256": image_hash,
        "heuristic": "vanilla-EU-roman-glyph-streak; not validated for Chinese font",
        "min_letters": min_letters,
        "entries_scanned": total,
        "candidate_count": len(candidates),
        "decode_error_count": len(error_entries),
        "by_file": dict(sorted(by_path.items())),
        "candidates": candidates,
        "decode_errors": error_entries,
        "all_candidates_need_review": True,
        "gameplay_verified": False,
    }


def write_private(path: Path, data: dict):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.write("\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("private_dump", type=Path, help="private JSON with raw_hex entries")
    parser.add_argument("--queue", type=Path,
                        help="Write candidate keys and possible English previews locally (mode 0600)")
    parser.add_argument("--min-letters", type=int, default=7)
    args = parser.parse_args(argv)
    try:
        source = json.loads(args.private_dump.read_text(encoding="utf-8"))
        full = triage(source, min_letters=args.min_letters, previews=bool(args.queue))
        if args.queue:
            write_private(args.queue, full)
        # No copyrighted previews printed to stdout.
        redacted = {k: v for k, v in full.items() if k not in ("candidates", "decode_errors")}
        print(json.dumps(redacted, ensure_ascii=False, indent=2))
        return 0 if not full["decode_error_count"] else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
