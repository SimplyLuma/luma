// SPDX-License-Identifier: GPL-2.0-or-later
// Three monitors side by side, as on the owner's desk (a 34" ultrawide
// between the laptop panel and a 27"): every monitor keeps the tiling gap on
// all four edges, shared edges included, for windows tiled by Tiling Shell
// (left and right halves) and maximized, with and without islands on the side
// edges. SA_PROTRUDE=1 runs it with a protruding Dash.
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

const EXT = 'tilingshell@ferrarodomenico.com';

async function openWindow() {
    Shell.AppSystem.get_default().lookup_app('org.oracle.Files.desktop')?.activate();
    for (let i = 0; i < 40; i++) {
        const w = global.display.list_all_windows().find(win => win.title === 'Files');
        if (w)
            return w;
        await L.sleep(250);
    }
    return null;
}

function frame(window) {
    const r = window.get_frame_rect();
    return {x: r.x, y: r.y, width: r.width, height: r.height};
}

function gapsOf(r, m) {
    return {left: r.x - m.x, right: m.x + m.width - (r.x + r.width),
        top: r.y - m.y, bottom: m.y + m.height - (r.y + r.height)};
}

async function work() {
    const shelf = await L.waitForShelf();
    for (let i = 0; i < 40 && Main.layoutManager.monitors.length < 3; i++)
        await L.sleep(250);
    await L.sleep(1500);
    const protrude = !!GLib.getenv('SA_PROTRUDE');
    if (protrude) {
        L.settings().set_string('shelf-edge-mode', 'protruding');
        await L.sleep(1500);
    }
    const monitors = Main.layoutManager.monitors;
    L.check('three monitors', monitors.length === 3);
    const primary = Main.layoutManager.primaryIndex;
    const window = await openWindow();
    L.check('a window opened', !!window);
    const ext = Main.extensionManager.lookup(EXT)?.stateObj;
    L.check('Tiling Shell is running', !!ext?._tilingManagers?.length);
    const padding = L.settings().get_int('shelf-padding');
    const layouts = {
        'no side islands': null,
        'dock on the primary left edge, media on its right': [
            {edge: 'left', anchor: 'center', islands: ['dock']},
            {edge: 'right', anchor: 'center', islands: ['media']},
            {edge: 'bottom', anchor: 'end', islands: ['live', 'well', 'quick-options', 'clock', 'notifications']}],
    };
    for (const [name, groups] of Object.entries(layouts)) {
        if (groups)
            await L.arrange(groups, 2500);
        else
            await L.reset(2500);
        for (const m of monitors) {
            const work = L.workArea(m.index);
            const g = gapsOf(work, m);
            L.log(`${name}: monitor ${m.index} work ${JSON.stringify(work)} gaps ${JSON.stringify(g)}`);
            const sides = groups && m.index === primary ? ['top'] : ['left', 'right', 'top'];
            L.check(`${name}: monitor ${m.index} reserves nothing on its empty ${sides.join(', ')}`,
                sides.every(s => g[s] === 0), JSON.stringify(g));
            if (groups && m.index === primary) {
                // The band's depth: inset (0 protruding) + thickness + padding.
                const band = (protrude ? 0 : 14) + 36 + 3 * padding;
                L.check(`${name}: the primary reserves its left band (shared edge)`, g.left >= band, JSON.stringify(g));
                L.check(`${name}: and its right band (shared edge)`, g.right >= band, JSON.stringify(g));
            }
            if (!window)
                continue;
            try { window.unmaximize(Meta.MaximizeFlags.BOTH); } catch { window.unmaximize(); }
            window.move_to_monitor(m.index);
            await L.sleep(300);
            window.move_resize_frame(true, m.x + 120, m.y + 120, 900, 600);
            await L.sleep(500);
            // Maximized.
            try { window.maximize(Meta.MaximizeFlags.BOTH); } catch { window.maximize(); }
            await L.sleep(700);
            const max = frame(window);
            L.check(`${name}: monitor ${m.index} maximized inside its work area`, L.inside(max, work, 1),
                `${JSON.stringify(max)} in ${JSON.stringify(work)}`);
            try { window.unmaximize(Meta.MaximizeFlags.BOTH); } catch { window.unmaximize(); }
            await L.sleep(500);
            // Tiled into each half by Tiling Shell.
            const tm = ext?._tilingManagers?.[m.index];
            for (const [half, tile] of [['left', {x: 0, y: 0, width: 0.5, height: 1, groups: []}],
                ['right', {x: 0.5, y: 0, width: 0.5, height: 1, groups: []}]]) {
                if (!tm)
                    continue;
                tm.onTileFromWindowMenu(tile, window);
                await L.sleep(900);
                const r = frame(window);
                const gm = gapsOf(r, m);
                L.log(`${name}: monitor ${m.index} ${half} tile ${JSON.stringify(r)} gaps to the screen ${JSON.stringify(gm)}`);
                L.check(`${name}: monitor ${m.index} ${half} tile inside the work area`, L.inside(r, work, 1),
                    `${JSON.stringify(r)} in ${JSON.stringify(work)}`);
                L.check(`${name}: monitor ${m.index} ${half} tile reaches its ${half} work-area edge (Tiling Shell's own gap only)`,
                    gm[half] - g[half] >= 0 && gm[half] - g[half] < 40, `${JSON.stringify(gm)} work ${JSON.stringify(g)}`);
            }
            await L.shot(`t3-${name.replace(/[^a-z]+/g, '-')}-m${m.index}`, m);
        }
    }
    if (protrude)
        L.settings().reset('shelf-edge-mode');
    await L.reset(1000);
}
export function init() { L.start('tiling3', work); }
export async function run() { await new Promise(() => {}); }
