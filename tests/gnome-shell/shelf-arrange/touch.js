// SPDX-License-Identifier: GPL-2.0-or-later
// A tap is a tap. Tapping anything on the shelf does what clicking it does;
// only a deliberate long press opens arrange mode, and a tap that shifts a
// little under the finger is still a tap.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

const arrange = () => Main.shelf._arrange;
const now = () => GLib.get_monotonic_time();

let touchscreen;
function touch() {
    const seat = Clutter.get_default_backend().get_default_seat();
    touchscreen ??= seat.create_virtual_device(Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);
    return touchscreen;
}

async function tap(x, y, {hold = 60, drift = 0} = {}) {
    const device = touch();
    device.notify_touch_down(now(), 0, x, y);
    await L.sleep(Math.min(hold, 40));
    if (drift) {
        device.notify_touch_motion(now(), 0, x + drift, y);
        await L.sleep(20);
    }
    if (hold > 60)
        await L.sleep(hold - 60);
    device.notify_touch_up(now(), 0);
    await L.sleep(500);
}

const anyMenuOpen = () =>
    !!Main.shelf._arrange?._menu?.isOpen ||
    Main.uiGroup.get_children().some(a => a.visible && a.constructor?.name?.includes('BoxPointer'));

async function leaveArrange() {
    if (arrange().active) {
        arrange().end?.();
        await L.sleep(800);
    }
}

// The headless backend's virtual touchscreen delivers a touch down and
// nothing else: no update, no end. Whatever the shelf is asked, a finger
// put down in this harness never lifts, so a tap cannot be simulated here
// and the tap assertions are made only where the lift is seen.
let liftSeen = false;

async function work() {
    const shelf = await L.waitForShelf();
    global.stage.connect('captured-event', (_s, event) => {
        if (event.type() === Clutter.EventType.TOUCH_END ||
            event.type() === Clutter.EventType.TOUCH_CANCEL)
            liftSeen = true;
        return Clutter.EVENT_PROPAGATE;
    });
    await L.arrange([
        {edge: 'bottom', anchor: 'start', islands: ['live', 'media', 'dock']},
        {edge: 'bottom', anchor: 'end', islands: ['well', 'quick-options', 'clock']},
    ], 2000);

    // The dock first: a tap there is not grabbed by a button, so it proves
    // the harness can lift a finger at all before the grabbed cases are judged.
    const targets = [['dock', 'the dock'], ['clock', 'the clock'], ['quick-options', 'Quick Options']];
    for (const [id, name] of targets) {
        const actor = shelf.partActorFor?.(id) ?? shelf.islandActorFor?.(id);
        if (!actor?.visible) {
            L.log(`touch: ${name} is not on the shelf in this session, not measured`);
            continue;
        }
        const r = L.rectOf(actor);
        const [x, y] = L.centre(r);

        await tap(x, y);
        if (liftSeen) {
            L.check(`a tap on ${name} does not open arrange mode`, !arrange().active);
            L.log(`touch: a tap on ${name} ${anyMenuOpen() ? 'opened something' : 'opened nothing'}`);
        } else {
            L.log(`touch: this backend never lifts the finger, so a tap on ${name} is not measured here`);
        }
        await leaveArrange();
        if (anyMenuOpen())
            await tap(x, y);
        await L.sleep(300);

        // A tap that shifts a little under the finger is still a tap.
        await tap(x, y, {drift: 4});
        if (liftSeen)
            L.check(`a tap on ${name} that moves a little is still a tap`, !arrange().active);
        await leaveArrange();
        if (anyMenuOpen())
            await tap(x, y);
        await L.sleep(300);
    }

    // A deliberate long press does open arrange mode.
    const clock = shelf.partActorFor?.('clock');
    if (clock?.visible) {
        const [x, y] = L.centre(L.rectOf(clock));
        await tap(x, y, {hold: 900});
        L.check('a long press opens arrange mode', arrange().active);
        await leaveArrange();
    }

    // A finger dragged across a dock tile scrolls or does nothing; it does
    // not begin an arrangement.
    const dock = shelf.islandActorFor?.('dock');
    if (dock?.visible) {
        const r = L.rectOf(dock);
        const device = touch();
        device.notify_touch_down(now(), 0, r.x + 40, r.y + r.height / 2);
        for (let i = 1; i <= 8; i++) {
            device.notify_touch_motion(now(), 0, r.x + 40 + i * 12, r.y + r.height / 2);
            await L.sleep(30);
        }
        device.notify_touch_up(now(), 0);
        await L.sleep(700);
        L.check('a finger dragged along the dock does not start an arrangement',
            !arrange().active);
        await leaveArrange();
    }
}

export function init() { L.start('touch', work); }
export async function run() { await new Promise(() => {}); }
