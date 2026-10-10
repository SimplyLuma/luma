// SPDX-License-Identifier: GPL-2.0-or-later
// Native GJS/Gio.Settings + production SignalTracker. No user's dconf/session.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import * as Tracker from './signalTracker.js';

const ShutdownOwner = GObject.registerClass({Signals: {shutdown: {}}},
class ShutdownOwner extends GObject.Object {});
globalThis.global = new ShutdownOwner();
const ActorOwner = GObject.registerClass({Signals: {destroy: {}}},
class ActorOwner extends GObject.Object {});
Tracker.registerDestroyableType(ActorOwner);
GObject.Object.prototype.connectObject = function (...args) {
    Tracker.connectObject(this, ...args);
};
GObject.Object.prototype.disconnectObject = function (owner) {
    Tracker.disconnectObject(this, owner);
};

function content(path) {
    const [ok, bytes] = GLib.file_get_contents(path);
    if (!ok) throw new Error(`Cannot read source: ${path}`);
    return new TextDecoder().decode(bytes);
}
const [path, kind] = ARGV;
if (!path || !['card', 'notifications'].includes(kind))
    throw new Error('Usage: gjs -m test-settings-teardown.js SOURCE card|notifications');
const production = content(path);
const region = production.slice(production.indexOf(kind === 'card'
    ? 'class PosterCard' : 'class PosterNotifications'));
const match = region.match(/this\.connect\('destroy', \(\) => \{([\s\S]*?)\n        \}\);/);
if (!match) throw new Error('Production destructor not found');
const actualDestructor = new Function(match[1]);
const owner = new ActorOwner();
if (kind === 'card') {
    owner._surfaceSettings = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
    owner._a11ySettings = new Gio.Settings({schema_id: 'org.gnome.desktop.a11y.interface'});
    owner._surfaceSettings.connectObject('changed::clock-format', () => {}, owner);
    owner._a11ySettings.connectObject('changed::high-contrast', () => {}, owner);
} else {
    owner._sources = new Map();
    owner._pending = new Map();
    owner._clockSettings = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
    owner._clockSettings.connectObject('changed::clock-format', () => {}, owner);
}
owner.connect('destroy', actualDestructor.bind(owner));
owner.emit('destroy');
if (Tracker.debugGetSignalTrackers().size !== 0)
    throw new Error('Disposed settings remain in the production signal tracker');
print(`NATIVE PRODUCTION ${kind.toUpperCase()} SETTINGS DISCONNECT BEFORE DISPOSE PASS`);
