#!/usr/bin/env python3
"""Summarize read-only EU game heap observations from a completed replay."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import re

from tools.eu_font_prototype import EU_IMAGE_SHA

SAMPLE = re.compile(r'eu_heap_sample frame=(\d+) stage=(\d+) largest=(\d+) total=(\d+) freeblocks=(\d+) blocks=(\d+)')
HEAP_BYTES = 0x801FF000 - 0x800AB800
PADDED_1BPP = 129 * 6144
TIGHT_1BPP = 32950 * 18
PADDED_4BPP = 129 * 18432


def summarize(directory: Path) -> dict:
    root = Path(__file__).resolve().parents[1]
    if not directory.resolve().is_relative_to((root / 'local').resolve()):
        raise ValueError('Replay evidence must remain under local/')
    replay = json.loads((directory / 'SHARE_THIS_report.json').read_text())
    if (replay.get('status') != 'eu_font_prototype_post_album_save_cold_reload_verified' or
            replay.get('source_image_sha256') != EU_IMAGE_SHA or not replay.get('heap_probe_enabled')):
        raise ValueError('A completed heap-instrumented EU replay is required')
    phases = {}
    for phase in ('first', 'cold'):
        log = (directory / phase / 'emulator.log').read_text(errors='replace')
        if 'eu_heap_invalid' in log:
            raise ValueError('Heap linked-list validation failed during sampling')
        by_stage = defaultdict(list)
        for raw in SAMPLE.findall(log):
            frame, stage, largest, total, freeblocks, blocks = map(int, raw)
            if not (0 < frame and 0 <= largest <= total <= HEAP_BYTES and 0 <= freeblocks <= blocks < 10000):
                raise ValueError('A heap sample lies outside the checked arena')
            by_stage[stage].append((frame, largest, total, freeblocks, blocks))
        required = {2, 12, 16, 18} if phase == 'first' else {2, 16, 18}
        if not required <= by_stage.keys():
            raise ValueError('Missing a required sampled scene')
        stages = {}
        for stage, samples in sorted(by_stage.items()):
            worst = min(samples, key=lambda item: item[1])
            stages[str(stage)] = {'samples': len(samples), 'min_largest_free_bytes': worst[1],
                                  'min_largest_at_frame': worst[0],
                                  'min_total_free_bytes': min(x[2] for x in samples),
                                  'samples_below_padded_1bpp': sum(x[1] < PADDED_1BPP for x in samples),
                                  'samples_below_tight_1bpp': sum(x[1] < TIGHT_1BPP for x in samples)}
        phases[phase] = {'samples': sum(len(rows) for rows in by_stage.values()), 'stages': stages}
    return {'schema_version': 1, 'status': 'sampled_heap_observations_not_a_full_game_memory_proof',
            'source_image_sha256': EU_IMAGE_SHA, 'test_image_sha256': replay['test_image_sha256'],
            'arena_bytes': HEAP_BYTES, 'native_font_bytes': {'banked_4bpp': PADDED_4BPP,
                                                            'banked_1bpp_with_padding': PADDED_1BPP,
                                                            'unbanked_1bpp_without_padding': TIGHT_1BPP},
            'sample_period_frames': 30, 'phases': phases,
            'limits': ['The read-only sample covers only the stated replay route and every 30th frame.',
                       'A currently small free block does not prove allocation would fail after the CD cache is evicted.',
                       'A large block does not prove that keeping it reserved would be safe in later scenes.',
                       'No font bank was allocated or loaded by this measurement.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path)
    p.add_argument('report', type=Path)
    a = p.parse_args()
    result = summarize(a.directory)
    with os.fdopen(os.open(a.report, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2); f.write('\n')
    print(json.dumps({'status': result['status'], 'first_samples': result['phases']['first']['samples']}))


if __name__ == '__main__':
    main()
