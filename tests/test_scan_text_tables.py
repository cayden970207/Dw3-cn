"""Synthetic ISO9660 + DW2003 counted-table tests; no game assets used."""
from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from tools.scan_text_tables import (
    Disc, DiscError, FILETABLE_LBA_OFFSET, FILETABLE_SECTORS_OFFSET,
    get_filetable, iso9660_files, local_image, read_text_file, scan,
    table_entries, write_private,
)


def path_entry(name: bytes, extent: int, parent: int) -> bytes:
    out = struct.pack("<BBIH", len(name), 0, extent, parent) + name
    return out + (b"\x00" if len(name) % 2 else b"")


def dir_record(name: bytes, lba: int, size: int, directory: bool = False) -> bytes:
    n = 33 + len(name)
    if n % 2:
        n += 1
    out = bytearray(n)
    out[0] = n
    struct.pack_into("<I", out, 2, lba)
    struct.pack_into("<I", out, 10, size)
    out[25] = 2 if directory else 0
    out[32] = len(name)
    out[33:33 + len(name)] = name
    return bytes(out)


def counted_table() -> bytes:
    # Two 4-aligned entries: empty entry 0, then two synthetic 1-byte glyphs.
    return struct.pack("<III", 2, 12, 16) + b"\x00" * 4 + b"\x0E\x0F\x00"


