// SPDX-License-Identifier: GPL-2.0-or-later
// Execute the production hotspot method against a disposable NM boundary.
// This verifies profile safety and lifecycle, not hardware connectivity.
const Gio = imports.gi.Gio;
const RuntimeGLib = imports.gi.GLib;
const root = ARGV[0];
if (!root) throw Error('usage: gjs network-behavior.js APPLIED_SHELL_SOURCE');
const [, bytes] = Gio.File.new_for_path(`${root}/js/ui/status/network.js`).load_contents(null);
const source = new TextDecoder().decode(bytes);
Reflect.parse(source, {target: 'module'});
const begin = source.indexOf('    async _toggleHotspot(enabled)');
const end = source.indexOf('    _syncCount()', begin);
if (begin < 0 || end < 0) throw Error('production hotspot method not found');
const method = source.slice(begin, end);
function assert(ok, why) { if (!ok) throw Error(why); }
function setting(name) {
    return class { constructor(properties) { this.name = name; Object.assign(this, properties); } };
}
const NM = {
    DeviceCapabilities: {NM_SUPPORTED: 1}, DeviceWifiCapabilities: {AP: 2},
    SimpleConnection: class { constructor() { this.settings = []; } add_setting(value) { this.settings.push(value); } },
    SettingConnection: setting('connection'), SettingWireless: setting('wireless'),
    SettingWirelessSecurity: setting('security'), SettingIP4Config: setting('ipv4'), SettingIP6Config: setting('ipv6'),
};
const GLib = {uuid_string_random: () => '01234567-89ab-cdef-0123-456789abcdef',
    Bytes: class { constructor(value) { this.value = value; } }};
