// SPDX-License-Identifier: GPL-2.0-or-later
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';
const results=[];
const out=GLib.getenv('LUMA_POLISH_OUTPUT');
function check(name, ok, detail='') { results.push({name,ok:!!ok,detail}); print(`${ok?'PASS':'FAIL'}: ${name} ${detail}`); }
const pause=async(ms=180)=>await Scripting.sleep(ms);
function rect(a) { const [x,y]=a.get_transformed_position(); const [w,h]=a.get_transformed_size(); return {x,y,w,h,right:x+w,bottom:y+h}; }
async function shot(name,a) {
    const b=rect(a), x=Math.max(0,Math.floor(b.x)-3),y=Math.max(0,Math.floor(b.y)-3);
    const width=Math.min(global.stage.width-x,Math.ceil(b.right)-x+6),height=Math.min(global.stage.height-y,Math.ceil(b.bottom)-y+6);
    const stream=Gio.File.new_for_path(`${out}/${name}.png`).replace(null,false,Gio.FileCreateFlags.NONE,null);
    await new Shell.Screenshot().screenshot_area(x,y,width,height,stream); stream.close(null);
}
const Notifier=GObject.registerClass({Signals:{status:{},label:{},ready:{},icon:{}}},class Notifier extends GObject.Object {
    _init(id,title){super._init();this.id=id;this.uniqueId=id;this.title=title;this.status='Active';this.icon={name:'web-browser-symbolic'};}
});
async function work() {
    const appearance=new Gio.Settings({schema_id:'org.project_luma.shell-state'});
    const iface=new Gio.Settings({schema_id:'org.gnome.desktop.interface'});
    iface.set_boolean('enable-animations',false);
    iface.set_string('clock-format','12h');
    Main.overview.hide(); await pause(800);
    const q=Main.panel.statusArea.quickSettings;
    const well=Main.shelf._well;
    check('shipped Figtree font is available',q._statusAmPm.clutter_text.get_layout().get_context().get_font_map().list_families().some(f=>f.get_name().includes('Figtree')));
    check('native status clock and Well exist',!!q._statusAmPm&&!!well);
    const trays=[];
    for(const [i,title] of ['chrome_status_icon_1','discord_status_icon','Steam'].entries()) {
        const tray=new PanelMenu.Button(0,title);
        tray._indicator=new Notifier(`luma-polish-${i}`,title);
        tray._icon=new St.Icon({icon_name:i===1?'audio-headphones-symbolic':'web-browser-symbolic',icon_size:24});
        tray.add_child(tray._icon);tray.menu.addAction('Application action',()=>{});
        Main.panel.addToStatusArea(`lumaPolish${i}`,tray);tray.show();trays.push(tray);
        well.setPinned(tray._indicator.id,false);
    }
    Main.shelf._sync();await pause(400);
    check('background app launcher has a visible icon',well._chip.child instanceof St.Icon);
    check('background app names hide status implementation IDs',well._rest.filter(s=>s.key.startsWith('luma-polish-')).every(s=>!s.name.includes('status_icon')));
    const source=well._rest.find(s=>s.key==='luma-polish-0');
    const glyph=source.makeGlyph(24);Main.layoutManager.uiGroup.add_child(glyph);await pause();
    check('background glyph preserves source artwork',glyph.child instanceof St.Icon && glyph.child.gicon.equal(trays[0]._icon.gicon));
    trays[0]._icon.gicon=new Gio.ThemedIcon({name:'audio-headphones-symbolic'});await pause();
    check('background glyph follows live artwork changes',glyph.child instanceof St.Icon && glyph.child.gicon.equal(trays[0]._icon.gicon));
    glyph.destroy();
    q.menu.open();await pause();
    const radioRow=q.menu._mainPage.get_children().find(a=>a.has_style_class_name?.('luma-options-radios'));
    check('native Quick Options radio row exists',!!radioRow);
    for(const child of radioRow.get_children()) child.hide();
    const radios=[];
    for(const title of ['Wi-Fi','Bluetooth']) {
        const control=new QuickSettings.QuickMenuToggle({title,subtitle:title==='Wi-Fi'?'Connected':'2 connected',icon_name:title==='Wi-Fi'?'network-wireless-signal-excellent-symbolic':'bluetooth-active-symbolic',toggle_mode:true,checked:true});
        q.menu.addItem(control,2);control.get_parent().remove_child(control);radioRow.add_child(control);
        control.x_expand=true;control.add_style_class_name('luma-options-radio');control.setPill(false);control.menu._studioTitle=title;control.menu._studioRadio=control;
        radios.push(control);
    }
    const section=new PopupMenu.PopupMenuSection();section.box.add_style_class_name('luma-options-list');
    const rows=[];
    for(const [i,title] of ['HiveCoworking','BuzzSecurity','Kupersmith','PeachtreeKC24G'].entries()) {
        const row=new QuickSettings.QuickSheetRow();row.setTitle(title);row.setState(i===0?'Connected':'Secured');row.setIcon('network-wireless-signal-excellent-symbolic');row.setCurrent(i===0);section.addMenuItem(row);rows.push(row);
    }
    radios[0].menu.addMenuItem(section);radios[0].menu.addMenuItem(new PopupMenu.PopupMenuItem('Wi-Fi settings'));
    const bluetoothSection=new PopupMenu.PopupMenuSection();
    const bluetoothRows=[];
    for(const [i,title] of ['BT5.0 KB','MX Master 3S','Air75 BT3.0',"Nicholas’s Buds4 Pro",...Array.from({length:10},(_,i)=>`Device ${i+1}`)].entries()) {
        // Device subclasses pass style_class; exercise that production call shape.
        const row=new QuickSettings.QuickSheetRow({style_class:'bt-device-item'});
        row.setTitle(title);row.setState(i<2?'Connected · 100%':'Not connected');
        row.setIcon('bluetooth-active-symbolic');row.setCurrent(i<2);
        bluetoothSection.addMenuItem(row);bluetoothRows.push(row);
        check('Bluetooth rows retain shared style class',row.has_style_class_name('luma-sheet-row'));
    }
    radios[1].menu.addMenuItem(bluetoothSection);

    for(const theme of ['dark','light','frost','glass']) {
        appearance.set_string('surface-treatment',theme);await pause(300);
        q.menu.close();await pause();
        for(const period of ['AM','PM','A.M.']) {
            q._statusTime.text='11:19';q._statusAmPm.text=period;q._statusAmPm.show();await pause();
            const label=q._statusAmPm, r=rect(label),frame=rect(q._statusClock);
            const layout=label.clutter_text.get_layout().copy();layout.set_width(-1);layout.set_ellipsize(Pango.EllipsizeMode.NONE);
            const [,natural]=layout.get_pixel_extents();const scale=Math.max(1,label.clutter_text.get_resource_scale());
            check(`${theme} ${period} full text fits label`,r.w+1>=natural.width/scale,JSON.stringify({allocated:r.w,natural:natural.width/scale}));
            check(`${theme} ${period} fits clock`,r.right<=frame.right+1,JSON.stringify({period:r,frame}));
        }
        await shot(`clock-${theme}`,q._statusClock);
        q.menu.open();await pause(250);
        for(const control of radios) {
            const button=control._menuButton,b=rect(button),card=rect(control);
            check(`${theme} disclosure inset`,card.right-b.right>=5,JSON.stringify({card,button:b}));
            check(`${theme} radio uses native rounded paint`,!control.get_effect('luma-control-clip'));
            check(`${theme} radio has no dark bottom rim`,control.get_theme_node().get_border_width(St.Side.BOTTOM)===0);
        }
        radios[0]._menuButton.add_style_pseudo_class('hover');await pause();
        const hoverButton=rect(radios[0]._menuButton),hoverCard=rect(radios[0]);
        check(`${theme} hovered disclosure stays inside card`,hoverButton.right<=hoverCard.right-5);
        await shot(`quick-options-${theme}`,q.menu._boxPointer);
        for(const control of radios) control._menuButton.add_style_pseudo_class('hover');
        await pause();
        for(const control of radios) {
            const card=rect(control),button=rect(control._menuButton);
            check(`${theme} hovered disclosure stays inset`,card.right-button.right>=7,JSON.stringify({card,button}));
        }
        await shot(`quick-options-hover-${theme}`,q.menu._boxPointer);
        for(const control of radios) control._menuButton.remove_style_pseudo_class('hover');

        radios[0]._menuButton.remove_style_pseudo_class('hover');
        radios[0].menu.open();await pause(300);
        const scroll=rect(q.menu._pageScroll), first=rect(rows[0]);
        check(`${theme} list has one modest inset`,first.x-scroll.x>=4 && first.x-scroll.x<=14,JSON.stringify({scroll,first}));
        for(let i=1;i<rows.length;i++) {
            const a=rect(rows[i-1]),b=rect(rows[i]);
            check(`${theme} network rows have breathing room`,b.y-a.bottom>=5,JSON.stringify({gap:b.y-a.bottom,height:a.h}));
        }
        await shot(`wifi-${theme}`,q.menu._boxPointer);
        radios[0].menu.close();await pause();
        radios[1].menu.open();await pause(250);
        for(let i=1;i<bluetoothRows.length;i++) {
            const a=rect(bluetoothRows[i-1]),b=rect(bluetoothRows[i]);
            check(`${theme} Bluetooth rows have breathing room`,b.y-a.bottom>=7,JSON.stringify({gap:b.y-a.bottom}));
            check(`${theme} Bluetooth rows keep two-line height`,a.h>=55,JSON.stringify(a));
        }
        check(`${theme} detail viewport uses no offscreen alpha mask`,!q.menu._pageScroll.get_effect('luma-page-clip'));
        check(`${theme} Bluetooth section has no nested background`,bluetoothSection.box.get_theme_node().get_background_color().alpha===0);
        await shot(`bluetooth-${theme}`,q.menu._boxPointer);
        radios[1].menu.close();q.menu.close();await pause();
        well._togglePopover();await pause(250);
        const pop=well._popover;
        check(`${theme} background popover has no redundant count`,!pop.box.get_children()[0].get_children().some(a=>a.has_style_class_name?.('luma-well-popover-count')));
        await shot(`background-apps-${theme}`,pop.actor);pop.close();await pause();
    }
    const bus=Gio.DBus.session;
    const call=(method,args)=>new Promise((resolve,reject)=>bus.call(
        'org.gnome.Mutter.DisplayConfig','/org/gnome/Mutter/DisplayConfig',
        'org.gnome.Mutter.DisplayConfig',method,args,null,Gio.DBusCallFlags.NONE,4000,null,
        (connection,result)=>{try{resolve(connection.call_finish(result));}catch(error){reject(error);}}));
    const [serial,monitors]=(await call('GetCurrentState',null)).deep_unpack();
    const [spec,modes]=monitors[0];
    const mode=modes.find(m=>m[6]['is-current']?.deep_unpack())??modes[0];
    check('monitor supports real fractional scale',mode[5].some(v=>Math.abs(v-1.25)<.001));
    await call('ApplyMonitorsConfig',new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
        [serial,1,[[0,0,1.25,0,true,[[spec[0],mode[0],{}]]]],{'layout-mode':new GLib.Variant('u',1)}]));
    await pause(600);
    for(const direction of [Clutter.TextDirection.LTR,Clutter.TextDirection.RTL]) {
        q._statusTimeRow.text_direction=direction;
        for(const period of ['AM','PM','A.M.','午後']) {
            q._statusAmPm.text=period;q._statusAmPm.show();await pause();
            const label=q._statusAmPm, r=rect(label),frame=rect(q._statusClock);
            const layout=label.clutter_text.get_layout().copy();layout.set_width(-1);layout.set_ellipsize(Pango.EllipsizeMode.NONE);
            const [,natural]=layout.get_pixel_extents(),scale=label.clutter_text.get_resource_scale();
            check(`fractional ${direction} ${period} uses scaled resources`,scale>1,`${scale}`);
            check(`fractional ${direction} ${period} full text fits`,r.w+1>=natural.width/scale);
            check(`fractional ${direction} ${period} stays inside clock`,r.x>=frame.x-1&&r.right<=frame.right+1);
        }
    }
    await shot('clock-fractional',q._statusClock);
    for(const control of radios) control.destroy();
    for(const tray of trays) tray.destroy();
}
export function init() {
    GLib.timeout_add(GLib.PRIORITY_DEFAULT,5000,()=>{
        work().catch(e=>{check('native scenario completes',false,`${e}\n${e.stack}`);logError(e);}).finally(()=>{
            GLib.file_set_contents(`${out}/result.json`,JSON.stringify({pass:results.every(r=>r.ok),results},null,2));global.context.terminate();
        });return GLib.SOURCE_REMOVE;
    });
}
