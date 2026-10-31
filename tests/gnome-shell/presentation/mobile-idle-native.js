// SPDX-License-Identifier: Apache-2.0
// Mobile idle, status pull and lock-screen probe for the installed Shell.
// Run through run.sh: PRESENTATION_AUTOMATION=<this file> PRESENTATION_MONITOR=360x808.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const results = [];
const out = GLib.getenv('PRESENTATION_OUT');
const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT,
    ms, () => { resolve(); return GLib.SOURCE_REMOVE; }));
function check(name, condition, detail = '') {
    results.push({name, ok: Boolean(condition), detail});
    console.log(`[mobile-idle] ${condition ? 'PASS' : 'FAIL'} ${name} ${detail}`);
}
function describe(actor) {
    const chain = [];
    for (let a = actor; a && chain.length < 5; a = a.get_parent())
        chain.push(`${a.constructor.name}${a.style_class ? `.${a.style_class.split(' ')[0]}` : ''}`);
    return chain.join(' < ');
}
// Frames painted while nothing should change.
async function idleFrames(ms = 3000) {
    let frames = 0;
    const id = global.stage.connect('after-paint', () => frames++);
    await sleep(ms);
    global.stage.disconnect(id);
    return frames;
}

async function work() {
    const presentation = Main.presentation;
    const settings = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});
    settings.set_string('presentation-mode', 'mobile');
    for (let i = 0; i < 40 && presentation?.mode !== 'mobile'; i++)
        await sleep(250);
    check('Mobile presentation', presentation?.mode === 'mobile');
    await sleep(2000);
    check('Home is idle', (await idleFrames()) <= 2, 'frames in 3 s');

    const monitor = Main.layoutManager.primaryMonitor;
    const statusPoint = (x = monitor.x + monitor.width - 24) => {
        const picked = global.stage.get_actor_at_pos(Clutter.PickMode.REACTIVE, x, monitor.y + 20);
        return {picked, owned: presentation._statusRight.contains(picked) || picked === presentation._statusRight};
    };
    let point = statusPoint();
    check('right status area receives touches', point.owned, describe(point.picked));

    Main.notify('Probe', 'A notification for the island');
    await sleep(1500);
    point = statusPoint();
    check('right status area still receives touches with a notification', point.owned, describe(point.picked));
    for (const x of [monitor.width * 0.6, monitor.width * 0.75, monitor.width - 60]) {
        const p = statusPoint(monitor.x + x);
        console.log(`[mobile-idle] pick ${Math.round(x)}: ${describe(p.picked)}`);
    }
    check('Home with a notification settles', (await idleFrames()) <= 2, 'frames in 3 s');

    // A finger pulled down from the status icons, as on a phone.
    const quick = Main.panel.statusArea.quickSettings;
    const seat = Clutter.get_default_backend().get_default_seat();
    const finger = seat.create_virtual_device(Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);
    const now = () => GLib.get_monotonic_time();
    const pull = async (fromX, name) => {
        finger.notify_touch_down(now(), 0, fromX, monitor.y + 20);
        await sleep(32);
        for (let step = 1; step <= 12; step++) {
            finger.notify_touch_motion(now(), 0, fromX, monitor.y + 20 + step * 40);
            await sleep(16);
        }
        finger.notify_touch_up(now(), 0);
        await sleep(1200);
        check(`Quick Options opens from a finger pull ${name}`, quick.menu.isOpen,
            describe(global.stage.get_actor_at_pos(Clutter.PickMode.REACTIVE, fromX, monitor.y + 20)));
        quick.menu.close();
        await sleep(800);
    };
    const x = monitor.x + monitor.width - 24, y = monitor.y + 20;
    await pull(x, 'on the status icons');
    // The corner beyond the icons is status inset (a display's rounded corner
    // widens it); a pull from there is still a pull from the status area.
    await pull(monitor.x + monitor.width - 3, 'from the screen corner');
    finger.notify_touch_down(now(), 0, x, y);
    await sleep(60);
    finger.notify_touch_up(now(), 0);
    await sleep(1200);
    check('Quick Options opens from a tap on the status icons', quick.menu.isOpen);
    quick.menu.close();
    await sleep(800);
    finger.run_dispose();

    // Nothing may poll the main loop while the phone sits idle.
    const ticks = () => {
        const [, stat] = GLib.file_get_contents('/proc/self/stat');
        const fields = new TextDecoder().decode(stat).split(') ')[1].split(' ');
        return Number(fields[11]) + Number(fields[12]);
    };
    // A Shelf that never presented (a session that starts on a phone) must not
    // keep waiting for its own allocation while Mobile hides it.
    if (Main.shelf) {
        Main.shelf._initialLayoutReady = false;
        Main.shelf._queueInitialPresentation();
        await sleep(300);
    }
    check('Shelf presentation wait is not polling in Mobile', !Main.shelf?._initialPresentationId);
    const before = ticks();
    await sleep(5000);
    check('idle Shell uses no CPU', ticks() - before <= 10, `${ticks() - before} ticks in 5 s`);

    const a11y = new Gio.Settings({schema_id: 'org.gnome.desktop.a11y.applications'});
    a11y.set_boolean('screen-keyboard-enabled', true);
    await sleep(500);
    Main.keyboard.open(Main.layoutManager.primaryIndex);
    await sleep(1500);
    check('open keyboard is idle', (await idleFrames()) <= 2, 'frames in 3 s');
    Main.keyboard.close();
    a11y.reset('screen-keyboard-enabled');
    await sleep(800);

    // Headless sessions without GDM have no screen shield.
    if (!Main.screenShield)
        return;
    Main.screenShield.lock(false);
    await sleep(3000);
    check('locked', Main.sessionMode.isLocked);
    check('lock screen is idle', (await idleFrames()) <= 4, 'frames in 3 s');
    Main.screenShield._dialog?._showPrompt?.();
    await sleep(2000);
    check('unlock prompt is idle', (await idleFrames()) <= 4, 'frames in 3 s (clock and cursor excluded)');
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
