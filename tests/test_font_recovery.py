"""Synthetic safety regressions for bounded font parsing and reconstruction."""
import hashlib
import struct
import unittest

from tools.font_recovery_probe import glyph_ink, inspect_tim, unpack_rlen
from tools.roundtrip_cn import inspect_resource


class FontRecoveryTests(unittest.TestCase):
    def test_rlen_checks_literal_and_output_bounds(self):
        self.assertEqual(unpack_rlen(b"RLEN" + struct.pack("<I", 3) + b"\x03abc\x00"), (b"abc", 13))
        with self.assertRaisesRegex(ValueError, "exceeds"):
            unpack_rlen(b"RLEN" + struct.pack("<I", 3) + b"\x03a")
        with self.assertRaisesRegex(ValueError, "declared size"):
            unpack_rlen(b"RLEN" + struct.pack("<I", 1) + b"\x02ab\x00")
        with self.assertRaisesRegex(ValueError, "Unterminated"):
            unpack_rlen(b"RLEN" + struct.pack("<I", 1) + b"\x01a")

    def test_tim_dimensions_must_match_block_size(self):
        valid = struct.pack("<II I HHHH H", 0x10, 2, 14, 0, 0, 1, 1, 0)
        self.assertEqual(inspect_tim(valid)["blocks"][0]["width_words"], 1)
        invalid = bytearray(valid)
        struct.pack_into("<H", invalid, 16, 2)
        with self.assertRaisesRegex(ValueError, "dimensions"):
            inspect_tim(bytes(invalid))

    def test_glyph_bounds_are_exclusive(self):
        tile = bytearray(72)
        tile[0] = 0x02
        tile[-1] = 0xF0
        self.assertEqual(glyph_ink(bytes(tile)), [0, 0, 12, 12])
        tile[0] = 3
        with self.assertRaisesRegex(ValueError, "palette"):
            glyph_ink(bytes(tile))

    def test_complete_resource_template_preserves_nontext_tail(self):
        data = struct.pack("<III", 2, 12, 13) + b"\x00\xf0\x04\x00\x7e"
        entries = []
        for index, offset, raw in ((0, 12, b"\x00"), (1, 13, b"\xf0\x04\x00")):
            entries.append({"table": None, "index": index, "offset_bytes": offset,
                            "raw_hex": raw.hex(), "encoded_bytes": len(raw),
                            "raw_sha256": hashlib.sha256(raw).hexdigest()})
        resource = {"file_id": 3, "file_sha256": hashlib.sha256(data).hexdigest(),
                    "file_size_bytes": len(data), "container": False,
                    "table_count": 1, "entries": entries}
        report = inspect_resource(data, resource)
        self.assertTrue(report["file_template_equal"])
        self.assertEqual(report["counts"]["entries_passed"], 2)
        self.assertEqual(report["entries_with_nonzero_slot_tail"], 1)
        self.assertEqual(report["nonzero_slot_tail_bytes"], 1)


if __name__ == "__main__":
    unittest.main()
