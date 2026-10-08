// SPDX-License-Identifier: GPL-2.0-or-later
// Actual packaged Shelf + virtual seat input in an owned disposable compositor.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';
let assertions = 0;
const sleep = Scripting.sleep;
function check(value, text) {
    print(`${value ? 'PASS' : 'FAIL'} ${text}`);
    if (!value) throw new Error(text);
    assertions++;
}
function rect(actor) {
    const b = actor.get_transformed_extents();
    return {x:b.get_x(), y:b.get_y(), w:b.get_width(), h:b.get_height()};
}
function center(actor) { const b=rect(actor); return [b.x+b.w/2,b.y+b.h/2]; }
export async function run() {
    const shelf = Main.shelf;
    const s = shelf._settings;
    const iface = new Gio.Settings({schema_id:'org.gnome.desktop.interface'});
    new Gio.Settings({schema_id:'org.gnome.shell'}).set_strv('enabled-extensions', []);
    Main.overview.hide();
    s.set_strv('dock-folders', ['file:///var/tmp/luma-dash-native/folder']);
    s.set_boolean('dock-folders-visible', true);
    s.set_boolean('dock-folders-separate', false);
    s.set_string('shelf-surface-mode', 'separate');
    s.set_boolean('shelf-free-placement', false);
    const arrange = shelf._arrange;
    const seat = global.stage.context.get_backend().get_default_seat();
    const pointer=seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const keyboard=seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    const move=async (x,y,delay=45) => {pointer.notify_absolute_motion(GLib.get_monotonic_time(),x,y);await sleep(delay);};
    const press=async down => {pointer.notify_button(GLib.get_monotonic_time(),Clutter.BUTTON_PRIMARY,
        down?Clutter.ButtonState.PRESSED:Clutter.ButtonState.RELEASED);await sleep(45);};
    async function key(symbol) {
        keyboard.notify_keyval(GLib.get_monotonic_time(),symbol,Clutter.KeyState.PRESSED);
        keyboard.notify_keyval(GLib.get_monotonic_time(),symbol,Clutter.KeyState.RELEASED);
        await sleep(200);
    }
    function groups() { return s.get_value('shelf-arrangement').recursiveUnpack(); }
    function group(id) { return groups().find(g=>g.islands.includes(id)); }
    async function reset() {
        arrange.end();
        shelf.writeArrangement([
            {display:'',edge:'bottom',anchor:'center',position:0.5,islands:['dock','folders']},
            {display:'',edge:'top',anchor:'end',position:0.5,islands:['well','quick-options','clock']},
            {display:'',edge:'top',anchor:'start',position:0.5,islands:['live','live2','media','notifications']},
        ]);
        await sleep(500);
    }
    async function grab(id) {
        if (id==='live') { arrange.begin({select:id}); await sleep(300); }
        const origin=center(shelf.partActorFor(id));
        await move(...origin,100); await press(true);
        if (id==='live') await move(origin[0]+12,origin[1]+12,200);
        else await sleep(520);
        check(arrange.active, `${id}: real pointer hold enters arrangement`);
        check(!!arrange._drag, `${id}: grab is visible before any motion`);
        check(!arrange._card, `${id}: no blocking arrangement modal`);
        const carrier=arrange._dragGroup();
        check(carrier?.mapped && carrier.scale_x > 1, `${id}: carrier is visibly lifted at recognition`);
        return {origin,carrier};
    }
    async function track(id, to, carrier) {
        const start=global.get_pointer();
        for(let step=1;step<=16;step++) {
            const x=start[0]+(to[0]-start[0])*step/16;
            const y=start[1]+(to[1]-start[1])*step/16;
            await move(x,y,35);
            for (let wait=0;wait<20 && Math.hypot(arrange._drag.pointer[0]-x,arrange._drag.pointer[1]-y)>1;wait++)
                await sleep(10);
            check(Math.hypot(arrange._drag.pointer[0]-x,arrange._drag.pointer[1]-y)<=1,
                `${id}: virtual pointer motion reached native drag handler step ${step}`);
            // Sample actual painted allocation, not a requested position
            // while Mutter still has the virtual input queued.
            await new Promise(resolve=> {
                const painted=global.stage.connect_after('after-paint',()=> {global.stage.disconnect(painted);resolve();});
                global.stage.queue_redraw();
            });
            check(arrange._dragGroup()===carrier, `${id}: carrier identity survives preview step ${step}`);
            const drag=arrange._drag;
            const held=drag.ids.length>1?carrier:shelf.partActorFor(id);
            const r=rect(held);
            const turned=drag.formEdge && ['left','right'].includes(drag.formEdge)!==['left','right'].includes(drag.edge);
            const [fx,fy]=turned?[...drag.grabFraction].reverse():drag.grabFraction;
            const error=Math.hypot(r.x+r.w*fx-x,r.y+r.h*fy-y);
            print(`ACTUAL ${id} held-point-error=${error.toFixed(2)} opacity=${carrier.surface.opacity} rect=${JSON.stringify(r)} pointer=${x},${y} frac=${fx},${fy} group=${carrier.x},${carrier.y},${carrier.width},${carrier.height} scale=${carrier.scale_x}`);
            check(error<=3, `${id}: visible pressed point stays under pointer step ${step}`);
            check(carrier.surface.opacity===255 && carrier.surface.translation_x===0 && carrier.surface.translation_y===0,
                `${id}: no inherited surface flicker/translation step ${step}`);
        }
    }
    for(const animations of [true,false]) {
        iface.set_boolean('enable-animations',animations);
        s.set_string('surface-treatment',animations?'light':'dark');
        await reset();
        const folders=shelf.dockFolders;
        check(folders.rail.get_parent()===shelf._dockMaterial, 'initial adjacent folders really join the dock');
        let {carrier}=await grab('folders');
        check(folders.rail.get_parent()===shelf._foldersMaterial, 'direct grab detaches real rail with Settings toggle unchanged');
        await track('folders',[global.stage.width/2,22],carrier);
        await press(false); await sleep(400); arrange.end(); await sleep(300);
        check(group('folders')!==group('dock') && group('folders')?.edge==='top', 'actual pointer drop persists detached folder placement');
        check(s.get_boolean('dock-folders-separate')===false, 'direct detach does not require changing legacy toggle');
        shelf._sync(); await sleep(150);
        check(folders.rail.get_parent()===shelf._foldersMaterial, 'saved detach survives a fresh layout read');
        ({carrier}=await grab('folders'));
        const dock=rect(shelf.partActorFor('dock'));
        await track('folders',[dock.x+dock.w+10,dock.y+dock.h/2],carrier);
        check(arrange._drag.target?.kind==='attach', 'real pointer chooses dock attachment');
        await press(false); await sleep(400); arrange.end(); await sleep(300);
        check(group('dock').islands.includes('folders'), 'actual pointer join persists folder in dock group');
        check(folders.rail.get_parent()===shelf._dockMaterial, 'joined saved rail returns to actual dock actor');
        await reset();
        ({carrier}=await grab('live'));
        const liveDock=rect(shelf.partActorFor('dock'));
        await track('live',[liveDock.x-10,liveDock.y+liveDock.h/2],carrier);
        check(arrange._drag.target?.kind==='attach','native Live extensions slot joins dock directly');
        await press(false); await sleep(400);
        check(group('dock').islands.includes('live') && group('dock').islands.includes('media'),
            'Live extensions family attachment persists together');
        // In arrange mode live content is intentionally represented by the
        // real shared slot; drag that slot away to separate its entire family.
        ({carrier}=await grab('live'));
        await track('live',[global.stage.width/2,22],carrier);
        await press(false); await sleep(400); arrange.end(); await sleep(300);
        check(group('live')!==group('dock') && group('live').edge==='top' && group('live').islands.includes('media'),
            'native Live extensions family detaches and persists together');
        await reset();
        for(const id of ['quick-options','clock','live']) {
            const before=JSON.stringify(groups());
            ({carrier}=await grab(id));
            await track(id,[global.stage.width/2,global.stage.height/2],carrier);
            await key(Clutter.KEY_Escape); await press(false); arrange.end(); await sleep(250);
            check(JSON.stringify(groups())===before, `${id}: Escape cancels without corrupting persisted arrangement`);
        }
    }
    print(`DASH DIRECT ${assertions} assertions PASS`);
}
