"""All fixtures are invented bytes; tests do not use ROM material."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.byte_patch import make_patch, apply_patch, validate

ROOT = Path(__file__).resolve().parents[1]


class DeltaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name)
        self.src, self.dst, self.out = (folder / name for name in ("base.img", "edit.img", "out.img"))
        self.base = bytearray(1024 * 1024 + 17)
        self.edited = self.base[:]
        for index, value in ((0, 11), (13, 12), (14, 13), (1024 * 1024 - 1, 14),
                             (1024 * 1024, 15), (len(self.edited) - 1, 16)):
            self.edited[index] = value
        self.src.write_bytes(self.base)
        self.dst.write_bytes(self.edited)

    def patch(self):
        return make_patch(self.src, self.dst)

    def test_roundtrip(self):
        patch = self.patch()
        self.assertEqual(patch["changed_bytes"], 6)
        self.assertEqual(len(patch["spans"]), 4)
        outcome = apply_patch(patch, self.src, self.out)
        self.assertEqual(outcome["sha256"], hashlib.sha256(self.edited).hexdigest())
        self.assertFalse(outcome["gameplay_verified"])
        self.assertEqual(self.out.read_bytes(), self.edited)
        self.assertEqual(self.src.read_bytes(), self.base)

    def test_source_mismatch(self):
        patch = self.patch()
        self.src.write_bytes(b"\x02" * len(self.base))
        with self.assertRaisesRegex(ValueError, "Source image"):
            apply_patch(patch, self.src, self.out)
        self.assertFalse(self.out.exists())

    def test_target_mismatch_removes_temp(self):
        patch = self.patch()
        patch["target_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "Target SHA"):
            apply_patch(patch, self.src, self.out)
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.out.parent.glob(".dw3-*")), [])

    def test_span_precondition(self):
        patch = self.patch()
        patch["spans"][0]["before_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "precondition"):
            apply_patch(patch, self.src, self.out)
        self.assertFalse(self.out.exists())

    def test_output_existing_does_not_overwrite(self):
        self.out.write_bytes(b"KEEP")
        with self.assertRaises(FileExistsError):
            apply_patch(self.patch(), self.src, self.out)
        self.assertEqual(self.out.read_bytes(), b"KEEP")

    def test_source_is_immutable(self):
        with self.assertRaisesRegex(ValueError, "overwrite"):
            apply_patch(self.patch(), self.src, self.src)
        self.assertEqual(self.src.read_bytes(), self.base)

    def test_rejects_overlapping_spans(self):
        patch = self.patch()
        patch["spans"][1]["offset"] = 0
        with self.assertRaisesRegex(ValueError, "Overlap"):
            validate(patch)

    def test_rejects_corrupt_hex(self):
        patch = self.patch()
        patch["spans"][0]["after_hex"] = "zz"
        with self.assertRaisesRegex(ValueError, "after_hex"):
            validate(patch)

    def test_rejects_negative_and_bool_offset(self):
        for val in (-1, True):
            with self.subTest(val=val):
                patch = self.patch()
                patch["spans"][0]["offset"] = val
                with self.assertRaises(ValueError):
                    validate(patch)

    def test_rejects_changed_count(self):
        patch = self.patch()
        patch["changed_bytes"] += 1
        with self.assertRaisesRegex(ValueError, "budget/count"):
            validate(patch)

    def test_rejects_large_diff(self):
        with self.assertRaisesRegex(ValueError, "safety limit"):
            make_patch(self.src, self.dst, max_bytes=3)

    def test_rejects_different_lengths(self):
        self.dst.write_bytes(b"short")
        with self.assertRaisesRegex(ValueError, "equal"):
            make_patch(self.src, self.dst)

    def test_rejects_identical(self):
        self.dst.write_bytes(self.base)
        with self.assertRaisesRegex(ValueError, "No changes"):
            make_patch(self.src, self.dst)

    def test_cli_make_apply(self):
        patch_path = self.out.parent / "delta.json"
        cli = [sys.executable, str(ROOT / "tools/byte_patch.py")]
        a = subprocess.run(cli + ["make", str(self.src), str(self.dst), str(patch_path)],
                           capture_output=True, text=True)
        self.assertEqual(a.returncode, 0, a.stderr)
        b = subprocess.run(cli + ["apply", str(patch_path), str(self.src), str(self.out)],
                           capture_output=True, text=True)
        self.assertEqual(b.returncode, 0, b.stderr)
        self.assertEqual(self.out.read_bytes(), self.edited)
        self.assertEqual(json.loads(patch_path.read_text())["changed_bytes"], 6)


if __name__ == "__main__":
    unittest.main()
