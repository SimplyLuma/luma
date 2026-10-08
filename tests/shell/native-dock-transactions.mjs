import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';
import * as AppFavorites from 'resource:///org/gnome/shell/ui/appFavorites.js';
import * as AppDisplay from 'resource:///org/gnome/shell/ui/appDisplay.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';
import * as DND from 'resource:///org/gnome/shell/ui/dnd.js';
const wait=ms=>Scripting.sleep(ms);
const assert=(value,label)=>{if(!value)throw new Error(label);};
const ids=()=>AppFavorites.getAppFavorites().getFavorites().map(a=>a.get_id());
const same=(a,b,label)=>assert(JSON.stringify(a)===JSON.stringify(b),`${label}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`);
const box=a=>{const[x,y]=a.get_transformed_position();const[w,h]=a.get_transformed_size();return{x,y,w,h};};
export function init(){GLib.timeout_add(GLib.PRIORITY_DEFAULT,2200,()=>{checks().catch(e=>console.error(e.message+'\n'+e.stack)).finally(()=>global.context.terminate());return GLib.SOURCE_REMOVE;});}
export async function run(){await new Promise(()=>{});}
async function checks(){
 const shelf=Main.shelf,dash=shelf._dash,apps=Shell.AppSystem.get_default();
 const settings=new Gio.Settings({schema_id:'org.project_luma.shell-state'});
 new Gio.Settings({schema_id:'org.gnome.desktop.interface'}).set_boolean('enable-animations',false);
 settings.set_string('shelf-edge','bottom');settings.set_int('shelf-padding',12);settings.set_string('shelf-surface-mode','separate');
 const choices=apps.get_installed().filter(a=>a.should_show()).map(a=>a.get_id());
 const initial=choices.slice(0,3),extra=choices[3];assert(initial.length===3&&extra,'installed fixture apps');
 async function reset(value=initial){global.settings.set_strv('favorite-apps',value);Gio.Settings.sync();await wait(500);same(ids(),value,'native favorite reset');}
 const source=id=>dash._box.get_children().find(i=>!i.animatingOut&&i.child?._delegate?.app?.get_id()===id)?.child;
 function begin(icon){Main.overview.beginItemDrag(icon);}
 function finish(icon,cancel=false){if(cancel)Main.overview.cancelledItemDrag(icon);Main.overview.endItemDrag(icon);}
 await reset();
 const originalDesktop=apps.lookup_app(initial[0]).get_app_info().get_filename();
 let icon=source(initial[0]);icon.popupMenu();await wait(120);
 assert(icon._menu._toggleFavoriteItem.visible,'native app menu exposes pin action');
 icon._menu._toggleFavoriteItem.emit('activate',null);icon._menu.close();await wait(400);
 same(ids(),initial.slice(1),'native menu unpin');
 const notice=MessageTray.getSystemSource().notifications.at(-1);
 assert(notice.actions[0].label==='Undo','real unpin Undo notification');
 notice.actions[0].activate();await wait(400);same(ids(),initial,'native notification Undo restored exact position');

 // An unaccepted drop and Escape/cancel never alter preferences.
 icon=source(initial[0]);begin(icon);await wait(250);
 assert(dash._showAppsIcon.label.visible&&dash._showAppsIcon.label.reactive,'native removal target exposed only during favorite drag');
 assert(dash.showAppsButton.accessible_name==='Remove from Dash','explicit unpin target accessible name');
 assert(!dash._showAppsIcon.visible,'removal affordance does not reflow app tiles');
 finish(icon,true);await wait(300);same(ids(),initial,'cancel restored favorites');assert(!dash._showAppsIcon.label.visible&&!dash._showAppsIcon.label.reactive,'cancel hid removal target');
 // Reorder using actual native drop handlers, all physical dock orientations.
 for(const edge of ['bottom','top','left','right']){
  await reset();settings.set_string('shelf-edge',edge);await wait(400);icon=source(initial[0]);begin(icon);await wait(250);
  const vertical=['left','right'].includes(edge);const extent=vertical?dash._box.height:dash._box.width;
  const coordinate=extent-1;
  const result=dash.handleDragOver(icon,icon,vertical?18:coordinate,vertical?coordinate:18,global.get_current_time());
  assert(result===DND.DragMotionResult.MOVE_DROP,'native insertion placeholder '+edge);
  const pos=dash._dragPlaceholderPos;
  assert(dash.acceptDrop(icon,icon,0,0,global.get_current_time()),'native reorder acceptance '+edge);finish(icon);await wait(400);
  console.log('REORDER',edge,'placeholder',pos,'result',JSON.stringify(ids()));
  same(ids(),[initial[1],initial[2],initial[0]],'after-last reorder '+edge);
 }
 // A real AppDisplay icon uses the same drop route as the application catalog.
 await reset();settings.set_string('shelf-edge','bottom');await wait(300);
 console.log('CATALOG create');const external=new AppDisplay.AppIcon(apps.lookup_app(extra),{setSizeManually:true,showLabel:false});await wait(100);
 console.log('CATALOG begin');begin(external);await wait(100);assert(!dash._showAppsIcon.visible,'untracked application cannot show removal target');
 console.log('CATALOG over');dash.handleDragOver(external,external,1,18,global.get_current_time());console.log('CATALOG cancel');finish(external,true);await wait(200);same(ids(),initial,'canceled catalog drag did not pin');
 console.log('CATALOG begin');begin(external);await wait(100);console.log('CATALOG over');dash.handleDragOver(external,external,1,18,global.get_current_time());assert(dash.acceptDrop(external,external,1,18,global.get_current_time()),'catalog drop accepted');console.log('CATALOG finish');finish(external);await wait(400);same(ids(),[extra,...initial],'catalog drop pinned once');external.destroy();
 // The native unpin target removes only the favorite record.
 icon=source(initial[0]);begin(icon);await wait(200);assert(dash._showAppsIcon.acceptDrop(icon,icon,0,0,global.get_current_time()),'explicit remove target accepted');finish(icon);await wait(400);same(ids(),[extra,initial[1],initial[2]],'explicit remove target unpinned');assert(Gio.File.new_for_path(originalDesktop).query_exists(null),'drop removal retained installed application');
 Gio.Settings.sync();
 const subprocess=Gio.Subprocess.new(['gsettings','get','org.gnome.shell','favorite-apps'],Gio.SubprocessFlags.STDOUT_PIPE|Gio.SubprocessFlags.STDERR_PIPE);
 const[ok,out]=subprocess.communicate_utf8(null,null);assert(ok&&out.includes(extra)&&!out.includes(initial[0]),'fresh process read persisted favorites');
 // Physical native pointer paths: reorder, Escape, explicit remove target.
 await reset();settings.set_string('shelf-edge','bottom');await wait(400);
 DND.addDragMonitor({dragMotion:e=>{const parents=[];for(let a=e.targetActor;a&&parents.length<8;a=a.get_parent())parents.push([a.constructor.name,a.name,a._delegate?.constructor.name]);console.log('NATIVE_MOTION',JSON.stringify({x:e.x,y:e.y,parents}));return DND.DragMotionResult.CONTINUE;}});
 const seat=Clutter.get_default_backend().get_default_seat();
 const pointer=seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
 const keyboard=seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
 const motion=(x,y)=>pointer.notify_absolute_motion(GLib.get_monotonic_time(),x,y);
 const button=state=>pointer.notify_button(GLib.get_monotonic_time(),Clutter.BUTTON_PRIMARY,state);
 async function dragStart(id){const a=box(source(id));motion(a.x+a.w/2,a.y+a.h/2);await wait(50);button(Clutter.ButtonState.PRESSED);await wait(50);motion(a.x+a.w/2,a.y-40);await wait(180);assert(source(id)._draggable._dragActor,'actual pointer established native drag');}
 await dragStart(initial[0]);let last=box(source(initial[2]));motion(last.x+last.w-1,last.y+last.h/2);await wait(180);console.log('POINTER_REORDER',JSON.stringify({last,pointer:global.get_pointer(),placeholder:dash._dragPlaceholderPos,drag:!!source(initial[0])._draggable._dragActor}));button(Clutter.ButtonState.RELEASED);await wait(450);same(ids(),[initial[1],initial[2],initial[0]],'actual pointer reordered favorite');
 await reset();await dragStart(initial[0]);keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_Escape,Clutter.KeyState.PRESSED);keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_Escape,Clutter.KeyState.RELEASED);await wait(150);button(Clutter.ButtonState.RELEASED);await wait(250);same(ids(),initial,'actual Escape canceled drag');
 await dragStart(initial[0]);const target=box(dash._showAppsIcon.label);motion(target.x+target.w/2,target.y+target.h/2);await wait(180);button(Clutter.ButtonState.RELEASED);await wait(450);same(ids(),initial.slice(1),'actual pointer removed favorite only');
 console.log('PASS native dock transactions: native menu and Undo, cancel, four-edge reorder, catalog add/cancel, explicit unpin, installed app retained, fresh-process persistence, actual pointer reorder/remove, keyboard Escape');
}
