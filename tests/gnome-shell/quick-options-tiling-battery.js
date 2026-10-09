// SPDX-License-Identifier: GPL-2.0-or-later
// gjs -m quick-options-tiling-battery.js quickSettings.js system.js extension.js
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import UPower from 'gi://UPowerGlib';

String.prototype.format = imports.format.format;
function read(path) {
    const [, bytes] = Gio.File.new_for_path(path).load_contents(null);
    return new TextDecoder().decode(bytes);
}
function script(source) {
    return source.replace(/^import .*;\n/gm, '')
        .replace(/^export default /gm, '').replace(/^export /gm, '');
}
function equal(actual, expected, name) {
    if (actual !== expected)
        throw new Error(`${name}: ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`);
}
if (ARGV.length !== 3)
    throw new Error('Expected actual QuickSettings, System and Tiling extension module paths');
const [quick, system, extension] = ARGV.map(read);
for (const source of [quick, system, extension])
    new Function(script(source));

const start = system.indexOf('export function lumaBatteryStatus(');
const end = system.indexOf('\n}', start) + 2;
if (start < 0 || end < 2)
    throw new Error('Actual battery formatter missing');
const formatBattery = new Function('UPower', '_',
    script(system.slice(start, end)) + '\nreturn lumaBatteryStatus;')(UPower, text => text);
const S = UPower.DeviceState;
const cases = [
    [{State: S.FULLY_CHARGED, Percentage: 100, TimeToEmpty: 5971 * 3600}, 'Fully charged'],
    [{State: S.CHARGING, Percentage: 64, TimeToEmpty: 5971 * 3600}, 'Charging'],
    [{State: S.CHARGING, Percentage: 100}, 'Fully charged'],
    [{State: S.PENDING_CHARGE, Percentage: 80}, 'Plugged in'],
    [{State: S.UNKNOWN, Percentage: 90, TimeToEmpty: 3600}, ''],
    [{State: S.DISCHARGING, Percentage: 100, TimeToEmpty: 5971 * 3600}, ''],
    [{State: S.DISCHARGING, Percentage: 75, TimeToEmpty: 5971 * 3600}, ''],
    [{State: S.DISCHARGING, Percentage: 75, TimeToEmpty: NaN}, ''],
    [{State: S.DISCHARGING, Percentage: 75, TimeToEmpty: Infinity}, ''],
    [{State: S.DISCHARGING, Percentage: 75, TimeToEmpty: 0}, ''],
    [{State: S.DISCHARGING, Percentage: 75, TimeToEmpty: -1}, ''],
    [{State: S.DISCHARGING, Percentage: 75, TimeToEmpty: 2 * 3600 + 17 * 60}, '2 h 17 min left'],
    [{State: S.DISCHARGING, Percentage: 3, TimeToEmpty: 17 * 60}, '17 min left'],
    [{State: S.DISCHARGING, Percentage: 1, TimeToEmpty: 45}, 'Less than a minute left'],
];
cases.forEach(([proxy, expected], index) => equal(formatBattery(proxy), expected, `battery ${index}`));

// The header button is momentary; it must invert the bound native toggle.
const handlerStart = quick.indexOf("this._pageSwitch.connect('clicked', () => {");
const handlerEnd = quick.indexOf("\n        });", handlerStart);
if (handlerStart < 0 || handlerEnd < 0) throw new Error('Actual page switch handler missing');
const handlerBody = quick.slice(quick.indexOf('{', handlerStart) + 1, handlerEnd);
const clickPage = new Function(handlerBody);
class TilingToggle { constructor() { this.checked = false; } }
const headerRadio = new TilingToggle();
const header = {_activeMenu: {_studioRadio: headerRadio}, _pageSwitch: {checked: false}};
clickPage.call(header);
equal(headerRadio.checked, true, 'momentary page button enables tiling');
clickPage.call(header);
equal(headerRadio.checked, false, 'momentary page button disables tiling');

const register = klass => klass;
const endpointCalls = [];
const endpointGio = {DBusCallFlags: Gio.DBusCallFlags, DBus: {session: {call(...args) {
    endpointCalls.push(args);
    args.at(-1)({call_finish: reply => reply}, new GLib.Variant('(b)', [true]));
}}}};
const Toggle = new Function('GObject', 'QuickSettings', 'Extension', 'Gio', 'GLib',
    script(extension) + '\nreturn TilingToggle;')(
    {registerClass: register}, {QuickMenuToggle: class {}, SystemIndicator: class {}}, class {}, endpointGio, GLib);
