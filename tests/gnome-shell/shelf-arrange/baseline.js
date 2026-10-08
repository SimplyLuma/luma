// SPDX-License-Identifier: GPL-2.0-or-later
// A plain screenshot of the shelf, for comparing a build with and without
// the arrangement patches (the default layout must not change).
import * as L from './lib.js';
async function work() {
    await L.sleep(9000);
    L.notify();
    await L.sleep(2000);
    await L.shot('baseline');
    await L.shot('baseline-shelf', {x: 0, y: global.stage.height - 110, width: global.stage.width, height: 110});
}
export function init() { L.start('baseline', work, 2000); }
export async function run() { await new Promise(() => {}); }
