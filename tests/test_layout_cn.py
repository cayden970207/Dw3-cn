"""Safety and optional real-image checks for the narrowly scoped layout model."""
import json
import unittest
from pathlib import Path

from tools.layout_cn import Font1, LayoutError, measure, white_bbox, neutral_ink_bbox
from tools.text_codec import cn_canonical_code, load_reviewed_glyphs

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / 'local/font-recovery/run-20261107'
INDEX = ROOT / 'local/dw3-probe/20261107T092435Z-03a090/PRIVATE_DO_NOT_UPLOAD.json'
EXE = LOCAL / 'PRIVATE_executable.bin'
FONT = LOCAL / 'PRIVATE_647_userdata.bin'
SCREEN_BEFORE = LOCAL / 'runtime-album-before-long/run/PRIVATE_album_wait_360.ppm'
SCREEN_AFTER = LOCAL / 'runtime-album-after/run/PRIVATE_card_album_after.ppm'
LAB = ROOT / 'local/font-recovery/round2-lab-dynamic-runtime-v2'
SELECTION = ROOT / 'local/font-recovery/batch02-deck-before-v1/PRIVATE_deck_selection.ppm'
MONEY = ROOT / 'local/font-recovery/p1-batch02-money-buy-runtime-v1/PRIVATE_insufficient_funds.ppm'
EXPANDED_DECK = ROOT / 'local/font-recovery/p1-batch02-expanded-deck-runtime-v1'


class MappingSafetyTests(unittest.TestCase):
    def test_reviewed_subset_has_consistent_unique_canonical_codes(self):
        glyphs = load_reviewed_glyphs(ROOT / 'data/baselines/c7130c53-reviewed-glyphs.json')
        self.assertEqual(glyphs[345], '包')
        self.assertEqual(cn_canonical_code(345).hex(), 'f161')
        self.assertEqual(len(set(glyphs.values())), len(glyphs))


@unittest.skipUnless(all(p.is_file() for p in (INDEX, EXE, FONT, SCREEN_BEFORE, SCREEN_AFTER)),
                     'Owner-only real image evidence is unavailable')
class LocalRealImageLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.font = Font1(EXE.read_bytes(), FONT.read_bytes())
        cls.index = json.loads(INDEX.read_text())

    def source(self, fid, entry):
        f = next(x for x in self.index['text_files'] if x['file_id'] == fid)
        return bytes.fromhex(next(x['raw_hex'] for x in f['entries'] if x['index'] == entry and x['table'] is None))

    def test_real_title_pixel_bounds_and_distinct_game_measure(self):
        old = measure(self.font, self.source(38, 1))
        reviewed = load_reviewed_glyphs(ROOT / 'data/baselines/c7130c53-reviewed-glyphs.json')
        codes = {v: cn_canonical_code(k) for k, v in reviewed.items()}
        new = measure(self.font, b''.join(codes[ch] for ch in '卡片图鉴') + b'\0')
        self.assertEqual((old['draw_max_advance_px'], new['draw_max_advance_px'], new['game_measure_max_px']), (61, 48, 22))
        self.assertEqual(white_bbox(SCREEN_BEFORE, (0, 23, 180, 38)), [17, 26, 77, 36])
        self.assertEqual(white_bbox(SCREEN_AFTER, (0, 23, 180, 38)), [16, 25, 63, 36])
        self.assertEqual(new['ink_bbox_relative_exclusive'], [0, 0, 47, 11])

    def test_real_dynamic_insert_requires_explicit_value(self):
        source = self.source(52, 12)
        with self.assertRaisesRegex(LayoutError, 'Inserted line'):
            measure(self.font, source)
        digits = bytes([5, 4, 4, 4, 4, 0])  # concrete 10000; not a bound on all values
        result = measure(self.font, source, inserted={1: digits})
        self.assertEqual(result['draw_max_advance_px'], 59)
        self.assertEqual(result['operations']['dynamic_line_insert'], 1)
        self.assertEqual(result['window_fit'], 'unresolved_without_verified_context')

    def test_real_explicit_newline_does_not_become_automatic_wrap(self):
        result = measure(self.font, self.source(3, 14))
        self.assertEqual(result['draw_advance_by_line_px'], [48, 48])
        self.assertEqual(result['operations']['newline'], 1)
        with self.assertRaisesRegex(LayoutError, 'page boundary'):
            measure(self.font, self.source(3, 14), page_lines=1)

    @unittest.skipUnless(all((LAB / name).is_file() for name in
                         ('PRIVATE_lab_line0.bin', 'PRIVATE_lab_line1.bin', 'PRIVATE_lab_chart.ppm')),
                         'Owner-only dynamic-name capture is unavailable')
    def test_captured_dynamic_name_matches_actual_screen_ink(self):
        line0 = (LAB / 'PRIVATE_lab_line0.bin').read_bytes()
        line1 = (LAB / 'PRIVATE_lab_line1.bin').read_bytes()
        self.assertEqual(line0, self.source(59, 39))
        measured = measure(self.font, line0, inserted={1: line1})
        self.assertEqual((measured['draw_max_advance_px'], measured['game_measure_max_px']), (72, 44))
        box = measured['ink_bbox_relative_exclusive']
        self.assertEqual([box[0] + 20, box[1] + 25, box[2] + 20, box[3] + 25],
                         white_bbox(LAB / 'PRIVATE_lab_chart.ppm', (20, 25, 160, 38)))

    @unittest.skipUnless(SELECTION.is_file(), 'Owner-only batch-02 source screenshot is unavailable')
    def test_selection_source_includes_the_real_grey_edge_pixel(self):
        measured = measure(self.font, self.source(52, 25))
        b = measured['ink_bbox_relative_exclusive']
        predicted = [b[0]+151, b[1]+34, b[2]+151, b[3]+34]
        rect = (150, 34, 294, 47)
        self.assertEqual(white_bbox(SELECTION, rect), [152, 35, 243, 45])
        self.assertEqual(neutral_ink_bbox(SELECTION, rect), predicted)

    @unittest.skipUnless(MONEY.is_file(), 'Owner-only translated money screenshot is unavailable')
    def test_reviewed_insufficient_funds_ink_matches_the_real_screen(self):
        reviewed = load_reviewed_glyphs(ROOT / 'data/baselines/c7130c53-reviewed-glyphs.json')
        codes = {v: cn_canonical_code(k) for k, v in reviewed.items()}
        result = measure(self.font, b''.join(codes[ch] for ch in '金钱不足') + b'\0')
        self.assertEqual((result['draw_max_advance_px'], result['game_measure_max_px']), (48, 20))
        box = result['ink_bbox_relative_exclusive']
        self.assertEqual([box[0]+154, box[1]+57, box[2]+154, box[3]+57],
                         neutral_ink_bbox(MONEY, (153, 57, 297, 70)))

    @unittest.skipUnless(all((EXPANDED_DECK / name).is_file() for name in
                         ('PRIVATE_deck_selection.ppm', 'PRIVATE_deck_menu.ppm')),
                         'Owner-only expanded batch screenshots are unavailable')
    def test_reviewed_selection_and_rename_match_actual_screen_ink(self):
        reviewed = load_reviewed_glyphs(ROOT / 'data/baselines/c7130c53-reviewed-glyphs.json')
        codes = {v: cn_canonical_code(k) for k, v in reviewed.items()}
        for text, x, y, filename, rect, advance in (
                ('选择卡组', 151, 34, 'PRIVATE_deck_selection.ppm', (150, 34, 294, 47), 48),
                ('改名', 167, 49, 'PRIVATE_deck_menu.ppm', (166, 49, 310, 62), 24)):
            measured = measure(self.font, b''.join(codes[ch] for ch in text) + b'\0')
            box = measured['ink_bbox_relative_exclusive']
            self.assertEqual(measured['draw_max_advance_px'], advance)
            self.assertEqual([box[0]+x, box[1]+y, box[2]+x, box[3]+y],
                             neutral_ink_bbox(EXPANDED_DECK / filename, rect))


if __name__ == '__main__':
    unittest.main()
