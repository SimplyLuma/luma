// SPDX-License-Identifier: GPL-2.0-or-later
// A genuinely stopped disposable GTK client triggers the native window-manager
// close dialog. The factory observation preserves the original constructor and
// native response handlers; no application/close-dialog semantics are mocked.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as CloseDialog from 'resource:///org/gnome/shell/ui/closeDialog.js';
import * as Dialog from 'resource:///org/gnome/shell/ui/dialog.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';
import * as SurfaceMaterials from 'resource:///org/gnome/shell/ui/lumaSurfaceMaterials.js';
const sleep = ms => Scripting.sleep(ms);
const failures = [];
let count = 0;
function check(value, message) { count++; print(`${value ? 'PASS' : 'FAIL'} close-dialog: ${message}`); if (!value) failures.push(message); }
async function capture(name) {
    const path = GLib.getenv('LUMA_CLOSE_EVIDENCE');
    if (!path) return;
    const stream = Gio.File.new_for_path(`${path}/${name}.png`).replace(null, false, Gio.FileCreateFlags.NONE, null);
    try { await new Shell.Screenshot().screenshot(false, stream); } finally { stream.close(null); }
}
export async function run() {
    const iface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
    const a11y = new Gio.Settings({schema_id: 'org.gnome.desktop.a11y.interface'});
    const appearance = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});
    iface.set_boolean('enable-animations', false); Main.overview.hide();
    GLib.setenv('WAYLAND_DISPLAY', 'wayland-close-native', true);
    GLib.setenv('GDK_BACKEND', 'wayland', true);
    const seat = global.stage.context.get_backend().get_default_seat();
    const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    const touch = seat.create_virtual_device(Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    let observed = null;
    const originalInit = CloseDialog.CloseDialog.prototype._init;
    CloseDialog.CloseDialog.prototype._init = function(window) {
        originalInit.call(this, window); observed = {close: this, window};
    };
    let process = null;
    try {
        for (const [index,treatment] of ['light', 'dark', 'frost', 'glass'].entries()) {
            appearance.set_string('surface-treatment', treatment);
            iface.set_string('color-scheme', treatment === 'dark' ? 'prefer-dark' : 'prefer-light');
            await sleep(200); observed = null;
            process = Gio.Subprocess.new(['python3', GLib.getenv('LUMA_CLOSE_CLIENT')], Gio.SubprocessFlags.NONE);
            let actor;
            for (let i = 0; i < 50 && !actor; i++) {
                await sleep(100);
                actor = global.get_window_actors().find(a => a.mapped && a.width >= 700 && a.height >= 450 && a.meta_window.get_title() === 'Native Close Dialog Fixture');
            }
            if (!actor) throw Error('Real GTK window did not map; no close-dialog evidence produced');
            const window = actor.meta_window;
            check(window.get_client_type() === Meta.WindowClientType.WAYLAND, `${treatment}: real Wayland Meta recipient`);
            process.send_signal(19); // SIGSTOP: the actual disposable client cannot answer pings.
            await sleep(100); window.delete(global.display.get_current_time_roundtrip());
            for (let i = 0; i < 130 && !(observed?.window === window && observed.close._dialog?._dialog.mapped); i++) await sleep(100);
            if (observed?.window !== window || !observed.close._dialog?._dialog.mapped)
                throw Error('Native WM did not open its unresponsive-client dialog');
            const close = observed.close, owner = close._dialog, surface = owner._dialog;
            const responses = []; close.connect('response', (_dialog,response) => responses.push(response));
            await sleep(250);
            check(surface.mapped && surface.width > 250 && surface.height > 120, `${treatment}: actual dialog mapped allocation`);
            check(owner instanceof Dialog.Dialog, `${treatment}: existing shared Dialog owner`);
            check(surface.has_style_class_name('close-dialog') && surface.has_style_class_name('luma-system-prompt'), `${treatment}: native unresponsive dialog receives shared material`);
            const effective = SurfaceMaterials.effectiveTreatment();
            const expectedClasses = effective === 'frost' ? ['luma-surface-dark', 'luma-surface-smoke'] : [`luma-surface-${effective}`];
            print(`ACTUAL close-dialog requested=${treatment} effective=${effective} classes=${surface.style_class}`);
            check(expectedClasses.every(name => surface.has_style_class_name(name)), `${treatment}: actual native appearance binding (${effective})`);
            const [force,wait] = owner.buttonLayout.get_children();
            check(force.label === 'Force Quit' && wait.label === 'Wait', `${treatment}: upstream action order unchanged`);
            check(force.can_focus && wait.can_focus && force.reactive && wait.reactive, `${treatment}: both actions accessible`);
            check(owner.initialKeyFocus === force && force.has_style_pseudo_class('default'), `${treatment}: upstream default/focus semantics`);
            check(surface.get_theme_node().get_border_radius(St.Corner.TOPLEFT) === 15, `${treatment}: actual shared15px material radius`);
            check(force.height >= 36 && wait.height === force.height, `${treatment}: consistent native action geometry`);
            check(actor.get_first_child().get_effect('gnome-shell-frozen-window') !== null, `${treatment}: native frozen-window dimming`);
            a11y.set_boolean('high-contrast', true); await sleep(150);
            check(surface.has_style_class_name('luma-prompt-high-contrast'), `${treatment}: native high-contrast binding`);
            a11y.set_boolean('high-contrast', false); await sleep(150); await capture(`close-${treatment}`);
            const before = responses.length;
            if (index === 0 || index === 3) {
                close.vfunc_focus(); await sleep(150);
                const key = index === 0 ? Clutter.KEY_Escape : Clutter.KEY_Return;
                keyboard.notify_keyval(GLib.get_monotonic_time(), key, Clutter.KeyState.PRESSED);
                keyboard.notify_keyval(GLib.get_monotonic_time(), key, Clutter.KeyState.RELEASED);
            } else {
                const button = index === 1 ? wait : force;
                const [x,y] = button.get_transformed_position(), [w,h] = button.get_transformed_size();
                if (index === 2) {
                    touch.notify_touch_down(GLib.get_monotonic_time(), 0, x+w/2, y+h/2); await sleep(70);
                    touch.notify_touch_up(GLib.get_monotonic_time(), 0);
                } else {
                    pointer.notify_absolute_motion(GLib.get_monotonic_time(), x+w/2, y+h/2); await sleep(60);
                    pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
                    pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
                }
            }
            for (let i = 0; i < 20 && responses.length === before; i++) await sleep(100);
            const waiting = index < 2;
            check(responses.length === before+1 && responses.at(-1) === (waiting ? Meta.CloseDialogResponse.WAIT : Meta.CloseDialogResponse.FORCE_CLOSE), `${treatment}: real ${['Escape Wait','mouse Wait','touch Force Quit','default Return Force Quit'][index]} native response`);
            if (waiting) {
                check(GLib.file_test(`/proc/${process.get_identifier()}/status`, GLib.FileTest.EXISTS), `${treatment}: Wait preserves stopped application process`);
                process.send_signal(18); process.send_signal(15);
            } else {
                for (let i = 0; i < 25 && GLib.file_test(`/proc/${process.get_identifier()}/status`, GLib.FileTest.EXISTS); i++) await sleep(100);
                check(!GLib.file_test(`/proc/${process.get_identifier()}/status`, GLib.FileTest.EXISTS), `${treatment}: Force Quit actually terminates client`);
                // Cleanup remains explicit if any gate failed; never leave a stopped fixture.
                if (GLib.file_test(`/proc/${process.get_identifier()}/status`, GLib.FileTest.EXISTS)) { process.send_signal(18); process.send_signal(15); }
            }
            process = null; await sleep(350);
        }
    } finally {
        CloseDialog.CloseDialog.prototype._init = originalInit;
        if (process) { process.send_signal(18); process.send_signal(15); }
        a11y.set_boolean('high-contrast', false); appearance.reset('surface-treatment');
    }
    if (failures.length) throw Error(failures.join('; '));
    print(`NATIVE CLOSE DIALOG PASS (${count} assertions)`);
}
