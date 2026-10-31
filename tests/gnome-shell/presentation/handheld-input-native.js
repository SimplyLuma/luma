// SPDX-License-Identifier: Apache-2.0
// Handheld input on the installed Shell: fast typing through the on-screen
// keyboard with a deliberately slow engine and suggestions, an
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

    // Start without words learned by an earlier run.
    GLib.unlink(GLib.build_filenamev([GLib.get_user_data_dir(), 'luma', 'keycap-words.json']));
    const typed = `${out}/typed.txt`;
    const context = global.create_app_launch_context(0, -1);
    Gio.AppInfo.create_from_commandline(
        `python3 ${source}/tests/gnome-shell/presentation/typing-window.py ${typed}`,
        'Typing fixture', Gio.AppInfoCreateFlags.NONE).launch([], context);
    const app = () => global.display.focus_window?.get_wm_class()?.includes('TypingFixture')
        ? global.display.focus_window : null;
    // Wait for the client to take the full-screen size: the keyboard closes
    // when its window moves, as upstream does.
    await until(() => app() && frame(app()) === full);
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

    // Fast typing with a slow engine: every input-method call and every
    // suggestion takes 500 ms. A tap must still reach the application at once
    // (within the frame it happened in), in order, at 0-2 ms per key.
    const delay = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
        resolve();
        return GLib.SOURCE_REMOVE;
    }));
    const handleVirtualKey = Main.inputMethod.handleVirtualKey;
    Main.inputMethod.handleVirtualKey = async keyval => {
        await delay(500);
        return handleVirtualKey.call(Main.inputMethod, keyval);
    };
    const suggester = keyboard._suggester;
    const analyse = suggester?.analyse;
    if (suggester) {
        suggester.analyse = async word => {
            await delay(500);
            return analyse.call(suggester, word);
        };
    }
    const commits = [];
    const commit = Main.inputMethod.commit;
    Main.inputMethod.commit = text => {
        commits.push({text, at: GLib.get_monotonic_time()});
        return commit.call(Main.inputMethod, text);
    };
    const text = 'the quick brown fox, jumps over the lazy dog. and keeps typing fast; very fast ';
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
    // Each tap's character must reach the application (one text-input
    // commit per Wayland "done") within a frame of the tap.
    const taps = [];
    for (const [index, character] of [...text].entries()) {
        taps.push(GLib.get_monotonic_time());
        letter?.emit('commit', character);
        await sleep(index % 3);  // 0-2 ms per key
    }
    await until(() => commits.map(entry => entry.text).join('').length >= text.length, 3000);
    const latencies = [];
    let covered = 0;
    for (const entry of commits) {
        for (let i = 0; i < [...entry.text].length && covered < taps.length; i++, covered++)
            latencies.push((entry.at - taps[covered]) / 1000);
    }
    const committed = commits.map(entry => entry.text).join('');
    const worst = Math.max(...latencies);
    const mean = latencies.reduce((sum, value) => sum + value, 0) / Math.max(1, latencies.length);
    check('taps are committed in order', committed === text, JSON.stringify(committed));
    check('every tap reaches the application within one frame despite a 500 ms engine', worst < 16,
        `key-to-commit mean ${mean.toFixed(2)} ms, worst ${worst.toFixed(2)} ms over ${latencies.length} keys in ${commits.length} commits`);
    keyboard._latched = false;
    await sleep(2000);
    let received = '';
    try {
        received = new TextDecoder().decode(GLib.file_get_contents(typed)[1]);
    } catch {}
    check('fast typing arrives complete and in order', received === text, JSON.stringify(received));

    // Suggestions arrive afterwards, off the key path, for the latest word.
    for (const character of 'helo')
        letter?.emit('commit', character);
    await sleep(2500);
    const offered = keyboard._suggestions.get_children().map(button => button.label);
    check('suggestions follow a slow engine for the latest word', offered.some(label => /^hello$/i.test(label)),
        JSON.stringify(offered));
    keyboard._applySuggestion('helo', 'hello');
    await sleep(1500);
    try {
        received = new TextDecoder().decode(GLib.file_get_contents(typed)[1]);
    } catch {}
    check('a chosen correction replaces only its word', received === `${text}hello `, JSON.stringify(received));
    Main.inputMethod.handleVirtualKey = handleVirtualKey;
    Main.inputMethod.commit = commit;
    if (suggester)
        suggester.analyse = analyse;

    // Autocorrect at the word boundary, undone by backspace and remembered.
    const read = path => {
        try {
            return new TextDecoder().decode(GLib.file_get_contents(path)[1]);
        } catch {
            return '';
        }
    };
    const typeSlowly = async word => {
        for (const character of word) {
            letter?.emit('commit', character);
            await sleep(60);
        }
    };
    const deleteKey = [];
    const findDelete = actor => {
        for (const child of actor.get_children()) {
            if (child.keyButton && child._iconName === 'osk-delete-symbolic' && child.mapped)
                deleteKey.push(child);
            findDelete(child);
        }
    };
    findDelete(keyboard._currentPage);
    const start = read(typed);
    await typeSlowly('teh');
    await sleep(1500);
    letter?.emit('commit', ' ');
    await sleep(1000);
    check('autocorrect applies at the word boundary', read(typed) === `${start}the `, JSON.stringify(read(typed)));
    deleteKey[0]?.emit('released');
    await sleep(1000);
    check('backspace right after restores the typed word', read(typed) === `${start}teh`, JSON.stringify(read(typed)));
    letter?.emit('commit', ' ');
    await typeSlowly('teh');
    await sleep(1500);
    letter?.emit('commit', ' ');
    await sleep(1000);
    check('a restored word is not corrected again', read(typed) === `${start}teh teh `, JSON.stringify(read(typed)));

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

    // Nothing is corrected, suggested or learned in a password field.
    const secret = `${out}/secret.txt`;
    Gio.AppInfo.create_from_commandline(
        `python3 ${source}/tests/gnome-shell/presentation/typing-window.py ${secret} password`,
        'Password fixture', Gio.AppInfoCreateFlags.NONE).launch([], context);
    await until(() => global.display.focus_window?.get_wm_class()?.includes('TypingFixturePassword'));
    await sleep(1500);
    Main.keyboard.open(Main.layoutManager.primaryIndex);
    await sleep(1500);
    const passwordKeyboard = Main.keyboard.keyboardActor;
    let key = null;
    const findKey = actor => {
        for (const child of actor.get_children()) {
            if (!key && child.keyButton && !child._keyval && !child._hasAction && child.mapped)
                key = child;
            findKey(child);
        }
    };
    findKey(passwordKeyboard._currentPage);
    for (const character of 'teh') {
        key?.emit('commit', character);
        await sleep(60);
    }
    await sleep(1500);
    key?.emit('commit', ' ');
    await sleep(1000);
    check('a password field is never corrected', read(secret) === 'teh ', JSON.stringify(read(secret)));
    check('a password field gets no suggestions', passwordKeyboard._suggestions.get_n_children() === 0);
    global.display.focus_window?.delete(global.get_current_time());
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
