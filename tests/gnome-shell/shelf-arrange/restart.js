// SPDX-License-Identifier: GPL-2.0-or-later
// Persistence across a Shell restart: the arrangement keyboard.js left, read
// back from settings by a fresh Shell, draws the same groups at the same
// places with the same work area.
import GLib from 'gi://GLib';
import * as L from './lib.js';

async function work() {
    await L.waitForShelf();
    // The notification keyboard.js posted, again, so the same islands show.
    L.notify('Build finished', 'Shell .115 is ready');
    await L.sleep(2500);
    const [, bytes] = GLib.file_get_contents(`${L.OUT}/before-restart.json`);
    const before = JSON.parse(new TextDecoder().decode(bytes));
    const now = {stored: L.stored(), groups: L.groups().map(x => ({edge: x.edge, anchor: x.anchor,
        islands: x.islands.map(i => [i.id, i.rect])})), work: L.workArea()};
    L.check('the stored arrangement survived the restart', JSON.stringify(now.stored) === JSON.stringify(before.stored));
    L.check('the same groups are drawn', JSON.stringify(now.groups.map(g => [g.edge, g.anchor, g.islands.map(i => i[0])])) ===
        JSON.stringify(before.groups.map(g => [g.edge, g.anchor, g.islands.map(i => i[0])])),
        JSON.stringify(now.groups.map(g => [g.edge, g.anchor, g.islands.map(i => i[0])])));
    const moved = [];
    before.groups.forEach((g, i) => g.islands.forEach(([id, r], j) => {
        const n = now.groups[i]?.islands[j]?.[1];
        if (!n || Math.abs(n.x - r.x) > 1 || Math.abs(n.y - r.y) > 1 || Math.abs(n.width - r.width) > 1)
            moved.push([id, r, n]);
    }));
    L.check('every island at the same place', moved.length === 0, JSON.stringify(moved));
    L.check('the same work area', JSON.stringify(now.work) === JSON.stringify(before.work), JSON.stringify(now.work));
    await L.shot('r-after-restart');
}
export function init() { L.start('restart', work); }
export async function run() { await new Promise(() => {}); }
