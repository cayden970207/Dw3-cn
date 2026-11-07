"""Synthetic only: validate safety invariants, not actual Chinese fonts or game."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.apply_text_bytes import PatchError, plan_changes, edits_by_sector, controls, apply
from tools.psx_sector import SYNC, regenerate_form1, check_form1

OLD = b"\x0e\x0f\x10\x00"
NEW = b"\x0e\x00"


def hashof(data):
    return hashlib.sha256(data).hexdigest()


def report(imagehash="a" * 64):
    return {
        "schema_version": 1, "inventory_complete": True,
        "image_sha256": imagehash, "image_size_bytes": 60 * 2352,
        "sector_bytes": 2352,
        "text_files": [{
            "file_id": 17, "lba": 50, "file_size_bytes": 2048,
            "entries": [{"table": None, "index": 1, "offset_bytes": 100,
                         "encoded_bytes": len(OLD),
                         "raw_hex": OLD.hex(), "raw_sha256": hashof(OLD)}],
        }],
    }


def manifest(imagehash="a" * 64):
    return {"schema_version": 1, "source_image_sha256": imagehash,
            "changes": [{"file_id": 17, "table": None, "index": 1,
                         "original_sha256": hashof(OLD),
                         "replacement_hex": NEW.hex()}]}


class TextPatchSafetyTests(unittest.TestCase):
    def test_plan_keeps_original_slot_size(self):
        p = plan_changes(report(), manifest())
        self.assertEqual(p[0]["padded"], b"\x0e\x00\x00\x00")
        self.assertEqual(list(edits_by_sector(p)), [50])

    def test_wrong_image_hash_fails(self):
        with self.assertRaisesRegex(PatchError, "Source image hash"):
            plan_changes(report(), manifest("0" * 64))

    def test_wrong_entry_hash_fails(self):
        m = manifest()
        m["changes"][0]["original_sha256"] = "0" * 64
        with self.assertRaisesRegex(PatchError, "Original text"):
            plan_changes(report(), m)

    def test_longer_string_rejected(self):
        m = manifest()
        m["changes"][0]["replacement_hex"] = b"TOOLONG\x00".hex()
        with self.assertRaisesRegex(PatchError, "capacity"):
            plan_changes(report(), m)

    def test_bad_terminator_rejected(self):
        m = manifest()
        m["changes"][0]["replacement_hex"] = "0e0f"
        with self.assertRaisesRegex(PatchError, "final NUL"):
            plan_changes(report(), m)

    def test_reserved_empty_index_rejected(self):
        m = manifest()
        m["changes"][0]["index"] = 0
        with self.assertRaisesRegex(PatchError, "entry zero"):
            plan_changes(report(), m)

    def test_controls_include_argument_bytes(self):
        a = b"\x0e\x02\x05\x01\x0f\x00"
        b = b"\x0e\x02\x05\x02\x0f\x00"
        self.assertNotEqual(controls(a), controls(b))

    def test_incomplete_scan_rejected(self):
        r = report()
        r["inventory_complete"] = False
        with self.assertRaisesRegex(PatchError, "incomplete"):
            plan_changes(r, manifest())

    def test_duplicate_change_rejected(self):
        m = manifest()
        m["changes"].append(dict(m["changes"][0]))
        with self.assertRaisesRegex(PatchError, "Duplicate patch"):
            plan_changes(report(), m)

    def test_slot_overlap_rejected(self):
        r = report()
        row = dict(r["text_files"][0]["entries"][0])
        row["index"], row["offset_bytes"] = 2, 102
        r["text_files"][0]["entries"].append(row)
        m = manifest()
        change = dict(m["changes"][0])
        change["index"] = 2
        m["changes"].append(change)
        with self.assertRaisesRegex(PatchError, "Overlapping"):
            plan_changes(r, m)

    def test_sector_boundary_split(self):
        p = [{"lba": 50, "offset": 2046,
              "old": OLD, "padded": b"\x0e\x00\x00\x00"}]
        edits = edits_by_sector(p)
        self.assertEqual(sorted(edits), [50, 51])
        self.assertEqual(edits[51][0][0], 0)

    def test_full_local_copy_preserves_input_and_parity(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "fake.bin"
            out = Path(folder) / "modified.bin"
            raw = bytearray(60 * 2352)
            sec = bytearray(2352)
            sec[:12] = SYNC
            sec[12:16] = b"\x01\x02\x03\x02"
            sec[16:24] = b"\x00\x00\x08\x00" * 2
            sec[24 + 100:24 + 104] = OLD
            raw[50 * 2352:51 * 2352] = regenerate_form1(bytes(sec))
            source.write_bytes(raw)
            original_hash = hashof(raw)

            def pretend_scan(path, private=False):
                self.assertTrue(private)
                content = Path(path).read_bytes()
                r = report(hashof(content))
                val = content[50 * 2352 + 24 + 100:50 * 2352 + 24 + 104]
                val = val[:val.index(0) + 1]
                row = r["text_files"][0]["entries"][0]
                row.update(raw_hex=val.hex(), encoded_bytes=len(val),
                           raw_sha256=hashof(val))
                return r

            with patch("tools.apply_text_bytes.scan", side_effect=pretend_scan):
                outcome = apply(source, manifest(original_hash), out)
            self.assertFalse(outcome["gameplay_verified"])
            self.assertEqual(hashof(source.read_bytes()), original_hash)
            self.assertTrue(out.exists())
            changed = out.read_bytes()
            self.assertEqual(changed[50 * 2352 + 24 + 100:50 * 2352 + 24 + 104],
                             b"\x0e\x00\x00\x00")
            self.assertTrue(check_form1(changed[50 * 2352:51 * 2352]))
            self.assertEqual(changed[:50 * 2352], source.read_bytes()[:50 * 2352])
            self.assertEqual(changed[51 * 2352:], source.read_bytes()[51 * 2352:])

    def test_output_collision_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            input_path = Path(folder) / "original.bin"
            input_path.write_bytes(b"original")
            with self.assertRaisesRegex(PatchError, "original"):
                apply(input_path, manifest(), input_path)
            out = Path(folder) / "exists.bin"
            out.write_bytes(b"keep")
            with self.assertRaisesRegex(PatchError, "already exists"):
                apply(input_path, manifest(), out)
            self.assertEqual(out.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
