// SPDX-License-Identifier: GPL-2.0-or-later
// The one placement rule for shelf surfaces (shelfSurfacePlacement in
// js/ui/shelfMetrics.js): SHELF_SURFACE_GAP beyond the island's desktop-facing
// edge, centred on the control, aligned to the island's edge when centring
// would cross the 14px inset.
// Run: gjs -m shelf-surface-placement.js path/to/shelfMetrics.js
import GLib from 'gi://GLib';
import System from 'system';
const M = await import(
    GLib.filename_to_uri(GLib.canonicalize_filename(ARGV[0], GLib.get_current_dir()), null));

let failures = 0;
const check = (name, actual, expected) => {
    const a = JSON.stringify(actual), e = JSON.stringify(expected);
    if (a !== e) {
        failures++;
        printerr(`FAIL ${name}: expected ${e}, got ${a}`);
    }
};

check('one gap', M.SHELF_SURFACE_GAP, 8);
check('no island gap constant revived', 'SHELF_ISLAND_GAP' in M, false);
check('no separate preview rule', 'previewPlacement' in M, false);

const monitor = {x: 0, y: 0, width: 2560, height: 1440};
const size = {width: 236, height: 48};

// Bottom: 8 above the island's top edge, whatever the control's size.
const island = {x: 1202, y: 1374, width: 265, height: 52};
const small = {x: 1210, y: 1386, width: 28, height: 28};
const large = {x: 1210, y: 1379, width: 42, height: 42};
for (const scale of [1, 2]) {
    const i = {x: island.x, y: island.y, width: island.width, height: island.height * scale};
    const a = M.shelfSurfacePlacement('bottom', small, i, size, monitor, scale);
    const b = M.shelfSurfacePlacement('bottom', large, i, size, monitor, scale);
    check(`bottom ${scale}x gap`, i.y - (a.y + size.height), 8 * scale);
    check(`bottom ${scale}x same place for any control height`, a.y, b.y);
}
let r = M.shelfSurfacePlacement('bottom', small, island, size, monitor, 1);
check('bottom centred on the control', r.x + size.width / 2, small.x + small.width / 2);

// Top, left, right: the desktop-facing edge.
r = M.shelfSurfacePlacement('top', {x: 1210, y: 14, width: 28, height: 28}, {x: 1202, y: 8, width: 265, height: 52}, size, monitor, 1);
check('top gap', r.y - (8 + 52), 8);
r = M.shelfSurfacePlacement('left', {x: 20, y: 700, width: 36, height: 36}, {x: 14, y: 600, width: 52, height: 300}, size, monitor, 1);
check('left gap', r.x - (14 + 52), 8);
check('left centred', r.y + size.height / 2, 718);
r = M.shelfSurfacePlacement('right', {x: 2504, y: 700, width: 36, height: 36}, {x: 2494, y: 600, width: 52, height: 300}, size, monitor, 2);
check('right gap at 2x', 2494 - (r.x + size.width), 16);

// Near a screen edge: aligned to the island's edge, not clamped to the monitor.
const edgeIsland = {x: 2281, y: 1374, width: 265, height: 52};
const edgeControl = {x: 2500, y: 1382, width: 36, height: 36};
r = M.shelfSurfacePlacement('bottom', edgeControl, edgeIsland, size, monitor, 1);
check('right end aligned to the island', r.x + size.width, edgeIsland.x + edgeIsland.width);
check('right end inside the inset', monitor.width - (r.x + size.width) >= 14, true);
const startIsland = {x: 14, y: 1374, width: 101, height: 52};
r = M.shelfSurfacePlacement('bottom', {x: 22, y: 1382, width: 36, height: 36}, startIsland, size, monitor, 1);
check('left end aligned to the island', r.x, startIsland.x);
const tall = {width: 300, height: 400};
r = M.shelfSurfacePlacement('right', {x: 2504, y: 40, width: 36, height: 36}, {x: 2494, y: 14, width: 52, height: 900}, tall, monitor, 1);
check('vertical start aligned to the island', r.y, 14);

// A second monitor that does not start at 0.
const second = {x: 2560, y: 200, width: 1920, height: 1080};
r = M.shelfSurfacePlacement('bottom', {x: 3500, y: 1214, width: 36, height: 36}, {x: 3400, y: 1206, width: 300, height: 52}, size, second, 1);
check('second monitor gap', 1206 - (r.y + size.height), 8);
check('second monitor centred', r.x + size.width / 2, 3518);

if (failures) {
    printerr(`shelf-surface-placement: ${failures} failure(s)`);
    System.exit(1);
}
print('shelf-surface-placement: PASS');
