#!/usr/bin/env python3
"""Measure inspected font-1 drawing, old game measurement, and local ink bounds.

This is a read-only, image-hash-bound investigator, not a write authorizer.
Only font 1, palette 0, unscaled, proportional, stored non-SJIS text is
implemented. Unknown controls and unresolved insertion contexts fail closed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct

try:
    from tools.collect_runtime_evidence import ppm
    from tools.font_recovery_probe import ARCHIVE_SHA, EXE_SHA, EXE_BASE, FONT_LBA, FONT_SECTORS, font_images, u16
    from tools.psx_sector import check_form1
    from tools.roundtrip_cn import inspect_resource
    from tools.scan_text_tables import Disc, hash_file, local_image
    from tools.text_codec import CN_C713, CN_IMAGE_SHA256, tokenize
except ModuleNotFoundError:
    from collect_runtime_evidence import ppm
    from font_recovery_probe import ARCHIVE_SHA, EXE_SHA, EXE_BASE, FONT_LBA, FONT_SECTORS, font_images, u16
    from psx_sector import check_form1
    from roundtrip_cn import inspect_resource
    from scan_text_tables import Disc, hash_file, local_image
    from text_codec import CN_C713, CN_IMAGE_SHA256, tokenize

FONT_RAW_SHA = "4a3462be607f559ac7cac81de03f2036f0df51c0ab999ddfaaaef387348db15a"
GLYPH_SHA = "d3fc6b6bb9cb6d0750e2375984b90df949f7102ad7cb632491c80ded0e23efe5"
FONT1_BASE, FONT1_EXT = 0x8004E5A4, 0x8004EF88
GLYPH_OFFSET, GLYPH_COUNT, GLYPH_STRIDE = 41604, 1997, 72


class LayoutError(ValueError):
    pass


class Font1:
    def __init__(self, exe: bytes, data: bytes):
        if hashlib.sha256(exe).hexdigest() != EXE_SHA:
            raise LayoutError("Executable hash does not match the inspected image")
        self.exe = exe
        self.info, self.images, _ = font_images(data)
        if len(self.images) != 2:
            raise LayoutError("Expected exactly two source font texture pages")
        self.tiles = data[GLYPH_OFFSET:GLYPH_OFFSET + GLYPH_COUNT * GLYPH_STRIDE]
        if hashlib.sha256(self.tiles).hexdigest() != GLYPH_SHA:
            raise LayoutError("Expanded glyph array hash mismatch")
        for page in self.info:
            block = page["blocks"][-1]
            if (block["width_words"], block["height"]) != (64, 256):
                raise LayoutError("Unexpected source texture page dimensions")

    def record(self, address: int, index: int):
        if type(index) is not int or index < 0:
            raise LayoutError("Invalid font metric index")
        return struct.unpack_from("<BBBBBBBbbBB", self.exe, address - EXE_BASE + index * 11)

    def palette(self, record):
        x, y = record[3], record[4]  # palette 0 only
        if not 0 <= y < 256 or x + 15 >= 128:
            raise LayoutError("Font CLUT is outside the two decoded pages")
        result = []
        for i in range(16):
            page, col = divmod(x + i, 64)
            block = self.info[page]["blocks"][-1]
            result.append(u16(self.images[page], block["data_offset"] + (y * 64 + col) * 2))
        return result

    def glyph(self, token):
        base = self.record(FONT1_BASE, 0)
        if token.kind == "cn_glyph":
            index = token.glyph_index
            if not 0 <= index < GLYPH_COUNT:
                raise LayoutError("Chinese glyph outside inspected array")
            data = self.tiles[index * 72:(index + 1) * 72]
            palette = self.palette(base)
            pixels = []
            for y in range(12):
                for x in range(12):
                    colour = (data[y * 6 + x // 2] >> (4 * (x % 2))) & 15
                    if palette[colour] != 0:
                        pixels.append((x, y))
            trail = token.raw[1]
            legacy = 0
            if 1 <= trail <= 0x72:
                old = self.record(FONT1_EXT, trail - 1)
                legacy = old[9] + old[7]
            return 12, legacy, pixels
        if token.kind == "base_glyph" and 4 <= token.glyph_index < 0xEA:
            metric = self.record(FONT1_BASE, token.glyph_index - 4)
        elif token.kind == "extended_glyph" and 1 <= token.glyph_index <= 0x72:
            metric = self.record(FONT1_EXT, token.glyph_index - 1)
        else:
            raise LayoutError("Unknown/fallback glyph or unsupported text mode")
        legacy = metric[9] + metric[7]
        # message_draw_window substitutes font->glyphs[0] for tpage 0xff.
        draw = base if metric[0] == 0xFF else metric
        page, u, v, _, _, width, height, x_offset, y_offset, advance, _ = draw
        if page not in (0, 1) or u + width > 256 or v + height > 256:
            raise LayoutError("Glyph rectangle is outside inspected font pages")
        palette = self.palette(draw)
        block = self.info[page]["blocks"][-1]
        pixels = []
        for y in range(height):
            for x in range(width):
                px = u + x
                byte = self.images[page][block["data_offset"] + (v + y) * 128 + px // 2]
                colour = (byte >> (4 * (px % 2))) & 15
                if palette[colour] != 0:
                    pixels.append((x + x_offset, y + y_offset))
        return advance + x_offset, legacy, pixels


def measure(font: Font1, source: bytes, *, inserted: dict[int, bytes] | None = None,
            page_lines: int = 2) -> dict:
    if page_lines < 1 or page_lines > 6:
        raise LayoutError("Explicit page line count must be 1..6")
    inserted = inserted or {}
    pen_x = pen_y = legacy_x = 0
    draw_lines, measure_lines, pixels = [], [], []
    operations = []

    def walk(data: bytes, nested=False):
        nonlocal pen_x, pen_y, legacy_x
        for token in tokenize(data, variant=CN_C713):
            if token.kind == "terminator":
                return
            if token.kind == "control":
                if nested:
                    raise LayoutError("Controls inside an inserted line require separate runtime verification")
                if token.control_id == 1:
                    if len(draw_lines) + 1 >= page_lines:
                        raise LayoutError("Text would cross the configured page boundary")
                    draw_lines.append(pen_x)
                    measure_lines.append(legacy_x)
                    pen_x, legacy_x, pen_y = 0, 0, pen_y + 14
                    operations.append("newline")
                elif token.control_id == 5:
                    slot = token.raw[2]
                    if slot not in inserted or slot == 0:
                        raise LayoutError("Inserted line bytes and context are required")
                    operations.append("dynamic_line_insert")
                    walk(inserted[slot], nested=True)
                else:
                    raise LayoutError(f"Control {token.control_id} needs an independently verified runtime context")
                continue
            advance, old_width, ink = font.glyph(token)
            pixels.extend((x + pen_x, y + pen_y) for x, y in ink)
            pen_x += advance
            legacy_x += old_width
            operations.append(token.kind)
    walk(source)
    draw_lines.append(pen_x)
    measure_lines.append(legacy_x)
    box = ([min(x for x, _ in pixels), min(y for _, y in pixels),
            max(x for x, _ in pixels) + 1, max(y for _, y in pixels) + 1] if pixels else None)
    return {"draw_advance_by_line_px": draw_lines, "draw_max_advance_px": max(draw_lines),
            "game_measure_by_line_px": measure_lines, "game_measure_max_px": max(measure_lines),
            "ink_bbox_relative_exclusive": box, "line_count": len(draw_lines),
            "operations": {name: operations.count(name) for name in sorted(set(operations))},
            "window_fit": "unresolved_without_verified_context"}


def white_bbox(path: Path, rect: tuple[int, int, int, int]):
    data = ppm(path)
    x0, y0, x1, y1 = rect
    pixels = []
    for y in range(y0, y1):
        for x in range(x0, x1):
            rgb = data[(y * 320 + x) * 3:(y * 320 + x) * 3 + 3]
            if min(rgb) >= 160 and max(rgb) - min(rgb) < 40:
                pixels.append((x, y))
    if not pixels:
        raise LayoutError("No visible light title pixels in inspected screenshot region")
    return [min(x for x, _ in pixels), min(y for _, y in pixels),
            max(x for x, _ in pixels) + 1, max(y for _, y in pixels) + 1]


def neutral_ink_bbox(path: Path, rect: tuple[int, int, int, int]):
    """Include the observed grey antialiasing in the three batch-02 windows.

    This threshold is tied to those inspected blue backgrounds, not a general
    segmentation rule for arbitrary game windows or palettes.
    """
    data = ppm(path)
    x0, y0, x1, y1 = rect
    pixels = []
    for y in range(y0, y1):
        for x in range(x0, x1):
            rgb = data[(y * 320 + x) * 3:(y * 320 + x) * 3 + 3]
            if min(rgb) >= 80 and max(rgb) - min(rgb) < 8:
                pixels.append((x, y))
    if not pixels:
        raise LayoutError('No neutral text ink in the inspected screenshot region')
    return [min(x for x, _ in pixels), min(y for _, y in pixels),
            max(x for x, _ in pixels) + 1, max(y for _, y in pixels) + 1]


def non_background_bbox(path: Path, rect: tuple[int, int, int, int], background: bytes):
    if len(background) != 3:
        raise LayoutError("Background colour must be RGB")
    data = ppm(path)
    x0, y0, x1, y1 = rect
    points = [(x, y) for y in range(y0, y1) for x in range(x0, x1)
              if data[(y * 320 + x) * 3:(y * 320 + x) * 3 + 3] != background]
    if not points:
        raise LayoutError("No non-background pixels in inspected screenshot region")
    return [min(x for x, _ in points), min(y for _, y in points),
            max(x for x, _ in points) + 1, max(y for _, y in points) + 1]


def actual_assets(image: Path, audit: dict):
    if hash_file(image) != ARCHIVE_SHA:
        raise LayoutError("Source ZIP hash mismatch")
    boot = [f for f in audit["iso_directory_files"] if f["path"].upper() == "SLES_039.36"]
    if len(boot) != 1 or audit.get("source_image_sha256") != CN_IMAGE_SHA256:
        raise LayoutError("Prior audit is not bound to the inspected image")
    with local_image(image) as raw:
        if hash_file(raw) != CN_IMAGE_SHA256:
            raise LayoutError("Unpacked image hash mismatch")
        disc = Disc(raw)
        if (disc.sector_bytes, disc.user_offset) != (2352, 24):
            raise LayoutError("Unsupported sector format")
        exe = disc.read_user(boot[0]["lba"], boot[0]["size_bytes"])
        with raw.open("rb") as stream:
            stream.seek(FONT_LBA * 2352)
            sectors = stream.read(FONT_SECTORS * 2352)
        if hashlib.sha256(sectors).hexdigest() != FONT_RAW_SHA or len(sectors) != FONT_SECTORS * 2352:
            raise LayoutError("Font extent hash or length mismatch")
        if not all(check_form1(sectors[i:i + 2352]) for i in range(0, len(sectors), 2352)):
            raise LayoutError("Font sector integrity check failed")
        userdata = b"".join(sectors[i + 24:i + 2072] for i in range(0, len(sectors), 2352))
        return Font1(exe, userdata)


def run(image: Path, index_path: Path, audit_path: Path, before: Path, after: Path,
        shop_screenshot: Path | None = None, empty_screenshot: Path | None = None,
        deck_screenshot: Path | None = None, lab_run: Path | None = None) -> dict:
    index = json.loads(index_path.read_text())
    audit = json.loads(audit_path.read_text())
    if index.get("image_sha256") != CN_IMAGE_SHA256 or not index.get("inventory_complete"):
        raise LayoutError("Private index is not the inspected image's complete index")
    font = actual_assets(image, audit)
    samples = [(3, 1, "pure_chinese"), (73, 13, "mixed_chinese_latin"),
               (59, 9, "chinese_digits"), (59, 5, "chinese_punctuation_space"),
               (3, 14, "explicit_newline"), (52, 12, "dynamic_price_insert"),
               (59, 39, "dynamic_digimon_name_insert"),
               (38, 2, "album_right_edge_label"), (52, 5, "shop_menu_1"),
               (52, 6, "shop_menu_2"), (52, 7, "shop_menu_3"),
               (52, 21, "empty_booster_prompt"), (52, 26, "deck_submenu")]
    source = {}
    with local_image(image) as raw:
        if hash_file(raw) != CN_IMAGE_SHA256:
            raise LayoutError("Unpacked image hash changed")
        disc = Disc(raw)
        for fid in sorted({fid for fid, _, _ in samples} | {38}):
            f = next(f for f in index["text_files"] if f["file_id"] == fid)
            data = disc.read_user(f["lba"], f["file_size_bytes"])
            checked = inspect_resource(data, f)
            if checked["errors"] or not checked["file_template_equal"]:
                raise LayoutError(f"File {fid} did not match its prior private index")
            for row in f["entries"]:
                source[(fid, row["index"])] = row
        if lab_run is not None:
            lab = [f for f in audit["iso_directory_files"] if f["path"].upper() == "AAA/PRO/STGDGLAB.PRO"]
            if len(lab) != 1 or hashlib.sha1(disc.read_user(lab[0]["lba"], lab[0]["size_bytes"])).hexdigest() != "c2b82222d418fbf0a101f7062499ee5719fea7b5":
                raise LayoutError("Lab overlay does not match the inspected caller")
    rows = []
    for fid, entry, label in samples:
        if label == "dynamic_digimon_name_insert":
            # Only the captured, concrete line can support this measurement.
            continue
        row = source[(fid, entry)]
        raw = bytes.fromhex(row["raw_hex"])
        # Card shop's price is supplied with set_line_number into line 1; this
        # concrete value is a controlled example, not a proof of all prices.
        inserts = {1: bytes(4 + int(ch) for ch in "10000") + b"\0"} if label == "dynamic_price_insert" else {}
        try:
            measured = measure(font, raw, inserted=inserts)
            state = "measured_context_or_runtime_unverified"
        except LayoutError as exc:
            measured, state = {"reason": str(exc)}, "unresolved"
        rows.append({"file_id": fid, "entry_index": entry, "category": label,
                     "source_sha256": row["raw_sha256"], "status": state, "measurement": measured,
                     "dynamic_input": "10000, controlled scenario; not a bound on every possible price" if inserts else None})
    original = bytes.fromhex(source[(38, 1)]["raw_hex"])
    # These four codes have independent user glyph review and were actually
    # rendered in the existing sample; no other Unicode mapping is assumed.
    translated = bytes.fromhex("f034f04bf44ef79900")
    snapshots = []
    for name, raw, picture, expected_hash in (
        ("original", original, before, "59ad673d725cc599fd79f2da036e2e627769a6215761449fda234538af3d5b9f"),
        ("sample", translated, after, "9344fc48e904499111ef352b5f4940694fc3c5e00c4ea7e392228f95981e7b8d")):
        if hash_file(picture) != expected_hash:
            raise LayoutError("Screenshot does not match the inspected replay")
        m = measure(font, raw)
        box = m["ink_bbox_relative_exclusive"]
        predicted = [box[0] + 16, box[1] + 25, box[2] + 16, box[3] + 25]
        observed = white_bbox(picture, (0, 23, 180, 38))
        snapshots.append({"case": name, "measurement": m, "origin_px": [16, 25],
                          "predicted_ink_bbox_exclusive": predicted,
                          "observed_light_ink_bbox_exclusive": observed,
                          "predicted_matches_observed": predicted == observed})
    if not all(item["predicted_matches_observed"] for item in snapshots):
        raise LayoutError("Predicted title pixels differ from actual replay screenshots")
    near_row = next(r for r in rows if r["file_id"] == 38 and r["entry_index"] == 2)
    near = near_row["measurement"]
    box = near["ink_bbox_relative_exclusive"]
    predicted_near = [box[0] + 208, box[1] + 25, box[2] + 208, box[3] + 25]
    # The inspected ROI has one flat background colour, making the coloured
    # controller icon measurable without treating it as a white glyph.
    observed_near = non_background_bbox(before, (208, 25, 320, 38), bytes((0, 0, 32)))
    if predicted_near != observed_near:
        raise LayoutError("Near-edge label ink does not match the actual screenshot")
    near_row["status"] = "font1_palette0_screen_ink_verified"
    near_row["screen_validation"] = {"origin_px": [208, 25], "screen_width_from_origin_px": 112,
        "predicted_ink_bbox_exclusive": predicted_near, "observed_ink_bbox_exclusive": observed_near,
        "screen_right_margin_px": 320 - observed_near[2], "exact_window_clip_bounds": "unresolved"}
    shop = []
    if shop_screenshot is not None:
        if hash_file(shop_screenshot) != "007eaa2e5892dec08b7b863282a2db1d90ee98c1a072734f4ec5ba045e3235e7":
            raise LayoutError("Shop screenshot does not match the inspected replay")
        for entry, y in ((5, 53), (6, 67), (7, 81)):
            measured = next(r["measurement"] for r in rows if r["file_id"] == 52 and r["entry_index"] == entry)
            box = measured["ink_bbox_relative_exclusive"]
            predicted = [box[0] + 167, box[1] + y, box[2] + 167, box[3] + y]
            observed = white_bbox(shop_screenshot, (166, y, 310, y + 13))
            if predicted != observed:
                raise LayoutError(f"Shop entry {entry} predicted ink does not match its screenshot")
            shop.append({"resource_id": 52, "entry_index": entry, "origin_px": [167, y],
                         "draw_advance_px": measured["draw_max_advance_px"],
                         "predicted_ink_bbox_exclusive": predicted,
                         "observed_light_ink_bbox_exclusive": observed,
                         "predicted_matches_observed": True,
                         "game_measure_result_used_by_window": False,
                         "max_visible_screen_width_px": 153,
                         "note": "The visible 320-pixel screen is a hard limit, not a proven arbitrary window or line-wrapping bound."})
    more_contexts = []
    for name, picture, expected, entry, x, y in (
        ("empty_booster", empty_screenshot, "7d27d98e4c23e5defca9b0ea93b6cf40db3b54fea7b6c9c2b42fcfc576fb44b5", 21, 154, 113),
        ("deck_submenu", deck_screenshot, "1a00e5caf481574d4956a9418eb616cd366bc261d105090a5fbec4166cbbe058", 26, 167, 35)):
        if picture is None:
            continue
        if hash_file(picture) != expected:
            raise LayoutError(f"{name} screenshot differs from the inspected replay")
        m = next(r["measurement"] for r in rows if r["file_id"] == 52 and r["entry_index"] == entry)
        box = m["ink_bbox_relative_exclusive"]
        predicted = [box[0]+x, box[1]+y, box[2]+x, box[3]+y]
        observed = white_bbox(picture, (x-1, y, min(x+143, 320), y+13))
        if observed != predicted:
            raise LayoutError(f"{name} predicted ink differs from screenshot")
        more_contexts.append({"context": name, "resource_id": 52, "entry_index": entry,
                              "origin_px": [x,y], "draw_advance_px": m["draw_max_advance_px"],
                              "predicted_ink_bbox_exclusive": predicted, "observed_ink_bbox_exclusive": observed,
                              "predicted_matches_observed": True, "game_measure_result_used_by_window": False,
                              "max_visible_screen_width_px": 320-x, "exact_window_clip_bounds": "unresolved"})
    lab_validation = None
    if lab_run is not None:
        local = (Path(__file__).resolve().parents[1] / "local").resolve()
        lab_run = lab_run.resolve()
        if lab_run == local or not lab_run.is_relative_to(local):
            raise LayoutError("Private lab capture must remain under local/")
        image_path = lab_run / "PRIVATE_lab_chart.ppm"
        if hash_file(image_path) != "7ac991a63a66d4ed825c02cc80ab79e78a615279038e4228aa5eb623b58d62f3":
            raise LayoutError("Lab screenshot differs from the reviewed replay")
        result = json.loads((lab_run / "result.json").read_text())
        trace = json.loads((lab_run / "PRIVATE_trace.json").read_text())
        info = json.loads((lab_run / "PRIVATE_lab_window.json").read_text())
        log = (lab_run / "emulator.log").read_text(errors="replace")
        sample_cue = local / "font-recovery/run-20261107/card_album_test/DW3-card-album-test.cue"
        sample_bin = sample_cue.with_suffix(".bin")
        if (result.get("status") != "ok" or
            not any(c.get("name") == "lab_chart_observed" and c.get("stage") == 13 for c in result.get("checkpoints", [])) or
            any(s.get("type") in ("reset", "write_mem") for s in trace.get("steps", [])) or
            log.count("lab_dynamic_capture_frame=6938") != 1 or
            f"Loaded CD Image: {sample_cue}[+cue]." not in log or
            hash_file(sample_bin) != "afdb58042d8060a9523305f18a3e35e6841089d903248ba8ebd2408302b7e433"):
            raise LayoutError("Lab replay lacks the expected natural-input capture")
        if any(info.get(k) != v for k, v in {"frame": 6938, "chart_state": 1, "chart_step": 3,
            "x": 20, "y": 25, "font_pointer": 0x8004DD8C, "measured_width": 0,
            "palette": 0, "fixed": 0, "scaled": 0, "line0_is_sjis": 0, "line1_is_sjis": 0}.items()):
            raise LayoutError("Lab window is outside the inspected font-1 mode")
        line0 = (lab_run / "PRIVATE_lab_line0.bin").read_bytes()
        line1 = (lab_run / "PRIVATE_lab_line1.bin").read_bytes()
        row = source[(59, 39)]
        if (line0.hex() != row["raw_hex"] or len(line0) != info["line0_length"] + 1 or
            len(line1) != info["line1_length"] + 1 or not line1.endswith(b"\0")):
            raise LayoutError("Lab text buffers do not match their recorded source and lengths")
        measured = measure(font, line0, inserted={1: line1})
        box = measured["ink_bbox_relative_exclusive"]
        predicted = [box[0] + 20, box[1] + 25, box[2] + 20, box[3] + 25]
        observed = white_bbox(image_path, (20, 25, 160, 38))
        if predicted != observed:
            raise LayoutError("Dynamic-name prediction does not match the screenshot")
        lab_validation = {"resource_id": 59, "entry_index": 39, "source_sha256": row["raw_sha256"],
            "overlay_sha1": "c2b82222d418fbf0a101f7062499ee5719fea7b5",
            "context": "lab Digimon chart title; line 1 inserted by control 5",
            "capture_frame": 6938, "font_pointer": "0x8004DD8C", "origin_px": [20, 25],
            "captured_window_measured_width_px": 0, "game_measure_result_used_by_window": False,
            "measurement": measured, "predicted_ink_bbox_exclusive": predicted,
            "observed_light_ink_bbox_exclusive": observed, "predicted_matches_observed": True,
            "max_visible_screen_width_px": 300, "exact_window_clip_bounds": "unresolved",
            "screenshot_sha256": hash_file(image_path),
            "runtime": {"emulator": "PCSX-Redux a74a0587 build 433", "cpu": "dynarec arm64",
                "bios": "OpenBIOS", "configuration": "headless, unthrottled, copied private memory card",
                "input_cue": str(sample_cue.relative_to(local.parent)),
                "input_disc_sha256": "afdb58042d8060a9523305f18a3e35e6841089d903248ba8ebd2408302b7e433"},
            "scope": "One captured name and this window only; inserted bytes and save remain private."}
    return {"schema_version": 1, "status": "font1_listed_screen_pixel_bounds_verified_other_contexts_pending",
            "source_image_sha256": CN_IMAGE_SHA256, "font": 1, "palette": 0,
            "mode": "unscaled proportional stored non-SJIS text",
            "title_screenshot_validation": snapshots, "shop_menu_screenshot_validation": shop,
            "other_screen_validation": more_contexts, "dynamic_insert_screen_validation": lab_validation,
            "game_measure_evidence": "Static emulation of the inspected original game measurement instructions; not a live function call.",
            "real_source_samples": rows,
            "limits": ["Observed screenshot checks cover only the listed album, shop, deck and captured lab labels.",
                       "Other samples have measured advances and ink, but their actual font, palette, clip bounds, and runtime appearance are not established by this tool.",
                       "The concrete dynamic value is not an upper bound for dynamic insertion.",
                       "Other controls, wrapping, scaling, font 2/3, and centered windows fail closed or remain unresolved."],
            "general_writer_unblocked": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("image", "private_index", "prior_audit", "before_screenshot", "after_screenshot", "report"):
        p.add_argument(name, type=Path)
    p.add_argument("--shop-screenshot", type=Path)
    p.add_argument("--empty-screenshot", type=Path)
    p.add_argument("--deck-screenshot", type=Path)
    p.add_argument("--lab-run", type=Path, help="Private captured lab-chart replay under local/")
    a = p.parse_args()
    try:
        result = run(a.image, a.private_index, a.prior_audit, a.before_screenshot, a.after_screenshot,
                     a.shop_screenshot, a.empty_screenshot, a.deck_screenshot, a.lab_run)
    except (OSError, ValueError, KeyError, StopIteration, struct.error) as exc:
        result = {"schema_version": 1, "status": "blocked", "source_image_sha256": CN_IMAGE_SHA256,
                  "error_type": type(exc).__name__, "error": str(exc)[:500], "general_writer_unblocked": False}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(a.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(json.dumps({"status": result["status"], "report": str(a.report)}))
    return 2 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
