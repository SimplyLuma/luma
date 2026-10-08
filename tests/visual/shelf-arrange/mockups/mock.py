#!/usr/bin/env python3
"""Annotated mockups for ADR-044 (movable shelf islands).

Every scene is laid out by the same rules the ADR specifies (zones, 9px
attach gutter, 24px band separation, 14px inset, struts), so the pictures are
a check on the model as well as an illustration of it.
"""
import html
import os
import subprocess

W, H = 1440, 900
INSET, THICK, GAP, SEP, PAD, RADIUS = 14, 56, 9, 24, 10, 15
RESERVE = INSET + THICK + PAD
OUT = os.environ.get("SHELF_ARRANGE_MOCKUP_DIR", os.path.dirname(os.path.abspath(__file__)))

MODES = {
    'dark': dict(island='#21252B', stroke='rgba(255,255,255,0.06)', ink='#E7E9E8', ink2='#9BA1A8',
                 tile='rgba(255,255,255,0.072)', line='rgba(255,255,255,0.10)', state='#8a99a8',
                 scrim='rgba(0,0,0,0.28)', outline='rgba(255,255,255,0.22)', card='#252a30',
                 cardBorder='rgba(255,255,255,0.075)', shadow=0.34),
    'light': dict(island='#FFFFFF', stroke='rgba(25,27,31,0.07)', ink='#23272d', ink2='#6b7280',
                  tile='rgba(35,39,45,0.06)', line='rgba(25,27,31,0.12)', state='#61728a',
                  scrim='rgba(20,24,30,0.18)', outline='rgba(25,27,31,0.20)', card='#f6f7f8',
                  cardBorder='rgba(35,44,52,0.09)', shadow=0.16),
    'frost': dict(island='rgba(28,33,40,0.86)', stroke='rgba(255,255,255,0.14)', ink='#ffffff',
                  ink2='rgba(255,255,255,0.66)', tile='rgba(10,20,33,0.20)', line='rgba(255,255,255,0.18)',
                  state='#a9b5c1', scrim='rgba(0,0,0,0.28)', outline='rgba(255,255,255,0.26)',
                  card='rgba(34,40,48,0.90)', cardBorder='rgba(255,255,255,0.14)', shadow=0.30),
    'glass': dict(island='rgba(14,19,26,0.78)', stroke='rgba(255,255,255,0.22)', ink='#ffffff',
                  ink2='rgba(255,255,255,0.66)', tile='rgba(255,255,255,0.10)', line='rgba(255,255,255,0.22)',
                  state='#a9b5c1', scrim='rgba(0,0,0,0.28)', outline='rgba(255,255,255,0.26)',
                  card='rgba(20,26,34,0.82)', cardBorder='rgba(255,255,255,0.22)', shadow=0.30),
}

APPS = [('#3d7be0', 'F'), ('#e8583f', 'V'), ('#2fa66a', 'M'), ('#f2b233', 'N'),
        ('#8a5cf0', 'T'), ('#1f9bb5', 'C'), ('#5b6470', '>_')]
STATUS = {'well', 'quick-options', 'clock'}
NAMES = {'dock': 'Dock', 'live': 'Timer', 'media': 'Music', 'notifications': 'Notifications',
         'well': 'Well', 'quick-options': 'Quick Options', 'clock': 'Clock'}


def part_length(island, vertical):
    """Content length of one island (without the island's own padding)."""
    if island == 'dock':
        return len(APPS) * 36 + (len(APPS) - 1) * 6
    if vertical:
        return {'live': 36 + 6 + 16 + 6 + 28, 'media': 36 + 8 + 3 * 28, 'notifications': 36 + 4 + 16,
                'well': 36, 'quick-options': 3 * 18 + 2 * 6, 'clock': 58}[island]
    return {'live': 144, 'media': 270, 'notifications': 52, 'well': 36,
            'quick-options': 76, 'clock': 76}[island]


def fuse(members):
    """Adjacent status parts fuse into one painted island (ADR-044 section 2)."""
    out = []
    for island in members:
        if island in STATUS and out and isinstance(out[-1], list):
            out[-1].append(island)
        elif island in STATUS:
            out.append([island])
        else:
            out.append(island)
    return [tuple(x) if isinstance(x, list) else x for x in out]


def painted_length(painted, vertical):
    if isinstance(painted, tuple):
        parts = [part_length(p, vertical) for p in painted]
        return 2 * PAD + sum(parts) + (len(parts) - 1) * (1 + 2 * PAD)
    return 2 * PAD + part_length(painted, vertical)


class Group:
    def __init__(self, edge, anchor, islands, position=0.5, monitor=0):
        self.edge, self.anchor, self.islands = edge, anchor, list(islands)
        self.position, self.monitor = position, monitor

    @property
    def vertical(self):
        return self.edge in ('left', 'right')

    def painted(self):
        return fuse(self.islands)

    def length(self, extra=0):
        painted = self.painted()
        return sum(painted_length(p, self.vertical) for p in painted) + GAP * (len(painted) - 1) + extra


def solve(groups, w=W, h=H, ox=0, oy=0, opened=None):
    """Place every group. Returns {group: (x, y, w, h)} plus island rects."""
    bands = {g.edge for g in groups}
    placed = {}
    for edge in ('top', 'bottom', 'left', 'right'):
        members = [g for g in groups if g.edge == edge]
        if not members:
            continue
        vertical = edge in ('left', 'right')
        if vertical:
            a0 = INSET + (THICK + INSET if 'top' in bands else 0)
            a1 = h - INSET - (THICK + INSET if 'bottom' in bands else 0)
        else:
            a0, a1 = INSET, w - INSET
        items = []
        for g in members:
            extra = opened[1] if opened and opened[0] is g else 0
            length = g.length(extra)
            ideal = {'start': a0, 'end': a1 - length, 'center': (a0 + a1) / 2 - length / 2,
                     'free': a0 + g.position * (a1 - a0) - length / 2}[g.anchor]
            items.append([ideal, length, g])
        items.sort(key=lambda item: item[0])
        cursor = a0
        for item in items:
            item[0] = max(item[0], cursor)
            cursor = item[0] + item[1] + SEP
        cursor = a1
        for item in reversed(items):
            item[0] = min(item[0], cursor - item[1])
            cursor = item[0] - SEP
        for start, length, g in items:
            start = round(start)
            if vertical:
                x = INSET if edge == 'left' else w - INSET - THICK
                placed[g] = (ox + x, oy + start, THICK, length)
            else:
                y = INSET if edge == 'top' else h - INSET - THICK
                placed[g] = (ox + start, oy + y, length, THICK)
    return placed


