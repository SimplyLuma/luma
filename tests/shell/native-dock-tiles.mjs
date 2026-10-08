import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';
import {DASH_TILE_SIZE} from 'resource:///org/gnome/shell/ui/shelfMetrics.js';
function check(v,w,label){if(!Number.isFinite(v)||Math.abs(v-w)>.1)throw new Error(`${label}: ${v} != ${w}`);}
function bounds(a){const [x,y]=a.get_transformed_position();const [w,h]=a.get_transformed_size();return{x,y,w,h};}
export function init(){GLib.timeout_add(GLib.PRIORITY_DEFAULT,2000,()=>{checks().catch(e=>console.error(e.message+'\n'+e.stack)).finally(()=>global.context.terminate());return GLib.SOURCE_REMOVE;});}
export async function run(){await new Promise(()=>{});}
async function capture(name){const f=Gio.File.new_for_path(GLib.build_filenamev([GLib.get_home_dir(),name+'.png']));const out=f.replace(null,false,Gio.FileCreateFlags.NONE,null);await new Shell.Screenshot().screenshot(false,out);out.close(null);}
async function checks(){
 const s=new Gio.Settings({schema_id:'org.project_luma.shell-state'});
 new Gio.Settings({schema_id:'org.gnome.desktop.interface'}).set_boolean('enable-animations',false);
 const scale=Number(GLib.getenv('LUMA_STATUS_SCALE')??1);St.ThemeContext.get_for_stage(global.stage).scale_factor=scale;
 const shelf=Main.shelf;if(!shelf)throw new Error('No native Shelf');
 if(GLib.getenv('LUMA_STATUS_RTL')==='1')shelf.text_direction=Clutter.TextDirection.RTL;
 const apps=Shell.AppSystem.get_default().get_installed().filter(a=>a.should_show()).slice(0,24).map(a=>a.get_id());
 if(apps.length<12)throw new Error('Insufficient installed apps for real overflow test');
 global.settings.set_strv('favorite-apps',apps);
 await Scripting.sleep(2000);
 s.set_string('shelf-surface-mode','separate');s.set_boolean('shelf-span-full',false);s.set_string('status-frame-scope','split');s.set_string('status-frame','fill');
 let cases=0;
 for(const padding of (DASH_TILE_SIZE===44?[12]:[4,12,24]))for(const edge of ['bottom','top','left','right'])for(const material of ['dark','light','frost','glass']){
  if(GLib.getenv('LUMA_DOCK_FOCUS')==='1'&&(padding!==12||edge!=='bottom'||material!=='dark'))continue;
  s.set_int('shelf-padding',padding);s.set_string('shelf-edge',edge);s.set_string('shelf-material',material);
  await Scripting.sleep(350);
  const monitor=Main.layoutManager.primaryMonitor;
  let area=global.workspace_manager.get_active_workspace().get_work_area_for_monitor(monitor.index);
  const depth=(DASH_TILE_SIZE+2*padding+(s.get_string('shelf-edge-mode')==='protruding'?0:padding))*scale;
  for(let retry=0;retry<20;retry++) {
   area=global.workspace_manager.get_active_workspace().get_work_area_for_monitor(monitor.index);
   if(area.x===monitor.x+(edge==='left'?depth:0)&&area.y===monitor.y+(edge==='top'?depth:0)&&area.width===monitor.width-(['left','right'].includes(edge)?depth:0)&&area.height===monitor.height-(['top','bottom'].includes(edge)?depth:0))break;
   await Scripting.sleep(50);
  }
  console.log('WORK_AREA',JSON.stringify({edge,padding,area:[area.x,area.y,area.width,area.height],strut:bounds(shelf._workArea),shelf:bounds(shelf)}));
  check(area.x,monitor.x+(edge==='left'?depth:0),'work area left');
  check(area.y,monitor.y+(edge==='top'?depth:0),'work area top');
  check(area.width,monitor.width-(['left','right'].includes(edge)?depth:0),'work area width');
  check(area.height,monitor.height-(['top','bottom'].includes(edge)?depth:0),'work area height');
  const vertical=['left','right'].includes(edge);let island=bounds(shelf._dockIsland);const node=shelf._dockMaterial.get_theme_node();
  for(const side of [St.Side.TOP,St.Side.RIGHT,St.Side.BOTTOM,St.Side.LEFT])check(node.get_padding(side)+node.get_border_width(side),padding*scale,'real material padding');
  const liveItems=()=>shelf._dash._box.get_children().filter(x=>!x.animatingOut&&x.child?._delegate?.icon?.icon?.mapped);
  let items=liveItems();
  if(!items.length)throw new Error('No installed app tiles');
  const tiles=items.map(x=>bounds(x.child));const adjustment=vertical?shelf._dockScroll.vadjustment:shelf._dockScroll.hadjustment;
  if(adjustment.upper>adjustment.page_size+.1&&!shelf._dockScroll.get_effect('fade')?.get_enabled())throw new Error('Missing enabled native scroll fade');
  for(const end of ['start','end']){
   const reverse=!vertical&&shelf.text_direction===Clutter.TextDirection.RTL;
   adjustment.value=(end==='start')!==reverse?adjustment.lower:adjustment.upper-adjustment.page_size;await Scripting.sleep(80);island=bounds(shelf._dockIsland);
   items=liveItems();const t=items.map(x=>bounds(x.child));
   const extreme=end==='start'?Math.min(...t.map(x=>vertical?x.y:x.x)):Math.max(...t.map(x=>vertical?x.y+x.h:x.x+x.w));
   const expected=end==='start'?(vertical?island.y:island.x)+padding*scale:(vertical?island.y+island.h:island.x+island.w)-padding*scale;
   if(Math.abs(extreme-expected)>.1)console.log('INSET_DETAIL',JSON.stringify({edge,padding,end,island,scroll:bounds(shelf._dockScroll),dash:bounds(shelf._dash),box:bounds(shelf._dash._box),tiles:t,value:adjustment.value,upper:adjustment.upper,page:adjustment.page_size,prefs:[shelf._dockContent,shelf._dash,shelf._dash._dashContainer,shelf._dash._box].map(a=>({name:a.constructor.name,pref:a.get_preferred_height(DASH_TILE_SIZE*scale),width:a.get_preferred_width(DASH_TILE_SIZE*scale),nh:a.natural_height_set,mh:a.min_height_set,request:a.request_mode}))}));
   check(extreme,expected,'scroll end inset '+edge+' '+end);
   if(padding===12&&material==='dark')await capture(edge+'-'+end);
  }
  adjustment.value=adjustment.lower;await Scripting.sleep(80);island=bounds(shelf._dockIsland);
  items=liveItems();for(const item of items){const t=bounds(item.child);check(vertical?t.x-island.x:t.y-island.y,padding*scale,'cross inset');check(t.w,DASH_TILE_SIZE*scale,'tile width');check(t.h,DASH_TILE_SIZE*scale,'tile height');}
  const item=items[0],icon=item.child,bar=item._shelfIndicator;
  if(!bar)throw new Error('Missing native bar');
  icon._dot.show();await Scripting.sleep(20);
  const b=bounds(bar);check(vertical?b.w:b.h,3*scale,'bar depth');check(vertical?b.h:b.w,18*scale,'bar length');
  check(edge==='left'?b.x:edge==='right'?b.x+b.w:edge==='top'?b.y:b.y+b.h,edge==='left'?island.x:edge==='right'?island.x+island.w:edge==='top'?island.y:island.y+island.h,'bar island edge');
  const color=bar.get_theme_node().get_background_color();check(color.red,['light','frost'].includes(material)?33:255,'material red');check(bar.opacity,235,'bar opacity');
  const radii={bottom:[2,2,0,0],top:[0,0,2,2],left:[0,2,2,0],right:[2,0,0,2]}[edge];[St.Corner.TOPLEFT,St.Corner.TOPRIGHT,St.Corner.BOTTOMRIGHT,St.Corner.BOTTOMLEFT].forEach((c,i)=>check(bar.get_theme_node().get_border_radius(c),radii[i]*scale,'bar radius'));
  for(const state of ['hover','drag']){
   if(state==='hover')icon.set_hover(true);else{icon._dragging=true;icon._syncShelfArtwork();}
   await Scripting.sleep(20);const now=bounds(bar);check(now.x,b.x,'stationary bar x');check(now.y,b.y,'stationary bar y');check(icon.icon.scale_x,state==='drag'?1.12:1,'artwork scale');
   if(padding===12&&edge==='bottom'&&material==='dark')await capture(state);
   icon.set_hover(false);icon._dragging=false;icon._syncShelfArtwork();await Scripting.sleep(20);
  }
  if(scale===1&&DASH_TILE_SIZE===36&&padding===12&&edge==='bottom'&&material==='dark'){
   console.log('BAR_PIXEL_BOX',JSON.stringify(b));
   const t=bounds(icon);const pointer=Clutter.get_default_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
   pointer.notify_absolute_motion(GLib.get_monotonic_time(),t.x+t.w/2,t.y+t.h/2);await Scripting.sleep(25);
   pointer.notify_button(GLib.get_monotonic_time(),Clutter.BUTTON_PRIMARY,Clutter.ButtonState.PRESSED);await Scripting.sleep(25);
   check(icon.icon.scale_x,.94,'actual native press artwork');check(bounds(bar).x,b.x,'press bar x');check(bounds(bar).y,b.y,'press bar y');await capture('press');
   pointer.notify_absolute_motion(GLib.get_monotonic_time(),10,10);await Scripting.sleep(150);check(icon._draggable._dragActor.scale_x,1.12,'actual native drag clone');check(bounds(bar).x,b.x,'actual drag bar x');check(bounds(bar).y,b.y,'actual drag bar y');await capture('actual-drag');pointer.notify_button(GLib.get_monotonic_time(),Clutter.BUTTON_PRIMARY,Clutter.ButtonState.RELEASED);await Scripting.sleep(180);
  }
  if(padding===12&&material==='dark')await capture(edge);
  check(vertical?island.w:island.h,(DASH_TILE_SIZE+2*padding)*scale,'common island thickness');
  const status=Main.panel.statusArea.quickSettings;
  if(!vertical)for(const part of [status._statusClock,status._statusControls])check(part.height,DASH_TILE_SIZE*scale,'shared status tile height');
  cases++;
 }
 console.log('PASS native dock tiles',cases,'scale',scale,'tile',DASH_TILE_SIZE);
}
