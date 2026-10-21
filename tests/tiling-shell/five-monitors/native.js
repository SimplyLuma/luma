// SPDX-License-Identifier: GPL-2.0-or-later
// Real Shell monitors, native pointer drag and installed Tiling Shell.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
const out=GLib.getenv('LUMA_FIVE_OUT'),results=[];
const geometry=r=>({x:r.x,y:r.y,width:r.width,height:r.height});
const scale=Number(GLib.getenv('LUMA_FIVE_SCALE')??'1');
const pause=ms=>new Promise(r=>GLib.timeout_add(GLib.PRIORITY_DEFAULT,ms,()=>{r();return GLib.SOURCE_REMOVE;}));
const check=(name,ok,detail='')=>{results.push({name,ok:!!ok,detail});print(`${ok?'PASS':'FAIL'} ${name} ${detail}`);};
const call=(method,args)=>new Promise((resolve,reject)=>Gio.DBus.session.call('org.gnome.Mutter.DisplayConfig','/org/gnome/Mutter/DisplayConfig','org.gnome.Mutter.DisplayConfig',method,args,null,Gio.DBusCallFlags.NONE,5000,null,(c,r)=>{try{resolve(c.call_finish(r));}catch(e){reject(e);}}));
async function shot(name){const stream=Gio.File.new_for_path(`${out}/${name}.png`).replace(null,false,Gio.FileCreateFlags.NONE,null);await new Shell.Screenshot().screenshot(false,stream);stream.close(null);}
async function work(){
 const [serial,physical]=(await call('GetCurrentState',null)).deep_unpack();
 check('five real virtual outputs',physical.length===5,`${physical.length}`);if(physical.length!==5)return;
 const modes=physical.map(([spec,list])=>({connector:spec[0],mode:list.find(m=>m[6]['is-current']?.deep_unpack())??list[0]}));
 modes.sort((a,b)=>a.mode[1]-b.mode[1]);const centre=modes.pop();
 const centreWidth=centre.mode[1]/scale,centreHeight=centre.mode[2]/scale;
 const sideWidth=modes[0].mode[1],sideHeight=modes[0].mode[2];
 const positions=[[0,0],[0,sideHeight],[sideWidth+centreWidth,0],[sideWidth+centreWidth,sideHeight],[sideWidth,Math.round((2*sideHeight-centreHeight)/2)]];
 await call('ApplyMonitorsConfig',new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',[serial,1,[...modes,centre].map((m,i)=>[...positions[i],i===4?scale:1,0,i===4,[[m.connector,m.mode[0],{}]]]),{'layout-mode':new GLib.Variant('u',1)}]));
 await pause(1800);
 const extension=Main.extensionManager.lookup('tilingshell@ferrarodomenico.com')?.stateObj;
 check('one tiling manager per output',extension?._tilingManagers.length===5);
 if(!extension)return;
 const settings=extension.getSettings();
 settings.set_boolean('enable-tiling-system',true);settings.set_boolean('show-layout-while-dragging',true);
 settings.set_boolean('enable-span-multiple-tiles',true);
 const layout={id:'five-monitor-probe',tiles:[{x:0,y:0,width:1/3,height:1,groups:[]},{x:1/3,y:0,width:1/3,height:1,groups:[]},{x:2/3,y:0,width:1/3,height:1,groups:[]}]};
 settings.set_string('layouts-json',JSON.stringify([layout]));
 settings.set_value('selected-layouts',new GLib.Variant('aas',Array.from({length:5},()=>['five-monitor-probe'])));
 await pause(600);
 const child=Gio.Subprocess.new(['python3',GLib.getenv('LUMA_FIVE_APP'),'org.projectluma.FiveMonitorProbe'],Gio.SubprocessFlags.NONE);
 const pointer=Clutter.get_default_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
 const move=async(x,y,ms=150)=>{pointer.notify_absolute_motion(GLib.get_monotonic_time(),x,y);await pause(ms);};
 const button=state=>pointer.notify_button(GLib.get_monotonic_time(),Clutter.BUTTON_PRIMARY,state);
 let window;
 try{
  for(let i=0;i<60;i++){window=global.get_window_actors().find(a=>a.mapped&&a.meta_window.get_title()==='Shelf test')?.meta_window;if(window)break;await pause(100);}
  check('native probe window exists',!!window);if(!window)return;
  const central=Main.layoutManager.monitors.find(m=>m.width===centreWidth);check('centre is full height between stacked outputs',central?.x===sideWidth&&central?.height===centreHeight,JSON.stringify(Main.layoutManager.monitors));
  const cx=central.x+central.width*.5,top=central.y+central.height*.2,bottom=central.y+central.height*.8,right=central.x+central.width+sideWidth*.25;
  const left=sideWidth*.4,sideTop=sideHeight*.3,sideBottom=sideHeight*1.5,entry=central.x+20;
  for(const [caseName,points] of [
   ['left-top',[[left,sideTop],[entry,top],[cx,top],[cx,bottom]]],
   ['left-bottom',[[left,sideBottom],[entry,bottom],[cx,bottom],[cx,top]]],
   ['right-top',[[right,sideTop],[central.x+central.width-40,top],[cx,top],[cx,bottom]]],
   ['right-bottom',[[right,sideBottom],[central.x+central.width-40,bottom],[cx,bottom],[cx,top]]],
   ['same-point-reentry',[[cx,top],[right,sideTop],[cx,top]]],
   ['rapid-reentry',[[cx,bottom],[right,sideBottom],[cx,top],[left,sideTop],[cx,bottom],[cx,top]]],
   ['fast-reentry',[[cx,top],[cx+central.width*.3,bottom],[right,bottom],[cx,top],[left,sideTop],[cx,bottom]]]
  ]){
   window.unmaximize();window.move_resize_frame(false,central.x+80,central.y+80,640,400);window.activate(global.get_current_time());await pause(500);
   const frame=window.get_frame_rect();await move(frame.x+150,frame.y+20);button(Clutter.ButtonState.PRESSED);await pause(180);
   for(const [x,y] of points){await move(x,y,caseName==='fast-reentry'?40:caseName==='same-point-reentry'?400:150);}
   await pause(500); // Let the final native motion and preview animation settle.
   const manager=extension._tilingManagers.find(m=>m._monitor.index===central.index),preview=manager._selectedTilesPreview;
   const [x,y]=points.at(-1),r=preview.rect;
   if(GLib.getenv('LUMA_FIVE_TRACE')==='1')print('DRAG_TRACE '+JSON.stringify({caseName,wanted:[x,y],actual:global.get_pointer(),last:manager._lastCursorPos,showing:preview.showing,selection:geometry(r),tiles:manager._workspaceTilingLayout.get(window.get_workspace())._previews.map(p=>({rect:geometry(p.rect),original:geometry(p.originalRect),restore:p.canRestore}))}));
   check(`${caseName} real drag active`,manager._isGrabbingWindow);
   check(`${caseName} centre selection covers pointer`,preview.showing&&x>=r.x&&x<=r.x+r.width&&y>=r.y&&y<=r.y+r.height,JSON.stringify(geometry(r)));
   const area=manager._lumaCellArea;
   check(`${caseName} centre column has complete height`,Math.abs(r.y-area.y)<=1&&Math.abs(r.height-area.height)<=1,JSON.stringify({selection:geometry(r),area:geometry(area)}));
   await shot(`five-${caseName}`);
   button(Clutter.ButtonState.RELEASED);await pause(700);
   check(`${caseName} actual drop fills full-height centre column`,window.assignedTile&&Math.abs(window.get_frame_rect().height-(area.height-manager._selectedTilesPreview.gaps.top-manager._selectedTilesPreview.gaps.bottom))<=2,JSON.stringify(geometry(window.get_frame_rect())));
  }
 }finally{button(Clutter.ButtonState.RELEASED);child.force_exit();}
}
export function init(){GLib.timeout_add(GLib.PRIORITY_DEFAULT,5000,()=>{work().catch(e=>check('five-monitor scenario completed',false,String(e.stack))).finally(()=>{GLib.file_set_contents(`${out}/result.json`,JSON.stringify({pass:results.every(r=>r.ok),results},null,2));global.context.terminate();});return GLib.SOURCE_REMOVE;});}
