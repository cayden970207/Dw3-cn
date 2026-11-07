#!/usr/bin/env python3
"""Private real-input album regression: page actions, post-album save, cold load.

Requires the recorded emulator, reference checkout and existing test copy.
Never writes a supplied memory card: each phase gets its own new local copy.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

try:
    from tools.replay_card_album import (BIOS_SHA1, EMULATOR_SHA256, REFERENCE_COMMIT,
                                         STACK_COMMIT, git_head, short_trace, source_cue)
    from tools.scan_text_tables import hash_file
except ModuleNotFoundError:
    from replay_card_album import (BIOS_SHA1, EMULATOR_SHA256, REFERENCE_COMMIT,
                                   STACK_COMMIT, git_head, short_trace, source_cue)
    from scan_text_tables import hash_file


def index_one(steps, predicate, start=0):
    hits = [i for i in range(start, len(steps)) if predicate(steps[i])]
    if not hits:
        raise ValueError("Required pinned replay step not found")
    return hits[0]


def checkpoint(name):
    return {"type": "checkpoint", "name": name}


def make_traces(reference: Path):
    original = json.loads((reference / "tests/replay/scripts/first_battle_save.json").read_text())["steps"]
    base = short_trace(reference)
    steps = base["steps"]
    at = index_one(steps, lambda s: s.get("name") == "album_open")
    if steps[at + 1].get("type") != "wait_frames":
        raise ValueError("Unexpected album entry sequence")
    paging = [
        {"type": "wait_frames", "frames": 600}, checkpoint("page_first_before"),
        {"type": "press", "buttons": ["L1"], "frames": 3, "release": 120}, checkpoint("page_first_clamp"),
        {"type": "press", "buttons": ["R1"], "frames": 3, "release": 120}, checkpoint("page_forward"),
        {"type": "press", "buttons": ["L1"], "frames": 3, "release": 120}, checkpoint("page_backward"),
        {"type": "press", "buttons": ["R1"], "frames": 3, "release": 120, "repeat": 30},
        {"type": "wait_frames", "frames": 150}, checkpoint("page_last_clamp"),
        {"type": "press", "buttons": ["L1"], "frames": 3, "release": 120, "repeat": 30},
        {"type": "wait_frames", "frames": 150}, checkpoint("page_first_return"),
    ]
    # The following original press exits to stage 16. Reopen and exit once more
    # before the retained deck-editor and field-return checks.
    close = steps[at + 2]
    reopen = steps[at - 1]
    if close.get("buttons") != ["TRIANGLE"] or reopen.get("buttons") != ["CROSS"]:
        raise ValueError("Unexpected pinned album open/exit actions")
    after_close = at + 3
    if steps[after_close].get("name") == "album_return":
        after_close += 1
    steps = (steps[:at + 1] + paging + [close, checkpoint("album_return_first"),
             {"type": "wait_frames", "frames": 300}, reopen,
             {"type": "wait_frames", "frames": 600}, checkpoint("album_reopened"),
             close, checkpoint("album_return_second")] + steps[after_close:])

    # Follow the already investigated route from the arena to the inn, then
    # talk to its save attendant. This card already has one earlier save, so
    # the first-run "create a new file" wait must not be reused.
    shop_left = index_one(original, lambda s: s.get("name") == "card_shop_left")
    stairs = index_one(original, lambda s: s.get("type") == "walk" and s.get("x") == 199 and s.get("y") == 237, shop_left)
    steps += original[shop_left + 1:stairs + 1]
    prior_saved = index_one(original, lambda s: s.get("name") == "saved")
    attendant = max(i for i in range(prior_saved) if original[i].get("type") == "walk" and
                    original[i].get("x") == 240 and original[i].get("y") == 212)
    enter_save = index_one(original, lambda s: s.get("type") == "press" and
                           s.get("until", {}).get("stage") == 12, attendant)
    steps += original[attendant:enter_save + 2]  # talk, then select card slot
    steps += [{"type": "press", "buttons": ["CROSS"], "frames": 2, "release": 30,
               "until": {"type": "wait_mem", "addr": "0x80048750", "size": 4, "value": 4},
               "timeout": 4000},
              {"type": "wait_mem", "addr": "0x80048A50", "size": 4, "value": "0x2700", "timeout": 4000},
              checkpoint("post_album_saved"),
              {"type": "wait_frames", "frames": 160},
              {"type": "press", "buttons": ["CROSS"], "frames": 4},
              {"type": "press", "buttons": ["TRIANGLE"], "frames": 2, "release": 30,
               "until": {"type": "wait_stage", "stage": 2}, "timeout": 1500},
              checkpoint("post_album_save_returned")]
    first = {"name": "album_regression_save", "max_frames": 80000,
             "default_timeout": 3000, "steps": steps}

    reset = index_one(original, lambda s: s.get("type") == "reset")
    loaded = index_one(original, lambda s: s.get("name") == "loaded", reset)
    cold_steps = original[reset + 1:loaded] + [checkpoint("cold_loaded"),
                  {"type": "wait_mem", "addr": "0x80048F42", "size": 1, "value": 1, "timeout": 300}]
    # From the saved inn location down the stairs to the arena, then open the
    # album directly: the saved card-case flag has already been checked.
    stairs_down = index_one(original, lambda s: s.get("type") == "walk" and
                            s.get("x") == 199 and s.get("y") == 237, loaded)
    arena = index_one(original, lambda s: s.get("type") == "press" and
                      s.get("until", {}).get("map") == "0x211", stairs_down)
    cardcase = index_one(original, lambda s: s.get("name") == "card_case", arena)
    album = index_one(original, lambda s: s.get("name") == "album_open", cardcase)
    cold_steps += original[stairs_down:arena + 1] + original[cardcase + 1:album] + [checkpoint("cold_album_open"),
                  {"type": "wait_frames", "frames": 600}, checkpoint("cold_album_visible")]
    cold = {"name": "album_regression_cold_load", "max_frames": 22000,
            "default_timeout": 3000, "steps": cold_steps}
    if any(s.get("type") in ("write_mem", "reset") for s in cold["steps"]):
        raise ValueError("Cold process must start fresh and use no state injection")
    if any(s.get("type") == "write_mem" for s in first["steps"]):
        raise ValueError("Regression must not inject game state")
    return first, cold


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sample_image", type=Path)
    parser.add_argument("sample_report", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--emulator", type=Path, required=True)
    parser.add_argument("--bios", type=Path, required=True)
    parser.add_argument("--version-json", type=Path, required=True)
    parser.add_argument("--seed-card", type=Path, required=True,
                        help="Existing private pre-album save; copied, never modified")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    local = (root / "local").resolve()
    out, reference = args.output.resolve(), args.reference.resolve()
    if out == local or not out.is_relative_to(local):
        raise ValueError("Output must be a new directory under local/")
    if git_head(reference) != REFERENCE_COMMIT or git_head(reference / "psxstack") != STACK_COMMIT:
        raise ValueError("Reference checkout differs from pinned commits")
    if any(subprocess.run(["git", "-C", str(p), "diff", "--quiet", "HEAD"]).returncode
           for p in (reference, reference / "psxstack")):
        raise ValueError("Reference checkout has tracked modifications")
    import hashlib
    if hashlib.sha1(args.bios.read_bytes()).hexdigest() != BIOS_SHA1 or hash_file(args.emulator) != EMULATOR_SHA256:
        raise ValueError("Emulator or OpenBIOS differs from inspected build")
    version = json.loads(args.version_json.read_text())
    if version.get("version") != "a74a0587" or version.get("buildId") != 433:
        raise ValueError("Emulator version metadata differs")
    first, cold = make_traces(reference)
    reset = index_one(first["steps"], lambda s: s.get("type") == "reset")
    first["steps"] = first["steps"][reset + 1:]
    first["name"] = "album_regression_from_pre_album_save"
    first["max_frames"] = 60000
    seed = args.seed_card.resolve()
    if not seed.is_relative_to(local) or not seed.is_file() or seed.stat().st_size != 131072:
        raise ValueError("Seed must be an existing private 128 KiB memory card under local/")
    seed_sha = hash_file(seed)
    out.mkdir(mode=0o700, parents=False, exist_ok=False)
    sys.path.insert(0, str(reference / "psxstack/tools/replay"))
    import emulator
    with source_cue(args.sample_image.resolve(), "sample", args.sample_report) as cue:
        cfg = emulator.configure(root=reference, game_json=reference / "port/game/game.json", redux_dir=out,
            iso=cue, scripts_dir=reference / "tests/replay/scripts", expected_dir=out,
            probes=reference / "tests/replay/probes.lua")
        cfg.redux, cfg.redux_version, cfg.openbios = args.emulator.resolve(), args.version_json.resolve(), args.bios.resolve()
        os.environ["DW3_REPLAY_RUN_LUA"] = str(reference / "psxstack/tools/replay/run.lua")
        def run_process(name, trace, source_card):
            phase = out / name
            phase.mkdir(mode=0o700)
            card = phase / "memcard1.mcd"
            with source_card.open("rb") as src, card.open("xb") as dst:
                shutil.copyfileobj(src, dst)
            if hash_file(source_card) != hash_file(card):
                raise ValueError("Private memory-card copy mismatch")
            card_sha_before = hash_file(card)
            trace_lua = phase / "script.lua"
            trace_lua.write_text("return " + emulator.lua_literal(emulator.parse_ints(trace)) + "\n")
            (out / f"PRIVATE_{name}_trace.json").write_text(json.dumps(trace, indent=2) + "\n")
            env = emulator.replay_env(trace_lua, phase, speed=0)
            cmd = [str(cfg.redux), "-no-ui", "-stdout", "-testmode", "-run", "-iso", str(cue),
                   "-bios", str(cfg.openbios), "-memcard1", str(card), "-memcard2", str(phase / "memcard2.mcd"),
                   "-dofile", str(root / "tools/album_regression_capture.lua")]
            with (phase / "emulator.log").open("w") as log:
                proc = subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=600)
            result_path = phase / "result.json"
            if not result_path.exists():
                raise RuntimeError(f"{name} emulator exited {proc.returncode} without a result")
            result = json.loads(result_path.read_text())
            if proc.returncode or result.get("status") != "ok":
                raise RuntimeError(f"{name} replay failed: {result.get('message')}")
            return result, card_sha_before

        result, copied_sha = run_process("first", first, seed)
        if hash_file(seed) != seed_sha or copied_sha != seed_sha:
            raise ValueError("Source memory card changed")
        new_card = out / "first/memcard1.mcd"
        if hash_file(new_card) == seed_sha:
            raise ValueError("No changed card bytes were saved after visiting the album")
        print('first_process_finished frames=' + str(result['frames']), flush=True)
        cold_result, card_sha_before = run_process("cold", cold, new_card)
        print(json.dumps({"cold_status": cold_result["status"], "cold_frames": cold_result["frames"],
                          "copied_card_sha256_before_cold_boot": card_sha_before}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
