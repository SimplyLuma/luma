import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(process.argv[2],'utf8');
const fn=source.slice(source.indexOf('export function dashGeometry'),source.indexOf('// Corner order'));
const ctx=vm.createContext({DASH_TILE_SIZE:36,DASH_PADDING:10});
vm.runInContext(fn.replace('export ',''),ctx);
let count=0;
for(const scale of [1,1.25,1.5,2]) for(const padding of [4,10,24])
for(const edge of ['top','bottom','left','right']) for(const attached of [false,true])
for(const span of [false,true]) for(const ends of [false,true]) {
 const m={x:-1920,y:120,width:1920,height:1080};
 const g=ctx.dashGeometry(m,edge,attached,span,ends,400,scale,padding);
 const gap=attached?0:padding*scale;
 const inner={top:g.y+g.height,bottom:g.y,left:g.x+g.width,right:g.x};
 const r=g.workArea;
 const boundary={top:r.y+r.height,bottom:r.y,left:r.x+r.width,right:r.x};
 assert.equal(Math.abs(inner[edge]-boundary[edge]),gap);
 assert.equal(g.edgeInsets.length,3);
 for(const a of [r,...g.edgeInsets]) {
  assert(a.x>=m.x && a.y>=m.y);
  assert(a.x+a.width<=m.x+m.width && a.y+a.height<=m.y+m.height);
 }
 for(const a of g.edgeInsets) assert.equal(Math.min(a.width,a.height),gap);
 count++;
}
console.log(`PASS ${count} edge-gutter cases: all edges, floating/attached, span, fractional scale and offset monitor`);