def island_rects(g, rect, opened=None):
    x, y, _w, _h = rect
    out = []
    cursor = 0
    for index, painted in enumerate(g.painted()):
        if opened and opened[0] is g and opened[2] == index:
            cursor += opened[1]
        length = painted_length(painted, g.vertical)
        out.append((painted, (x, y + cursor, THICK, length) if g.vertical else (x + cursor, y, length, THICK)))
        cursor += length + GAP
    return out


def work_area(groups, w=W, h=H):
    edges = {g.edge for g in groups}
    left = RESERVE if 'left' in edges else PAD
    right = RESERVE if 'right' in edges else PAD
    top = RESERVE if 'top' in edges else PAD
    bottom = RESERVE if 'bottom' in edges else PAD
    return (left, top, w - left - right, h - top - bottom)


# ---------------------------------------------------------------- drawing

def esc(text):
    return html.escape(text, quote=True)


def defs(mode):
    m = MODES[mode]
    return f'''<defs>
<linearGradient id="wall" x1="0" y1="0" x2="1" y2="1">
  <stop offset="0" stop-color="#1b3a5c"/><stop offset=".55" stop-color="#3f5f86"/><stop offset="1" stop-color="#d69a74"/></linearGradient>
<radialGradient id="glow" cx=".72" cy=".78" r=".55"><stop offset="0" stop-color="#f4c28e" stop-opacity=".55"/><stop offset="1" stop-color="#f4c28e" stop-opacity="0"/></radialGradient>
<filter id="isl" x="-20%" y="-60%" width="140%" height="240%">
  <feDropShadow dx="0" dy="1" stdDeviation="1" flood-color="#000" flood-opacity="{m['shadow'] * .6}"/>
  <feDropShadow dx="0" dy="6" stdDeviation="9" flood-color="#000" flood-opacity="{m['shadow']}"/></filter>
<filter id="lift" x="-30%" y="-80%" width="160%" height="300%">
  <feDropShadow dx="0" dy="2" stdDeviation="2" flood-color="#000" flood-opacity="{m['shadow'] * .8}"/>
  <feDropShadow dx="0" dy="16" stdDeviation="18" flood-color="#000" flood-opacity="{min(.6, m['shadow'] * 1.5)}"/></filter>
<filter id="card" x="-20%" y="-20%" width="140%" height="160%">
  <feDropShadow dx="0" dy="10" stdDeviation="16" flood-color="#0c1015" flood-opacity=".35"/></filter>
<filter id="blur"><feGaussianBlur stdDeviation="18"/></filter>
</defs>'''


def wallpaper(w=W, h=H, ox=0, oy=0):
    return (f'<rect x="{ox}" y="{oy}" width="{w}" height="{h}" fill="url(#wall)"/>'
            f'<rect x="{ox}" y="{oy}" width="{w}" height="{h}" fill="url(#glow)"/>'
            f'<path d="M{ox} {oy + h * .70} C {ox + w * .3} {oy + h * .58}, {ox + w * .6} {oy + h * .86}, {ox + w} {oy + h * .64} L {ox + w} {oy + h} L {ox} {oy + h} Z" fill="#132a44" opacity=".55"/>')


def window(rect, mode, title='Filer — Documents'):
    x, y, w, h = rect
    light = mode == 'light'
    body = '#f6f7f8' if light else '#1d2126'
    bar = '#ffffff' if light else '#262b31'
    ink = '#23272d' if light else '#e7e9e8'
    faint = 'rgba(25,27,31,.08)' if light else 'rgba(255,255,255,.06)'
    rows = ''.join(f'<rect x="{x + 232}" y="{y + 70 + i * 34}" width="{w - 262}" height="22" rx="6" fill="{faint}"/>'
                   for i in range(max(0, int((h - 90) / 34))))
    side = ''.join(f'<rect x="{x + 18}" y="{y + 64 + i * 30}" width="{150 - (i % 3) * 22}" height="12" rx="6" fill="{faint}"/>'
                   for i in range(max(0, min(9, int((h - 90) / 30)))))
    return f'''<g>
<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{body}" stroke="rgba(0,0,0,.18)"/>
<path d="M{x} {y + 12} a12 12 0 0 1 12 -12 h{w - 24} a12 12 0 0 1 12 12 v34 h{-w} z" fill="{bar}"/>
<rect x="{x}" y="{y + 46}" width="{w}" height="1" fill="{faint}"/>
<rect x="{x + 200}" y="{y + 47}" width="1" height="{h - 47}" fill="{faint}"/>
<text x="{x + w / 2}" y="{y + 28}" text-anchor="middle" font-size="13" font-weight="650" fill="{ink}">{esc(title)}</text>
<g fill="{ink}" opacity=".55"><circle cx="{x + w - 76}" cy="{y + 23}" r="5"/><circle cx="{x + w - 52}" cy="{y + 23}" r="5"/><circle cx="{x + w - 28}" cy="{y + 23}" r="5"/></g>
{side}{rows}</g>'''


def icon_tile(x, y, color, glyph, size=36):
    return (f'<rect x="{x}" y="{y}" width="{size}" height="{size}" rx="{size * 10 / 36}" fill="{color}"/>'
            f'<text x="{x + size / 2}" y="{y + size / 2 + 5}" text-anchor="middle" font-size="{15 if len(glyph) == 1 else 12}" '
            f'font-weight="700" fill="#fff">{esc(glyph)}</text>')


def glyph_play(cx, cy, ink, kind):
    if kind == 'play':
        return f'<rect x="{cx - 5}" y="{cy - 7}" width="3.5" height="14" rx="1" fill="{ink}"/><rect x="{cx + 1.5}" y="{cy - 7}" width="3.5" height="14" rx="1" fill="{ink}"/>'
    if kind == 'prev':
        return f'<path d="M{cx + 5} {cy - 6} L{cx - 3} {cy} L{cx + 5} {cy + 6} Z M{cx - 5} {cy - 6} v12" stroke="{ink}" stroke-width="1.6" fill="none" opacity=".55" stroke-linejoin="round"/>'
    return f'<path d="M{cx - 5} {cy - 6} L{cx + 3} {cy} L{cx - 5} {cy + 6} Z M{cx + 5} {cy - 6} v12" stroke="{ink}" stroke-width="1.6" fill="none" opacity=".55" stroke-linejoin="round"/>'


