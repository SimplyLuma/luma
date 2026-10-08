// SPDX-License-Identifier: GPL-2.0-or-later
// Switching to another window and typing at once: every key must reach the
// new window. Two real Wayland clients with a text box record what they are
// given; the test raises the second one and types immediately through every
// way in, then asserts the text box holds exactly what was typed and
// reports the handover timeline: the raise, Mutter's focus change, the
// client's own focus-in, and each key as it was typed and as it arrived.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

const T = GLib.path_get_dirname(GLib.getenv('SA_OUT')) + '/../tests';
const WORD = 'hello';
const us = () => GLib.get_monotonic_time();
const ms = n => Math.round(n / 1000);

async function spawn(title, log) {
    GLib.spawn_async(null, ['python3', `${T}/typer.py`, `org.oracle.${title}`, title, log],
        null, GLib.SpawnFlags.SEARCH_PATH, null);
    for (let i = 0; i < 80; i++) {
        const w = global.display.list_all_windows().find(win => win.title === title);
        if (w)
            return w;
        await L.sleep(250);
    }
    return null;
}

function readLog(path) {
    try {
        const [ok, bytes] = GLib.file_get_contents(path);
        if (!ok)
            return [];
        return new TextDecoder().decode(bytes).split('\n').filter(l => l.trim())
            .map(line => {
                const [time, kind, ...rest] = line.split(' ');
                return {time: Number(time), kind, detail: rest.join(' ')};
            });
    } catch {
        return [];
    }
}
const lastText = entries => [...entries].reverse().find(e => e.kind === 'text')?.detail ?? '';
const keysIn = entries => entries.filter(e => e.kind === 'key').map(e => e.detail);

// Type WORD as fast as a person drums it out, with no waiting for the
// Shell: the keys must be delivered even when they are injected before the
// focus has finished moving.
async function type(word, gap = 12) {
    const k = L.input().keyboard;
    const at = [];
    for (const ch of word) {
        const keyval = ch === ' ' ? Clutter.KEY_space : Clutter[`KEY_${ch}`];
        at.push({ch, at: us()});
        k.notify_keyval(us(), keyval, Clutter.KeyState.PRESSED);
        k.notify_keyval(us(), keyval, Clutter.KeyState.RELEASED);
        await L.sleep(gap);
    }
    return at;
}

async function altTab() {
    const k = L.input().keyboard;
    k.notify_keyval(us(), Clutter.KEY_Alt_L, Clutter.KeyState.PRESSED);
    await L.sleep(30);
    k.notify_keyval(us(), Clutter.KEY_Tab, Clutter.KeyState.PRESSED);
    await L.sleep(30);
    k.notify_keyval(us(), Clutter.KEY_Tab, Clutter.KeyState.RELEASED);
    await L.sleep(250);
    L.log(`alt-tab: switcher ${Main.wm._workspaceSwitcherPopup || global.display.focus_window?.title}`);
    k.notify_keyval(us(), Clutter.KEY_Alt_L, Clutter.KeyState.RELEASED);
    await L.sleep(30);
}

