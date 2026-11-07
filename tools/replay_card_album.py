#!/usr/bin/env python3
"""Replay the isolated card-album scenario using a local PCSX-Redux installation.

The pinned upstream replay scripts are read from the user's local reference
checkout, not included in this project. New screenshots, logs and fresh memory
cards stay under a newly created ignored local/ directory.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

try:
    from tools.font_recovery_probe import ARCHIVE_SHA
    from tools.scan_text_tables import hash_file, local_image
    from tools.text_codec import CN_IMAGE_SHA256
except ModuleNotFoundError:
    from font_recovery_probe import ARCHIVE_SHA
    from scan_text_tables import hash_file, local_image
    from text_codec import CN_IMAGE_SHA256

REFERENCE_COMMIT = "841521ab2071096038316b93b8f778255b85730a"
STACK_COMMIT = "f93bf5905ac595aeba11762cb9e5fa9dc233987b"
BIOS_SHA1 = "7bc681b350a3ac1e1e95ab27d56775384bcb4801"
EMULATOR_SHA256 = "53451b6b313af0b5342d23ff50a60bea283a7268ef68df0f5482d8ade4e8cb3c"


def git_head(path: Path) -> str:
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()


def short_trace(reference: Path) -> dict:
    script = json.loads((reference / "tests/replay/scripts/first_battle_save.json").read_text())
    positions = [i for i, step in enumerate(script["steps"]) if step.get("name") == "album_open"]
    if len(positions) != 1:
        raise ValueError("Pinned replay no longer contains one album checkpoint")
    pos = positions[0]
    original = script["steps"]
    steps = original[:pos + 1] + [{"type": "wait_frames", "frames": 600}]
    for n, step in enumerate(original[pos + 1:]):
        steps.append(step)
        if n == 0:
            steps.append({"type": "checkpoint", "name": "album_return"})
        if step.get("type") == "wait_map" and step.get("map") == "0x211":
            steps.append({"type": "checkpoint", "name": "field_return"})
            break
    else:
        raise ValueError("Pinned replay no longer returns to the field")
    if any(step.get("type") == "write_mem" for step in steps):
        raise ValueError("This scenario must not inject game state")
    return {"name": "cn_card_album_sample", "description": "Fresh normal-input replay; local captures only",
            "max_frames": 50000, "default_timeout": script["default_timeout"], "steps": steps}


@contextmanager
def source_cue(image: Path, mode: str, sample_report: Path | None):
    if mode == "original":
        if hash_file(image) != ARCHIVE_SHA:
            raise ValueError("Original ZIP hash mismatch")
        with local_image(image) as raw:
            if hash_file(raw) != CN_IMAGE_SHA256:
                raise ValueError("Unpacked source hash mismatch")
            cue = raw.parent / "user-disc.cue"
            cue.write_text(f'FILE "{raw.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
            yield cue
    else:
        if sample_report is None:
            raise ValueError("Sample mode requires its metadata report")
        report = json.loads(sample_report.read_text())
        if (report.get("source_image_sha256") != CN_IMAGE_SHA256 or
                report.get("resource_id") != 38 or report.get("entry_index") != 1 or
                hash_file(image) != report.get("test_image_sha256")):
            raise ValueError("Sample image does not match its creation report")
        cue = image.with_suffix(".cue")
        expected = f'FILE "{image.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
        if cue.read_text() != expected:
            raise ValueError("Sample CUE differs from the generated image binding")
        yield cue


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("original", "sample"))
    parser.add_argument("image", type=Path, help="Original ZIP or the generated sample BIN")
    parser.add_argument("output_dir", type=Path, help="New directory under local/")
    parser.add_argument("--sample-report", type=Path)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--emulator", type=Path, required=True)
    parser.add_argument("--bios", type=Path, required=True)
    parser.add_argument("--version-json", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    local = (root / "local").resolve()
    output = args.output_dir.resolve()
    reference = args.reference.resolve()
    if output == local or not output.is_relative_to(local):
        raise ValueError("Replay output must be under the ignored local/ tree")
    if git_head(reference) != REFERENCE_COMMIT or git_head(reference / "psxstack") != STACK_COMMIT:
        raise ValueError("Reference repository or replay submodule differs from the pinned commit")
    for checkout in (reference, reference / "psxstack"):
        if subprocess.run(["git", "-C", str(checkout), "diff", "--quiet", "HEAD"], check=False).returncode:
            raise ValueError("Reference checkout has uncommitted tracked changes")
    if hashlib.sha1(args.bios.read_bytes()).hexdigest() != BIOS_SHA1:
        raise ValueError("This replay has only been checked with the recorded OpenBIOS build")
    version = json.loads(args.version_json.read_text())
    if version.get("version") != "a74a0587" or version.get("buildId") != 433:
        raise ValueError("This replay has only been checked with the recorded emulator build")
    if hash_file(args.emulator) != EMULATOR_SHA256:
        raise ValueError("Emulator executable differs from the recorded macOS ARM build")
    trace = short_trace(reference)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    trace_path = output / "PRIVATE_trace.json"
    trace_path.write_text(json.dumps(trace, indent=2) + "\n")
    sys.path.insert(0, str(reference / "psxstack/tools/replay"))
    import emulator
    with source_cue(args.image.resolve(), args.mode, args.sample_report) as cue:
        config = emulator.configure(root=reference, game_json=reference / "port/game/game.json",
            redux_dir=output, iso=cue, scripts_dir=reference / "tests/replay/scripts",
            expected_dir=output, probes=reference / "tests/replay/probes.lua")
        config.redux = args.emulator.resolve()
        config.redux_version = args.version_json.resolve()
        config.openbios = args.bios.resolve()
        os.environ["DW3_REPLAY_RUN_LUA"] = str(reference / "psxstack/tools/replay/run.lua")
        result = emulator.run_once(trace_path, trace, config.openbios, output / "run",
                                   lua=root / "tools/card_album_capture.lua", speed=0, iso=cue)
    (output / "PRIVATE_run_summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": "replay_completed", "frames": result["frames"],
                      "checkpoints": [c["name"] for c in result["checkpoints"]]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
