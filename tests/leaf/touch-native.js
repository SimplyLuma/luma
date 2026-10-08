// SPDX-License-Identifier: GPL-2.0-or-later
// Real Wayland touchscreen events delivered to an ordinary-user native Leaf.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';
const sleep=ms=>Scripting.sleep(ms);
const dir=GLib.getenv('LUMA_LEAF_TOUCH_CONTROL');
function state(){try{const[,bytes]=GLib.file_get_contents(`${dir}/state.json`);return JSON.parse(new TextDecoder().decode(bytes))}catch{return null}}
async function until(kind){for(let i=0;i<300;i++){const s=state();if(s?.kind==='error')throw Error(s.error);if(s?.kind===kind)return s;await sleep(100)}throw Error(`Leaf did not reach ${kind}`)}
function command(value){GLib.file_set_contents(`${dir}/command`,value)}
export async function run(){
 const touch=global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);
 await sleep(400);
 const process=Gio.Subprocess.new(['python3',GLib.getenv('LUMA_LEAF_TOUCH_APP')],Gio.SubprocessFlags.NONE);
 try{
  const ready=await until('ready');
  const win=global.get_window_actors().map(a=>a.meta_window).find(w=>w.get_pid()===ready.pid);
  if(!win)throw Error('actual native Leaf window absent');
  Main.overview.hide();win.activate(global.get_current_time());await sleep(600);
  const r=win.get_buffer_rect(),web=ready.web;print('LEAF NATIVE READY '+JSON.stringify({ready,buffer:{x:r.x,y:r.y,width:r.width,height:r.height},frame:{x:win.get_frame_rect().x,y:win.get_frame_rect().y,width:win.get_frame_rect().width,height:win.get_frame_rect().height},focus:win.has_focus(),overview:Main.overview.visible}));
  const x=r.x+web.x+web.width*.55,y=r.y+web.y+web.height*.4;
  const stream=Gio.File.new_for_path(`${dir}/native-before.png`).replace(null,false,Gio.FileCreateFlags.NONE,null);
  try {await new Shell.Screenshot().screenshot(false,stream)}finally{stream.close(null)}
  async function swipe(dx){touch.notify_touch_down(GLib.get_monotonic_time(),0,x,y);await sleep(45);for(let i=1;i<=10;i++){touch.notify_touch_motion(GLib.get_monotonic_time(),0,x+dx*i/10,y+2);await sleep(20)}touch.notify_touch_up(GLib.get_monotonic_time(),0);await sleep(650)}
  await swipe(-180);command('forward');await until('forward');
  await swipe(180);command('back');const back=await until('back');
  const current=win.get_buffer_rect();
  const tx=current.x+back.web.x+back.text.x,ty=current.y+back.web.y+back.text.y;
  print("LEAF HOLD POINT "+JSON.stringify({back,current:{x:current.x,y:current.y},tx,ty}));
  touch.notify_touch_down(GLib.get_monotonic_time(),0,tx,ty);await sleep(750);touch.notify_touch_up(GLib.get_monotonic_time(),0);await sleep(500);command('hold');
  async function tap(point){const current=win.get_buffer_rect();touch.notify_touch_down(GLib.get_monotonic_time(),0,current.x+point.x,current.y+point.y);await sleep(80);touch.notify_touch_up(GLib.get_monotonic_time(),0);await sleep(500)}
  async function shot(name){const out=Gio.File.new_for_path(`${dir}/${name}.png`).replace(null,false,Gio.FileCreateFlags.NONE,null);try{await new Shell.Screenshot().screenshot(false,out)}finally{out.close(null)}}
  const held=await until('held');print('LEAF HELD '+JSON.stringify(held));await shot('native-held');await tap(held.button);
  if(GLib.getenv('LUMA_LEAF_TOUCH_NOTE')==='1'){
   command('note_editor');const editor=await until('note_editor');await shot('native-note-editor');await tap(editor.text);
   const keyboard=global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
   for(const character of 'native touch note'){keyboard.notify_keyval(GLib.get_monotonic_time(),character.codePointAt(0),Clutter.KeyState.PRESSED);await sleep(25);keyboard.notify_keyval(GLib.get_monotonic_time(),character.codePointAt(0),Clutter.KeyState.RELEASED);await sleep(25)}
   await sleep(200);await shot('native-note-typed');await tap(editor.save);command('noted');const noted=await until('noted');await tap(noted.bookmark);command('pinned');
  }else{
   command('picker');const picker=await until('picker');print('LEAF PICKER '+JSON.stringify(picker));await shot('native-picker');await tap(picker.button);command('saved');
  }
  const result=await until('pass');print('LEAF NATIVE COMPOSITOR TOUCH PASS '+JSON.stringify({ready,result,buffer:{x:r.x,y:r.y,width:r.width,height:r.height}}));
 }finally{try{process.send_signal(15)}catch{}}
}
