#!/usr/bin/env python3
"""Hash-locked, same-or-shorter DW2003 text byte writer (not a translator).

Inputs are ORIGINAL user game images and privately reviewed ENCODED text bytes.
Only supports stable original pointers. Requires full source text scan. For
Mode2/2352 it checks existing Form1 EDC/ECC and recalculates edited sectors.
Never modifies source, never overwrites output, and never proves gameplay QA.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

try:
    from tools.scan_text_tables import scan, local_image, DiscError
    from tools.psx_sector import validate_layout, check_form1, regenerate_form1, SectorError
except ModuleNotFoundError:
    from scan_text_tables import scan, local_image, DiscError
    from psx_sector import validate_layout, check_form1, regenerate_form1, SectorError

HASH64 = re.compile(r"[a-f0-9]{64}\Z")
HEXBYTES = re.compile(r"(?:[a-f0-9]{2})+\Z")
CTRL_LEN = (2, 2, 3, 2, 5, 3, 3, 2, 3, 2)
MAX_CHANGES = 128
MAX_TOTAL_BYTES = 32768
MAX_SECTORS = 128


class PatchError(ValueError):
    pass


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def controls(raw: bytes) -> tuple[bytes, ...]:
    """Conservatively parse original EU control markers and their arguments."""
    if type(raw) is not bytes or len(raw) < 2 or raw[-1] != 0:
        raise PatchError("Nonempty NUL-terminated byte string required")
    i, tokens = 0, []
    while i < len(raw) - 1:
        byte = raw[i]
        if byte == 0:
            raise PatchError("Interior NUL byte")
        if byte in (1, 3):
            if i + 1 >= len(raw) - 1:
                raise PatchError("Truncated extended glyph")
            i += 2
        elif byte == 2:
            if i + 1 >= len(raw) - 1 or raw[i + 1] >= len(CTRL_LEN):
                raise PatchError("Unknown or truncated control")
            size = CTRL_LEN[raw[i + 1]]
            if i + size > len(raw) - 1:
                raise PatchError("Truncated control arguments")
            tokens.append(raw[i:i + size])
            i += size
        else:
            i += 1
    return tuple(tokens)


def plan_changes(report: dict, manifest: dict) -> list[dict]:
    if not isinstance(report, dict) or not report.get("inventory_complete"):
        raise PatchError("Actual text inventory incomplete; refusing to patch")
    if report.get("schema_version") != 1 or report.get("sector_bytes") not in (2048, 2352):
        raise PatchError("Unsupported scan metadata")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise PatchError("Unsupported patch manifest")
    expected = manifest.get("source_image_sha256")
    if not isinstance(expected, str) or not HASH64.fullmatch(expected):
        raise PatchError("Manifest needs exact lowercase source_image_sha256")
    if expected != report.get("image_sha256"):
        raise PatchError("Source image hash does not match")
    changes = manifest.get("changes")
    if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_CHANGES:
        raise PatchError("Invalid change count")
    index = {}
    for file in report.get("text_files", []):
        for row in file.get("entries", []):
            key = (file["file_id"], row.get("table"), row["index"])
            if key in index:
                raise PatchError("Duplicate source address")
            index[key] = (file, row)
    output, ranges, seen, total = [], [], set(), 0
    for change in changes:
        if not isinstance(change, dict):
            raise PatchError("Invalid change")
        fid, table, idx = change.get("file_id"), change.get("table"), change.get("index")
        if type(fid) is not int or type(idx) is not int or (table is not None and type(table) is not int):
            raise PatchError("Invalid text address")
        key = (fid, table, idx)
        if key in seen:
            raise PatchError("Duplicate patch address")
        seen.add(key)
        if idx == 0:
            raise PatchError("Cannot replace empty table entry zero")
        if key not in index:
            raise PatchError("Text address not in scanned source")
        file, row = index[key]
        raw_hex = row.get("raw_hex")
        if not isinstance(raw_hex, str):
            raise PatchError("Missing PRIVATE raw_hex in scan")
        try:
            old = bytes.fromhex(raw_hex)
        except ValueError as exc:
            raise PatchError("Bad source raw_hex") from exc
        if hash_bytes(old) != row.get("raw_sha256") or len(old) != row.get("encoded_bytes"):
            raise PatchError("Source text hash/length inconsistent")
        if row["raw_sha256"] != change.get("original_sha256"):
            raise PatchError("Original text SHA-256 mismatch")
        new_hex = change.get("replacement_hex")
        if not isinstance(new_hex, str) or not HEXBYTES.fullmatch(new_hex):
            raise PatchError("Replacement must be lowercase hexadecimal bytes")
        new = bytes.fromhex(new_hex)
        if len(new) > len(old):
            raise PatchError("Translation exceeds original byte capacity")
        if not new or new[-1] != 0 or 0 in new[:-1]:
            raise PatchError("Replacement must have exactly one final NUL")
        if controls(old) != controls(new):
            raise PatchError("Control sequence changed; manual engineering required")
        padded = new + b"\x00" * (len(old) - len(new))
        if padded == old:
            raise PatchError("No-op text replacement")
        offset, lba, size = row.get("offset_bytes"), file.get("lba"), file.get("file_size_bytes")
        if (type(offset) is not int or type(lba) is not int or type(size) is not int or
                offset < 0 or lba < 0 or offset + len(old) > size):
            raise PatchError("Missing or invalid physical text address")
        absolute = lba * 2048 + offset
        ranges.append((absolute, absolute + len(old)))
        total += len(old)
        output.append({"key": key, "lba": lba, "offset": offset,
                       "old": old, "new": new, "padded": padded})
    if total > MAX_TOTAL_BYTES:
        raise PatchError("Stage exceeds text modification budget")
    ranges.sort()
    if any(a[1] > b[0] for a, b in zip(ranges, ranges[1:])):
        raise PatchError("Overlapping physical replacements")
    return output


def edits_by_sector(plan: list[dict]) -> dict[int, list[tuple[int, int, int]]]:
    sectors = {}
    for row in plan:
        for i, (before, after) in enumerate(zip(row["old"], row["padded"])):
            logical = row["offset"] + i
            sector = row["lba"] + logical // 2048
            sectors.setdefault(sector, []).append((logical % 2048, before, after))
    if len(sectors) > MAX_SECTORS:
        raise PatchError("Too many changed sectors for one stage")
    for lba, edits in sectors.items():
        seen = set()
        for offset, _, _ in edits:
            if offset in seen:
                raise PatchError(f"Two changes target sector {lba} byte {offset}")
            seen.add(offset)
    return sectors


def rewrite_sectors(writer, source_path: Path, sectors: dict, physical_size: int):
    data_start = 24 if physical_size == 2352 else 0
    with source_path.open("rb") as source:
        for lba, operations in sorted(sectors.items()):
            pos = lba * physical_size
            source.seek(pos)
            old = source.read(physical_size)
            if len(old) != physical_size:
                raise PatchError(f"Sector {lba} out of bounds")
            if physical_size == 2352:
                try:
                    validate_layout(old)
                    if not check_form1(old):
                        raise PatchError(f"Sector {lba} has invalid existing EDC/ECC")
                except SectorError as exc:
                    raise PatchError(f"Sector {lba}: {exc}") from exc
            new = bytearray(old)
            for offset, before, after in operations:
                if old[data_start + offset] != before:
                    raise PatchError(f"Sector {lba}: original byte mismatch at {offset}")
                new[data_start + offset] = after
            if physical_size == 2352:
                new = bytearray(regenerate_form1(bytes(new)))
                if not check_form1(new):
                    raise PatchError(f"Sector {lba}: parity verification failed")
            writer.seek(pos)
            writer.write(new)


def assert_entry_equivalence(before: dict, after: dict, plan: list[dict]):
    def records(scan_result):
        return {
            (f["file_id"], r.get("table"), r["index"]): bytes.fromhex(r["raw_hex"])
            for f in scan_result["text_files"] for r in f["entries"]
        }
    old, new = records(before), records(after)
    if old.keys() != new.keys():
        raise PatchError("Text address inventory changed after writing")
    expected = {row["key"]: row["new"] for row in plan}
    for key, original in old.items():
        if new[key] != expected.get(key, original):
            raise PatchError(f"Rescanned text mismatch at {key}")


def apply(source_image: Path, manifest: dict, dest: Path) -> dict:
    """Return a verified local image; full in-game testing remains separate."""
    source_image, dest = Path(source_image), Path(dest)
    if source_image.resolve() == dest.resolve():
        raise PatchError("Cannot overwrite the original image")
    if dest.exists():
        raise PatchError("Output already exists; refusing overwrite")
    with local_image(source_image) as source:
        before = scan(source, private=True)
        plan = plan_changes(before, manifest)
        sector_ops = edits_by_sector(plan)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w+b", suffix=".img",
                                             prefix=".dw3-work-", dir=dest.parent,
                                             delete=False) as fp:
                tmp = Path(fp.name)
                with Path(source).open("rb") as inp:
                    shutil.copyfileobj(inp, fp, 1024 * 1024)
                fp.flush()
                rewrite_sectors(fp, Path(source), sector_ops, before["sector_bytes"])
                fp.flush()
                os.fsync(fp.fileno())
            after = scan(tmp, private=True)
            if not after.get("inventory_complete"):
                raise PatchError("Full edited-image text rescan failed")
            if after["image_size_bytes"] != before["image_size_bytes"]:
                raise PatchError("Edited image size changed")
            assert_entry_equivalence(before, after, plan)
            if dest.exists():
                raise PatchError("Output appeared during generation")
            os.replace(tmp, dest)
            tmp = None
            return {
                "status": "generated_not_gameplay_verified",
                "source_sha256": before["image_sha256"],
                "target_sha256": after["image_sha256"],
                "updated_entries": len(plan), "touched_sectors": len(sector_ops),
                "edc_ecc_regenerated": before["sector_bytes"] == 2352,
                "gameplay_verified": False,
                "warning": "Requires real Chinese font/codec validation and actual game testing",
            }
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path)
    ap.add_argument("manifest", type=Path)
    ap.add_argument("output", type=Path)
    args = ap.parse_args(argv)
    try:
        data = json.loads(args.manifest.read_text(encoding="utf-8"))
        result = apply(args.source, data, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError, EOFError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
