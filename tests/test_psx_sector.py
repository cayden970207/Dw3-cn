"""Synthetic sectors only; no game data involved."""
import unittest

from tools.psx_sector import (
    SECTOR_BYTES, SYNC, SectorError, check_form1, edc32,
    patch_form1_user_bytes, regenerate_form1, validate_layout,
)


def fake_sector():
    raw = bytearray(SECTOR_BYTES)
    raw[:12] = SYNC
    raw[12:16] = b"\x01\x02\x03\x02"
    raw[16:24] = b"\x00\x00\x08\x00" * 2
    raw[24:24 + 2048] = bytes((i * 17 + 2) & 255 for i in range(2048))
    return regenerate_form1(bytes(raw))


def independent_bitwise_edc(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xD8018001 if crc & 1 else 0)
    return crc


class RawSectorTests(unittest.TestCase):
    def test_edc_matches_bitwise_reference(self):
        for case in (b"", b"123456789", bytes(range(256)), bytes(range(255, -1, -1))):
            self.assertEqual(edc32(case), independent_bitwise_edc(case))

    def test_generation_changes_only_checksum_region(self):
        raw = bytearray(fake_sector())
        checksum = bytes(raw[0x818:])
        raw[0x818:] = b"\x00" * (SECTOR_BYTES - 0x818)
        result = regenerate_form1(bytes(raw))
        self.assertEqual(result[0x818:], checksum)
        self.assertEqual(result[:0x818], bytes(raw[:0x818]))
        self.assertTrue(check_form1(result))

    def test_patch_across_user_data(self):
        before = fake_sector()
        old = before[24 + 47:24 + 51]
        after = patch_form1_user_bytes(before, 47, old, b"ABCD")
        self.assertTrue(check_form1(after))
        self.assertEqual(after[24 + 47:24 + 51], b"ABCD")
        self.assertEqual(after[:24], before[:24])
        self.assertEqual(len(after), len(before))
        self.assertFalse(check_form1(after[:24] + b"\xFF" + after[25:]))

    def test_invalid_existing_parity_rejected(self):
        raw = bytearray(fake_sector())
        raw[100] ^= 0x55
        with self.assertRaisesRegex(SectorError, "Original sector EDC/ECC is invalid"):
            patch_form1_user_bytes(bytes(raw), 0, bytes(raw[24:25]), b"z")

    def test_preimage_required(self):
        with self.assertRaisesRegex(SectorError, "preimage"):
            patch_form1_user_bytes(fake_sector(), 0, b"Wrong", b"Bytes")

    def test_rejects_form2_and_audio(self):
        raw = bytearray(fake_sector())
        raw[18] |= 0x20
        raw[22] |= 0x20
        with self.assertRaisesRegex(SectorError, "Form 2"):
            validate_layout(raw)
        raw = bytearray(fake_sector())
        raw[15] = 1
        with self.assertRaisesRegex(SectorError, "Mode 2"):
            validate_layout(raw)

    def test_rejects_corrupt_headers(self):
        raw = bytearray(fake_sector())
        raw[0] = 1
        with self.assertRaisesRegex(SectorError, "synchronization"):
            validate_layout(raw)
        raw = bytearray(fake_sector())
        raw[20] ^= 0x40
        with self.assertRaisesRegex(SectorError, "subheaders"):
            validate_layout(raw)

    def test_rejects_user_spill_and_wrong_length(self):
        with self.assertRaisesRegex(SectorError, "outside"):
            patch_form1_user_bytes(fake_sector(), 2047, b"2", b"XY")
        with self.assertRaisesRegex(SectorError, "same byte length"):
            patch_form1_user_bytes(fake_sector(), 0, b"a", b"XX")
        with self.assertRaisesRegex(SectorError, "outside"):
            patch_form1_user_bytes(fake_sector(), -1, b"a", b"X")

    def test_stable_on_repeated_regeneration(self):
        raw = fake_sector()
        self.assertEqual(regenerate_form1(regenerate_form1(raw)), raw)

    def test_header_address_is_untouched(self):
        raw = fake_sector()
        mutated = bytearray(raw)
        mutated[12:15] = b"\x25\x26\x27"
        # Mode2 parity treats address as all zero; EDC excludes address.
        self.assertTrue(check_form1(bytes(mutated)))
        self.assertEqual(regenerate_form1(bytes(mutated)), bytes(mutated))


if __name__ == "__main__":
    unittest.main()
