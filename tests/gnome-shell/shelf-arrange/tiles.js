// SPDX-License-Identifier: GPL-2.0-or-later
// The owner's desk: the ThinkPad panel (1536x960) left of a 5120x1440
// primary, his Dash (Quick Options and notifications at the bottom end, the
// dock at the bottom centre, the live family on top; islands, protruding,
// Span and Float off) and his tiling layouts. Every tile of every default
// layout gets a window; every tile that reaches the bottom (or the top)
// touches the same boundary -- one gap from the Dash or from the screen --
// both as Mutter places the window and as it is drawn, before
// and after the live extension on top comes and goes three times.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

const EXT = 'tilingshell@ferrarodomenico.com';
const T = GLib.path_get_dirname(GLib.getenv('SA_OUT')) + '/../tests';
const LAYOUTS = [
    {id: 'Luma Split', tiles: [{x: 0, y: 0, width: 0.5, height: 1}, {x: 0.5, y: 0, width: 0.5, height: 1}]},
    {id: 'Layout 1', tiles: [{x: 0, y: 0, width: 0.22, height: 0.5}, {x: 0, y: 0.5, width: 0.22, height: 0.5},
        {x: 0.22, y: 0, width: 0.56, height: 1}, {x: 0.78, y: 0, width: 0.22, height: 0.5},
        {x: 0.78, y: 0.5, width: 0.22, height: 0.5}]},
    {id: 'Layout 2', tiles: [{x: 0, y: 0, width: 0.22, height: 1}, {x: 0.22, y: 0, width: 0.56, height: 1},
        {x: 0.78, y: 0, width: 0.22, height: 1}]},
    {id: 'Halves', tiles: [{x: 0, y: 0, width: 1, height: 0.5}, {x: 0, y: 0.5, width: 1, height: 0.49999999999999994}]},
];

