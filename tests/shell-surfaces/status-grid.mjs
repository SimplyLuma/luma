import assert from 'node:assert/strict';
import {statusGrid} from '../../src/luma-shell-presentation/statusGrid.js';
for (const count of [0,1,2,3,4,5,6,7,8,9,12,100]) {
    const grid = statusGrid(count);
    assert.equal(grid.cells.length, Math.min(count,8));
    assert.ok(grid.columns <= 4);
    assert.ok(grid.rows <= 2);
    assert.equal(grid.cells.filter(cell=>cell.more).length,count>8?1:0);
    if(count>8) assert.equal(grid.overflow,count-7);
    if(count<=3) assert.equal(grid.rows,1);
    for(let row=0;row<grid.rows;row++) {
        const cells=grid.cells.filter(cell=>cell.row===row);
        if(!cells.length) continue;
        // Equal clearance on each side, even when a row is shorter.
        assert.equal(cells[0].column,grid.columns-1-cells.at(-1).column);
        for(let i=1;i<cells.length;i++) assert.equal(cells[i].column-cells[i-1].column,1);
    }
}
console.log('Status grid: compact counts, balanced rows and overflow passed');

// Narrow rails must fit every real status count without clipping a third column.
for (let count = 0; count <= 12; count++) {
    const grid = statusGrid(count, 2);
    assert.ok(grid.columns <= 2);
    assert.equal(grid.cells.length, Math.min(count, 8));
    for (const cell of grid.cells) {
        assert.ok(cell.column >= 0 && cell.column < 2);
        assert.ok(cell.row >= 0 && cell.row < grid.rows);
    }
}
console.log('Status grid: vertical rail counts and overflow passed');
