// SPDX-License-Identifier: GPL-2.0-or-later
// Holding a window in its tile: a window that closes while it is held takes
// its hold with it (no JS errors, nothing left running), a window pushed out
// of its tile is put back, and anything that is not a window is refused
// loudly instead of being called as one. The suite's JS error count is part
// of this scenario's result.
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

const EXT = 'tilingshell@ferrarodomenico.com';
const T = GLib.path_get_dirname(GLib.getenv('SA_OUT')) + '/../tests';

async function spawn(title) {
    GLib.spawn_async(null, ['python3', `${T}/win.py`, `org.oracle.${title}`, title],
        null, GLib.SpawnFlags.SEARCH_PATH, null);
    for (let i = 0; i < 60; i++) {
        const w = global.display.list_all_windows().find(win => win.title === title);
        if (w)
            return w;
        await L.sleep(250);
    }
    return null;
}
const frame = w => { const r = w.get_frame_rect(); return {x: r.x, y: r.y, width: r.width, height: r.height}; };

async function work() {
    const shelf = await L.waitForShelf();
    const index = Main.layoutManager.primaryIndex;
    const ext = Main.extensionManager.lookup(EXT)?.stateObj;
    const tm = ext?._tilingManagers?.[index];
    L.check('Tiling Shell runs on the primary', !!tm);
    await L.arrange([{edge: 'bottom', anchor: 'center', islands: ['dock']},
        {edge: 'bottom', anchor: 'end', islands: ['well', 'quick-options', 'clock', 'notifications']}], 1400);
    const left = await spawn('Left'), right = await spawn('Right');
    L.check('two windows', !!left && !!right);
    for (const w of [left, right])
        w.move_to_monitor(index);
    await L.sleep(800);

    // A window that goes away while it is held.
    const closing = await spawn('Closing');
    closing.move_to_monitor(index);
    await L.sleep(800);
    tm.onTileFromWindowMenu({x: 0, y: 0, width: 0.5, height: 1, groups: []}, left);
    tm.onTileFromWindowMenu({x: 0.5, y: 0, width: 0.5, height: 1, groups: []}, right);
    await L.sleep(1500);
    const held = [frame(left), frame(right)];
    for (let round = 1; round <= 3; round++) {
        const w = round === 1 ? closing : await spawn(`Closing ${round}`);
        w.move_to_monitor(index);
        await L.sleep(600);
        tm.onTileFromWindowMenu({x: 0.5, y: 0, width: 0.5, height: 1, groups: []}, w);
        // Inside the hold's own window: the check would run on a window
        // Mutter has already let go of.
        await L.sleep(80);
        w.delete(global.get_current_time());
        await L.sleep(1200);
        L.check(`round ${round}: the window that closed while held is gone`,
            !global.display.list_all_windows().includes(w));
    }
    await L.sleep(800);
    L.check('the windows that stayed are still exactly in their tiles',
        JSON.stringify([frame(left), frame(right)]) === JSON.stringify(held),
        `${JSON.stringify([frame(left), frame(right)])} vs ${JSON.stringify(held)}`);
    L.check('no hold is left running', (tm._lumaTileChecks?.size ?? 0) === 0,
        `${tm._lumaTileChecks?.size}`);

    // Pushed out of its tile while held: put back.
    const want = frame(left);
    tm.onTileFromWindowMenu({x: 0, y: 0, width: 0.5, height: 1, groups: []}, left);
    await L.sleep(60);
    left.move_frame(false, want.x, want.y - 90);
    await L.sleep(1500);
    L.check('a window pushed out of its tile while held is put back',
        JSON.stringify(frame(left)) === JSON.stringify(want),
        `${JSON.stringify(frame(left))} want ${JSON.stringify(want)}`);

    // Anything that is not a window is refused, and says so.
    let threw = null;
    try {
        tm._lumaHoldTile(left.get_compositor_private(), want, 50, 'oracle');
    } catch (e) {
        threw = `${e}`;
    }
    L.check('a window actor is refused, not called as a window', threw === null, `${threw}`);
    let threw2 = null;
    try {
        tm._lumaHoldTile({assignedTile: {}, minimized: false}, want, 50, 'oracle');
        tm._lumaReleaseTile({});
    } catch (e) {
        threw2 = `${e}`;
    }
    L.check('a plain object is refused too', threw2 === null, `${threw2}`);
    await L.sleep(400);
    L.check('the refusals started no hold', (tm._lumaTileChecks?.size ?? 0) === 0,
        `${tm._lumaTileChecks?.size}`);
    for (const w of [left, right])
        w.delete(global.get_current_time());
    await L.sleep(600);
    await L.reset(800);
}
export function init() { L.start('hold', work); }
export async function run() { await new Promise(() => {}); }
