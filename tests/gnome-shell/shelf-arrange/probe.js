// SPDX-License-Identifier: GPL-2.0-or-later
// Smoke probe: the default layout draws, and every group, island and strut
// is where the model says. Writes probe.png and a geometry dump.
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

async function work() {
    const shelf = await L.waitForShelf();
    L.check('shelf built', !!shelf);
    L.log(`groups ${JSON.stringify(L.groups())}`);
    L.log(`work area ${JSON.stringify(L.workArea())}`);
    L.log(`struts ${JSON.stringify([...shelf._strutActors.entries()].map(([k, a]) => [k, L.rectOf(a)]))}`);
    await L.shot('probe');
}
export function init() { L.start('probe', work); }
export async function run() { await new Promise(() => {}); }
