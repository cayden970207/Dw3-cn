#!/usr/bin/env python3
"""Audit indexed real-image text without writing image or original text bytes.

This reuses the prior private index; it is not another initial discovery scan.
The public output contains counts and checksums only.  File reconstruction
starts with each complete original resource as a template, then replaces only
indexed string ranges with re-encoded tokens, preserving all other bytes.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import struct

try:
    from tools.scan_text_tables import Disc, hash_file, local_image
    from tools.text_codec import CN_C713, CN_IMAGE_SHA256, CodecError, load_reviewed_glyphs, tokenize
except ModuleNotFoundError:
    from scan_text_tables import Disc, hash_file, local_image
    from text_codec import CN_C713, CN_IMAGE_SHA256, CodecError, load_reviewed_glyphs, tokenize


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def word(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError("Table word outside resource")
    return struct.unpack_from("<I", data, offset)[0]


def table_bounds(data: bytes, container: bool) -> dict[int | None, tuple[int, int]]:
    if not container:
        return {None: (0, len(data))}
    header_size = word(data, 0)
    if not 4 <= header_size <= min(len(data), 4096) or header_size % 4:
        raise ValueError("Invalid container header")
    offsets = [word(data, i) for i in range(0, header_size, 4)]
    used = [x for x in offsets if x]
    if not used or used[0] != header_size or any(x < header_size or x >= len(data) for x in used):
        raise ValueError("Invalid subtable extent")
    if any(a >= b for a, b in zip(used, used[1:])):
        raise ValueError("Subtable offsets overlap or are not increasing")
    bounds = {}
    for n, offset in enumerate(offsets):
        if offset:
            following = next((x for x in used if x > offset), len(data))
            bounds[n] = (offset, following)
    return bounds


def inspect_resource(data: bytes, resource: dict, *, variant: str = CN_C713) -> dict:
    if sha(data) != resource["file_sha256"] or len(data) != resource["file_size_bytes"]:
        raise ValueError("Resource hash or size differs from prior index")
    groups = defaultdict(list)
    for entry in resource["entries"]:
        groups[entry.get("table")].append(entry)
    bounds = table_bounds(data, bool(resource["container"]))
    if set(groups) != set(bounds) or len(bounds) != resource["table_count"]:
        raise ValueError("Indexed tables differ from current resource")
    counts = Counter()
    glyph_indices = set()
    glyph_frequencies = Counter()
    errors = []
    unknown_instances = []
    reconstructed = bytearray(data)
    nonzero_gap_entries = 0
    nonzero_gap_bytes = 0
    for table, (base, table_end) in bounds.items():
        count = word(data, base)
        if not 0 < count <= 4999 or base + 4 + count * 4 > table_end:
            raise ValueError("Invalid text table count")
        rows = sorted(groups[table], key=lambda row: row["index"])
        if len(rows) != count or [r["index"] for r in rows] != list(range(count)):
            raise ValueError("Text table entries differ from prior index")
        pointers = [base + word(data, base + 4 + i * 4) for i in range(count)]
        if pointers[0] != base + 4 + count * 4 or any(p >= table_end for p in pointers):
            raise ValueError("Text pointers outside table")
        if any(a >= b for a, b in zip(pointers, pointers[1:])):
            raise ValueError("Text pointers are not strictly increasing")
        for i, row in enumerate(rows):
            try:
                raw = bytes.fromhex(row["raw_hex"])
                begin = pointers[i]
                limit = pointers[i + 1] if i + 1 < count else table_end
                if row["offset_bytes"] != begin or begin + len(raw) > limit:
                    raise ValueError("Indexed entry differs from pointer bounds")
                if len(raw) != row["encoded_bytes"] or sha(raw) != row["raw_sha256"]:
                    raise ValueError("Indexed entry hash or length differs")
                if data[begin:begin + len(raw)] != raw:
                    raise ValueError("Indexed entry bytes differ from image")
                parts = tokenize(raw, variant=variant)
                reencoded = b"".join(token.raw for token in parts)
                if reencoded != raw:
                    raise ValueError("Token roundtrip mismatch")
                reconstructed[begin:begin + len(raw)] = reencoded
                counts.update(t.kind for t in parts)
                glyph_indices.update(t.glyph_index for t in parts if t.kind == "cn_glyph")
                glyph_frequencies.update(t.glyph_index for t in parts if t.kind == "cn_glyph")
                unknown_instances.extend({"table": table, "index": row["index"],
                                          "token_offset": token.offset, "kind": token.kind}
                                         for token in parts if token.kind.startswith("unknown_"))
                gap = data[begin + len(raw):limit]
                nonzero = sum(1 for value in gap if value)
                nonzero_gap_entries += bool(nonzero)
                nonzero_gap_bytes += nonzero
                counts["entries_passed"] += 1
                if len(raw) == 1:
                    counts["empty_entries"] += 1
            except (KeyError, ValueError, CodecError) as exc:
                errors.append({"table": table, "index": row.get("index"), "reason": str(exc)})
    reconstructed_sha = sha(bytes(reconstructed))
    return {"file_id": resource["file_id"], "entry_count": len(resource["entries"]),
            "counts": dict(counts), "cn_glyph_indices": glyph_indices,
            "cn_glyph_frequencies": glyph_frequencies, "errors": errors,
            "unknown_instances": unknown_instances,
            "source_file_sha256": resource["file_sha256"],
            "reconstructed_file_sha256": reconstructed_sha,
            "file_template_equal": reconstructed_sha == resource["file_sha256"],
            "entries_with_nonzero_slot_tail": nonzero_gap_entries,
            "nonzero_slot_tail_bytes": nonzero_gap_bytes}


def run(image: Path, private_index: Path, review_path: Path) -> dict:
    index = json.loads(private_index.read_text(encoding="utf-8"))
    if index.get("image_sha256") != CN_IMAGE_SHA256 or not index.get("inventory_complete"):
        raise ValueError("Private index is not the complete, inspected c713 image index")
    files = index.get("text_files")
    if not isinstance(files, list) or len(files) != 50:
        raise ValueError("Expected the prior 50-resource English-slot index")
    totals = Counter()
    glyphs = set()
    glyph_frequencies = Counter()
    reviewed = load_reviewed_glyphs(review_path)
    per_file = []
    with local_image(image) as unpacked:
        if hash_file(unpacked) != CN_IMAGE_SHA256:
            raise ValueError("Input image hash differs from the inspected baseline")
        disc = Disc(unpacked)
        if (disc.sector_bytes, disc.user_offset) != (2352, 24):
            raise ValueError("Input image sector layout differs from baseline")
        for resource in files:
            data = disc.read_user(resource["lba"], resource["file_size_bytes"])
            row = inspect_resource(data, resource)
            glyphs.update(row.pop("cn_glyph_indices"))
            glyph_frequencies.update(row.pop("cn_glyph_frequencies"))
            totals.update(row["counts"])
            per_file.append(row)
    expected = sum(file["entry_count"] for file in files)
    errors = sum(len(file["errors"]) for file in per_file)
    file_equal = sum(file["file_template_equal"] for file in per_file)
    passed = totals["entries_passed"]
    return {
        "schema_version": 1, "source_image_sha256": CN_IMAGE_SHA256,
        "status": "token_lossless_glyph_and_runtime_unresolved" if passed == expected and not errors and file_equal == len(files) else "roundtrip_incomplete",
        "scope": "Prior English-slot private index, validated against the current exact image",
        "resource_count": len(files), "indexed_entries": expected,
        "token_lossless_entries": passed, "failed_entries": errors,
        "empty_entries": totals["empty_entries"],
        "token_counts": {k: v for k, v in totals.items() if k not in ("entries_passed", "empty_entries")},
        "distinct_chinese_glyph_indices_observed": len(glyphs),
        "unicode_mapping": {"reviewed_chinese_characters": len(reviewed),
                            "reviewed_chinese_glyph_tokens": sum(glyph_frequencies[i] for i in reviewed),
                            "unresolved_chinese_glyph_tokens": totals["cn_glyph"] - sum(glyph_frequencies[i] for i in reviewed),
                            "out_of_range_chinese_glyph_tokens": totals["unknown_cn_glyph"],
                            "out_of_range_extended_glyph_tokens": totals["unknown_extended_glyph"],
                            "fallback_tokens": totals["unknown_fallback"],
                            "status": "partial_user_review_other_glyphs_unresolved",
                            "note": "Glyph indices and lossless bytes are not Unicode recognition. The extended-token bound uses the inspected font-1 table; the rendering context of every indexed entry is not established."},
        "file_reconstruction": {"matching_resources": file_equal, "total_resources": len(files),
                                "method": "Copy each complete original file as a template, then replace indexed strings with their token bytes; preserve all other bytes.",
                                "independent_format_rebuild": False,
                                "entries_with_nonzero_slot_tail": sum(f["entries_with_nonzero_slot_tail"] for f in per_file),
                                "nonzero_slot_tail_bytes": sum(f["nonzero_slot_tail_bytes"] for f in per_file)},
        "actual_gameplay_verified": False, "runtime_status": "not_tested_by_roundtrip_operation",
        "limitations": ["Other language and non-indexed game text are outside this roundtrip.",
                        "Unknown glyphs are retained verbatim; this does not prove their Unicode meanings.",
                        "This read-only roundtrip does not make or exercise a test copy."],
        "files": per_file,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("private_index", type=Path)
    parser.add_argument("output_report", type=Path)
    parser.add_argument("--review", type=Path, default=Path("data/baselines/c7130c53-reviewed-glyphs.json"))
    args = parser.parse_args()
    try:
        result = run(args.image, args.private_index, args.review)
    except (OSError, ValueError, KeyError, StopIteration, struct.error) as exc:
        result = {"schema_version": 1, "source_image_sha256": CN_IMAGE_SHA256,
                  "status": "roundtrip_incomplete", "error_type": type(exc).__name__,
                  "error": str(exc)[:500], "actual_gameplay_verified": False}
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(args.output_report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as out:
        json.dump(result, out, indent=2, ensure_ascii=False)
        out.write("\n")
    print(json.dumps({k: result[k] for k in ("status", "indexed_entries", "token_lossless_entries", "failed_entries") if k in result}, ensure_ascii=False))
    return 0 if result["status"] != "roundtrip_incomplete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
