import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from tools.qa_text import prepare_profile, tokenize, validate_catalog

ROOT = Path(__file__).resolve().parents[1]


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads((ROOT / 'data/catalog.example.json').read_text(encoding='utf-8'))
        self.profile = json.loads((ROOT / 'data/profile.example.json').read_text(encoding='utf-8'))
        self.entry = self.catalog['entries'][0]

    def report(self):
        return validate_catalog(self.catalog, self.profile)

    def codes(self):
        return {item['code'] for item in self.report()['errors']}

    def test_fixture_exact_limits_and_pending(self):
        report = self.report()
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['status'], 'pending')
        self.assertFalse(report['gameplay_verified'])
        measured = report['measurements'][0]
        self.assertEqual(measured['width_px'], 84)
        self.assertEqual(measured['encoded_bytes_including_terminator'], 10)

    def test_missing_translation(self):
        self.entry['target'] = ''
        self.assertIn('missing_translation', self.codes())

    def test_duplicate_id(self):
        self.catalog['entries'].append(copy.deepcopy(self.entry))
        self.assertIn('duplicate_id', self.codes())

    def test_missing_glyph(self):
        self.entry['target'] += '界'
        self.assertIn('missing_glyph', self.codes())

    def test_width_overflow(self):
        self.entry['limits']['max_width_px'] = 83
        self.assertIn('width_overflow', self.codes())

    def test_byte_overflow_includes_terminator(self):
        self.entry['limits']['max_bytes'] = 9
        self.assertIn('byte_overflow', self.codes())

    def test_line_overflow(self):
        self.entry.update(source='A<1>B', target='你好<1>！')
        self.assertIn('line_overflow', self.codes())

    def test_page_resets_line_count(self):
        self.entry.update(source='A<3>B', target='你好<3>！')
        self.assertNotIn('line_overflow', self.codes())
        self.assertEqual(self.report()['errors'], [])

    def test_missing_insert_control(self):
        self.entry['target'] = self.entry['target'].replace('<5,1>', '')
        self.assertIn('control_sequence', self.codes())

    def test_reordered_controls(self):
        self.entry['target'] = '<2,2>你好<5,1>！'
        self.assertIn('control_sequence', self.codes())

    def test_unknown_control(self):
        self.entry.update(source='A<99>', target='你<99>')
        self.assertIn('unknown_control', self.codes())

    def test_malformed_control(self):
        self.entry['target'] += '<1'
        self.assertIn('malformed_control', self.codes())

    def test_unmatched_bracket(self):
        with self.assertRaises(ValueError):
            tokenize('你>')

    def test_invisible_characters(self):
        for character in ('\x00', '\t', '\n', '\u200b'):
            with self.subTest(character=repr(character)):
                self.entry['target'] = '你好' + character
                self.assertIn('invalid_character', self.codes())

    def test_non_nfc(self):
        self.entry['target'] = 'e\u0301'
        self.assertIn('normalization', self.codes())

    def test_missing_limits(self):
        del self.entry['limits']
        self.assertIn('limits', self.codes())

    def test_boolean_not_numeric_limit(self):
        self.entry['limits']['max_lines'] = True
        self.assertIn('limits', self.codes())

    def test_bad_entry_type(self):
        self.catalog['entries'].append(None)
        self.assertIn('schema', self.codes())

    def test_bad_target_type(self):
        self.entry['target'] = 123
        self.assertIn('schema', self.codes())

    def test_empty_catalog(self):
        self.catalog['entries'] = []
        self.assertIn('empty_catalog', self.codes())

    def test_runtime_evidence_required(self):
        self.entry['state'] = 'game_verified'
        self.assertIn('missing_evidence', self.codes())

    def test_metadata_cannot_prove_gameplay(self):
        self.profile.update(synthetic=False, measurement_verified=True)
        self.entry.update(state='game_verified', runtime_evidence='example-not-validated-by-this-tool')
        report = self.report()
        self.assertEqual(report['status'], 'passed_static')
        self.assertFalse(report['gameplay_verified'])

    def test_insert_width_required(self):
        del self.profile['controls']['<5,1>']['max_width_px']
        with self.assertRaises(ValueError):
            prepare_profile(self.profile)

    def test_bad_hex_rejected(self):
        self.profile['glyphs']['你']['hex'] = 'G1'
        with self.assertRaises(ValueError):
            prepare_profile(self.profile)

    def test_nul_glyph_rejected(self):
        self.profile['glyphs']['你']['hex'] = '00'
        with self.assertRaises(ValueError):
            prepare_profile(self.profile)

    def test_negative_advance_rejected(self):
        self.profile['glyphs']['你']['advance_px'] = -1
        with self.assertRaises(ValueError):
            prepare_profile(self.profile)

    def test_bad_catalog_schema(self):
        with self.assertRaises(ValueError):
            validate_catalog([], self.profile)

    def test_cli_default_and_strict(self):
        command = [sys.executable, str(ROOT / 'tools/qa_text.py'), str(ROOT / 'data/catalog.example.json'), '--profile', str(ROOT / 'data/profile.example.json')]
        for extra, code in (([], 0), (['--strict'], 1)):
            with self.subTest(extra=extra):
                result = subprocess.run(command + extra, capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertFalse(json.loads(result.stdout)['gameplay_verified'])


if __name__ == '__main__':
    unittest.main()
