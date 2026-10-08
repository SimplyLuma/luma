// SPDX-License-Identifier: GPL-2.0-or-later
// Production Power-row permission bindings; never invokes real SystemActions.
const Gio = imports.gi.Gio;
if (!ARGV[0]) throw Error('usage: gjs system-behavior.js APPLIED_SHELL_SOURCE [--self-test]');
const [, bytes] = Gio.File.new_for_path(`${ARGV[0]}/js/ui/status/system.js`).load_contents(null);
const source = new TextDecoder().decode(bytes);
Reflect.parse(source, {target: 'module'});
const begin = source.indexOf('    _addSystemAction(');
const sync = source.indexOf('    _sync() {', begin);
const end = source.indexOf('\n});', sync);
if ([begin, sync, end].some(index => index < 0)) throw Error('Power row methods missing');
const methods = `${source.slice(begin, sync)},${source.slice(sync, end)}`;
class QuickSheetRow {
    constructor() { this.visible = true; this.listeners = {}; }
    setTitle(title) { this.title = title; }
    setIcon(icon) { this.icon = icon; }
    connect(name, callback) { this.listeners[name] = callback; }
}
const flags = {DEFAULT: 0, SYNC_CREATE: 1};
const production = new Function('QuickSheetRow', 'GObject', `return ({${methods}});`)
    (QuickSheetRow, {BindingFlags: flags});
function assert(ok, message) { if (!ok) throw Error(message); }
function permissionsTest() {
    const rows = [], bindings = [], invoked = [];
    const actor = Object.assign({_items: [], _powerSection: {addMenuItem(row) { rows.push(row); }},
        _systemActions: {bind_property(property, row, target, bindingFlags) {
            assert(target === 'visible' && (bindingFlags & flags.SYNC_CREATE), 'permissions must initialize row visibility');
            row.visible = false;
            bindings.push({property, row});
        }}}, production);
    const permissions = ['can-suspend', 'can-restart', 'can-power-off', 'can-logout'];
    const icons = ['moon', 'rotate-cw', 'power', 'log-out'];
    permissions.forEach((property, index) => actor._addSystemAction(`Action ${index}`, property,
        () => invoked.push(property)));
    actor._sync();
    assert(!actor.visible && rows.length === 4 && bindings.length === 4, 'a denied Power page must have no visible actions');
    rows.forEach((row, index) => {
        assert(row.icon === `lumaui-${icons[index]}-symbolic` && bindings[index].property === permissions[index],
            'each action must keep its own icon and permission binding');
        row.visible = true; row.listeners['notify::visible']();
        assert(actor.visible, 'an external permission change must update the footer');
        row.listeners.activate();
        row.visible = false; row.listeners['notify::visible']();
        assert(!actor.visible, 'revoked permission must hide the last eligible action');
    });
    assert(JSON.stringify(invoked) === JSON.stringify(permissions), 'each row must retain its original action callback');
}
if (ARGV.includes('--self-test')) {
    const original = production._sync;
    production._sync = function () { this.visible = true; };
    let detected = false;
    try { permissionsTest(); } catch { detected = true; }
    finally { production._sync = original; }
    assert(detected, 'checker must reject a footer with no permitted actions');
    print('PASS self-test: denied Power actions detected');
}
permissionsTest();
print('PASS Power-row permissions, icons, external changes and action callbacks');
