import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const s=fs.readFileSync(process.argv[2],'utf8');
const a=s.indexOf('    setShelfEdge(edge) {'), b=s.indexOf('    setFixedIconSize',a);
const ctx=vm.createContext({Clutter:{OffscreenRedirect:{NEVER:0,ALWAYS:1},Orientation:{VERTICAL:0,HORIZONTAL:1},ActorAlign:{FILL:0,CENTER:1}},St:{ThemeContext:{get_for_stage:()=>({scale_factor:1})}},global:{stage:{}},DASH_TILE_GAP:4});
vm.runInContext('f=function '+s.slice(a,b).trim(),ctx);
for(const edge of ['bottom','top','left','right',null]) {
 const actor={_box:{clip_to_allocation:true,layout_manager:{},get_children:()=>[]},_dashContainer:{},set_offscreen_redirect(){},_clearDragPlaceholder(){}};
 ctx.f.call(actor,edge);assert.equal(actor._box.clip_to_allocation,!edge);
}
console.log('PASS: all dock orientations relinquish inner clip; overview restores it');
