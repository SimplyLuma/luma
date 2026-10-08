// SPDX-License-Identifier: GPL-2.0-or-later
// One gap everywhere: a tiled window sits the same distance from another
// window, from a Dash island and from the screen's edge, on every edge with
// and without islands, and with the Dash on each of the four edges.
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

const EXT = 'tilingshell@ferrarodomenico.com';
const T = GLib.path_get_dirname(GLib.getenv('SA_OUT')) + '/../tests';
const GAP = 16;

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
    const m = Main.layoutManager.primaryMonitor;
    const index = Main.layoutManager.primaryIndex;
    const ext = Main.extensionManager.lookup(EXT)?.stateObj;
    const tm = ext?._tilingManagers?.[index];
    L.check('Tiling Shell runs on the primary', !!tm);
    const ts = ext.getSettings();
    ts.set_uint('inner-gaps', GAP);
    ts.set_uint('outer-gaps', GAP);
    await L.sleep(800);
    const a = await spawn('First'), b = await spawn('Second');
    L.check('two windows', !!a && !!b);
    for (const w of [a, b])
        w.move_to_monitor(index);
    await L.sleep(800);
    for (const [edge, other] of [['bottom', 'top'], ['top', 'bottom'], ['left', 'right'], ['right', 'left']]) {
        // The Dash holds one edge; the other three hold nothing.
        await L.arrange([{edge, anchor: 'center', islands: ['dock']},
            {edge, anchor: 'end', islands: ['well', 'quick-options', 'clock']}], 1600);
        const vertical = edge === 'left' || edge === 'right';
        // Two tiles across the work area, side by side the other way round
        // from the Dash's edge, so both touch it.
        const tiles = vertical
            ? [{x: 0, y: 0, width: 1, height: 0.5}, {x: 0, y: 0.5, width: 1, height: 0.5}]
            : [{x: 0, y: 0, width: 0.5, height: 1}, {x: 0.5, y: 0, width: 0.5, height: 1}];
        tiles.forEach((tile, i) => tm.onTileFromWindowMenu({...tile, groups: []}, [a, b][i]));
        await L.sleep(1800);
        const fa = frame(a), fb = frame(b);
        const between = vertical ? fb.y - (fa.y + fa.height) : fb.x - (fa.x + fa.width);
        L.check(`${edge}: one gap between the two windows`, between === GAP, `${between}`);
        // The island: the Dash's band on that edge, as it is drawn.
        const dock = L.rectOf(shelf.islandActorFor('dock'));
        const toIsland = {
            bottom: dock.y - (fa.y + fa.height),
            top: fa.y - (dock.y + dock.height),
            left: fa.x - (dock.x + dock.width),
            right: dock.x - (fa.x + fa.width),
        }[edge];
        L.check(`${edge}: one gap from the window to the island`, toIsland === GAP,
            `${toIsland} window ${JSON.stringify(fa)} island ${JSON.stringify(dock)}`);
        // The three edges the Dash does not hold: one gap to the screen.
        const screen = {
            top: fa.y - m.y,
            left: fa.x - m.x,
            right: m.x + m.width - (fb.x + fb.width),
            bottom: m.y + m.height - ((vertical ? fb : fa).y + (vertical ? fb : fa).height),
        };
        for (const free of ['top', 'bottom', 'left', 'right']) {
            if (free === edge)
                continue;
            const at = vertical && (free === 'top' || free === 'bottom') ? screen[free]
                : !vertical && (free === 'left' || free === 'right') ? screen[free] : screen[free];
            L.check(`${edge} Dash: one gap from the window to the ${free} screen edge`, at === GAP,
                `${at} ${JSON.stringify({fa, fb, m: {x: m.x, y: m.y, width: m.width, height: m.height}})}`);
        }
        await L.shot(`gaps-${edge}`, m);
    }
    // With no Dash at all every edge is one gap from the screen.
    await L.arrange([{edge: 'bottom', anchor: 'center', islands: ['dock', 'well', 'quick-options', 'clock']}], 1200);
    const s = L.settings();
    s.set_boolean('shelf-reserve-work-area', false);
    await L.sleep(1800);
    tm.onTileFromWindowMenu({x: 0, y: 0, width: 0.5, height: 1, groups: []}, a);
    await L.sleep(1500);
    const fa = frame(a);
    const pad = shelf._padding;
    L.check('Reserve work area off: the window keeps one gap from the screen', fa.y - m.y === GAP && fa.x - m.x === GAP,
        `${JSON.stringify(fa)} padding ${pad}`);
    s.reset('shelf-reserve-work-area');
    await L.sleep(1200);
    await L.reset(800);
}
export function init() { L.start('gaps', work); }
export async function run() { await new Promise(() => {}); }
