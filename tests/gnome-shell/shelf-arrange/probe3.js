// SPDX-License-Identifier: GPL-2.0-or-later
// Three monitors side by side: every monitor's work area, and Tiling Shell's.
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';
async function work() {
    await L.waitForShelf();
    for (let i = 0; i < 40 && Main.layoutManager.monitors.length < 3; i++)
        await L.sleep(250);
    await L.sleep(1500);
    for (const m of Main.layoutManager.monitors)
        L.log(`monitor ${m.index} ${m.x},${m.y} ${m.width}x${m.height} work ${JSON.stringify(L.workArea(m.index))}`);
    L.log(`inset: ${global.display.work_area_inset ?? '?'}`);
}
export function init() { L.start('probe3', work); }
export async function run() { await new Promise(() => {}); }
