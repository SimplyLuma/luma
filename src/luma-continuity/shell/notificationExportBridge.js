// SPDX-License-Identifier: Apache-2.0
// Private GJS integration candidate; hosted by existing Shell, not a daemon.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {NotificationExport} from './notificationExport.js';

const PATH = '/org/projectluma/Connect/Notifications';
const IFACE = 'org.projectluma.Connect.NotificationExport1';
const XML = `<node><interface name="${IFACE}">
  <method name="Open"><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
  <method name="Snapshot"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="Invoke"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="Close"/>
  <signal name="Changed"><arg type="t"/><arg type="as"/></signal>
</interface></node>`;

export class NotificationExportBridge {
    constructor(main, actionableStates) {
        this.main = main;
        this.owner = null;
        this.watches = [];
        this.sourceConsent = new WeakMap();
        this.actionConsent = new WeakMap();
        this.policy = new NotificationExport({
            randomId: () => GLib.uuid_string_random().replaceAll('-', ''),
            now: () => GLib.get_monotonic_time() / 1_000_000,
            isLocked: () => main.sessionMode.isLocked || main.sessionMode.isGreeter,
            sourceAllowed: (source, peer) => this.sourceConsent.get(source)?.has(peer) ?? false,
            actionAllowed: (source, _notification, _action, peer) =>
                this.actionConsent.get(source)?.has(peer) ?? false,
            isActionable: notification => actionableStates.has(notification.lifecycleState),
        });
        this.dbus = Gio.DBusExportedObject.wrapJSObject(XML, this);
        // Reuse Shell's connection/identity. Never own org.freedesktop.Notifications.
        this.dbus.export(Gio.DBus.session, PATH);
        this.nameWatch = Gio.bus_watch_name_on_connection(Gio.DBus.session,
            'org.projectluma.Connect1', Gio.BusNameWatcherFlags.NONE,
            (_connection, _name, owner) => { this.policy.revoke(); this.owner = owner; },
            () => { this.policy.revoke(); this.owner = null; });
        this._watch(main.sessionMode, 'updated', () => this._changed());
        this._watch(main.messageTray, 'source-added', (_tray, source) => {
            this._observeSource(source); this._changed();
        });
        this._watch(main.messageTray, 'source-removed', () => this._changed());
        for (const source of main.messageTray.getSources())
            this._observeSource(source);
    }

    _watch(object, signal, callback) {
        const id = object.connect(signal, callback);
        this.watches.push([object, id]);
    }

    _observeSource(source) {
        this._watch(source, 'notification-added', (_source, notification) => {
            this._observeNotification(notification); this._changed();
        });
        this._watch(source, 'destroy', () => this._changed());
        for (const notification of source.notifications)
            this._observeNotification(notification);
    }

    _observeNotification(notification) {
        for (const signal of ['notify', 'action-added', 'action-removed', 'destroy'])
            this._watch(notification, signal, () => this._changed());
    }

    // Invoke from trusted local Settings integration with actual Source objects,
    // not app titles or remote-supplied desktop IDs. Default remains deny.
    setSourceConsent(source, peer, {read = false, actions = false}) {
        for (const [map, value] of [[this.sourceConsent, read], [this.actionConsent, read && actions]]) {
            let peers = map.get(source);
            if (!peers) { peers = new Set(); map.set(source, peers); }
            if (value) peers.add(peer); else peers.delete(peer);
        }
        this._changed();
    }

    // Required explicit hook from notificationDaemon before replacement even
    // when identical properties would not emit notify. GTK replacement too.
    notificationReplaced() { this._changed(); }

    _changed() {
        const {generation, removed} = this.policy.changed();
        if (this.owner)
            Gio.DBus.session.emit_signal(this.owner, PATH, IFACE, 'Changed',
                new GLib.Variant('(tas)', [generation, removed]));
    }

    _call(invocation, fn, outputType = '()') {
        if (!this.owner || invocation.get_sender() !== this.owner) {
            invocation.return_dbus_error(`${IFACE}.Error.Unauthorized`, 'Unauthorized');
            return;
        }
        try {
            const result = fn();
            invocation.return_value(new GLib.Variant(outputType, outputType === '()' ? [] : [result]));
        } catch (_) {
            // Never log/return producer text, native callback errors or tokens.
            invocation.return_dbus_error(`${IFACE}.Error.Denied`, 'Denied');
        }
    }

    OpenAsync([peer, session], invocation) {
        this._call(invocation, () => this.policy.bind(this.owner, peer, session));
    }
    SnapshotAsync([peer, session], invocation) {
        this._call(invocation, () => JSON.stringify(this.policy.snapshot(this.owner, peer, session,
            this.main.messageTray.getSources())), '(s)');
    }
    InvokeAsync([peer, session, token, request], invocation) {
        this._call(invocation, () => this.policy.invoke(this.owner, peer, session, token, request), '(s)');
    }
    CloseAsync(_parameters, invocation) {
        this._call(invocation, () => this.policy.revoke());
    }
    destroy() {
        this.policy.revoke();
        Gio.bus_unwatch_name(this.nameWatch);
        for (const [object, id] of this.watches) {
            try { object.disconnect(id); } catch (_) { /* disposed native source */ }
        }
        this.watches = [];
        this.dbus.unexport();
    }
}
