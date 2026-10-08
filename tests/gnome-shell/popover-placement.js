// SPDX-License-Identifier: GPL-2.0-or-later
// Placement checks for Shelf popovers (js/ui/lumaPopoverPlacement.js).
// Run: gjs -m popover-placement.js path/to/lumaPopoverPlacement.js
import GLib from 'gi://GLib';
import System from 'system';
const P = await import(
    GLib.filename_to_uri(GLib.canonicalize_filename(ARGV[0], GLib.get_current_dir()), null));

let failures = 0;
const check = (name, actual, expected) => {
    const a = JSON.stringify(actual), e = JSON.stringify(expected);
    if (a !== e) {
        failures++;
        printerr(`FAIL ${name}: expected ${e}, got ${a}`);
    }
};

// The owner's displays: a 5120x1440 ultrawide at (1536,0), the laptop panel
// below-left at 1.25.
const wide = {x: 1536, y: 0, width: 5120, height: 1440};
const laptop = {x: 0, y: 695, width: 1920, height: 1200};
const bottom = {top: 0, bottom: 80, left: 0, right: 0};
const size = {width: 344, height: 600};
const base = {gap: 8, lift: 8, inset: 14, size};

check('edge bottom', P.shelfEdge(bottom), 'bottom');
check('edge none reads bottom', P.shelfEdge({top: 0, bottom: 0, left: 0, right: 0}), 'bottom');
check('edge left', P.shelfEdge({top: 0, bottom: 0, left: 70, right: 0}), 'left');

// Above the item, centred on it.
const beacon = {x: 5000, y: 1374, width: 180, height: 36};
let r = P.placePopover({...base, anchor: beacon, monitor: wide, reserve: bottom});
check('centred on the beacon', r.x + size.width / 2, beacon.x + beacon.width / 2);
check('8 above its island', r.y + size.height, beacon.y - 8 - 8);
check('fits above', r.maxHeight, beacon.y - 16 - 14);

// Near the right edge it is clamped on-screen, not pushed off it.
const edgeBeacon = {x: 6560, y: 1374, width: 80, height: 36};
r = P.placePopover({...base, anchor: edgeBeacon, monitor: wide, reserve: bottom});
check('clamped to the right edge', r.x, wide.x + wide.width - 14 - size.width);
// And on the left edge of a monitor that does not start at 0.
const leftBeacon = {x: 1540, y: 1374, width: 40, height: 36};
r = P.placePopover({...base, anchor: leftBeacon, monitor: wide, reserve: bottom});
check('clamped to the left edge', r.x, wide.x + 14);

// The laptop panel below and left of the ultrawide.
const lapBeacon = {x: 900, y: 1829, width: 120, height: 36};
r = P.placePopover({...base, anchor: lapBeacon, monitor: laptop, reserve: bottom});
check('laptop centred', r.x + size.width / 2, 960);
check('laptop above', r.y + size.height, 1829 - 16);
check('laptop stays on its monitor', r.y >= laptop.y + 14, true);

// A tall list is limited by the room above the item.
const tall = P.placePopover({...base, size: {width: 344, height: 5000}, anchor: beacon, monitor: wide, reserve: bottom});
check('tall list clamps height and stays on screen', tall.y, 14);

// A Shelf on the top edge opens below; on a side edge, beside.
r = P.placePopover({...base, anchor: {x: 3000, y: 22, width: 60, height: 36}, monitor: wide,
    reserve: {top: 80, bottom: 0, left: 0, right: 0}});
check('top shelf opens below', r.y, 22 + 36 + 16);
r = P.placePopover({...base, anchor: {x: 1558, y: 700, width: 36, height: 60}, monitor: wide,
    reserve: {top: 0, bottom: 0, left: 80, right: 0}});
check('left shelf opens to the right', r.x, 1558 + 36 + 16);
check('left shelf centred vertically', r.y + size.height / 2, 730);
r = P.placePopover({...base, anchor: {x: 6600, y: 1380, width: 36, height: 50}, monitor: wide,
    reserve: {top: 0, bottom: 0, left: 0, right: 80}});
check('right shelf near the bottom clamps', r.y + size.height, 1440 - 14);

if (failures) {
    printerr(`popover-placement: ${failures} failure(s)`);
    System.exit(1);
}
print('popover-placement: PASS');
