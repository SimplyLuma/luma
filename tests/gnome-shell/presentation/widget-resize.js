// SPDX-License-Identifier: Apache-2.0
import {reconcileGrid, resizeItem, resizeCell, findItem, overlaps, MAX_PAGES} from '../../../src/luma-shell-presentation/grid.js';
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const widget = (id, w = 2, h = 2) => ({id, kind: 'widget', w, h, minW: 2, minH: 2});
const apps = count => Array.from({length: count}, (_, i) => ({id: `org.example.App${i}.desktop`, kind: 'app', w: 1, h: 1}));
function valid(grid, ids) {
    const items = grid.pages.flat();
    assert(items.length === ids.length && new Set(items.map(i => i.id)).size === ids.length, 'lost or duplicated an item');
    assert(ids.every(id => items.some(i => i.id === id)), 'changed an identity');
    for (const page of grid.pages) for (const item of page) {
        assert(Object.keys(item).sort().join() === 'h,id,kind,w,x,y', 'leaked provider metadata into saved state');
        assert([item.x, item.y, item.w, item.h].every(Number.isInteger), 'fractional cells');
        assert(item.x >= 0 && item.y >= 0 && item.w > 0 && item.h > 0 && item.x + item.w <= grid.columns && item.y + item.h <= grid.rows, 'out of bounds');
        assert(!page.some(other => other !== item && overlaps(item, other)), 'overlap');
        if (item.kind === 'app') assert(item.w === 1 && item.h === 1, 'resized an app icon');
    }
}
for (const [columns, rows] of [[5, 3], [5, 4], [4, 8], [8, 6], [3, 2]]) {
    const definitions = [widget('weather'), widget('calendar'), ...apps(42)];
    const original = reconcileGrid(null, definitions, columns, rows);
    const before = JSON.stringify(original), ids = definitions.map(i => i.id);
    valid(original, ids);
    for (let w = 2; w <= columns; w++) for (let h = 2; h <= rows; h++) {
        const next = resizeItem(original, 'weather', w, h, {minW: 2, minH: 2});
        valid(next, ids);
        assert(JSON.stringify(original) === before, 'preview mutated saved layout');
        assert(findItem(next, 'calendar').w === 2 && findItem(next, 'calendar').h === 2, 'squashed another widget');
        const restored = reconcileGrid(next, definitions, columns, rows);
        assert(findItem(restored, 'weather').w === w && findItem(restored, 'weather').h === h, 'rebuild forgot resized dimensions');
        valid(restored, ids);
        const migrated = reconcileGrid(next, definitions, rows, columns);
        valid(migrated, ids);
    }
}
const definitions = [widget('weather'), widget('calendar'), ...apps(7)];
const fullPage = reconcileGrid(null, definitions, 5, 3);
const grown = resizeItem(fullPage, 'weather', 3, 2);
assert(findItem(grown, 'calendar').page === 0, 'large neighbour should push icons aside before leaving the page');
assert(grown.pages[1].length === 2 && grown.pages[1].every(i => i.kind === 'app'), 'growth should overflow only the two extra icon cells');
assert(JSON.stringify(resizeItem(fullPage, 'weather', 3, 2)) === JSON.stringify(grown), 'reflow is not deterministic');
const grownAgain = resizeItem(grown, 'weather', 4, 2);
valid(grownAgain, definitions.map(i => i.id));
const tiny = reconcileGrid(grownAgain, definitions, 1, 1);
valid(tiny, definitions.map(i => i.id));
const full = {columns: 2, rows: 2, pages: Array.from({length: MAX_PAGES}, (_, page) =>
    page === 0 ? [{id: 'w', kind: 'widget', x: 0, y: 0, w: 1, h: 1},
        ...[[1,0],[0,1],[1,1]].map(([x,y],i) => ({id:`p0-${i}`,kind:'app',x,y,w:1,h:1}))]
        : Array.from({length: 4}, (_, i) => ({id:`p${page}-${i}`,kind:'app',x:i%2,y:Math.floor(i/2),w:1,h:1})))};
const before = JSON.stringify(full);
let refused = false;
try { resizeItem(full, 'w', 2, 2); } catch { refused = true; }
assert(refused && JSON.stringify(full) === before, 'page-limit failure must be atomic');
for (const [id, w, h, limits] of [['weather', 1.5, 2, {}], ['weather', 0, 2, {}],
    ['weather', 6, 2, {}], ['weather', 1, 2, {minW:2}], ['weather', 3, 2, {maxW:2}],
    ['missing', 2, 2, {}], ['org.example.App0.desktop', 2, 2, {}]]) {
    let rejected = false;
    try { resizeItem(fullPage, id, w, h, limits); } catch { rejected = true; }
    assert(rejected, `accepted invalid resize ${id} ${w}x${h}`);
}
assert(resizeCell(2, 55, 100, 2, 5) === 2, 'boundary should hold');
assert(resizeCell(2, 65, 100, 2, 5) === 3, 'crossed boundary should grow');
assert(resizeCell(2, 50, 100, 2, 5, 3) === 3, 'small reversal should not jitter');
assert(resizeCell(2, 30, 100, 2, 5, 3) === 2, 'deliberate reversal should shrink');
console.log('PASS widget resize: whole cells, preserved spans, nearby reflow, overflow, persistence, migration, cancellation safety, page-limit rollback, hysteresis');
