#!/usr/bin/env python3
"""Build the fixed, user-reviewed card-menu batch from the original ZIP.

This is not a generic writer. It checks exact source, glyphs, overlay hashes,
real pre-change screenshots, control sequences, ink, byte capacity, the whole
raw-disc difference and protected tail. All game data remains under local/.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

try:
    from tools.byte_patch import make_patch
    from tools.font_recovery_probe import ARCHIVE_SHA, EXE_SHA, FONT_LBA, FONT_SECTORS, filetable
    from tools.layout_cn import Font1, actual_assets, measure, white_bbox
    from tools.make_card_album_sample import FONT_RAW_SHA, digest_extent, exclusive_write
    from tools.prepare_translation_patch import prepare
    from tools.psx_sector import EDC_BEGIN, USER_BEGIN, check_form1, patch_form1_user_bytes
    from tools.roundtrip_cn import inspect_resource
    from tools.scan_text_tables import Disc, hash_file, local_image
    from tools.text_codec import CN_C713, CN_IMAGE_SHA256, cn_canonical_code, load_reviewed_glyphs, tokenize
except ModuleNotFoundError:
    from byte_patch import make_patch
    from font_recovery_probe import ARCHIVE_SHA, EXE_SHA, FONT_LBA, FONT_SECTORS, filetable
    from layout_cn import Font1, actual_assets, measure, white_bbox
    from make_card_album_sample import FONT_RAW_SHA, digest_extent, exclusive_write
    from prepare_translation_patch import prepare
    from psx_sector import EDC_BEGIN, USER_BEGIN, check_form1, patch_form1_user_bytes
    from roundtrip_cn import inspect_resource
    from scan_text_tables import Disc, hash_file, local_image
    from text_codec import CN_C713, CN_IMAGE_SHA256, cn_canonical_code, load_reviewed_glyphs, tokenize

DISC_NAME = "DW3-p1-batch01.bin"
EXPECTED = {
    (38, 1): ("卡片图鉴", 16, 25, "album"),
    (52, 5): ("买一张卡片", 167, 53, "shop"),
    (52, 6): ("打开卡包", 167, 67, "shop"),
    (52, 7): ("编辑卡组", 167, 81, "shop"),
    (52, 21): ("没有卡包", 154, 113, "empty"),
    (52, 26): ("编辑卡组", 167, 35, "deck"),
}
PICTURES = {
    "album": "59ad673d725cc599fd79f2da036e2e627769a6215761449fda234538af3d5b9f",
    "shop": "75426217c22d016acbf0dc6bb982bbf72090161e9310b65b3e47b0ed365f6dd0",
    "empty": "7d27d98e4c23e5defca9b0ea93b6cf40db3b54fea7b6c9c2b42fcfc576fb44b5",
    "deck": "1a00e5caf481574d4956a9418eb616cd366bc261d105090a5fbec4166cbbe058",
}
OVERLAYS = {"STCRDABM.PRO": "0e58e7aef70165be0bb6b95d59236941ab648b28",
            "STCRDSHP.PRO": "d1e01bd598694a6aa8ddd0978f6d382fa970d314",
            "STCRDDEK.PRO": "f813f89a1ab17597e6a429c42d0acd81dd41aa49"}


def run(image: Path, index_path: Path, audit_path: Path, glyph_path: Path, translations_path: Path,
        output: Path, pictures: dict[str, Path]) -> dict:
    root, output = Path(__file__).resolve().parents[1], output.resolve()
    local = (root / "local").resolve()
    if output == local or not output.is_relative_to(local) or image.resolve().is_relative_to(output):
        raise ValueError("Output must be a new ignored local directory separate from the source")
    if hash_file(image) != ARCHIVE_SHA:
        raise ValueError("Source ZIP differs from the inspected archive")
    index, audit = json.loads(index_path.read_text()), json.loads(audit_path.read_text())
    reviewed, translations = load_reviewed_glyphs(glyph_path), json.loads(translations_path.read_text())
    if translations.get("source_image_sha256") != CN_IMAGE_SHA256 or translations.get("schema_version") != 1:
        raise ValueError("Translation review is not tied to the inspected image")
    entries = translations["entries"]
    by_key = {(e["file_id"], e["index"]): e for e in entries}
    if len(entries) != len(EXPECTED) or set(by_key) != set(EXPECTED):
        raise ValueError("This specialized writer accepts exactly the six inspected entries")
    for key, (target, _, _, _) in EXPECTED.items():
        row = by_key[key]
        if row.get("table") is not None or row.get("target") != target:
            raise ValueError("The reviewed batch or its exact translation changed")
        if any(ch not in reviewed.values() for ch in target):
            raise ValueError("A target contains a glyph that the user has not confirmed")
    for name, expected in PICTURES.items():
        if name not in pictures or hash_file(pictures[name]) != expected:
            raise ValueError(f"Private {name} pre-change screenshot hash mismatch")

    font = actual_assets(image, audit)
    profile = {"schema_version": 1, "verified_for_image_sha256": CN_IMAGE_SHA256,
               "mapping_reviewed": True,
               "glyphs": {ch: cn_canonical_code(i).hex() for i, ch in reviewed.items()}}
    translation_review = {"schema_version": 1, "source_image_sha256": CN_IMAGE_SHA256,
                          "entries": [{"file_id": e["file_id"], "table": None, "index": e["index"],
                                       "original_sha256": e["original_sha256"], "reviewed": True,
                                       "segments": [e["target"]]} for e in entries]}
    manifest = prepare(index, profile, translation_review)
    replacements = {(e["file_id"], e["index"]): bytes.fromhex(e["replacement_hex"]) for e in manifest["changes"]}
    if set(replacements) != set(EXPECTED):
        raise ValueError("Prepared manifest does not match the reviewed batch")
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    temporary = None
    with local_image(image) as raw:
        if hash_file(raw) != CN_IMAGE_SHA256:
            raise ValueError("Source image hash mismatch")
        disc = Disc(raw)
        if (disc.sector_bytes, disc.user_offset) != (2352, 24):
            raise ValueError("Unsupported disc layout")
        boot = [x for x in audit["iso_directory_files"] if x["path"].upper() == "SLES_039.36"]
        if len(boot) != 1:
            raise ValueError("Boot executable missing")
        exe = disc.read_user(boot[0]["lba"], boot[0]["size_bytes"])
        if hashlib.sha256(exe).hexdigest() != EXE_SHA:
            raise ValueError("Executable mismatch")
        for name, sha1 in OVERLAYS.items():
            found = [x for x in audit["executable_filetable"] if (x.get("directory_path") or "").endswith('/' + name)]
            if len(found) != 1:
                raise ValueError("Expected one matching menu overlay")
            row = found[0]
            if filetable(exe, row["file_id"]) != (row["lba"], row["sectors"]):
                raise ValueError("Menu overlay filetable mismatch")
            overlay = disc.read_user(row["lba"], row["directory_size_bytes"])
            if hashlib.sha1(overlay).hexdigest() != sha1:
                raise ValueError("Menu overlay no longer matches inspected drawing path")
        files = {}
        for fid in (38, 52):
            f = next(f for f in index["text_files"] if f["file_id"] == fid)
            data = disc.read_user(f["lba"], f["file_size_bytes"])
            checked = inspect_resource(data, f)
            if checked["errors"] or not checked["file_template_equal"]:
                raise ValueError(f"File {fid} failed its full private-index check")
            files[fid] = (f, data)
        sector_data, ranges, entry_reports = {}, [], []
        with raw.open("rb") as stream:
            for key, (_, x, y, context) in EXPECTED.items():
                fid, number = key
                f, data = files[fid]
                row = next(r for r in f["entries"] if r["table"] is None and r["index"] == number)
                original, replacement = bytes.fromhex(row["raw_hex"]), replacements[key]
                if (row["raw_sha256"] != by_key[key]["original_sha256"] or
                        data[row["offset_bytes"]:row["offset_bytes"] + len(original)] != original):
                    raise ValueError("Source entry differs from its hash-bound preimage")
                old_tokens, new_tokens = tokenize(original, variant=CN_C713), tokenize(replacement, variant=CN_C713)
                if any(t.kind == "control" for t in old_tokens + new_tokens):
                    raise ValueError("The fixed menu entries must not contain control codes")
                if any(t.kind not in ("cn_glyph", "terminator") for t in new_tokens):
                    raise ValueError("Replacement must contain only reviewed Chinese glyphs")
                if len(replacement) > len(original):
                    raise ValueError("Replacement exceeds the original byte capacity")
                old_layout, new_layout = measure(font, original), measure(font, replacement)
                old_box, new_box = old_layout["ink_bbox_relative_exclusive"], new_layout["ink_bbox_relative_exclusive"]
                if old_box is None or new_box is None or new_layout["draw_max_advance_px"] > old_layout["draw_max_advance_px"]:
                    raise ValueError("Replacement exceeds the source's verified drawing advance")
                predicted = [old_box[0] + x, old_box[1] + y, old_box[2] + x, old_box[3] + y]
                # The local screenshots show only the relevant window in this
                # small ROI; the exact screenshot hash also binds the context.
                observed = white_bbox(pictures[context], (x - 1, y, min(x + 143, 320), y + 13))
                if observed != predicted:
                    raise ValueError(f"Original screenshot does not match source ink for {key}")
                if not (new_box[0] >= 0 and new_box[1] >= 0 and new_box[2] <= old_layout["draw_max_advance_px"] and
                        new_box[3] <= 12 and x + new_box[2] <= 320 and y + new_box[3] <= 240):
                    raise ValueError("New glyph ink exceeds the validated source text band or screen")
                offset = row["offset_bytes"]
                lba, user_offset = f["lba"] + offset // 2048, offset % 2048
                if user_offset + len(original) > 2048:
                    raise ValueError("Entry crosses an unsupported sector boundary")
                if lba not in sector_data:
                    stream.seek(lba * 2352)
                    sector_data[lba] = stream.read(2352)
                    if not check_form1(sector_data[lba]):
                        raise ValueError("Source sector has invalid EDC/ECC")
                padded = replacement + bytes(len(original) - len(replacement))
                sector_data[lba] = patch_form1_user_bytes(sector_data[lba], user_offset, original, padded)
                begin = lba * 2352 + USER_BEGIN + user_offset
                ranges.append((begin, begin + len(original)))
                entry_reports.append({"file_id": fid, "entry_index": number, "original_sha256": row["raw_sha256"],
                    "translation": by_key[key]["target"], "role": by_key[key]["role"],
                    "slot_bytes": len(original), "replacement_bytes_including_terminator": len(replacement),
                    "original_control_count": 0, "replacement_control_count": 0,
                    "window_origin_px": [x, y], "source_advance_px": old_layout["draw_max_advance_px"],
                    "replacement_advance_px": new_layout["draw_max_advance_px"],
                    "source_ink_bbox_px": predicted,
                    "replacement_ink_bbox_px": [new_box[0]+x, new_box[1]+y, new_box[2]+x, new_box[3]+y],
                    "actual_source_screenshot_bbox_matched": True, "context": context,
                    "status": "runtime_pending"})
        try:
            with tempfile.NamedTemporaryFile(mode="w+b", prefix=".p1-batch-", dir=output, delete=False) as tmp:
                temporary = Path(tmp.name)
                with raw.open("rb") as source:
                    shutil.copyfileobj(source, tmp, length=1024 * 1024)
                for lba, sector in sector_data.items():
                    tmp.seek(lba * 2352)
                    tmp.write(sector)
                tmp.flush()
                os.fsync(tmp.fileno())
            with temporary.open("rb") as stream:
                for lba, sector in sector_data.items():
                    stream.seek(lba * 2352)
                    saved = stream.read(2352)
                    if saved != sector or not check_form1(saved):
                        raise ValueError("A modified sector failed final EDC/ECC verification")
            delta = make_patch(raw, temporary)
            if delta["source_sha256"] != CN_IMAGE_SHA256:
                raise ValueError("Whole-disc comparison source mismatch")
            allowed = ranges + [(lba * 2352 + EDC_BEGIN, (lba + 1) * 2352) for lba in sector_data]
            changed_lbas = set()
            for span in delta["spans"]:
                start, end = span["offset"], span["offset"] + len(span["after_hex"]) // 2
                if not any(a <= start < end <= b for a, b in allowed):
                    raise ValueError("Whole-disc diff includes unexpected bytes")
                changed_lbas.add(start // 2352)
            if changed_lbas != set(sector_data):
                raise ValueError("Unexpected number of changed sectors")
            font_extent = (FONT_LBA * 2352, FONT_SECTORS * 2352)
            if digest_extent(raw, *font_extent) != FONT_RAW_SHA or digest_extent(temporary, *font_extent) != FONT_RAW_SHA:
                raise ValueError("Protected ID 647 changed")
            if hash_file(image) != ARCHIVE_SHA:
                raise ValueError("Original archive changed")
            result = {"schema_version": 1, "status": "batch_test_copy_created_runtime_pending",
                      "source_image_sha256": CN_IMAGE_SHA256, "test_image_sha256": delta["target_sha256"],
                      "source_archive_sha256": ARCHIVE_SHA, "image_size_bytes": delta["size_bytes"],
                      "entries": entry_reports, "expected_changed_sectors": sorted(changed_lbas),
                      "changed_raw_bytes": delta["changed_bytes"], "changed_raw_sectors": len(changed_lbas),
                      "unchanged_raw_sectors": disc.sector_count - len(changed_lbas),
                      "all_differences_within_reviewed_text_or_modified_sector_checksums": True,
                      "modified_sector_edc_ecc_before_and_after_valid": True,
                      "font_id_647_raw_sha256_before_and_after": FONT_RAW_SHA,
                      "generic_writer_unblocked": False, "runtime_verified": False}
            os.link(temporary, output / DISC_NAME)
            exclusive_write(output / "DW3-p1-batch01.cue",
                f'FILE "{DISC_NAME}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'.encode())
            exclusive_write(output / "PRIVATE_batch_delta.json", (json.dumps(delta, indent=2) + "\n").encode())
            exclusive_write(output / "SHARE_THIS_batch_creation_report.json",
                            (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
            return result
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("image", "private_index", "prior_audit", "glyph_review", "translations", "output"):
        p.add_argument(name, type=Path)
    for name in PICTURES:
        p.add_argument('--' + name + '-screenshot', type=Path, required=True)
    a = p.parse_args()
    try:
        result = run(a.image, a.private_index, a.prior_audit, a.glyph_review, a.translations, a.output,
                     {name: getattr(a, name + '_screenshot') for name in PICTURES})
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        print(json.dumps({"status": "blocked", "error_type": type(exc).__name__, "error": str(exc)[:500]}))
        return 2
    print(json.dumps({"status": result["status"], "test_image_sha256": result["test_image_sha256"],
                      "changed_sectors": result["changed_raw_sectors"]}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
