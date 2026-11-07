"""Tests for the real batch-01 save regression and its no-injection boundary."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from tools.batch01_save_regression import traces
from tools.collect_batch01_save_regression import run
from tools.collect_batch02_runtime import run as collect_batch02
from tools.make_card_menu_batch02 import check_diff, run as run_batch02, TARGETS
from tools.font_recovery_probe import ARCHIVE_SHA
from tools.text_codec import CN_IMAGE_SHA256

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / 'local/reference/dw2003recomp'
LOCAL = ROOT / 'local/font-recovery'
DIRECTORY = LOCAL / 'batch01-postmenu-save-v1'
IMAGE = LOCAL / 'p1-batch01/DW3-p1-batch01.bin'
SEED = LOCAL / 'round2-album-regression-v4/first/memcard1.mcd'
SECOND = LOCAL / 'p1-batch02-money-save-v1'
SECOND_IMAGE = LOCAL / 'p1-batch02-money-v1/DW3-p1-batch02.bin'
SECOND_SEED = LOCAL / 'batch01-postmenu-save-v1/first/memcard1.mcd'


class BoundaryTests(unittest.TestCase):
    def test_visual_attestation_is_required_before_reading_evidence(self):
        with self.assertRaisesRegex(ValueError, 'Inspect'):
            run(Path('/unavailable'), Path('/unavailable'), Path('/unavailable'), {}, False)
        with self.assertRaisesRegex(ValueError, 'Inspect'):
            collect_batch02(*([Path('/unavailable')] * 11), False)

    def test_disc_diff_rejects_unapproved_bytes(self):
        base = 2313 * 2352
        good = {'spans': [{'offset': base + 100, 'after_hex': '0102'}]}
        check_diff(good, [(base + 100, base + 102)], {2313})
        bad = {'spans': [{'offset': base + 99, 'after_hex': '0102'}]}
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            check_diff(bad, [(base + 100, base + 102)], {2313})

    def test_missing_glyph_review_prevents_copy_creation(self):
        target, _, _, _, digest = TARGETS[25]
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            for name in ('index.json', 'audit.json'):
                (temp / name).write_text('{}')
            (temp / 'selected.json').write_text(json.dumps({'schema_version': 1,
                'source_image_sha256': CN_IMAGE_SHA256, 'entries': [{'file_id': 52, 'table': None,
                'entry_index': 25, 'target': target, 'original_sha256': digest, 'translation_reviewed': True}]}))
            output = ROOT / 'local' / ('never-created-' + uuid.uuid4().hex)
            with patch('tools.make_card_menu_batch02.hash_file', return_value=ARCHIVE_SHA), \
                 patch('tools.make_card_menu_batch02.load_reviewed_glyphs', return_value={48: '卡'}):
                with self.assertRaisesRegex(ValueError, 'glyph identity is incomplete'):
                    run_batch02(temp / 'source.zip', temp / 'index.json', temp / 'audit.json', temp / 'glyphs.json',
                                temp / 'p1.json', temp / 'selected.json', output, {}, {})
            self.assertFalse(output.exists())

    @unittest.skipUnless((REF / 'tests/replay/scripts/first_battle_save.json').is_file(), 'Pinned private reference unavailable')
    def test_save_follows_real_menu_visits_and_cold_phase_never_writes(self):
        first, cold = traces(REF)
        for trace in (first, cold):
            self.assertFalse(any(s.get('type') in ('reset', 'write_mem') for s in trace['steps']))
        names = [s.get('name') for s in first['steps'] if s.get('type') == 'checkpoint']
        self.assertLess(names.index('batch_shop_return'), names.index('post_album_saved'))
        self.assertFalse(any(s.get('name') == 'post_album_saved' for s in cold['steps']))

    @unittest.skipUnless(all(p.is_file() for p in (DIRECTORY / 'first/result.json', DIRECTORY / 'cold/result.json', IMAGE, SEED)),
                         'Owner-only emulator regression evidence unavailable')
    def test_real_cold_load_and_revisited_screen_masks(self):
        prior = json.loads((ROOT / 'reports/SHARE_THIS_p1_batch01_report.json').read_text())
        report = run(DIRECTORY, IMAGE, SEED, prior, True)
        self.assertTrue(report['cold_process_loaded_post_menu_save'])
        self.assertGreater(report['playtime_seconds']['cold_reloaded'], report['playtime_seconds']['initial_load'])
        self.assertTrue(all(x['light_pixel_mask_matches_prior'] for x in report['phases']['cold']['captures'].values()))

    @unittest.skipUnless(all(p.is_file() for p in (SECOND / 'first/result.json', SECOND / 'cold/result.json', SECOND_IMAGE, SECOND_SEED)),
                         'Owner-only batch-02 emulator evidence unavailable')
    def test_real_second_batch_save_and_revisited_insufficient_funds(self):
        prior = json.loads((ROOT / 'reports/SHARE_THIS_p1_batch01_report.json').read_text())
        batch02 = json.loads((ROOT / 'reports/SHARE_THIS_p1_batch02_report.json').read_text())
        report = run(SECOND, SECOND_IMAGE, SECOND_SEED, prior, True, batch02)
        self.assertEqual(report['status'], 'batch02_post_menu_save_cold_load_revisit_verified')
        self.assertTrue(report['phases']['cold']['captures']['insufficient_funds']['light_pixel_mask_matches_prior'])
        self.assertGreater(report['playtime_seconds']['cold_reloaded'], report['playtime_seconds']['initial_load'])


if __name__ == '__main__':
    unittest.main()
