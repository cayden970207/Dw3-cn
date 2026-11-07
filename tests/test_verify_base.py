import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.verify_base import inspect_image

ROOT = Path(__file__).resolve().parents[1]


class ImageTests(unittest.TestCase):
    def inspect_bytes(self, data):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'synthetic.img'
            path.write_bytes(data)
            result = inspect_image(path)
            self.assertEqual(path.read_bytes(), data, 'Inspection must not modify input')
            return result

    def test_fingerprint_and_no_false_compatibility(self):
        data = b'only synthetic bytes'
        result = self.inspect_bytes(data)
        self.assertEqual(result['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(result['size_bytes'], len(data))
        self.assertEqual(result['layout_candidates'], [])
        self.assertFalse(result['patch_compatible'])
        self.assertFalse(result['boot_file_verified'])

    def test_layout_candidates(self):
        for sector_size, offset in ((2048, 0), (2352, 16), (2352, 24)):
            with self.subTest(sector_size=sector_size, offset=offset):
                data = bytearray(17 * sector_size)
                position = 16 * sector_size + offset
                data[position:position + 7] = b'\x01CD001\x01'
                result = self.inspect_bytes(bytes(data))
                self.assertEqual(result['layout_candidates'], [{'sector_size': sector_size, 'user_data_offset': offset}])

    def test_serial_is_only_candidate(self):
        result = self.inspect_bytes(b'BOOT = cdrom:\\SLES_039.36;1\r\n')
        self.assertEqual(result['serial_candidates'], ['SLES-03936'])
        self.assertFalse(result['boot_file_verified'])

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            self.inspect_bytes(b'')

    def test_archives_rejected(self):
        for prefix in (b'PK\x03\x04', b'7z\xbc\xaf\x27\x1c', b'Rar!', b'MComprHD'):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                self.inspect_bytes(prefix + b'fixture')

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(OSError):
            inspect_image(Path(folder) / 'missing')

    def test_cli_expected_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sample.img'
            path.write_bytes(b'synthetic')
            command = [sys.executable, str(ROOT / 'tools/verify_base.py'), str(path), '--expect']
            digest = hashlib.sha256(b'synthetic').hexdigest()
            for value, code in ((digest, 0), ('0' * 64, 1), ('bad-hash', 2)):
                with self.subTest(value=value):
                    result = subprocess.run(command + [value], capture_output=True, text=True)
                    self.assertEqual(result.returncode, code, result.stderr)


if __name__ == '__main__':
    unittest.main()
