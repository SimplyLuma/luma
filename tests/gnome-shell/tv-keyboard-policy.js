// SPDX-License-Identifier: GPL-2.0-or-later
// The TV posture of the Keycap keyboard: its layout, remote navigation and
// action label, and the wiring that keeps it to TV posture.
// Run: gjs -m tv-keyboard-policy.js js/ui/lumaPresentation/tvKeyboard.js \
//        js/ui/lumaPresentation/tvTokens.js js/ui/keyboard.js
// (node tv-keyboard-policy.js ... works too).
const gjs = typeof globalThis.ARGV !== 'undefined';
const args = gjs ? globalThis.ARGV : process.argv.slice(2);
const GLib = gjs ? (await import('gi://GLib')).default : null;
const fs = gjs ? null : await import('node:fs');
const cwd = gjs ? GLib.get_current_dir() : process.cwd();
const absolute = path => (path.startsWith('/') ? path : `${cwd}/${path}`);
const read = path => (gjs ? new TextDecoder().decode(GLib.file_get_contents(path)[1])
    : fs.readFileSync(path, 'utf8'));

function assert(value, message) {
    if (!value)
        throw new Error(`FAIL: ${message}`);
}

const Tv = await import(`file://${absolute(args[0])}`);
const {tv: tokens} = await import(`file://${absolute(args[1])}`);
const keyboard = read(args[2]);

// The Shell's us.json, abridged to what the TV layout reads.
const chars = s => [...s].map(c => ({strings: [c]}));
const del = {action: 'delete', iconName: 'osk-delete-symbolic'};
const enter = {keyval: '0xff0d', iconName: 'osk-enter-symbolic'};
const shift = level => ({action: 'levelSwitch', level, iconName: 'osk-shift-symbolic'});
const tail = label => [{action: 'levelSwitch', level: label === 'ABC' ? 'default' : 'opt', label},
    {action: 'emoji', iconName: 'osk-emoji-picker-symbolic'}, {strings: [' ']},
    {action: 'languageMenu', iconName: 'osk-layout-symbolic'}, {action: 'hide', iconName: 'osk-hide-symbolic'}];
const us = [
    {level: 'default', mode: 'default', rows: [[...chars('qwertyuiop'), del], [...chars('asdfghjkl'), enter],
        [shift('shift'), ...chars('zxcvbnm,.'), shift('shift')], tail('?123')]},
    {level: 'opt', mode: 'locked', rows: [[...chars('1234567890'), del], [...chars('@#$%&-+()'), enter],
        [{action: 'levelSwitch', level: 'opt+shift', label: '=/<'}, ...chars('*"\':;!?'),
            {action: 'levelSwitch', level: 'opt+shift', label: '=/<'}], tail('ABC')]},
];
const original = JSON.stringify(us);
const [letters, symbols] = Tv.tvLevels(us);
const label = key => key.keyval ?? key.strings?.[0] ?? key.label ?? key.action;
const widths = row => row.reduce((sum, key) => sum + key.width + key.leftOffset, 0);

assert(JSON.stringify(us) === original, 'the language model is not changed');
assert(letters.rows.length === 4, 'three letter rows and the bottom row');
assert(letters.rows[0].map(label).join('') === 'qwertyuiop', 'top row: ten letters, no delete');
assert(letters.rows[1].map(label).join('') === 'asdfghjkl', 'middle row: no Enter');
assert(letters.rows[1][0].leftOffset === 0.5, 'middle row centred by half a key');
assert(letters.rows[2].map(label).join(' ') === 'levelSwitch z x c v b n m delete',
    'last letter row: one shift, the letters, delete (punctuation is on 123)');
assert(letters.rows[2][0].leftOffset === 0.5, 'last letter row centred');
const bottom = letters.rows[3];
assert(bottom.map(label).join('|') === '123| |0xff0d', 'bottom row: 123, space, the action key');
assert(bottom[2].tvAction && bottom[2].label === 'Done', 'the action key is Enter, labelled Done by default');
assert(bottom.every(key => !['emoji', 'languageMenu', 'hide'].includes(key.action)), 'no emoji, language or hide');
assert(widths(bottom) === 10 && widths(letters.rows[0]) === 10, 'rows span the same ten keys');
assert(symbols.rows[0].map(label).join('') === '1234567890', '123: digits on top');
assert(symbols.rows[2][0].label === '=/<' && symbols.rows[2].filter(k => k.action === 'levelSwitch').length === 1,
    '123: one switch to the second symbol level');
assert(symbols.rows[2].map(label).includes('!'), '123 keeps its punctuation');
assert(symbols.rows[3][0].label === 'ABC', '123: ABC returns to letters');
const email = Tv.tvLevels([{level: 'default', rows: [[...chars('qwertyuiop')], [shift('shift'), ...chars('zxc')],
    [{action: 'levelSwitch', level: 'opt', label: '?123'}, {strings: ['@']}, {strings: [' ']}, {strings: ['.com']}]]}],
{keepExtras: true})[0];
assert(email.rows[2].map(label).join('|') === '123|@|.com| |0xff0d', 'e-mail and URL layouts keep @ and .com');

