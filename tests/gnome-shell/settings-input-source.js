// SPDX-License-Identifier: GPL-2.0-or-later
// gjs -m settings-input-source.js actual/js/ui/shellDBus.js
// Exercises the actual method body with a controlled input-source manager.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

const [, bytes] = Gio.File.new_for_path(ARGV[0]).load_contents(null);
const source = new TextDecoder().decode(bytes);
const start = source.indexOf('    async SelectInputSourceAsync(');
const end = source.indexOf('    async BeginApplicationDragAsync(', start);
if (start < 0 || end < 0)
    throw new Error('Production keyboard selection method missing');
if (!source.includes("this._inputSourceSenderChecker = new DBusSenderChecker([\n            'org.gnome.Settings',\n        ]);"))
    throw new Error('Keyboard selection must admit only the Settings bus owner');
const Main = {sessionMode: {isLocked: false, isGreeter: false}};
const us = {type: 'xkb', id: 'us'};
const gb = {type: 'xkb', id: 'gb'};
const ibus = {type: 'ibus', id: 'test'};
let calls = 0;
const manager = {
    inputSources: {0: us, 1: gb, 2: ibus}, currentSource: us,
    activateInputSource(item, interactive) {
        if (!interactive)
            throw new Error('Selection must persist the user-initiated source');
        calls++;
        this.currentSource = item;
    },
};
const Keyboard = {getInputSourceManager: () => manager};
const proto = new Function('Gio', 'GLib', 'Main', 'Keyboard',
    `return class {${source.slice(start, end)}};`)(Gio, GLib, Main, Keyboard);
const target = new proto();
let allowed = true;
target._inputSourceSenderChecker = {async checkInvocation() {
    if (!allowed)
        throw new Error('Unauthorized sender');
}};
function assert(value, message) {
    if (!value)
        throw new Error(message);
}
async function invoke(type, id) {
    const result = {reply: null, error: null};
    await target.SelectInputSourceAsync([type, id], {
        return_value(value) { result.reply = value.deepUnpack(); },
        return_gerror(error) { result.error = error; },
        return_error_literal(domain, code, message) { result.error = {code, message}; },
    });
    assert(Boolean(result.error) !== Boolean(result.reply), 'Exactly one terminal result');
    return result;
}
assert((await invoke('xkb', 'gb')).reply[0] && calls === 1 && manager.currentSource === gb,
    'Configured XKB source activates with actual manager readback');
assert((await invoke('ibus', 'test')).reply[0] && calls === 2 && manager.currentSource === ibus,
    'Configured IBus source can be selected');
allowed = false;
assert((await invoke('xkb', 'us')).error && calls === 2, 'Unauthorized sender cannot activate');
allowed = true;
Main.sessionMode.isLocked = true;
assert((await invoke('xkb', 'us')).error && calls === 2, 'Locked session cannot activate');
Main.sessionMode.isLocked = false;
Main.sessionMode.isGreeter = true;
assert((await invoke('xkb', 'us')).error && calls === 2, 'Greeter cannot activate');
Main.sessionMode.isGreeter = false;
assert((await invoke('xkb', 'missing')).error && calls === 2, 'Unconfigured layout is rejected');
assert((await invoke('ibus', 'gb')).error && calls === 2, 'Type and identifier must both match');
manager.activateInputSource = () => { throw new Error('Engine unavailable'); };
assert((await invoke('xkb', 'us')).error && manager.currentSource === ibus, 'Manager failure preserves source');
manager.activateInputSource = () => {};
assert((await invoke('xkb', 'us')).reply[0] === false, 'Failed readback never reports selection');
print('PASS: input selection, caller admission, lock/greeter, stale source and failed readback');
