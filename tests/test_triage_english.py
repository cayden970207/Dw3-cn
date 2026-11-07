"""No game bytes: tests use only synthetic glyph encoding and identifiers."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.triage_english import TriageError, roman_runs, triage, write_private


def fake_text(txt):
    out = bytearray()
    for c in txt:
        if c == " ":
            out.extend((1, 1))
        elif "A" <= c <= "Z":
            out.append(14 + ord(c) - ord("A"))
        elif "a" <= c <= "z":
            out.append(40 + ord(c) - ord("a"))
        elif "0" <= c <= "9":
            out.append(4 + ord(c) - ord("0"))
        else:
            raise ValueError("Unsupported fixture character")
    out.append(0)
    return bytes(out)


def fixture(text):
    raw = fake_text(text)
    return {
        "schema_version": 1,
        "image_sha256": "3" * 64,
        "text_files": [{
            "file_id": 0x11,
            "path": "AAA/DAT/COUNTRY/ENG/ESCARDGM.BIN",
            "entries": [{
                "table": None,
                "index": 1,
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "encoded_bytes": len(raw),
                "raw_hex": raw.hex(),
            }],
        }],
    }


class EnglishTriageTests(unittest.TestCase):
    def test_only_flags_review_candidates(self):
        result = triage(fixture("Card Album"))
        self.assertEqual(result["candidate_count"], 1)
        self.assertTrue(result["all_candidates_need_review"])
        self.assertFalse(result["gameplay_verified"])
        self.assertNotIn("preview", result["candidates"][0])

    def test_preview_is_opt_in(self):
        result = triage(fixture("Card Album"), previews=True)
        self.assertEqual(result["candidates"][0]["preview"], ["Card Album"])

    def test_small_names_not_automatically_untranslated(self):
        self.assertEqual(triage(fixture("Jun"))["candidate_count"], 0)
        self.assertEqual(triage(fixture("1234567890"))["candidate_count"], 0)

    def test_control_argument_is_never_read_as_english(self):
        raw = b"\x02\x02\x01" + fake_text("Card Album")
        self.assertEqual(roman_runs(raw), ["Card Album"])
        with self.assertRaisesRegex(TriageError, "Truncated control"):
            roman_runs(b"\x02\x02\x00")

    def test_ext_glyph_is_not_decoded_as_latin(self):
        raw = fake_text("Card")[:-1] + b"\x01\x33" + fake_text("Album")
        self.assertEqual(roman_runs(raw), [])

    def test_missing_raw_dump_is_rejected(self):
        data = fixture("Card Album")
        del data["text_files"][0]["entries"][0]["raw_hex"]
        with self.assertRaisesRegex(TriageError, "PRIVATE raw_hex"):
            triage(data)

    def test_broken_metadata_rejected(self):
        data = fixture("Card Album")
        data["text_files"][0]["entries"][0]["raw_sha256"] = "0" * 64
        with self.assertRaisesRegex(TriageError, "hash mismatch"):
            triage(data)

    def test_duplicate_id_is_rejected(self):
        data = fixture("Card Album")
        data["text_files"][0]["entries"].append(dict(data["text_files"][0]["entries"][0]))
        with self.assertRaisesRegex(TriageError, "Duplicate"):
            triage(data)

    def test_unknown_control_marks_error_not_english(self):
        data = fixture("Card Album")
        raw = b"\x02\xFE\x0E\x0F\x00"
        e = data["text_files"][0]["entries"][0]
        e.update(raw_hex=raw.hex(), raw_sha256=hashlib.sha256(raw).hexdigest(),
                 encoded_bytes=len(raw))
        result = triage(data)
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["decode_error_count"], 1)

    def test_local_queue_creation_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "local" / "queue.json"
            data = triage(fixture("Card Album"), previews=True)
            write_private(path, data)
            self.assertTrue(path.exists())
            self.assertIn("Card Album", path.read_text(encoding="utf-8"))
            with self.assertRaises(FileExistsError):
                write_private(path, data)


if __name__ == "__main__":
    unittest.main()
