// SPDX-License-Identifier: Apache-2.0
// The TV home's pure rules: opt-in, search, the tile grid, spatial focus, keys.
import assert from 'node:assert/strict';
import {tv} from '../../src/luma-shell-presentation/tvTokens.js';
import {nearest, remoteAction, repeatAllowed, searchTvApps, tileGrid, tvApps, tvEntry, tvUnit, weatherGlyph, greetingPart}
    from '../../src/luma-shell-presentation/tvModel.js';

// One unit is a pixel of 1920x1080, whatever the screen.
assert.equal(tvUnit(1920, 1080, tv.layout), 1);
assert.equal(tvUnit(3840, 2160, tv.layout), 2);
assert.equal(tvUnit(1280, 720, tv.layout), 2 / 3);
assert.equal(tvUnit(1920, 1200, tv.layout), 1);       // 16:10: the width decides
assert.equal(tvUnit(2560, 1080, tv.layout), 1);       // ultrawide: the height decides
assert.equal(tvUnit(0, 1080, tv.layout), 0);

// The shared bar: 96 in, 56 down, 74 tall.
assert.deepEqual([tv.layout.safe_x, tv.bar.top, tv.bar.height], [96, 56, 74]);
assert.ok(/^rgba\(/.test(tv.materials.well));

// Only apps that declare X-Luma-TV=true appear.
const keys = values => key => values[key] ?? null;
assert.equal(tvEntry('a.desktop', 'A', keys({})), null);
assert.equal(tvEntry('a.desktop', 'A', keys({'X-Luma-TV': 'false'})), null);
assert.deepEqual(tvEntry('a.desktop', 'A', keys({'X-Luma-TV': 'true', 'X-Luma-TV-Tile': 'org.a.A-tv'})),
    {id: 'a.desktop', name: 'A', tile: 'org.a.A-tv', presentation: 'fullscreen'});
assert.equal(tvEntry('a.desktop', 'A', keys({'X-Luma-TV': 'true', 'X-Luma-TV-Tile': '../x'})).tile, '');
const apps = tvApps([tvEntry('t.desktop', 'Tide', keys({'X-Luma-TV': 'true'})), null,
    tvEntry('w.desktop', 'Weather', keys({'X-Luma-TV': 'true'})),
    tvEntry('t.desktop', 'Tide', keys({'X-Luma-TV': 'true'}))]);
assert.deepEqual(apps.map(app => app.name), ['Tide', 'Weather']);
assert.deepEqual(searchTvApps(apps, 'wea').map(app => app.name), ['Weather']);
assert.deepEqual(searchTvApps(apps, '  ').map(app => app.name), []);
assert.deepEqual(searchTvApps(apps, 'e').map(app => app.name), ['Weather', 'Tide']);

// Five 304 tiles across the 1920 safe area, edge to edge, 52 between rows.
const grid = tileGrid(7, 1, 1920, tv);
assert.equal(grid.columns, 5);
assert.equal(grid.cells[0].x, 96);
assert.equal(grid.cells[4].x + grid.cells[4].width, 1920 - 96);
assert.equal(grid.width, 304);
assert.equal(Math.round(grid.height), 171);
assert.equal(grid.cells[5].row, 1);
assert.equal(grid.cells[5].y - grid.cells[0].y, grid.rowHeight + 52);
// The same layout scaled at 4K, and a 4:3 screen keeps the safe margins.
const big = tileGrid(5, 2, 3840, tv);
assert.equal(big.cells[4].x + big.cells[4].width, 3840 - 192);
const square = tileGrid(5, 1024 / 1920, 1024, tv);
assert.ok(square.columns >= 1 && square.cells[0].x === 96 * 1024 / 1920);

// Spatial focus: the nearest thing in the pressed direction.
const items = grid.cells;
assert.equal(nearest(items, items[0], 'right'), items[1]);
assert.equal(nearest(items, items[0], 'left'), null);
assert.equal(nearest(items, items[1], 'down'), items[6]);
assert.equal(nearest(items, items[3], 'down'), items[6]);
assert.equal(nearest(items, items[6], 'up'), items[1]);
assert.equal(nearest(items, items[0], 'up'), null);

// Remote keys.
assert.equal(remoteAction('Return'), 'ok');
// Remote and gamepad keys reach the TV home as these (tvInput.js).
assert.equal(remoteAction('XF86Back'), null);
assert.equal(remoteAction('XF86OK'), null);
assert.equal(remoteAction('Escape'), 'back');
assert.equal(remoteAction('Left'), 'left');
assert.equal(remoteAction('a'), null);

assert.equal(weatherGlyph('Clear', 13).tint, 'sun');
assert.equal(weatherGlyph('Clear', 23).icon, 'lumaui-moon-symbolic');
assert.equal(weatherGlyph('Rain', 13).tint, null);
assert.equal(greetingPart(9), 'morning');
assert.equal(greetingPart(15), 'afternoon');
assert.equal(greetingPart(20), 'evening');
// A held key: only the arrows repeat.
assert.ok(['up', 'down', 'left', 'right'].every(repeatAllowed));
assert.ok(!repeatAllowed('ok') && !repeatAllowed('back') && !repeatAllowed('menu'));
console.log('tv-model: ok');