// The remote: cells as the Shell measures them (row, x1, x2).
const cells = [];
[letters.rows[0], letters.rows[1], letters.rows[2], letters.rows[3]].forEach((row, r) => {
    let x = 0;
    for (const key of row) {
        x += key.leftOffset;
        cells.push({row: r, x1: x * 108, x2: (x + key.width) * 108, key: label(key)});
        x += key.width;
    }
});
const at = name => cells.findIndex(cell => cell.key === name);
const move = (from, direction) => cells[Tv.tvMove(cells, at(from), direction)]?.key;
assert(Tv.tvMove(cells, at('q'), 'up') === -1, 'Up from the top row puts the keyboard away');
assert(move('q', 'left') === 'q' && move('p', 'right') === 'p', 'Left and Right stop at the row ends');
assert(move('q', 'right') === 'w', 'Right moves along the row');
assert(move('q', 'down') === 'a' && move('p', 'down') === 'l', 'Down keeps under the same spot');
assert(move('a', 'down') === 'levelSwitch' && move('l', 'down') === 'delete', 'down to shift and delete');
assert(move('g', 'down') === 'v' && move('v', 'down') === ' ', 'down to the middle, then space');
assert(move('delete', 'down') === '0xff0d', 'delete is above the action key');
assert(move(' ', 'down') === ' ', 'Down from the bottom row stays');
assert(['c', 'v'].includes(move(' ', 'up')), 'Up from space returns to the middle');
assert(Tv.tvMove([], 0, 'left') === -1 && Tv.tvMove(cells, 999, 'left') === 0, 'no cells, stale index');

assert(Tv.tvRemoteKey('Return') === 'ok' && Tv.tvRemoteKey('KP_Enter') === 'ok', 'OK types the focused key');
assert(Tv.tvRemoteKey('Escape') === 'back' && Tv.tvRemoteKey('Back') === 'back', 'Back puts it away');
assert(Tv.tvRemoteKey('BackSpace') === null && Tv.tvRemoteKey('a') === null,
    'Backspace and letters go straight to the field');
assert(Tv.TV_REMOTE_KEYS.includes('XF86Back') && !Tv.TV_REMOTE_KEYS.includes('BackSpace'),
    'the remote Back key is taken; Backspace is not');

const purposes = {NORMAL: 0, URL: 5, EMAIL: 6};
assert(Tv.tvActionLabel('Search', purposes.NORMAL, purposes) === 'Search', 'the app names the action');
assert(Tv.tvActionLabel('  Add ', purposes.URL, purposes) === 'Add', 'an asked label wins over the purpose');
assert(Tv.tvActionLabel('', purposes.URL, purposes) === 'Go', 'an address goes');
assert(Tv.tvActionLabel(null, purposes.NORMAL, purposes) === 'Done', 'anything else is done');
assert(Tv.tvActionLabel('x'.repeat(80), 0, purposes).length === 24, 'a label stays one short word');

assert(Tv.tvKeyboardHeight(tokens) === 428, 'the mockup keyboard: 26 + 4 x 78 + 3 x 14 + 48');
assert(Tv.tvCell(tokens).width === 108 && Tv.tvCell(tokens).height === 92, 'a cell is a cap and half its gaps');
assert(tokens.keyboard.colours.action_top.startsWith('rgba('), 'OKLCH colours are generated as rgba()');

// Wiring: TV posture only, the remote's keys only while open.
assert(/enableKeyboard \|\| autoEnabled \|\| this\._tv;/.test(keyboard), 'every field gets the keyboard on a TV');
assert(/_tvShown\(\) \{[\s\S]*?_tvGrabKeys\(TvKeyboard\.TV_REMOTE_KEYS\)/.test(keyboard), 'keys taken as it shows');
assert(/_tvHidden\(\) \{\s*this\._tvReleaseKeys\(\);/.test(keyboard), 'and given back as it hides');
assert(/_resizesWindows\(\) \{[\s\S]*?return !this\._tv &&/.test(keyboard), 'TV apps are never resized');
assert(/setCursorLocation\([^)]*\) \{[\s\S]*?if \(this\._tv\) \{\s*this\._setFocusWindow\(null\);/.test(keyboard),
    'TV apps are never slid up');
assert(/const levels = this\._tv && !pad/.test(keyboard), 'the TV layout only in TV posture; pads stay pads');
assert(/_completionAllowed\(\) \{\s*\/\/[^\n]*\n\s*return !this\._tv &&/.test(keyboard), 'no suggestion strip on a TV');
assert(/org\.projectluma\.Shell\.Keyboard1/.test(keyboard) && /SetActionLabel/.test(keyboard) &&
    /name="Height" type="u"/.test(keyboard), 'the application contract');

(gjs ? globalThis.print : console.log)('tv-keyboard-policy: ok');
