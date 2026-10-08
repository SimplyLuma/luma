#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Render the luma-firmware Plymouth theme's artwork from Luma's SVG sources.

luma-firmware keeps the image the firmware published through the ACPI BGRT
table and draws Luma's own pieces around it with Plymouth's two-step plugin.
That plugin reads fixed file names from the theme's ImageDir and draws each
image at its natural size, so every piece is rendered here, at build time, to
the exact pixel size it is shown at:

  watermark.png          the Luma wordmark, bottom centre of every screen
  throbber-NNNN.png      Luma's four-dot loader, under the firmware logo
  lock.png, entry.png,   the disk-unlock (and other) prompt; two-step refuses
  bullet.png             to start at all without these three
  keyboard.png           the layout indicator beside Plymouth's own
                         keymap-render.png, and the Caps Lock indicator
  capslock.png

Nothing is hand-drawn into a binary. The wordmark and the loader dot come
from the brand SVGs; the prompt glyphs are geometry below. rsvg-convert is the
only rasterizer, the same one the rest of the boot theme uses, and its PNGs
carry no timestamps, so a rebuild from the same sources is byte-identical.

Two-step draws every image at device scale 1 and interpolates it on a 2x
display; it has no high-density asset lookup. The artwork is therefore
designed on the 1x pixel grid, where Plymouth shows it unscaled.
"""
from __future__ import annotations

import argparse
import math
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

SVG_NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', SVG_NS)

# Luma's dark-surface palette (config/shared/design-tokens.json, the Ink
# boot surface). Firmware boot images are black in practice -- the Windows
# boot-screen requirements that vendors follow demand it -- so the light mark
# is the one that reads on them.
INK = '#f2f3f4'
SURFACE = '#21252b'
MUTED = '#bfc3c8'

# Bottom-centre wordmark. Fedora's watermark is 43 px tall; the Luma wordmark
# is a single word with a tall ascender, so the same optical weight is a
# little shorter.
WATERMARK_HEIGHT = 32

# The loader is luma-loading's own: four dots, a raised-cosine opacity pulse
# from 22 % to 100 %, each dot trailing the one before it. luma-loading pulses
# on a 1.15 s cycle with a 0.13 s stagger. Plymouth's throbber plays its
# frames over a fixed 2.0 s (THROBBER_DURATION) at 30 frames per second, so
# the loop here is two 1.0 s cycles in 60 frames with the stagger scaled by
# the same ratio; the loop point is seamless.
DOT_SIZE = 8
DOT_GAP = 8
DOT_COUNT = 4
MINIMUM_OPACITY = 0.22
THROBBER_SECONDS = 2.0
THROBBER_FRAMES = 60
CYCLES_PER_LOOP = 2
STAGGER = 0.13 * (THROBBER_SECONDS / CYCLES_PER_LOOP) / 1.15

# Prompt. Luma's standard control is 36 px tall with a 10 px island radius.
# The field is light because Plymouth draws typed question answers in black.
# Plymouth starts that text at the very left edge of entry.png and puts
# lock.png flush against it, so the field's rounded left end is drawn as the
# right edge of lock.png: the field then begins before the text does, and the
# two images meet on the same pixel column at any screen size.
ENTRY_WIDTH = 300
ENTRY_HEIGHT = 36
ENTRY_RADIUS = 10
FIELD_LEAD = 12    # visible field to the left of entry.png: the text inset
BULLET_PITCH = 16  # two-step spaces bullets by the bullet image's width
BULLET_SIZE = 8
LOCK_WIDTH = 60
LOCK_HEIGHT = 36


def svg(width: float, height: float, body: str) -> str:
    return (f'<svg xmlns="{SVG_NS}" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">{body}</svg>')


def rasterize(document: str, output: Path, width: int, height: int) -> None:
    subprocess.run(['rsvg-convert', f'--width={width}', f'--height={height}',
                    '--format=png', f'--output={output}'],
                   input=document.encode(), check=True)


def recolour(source: str, colour: str) -> ET.Element:
    """Return the brand SVG with its ink (currentColor) set to colour."""
    tree = ET.fromstring(source)
    for element in tree.iter():
        if element.get('fill') == 'currentColor':
            element.set('fill', colour)
        if element.tag == f'{{{SVG_NS}}}title':
            element.text = None
    return tree


def watermark(wordmark: str, output: Path) -> None:
    tree = recolour(wordmark, INK)
    box = [float(value) for value in tree.get('viewBox').split()]
    height = WATERMARK_HEIGHT
    width = round(height * box[2] / box[3])
    # Scale uniformly; the rounding of the width is absorbed as transparent
    # margin rather than as a stretch of the canonical geometry.
    tree.set('width', str(width))
    tree.set('height', str(height))
    tree.set('preserveAspectRatio', 'xMidYMid meet')
    rasterize(ET.tostring(tree, encoding='unicode'), output, width, height)


def dot_opacity(time: float, index: int) -> float:
    cycle = THROBBER_SECONDS / CYCLES_PER_LOOP
    phase = ((time - index * STAGGER) % cycle) / cycle
    return MINIMUM_OPACITY + (1 - MINIMUM_OPACITY) * (1 - math.cos(2 * math.pi * phase)) / 2


def throbber(dot: str, directory: Path) -> None:
    source = ET.fromstring(dot)
    box = source.get('viewBox')
    shapes = ''.join(ET.tostring(child, encoding='unicode') for child in source)
    width = DOT_COUNT * DOT_SIZE + (DOT_COUNT - 1) * DOT_GAP
    for frame in range(THROBBER_FRAMES):
        time = frame * THROBBER_SECONDS / THROBBER_FRAMES
        body = ''
        for index in range(DOT_COUNT):
            opacity = dot_opacity(time, index)
            body += (f'<svg x="{index * (DOT_SIZE + DOT_GAP)}" y="0" '
                     f'width="{DOT_SIZE}" height="{DOT_SIZE}" viewBox="{box}">'
                     f'<g fill="{INK}" opacity="{opacity:.4f}">{shapes}</g></svg>')
        document = svg(width, DOT_SIZE, body)
        # The brand dot hard-codes its dark fill on the shape; the group fill
        # only applies once that attribute is gone.
        document = document.replace(' fill="#21252B"', '')
        rasterize(document, directory / f'throbber-{frame + 1:04d}.png', width, DOT_SIZE)


def field(offset: float, fill: str = INK) -> str:
    """The whole rounded field, positioned so its left end starts at offset."""
    return (f'<rect x="{offset}" y="0" width="{FIELD_LEAD + ENTRY_WIDTH}" height="{ENTRY_HEIGHT}" '
            f'rx="{ENTRY_RADIUS}" fill="{fill}"/>')


# The prompt pieces luma-firmware's two-step names; luma-loading draws the
# same pieces from its script under its own names.
TWO_STEP_PROMPT = {'entry': 'entry.png', 'bullet': 'bullet.png', 'lock': 'lock.png',
                   'keyboard': 'keyboard.png', 'capslock': 'capslock.png'}


def prompt(directory: Path, ink: str = INK, surface: str = SURFACE, muted: str = MUTED,
           names: dict[str, str] = TWO_STEP_PROMPT) -> None:
    """Render the unlock prompt: a light field on a dark surface (or the
    reverse), bullets in the surface colour, a padlock and the indicators."""
    rasterize(svg(ENTRY_WIDTH, ENTRY_HEIGHT, field(-FIELD_LEAD, ink)),
              directory / names['entry'], ENTRY_WIDTH, ENTRY_HEIGHT)
    rasterize(svg(BULLET_PITCH, BULLET_PITCH,
                  f'<circle cx="{BULLET_PITCH / 2}" cy="{BULLET_PITCH / 2}" '
                  f'r="{BULLET_SIZE / 2}" fill="{surface}"/>'),
              directory / names['bullet'], BULLET_PITCH, BULLET_PITCH)
    # A padlock 18 x 24 on the grid, 18 px clear of the field, followed by
    # the field's rounded left end.
    lock = (f'<path d="M15 16.5v-4a6 6 0 0 1 12 0v4" fill="none" stroke="{ink}" '
            'stroke-width="2.5" stroke-linecap="round"/>'
            f'<rect x="12" y="16" width="18" height="14" rx="3.5" fill="{ink}"/>'
            + field(LOCK_WIDTH - FIELD_LEAD, ink))
    rasterize(svg(LOCK_WIDTH, LOCK_HEIGHT, lock), directory / names['lock'], LOCK_WIDTH, LOCK_HEIGHT)
    if 'keyboard' in names:
        # Plymouth renders the layout name itself (keymap-render.png) in a
        # 75 % grey; the indicator glyphs match it.
        keyboard = (f'<rect x="4" y="10" width="32" height="21" rx="4.5" fill="none" '
                    f'stroke="{muted}" stroke-width="2.25"/>'
                    + ''.join(f'<rect x="{x}" y="{y}" width="3.5" height="3" rx="1" fill="{muted}"/>'
                              for y in (14.5, 19.5) for x in (10, 15.5, 21, 26.5))
                    + f'<rect x="13" y="24.5" width="14" height="3" rx="1" fill="{muted}"/>')
        rasterize(svg(40, 40, keyboard), directory / names['keyboard'], 40, 40)
    capslock = (f'<path d="M12 3.5 3.5 13H8v5.5h8V13h4.5Z" fill="none" stroke="{muted}" '
                'stroke-width="2" stroke-linejoin="round"/>'
                f'<rect x="8" y="21.5" width="8" height="2.5" rx="1" fill="{muted}"/>')
    rasterize(svg(24, 28, capslock), directory / names['capslock'], 24, 28)


def render(wordmark: Path, dot: Path, directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob('*.png'):
        stale.unlink()
    watermark(wordmark.read_text(), directory / 'watermark.png')
    throbber(dot.read_text(), directory)
    prompt(directory)
    return sorted(directory.glob('*.png'))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--wordmark', type=Path, required=True,
                        help='canonical luma-wordmark.svg')
    parser.add_argument('--dot', type=Path, required=True,
                        help='canonical luma-loading-dot.svg')
    parser.add_argument('--output', type=Path, required=True,
                        help='theme ImageDir to (re)populate')
    args = parser.parse_args()
    for path in render(args.wordmark, args.dot, args.output):
        print(path.name)


if __name__ == '__main__':
    main()
