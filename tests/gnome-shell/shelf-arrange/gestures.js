// SPDX-License-Identifier: GPL-2.0-or-later
// Three fingers sideways switches app, four switches workspace, and a swipe
// let go where it started changes nothing. The finger counts are read off
// the gestures themselves; the switch is driven through the controller the
// touchpad drives, so a swipe that is taken back really does put everything
// as it was.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

const T = GLib.path_get_dirname(GLib.getenv('SA_OUT')) + '/../tests';

async function spawn(title) {
    GLib.spawn_async(null, ['python3', `${T}/win.py`, `org.oracle.${title}`, title],
        null, GLib.SpawnFlags.SEARCH_PATH, null);
    for (let i = 0; i < 80; i++) {
        const w = global.display.list_all_windows().find(win => win.title === title);
        if (w)
            return w;
        await L.sleep(250);
    }
    return null;
}

const titleOf = () => global.display.focus_window?.title ?? 'none';

async function work() {
    await L.waitForShelf();
    const state = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});

    // The defaults, written once.
    L.check('four fingers switch workspace', state.get_int('touchpad-workspace-fingers') === 4,
        `${state.get_int('touchpad-workspace-fingers')}`);
    L.check('three fingers switch app', state.get_boolean('touchpad-app-switch'));
    L.check('the gesture defaults were written once',
        state.get_uint('touchpad-gesture-migration') === 1,
        `${state.get_uint('touchpad-gesture-migration')}`);

    // A person who changes them keeps their choice: the migration does not
    // run again.
    state.set_int('touchpad-workspace-fingers', 3);
    await L.sleep(300);
    L.check('a changed setting is not written over',
        state.get_int('touchpad-workspace-fingers') === 3);
    state.set_int('touchpad-workspace-fingers', 4);
    await L.sleep(300);

    const appSwitch = Main.wm._lumaAppSwitch;
    L.check('the app switch gesture exists', !!appSwitch);
    if (!appSwitch)
        return;

    const fingersOf = tracker => {
        const touchpad = tracker?._touchpadGesture;
        return touchpad ? [touchpad._fingerCount, touchpad._exactFingerCount] : [null, null];
    };
    const [appFingers, appExact] = fingersOf(appSwitch.tracker);
    const [wsFingers, wsExact] = fingersOf(Main.wm._workspaceAnimation?._swipeTracker);
    L.check('the app gesture is exactly three fingers', appFingers === 3 && appExact === true,
        `${appFingers} ${appExact}`);
    L.check('the workspace gesture is exactly four fingers', wsFingers === 4 && wsExact === true,
        `${wsFingers} ${wsExact}`);
    L.check('they are horizontal, so three fingers up is still the overview',
        appSwitch.tracker.orientation === Clutter.Orientation.HORIZONTAL);

    const a = await spawn('SwipeOne');
    const b = await spawn('SwipeTwo');
    const c = await spawn('SwipeThree');
    L.check('three windows to switch between', !!a && !!b && !!c);
    if (!a || !b || !c)
        return;
    await L.sleep(1500);

    // The app the person is in, and the one before it.
    c.activate(global.get_current_time());
    await L.sleep(1200);
    const order = appSwitch.appOrder();
    L.check('the apps are in the order they were used',
        order.length >= 3 && order[0].window.title === 'SwipeThree',
        JSON.stringify(order.slice(0, 3).map(o => o.window.title)));

    const monitorIndex = Main.layoutManager.primaryIndex;
    const swipe = async (endProgress, {steps = 6} = {}) => {
        appSwitch._begin(appSwitch.tracker, monitorIndex);
        for (let i = 1; i <= steps; i++)
            appSwitch._update(appSwitch.tracker, endProgress * i / steps);
        await L.sleep(120);
        appSwitch._end(appSwitch.tracker, 0, endProgress);
        await L.sleep(900);
    };

    // Taken back: let go where it started.
    const before = titleOf();
    appSwitch._begin(appSwitch.tracker, monitorIndex);
    for (const p of [-0.1, -0.3, -0.5, -0.3, 0])
        appSwitch._update(appSwitch.tracker, p);
    await L.sleep(150);
    appSwitch._end(appSwitch.tracker, 0, 0);
    await L.sleep(900);
    L.check('a swipe let go where it started changes nothing', titleOf() === before,
        `${before} -> ${titleOf()}`);
    L.check('no clones are left behind after a swipe taken back',
        !Main.uiGroup.get_children().some(a_ => a_.name === 'lumaAppSwitchGroup'));
    L.check('the windows are visible again after a swipe taken back',
        global.get_window_actors().every(actor => actor.visible || actor.meta_window.minimized));

    // Committed: one step back through the apps.
    await swipe(-1);
    L.check('a swipe through switches to the app used before this one',
        titleOf() === 'SwipeTwo', `${titleOf()}`);
    L.check('no clones are left behind after a swipe through',
        !Main.uiGroup.get_children().some(a_ => a_.name === 'lumaAppSwitchGroup'));
    L.check('every window is visible again after a swipe through',
        global.get_window_actors().every(actor => actor.visible || actor.meta_window.minimized));

    // And back the other way.
    await swipe(-1);
    L.check('another swipe keeps going back through the apps',
        titleOf() === 'SwipeThree' || titleOf() === 'SwipeOne', `${titleOf()}`);

    await L.shot('gestures-after-switch');
}

export function init() { L.start('gestures', work); }
export async function run() { await new Promise(() => {}); }
