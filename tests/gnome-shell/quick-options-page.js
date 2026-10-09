// SPDX-License-Identifier: GPL-2.0-or-later
// Exercise methods from the exact composed or packaged Shell JavaScript.
// Run: gjs -m quick-options-page.js quickSettings.js bluetooth.js popupMenu.js
import Gio from 'gi://Gio';

function read(path) {
    const [loaded, bytes] = Gio.File.new_for_path(path).load_contents(null);
    if (!loaded) throw new Error(`Cannot read ${path}`);
    return new TextDecoder().decode(bytes);
}
function method(source, name) {
    const marker = `    ${name}(`;
    const start = source.indexOf(marker);
    const body = source.indexOf('{', start);
    const end = source.indexOf('\n    }', body);
    if (start < 0 || body < 0 || end < 0) throw new Error(`Missing ${name}`);
    return source.slice(body + 1, end);
}
function equal(label, actual, expected) {
    if (actual !== expected) throw new Error(`${label}: expected ${expected}, got ${actual}`);
}
const quick = read(ARGV[0]);
const bluetooth = read(ARGV[1]);
const popup = read(ARGV[2]);
let managed = true;
let closed = 0;
const menu = {
    actor: {clear_constraints() {}, hide() {}},
    box: {add_style_class_name() {}, get_children: () => []},
    _titleRow: {hide() {}},
    connect() {},
    close() { closed++; },
};
const item = {
    menu,
    _menuManager: {removeMenu(value) { equal('removed detail menu', value, menu); managed = false; }},
    connect() {},
};
const host = {
    _grid: {layout_manager: {child_set_property() {}}},
    _pages: {add_child() {}},
    // Use the real late-indicator adoption method. Without Studio utilities,
    // ordinary embedded pages retain their original menu ownership behavior.
    _adoptStudioTiling: new Function('item', method(quick, '_adoptStudioTiling')),
};
const PopupMenu = {PopupSeparatorMenuItem: class {}};
new Function('item', 'colSpan', 'PopupMenu', method(quick, '_completeAddItem'))
    .call(host, item, 1, PopupMenu);
// The detail body's private grab closes on a press in the sibling header.
// Removing that grab leaves the enclosing menu's existing grab authoritative.
const Clutter = {EventType: {BUTTON_PRESS: 1, TOUCH_BEGIN: 2, KEY_PRESS: 3, ENTER: 4},
    EVENT_PROPAGATE: 0, EVENT_STOP: 1};
const eventGlobal = {stage: {get_event_actor: () => ({headerSwitch: true})}};
const BoxPointer = {PopupAnimation: {FULL: 1}};
const captured = new Function('actor', 'event', 'Clutter', 'global', 'BoxPointer',
    method(popup, '_onCapturedEvent'));
if (managed) captured.call({}, {_delegate: menu, contains: () => false},
    {type: () => Clutter.EventType.BUTTON_PRESS}, Clutter, eventGlobal, BoxPointer);
equal('header switch press keeps embedded page open', closed, 0);
equal('embedded page has no duplicate modal manager', managed, false);
// External menu providers have no per-item manager and still embed safely.
new Function('item', 'colSpan', 'PopupMenu', method(quick, '_completeAddItem'))
    .call(host, {menu, connect() {}}, 1, PopupMenu);

const marker = "this.menu.connectObject('open-state-changed', (_m, open) => {";
const start = bluetooth.indexOf(marker);
const end = bluetooth.indexOf('\n        }, this);', start);
if (start < 0 || end < 0) throw new Error('Missing discovery open callback');
const body = bluetooth.slice(start + marker.length, end);
let scans = 0;
let stops = 0;
const toggle = {
    _client: {active: true},
    _scanNearby() { scans++; },
    _syncNearby() {},
    _stopNearby() { stops++; },
};
const opened = new Function('_m', 'open', body).bind(toggle);
opened(null, true);
equal('opening powered Bluetooth starts discovery', scans, 1);
opened(null, false);
equal('closing stops owned discovery', stops, 1);
toggle._client.active = false;
opened(null, true);
equal('opening powered-off Bluetooth does not scan', scans, 1);

toggle._deviceItems = new Map();
toggle._updatePlaceholder = () => {};
toggle._sync = () => {};
toggle.menu = {isOpen: true};
toggle._client.active = true;
const powered = new Function(method(bluetooth, '_onActiveChanged')).bind(toggle);
powered();
equal('enabling Bluetooth in the page starts discovery', scans, 2);
toggle.menu.isOpen = false;
powered();
equal('enabling with the page closed does not scan', scans, 2);

let discoveries = 0;
let cancellations = 0;
const adapter = {default_adapter: '/adapter/A', default_adapter_setup_mode: false};
const discovery = {
    _client: {active: true, _client: adapter},
    _nearStatus: {text: ''}, _lookAgain: {hide() {}, show() {}},
    _pairAgent: {cancel() { cancellations++; }},
    _syncNearby() { discoveries++; },
};
// A timer in the exact scan method is a regression: the open picker owns
// discovery for its lifetime, not a bounded preview. Native9s proof is separate.
const GLib = {timeout_add() {throw new Error('open discovery must not expire on a timer');}};
const scan = new Function('GLib', '_', method(bluetooth, '_scanNearby'));
const stop = new Function(method(bluetooth, '_stopNearby'));
scan.call(discovery, GLib, text => text);
equal('new scan enables adapter discovery', adapter.default_adapter_setup_mode, true);
equal('new scan records ownership', discovery._discoveryOwned, true);
equal('open scan records active lifecycle', discovery._discovering, true);
scan.call(discovery, GLib, text => text);
equal('repeated refresh keeps the same discovery lease', discoveries, 1);
stop.call(discovery);
equal('closing owned picker stops adapter discovery', adapter.default_adapter_setup_mode, false);
equal('closing clears active lifecycle', discovery._discovering, false);
equal('closing cancels owned pairing', cancellations, 1);
adapter.default_adapter_setup_mode = true;
scan.call(discovery, GLib, text => text);
equal('external discovery is not claimed', discovery._discoveryOwned, false);
stop.call(discovery);
equal('external discovery survives closing', adapter.default_adapter_setup_mode, true);
adapter.default_adapter_setup_mode = false;
scan.call(discovery, GLib, text => text);
adapter.default_adapter = '/adapter/B';
adapter.default_adapter_setup_mode = true;
stop.call(discovery);
equal('adapter replacement preserves the replacement lease', adapter.default_adapter_setup_mode, true);
const stopsBeforeRadioOff = stops;
toggle.menu.isOpen = true;
toggle._client.active = false;
powered();
equal('radio-off ends owned picker discovery', stops, stopsBeforeRadioOff + 1);
print('Quick Options: embedded page and owned open discovery behavior passed');
