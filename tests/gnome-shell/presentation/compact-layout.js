// SPDX-License-Identifier: Apache-2.0
import {homeLayout} from '../../../src/luma-shell-presentation/layout.js';
import {reconcileGrid, overlaps} from '../../../src/luma-shell-presentation/grid.js';
import {readDisplayGeometry, readDisplayProfiles, selectDisplayProfile, displayExclusions, statusInsets, avoidDisplayExclusions, surfaceInsets} from '../../../src/luma-shell-presentation/safeAreas.js';
function assert(value, message) { if (!value) throw Error(message); }
const layout=homeLayout(360,400,'tablet',50,{cellHeight:60});
assert(layout.iconColumns===4 && layout.otherRows===4 && layout.iconSize===44,'Titan must fit four by four with cell-sized icons');
const fold=homeLayout(404,908,'mobile',50);
assert(fold.iconColumns===4 && fold.iconSize>40,'phones use four columns with icons sized to the cell');
assert(homeLayout(300,640,'mobile',50).iconColumns===3,'very narrow screens use three columns');
assert(homeLayout(390,844,'mobile',35).otherRows>4,'tall phones use their available rows');
assert(statusInsets(null,{width:1280,height:800},44,24).left===24,'unprofiled tablet keeps its original edge');
const definitions=[{id:'weather',kind:'widget',w:2,h:2},{id:'calendar',kind:'widget',w:3,h:2},
    ...Array.from({length:35},(_,id)=>({id:String(id),kind:'app',w:1,h:1}))];
const grid=reconcileGrid(null,definitions,layout.columns,layout.otherRows);
assert(grid.pages.flat().filter(item=>item.kind==='app').every(item=>item.w===1&&item.h===1),'apps must satisfy shared state contract');
assert(grid.pages[0].length===8,'two widgets plus six apps fill the first four-by-four page');
const old={columns:2,rows:2,pages:definitions.map(item=>[{...item,x:0,y:0,w:1,h:1}])};
const migrated=reconcileGrid(old,definitions,4,4);
assert(JSON.stringify(grid)===JSON.stringify(migrated),'resize must repack sparse old pages');
assert(JSON.stringify(reconcileGrid(grid,definitions,4,4))===JSON.stringify(grid),'same geometry keeps arranged positions');
for (const [width,height,metrics] of [[320,320,{}],[360,400,{}],[390,844,{}],[360,400,{cellHeight:90}],[1280,800,{}]]) {
    const l=homeLayout(width,height,'tablet',35,metrics);
    assert(l.top+l.otherRows*l.rowHeight <= height-l.bottom+8,'grid overlaps dock');
    const g=reconcileGrid(null,definitions,l.columns,l.otherRows);
    for(const page of g.pages) for(const item of page)
        assert(!page.some(other=>other!==item&&overlaps(item,other)),'overlapping home items');
}
const profile=readDisplayGeometry(JSON.stringify({schema:1,nativeWidth:1080,nativeHeight:1200,
    cutouts:[{x:0,y:0,width:132,height:132}]}));
const monitor={width:360,height:400};
assert(statusInsets(profile,monitor,44).left===52,'corner cutout clearance');
assert(statusInsets(profile,monitor,44).right===12,'opposite edge unchanged');
const center={...profile,cutouts:[{x:480,y:0,width:120,height:120}]};
assert(statusInsets(center,monitor,44).left===12,'center cutout must not shift clock');
const target=avoidDisplayExclusions(center,monitor,{x:150,y:0,width:60,height:44});
assert(!overlaps(target,displayExclusions(center,monitor)[0]),'island must avoid centered cutout');
for(let transform=0;transform<8;transform++) for(const rect of displayExclusions({...profile,transform},monitor))
    assert(rect.x>=0 && rect.y>=0 && rect.x+rect.width<=360 && rect.y+rect.height<=400,'rotated exclusion bounds');
assert(readDisplayGeometry('{bad')===null,'invalid metadata fallback');
assert(readDisplayGeometry('{"schema":1,"nativeWidth":0,"nativeHeight":100,"cutouts":[]}')===null,'zero geometry rejected');
assert(displayExclusions(null,monitor).length===0,'unprofiled screen unchanged');
console.log('PASS compact grid density, migration, text sizing, widget spans, cutout corners/center/8 transforms');

const cornerContent = surfaceInsets(profile, monitor, {x:30,y:0,width:300,height:80},14,8);
assert(cornerContent.left === 22 && cornerContent.top === 14, 'edge banner clears corner using content only');
const centerContent = surfaceInsets(center, monitor, {x:30,y:0,width:300,height:80},14,8);
assert(centerContent.left === 14 && centerContent.top === 48, 'center cutout moves banner content down');
assert(surfaceInsets(null, monitor, {x:30,y:0,width:300,height:80},14).left === 14, 'unprofiled banner keeps its padding');

const two={schema:2,displays:[{connector:'DSI-1',nativeWidth:1080,nativeHeight:2424,cutouts:[{x:0,y:0,width:104,height:104}]},
    {connector:'DSI-2',nativeWidth:2076,nativeHeight:2152,cutouts:[{x:1980,y:0,width:96,height:96}]}]};
const profiles=readDisplayProfiles(JSON.stringify(two));
assert(profiles.length===2,'schema 2 reads every display');
assert(selectDisplayProfile(profiles,'DSI-2',true,1).cutouts[0].x===1980 &&
    selectDisplayProfile(profiles,'DSI-2',true,1).transform===1,'primary connector selects its profile and transform');
assert(selectDisplayProfile(profiles,'HDMI-1',true)===null && selectDisplayProfile(profiles,'DSI-1',true).connector==='DSI-1',
    'an unprofiled primary does not drop the other profiles');
assert(selectDisplayProfile(profiles,'DSI-1',false)===null,'external displays use normal insets');
assert(readDisplayProfiles(JSON.stringify({schema:1,nativeWidth:1080,nativeHeight:1200,cutouts:[]})).length===1,'schema 1 still reads');
assert(selectDisplayProfile(readDisplayProfiles(JSON.stringify({schema:1,nativeWidth:1080,nativeHeight:1200,cutouts:[]})),'DSI-9',true)!==null,
    'a schema 1 profile without a connector matches any built-in primary');
assert(readDisplayProfiles(JSON.stringify({schema:2,displays:[two.displays[0],two.displays[0]]}))===null,'duplicate connectors rejected');
assert(readDisplayProfiles(JSON.stringify({schema:2,displays:[{...two.displays[0],connector:undefined}]}))===null,'schema 2 requires connectors');
assert(readDisplayProfiles(JSON.stringify({schema:2,displays:[]}))===null,'empty display list rejected');
const outer={width:404,height:908};
assert(statusInsets(selectDisplayProfile(profiles,'DSI-1',true,0),outer,44).left>12,'outer corner clears the clock');
console.log('PASS multi-display profiles: schema 2, per-change selection, schema 1 compatibility');

