#!/usr/bin/env python3
"""Check the five batch-02 DRAFTS against published evidence and glyph review.

This reads repository JSON only. It never reads/writes a disc, never returns
an executable patch, and cannot clear any existing writer safety gate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

try:
    from tools.text_codec import CN_IMAGE_SHA256, load_reviewed_glyphs
except ModuleNotFoundError:
    from text_codec import CN_IMAGE_SHA256, load_reviewed_glyphs

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {(52, i) for i in (15, 19, 20, 25, 27)}
SHA = re.compile(r"[0-9a-f]{64}\Z")


def address(row: dict, *, prior: bool = False) -> tuple[int, int]:
    fid = row.get("file_id", row.get("resource_id"))
    idx = row.get("index") if prior else row.get("entry_index")
    if type(fid) is not int or type(idx) is not int or fid < 0 or idx < 0:
        raise ValueError("Invalid resource/entry address")
    if prior and row.get("table") is not None:
        raise ValueError("Expected a flat previous-menu table")
    return fid, idx


def check(draft: dict, prior: dict, evidence: dict, reviewed: dict[int, str]) -> dict:
    for document in (draft, prior, evidence):
        if (not isinstance(document, dict) or document.get("schema_version") != 1 or
                document.get("source_image_sha256") != CN_IMAGE_SHA256):
            raise ValueError("All inputs must use the inspected original-image baseline")
    if (draft.get("status") != "translation_drafts_only" or
            draft.get("safe_to_write") is not False or
            draft.get("gameplay_verified") is not False):
        raise ValueError("Draft catalog must not claim write or gameplay approval")
    preserved = {address(row, prior=True) for row in prior.get("entries", [])}
    sources = {}
    for row in evidence.get("candidate_shortlist", []):
        key = address(row)
        if key in sources:
            raise ValueError("Duplicate address in candidate evidence")
        sources[key] = row
    entries = draft.get("entries")
    if not isinstance(entries, list) or len(entries) != len(TARGETS):
        raise ValueError("Batch 02 must contain exactly the five remaining candidates")
    known = set(reviewed.values())
    if (len(known) != len(reviewed) or
            any(not isinstance(ch, str) or len(ch) != 1 for ch in known)):
        raise ValueError("Glyph identities must be unique single characters")
    seen, results, required = set(), [], []
    for row in entries:
        key = address(row)
        if key in seen or key not in TARGETS or key in preserved:
            raise ValueError("Duplicate, out-of-scope, or already completed target")
        seen.add(key)
        source = sources.get(key)
        if not source or source.get("status") != "unresolved_not_modified":
            raise ValueError("Target is not an unresolved candidate in the current report")
        digest = row.get("original_sha256")
        if not isinstance(digest, str) or not SHA.fullmatch(digest) or digest != source.get("source_sha256"):
            raise ValueError("Source entry hash differs from actual-image report")
        for field in ("meaning_zh", "font", "window_context", "window_origin_px", "trigger"):
            if row.get(field) != source.get(field):
                raise ValueError(f"Candidate context mismatch: {field}")
        if row.get("source_text_reviewed") is not False or row.get("runtime_verified") is not False:
            raise ValueError("Draft cannot inherit source-text or runtime approval")
        text = row.get("proposed_zh_cn")
        if (not isinstance(text, str) or not 1 <= len(text) <= 30 or
                any(not '\u4e00' <= ch <= '\u9fff' for ch in text)):
            raise ValueError("This draft checker accepts short plain-CJK labels only")
        for ch in text:
            if ch not in required:
                required.append(ch)
        missing = list(dict.fromkeys(ch for ch in text if ch not in known))
        blockers = ["original_text_and_control_review_pending", "source_byte_capacity_pending",
                    "actual_window_layout_pending", "runtime_and_postvisit_save_load_pending"]
        if missing:
            blockers.insert(0, "glyph_identity_missing")
        results.append({
            "file_id": key[0], "entry_index": key[1], "original_sha256": digest,
            "proposed_zh_cn": text, "missing_reviewed_characters": missing,
            "conditional_plain_cn_bytes_with_nul": 2 * len(text) + 1,
            "conditional_cn_advance_px": 12 * len(text),
            "blockers": blockers, "safe_to_write": False,
        })
    return {
        "schema_version": 1, "batch_id": "p1-batch02", "status": "drafts_not_ready_for_writing",
        "source_image_sha256": CN_IMAGE_SHA256, "draft_entries": len(results),
        "previous_entries_preserved": len(preserved), "reviewed_glyph_count": len(reviewed),
        "required_distinct_characters": len(required),
        "missing_reviewed_characters": [ch for ch in required if ch not in known],
        "entries": results, "source_image_read_this_run": False,
        "original_text_read_this_run": False, "new_playable_entries": 0,
        "safe_to_write": False, "gameplay_verified": False,
        "scope_note": "This checks only the historical draft; separately verified playable entries are recorded in runtime reports.",
        "measurement_note": "Conditional counts assume canonical two-byte Chinese glyphs, 12px advance and no controls. No glyph bitmap, source slot capacity or exact clipping bounds were loaded; these are not layout approval.",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", type=Path, default=ROOT / "data/p1_batch02_draft.json")
    parser.add_argument("--prior", type=Path, default=ROOT / "data/p1_batch01_reviewed.json")
    parser.add_argument("--evidence", type=Path, default=ROOT / "reports/SHARE_THIS_p1_batch01_report.json")
    parser.add_argument("--glyphs", type=Path, default=ROOT / "data/baselines/c7130c53-reviewed-glyphs.json")
    args = parser.parse_args(argv)
    try:
        documents = [json.loads(p.read_text(encoding="utf-8")) for p in (args.draft, args.prior, args.evidence)]
        result = check(*documents, load_reviewed_glyphs(args.glyphs))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1  # Valid draft report, but never permission to write a disc.


if __name__ == "__main__":
    raise SystemExit(main())
