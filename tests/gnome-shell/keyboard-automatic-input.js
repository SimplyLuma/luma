// SPDX-License-Identifier: GPL-2.0-or-later
// Run with Node and the fully patched native keyboard.js as the argument.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const source = readFileSync(process.argv[2], 'utf8');
function method(name) {
    const start = source.indexOf(`    ${name}(`);
    assert(start >= 0, `Missing native method ${name}`);
    const body = source.indexOf('{', start);
    const end = source.indexOf('\n    }', body);
    assert(end > body);
    return source.slice(body + 1, end);
}
const Clutter = {InputDeviceType: {KEYBOARD_DEVICE: 1, TOUCHSCREEN_DEVICE: 2}};
class Keyboard {
    connect() {}
    setCursorLocation() {}
    destroy() { this.destroyed = true; }
}
const sync = new Function('Clutter', 'Keyboard', 'SHOW_KEYBOARD', method('_syncEnabled'));
const device = (type, node) => ({get_device_type: () => type, get_device_node: () => node});
const physical = device(1, '/dev/input/event2');
const virtual = device(1, null);
function manager({explicit = false, touch = true, lastTouch = true, devices = []} = {}) {
    return {
        _a11yApplicationsSettings: {get_boolean: () => explicit},
        _seat: {get_touch_mode: () => touch, list_devices: () => devices},
        _lastDeviceIsTouchscreen: () => lastTouch,
        _keyboard: null, _bottomDragGesture: {},
    };
}
const cases = [
    [{devices: [physical]}, false, 'Physical keyboard keeps automatic OSK hidden after touch'],
    [{devices: [virtual]}, true, 'Virtual keyboards do not suppress touch input'],
    [{devices: []}, true, 'Touch-only phone receives automatic OSK'],
    [{touch: false}, false, 'Pointer session does not summon OSK'],
    [{lastTouch: false}, false, 'Non-touch focus does not summon OSK'],
    [{explicit: true, devices: [physical]}, true, 'Explicit accessibility OSK overrides physical keyboard'],
];
for (const [options, expected, message] of cases) {
    const m = manager(options); sync.call(m, Clutter, Keyboard, 'screen-keyboard-enabled');
    assert.equal(!!m._keyboard, expected, message);
}
const m = manager(); sync.call(m, Clutter, Keyboard, 'screen-keyboard-enabled');
const old = m._keyboard; m._seat.list_devices = () => [physical];
sync.call(m, Clutter, Keyboard, 'screen-keyboard-enabled');
assert(old.destroyed && !m._keyboard, 'Hotplug closes automatic OSK');
m._seat.list_devices = () => []; sync.call(m, Clutter, Keyboard, 'screen-keyboard-enabled');
assert(m._keyboard, 'Unplug restores automatic touch input');
const handle = new Function('event', 'global', 'Main', method('maybeHandleEvent'));
assert.equal(handle.call({_keyboard: {}}, {}, {stage: {get_event_actor: () => null}}, {
    layoutManager: {keyboardBox: {contains() { throw new Error('Null actor reached contains'); }}},
}), false, 'Unmapped touch events do not trap modal dialogs');
console.log('PASS: automatic input/accessibility/hotplug policy and null-event regression');
