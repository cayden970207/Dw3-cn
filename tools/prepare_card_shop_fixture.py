#!/usr/bin/env python3
"""Build a clearly artificial shop-condition fixture from one known private card.

This is an isolated memory-card COPY, not a normal-play achievement. It never
touches a disc or writes its input. The two deliberately prepared game-state
conditions are inspected again by the emulator during real menu navigation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct

SEED_SHA256 = '500e2beb76b5a18a2697e25316485756761cf8799c77e63ba5fd37ae828cc8b5'
CARD_SIZE = 0x20000
HEADER = 0x2200
SLOT = 0x2300
SLOT_LENGTH = 0x26C4
BOOSTER_ITEM = 91
SHOP_CARD = 65
MONEY = 10000


def xor(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
    return value


def create_fixture(source: bytes) -> tuple[bytes, list[int]]:
    if len(source) != CARD_SIZE or hashlib.sha256(source).hexdigest() != SEED_SHA256:
        raise ValueError('Not the pinned independent project save')
    if source[:2] != b'MC' or source[0x2000:0x2002] != b'SC':
        raise ValueError('Unexpected memory-card structure')
    if source[0x8a:0x9e] != b'BESLES-03936DMW3-EUR':
        raise ValueError('Wrong save file or directory placement')
    for n, expected, nxt in ((1, 0x51, 1), (2, 0x52, 2), (3, 0x52, 3), (4, 0x53, 0xffff)):
        entry = source[n * 128:(n + 1) * 128]
        if entry[0] != expected or int.from_bytes(entry[8:10], 'little') != nxt or xor(entry[:127]) != entry[127]:
            raise ValueError('Unexpected memory-card block chain or directory checksum')
    if (source[0x2002] != 0x13 or source[HEADER + 4:HEADER + 8] != b'DMW3' or
        source[HEADER + 2] != 4 or source[SLOT + 2] != 4 or
        source[HEADER] != xor(source[HEADER + 4:HEADER + 0xd4]) or
        source[SLOT] != xor(source[SLOT + 4:SLOT + SLOT_LENGTH])):
        raise ValueError('Save header, slot, or checksum is invalid')
    if (struct.unpack_from('<i', source, SLOT + 0x6c)[0] != 2 or
        struct.unpack_from('<i', source, HEADER + 0x28)[0] != 2 or
        source[SLOT + 0x7c + 0x192] != 1 or source[SLOT + 0x7c + BOOSTER_ITEM] != 0 or
        source[SLOT + 0x3a2 + SHOP_CARD] != 0 or source[SLOT + 0x4df + SHOP_CARD] != 0):
        raise ValueError('Seed state differs from the documented independent save')
    dest = bytearray(source)
    struct.pack_into('<i', dest, SLOT + 0x6c, MONEY)
    struct.pack_into('<i', dest, HEADER + 0x28, MONEY)
    dest[SLOT + 0x7c + BOOSTER_ITEM] = 1
    dest[SLOT + 0x3a2 + SHOP_CARD] = 9
    dest[SLOT + 0x4df + SHOP_CARD] = 1
    dest[SLOT] = xor(dest[SLOT + 4:SLOT + SLOT_LENGTH])
    dest[HEADER] = xor(dest[HEADER + 4:HEADER + 0xd4])
    allowed = {HEADER, SLOT, SLOT + 0x7c + BOOSTER_ITEM, SLOT + 0x3a2 + SHOP_CARD,
               SLOT + 0x4df + SHOP_CARD, *range(HEADER + 0x28, HEADER + 0x2c),
               *range(SLOT + 0x6c, SLOT + 0x70)}
    changes = [i for i, (a, b) in enumerate(zip(source, dest)) if a != b]
    if not changes or not set(changes) <= allowed:
        raise ValueError('Changes escape the prepared fields')
    return bytes(dest), changes


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('seed', type=Path)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    local = (Path(__file__).resolve().parents[1] / 'local').resolve()
    seed, output = args.seed.resolve(), args.output.resolve()
    if not seed.is_relative_to(local) or not output.is_relative_to(local) or output == seed:
        raise ValueError('Input and a distinct new output must both be under local/')
    data = seed.read_bytes()
    fixture, changes = create_fixture(data)
    if hashlib.sha256(seed.read_bytes()).hexdigest() != SEED_SHA256:
        raise ValueError('Input changed during construction')
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as f:
        f.write(fixture)
    print(json.dumps({'kind': 'artificial_condition_fixture_not_normal_play',
        'seed_sha256': SEED_SHA256, 'fixture_sha256': hashlib.sha256(fixture).hexdigest(),
        'changed_byte_offsets': changes, 'money': MONEY, 'booster_item_id': BOOSTER_ITEM,
        'booster_count': 1, 'card_id': SHOP_CARD, 'card_count': 9}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
