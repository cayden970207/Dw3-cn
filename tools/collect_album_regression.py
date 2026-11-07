#!/usr/bin/env python3
"""Create a sanitized report from two completed private emulator processes."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re

try:
    from tools.replay_card_album import BIOS_SHA1, EMULATOR_SHA256
    from tools.scan_text_tables import hash_file
    from tools.text_codec import CN_IMAGE_SHA256
except ModuleNotFoundError:
    from replay_card_album import BIOS_SHA1, EMULATOR_SHA256
    from scan_text_tables import hash_file
    from text_codec import CN_IMAGE_SHA256

SAMPLE_SHA = "afdb58042d8060a9523305f18a3e35e6841089d903248ba8ebd2408302b7e433"


def run(directory: Path, sample: Path, seed_card: Path) -> dict:
    if hash_file(sample) != SAMPLE_SHA:
        raise ValueError("Existing single-title sample hash mismatch")
    first = json.loads((directory / "first/result.json").read_text())
    cold = json.loads((directory / "cold/result.json").read_text())
    first_trace = json.loads((directory / "PRIVATE_first_trace.json").read_text())
    cold_trace = json.loads((directory / "PRIVATE_cold_trace.json").read_text())
    if any(r.get("status") != "ok" for r in (first, cold)):
        raise ValueError("Both independent emulator processes must finish successfully")
    if any(s.get("type") in ("reset", "write_mem") for t in (first_trace, cold_trace) for s in t["steps"]):
        raise ValueError("Recorded phases include an in-process reset or state injection")
    cp = {x["name"]: x for x in first["checkpoints"]}
    cc = {x["name"]: x for x in cold["checkpoints"]}
    wanted = {"page_first_before": 1, "page_first_clamp": 1, "page_forward": 2,
              "page_backward": 1, "page_last_clamp": 27, "page_first_return": 1}
    log = (directory / "first/emulator.log").read_text(errors="replace")
    observations = [(int(p), int(f)) for p, f in re.findall(r'regression_page=(\d+) frame=(\d+)', log)]
    checks = {}
    for name, expected in wanted.items():
        if name not in cp or cp[name]["stage"] != 18:
            raise ValueError("A page checkpoint is missing")
        earlier = [page for page, frame in observations if frame <= cp[name]["frame"]]
        if not earlier or earlier[-1] != expected:
            raise ValueError(f"Observed page differs at {name}")
        checks[name] = {"frame": cp[name]["frame"], "observed_page": earlier[-1]}
    all_pages = sorted({page for page, _ in observations})
    if all_pages != list(range(1, 28)):
        raise ValueError("Not every page was observed")
    required = {"album_return_first": 16, "album_reopened": 18, "album_return_second": 16,
                "deck_edit_open": 4, "field_return": 2, "post_album_saved": 12,
                "post_album_save_returned": 2}
    if any(name not in cp or cp[name]["stage"] != stage for name, stage in required.items()):
        raise ValueError("A navigation or post-album save checkpoint is missing")
    started = re.search(r'regression_save_write_started_frame=(\d+)', log)
    finished = re.search(r'regression_save_done_frame=(\d+)', log)
    if not started or not finished or not (cp["field_return"]["frame"] < int(started.group(1)) <
                                           int(finished.group(1)) == cp["post_album_saved"]["frame"]):
        raise ValueError("No complete post-album card write was observed")
    if hash_file(seed_card) == hash_file(directory / "first/memcard1.mcd"):
        raise ValueError("Private card did not change after the post-album save")
    for name, stage in (("cold_loaded", 2), ("cold_album_open", 18), ("cold_album_visible", 18)):
        if name not in cc or cc[name]["stage"] != stage:
            raise ValueError("Cold-start replay did not reach a required checkpoint")
    if not any(s.get("type") == "wait_mem" and s.get("addr") == "0x80048F42" and
               s.get("value") == 1 for s in cold_trace["steps"]):
        raise ValueError("Cold replay did not require the saved card-case item")
    photos = {}
    for path in ("first/PRIVATE_page_first.ppm", "first/PRIVATE_page_second.ppm", "first/PRIVATE_page_last.ppm",
                 "first/PRIVATE_page_first_return.ppm", "first/PRIVATE_post_album_save.ppm", "cold/PRIVATE_page_first.ppm"):
        p = directory / path
        if not p.is_file():
            raise ValueError("A required local screenshot is missing")
        photos[path] = hash_file(p)
    cold_log = (directory / "cold/emulator.log").read_text(errors="replace")
    return {"schema_version": 1, "status": "album_pages_and_post_visit_cold_load_verified",
            "source_image_sha256": CN_IMAGE_SHA256, "sample_image_sha256": SAMPLE_SHA,
            "emulator": {"name": "PCSX-Redux", "version": "a74a0587", "build_id": 433,
                         "executable_sha256": EMULATOR_SHA256, "bios": "OpenBIOS", "bios_sha1": BIOS_SHA1,
                         "mode": "headless, unthrottled, two separate processes, private memory-card copies"},
            "input_cue": "local/font-recovery/run-20261107/card_album_test/DW3-card-album-test.cue",
            "page_checks": checks, "observed_pages": all_pages,
            "navigation_checkpoints": {name: {"frame": cp[name]["frame"], "stage": stage}
                                       for name, stage in required.items()},
            "post_album_save": {"write_started_frame": int(started.group(1)),
                                "write_completed_frame": int(finished.group(1)),
                                "saved_screen_locally_captured": True,
                                "fresh_card_copy_changed_after_visit": True},
            "cold_restart": {"separate_process": True, "read_saved_card_case_item": 1,
                             "checkpoints": {name: {"frame": cc[name]["frame"], "stage": cc[name]["stage"]}
                                             for name in ("cold_loaded", "cold_album_open", "cold_album_visible")}},
            "local_screenshot_sha256": photos,
            "pad_warnings": {"first_process_unknown_command_43": log.count("Unknown command for pad: 43"),
                             "cold_process_unknown_command_43": cold_log.count("Unknown command for pad: 43"),
                             "assessment": "Page values, menu transitions and cold-load state changed as requested despite these warnings; untested controller behavior is not covered."},
            "limitations": ["Only the recorded single-title sample and one emulator/OpenBIOS configuration were tested.",
                            "Screenshots and independent memory cards remain private and are not included."]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("directory", "sample_image", "seed_card", "report"):
        p.add_argument(name, type=Path)
    a = p.parse_args()
    try:
        result = run(a.directory, a.sample_image, a.seed_card)
    except (OSError, ValueError, KeyError) as exc:
        result = {"schema_version": 1, "status": "blocked", "error_type": type(exc).__name__, "error": str(exc)[:500]}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(a.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps({"status": result["status"], "report": str(a.report)}))
    return 2 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
