// SPDX-License-Identifier: GPL-2.0-or-later
// Actual native actors and virtual touchscreen. BlueZ lease recipient and
// devices are explicit fixtures; this is not physical Bluetooth pairing proof.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';
const sleep = ms => Scripting.sleep(ms);
const check = (value, message) => {if (!value) throw Error(message); print(`PASS picker: ${message}`);};
async function capture(name) {
    const dir=GLib.getenv('LUMA_PICKER_EVIDENCE'); if (!dir) return;
    const file=Gio.File.new_for_path(`${dir}/${name}.png`);
    const stream=file.replace(null,false,Gio.FileCreateFlags.NONE,null);
    try {await new Shell.Screenshot().screenshot(false,stream);} finally {stream.close(null);}
}
export async function run() {
    const scale = Number(GLib.getenv('LUMA_PICKER_SCALE') ?? '1');
    St.ThemeContext.get_for_stage(global.stage).scale_factor = scale;
    new Gio.Settings({schema_id:'org.gnome.desktop.interface'}).set_boolean('enable-animations',false);
    new Gio.Settings({schema_id:'org.project_luma.shell-state'}).set_string('surface-treatment',GLib.getenv('LUMA_PICKER_TREATMENT') ?? 'dark');
    Main.overview.hide(); await sleep(400);
    if (GLib.getenv('LUMA_PICKER_GREEN')) {
        St.ThemeContext.get_for_stage(global.stage).set_theme(new St.Theme({application_stylesheet:Gio.File.new_for_path('/var/tmp/luma-connectivity-audit-20261006/picker/theme.css')}));
        await sleep(200);
    }
    const qs=Main.panel.statusArea.quickSettings, menu=qs.menu;
    menu.open();await sleep(200);await capture('quick-options-main');menu.close();
    if (!(GLib.getenv('LUMA_PICKER_PHASE') ?? '').startsWith('bluetooth')) {
        const toggle=new QuickSettings.QuickMenuToggle({title:'Wi-Fi',checked:true,icon_name:'network-wireless-symbolic'});
        toggle.menu.setHeader('network-wireless-symbolic','Wi-Fi');
        toggle.menu._studioRadio=toggle;toggle.menu._studioTitle='Wi-Fi';menu.addItem(toggle);
        let activated=0;const rows=[];
        for(let i=0;i<40;i++) {const row=new QuickSettings.QuickSheetRow();row.setTitle(`Nearby network ${i+1}`);row.setState('Secured');row.connect('activate',()=>activated++);toggle.menu.addMenuItem(row);rows.push(row);}
        menu.open();toggle.menu.open();await sleep(300);
        const scroll=menu._pageScroll, adjustment=scroll.vadjustment;
        check(adjustment.upper>adjustment.page_size,'native list genuinely overflows');
        adjustment.value=0;
        const [x,y]=scroll.get_transformed_position();
        const touch=global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);
        const px=x+scroll.width/2,py=y+Math.min(220,scroll.height-30);
        touch.notify_touch_down(GLib.get_monotonic_time(),0,px,py);await sleep(40);
        for(let i=1;i<=12;i++){touch.notify_touch_motion(GLib.get_monotonic_time(),0,px,py-i*10);await sleep(16);}
        touch.notify_touch_up(GLib.get_monotonic_time(),0);await sleep(250);
        check(adjustment.value>60,'actual touchscreen drag scrolls the native picker');
        check(activated===0,'a scroll gesture does not activate a network on release');
        print(`PICKER GEOMETRY ${JSON.stringify({x,y,width:scroll.width,height:scroll.height,value:adjustment.value,rowHeight:rows[0].height})}`);
        check(rows[0].height>=52*scale,'two-line rows have usable vertical spacing at display scale');
        check(scroll.overlay_scrollbars,'scrollbar does not reserve asymmetric content width');
        check(!!scroll.get_effect('luma-page-clip'),'complete list and scrollbar share the native rounded clip');
        await capture('wifi-touch-scrolled');
        rows.at(-1).grab_key_focus();await sleep(200);
        check(adjustment.value>=adjustment.upper-adjustment.page_size-1,'keyboard can still reveal the last row');
        toggle.menu.close();menu.close();toggle.destroy();await sleep(200);
    }
    const bt=qs._bluetooth.quickSettingsItems[0], previous=bt._client;
    const device=(i)=>({paired:false,trusted:false,alias:`Nearby Bluetooth ${i}`,icon:'bluetooth-symbolic',get_object_path:()=>`/audit/device${i}`});
    const devices=Array.from({length:181},(_,i)=>device(i));
    const owner={default_adapter:'/audit/adapterA',default_adapter_setup_mode:false,get_devices:()=>({get_n_items:()=>owner.default_adapter_setup_mode?devices.length:0,get_item:i=>devices[i]})};
    const fixture={active:true,_client:owner,getDevices:()=>[]};
    let pairs=0;const pair=bt._pairAgent.pair;bt._pairAgent.pair=()=>pairs++;
    bt._client=fixture;bt.visible=true;
    try {
        menu.open();bt.menu.open();await sleep(250);
        check(owner.default_adapter_setup_mode,'opening picker starts native discovery lease');
        const first=bt._nearSection.box.get_children().find(actor=>actor._delegate instanceof QuickSettings.QuickSheetRow);
        check(first,'actual native nearby row exists');first.grab_key_focus();
        if (GLib.getenv('LUMA_PICKER_PHASE') !== 'bluetooth-expiry') {
        bt._syncNearby();await sleep(150);
        check(bt._nearSection.box.get_children().includes(first),'device bursts retain existing row actors');
        check(global.stage.key_focus===first,'discovery refresh preserves keyboard focus');
        let ticks=0,last=GLib.get_monotonic_time(),gap=0;
        const heartbeat=GLib.timeout_add(GLib.PRIORITY_DEFAULT,10,()=>{const now=GLib.get_monotonic_time();gap=Math.max(gap,now-last);last=now;ticks++;return GLib.SOURCE_CONTINUE;});
        for(let i=0;i<500;i++)previous._queueDevicesChanged();await sleep(200);
        GLib.source_remove(heartbeat);
        check(ticks>=5 && gap<200000,'native discovery bursts yield to input frames');
        print(`PICKER HEARTBEAT ${ticks} ticks maximum_gap_us=${gap}`);
        }
        await sleep(9000);
        check(owner.default_adapter_setup_mode,'discovery remains active beyond old eight-second expiry');
        check(bt._nearSection.box.get_children().includes(first),'nearby devices remain present while picker stays open');
        first._delegate.emit('activate',null);check(pairs===1,'retained nearby row remains actionable');
        await capture('bluetooth-after-nine-seconds');
        bt.menu.close();menu.close();await sleep(100);
        check(!owner.default_adapter_setup_mode,'closing picker releases its own discovery lease');
        owner.default_adapter_setup_mode=true;
        bt._scanNearby();bt._stopNearby();
        check(owner.default_adapter_setup_mode,'closing leaves an existing external discovery lease alone');
        owner.default_adapter_setup_mode=false;bt._scanNearby();owner.default_adapter='/audit/adapterB';owner.default_adapter_setup_mode=true;bt._stopNearby();
        check(owner.default_adapter_setup_mode,'adapter replacement never stops another adapter lease');
    } finally {bt._stopNearby();bt._client=previous;bt._pairAgent.pair=pair;bt._syncNearby();menu.close();}
    print('NATIVE PICKER PASS; virtual input and explicit BlueZ fixture, no physical radio claim');
}
