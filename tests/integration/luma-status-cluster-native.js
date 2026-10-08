// SPDX-License-Identifier: GPL-2.0-or-later
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import St from 'gi://St';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';
export function init() {
    if (GLib.getenv('LUMA_DASH_DIRECT') !== '1' || GLib.getenv('GSETTINGS_BACKEND') !== 'keyfile')
        throw new Error('Status checks require the disposable headless runner and private keyfile settings');
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 2500, () => {
        checks().catch(e => console.error(`${e.message}\n${e.stack}`)).finally(() => global.context.terminate());
        return GLib.SOURCE_REMOVE;
    });
}
export async function run() { await new Promise(() => {}); }
function require(ok, message) { if (!ok) throw new Error(message); }
async function checks() {
    const s = new Gio.Settings({schema_id:'org.project_luma.shell-state'});
    new Gio.Settings({schema_id:'org.gnome.desktop.interface'}).set_boolean('enable-animations', false);
    const q = Main.panel.statusArea.quickSettings;
    if (GLib.getenv('LUMA_STATUS_SCALE') === '2') St.ThemeContext.get_for_stage(global.stage).scale_factor = 2;
    const rtl = GLib.getenv('LUMA_STATUS_RTL') === '1';
    q.text_direction = rtl ? Clutter.TextDirection.RTL : Clutter.TextDirection.LTR;
    Main.shelf._group.text_direction = q.text_direction;
    require(q._statusActive, 'Native status cluster not active');
    require(Main.shelf._actionsIsland._content === q, 'Whole material is not the actual menu button');
    require(!Main.panel.statusArea.dateMenu.reactive, 'Old date button is reactive');
    s.set_boolean('status-divider', true);
    let count = 0;
    for (const edge of ['bottom','top','left','right']) {
      for (const scope of ['all','controls','split','joined','flush']) {
        for (const frame of ['none','stroke','fill','both']) {
        for (const separate of [false,true]) for (const right of [false,true]) {
          s.set_string('shelf-surface-mode',separate?'separate':'connected');
          s.set_boolean('status-controls-right',right);
          const padding = frame==='stroke'?4:frame==='fill'?24:10;
          s.set_int('shelf-padding',padding);
          s.set_string('shelf-material', ['dark','light','frost','glass'][['none','stroke','fill','both'].indexOf(frame)]);
          s.set_string('shelf-edge', edge);s.set_string('status-frame-scope',scope);s.set_string('status-frame',frame);
          await Scripting.sleep(100);
          const vertical = ['left','right'].includes(edge);
          const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
          require(Math.abs((vertical?q.width:q.height)-(36+2*padding)*scale)<1, `${edge}/${scope}/${frame} thickness ${q.width}x${q.height}`);
          require(q._statusTime.mapped && q._statusDateLabel.mapped, 'Clock not mapped');
          const lightMaterial = ['light', 'frost'].includes(s.get_string('shelf-material'));
          const foreground = q._statusTime.get_theme_node().get_foreground_color();
          require(lightMaterial ? foreground.red < 40 : foreground.red > 200, 'Clock foreground contradicts material');
          require(q._statusTime.clutter_text.get_attributes().to_string().includes('foreground'), 'Line-height adjustment erased native text paint');
          require(q._statusDateLabel.clutter_text.get_attributes().to_string().includes('foreground'), 'Date native text paint missing');
          const [qx,qy] = q.get_transformed_position();
          const [vx,vy] = q._statusViewport.get_transformed_position();
          const [vw,vh] = q._statusViewport.get_transformed_size();
          const expected = scope==='flush'?0:padding*scale;
          require(Math.abs(vx-qx-expected)<1 && Math.abs(vy-qy-expected)<1, `${edge}/${scope}/${frame}: uneven leading padding`);
          require(Math.abs((vertical?q.width-vw:q.height-vh)-2*expected)<1, `${edge}/${scope}/${frame}/${separate}/${right}: uneven trailing padding q=${q.width}x${q.height}, viewport=${vw}x${vh}, expected=${expected}, pos=${qx},${qy}/${vx},${vy}`);
          const [cx,cy] = q._statusClock.get_transformed_position();
          if (!vertical && frame === 'none' && scope !== 'flush') {
            const natural = q._statusClockBox.get_preferred_width(-1)[1];
            require(Math.abs(q._statusClock.width - natural - (scope === 'joined' ? padding * scale : 0)) <= 1, 'Unexplained extra clock width');
          }
          const seamNode = q._statusSeam.get_theme_node();
          require(q._statusSeam.mapped && seamNode.get_background_color().alpha > 0, 'Divider missing with optional frame disabled');
          const joined = ['joined', 'flush'].includes(scope);
          const before = vertical ? St.Side.TOP : St.Side.LEFT;
          const after = vertical ? St.Side.BOTTOM : St.Side.RIGHT;
          const inset = joined ? 0 : padding * scale;
          require(seamNode.get_margin(before) === inset && seamNode.get_margin(after) === inset, 'Divider gaps do not match Dash padding');
          const [gx,gy] = q._statusControls.get_transformed_position();
          require(vertical ? (cy<gy)===right : (cx<gx)===(right!==rtl), 'Order does not follow direction');
          for (const child of q._indicators.get_children().filter(actor=>actor.visible))
            require(child.width===13*scale && child.height===13*scale, 'Glyph not 13px');
          if (scope!=='flush') {
            const [dx,dy] = q._statusDateLabel.get_transformed_position();
            const [dw,dh] = q._statusDateLabel.get_transformed_size();
            require(dy+dh <= qy+q.height-expected+1, `${edge}/${scope}/${frame}/${separate}/${right}: Date clipped below content q=${qx},${qy}/${q.width},${q.height} date=${dx},${dy}/${dw},${dh} expected=${expected}`);
            require(dx+dw <= qx+q.width-expected+1, `${edge}/${scope}/${frame}/${separate}/${right}: Date clipped past content q=${qx},${qy}/${q.width},${q.height} date=${dx},${dy}/${dw},${dh} expected=${expected}`);
          }
          count++;
        }
        }
      }
    }
    s.set_int('shelf-padding',10);s.set_string('shelf-material','dark');s.set_string('shelf-surface-mode','separate');s.set_boolean('status-controls-right',true);
    for (const edge of ['bottom','left']) {
      s.set_string('shelf-edge',edge);
      for (const scope of ['all','joined','flush']) {
        s.set_string('status-frame-scope',scope);s.set_string('status-frame','both');
        await Scripting.sleep(150);
        const path = GLib.build_filenamev([GLib.get_home_dir(), `${edge}-${scope}.png`]);
        const stream = Gio.File.new_for_path(path).replace(null,false,Gio.FileCreateFlags.NONE,null);
        await new Shell.Screenshot().screenshot(false,stream);stream.close(null);
      }
    }
    s.set_string('shelf-edge','bottom');s.set_string('status-frame-scope','all');
    s.set_int('status-time-size',20);s.set_int('status-date-size',14);
    await Scripting.sleep(100);
    console.log(`MAXTYPE time=${q._statusTime.get_transformed_position()}/${q._statusTime.get_transformed_size()} date=${q._statusDateLabel.get_transformed_position()}/${q._statusDateLabel.get_transformed_size()}`);
    const [qx,qy] = q.get_transformed_position();
    const [tx,ty] = q._statusTime.get_transformed_position();
    const [tw,th] = q._statusTime.get_transformed_size();
    const [dx,dy] = q._statusDateLabel.get_transformed_position();
    const [dw,dh] = q._statusDateLabel.get_transformed_size();
    const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
    require(ty >= qy+9*scale && dy+dh <= qy+q.height-9*scale && ty+th <= dy, 'Large text exceeds padded content');
    s.set_int('status-time-size',13);s.set_int('status-date-size',9);
    await Scripting.sleep(150);
    const seat = Clutter.get_default_backend().get_default_seat();
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    for (const target of [q._statusTime,q._statusDateLabel,q._statusControls,q]) {
        const [x,y] = target.get_transformed_position();
        const [w,h] = target.get_transformed_size();
        pointer.notify_absolute_motion(GLib.get_monotonic_time(),x+(target===q?2:w/2),y+(target===q?2:h/2));
        await Scripting.sleep(80);
        require(q.hover && q._statusHover.opacity===255, 'Whole control hover failed');
        pointer.notify_button(GLib.get_monotonic_time(),Clutter.BUTTON_PRIMARY,Clutter.ButtonState.PRESSED);
        pointer.notify_button(GLib.get_monotonic_time(),Clutter.BUTTON_PRIMARY,Clutter.ButtonState.RELEASED);
        await Scripting.sleep(100);
        require(q.menu.isOpen && !Main.panel.statusArea.dateMenu.menu.isOpen, 'Pointer did not open only Quick Options');
        q.menu.close();await Scripting.sleep(100);
    }
    for (const key of [Clutter.KEY_Return, Clutter.KEY_space]) {
        q.grab_key_focus();
        keyboard.notify_keyval(GLib.get_monotonic_time(),key,Clutter.KeyState.PRESSED);
        keyboard.notify_keyval(GLib.get_monotonic_time(),key,Clutter.KeyState.RELEASED);
        await Scripting.sleep(100);
        require(q.menu.isOpen, 'Keyboard activation failed');q.menu.close();
    }
    const focusable = [];
    const visit = actor => { if (actor.can_focus) focusable.push(actor);actor.get_children().forEach(visit); };
    visit(q);require(focusable.length===1 && focusable[0]===q, 'Cluster has multiple tab stops');
    console.log('INPUT PASS '+q.accessible_name);
    Main.panel.toggleCalendar();await Scripting.sleep(100);
    require(q.menu.isOpen && !Main.panel.statusArea.dateMenu.menu.isOpen, 'Calendar key did not open Quick Options');
    q.menu.close();q.openNotifications();await Scripting.sleep(100);
    require(q._notifications.isOpen, 'Notifications not accessible');
    require(q._notifications.box.contains(Main.panel.statusArea.dateMenu._messageList), 'History missing');
    q._notifications.close();
    require(q._statusDateLabel.text===q.menu._lumaDate.text, 'Menu date differs from shelf');
    Main.shelf.hide();
    Main.shelf._restorePanelActors();await Scripting.sleep(100);
    require(!q._statusActive && Main.panel.statusArea.dateMenu.reactive, 'Panel restoration failed');
    Main.shelf._sync();await Scripting.sleep(100);
    require(q._statusActive && !Main.panel.statusArea.dateMenu.reactive, 'Desktop recovery failed');
    console.log(`PASS native status cluster ${count}`);
}
