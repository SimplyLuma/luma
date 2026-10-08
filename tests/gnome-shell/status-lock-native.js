// SPDX-License-Identifier: GPL-2.0-or-later
// Run only in an owned disposable headless compositor/private profile.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PrairieLogin from 'resource:///org/gnome/shell/ui/prairieLogin.js';
import * as Osd from 'resource:///org/gnome/shell/ui/osdWindow.js';
import {SHELF_SURFACE_GAP} from 'resource:///org/gnome/shell/ui/lumaShelfSurface.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

const failures = [];
let assertions = 0;
function check(ok, message) {
    assertions++;
    print(`${ok ? 'PASS' : 'FAIL'} ${message}`);
    if (!ok) failures.push(message);
}
function rect(actor) {
    const [x,y]=actor.get_transformed_position();
    const [width,height]=actor.get_transformed_size();
    return {x,y,width,height};
}
function baseline(label) {
    const [,y]=label.clutter_text.get_transformed_position();
    return y+label.clutter_text.get_layout().get_baseline()/Pango.SCALE;
}
// A test-owned status-notifier input uses the normal native Well adapter.
// It does not replace any product layout, menu or activation method.
const NativeNotifier=GObject.registerClass({Signals:{status:{},label:{},ready:{},icon:{}}},class NativeNotifier extends GObject.Object {
    _init(){super._init();this.uniqueId='luma-shell104-probe';this.id=this.uniqueId;this.title='Native app action';this.status='Active';this.icon={name:'application-x-executable'};this.activations=0;}
    activate(){this.activations++;}
});
const ClockLayout=GObject.registerClass(class ClockLayout extends Clutter.LayoutManager {
    vfunc_allocate(container,box) {
        container.get_first_child().allocatePoster(box.get_width(),box.get_height(),
            this.cardWidth,St.ThemeContext.get_for_stage(global.stage).scale_factor,
            container.get_text_direction()===Clutter.TextDirection.RTL);
    }
});
async function shot(name) {
    const path=GLib.build_filenamev([GLib.getenv('LUMA_STATUS_LOCK_CAPTURE'),name+'.png']);
    const stream=Gio.File.new_for_path(path).replace(null,false,Gio.FileCreateFlags.NONE,null);
    await new Shell.Screenshot().screenshot(false,stream);stream.close(null);
}
export async function run() {
    const iface=new Gio.Settings({schema_id:'org.gnome.desktop.interface'});
    const appearance=new Gio.Settings({schema_id:'org.project_luma.shell-state'});
    iface.set_boolean('enable-animations',false);
    const context=St.ThemeContext.get_for_stage(global.stage);
    const q=Main.panel.statusArea.quickSettings;
    await Scripting.sleep(100);
    const initialDate=rect(q._statusDateLabel),initialFrame=rect(q._statusClock);
    check(q._statusDateLabel.visible&&initialDate.height>0&&q._statusDateLabel.text.trim().length>0,
        'Cold initial shelf date is visible before fixture font/theme changes');
    check(initialDate.y+initialDate.height<=initialFrame.y+initialFrame.height+1,
        'Cold initial shelf date fits its native frame');
    print('STATUS_COLD_NATIVE '+JSON.stringify({initialDate,initialFrame,
        monitors:Main.layoutManager.monitors.map(({x,y,width,height})=>({x,y,width,height}))}));
    context.set_font(Pango.FontDescription.from_string('Figtree 11'));
    Main.overview.hide();await Scripting.sleep(400);
    const tray=new PanelMenu.Button(0,'Native tray fixture');
    tray.add_child(new St.Icon({icon_name:'application-x-executable-symbolic',icon_size:14}));
    tray._indicator=new NativeNotifier();
    tray._icon=tray.get_first_child();
    tray.menu.addAction('Application action',()=>{});
    tray.show();
    Main.panel.addToStatusArea('lumaShell104NativeProbe',tray);
    Main.shelf._sync();
    Main.shelf._well.setPinned(tray._indicator.uniqueId,true);
    const clock=new PrairieLogin.Clock();
    const layout=new ClockLayout();layout.cardWidth=480;
    const host=new St.Widget({layout_manager:layout,width:1366,height:800});
    host.add_child(clock);Main.uiGroup.add_child(host);
    const red=GLib.getenv('LUMA_STATUS_LOCK_RED')==='1';
    const themes=red?['dark']:['light','dark','frost','glass'];
    for(const theme of themes) for(const scale of red?[1]:[1,2])
    for(const rtl of red?[false]:[false,true]) for(const format of red?['12h']:['12h','24h']) {
        appearance.set_string('surface-treatment',theme);
        iface.set_string('clock-format',format);context.scale_factor=scale;
        host.text_direction=rtl?Clutter.TextDirection.RTL:Clutter.TextDirection.LTR;
        q.text_direction=host.text_direction;
        host.set_size(1366*scale,800*scale);layout.cardWidth=480*scale;
        // Fixture stimulus changes the native privacy source actor; no layout
        // or product method is replaced. The real Shell owns all allocations.
        q._volumeInput._indicator.visible=true;
        q._syncStatusSlots();
        q._slots.mic.visible=true;
        clock._updateClock();host.queue_relayout();
        await Scripting.sleep(250);
        const controls=rect(q._statusControls);
        const shelfTime=rect(q._statusTime), shelfPeriod=rect(q._statusAmPm), shelfDate=rect(q._statusDateLabel);
        const clockBounds=rect(q._statusClock);
        check(q._statusDateLabel.visible && shelfDate.height>0 && shelfDate.width>0 && q._statusDateLabel.text.trim().length>0, 'Shelf date owns a visible nonzero allocation');
        check(shelfDate.y+shelfDate.height<=clockBounds.y+clockBounds.height+1, 'Shelf date paint allocation stays within its clock frame');
        for(const label of [q._statusTime,q._statusDateLabel,q._statusAmPm].filter(a=>a.visible)) {
            const ink=label.clutter_text.get_layout().get_pixel_extents()[0];
            const [,y]=label.clutter_text.get_transformed_position();
            check(y+ink.y>=clockBounds.y-1&&y+ink.y+ink.height<=clockBounds.y+clockBounds.height+1,
                'Actual shaped clock ink remains inside its frame');
        }
        if(q._statusAmPm.visible)check(shelfPeriod.height>0 && Math.abs(baseline(q._statusTime)-baseline(q._statusAmPm))<=1, 'Shelf period owns an allocation on the digit baseline');
        const slots=q._statusSlots.get_children().filter(a=>a.visible);
        check(slots.length===4,`${theme}/${scale}/${rtl}/${format}: privacy and three ordinary indicators present`);
        for(const slot of slots) {
            const b=rect(slot);
            check(b.x>=controls.x && b.x+b.width<=controls.x+controls.width,
                'Each visible status glyph fits inside its native control allocation');
        }
        const trayTarget=Main.shelf._well._items.get(tray);
        check(trayTarget?.mapped && trayTarget.reactive && trayTarget.can_focus,'Application tray action remains mapped and keyboard reachable');
        check(format==='24h'?!clock._ampm.visible:clock._ampm.visible&&clock._ampm.text.trim().length>0,
            'Period follows the actual native 12/24-hour setting');
        if(format==='12h')check(rect(clock._ampm).height>0, 'Lock period owns a nonzero paint allocation');
        if(format==='12h')check(Math.abs(baseline(clock._time)-baseline(clock._ampm))<=1,
            'Lock digits and period share their actual baseline');
        check(Math.abs(baseline(clock._time)-baseline(clock._date))<=1,
            'Lock date shares the time baseline instead of sagging');
        const t=rect(clock._time),d=rect(clock._date);
        const gap=rtl?t.x-(d.x+d.width):d.x-(t.x+t.width);
        check(gap<=110*scale,'Lock date stays beside measured time rather than a fixed-width slot');
        print('STATUS_LOCK_NATIVE '+JSON.stringify({theme,scale,rtl,format,controls,
            slots:slots.map(rect),shelfTime,shelfPeriod,shelfDate,clockBounds,clockPref:q._statusClock.get_preferred_height(-1),boxPref:q._statusClockBox.get_preferred_height(-1),thickness:Main.shelf._thickness,materialThickness:Main.shelf._actionsIsland._thickness,rowMetrics:clock._timeRow?._metrics().map(({width,height,baseline})=>({width,height,baseline})),tray:{target:trayTarget?rect(trayTarget):null,mapped:trayTarget?.mapped,reactive:trayTarget?.reactive,focus:trayTarget?.can_focus},time:rect(clock._time),period:rect(clock._ampm),
            date:rect(clock._date),baselines:[baseline(clock._time),baseline(clock._ampm),baseline(clock._date)]}));
        if(scale===1 && !rtl && format==='12h')await shot('status-lock-'+theme);
    }
    let trayDestroyed=false;
    if(!red) {
        context.scale_factor=1;iface.set_string('clock-format','12h');
        for(const width of [1024,800,500,360]) for(const rtl of [false,true]) {
            host.text_direction=rtl?Clutter.TextDirection.RTL:Clutter.TextDirection.LTR;
            host.set_size(width,800);layout.cardWidth=Math.min(width-40,480);
            host.queue_relayout();await Scripting.sleep(100);
            if(width-layout.cardWidth-96<220) {
                check(!clock.visible,'Narrow authentication card keeps priority over a decorative poster');
                continue;
            }
            check(clock.visible,'Poster remains visible when native card leaves space');
            const c=rect(clock),d=rect(clock._date),t=rect(clock._time),a=rect(clock._ampm);
            check(d.width>0&&d.height>0&&a.height>0,'Responsive date and period retain paint allocations');
            check(d.x>=c.x&&d.x+d.width<=c.x+c.width+1&&d.y+d.height<=c.y+c.height+1,
                'Responsive date fits its own poster');
            if(clock._divider.visible)check(Math.abs(baseline(clock._date)-baseline(clock._time))<=1,
                'Responsive beside-date follows time baseline');
            else check(d.y>=t.y+t.height,'Responsive stacked date follows below the time');
        }
        host.destroy();tray.destroy();trayDestroyed=true;await Scripting.sleep(300);
        // Native session modes are fixture stimuli; no authentication request
        // or hardware brightness write is performed by this presentation test.
        for(const mode of ['unlock-dialog','gdm']) {
            Main.sessionMode.pushMode(mode);await Scripting.sleep(200);
            check(mode==='gdm'?Main.sessionMode.isGreeter:Main.sessionMode.isLocked,
                'Actual native session mode reaches the requested OSD branch');
            for(let index=0;index<Main.layoutManager.monitors.length;index++) {
                const osd=new Osd.OsdWindow(index);
                osd.setIcon(Gio.ThemedIcon.new('display-brightness-symbolic'));
                osd.setLevel(0.24);osd.show();await Scripting.sleep(100);
                const b=rect(osd),m=Main.layoutManager.monitors[index];
                const w=Main.layoutManager.getWorkAreaForMonitor(index);
                const gap=SHELF_SURFACE_GAP*context.scale_factor;
                check(Math.abs(b.x-(m.x+(m.width-b.width)/2))<=1,
                    'Locked/greeter OSD centers on its requested monitor');
                check(Math.abs(b.y-(w.y+w.height-gap-b.height))<=1,
                    'Locked/greeter OSD uses monitor bottom inset instead of hidden shelf');
                print('OSD_NATIVE '+JSON.stringify({mode,index,osd:b,monitor:m,work:{x:w.x,y:w.y,width:w.width,height:w.height}}));
                if(index===0)await shot('osd-'+mode);
                osd.destroy();
            }
            Main.sessionMode.popMode(mode);await Scripting.sleep(200);
        }
    }
    // Record RED failures before teardown of the old Clock implementation.
    if(failures.length)throw new Error(`Native status/lock checks: ${failures.length} failures in ${assertions} assertions: ${failures.join('; ')}`);
    if(!trayDestroyed)tray.destroy();if(red)host.destroy();
    await Scripting.sleep(300);
    print(`Native status/lock checks: PASS (${assertions} assertions)`);
}
