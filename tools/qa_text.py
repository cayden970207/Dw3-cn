#!/usr/bin/env python3
"""Conservative catalog checks, NOT a game decoder, patcher or runtime proof.

The profile supplies measured glyph advances and exact control spellings.
All control tokens, including layout controls, must retain their original order.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import unicodedata

TOKEN = re.compile(r'<[0-9]+(?:,[0-9]+)*>')
KINDS = {'newline', 'page', 'wait', 'insert', 'other'}
STATES = {'draft', 'translated', 'reviewed', 'game_verified'}


def integer(value: object, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def byte_length(value: object, label: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2})+', value):
        raise ValueError(f'{label}: expected nonempty, even-length hexadecimal string')
    return len(bytes.fromhex(value))


def prepare_profile(profile: object) -> tuple[dict, dict, int]:
    if not isinstance(profile, dict) or profile.get('schema_version') != 1:
        raise ValueError('Profile must be an object with schema_version 1')
    if type(profile.get('synthetic')) is not bool:
        raise ValueError('Profile must explicitly set synthetic to true or false')
    glyphs, controls = profile.get('glyphs'), profile.get('controls')
    if not isinstance(glyphs, dict) or not isinstance(controls, dict):
        raise ValueError('Profile glyphs and controls must be objects')
    prepared_glyphs, prepared_controls = {}, {}
    for char, item in glyphs.items():
        if len(char) != 1 or not isinstance(item, dict) or not integer(item.get('advance_px')):
            raise ValueError(f'Invalid glyph metrics: {char!r}')
        size = byte_length(item.get('hex'), f'glyph {char!r}')
        if b'\x00' in bytes.fromhex(item['hex']):
            raise ValueError(f'Glyph {char!r} includes a NUL byte; unsupported by this catalog format')
        prepared_glyphs[char] = (size, item['advance_px'])
    for token, item in controls.items():
        if not TOKEN.fullmatch(token) or not isinstance(item, dict) or item.get('kind') not in KINDS:
            raise ValueError(f'Invalid control definition: {token!r}')
        size = byte_length(item.get('hex'), f'control {token}')
        width = item.get('max_width_px', 0)
        if not integer(width) or (item['kind'] == 'insert' and 'max_width_px' not in item):
            raise ValueError(f'{token}: insert controls need an explicit nonnegative max_width_px')
        prepared_controls[token] = (size, item['kind'], width)
    terminator_size = byte_length(profile.get('terminator_hex'), 'terminator_hex')
    return prepared_glyphs, prepared_controls, terminator_size


def tokenize(text: str) -> list[tuple[str, str]]:
    parts, position = [], 0
    while position < len(text):
        if text[position] == '<':
            match = TOKEN.match(text, position)
            if not match:
                raise ValueError(f'Malformed control at character {position}')
            parts.append(('control', match.group()))
            position = match.end()
        else:
            if text[position] == '>':
                raise ValueError(f'Unmatched > at character {position}')
            parts.append(('glyph', text[position]))
            position += 1
    return parts


def validate_catalog(catalog: object, profile: object) -> dict:
    glyphs, controls, terminator_size = prepare_profile(profile)
    if not isinstance(catalog, dict) or catalog.get('schema_version') != 1 or not isinstance(catalog.get('entries'), list):
        raise ValueError('Catalog needs schema_version 1 and an entries array')
    errors, pending, measurements = [], [], []
    seen = set()

    def issue(destination: list, key: str, code: str, message: str) -> None:
        destination.append({'id': key, 'code': code, 'message': message})

    if profile['synthetic']:
        issue(pending, '*', 'synthetic_profile', 'Synthetic fixture; not an actual game font/encoding.')
    if profile.get('measurement_verified') is not True:
        issue(pending, '*', 'unverified_metrics', 'Actual glyph advances and window limits require game measurement.')
    if not catalog['entries']:
        issue(errors, '*', 'empty_catalog', 'An empty catalog is not translation coverage.')
    for index, entry in enumerate(catalog['entries']):
        key = entry.get('id') if isinstance(entry, dict) else None
        if not isinstance(key, str) or not key.strip():
            issue(errors, f'entry:{index}', 'schema', 'Entry needs a nonempty string id.')
            continue
        if key in seen:
            issue(errors, key, 'duplicate_id', 'Entry id is duplicated.')
        seen.add(key)
        source, target = entry.get('source'), entry.get('target')
        if not isinstance(source, str) or not isinstance(target, str):
            issue(errors, key, 'schema', 'source and target must be strings.')
            continue
        if not target.strip():
            issue(errors, key, 'missing_translation', 'Target is empty.')
            continue
        state = entry.get('state')
        if state not in STATES:
            issue(errors, key, 'state', 'Unknown or missing translation state.')
        elif state not in {'reviewed', 'game_verified'}:
            issue(pending, key, 'translation_unreviewed', 'Translation has not been marked reviewed.')
        if state == 'game_verified' and not entry.get('runtime_evidence'):
            issue(errors, key, 'missing_evidence', 'game_verified requires a runtime_evidence reference.')
        if unicodedata.normalize('NFC', target) != target:
            issue(errors, key, 'normalization', 'Target must be NFC-normalized.')
        if any(unicodedata.category(char) in {'Cc', 'Cf', 'Cs'} for char in target):
            issue(errors, key, 'invalid_character', 'NUL, tabs, raw newlines and invisible controls are forbidden; use explicit tokens.')
        limits = entry.get('limits')
        if not isinstance(limits, dict) or any(not integer(limits.get(name), 1) for name in ('max_width_px', 'max_lines', 'max_bytes')):
            issue(errors, key, 'limits', 'Explicit positive max_width_px, max_lines and max_bytes are required.')
            continue
        try:
            source_parts, target_parts = tokenize(source), tokenize(target)
        except ValueError as exc:
            issue(errors, key, 'malformed_control', str(exc))
            continue
        source_codes = [text for kind, text in source_parts if kind == 'control']
        target_codes = [text for kind, text in target_parts if kind == 'control']
        if source_codes != target_codes:
            issue(errors, key, 'control_sequence', 'Control sequence/arguments changed; requires separate engineering review.')
        for token in sorted(set(source_codes + target_codes) - controls.keys()):
            issue(errors, key, 'unknown_control', f'No verified profile definition for {token}.')
        width, widest, lines, most_lines, encoded_size = 0, 0, 1, 1, terminator_size
        for kind, text in target_parts:
            if kind == 'glyph':
                if text not in glyphs:
                    issue(errors, key, 'missing_glyph', f'No glyph mapping for {text!r}.')
                    continue
                size, advance = glyphs[text]
                encoded_size += size
                width += advance
            elif text in controls:
                size, control_kind, insert_width = controls[text]
                encoded_size += size
                if control_kind == 'newline':
                    widest = max(widest, width)
                    width = 0
                    lines += 1
                elif control_kind == 'page':
                    widest, most_lines = max(widest, width), max(most_lines, lines)
                    width, lines = 0, 1
                elif control_kind == 'insert':
                    width += insert_width
        widest, most_lines = max(widest, width), max(most_lines, lines)
        measurements.append({'id': key, 'width_px': widest, 'max_lines_per_page': most_lines, 'encoded_bytes_including_terminator': encoded_size})
        for measured, limit, code in ((widest, limits['max_width_px'], 'width_overflow'), (most_lines, limits['max_lines'], 'line_overflow'), (encoded_size, limits['max_bytes'], 'byte_overflow')):
            if measured > limit:
                issue(errors, key, code, f'{measured} exceeds configured limit {limit}.')
    return {
        'entries_checked': len(catalog['entries']),
        'status': 'blocked' if errors else ('pending' if pending else 'passed_static'),
        'errors': errors,
        'pending': pending,
        'measurements': measurements,
        'gameplay_verified': False,
        'limitations': 'Only supplied entries and profile are checked. Advance widths do not prove ink/clipping bounds, runtime buffers, coverage, pointer integrity or gameplay correctness.',
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('catalog', type=Path)
    parser.add_argument('--profile', required=True, type=Path)
    parser.add_argument('--strict', action='store_true', help='Also fail on pending items')
    args = parser.parse_args(argv)
    try:
        catalog = json.loads(args.catalog.read_text(encoding='utf-8'))
        profile = json.loads(args.profile.read_text(encoding='utf-8'))
        report = validate_catalog(catalog, profile)
    except (OSError, ValueError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report['errors'] or (args.strict and report['pending']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