async function spawn(title) {
    GLib.spawn_async(null, ['python3', `${T}/win.py`, `org.oracle.${title.replace(/\W/g, '')}`, title],
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
// Where the window is drawn: its actor's painted frame (a frozen or scaled
// actor shows elsewhere than its frame).
function drawn(w) {
    const actor = w.get_compositor_private();
    const f = w.get_frame_rect(), b = w.get_buffer_rect();
    const [x, y] = actor.get_transformed_position();
    const sx = actor.scale_x, sy = actor.scale_y;
    return {x: Math.round(x + (f.x - b.x) * sx), y: Math.round(y + (f.y - b.y) * sy),
        width: Math.round(f.width * sx), height: Math.round(f.height * sy)};
}

async function work() {
    const shelf = await L.waitForShelf();
    for (let i = 0; i < 40 && Main.layoutManager.monitors.length < 2; i++)
        await L.sleep(250);
    // As on the owner's desk: the panel's top at y 695, so it reaches
    // below the primary's bottom edge.
    await L.layoutMonitors(GLib.getenv('SA_TILES_FLAT') ? ['1536x960', '5120x1440*'] : ['1536x960+0+695', '5120x1440*+1536+0']);
    await L.sleep(1000);
    const s = L.settings();
    s.set_string('shelf-surface-mode', 'separate');
    s.set_string('shelf-edge-mode', 'protruding');
    s.set_boolean('shelf-span-full', false);
    s.set_boolean('shelf-float-ends', false);
    await L.arrange([{edge: 'bottom', anchor: 'end', islands: ['well', 'quick-options', 'clock']},
        {edge: 'bottom', anchor: 'center', islands: ['dock']},
        {edge: 'top', anchor: 'center', islands: ['live', 'media']},
        {edge: 'bottom', anchor: 'end', islands: ['notifications']}], 2000);
    const m = Main.layoutManager.primaryMonitor;
    const index = Main.layoutManager.primaryIndex;
    const ext = Main.extensionManager.lookup(EXT)?.stateObj;
    const tm = ext?._tilingManagers?.[index];
    L.check('Tiling Shell runs on the primary', !!tm);
    const ts = ext.getSettings();
    ts.set_uint('inner-gaps', 10);
    const outer = ts.get_uint('outer-gaps');
    const live = shelf._liveIndicator;
    const windows = [];
    for (let i = 0; i < 5; i++)
        windows.push(await spawn(`Tile ${i}`));
    L.check('five windows', windows.every(Boolean));
    await L.sleep(1500);
    for (const w of windows)
        w.move_to_monitor(index);
    await L.sleep(500);
    const check = (layout, phase) => {
        const wa = L.workArea(index);
        // One gap everywhere: the work area already holds the margin a
        // window keeps from an island, so the tiles add the rest of the gap
        // there and the whole gap on an edge the Dash does not hold.
        const kept = Main.shelf.reservedMargins(index);
        const gapAt = edge => Math.max(0, outer - (kept[edge] ?? 0));
        const bottom = wa.y + wa.height - gapAt('bottom'), top = wa.y + gapAt('top');
        layout.tiles.forEach((tile, i) => {
            const w = windows[i];
            const f = frame(w), d = drawn(w);
            const name = `${layout.id} ${phase}: tile ${i} (${tile.x},${tile.y} ${tile.width}x${tile.height.toFixed(2)})`;
            if (Math.abs(tile.y + tile.height - 1) < 0.001) {
                L.check(`${name} reaches the bottom boundary (work area less the outer gap)`,
                    f.y + f.height === bottom && Math.abs(d.y + d.height - bottom) <= 1,
                    `frame ${JSON.stringify(f)} drawn ${JSON.stringify(d)} bottom ${bottom} work ${JSON.stringify(wa)}`);
            }
            if (tile.y === 0) {
                L.check(`${name} reaches the top boundary`, f.y === top && Math.abs(d.y - top) <= 1,
                    `frame ${JSON.stringify(f)} drawn ${JSON.stringify(d)} top ${top}`);
            }
        });
    };
    for (const layout of LAYOUTS) {
        layout.tiles.forEach((tile, i) => tm.onTileFromWindowMenu({...tile, groups: []}, windows[i]));
        await L.sleep(2000);
        check(layout, 'tiled');
        if (layout.id === 'Layout 1') {
            // A late answer from the client, or Mutter keeping the window
            // inside a work area that is about to change, pushes the
            // bottom-right window up after it was tiled: it goes back.
            const w = windows[4], want = frame(w);
            tm.onTileFromWindowMenu({...layout.tiles[4], groups: []}, w);
            await L.sleep(60);
            w.move_frame(false, want.x, want.y - 86);
            await L.sleep(1500);
            L.check('a lower tile\'s window pushed up after tiling is put back in its tile',
                JSON.stringify(frame(w)) === JSON.stringify(want), `${JSON.stringify(frame(w))} want ${JSON.stringify(want)}`);
        }
        for (let round = 1; round <= 3; round++) {
            live.visible = false;
            await L.sleep(1200);
            live.visible = true;
            await L.sleep(1200);
        }
        check(layout, 'after the live extension came and went 3 times');
        if (layout.id === 'Layout 1')
            await L.shot('tiles-layout1', m);
        live.visible = false;
        await L.sleep(1200);
        check(layout, 'with the top edge empty');
        live.visible = true;
        await L.sleep(1200);
    }
    // By hand: each window dragged by its title bar into a tile of Layout 1
    // (Tiling Shell shows the layout while dragging), with the live
    // extension on top shown and then gone.
    const layout1 = LAYOUTS[1];
    ts.set_boolean?.('show-layout-while-dragging', true);
    for (const liveShown of [true, false]) {
        live.visible = liveShown;
        await L.sleep(1500);
        // Nothing left open from before (Tiling Shell's window suggestions).
        await L.key(Clutter.KEY_Escape);
        for (const [i, tile] of layout1.tiles.entries()) {
            const w = windows[i];
            try { w.unmaximize?.(3); } catch {}
            // Taller than a half-height tile, like a window used before:
            // moved into a lower tile it must shrink, never be pushed up.
            w.move_resize_frame(false, m.x + 200 + i * 300, m.y + 200, 700, 900);
            await L.sleep(500);
            const f = frame(w);
            const wa = L.workArea(index);
            const target = [wa.x + (tile.x + tile.width / 2) * wa.width, wa.y + (tile.y + tile.height / 2) * wa.height];
            const from = [f.x + f.width / 2, f.y + 14];
            await L.move(...from, 150);
            w.raise();
            w.activate(global.get_current_time());
            await L.sleep(400);
            await L.press();
            for (let k = 1; k <= 24; k++)
                await L.move(from[0] + (target[0] - from[0]) * k / 24, from[1] + (target[1] - from[1]) * k / 24, 25);
            await L.sleep(500);
            await L.release();
            await L.sleep(1200);
        }
        check(layout1, `dragged into tiles, live extension ${liveShown ? 'shown' : 'gone'}`);
        await L.shot(`tiles-dragged-${liveShown ? 'live' : 'nolive'}`, m);
    }
    live.visible = true;
    for (const key of ['shelf-surface-mode', 'shelf-edge-mode', 'shelf-span-full', 'shelf-float-ends'])
        s.reset(key);
    await L.reset(800);
}
export function init() { L.start('tiles', work); }
export async function run() { await new Promise(() => {}); }
