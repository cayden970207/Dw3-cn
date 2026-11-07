#!/usr/bin/env python3
"""Create one tightly scoped, local-only card-album test disc.

This is not a general translation writer. It requires the exact inspected
image, its private index, the matching overlay, and the user's partial glyph
review. It never opens the source for writing and never overwrites output.
The resulting disc and its companion CUE contain game data: keep them local.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile

try:
    from tools.byte_patch import make_patch
    from tools.font_recovery_probe import ARCHIVE_SHA, FONT_LBA, FONT_SECTORS, run as investigate
    from tools.prepare_translation_patch import prepare
    from tools.psx_sector import EDC_BEGIN, USER_BEGIN, check_form1, patch_form1_user_bytes
    from tools.roundtrip_cn import inspect_resource
    from tools.scan_text_tables import Disc, hash_file, local_image
    from tools.text_codec import CN_C713, CN_IMAGE_SHA256, cn_canonical_code, load_reviewed_glyphs, tokenize
except ModuleNotFoundError:
    from byte_patch import make_patch
    from font_recovery_probe import ARCHIVE_SHA, FONT_LBA, FONT_SECTORS, run as investigate
    from prepare_translation_patch import prepare
    from psx_sector import EDC_BEGIN, USER_BEGIN, check_form1, patch_form1_user_bytes
    from roundtrip_cn import inspect_resource
    from scan_text_tables import Disc, hash_file, local_image
    from text_codec import CN_C713, CN_IMAGE_SHA256, cn_canonical_code, load_reviewed_glyphs, tokenize

ENTRY_SHA = "870746459242413a79eb73d41ba6f1e73b30c25dc6b073622f6c2f6a1d681fa3"
RESOURCE_SHA = "31fae89a1956bd41b7d56b1eaf06dde58e2663a5b271ecb96797b8668e49b7e5"
FONT_RAW_SHA = "4a3462be607f559ac7cac81de03f2036f0df51c0ab999ddfaaaef387348db15a"
DISC_NAME = "DW3-card-album-test.bin"
CUE_NAME = "DW3-card-album-test.cue"


def exclusive_write(path: Path, data: bytes) -> None:
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())


def digest_extent(path: Path, start: int, length: int) -> str:
    with path.open("rb") as stream:
        stream.seek(start)
        data = stream.read(length)
    if len(data) != length:
        raise ValueError("Truncated protected extent")
    return hashlib.sha256(data).hexdigest()


def make_sample(image: Path, index_path: Path, audit_path: Path, review_path: Path,
                draft_path: Path, output_dir: Path) -> dict:
    root = Path(__file__).resolve().parents[1]
    local = (root / "local").resolve()
    output_dir = output_dir.resolve()
    if output_dir == local or not output_dir.is_relative_to(local):
        raise ValueError("Test disc must be in a new directory under the ignored local/ tree")
    if image.resolve().is_relative_to(output_dir):
        raise ValueError("Source image must not be within the output directory")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    reviewed = load_reviewed_glyphs(review_path)
    with draft_path.open(encoding="utf-8", newline="") as stream:
        drafts = [row for row in csv.DictReader(stream) if row["context"] == "card album menu"]
    if len(drafts) != 1 or drafts[0]["proposed_zh_cn"] != "卡片图鉴":
        raise ValueError("The single inspected draft is missing or changed")
    if any(ch not in reviewed.values() for ch in drafts[0]["proposed_zh_cn"]):
        raise ValueError("The proposed title contains an unreviewed glyph")
    static, _ = investigate(image, index_path, audit_path, draft_path, review_path)
    title = static["first_untranslated_candidate"]
    if (not title["all_proposed_characters_user_reviewed"] or title["source_control_count"] != 0 or
            title["source_draw_advance_pixels"] != 61 or title["proposed_draw_advance_pixels"] != 48 or
            title["window_origin_pixels"] != [16, 25]):
        raise ValueError("Single-title rendering preconditions failed")
    glyphs = {ch: cn_canonical_code(i).hex() for i, ch in reviewed.items()}
    profile = {"schema_version": 1, "verified_for_image_sha256": CN_IMAGE_SHA256,
               "mapping_reviewed": True, "glyphs": glyphs}
    review = {"schema_version": 1, "source_image_sha256": CN_IMAGE_SHA256,
              "entries": [{"file_id": 38, "table": None, "index": 1,
                           "original_sha256": ENTRY_SHA, "reviewed": True,
                           "segments": [drafts[0]["proposed_zh_cn"]]}]}
    prepared = prepare(index, profile, review)
    replacement = bytes.fromhex(prepared["changes"][0]["replacement_hex"])
    tokens = tokenize(replacement, variant=CN_C713)
    if len(replacement) != 9 or [t.kind for t in tokens] != ["cn_glyph"] * 4 + ["terminator"]:
        raise ValueError("Prepared replacement has unexpected tokens or size")
    files = [f for f in index["text_files"] if f["file_id"] == 38]
    if len(files) != 1:
        raise ValueError("Private index does not identify exactly one target resource")
    resource = files[0]
    rows = [row for row in resource["entries"] if row["table"] is None and row["index"] == 1]
    if len(rows) != 1 or resource["file_sha256"] != RESOURCE_SHA or rows[0]["raw_sha256"] != ENTRY_SHA:
        raise ValueError("Target source hashes differ from the inspected sample")
    row = rows[0]
    original = bytes.fromhex(row["raw_hex"])
    if len(original) != 12 or hashlib.sha256(original).hexdigest() != ENTRY_SHA:
        raise ValueError("Original title length or hash changed")
    if any(t.kind == "control" for t in tokenize(original, variant=CN_C713)):
        raise ValueError("Source control sequence is not empty")
    padded = replacement + bytes(len(original) - len(replacement))

    # Create a new private directory atomically. A previously existing directory
    # is never reused because it may contain the user's files.
    output_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    temporary = None
    with local_image(image) as source:
        if hash_file(source) != CN_IMAGE_SHA256:
            raise ValueError("Unpacked source image hash changed")
        disc = Disc(source)
        if (disc.sector_bytes, disc.user_offset) != (2352, 24):
            raise ValueError("Unsupported source sector layout")
        data = disc.read_user(resource["lba"], resource["file_size_bytes"])
        inspection = inspect_resource(data, resource)
        if inspection["errors"] or not inspection["file_template_equal"]:
            raise ValueError("Target file table, pointers, or original text differ")
        offset = row["offset_bytes"]
        if data[offset:offset + len(original)] != original:
            raise ValueError("Actual title differs from its indexed preimage")
        # Verify both adjacent table pointers, not just a scanner-reported offset.
        if struct.unpack_from("<II", data, 8) != (offset, offset + len(original)):
            raise ValueError("Target entry is not bounded by its actual pointer table")
        sector_number = resource["lba"] + offset // 2048
        user_offset = offset % 2048
        if user_offset + len(original) > 2048:
            raise ValueError("Target crosses a sector boundary")
        with source.open("rb") as source_file:
            source_file.seek(sector_number * 2352)
            sector = source_file.read(2352)
        if not check_form1(sector):
            raise ValueError("Original target-sector checksum is invalid")
        patched = patch_form1_user_bytes(sector, user_offset, original, padded)
        try:
            with tempfile.NamedTemporaryFile(mode="w+b", prefix=".card-album-", dir=output_dir, delete=False) as tmp:
                temporary = Path(tmp.name)
                with source.open("rb") as src:
                    shutil.copyfileobj(src, tmp, length=1024 * 1024)
                tmp.seek(sector_number * 2352)
                tmp.write(patched)
                tmp.flush()
                os.fsync(tmp.fileno())
            with temporary.open("rb") as file:
                file.seek(sector_number * 2352)
                actual_sector = file.read(2352)
            if actual_sector != patched or not check_form1(actual_sector):
                raise ValueError("Written sector failed EDC/ECC or byte verification")
            delta = make_patch(source, temporary)
            if delta["source_sha256"] != CN_IMAGE_SHA256 or delta["size_bytes"] != source.stat().st_size:
                raise ValueError("Whole-image verification failed")
            raw_start = sector_number * 2352
            allowed = ((raw_start + USER_BEGIN + user_offset, raw_start + USER_BEGIN + user_offset + len(original)),
                       (raw_start + EDC_BEGIN, raw_start + 2352))
            for span in delta["spans"]:
                start = span["offset"]
                end = start + len(span["after_hex"]) // 2
                if not any(a <= start < end <= b for a, b in allowed):
                    raise ValueError("An unexpected byte outside the title and checksum changed")
            font_extent = (FONT_LBA * 2352, FONT_SECTORS * 2352)
            source_font_sha = digest_extent(source, *font_extent)
            output_font_sha = digest_extent(temporary, *font_extent)
            if source_font_sha != FONT_RAW_SHA or output_font_sha != FONT_RAW_SHA:
                raise ValueError("Protected filetable ID 647 changed")
            if hash_file(image) != ARCHIVE_SHA:
                raise ValueError("Original archive hash changed while preparing sample")
            report = {
                "schema_version": 1, "status": "test_copy_created_runtime_pending",
                "source_image_sha256": delta["source_sha256"], "test_image_sha256": delta["target_sha256"],
                "source_archive_sha256": ARCHIVE_SHA, "image_size_bytes": delta["size_bytes"],
                "resource_id": 38, "entry_index": 1, "target_lba": sector_number,
                "source_slot_bytes": len(original), "replacement_bytes_including_terminator": len(replacement),
                "padding_bytes": len(padded) - len(replacement), "source_and_replacement_controls": 0,
                "source_draw_advance_pixels": 61, "replacement_draw_advance_pixels": 48,
                "font_id_647_raw_sha256_before_and_after": source_font_sha,
                "changed_raw_bytes": delta["changed_bytes"], "changed_raw_sectors": 1,
                "unchanged_raw_sectors": disc.sector_count - 1,
                "all_differences_within_title_or_target_sector_edc_ecc": True,
                "target_sector_edc_ecc_before_and_after_valid": True,
                "other_raw_sectors_byte_identical": True, "runtime_verified": False,
                "note": "One image-specific sample; the generic writer remains blocked by the width-measurement mismatch."
            }
            # Link is atomic and fails if a destination has appeared; unlike
            # replace(), it can never overwrite the user's existing image.
            os.link(temporary, output_dir / DISC_NAME)
            exclusive_write(output_dir / CUE_NAME,
                            f'FILE "{DISC_NAME}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'.encode())
            exclusive_write(output_dir / "PRIVATE_card_album_delta.json",
                            (json.dumps(delta, indent=2) + "\n").encode())
            exclusive_write(output_dir / "SHARE_THIS_card_album_test_report.json",
                            (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode())
            return report
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("image", "private_index", "prior_audit", "review", "draft", "output_dir"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    try:
        result = make_sample(args.image, args.private_index, args.prior_audit, args.review, args.draft, args.output_dir)
    except (OSError, ValueError, KeyError, StopIteration, struct.error) as exc:
        print(json.dumps({"status": "blocked", "error_type": type(exc).__name__, "error": str(exc)[:500]}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
