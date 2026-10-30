// SPDX-License-Identifier: GPL-2.0-or-later
// Run inside the installed Shell of a disposable graphical VM. The producer
// must be the real source-owned Android fixture, not a synthetic Shell source.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';
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
    const runtimePrefix = `${GLib.get_user_runtime_dir()}/luma-android-reply-ui-`;
    const runtimeOutput = typeof out === 'string' && out.startsWith(runtimePrefix) &&
        /^[A-Za-z0-9_-]+$/.test(out.slice(runtimePrefix.length));
    check(runtimeOutput || /^\/var\/tmp\/luma-android-reply-ui-[A-Za-z0-9_-]+$/.test(out ?? ''),
        'Private test output required');
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
        const closed = new Set([MessageTray.NotificationLifecycleState.ACTED,
            MessageTray.NotificationLifecycleState.DISMISSED, MessageTray.NotificationLifecycleState.EXPIRED]);
        const fixtures = () => Main.messageTray.getSources().flatMap(source => source.notifications ?? [])
            .filter(item => item.source?.app?.get_id()?.replace(/\.desktop$/, '') === appId &&
                item.title === 'Android integration fixture' && !closed.has(item.lifecycleState));
        report.status = 'waiting-for-prior-fixture-notification-removal'; save();
        let clearSamples = 0;
        for (let attempt = 0; attempt < 100 && clearSamples < 2; attempt++) {
            clearSamples = fixtures().length === 0 ? clearSamples + 1 : 0;
            await sleep(100);
        }
        check(clearSamples >= 2, 'Previous fixture notification did not withdraw after app force-stop');
        report.prior_fixture_withdrawal_observed = true;
        report.status = 'waiting-for-real-android-notification'; save();
        let notification;
        for (let attempt = 0; attempt < 2400 && !notification; attempt++) {
            notification = fixtures()[0];
            if (!notification) await sleep(200);
        }
        check(notification, 'Actual Android fixture notification did not arrive');
        check(notification.inlineReply?.send, 'Installed notification has no negotiated native reply');
        report.surface = 'live-notification-card';
        report.notification_policy = {dnd: tray.dnd,
            show_banners: notification.source.policy.showBanners};
        check(!tray.isOpen && !tray.dnd && notification.source.policy.showBanners,
            'Live notification presentation is unavailable under the current policy');
        // History intentionally hides inline editors. Observe the real daemon
        // presentation instead of replacing its live card with the history UI.
        await sleep(500);
        const generation = notification.inlineReply;
        function controls() {
            check(notification.inlineReply === generation,
                'Notification reply generation changed during input');
            check(tray._current === notification && tray.state === 'card',
                'The actual Luma notification owner no longer presents this notification');
            // Return only values: never keep a native actor across an await.
            // A replacement can destroy its editor while this probe yields.
            const card = findCard(tray, notification);
            check(card && tray._card === card && tray.contains(card),
                'Actual native reply controls are not mapped under their Luma owner');
            return {entry: bounds(card._replyEntry), button: bounds(card._replyButton),
                focused: card._replyEntry.contains(global.stage.key_focus),
                text: card._replyEntry.get_text()};
        }
        const initial = controls();
        report.presentation_state = tray.state;
        report.entry_bounds = initial.entry;
        report.button_bounds = initial.button;
        const seat = global.stage.context.get_backend().get_default_seat();
        pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
        keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        async function click(kind) {
            let box = controls()[kind];
            pointer.notify_absolute_motion(GLib.get_monotonic_time(), box.x + box.width / 2, box.y + box.height / 2);
            await sleep(50);
            box = controls()[kind];
            pointer.notify_absolute_motion(GLib.get_monotonic_time(), box.x + box.width / 2, box.y + box.height / 2);
            pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
            await sleep(40);
            pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
            await sleep(100);
        }
        await click('entry');
        check(controls().focused, 'Native reply entry did not receive keyboard focus');
        for (const character of text) {
            keyboard.notify_keyval(GLib.get_monotonic_time(), character.codePointAt(0), Clutter.KeyState.PRESSED);
            keyboard.notify_keyval(GLib.get_monotonic_time(), character.codePointAt(0), Clutter.KeyState.RELEASED);
            await sleep(15);
        }
        // Virtual devices enqueue input; a timer can run before the next stage
        // event dispatch. Require the editor's actual value, within a deadline.
        for (let attempt = 0; attempt < 100 && controls().text !== text; attempt++)
            await sleep(20);
        report.typed_text = controls().text;
        check(report.typed_text === text, 'Virtual keyboard text did not reach the native editor');
        await capture('before-send');
        check(controls().text === text, 'Native editor changed before submission');
        await click('button');
        await sleep(500);
        report.status = 'native-input-submitted-pending-android-receipt';
        // The original Android PendingIntent may withdraw the card before the
        // host acknowledgement. Only the APK receipt establishes delivery.
        await capture('after-send');
    } catch (error) {
        report.status = 'failed-or-inconclusive'; report.error = error.message;
        await capture('failed-state'); save(); throw error;
    } finally {
        // Dispose while JavaScript is running, rather than during GC.
        try {
            keyboard?.run_dispose();
            pointer?.run_dispose();
            report.virtual_devices_disposed = true;
        } catch (error) {
            report.status = 'failed-or-inconclusive';
            report.error = `Virtual input cleanup failed: ${error.message}`;
            throw error;
        } finally { save(); }
    }
}