const endpointOwner = Object.create(Toggle.prototype);
endpointOwner._cancellable = new Gio.Cancellable();
for (const method of ['EnableExtension', 'DisableExtension']) {
    endpointOwner._callExtensionService(method);
    const call = endpointCalls.at(-1);
    equal(call[0], 'org.gnome.Shell', `${method} native manager bus`);
    equal(call[1], '/org/gnome/Shell', `${method} native manager object`);
    equal(call[2], 'org.gnome.Shell.Extensions', `${method} manager interface`);
    equal(call[3], method, `${method} request method`);
    equal(call[4].deepUnpack()[0], 'tilingshell@ferrarodomenico.com', `${method} managed extension`);
}
print('PASS two native extension-manager wire requests');
const uuid = 'tilingshell@ferrarodomenico.com';
function toggle({enabled = false, disabled = false, auto = false, accepted = true, writable = true} = {}) {
    const item = Object.create(Toggle.prototype);
    const state = {enabled, disabled, auto, accepted, writable, calls: []};
    item._shellSettings = {get_strv: key => key === 'enabled-extensions'
        ? (state.enabled ? [uuid] : []) : (state.disabled ? [uuid] : [])};
    item._tilingSettings = {
        get_boolean: () => state.auto,
        set_boolean: (_key, value) => {
            if (!state.writable) return false;
            state.auto = value;
            return true;
        },
    };
    item._callExtensionService = (method, callback) => {
        state.calls.push(method);
        if (state.accepted === true) state.enabled = method === 'EnableExtension';
        callback(state.accepted === null ? null : {deepUnpack: () => [state.accepted]});
    };
    item._syncFromSettings();
    return [item, state];
}
for (const [settings, expected] of [
    [{enabled: true, auto: true}, true],
    [{enabled: true, auto: true, disabled: true}, false],
    [{enabled: true, auto: false}, false],
    [{enabled: false, auto: true}, false],
]) {
    const [item] = toggle(settings);
    equal(item.checked, expected, 'tiling effective state');
}
for (const accepted of [true, false, null]) {
    const [item, state] = toggle({accepted});
    item.checked = true;
    item._applyRuntimeState();
    equal(state.auto, accepted === true, 'tiling enable and refusal rollback');
    equal(state.calls.length, 1, 'one bounded enable request');
}
{
    const [item, state] = toggle({writable: false});
    item.checked = true;
    item._applyRuntimeState();
    equal(state.calls.length, 0, 'locked tiling sends no enable request');
    equal(item.checked, false, 'locked tiling retains effective state');
}
{
    const [item, state] = toggle({enabled: true, auto: true});
    item.checked = false;
    item._applyRuntimeState();
    equal(state.auto, false, 'turning off disables automatic placement');
    equal(state.calls[0], 'DisableExtension', 'turning off disables runtime');
}
// Use real GObject notifications: a synchronous settings callback during a
// checked notification can queue a second notification after _syncing clears.
const NativeSwitch = GObject.registerClass({Properties: {
    checked: GObject.ParamSpec.boolean('checked', 'checked', 'checked',
        GObject.ParamFlags.READWRITE, false),
}}, class NativeSwitch extends GObject.Object {});
function nativeSwitch() {
    const item = new NativeSwitch();
    const state = {enabled: false, auto: false, calls: [], pending: []};
    item._syncFromSettings = Toggle.prototype._syncFromSettings;
    item._applyRuntimeState = Toggle.prototype._applyRuntimeState;
    item._shellSettings = {get_strv: key => key === 'enabled-extensions' && state.enabled ? [uuid] : []};
    item._tilingSettings = {get_boolean: () => state.auto, set_boolean: (_key, value) => {
        state.auto = value;
        item._syncFromSettings();
        return true;
    }};
    item._callExtensionService = (method, callback) => {
        state.calls.push(method);
        state.pending.push(accepted => {
            if (accepted) state.enabled = method === 'EnableExtension';
            item._syncFromSettings();
            callback({deepUnpack: () => [accepted]});
        });
    };
    item.connect('notify::checked', () => item._applyRuntimeState());
    return [item, state];
}
{
    const [item, state] = nativeSwitch();
    item.checked = true;
    equal(state.calls.length, 1, 'synchronous setting change sends one enable');
    equal(item.checked, true, 'requested state survives until asynchronous reply');
    state.pending.shift()(true);
    equal(item.checked, true, 'accepted native enable retained');
    equal(state.auto, true, 'accepted automatic placement retained');
    item.checked = false;
    state.pending.shift()(true);
    equal(state.calls.join(','), 'EnableExtension,DisableExtension', 'one request per actual toggle');
    equal(item.checked, false, 'accepted native disable retained');
}
{
    const [item, state] = nativeSwitch();
    item.checked = true;
    state.pending.shift()(false);
    equal(item.checked, false, 'native refusal restores switch');
    equal(state.auto, false, 'native refusal restores placement');
    equal(state.calls.length, 1, 'native refusal sends no inverse request');
}
{
    const [item, state] = nativeSwitch();
    item.checked = true;
    item.checked = false;
    equal(state.calls.length, 1, 'second click waits for first request');
    state.pending.shift()(true);
    equal(state.calls.at(-1), 'DisableExtension', 'second click runs after first reply');
    state.pending.shift()(true);
    equal(item.checked, false, 'latest click retained');
    equal(state.auto, false, 'latest click retains placement');
}
print(JSON.stringify({syntax_modules: 3, battery_cases: cases.length, tiling_cases: 11,
    native_notification_cases: 3, result: 'PASS'}));
