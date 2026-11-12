// SPDX-License-Identifier: GPL-2.0-or-later
// Search's outside-press dismissal and the on-screen keyboard.
// Run: gjs -m search-keyboard-policy.js js/ui/lumaSearch.js
// (node search-keyboard-policy.js ... works too). The dialog's
// captured-event handler is taken from the source and run against stubs.
const gjs = typeof globalThis.ARGV !== 'undefined';
const args = gjs ? globalThis.ARGV : process.argv.slice(2);
const GLib = gjs ? (await import('gi://GLib')).default : null;
const fs = gjs ? null : await import('node:fs');
const read = path => (gjs ? new TextDecoder().decode(GLib.file_get_contents(path)[1])
    : fs.readFileSync(path, 'utf8'));

function assert(value, message) {
    if (!value)
        throw new Error(`FAIL: ${message}`);
}

const source = read(args[0]);
const start = source.indexOf("this.connect('captured-event', (_actor, event) => {");
assert(start >= 0, 'Search dismisses outside presses from its captured-event handler');
const open = source.indexOf('{', source.indexOf('=>', start));
let depth = 0, end = open;
for (; end < source.length; end++) {
    if (source[end] === '{')
        depth++;
    else if (source[end] === '}' && --depth === 0)
        break;
}
const body = source.slice(open + 1, end);

const Clutter = {
    EVENT_PROPAGATE: false,
    EVENT_STOP: true,
    EventType: {BUTTON_PRESS: 1, TOUCH_BEGIN: 2, TOUCH_UPDATE: 3, TOUCH_END: 4, KEY_PRESS: 5},
};
const surface = (x, y, width, height) => ({mapped: true,
    get_transformed_position: () => [x, y], get_transformed_size: () => [width, height]});
// A 372x828 phone: the field at the top, the keyboard over the bottom 326 px.
const KEYBOARD_TOP = 828 - 326;
function run(type, x, y) {
    const routed = [];
    const Main = {keyboard: {maybeHandleEvent: event => {
        if (event.y < KEYBOARD_TOP)
            return false;
        routed.push(event);
        return true;
    }}};
    const dialog = {closed: 0, close() {
        this.closed++;
    }, _fieldIsland: surface(24, 108, 324, 48), _intro: surface(24, 164, 324, 120),
    _results: {mapped: false}};
    const event = {x, y, type: () => type, get_coords: () => [x, y]};
    const handler = new Function('Clutter', 'Main', '_actor', 'event', body);
    const result = handler.call(dialog, Clutter, Main, dialog, event);
    return {result, closed: dialog.closed, routed: routed.length};
}

for (const type of [Clutter.EventType.TOUCH_BEGIN, Clutter.EventType.BUTTON_PRESS]) {
    const key = run(type, 186, 700);
    assert(key.closed === 0, 'a key press on the on-screen keyboard does not close Search');
    assert(key.routed === 1 && key.result === Clutter.EVENT_STOP,
        'the keyboard takes its press while Search holds the grab');
}
const outside = run(Clutter.EventType.TOUCH_BEGIN, 186, 400);
assert(outside.closed === 1 && outside.result === Clutter.EVENT_STOP, 'a press outside Search still closes it');
const field = run(Clutter.EventType.TOUCH_BEGIN, 100, 120);
assert(field.closed === 0 && field.result === Clutter.EVENT_PROPAGATE, 'a press on the field reaches the field');
const motion = run(Clutter.EventType.TOUCH_UPDATE, 186, 700);
assert(motion.closed === 0 && motion.routed === 0 && motion.result === Clutter.EVENT_PROPAGATE,
    'later touch events reach ModalDialog, which routes them to the keyboard');

console.log('PASS Search: keyboard presses type, outside presses close, field presses propagate');