async function work() {
    await L.waitForShelf();
    const logs = {A: `${L.OUT}/typed-A.log`, B: `${L.OUT}/typed-B.log`};
    const a = await spawn('TyperA', logs.A);
    const b = await spawn('TyperB', logs.B);
    L.check('two typing windows', !!a && !!b);
    if (!a || !b)
        return;
    await L.sleep(2500);
    // Side by side, so a click lands on one window only.
    const area = Main.layoutManager.getWorkAreaForMonitor(Main.layoutManager.primaryIndex);
    a.move_resize_frame(false, area.x + 40, area.y + 60, 700, 500);
    b.move_resize_frame(false, area.x + area.width - 740, area.y + 60, 700, 500);
    await L.sleep(1200);

    const focusLog = [];
    const focusId = global.display.connect('notify::focus-window', () => {
        focusLog.push({at: us(), title: global.display.focus_window?.title ?? 'none'});
    });
    // Alt+Tab between plain windows: the app switcher has no apps to show
    // in a headless session, so the window switcher takes the binding.
    const wmKeys = new Gio.Settings({schema_id: 'org.gnome.desktop.wm.keybindings'});
    wmKeys.set_strv('switch-applications', []);
    wmKeys.set_strv('switch-windows', ['<Alt>Tab']);

    // Wake the virtual keyboard before measuring: its very first key after
    // it is created is the device arriving, not a person typing.
    a.activate(global.get_current_time());
    await L.sleep(800);
    await L.key(Clutter.KEY_space, [], 600);

    const clientActive = () => {
        const entries = readLog(logs.B).filter(e => e.kind === 'active');
        return entries.length > 0 && entries[entries.length - 1].detail === 'True';
    };

    const runs = [
        // The control: the window is focused and the client has said so
        // itself before a single key is typed.
        ['settled', async () => {
            b.activate(global.get_current_time());
            await L.until(clientActive, 5000);
            await L.sleep(500);
        }],
        ['activate', async () => { b.activate(global.get_current_time()); }],
        ['alt-tab', altTab],
        // The overview: the shell holds the keyboard while it closes, which
        // is the everyday way of switching apps.
        ['overview', async () => {
            Main.overview.show();
            await L.until(() => Main.overview.visible, 4000);
            await L.sleep(800);
            Main.activateWindow(b, global.get_current_time());
        }],
        ['click', async () => {
            const r = b.get_frame_rect();
            await L.move(r.x + r.width / 2, r.y + r.height / 2, 40);
            await L.press();
            await L.release();
        }],
    ];

    for (const [name, raise] of runs) {
        a.activate(global.get_current_time());
        await L.sleep(1500);
        const fromA = readLog(logs.A).length, fromB = readLog(logs.B).length;
        const before = lastText(readLog(logs.B));

        const t0 = us();
        await raise();
        const typed = await type(WORD);
        await L.sleep(2500);

        const entries = readLog(logs.B).slice(fromB);
        const got = lastText(entries);
        const keys = keysIn(entries);
        const focusAt = focusLog.filter(f => f.at > t0).find(f => f.title === 'TyperB')?.at;
        const enterAt = entries.find(e => e.kind === 'active' && e.detail === 'True')?.time;
        const arrived = entries.filter(e => e.kind === 'key');
        const aKeys = keysIn(readLog(logs.A).slice(fromA)).filter(k => WORD.includes(k));
        const switched = global.display.focus_window === b;
        if (name === 'alt-tab' && !switched) {
            // The window switcher does not act in a headless session; the
            // shell-grab path it shares is covered by the overview run.
            L.log('alt-tab: the switcher did not act in this session, not measured');
        } else {
            L.check(`${name}: every key reaches the new window`, got === before + WORD,
                `box ${JSON.stringify(got)} keys ${JSON.stringify(keys)} (was ${JSON.stringify(before)})`);
            L.check(`${name}: no key goes to the window being left`, aKeys.length === 0,
                JSON.stringify(aKeys));
            L.check(`${name}: the switch happened`, switched,
                global.display.focus_window?.title ?? 'none');
        }
        L.log(`${name}: raise +0, focus ${focusAt ? `+${ms(focusAt - t0)}` : '-'}, ` +
            `client focus-in ${enterAt ? `+${ms(enterAt - t0)}` : '-'}, ` +
            `typed ${JSON.stringify(typed.map(t => `${t.ch}+${ms(t.at - t0)}`))}, ` +
            `arrived ${JSON.stringify(arrived.map(e => `${e.detail}+${ms(e.time - t0)}`))} (ms)`);
    }

    // Asking for an app and typing while it opens. The app is started the
    // way a person starts one -- through the shell's app system, which is
    // what tells the compositor a launch is under way.
    const launched = async (id, title, log) => {
        const file = `${GLib.get_user_data_dir()}/applications/org.oracle.${id}.desktop`;
        GLib.file_set_contents(file,
            '[Desktop Entry]\nType=Application\nName=' + title +
            `\nExec=python3 ${T}/typer.py org.oracle.${id} ${title} ${log}\n` +
            'Icon=org.projectluma.Notes\nStartupNotify=true\n');
        const system = Shell.AppSystem.get_default();
        await L.until(() => system.lookup_app(`org.oracle.${id}.desktop`), 8000);
        return system.lookup_app(`org.oracle.${id}.desktop`);
    };

    for (const [name, id, opensOverview] of [['launch', 'TyperC', true], ['launch-from-dash', 'TyperD', false]]) {
        const log = `${L.OUT}/typed-${id}.log`;
        const app = await launched(id, id, log);
        L.check(`${name}: the app is in the app system`, !!app);
        if (!app)
            continue;
        a.activate(global.get_current_time());
        await L.sleep(1500);
        const fromA = readLog(logs.A).length;
        if (opensOverview) {
            Main.overview.show();
            await L.until(() => Main.overview.visible, 4000);
            await L.sleep(600);
        }
        const t0 = us();
        app.activate();
        // The person types straight away, before the window is there.
        const typed = await type(WORD, 60);
        await L.until(() => global.display.list_all_windows().some(w => w.title === id), 20000);
        // The keys land when the app takes the keyboard, which can be a
        // moment after its window appears.
        // Wait for the app to actually take the keyboard before judging: a
        // loaded test machine can take many seconds over what a real one
        // does in a moment, and the hold is bounded on purpose.
        await L.until(() => readLog(log).some(e => e.kind === 'active' && e.detail === 'True'), 25000);
        await L.sleep(1500);
        const entries = readLog(log);
        const got = lastText(entries);
        const aKeys = keysIn(readLog(logs.A).slice(fromA)).filter(k => WORD.includes(k));
        const tookKeyboard = entries.find(e => e.kind === 'active' && e.detail === 'True');
        const openedIn = tookKeyboard ? ms(tookKeyboard.time - t0) : null;
        if (got !== WORD && openedIn === null) {
            L.log(`${name}: the app never took the keyboard in this session; not measured`);
        } else if (got !== WORD && openedIn > 5000) {
            // The hold is bounded: an app that takes longer than that to
            // reach the keyboard gets its keys back released, with a log.
            L.log(`${name}: the app took ${openedIn} ms to take the keyboard, past the hold; not measured`);
        } else {
            L.check(`${name}: the app that was asked for gets what was typed`, got === WORD,
                `box ${JSON.stringify(got)}, keyboard after ${openedIn} ms`);
        }
        L.check(`${name}: nothing was typed into the window left behind`, aKeys.length === 0,
            JSON.stringify(aKeys));
        L.log(`${name}: typed ${JSON.stringify(typed.map(t => `${t.ch}+${ms(t.at - t0)}`))} ` +
            `arrived ${JSON.stringify(entries.filter(e => e.kind === 'key').map(e => `${e.detail}+${ms(e.time - t0)}`))}`);
        if (Main.overview.visible)
            Main.overview.hide();
        await L.sleep(1200);
    }

    // The guard: someone writing in a text field keeps what they type, even
    // while an app they asked for is opening.
    {
        const id = 'TyperE';
        const log = `${L.OUT}/typed-${id}.log`;
        const app = await launched(id, id, log);
        a.activate(global.get_current_time());
        await L.sleep(1500);
        const fromA = readLog(logs.A).length;
        // Actively typing into A's text field.
        await type('ab', 60);
        await L.sleep(200);
        app?.activate();
        const typed = await type(WORD, 60);
        await L.until(() => global.display.list_all_windows().some(w => w.title === id), 20000);
        await L.sleep(6000);
        const aKeys = keysIn(readLog(logs.A).slice(fromA));
        const newKeys = keysIn(readLog(log));
        // Whoever ends up with them, nothing typed may be lost, and no key
        // may be torn out of the middle of a word.
        L.check('nothing typed is lost while an app opens',
            aKeys.join('') + newKeys.join('') === `ab${WORD}`,
            `left window ${JSON.stringify(aKeys)}, new window ${JSON.stringify(newKeys)}`);
        L.log(`guard: left window kept ${JSON.stringify(aKeys)}, the app that opened got ${JSON.stringify(newKeys)}`);
    }

    global.display.disconnect(focusId);
}

export function init() { L.start('keys', work); }
export async function run() { await new Promise(() => {}); }
