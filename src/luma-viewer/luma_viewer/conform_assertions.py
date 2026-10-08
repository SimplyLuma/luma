# SPDX-License-Identifier: Apache-2.0
"""Check actual Viewer interaction results in conform capture JSONs.

Run: python3 -m luma_viewer.conform_assertions <four run folders>
These checks supplement the unchanged measurement gate, never replace it.
"""
import json
from pathlib import Path
import sys

DETECTORS = {
    'text-tel': ('Call', 'Add to Contacts'),
    'text-addr': ('Open in Maps',),
    'text-date': ('Add to Calendar',),
    'text-money': ('Convert currency',),
}
FIELDS = {'tenant': 'Nick Field', 'orig': '01/01/2026', 'date': '09/25/2026'}


def check(folder, states):
    folder = Path(folder)
    for state in states:
        spec = json.loads((folder / f'spec-{state}.json').read_text())
        gtk = json.loads((folder / f'gtk-{state}.json').read_text())
        assert not gtk['notes'], (folder, state, gtk['notes'])
        widgets = gtk['widgets']
        named = {w['name']: w for w in widgets}
        visible_text = [w.get('text', '') for w in widgets if not w.get('offscreen')]
        if state == 'inline-editor':
            wrapper = named['vw-text-entry']
            entry = named['vw-text-entry-input']
            assert not wrapper.get('offscreen') and not entry.get('offscreen'), (folder, state, 'editor not visible')
            assert entry['role'] == 'text-box' and entry.get('text', '') == '', (folder, state, 'native textbox missing')
            assert any(w.get('placeholder') == 'Type here' and w.get('text') == '' and not w.get('offscreen') for w in widgets), (folder, state, 'native placeholder missing')
            assert entry['box'][0] >= 0 and entry['box'][0] + entry['box'][2] <= spec['window']['w'] + 2, (folder, state, 'editor outside window')
        elif state in DETECTORS:
            for action in DETECTORS[state]:
                assert action in visible_text, (folder, state, 'missing visible action', action)
            assert any(t.startswith('Copy “') for t in visible_text), (folder, state, 'Copy missing')
            if spec['window']['w'] == 390:
                assert any(not w.get('offscreen') and
                           ('drawer' in w['type'].lower() or 'lumaui-menu-drawer' in w.get('classes', []))
                           for w in widgets), (folder, state, 'visible phone drawer missing')
        elif state.startswith('pdf-page-'):
            page = int(state[-1])
            thumb = named[f'vw-page-thumbnail-{page}']
            assert thumb['role'] == 'button' and not thumb.get('offscreen'), (folder, state, 'thumbnail inaccessible')
            area = named[f'vw-page-{page}']
            assert not area.get('offscreen'), (folder, state, 'selected PDF page offscreen')
            # Navigation places the target page 60px below the scroller's top.
            stage = named['vw-stage']['box']
            assert abs(area['box'][1] - stage[1] - 60) <= 2, (folder, state, 'page did not scroll', area['box'], stage)
        elif state == 'pdf-filled':
            for key, value in FIELDS.items():
                assert named[f'vw-form-{key}'].get('text') == value, (folder, state, key, 'value not retained')
    return len(states)


def main():
    scenario = Path(__file__).resolve().parents[3] / 'tools/lumaui-conform/scenarios/viewer.json'
    data = json.loads(scenario.read_text())
    states = [s['name'] for s in data['states']]
    assert set(data['phone_states']) == set(states), 'Every desktop state needs phone coverage'
    for folder in sys.argv[1:]:
        print(f'{folder}: {check(folder, states)} captured states checked')
    if len(sys.argv) == 1:
        raise SystemExit('Supply conform run folders')


if __name__ == '__main__':
    main()
