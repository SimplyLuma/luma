// SPDX-License-Identifier: GPL-2.0-or-later
// Actual installed actor geometry and real virtual input in disposable Shell.
// MPRIS provider is an explicit protocol fixture, not physical audio proof.
import Clutter from 'gi://Clutter';import Gio from 'gi://Gio';import GLib from 'gi://GLib';import Shell from 'gi://Shell';import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';import * as Notifications from 'resource:///org/gnome/shell/ui/lumaNotificationBeacon.js';import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';
const sleep=ms=>Scripting.sleep(ms);const failures=[];let count=0;
const check=(v,m)=>{count++;print(`${v?'PASS':'FAIL'} activity-touch: ${m}`);if(!v)failures.push(m);};
const rect=a=>{const[x,y]=a.get_transformed_position(),[width,height]=a.get_transformed_size();return{x,y,width,height}};
async function capture(name){const dir=GLib.getenv('LUMA_ACTIVITY_EVIDENCE');if(!dir)return;const stream=Gio.File.new_for_path(`${dir}/${name}.png`).replace(null,false,Gio.FileCreateFlags.NONE,null);try{await new Shell.Screenshot().screenshot(false,stream);}finally{stream.close(null);}}
export async function run(){
const scale=Number(GLib.getenv('LUMA_ACTIVITY_SCALE')??'1');St.ThemeContext.get_for_stage(global.stage).scale_factor=scale;new Gio.Settings({schema_id:'org.gnome.desktop.interface'}).set_boolean('enable-animations',false);Main.overview.hide();await sleep(300);
const favorites=JSON.parse(GLib.getenv('LUMA_ACTIVITY_FAVORITES'));const settings=new Gio.Settings({schema_id:'org.gnome.shell'});settings.set_strv('favorite-apps',favorites);await sleep(1500);
const process=Gio.Subprocess.new(['python3',GLib.getenv('LUMA_ACTIVITY_PROVIDER')],Gio.SubprocessFlags.NONE);
try{
 await sleep(2200);const s=Main.shelf,m=s.getIsland('media');check(s._media.eligible,'normal D-Bus MPRIS player is eligible');check(m.visible&&m.mapped&&s._mediaAllowed,'default22-app dock preserves visible live media');check(s._dockScroll.width<s._dockContent.get_preferred_width(-1)[1],'native dock scrolls to make room for media');
 const q=Main.panel.statusArea.quickSettings;check(q._statusDateLabel.mapped&&q._statusDateLabel.height>0,'date remains visible beside dock and media');const qr=rect(q),dr=rect(q._statusDateLabel),monitor=Main.layoutManager.primaryMonitor;check(qr.width>180*scale&&dr.x>=qr.x&&dr.x+dr.width<=qr.x+qr.width+1&&qr.x+qr.width<=monitor.x+monitor.width-15,'whole status controls and date fit the measured native row');
 print('ACTIVITY_NATIVE '+JSON.stringify({favorites:settings.get_strv('favorite-apps').length,mediaAllowed:s._mediaAllowed,liveShown:s._liveShown,liveLimit:s._liveLimit,dock:rect(s.getIsland('dock')),media:rect(m),groups:s._groups.map(g=>({rect:rect(g),preferred:g.row.get_preferred_width(-1),containsDock:g.row.contains(s._dockIsland)})),dockNatural:s._dockNatural,status:rect(q),viewport:s._dockScroll.width,natural:s._dockContent.get_preferred_width(-1)[1]}));
 if(m.visible){s._media._toggle.emit('clicked',Clutter.BUTTON_PRIMARY);await sleep(200);const[,data]=GLib.file_get_contents(GLib.getenv('LUMA_MEDIA_ACTION_LOG'));check(new TextDecoder().decode(data).includes('PlayPause'),'native media button dispatches to actual MPRIS recipient');}
 settings.set_strv('favorite-apps',favorites.slice(0,4));await sleep(1000);check(m.visible&&s._mediaAllowed,'activity remains visible when dock gets smaller');
 settings.set_strv('favorite-apps',favorites);await sleep(1000);check(m.visible&&s._mediaAllowed,'activity remains visible when full dock returns');
 await capture('default-dock-live-media');
 const lip=Notifications.peekNotificationLip();check(!!lip&&lip.mapped,'native notification nub is mapped');lip.close();lip._rest();await sleep(300);
 const touch=global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);
 async function input(dx,dy){const r=rect(lip),x=r.x+r.width/2,y=r.y+r.height/2;touch.notify_touch_down(GLib.get_monotonic_time(),0,x,y);await sleep(40);for(let i=1;i<=8;i++){touch.notify_touch_motion(GLib.get_monotonic_time(),0,x+dx*i/8,y+dy*i/8);await sleep(18);}touch.notify_touch_up(GLib.get_monotonic_time(),0);await sleep(300);}
 for(let i=0;i<3;i++){await input(0,0);check(lip.isOpen,`touch tap opens notification tray ${i+1}`);lip.close();lip._rest();await sleep(250);await input(0,70);check(lip.isOpen,`touch downward pull opens notification tray ${i+1}`);lip.close();lip._rest();await sleep(250);}
 await input(70,0);check(!lip.isOpen,'horizontal drag does not fire a tap');await input(0,-60);check(!lip.isOpen,'upward drag does not open the tray');
 // Mouse and keyboard use the same native owner after gesture arbitration.
 const pointer=global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);let r=rect(lip);pointer.notify_absolute_motion(GLib.get_monotonic_time(),r.x+r.width/2,r.y+r.height/2);await sleep(50);pointer.notify_button(GLib.get_monotonic_time(),Clutter.BUTTON_PRIMARY,Clutter.ButtonState.PRESSED);pointer.notify_button(GLib.get_monotonic_time(),Clutter.BUTTON_PRIMARY,Clutter.ButtonState.RELEASED);await sleep(250);check(lip.isOpen,'native mouse click still opens notification tray');await capture('notification-touch-tray');lip.close();lip._rest();await sleep(200);
 const keyboard=global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);lip.grab_key_focus();keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_Return,Clutter.KeyState.PRESSED);keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_Return,Clutter.KeyState.RELEASED);await sleep(200);check(lip.isOpen,'keyboard Return still opens notification tray');keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_Escape,Clutter.KeyState.PRESSED);keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_Escape,Clutter.KeyState.RELEASED);await sleep(200);check(!lip.isOpen,'keyboard Escape dismisses notification tray');
 Main.sessionMode.pushMode('unlock-dialog');await sleep(200);check(!lip.visible&&!lip.isOpen,'locked native session hides and closes notification tray');await input(0,0);check(!lip.isOpen,'touch cannot reopen notifications in locked session');Main.sessionMode.popMode('unlock-dialog');await sleep(200);
}finally{process.send_signal(15);await sleep(300);}
check(!Main.shelf._media.eligible,'provider exit removes stale media eligibility');
if(failures.length)throw Error(failures.join('; '));print(`NATIVE LIVE BUDGET AND TOUCH PASS (${count} assertions)`);
}
