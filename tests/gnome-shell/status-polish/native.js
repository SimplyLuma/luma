// SPDX-License-Identifier: GPL-2.0-or-later
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {BrightnessScale} from 'resource:///org/gnome/shell/misc/brightnessManager.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as PrairieLogin from 'resource:///org/gnome/shell/ui/prairieLogin.js';
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
async function checkBattery(q, appearance, iface, suffix) {
    const power = q._system._systemItem.powerToggle;
    const old = {visible: power.visible, title: power.title,
        percent: iface.get_boolean('show-battery-percentage'), edge: appearance.get_string('shelf-edge')};
    try {
        // Synthetic battery availability; the actual indicator/settings binding,
        // dock measurement and rendered text are production actors.
        power.visible = true;
        power.title = '42%';
        for (const edge of ['bottom', 'left', 'right', 'top']) {
            appearance.set_string('shelf-edge', edge);
            Main.shelf._sync();
            for (const direction of [Clutter.TextDirection.LTR, Clutter.TextDirection.RTL]) {
                q._statusSlots.text_direction = direction;
                iface.set_boolean('show-battery-percentage', true);
                await pause(200);
                const slot = q._slots.battery;
                const label = slot.percentage;
                const r = rect(label), cell = rect(slot), grid = rect(q._statusSlots);
                check(`${suffix} ${edge} ${direction} battery percent follows Settings`, label.visible && label.text === '42%');
                check(`${suffix} ${edge} ${direction} percent fits cell`, r.x >= cell.x - 1 && r.right <= cell.right + 1 && r.bottom <= cell.bottom + 1);
                check(`${suffix} ${edge} ${direction} battery fits grid`, cell.x >= grid.x - 1 && cell.right <= grid.right + 1 && cell.bottom <= grid.bottom + 1);
                if (direction === Clutter.TextDirection.LTR)
                    await shot(`battery-${suffix}-${edge}`, q._statusControls);
                iface.set_boolean('show-battery-percentage', false);
                await pause();
                check(`${suffix} ${edge} ${direction} percent hides live`, !label.visible);
            }
        }
        power.visible = false;
        await pause();
        check(`${suffix} no battery hides dock battery slot`, !q._slots.battery.visible);
    } finally {
        power.title = old.title; power.visible = old.visible;
        iface.set_boolean('show-battery-percentage', old.percent);
        appearance.set_string('shelf-edge', old.edge);
        q._statusSlots.text_direction = Clutter.TextDirection.LTR;
        Main.shelf._sync();
    }
}
async function checkLogin() {
    const button = new PrairieLogin.PowerButton();
    const card = new St.BoxLayout({style_class: 'poster-card', x: 200, y: 150,
        width: 460, height: 400});
    card.add_child(button);
    Main.uiGroup.add_child(card);
    try {
        button._items.forEach(item => item.show());
        button._menu.open(); await pause(300);
        const rows = button._items.map(rect);
        check('login menu has exactly three actions', button._menu.box.get_children().length === 3);
        for (let i = 1; i < rows.length; i++)
            check(`login action ${i} has no spacer gap`, rows[i].y - rows[i - 1].bottom <= 8, JSON.stringify(rows));
        const color = card.get_theme_node().get_background_color();
        check('login card surface is solid', color.alpha === 255);
        check('login card has no background image', !card.get_theme_node().get_background_image());
        await shot('login-power-menu', button._menu.actor);
        button._menu.close(); await pause();
        await shot('login-card-surface', card);
    } finally {card.destroy();}
}
async function checkBrightness() {
    const manager = Main.brightnessManager;
    const originalScale = manager._globalScale;
    let modalGrab = null;
    const modal = new St.Widget({reactive: true, can_focus: true, width: 10, height: 10});
    Main.uiGroup.add_child(modal);
    const originalDimming = manager._dimmingEnabled;
    const scale = new BrightnessScale('Fixture brightness', 0.50);
    const keyboard = Clutter.get_default_backend().get_default_seat()
        .create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    const key = async (value, duration = 100) => {
        keyboard.notify_keyval(GLib.get_monotonic_time(), value, Clutter.KeyState.PRESSED);
        await pause(duration);
        keyboard.notify_keyval(GLib.get_monotonic_time(), value, Clutter.KeyState.RELEASED);
        await pause(100);
    };
    try {
        manager._globalScale = scale;
        for (const [name, mode] of [['desktop', Shell.ActionMode.NORMAL], ['locked', Shell.ActionMode.LOCK_SCREEN]]) {
            if (modalGrab) Main.popModal(modalGrab);
            modalGrab = Main.pushModal(modal, {actionMode: mode});
            scale.value = .50;
            manager._dimmingEnabled = true;
            await key(Clutter.KEY_MonBrightnessUp);
            check(`${name} brightness key advances one percent`, Math.abs(scale.value - .51) < .00001, `${scale.value}`);
            check(`${name} brightness key clears idle dimming`, !manager.dimming);
            await key(Clutter.KEY_MonBrightnessDown);
            check(`${name} brightness down advances one percent`, Math.abs(scale.value - .50) < .00001, `${scale.value}`);
        }
        scale.value = .50;
        let changes = 0, previous = scale.value, deltas = [];
        const id = scale.connect('notify::value', () => {
            changes++; deltas.push(scale.value - previous); previous = scale.value;
        });
        await key(Clutter.KEY_MonBrightnessUp, 900);
        scale.disconnect(id);
        check('held brightness key repeats', changes > 1, `${changes} changes`);
        check('each repeated brightness key uses one percent', deltas.every(delta => Math.abs(delta - .01) < .00001));
        scale.value = .01; scale.stepDown(); scale.stepDown();
        check('brightness lower bound stays at zero', scale.value === 0);
        scale.value = .99; scale.stepUp(); scale.stepUp();
        check('brightness upper bound stays at one hundred', scale.value === 1);
        const coarse = new BrightnessScale('Coarse hardware', .5, 10);
        coarse.stepDown();
        check('coarse backlight retains real hardware granularity', Math.abs(coarse.value - .4) < .00001);
        check('brightness keys registered while locked',
            Boolean(Main.wm._allowedKeybindings['screen-brightness-up'] & Shell.ActionMode.LOCK_SCREEN));
    } finally {
        manager._globalScale = originalScale;
        manager._dimmingEnabled = originalDimming;
        if (modalGrab) Main.popModal(modalGrab);
        modal.destroy();
    }
}
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
    await checkBattery(q, appearance, iface, '100');
    await checkLogin();
    await checkBrightness();
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
    await checkBattery(q, appearance, iface, '125');
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
