// SPDX-License-Identifier: Apache-2.0
import {homeLayout} from '../../../src/luma-shell-presentation/layout.js';
import {reconcileGrid, fits} from '../../../src/luma-shell-presentation/grid.js';
function assert(ok, message) { if (!ok) throw new Error(message); }
const definitions = [{id: 'weather', kind: 'widget', w: 2, h: 2, minW: 2, minH: 2},
    ...Array.from({length: 60}, (_, i) => ({id: `app${i}`, kind: 'app', w: 1, h: 1}))];
for (const mode of ['mobile', 'tablet']) {
    for (const [width, height] of [[360, 400], [390, 844], [540, 600], [768, 1024], [1180, 820]]) {
        const automatic = homeLayout(width, height, mode, 60);
        assert(JSON.stringify(automatic) === JSON.stringify(homeLayout(width, height, mode, 60, {},
            {columns: 0, rows: 0, iconSize: 'automatic', showLabels: true})), 'default geometry changed');
        for (const columns of [3, 4, 5, 6, 12]) for (const rows of [2, 4, 6, 12]) {
            const layout = homeLayout(width, height, mode, 60, {}, {columns, rows, iconSize: 'large'});
            const available = height - layout.top - layout.bottom;
            assert(layout.columns <= columns && layout.columns >= 2, 'invalid column bound');
            assert(layout.otherRows <= rows && layout.otherRows >= 2, 'invalid row bound');
            assert(layout.contentWidth / layout.columns >= 56, 'horizontal touch targets too small');
            assert(layout.rowHeight >= 56 && layout.otherRows * layout.rowHeight <= available + 1, 'rows overlap dock');
            assert(layout.iconSize <= Math.min(layout.contentWidth / layout.columns - 12, layout.rowHeight - 24), 'icon/label overflow');
            const grid = reconcileGrid(null, definitions, layout.columns, layout.otherRows);
            assert(grid.pages.flat().length === definitions.length, 'lost an item');
            for (const page of grid.pages) for (const item of page)
                assert(fits(grid, page.filter(other => other !== item), item), 'overlap after density migration');
            const before = JSON.stringify(grid);
            const migrated = reconcileGrid(grid, definitions, automatic.columns, automatic.otherRows);
            assert(migrated.pages.flat().length === definitions.length && JSON.stringify(grid) === before,
                'migration mutated saved arrangement');
        }
        const sizes = ['small', 'medium', 'large'].map(iconSize =>
            homeLayout(width, height, mode, 60, {}, {iconSize}).iconSize);
        assert(sizes[0] <= sizes[1] && sizes[1] <= sizes[2], 'icon size choices reversed');
    }
}
console.log('PASS Home preferences: shared Mobile/Tablet geometry, default compatibility, safe density, icon sizing, complete layout migration');
