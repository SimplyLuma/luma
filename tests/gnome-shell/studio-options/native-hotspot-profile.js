// SPDX-License-Identifier: GPL-2.0-or-later
// Real libnm setting construction with an in-memory client boundary.
// Never contacts NetworkManager or writes a system connection.
imports.gi.versions.NM = '1.0';
const Gio = imports.gi.Gio;
const GLib = imports.gi.GLib;
const NM = imports.gi.NM;
if (!ARGV[0]) throw Error('usage: gjs native-hotspot-profile.js APPLIED_SHELL_SOURCE');
const [, bytes] = Gio.File.new_for_path(`${ARGV[0]}/js/ui/status/network.js`).load_contents(null);
const source = new TextDecoder().decode(bytes);
Reflect.parse(source, {target: 'module'});
const begin = source.indexOf('    async _toggleHotspot(enabled)');
const end = source.indexOf('    _syncCount() {', begin);
if (begin < 0 || end < 0) throw Error('production hotspot method missing');
// Observe the production error boundary without changing its recovery behavior.
const observedErrors = [];
const method = source.slice(begin, end).replace('} catch {', '} catch (error) { observedErrors.push(error);');
const toggle = new Function('NM', 'GLib', '_', 'observedErrors', `return ({${method}})._toggleHotspot;`)
    (NM, GLib, value => value, observedErrors);
function assert(ok, message) { if (!ok) throw Error(message); }
const loop = new GLib.MainLoop(null, false);
let failed = false;
GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
(async () => {
    print(`NATIVE device capabilities: ${JSON.stringify(NM.DeviceCapabilities)}`);
    print(`NATIVE Wi-Fi capabilities: ${JSON.stringify(NM.DeviceWifiCapabilities)}`);
    let created = null, activations = 0;
    assert(NM.DeviceCapabilities.NM_SUPPORTED === 1 && NM.DeviceCapabilities.SUPPORTED === undefined,
        'native adapter capability enum must not invent a SUPPORTED alias');
    const device = {get_capabilities: () => NM.DeviceCapabilities.NM_SUPPORTED,
        get_wireless_capabilities: () => NM.DeviceWifiCapabilities.AP};
    const client = {get_connections: () => [],
        add_and_activate_connection_async(connection, selected, _path, _cancel, callback) {
            assert(selected === device, 'hotspot must use the selected AP-capable adapter');
            created = connection; callback(client, null);
        }, add_and_activate_connection_finish: () => created};
    const actor = {_client: client, _items: new Map([[device, {}]]),
        _hotspot: {label: {text: 'Hotspot'}}, _syncHotspot() {}};
    print('NATIVE libnm: dispatch secure profile toggle');
    await toggle.call(actor, true);
    print('NATIVE libnm: toggle settled');
    for (const error of observedErrors) printerr(`Production hotspot error: ${error.message}\n${error.stack}`);
    assert(created instanceof NM.SimpleConnection && !actor._hotspotBusy, 'real libnm profile must be constructed and settled');
    print('NATIVE libnm: verify profile');
    assert(created.verify(), 'libnm must accept the complete profile');
    assert(created.get_setting_connection().get_connection_type() === '802-11-wireless', 'connection must be wireless');
    assert(created.get_setting_wireless().get_mode() === 'ap', 'wireless setting must use AP mode');
    const security = created.get_setting_wireless_security();
    assert(security.get_key_mgmt() === 'wpa-psk' && security.get_psk().length >= 8,
        'native security setting must carry a valid secured passphrase');
    assert(created.get_setting_ip4_config().get_method() === 'shared' &&
        created.get_setting_ip6_config().get_method() === 'disabled', 'sharing must have the declared IP configuration');
    client.get_connections = () => [created];
    client.activate_connection_async = (connection, selected, _path, _cancel, callback) => {
        assert(connection === created && selected === device, 'secured native profile reuse must preserve its identity');
        activations++; callback(client, null);
    };
    client.activate_connection_finish = () => created;
    await toggle.call(actor, true);
    assert(activations === 1 && actor._hotspot.label.text === 'Hotspot', 'native secured profile must reuse cleanly');
    print('PASS real libnm profile validation and secured reuse (memory only)');
})().then(() => loop.quit()).catch(error => { printerr(`${error.message}\n${error.stack}`); failed = true; loop.quit(); });
    return GLib.SOURCE_REMOVE;
});
loop.run();
if (failed) imports.system.exit(1);
