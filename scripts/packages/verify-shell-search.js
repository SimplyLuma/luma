// SPDX-License-Identifier: Apache-2.0
// Exercise the built search event handler against painted and transparent areas.
const [ok, bytes] = imports.gi.GLib.file_get_contents(ARGV[0]);
if (!ok)
    throw new Error('Cannot read built search source');
const source = new TextDecoder().decode(bytes);
const body = source.match(/this.connect\('captured-event', \(_actor, event\) => \{([\s\S]*?)\n        \}\);/)?.[1];
if (!body)
    throw new Error('Missing search capture handler');
const Clutter = {EventType: {BUTTON_PRESS: 1, TOUCH_BEGIN: 2}, EVENT_PROPAGATE: 0, EVENT_STOP: 1};
const handler = new Function('_actor', 'event', 'Clutter', body);
const surface = (x, y, w, h, mapped = true) => ({mapped,
    get_transformed_position: () => [x, y], get_transformed_size: () => [w, h]});
const dialog = {_fieldIsland: surface(100, 100, 400, 60),
    _intro: surface(100, 180, 400, 100), _results: surface(100, 180, 400, 300, false),
    closed: false, close() { this.closed = true; }};
const cases = [[110,110,false], [110,170,true], [110,190,false],
    [110,300,true], [99,110,true], [500,110,true], [-300,120,true]];
for (const type of [1, 2]) {
    for (const [x,y,dismiss] of cases) {
        dialog.closed = false;
        const result = handler.call(dialog, null, {type: () => type, get_coords: () => [x,y]}, Clutter);
        if (dialog.closed !== dismiss || result !== Number(dismiss))
            throw new Error(`Search dismissal failed: ${type} at ${x},${y}`);
    }
}
dialog._intro.mapped = false;
dialog._results.mapped = true;
dialog.closed = false;
handler.call(dialog, null, {type: () => 1, get_coords: () => [110,300]}, Clutter);
if (dialog.closed)
    throw new Error('Visible results must retain clicks');
dialog.closed = false;
handler.call(dialog, null, {type: () => 3, get_coords: () => [0,0]}, Clutter);
if (dialog.closed)
    throw new Error('Pointer movement must not dismiss search');
print('Built search dismissal: 16 cases PASS');