def synthetic_image(path: Path, sector_bytes=2352, user_offset=24) -> None:
    """A minimal fake ISO with a hidden AAA path, a PS-X EXE, one text table."""
    data = bytearray(270 * sector_bytes)
    def put(lba, blob):
        for start in range(0, len(blob), 2048):
            off = (lba + start // 2048) * sector_bytes + user_offset
            data[off:off + len(blob[start:start + 2048])] = blob[start:start + 2048]
    paths = [
        (b"\x00", 21, 1),
        (b"AAA", 22, 1),
        (b"DAT", 23, 2),
        (b"COUNTRY", 24, 3),
        (b"ENG", 25, 4),
    ]
    pt = b"".join(path_entry(*item) for item in paths)
    pvd = bytearray(2048)
    pvd[:7] = b"\x01CD001\x01"
    struct.pack_into("<I", pvd, 132, len(pt))
    struct.pack_into("<I", pvd, 140, 20)
    put(16, pvd)
    put(20, pt)
    for name, lba, _ in paths:
        records = dir_record(b"\x00", lba, 2048, directory=True)
        # The AAA hierarchy does *not* appear as entries in its parent dirs,
        # reproducing a path-table-only directory traversal case.
        if lba == 21:
            records += dir_record(b"SLES_039.36;1", 30, 0x40000)
        if lba == 25:
            records += dir_record(b"ESCARDGM.BIN;1", 200, len(counted_table()))
        put(lba, records)
    exe = bytearray(0x40000)
    exe[:8] = b"PS-X EXE"
    struct.pack_into("<I", exe, FILETABLE_LBA_OFFSET + 0x11 * 4, 200)
    struct.pack_into("<H", exe, FILETABLE_SECTORS_OFFSET + 0x11 * 2, 1)
    put(30, exe)
    put(200, counted_table())
    path.write_bytes(data)


class TextTableTests(unittest.TestCase):
    def test_counts_offsets_and_nul(self):
        table = counted_table()
        self.assertEqual(table_entries(table), [(12, 13), (16, 19)])
        self.assertEqual(table[table_entries(table)[1][0]:], b"\x0e\x0f\x00")

    def test_container_subtables(self):
        blob = struct.pack("<I", 4) + counted_table()
        self.assertEqual(read_text_file(blob, True), [(0, [(16, 17), (20, 23)])])

    def test_bad_headers_fail_closed(self):
        with self.assertRaises(DiscError):
            table_entries(b"")
        with self.assertRaisesRegex(DiscError, "First string"):
            table_entries(struct.pack("<III", 2, 8, 12) + b"\x00" * 5)
        with self.assertRaisesRegex(DiscError, "not terminated"):
            table_entries(struct.pack("<II", 1, 8) + b"\x0e")
        with self.assertRaisesRegex(DiscError, "Invalid container"):
            read_text_file(b"\x00" * 16, True)

    def test_duplicate_or_overlapping_string_offsets(self):
        blob = struct.pack("<III", 2, 12, 12) + b"\x00" * 6
        with self.assertRaisesRegex(DiscError, "not strictly"):
            table_entries(blob)


class DiscScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.image = self.folder / "fake-image.bin"
        synthetic_image(self.image)

    def test_raw_mode2_detection_and_hidden_path(self):
        disc = Disc(self.image)
        self.assertEqual((disc.sector_bytes, disc.user_offset), (2352, 24))
        files = iso9660_files(disc)
        self.assertEqual(files[200]["path"], "AAA/DAT/COUNTRY/ENG/ESCARDGM.BIN")
        self.assertEqual(files[30]["path"], "SLES_039.36")
        info = get_filetable(disc, files)
        self.assertEqual(info["filetable_matches"], 1)
        self.assertFalse(info["reference_offsets_supported"])

    def test_fingerprinted_partial_catalog(self):
        result = scan(self.image)
        self.assertEqual(result["image_sha256"], hashlib.sha256(self.image.read_bytes()).hexdigest())
        self.assertEqual(result["total_text_files"], 1)
        self.assertEqual(result["total_entries"], 2)
        self.assertFalse(result["inventory_complete"])
        self.assertFalse(result["gameplay_verified"])
        self.assertNotIn("raw_hex", str(result))
        row = result["text_files"][0]
        self.assertEqual(row["file_id"], 0x11)
        self.assertEqual(row["entry_count"], 2)
        self.assertEqual(row["entries"][1]["encoded_bytes"], 3)

    def test_private_dump_stays_separate(self):
        result = scan(self.image, private=True)
        self.assertEqual(result["text_files"][0]["entries"][1]["raw_hex"], "0e0f00")
        path = self.folder / "private.json"
        write_private(path, result)
        self.assertIn('"raw_hex": "0e0f00"', path.read_text(encoding="utf-8"))
        with self.assertRaises(FileExistsError):
            write_private(path, result)

    def test_zip_member_is_temporary(self):
        zip_path = self.folder / "disc.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(self.image, arcname="../sneaky/Game.iso")
        result = scan(zip_path)
        self.assertEqual(result["total_entries"], 2)
        self.assertEqual(result["image_sha256"], hashlib.sha256(self.image.read_bytes()).hexdigest())
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()),
                         ["disc.zip", "fake-image.bin"])
        with local_image(zip_path) as image:
            self.assertTrue(image.exists())
            self.assertEqual(image.name, "user-disc.img")
        self.assertFalse(image.exists())

    def test_zip_requires_unique_member(self):
        zip_path = self.folder / "two.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.write(self.image, "one.bin")
            archive.write(self.image, "two.iso")
        with self.assertRaisesRegex(DiscError, "exactly one"):
            with local_image(zip_path):
                pass

    def test_mode1_2048_image(self):
        path = self.folder / "fake-2048.iso"
        synthetic_image(path, sector_bytes=2048, user_offset=0)
        disc = Disc(path)
        self.assertEqual((disc.sector_bytes, disc.user_offset), (2048, 0))
        self.assertEqual(scan(path)["total_entries"], 2)

    def test_corrupt_descriptor_fails(self):
        with self.image.open("r+b") as fp:
            fp.seek(16 * 2352 + 24)
            fp.write(b"\xFF")
        with self.assertRaisesRegex(DiscError, "descriptor"):
            scan(self.image)

    def test_bad_table_does_not_create_false_success(self):
        with self.image.open("r+b") as fp:
            fp.seek(200 * 2352 + 24)
            fp.write(b"\x00" * 16)
        result = scan(self.image)
        self.assertEqual(result["total_text_files"], 0)
        self.assertFalse(result["inventory_complete"])
        self.assertTrue(any("ID 0x11" in item for item in result["issues"]))


if __name__ == "__main__":
    unittest.main()
