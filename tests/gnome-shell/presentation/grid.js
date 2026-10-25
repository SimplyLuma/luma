// SPDX-License-Identifier: Apache-2.0
import {reconcileGrid, moveItem, copyGrid, overlaps, MAX_PAGES} from '../../../src/luma-shell-presentation/grid.js';
const app = id => ({id: `org.example.App${id}.desktop`, kind: 'app', w: 1, h: 1});
const widget = {id: 'weather', kind: 'widget', w: 2, h: 2};
function assert(value, message) { if (!value) throw new Error(message); }
function valid(grid, expected) {
    const all = grid.pages.flat();
    assert(all.length === expected, 'item count changed');
    assert(new Set(all.map(item => item.id)).size === expected, 'duplicate items');
    for (const page of grid.pages) {
        for (const item of page) {
            assert([item.x, item.y, item.w, item.h].every(Number.isInteger), 'fractional cell');
            assert(item.x >= 0 && item.y >= 0 && item.x + item.w <= grid.columns &&
                item.y + item.h <= grid.rows, 'out of bounds');
            assert(!page.some(other => other !== item && overlaps(other, item)), 'overlapping items');
        }
    }
}
for (const [columns, rows] of [[4, 5], [10, 5], [6, 9], [3, 2]]) {
    const definitions = [widget, ...Array.from({length: 89}, (_unused, index) => app(index))];
    let grid = reconcileGrid(null, definitions, columns, rows);
    valid(grid, 90);
    const original = JSON.stringify(grid);
    const snapshot = copyGrid(grid);
    snapshot.pages[0][0].x = 999;
    assert(JSON.stringify(grid) === original, 'cancelled draft mutated saved positions');
    let seed = 23;
    for (let step = 0; step < 200; step++) {
        seed = (seed * 16807) % 2147483647;
        const item = grid.pages.flat()[seed % 90];
        grid = moveItem(grid, item.id, seed % grid.pages.length,
            seed % (columns - item.w + 1), (seed >>> 3) % (rows - item.h + 1));
        valid(grid, 90);
    }
    const beforeInstall = copyGrid(grid);
    grid = reconcileGrid(grid, [...definitions, app(90)], columns, rows);
    valid(grid, 91);
    for (const [pageIndex, page] of beforeInstall.pages.entries()) {
        for (const item of page)
            assert(grid.pages[pageIndex].some(other => JSON.stringify(item) === JSON.stringify(other)),
                'new install moved an arranged item');
    }
    grid = reconcileGrid(grid, definitions, rows, columns);
    valid(grid, 90);
    assert(grid.pages.flat().find(item => item.id === 'weather').w === 2, 'widget span changed');
    const moved = moveItem(grid, definitions[1].id, grid.pages.length, 0, 0);
    assert(moved.pages.length === grid.pages.length + 1, 'edge drop did not create a page');
    valid(moved, 90);
    let rejected = false;
    try { moveItem(grid, 'weather', 0, rows - 1, 0); } catch { rejected = true; }
    assert(rejected, 'out of bounds widget accepted');
    print(`PASS cell grid ${columns}x${rows}: apps, widgets, collision displacement, installs, reflow, new pages`);
}
let rejected = false;
try { moveItem(reconcileGrid(null, [app(0)], 4, 5), app(0).id, MAX_PAGES, 0, 0); } catch { rejected = true; }
assert(rejected, 'unbounded page creation');

// A full final page can displace into the cell freed on an earlier page.
const full = reconcileGrid(null, Array.from({length: MAX_PAGES}, (_unused, i) => app(i)), 1, 1);
const swapped = moveItem(full, app(0).id, MAX_PAGES - 1, 0, 0);
valid(swapped, MAX_PAGES);
assert(swapped.pages[0][0].id === app(MAX_PAGES - 1).id, 'occupied drop did not use the freed cell');
print('PASS full page-limit swap uses the freed cell');
