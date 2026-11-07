#!/usr/bin/env python3
"""Verify five prior candidate sources and three real window paths, read-only."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path

try:
    from tools.font_recovery_probe import EXE_SHA, filetable
    from tools.layout_cn import actual_assets, measure, neutral_ink_bbox
    from tools.make_card_menu_batch02 import P1_SHA, SCREEN_SHA, TARGETS
    from tools.roundtrip_cn import inspect_resource
    from tools.scan_text_tables import Disc, hash_file, local_image
    from tools.text_codec import CN_C713, CN_IMAGE_SHA256, tokenize
except ModuleNotFoundError:
    from font_recovery_probe import EXE_SHA, filetable
    from layout_cn import actual_assets, measure, neutral_ink_bbox
    from make_card_menu_batch02 import P1_SHA, SCREEN_SHA, TARGETS
    from roundtrip_cn import inspect_resource
    from scan_text_tables import Disc, hash_file, local_image
    from text_codec import CN_C713, CN_IMAGE_SHA256, tokenize

OVERLAYS = {'STCRDDEK.PRO': 'f813f89a1ab17597e6a429c42d0acd81dd41aa49',
            'STCRDSHP.PRO': 'd1e01bd598694a6aa8ddd0978f6d382fa970d314'}
SEED_SHA = '4c0497c96447801d4623e4412e5ae2b38fae85b33ba1c343ce53604327a44400'


def run(image: Path, index_path: Path, audit_path: Path, draft_path: Path, batch_image: Path,
        deck_dir: Path, buy_dir: Path):
    if hash_file(batch_image) != P1_SHA:
        raise ValueError('Runtime input is not the verified first batch')
    index, audit, draft = (json.loads(p.read_text()) for p in (index_path, audit_path, draft_path))
    if any(d.get('source_image_sha256', d.get('image_sha256')) != CN_IMAGE_SHA256 for d in (index, audit, draft)):
        raise ValueError('Evidence inputs do not use the inspected image')
    font = actual_assets(image, audit)
    f = next(x for x in index['text_files'] if x['file_id'] == 52)
    with local_image(image) as raw:
        if hash_file(raw) != CN_IMAGE_SHA256:
            raise ValueError('Unpacked original differs')
        disc = Disc(raw)
        boot = next(x for x in audit['iso_directory_files'] if x['path'].upper() == 'SLES_039.36')
        exe = disc.read_user(boot['lba'], boot['size_bytes'])
        if hashlib.sha256(exe).hexdigest() != EXE_SHA or filetable(exe, 52) != (2313, 1):
            raise ValueError('Executable or file table mismatch')
        for name, digest in OVERLAYS.items():
            row = next(x for x in audit['executable_filetable'] if (x.get('directory_path') or '').endswith('/' + name))
            if filetable(exe, row['file_id']) != (row['lba'], row['sectors']):
                raise ValueError('Overlay file table differs')
            if hashlib.sha1(disc.read_user(row['lba'], row['directory_size_bytes'])).hexdigest() != digest:
                raise ValueError('Target overlay differs from inspected caller')
        checked = inspect_resource(disc.read_user(f['lba'], f['file_size_bytes']), f)
        if checked['errors'] or not checked['file_template_equal']:
            raise ValueError('Target resource differs from its private index')
    replay = {}
    for kind, folder in (('deck', deck_dir), ('buy', buy_dir)):
        result = json.loads((folder / 'result.json').read_text())
        trace = json.loads((folder / 'PRIVATE_trace.json').read_text())
        log = (folder / 'emulator.log').read_text(errors='replace')
        if result.get('status') != 'ok' or any(x.get('type') in ('reset', 'write_mem') for x in trace['steps']):
            raise ValueError('Source replay failed or injected game state')
        if hash_file(folder / 'memcard1.mcd') != SEED_SHA:
            raise ValueError('The independent source-replay memory card changed')
        checkpoints = {x['name']: x for x in result['checkpoints']}
        names = (('batch02_deck_selection', 'batch02_deck_menu', 'batch02_menu_cancelled', 'batch02_menu_reopened',
                  'batch02_name_entry', 'batch02_name_return',
                  'batch02_deck_return_to_party') if kind == 'deck' else
                 ('batch02_buy_open', 'batch02_insufficient_funds', 'batch02_buy_return_to_field'))
        if any(n not in checkpoints for n in names):
            raise ValueError('Source replay checkpoint missing')
        if kind == 'deck' and not all(x in log for x in ('batch02_deck state=1 step=4',
                'batch02_deck state=1 step=13', 'batch02_deck state=1 step=15',
                'batch02_name state=1 step=2', 'batch02_name state=1 step=100')):
            raise ValueError('Deck selection, rename entry, or return not observed')
        if kind == 'buy' and 'batch02_buy state=1 step=12 substep=19' not in log:
            raise ValueError('Insufficient-funds state not observed')
        replay[kind] = {'checkpoints': {n: {'frame': checkpoints[n]['frame'], 'stage': checkpoints[n]['stage']} for n in names},
                        'pad_warning_count': log.count('Unknown command for pad: 43'),
                        'listener_stack_reset_count': log.count('replay: Lua stack reset at frame 8192')}
        if kind == 'deck':
            before = (folder / checkpoints['batch02_name_entry']['gamestate_file']).read_bytes()
            after = (folder / checkpoints['batch02_name_return']['gamestate_file']).read_bytes()
            if len(before) != 0x275c or len(after) != 0x275c or before[0x628:0x63e] == after[0x628:0x63e]:
                raise ValueError('Name entry did not actually change the first deck name')
            replay[kind]['menu_cancelled_and_reopened'] = True
            replay[kind]['name_changed_in_memory_then_returned'] = True
            replay[kind]['save_not_performed'] = True
    sources = []
    pictures = {'selection': deck_dir / 'PRIVATE_deck_selection.ppm', 'rename': deck_dir / 'PRIVATE_deck_menu.ppm',
                'buy': buy_dir / 'PRIVATE_insufficient_funds.ppm'}
    for entry in draft['entries']:
        n = entry['entry_index']
        row = next(x for x in f['entries'] if x['table'] is None and x['index'] == n)
        if row['raw_sha256'] != entry['original_sha256']:
            raise ValueError('Draft source hash differs from the indexed image')
        raw = bytes.fromhex(row['raw_hex'])
        tokens = tokenize(raw, variant=CN_C713)
        metric = measure(font, raw)
        r = {'file_id': 52, 'entry_index': n, 'source_sha256': row['raw_sha256'],
             'source_bytes_including_terminator': len(raw),
             'control_count': sum(x.kind == 'control' for x in tokens),
             'dynamic_insert_count': sum(x.kind == 'control' and x.control_id == 5 for x in tokens),
             'source_draw_advance_px': metric['draw_max_advance_px'], 'window_origin_px': entry['window_origin_px'],
             'window_context': entry['window_context'], 'trigger': entry['trigger'],
             'source_runtime_status': 'runtime_pending', 'translation_status': 'not_assessed_by_source_audit'}
        if n == 15:
            r['trigger_preconditions'] = ['At least one owned item with booster icon 0x62.']
        elif n == 20:
            r['trigger_preconditions'] = ['The chosen card has nine copies.',
                                          'Money is at least the card price; insufficient money is checked first.']
        # This earlier audit has source screenshots only for these three paths.
        # Later target additions must be assessed by their own runtime audit.
        if n in (19, 25, 27):
            _, x, y, context, digest = TARGETS[n]
            if digest != row['raw_sha256'] or hash_file(pictures[context]) != SCREEN_SHA[context]:
                raise ValueError('Source screenshot or expected entry hash differs')
            b = metric['ink_bbox_relative_exclusive']
            predicted = [b[0]+x, b[1]+y, b[2]+x, b[3]+y]
            observed = neutral_ink_bbox(pictures[context], (x-1, y, min(x+143,320), y+13))
            if predicted != observed:
                raise ValueError('Predicted source ink does not match its real screenshot')
            r.update({'source_runtime_status': 'actual_window_and_state_verified', 'source_ink_bbox_px': observed,
                      'source_screenshot_sha256': hash_file(pictures[context]),
                      'screenshot_ink_rule': 'neutral RGB, minimum channel >= 80 and spread < 8; scoped to these observed blue windows',
                      'game_measure_result_used_by_window': False,
                      'exact_window_clip_bounds': 'unresolved'})
        sources.append(r)
    return {'schema_version': 1, 'status': 'five_sources_checked_three_runtime_paths_verified',
            'source_image_sha256': CN_IMAGE_SHA256, 'runtime_input_batch01_sha256': P1_SHA,
            'overlay_sha1': OVERLAYS, 'entries': sources, 'runtime': replay,
            'limitations': ['Source verification does not approve candidate glyphs or a translation.',
                'Booster selection and nine-card-limit cases have not been triggered.',
                'Exact window clipping bounds and other fonts remain unresolved.',
                'Pad warnings and the upstream periodic listener-reset warning remain in the local logs; only observed state changes are reported.',
                'Original text, fonts, private screenshots and memory cards remain local.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('image', 'private_index', 'prior_audit', 'draft', 'batch_image', 'deck_dir', 'buy_dir', 'report'):
        p.add_argument(name, type=Path)
    a = p.parse_args()
    try:
        result = run(a.image, a.private_index, a.prior_audit, a.draft, a.batch_image, a.deck_dir, a.buy_dir)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        result = {'schema_version': 1, 'status': 'blocked', 'error_type': type(exc).__name__, 'error': str(exc)[:500]}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(a.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as out:
        json.dump(result, out, ensure_ascii=False, indent=2); out.write('\n')
    print(json.dumps({'status': result['status'], 'report': str(a.report)}))
    return 2 if result['status'] == 'blocked' else 0


if __name__ == '__main__':
    raise SystemExit(main())
