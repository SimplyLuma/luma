#!/usr/bin/env python3
"""summary.py EVIDENCE_DIR: one checklist line per handoff evidence item, from the matrix results."""
import json, os, sys

E = sys.argv[1]


def load(case):
    try:
        return json.load(open(os.path.join(E, case, 'results.json')))
    except OSError:
        return None


def ok(flag):
    return 'PASS' if flag else 'FAIL'


lines = []
bars = []
for name in sorted(os.listdir(E)):
    if not name.startswith('bar-') or not os.path.isdir(os.path.join(E, name)):
        continue
    d = load(name)
    b = d and d['cases'].get('bar')
    if not isinstance(b, list):
        lines.append(f'1. {name}: FAIL (no result)')
        continue
    modes = [m for m in b if m['name'].startswith('bar-')]
    menu = next((m for m in b if m['name'] == 'menu'), {})
    tip = next((m for m in b if m['name'] == 'tooltip'), {})
    passed = len(modes) == 5 and all(m.get('pass') for m in modes)
    bars.append(passed)
    lines.append(f"1. {name}: {ok(passed)}: five modes, bar gap {sorted({m['gapLogical'] for m in modes})} logical = "
                 f"{sorted({m['gapDevicePx'] for m in modes})} device px; "
                 f"2. menu gap {menu.get('gapLogical')} centred {menu.get('centredOnOptions')}; tooltip gap {tip.get('gapLogical')}")

f = load('flows')
if f:
    c = f['cases']
    s = c.get('shortcuts', {})
    one, two, hover, three, dragged, four = (s.get(k, {}) for k in
        ('ctrlShift1', 'ctrlShift2', 'ctrlShift2Hover', 'ctrlShift3', 'ctrlShift3Dragged', 'ctrlShift4'))
    lines.append(f"Shortcuts: Ctrl+Shift+1 {ok(one.get('visible') and not one.get('hasSelection'))} (mode {one.get('mode')}, no selection); "
                 f"Ctrl+Shift+2 {ok(two.get('mode') == 'window' and two.get('checkedWindow') is None and hover.get('checkedWindow'))} "
                 f"(window mode, nothing picked, hover picks {hover.get('checkedWindow')}); "
                 f"Ctrl+Shift+3 {ok(three.get('mode') == 'selection' and not three.get('hasSelection') and dragged.get('hasSelection'))} (drag draws {dragged.get('selection')}); "
                 f"Ctrl+Shift+4 {ok(four.get('mode') == 'record-screen' and s.get('ctrlShift4Recording', {}).get('recording') and not s.get('ctrlShift4Stopped', {}).get('recording'))} (records, same chord stops)")
    sel = c.get('selection', {})
    lines.append(f"3. Selection: {ok(sel.get('file', {}).get('width') == sel.get('expected', {}).get('width') and sel.get('file', {}).get('height') == sel.get('expected', {}).get('height'))} "
                 f"drawn {sel.get('drawn', {}).get('selection')} moved {sel.get('moved', {}).get('selection')} resized {sel.get('resized', {}).get('selection')} "
                 f"label {sel.get('resized', {}).get('sizeLabel')}; PNG {sel.get('file', {}).get('width')}x{sel.get('file', {}).get('height')} (see analysis.txt for the pixel comparison)")
    w = c.get('window', {})
    lines.append(f"4. Window: {ok(w.get('hoverClaude', {}).get('checkedWindow') == 'Claude' and w.get('claudeFile') and w.get('secondFile'))} "
                 f"partly off screen and behind: frame {w.get('claudeFrame')} -> PNG {w.get('claudeFile', {}).get('width')}x{w.get('claudeFile', {}).get('height')} (the window's buffer {w.get('claudeBuffer')}); "
                 f"behind: PNG {w.get('secondFile', {}).get('width')}x{w.get('secondFile', {}).get('height')}")
    t = c.get('timer', {})
    lines.append(f"5. Timer: {ok(t.get('captured') and t.get('counting', {}).get('countdown') and not t.get('afterEscape', {}).get('countdown') and t.get('afterEscape', {}).get('visible') and t.get('noCaptureAfterCancel'))} "
                 f"countdown shown ({t.get('counting', {}).get('seconds')}), capture after 5 s, Escape cancels and returns to the bar, no file after cancel")
    th = c.get('thumbnail', {})
    lines.append(f"6. Thumbnail (screenshot): {ok(th.get('visible') and th.get('opened'))} '{th.get('name')}' / '{th.get('where')}', "
                 f"gap {th.get('gapLogical')}, right inset {th.get('rightInsetLogical')}; click -> {th.get('opened')}")
    probe = c.get('paintprobe', {})
    lines.append(f"Paint probe: screen paints {probe.get('beforeShot')}, after one screenshot {probe.get('afterShot')}")

r = load('record')
if r:
    rec = r['cases'].get('record', {})
    stops = rec.get('stops', [])
    first = stops[0] if stops else {}
    lines.append(f"6. Thumbnail (recording): {ok(first.get('thumbnail') and first.get('opened'))} {first.get('thumbnail')}; click -> {first.get('opened')}")
    lines.append('7. Recording: ' + '; '.join(f"{s['how']} {ok(s['recording'] and s['stopped'])} (pill {s['pill']}, GNOME indicator shown {s['gnomeIndicator']})" for s in stops) +
                 f"; whole screen {ok(rec.get('screen', {}).get('recording'))}; video frames: see analysis.txt")

sv = load('saveto')
if sv:
    st = sv['cases'].get('saveto', {})
    for dest in ('pictures', 'desktop', 'clipboard', 'other'):
        d = st.get(dest, {})
        good = d.get('clipboardPngBytes', 0) > 0 and (not d.get('newFiles') if dest == 'clipboard' else len(d.get('newFiles', [])) == 1)
        lines.append(f"8. Save to {dest}: {ok(good)} files {d.get('newFiles')} clipboard PNG {d.get('clipboardPngBytes')} bytes, thumbnail '{d.get('thumbnail')}'")
p = load('persist')
if p:
    pc = p['cases'].get('persist', {})
    lines.append(f"8. Options after a new session: {ok(pc.get('saveTo') == 'desktop' and pc.get('timer') == 10 and pc.get('thumbnail') is False and pc.get('remember') is False and pc.get('pointer') is True and pc.get('lastMode') == 'window')} {pc}")

sk = load('search-keyboard')
if sk:
    se = sk['cases'].get('search', {})
    lines.append(f"9. Search: {ok(se.get('opened', {}).get('visible') and se.get('file'))} opened from Beam search; capture compared with the screen before search in analysis.txt")
    kb = sk['cases'].get('keyboard', {})
    names = [x['name'] for x in kb.get('controls', [])]
    same = all(x['name'] == x['tooltip'] for x in kb.get('controls', []))
    lines.append(f"10. Keyboard: {ok(kb.get('captured'))} focus path {[x['focus'] for x in kb.get('steps', [])]}; "
                 f"ring {kb.get('focusRing')}; names = tooltips {same}: {names}; toolbar {kb.get('bar')}")

mm = load('multimonitor')
if mm:
    m = mm['cases'].get('multimonitor', {})
    lines.append(f"11. Two monitors: {ok(m.get('barMonitor') == m.get('shelfMonitor') and m.get('state', {}).get('hasSelection') and (m.get('bar') or {}).get('pass'))} "
                 f"selection {m.get('state', {}).get('selection')} across both, bar on monitor {m.get('barMonitor')} (shelf {m.get('shelfMonitor')}), gap {(m.get('bar') or {}).get('gapLogical')}")

print('\n'.join(lines))
