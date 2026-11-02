// SPDX-License-Identifier: Apache-2.0
// TV posture input: one Shell service turns every controller into the TV's
// keys (arrows, Return = OK, Escape = Back, Menu) and Home into the TV home.
// Gamepads (USB, 2.4 GHz, Bluetooth) are read through libmanette and their
// keys injected from a virtual keyboard; remote keys that are not TV keys
// (XF86Back, XF86OK, CEC's keys through rc-cec, ...) are taken as Mutter
// accelerators and re-sent as TV keys. The TV home, the TV keyboard and every
// application therefore read keys only; none of them carries gamepad code.
// It exists only while the posture is TV. The rules are tvInputModel.js;
// docs/developer-platform/tv-mode.md ("Remotes and gamepads").
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {tv as TOKENS} from './tvTokens.js';
import {tvEntry} from './tvModel.js';
import {AXIS, BUTTON, DIRECTIONS, REMOTE_KEYS, TV_KEYS, buttonAction, confirmButton, hatDirection, padIdentity,
    repeatDelay, stickDirection, translates} from './tvInputModel.js';

const BUS_NAME = 'org.projectluma.Shell.Remote';
const OBJECT_PATH = '/org/projectluma/Shell/Remote';
const IFACE = `<node>
  <interface name="org.projectluma.Shell.Remote1">
    <method name="ClaimGamepads">
      <arg type="b" direction="in" name="claimed"/>
    </method>
    <property name="Gamepads" type="as" access="read"/>
  </interface>
</node>`;

const SCHEMA = 'org.project_luma.shell-state';
const CONFIRM_KEY = 'tv-confirm-button';
const KEYVALS = Object.fromEntries(Object.entries(TV_KEYS).map(([action, name]) => [action, Clutter[`KEY_${name}`]]));

export class TvInput {
    // `home` opens the TV home.
    constructor({home}) {
        this._home = home;
        this._input = TOKENS.input;
        this._sources = new Map();      // a pad's control -> the action it holds
        this._repeats = new Map();      // direction -> {steps, id}
        this._pads = new Map();         // Manette.Device -> {id, signals, stick, buttons}
        this._claims = new Map();       // bus name -> {pid, watch}
        this._grabs = new Map();        // accelerator action -> keysym name
        this._nextPad = 0;
        // Which face button is OK (luma-shell-state); an older schema without
        // the key behaves as automatic.
        this._settings = new Gio.Settings({schema_id: SCHEMA});
        this._hasConfirm = this._settings.settings_schema.has_key(CONFIRM_KEY);
        this._confirmId = this._hasConfirm
            ? this._settings.connect(`changed::${CONFIRM_KEY}`, () => this._applyConfirm()) : 0;
        this._keys = Clutter.get_default_backend().get_default_seat()
            .create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        this._grabRemoteKeys();
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._nameId = Gio.bus_own_name_on_connection(Gio.DBus.session, BUS_NAME,
            Gio.BusNameOwnerFlags.REPLACE, null, null);
        this._startGamepads();
    }

    destroy() {
        this._destroyed = true;
        if (this._confirmId) this._settings.disconnect(this._confirmId);
        for (const action of [...this._sources.values()]) this._release(action);
        this._sources.clear();
        for (const {id} of this._repeats.values()) GLib.source_remove(id);
        this._repeats.clear();
        for (const device of [...this._pads.keys()]) this._padRemoved(device);
        for (const id of this._monitorSignals ?? []) this._monitor.disconnect(id);
        this._monitor = null;
        if (this._acceleratorId) global.display.disconnect(this._acceleratorId);
        for (const action of this._grabs.keys()) {
            Main.wm.allowKeybinding(Meta.external_binding_name_for_action(action), Shell.ActionMode.NONE);
            global.display.ungrab_accelerator(action);
        }
        this._grabs.clear();
        for (const {watch} of this._claims.values()) Gio.bus_unwatch_name(watch);
        this._claims.clear();
        this._dbus.unexport();
        Gio.bus_unown_name(this._nameId);
        this._keys = null;
    }

    // ── keys out ──────────────────────────────────────────────────────────