def small_glyph(name, cx, cy, ink):
    st = f'stroke="{ink}" stroke-width="1.4" fill="none" stroke-linecap="round" stroke-linejoin="round"'
    if name == 'mic':
        return f'<rect x="{cx - 2.5}" y="{cy - 6}" width="5" height="8" rx="2.5" {st}/><path d="M{cx - 5} {cy} a5 5 0 0 0 10 0 M{cx} {cy + 5} v2" {st}/>'
    if name == 'wifi':
        return (f'<path d="M{cx - 7} {cy - 2} a10 10 0 0 1 14 0 M{cx - 4.5} {cy + 1} a6.5 6.5 0 0 1 9 0" {st}/>'
                f'<circle cx="{cx}" cy="{cy + 4}" r="1.3" fill="{ink}"/>')
    if name == 'bt':
        return f'<path d="M{cx - 4} {cy - 3} l8 6 l-4 3 v-12 l4 3 l-8 6" {st}/>'
    if name == 'vol':
        return f'<path d="M{cx - 6} {cy - 2} h3 l4 -3 v10 l-4 -3 h-3 z M{cx + 4} {cy - 3} a4 4 0 0 1 0 6" {st}/>'
    if name == 'gauge':
        return f'<path d="M{cx - 6} {cy + 3} a6 6 0 1 1 12 0 M{cx} {cy + 2} l3 -4" {st}/>'
    return (f'<rect x="{cx - 6}" y="{cy - 3.5}" width="11" height="7" rx="1.5" {st}/>'
            f'<rect x="{cx - 4.5}" y="{cy - 2}" width="6" height="4" rx=".5" fill="{ink}"/><path d="M{cx + 6.5} {cy - 1} v2" {st}/>')


def draw_part(part, x, y, vertical, m, edge):
    """Content of one island part, laid out from (x, y) at its content origin."""
    ink, ink2, tile = m['ink'], m['ink2'], m['tile']
    out = []
    if part == 'dock':
        for i, (color, glyph) in enumerate(APPS):
            tx, ty = (x, y + i * 42) if vertical else (x + i * 42, y)
            out.append(icon_tile(tx, ty, color, glyph))
            if i in (0, 1, 3):
                focused = i == 1
                length = 12 if focused else 4
                if vertical:
                    dx = (x - 8) if edge == 'left' else (x + 36 + 4)
                    dx = x - 8 if edge == 'left' else x + 40
                    out.append(f'<rect x="{dx}" y="{ty + 18 - length / 2}" width="4" height="{length}" rx="2" fill="{ink}"/>')
                else:
                    dy = y + 40 if edge == 'bottom' else y - 8
                    out.append(f'<rect x="{tx + 18 - length / 2}" y="{dy}" width="{length}" height="4" rx="2" fill="{ink}"/>')
    elif part == 'live':
        out.append(f'<rect x="{x}" y="{y}" width="36" height="36" rx="10" fill="{tile}"/>')
        out.append(f'<circle cx="{x + 18}" cy="{y + 19}" r="8" fill="none" stroke="{ink}" stroke-width="1.6"/><path d="M{x + 18} {y + 14} v5 l3 2" stroke="{ink}" stroke-width="1.6" fill="none" stroke-linecap="round"/>')
        if vertical:
            out.append(f'<text x="{x + 18}" y="{y + 56}" text-anchor="middle" font-size="12" font-weight="700" fill="{ink}">4:12</text>')
            out.append(f'<rect x="{x + 4}" y="{y + 66}" width="28" height="28" rx="8" fill="{tile}"/><rect x="{x + 13}" y="{y + 75}" width="10" height="10" rx="2" fill="{ink}"/>')
        else:
            out.append(f'<text x="{x + 48}" y="{y + 15}" font-size="13" font-weight="700" fill="{ink}">Tea</text>')
            out.append(f'<text x="{x + 48}" y="{y + 31}" font-size="11.5" font-weight="500" fill="{ink2}">4:12 left</text>')
    elif part == 'media':
        out.append(f'<rect x="{x}" y="{y}" width="36" height="36" rx="10" fill="#e9e5dc"/>')
        out.append(f'<text x="{x + 18}" y="{y + 25}" text-anchor="middle" font-size="18" fill="#2a2a2a">♫</text>')
        if vertical:
            for i, kind in enumerate(('prev', 'play', 'next')):
                out.append(glyph_play(x + 18, y + 36 + 8 + 14 + i * 28, ink, kind))
        else:
            out.append(f'<text x="{x + 48}" y="{y + 15}" font-size="13" font-weight="700" fill="{ink}">Harvest Moon</text>')
            out.append(f'<text x="{x + 48}" y="{y + 31}" font-size="11.5" font-weight="500" fill="{ink2}">Neil Young</text>')
            for i, kind in enumerate(('prev', 'play', 'next')):
                out.append(glyph_play(x + 190 + i * 30, y + 18, ink, kind))
    elif part == 'notifications':
        bx, by = (x + 18, y + 16) if vertical else (x + 16, y + 18)
        out.append(f'<path d="M{bx - 7} {by + 5} h14 l-2 -3 v-5 a5 5 0 0 0 -10 0 v5 z" fill="none" stroke="{ink}" stroke-width="1.6" stroke-linejoin="round"/><path d="M{bx - 2} {by + 8} a2 2 0 0 0 4 0" stroke="{ink}" stroke-width="1.6" fill="none"/>')
        if vertical:
            out.append(f'<text x="{x + 18}" y="{y + 50}" text-anchor="middle" font-size="13" font-weight="700" fill="{ink}">3</text>')
        else:
            out.append(f'<text x="{x + 38}" y="{y + 23}" font-size="13" font-weight="700" fill="{ink}">3</text>')
    elif part == 'well':
        out.append(f'<rect x="{x}" y="{y}" width="36" height="36" rx="10" fill="{tile}"/><rect x="{x + 12}" y="{y + 12}" width="12" height="12" rx="2" fill="{ink}" opacity=".8"/>')
    elif part == 'quick-options':
        cols = 2 if vertical else 3
        names = ['mic', 'wifi', 'bt', 'vol', 'gauge', 'batt']
        for index, name in enumerate(names):
            r, c = divmod(index, cols)
            gx = x + (11 + c * 14 if vertical else 14 + c * 24)
            gy = y + (10 + r * 20 if vertical else 10 + r * 17)
            out.append(small_glyph(name, gx, gy, ink))
    elif part == 'clock':
        if vertical:
            out.append(f'<text x="{x + 18}" y="{y + 16}" text-anchor="middle" font-size="12.5" font-weight="700" fill="{ink}">18:03</text>')
            out.append(f'<text x="{x + 18}" y="{y + 34}" text-anchor="middle" font-size="10" font-weight="600" fill="{ink2}">Fri</text>')
            out.append(f'<text x="{x + 18}" y="{y + 48}" text-anchor="middle" font-size="10" font-weight="600" fill="{ink2}">Sep 18</text>')
        else:
            out.append(f'<text x="{x + 38}" y="{y + 16}" text-anchor="middle" font-size="13" font-weight="700" fill="{ink}">18:03</text>')
            out.append(f'<text x="{x + 38}" y="{y + 31}" text-anchor="middle" font-size="11" font-weight="600" fill="{ink2}">Fri Sep 18</text>')
    return ''.join(out)


