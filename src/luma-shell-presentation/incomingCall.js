// SPDX-License-Identifier: Apache-2.0
// A ringing call takes the handheld screen: it wakes the display and sits
// above the lock screen with Answer and Decline, so a call never needs an
// unlock or a trip to the notification list. Answered while locked, the same
// surface carries the call's own controls until the call ends or the user
// unlocks. It renders the call notification Phone already publishes (the
// shell's incoming-call contract: critical, with answer and decline actions),
// so any calling app that follows that contract gets it.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageList from 'resource:///org/gnome/shell/ui/messageList.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';

function action(notification, id) {
    return notification?.actions?.find(a => a.id === id) ?? null;
}

// Like the incoming contract, recognised by its actions: notifications carry
// no category in the shell.
function isOngoingCall(notification) {
    return notification?.urgency === MessageTray.Urgency.CRITICAL &&
        !!action(notification, 'end') && !MessageList.isIncomingCallNotification(notification);
}

export class CallTakeover {
    constructor(presentation) {
        this._presentation = presentation;
        this._sources = new Map();
        this._calls = new Set();
        this._privacy = new Gio.Settings({schema_id: 'org.gnome.desktop.notifications'});
        this._sourceAdded = Main.messageTray.connect('source-added', (_tray, source) => this._watch(source));
        for (const source of Main.messageTray.getSources())
            this._watch(source);
        this._lockChanged = Main.sessionMode.connect('updated', () => this._sync());
    }

    _watch(source) {
        if (this._sources.has(source))
            return;
        const ids = [
            source.connect('notification-added', (_source, notification) => this._track(notification)),
            source.connect('destroy', () => this._sources.delete(source)),
        ];
        this._sources.set(source, ids);
        for (const notification of source.notifications ?? [])
            this._track(notification);
    }

    _track(notification) {
        if (this._calls.has(notification))
            return;
        if (!MessageList.isIncomingCallNotification(notification) && !isOngoingCall(notification))
            return;
        this._calls.add(notification);
        const ids = [
            notification.connect('notify', () => this._sync()),
            notification.connect('destroy', () => {
                for (const id of ids)
                    notification.disconnect(id);
                this._calls.delete(notification);
                this._sync();
            }),
        ];
        if (MessageList.isIncomingCallNotification(notification))
            this._wake();
        this._sync();
    }

    // The ringing call, else (only while locked) the call in progress.
    _current() {
        const calls = [...this._calls];
        const ringing = calls.find(n => MessageList.isIncomingCallNotification(n));
        if (ringing)
            return ringing;
        return Main.sessionMode.isLocked ? calls.find(isOngoingCall) ?? null : null;
    }

    _sync() {
        const notification = this._presentation.mode === 'mobile' ? this._current() : null;
        if (!notification) {
            this._hide();
            return;
        }
        this._show(notification);
    }

    _wake() {
        Main.screenShield?._wakeUpScreen?.();
        Gio.DBus.session.call('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
            'org.freedesktop.DBus.Properties', 'Set', new GLib.Variant('(ssv)',
                ['org.gnome.Mutter.DisplayConfig', 'PowerSaveMode', new GLib.Variant('i', 0)]),
            null, Gio.DBusCallFlags.NONE, 3000, null, (bus, result) => {
                try {
                    bus.call_finish(result);
                } catch (error) {
                    console.warn(`Incoming call could not wake the display: ${error.message}`);
                }
            });
    }

