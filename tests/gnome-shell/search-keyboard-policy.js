// SPDX-License-Identifier: GPL-2.0-or-later
// Search's outside-press dismissal and the on-screen keyboard, and the
// words and esc chip for each presentation.
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

function methodBody(name) {
    const at = source.indexOf(`    ${name}() {`);
    assert(at >= 0, `lumaSearch.js has ${name}()`);
    const brace = source.indexOf('{', at);
    let level = 0, stop = brace;
    for (; stop < source.length; stop++) {
        if (source[stop] === '{')
            level++;
        else if (source[stop] === '}' && --level === 0)
            break;
    }
    return source.slice(brace + 1, stop);
}
const syncFormFactor = methodBody('_syncFormFactor');
function words(mode, deviceClass = '') {
    const dialog = {_entry: {}, _introTitle: {}, _closeButton: {}};
    const MainStub = {presentation: mode === null ? null : {mode}};
    const GLibStub = {getenv: name => (name === 'LUMA_DEVICE_CLASS' ? deviceClass : null)};
    new Function('Main', 'GLib', '_', syncFormFactor).call(dialog, MainStub, GLibStub, text => text);
    return {hint: dialog._entry.hint_text, a11y: dialog.accessible_name,
        title: dialog._introTitle.text, esc: dialog._closeButton.visible};
}
const desktop = words('desktop');
assert(desktop.hint === 'Search this machine' && desktop.a11y === desktop.hint &&
    desktop.title === 'Everything on this machine, in one place.' && desktop.esc === true,
'the desktop keeps "this machine" and the esc chip');
const phone = words('mobile');
assert(phone.hint === 'Search this phone' && phone.title === 'Everything on this phone, in one place.' &&
    phone.esc === false, 'a phone says "phone" and has no esc chip');
const tablet = words('tablet');
assert(tablet.hint === 'Search this tablet' && tablet.title === 'Everything on this tablet, in one place.' &&
    tablet.esc === false, 'a tablet says "tablet" and has no esc chip');
assert(words(null, 'handheld').hint === 'Search this phone', 'a handheld without a presentation is a phone');
assert(words(null).esc === true, 'a desktop without a presentation keeps the chip');

console.log('PASS Search: keyboard presses type, outside presses close, field presses propagate; words and esc chip follow the presentation');
