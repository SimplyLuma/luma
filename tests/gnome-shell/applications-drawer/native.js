// SPDX-License-Identifier: GPL-2.0-or-later
// Owned disposable native Shell only; installed application metadata is real.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as ParentalControlsManager from 'resource:///org/gnome/shell/misc/parentalControlsManager.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';
const out=GLib.getenv('LUMA_POLISH_OUTPUT'), results=[];
const pause=async(ms=180)=>await Scripting.sleep(ms);
function check(name,ok,detail=''){results.push({name,ok:!!ok,detail});print(`${ok?'PASS':'FAIL'} ${name} ${detail}`);}
async function until(predicate){for(let i=0;i<50;i++){if(predicate())return;await pause(100);}throw new Error('Timed out waiting for application registry');}
function rect(actor){const [x,y]=actor.get_transformed_position(),[w,h]=actor.get_transformed_size();return{x,y,w,h};}
async function shot(name){const stream=Gio.File.new_for_path(`${out}/${name}.png`).replace(null,false,Gio.FileCreateFlags.NONE,null);await new Shell.Screenshot().screenshot(false,stream);stream.close(null);}
async function work(){
 const settings=new Gio.Settings({schema_id:'org.project_luma.shell-state'});
 const folders=Main.shelf.dockFolders;
 check('real dock folders controller exists',!!folders);
 check('untouched defaults include Applications',folders.uris.includes('applications:///'));
 const downloads=GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD);
 check('untouched defaults include XDG Downloads',downloads&&folders.uris.includes(Gio.File.new_for_path(downloads).get_uri()));
 const model=folders._models.get('applications:///');
 const policy=ParentalControlsManager.getDefault();
 await until(()=>model?.loaded&&policy.initialized);
 const initial=model.items.length, columns=folders.applicationMetrics().columns;
 // Real private desktop entries force a partial last row and exercise long names.
 // They are never installed in the owner's desktop or used for review screenshots.
 const extra=3*columns+(Math.min(3,columns-1)-initial%columns+columns)%columns;
 for(let i=0;i<extra;i++){
  const path=`${GLib.get_user_data_dir()}/applications/org.projectluma.LayoutCheck${i}.desktop`;
  GLib.file_set_contents(path,`[Desktop Entry]\nType=Application\nName=Z Layout Check ${i} with a longer application name\nExec=/usr/bin/true\nIcon=utilities-terminal\nTerminal=false\n`);
 }
 await until(()=>model.items.length===initial+extra);
 check('fixture exercises multiple rows and an incomplete final row',model.items.length>columns&&model.items.length%columns!==0);
 const expected=Shell.AppSystem.get_default().get_installed().filter(info=>info.should_show()&&policy.shouldShowApp(info));
 check('drawer uses the actual installed visible applications',model.items.length===expected.length,`${model.items.length}`);
 check('application identities remain unique',new Set(model.items.map(item=>item.app.get_id())).size===model.items.length);
 const keyboard=Clutter.get_default_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
 const key=async(symbol)=>{keyboard.notify_keyval(GLib.get_monotonic_time(),symbol,Clutter.KeyState.PRESSED);await pause(40);keyboard.notify_keyval(GLib.get_monotonic_time(),symbol,Clutter.KeyState.RELEASED);await pause();};
 for(const treatment of ['dark','light']){
  settings.set_string('surface-treatment',treatment);await pause(250);
  const tile=folders._tiles.get(model.uri);tile.grab_key_focus();await key(Clutter.KEY_space);await pause(250);
  check(`${treatment} keyboard opens Applications folder`,folders.openFolder===model&&folders._surface.visible);
  check(`${treatment} applications use grid without file view switch`,folders.viewFor(model)==='grid'&&!folders._surface._views.visible);
  check(`${treatment} search is available and focused`,folders._surface.search.visible&&folders._surface.search.contains(global.stage.get_key_focus()));
  const searchColour=folders._surface.search.get_theme_node().get_foreground_color();
  check(`${treatment} search text follows surface contrast`,treatment==='light'?searchColour.red<50:searchColour.red>200,searchColour.to_string());
  const surface=rect(folders._surface),monitor=Main.layoutManager.primaryMonitor;
  check(`${treatment} drawer fits the monitor`,surface.x>=monitor.x&&surface.y>=monitor.y&&surface.x+surface.w<=monitor.x+monitor.width+1&&surface.y+surface.h<=monitor.y+monitor.height+1,JSON.stringify(surface));
  check(`${treatment} grid renders real app buttons`,folders._itemActors().length===model.items.length);
  const header=rect(folders._surface._header), search=rect(folders._surface.search);
  check(`${treatment} app count does not add a second header line`,!folders._surface._meta.visible);
  check(`${treatment} search has space below the header`,search.y-header.y-header.h>=3,JSON.stringify({header,search}));
  const columns=folders.gridColumns(), cells=folders._itemActors();
  const positions=cells.map(rect), reference=positions.slice(0,columns);
  check(`${treatment} app tiles use consistent column widths`,positions.every(p=>Math.abs(p.w-reference[0].w)<=1));
  check(`${treatment} every row aligns with the first row`,positions.every((p,i)=>Math.abs(p.x-reference[i%columns].x)<=1),JSON.stringify(positions));
  check(`${treatment} all app rows have the same height`,positions.every(p=>Math.abs(p.h-reference[0].h)<=1));
  const viewport=rect(folders._surface.scroll);
  check(`${treatment} four rows fit without hiding the final row labels`,cells.every(cell=>{
   const label=rect(cell.child.get_children()[1]);
   return label.y>=viewport.y&&label.y+label.h<=viewport.y+viewport.h;
  }));
  check(`${treatment} two-line app names fit inside their tiles`,cells.every(cell=>{
   const bounds=rect(cell),label=rect(cell.child.get_children()[1]);
   return label.y>=bounds.y&&label.y+label.h<=bounds.y+bounds.h;
  }));
  check(`${treatment} Applications shortcut loads full-colour resource artwork`,folders._tiles.get(model.uri)._mark.gicon.to_string().includes('luma-dock-applications.svg'));
  const downloadTile=folders._tiles.get(Gio.File.new_for_path(downloads).get_uri());
  check(`${treatment} Downloads shortcut uses matching resource artwork`,downloadTile._mark.gicon.to_string().includes('luma-dock-downloads.svg'));
  await shot(`applications-drawer-${treatment}`);
  folders._surface.search.set_text('no-such-application-qualification');await pause();
  check(`${treatment} search filters without stale icons`,folders._itemActors().length===0);
  check(`${treatment} typing retains search focus`,folders._surface.search.contains(global.stage.get_key_focus()));
  await key(Clutter.KEY_Escape);check(`${treatment} Escape dismisses drawer`,!folders.openFolder);
 }
 folders.open(model);await pause();folders._surface.search.set_text('Luma Drawer Probe');await pause();
 check('search finds the actual installed fixture application',folders._itemActors().length===1);
 await key(Clutter.KEY_Return);
 const probe=Shell.AppSystem.get_default().lookup_app('org.projectluma.DrawerProbe.desktop');
 await until(()=>probe.get_windows().length>0);
 check('Enter launches the actual desktop application',probe.get_windows().length>0&&!folders.openFolder);
 for(const window of probe.get_windows())window.delete(global.get_current_time());
 const custom=folders.uris.find(uri=>uri!=='applications:///');
 settings.set_strv('dock-folders',custom?[custom]:[]);await pause();
 check('explicit custom folders are preserved',folders.uris.length===(custom?1:0)&&!folders.uris.includes('applications:///'));
 settings.set_strv('dock-folders',[]);await pause();
 check('explicit empty folder list stays empty',folders.uris.length===0&&!folders.rail.visible);
 settings.reset('dock-folders');await pause();
 check('reset restores automatic shortcuts',folders.uris.includes('applications:///'));
}
export function init(){GLib.timeout_add(GLib.PRIORITY_DEFAULT,5000,()=>{work().catch(error=>{check('native drawer completes',false,`${error}\n${error.stack}`);logError(error);}).finally(()=>{GLib.file_set_contents(`${out}/result.json`,JSON.stringify({pass:results.every(r=>r.ok),results},null,2));global.context.terminate();});return GLib.SOURCE_REMOVE;});}