function compile(text) {
    return new Function('NM', 'GLib', '_', 'TextEncoder', `return ({${text}})._toggleHotspot;`)
        (NM, GLib, value => value, TextEncoder);
}
const toggle = compile(method);
const [, quickBytes] = Gio.File.new_for_path(`${root}/js/ui/quickSettings.js`).load_contents(null);
const quickSource = new TextDecoder().decode(quickBytes);
Reflect.parse(quickSource, {target: 'module'});
const bindBegin = quickSource.indexOf('export function bindStudioSwitchRow(');
const bindEnd = quickSource.indexOf('export const QuickSheetRow', bindBegin);
if (bindBegin < 0 || bindEnd < 0) throw Error('production radio binding not found');
const bindText = quickSource.slice(bindBegin, bindEnd).replace('export ', '');
const bindRow = new Function(`${bindText}; return bindStudioSwitchRow;`)();
function radioBindingTest() {
    let writes = 0, checked = false, notify;
    const control = {reactive: true, reject: false,
        get checked() { return checked; },
        set checked(value) { if (checked !== value) { checked = value; notify?.(); } },
        connectObject(_checked, callback) { notify = callback; },
        emit(signal) { assert(signal === 'clicked', 'must use existing native action'); writes++; if (!this.reject) this.checked = !this.checked; }};
    const row = new PopupMenu.PopupSwitchMenuItem('Airplane mode', false);
    bindRow(control, row);
    control.checked = true;
    assert(row.state === true && writes === 0, 'external radio update must not write back');
    row.setToggleState(false);
    assert(control.checked === false && row.state === false && writes === 1, 'user toggle must delegate exactly once');
    control.reject = true; row.setToggleState(true);
    assert(control.checked === false && row.state === false && writes === 2, 'failed native action must retain the authoritative state');
    control.reactive = false; notify();
    row.setToggleState(true);
    assert(row.reactive === false && writes === 2, 'disabled radio must not dispatch a command');
}
const syncBegin = source.indexOf('    _setupHotspot()');
if (syncBegin < 0) throw Error('production hotspot setup not found');
const syncMethods = source.slice(syncBegin, begin);
const PopupMenu = {
    PopupSwitchMenuItem: class {
        constructor(text, state) { this.label = {text}; this.state = state; }
        connect(_name, callback) { this.callback = callback; }
        setToggleState(state) {
            if (this.state === state) return;
            this.state = state; this.callback?.(this, state);
        }
    },
    PopupMenuItem: class { constructor(text) { this.label = {text}; } },
};
function compileSync(text) {
    const methods = text.replace('\n    _syncHotspot()', ',\n    _syncHotspot()');
    return new Function('PopupMenu', '_', `return ({${methods}});`)(PopupMenu, value => value);
}
function externalUpdateTest(methods = compileSync(syncMethods)) {
    const calls = [], record = {is_hotspot: false};
    const actor = {_items: new Map([[{}, record]]), checked: true, visible: true,
        menu: {length: 1, addMenuItem() {}}, connectObject() {},
        _syncHotspot: methods._syncHotspot, _toggleHotspot(value) { calls.push(value); }};
    methods._setupHotspot.call(actor);
    record.is_hotspot = true;
    actor._syncHotspot();
    assert(actor._hotspot.state === true && calls.length === 0, 'external NM update must only update the switch');
    actor._hotspot.setToggleState(false);
    assert(calls.length === 1 && calls[0] === false, 'user toggle must dispatch once after external synchronization');
}
function fixture({supported = true, saved = [], fail = false} = {}) {
    const calls = [];
    const device = {get_capabilities: () => 1, get_wireless_capabilities: () => supported ? 2 : 0};
    const client = {
        get_connections: () => saved,
        add_and_activate_connection_async(connection, selected, _specific, _cancel, callback) {
            calls.push({kind: 'create', connection, selected}); callback(this, {});
        },
        add_and_activate_connection_finish() { if (fail) throw Error('NM rejected profile'); },
        activate_connection_async(connection, selected, _specific, _cancel, callback) {
            calls.push({kind: 'activate', connection, selected}); callback(this, {});
        },
        activate_connection_finish() { if (fail) throw Error('NM rejected activation'); },
    };
    const actor = {_client: client, _items: new Map([[device, {is_hotspot: false}]]),
        _hotspotBusy: false, _hotspot: {label: {text: 'Hotspot'}},
        _syncHotspot() { calls.push({kind: 'sync', busy: this._hotspotBusy}); }};
    return {actor, calls, device};
}
async function secureProfileTest(operation = toggle) {
    const {actor, calls, device} = fixture();
    await operation.call(actor, true);
    const writes = calls.filter(c => c.kind === 'create');
    assert(writes.length === 1 && writes[0].selected === device, 'must create once on the selected AP-capable adapter');
    const settings = Object.fromEntries(writes[0].connection.settings.map(s => [s.name, s]));
    assert(settings.connection.autoconnect === false, 'new sharing profile must not activate automatically');
    assert(settings.wireless.mode === 'ap', 'sharing must use access-point mode');
    assert(settings.security.key_mgmt === 'wpa-psk' && /^[0-9a-f]{32}$/.test(settings.security.psk), 'sharing must require a generated WPA key');
    assert(settings.ipv4.method === 'shared' && settings.ipv6.method === 'disabled', 'sharing must use the intended IP configuration');
    assert(actor._hotspotBusy === false && calls.at(-1).busy === false, 'busy state must clear after success');
}
async function lifecycleTest() {
    const saved = {get_id: () => 'Luma Hotspot', get_setting_wireless: () => ({mode: 'ap'}),
        get_setting_wireless_security: () => ({key_mgmt: 'wpa-psk'}), get_setting_ip4_config: () => ({method: 'shared'})};
    const reused = fixture({saved: [saved]});
    await toggle.call(reused.actor, true);
    assert(reused.calls.filter(c => c.kind === 'create').length === 0, 'existing AP profile must not be rewritten');
    assert(reused.calls.filter(c => c.kind === 'activate')[0]?.connection === saved, 'existing AP profile must be activated');
    for (const foreign of [{...saved, get_id: () => 'Personal hotspot'},
        {...saved, get_setting_wireless_security: () => ({key_mgmt: 'none'})},
        {...saved, get_setting_ip4_config: () => ({method: 'auto'})}]) {
        const isolated = fixture({saved: [foreign]});
        await toggle.call(isolated.actor, true);
        assert(isolated.calls.filter(c => c.kind === 'create').length === 1 &&
            !isolated.calls.some(c => c.kind === 'activate'), 'foreign, open or non-sharing profiles must not be activated');
    }
    for (const options of [{supported: false}, {fail: true}]) {
        const failed = fixture(options);
        await toggle.call(failed.actor, true);
        assert(!failed.actor._hotspotBusy && failed.actor._hotspot.label.text.includes('unavailable'), 'failure must be visible and clear busy state');
        if (!options.supported && !options.fail)
            assert(!failed.calls.some(c => c.kind === 'create'), 'unsupported hardware must not receive a profile write');
    }
    const busy = fixture(); busy.actor._hotspotBusy = true;
    await toggle.call(busy.actor, true);
    assert(busy.calls.length === 0, 'overlapping toggles must not duplicate a write');
    const disabled = fixture(); let stopped = 0, untouched = 0;
    disabled.actor._items = new Map([[{}, {is_hotspot: true, activate() { stopped++; }}],
        [{}, {is_hotspot: false, activate() { untouched++; }}]]);
    await toggle.call(disabled.actor, false);
    assert(stopped === 1 && untouched === 0 && !disabled.actor._hotspotBusy, 'turning sharing off must only deactivate hotspot items');
}
const loop = new RuntimeGLib.MainLoop(null, false);
(async () => {
    radioBindingTest(); print('PASS radioBindingTest');
    externalUpdateTest(); print('PASS externalUpdateTest');
    await secureProfileTest(); print('PASS secureProfileTest');
    await lifecycleTest(); print('PASS lifecycleTest');
    let detected = false;
    try { await secureProfileTest(compile(method.replace("key_mgmt: 'wpa-psk'", "key_mgmt: 'none'"))); }
    catch { detected = true; }
    assert(detected, 'fault injection must detect an insecure profile');
    detected = false;
    try { externalUpdateTest(compileSync(syncMethods.replace('if (!this._hotspotSyncing)', 'if (true)'))); }
    catch { detected = true; }
    assert(detected, 'fault injection must detect writes caused by external state updates');
    print('SELF-TEST detected insecure hotspot profile');
    print('SELF-TEST detected external-update activation');
})().then(() => loop.quit()).catch(error => { printerr(error.stack ?? error); imports.system.exit(1); });
loop.run();
