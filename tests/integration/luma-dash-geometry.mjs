import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
if (!process.argv[2]) throw new Error('Usage: node luma-dash-geometry.mjs PATH_TO_PATCHED_SHELF_JS');
const source = fs.readFileSync(process.argv[2], 'utf8');
const geometry = source.slice(source.indexOf('export function dashGeometry'), source.indexOf('// Corner order'));
let cases=0;
for (const tile of [36,44]) for (const scale of [1,2]) for (const padding of [4,12,24]) {
 const context=vm.createContext({DASH_TILE_SIZE:tile,DASH_PADDING:10});
 vm.runInContext(geometry.replaceAll('export ',''),context);
 const monitor={x:-1920,y:120,width:1920,height:1080};
 for(const edge of ['top','bottom','left','right']) for(const attached of [false,true]) for(const span of [false,true]) for(const ends of [false,true]) {
  const g=context.dashGeometry(monitor,edge,attached,span,ends,400,scale,padding);
  const r=g.workArea;const vertical=['left','right'].includes(edge);
  assert.equal(vertical?g.width:g.height,(tile+2*padding)*scale);
  assert.equal(vertical?r.height:r.width,vertical?monitor.height:monitor.width);
  assert(r.x>=monitor.x&&r.y>=monitor.y&&r.x+r.width<=monitor.x+monitor.width&&r.y+r.height<=monitor.y+monitor.height);
  assert.equal(({top:r.y,left:r.x,bottom:r.y+r.height,right:r.x+r.width})[edge],({top:monitor.y,left:monitor.x,bottom:monitor.y+monitor.height,right:monitor.x+monitor.width})[edge]);
  assert.equal(Math.abs(({top:r.y+r.height,left:r.x+r.width,bottom:r.y,right:r.x})[edge]-({top:g.y+g.height,left:g.x+g.width,bottom:g.y,right:g.x})[edge]), attached ? 0 : padding * scale);
  cases++;
 }
}
console.log(`PASS ${cases} offset-monitor geometry cases: all edges/shapes, padding4/12/24, scale1/2, tile36/44, edge reservation retaining the matching inner Dash gap`);

const start = source.indexOf('    setSurfaceVisible(');
const end = source.indexOf('\n    // Constraint-backed', start);
const method = source.slice(start, end).trim();
const policy = vm.runInNewContext(`({${method}})`);
for (const protrude of [false, true])
  for (const span of [false, true])
    for (const connected of [false, true]) {
      for (const isRoot of [false, true]) {
        const actor = {_shadows: [{}, {}, {}], _stroke: {}, _materialBinding: {setEnabled(value) { this.enabled = value; }}};
        const visible = isRoot === connected;
        policy.setSurfaceVisible.call(actor, visible, !protrude && !span);
        assert.equal(actor._stroke.visible, visible);
        assert.equal(actor._materialBinding.enabled, visible);
        for (const shadow of actor._shadows)
          assert.equal(shadow.visible, visible && !protrude && !span);
      }
    }
console.log('PASS16 decoration policies: attached OR spanning removes shadows, retains owning stroke');
