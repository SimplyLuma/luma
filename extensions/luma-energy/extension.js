// SPDX-License-Identifier: MPL-2.0
//
// Luma Energy attention state.
//
// On Wayland there is no protocol that tells a client it is completely covered
// by another window. A client only learns that it has been unmapped. So a
// Chromium or Electron window buried under a maximised window believes it is
// visible and keeps painting, keeps running timers, keeps compositing --
// on every Wayland desktop, not just this one. The browser is not being lied
// to; it simply is not being told.
//
// The compositor is the only component that knows. This extension is how it
// says so. It reports and nothing else: it holds no policy, applies no limit,
// and if luma-energy is not running it does nothing at all.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as AltTab from 'resource:///org/gnome/shell/ui/altTab.js';

const BUS_NAME = 'org.projectluma.Energy1';
const OBJECT_PATH = '/org/projectluma/Energy1';
const INTERFACE = 'org.projectluma.Energy1';

// Window state settles in bursts -- a workspace switch restacks everything --
// so the report is coalesced. Short enough that the system reacts while the
// person is still looking at the result of what they did.
const SETTLE_MS = 250;

// States, most awake first. An application takes the state of its most awake
// window, so one visible window is enough to keep the whole application out of
// the way of any limit.
const FOCUSED = 'focused';
const VISIBLE = 'visible';
const OCCLUDED = 'occluded';
const HIDDEN = 'hidden';

/**
 * Window types that say nothing about whether the person is using an app.
 * A dock, a notification or a tooltip is not the application being used.
 */
function reportable(window) {
    if (!window || window.is_override_redirect())
        return false;
    const type = window.get_window_type();
    return type === Meta.WindowType.NORMAL ||
           type === Meta.WindowType.DIALOG ||
           type === Meta.WindowType.MODAL_DIALOG ||
           type === Meta.WindowType.UTILITY;
}

/**
 * The cgroup an application's window belongs to.
 *
 * Read from the kernel rather than built from a desktop id, so it is right for
 * rpm, deb, Flatpak, Snap, AppImage and anything else, with no table of
 * application ids to keep up to date. A window whose pid we cannot see -- an
 * X11 client on another machine -- simply is not reported.
 */
function cgroupOf(window) {
    const pid = window.get_pid();
    if (!pid || pid <= 0)
        return null;
    let contents;
    try {
        const [ok, bytes] = GLib.file_get_contents(`/proc/${pid}/cgroup`);
        if (!ok)
            return null;
        contents = new TextDecoder().decode(bytes);
    } catch {
        return null;
    }
    // cgroup v2: a single "0::/user.slice/..." line.
    for (const line of contents.split('\n')) {
        const parts = line.split(':');
        if (parts.length === 3 && parts[0] === '0' && parts[2].startsWith('/'))
            return parts[2];
    }
    return null;
}

/**
 * Is every part of this window covered by windows above it?
 *
 * Deliberately conservative: a window counts as occluded only when a single
 * higher window covers it outright. Accumulating a region would catch more
 * cases, and would also be the kind of cleverness that eventually decides a
 * window the person is reading is invisible. Being wrong in this direction
 * costs a little battery. Being wrong in the other direction costs the
 * person's trust, and they will turn the whole feature off.
 */
function occludedBy(rect, higher) {
    for (const other of higher) {
        const r = other.get_frame_rect();
        if (r.x <= rect.x && r.y <= rect.y &&
            r.x + r.width >= rect.x + rect.width &&
            r.y + r.height >= rect.y + rect.height)
            return true;
    }
    return false;
}

export default class LumaEnergyExtension extends Extension {
    enable() {
        this._proxy = null;
        this._settleId = 0;
        this._windowSignals = new Map();
        this._displaySignals = [];
        this._overviewSignals = [];
        this._lastReport = '';
        this._originalAltTab = null;

        Gio.DBusProxy.new(
            Gio.DBus.session, Gio.DBusProxyFlags.DO_NOT_AUTO_START, null,
            BUS_NAME, OBJECT_PATH, INTERFACE, null,
            (source, result) => {
                try {
                    this._proxy = Gio.DBusProxy.new_finish(result);
                } catch {
                    // Luma Energy is not installed or not running. Reporting
                    // to nobody is not an error; the desktop is unaffected.
                    this._proxy = null;
                    return;
                }
                this._schedule();
            });

        const display = global.display;
        this._displaySignals.push(
            display.connect('notify::focus-window', () => this._schedule()),
            display.connect('restacked', () => this._schedule()),
            display.connect('window-created', (_d, window) => {
                this._watch(window);
                // A window being created is the person arriving at an
                // application. Anything of that application that is asleep
                // should be awake before its first frame.
                this._thaw(window);
                this._schedule();
            }),
            display.connect('window-demands-attention', (_d, window) => this._thaw(window)),
            display.connect('window-marked-urgent', (_d, window) => this._thaw(window)));

        this._workspaceSignal = global.workspace_manager.connect(
            'active-workspace-changed', () => this._schedule());

        // Every one of these is the person about to look at something. Thaw on
        // the intention, before the animation, so that nothing they pick has
        // to be woken while they watch.
        this._overviewSignals.push(
            Main.overview.connect('showing', () => this._thawAll()),
            Main.overview.connect('hiding', () => this._schedule()));

        const popup = AltTab.AppSwitcherPopup.prototype;
        if (typeof popup.show === 'function') {
            this._originalAltTab = popup.show;
            const self = this;
            popup.show = function (...args) {
                // Alt-Tab thaws everything, not the selected application: by
                // the time the selection is known the person is already
                // waiting for it.
                self._thawAll();
                return self._originalAltTab.apply(this, args);
            };
        }

        for (const actor of global.get_window_actors())
            this._watch(actor.meta_window);

        this._schedule();
    }