def draw_island(painted, rect, vertical, mode, edge, lifted=False, opacity=1, scale=1.0):
    m = MODES[mode]
    x, y, w, h = rect
    cx, cy = x + w / 2, y + h / 2
    transform = f' transform="translate({cx} {cy - (5 if lifted else 0)}) scale({scale}) translate({-cx} {-cy})"' if scale != 1 else ''
    out = [f'<g opacity="{opacity}"{transform}>',
           f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{RADIUS}" fill="{m["island"]}" filter="url(#{"lift" if lifted else "isl"})"/>',
           f'<rect x="{x + .5}" y="{y + .5}" width="{w - 1}" height="{h - 1}" rx="{RADIUS - .5}" fill="none" stroke="{m["stroke"]}"/>']
    parts = painted if isinstance(painted, tuple) else (painted,)
    cursor = PAD
    for index, part in enumerate(parts):
        length = part_length(part, vertical)
        px, py = (x + PAD, y + cursor) if vertical else (x + cursor, y + PAD)
        out.append(draw_part(part, px, py, vertical, m, edge))
        cursor += length
        if index < len(parts) - 1:
            if vertical:
                out.append(f'<rect x="{x + 16}" y="{y + cursor + PAD}" width="24" height="1" fill="{m["line"]}"/>')
            else:
                out.append(f'<rect x="{x + cursor + PAD}" y="{y + 16}" width="1" height="24" fill="{m["line"]}"/>')
            cursor += 1 + 2 * PAD
    out.append('</g>')
    return ''.join(out)


def draw_groups(groups, mode, placed, skip=None, opened=None, dim=None):
    out = []
    for g in groups:
        for painted, rect in island_rects(g, placed[g], opened):
            if skip and painted == skip[1] and g is skip[0]:
                continue
            out.append(draw_island(painted, rect, g.vertical, mode, g.edge,
                                   opacity=(0.92 if dim else 1)))
    return ''.join(out)


def zone_slots(groups, w=W, h=H):
    """Empty zone placeholders: (edge, anchor, rect)."""
    bands = {g.edge for g in groups}
    occupied = {(g.edge, g.anchor) for g in groups}
    slots = []
    L = 120
    for edge in ('top', 'bottom', 'left', 'right'):
        for anchor in ('start', 'center', 'end'):
            if (edge, anchor) in occupied:
                continue
            if edge in ('top', 'bottom'):
                y = INSET if edge == 'top' else h - INSET - THICK
                x = {'start': INSET, 'center': w / 2 - L / 2, 'end': w - INSET - L}[anchor]
                slots.append((edge, anchor, (x, y, L, THICK)))
            else:
                a0 = INSET + (THICK + INSET if 'top' in bands or True else 0)
                a1 = h - INSET - (THICK + INSET)
                x = INSET if edge == 'left' else w - INSET - THICK
                y = {'start': a0, 'center': (a0 + a1) / 2 - L / 2, 'end': a1 - L}[anchor]
                slots.append((edge, anchor, (x, y, THICK, L)))
    return slots


def dashed(rect, color, width=1.5, dash='5 5', radius=RADIUS, fill='none'):
    x, y, w, h = rect
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{color}" stroke-width="{width}" stroke-dasharray="{dash}"/>'


def outline(rect, color, grow=6):
    x, y, w, h = rect
    return f'<rect x="{x - grow}" y="{y - grow}" width="{w + 2 * grow}" height="{h + 2 * grow}" rx="{RADIUS + grow}" fill="none" stroke="{color}" stroke-width="1"/>'


def grip(g, rect, color):
    x, y, w, h = rect
    if g.vertical:
        gx = x + w + 8 if g.edge == 'left' else x - 8 - 6
        return f'<rect x="{gx}" y="{y + h / 2 - 12}" width="6" height="24" rx="3" fill="{color}"/>'
    gy = y - 8 - 6 if g.edge == 'bottom' else y + h + 8
    return f'<rect x="{x + w / 2 - 12}" y="{gy}" width="24" height="6" rx="3" fill="{color}"/>'


def arrange_card(mode, w=W, h=H, x=None, y=None):
    m = MODES[mode]
    cw, ch = 360, 112
    x = w / 2 - cw / 2 if x is None else x
    y = h / 2 - ch / 2 if y is None else y
    btn = m['tile']
    return f'''<g filter="url(#card)">
<rect x="{x}" y="{y}" width="{cw}" height="{ch}" rx="14" fill="{m['card']}" stroke="{m['cardBorder']}"/></g>
<text x="{x + 20}" y="{y + 32}" font-size="14" font-weight="700" fill="{m['ink']}">Arrange islands</text>
<text x="{x + 20}" y="{y + 52}" font-size="12" font-weight="500" fill="{m['ink2']}">Drag to move. Drop beside an island to attach it.</text>
<rect x="{x + 20}" y="{y + 68}" width="86" height="28" rx="8" fill="{btn}"/>
<text x="{x + 63}" y="{y + 86}" text-anchor="middle" font-size="12.5" font-weight="600" fill="{m['ink']}">Reset</text>
<rect x="{x + cw - 106}" y="{y + 68}" width="86" height="28" rx="8" fill="{m['state']}"/>
<text x="{x + cw - 63}" y="{y + 86}" text-anchor="middle" font-size="12.5" font-weight="650" fill="{'#ffffff' if mode == 'light' else '#161a1f'}">Done</text>'''


def callout(n, x, y, tx=None, ty=None):
    line = f'<line x1="{x}" y1="{y}" x2="{tx}" y2="{ty}" stroke="#ffcf5a" stroke-width="1.5"/>' if tx is not None else ''
    return (f'{line}<circle cx="{x}" cy="{y}" r="12" fill="#ffcf5a" stroke="#1b1b1b" stroke-width="1"/>'
            f'<text x="{x}" y="{y + 4.5}" text-anchor="middle" font-size="13" font-weight="800" fill="#1b1b1b">{n}</text>')


def legend(items, y0, w=W):
    out = [f'<rect x="0" y="{y0}" width="{w}" height="{40 + 26 * len(items)}" fill="#111418"/>']
    for i, text in enumerate(items):
        yy = y0 + 30 + i * 26
        out.append(callout(i + 1, 34, yy - 5))
        out.append(f'<text x="58" y="{yy}" font-size="14.5" font-weight="500" fill="#e7e9e8">{esc(text)}</text>')
    return ''.join(out), 40 + 26 * len(items)


def scene(name, title, body, mode='dark', notes=(), height=H):
    leg, lh = legend(notes, height) if notes else ('', 0)
    total = height + lh + 44
    side = max(W, total)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{side}" height="{side}" viewBox="0 -44 {side} {side}" '
           f'font-family="Inter, -apple-system, Helvetica Neue, sans-serif">{defs(mode)}'
           f'<rect x="0" y="-44" width="{W}" height="44" fill="#111418"/>'
           f'<text x="20" y="-15" font-size="17" font-weight="700" fill="#e7e9e8">{esc(title)}</text>'
           f'{body}{leg}</svg>')
    path = os.path.join(OUT, f'{name}.svg')
    with open(path, 'w') as f:
        f.write(svg)
    return path, total


