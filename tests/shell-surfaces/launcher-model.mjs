import assert from 'node:assert/strict';
import {catalogPage, appCells, folderWindow} from '../../src/luma-shell-presentation/launcherModel.js';
const apps = Array.from({length:61}, (_,i)=>({name:`App ${String(i).padStart(2,'0')}`}));
assert.equal(catalogPage(apps,'',2).items.length,5);
assert.equal(catalogPage(apps,'',99).page,2);
assert.equal(catalogPage(apps,' APP 0 ',2).matches.length,10);
assert.equal(catalogPage(apps,'missing').matches.length,0);
for(const n of [1,2,7]) {
 const cells=appCells(n,980,488,true);
 assert.equal(cells[0].x,980-cells.at(-1).x-cells.at(-1).width);
 assert.equal(cells[0].y,(488-116)/2);
}
const cells=appCells(28,980,488);
assert.equal(cells[7].x,0);assert.equal(cells[7].y,122);
assert.equal(cells.at(-1).y+cells.at(-1).height,482);
for(const count of [0,1,8,512]) for(const value of [0,116,10000]) {
 const window=folderWindow(Math.min(value,Math.max(0,count*116-632)),632,count);
 assert.ok(window.start>=0 && window.end<=count && window.start<=window.end);
 assert.ok(window.end-window.start<=11,'bounded visible actors, independent of total file count');
}
console.log('Launcher model: paging, search, centered results and bounded folder actors passed');
