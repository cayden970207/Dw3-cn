"""Safety tests for the new EU font experiment; test data is invented."""
import hashlib
import random
import struct
import unittest

from tools.eu_banked_codec import MAX_GLYPHS, encode_index, encode_text, tokenize_proposed
from tools.eu_font_prototype import merge_ranges, pack_rlen
from tools.eu_text_rebuild_experiment import rebuild_flat
from tools.font_recovery_probe import unpack_rlen
from tools.replay_eu_font_save import validate_listener_resets
from tools.roundtrip_cn import inspect_resource
from tools.text_codec import CodecError, REFERENCE

try:
    from PIL import Image
    from tools.build_new_font_banks import encode_native
except ImportError:
    Image = None


def synthetic_resource(strings_and_tails):
    """Build a flat table with explicitly retained tails, no game data."""
    header = 4 + 4 * len(strings_and_tails)
    data = bytearray(b'\0' * header)
    struct.pack_into('<I', data, 0, len(strings_and_tails))
    entries = []
    for i, (raw, tail) in enumerate(strings_and_tails):
        offset = len(data)
        struct.pack_into('<I', data, 4+i*4, offset)
        entries.append({'table': None, 'index': i, 'offset_bytes': offset,
                        'raw_hex': raw.hex(), 'encoded_bytes': len(raw),
                        'raw_sha256': hashlib.sha256(raw).hexdigest()})
        data.extend(raw + tail)
    data = bytes(data)
    return data, {'file_id': 1, 'file_sha256': hashlib.sha256(data).hexdigest(),
                  'file_size_bytes': len(data), 'container': False,
                  'table_count': 1, 'entries': entries}


class EuFontExperimentTests(unittest.TestCase):
    def test_rlen_roundtrip_packet_boundaries(self):
        rng = random.Random(82)
        examples = [bytes([7])*n for n in (1, 2, 126, 127, 128, 254, 255)]
        examples += [bytes(rng.randrange(256) for _ in range(n)) for n in (1, 127, 128, 300)]
        for source in examples:
            with self.subTest(size=len(source)):
                packed = pack_rlen(source)
                self.assertEqual(unpack_rlen(packed), (source, len(packed)))
        with self.assertRaises(ValueError):
            pack_rlen(b'')

    def test_approved_ranges_do_not_bridge_gaps(self):
        self.assertEqual(merge_ranges([(20, 25), (3, 8), (8, 10), (4, 7)]), [(3, 10), (20, 25)])
        with self.assertRaises(ValueError):
            merge_ranges([(2, 2)])

    def test_banked_encoding_has_no_zero_and_roundtrips_endpoints(self):
        for index in (0, 254, 255, 256, 233*255+233, MAX_GLYPHS-1):
            encoded = encode_index(index, MAX_GLYPHS)
            self.assertNotIn(0, encoded)
            token, terminator = tokenize_proposed(encoded + b'\0', MAX_GLYPHS)
            self.assertEqual((token.kind, token.glyph_index, terminator.kind),
                             ('bank_glyph', index, 'terminator'))
        for value, count in ((-1, 1), (1, 1), (0, 0), (0, MAX_GLYPHS+1), (True, 1)):
            with self.assertRaises(CodecError):
                encode_index(value, count)

    def test_marker_inside_control_or_extended_token_is_not_a_prefix(self):
        raw = b'\x01\xea\x02\x04\xea\x01\x02\x00'
        tokens = tokenize_proposed(raw, 100)
        self.assertEqual([t.kind for t in tokens], ['unknown_extended_glyph', 'control', 'terminator'])
        self.assertEqual(b''.join(t.raw for t in tokens), raw)
        self.assertEqual(tokens[1].control_id, 4)

    def test_banked_parser_rejects_truncation_and_invalid_index(self):
        for raw in (b'\xea\0', b'\xea\x01\0', b'\xea\x02\x01\0',
                    b'\xea\x01\0\x01\0', b'\x02\x04\x01\0'):
            with self.subTest(raw=raw), self.assertRaises(CodecError):
                tokenize_proposed(raw, 1)
        with self.assertRaises(CodecError):
            encode_text('缺', {'卡': 0})

    def test_rebuild_moves_pointers_and_keeps_nonzero_tails(self):
        data, resource = synthetic_resource([(b'\x04\0', b'\x7e\x55'), (b'\x05\0', b'\x73')])
        original = data
        rebuilt, changes = rebuild_flat(data, resource, {0: encode_index(0, 10)+b'\0'}, 10)
        first, second = struct.unpack_from('<II', rebuilt, 4)
        self.assertEqual((first, second), (12, 20))
        self.assertEqual(rebuilt[first:first+6], encode_index(0, 10)+b'\0\x7e\x55')
        self.assertEqual(rebuilt[second:], b'\x05\0\x73')
        self.assertEqual(data, original)
        self.assertEqual(changes[0]['original_slot_bytes'], 4)
        self.assertEqual(changes[0]['replacement_bytes_including_terminator'], 4)

    def test_rebuild_rejects_changed_controls_and_unknowns(self):
        data, resource = synthetic_resource([(b'\x04\x02\x05\x01\0', b'')])
        with self.assertRaisesRegex(ValueError, 'Control tokens changed'):
            rebuild_flat(data, resource, {0: encode_index(0, 10)+b'\0'}, 10)
        data, resource = synthetic_resource([(b'\xf0\0', b'')])
        with self.assertRaisesRegex(ValueError, 'Unknown token'):
            rebuild_flat(data, resource, {0: encode_index(0, 10)+b'\0'}, 10)
        self.assertFalse(inspect_resource(data, resource, variant=REFERENCE)['errors'])
        self.assertTrue(inspect_resource(data, resource)['errors'])

    def test_unexpected_listener_errors_are_not_counted_as_known_resets(self):
        reset = 'run.lua:391: replay: Lua stack reset at frame 8192\nError in event listener:'
        self.assertEqual(validate_listener_resets(reset), 1)
        with self.assertRaises(ValueError):
            validate_listener_resets(reset + '\nError in event listener: unrelated error')
        with self.assertRaises(ValueError):
            validate_listener_resets('other: replay: Lua stack reset at frame 8192\nError in event listener:')

    @unittest.skipIf(Image is None, 'Pillow/fonttools optional for font generation')
    def test_native_bpp_roundtrip_exact_pixels(self):
        image = Image.new('1', (12, 12))
        for x, y in ((0, 0), (1, 0), (5, 4), (11, 11)):
            image.putpixel((x, y), 1)
        four, one = encode_native(image)
        self.assertEqual((len(four), len(one)), (72, 18))
        for i in range(144):
            expected = bool(image.getpixel((i % 12, i // 12)))
            self.assertEqual(bool((four[i//2] >> (4*(i % 2))) & 15), expected)
            self.assertEqual(bool((one[i//8] >> (7-(i % 8))) & 1), expected)


if __name__ == '__main__':
    unittest.main()
