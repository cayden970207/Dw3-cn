#!/usr/bin/env python3
"""Token boundaries for the EU text format and the inspected Chinese image.

Tokens retain the original bytes.  A glyph index is not a Unicode decoding,
and the image-specific rules are selected only by the exact inspected hash.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

CN_IMAGE_SHA256 = "c7130c532f42d7c01f393bf067b073a1e4b924607a71e2287b0c3792468777fc"
REFERENCE = "eu-reference"
CN_C713 = "cn-c7130c53"
CN_GLYPH_COUNT = 1997
CN_GLYPH_ARRAY_SHA256 = "d3fc6b6bb9cb6d0750e2375984b90df949f7102ad7cb632491c80ded0e23efe5"
CONTROL_LENGTHS = (1, 2, 3, 2, 5, 3, 3, 2, 3, 2)


class CodecError(ValueError):
    """The byte sequence cannot safely be segmented using this codec."""


@dataclass(frozen=True)
class Token:
    kind: str
    offset: int
    raw: bytes
    glyph_index: int | None = None
    control_id: int | None = None


def variant_for_image(image_sha256: str) -> str:
    return CN_C713 if image_sha256 == CN_IMAGE_SHA256 else REFERENCE


def cn_glyph_index(lead: int, trail: int) -> int:
    if not 0xF0 <= lead <= 0xFF or not 0 <= trail <= 0xFF:
        raise CodecError("Invalid Chinese glyph byte pair")
    return (lead - 0xF0) * 252 + trail - 4


def cn_canonical_code(index: int) -> bytes:
    if type(index) is not int or not 0 <= index < CN_GLYPH_COUNT:
        raise CodecError("Chinese glyph index outside inspected font")
    return bytes((0xF0 + index // 252, 4 + index % 252))


def load_reviewed_glyphs(path: Path) -> dict[int, str]:
    """Load the explicit user-reviewed subset; it is not a complete codec."""
    review = json.loads(Path(path).read_text(encoding="utf-8"))
    if (review.get("source_image_sha256") != CN_IMAGE_SHA256 or
            review.get("source_glyph_array_sha256") != CN_GLYPH_ARRAY_SHA256 or
            review.get("review", {}).get("reviewer") != "user"):
        raise CodecError("Reviewed glyph subset is not bound to the inspected image and user review")
    output = {}
    characters = set()
    for row in review.get("glyphs", []):
        index, character, code = row.get("glyph_index"), row.get("unicode"), row.get("canonical_code_hex")
        if (type(index) is not int or not isinstance(character, str) or len(character) != 1 or
                cn_canonical_code(index).hex() != code or index in output or character in characters):
            raise CodecError("Invalid or duplicate reviewed glyph mapping")
        output[index] = character
        characters.add(character)
    if not output:
        raise CodecError("The reviewed glyph subset is empty")
    return output


def tokenize(source: bytes, *, variant: str = REFERENCE) -> list[Token]:
    """Segment an entire NUL-terminated stored string, without decoding it.

    The inspected game's message-copy path uses strlen, so zero cannot be a
    stored glyph byte or a control argument. Unknown/fallback glyphs are kept
    as tokens; invalid structure raises rather than guessing a boundary.
    """
    if variant not in (REFERENCE, CN_C713):
        raise CodecError("Unknown codec variant")
    if not isinstance(source, bytes) or not source or source[-1] != 0:
        raise CodecError("A final NUL terminator is required")
    if 0 in source[:-1]:
        raise CodecError("Interior NUL conflicts with the inspected strlen path")
    tokens = []
    pos = 0
    end = len(source) - 1
    while pos < end:
        first = source[pos]
        if variant == CN_C713 and first >= 0xF0:
            if pos + 1 >= end:
                raise CodecError("Truncated Chinese glyph pair")
            raw = source[pos:pos + 2]
            index = cn_glyph_index(raw[0], raw[1])
            kind = "cn_glyph" if 0 <= index < CN_GLYPH_COUNT else "unknown_cn_glyph"
            tokens.append(Token(kind, pos, raw, glyph_index=index))
            pos += 2
        elif first == 1:
            if pos + 1 >= end:
                raise CodecError("Truncated extended glyph")
            raw = source[pos:pos + 2]
            kind = "extended_glyph" if 1 <= raw[1] <= 0x72 else "unknown_extended_glyph"
            tokens.append(Token(kind, pos, raw, glyph_index=raw[1]))
            pos += 2
        elif first == 2:
            if pos + 1 >= len(source):
                raise CodecError("Truncated control identifier")
            control = source[pos + 1]
            if control >= len(CONTROL_LENGTHS):
                raise CodecError("Unknown control identifier")
            size = CONTROL_LENGTHS[control]
            if pos + size > end:
                raise CodecError("Truncated control arguments")
            tokens.append(Token("control", pos, source[pos:pos + size], control_id=control))
            pos += size
        elif first == 3 or first >= 0xEA:
            # For stored (not Shift-JIS) text, the type-3 fallback advances one byte.
            tokens.append(Token("unknown_fallback", pos, source[pos:pos + 1]))
            pos += 1
        else:
            tokens.append(Token("base_glyph", pos, source[pos:pos + 1], glyph_index=first))
            pos += 1
    tokens.append(Token("terminator", end, b"\x00"))
    if b"".join(token.raw for token in tokens) != source:
        raise CodecError("Token boundaries do not preserve the source")
    return tokens
