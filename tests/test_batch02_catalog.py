"""Repository-metadata checks only; no game image, screenshot or font bytes."""
import copy
import json
from pathlib import Path
import unittest

from tools.check_batch02 import check, TARGETS

ROOT = Path(__file__).resolve().parents[1]


class Batch02CatalogTests(unittest.TestCase):
    def setUp(self):
        self.draft = json.loads((ROOT / 'data/p1_batch02_draft.json').read_text(encoding='utf-8'))
        self.prior = json.loads((ROOT / 'data/p1_batch01_reviewed.json').read_text(encoding='utf-8'))
        self.evidence = json.loads((ROOT / 'reports/SHARE_THIS_p1_batch01_report.json').read_text(encoding='utf-8'))
        # Identity-only fixture from the dated draft snapshot; these integers
        # are NOT game glyph indices and are never used to encode text.
        self.review = dict(enumerate(self.draft['review_snapshot_characters']))

    def run_check(self):
        return check(self.draft, self.prior, self.evidence, self.review)

    def test_five_nonoverlapping_targets_match_real_metadata(self):
        result = self.run_check()
        self.assertEqual({(r['file_id'], r['entry_index']) for r in result['entries']}, TARGETS)
        self.assertEqual(result['previous_entries_preserved'], 6)
        self.assertEqual(result['draft_entries'], 5)

    def test_dated_snapshot_requires_thirteen_additional_identities(self):
        result = self.run_check()
        self.assertEqual(result['required_distinct_characters'], 18)
        self.assertEqual(set(result['missing_reviewed_characters']), set('选择金钱不足已持九更改名称'))
        self.assertEqual(len(result['missing_reviewed_characters']), 13)

    def test_counts_are_conditional_not_rendering_approval(self):
        result = self.run_check()
        self.assertEqual([r['conditional_cn_advance_px'] for r in result['entries']], [48, 48, 60, 48, 48])
        self.assertEqual([r['conditional_plain_cn_bytes_with_nul'] for r in result['entries']], [9, 9, 11, 9, 9])
        self.assertFalse(result['safe_to_write'])
        self.assertEqual(result['new_playable_entries'], 0)
        self.assertFalse(result['source_image_read_this_run'])

    def test_all_identities_still_cannot_unlock_writer(self):
        chars = set(''.join(r['proposed_zh_cn'] for r in self.draft['entries']))
        self.review = dict(enumerate(sorted(chars)))
        result = self.run_check()
        self.assertEqual(result['missing_reviewed_characters'], [])
        self.assertFalse(result['safe_to_write'])
        self.assertTrue(all('actual_window_layout_pending' in r['blockers'] for r in result['entries']))

    def test_wrong_image_baseline_rejected(self):
        self.draft['source_image_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'baseline'):
            self.run_check()

    def test_wrong_source_entry_hash_rejected(self):
        self.draft['entries'][0]['original_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'Source entry hash'):
            self.run_check()

    def test_wrong_window_context_rejected(self):
        self.draft['entries'][0]['window_origin_px'] = [0, 0]
        with self.assertRaisesRegex(ValueError, 'context mismatch'):
            self.run_check()

    def test_already_completed_52_7_cannot_be_replaced(self):
        self.draft['entries'][0]['entry_index'] = 7
        with self.assertRaisesRegex(ValueError, 'already completed'):
            self.run_check()

    def test_duplicate_target_rejected(self):
        self.draft['entries'][1] = copy.deepcopy(self.draft['entries'][0])
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.run_check()

    def test_false_runtime_approval_rejected(self):
        self.draft['entries'][0]['runtime_verified'] = True
        with self.assertRaisesRegex(ValueError, 'approval'):
            self.run_check()

    def test_control_syntax_is_not_plain_ui_text(self):
        self.draft['entries'][0]['proposed_zh_cn'] = '选择\n卡包'
        with self.assertRaisesRegex(ValueError, 'plain-CJK'):
            self.run_check()

    def test_report_does_not_publish_game_bytes_or_executable_patch(self):
        output = json.dumps(self.run_check(), ensure_ascii=False)
        for field in ('raw_hex', 'replacement_hex', 'before_hex', 'after_hex'):
            self.assertNotIn(field, output)


if __name__ == '__main__':
    unittest.main()
