// SPDX-License-Identifier: Apache-2.0
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as LumaGrid from 'resource:///org/gnome/shell/ui/lumaGrid.js';
export async function testDesktopDrag(p, check, sleep, shot, source) {
    const tiler = Main.extensionManager.lookup('tilingshell@ferrarodomenico.com')?.stateObj;
    check('real Desktop tiler exposes window preview drag', Boolean(tiler?.beginWindowPreviewDrag));
    if (!tiler?.beginWindowPreviewDrag) throw new Error('Missing native tiling preview API');
    const settings = tiler.getSettings();
    settings.set_boolean('enable-autotiling', false);
    const tiles = [0,1,2].map(i => ({x:i/3,y:0,width:1/3,height:1,groups:[]}));
    settings.set_string('layouts-json', JSON.stringify([{id:'switcher-three-zones',tiles}]));
    settings.set_value('selected-layouts', new GLib.Variant('aas', [Main.layoutManager.monitors.map(()=> 'switcher-three-zones')]));
    await sleep(600);
    const children = ['A','B'].map(id => Gio.Subprocess.new(['python3', `${source}/tests/gnome-shell/presentation/window.py`,
        '--application-id', `org.example.DesktopDrag${id}`, '--title', `DesktopDrag${id}`], Gio.SubprocessFlags.NONE));
    let windows;
    for (let i=0;i<60;i++) {
        windows = ['A','B'].map(id => global.get_window_actors().map(a=>a.meta_window).find(w=>w.title===`DesktopDrag${id}`));
        if (windows.every(w => w?.get_compositor_private()?.mapped && w.get_frame_rect().width > 0)) break;
        await sleep(100);
    }
    check('two real Desktop clients opened', windows.every(Boolean));
    const [a,b] = windows;
    if (!a || !b) throw new Error('Missing Desktop clients');
    const manager=tiler._tilingManagers[a.get_monitor()];
    manager.onTileFromWindowMenu(tiles[0],a);manager.onTileFromWindowMenu(tiles[1],b);
    await sleep(1000);
    const frame = w => {const r=w.get_frame_rect();return [r.x,r.y,r.width,r.height];};
    const neighbor=JSON.stringify(frame(b));
    const pointer=Clutter.get_default_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const keyboard=Clutter.get_default_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    const now=()=>GLib.get_monotonic_time();
    const open=async()=>{LumaGrid.show();await sleep(550);return Main.uiGroup.get_children().find(actor=>actor.constructor.name==='GridPopup');};
    const begin=async(grid)=>{
        const card=grid._cards.find(c=>c.window===a);
        check('Desktop uses existing native Grid card drag', card._delegate?.window===a);
        const [x,y]=card.get_transformed_position(),[w,h]=card.get_transformed_size();
        pointer.notify_absolute_motion(now(),x+w/2,y+h/2);
        await sleep(100);
        pointer.notify_button(now(),Clutter.BUTTON_PRIMARY,Clutter.ButtonState.PRESSED);
        await sleep(650);
        return [x+w/2,y+h/2];
    };
    const grid=await open();
    const browseCard=grid._cards.find(c=>c.window===a);
    const [bx,by]=browseCard.get_transformed_position(),[bw,bh]=browseCard.get_transformed_size();
    pointer.notify_absolute_motion(now(),bx+bw/2,by+bh/2);
    pointer.notify_button(now(),Clutter.BUTTON_PRIMARY,Clutter.ButtonState.PRESSED);
    for(let i=1;i<=8;i++){pointer.notify_absolute_motion(now(),bx+bw/2-100*i/8,by+bh/2);await sleep(15);}
    pointer.notify_button(now(),Clutter.BUTTON_PRIMARY,Clutter.ButtonState.RELEASED);await sleep(200);
    check('ordinary Grid browsing drag does not start window placement',!p._tasks._activeDrag && grid.opacity===255 && !tiler._lumaPreviewDrag);
    const start=await begin(grid);
    const original=JSON.stringify(a.assignedTile);
    for(let step=1;step<=20;step++){
        const m=Main.layoutManager.primaryMonitor;
        pointer.notify_absolute_motion(now(),start[0]+(m.width*.84-start[0])*step/20,start[1]+(m.height*.5-start[1])*step/20);
        await sleep(25);
    }
    check('holding a card reveals Desktop and the existing tiling zones',grid.opacity===0 && global.window_group.visible &&
        manager._workspaceTilingLayout.get(a.get_workspace()).showing && manager._selectedTilesPreview.showing);
    check('held real window is hidden without releasing its original tile',a.get_compositor_private().opacity===0 && JSON.stringify(a.assignedTile)===original);
    const ghost=p._tasks._activeDrag?._dragActor;
    const size=ghost?.get_transformed_size() ?? [0,0];
    check('held window keeps a bounded thumbnail',Math.max(...size)<=301 && Math.min(...size)>0,JSON.stringify(size));
    await shot('desktop-grid-drag-existing-zones');
    pointer.notify_button(now(),Clutter.BUTTON_PRIMARY,Clutter.ButtonState.RELEASED);
    await sleep(1000);
    check('side drop uses selected three-zone Desktop layout',Math.abs(a.assignedTile?.x-2/3)<.01 && Math.abs(a.assignedTile?.width-1/3)<.01 && grid._destroyed && a.get_compositor_private().opacity===255,
        JSON.stringify({tile:a.assignedTile,frame:frame(a)}));
    check('moving held window leaves its neighbor unchanged',JSON.stringify(frame(b))===neighbor);
    if(Main.layoutManager.monitors.length>1){
        const crossGrid=await open(),origin=await begin(crossGrid),target=Main.layoutManager.monitors[1];
        for(let step=1;step<=20;step++){
            pointer.notify_absolute_motion(now(),origin[0]+(target.x+target.width*.16-origin[0])*step/20,
                origin[1]+(target.y+target.height*.5-origin[1])*step/20);await sleep(25);
        }
        pointer.notify_button(now(),Clutter.BUTTON_PRIMARY,Clutter.ButtonState.RELEASED);await sleep(1000);
        check('Grid drop across monitors uses target monitor zones',a.get_monitor()===1 && Math.abs(a.assignedTile?.x)<.01 &&
            crossGrid._destroyed && JSON.stringify(frame(b))===neighbor,JSON.stringify({monitor:a.get_monitor(),tile:a.assignedTile,frame:frame(a)}));
    }
    const before=JSON.stringify({frame:frame(a),tile:a.assignedTile});
    const cancelGrid=await open();await begin(cancelGrid);
    pointer.notify_absolute_motion(now(),Main.layoutManager.primaryMonitor.width*.2,Main.layoutManager.primaryMonitor.height*.5);
    await sleep(200);
    keyboard.notify_keyval(now(),Clutter.KEY_Escape,Clutter.KeyState.PRESSED);
    keyboard.notify_keyval(now(),Clutter.KEY_Escape,Clutter.KeyState.RELEASED);
    pointer.notify_button(now(),Clutter.BUTTON_PRIMARY,Clutter.ButtonState.RELEASED);
    await sleep(700);
    check('cancel restores Grid, real window opacity, original tile and geometry',!cancelGrid._destroyed && cancelGrid.opacity===255 &&
        a.get_compositor_private().opacity===255 && JSON.stringify({frame:frame(a),tile:a.assignedTile})===before && !tiler._lumaPreviewDrag);
    LumaGrid.toggle();await sleep(400);
    const interrupted=tiler.beginWindowPreviewDrag(a);
    interrupted.update(Main.layoutManager.primaryMonitor.width*.5,Main.layoutManager.primaryMonitor.height*.5);
    tiler._createTilingManagers();
    check('rebuilding monitor managers cancels preview before disposing its zones',!tiler._lumaPreviewDrag && !interrupted.drop());
    await Main.extensionManager.disableExtension('tilingshell@ferrarodomenico.com');await sleep(300);
    const offGrid=await open();
    check('Grid respects disabled Desktop tiling without enabling it',!offGrid._cards.find(c=>c.window===a)._delegate &&
        Main.extensionManager.lookup('tilingshell@ferrarodomenico.com').state!==1);
    LumaGrid.toggle();await sleep(400);
    a.delete(global.get_current_time());b.delete(global.get_current_time());
}
