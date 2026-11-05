// SPDX-License-Identifier: GPL-2.0-or-later
// A dock folder's count badge paints above the folder's mark: the tile builds
// mark, badge, bar in that order, and the desktop folder stack swaps its mark
// in (and the original back) below the badge, never on top of it. The stack
// sizes the badge from the dock badge model, so one digit is a circle.
// Run: gjs -m dock-folder-badge-policy.js js/ui/lumaDockFolders.js \
//        js/ui/lumaPresentation/launcherFolders.js
// (node dock-folder-badge-policy.js ... works too).
const gjs = typeof globalThis.ARGV !== 'undefined';
const args = gjs ? globalThis.ARGV : process.argv.slice(2);
const GLib = gjs ? (await import('gi://GLib')).default : null;
const fs = gjs ? null : await import('node:fs');
const read = path => (gjs ? new TextDecoder().decode(GLib.file_get_contents(path)[1])
    : fs.readFileSync(path, 'utf8'));
const print = gjs ? globalThis.print : console.log;

function assert(value, message) {
    if (!value)
        throw new Error(`FAIL: ${message}`);
}

const tile = read(args[0]);
const stack = read(args[1]);

const markAt = tile.indexOf('box.add_child(this._mark);');
const badgeAt = tile.indexOf('box.add_child(this._badge);');
assert(markAt > 0 && badgeAt > markAt, 'the tile adds its mark before its badge');

const swaps = stack.match(/tile\.child\.(add_child|insert_child_[a-z_]+)\([^)]*\)/g) ?? [];
assert(swaps.length >= 2, 'the stack swaps the mark in and back');
for (const swap of swaps)
    assert(/^tile\.child\.insert_child_below\(\w+, tile\._badge\)$/.test(swap),
        `a swapped-in mark goes below the badge: ${swap}`);

assert(/BadgeModel\.badgeBox\(\{kind: 'pill'\}/.test(stack),
    'the stack sizes the badge from the dock badge model (a digit is a circle)');
assert(!/tile\._badge\.get_preferred_width/.test(stack),
    'the stack does not size the badge from its label alone');

print('dock folder badge order and size: ok');
