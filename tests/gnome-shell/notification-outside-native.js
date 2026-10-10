// SPDX-License-Identifier: GPL-2.0-or-later
// Actual Wayland client routing, real virtual input, and installed Shell actors.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';
import {getNotificationTray} from 'resource:///org/gnome/shell/ui/lumaNotificationBeacon.js';
import {sleep} from 'resource:///org/gnome/shell/ui/scripting.js';
let count = 0;
function check(value, name) {
    count++;
    print(`${value ? 'PASS' : 'FAIL'} notifications: ${name}`);
    if (!value) throw new Error(name);
}
const rect = actor => {
    const [x, y] = actor.get_transformed_position();
    const [width, height] = actor.get_transformed_size();
    return {x, y, width, height};
};
export async function run() {
    new Gio.Settings({schema_id: 'org.gnome.desktop.interface'}).set_boolean('enable-animations', false);
    Main.overview.hide();
    await sleep(700);
    const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.NONE});
    launcher.setenv('WAYLAND_DISPLAY', GLib.getenv('LUMA_NOTIFICATION_CLIENT_DISPLAY'), true);
    launcher.setenv('GDK_BACKEND', 'wayland', true);
    launcher.setenv('GSK_RENDERER', 'cairo', true);
    const process = launcher.spawnv(['python3', GLib.getenv('LUMA_NOTIFICATION_CLIENT')]);
    const lip = getNotificationTray();
    const seat = global.stage.context.get_backend().get_default_seat();
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const touch = seat.create_virtual_device(Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);
    const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    let source;
    try {
        let window;
        for (let i = 0; i < 40 && !window; i++) {
            await sleep(100);
            window = global.get_window_actors().find(a =>
                a.mapped && a.meta_window.get_title() === 'Notification outside-click probe')?.meta_window;
        }
        check(!!window, 'real Wayland application window appears');
        window.activate(global.get_current_time());
        await sleep(700);
        const frame = window.get_frame_rect();
        const x = frame.x + 40, y = frame.y + 80;
        print('CLIENT_GEOMETRY ' + JSON.stringify({frame, buffer: window.get_buffer_rect(),
            overview: Main.overview.visible, focus: global.display.focus_window?.get_title(),
            modalCount: Main.modalCount, mapped: window.get_compositor_private()?.mapped,
            point: [x, y]}));
        async function click(button = Clutter.BUTTON_PRIMARY) {
            pointer.notify_absolute_motion(GLib.get_monotonic_time(), x, y);
            await sleep(50);
            pointer.notify_button(GLib.get_monotonic_time(), button, Clutter.ButtonState.PRESSED);
            await sleep(40);
            pointer.notify_button(GLib.get_monotonic_time(), button, Clutter.ButtonState.RELEASED);
            await sleep(250);
        }
        function clientClicks() {
            try { return Number(new TextDecoder().decode(GLib.file_get_contents(GLib.getenv('LUMA_NOTIFICATION_CLIENT_CLICKS'))[1])); }
            catch { return 0; }
        }
        await click();
        check(clientClicks() > 0, 'click reaches actual application before opening notifications');
        for (const populated of [false, true]) {
            if (populated) {
                source = new MessageTray.Source({title: 'Notification test', iconName: 'dialog-information-symbolic'});
                Main.messageTray.add(source);
                source.addNotification(new MessageTray.Notification({source, title: 'Retain this notification', body: 'Opening and closing must not dismiss it.'}));
                await sleep(300);
            }
            const label = populated ? 'populated' : 'empty';
            for (const button of [Clutter.BUTTON_PRIMARY, Clutter.BUTTON_SECONDARY]) {
                lip.open(); await sleep(200);
                check(lip.isOpen, `${label} panel opens`);
                const bounds = rect(lip);
                check(x < bounds.x || x >= bounds.x + bounds.width || y < bounds.y || y >= bounds.y + bounds.height,
                    'test application point is outside notification bounds');
                await click(button);
                check(!lip.isOpen, `${label} panel closes on application ${button === Clutter.BUTTON_PRIMARY ? 'left' : 'right'} click`);
                check(!lip._grab && !lip._outsideId, 'close releases grab and capture listener');
            }
            lip.open(); await sleep(200);
            let bounds = rect(lip);
            pointer.notify_absolute_motion(GLib.get_monotonic_time(), bounds.x + 25, bounds.y + 20);
            pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
            pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
            await sleep(200);
            check(lip.isOpen, `${label} inside click keeps panel open`);
            touch.notify_touch_down(GLib.get_monotonic_time(), 0, x, y);
            await sleep(50);
            touch.notify_touch_up(GLib.get_monotonic_time(), 0);
            await sleep(200);
            check(!lip.isOpen, `${label} application touch closes panel`);
            lip.open(); await sleep(200);
            pointer.notify_absolute_motion(GLib.get_monotonic_time(), 8, global.stage.height - 8);
            pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
            pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
            await sleep(200);
            check(!lip.isOpen, `${label} desktop click closes panel`);
            lip.open(); await sleep(150);
            keyboard.notify_keyval(GLib.get_monotonic_time(), Clutter.KEY_Escape, Clutter.KeyState.PRESSED);
            keyboard.notify_keyval(GLib.get_monotonic_time(), Clutter.KEY_Escape, Clutter.KeyState.RELEASED);
            await sleep(200);
            check(!lip.isOpen, `${label} Escape closes panel`);
            if (populated) check(source.notifications.length === 1, 'outside dismissal preserves notification record');
            const before = clientClicks();
            await click();
            check(clientClicks() > before, 'application clicks resume after closing notifications');
        }
        lip.close(); lip._rest(); await sleep(200);
        let nub = rect(lip);
        pointer.notify_absolute_motion(GLib.get_monotonic_time(), nub.x + nub.width / 2, nub.y + nub.height / 2);
        pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
        await sleep(40);
        pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
        await sleep(200);
        check(lip.isOpen, 'real nub mouse click still opens after repeated dismissal');
        await click();
        check(!lip.isOpen, 'application click dismisses panel opened through nub');
        lip._rest(); await sleep(200); nub = rect(lip);
        touch.notify_touch_down(GLib.get_monotonic_time(), 0, nub.x + nub.width / 2, nub.y + nub.height / 2);
        await sleep(40);
        touch.notify_touch_up(GLib.get_monotonic_time(), 0);
        await sleep(200);
        check(lip.isOpen, 'real nub touch tap still opens');
        await click();
        check(!lip.isOpen, 'application click dismisses panel opened through touch');
        print(`NATIVE NOTIFICATION OUTSIDE PASS (${count} assertions)`);
    } finally {
        lip.close();
        source?.destroy();
        process.send_signal(15);
    }
}
