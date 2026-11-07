#!/usr/bin/env python3
"""PS1 CD-XA Mode 2 Form 1 (2352-byte) EDC/ECC verification and regeneration.

Independent stdlib implementation of the CD-ROM sector algorithms (EDC CRC
polynomial 0xD8018001, ECC GF(2^8), P/Q Reed-Solomon parity). The layout is
documented by psx-spx (https://psx-spx.consoledev.net/dev/ps1/cdr/cdromformat/).

This module never touches an input file. Only Mode 2 Form 1 is supported;
do NOT use on audio, Mode 1, Mode 2 Form 2, or arbitrary raw sectors.
"""
from __future__ import annotations

import struct

SECTOR_BYTES = 2352
SYNC = b"\x00" + b"\xFF" * 10 + b"\x00"
USER_BEGIN = 0x18
USER_END = 0x818
EDC_BEGIN = 0x818
EDC_END = 0x81C
P_BEGIN = 0x81C
Q_BEGIN = 0x8C8
END = 0x930


class SectorError(ValueError):
    """Sector structure/integrity does not satisfy supported Form 1 constraints."""


def _edc_table():
    result = []
    for i in range(256):
        v = i
        for _ in range(8):
            v = (v >> 1) ^ (0xD8018001 if v & 1 else 0)
        result.append(v)
    return tuple(result)


def _ecc_tables():
    forward = [0] * 256
    backward = [0] * 256
    for i in range(256):
        v = ((i << 1) ^ (0x11D if i & 0x80 else 0)) & 0xFF
        forward[i] = v
        backward[i ^ v] = i
    return tuple(forward), tuple(backward)


_EDC = _edc_table()
_FORWARD, _BACKWARD = _ecc_tables()


def edc32(data: bytes) -> int:
    """CD EDC CRC (not zlib CRC32): initialization 0, no final inversion."""
    value = 0
    for b in data:
        value = (value >> 8) ^ _EDC[(value ^ b) & 0xFF]
    return value


def _ecc_block(data: bytes, major_count: int, minor_count: int,
               major_mult: int, minor_inc: int) -> bytes:
    total = major_count * minor_count
    if len(data) < total:
        raise SectorError("ECC input shorter than interleaving block")
    out = bytearray(major_count * 2)
    for major in range(major_count):
        index = (major // 2) * major_mult + (major & 1)
        a = b = 0
        for _ in range(minor_count):
            temp = data[index]
            index = (index + minor_inc) % total
            a ^= temp
            b ^= temp
            a = _FORWARD[a]
        a = _BACKWARD[_FORWARD[a] ^ b]
        out[major] = a
        out[major + major_count] = a ^ b
    return bytes(out)


def validate_layout(sector: bytes):
    if len(sector) != SECTOR_BYTES:
        raise SectorError("Raw sector must have exactly 2352 bytes")
    if sector[:12] != SYNC:
        raise SectorError("Invalid CD-ROM synchronization pattern")
    if sector[15] != 2:
        raise SectorError("Only CD-XA Mode 2 is supported")
    if sector[16:20] != sector[20:24]:
        raise SectorError("Mode 2 duplicated subheaders do not match")
    if sector[18] & 0x20:
        raise SectorError("Mode 2 Form 2 has no ECC and is unsupported")
    return True


def regenerate_form1(sector: bytes) -> bytes:
    """Return a new 2352-byte sector with freshly computed Form 1 EDC/P/Q."""
    validate_layout(sector)
    buf = bytearray(sector)
    struct.pack_into("<I", buf, EDC_BEGIN, edc32(buf[0x10:EDC_BEGIN]))

    # In Mode 2 the four MSF/mode bytes are treated as zero for ECC calculation.
    ecc_input = bytearray(buf)
    ecc_input[0x0C:0x10] = b"\x00" * 4
    p = _ecc_block(ecc_input[0x0C:P_BEGIN], 86, 24, 2, 86)
    buf[P_BEGIN:Q_BEGIN] = p

    ecc_input = bytearray(buf)
    ecc_input[0x0C:0x10] = b"\x00" * 4
    q = _ecc_block(ecc_input[0x0C:Q_BEGIN], 52, 43, 86, 88)
    buf[Q_BEGIN:END] = q
    return bytes(buf)


def check_form1(sector: bytes) -> bool:
    """Whether a supported sector's stored EDC/ECC equals the regeneration."""
    return sector == regenerate_form1(sector)


def patch_form1_user_bytes(sector: bytes, user_offset: int, before: bytes,
                           after: bytes, *, require_valid_input=True) -> bytes:
    """Equal-size in-place logical data replacement, with all parity regenerated."""
    validate_layout(sector)
    if not isinstance(before, bytes) or not isinstance(after, bytes):
        raise SectorError("before/after must be bytes")
    if len(before) == 0 or len(before) != len(after):
        raise SectorError("before/after must be nonempty and the same byte length")
    if type(user_offset) is not int or user_offset < 0 or user_offset + len(after) > 2048:
        raise SectorError("Logical user data offset outside the 2048-byte Form 1 payload")
    if require_valid_input and not check_form1(sector):
        raise SectorError("Original sector EDC/ECC is invalid; refusing silent repair")
    start = USER_BEGIN + user_offset
    if sector[start:start + len(before)] != before:
        raise SectorError("Original sector bytes differ from required preimage")
    edited = bytearray(sector)
    edited[start:start + len(after)] = after
    result = regenerate_form1(bytes(edited))
    if not check_form1(result):
        raise SectorError("Generated ECC/EDC failed verification")
    if result[:USER_BEGIN] != sector[:USER_BEGIN]:
        raise SectorError("Raw sector header unexpectedly changed")
    return result
