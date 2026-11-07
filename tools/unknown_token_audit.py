#!/usr/bin/env python3
"""Locate six previously reported unknown tokens without changing their bytes.

The references are derived from overlays matched byte-for-byte to the pinned
upstream source. Runtime triggers and byte identities remain unresolved.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path

try:
    from tools.font_recovery_probe import ARCHIVE_SHA, EXE_BASE, EXE_SHA, filetable, u32
    from tools.roundtrip_cn import inspect_resource
    from tools.scan_text_tables import Disc, hash_file, local_image
    from tools.text_codec import CN_C713, CN_IMAGE_SHA256, tokenize
except ModuleNotFoundError:
    from font_recovery_probe import ARCHIVE_SHA, EXE_BASE, EXE_SHA, filetable, u32
    from roundtrip_cn import inspect_resource
    from scan_text_tables import Disc, hash_file, local_image
    from text_codec import CN_C713, CN_IMAGE_SHA256, tokenize

STAGE_BASE = 0x800A5DE0
CASES = [
    (199, 77, 18, "unknown_extended_glyph", "WSTAG200.PRO", 0x800A6CA0, 0, 0x800A737C, 178, [305, 616]),
    (199, 78, 18, "unknown_extended_glyph", "WSTAG200.PRO", 0x800A6CA0, 1, 0x800A737C, 178, [305, 616]),
    (206, 368, 18, "unknown_extended_glyph", "WSTAG520.PRO", 0x800A686C, 0, 0x800A6CD0, 103, [400, 303]),
    (213, 765, 81, "unknown_extended_glyph", "WSTAG620.PRO", 0x800A7AA8, 0, 0x800A7C3C, 101, [432, 260]),
    (220, 513, 33, "unknown_fallback", "WSTAG226.PRO", 0x800A6A48, 0, 0x800A6BD4, 104, [255, 352]),
    (220, 520, 14, "unknown_extended_glyph", "WSTAG226.PRO", 0x800A6A9C, 1, 0x800A6C10, 178, [277, 363]),
]
OVERLAY_SHA1 = {"WSTAG200.PRO": "764dea164d1305c81d8fdcd83a6e1eb8b9c1510c",
                "WSTAG226.PRO": "5cf176ef9bf48fb8dccaf5b762443513eb3cc98d",
                "WSTAG520.PRO": "3c5bb2f6a23dc31f155d1a3167f6946f8a0f2feb",
                "WSTAG620.PRO": "9441465e50419f23fd6370252d58edaf09333558"}


def run(image: Path, private_index: Path, prior_audit: Path) -> dict:
    if hash_file(image) != ARCHIVE_SHA:
        raise ValueError("Source archive hash mismatch")
    index = json.loads(private_index.read_text())
    audit = json.loads(prior_audit.read_text())
    if index.get("image_sha256") != CN_IMAGE_SHA256 or audit.get("source_image_sha256") != CN_IMAGE_SHA256:
        raise ValueError("Prior index or audit does not match source image")
    boot = [x for x in audit["iso_directory_files"] if x["path"].upper() == "SLES_039.36"]
    if len(boot) != 1:
        raise ValueError("Expected one boot executable")
    found = []
    with local_image(image) as raw:
        if hash_file(raw) != CN_IMAGE_SHA256:
            raise ValueError("Unpacked image hash mismatch")
        disc = Disc(raw)
        exe = disc.read_user(boot[0]["lba"], boot[0]["size_bytes"])
        if hashlib.sha256(exe).hexdigest() != EXE_SHA:
            raise ValueError("Executable hash mismatch")
        overlays, files = {}, {}
        for name, expected in OVERLAY_SHA1.items():
            rows = [r for r in audit["executable_filetable"] if (r.get("directory_path") or "").endswith("/" + name)]
            if len(rows) != 1:
                raise ValueError("Expected one indexed stage overlay")
            r = rows[0]
            if filetable(exe, r["file_id"]) != (r["lba"], r["sectors"]):
                raise ValueError("Actual filetable no longer matches stage overlay")
            data = disc.read_user(r["lba"], r["directory_size_bytes"])
            if hashlib.sha1(data).hexdigest() != expected:
                raise ValueError("Stage overlay does not match its inspected reference")
            overlays[name] = (data, r["file_id"])
        for fid in sorted({x[0] for x in CASES}):
            f = next(x for x in index["text_files"] if x["file_id"] == fid)
            actual_lba, actual_sectors = filetable(exe, fid)
            if actual_lba != f["lba"] or (f["file_size_bytes"] + 2047) // 2048 > actual_sectors:
                raise ValueError("Actual text filetable does not match its private index")
            data = disc.read_user(f["lba"], f["file_size_bytes"])
            checked = inspect_resource(data, f)
            if checked["errors"] or not checked["file_template_equal"]:
                raise ValueError(f"Text resource {fid} failed real-index validation")
            files[fid] = f
        for fid, entry, off, kind, name, talk, talk_no, actor, actor_id, coords in CASES:
            f = files[fid]
            row = next(x for x in f["entries"] if x["index"] == entry and x["table"] is None)
            raw_text = bytes.fromhex(row["raw_hex"])
            matches = [t for t in tokenize(raw_text, variant=CN_C713) if t.offset == off and t.kind == kind]
            if len(matches) != 1:
                raise ValueError("Unknown token location changed")
            overlay, stage_file_id = overlays[name]
            if u32(overlay, talk - STAGE_BASE + talk_no * 12 + 8) != entry:
                raise ValueError("Stage talk entry does not match the actual overlay")
            if u32(overlay, actor - STAGE_BASE + 4) != talk:
                raise ValueError("Stage actor does not point to the expected talk table")
            found.append({"file_id": fid, "entry_index": entry, "entry_sha256": row["raw_sha256"],
                          "token_offset": off, "token_kind": kind,
                          "token_length": len(matches[0].raw),
                          "stage_overlay": name, "stage_file_id": stage_file_id,
                          "stage_overlay_sha1": OVERLAY_SHA1[name], "talk_pointer": hex(talk),
                          "actor_pointer": hex(actor), "actor_id": actor_id, "actor_position": coords,
                          "source_path_scope": "Conditional field-actor dialogue; stage loading and actor pointer verified statically.",
                          "runtime_trigger_verified": False, "meaning": "unresolved", "preserved_unchanged": True})
    return {"schema_version": 1, "status": "six_tokens_located_context_static_runtime_pending",
            "source_image_sha256": CN_IMAGE_SHA256, "unknown_token_count": len(found),
            "cases": found,
            "limitations": ["Conditional flags and actual dialogue rendering were not exercised.",
                            "An extended token outside the inspected font-1 bound is not automatically corrupt.",
                            "Raw text and original token bytes remain in the private index, not this report."]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("image", "private_index", "prior_audit", "report"):
        p.add_argument(name, type=Path)
    a = p.parse_args()
    try:
        result = run(a.image, a.private_index, a.prior_audit)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        result = {"schema_version": 1, "status": "blocked", "source_image_sha256": CN_IMAGE_SHA256,
                  "error_type": type(exc).__name__, "error": str(exc)[:500]}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(a.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as out:
        json.dump(result, out, indent=2, ensure_ascii=False)
        out.write("\n")
    print(json.dumps({"status": result["status"], "report": str(a.report)}))
    return 2 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