    _tap(action) {
        const keyval = KEYVALS[action];
        if (!keyval || !this._keys) return;
        const time = GLib.get_monotonic_time();
        this._keys.notify_keyval(time, keyval, Clutter.KeyState.PRESSED);
        this._keys.notify_keyval(time, keyval, Clutter.KeyState.RELEASED);
    }

    _act(action) {
        if (action === 'home') this._home();
        else this._tap(action);
    }

    // ── remotes: keys that are not TV keys become TV keys ─────────────────

    _grabRemoteKeys() {
        for (const name of Object.keys(REMOTE_KEYS)) {
            const action = global.display.grab_accelerator(name, Meta.KeyBindingFlags.IGNORE_AUTOREPEAT);
            if (action === Meta.KeyBindingAction.NONE) {
                console.warn(`TV input: ${name} is taken by another accelerator`);
                continue;
            }
            Main.wm.allowKeybinding(Meta.external_binding_name_for_action(action), Shell.ActionMode.ALL);
            this._grabs.set(action, name);
        }
        this._acceleratorId = global.display.connect('accelerator-activated', (_display, action) => {
            const name = this._grabs.get(action);
            if (name) this._act(REMOTE_KEYS[name]);
        });
    }

    // ── gamepads ──────────────────────────────────────────────────────────

    async _startGamepads() {
        let Manette;
        try {
            Manette = (await import('gi://Manette?version=0.2')).default;
        } catch (error) {
            console.warn(`TV input: no gamepads without libmanette (${error.message})`);
            return;
        }
        if (this._destroyed) return;
        this._monitor = new Manette.Monitor();
        this._monitorSignals = [
            this._monitor.connect('device-connected', (_monitor, device) => this._padAdded(device)),
            this._monitor.connect('device-disconnected', (_monitor, device) => this._padRemoved(device)),
        ];
        const iter = this._monitor.iterate();
        for (let [ok, device] = iter.next(); ok; [ok, device] = iter.next()) this._padAdded(device);
    }

    _padAdded(device) {
        if (this._pads.has(device)) return;
        const pad = {id: this._nextPad++, stick: null, buttons: new Set(), x: 0, y: 0,
            identity: padIdentity(device.get_guid?.()), name: device.get_name() ?? ''};
        pad.confirm = this._confirmFor(pad);
        pad.signals = [
            device.connect('button-press-event', (_d, event) => this._button(pad, event, true)),
            device.connect('button-release-event', (_d, event) => this._button(pad, event, false)),
            device.connect('hat-axis-event', (_d, event) => this._hat(pad, event)),
            device.connect('absolute-axis-event', (_d, event) => this._axis(pad, event)),
        ];
        this._pads.set(device, pad);
        this._dbus.emit_property_changed('Gamepads', new GLib.Variant('as', this.Gamepads));
    }

    _padRemoved(device) {
        const pad = this._pads.get(device);
        if (!pad) return;
        for (const id of pad.signals) device.disconnect(id);
        for (const source of [...this._sources.keys()]) {
            if (source.startsWith(`${pad.id}:`)) this._hold(source, null);
        }
        this._pads.delete(device);
        if (!this._destroyed) this._dbus.emit_property_changed('Gamepads', new GLib.Variant('as', this.Gamepads));
    }

    _button(pad, event, pressed) {
        const [ok, code] = event.get_button();
        if (!ok) return;
        if (pressed) pad.buttons.add(code);
        else pad.buttons.delete(code);
        // Select+Start together go home: pads without a Guide button.
        if (pressed && pad.buttons.has(BUTTON.SELECT) && pad.buttons.has(BUTTON.START)) {
            this._press('home');
            return;
        }
        const action = buttonAction(code, pad.confirm);
        if (!action) return;
        if (DIRECTIONS.includes(action)) this._hold(`${pad.id}:button:${code}`, pressed ? action : null);
        else if (pressed) this._press(action);
    }

    _hat(pad, event) {
        const [ok, axis, value] = event.get_hat();
        if (ok && (axis === AXIS.HAT0X || axis === AXIS.HAT0Y))
            this._hold(`${pad.id}:hat:${axis}`, hatDirection(axis, value));
    }

