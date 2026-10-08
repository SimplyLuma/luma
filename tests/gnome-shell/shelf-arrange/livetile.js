// SPDX-License-Identifier: GPL-2.0-or-later
// A live extension on the top edge comes and goes three times over two
// tiled windows. Each time the tiles follow at once (no settle delay: the
// Dash made the change), in one glide per window with the window fully
// opaque throughout and one final geometry, and they return exactly to the
// rectangles they had, Tiling Shell's outer gap included on the emptied top
// edge. Tiling Shell's own panel button is never in the Dash.
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
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
    const m = Main.layoutManager.primaryMonitor;
    const index = Main.layoutManager.primaryIndex;
    const ext = Main.extensionManager.lookup(EXT)?.stateObj;
    const tm = ext?._tilingManagers?.[index];
    L.check('Tiling Shell is running', !!tm);
    const ts = ext?.getSettings?.();
    const outer = ts?.get_uint('outer-gaps') ?? 0;
    L.check('Tiling Shell keeps an outer gap', outer > 0, `${outer}`);
    // Tiling Shell's panel button, even if turned back on, is not in the Dash.
    ts?.set_boolean('show-indicator', true);
    await L.sleep(1500);
    const indicator = Main.panel.statusArea[EXT];
    const inDash = indicator && shelf.contains(indicator.container ?? indicator);
    L.check('Tiling Shell\'s panel button is never in the Dash', !inDash, `${!!indicator}`);
    ts?.reset('show-indicator');

    await L.arrange([{edge: 'top', anchor: 'center', islands: ['live', 'media']},
        {edge: 'bottom', anchor: 'center', islands: ['dock', 'well', 'quick-options', 'clock', 'notifications']}], 1800);
    const live = shelf._liveIndicator;
    L.check('a live extension is running', !!live);
    const media = shelf.getIsland('media');
    if (media)
        media.visible = false;
    live.visible = false;
    await L.sleep(1500);
    const a = await spawn('Left'), b = await spawn('Right');
    L.check('two windows', a && b);
    await L.sleep(1500);
    tm.onTileFromWindowMenu({x: 0, y: 0, width: 0.5, height: 1, groups: []}, a);
    tm.onTileFromWindowMenu({x: 0.5, y: 0, width: 0.5, height: 1, groups: []}, b);
    for (let i = 0; i < 40 && (frame(a).x !== m.x + outer || frame(b).y !== m.y + outer); i++)
        await L.sleep(150);
    await L.sleep(1000);
    const empty = [frame(a), frame(b)];
    const top = empty.map(r => r.y - m.y);
    L.check('with the top edge empty, the tiles keep the outer gap there', top.every(t => t === outer),
        `${JSON.stringify(top)} outer ${outer}`);
    let full = null;
    for (let round = 1; round <= 3; round++) {
        for (const show of [true, false]) {
            const trace = new Map([[a, []], [b, []]]);
            const opacity = new Map([[a, []], [b, []]]);
            const ids = [a, b].map(w => [w, w.connect('position-changed', () => trace.get(w).push(frame(w))),
                w.connect('size-changed', () => trace.get(w).push(frame(w)))]);
            const started = GLib.get_monotonic_time();
            live.visible = show;
            let movedAt = 0;
            for (let t = 0; t < 40; t++) {
                await L.sleep(20);
                for (const w of [a, b]) {
                    const actor = w.get_compositor_private();
                    opacity.get(w).push(actor ? actor.opacity : -1);
                }
                if (!movedAt && trace.get(a).length)
                    movedAt = (GLib.get_monotonic_time() - started) / 1000;
            }
            await L.sleep(800);
            ids.forEach(([w, p, s]) => { w.disconnect(p); w.disconnect(s); });
            const name = `round ${round}: the live extension ${show ? 'appears' : 'goes'}`;
            L.check(`${name}: the tiles follow at once (under 150 ms)`, movedAt > 0 && movedAt < 150, `${movedAt} ms`);
            L.check(`${name}: every window stays fully opaque`, [a, b].every(w => opacity.get(w).every(o => o === 255)),
                JSON.stringify([...opacity.values()].map(v => Math.min(...v))));
            const finals = [a, b].map(w => new Set(trace.get(w).map(r => JSON.stringify(r))).size);
            L.check(`${name}: one final geometry per window`, finals.every(n => n === 1),
                JSON.stringify([...trace.values()]));
            const now = [frame(a), frame(b)];
            if (show) {
                full ??= now;
                L.check(`${name}: the tiles move below the top band`, now.every(r => r.y > m.y + outer), JSON.stringify(now));
                L.check(`${name}: and match the first time exactly`, JSON.stringify(now) === JSON.stringify(full),
                    `${JSON.stringify(now)} vs ${JSON.stringify(full)}`);
            } else {
                L.check(`${name}: the tiles return exactly to their rectangles, gaps included`,
                    JSON.stringify(now) === JSON.stringify(empty), `${JSON.stringify(now)} vs ${JSON.stringify(empty)}; ` +
                    `work ${JSON.stringify(L.workArea(index))} reserved ${shelf._loggedReservations} tm ${JSON.stringify(L.rectOf ? {x: tm._workArea.x, y: tm._workArea.y, w: tm._workArea.width, h: tm._workArea.height} : null)} ` +
                    `live island ${shelf._liveIsland?.visible} groups ${JSON.stringify(L.groups().filter(g => g.edge === 'top'))}`);
            }
            if (round === 1)
                await L.shot(`lt-${show ? 'shown' : 'gone'}`, m);
            await L.sleep(1200);
        }
    }
    // Reduced motion: the windows are simply moved, nothing eases.
    const iface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
    iface.set_boolean('enable-animations', false);
    await L.sleep(600);
    live.visible = true;
    await L.sleep(60);
    const easing = [a, b].some(w => {
        const actor = w.get_compositor_private();
        return !!(actor?.get_transition('translation-x') || actor?.get_transition('translation-y') ||
            actor?.get_transition('scale-x') || actor?.get_transition('opacity'));
    });
    await L.sleep(800);
    L.check('reduced motion: the tiles move without easing, to the same rectangles', !easing &&
        JSON.stringify([frame(a), frame(b)]) === JSON.stringify(full), `${easing} ${JSON.stringify([frame(a), frame(b)])}`);
    iface.reset('enable-animations');
    await L.sleep(400);
    live.visible = true;
    await L.reset(800);
}
export function init() { L.start('livetile', work); }
export async function run() { await new Promise(() => {}); }
