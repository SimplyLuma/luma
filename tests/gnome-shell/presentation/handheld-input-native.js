// SPDX-License-Identifier: Apache-2.0
// Handheld input on the installed Shell: fast typing through the on-screen
// keyboard and the input method (word completion when it is available), an
// application that stays full screen under the keyboard and a top-edge pull,
// and the home gesture above the keyboard. Run through run.sh:
// PRESENTATION_AUTOMATION=<this file> PRESENTATION_INITIAL_MODE=mobile
// PRESENTATION_MONITOR=360x808.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const results = [];
const out = GLib.getenv('PRESENTATION_OUT');
const source = GLib.getenv('PRESENTATION_SOURCE');
const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT,
    ms, () => { resolve(); return GLib.SOURCE_REMOVE; }));
function check(name, condition, detail = '') {
    results.push({name, ok: Boolean(condition), detail});
    console.log(`[handheld-input] ${condition ? 'PASS' : 'FAIL'} ${name} ${detail}`);
}
async function until(test, ms = 10000) {
    for (let waited = 0; waited < ms && !test(); waited += 100)
        await sleep(100);
    return test();
}
const frame = window => {
    const rect = window.get_frame_rect();
    return `${rect.width}x${rect.height}+${rect.x}+${rect.y}${window.is_fullscreen() ? ' fullscreen' : ''}`;
};

async function work() {
    const presentation = Main.presentation;
    const settings = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});
    settings.set_string('presentation-mode', 'mobile');
    await until(() => presentation?.mode === 'mobile');
    check('Mobile presentation', presentation?.mode === 'mobile');
    const monitor = Main.layoutManager.primaryMonitor;
    const full = `${monitor.width}x${monitor.height}+${monitor.x}+${monitor.y} fullscreen`;

    const typed = `${out}/typed.txt`;
    const context = global.create_app_launch_context(0, -1);
    Gio.AppInfo.create_from_commandline(
        `python3 ${source}/tests/gnome-shell/presentation/typing-window.py ${typed}`,
        'Typing fixture', Gio.AppInfoCreateFlags.NONE).launch([], context);
    const app = () => global.display.focus_window?.get_wm_class()?.includes('TypingFixture')
        ? global.display.focus_window : null;
    await until(() => app() && app().is_fullscreen());
    const window = app();
    check('application opens full screen', window && frame(window) === full, window ? frame(window) : 'no window');
    if (!window)
        return;

    const a11y = new Gio.Settings({schema_id: 'org.gnome.desktop.a11y.applications'});
    a11y.set_boolean('screen-keyboard-enabled', true);
    await until(() => Main.keyboard.keyboardActor);
    Main.keyboard.open(Main.layoutManager.primaryIndex);
    await until(() => Main.keyboard.visible);
    await sleep(1500);
    const keyboard = Main.keyboard.keyboardActor;
    check('keyboard opens over the application', Main.keyboard.visible, `${keyboard.width}x${keyboard.height}`);
    check('application stays full screen under the keyboard', frame(window) === full, frame(window));

    // The home gesture stays reachable above the keyboard.
    const picked = global.stage.get_actor_at_pos(Clutter.PickMode.REACTIVE,
        monitor.x + monitor.width / 2, monitor.y + monitor.height - 4);
    check('home gesture is above the keyboard', presentation._gestureBar.contains(picked), `${picked}`);

    // Fast typing: a key every few milliseconds, through the tap's commit path.
    const completion = Boolean(keyboard._keyboardController._oskCompletionEnabled);
    const text = 'the quick brown fox, jumps over the lazy dog. and keeps typing fast; very fast ';
    const interval = Number(GLib.getenv('KEYCAP_TYPING_INTERVAL') ?? 20);
    // Any character key commits what it is given; its handler is the tap path.
    let letter = null;
    const find = actor => {
        for (const child of actor.get_children()) {
            if (!letter && child.keyButton && !child._keyval && !child._hasAction && child.mapped)
                letter = child;
            find(child);
        }
    };
    keyboard._setActiveLevel('default');
    keyboard._latched = true;  // Typed text stays lower case; sentence case has its own check.
    find(keyboard._currentPage);
    check('a character key is available', !!letter);
    for (const character of text) {
        letter?.emit('commit', character);
        await sleep(interval);
    }
    keyboard._latched = false;
    await sleep(3000);
    let received = '';
    try {
        received = new TextDecoder().decode(GLib.file_get_contents(typed)[1]);
    } catch {}
    check(`fast typing arrives complete and in order${completion ? ' through word completion' : ''}`,
        received.trim() === text.trim(), JSON.stringify(received));
    check('word completion engine took part', completion || !GLib.getenv('KEYCAP_REQUIRE_COMPLETION'));

    // A pull from the top edge belongs to the Shell, never to the window.
    const seat = Clutter.get_default_backend().get_default_seat();
    const finger = seat.create_virtual_device(Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);
    const now = () => GLib.get_monotonic_time();
    for (const startY of [2, 30, 60]) {
        finger.notify_touch_down(now(), 0, monitor.x + monitor.width / 2, monitor.y + startY);
        await sleep(32);
        for (let step = 1; step <= 12; step++) {
            finger.notify_touch_motion(now(), 0, monitor.x + monitor.width / 2, monitor.y + startY + step * 40);
            await sleep(16);
        }
        finger.notify_touch_up(now(), 0);
        await sleep(1200);
        Main.panel.statusArea.quickSettings.menu.close();
        Main.notificationTray?.close?.();
        await sleep(600);
        check(`application stays full screen after a pull from y=${startY}`, frame(window) === full, frame(window));
    }
    finger.run_dispose();
    Main.keyboard.close();
    await sleep(1200);
    check('application stays full screen after the keyboard closes', frame(window) === full, frame(window));
    // Windows that leave full screen while handheld come back.
    window.unmake_fullscreen();
    await sleep(1500);
    check('Mobile keeps its application full screen', frame(window) === full, frame(window));
    window.delete(global.get_current_time());
    a11y.reset('screen-keyboard-enabled');
}

export function init() {
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 6000, () => {
        work().catch(error => check('scenario completed', false, `${error}\n${error.stack}`)).finally(() => {
            GLib.file_set_contents(`${out}/results.json`, JSON.stringify(results, null, 2));
            global.context.terminate();
        });
        return GLib.SOURCE_REMOVE;
    });
}
export async function run() { await new Promise(() => {}); }
