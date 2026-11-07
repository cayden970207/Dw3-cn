#!/usr/bin/env python3
"""Prepare private DW2003 text-byte patch manifests from reviewed Chinese text.

Strict bridge between verified glyph encoding and tools/apply_text_bytes.py.
Input scanner report and translation review stay LOCAL and must not be pushed.
It preserves every source control code, player-name insertion and speaker
separator, and rejects unmapped glyphs, stale sources, and byte overflows.

Does not modify the ISO. It does not assert that glyph pixels or text widths
are visually correct. The resulting manifest must go through apply_text_bytes,
qa_text (with REAL metrics) and emulator regression tests.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

try:
    from tools.text_codec import CN_C713, REFERENCE, CodecError, tokenize, variant_for_image
except ModuleNotFoundError:
    from text_codec import CN_C713, REFERENCE, CodecError, tokenize, variant_for_image

SHA256 = re.compile(r"[0-9a-f]{64}\Z")
HEX = re.compile(r"(?:[0-9a-f]{2})+\Z")
MAX_REVIEWED_ENTRIES = 128
MAX_GLYPH_BYTES = 4


class PreparationError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def glyph_profile(profile: dict, source_sha: str) -> dict[str, bytes]:
    if not isinstance(profile, dict) or profile.get("schema_version") != 1:
        raise PreparationError("Invalid glyph profile schema")
    if profile.get("verified_for_image_sha256") != source_sha:
        raise PreparationError("Glyph profile has not been tied to the input image SHA-256")
    if profile.get("mapping_reviewed") is not True:
        raise PreparationError("Glyph mapping must be explicitly reviewed")
    glyphs = profile.get("glyphs")
    if not isinstance(glyphs, dict) or not glyphs:
        raise PreparationError("Nonempty glyphs map required")
    codes: dict[str, bytes] = {}
    used: dict[bytes, str] = {}
    for ch, raw in glyphs.items():
        if not isinstance(ch, str) or len(ch) != 1 or not isinstance(raw, str) or not HEX.fullmatch(raw):
            raise PreparationError("Glyph entries must map single Unicode characters to lowercase hex bytes")
        code = bytes.fromhex(raw)
        if not 1 <= len(code) <= MAX_GLYPH_BYTES or 0 in code:
            raise PreparationError("Invalid glyph byte sequence")
        if code[0] == 2 or (code[0] in (1, 3) and len(code) != 2):
            raise PreparationError("Glyph code conflicts with reserved control/extended byte sequences")
        if code in used:
            raise PreparationError(f"Two Unicode characters map to the same glyph code: {ch!r}")
        codes[ch] = code
        used[code] = ch
    all_codes = sorted(used, key=lambda x: (len(x), x))
    for i, code in enumerate(all_codes):
        if any(other.startswith(code) for other in all_codes[i + 1:]):
            raise PreparationError("Glyph byte sequences overlap by prefix; ambiguous decoder")
    if variant_for_image(source_sha) == CN_C713:
        used_indices = set()
        for code in all_codes:
            try:
                parts = tokenize(code + b"\x00", variant=CN_C713)
            except CodecError as exc:
                raise PreparationError(f"Invalid Chinese glyph boundary: {exc}") from exc
            if len(parts) != 2 or parts[0].kind not in ("cn_glyph", "base_glyph", "extended_glyph"):
                raise PreparationError("Glyph code does not represent one supported glyph")
            if parts[0].kind == "cn_glyph":
                if parts[0].glyph_index in used_indices:
                    raise PreparationError("Two glyph codes address the same Chinese glyph")
                used_indices.add(parts[0].glyph_index)
    return codes


def text_regions(source: bytes, glyph_codes: dict[str, bytes], *, variant: str = REFERENCE) -> tuple[list[bytes], list[bytes]]:
    """Split original entry into glyph runs and preserved binary control tags.

    The returned lists obey len(regions) = len(controls) + 1. We use explicit
    image-bound codec to avoid splitting custom glyphs across a control-like
    byte. Unknown glyphs remain in their original region.
    """
    if not isinstance(source, bytes) or len(source) < 2 or source[-1] != 0 or 0 in source[:-1]:
        raise PreparationError("Source text must have exactly one terminating NUL")
    controls: list[bytes] = []
    regions: list[bytes] = []
    run = bytearray()
    try:
        tokens = tokenize(source, variant=variant)
    except CodecError as exc:
        raise PreparationError(str(exc)) from exc
    for token in tokens:
        if token.kind == "control":
            regions.append(bytes(run))
            run.clear()
            controls.append(token.raw)
        elif token.kind != "terminator":
            run.extend(token.raw)
    regions.append(bytes(run))
    if len(regions) != len(controls) + 1:
        raise PreparationError("Incorrect region boundaries")
    if b"".join(region + control for region, control in zip(regions, controls)) + regions[-1] + b"\x00" != source:
        raise PreparationError("Source failed exact binary roundtrip")
    return regions, controls


def encode_segment(text: str, glyph_codes: dict[str, bytes]) -> bytes:
    if not isinstance(text, str):
        raise PreparationError("Replacement segment must be Unicode text or null")
    if "\x00" in text:
        raise PreparationError("Text cannot contain a NUL")
    try:
        return b"".join(glyph_codes[ch] for ch in text)
    except KeyError as exc:
        raise PreparationError(f"Glyph not in verified mapping: {exc.args[0]!r}") from exc


def prepared_bytes(original: bytes, segments: list, glyph_codes: dict[str, bytes], *, variant: str = REFERENCE) -> bytes:
    regions, controls = text_regions(original, glyph_codes, variant=variant)
    if not isinstance(segments, list) or len(segments) != len(regions):
        raise PreparationError(f"Text has {len(regions)} segments separated by control codes; supply exactly that many")
    out = bytearray()
    for i, (region, value) in enumerate(zip(regions, segments)):
        out.extend(region if value is None else encode_segment(value, glyph_codes))
        if i < len(controls):
            out.extend(controls[i])
    out.append(0)
    if len(out) > len(original):
        raise PreparationError("Replacement is longer than the original encoded byte capacity")
    if out == original:
        raise PreparationError("Replacement is identical to original")
    new_regions, new_controls = text_regions(bytes(out), glyph_codes, variant=variant)
    if new_controls != controls:
        raise PreparationError("Replacement changed or removed source control codes")
    return bytes(out)


def prepare(scan: dict, profile: dict, review: dict) -> dict:
    if not isinstance(scan, dict) or scan.get("schema_version") != 1 or not scan.get("inventory_complete"):
        raise PreparationError("Real, complete private scanner report is required")
    image_sha = scan.get("image_sha256")
    if not isinstance(image_sha, str) or not SHA256.fullmatch(image_sha):
        raise PreparationError("Missing actual image SHA-256")
    codes = glyph_profile(profile, image_sha)
    if not isinstance(review, dict) or review.get("schema_version") != 1:
        raise PreparationError("Invalid reviewed translation document")
    if review.get("source_image_sha256") != image_sha:
        raise PreparationError("Translation review is not for the same exact image")
    edits = review.get("entries")
    if not isinstance(edits, list) or not 1 <= len(edits) <= MAX_REVIEWED_ENTRIES:
        raise PreparationError("Invalid reviewed entry count")
    lookup = {}
    for file in scan.get("text_files", []):
        fid = file.get("file_id")
        for row in file.get("entries", []):
            key = (fid, row.get("table"), row.get("index"))
            if key in lookup:
                raise PreparationError("Duplicate scanner source address")
            lookup[key] = row

    manifest = {"schema_version": 1, "source_image_sha256": image_sha, "changes": []}
    seen = set()
    for entry in edits:
        if not isinstance(entry, dict):
            raise PreparationError("Invalid review entry")
        fid, sub, idx = entry.get("file_id"), entry.get("table"), entry.get("index")
        if type(fid) is not int or type(idx) is not int or (sub is not None and type(sub) is not int):
            raise PreparationError("Invalid entry address")
        key = (fid, sub, idx)
        if key in seen:
            raise PreparationError("Repeated translation entry")
        seen.add(key)
        if idx == 0 or key not in lookup:
            raise PreparationError("Unknown/reserved translation entry")
        if entry.get("reviewed") is not True:
            raise PreparationError("Only explicitly reviewed translations can be encoded")
        row = lookup[key]
        old_hex = row.get("raw_hex")
        if not isinstance(old_hex, str) or not HEX.fullmatch(old_hex):
            raise PreparationError("Missing private original raw_hex")
        old = bytes.fromhex(old_hex)
        old_sha = digest(old)
        if row.get("raw_sha256") != old_sha or row.get("encoded_bytes") != len(old):
            raise PreparationError("Scanner raw text hash/length mismatch")
        if entry.get("original_sha256") != old_sha:
            raise PreparationError("Translation source is stale")
        changed = prepared_bytes(old, entry.get("segments"), codes, variant=variant_for_image(image_sha))
        manifest["changes"].append({
            "file_id": fid, "table": sub, "index": idx,
            "original_sha256": old_sha, "replacement_hex": changed.hex()
        })
    return manifest


def save_private(path: Path, data: dict):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600),
                   "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.write("\n")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("private_scan", type=Path)
    p.add_argument("glyph_profile", type=Path)
    p.add_argument("reviewed_translations", type=Path)
    p.add_argument("output_manifest", type=Path)
    args = p.parse_args(argv)
    try:
        scan_report = json.loads(args.private_scan.read_text(encoding="utf-8"))
        glyphs = json.loads(args.glyph_profile.read_text(encoding="utf-8"))
        reviewed = json.loads(args.reviewed_translations.read_text(encoding="utf-8"))
        manifest = prepare(scan_report, glyphs, reviewed)
        save_private(args.output_manifest, manifest)
        print(json.dumps({
            "status": "manifest_created_not_gameplay_verified",
            "image_sha256": manifest["source_image_sha256"],
            "reviewed_entries": len(manifest["changes"]),
            "warning": "No source image changed. Actual game rendering/flow remain untested.",
        }, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
