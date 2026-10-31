// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../../../src/luma-shell-presentation/tabletTasks.js',import.meta.url),'utf8');
const hooks=new Map(), keys=[];
const gesture={set_begin_threshold(n){this.threshold=n;},connect(name,callback){hooks.set(name,callback);}};
const keyboard={notify_keyval(...args){keys.push(args.slice(1));},run_dispose(){this.disposed=true;}};
let android=false;
const window={get_wm_class:()=>android?'waydroid.app':'org.projectluma.Tide',get_title:()=>''};
const stage={add_action_full(_name,_phase,g){this.action=g;},remove_action(g){assert.equal(g,this.action);this.removed=true;},
 context:{get_backend:()=>({get_default_seat:()=>({create_virtual_device:()=>keyboard})})}};
const global={stage,display:{focus_window:window}};
const Main={actionMode:1,sessionMode:{isLocked:false,isGreeter:false}};
const context=vm.createContext({global,Main,GLib:{get_monotonic_time:()=>1},
 Shell:{ActionMode:{NORMAL:1},WindowTracker:{get_default:()=>({get_window_app:()=>({get_id:()=>android?'waydroid.app.desktop':'org.projectluma.Tide.desktop'})})}},
 Clutter:{PanGesture:function(){return gesture;},PanAxis:{X:1},EventPhase:{CAPTURE:1},InputDeviceType:{KEYBOARD_DEVICE:1},
 KEY_Alt_L:'Alt',KEY_Left:'Left',KEY_Escape:'Escape',KeyState:{PRESSED:1,RELEASED:0}}});
vm.runInContext(source.slice(source.indexOf('export class HandheldBackNavigation'),source.indexOf('export class HandheldMotion')).replace('export class','this.Back=class'),context);
const home={mode:'mobile',_home:{visible:false},_windows:new Set([window]),_scale:1,_tasks:{monitor:{x:0,y:0,width:360,height:400}}};
const back=new context.Back(home);
const input=(x=359,y=200,dx=0,dy=0,touch=true)=>({get_point_event:()=>({get_coords:()=>[x,y],get_event_sequence:()=>touch?{}:null}),
 get_accumulated_delta_abs:()=>({get_x:()=>dx,get_y:()=>dy})});
assert.equal(back.canBegin(input()),true);
for(const [x,y] of [[335,200],[360,200],[359,43],[359,372]])assert.equal(back.canBegin(input(x,y)),false);
assert.equal(back.canBegin(input(259,200,-100)),true,'initial edge position survives a drag into content');
assert.equal(back.canBegin(input(359,200,0,0,false)),false,'mouse scrollbar drags remain app input');
for(const mode of ['desktop','tablet']){home.mode=mode;assert.equal(back.canBegin(input()),false);}home.mode='mobile';
for(const prop of ['isLocked','isGreeter']){Main.sessionMode[prop]=true;assert.equal(back.canBegin(input()),false);Main.sessionMode[prop]=false;}
Main.actionMode=2;assert.equal(back.canBegin(input()),false);Main.actionMode=1;
function end(dx,dy=0){hooks.get('recognize')();hooks.get('end')(input(300,200,dx,dy));}
end(0);end(-4);end(100);end(-100,100);end(-60,0);
assert.deepEqual(keys,[['Alt',1],['Left',1],['Left',0],['Alt',0]],'only a deliberate inward swipe sends exactly one Back chord');
keys.length=0;end(-100);assert.equal(keys.length,4,'one action per completed gesture');
keys.length=0;hooks.get('recognize')();global.display.focus_window={};hooks.get('end')(input(300,200,-100));assert.equal(keys.length,0);global.display.focus_window=window;
android=true;end(-100);assert.deepEqual(keys,[['Escape',1],['Escape',0]],'Android keeps its focused hardware Back mapping');
keys.length=0;hooks.get('recognize')();hooks.get('cancel')();assert.equal(keys.length,0);
back.destroy();assert.equal(stage.removed,true);assert.equal(keyboard.disposed,true);
console.log('PASS right-edge Back: client scrollbar arbitration, direction/travel, focus, modal/mode isolation, Android and cleanup');
