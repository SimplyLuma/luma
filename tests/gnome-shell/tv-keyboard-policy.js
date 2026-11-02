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
const [letters, capitals, symbols] = Tv.tvLevels(us);
const label = key => key.keyval ?? key.strings?.[0] ?? key.label ?? key.action;
const widths = row => row.reduce((sum, key) => sum + key.width + (key.leftOffset ?? 0), 0);
const text = row => row.map(label).join(' ');

assert(JSON.stringify(us) === original, 'the language model is not changed');
assert(letters.rows.length === 5, 'digits, three letter rows and the bottom row');
assert(text(letters.rows[0]) === '1 2 3 4 5 6 7 8 9 0 delete', 'digits and delete on top (Leanback)');
assert(text(letters.rows[1]) === 'q w e r t y u i o p @', 'q-p then @');
assert(text(letters.rows[2]) === 'a s d f g h j k l _ &', 'a-l then _ &');
assert(text(letters.rows[3]) === 'z x c v b n m , . - ?', 'z-m then , . - ?');
assert(text(letters.rows[4]) === '123 levelSwitch   0xff0d', 'bottom row: 123, shift, space, the action key');
assert(letters.rows.every(row => widths(row) === 11), 'every row spans the same eleven columns');
assert(letters.rows.slice(0, 4).every(row => row.every(key => key.width === 1 && !key.leftOffset)),
    'every key in the grid rows is one cell: no stagger');
assert(letters.rows[4][2].width === 7 && letters.rows[4][3].width === 2, 'space takes seven cells, the action key two');
assert(letters.rows[4][3].tvAction && letters.rows[4][3].label === 'Done', 'the action key is Enter, labelled Done by default');
assert(text(capitals.rows[1]).startsWith('Q W E'), 'the shift level has the capitals');
assert(text(symbols.rows[0]) === text(letters.rows[0]), '123 keeps digits and delete on top');
assert(symbols.rows[1].map(label).includes('#') && symbols.rows[4][0].label === 'ABC', '123: symbols, ABC returns');
assert(symbols.rows.every(row => widths(row) <= 11), '123 stays on the grid');
const email = Tv.tvLevels([{level: 'default', rows: [[...chars('qwertyuiop')], [...chars('asdfghjkl')], [...chars('zxcvbnm')],
    [{action: 'levelSwitch', level: 'opt', label: '?123'}, {strings: ['.com']}, {strings: [' ']}]]}],
{keepExtras: true})[0];
assert(email.rows[1].map(label).includes('.com'), 'e-mail and URL layouts keep their .com');

// The remote: cells as the Shell measures them (row, x1, x2), 108 a cell.
const cells = [];
letters.rows.forEach((row, r) => {
    let x = 0;
    for (const key of row) {
        x += key.leftOffset ?? 0;
        cells.push({row: r, x1: x * 108, x2: (x + key.width) * 108, key: label(key)});
        x += key.width;
    }
});
const at = name => cells.findIndex(cell => cell.key === name);
let anchor = null;
const go = (from, ...steps) => {
    let index = at(from);
    anchor = null;
    for (const step of steps) {
        ({index, anchor} = Tv.tvMove(cells, index, step, anchor));
        if (index < 0)
            return null;
    }
    return cells[index].key;
};
assert(cells[Tv.tvStart(cells)].key === 'q', 'the focus starts on q');
assert(go('1', 'up') === null, 'Up from the top row puts the keyboard away');
assert(go('q', 'left') === '@' && go('@', 'right') === 'q', 'Left and Right wrap in the row');
assert(go('q', 'down') === 'a' && go('p', 'down') === '_' && go('@', 'down') === '&', 'Down keeps the column');
assert(go('d', 'down') === 'c' && go('c', 'down') === ' ', 'straight down to space');
assert(go('e', 'down', 'down', 'down', 'up', 'up', 'up') === 'e', 'through space and back to the same column');
assert(go('p', 'down', 'down', 'down') === '0xff0d', 'the action key under the right-hand columns');
assert(go('0xff0d', 'up') === '?', 'up from the action key to the column under its centre');
assert(go(' ', 'down') === ' ', 'Down from the bottom row stays');
assert(Tv.tvMove([], 0, 'left').index === -1 && Tv.tvMove(cells, 999, 'left').index === 0, 'no cells, stale index');

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

assert(Tv.tvKeyboardHeight(tokens) === 428, 'five rows in the mockup\'s height: 24 + 5 x 64 + 4 x 12 + 36');
assert(Tv.tvCell(tokens).width === 108 && Tv.tvCell(tokens).height === 76, 'a cell is a cap and half its gaps');
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
