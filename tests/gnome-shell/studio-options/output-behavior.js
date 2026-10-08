// SPDX-License-Identifier: GPL-2.0-or-later
// Production Gvc presentation boundaries; no live audio changes.
const Gio = imports.gi.Gio;
if (!ARGV[0]) throw Error('usage: gjs output-behavior.js APPLIED_SHELL_SOURCE [--self-test]');
const [, bytes] = Gio.File.new_for_path(`${ARGV[0]}/js/ui/status/volume.js`).load_contents(null);
const source = new TextDecoder().decode(bytes);
Reflect.parse(source, {target: 'module'});
function section(begin, end, from = 0) {
    const a = source.indexOf(begin, from), b = source.indexOf(end, a);
    if (a < 0 || b < 0) throw Error(`production boundary missing: ${begin}`);
    return source.slice(a, b);
}
const methods = [section('    _setActiveDevice(activeId)', '    // The device in use'),
    section('    _deviceIcon(device)', '    _sync() {', source.indexOf('class OutputStreamSlider')),
    section('    _findHeadphones(sink)', '    _portChanged(')].join(',');
const production = new Function('Gio', '_', `return ({${methods}});`)
    ({ThemedIcon: class { constructor({name}) { this.name = name; } }}, value => value);
function assert(ok, message) { if (!ok) throw Error(message); }
function selectionTest() {
    const origins = ['Bluetooth', 'Built in', 'HDMI'];
    const rows = origins.map(origin => ({_lumaOrigin: origin,
        setCurrent(value) { this.current = value; }, setState(value) { this.state = value; }}));
    const actor = {_deviceItems: new Map(rows.map((row, id) => [id, row])),
        _syncLevelDetail(id) { this.detailId = id; },
        _activateDevice() { throw Error('external state must never activate another output'); }};
    for (const selected of [0, 2, null]) {
        production._setActiveDevice.call(actor, selected);
        rows.forEach((row, id) => assert(row.current === (id === selected) && row.state === origins[id],
            'selection must move its marker without adding text to the origin'));
        assert(actor.detailId === selected, 'external output change must update the level detail');
    }
}
function iconsTest() {
    for (const [form, name, port, expected] of [
        ['headset', 'bluez_output.fixture', '', 'headphones'],
        ['headphone', 'fixture', '', 'headphones'],
        ['speaker', 'fixture', 'analog-output-headphones', 'headphones'],
        ['speaker', 'alsa_output.fixture.hdmi-stereo', '', 'monitor'],
        ['speaker', 'alsa_output.fixture.analog-stereo', '', 'speaker'],
        ['speaker', 'luma-airplay-fixture', '', 'speaker'],
    ]) {
        const stream = {get_name: () => name, get_form_factor: () => form,
            get_ports: () => port ? [port] : [], get_port: () => ({port})};
        const actor = {_control: {get_stream_from_device: () => stream}, _findHeadphones: production._findHeadphones};
        const icon = production._deviceIcon.call(actor, {});
        assert(icon.name === `lumaui-${expected}-symbolic`, 'output must use its existing classification and Lucide role');
    }
    const fallback = production._deviceIcon.call({_control: {get_stream_from_device: () => null}}, {});
    assert(fallback.name === 'lumaui-speaker-symbolic', 'unresolved output must keep a visible speaker role');
}
if (ARGV.includes('--self-test')) {
    const original = production._setActiveDevice;
    production._setActiveDevice = function (id) {
        original.call(this, id);
        this._deviceItems.get(id)?.setState('In use');
    };
    let detected = false;
    try { selectionTest(); } catch { detected = true; }
    finally { production._setActiveDevice = original; }
    assert(detected, 'checker must reject a duplicate selected-state subtitle');
    print('PASS self-test: duplicate output state text detected');
}
for (const test of [selectionTest, iconsTest]) { test(); print(`PASS ${test.name}`); }
