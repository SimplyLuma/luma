// SPDX-License-Identifier: GPL-2.0-or-later
// Run inside the installed Shell of a disposable graphical VM. The producer
// must be the real source-owned Android fixture, not a synthetic Shell source.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {getNotificationTray} from 'resource:///org/gnome/shell/ui/lumaNotificationBeacon.js';

const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
    resolve(); return GLib.SOURCE_REMOVE;
}));
const appId = 'waydroid.org.projectluma.androidtest';
const report = {scope: 'installed native notification reply entry and virtual input only',
    status: 'starting', production_qualified: false, android_delivery_qualified: false,
    first_frame_measured: false};
function check(value, reason) { if (!value) throw new Error(reason); }
function bounds(actor) {
    const [x, y] = actor.get_transformed_position();
    const [width, height] = actor.get_transformed_size();
    check(width > 0 && height > 0 && x >= 0 && y >= 0 && x + width <= global.stage.width + 1 &&
        y + height <= global.stage.height + 1, 'Reply control has no usable stage allocation');
    return {x, y, width, height};
}
function findCard(actor, notification) {
    if (actor.notification === notification && actor._replyEntry?.mapped && actor._replyButton?.mapped)
        return actor;
    for (const child of actor.get_children()) {
        const match = findCard(child, notification);
        if (match) return match;
    }
    return null;
}
export async function run(options = {}) {
    const out = options.out ?? GLib.getenv('LUMA_ANDROID_REPLY_EVIDENCE');
    const text = options.text ?? GLib.getenv('LUMA_ANDROID_REPLY_TEXT');
    check(/^\/var\/tmp\/luma-android-reply-ui-[A-Za-z0-9_-]+$/.test(out ?? ''), 'Private test output required');
    check(/^LumaAndroidReply-[0-9a-f]{8}$/.test(text ?? ''), 'Fixed synthetic reply text required');
    const save = () => GLib.file_set_contents(`${out}/native-ui.json`, JSON.stringify(report, null, 2));
    Gio._promisify(Shell.Screenshot.prototype, 'screenshot_stage_to_content');
    Gio._promisify(Shell.Screenshot, 'composite_to_stream');
    async function capture(name) {
        try {
            const [content, scale] = await new Shell.Screenshot().screenshot_stage_to_content();
            const stream = Gio.File.new_for_path(`${out}/${name}.png`).replace(null, false, Gio.FileCreateFlags.PRIVATE, null);
            try {
                await Shell.Screenshot.composite_to_stream(content.get_texture(), 0, 0,
                    global.stage.width, global.stage.height, scale, null, 0, 0, 1, stream);
            } finally { stream.close(null); }
            report[name] = 'captured';
        } catch { report[name] = 'unavailable'; }
    }
    let pointer, keyboard;
    const tray = getNotificationTray();
    try {
        report.boot_id = new TextDecoder().decode(GLib.file_get_contents('/proc/sys/kernel/random/boot_id')[1]).trim();
        report.started_unix_us = GLib.get_real_time();
        report.status = 'waiting-for-real-android-notification'; save();
        let notification;
        for (let attempt = 0; attempt < 2400 && !notification; attempt++) {
            notification = Main.messageTray.getSources().flatMap(source => source.notifications ?? []).find(item =>
                item.source?.app?.get_id()?.replace(/\.desktop$/, '') === appId &&
                item.title === 'Android integration fixture');
            if (!notification) await sleep(200);
        }
        check(notification, 'Actual Android fixture notification did not arrive');
        check(notification.inlineReply?.send, 'Installed notification has no negotiated native reply');
        tray.open(); await sleep(750);
        const card = findCard(global.stage, notification);
        check(card, 'Actual native reply entry is not mapped');
        report.entry_bounds = bounds(card._replyEntry);
        report.button_bounds = bounds(card._replyButton);
        const seat = global.stage.context.get_backend().get_default_seat();
        pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
        keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        async function click(actor) {
            const box = bounds(actor);
            pointer.notify_absolute_motion(GLib.get_monotonic_time(), box.x + box.width / 2, box.y + box.height / 2);
            await sleep(50);
            pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
            await sleep(40);
            pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
            await sleep(100);
        }
        await click(card._replyEntry);
        check(card._replyEntry.contains(global.stage.key_focus), 'Native reply entry did not receive keyboard focus');
        for (const character of text) {
            keyboard.notify_keyval(GLib.get_monotonic_time(), character.codePointAt(0), Clutter.KeyState.PRESSED);
            keyboard.notify_keyval(GLib.get_monotonic_time(), character.codePointAt(0), Clutter.KeyState.RELEASED);
            await sleep(15);
        }
        check(card._replyEntry.get_text() === text, 'Virtual keyboard text did not reach the native editor');
        report.typed_text = text;
        await capture('before-send');
        await click(card._replyButton);
        await sleep(500);
        report.status = 'native-input-submitted-pending-android-receipt';
        // The original Android PendingIntent may withdraw the card before the
        // host acknowledgement. Only the APK receipt establishes delivery.
        await capture('after-send'); save();
    } catch (error) {
        report.status = 'failed-or-inconclusive'; report.error = error.message; save(); throw error;
    } finally { tray.close(); }
}
