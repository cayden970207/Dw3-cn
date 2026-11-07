"""Private-index regression: no game text or images are stored in this test."""
import hashlib
import json
from pathlib import Path
import unittest

from tools.make_card_menu_batch04 import SOURCE_SHA, prepare_return
from tools.text_codec import CN_C713, load_reviewed_glyphs, tokenize

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / 'local/dw3-probe/20261107T092435Z-03a090/PRIVATE_DO_NOT_UPLOAD.json'
REVIEW = ROOT / 'data/baselines/c7130c53-reviewed-glyphs.json'


class Batch04Safety(unittest.TestCase):
    def test_mapping_correction_and_prior_targets(self):
        reviewed = load_reviewed_glyphs(REVIEW)
        self.assertEqual({96: '回', 985: '返', 1121: '园'},
                         {i: reviewed[i] for i in (96, 985, 1121)})
        # Existing compiled translations were selected by text identity. This
        # audit prevents a mistaken prior identification from being overlooked.
        for name in ('p1_batch01_reviewed.json', 'p1_batch02_selected.json',
                     'p1_batch02_expanded_selected.json', 'p1_batch03_selected.json'):
            data = json.loads((ROOT / 'data' / name).read_text())
            self.assertNotIn('回', json.dumps(data, ensure_ascii=False))

    @unittest.skipUnless(INDEX.exists(), 'Real private index is not distributed')
    def test_real_return_hint_preserves_prefix_and_rejects_bad_inputs(self):
        index = json.loads(INDEX.read_text())
        file = next(f for f in index['text_files'] if f['file_id'] == 52)
        row = next(e for e in file['entries'] if e['index'] == 4 and e['table'] is None)
        original = bytes.fromhex(row['raw_hex'])
        self.assertEqual(hashlib.sha256(original).hexdigest(), SOURCE_SHA)
        reviewed = load_reviewed_glyphs(REVIEW)
        replacement = prepare_return(original, reviewed)
        old, new = tokenize(original, variant=CN_C713), tokenize(replacement, variant=CN_C713)
        self.assertEqual([t.raw for t in old[:2]], [t.raw for t in new[:2]])
        self.assertEqual([(t.kind, t.glyph_index) for t in new[2:-1]],
                         [('cn_glyph', 985), ('cn_glyph', 96)])
        self.assertLessEqual(len(replacement), len(original))
        with self.assertRaises(ValueError):
            prepare_return(original[:-1] + b'X', reviewed)
        with self.assertRaises(ValueError):
            prepare_return(original, {985: '返', 96: '园'})


if __name__ == '__main__':
    unittest.main()
