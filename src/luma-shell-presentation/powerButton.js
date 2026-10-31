// SPDX-License-Identifier: Apache-2.0
// Handheld short press locks and blanks; logind retains emergency long press.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

export class HandheldPowerButton {
    constructor() {
        this._action = 0;
        this._wakeAction = 0;
        this._lastWake = 0;
        this._lastPress = 0;
        this._blanked = false;
        let deviceClass = GLib.getenv('LUMA_DEVICE_CLASS');
        try { deviceClass ||= new TextDecoder().decode(GLib.file_get_contents('/etc/luma-device-class')[1]).trim(); } catch {}
        if (deviceClass !== 'handheld') return;
        this._bus = Gio.DBus.session;
        this._cancellable = new Gio.Cancellable();
        Gio.DBusProxy.new(this._bus, Gio.DBusProxyFlags.NONE, null,
            'org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
            'org.gnome.Mutter.DisplayConfig', this._cancellable, (_source, result) => {
                try {
                    const proxy = Gio.DBusProxy.new_finish(result);
                    if (this._destroyed) return;
                    this._proxy = proxy;
                    this._ready();
                } catch (error) {
                    if (!this._destroyed) console.warn(`Handheld display power unavailable: ${error.message}`);
                }
            });
    }
    _ready() {
        this._changed = this._proxy.connect('g-properties-changed', () => {
            const mode = this._proxy.get_cached_property('PowerSaveMode')?.get_int32();
            if (mode === 0 && this._blanked) {
                this._blanked = false;
                this._lastWake = GLib.get_monotonic_time();
            }
        });
        this._activated = global.display.connect('accelerator-activated', (_display, action) => {
            // Controller gestures are wake-only, including on the lock screen.
            // Explicitly unblank instead of depending on an idle-active watch:
            // a short power press can blank while the session is already active.
            if (this._wakeAction && action === this._wakeAction) {
                const mode = this._proxy.get_cached_property('PowerSaveMode')?.get_int32();
                if (this._blanked || mode > 0) {
                    this._blanked = false;
                    this._lastWake = GLib.get_monotonic_time();
                    this._setMode(0);
                }
                return;
            }
            if (!this._action || action !== this._action) return;
            const now = GLib.get_monotonic_time();
            if (now - this._lastWake < 400000 || now - this._lastPress < 200000) return;
            this._lastPress = now;
            const off = this._blanked || this._proxy.get_cached_property('PowerSaveMode')?.get_int32() === 3;
            if (off) {
                this._blanked = false;
                this._setMode(0);
            } else {
                // Secure the session before switching off its display.
                Main.screenShield.lock(false);
                this._blanked = true;
                this._setMode(3);
            }
        });
        let attempts = 0;
        const grab = () => {
            if (!this._action) {
                this._action = global.display.grab_accelerator('XF86PowerOff', Meta.KeyBindingFlags.NONE);
                if (this._action)
                    Main.wm.allowKeybinding(Meta.external_binding_name_for_action(this._action), Shell.ActionMode.ALL);
            }
            if (!this._wakeAction) {
                this._wakeAction = global.display.grab_accelerator('XF86WakeUp', Meta.KeyBindingFlags.NONE);
                if (this._wakeAction)
                    Main.wm.allowKeybinding(Meta.external_binding_name_for_action(this._wakeAction), Shell.ActionMode.ALL);
            }
            if (this._action && this._wakeAction) {
                this._retry = 0;
                return GLib.SOURCE_REMOVE;
            }
            if (++attempts >= 20) {
                console.warn('Handheld power/wake shortcut unavailable; check native media-key profile ownership');
                this._retry = 0;
                return GLib.SOURCE_REMOVE;
            }
            return GLib.SOURCE_CONTINUE;
        };
        if (grab() === GLib.SOURCE_CONTINUE)
            this._retry = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 250, grab);
    }
    _setMode(mode) {
        this._bus.call('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
            'org.freedesktop.DBus.Properties', 'Set', new GLib.Variant('(ssv)',
                ['org.gnome.Mutter.DisplayConfig', 'PowerSaveMode', new GLib.Variant('i', mode)]),
            null, Gio.DBusCallFlags.NONE, 3000, null, (bus, result) => {
                try { bus.call_finish(result); } catch (error) { console.warn(`Display power request failed: ${error.message}`); }
            });
    }
    destroy() {
        this._destroyed = true;
        this._cancellable?.cancel();
        if (this._retry) GLib.source_remove(this._retry);
        for (const action of [this._action, this._wakeAction]) {
            if (!action) continue;
            Main.wm.allowKeybinding(Meta.external_binding_name_for_action(action), Shell.ActionMode.NONE);
            global.display.ungrab_accelerator(action);
        }
        if (this._activated) global.display.disconnect(this._activated);
        if (this._changed) this._proxy.disconnect(this._changed);
    }
}
