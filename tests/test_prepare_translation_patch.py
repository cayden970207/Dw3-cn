"""Synthetic text; tests contain no game dialogue or copyrighted glyphs."""
import hashlib
import tempfile
from pathlib import Path
import unittest

from tools.prepare_translation_patch import (
    PreparationError, encode_segment, glyph_profile, prepare, prepared_bytes,
    save_private, text_regions,
)

SHA = "3" * 64
ORIGINAL = b"\x0e\x0f\x02\x05\x01\x10\x11\x00"  # 'AB' + insert + 'CD' (synthetic)


def h(data):
    return hashlib.sha256(data).hexdigest()


def profile():
    return {"schema_version": 1, "verified_for_image_sha256": SHA,
            "mapping_reviewed": True,
            "glyphs": {"甲": "ee", "乙": "ed", "丙": "ec",
                       "A": "0e", "B": "0f", "C": "10", "D": "11"}}


def scan():
    return {"schema_version": 1, "inventory_complete": True,
            "image_sha256": SHA,
            "text_files": [{"file_id": 17, "entries": [{
                "table": None, "index": 1, "raw_hex": ORIGINAL.hex(),
                "raw_sha256": h(ORIGINAL), "encoded_bytes": len(ORIGINAL)
            }]}]}


def review():
    return {"schema_version": 1, "source_image_sha256": SHA,
            "entries": [{"file_id": 17, "table": None, "index": 1,
                         "original_sha256": h(ORIGINAL),
                         "reviewed": True, "segments": ["甲", "乙"]}]}


class BridgeTests(unittest.TestCase):
    def test_preserves_insert_code(self):
        codes = glyph_profile(profile(), SHA)
        out = prepared_bytes(ORIGINAL, ["甲", "乙"], codes)
        self.assertEqual(out, b"\xee\x02\x05\x01\xed\x00")
        self.assertEqual(text_regions(out, codes)[1], [b"\x02\x05\x01"])

    def test_partial_replacement_can_preserve_opaque_source(self):
        codes = glyph_profile(profile(), SHA)
        self.assertEqual(prepared_bytes(ORIGINAL, [None, "甲"], codes),
                         b"\x0e\x0f\x02\x05\x01\xee\x00")

    def test_manifest_addresses_are_hash_bound(self):
        out = prepare(scan(), profile(), review())
        self.assertEqual(out["changes"][0]["replacement_hex"],
                         "ee020501ed00")
        self.assertEqual(out["source_image_sha256"], SHA)

    def test_source_stale_is_rejected(self):
        rev = review()
        rev["entries"][0]["original_sha256"] = "0" * 64
        with self.assertRaisesRegex(PreparationError, "stale"):
            prepare(scan(), profile(), rev)

    def test_no_unreviewed_translations(self):
        rev = review()
        rev["entries"][0]["reviewed"] = False
        with self.assertRaisesRegex(PreparationError, "reviewed"):
            prepare(scan(), profile(), rev)

    def test_unknown_chinese_glyph_fails(self):
        rev = review()
        rev["entries"][0]["segments"] = ["未知", "乙"]
        with self.assertRaisesRegex(PreparationError, "Glyph not"):
            prepare(scan(), profile(), rev)

    def test_invalid_glyph_profile_hash_fails(self):
        p = profile()
        p["verified_for_image_sha256"] = "0" * 64
        with self.assertRaisesRegex(PreparationError, "not been tied"):
            prepare(scan(), p, review())

    def test_duplicate_unicode_mapping_fails(self):
        p = profile()
        p["glyphs"]["乙"] = "ee"
        with self.assertRaisesRegex(PreparationError, "same glyph code"):
            glyph_profile(p, SHA)

    def test_prefix_overlaps_fail(self):
        p = profile()
        p["glyphs"]["甲"] = "ee11"
        p["glyphs"]["乙"] = "ee"
        with self.assertRaisesRegex(PreparationError, "prefix"):
            glyph_profile(p, SHA)

    def test_forbid_control_byte_as_glyph(self):
        p = profile()
        p["glyphs"]["甲"] = "020501"
        with self.assertRaisesRegex(PreparationError, "reserved"):
            glyph_profile(p, SHA)

    def test_capacity_enforced(self):
        codes = glyph_profile(profile(), SHA)
        with self.assertRaisesRegex(PreparationError, "longer"):
            prepared_bytes(ORIGINAL, ["甲乙丙甲乙", "乙"], codes)

    def test_segment_count_enforced(self):
        codes = glyph_profile(profile(), SHA)
        with self.assertRaisesRegex(PreparationError, "exactly"):
            prepared_bytes(ORIGINAL, ["甲"], codes)

    def test_source_control_requires_complete_arguments(self):
        codes = glyph_profile(profile(), SHA)
        with self.assertRaisesRegex(PreparationError, "Truncated"):
            text_regions(b"\x0e\x02\x05\x00", codes)

    def test_unmodified_text_rejected(self):
        codes = glyph_profile(profile(), SHA)
        with self.assertRaisesRegex(PreparationError, "identical"):
            prepared_bytes(ORIGINAL, [None, None], codes)

    def test_end_zero_is_only_terminator(self):
        codes = glyph_profile(profile(), SHA)
        with self.assertRaisesRegex(PreparationError, "one terminating"):
            text_regions(b"\x0e\x00\x0f\x00", codes)

    def test_binary_roundtrip_with_opaque_glyph_bytes(self):
        codes = glyph_profile(profile(), SHA)
        raw = b"\xfe\xfd\x02\x02\x01\x01\x09\x00"
        regions, ctrls = text_regions(raw, codes)
        self.assertEqual(regions, [b"\xfe\xfd", b"\x01\x09"])
        self.assertEqual(ctrls, [b"\x02\x02\x01"])

    def test_one_source_entry_cannot_be_translated_twice(self):
        rev = review()
        rev["entries"].append(dict(rev["entries"][0]))
        with self.assertRaisesRegex(PreparationError, "Repeated"):
            prepare(scan(), profile(), rev)

    def test_mapping_review_is_explicit(self):
        p = profile()
        p["mapping_reviewed"] = False
        with self.assertRaisesRegex(PreparationError, "reviewed"):
            glyph_profile(p, SHA)

    def test_private_writer_will_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "local" / "patch.json"
            data = prepare(scan(), profile(), review())
            save_private(p, data)
            self.assertTrue(p.exists())
            with self.assertRaises(FileExistsError):
                save_private(p, data)


if __name__ == "__main__":
    unittest.main()
