"""ZIP inspector tests use entirely artificial byte streams."""
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from tools.inspect_zip import inspect_zip

ROOT = Path(__file__).resolve().parents[1]


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.archive = self.folder / "sample.zip"
        self.image = bytearray(17 * 2352)
        self.image[16 * 2352 + 24:16 * 2352 + 31] = b"\x01CD001\x01"
        self.image[100:118] = b"SLES_039.36;1 test"
        self.image = bytes(self.image)

    def write(self, files):
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as out:
            for name, raw in files.items():
                out.writestr(name, raw)

    def test_fingerprint_and_layout(self):
        self.write({"game/DW2003.iso": self.image})
        result = inspect_zip(self.archive)
        self.assertEqual(result["member_name"], "game/DW2003.iso")
        self.assertEqual(result["uncompressed_sha256"], hashlib.sha256(self.image).hexdigest())
        self.assertEqual(result["archive_sha256"], hashlib.sha256(self.archive.read_bytes()).hexdigest())
        self.assertEqual(result["serial_candidates"], ["SLES-03936"])
        self.assertEqual(result["layout_candidates"],
                         [{"sector_size": 2352, "user_data_offset": 24}])
        self.assertFalse(result["patch_compatible"])
        self.assertFalse(result["boot_file_verified"])

    def test_does_not_write_image_to_disk(self):
        self.write({"game/DW2003.iso": self.image})
        inspect_zip(self.archive)
        self.assertEqual(sorted(x.name for x in self.folder.iterdir()), ["sample.zip"])

    def test_requires_member_when_multiple_images(self):
        self.write({"a.bin": self.image, "b.iso": b"small"})
        with self.assertRaisesRegex(ValueError, "Multiple"):
            inspect_zip(self.archive)
        self.assertEqual(inspect_zip(self.archive, member="a.bin")["uncompressed_size_bytes"], len(self.image))

    def test_rejects_oversized_member(self):
        self.write({"game.iso": self.image})
        with self.assertRaisesRegex(ValueError, "size limit"):
            inspect_zip(self.archive, max_image_bytes=100)

    def test_rejects_no_game_image(self):
        self.write({"README.txt": b"text"})
        with self.assertRaisesRegex(ValueError, "No ISO"):
            inspect_zip(self.archive)

    def test_nonzip_rejected(self):
        self.archive.write_bytes(b"not a zip")
        with self.assertRaises(zipfile.BadZipFile):
            inspect_zip(self.archive)

    def test_cli_expected_hash(self):
        self.write({"game.iso": self.image})
        cmd = [sys.executable, str(ROOT / "tools/inspect_zip.py"), str(self.archive), "--expect"]
        sha = hashlib.sha256(self.image).hexdigest()
        for expected, status in ((sha, 0), ("0" * 64, 1), ("wrong", 2)):
            with self.subTest(expected=expected):
                proc = subprocess.run(cmd + [expected], capture_output=True, text=True)
                self.assertEqual(proc.returncode, status, proc.stderr)


if __name__ == "__main__":
    unittest.main()