    disable() {
        if (this._settleId) {
            GLib.source_remove(this._settleId);
            this._settleId = 0;
        }
        for (const id of this._displaySignals)
            global.display.disconnect(id);
        this._displaySignals = [];
        if (this._workspaceSignal) {
            global.workspace_manager.disconnect(this._workspaceSignal);
            this._workspaceSignal = 0;
        }
        for (const id of this._overviewSignals)
            Main.overview.disconnect(id);
        this._overviewSignals = [];
        for (const [window, ids] of this._windowSignals) {
            for (const id of ids)
                window.disconnect(id);
        }
        this._windowSignals.clear();
        if (this._originalAltTab) {
            // show() is inherited from SwitcherPopup, so wrapping it created an
            // own property that was not there before. Assigning the original
            // back would leave that own property behind and quietly change the
            // shape of a Shell class for everyone after us; deleting it puts
            // the prototype back exactly as it was found.
            delete AltTab.AppSwitcherPopup.prototype.show;
            if (AltTab.AppSwitcherPopup.prototype.show !== this._originalAltTab)
                AltTab.AppSwitcherPopup.prototype.show = this._originalAltTab;
            this._originalAltTab = null;
        }
        // Leaving without a word would strand every application in whatever
        // state it was last reported in. Say that nothing is known, which the
        // service reads as "apply nothing".
        this._send('SetAppStates', new GLib.Variant('(a(ss))', [[]]));
        this._proxy = null;
    }

    _watch(window) {
        if (!window || this._windowSignals.has(window))
            return;
        const ids = [
            window.connect('notify::minimized', () => this._schedule()),
            window.connect('notify::appears-focused', () => this._schedule()),
            window.connect('position-changed', () => this._schedule()),
            window.connect('size-changed', () => this._schedule()),
            window.connect('workspace-changed', () => this._schedule()),
            window.connect('unmanaged', () => {
                const held = this._windowSignals.get(window);
                if (held) {
                    for (const id of held)
                        window.disconnect(id);
                    this._windowSignals.delete(window);
                }
                this._schedule();
            }),
        ];
        this._windowSignals.set(window, ids);
    }

    _schedule() {
        if (this._settleId)
            return;
        this._settleId = GLib.timeout_add(GLib.PRIORITY_DEFAULT_IDLE, SETTLE_MS, () => {
            this._settleId = 0;
            this._report();
            return GLib.SOURCE_REMOVE;
        });
    }

    /** Work out one state per cgroup and send it, if it has changed. */
    _report() {
        if (!this._proxy)
            return;

        const active = global.workspace_manager.get_active_workspace();
        const focus = global.display.focus_window;
        const states = new Map();

        // Top of the stack first, so "the windows above this one" is simply
        // everything already seen.
        const stack = global.display.sort_windows_by_stacking(
            global.get_window_actors()
                .map(actor => actor.meta_window)
                .filter(window => reportable(window))).reverse();

        const above = [];
        for (const window of stack) {
            const cgroup = cgroupOf(window);
            if (!cgroup)
                continue;

            let state;
            if (window === focus)
                state = FOCUSED;
            else if (window.minimized)
                state = HIDDEN;
            else if (!window.located_on_workspace(active))
                state = OCCLUDED;
            else if (occludedBy(window.get_frame_rect(), above))
                state = OCCLUDED;
            else
                state = VISIBLE;

            if (!window.minimized && window.located_on_workspace(active))
                above.push(window);

            // An application is as awake as its most awake window.
            const held = states.get(cgroup);
            const order = [FOCUSED, VISIBLE, OCCLUDED, HIDDEN];
            if (!held || order.indexOf(state) < order.indexOf(held))
                states.set(cgroup, state);
        }

        const payload = [...states.entries()].sort((a, b) => a[0].localeCompare(b[0]));
        const digest = JSON.stringify(payload);
        if (digest === this._lastReport)
            return;
        this._lastReport = digest;
        this._send('SetAppStates', new GLib.Variant('(a(ss))', [payload]));
    }

    _thaw(window) {
        const cgroup = window ? cgroupOf(window) : null;
        if (cgroup)
            this._send('Thaw', new GLib.Variant('(s)', [cgroup]));
    }

    _thawAll() {
        this._send('ThawAll', null);
    }

    /**
     * Fire and forget. The Shell must never wait on another process to draw a
     * frame, so nothing here has a reply to miss and every failure is silent:
     * the worst case is that power is spent as it is spent today.
     */
    _send(method, parameters) {
        if (!this._proxy)
            return;
        try {
            this._proxy.call(method, parameters, Gio.DBusCallFlags.NO_AUTO_START,
                             -1, null, null);
        } catch {
            // Deliberately ignored; see above.
        }
    }
}
