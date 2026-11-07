#!/usr/bin/env python3
"""Read-only, hash-bound font and language evidence from the inspected image.

This is a targeted investigation using the earlier index, not a fresh text
discovery scan.  Only metadata is written to the shareable JSON.  Optional
glyph previews contain game artwork and must remain in an ignored directory.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import os
from pathlib import Path
import struct
import zlib

try:
    from tools.psx_sector import SectorError, check_form1, edc32, validate_layout
    from tools.scan_text_tables import Disc, hash_file, local_image
    from tools.text_codec import CN_C713, CN_IMAGE_SHA256, cn_canonical_code, load_reviewed_glyphs, tokenize
    from tools.triage_english import roman_runs
except ModuleNotFoundError:
    from psx_sector import SectorError, check_form1, edc32, validate_layout
    from scan_text_tables import Disc, hash_file, local_image
    from text_codec import CN_C713, CN_IMAGE_SHA256, cn_canonical_code, load_reviewed_glyphs, tokenize
    from triage_english import roman_runs

ARCHIVE_SHA = "aaf88ce1aa7492e22ec83722f8cf2b8985c08fac9cc193881dabbbafc4bec94f"
EXE_SHA = "c902e59f9dc8f35a6fe2792604c485adf09a71d59c4f0769fb9654803bc9713e"
EXE_BASE = 0x8000F800
OVERLAY_BASE = 0x80082CB0
WSTAG_BASE = 0x800A5DE0
FONT_ID = 647
FONT_LBA = 294280
FONT_SECTORS = 91
GLYPH_STRIDE = 72
CANDIDATES = ((81, "亚"), (121, "古"), (1, "兽"), (26, "数"), (28, "码"),
              (48, "卡"), (71, "片"), (1082, "图"), (1913, "鉴"),
              (52, "打"), (135, "开"), (1121, "园"), (13, "有"), (345, "包"),
              (883, "买"), (17, "一"), (318, "张"), (1343, "编"),
              (1192, "辑"), (262, "组"), (34, "没"),
              (931, "称"), (291, "金"), (7, "不"), (577, "足"), (712, "钱"),
              (621, "改"), (378, "名"), (523, "选"), (756, "择"),
              (347, "已"), (877, "持"), (1496, "九"), (985, "返"), (96, "回"))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError("32-bit field outside buffer")
    return struct.unpack_from("<I", data, offset)[0]


def u16(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 2 > len(data):
        raise ValueError("16-bit field outside buffer")
    return struct.unpack_from("<H", data, offset)[0]


def instruction(data: bytes, base: int, address: int) -> int:
    if address % 4:
        raise ValueError("Unaligned MIPS instruction address")
    return u32(data, address - base)


def jump_target(data: bytes, base: int, address: int) -> int:
    op = instruction(data, base, address)
    if op >> 26 not in (2, 3):
        raise ValueError(f"Expected direct MIPS jump at {address:#x}")
    return ((address + 4) & 0xF0000000) | ((op & 0x03FFFFFF) << 2)


def immediate(data: bytes, base: int, address: int, opcode: int) -> int:
    op = instruction(data, base, address)
    if op >> 26 != opcode:
        raise ValueError(f"Unexpected MIPS instruction at {address:#x}")
    return op & 0xFFFF


def write_exclusive(path: Path, data: bytes):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out:
        out.write(data)


def unpack_rlen(data: bytes) -> tuple[bytes, int]:
    if len(data) < 8 or data[:4] != b"RLEN":
        raise ValueError("Expected an RLEN image")
    size = u32(data, 4)
    if not 0 < size <= 4_000_000:
        raise ValueError("Invalid RLEN output size")
    result = bytearray()
    pos = 8
    while True:
        if pos >= len(data):
            raise ValueError("Unterminated RLEN data")
        code = data[pos]
        pos += 1
        if code == 0:
            break
        if code < 0x80:
            if pos + code > len(data):
                raise ValueError("RLEN literal exceeds its input")
            result.extend(data[pos:pos + code])
            pos += code
        else:
            if pos >= len(data):
                raise ValueError("RLEN repeat exceeds its input")
            result.extend(bytes((data[pos],)) * (code & 0x7F))
            pos += 1
        if len(result) > size:
            raise ValueError("RLEN expands past its declared size")
    if len(result) != size:
        raise ValueError("RLEN output length does not match its declaration")
    return bytes(result), pos


def inspect_tim(data: bytes) -> dict:
    if len(data) < 20 or u32(data, 0) != 0x10:
        raise ValueError("TIM magic/header is invalid")
    flags = u32(data, 4)
    pos = 8
    blocks = []
    for kind in (("clut", "image") if flags & 8 else ("image",)):
        size = u32(data, pos)
        if size < 12 or pos + size > len(data):
            raise ValueError("TIM block exceeds image")
        x, y, width, height = struct.unpack_from("<HHHH", data, pos + 4)
        if size != 12 + width * height * 2:
            raise ValueError("TIM block dimensions do not match its size")
        blocks.append({"kind": kind, "x": x, "y": y, "width_words": width,
                       "height": height, "data_offset": pos + 12})
        pos += size
    if pos != len(data):
        raise ValueError("Unexpected bytes after TIM image")
    return {"flags": flags, "bytes": len(data), "sha256": sha(data), "blocks": blocks}


def font_images(data: bytes) -> tuple[list[dict], list[bytes], int]:
    sub = u32(data, 0)
    if sub < 4 or sub >= len(data) or sub % 4:
        raise ValueError("Invalid outer font container")
    offsets = []
    for i in range(64):
        offset = u32(data, sub + i * 4)
        if offset == 0:
            break
        offsets.append(offset)
    else:
        raise ValueError("Unterminated font TIM list")
    if not offsets or offsets[0] != (len(offsets) + 1) * 4:
        raise ValueError("Invalid font TIM list header")
    if any(a >= b for a, b in zip(offsets, offsets[1:])):
        raise ValueError("Font image offsets are not increasing")
    parsed, images = [], []
    last_end = 0
    for i, offset in enumerate(offsets):
        begin = sub + offset
        end = sub + offsets[i + 1] if i + 1 < len(offsets) else len(data)
        if begin >= end or end > len(data):
            raise ValueError("Font image extent outside resource")
        image, consumed = unpack_rlen(data[begin:end])
        info = inspect_tim(image)
        info.update({"resource_offset": begin, "compressed_bytes": consumed})
        parsed.append(info)
        images.append(image)
        last_end = begin + consumed
    return parsed, images, last_end


def filetable(exe: bytes, fid: int) -> tuple[int, int]:
    return u32(exe, 0x80044F6C - EXE_BASE + fid * 4), u16(exe, 0x800474A4 - EXE_BASE + fid * 2)


def indexed_file(disc: Disc, exe: bytes, audit: dict, ending: str) -> tuple[bytes, dict]:
    matches = [f for f in audit["executable_filetable"]
               if (f.get("directory_path") or "").upper().endswith(ending.upper())]
    if len(matches) != 1:
        raise ValueError(f"Expected one prior filetable match for {ending}")
    item = matches[0]
    if filetable(exe, item["file_id"]) != (item["lba"], item["sectors"]):
        raise ValueError("Actual executable filetable disagrees with prior audit")
    return disc.read_user(item["lba"], item["directory_size_bytes"]), item


def glyph_ink(data: bytes) -> list[int] | None:
    if len(data) != GLYPH_STRIDE:
        raise ValueError("Glyph tile is not 12 by 12 at four bits per pixel")
    pixels = []
    for y in range(12):
        for x in range(12):
            value = (data[y * 6 + x // 2] >> (4 * (x % 2))) & 0xF
            if value not in (0, 2, 15):
                raise ValueError("Unexpected glyph palette index")
            if value:
                pixels.append((x, y))
    if not pixels:
        return None
    return [min(x for x, _ in pixels), min(y for _, y in pixels),
            max(x for x, _ in pixels) + 1, max(y for _, y in pixels) + 1]


def candidate_png(glyphs: bytes) -> bytes:
    """Render local candidates, with false colours and no Unicode labels."""
    scale, tile, margin = 6, 12, 6
    width = len(CANDIDATES) * (tile * scale + margin)
    height = tile * scale + margin * 2
    pixels = bytearray()
    for y in range(height):
        pixels.append(0)  # PNG filter
        for x in range(width):
            cell, local_x = divmod(x, tile * scale + margin)
            local_y = y - margin
            colour = (110, 110, 110)
            if cell < len(CANDIDATES) and local_x < tile * scale and 0 <= local_y < tile * scale:
                index = CANDIDATES[cell][0]
                tile_data = glyphs[index * GLYPH_STRIDE:(index + 1) * GLYPH_STRIDE]
                gx, gy = local_x // scale, local_y // scale
                value = (tile_data[gy * 6 + gx // 2] >> (4 * (gx % 2))) & 15
                if value == 2:
                    colour = (0, 0, 0)
                elif value == 15:
                    colour = (255, 255, 255)
            pixels.extend(colour)
    def chunk(kind: bytes, value: bytes) -> bytes:
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(pixels), 9)) + chunk(b"IEND", b""))


def run(image: Path, index_path: Path, audit_path: Path, draft_path: Path, review_path: Path) -> tuple[dict, bytes]:
    if hash_file(image) != ARCHIVE_SHA:
        raise ValueError("Original ZIP SHA-256 differs from the required baseline")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if index.get("image_sha256") != CN_IMAGE_SHA256 or audit.get("source_image_sha256") != CN_IMAGE_SHA256:
        raise ValueError("Prior index/audit is not bound to the required image")
    boot = [f for f in audit["iso_directory_files"] if f["path"].upper() == "SLES_039.36"]
    if len(boot) != 1:
        raise ValueError("Prior audit does not identify one executable")
    with local_image(image) as unpacked:
        if hash_file(unpacked) != CN_IMAGE_SHA256:
            raise ValueError("Uncompressed image SHA-256 differs from the baseline")
        disc = Disc(unpacked)
        if (disc.sector_bytes, disc.user_offset) != (2352, 24):
            raise ValueError("Unsupported image sector layout")
        exe = disc.read_user(boot[0]["lba"], boot[0]["size_bytes"])
        if sha(exe) != EXE_SHA:
            raise ValueError("Executable SHA-256 differs from the baseline")
        if filetable(exe, FONT_ID) != (FONT_LBA, FONT_SECTORS):
            raise ValueError("Font filetable extent differs from the baseline")
        with unpacked.open("rb") as stream:
            stream.seek(FONT_LBA * 2352)
            raw = stream.read(FONT_SECTORS * 2352)
        if len(raw) != FONT_SECTORS * 2352:
            raise ValueError("Truncated font sectors")
        user = bytearray()
        failures = []
        counts = Counter()
        for i in range(FONT_SECTORS):
            sector = raw[i * 2352:(i + 1) * 2352]
            try:
                validate_layout(sector)
            except SectorError as exc:
                raise ValueError(f"Unsupported font sector {FONT_LBA + i}: {exc}") from exc
            counts["layout_passed"] += 1
            edc_ok = u32(sector, 0x818) == edc32(sector[0x10:0x818])
            ecc_ok = check_form1(sector)
            counts["edc_passed"] += edc_ok
            counts["edc_and_ecc_passed"] += ecc_ok
            if not edc_ok or not ecc_ok:
                failures.append({"lba": FONT_LBA + i, "edc_passed": edc_ok, "edc_and_ecc_passed": ecc_ok})
            user.extend(sector[24:2072])
        if failures:
            raise ValueError("Font sector EDC/ECC validation failed; resource parsing stopped")
        directory_fonts = [f for f in audit["iso_directory_files"] if f["path"].upper().endswith("/CMFONTTM.BIN")]
        if len(directory_fonts) != 1:
            raise ValueError("Expected one directory copy of CMFONTTM")
        old_info = directory_fonts[0]
        old = disc.read_user(old_info["lba"], old_info["size_bytes"])
        country, _ = indexed_file(disc, exe, audit, "/CNTY_SEL.PRO")
        select_asset, _ = indexed_file(disc, exe, audit, "/CNTSELWN.BIN")
        album, _ = indexed_file(disc, exe, audit, "/STCRDABM.PRO")
        field, _ = indexed_file(disc, exe, audit, "/FIELDSTG.PRO")
        stage, stage_info = indexed_file(disc, exe, audit, "/WSTAG230.PRO")
        candidate_file = next(f for f in index["text_files"] if f["file_id"] == 38)
        candidate = next(e for e in candidate_file["entries"] if e["index"] == 1 and e["table"] is None)
        actual = disc.read_user(candidate_file["lba"], candidate_file["file_size_bytes"])
        text = bytes.fromhex(candidate["raw_hex"])
        if sha(text) != candidate["raw_sha256"] or actual[candidate["offset_bytes"]:candidate["offset_bytes"] + len(text)] != text:
            raise ValueError("Candidate entry does not match the current image")
        talk_file = next(f for f in index["text_files"] if f["file_id"] == 234)
        talk = next(e for e in talk_file["entries"] if e["index"] == 461 and e["table"] is None)
        talk_data = disc.read_user(talk_file["lba"], talk_file["file_size_bytes"])
        talk_raw = bytes.fromhex(talk["raw_hex"])
        if sha(talk_raw) != talk["raw_sha256"] or talk_data[talk["offset_bytes"]:talk["offset_bytes"] + len(talk_raw)] != talk_raw:
            raise ValueError("Dialogue entry does not match the current image")
    new_info, new_images, new_end = font_images(bytes(user))
    old_images_info, _, _ = font_images(old)
    if len(new_info) != 2 or len(old_images_info) != 2:
        raise ValueError("Expected exactly two validated TIM images")
    if [i["sha256"] for i in new_info] != [i["sha256"] for i in old_images_info]:
        raise ValueError("Directory and filetable font TIM images differ")
    glyph_offset, glyph_count = struct.unpack_from("<II", user, len(user) - 8)
    glyph_end = glyph_offset + glyph_count * GLYPH_STRIDE
    if glyph_count != 1997 or glyph_offset < new_end or glyph_end > len(user) - 8:
        raise ValueError("Invalid appended glyph extent")
    glyphs = bytes(user[glyph_offset:glyph_end])
    reviewed = load_reviewed_glyphs(review_path)
    if any(reviewed.get(i) not in (None, ch) for i, ch in CANDIDATES):
        raise ValueError("User review conflicts with a visual candidate")
    metrics = [glyph_ink(glyphs[i * GLYPH_STRIDE:(i + 1) * GLYPH_STRIDE]) for i in range(glyph_count)]
    hooks = {"font_load": (0x8001D234, 0x80010860),
             "decode": (0x8001D370, 0x800109F4), "draw": (0x8001A610, 0x80010A20)}
    for _, (at, target) in hooks.items():
        if jump_target(exe, EXE_BASE, at) != target:
            raise ValueError(f"Expected patched jump at {at:#x}")
    if immediate(exe, EXE_BASE, 0x80010880, 9) != FONT_ID:
        raise ValueError("Patched loader does not request file 647")
    if immediate(exe, EXE_BASE, 0x800109F4, 11) != 0xF0:
        raise ValueError("Patched decoder lead threshold changed")
    if immediate(exe, EXE_BASE, 0x80010B00, 9) != 12:
        raise ValueError("Patched draw width changed")
    if [immediate(exe, EXE_BASE, at, 40) for at in (0x80010B04, 0x80010B08, 0x80010B14, 0x80010B18)] != [5, 6, 9, 10]:
        raise ValueError("Patched glyph width/height/advance fields changed")
    if (immediate(exe, EXE_BASE, 0x80010A44, 9) != 0xFF10 or
            (instruction(exe, EXE_BASE, 0x80010A48) >> 6) & 31 != 8 or
            (instruction(exe, EXE_BASE, 0x80010A4C) >> 6) & 31 != 2 or
            instruction(exe, EXE_BASE, 0x80010A50) & 63 != 0x23 or
            instruction(exe, EXE_BASE, 0x80010A54) & 63 != 0x21 or
            immediate(exe, EXE_BASE, 0x80010A58, 9) != 0xFFFC):
        raise ValueError("Patched Chinese glyph index arithmetic changed")
    lengths = list(struct.unpack_from("<11i", exe, 0x8004DDD4 - EXE_BASE))
    if lengths != [1, 2, 3, 2, 5, 3, 3, 2, 3, 2, 0]:
        raise ValueError("Actual control length table differs from inspected rules")
    choices = list(country[0x80084424 - OVERLAY_BASE:0x80084424 - OVERLAY_BASE + 7])
    if choices != [2, 3, 5, 4, 6, 1, 0]:
        raise ValueError("Actual language selection table differs from inspected choice ordering")
    if immediate(country, OVERLAY_BASE, 0x80083F18, 15) != 0x8006 or immediate(country, OVERLAY_BASE, 0x80083F20, 43) != 0xCCA8:
        raise ValueError("Language-selection state store changed")
    if sha(select_asset) != "ef0924122e801eedf024d8065514e3cc95652dda34c9116b1fe78b7c95c9601a":
        raise ValueError("Language menu artwork differs from the visually inspected asset")
    bases = [immediate(album, OVERLAY_BASE, at, 9) for at in (0x80085760, 0x80085774, 0x80085788)]
    if bases != [0x16, 0x1D, 0x24]:
        raise ValueError("Card-album resource selection changed")
    # The referenced upstream source was matched byte-for-byte against this
    # overlay.  Its window zero uses entry one, at (16,25), without measure().
    if hashlib.sha1(album).hexdigest() != "0e58e7aef70165be0bb6b95d59236941ab648b28":
        raise ValueError("Card-album overlay no longer matches the inspected window path")
    if immediate(stage, WSTAG_BASE, 0x800A5EE8, 9) != 0xE8:
        raise ValueError("Stage dialogue language offset changed")
    if u32(stage, 0x800A6208 - WSTAG_BASE + 8) != 461 or u32(stage, 0x800A66D0 - WSTAG_BASE + 4) != 0x800A6208:
        raise ValueError("Stage actor/dialogue reference changed")
    if immediate(field, OVERLAY_BASE, 0x80088D78, 35) != 0x9DC4:
        raise ValueError("Field dialogue resource lookup changed")
    tokens = tokenize(talk_raw, variant=CN_C713)
    talk_indices = [t.glyph_index if t.kind == "cn_glyph" else None for t in tokens]
    if not any(talk_indices[i:i + 3] == [81, 121, 1] for i in range(len(talk_indices) - 2)):
        raise ValueError("Dialogue does not contain the independently inspected glyph candidates")
    with draft_path.open(encoding="utf-8", newline="") as stream:
        drafts = list(csv.DictReader(stream))
    draft = next(row for row in drafts if row["context"] == "card album menu")
    runs = roman_runs(text, min_letters=2, variant=CN_C713)
    if len(runs) != 1 or runs[0].casefold() != draft["source_en"].casefold():
        raise ValueError("Album title does not match the prior local draft")
    old_tokens = tokenize(text, variant=CN_C713)
    if any(t.kind not in ("base_glyph", "extended_glyph", "terminator") for t in old_tokens):
        raise ValueError("Album title has unexpected controls or glyph kinds")
    old_width = 0
    for token in old_tokens:
        if token.kind == "terminator":
            continue
        if token.kind == "base_glyph":
            table, n = 0x8004E5A4 - EXE_BASE, token.glyph_index - 4
        else:
            table, n = 0x8004EF88 - EXE_BASE, token.glyph_index - 1
        if n < 0:
            raise ValueError("Album title contains an invalid metric index")
        record = struct.unpack_from("<BBBBBBBbbBB", exe, table + n * 11)
        old_width += record[9] + record[7]
    if old_width != 61:
        raise ValueError("Album title width differs from the inspected font metrics")
    proposed = draft["proposed_zh_cn"]
    if proposed != "卡片图鉴":
        raise ValueError("The inspected single-title draft changed")
    reviewed_characters = set(reviewed.values())
    target_reviewed = all(ch in reviewed_characters for ch in proposed)
    # Font 1's original extended-glyph width is what the actual measurement
    # function uses for a Chinese pair's trail, unless a fixed width is passed.
    font14_ext = 0x8004EF88 - EXE_BASE
    ext4 = struct.unpack_from("<BBBBBBBbbBB", exe, font14_ext + 3 * 11)
    static_width = ext4[9] + ext4[7]
    font1_ext_count = struct.unpack_from("<h", exe, 0x8004DD74 - EXE_BASE + 24 + 22)[0]
    if font1_ext_count != 114 or static_width != 7:
        raise ValueError("Font 1's inspected measurement table changed")
    trail_7d_width = 0 if 0x7D > font1_ext_count else None
    base_glyph = struct.unpack_from("<BBBBBBBbbBB", exe, 0x8004E5A4 - EXE_BASE)
    clut_x, clut_y = base_glyph[3], base_glyph[4] + 2
    palette = []
    for index_in_palette in (0, 2, 15):
        x = clut_x + index_in_palette
        page, page_x = divmod(x, 64)
        if page >= len(new_images) or clut_y >= 256:
            raise ValueError("Font palette is outside the decoded pages")
        image_info = new_info[page]["blocks"][-1]
        if image_info["width_words"] != 64 or image_info["height"] != 256:
            raise ValueError("Font page dimensions changed")
        palette.append(u16(new_images[page], image_info["data_offset"] + (clut_y * 64 + page_x) * 2))
    if palette[0] != 0 or palette[1] == 0 or palette[2] == 0:
        raise ValueError("Font 1 palette 2 cannot establish the ink bounds")
    report = {
        "schema_version": 1, "source_image_sha256": CN_IMAGE_SHA256, "source_archive_sha256": ARCHIVE_SHA,
        "executable_sha256": EXE_SHA, "status": "static_font_path_and_partial_user_review_runtime_pending",
        "language_selection": {"menu_label": "中文", "label_evidence": "Visually inspected the extracted, decoded local menu artwork; no runtime screenshot.",
                               "choice_index": 0, "language_state": choices[0], "state_address": "0x8005CCA8",
                               "actual_country_overlay_sha256": sha(country), "actual_menu_asset_sha256": sha(select_asset),
                               "card_album_file_ids": [choices[0] + n for n in bases], "runtime_verified": False},
        "font_resource": {"file_id": FONT_ID, "lba": FONT_LBA, "sector_count": FONT_SECTORS,
                          "raw_sector_sha256": sha(raw), "user_data_sha256": sha(bytes(user)),
                          "sector_checks": dict(counts), "sector_failures": failures,
                          "directory_copy_lba": old_info["lba"], "directory_copy_sha256": sha(old),
                          "decoded_tim_hashes_equal_to_directory_copy": True,
                          "tail_tim_images": new_info, "directory_tim_images": old_images_info,
                          "glyph_offset_in_user_data": glyph_offset, "glyph_count": glyph_count,
                          "glyph_stride_bytes": GLYPH_STRIDE, "glyph_array_sha256": sha(glyphs),
                          "glyph_cell_pixels": [12, 12], "pixel_encoding": "4-bit packed, low nibble first",
                          "font1_palette2_zero_transparent_and_2_15_visible": True,
                          "note": "Use the executable filetable's tail extent; it is not free space."},
        "actual_exe_paths": {k: {"hook_address": hex(at), "target": hex(target)} for k, (at, target) in hooks.items()},
        "text_rules": {"control_lengths_including_marker": lengths[:-1], "terminator": 0,
                       "chinese_lead_range": [240, 255],
                       "glyph_index_formula": "(lead - 0xF0) * 252 + trail - 4",
                       "canonical_trail_range": [4, 255],
                       "nul_handling": "The inspected text-copy path uses strlen; embedded NUL is unsafe.",
                       "type_03_in_file_text": "one-byte fallback; not a two-byte glyph"},
        "candidate_glyphs": [{"unicode_candidate": ch, "glyph_index": i, "canonical_code_hex": cn_canonical_code(i).hex(),
                              "cell": [12, 12], "advance_pixels": 12, "offset_pixels": [0, 0],
                              "ink_bbox_palette2_exclusive": metrics[i],
                              "status": "user_reviewed_visual_identity" if reviewed.get(i) == ch else "visual_candidate_not_human_reviewed"}
                             for i, ch in CANDIDATES],
        "reviewed_chinese_mapping_count": len(reviewed),
        "dialogue_static_chain": {"stage_file_id": stage_info["file_id"], "stage_overlay_sha256": sha(stage),
                                  "language_state": choices[0], "base_file_id": 0xE8, "resolved_file_id": 234,
                                  "file_lba": talk_file["lba"], "entry_index": 461,
                                  "entry_sha256": talk["raw_sha256"], "entry_contains_candidate_glyphs": True,
                                  "actor_talk_pointer": "0x800A6208", "actor_record": "0x800A66D0",
                                  "field_overlay_sha256": sha(field), "runtime_trigger_verified": False},
        "first_untranslated_candidate": {"resource_id": 38, "entry_index": 1,
                                         "source_entry_sha256": candidate["raw_sha256"],
                                         "source_bytes_including_terminator": len(text),
                                         "prior_draft_matched": True, "status": "static_english_title_runtime_pending",
                                         "overlay_sha1": hashlib.sha1(album).hexdigest(),
                                         "window_font": 1, "window_origin_pixels": [16, 25],
                                         "source_draw_advance_pixels": old_width,
                                         "proposed_draw_advance_pixels": len(proposed) * 12,
                                         "source_control_count": sum(t.kind == "control" for t in old_tokens),
                                         "all_proposed_characters_user_reviewed": target_reviewed,
                                         "window_measurement": "This matched overlay does not measure window zero; the zero-initialized title is left aligned."},
        "width_safety": {"drawn_cn_cell_width_pixels": 12, "drawn_cn_advance_pixels": 12,
                         "font1_variable_measure_example_trail_04_pixels": static_width,
                         "font1_variable_measure_example_trail_7d_pixels": trail_7d_width,
                         "reason": "The actual variable-width routine applies the old extended-glyph table/limit to the decoder result; it does not account for the Chinese draw hook.",
                         "runtime_verified": False, "safe_to_write": False},
        "gameplay_verified": False, "test_copy_created": False,
        "blockers": ["Only the explicitly reviewed glyph subset has Unicode identities; other glyphs remain unresolved.",
                     "Rendered width and the existing measurement routine disagree for Chinese glyphs.",
                     "This static tool does not perform emulator/runtime validation."] +
                    ([] if target_reviewed else ["The glyphs for the whole proposed first translation are not yet reviewed."]),
        "privacy": "No original text, glyph bitmaps, game image or resource bytes are included."
    }
    return report, glyphs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("private_index", type=Path)
    parser.add_argument("prior_audit", type=Path)
    parser.add_argument("output_report", type=Path)
    parser.add_argument("--private-preview", type=Path, help="Optional LOCAL ONLY image; never publish it")
    parser.add_argument("--draft", type=Path, default=Path("data/p1_ui_draft.csv"))
    parser.add_argument("--review", type=Path, default=Path("data/baselines/c7130c53-reviewed-glyphs.json"))
    args = parser.parse_args()
    try:
        result, glyphs = run(args.image, args.private_index, args.prior_audit, args.draft, args.review)
    except (OSError, ValueError, KeyError, StopIteration, struct.error) as exc:
        result = {"schema_version": 1, "status": "blocked", "error_type": type(exc).__name__, "error": str(exc)[:500],
                  "source_image_sha256": CN_IMAGE_SHA256, "gameplay_verified": False}
        glyphs = None
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    if glyphs is not None and args.private_preview:
        write_exclusive(args.private_preview, candidate_png(glyphs))
    write_exclusive(args.output_report, (json.dumps(result, indent=2, ensure_ascii=False) + "\n").encode())
    print(json.dumps({"status": result["status"], "report": str(args.output_report)}, ensure_ascii=False))
    return 2 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
