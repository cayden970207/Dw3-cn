"""Regression tests for boundaries derived from the inspected EXE; synthetic bytes."""
import unittest

from tools.apply_text_bytes import PatchError, controls, plan_changes
from tools.prepare_translation_patch import text_regions
from tools.text_codec import (
    CN_C713, CN_IMAGE_SHA256, CodecError, cn_canonical_code, cn_glyph_index, tokenize,
)
from tools.triage_english import roman_runs


class TextCodecTests(unittest.TestCase):
    def test_chinese_glyph_index_and_canonical_endpoints(self):
        self.assertEqual(cn_canonical_code(0), b"\xf0\x04")
        self.assertEqual(cn_canonical_code(1996), b"\xf7\xec")
        self.assertEqual(cn_glyph_index(0xF1, 0x01), 249)
        with self.assertRaises(CodecError):
            cn_canonical_code(1997)

    def test_control_like_trail_byte_is_part_of_chinese_glyph(self):
        raw = b"\xf1\x02\x05\x01\x0e\x00"
        tokens = tokenize(raw, variant=CN_C713)
        self.assertEqual(tokens[0].glyph_index, 250)
        self.assertEqual(b"".join(t.raw for t in tokens), raw)
        self.assertEqual(controls(raw, variant=CN_C713), ())
        self.assertEqual(text_regions(raw, {}, variant=CN_C713)[1], [])

    def test_type_three_is_one_byte_for_disc_text(self):
        raw = b"\x03\x02\x07\x00"
        self.assertEqual([t.kind for t in tokenize(raw, variant=CN_C713)],
                         ["unknown_fallback", "control", "terminator"])
        self.assertEqual(controls(raw, variant=CN_C713), (b"\x02\x07",))
        self.assertEqual(text_regions(raw, {}, variant=CN_C713)[1], [b"\x02\x07"])

    def test_end_control_uses_actual_one_byte_length(self):
        raw = b"\x02\x00"
        tokens = tokenize(raw, variant=CN_C713)
        self.assertEqual([(t.kind, t.raw) for t in tokens],
                         [("control", b"\x02"), ("terminator", b"\x00")])

    def test_unknown_preserved_and_truncation_rejected(self):
        raw = b"\xff\xff\x01\x80\x00"
        tokens = tokenize(raw, variant=CN_C713)
        self.assertEqual([t.kind for t in tokens],
                         ["unknown_cn_glyph", "unknown_extended_glyph", "terminator"])
        self.assertEqual(b"".join(t.raw for t in tokens), raw)
        with self.assertRaisesRegex(CodecError, "Truncated Chinese"):
            tokenize(b"\xf0\x00", variant=CN_C713)
        with self.assertRaisesRegex(CodecError, "Interior NUL"):
            tokenize(b"\xf0\x00\x04\x00", variant=CN_C713)

    def test_triage_does_not_treat_pair_payload_as_a_control(self):
        raw = b"\xf1\x02\x0e\x0f\x10\x11\x12\x13\x14\x00"
        self.assertEqual(roman_runs(raw, min_letters=7, variant=CN_C713), ["ABCDEFG"])

    def test_real_image_write_remains_blocked(self):
        report = {"schema_version": 1, "inventory_complete": True, "sector_bytes": 2352,
                  "image_sha256": CN_IMAGE_SHA256}
        manifest = {"schema_version": 1, "source_image_sha256": CN_IMAGE_SHA256}
        with self.assertRaisesRegex(PatchError, "blocked"):
            plan_changes(report, manifest)


if __name__ == "__main__":
    unittest.main()
