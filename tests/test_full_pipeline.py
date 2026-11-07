"""End-to-end synthetic EU disc fixture; no Nintendo/Bandai game assets.

Build a 50-resource ISO-like filesystem with a PS-X EXE filetable and one
valid CD-XA Form1 sector; run scanner -> Chinese glyph translator -> writer ->
full rescan. This is a *tool* regression, NOT an actual game test.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from tools.scan_text_tables import (
    FILETABLE_LBA_OFFSET, FILETABLE_SECTORS_OFFSET,
    FILE_COUNT, TEXT_IDS, scan,
)
from tools.psx_sector import SYNC, check_form1, regenerate_form1
from tools.prepare_translation_patch import prepare
from tools.apply_text_bytes import apply, PatchError


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def path_entry(name: bytes, extent: int, parent: int) -> bytes:
    blob = struct.pack("<BBIH", len(name), 0, extent, parent) + name
    return blob + (b"\x00" if len(name) & 1 else b"")


def directory_record(name: bytes, lba: int, size: int, is_dir=False) -> bytes:
    n = 33 + len(name)
    if n & 1:
        n += 1
    out = bytearray(n)
    out[0] = n
    struct.pack_into("<I", out, 2, lba)
    struct.pack_into("<I", out, 10, size)
    out[25] = 2 if is_dir else 0
    out[32] = len(name)
    out[33:33 + len(name)] = name
    return bytes(out)


def directory_blob(entries: list[bytes], sectors=1):
    """ISO9660 records must not cross a sector boundary."""
    data = bytearray(2048 * sectors)
    pos = 0
    for rec in entries:
        if pos % 2048 + len(rec) > 2048:
            pos = (pos // 2048 + 1) * 2048
        if pos + len(rec) > len(data):
            raise ValueError("synthetic directory full")
        data[pos:pos + len(rec)] = rec
        pos += len(rec)
    return bytes(data)


def string_table():
    # 2 entries: reserved empty entry 0, and mock one-byte ABC string.
    return struct.pack("<III", 2, 12, 16) + b"\x00" * 4 + b"\x0e\x0f\x10\x00"


def build_disc(path: Path, sector_bytes=2352):
    total_sectors = 290
    user_offset = 24 if sector_bytes == 2352 else 0
    output = bytearray(total_sectors * sector_bytes)

    def put(lba, payload):
        for i in range(0, len(payload), 2048):
            sector = lba + i // 2048
            offset = sector * sector_bytes + user_offset
            part = payload[i:i + 2048]
            output[offset:offset + len(part)] = part

    pt_spec = [
        (b"\x00", 21, 1),
        (b"AAA", 22, 1),
        (b"DAT", 23, 2),
        (b"COUNTRY", 24, 3),
        (b"ENG", 25, 4),
    ]
    pt = b"".join(path_entry(name, lba, parent) for name, lba, parent in pt_spec)
    pvd = bytearray(2048)
    pvd[:7] = b"\x01CD001\x01"
    struct.pack_into("<I", pvd, 132, len(pt))
    struct.pack_into("<I", pvd, 140, 20)
    put(16, pvd)
    put(20, pt)
    for name, lba, _ in pt_spec:
        size = 4096 if lba == 25 else 2048
        records = [directory_record(b"\x00", lba, size, is_dir=True)]
        if lba == 21:
            records.append(directory_record(b"SLES_039.36;1", 30, 262144))
        if lba == 25:
            for i, fid in enumerate(TEXT_IDS):
                name = f"ESF{i:03d}.BIN;1".encode()
                content = string_table()
                if fid >= 0x112 + 2:
                    content = struct.pack("<I", 4) + content
                records.append(directory_record(name, 200 + i, len(content)))
                put(200 + i, content)
            records.append(directory_record(b"ESFILL.BIN;1", 260, 2048))
            put(260, b"X" * 2048)
        put(lba, directory_blob(records, sectors=size // 2048))

    exe = bytearray(262144)
    exe[:8] = b"PS-X EXE"
    # All the non-text IDs point to one synthetic filler resource so the
    # reference filetable address is independently testable.
    for i in range(FILE_COUNT):
        struct.pack_into("<I", exe, FILETABLE_LBA_OFFSET + 4 * i, 260)
        struct.pack_into("<H", exe, FILETABLE_SECTORS_OFFSET + 2 * i, 1)
    for i, fid in enumerate(TEXT_IDS):
        struct.pack_into("<I", exe, FILETABLE_LBA_OFFSET + 4 * fid, 200 + i)
    put(30, exe)

    if sector_bytes == 2352:
        for i in range(total_sectors):
            start = i * sector_bytes
            sec = bytearray(output[start:start + sector_bytes])
            sec[:12] = SYNC
            sec[12:16] = bytes((1, (i // 75) & 0xff, i & 0xff, 2))
            sec[16:24] = b"\x00\x00\x08\x00" * 2
            # Only the one sector we edit needs an actual checksum; scanner
            # reads user data without parity checking all sectors.
            if i == 200:
                sec = bytearray(regenerate_form1(bytes(sec)))
            output[start:start + sector_bytes] = sec
    path.write_bytes(output)


class FullSyntheticPipeline(unittest.TestCase):
    def run_pipeline(self, raw_sector_size=2352, zipped=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "fake-disc.bin"
            dest = root / "patched.bin"
            build_disc(source, sector_bytes=raw_sector_size)
            orig_sha = sha(source.read_bytes())
            original = scan(source, private=True)
            self.assertEqual(original["total_text_files"], 50)
            self.assertEqual(original["total_entries"], 100)
            self.assertTrue(original["inventory_complete"])
            self.assertTrue(original["boot"]["reference_offsets_supported"])
            self.assertEqual(original["sector_bytes"], raw_sector_size)

            idx = original["text_files"][0]
            self.assertEqual(idx["file_id"], 3)
            self.assertEqual(idx["lba"], 200)
            entry = idx["entries"][1]
            self.assertEqual(entry["index"], 1)
            self.assertEqual(entry["offset_bytes"], 16)
            self.assertEqual(entry["raw_hex"], "0e0f1000")

            profile = {
                "schema_version": 1,
                "verified_for_image_sha256": orig_sha,
                "mapping_reviewed": True,
                "glyphs": {"甲": "ee", "乙": "ed"},
            }
            review = {
                "schema_version": 1,
                "source_image_sha256": orig_sha,
                "entries": [{
                    "file_id": 3, "table": None, "index": 1,
                    "original_sha256": entry["raw_sha256"],
                    "reviewed": True,
                    "segments": ["甲乙"],
                }],
            }
            manifest = prepare(original, profile, review)
            self.assertEqual(manifest["changes"][0]["replacement_hex"], "eeed00")
            if zipped:
                archive = root / "fake.zip"
                with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
                    z.write(source, "game/DW2003.iso")
                input_image = archive
            else:
                input_image = source
            output = apply(input_image, manifest, dest)
            self.assertEqual(output["updated_entries"], 1)
            self.assertEqual(output["touched_sectors"], 1)
            self.assertFalse(output["gameplay_verified"])
            self.assertEqual(sha(source.read_bytes()), orig_sha)
            new = scan(dest, private=True)
            self.assertTrue(new["inventory_complete"])
            self.assertNotEqual(new["image_sha256"], orig_sha)
            self.assertEqual(new["text_files"][0]["entries"][1]["raw_hex"], "eeed00")
            self.assertEqual(new["text_files"][1]["file_sha256"],
                             original["text_files"][1]["file_sha256"])
            if raw_sector_size == 2352:
                raw = dest.read_bytes()
                modified = raw[200 * 2352:(200 + 1) * 2352]
                self.assertTrue(check_form1(modified))
                before = source.read_bytes()
                self.assertEqual(raw[:200 * 2352], before[:200 * 2352])
                self.assertEqual(raw[(201 * 2352):], before[(201 * 2352):])

    def test_complete_2352_form1_pipeline(self):
        self.run_pipeline(2352, zipped=False)

    def test_complete_2048_user_data_pipeline(self):
        self.run_pipeline(2048, zipped=False)

    def test_complete_zip_to_new_image_pipeline(self):
        self.run_pipeline(2352, zipped=True)


if __name__ == "__main__":
    unittest.main()