    _axis(pad, event) {
        const [ok, axis, value] = event.get_absolute();
        if (!ok || (axis !== AXIS.X && axis !== AXIS.Y)) return;
        if (axis === AXIS.X) pad.x = value;
        else pad.y = value;
        pad.stick = stickDirection(pad.x, pad.y, pad.stick, this._input);
        this._hold(`${pad.id}:stick`, pad.stick);
    }

    _confirmFor(pad) {
        return confirmButton(this._hasConfirm ? this._settings.get_string(CONFIRM_KEY) : 'automatic',
            pad.identity, pad.name);
    }

    _applyConfirm() {
        for (const pad of this._pads.values()) pad.confirm = this._confirmFor(pad);
    }

    // OK, Back, Menu and Home: once per press.
    _press(action) {
        if (translates(action, this._context())) this._act(action);
    }

    // Directions: held by a control (a D-pad button, a hat axis, the stick)
    // and repeated at the TV pace while any control holds them.
    _hold(source, action) {
        const previous = this._sources.get(source) ?? null;
        if (previous === action) return;
        if (action) this._sources.set(source, action);
        else this._sources.delete(source);
        if (previous && ![...this._sources.values()].includes(previous)) this._release(previous);
        if (action && !this._repeats.has(action) && translates(action, this._context())) this._step(action, 1);
    }

    _step(action, steps) {
        this._tap(action);
        const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, repeatDelay(steps, this._input), () => {
            this._repeats.delete(action);
            if ([...this._sources.values()].includes(action) && translates(action, this._context()))
                this._step(action, steps + 1);
            return GLib.SOURCE_REMOVE;
        });
        this._repeats.set(action, {steps, id});
    }

    _release(action) {
        const repeat = this._repeats.get(action);
        if (repeat) GLib.source_remove(repeat.id);
        this._repeats.delete(action);
    }

    // ── who gets the pad ──────────────────────────────────────────────────

    _context() {
        const focus = global.stage.key_focus;
        const shellFocus = Boolean(focus) && focus !== global.stage;
        const keyboardVisible = Boolean(Main.keyboard?.visible);
        const focused = global.display.focus_window;
        let window = null;
        if (focused && !shellFocus) {
            const app = Shell.WindowTracker.get_default().get_window_app(focused);
            const info = app?.get_app_info?.();
            const entry = info ? tvEntry(app.get_id(), app.get_name(), key => info.get_string(key)) : null;
            const pid = focused.get_pid();
            window = {
                presentation: entry?.presentation ?? '',
                claimed: pid > 0 && [...this._claims.values()].some(claim => claim.pid === pid),
            };
        }
        return {shellFocus, keyboardVisible, window};
    }

    // ── D-Bus: org.projectluma.Shell.Remote1 ──────────────────────────────

    get Gamepads() {
        return [...this._pads.keys()].map(device => device.get_name() ?? '');
    }

    // An application that reads the gamepads itself (a game, an emulator's
    // game view) claims them: while its window has the focus no TV keys come
    // from a pad. The claim ends with false or when the caller leaves the bus.
    ClaimGamepadsAsync([claimed], invocation) {
        const sender = invocation.get_sender();
        const drop = () => {
            const claim = this._claims.get(sender);
            if (claim) Gio.bus_unwatch_name(claim.watch);
            this._claims.delete(sender);
        };
        if (!claimed) {
            drop();
            invocation.return_value(null);
            return;
        }
        Gio.DBus.session.call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
            'GetConnectionUnixProcessID', new GLib.Variant('(s)', [sender]), new GLib.VariantType('(u)'),
            Gio.DBusCallFlags.NONE, -1, null, (connection, result) => {
                try {
                    const [pid] = connection.call_finish(result).deepUnpack();
                    if (this._destroyed) throw new Error('TV posture ended');
                    drop();
                    const watch = Gio.bus_watch_name_on_connection(Gio.DBus.session, sender,
                        Gio.BusNameWatcherFlags.NONE, null, () => drop());
                    this._claims.set(sender, {pid, watch});
                    invocation.return_value(null);
                } catch (error) {
                    invocation.return_error_literal(Gio.DBusError, Gio.DBusError.FAILED, error.message);
                }
            });
    }
}
