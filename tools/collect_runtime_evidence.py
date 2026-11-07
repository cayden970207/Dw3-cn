#!/usr/bin/env python3
"""Summarize private replay evidence without publishing game screenshots.

The explicit visual observation is made by a human inspecting the two local
screenshots; the pixel comparison itself cannot identify Chinese characters.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

try:
    from tools.font_recovery_probe import ARCHIVE_SHA
    from tools.scan_text_tables import hash_file
    from tools.text_codec import CN_IMAGE_SHA256
except ModuleNotFoundError:
    from font_recovery_probe import ARCHIVE_SHA
    from scan_text_tables import hash_file
    from text_codec import CN_IMAGE_SHA256


def ppm(path: Path) -> bytes:
    magic, dimensions, maximum, pixels = path.read_bytes().split(b"\n", 3)
    if (magic, dimensions, maximum, len(pixels)) != (b"P6", b"320 240", b"255", 320 * 240 * 3):
        raise ValueError("Screenshot is not the inspected 320x240 RGB format")
    return pixels


def pixel_diff(before: bytes, after: bytes) -> dict:
    if len(before) != len(after) or len(before) != 320 * 240 * 3:
        raise ValueError("Screenshot dimensions differ")
    points = [(i % 320, i // 320) for i in range(320 * 240)
              if before[i * 3:i * 3 + 3] != after[i * 3:i * 3 + 3]]
    if not points:
        raise ValueError("The two screenshots do not differ")
    return {"changed_pixels": len(points), "bbox_exclusive": [min(x for x, _ in points),
             min(y for _, y in points), max(x for x, _ in points) + 1, max(y for _, y in points) + 1],
            "changes_outside_title_window": sum(not (0 <= x < 180 and 20 <= y < 45) for x, y in points)}


def replay(path: Path, required: dict[str, int]) -> dict:
    summary = json.loads((path / "PRIVATE_run_summary.json").read_text())
    result = json.loads((path / "run/result.json").read_text())
    if result.get("status") != "ok":
        raise ValueError("Replay did not report completion")
    checkpoints = {x["name"]: x for x in summary["checkpoints"]}
    if any(name not in checkpoints or checkpoints[name]["stage"] != stage for name, stage in required.items()):
        raise ValueError("A required replay checkpoint is missing or has an unexpected stage")
    return {"emulator": summary["emulator"], "bios": summary["bios"], "frames": summary["frames"],
            "checkpoints": {name: {"frame": checkpoints[name]["frame"], "stage": stage}
                            for name, stage in required.items()}}


def write_exclusive(path: Path, value: dict) -> None:
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original_zip", type=Path)
    parser.add_argument("sample_image", type=Path)
    parser.add_argument("sample_report", type=Path)
    parser.add_argument("static_font_report", type=Path)
    parser.add_argument("before_replay", type=Path)
    parser.add_argument("after_replay", type=Path)
    parser.add_argument("before_ppm", type=Path)
    parser.add_argument("after_ppm", type=Path)
    parser.add_argument("language_log", type=Path)
    parser.add_argument("runtime_output", type=Path)
    parser.add_argument("font_output", type=Path)
    parser.add_argument("--sample-output", type=Path, help="Optional sanitized creation report with runtime scope")
    parser.add_argument("--batch-report", type=Path, help="Optional separately verified menu-batch report")
    parser.add_argument("--visual-title-checked", action="store_true",
                        help="Only after personally inspecting the two private screenshots")
    args = parser.parse_args()
    sample = json.loads(args.sample_report.read_text())
    static = json.loads(args.static_font_report.read_text())
    if (hash_file(args.original_zip) != ARCHIVE_SHA or sample.get("source_image_sha256") != CN_IMAGE_SHA256 or
            static.get("source_image_sha256") != CN_IMAGE_SHA256 or
            hash_file(args.sample_image) != sample.get("test_image_sha256")):
        raise ValueError("Source or sample identity mismatch")
    before = replay(args.before_replay, {"saved": 12, "loaded": 2, "album_open": 18})
    after = replay(args.after_replay, {"saved": 12, "loaded": 2, "album_open": 18,
                                      "album_return": 16, "deck_edit_open": 4, "field_return": 2})
    if before["emulator"] != after["emulator"] or before["bios"] != after["bios"]:
        raise ValueError("Replay environments differ")
    diff = pixel_diff(ppm(args.before_ppm), ppm(args.after_ppm))
    if diff["changes_outside_title_window"]:
        raise ValueError("Screenshots differ outside the inspected title area")
    language = args.language_log.read_text(errors="replace")
    if ("shot=PRIVATE_language_before" not in language or "before_selection_language=2" not in language or
            "after_selection_language=2 stage=14" not in language):
        raise ValueError("Language-selection observation is incomplete")
    if not args.visual_title_checked:
        raise ValueError("A human must visually inspect both screenshots before marking the title verified")
    runtime = {
        "schema_version": 1, "status": "single_sample_runtime_verified",
        "source_image_sha256": CN_IMAGE_SHA256, "sample_image_sha256": sample["test_image_sha256"],
        "emulator": after["emulator"], "bios": after["bios"],
        "language_selection": {"choice": "中文", "choice_index": 0, "state_after_confirmation": 2,
                               "evidence": "Local language artwork/screenshot visual inspection and independent memory log."},
        "original_replay": before, "sample_replay": after,
        "screenshots": {"before_ppm_sha256": hash_file(args.before_ppm),
                        "after_ppm_sha256": hash_file(args.after_ppm), "comparison": diff,
                        "visual_observation": "Analyst inspected both local screenshots: the original English title and the sample 卡片图鉴 are legible without visible overlap.",
                        "automatic_ocr_used": False, "images_published": False},
        "limits": ["Only this menu and the recorded replay path were checked; the full game was not tested.",
                   "Fresh memory cards were saved and reloaded before visiting the album; post-translation saving was not exercised.",
                   "The pinned replay logs nonfatal Lua listener resets and pad-command warnings; it nevertheless reached all listed checkpoints."],
    }
    static["status"] = "partial_user_review_one_menu_runtime_verified"
    static["language_selection"]["runtime_verified"] = True
    static["language_selection"]["label_evidence"] = "Local menu artwork and emulator screenshot visually inspected; state 2 observed after confirmation."
    static["first_untranslated_candidate"]["status"] = "one_sample_runtime_rendered"
    static["width_safety"]["sample_title_runtime_verified"] = True
    static["test_copy_created"] = True
    static["single_menu_gameplay_verified"] = True
    static["sample_image_sha256"] = sample["test_image_sha256"]
    static["runtime_evidence"] = {"report": args.runtime_output.name, "scope": "One card-album title and the recorded navigation path"}
    static["blockers"] = [f"Only {static['reviewed_chinese_mapping_count']} glyphs have human-reviewed Unicode identities; other glyphs remain unresolved.",
                          "The general variable-width measurement routine disagrees with Chinese drawing; the generic writer remains blocked.",
                          "Other menus, dialogue paths, and whole-game regression remain unverified."]
    if args.batch_report:
        batch = json.loads(args.batch_report.read_text())
        if (batch.get("source_image_sha256") != CN_IMAGE_SHA256 or
                batch.get("status") != "five_new_entries_runtime_verified" or batch.get("new_entries_verified") != 5):
            raise ValueError("Menu-batch report is not the verified c713 batch")
        static["status"] = "partial_user_review_and_five_menu_entries_runtime_verified"
        static["batch_runtime_evidence"] = {"report": args.batch_report.name,
                                            "test_image_sha256": batch["test_image_sha256"],
                                            "new_entries_verified": batch["new_entries_verified"]}
    write_exclusive(args.runtime_output, runtime)
    write_exclusive(args.font_output, static)
    if args.sample_output:
        sample["status"] = "test_copy_created_single_menu_runtime_verified"
        sample["runtime_verified"] = True
        sample["runtime_scope"] = "One card-album title and the recorded navigation path; see the runtime evidence report."
        sample["runtime_evidence_report"] = args.runtime_output.name
        write_exclusive(args.sample_output, sample)
    print(json.dumps({"status": runtime["status"], "screenshot_difference": diff}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