    _build() {
        this._actor = new St.Widget({
            style_class: 'luma-call-takeover',
            reactive: true,
            layout_manager: new Clutter.BinLayout(),
        });
        const column = new St.BoxLayout({
            style_class: 'luma-call-takeover-column',
            orientation: Clutter.Orientation.VERTICAL,
            x_expand: true,
            y_expand: true,
        });
        this._status = new St.Label({style_class: 'luma-call-takeover-status', x_align: Clutter.ActorAlign.CENTER});
        this._caller = new St.Label({style_class: 'luma-call-takeover-caller', x_align: Clutter.ActorAlign.CENTER});
        this._caller.clutter_text.line_wrap = true;
        this._detail = new St.Label({style_class: 'luma-call-takeover-detail', x_align: Clutter.ActorAlign.CENTER});
        this._buttons = new St.BoxLayout({
            style_class: 'luma-call-takeover-buttons',
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.END,
            y_expand: true,
        });
        column.add_child(this._status);
        column.add_child(this._caller);
        column.add_child(this._detail);
        column.add_child(this._buttons);
        this._actor.add_child(column);
        Main.layoutManager.modalDialogGroup.add_child(this._actor);
        this._monitorsChanged = Main.layoutManager.connect('monitors-changed', () => this._fit());
    }

    _fit() {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;
        this._actor.set_position(monitor.x, monitor.y);
        this._actor.set_size(monitor.width, monitor.height);
    }

    _button(label, icon, styleClass, callback) {
        const box = new St.BoxLayout({
            style_class: 'luma-call-takeover-action',
            orientation: Clutter.Orientation.VERTICAL,
        });
        const button = new St.Button({
            style_class: `luma-call-takeover-button ${styleClass}`,
            child: new St.Icon({icon_name: icon}),
            accessible_name: label,
            can_focus: true,
        });
        button.connect('clicked', callback);
        box.add_child(button);
        box.add_child(new St.Label({text: label, style_class: 'luma-call-takeover-label', x_align: Clutter.ActorAlign.CENTER}));
        return box;
    }

    _show(notification) {
        if (!this._actor)
            this._build();
        this._fit();
        const ringing = MessageList.isIncomingCallNotification(notification);
        const locked = Main.sessionMode.isLocked;
        const identity = !locked || this._privacy.get_boolean('show-in-lock-screen');
        this._status.text = ringing ? 'Incoming call' : notification.body || 'Call';
        this._caller.text = identity ? notification.title || 'Unknown caller' : 'Phone call';
        this._detail.text = identity ? '' : 'Unlock to see who is calling';
        this._detail.visible = !identity;

        const key = `${ringing}:${notification.actions.map(a => `${a.id}=${a.label}`).join(',')}`;
        if (key !== this._buttonsKey) {
            this._buttonsKey = key;
            this._buttons.destroy_all_children();
            const invoke = id => () => action(notification, id)?.activate();
            if (ringing) {
                this._buttons.add_child(this._button('Decline', 'luma-phone-hangup-symbolic', 'luma-call-takeover-decline', invoke('decline')));
                this._buttons.add_child(this._button('Answer', 'luma-phone-symbolic', 'luma-call-takeover-answer', invoke('answer')));
            } else {
                const mute = action(notification, 'mute');
                if (mute)
                    this._buttons.add_child(this._button(mute.label, 'luma-phone-mic-symbolic', 'luma-call-takeover-neutral', invoke('mute')));
                this._buttons.add_child(this._button('End', 'luma-phone-hangup-symbolic', 'luma-call-takeover-decline', invoke('end')));
            }
        }

        this._actor.show();
        Main.layoutManager.modalDialogGroup.set_child_above_sibling(this._actor, null);
        const mode = locked ? Shell.ActionMode.LOCK_SCREEN : Shell.ActionMode.SYSTEM_MODAL;
        if (this._grab && this._grabMode !== mode)
            this._release();
        if (!this._grab) {
            this._grab = Main.pushModal(this._actor, {actionMode: mode});
            this._grabMode = mode;
        }
    }

    _release() {
        if (this._grab)
            Main.popModal(this._grab);
        this._grab = null;
    }

    _hide() {
        this._release();
        this._actor?.hide();
    }

    destroy() {
        this._release();
        Main.messageTray.disconnect(this._sourceAdded);
        Main.sessionMode.disconnect(this._lockChanged);
        for (const [source, ids] of this._sources)
            for (const id of ids)
                source.disconnect(id);
        this._sources.clear();
        this._calls.clear();
        if (this._monitorsChanged)
            Main.layoutManager.disconnect(this._monitorsChanged);
        this._actor?.destroy();
    }
}
