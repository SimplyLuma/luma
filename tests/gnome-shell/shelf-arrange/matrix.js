// SPDX-License-Identifier: GPL-2.0-or-later
// Every combination of Use islands, Span full edge, Protrude and Float outer
// edges, with the dock on each edge of the primary and a hidden
// notifications island in the live family's group:
//   - what arrange mode shows is what the desktop shows after Done (every
//     island but a stretched dock at the same place, ±1 px)
//   - a hidden island takes no length and holds no edge
//   - Use islands off: one bar the whole length of each occupied edge
//     (SHELF_INSET short of each end with Float outer edges)
//   - Span with islands, Float off: the end groups reach the screen's ends
//   - Protrude: the islands touch the screen edge
//   - the work area: the band's depth on the dock's edge, nothing elsewhere
// The same on all four edges.
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as A from 'resource:///org/gnome/shell/ui/shelfArrangement.js';
import * as L from './lib.js';

const vertical = e => A.isVertical(e);
const alongOf = (r, e) => vertical(e) ? [r.y, r.y + r.height] : [r.x, r.x + r.width];
const crossGap = (r, m, e) => ({top: r.y - m.y, bottom: m.y + m.height - r.y - r.height,
    left: r.x - m.x, right: m.x + m.width - r.x - r.width})[e];

function visibleRects(shelf) {
    const out = {};
    for (const id of ['dock', 'live', 'media', 'clock']) {
        const a = shelf.islandActorFor(id);
        if (a?.visible && a.get_stage() && !a._isGhost)
            out[id] = L.rectOf(a);
    }
    return out;
}

async function work() {
    const shelf = await L.waitForShelf();
    const s = L.settings();
    const m = Main.layoutManager.primaryMonitor;
    let failures = 0;
    const check = (name, ok, detail) => { if (!ok) failures++; return L.check(name, ok, detail); };
    for (const islands of [true, false]) {
        for (const span of [false, true]) {
            for (const protrude of [false, true]) {
                for (const float of [true, false]) {
                    s.set_string('shelf-surface-mode', islands ? 'separate' : 'connected');
                    s.set_boolean('shelf-span-full', span);
                    s.set_string('shelf-edge-mode', protrude ? 'protruding' : 'floating');
                    s.set_boolean('shelf-float-ends', float);
                    const combo = `${islands ? 'islands' : 'bar'}${span ? '+span' : ''}${protrude ? '+protrude' : ''}${float ? '+float' : ''}`;
                    for (const edge of A.EDGES) {
                        await L.arrange([
                            {edge, anchor: 'start', islands: ['live', 'media', 'notifications']},
                            {edge, anchor: 'center', islands: ['dock']},
                            {edge, anchor: 'end', islands: ['well', 'quick-options', 'clock']},
                        ], 1400);
                        const name = `${combo} ${edge}`;
                        check(`${name}: the notifications island is hidden`, !shelf.getIsland('notifications').visible);
                        // Arrange mode, then Done.
                        shelf._arrange.begin({select: 'clock'});
                        await L.sleep(900);
                        const editor = visibleRects(shelf);
                        shelf._arrange.end();
                        await L.sleep(1100);
                        const live = visibleRects(shelf);
                        const moved = Object.keys(editor).filter(id => {
                            if (!live[id])
                                return true;
                            const [a0, a1] = alongOf(editor[id], edge), [b0, b1] = alongOf(live[id], edge);
                            return Math.abs(a0 - b0) > 1 || Math.abs(a1 - b1) > 1;
                        });
                        check(`${name}: the desktop shows what arrange mode showed`, !moved.length,
                            moved.map(id => `${id} ${JSON.stringify(editor[id])} -> ${JSON.stringify(live[id])}`).join('; '));
                        const drawn = L.groups().filter(g => g.edge === edge && g.monitor === m.index && g.islands.length);
                        const [ms, me] = vertical(edge) ? [m.y, m.y + m.height] : [m.x, m.x + m.width];
                        const inset = float || (islands && !span) ? 14 : 0;
                        if (!islands) {
                            const bar = drawn[0]?.rect;
                            const [b0, b1] = bar ? alongOf(bar, edge) : [NaN, NaN];
                            check(`${name}: one bar the length of the edge`, drawn.length === 1 &&
                                Math.abs(b0 - (ms + inset)) <= 1 && Math.abs(b1 - (me - inset)) <= 1,
                                `${drawn.length} ${b0}..${b1} in ${ms}..${me}`);
                        } else {
                            // The live family's run holds only its visible islands.
                            const fam = drawn.find(g => g.islands.some(i => i.id === 'live' || i.id === 'media'));
                            if (fam) {
                                const vis = fam.islands.filter(i => i.id === 'live' || i.id === 'media').map(i => alongOf(i.rect, edge));
                                const [g0, g1] = alongOf(fam.rect, edge);
                                check(`${name}: the hidden notifications island takes no length`,
                                    Math.abs(g1 - Math.max(...vis.map(v => v[1]))) <= 1, `${g0}..${g1} vs ${JSON.stringify(vis)}`);
                            }
                            // The runs at the ends reach 14 px in, or the
                            // screen's ends with Span and no Float.
                            const runs = drawn.map(g => alongOf(g.rect, edge));
                            const first = Math.min(...runs.map(r => r[0])), last = Math.max(...runs.map(r => r[1]));
                            const end = span && !float ? 0 : 14;
                            check(`${name}: the end groups sit ${end} px from the screen's ends`,
                                Math.abs(first - (ms + end)) <= 1 && Math.abs(last - (me - end)) <= 1, `${first}..${last} in ${ms}..${me}`);
                        }
                        const rect = live.clock ?? live.dock;
                        check(`${name}: ${protrude ? 'touches the screen edge' : 'floats 14 px in'}`,
                            Math.abs(crossGap(rect, m, edge) - (protrude ? 0 : 14)) <= 1, `${crossGap(rect, m, edge)}`);
                        const w = L.workArea();
                        const reserved = {top: w.y - m.y, bottom: m.y + m.height - w.y - w.height,
                            left: w.x - m.x, right: m.x + m.width - w.x - w.width};
                        const band = A.bandGeometry(m, edge, shelf._metrics(1));
                        const others = A.EDGES.filter(e => e !== edge).map(e => reserved[e]);
                        check(`${name}: the work area reserves the band on ${edge} and nothing elsewhere`,
                            Math.abs(reserved[edge] - band.depth) <= 1 && others.every(v => v === 0),
                            JSON.stringify(reserved));
                        if (edge === 'bottom' || edge === 'left')
                            await L.shot(`mx-${combo}-${edge}`, vertical(edge)
                                ? {x: edge === 'left' ? m.x : m.x + m.width - 140, y: m.y, width: 140, height: m.height}
                                : {x: m.x, y: m.y + m.height - 140, width: m.width, height: 140});
                    }
                }
            }
        }
    }
    L.log(`matrix failures ${failures}`);
    for (const key of ['shelf-surface-mode', 'shelf-span-full', 'shelf-edge-mode', 'shelf-float-ends'])
        s.reset(key);
    await L.reset(800);
}
export function init() { L.start('matrix', work); }
export async function run() { await new Promise(() => {}); }
