// SPDX-License-Identifier: GPL-2.0-or-later
// Production battery renderer with native GLib escaping and no UPower access.
const Gio = imports.gi.Gio;
const GLib = imports.gi.GLib;
const Pango = imports.gi.Pango;
if (!ARGV[0]) throw Error('usage: gjs power-title-behavior.js APPLIED_SOURCE');
const [, bytes] = Gio.File.new_for_path(`${ARGV[0]}/js/ui/status/system.js`).load_contents(null);
const source = new TextDecoder().decode(bytes);
const begin = source.indexOf('        const syncBattery = () => {');
const end = source.indexOf('        this._powerToggle._proxy.connectObject', begin);
if (begin < 0 || end < 0) throw Error('production battery renderer missing');
const renderer = source.slice(begin, end);
function assert(ok, message) { if (!ok) throw Error(message); }
for (const [title, visible, expected] of [[null, false, ''], [undefined, false, ''],
    ['73%', true, '73%'], ['<&>', true, '&lt;&amp;&gt;']]) {
    let markup;
    const label = {clutter_text: {set_markup(value) { markup = value; }}};
    const key = {};
    const actor = {_powerToggle: {title, visible, _proxy: {}}};
    const sync = new Function('UPower', 'GLib', 'label', 'key', '_',
        renderer + '\nreturn syncBattery;').call(actor, {DeviceState: {CHARGING: 1}}, GLib, label, key, value => value);
    sync();
    assert(markup === `<span weight="650">${expected}</span><span alpha="40632"></span>`,
        'missing titles must render empty; available titles must retain native escaping');
    const [parsed, , text] = Pango.parse_markup(markup, -1, '\0');
    assert(parsed && text === (title ?? ''), 'native Pango markup must retain the actual title');
    assert(key.visible === visible, 'existing PowerToggle visibility must stay authoritative');
}
print('PASS production battery title: null/uninitialized, populated and escaped values');
