// SPDX-License-Identifier: Apache-2.0
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const BUS = 'org.gnome.Mutter.DisplayConfig';
const PATH = '/org/gnome/Mutter/DisplayConfig';
const read = path => new TextDecoder().decode(GLib.file_get_contents(path)[1]).trim();

export default class Tester extends Extension {
    enable() {
        this._cancel = new Gio.Cancellable();
        this._directory = `${GLib.get_user_runtime_dir()}/luma-tester`;
        this._marker = `${this._directory}/ready.json`;
        GLib.unlink(this._marker);
        if (Main.layoutManager._startingUp) {
            this._startup = Main.layoutManager.connect('startup-complete', () => {
                Main.layoutManager.disconnect(this._startup);
                this._startup = 0;
                this._configure().catch(logError);
            });
        } else {
            this._configure().catch(logError);
        }
    }

    _call(method, parameters) {
        return new Promise((resolve, reject) => {
            Gio.DBus.session.call(BUS, PATH, BUS, method, parameters, null,
                Gio.DBusCallFlags.NONE, 15000, this._cancel, (bus, result) => {
                    try { resolve(bus.call_finish(result).deepUnpack()); }
                    catch (error) { reject(error); }
                });
        });
    }

    async _configure() {
        const target = read('/etc/luma-tester-target');
        if (target === 'mobile') {
            const [serial, monitors] = await this._call('GetCurrentState', null);
            const [spec, modes] = monitors[0];
            const mode = modes.find(m => m[1] === 432 && m[2] === 960) ??
                modes.find(m => m[6]['is-current']?.deepUnpack()) ?? modes[0];
            const rotation = mode[1] > mode[2] ? 1 : 0;
            const scale = mode[5].includes(1) ? 1 : mode[4];
            await this._call('ApplyMonitorsConfig', new GLib.Variant(
                '(uua(iiduba(ssa{sv}))a{sv})',
                [serial, 1, [[0, 0, scale, rotation, true, [[spec[0], mode[0], {}]]]], {}]));
        }
        if (this._cancel.is_cancelled())
            return;
        // Allow monitor allocation and handheld posture to settle, then require
        // an actual complete paint. No boot timer stands in for readiness.
        let paints = 0;
        this._paint = global.stage.connect_after('after-paint', () => {
            const monitor = Main.layoutManager.primaryMonitor;
            if (!monitor || (target === 'mobile' && monitor.height <= monitor.width))
                return;
            if (++paints < 2) {
                global.stage.queue_redraw();
                return;
            }
            global.stage.disconnect(this._paint);
            this._paint = 0;
            GLib.mkdir_with_parents(this._directory, 0o700);
            GLib.file_set_contents(this._marker, JSON.stringify({
                target, boot_id: read('/proc/sys/kernel/random/boot_id'),
                width: monitor.width, height: monitor.height,
            }));
        });
        global.stage.queue_redraw();
    }

    disable() {
        this._cancel.cancel();
        if (this._startup)
            Main.layoutManager.disconnect(this._startup);
        if (this._paint)
            global.stage.disconnect(this._paint);
        this._startup = this._paint = 0;
        GLib.unlink(this._marker);
    }
}
