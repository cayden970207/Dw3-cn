"""Safety checks for local sample publication and replay evidence."""
import tempfile
import unittest
from pathlib import Path

from tools.collect_runtime_evidence import pixel_diff
from tools.make_card_album_sample import exclusive_write, make_sample


class SampleSafetyTests(unittest.TestCase):
    def test_exclusive_write_never_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "existing"
            target.write_bytes(b"keep")
            with self.assertRaises(FileExistsError):
                exclusive_write(target, b"new")
            self.assertEqual(target.read_bytes(), b"keep")

    def test_sample_rejects_output_outside_private_tree_before_reading_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            with self.assertRaisesRegex(ValueError, "under the ignored local"):
                make_sample(missing, missing, missing, missing, missing, Path(directory) / "out")

    def test_pixel_comparison_checks_scope_without_claiming_ocr(self):
        before = bytes(320 * 240 * 3)
        after = bytearray(before)
        after[(26 * 320 + 17) * 3] = 255
        report = pixel_diff(before, bytes(after))
        self.assertEqual(report["changed_pixels"], 1)
        self.assertEqual(report["bbox_exclusive"], [17, 26, 18, 27])
        self.assertEqual(report["changes_outside_title_window"], 0)
        with self.assertRaisesRegex(ValueError, "do not differ"):
            pixel_diff(before, before)


if __name__ == "__main__":
    unittest.main()
