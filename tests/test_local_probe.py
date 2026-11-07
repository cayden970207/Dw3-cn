"""One-command probe reports tested with synthetic metadata only."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.local_probe import run_probe, public_summary, REPORT_FILENAME, PRIVATE_FILENAME


FINGERPRINT = {
    "archive_sha256": "a" * 64,
    "member_name": "fake.iso",
    "uncompressed_sha256": "b" * 64,
    "uncompressed_size_bytes": 1000,
    "layout_candidates": [{"sector_size": 2352, "user_data_offset": 24}],
    "serial_candidates": ["SLES-03936"],
}
SCAN = {
    "image_sha256": "b" * 64,
    "sector_bytes": 2352,
    "user_data_offset": 24,
    "boot": {"reference_offsets_supported": False, "filetable_unmatched": [{
        "file_id": 2, "lba": 200, "filetable_sectors": 3, "iso_sectors": None,
        "reason": "lba_absent_from_iso_directory", "extent_within_image": True,
    }]},
    "inventory_complete": False,
    "total_text_files": 1,
    "total_entries": 1,
    "issues": ["Synthetic incomplete file count"],
    "warnings": ["1 executable filetable entry does not match ISO directory extents"],
    "text_files": [{
        "file_id": 17,
        "path": "AAA/DAT/COUNTRY/ENG/ESCARDGM.BIN",
        "table_count": 1,
        "entry_count": 1,
        "file_size_bytes": 16,
        "file_sha256": "c" * 64,
        "entries": [{
            "table": None,
            "index": 1,
            "raw_hex": "0e0f00",
            "raw_sha256": "d" * 64,
        }],
    }],
}


class ProbeTests(unittest.TestCase):
    def test_summary_has_no_raw_data(self):
        result = public_summary(FINGERPRINT, SCAN)
        self.assertEqual(result["structure"]["indexed_entries"], 1)
        self.assertNotIn("raw_hex", json.dumps(result))
        self.assertNotIn("0e0f00", json.dumps(result))
        self.assertEqual(result["source"]["image_sha256"], "b" * 64)
        self.assertEqual(result["structure"]["reference_exe"]["filetable_unmatched"][0]["file_id"], 2)
        self.assertEqual(result["structure"]["warnings"], SCAN["warnings"])

    def test_mismatched_hash_refused(self):
        altered = dict(SCAN, image_sha256="f" * 64)
        with self.assertRaisesRegex(ValueError, "differs"):
            public_summary(FINGERPRINT, altered)

    @patch("tools.local_probe.scan", return_value=SCAN)
    @patch("tools.local_probe._fingerprint", return_value=FINGERPRINT)
    def test_incomplete_scan_writes_both_separate_files(self, fingerprint, scan):
        with tempfile.TemporaryDirectory() as folder:
            status, report = run_probe(Path(folder) / "demo.zip", Path(folder) / "out")
            self.assertEqual(status, 1)
            public = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(public["status"], "structure_incomplete")
            self.assertNotIn("raw_hex", report.read_text(encoding="utf-8"))
            private = report.with_name(PRIVATE_FILENAME)
            self.assertEqual(json.loads(private.read_text())["text_files"][0]["entries"][0]["raw_hex"], "0e0f00")
            self.assertTrue(public["private_dump_saved_locally"])
            fingerprint.assert_called_once()
            scan.assert_called_once()

    @patch("tools.local_probe.scan", return_value=dict(SCAN, inventory_complete=True))
    @patch("tools.local_probe._fingerprint", return_value=FINGERPRINT)
    def test_structure_complete_still_not_gameplay(self, fingerprint, scan):
        with tempfile.TemporaryDirectory() as folder:
            code, report = run_probe(Path(folder) / "fake.iso", Path(folder) / "output")
            self.assertEqual(code, 0)
            obj = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(obj["status"], "structure_passed_not_gameplay_verified")
            self.assertFalse(obj["gameplay_verified"])
            self.assertEqual(obj["structure"]["warnings"], SCAN["warnings"])

    @patch("tools.local_probe._fingerprint", side_effect=ValueError("bad archive"))
    def test_fingerprint_failure_still_gives_safe_report(self, fingerprint):
        with tempfile.TemporaryDirectory() as folder:
            code, report = run_probe(Path(folder) / "demo.zip", Path(folder) / "out")
            self.assertEqual(code, 2)
            obj = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(obj["error"]["phase"], "fingerprint")
            self.assertFalse(obj["private_dump_saved_locally"])
            self.assertFalse(report.with_name(PRIVATE_FILENAME).exists())

    @patch("tools.local_probe.scan", side_effect=ValueError("invalid resource table"))
    @patch("tools.local_probe._fingerprint", return_value=FINGERPRINT)
    def test_scan_failure_preserves_fingerprint(self, fingerprint, scan):
        with tempfile.TemporaryDirectory() as folder:
            code, report = run_probe(Path(folder) / "fake.iso", Path(folder) / "out")
            self.assertEqual(code, 2)
            obj = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(obj["source"]["image_sha256"], "b" * 64)
            self.assertEqual(obj["error"]["phase"], "text_scan")
            self.assertFalse(obj["gameplay_verified"])


if __name__ == "__main__":
    unittest.main()
