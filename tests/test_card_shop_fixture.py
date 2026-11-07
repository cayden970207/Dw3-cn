"""Safety checks for the isolated, explicitly artificial save fixture."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import uuid

from tools.batch03_save_regression import traces
from tools.font_recovery_probe import ARCHIVE_SHA
from tools.make_card_menu_batch02 import TARGETS, run as build
from tools import prepare_card_shop_fixture as fixture
from tools.text_codec import CN_IMAGE_SHA256

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / 'local/reference/dw2003recomp'


def seed_card():
    data = bytearray(fixture.CARD_SIZE)
    data[:2] = b'MC'
    for n, kind, nxt in ((1, 0x51, 1), (2, 0x52, 2), (3, 0x52, 3), (4, 0x53, 0xffff)):
        pos = n * 128
        data[pos] = kind
        struct.pack_into('<H', data, pos + 8, nxt)
        if n == 1:
            data[pos + 10:pos + 30] = b'BESLES-03936DMW3-EUR'
        data[pos + 127] = fixture.xor(data[pos:pos + 127])
    data[0x2000:0x2003] = b'SC\x13'
    h, s = fixture.HEADER, fixture.SLOT
    data[h + 2] = data[s + 2] = 4
    data[h + 4:h + 8] = b'DMW3'
    struct.pack_into('<i', data, h + 0x28, 2)
    struct.pack_into('<i', data, s + 0x6c, 2)
    data[s + 0x7c + 0x192] = 1
    data[h] = fixture.xor(data[h + 4:h + 0xd4])
    data[s] = fixture.xor(data[s + 4:s + fixture.SLOT_LENGTH])
    return bytes(data)


class TestCardShopFixture(unittest.TestCase):
    def test_precise_fields_and_checksums(self):
        source = seed_card()
        with patch.object(fixture, 'SEED_SHA256', hashlib.sha256(source).hexdigest()):
            result, changes = fixture.create_fixture(source)
        self.assertEqual(struct.unpack_from('<i', result, fixture.SLOT + 0x6c)[0], 10000)
        self.assertEqual(result[fixture.SLOT + 0x7c + 91], 1)
        self.assertEqual(result[fixture.SLOT + 0x3a2 + 65], 9)
        self.assertEqual(result[fixture.HEADER], fixture.xor(result[fixture.HEADER + 4:fixture.HEADER + 0xd4]))
        self.assertEqual(result[fixture.SLOT], fixture.xor(result[fixture.SLOT + 4:fixture.SLOT + fixture.SLOT_LENGTH]))
        self.assertEqual(changes, [i for i, (a, b) in enumerate(zip(source, result)) if a != b])
        self.assertEqual(struct.unpack_from('<i', source, fixture.SLOT + 0x6c)[0], 2)

    def test_refuses_changed_seed_and_invalid_checksums(self):
        source = seed_card()
        with self.assertRaises(ValueError):
            fixture.create_fixture(source)
        broken = bytearray(source)
        broken[fixture.SLOT + 0x100] ^= 1
        with patch.object(fixture, 'SEED_SHA256', hashlib.sha256(broken).hexdigest()):
            with self.assertRaises(ValueError):
                fixture.create_fixture(bytes(broken))

    def test_nine_card_translation_rejects_unreviewed_glyph(self):
        target, _, _, _, digest = TARGETS[20]
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            for name in ('index.json', 'audit.json'):
                (temp / name).write_text('{}')
            (temp / 'selected.json').write_text(json.dumps({'schema_version': 1,
                'source_image_sha256': CN_IMAGE_SHA256, 'entries': [{'file_id': 52, 'table': None,
                'entry_index': 20, 'target': target, 'original_sha256': digest, 'translation_reviewed': True}]}))
            output = ROOT / 'local' / ('never-created-' + uuid.uuid4().hex)
            with patch('tools.make_card_menu_batch02.hash_file', return_value=ARCHIVE_SHA), \
                 patch('tools.make_card_menu_batch02.load_reviewed_glyphs', return_value={347: '已', 877: '持', 13: '有', 318: '张'}):
                with self.assertRaisesRegex(ValueError, 'glyph identity is incomplete'):
                    build(temp / 'source.zip', temp / 'index.json', temp / 'audit.json', temp / 'glyphs.json',
                          temp / 'p1.json', temp / 'selected.json', output, {}, {})
            self.assertFalse(output.exists())

    @unittest.skipUnless((REF / 'tests/replay/scripts/first_battle_save.json').is_file(), 'Pinned private reference unavailable')
    def test_save_occurs_after_both_menus_and_cold_run_only_revisits(self):
        first, cold = traces(REF)
        for spec in (first, cold):
            self.assertFalse(any(s.get('type') in ('write_mem', 'reset') for s in spec['steps']))
        names = [s.get('name') for s in first['steps'] if s.get('type') == 'checkpoint']
        self.assertLess(names.index('batch03_nine_card_limit'), names.index('batch03_post_menu_saved'))
        self.assertLess(names.index('batch03_save_booster_selection'), names.index('batch03_post_menu_saved'))
        self.assertFalse(any(s.get('name') == 'batch03_post_menu_saved' for s in cold['steps']))


if __name__ == '__main__':
    unittest.main()
