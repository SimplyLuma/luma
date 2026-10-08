// SPDX-License-Identifier: GPL-2.0-or-later
// Geometry of the window travel to and from the dock (js/ui/lumaWindowPath.js).
// Run: gjs -m window-motion.js path/to/lumaWindowPath.js
import GLib from 'gi://GLib';
import System from 'system';
const M = await import(GLib.filename_to_uri(GLib.canonicalize_filename(ARGV[0], GLib.get_current_dir()), null));

let failures = 0;
const check = (name, ok, detail = '') => {
    if (!ok) {
        failures++;
        printerr(`FAIL ${name} ${detail}`);
    }
};
const near = (a, b, e = 0.5) => Math.abs(a - b) <= e;
const win = {x: 400, y: 200, width: 1200, height: 800};
const icon = {x: 1100, y: 1382, width: 36, height: 36};

for (const edge of ['bottom', 'top', 'left', 'right']) {
    const a = M.travelFrame(0, win, icon, edge), b = M.travelFrame(1, win, icon, edge);
    check(`${edge} starts at the window`, near(a.x, win.x) && near(a.y, win.y) && near(a.width, win.width) && near(a.height, win.height) && a.opacity === 255);
    check(`${edge} ends at the icon`, near(b.x, icon.x) && near(b.y, icon.y) && near(b.width, icon.width) && near(b.height, icon.height) && b.opacity === 0);
}
// Bottom shelf: along the shelf (width, x) leads; toward it (height, y) follows.
const mid = M.travelFrame(0.4, win, icon, 'bottom');
const shareW = (win.width - mid.width) / (win.width - icon.width);
const shareH = (win.height - mid.height) / (win.height - icon.height);
check('bottom: narrows before it drops', shareW > shareH + 0.2, `${shareW} ${shareH}`);
const midLeft = M.travelFrame(0.4, win, {x: 14, y: 700, width: 36, height: 36}, 'left');
const lw = (win.width - midLeft.width) / (win.width - 36), lh = (win.height - midLeft.height) / (win.height - 36);
check('left: shortens before it moves across', lh > lw + 0.2, `${lw} ${lh}`);
// Continuous: no frame jumps more than a small step between samples.
let previous = M.travelFrame(0, win, icon, 'bottom');
let biggest = 0;
for (let i = 1; i <= 100; i++) {
    const frame = M.travelFrame(i / 100, win, icon, 'bottom');
    biggest = Math.max(biggest, Math.abs(frame.x - previous.x), Math.abs(frame.y - previous.y), Math.abs(frame.width - previous.width));
    previous = frame;
}
check('continuous at 100 samples', biggest < 60, `${biggest}`);
// Monotonic size: the window only ever gets smaller on the way in.
let w = Infinity, h = Infinity, mono = true;
for (let i = 0; i <= 100; i++) {
    const frame = M.travelFrame(i / 100, win, icon, 'right');
    mono &&= frame.width <= w + 1e-9 && frame.height <= h + 1e-9;
    w = frame.width; h = frame.height;
}
check('size shrinks monotonically', mono);
check('edge of a bottom icon', M.edgeOf(icon, {x: 0, y: 0, width: 2560, height: 1440}) === 'bottom');
check('edge of a left icon', M.edgeOf({x: 20, y: 700, width: 36, height: 36}, {x: 0, y: 0, width: 2560, height: 1440}) === 'left');
check('edge on a second monitor', M.edgeOf({x: 2560 + 1900, y: 500, width: 36, height: 36}, {x: 2560, y: 0, width: 1920, height: 1080}) === 'right');

if (failures) {
    printerr(`window-motion: ${failures} failure(s)`);
    System.exit(1);
}
print('window-motion: PASS');
