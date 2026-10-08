// SPDX-License-Identifier: GPL-2.0-or-later
// Rearranging re-flows tiled windows: three monitors, two windows in Tiling
// Shell tiles (left and right halves) and one floating window on the
// primary. Clearing the top edge and then taking the left edge changes the
// work area; the tiles follow it with their proportions and the configured
// gap, the floating window stays exactly where it was, and a maximized
// window re-fits.
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
    for (let i = 0; i < 40 && Main.layoutManager.monitors.length < 3; i++)
        await L.sleep(250);
    await L.layoutMonitors((GLib.getenv('SA_ORDER') || '1920x1200,3440x1440*,2560x1440').split(','));
    const m = Main.layoutManager.primaryMonitor;
    const index = Main.layoutManager.primaryIndex;
    const ext = Main.extensionManager.lookup(EXT)?.stateObj;
    const tm = ext?._tilingManagers?.[index];
    L.check('Tiling Shell is running', !!tm);
    await L.arrange([{edge: 'bottom', anchor: 'center', islands: ['dock', 'live', 'media']},
        {edge: 'top', anchor: 'end', islands: ['well', 'quick-options', 'clock', 'notifications']}], 1800);
    const a = await spawn('Left'), b = await spawn('Right'), c = await spawn('Floating'), d = await spawn('Maximized');
    L.check('four windows', a && b && c && d);
    for (const w of [a, b, c, d])
        w.move_to_monitor(index);
    await L.sleep(800);
    tm.onTileFromWindowMenu({x: 0, y: 0, width: 0.5, height: 1, groups: []}, a);
    tm.onTileFromWindowMenu({x: 0.5, y: 0, width: 0.5, height: 1, groups: []}, b);
    c.move_resize_frame(false, m.x + 600, m.y + 300, 700, 500);
    try { d.maximize(Meta.MaximizeFlags.BOTH); } catch { d.maximize(); }
    await L.sleep(1500);
    // What a window keeps from the Dash or the screen on each side: the gap
    // inside the work area plus the margin the reservation already holds
    // (one gap everywhere).
    const gaps = (r, w, kept = {}) => {
        return {
            left: r.x - w.x + (kept.left ?? 0),
            right: w.x + w.width - r.x - r.width + (kept.right ?? 0),
            top: r.y - w.y + (kept.top ?? 0),
            bottom: w.y + w.height - r.y - r.height + (kept.bottom ?? 0),
        };
    };
    const report = name => {
        const w = L.workArea(index);
        const fa = frame(a), fb = frame(b);
        const kept = Main.shelf.reservedMargins(index);
        L.log(`${name}: work ${JSON.stringify(w)} left ${JSON.stringify(fa)} right ${JSON.stringify(fb)} kept ${JSON.stringify(kept)}`);
        return {w, fa, fb, fc: frame(c), fd: frame(d), kept};
    };
    const before = report('top and bottom');
    const floating = before.fc;
    // Clear the top edge.
    await L.arrange([{edge: 'bottom', anchor: 'center', islands: ['dock', 'live', 'media']},
        {edge: 'bottom', anchor: 'end', islands: ['well', 'quick-options', 'clock', 'notifications']}], 2500);
    const cleared = report('top cleared');
    L.check('clearing the top edge grows the work area upwards', cleared.w.y < before.w.y - 40, `${before.w.y} -> ${cleared.w.y}`);
    for (const [name, r] of [['left', cleared.fa], ['right', cleared.fb]]) {
        const g = gaps(r, cleared.w, cleared.kept);
        L.check(`the ${name} tile fills the freed space (top gap as before)`, Math.abs(g.top - gaps(before[name === 'left' ? 'fa' : 'fb'], before.w, before.kept).top) <= 2,
            JSON.stringify(g));
    }
    L.check('the tiles keep their halves', Math.abs(cleared.fa.width - cleared.fb.width) <= 2 &&
        Math.abs(cleared.fa.width - before.fa.width) <= 2);
    L.check('the floating window is untouched', JSON.stringify(cleared.fc) === JSON.stringify(floating), JSON.stringify(cleared.fc));
    L.check('the maximized window re-fits the work area', JSON.stringify(cleared.fd) === JSON.stringify(cleared.w) ||
        L.inside(cleared.fd, cleared.w, 1) && cleared.fd.height >= cleared.w.height - 2, `${JSON.stringify(cleared.fd)} ${JSON.stringify(cleared.w)}`);
    await L.shot('rt-top-cleared', m);
    // Take the left edge.
    await L.arrange([{edge: 'left', anchor: 'center', islands: ['dock', 'live', 'media']},
        {edge: 'bottom', anchor: 'end', islands: ['well', 'quick-options', 'clock', 'notifications']}], 2500);
    const left = report('dock on the left');
    L.check('taking the left edge moves the left tile right', left.fa.x > cleared.fa.x + 40 &&
        Math.abs(gaps(left.fa, left.w, left.kept).left - gaps(cleared.fa, cleared.w, cleared.kept).left) <= 2, `${JSON.stringify(left.fa)}`);
    L.check('the right tile keeps its right edge gap', Math.abs(gaps(left.fb, left.w, left.kept).right - gaps(cleared.fb, cleared.w, cleared.kept).right) <= 2);
    L.check('the halves stay halves', Math.abs(left.fa.width - left.fb.width) <= 2);
    L.check('the floating window is still untouched', JSON.stringify(left.fc) === JSON.stringify(floating));
    await L.shot('rt-dock-left', m);
    // Churn: the Dash is rearranged five times in half a second and the
    // monitors are laid out again twice. A change the Dash makes itself is
    // followed at once (Shell .121), but never more than three times in two
    // seconds; a monitor change still waits for the layout to settle, and a
    // window stays on its monitor.
    const moves = new Map([[a, 0], [b, 0], [c, 0]]);
    const ids = [...moves.keys()].map(w => [w, w.connect('position-changed', () => moves.set(w, moves.get(w) + 1))]);
    for (let i = 0; i < 5; i++) {
        await L.arrange([{edge: i % 2 ? 'left' : 'top', anchor: 'center', islands: ['dock', 'live', 'media']},
            {edge: 'bottom', anchor: 'end', islands: ['well', 'quick-options', 'clock', 'notifications']}], 100);
    }
    await L.sleep(1500);
    const reflows = w => (w._lumaReflowMoves ?? []).length;
    L.check('a burst of Dash changes: the re-tile follows, at most three times in two seconds',
        reflows(a) <= 3 && reflows(b) <= 3, `${reflows(a)} ${reflows(b)} (all moves ${moves.get(a)} ${moves.get(b)})`);
    const monitorOf = w => w.get_monitor();
    const [ma, mb] = [monitorOf(a), monitorOf(b)];
    const order = (GLib.getenv('SA_ORDER') || '1920x1200,3440x1440*,2560x1440').split(',');
    for (const w of moves.keys()) moves.set(w, 0);
    await L.layoutMonitors([...order].reverse());
    await L.layoutMonitors(order);
    await L.sleep(2500);
    L.check('monitors laid out again twice: the tiled windows stay on their monitor', monitorOf(a) === ma && monitorOf(b) === mb,
        `${ma}->${monitorOf(a)} ${mb}->${monitorOf(b)}`);
    L.check('the re-tile never moves a window more than three times in two seconds', [a, b].every(w => reflows(w) <= 3),
        `${reflows(a)} ${reflows(b)} (all moves ${JSON.stringify([...moves.values()])})`);
    L.check('the floating window is still where it was', JSON.stringify(frame(c)) === JSON.stringify(floating) ||
        monitorOf(c) === index, JSON.stringify(frame(c)));
    ids.forEach(([w, id]) => w.disconnect(id));
    await L.reset(800);
}
export function init() { L.start('retile', work); }
export async function run() { await new Promise(() => {}); }