def desktop(groups, mode, with_window=True, title='Filer — Documents'):
    placed = solve(groups)
    wa = work_area(groups)
    body = wallpaper()
    if with_window:
        body += window(wa, mode, title)
    body += draw_groups(groups, mode, placed)
    return body, placed, wa


def strut_marks(groups, wa):
    x, y, w, h = wa
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="#ffcf5a" stroke-width="1.5" stroke-dasharray="8 6" opacity=".9"/>'
            f'<text x="{x + 10}" y="{y + h - 10}" font-size="12" font-weight="700" fill="#ffcf5a">work area (maximized windows fill this)</text>')


# ---------------------------------------------------------------- scenes

def default_groups():
    return [Group('bottom', 'center', ['dock', 'live', 'media', 'notifications', 'well', 'quick-options', 'clock'])]


def build():
    paths = []

    # 1 Default
    groups = default_groups()
    body, placed, wa = desktop(groups, 'dark')
    body += strut_marks(groups, wa)
    body += callout(1, placed[groups[0]][0] + 40, placed[groups[0]][1] - 30)
    status = island_rects(groups[0], placed[groups[0]])[-1][1]
    body += callout(2, status[0] + status[2] / 2, status[1] - 30)
    paths.append(scene('01-default', '1 · Default layout (unchanged from Shell .114)', body, notes=[
        'One bottom-centre group: dock, timer, music, notifications, then the fused status island. Islands 9 px apart.',
        'Well, Quick Options and Clock are three placeable parts. Side by side, they fuse into today\'s status island.',
        'Dashed line: the work area. The bottom band reserves 14 + 56 + 10 px, so maximized windows stop above it.']))

    # 2 Arrange mode entered by holding the music island
    groups = default_groups()
    placed = solve(groups)
    wa = work_area(groups)
    m = MODES['dark']
    body = wallpaper() + window(wa, 'dark') + f'<rect x="0" y="0" width="{W}" height="{H}" fill="{m["scrim"]}"/>'
    g = groups[0]
    rects = island_rects(g, placed[g])
    for slot in zone_slots(groups):
        body += dashed(slot[2], m['outline'])
    body += outline(placed[g], m['outline'])
    body += grip(g, placed[g], m['outline'].replace('.22', '.6'))
    for painted, rect in rects:
        if painted == 'media':
            body += draw_island(painted, rect, False, 'dark', 'bottom', lifted=True, scale=1.04)
            media_rect = rect
        else:
            body += draw_island(painted, rect, False, 'dark', 'bottom')
    body += arrange_card('dark')
    body += callout(1, media_rect[0] + media_rect[2] / 2, media_rect[1] - 34)
    body += callout(2, INSET + 140, INSET + THICK / 2)
    body += callout(3, placed[g][0] + placed[g][2] / 2 + 28, placed[g][1] - 17)
    body += callout(4, W / 2 + 200, H / 2 - 56)
    body += callout(5, W / 2, 200)
    paths.append(scene('02-arrange-mode', '2 · Hold the music island for 400 ms: arrange mode', body, notes=[
        'The held island lifts (scale 1.04, deeper shadow) and is already grabbed. Move to drag; release to keep arranging.',
        'Every empty zone shows a faint dashed slot: 4 edges × start / centre / end. Corners are the top and bottom ends.',
        'Groups get a hairline outline 6 px out, plus a grip. Drag the grip to move the whole group together.',
        'The arrange card: Reset and Done. Esc, Enter or a click on the empty desktop also finish.',
        'A scrim dims windows. Arrange mode is modal, like a menu, so windows take no clicks.']))

    # 3 Dock dragged toward bottom-left: free placement preview
    groups = default_groups()
    placed = solve(groups)
    body = wallpaper() + window(wa, 'dark') + f'<rect x="0" y="0" width="{W}" height="{H}" fill="{m["scrim"]}"/>'
    dragged = 'dock'
    remaining = Group('bottom', 'center', groups[0].islands[1:])
    target = Group('bottom', 'start', ['dock'])
    rp = solve([remaining, target])
    for slot in zone_slots([Group('bottom', 'center', [])]):
        if slot[0] == 'bottom' and slot[1] == 'start':
            continue
        body += dashed(slot[2], m['outline'])
    ghost_len = target.length()
    zone = rp[target]
    body += f'<rect x="{zone[0] - 6}" y="{zone[1] - 6}" width="{zone[2] + 12}" height="{zone[3] + 12}" rx="{RADIUS + 6}" fill="{m["state"]}" fill-opacity=".18" stroke="{m["state"]}" stroke-width="1.5"/>'
    body += dashed(zone, m['ink'], dash='6 4')
    body += outline(rp[remaining], m['outline'])
    body += draw_groups([remaining], 'dark', rp)
    drag_rect = (170, H - INSET - THICK - 70, ghost_len, THICK)
    body += draw_island('dock', drag_rect, False, 'dark', 'bottom', lifted=True, scale=1.04)
    body += f'<path d="M{drag_rect[0] + 150} {drag_rect[1] + 30} l 0 22 l 6 -6 l 7 12 l 4 -2 l -7 -12 l 8 -1 z" fill="#fff" stroke="#000" stroke-width="1"/>'
    body += callout(1, zone[0] + zone[2] / 2, zone[1] - 20)
    body += callout(2, drag_rect[0] + drag_rect[2] + 22, drag_rect[1] + 10)
    body += callout(3, rp[remaining][0] + rp[remaining][2] / 2, rp[remaining][1] - 22)
    paths.append(scene('03-drag-dock-free', '3 · Dragging the dock to the bottom-left corner: "place freely here"', body, notes=[
        'Free-placement preview: the zone fills with the state colour at 18% and a dashed ghost shows where the dock will land.',
        'The dragged island moves by translation only. It keeps its form until the drop; nothing else relayouts.',
        'The group the dock left closes its gap, and the band\'s groups slide to where the drop will put them (24 px apart).']))

    # 4 Result: dock bottom-left, then back to middle
    groups = [Group('bottom', 'start', ['dock']),
              Group('bottom', 'center', ['live', 'media', 'notifications', 'well', 'quick-options', 'clock'])]
    body, placed, wa = desktop(groups, 'dark')
    body += callout(1, placed[groups[0]][0] + placed[groups[0]][2] / 2, placed[groups[0]][1] - 22)
    body += callout(2, placed[groups[1]][0] + placed[groups[1]][2] / 2, placed[groups[1]][1] - 22)
    paths.append(scene('04-dock-bottom-left', '4 · Dock at the bottom left (free); the rest stays in the middle', body, notes=[
        'Dock: bottom edge, anchor start. A free island is simply a group of one.',
        'The centre group keeps its place. Groups on one edge stay at least 24 px apart, so they never read as attached.',
        'Dragging the dock back to the middle and dropping it beside Timer re-attaches it: the default layout (scene 1) returns.']))

    # 5 Clock on the right (free) then attach to the dock
    groups = [Group('bottom', 'center', ['dock', 'live', 'media', 'notifications', 'well', 'quick-options']),
              Group('bottom', 'end', ['clock'])]
    body, placed, wa = desktop(groups, 'dark')
    body += callout(1, placed[groups[1]][0] + placed[groups[1]][2] / 2, placed[groups[1]][1] - 22)
    qo = island_rects(groups[0], placed[groups[0]])[-1][1]
    body += callout(2, qo[0] + qo[2] / 2, qo[1] - 22)
    paths.append(scene('05a-clock-free-right', '5a · Dock in the middle, clock split off to the bottom-right corner', body, notes=[
        'Clock: free at the bottom-right corner (bottom edge, end). It is still one click to the calendar and notifications.',
        'Well and Quick Options stay fused. The status island is now just those two parts.']))

    # 5b attach preview: clock dragged to the dock's right side, gap opens
    groups = [Group('bottom', 'center', ['dock', 'live', 'media', 'notifications', 'well', 'quick-options'])]
    clock_len = Group('bottom', 'start', ['clock']).length()
    opened = (groups[0], clock_len + GAP, 1)
    placed = solve(groups, opened=opened)
    wa = work_area(groups)
    body = wallpaper() + window(wa, 'dark') + f'<rect x="0" y="0" width="{W}" height="{H}" fill="{m["scrim"]}"/>'
    for slot in zone_slots(groups):
        body += dashed(slot[2], m['outline'])
    body += outline(placed[groups[0]], m['outline'])
    rects = island_rects(groups[0], placed[groups[0]], opened)
    for painted, rect in rects:
        body += draw_island(painted, rect, False, 'dark', 'bottom')
    dock_rect = rects[0][1]
    caret_x = dock_rect[0] + dock_rect[2] + GAP / 2
    gap_rect = (dock_rect[0] + dock_rect[2] + GAP, dock_rect[1], clock_len, THICK)
    body += dashed(gap_rect, m['ink'], dash='6 4')
    body += f'<rect x="{caret_x - 1}" y="{dock_rect[1] - 4}" width="2" height="{THICK + 8}" rx="1" fill="{m["state"]}"/>'
    drag = (gap_rect[0] + 26, gap_rect[1] - 58, clock_len, THICK)
    body += draw_island('clock', drag, False, 'dark', 'bottom', lifted=True, scale=1.04)
    body += f'<path d="M{drag[0] + 60} {drag[1] + 34} l 0 22 l 6 -6 l 7 12 l 4 -2 l -7 -12 l 8 -1 z" fill="#fff" stroke="#000" stroke-width="1"/>'
    body += callout(1, gap_rect[0] + gap_rect[2] / 2 + 70, gap_rect[1] - 20)
    body += callout(2, caret_x, dock_rect[1] - 20)
    body += f'<rect x="{drag[0] + drag[2] + 16}" y="{drag[1] + 12}" width="236" height="30" rx="8" fill="#111418" opacity=".85"/>'
    body += f'<text x="{drag[0] + drag[2] + 28}" y="{drag[1] + 32}" font-size="12.5" font-weight="600" fill="#e7e9e8">Bottom edge, left corner, after Dock</text>'
    body += callout(3, drag[0] + drag[2] + 16 + 250, drag[1] + 27)
    paths.append(scene('05b-attach-preview', '5b · Dragging the clock onto the dock: "attach beside Dock"', body, notes=[
        'Attach preview: the target group opens a real gap (clock length + 9 px) where the clock will go. Its neighbours slide aside.',
        'A 2 px state-coloured caret marks the seam. The whole group moves together from now on.',
        'The same text is spoken to screen readers. Keyboard: Ctrl+Alt+arrows step through these same targets.']))

    groups = [Group('bottom', 'center', ['dock', 'clock', 'live', 'media', 'notifications', 'well', 'quick-options'])]
    body, placed, wa = desktop(groups, 'dark')
    r = island_rects(groups[0], placed[groups[0]])[1][1]
    body += callout(1, r[0] + r[2] / 2, r[1] - 22)
    paths.append(scene('05c-clock-attached', '5c · Result: the clock attached beside the dock (9 px gutter)', body, notes=[
        'The clock is its own island in the dock\'s group. Drag it away to detach it; drag the group grip to move them all.']))

    # 6 Dock on the left, music top-right
    groups = [Group('left', 'center', ['dock']),
              Group('top', 'end', ['media']),
              Group('bottom', 'end', ['live', 'notifications', 'well', 'quick-options', 'clock'])]
    body, placed, wa = desktop(groups, 'dark')
    body += strut_marks(groups, wa)
    body += callout(1, placed[groups[0]][0] + THICK + 22, placed[groups[0]][1] + 20)
    body += callout(2, placed[groups[1]][0] - 22, placed[groups[1]][1] + THICK / 2)
    body += callout(3, placed[groups[2]][0] - 22, placed[groups[2]][1] + THICK / 2)
    body += callout(4, wa[0] + 40, wa[1] + wa[3] - 40)
    # a Quick Options popover opening from the bottom-right group, away from its edge
    qo = island_rects(groups[2], placed[groups[2]])[-1][1]
    pw, ph = 320, 250
    px, py = min(qo[0] + qo[2] / 2 - pw / 2, W - INSET - pw), qo[1] - 8 - ph
    body += f'<g filter="url(#card)"><rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="14" fill="#252a30" stroke="rgba(255,255,255,.075)"/></g>'
    for i in range(3):
        for j in range(2):
            body += f'<rect x="{px + 16 + j * 148}" y="{py + 16 + i * 56}" width="140" height="46" rx="12" fill="{"#8a99a8" if (i + j) % 2 == 0 else "rgba(255,255,255,.07)"}"/>'
    body += f'<rect x="{px + 16}" y="{py + 190}" width="{pw - 32}" height="6" rx="3" fill="rgba(255,255,255,.12)"/><rect x="{px + 16}" y="{py + 190}" width="{(pw - 32) * .6}" height="6" rx="3" fill="#8a99a8"/>'
    body += callout(5, px + pw - 20, py + 20)
    paths.append(scene('06-dock-left-music-top-right', '6 · Dock on the whole left side, music in the top-right corner', body, notes=[
        'Left edge, centre: the dock turns into a vertical column. Window marks sit on the screen-edge side.',
        'Top edge, end (the top-right corner): the music island keeps its full horizontal form.',
        'The status island and the others sit at the bottom-right corner.',
        'Three bands, three struts. Maximized and tiled windows (Tiling Shell too) fill only the dashed work area.',
        'Surfaces open away from their own island\'s edge. Quick Options opens upward from the bottom-right, clamped 14 px inside.']))

    # 7 Clock top centre, music top left
    groups = [Group('top', 'center', ['clock']),
              Group('top', 'start', ['media']),
              Group('bottom', 'center', ['dock', 'live', 'notifications', 'well', 'quick-options'])]
    body, placed, wa = desktop(groups, 'dark')
    body += callout(1, placed[groups[0]][0] + placed[groups[0]][2] + 22, placed[groups[0]][1] + THICK / 2)
    body += callout(2, placed[groups[1]][0] + placed[groups[1]][2] + 22, placed[groups[1]][1] + THICK / 2)
    bw, bh = 380, 72
    bx, by = W / 2 - bw / 2, wa[1] + 14 - PAD
    body += f'<g filter="url(#card)"><rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="14" fill="#252a30" stroke="rgba(255,255,255,.075)"/></g>'
    body += icon_tile(bx + 16, by + 18, '#2fa66a', 'M')
    body += f'<text x="{bx + 64}" y="{by + 32}" font-size="13" font-weight="700" fill="#e7e9e8">Ana Ruiz</text>'
    body += f'<text x="{bx + 64}" y="{by + 51}" font-size="12" font-weight="500" fill="#9BA1A8">Running 5 min late — start without me?</text>'
    body += callout(3, bx + bw + 22, by + bh / 2)
    paths.append(scene('07-clock-top-centre', '7 · Just the time at the top centre, music at the top left', body, notes=[
        'The clock alone at top centre. Its calendar and notifications open downward from it.',
        'Music at the top-left corner (top edge, start).',
        'Notification banners keep their top-centre place but start below the top band. They never cover an island.']))

    # 8 Vertical forms close-up + right dock
    groups = [Group('right', 'center', ['dock', 'media']),
              Group('left', 'start', ['live']),
              Group('left', 'end', ['notifications', 'well', 'quick-options', 'clock'])]
    body, placed, wa = desktop(groups, 'dark')
    body += callout(1, placed[groups[0]][0] - 22, placed[groups[0]][1] + 20)
    media_r = island_rects(groups[0], placed[groups[0]])[1][1]
    body += callout(2, media_r[0] - 22, media_r[1] + 40)
    body += callout(3, placed[groups[1]][0] + THICK + 22, placed[groups[1]][1] + 30)
    body += callout(4, placed[groups[2]][0] + THICK + 22, placed[groups[2]][1] + 40)
    paths.append(scene('08-vertical-forms', '8 · Vertical forms: dock and music on the right, others on the left', body, notes=[
        'Vertical dock. Music attached below it (9 px) in its compact form: artwork over stacked controls, no rotated text.',
        'Compact music: the title and artist move into the tooltip and the accessible name.',
        'Compact live island: leading tile, short time "4:12", primary action.',
        'Notifications, then the fused status island: Well, a 2×3 Quick Options grid and the wrapped clock.']))

    # 9 Four modes of arrange mode
    tiles = []
    for index, mode in enumerate(('dark', 'light', 'frost', 'glass')):
        m = MODES[mode]
        tw, th = 700, 380
        ox, oy = (index % 2) * (tw + 20) + 10, (index // 2) * (th + 20) + 10
        gs = [Group('bottom', 'center', ['media', 'well', 'quick-options', 'clock'])]
        pl = solve(gs, w=tw, h=th, ox=ox, oy=oy)
        wa2 = work_area(gs, tw, th)
        t = [f'<svg x="{ox}" y="{oy}" width="{tw}" height="{th}" viewBox="{ox} {oy} {tw} {th}" overflow="hidden">',
             wallpaper(tw, th, ox, oy), window((ox + wa2[0], oy + wa2[1], wa2[2], wa2[3]), mode),
             f'<rect x="{ox}" y="{oy}" width="{tw}" height="{th}" fill="{m["scrim"]}"/>']
        for slot in zone_slots(gs, tw, th):
            x, y, w2, h2 = slot[2]
            if slot[0] in ('left', 'right') and slot[1] != 'center':
                continue
            t.append(dashed((ox + x, oy + y, w2, h2), m['outline']))
        t.append(outline(pl[gs[0]], m['outline']))
        t.append(grip(gs[0], pl[gs[0]], m['outline']))
        for painted, rect in island_rects(gs[0], pl[gs[0]]):
            t.append(draw_island(painted, rect, False, mode, 'bottom', lifted=painted == 'media',
                                 scale=1.04 if painted == 'media' else 1))
        t.append(arrange_card(mode, tw, th, ox + tw / 2 - 180, oy + 110))
        t.append(f'<text x="{ox + 16}" y="{oy + 30}" font-size="16" font-weight="800" fill="#fff" stroke="#000" stroke-width=".6">{mode.title()}</text>')
        t.append('</svg>')
        tiles.append(''.join(t))
    body = f'<rect x="0" y="0" width="{W}" height="{2 * 400 + 20}" fill="#111418"/>' + ''.join(tiles)
    paths.append(scene('09-four-modes', '9 · Arrange mode in Dark, Light, Frost and Glass', body, height=2 * 400 + 20, notes=[
        'Scrim, hairline outlines and dashed zones use each treatment\'s ink; the active preview uses its state colour (ADR-043).',
        'The arrange card is the treatment\'s menu surface. Frost and Glass islands keep their smoked veil and blur.']))

    # 10 Keyboard
    groups = [Group('bottom', 'center', ['dock', 'live', 'media', 'notifications', 'well', 'quick-options']),
              Group('top', 'center', ['clock'])]
    placed = solve(groups)
    wa = work_area(groups)
    body = wallpaper() + window(wa, 'dark') + f'<rect x="0" y="0" width="{W}" height="{H}" fill="{m["scrim"]}"/>'
    for slot in zone_slots(groups):
        body += dashed(slot[2], m['outline'])
    body += draw_groups(groups, 'dark', placed)
    c = placed[groups[1]]
    body += f'<rect x="{c[0] - 3}" y="{c[1] - 3}" width="{c[2] + 6}" height="{c[3] + 6}" rx="{RADIUS + 3}" fill="none" stroke="#80a8d2" stroke-width="2"/>'
    body += f'<rect x="{c[0] - 170}" y="{c[1] + THICK + 22}" width="{c[2] + 340}" height="54" rx="10" fill="#111418" opacity=".88"/>'
    body += f'<text x="{c[0] + c[2] / 2}" y="{c[1] + THICK + 44}" text-anchor="middle" font-size="13" font-weight="700" fill="#e7e9e8">Ctrl + Alt + ↑ moved the clock: bottom → top edge, centre</text>'
    body += f'<text x="{c[0] + c[2] / 2}" y="{c[1] + THICK + 64}" text-anchor="middle" font-size="12" font-weight="500" fill="#9BA1A8">Spoken: "Clock, top edge, centre"</text>'
    body += callout(1, c[0] + c[2] + 26, c[1] + THICK / 2)
    paths.append(scene('10-keyboard', '10 · Keyboard: focus an island, Ctrl+Alt+arrows move it between targets', body, notes=[
        'Keyboard focus ring on the selected island. Tab / Shift+Tab select, Ctrl+Alt+arrows move, Ctrl+Alt+Shift+arrows move the group, Ctrl+Z undoes.']))
    return paths


def render(paths):
    pngs = []
    for path, total in paths:
        base = os.path.basename(path)
        subprocess.run(['qlmanage', '-t', '-s', str(W * 2), '-o', OUT, path],
                       check=True, capture_output=True)
        thumb = path + '.png'
        png = path[:-4] + '.png'
        # Quick Look renders into a square canvas; crop to the scene.
        subprocess.run(['magick', thumb, '-crop', f'{W * 2}x{total * 2}+0+0', '+repage',
                        '-resize', f'{W}x', png], check=True)
        os.remove(thumb)
        pngs.append(png)
    return pngs


def page(pngs):
    items = ''.join(f'<figure><img src="{os.path.basename(p)}" alt="{esc(os.path.basename(p))}"><figcaption>{esc(os.path.basename(p))}</figcaption></figure>' for p in pngs)
    with open(os.path.join(OUT, 'index.html'), 'w') as f:
        f.write(f'''<!doctype html><html><head><meta charset="utf-8"><title>Movable Shelf Islands</title>
<style>:root{{--bg:#f4f5f6;--ink:#1d2126}}@media (prefers-color-scheme:dark){{:root{{--bg:#111418;--ink:#e7e9e8}}}}
body{{background:var(--bg);color:var(--ink);font:15px/1.5 Inter,-apple-system,sans-serif;margin:0;padding:24px 16px}}
main{{max-width:1480px;margin:auto}} img{{width:100%;height:auto;border-radius:10px;display:block}}
figure{{margin:0 0 36px}} figcaption{{opacity:.7;font-size:13px;margin-top:6px}}</style></head>
<body><main><h1>ADR-044 · Movable shelf islands — mockups</h1>
<p>Laid out by the ADR's own rules: 12 zones, 9 px attach gutter, 24 px between groups on one edge, 14 px inset, struts per occupied edge.</p>
{items}</main></body></html>''')


if __name__ == '__main__':
    page(render(build()))
    print('ok')
