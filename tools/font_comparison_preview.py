#!/usr/bin/env python3
"""Create a local, synthetic comparison of licensed Chinese font candidates.

The first three panels rasterize at the game's 12-pixel cell size and enlarge
with nearest-neighbour. The last is a 48-pixel illustrative rendering, not
a claim that a high-resolution replacement is supported in-game.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTFont

LINES = ('数码宝贝世界  卡片图鉴', '选择记忆卡插槽  已持有九张')
SOURCES = (
    ('Ark Pixel 12px', 'add3145c4372eca6c386b2adf90e98fea21e848809da56ceed90f917a9caeb4e'),
    ('Fusion Pixel 12px', '2e63342fd51ba29831406c8ad8fc919868de0c9dc9277d3a11614a5bc7a26907'),
    ('Noto Sans CJK SC 12px', '2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b'),
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render(paths, output: Path):
    root = Path(__file__).resolve().parents[1]
    if not output.resolve().is_relative_to((root / 'local').resolve()) or output.exists():
        raise ValueError('Preview must be a new local file')
    panels, masks, missing = [], {}, {}
    for (label, expected), path in zip(SOURCES, paths):
        if sha(path) != expected:
            raise ValueError('Font source hash differs: ' + label)
        cmap = TTFont(path, lazy=True).getBestCmap()
        missing[label] = sorted({ord(c) for line in LINES for c in line if ord(c) not in cmap})
        if missing[label]:
            raise ValueError('A comparison glyph is absent from ' + label)
        font = ImageFont.truetype(str(path), 12)
        panel = Image.new('RGB', (320, 54), (3, 8, 38))
        d = ImageDraw.Draw(panel)
        d.rectangle((1, 1, 318, 52), outline=(62, 122, 213))
        d.text((7, 3), label, fill=(154, 202, 255), font=ImageFont.load_default(size=9))
        bits = Image.new('1', (300, 32))
        bd = ImageDraw.Draw(bits)
        for row, line in enumerate(LINES):
            bd.text((0, row * 15), line, fill=1, font=font, anchor='lt')
        panel.paste((255, 255, 255), (8, 17), bits)
        masks[label] = bits.tobytes()
        panels.append(panel.resize((1280, 216), Image.Resampling.NEAREST))
    hd = Image.new('RGB', (1280, 216), (3, 8, 38))
    d = ImageDraw.Draw(hd)
    d.rectangle((4, 4, 1275, 211), outline=(62, 122, 213), width=4)
    d.text((28, 12), 'Noto Sans CJK SC 48px (HD mock-up; not in-game)',
           fill=(154, 202, 255), font=ImageFont.load_default(size=29))
    font = ImageFont.truetype(str(paths[2]), 48)
    for row, line in enumerate(LINES):
        d.text((32, 68 + row * 60), line, fill='white', font=font, anchor='lt')
    panels.append(hd)
    combined = Image.new('RGB', (1280, sum(p.height for p in panels)))
    y = 0
    for panel in panels:
        combined.paste(panel, (0, y)); y += panel.height
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as f:
        combined.save(f, format='PNG')
    return {'preview_sha256': sha(output), 'native_ark_fusion_masks_equal': masks[SOURCES[0][0]] == masks[SOURCES[1][0]],
            'missing_comparison_codepoints': missing, 'native_font_size_px': 12, 'hd_font_size_px': 48,
            'note': 'Synthetic presentation; no game footage and no HD runtime claim.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for n in ('ark', 'fusion', 'noto', 'output'):
        p.add_argument(n, type=Path)
    a = p.parse_args()
    print(json.dumps(render((a.ark, a.fusion, a.noto), a.output), ensure_ascii=False))


if __name__ == '__main__':
    main()
