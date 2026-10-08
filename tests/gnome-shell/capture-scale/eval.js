// Headless oracle for Shell .142 (0181-0183): saved capture sizes per monitor
// scale (1920x1200 at 1.25 beside 5120x1440 at 1.0), each screenshot shortcut's
// mode and crosshair, the mode order, Escape, the Options menu, quiet window
// attention and the notification island's fold. Run with the capture harness:
// C_EVAL=<this file> S142_LAYOUT=dual|single C_MONITORS=1920x1200,5120x1440 go.sh
import Clutter from 'gi://Clutter';
import GdkPixbuf from 'gi://GdkPixbuf';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
const log = (m, o) => console.log(`[s142] ${m} ${JSON.stringify(o ?? {})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const call = (method, params, sig) => new Promise((resolve, reject) => Gio.DBus.session.call('org.gnome.Mutter.DisplayConfig',
    '/org/gnome/Mutter/DisplayConfig', 'org.gnome.Mutter.DisplayConfig', method, params,
    sig ? new GLib.VariantType(sig) : null, 0, -1, null, (c, r) => { try { resolve(c.call_finish(r)); } catch (e) { reject(e); } }));
export default async function ({Main, shot}) {
    const home = GLib.get_home_dir();
    const [serial, monitors] = (await call('GetCurrentState', null)).deepUnpack();
    const cur = m => (m[1].find(x => x[6]?.['is-current']?.deepUnpack?.()) ?? m[1][0])[0];
    const dual = GLib.getenv('S142_LAYOUT') === 'dual';
    const logical = [[0, 0, 1.25, 0, !dual, [[monitors[0][0][0], cur(monitors[0]), {}]]]];
    if (dual)
        logical.push([1536, 0, 1.0, 0, true, [[monitors[1][0][0], cur(monitors[1]), {}]]]);
    await call('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})', [serial, 1, logical, {}]));
    await sleep(4000);
    const ui = Main.screenshotUI;
    const Capture = await import('resource:///org/gnome/shell/ui/lumaCapture.js');
    const mons = Main.layoutManager.monitors.map(m => [m.x, m.y, m.width, m.height]);
    log('layout', {mons, views: global.stage.peek_stage_views().map(v => v.get_scale())});
    const dir = `${home}/Pictures/Screenshots`;
    GLib.mkdir_with_parents(dir, 0o755);
    const newest = () => {
        const e = Gio.File.new_for_path(dir).enumerate_children('standard::name,time::modified', 0, null);
        let best = null, t = -1, i;
        while ((i = e.next_file(null))) { const m = i.get_modification_date_time().to_unix_usec(); if (m > t) { t = m; best = i.get_name(); } }
        return best ? `${dir}/${best}` : null;
    };
    const results = [];
    const take = async (name, setup, expect) => {
        await ui.open();
        await sleep(900);
        setup();
        await sleep(300);
        const before = newest();
        await ui._saveScreenshot();
        ui.close(true);
        await sleep(1200);
        const f = newest();
        const pb = f && f !== before ? GdkPixbuf.Pixbuf.new_from_file(f) : null;
        const got = pb ? [pb.width, pb.height] : null;
        const pass = !!got && Math.abs(got[0] - expect[0]) <= 1 && Math.abs(got[1] - expect[1]) <= 1;
        results.push({name, got, expect, pass});
        log('case', results.at(-1));
        if (pb) GLib.spawn_command_line_sync(`cp "${f}" ${GLib.getenv('ORACLE_OUT')}/${name}.png`);
    };
    const sel = (x, y, w, h) => () => { ui._setCaptureMode(Capture.Mode.SELECTION); ui._areaSelector.setSelection(x, y, w, h); };
    await take('sel-laptop', sel(100, 100, 636, 257), [795, 321]);
    if (dual) {
        await take('sel-external', sel(2000, 100, 636, 257), [636, 257]);
        await take('sel-span', sel(1400, 100, 300, 200), [375, 250]);
        const scr = i => () => { ui._setCaptureMode(Capture.Mode.SCREEN); ui._screenSelectors.forEach((s, j) => { s.checked = j === i; }); };
        await take('screen-laptop', scr(0), [1920, 1200]);
        await take('screen-external', scr(1), [5120, 1440]);
    } else {
        const scr = () => { ui._setCaptureMode(Capture.Mode.SCREEN); ui._screenSelectors[0].checked = true; };
        await take('screen-laptop', scr, [1920, 1200]);
    }
    // Windows: each on its monitor, captured at that monitor's scale.
    const win = t => global.get_window_actors().map(a => a.metaWindow).find(w => w.get_title() === t);
    const takeWindow = async (name, title, x, y, scale) => {
        const w = win(title);
        if (!w) { results.push({name, error: 'no window'}); return; }
        w.move_frame(true, x, y);
        w.activate(global.get_current_time());
        await sleep(800);
        const fr = w.get_frame_rect(), br = w.get_buffer_rect();
        await take(name, () => {
            ui._setCaptureMode(Capture.Mode.WINDOW);
            const all = ui._windowSelectors.flatMap(sel => sel.windows());
            all.forEach(x => { x.checked = x.window?.metaWindow === w || x.metaWindow === w || x.windowActor?.metaWindow === w; });
            log('window-pick', {checked: all.filter(x => x.checked).length, frame: [fr.width, fr.height], buffer: [br.width, br.height]});
        }, [Math.round(br.width * scale), Math.round(br.height * scale)]);
        results.at(-1).buffer = [br.width, br.height];
    };
    await takeWindow('window-laptop', 'Claude', 200, 150, Number(GLib.getenv('S142_LAPTOP_BUFFER') || 2));
    if (dual)
        await takeWindow('window-external', 'Claude', 2400, 200, 1.0);
    // Shortcuts: each opens its own mode, whatever was used last; the
    // crosshair is there at once in Selection.
    const seat0 = global.stage.context.get_backend().get_default_seat();
    const kb0 = seat0.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    const chord = async n => {
        let t = GLib.get_monotonic_time();
        const keys = [Clutter.KEY_Control_L, Clutter.KEY_Shift_L, Clutter[`KEY_${n}`]];
        for (const k of keys) kb0.notify_keyval(t += 1000, k, Clutter.KeyState.PRESSED);
        for (const k of keys.reverse()) kb0.notify_keyval(t += 1000, k, Clutter.KeyState.RELEASED);
        await sleep(1500);
    };
    for (const [first, n, want] of [['window', 1, 'selection'], ['selection', 2, 'window'], ['window', 3, 'screen'], ['screen', 1, 'selection']]) {
        ui._options.lastMode = first;
        await chord(n);
        const cursor = ui._areaSelector.cursor_type;
        results.push({name: `ctrl-shift-${n}-after-${first}`, mode: ui._captureMode, want, visible: ui.visible,
            cursor: want === 'selection' ? cursor : undefined,
            pass: ui.visible && ui._captureMode === want && (want !== 'selection' || cursor === Clutter.CursorType.CROSSHAIR)});
        log('case', results.at(-1));
        ui.close(true);
        await sleep(600);
    }
    // Mode buttons in shortcut order, with the shortcut in the tooltip text.
    {
        await ui.open();
        await sleep(600);
        const order = [...ui._bar.modeButtons.entries()].map(([m, b]) => [m, b.accessible_name, b.accessible_description ?? '']);
        const want = ['selection', 'window', 'screen', 'record-selection', 'record-screen'];
        results.push({name: 'bar-order', order, pass: JSON.stringify(order.map(o => o[0])) === JSON.stringify(want) &&
            order[0][2] === 'Ctrl+Shift+1' && order[1][2] === 'Ctrl+Shift+2' && order[2][2] === 'Ctrl+Shift+3'});
        log('case', results.at(-1));
        ui.close(true);
        await sleep(600);
    }
    // Window attention: no notification, ever.
    {
        const before = Main.messageTray.getSources().length;
        const other = global.get_window_actors().map(a => a.metaWindow).find(w => !w.has_focus() && !w.is_skip_taskbar());
        if (other)
            Main.windowAttentionHandler._onWindowDemandsAttention(global.display, other);
        await sleep(500);
        results.push({name: 'attention-no-notification', window: other?.get_title(), sources: [before, Main.messageTray.getSources().length],
            pass: !!other && Main.messageTray.getSources().length === before});
        log('case', results.at(-1));
    }
    // The notification island leaves by folding into its edge: width kept,
    // the last notification still drawn, height and opacity easing together.
    const MT = await import('resource:///org/gnome/shell/ui/messageTray.js');
    const St = (await import('gi://St')).default;
    const islandCase = async name => {
        const island = Main.shelf?._beaconIsland;
        const source = new MT.Source({title: 'Oracle', iconName: 'dialog-information-symbolic'});
        Main.messageTray.add(source);
        const n = new MT.Notification({source, title: 'Oracle', body: 'Leaving test'});
        source.addNotification(n);
        await sleep(6000);
        const animate = St.Settings.get().enable_animations;
        const shown = island ? [island.visible, Math.round(island.width), island.opacity] : null;
        n.destroy();
        await sleep(60);
        const mid = island ? {sx: island.scale_x, sy: island.scale_y, op: island.opacity, w: Math.round(island.width),
            beacon: Main.shelf._beacon.visible, pivot: island.pivot_point.y} : null;
        await sleep(700);
        results.push({name, animate, shown, mid, after: island?.visible,
            pass: !!mid && shown?.[0] === true && mid.w >= shown[1] - 1 && mid.beacon && mid.op < 255 &&
                (animate ? mid.sx === 1 && mid.sy < 1 : mid.sy === 1) && island.visible === false && !Main.shelf._beacon.visible});
        log('case', results.at(-1));
    };
    await islandCase('island-leaves-reduced-or-default');
    try { St.Settings.get().uninhibit_animations(); } catch (e) { log('uninhibit', {e: `${e}`}); }
    await islandCase('island-leaves-animated');
    // Escape closes the tool.
    {
        const seat = global.stage.context.get_backend().get_default_seat();
        const kb = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        await ui.open();
        await sleep(900);
        const t = GLib.get_monotonic_time();
        kb.notify_keyval(t, Clutter.KEY_Escape, Clutter.KeyState.PRESSED);
        kb.notify_keyval(t + 1000, Clutter.KEY_Escape, Clutter.KeyState.RELEASED);
        await sleep(800);
        results.push({name: 'escape-closes', pass: !ui.visible});
        log('case', results.at(-1));
        ui.close(true);
    }
    // The Options menu with Nick's shelf, on the primary (external) monitor.
    {
        const st = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});
        st.set_string('shelf-edge-mode', 'protruding'); st.set_boolean('shelf-float-ends', false);
        st.set_int('shelf-padding', 10); st.set_string('surface-treatment', 'dark');
        await sleep(1500);
        await ui.open();
        await sleep(900);
        if (GLib.getenv('S142_MENU_LAPTOP')) { ui._barPlace = null; }
        ui._openMenu();
        await sleep(900);
        const r = a => { const [x, y] = a.get_transformed_position(); const [w, h] = a.get_transformed_size(); return [x, y, w, h].map(Math.round); };
        const tree = a => ({t: a.constructor.name, s: a.style_class ?? '', b: r(a), c: a.get_children().filter(c => c.visible).slice(0, 4).map(tree)});
        const menuActor = ui._menu.actor ?? ui._menu; const m = r(menuActor), bar = r(ui._bar);
        const mon = Main.layoutManager.monitors[Main.layoutManager.findIndexForActor(ui._bar)];
        results.push({name: 'menu', menu: m, bar, monitor: [mon.x, mon.y, mon.width, mon.height], fits: m[1] >= mon.y && m[1] + m[3] <= mon.y + mon.height && m[2] < 400, type: ui._menu.constructor.name, items: ui._menu.items?.length});
        log('case', results.at(-1));
        log("menu-tree", tree(menuActor));
        await shot('menu-nick', Math.max(mon.x, m[0] - 200), Math.max(mon.y, m[1] - 100), Math.min(900, mon.width), Math.min(mon.height, m[3] + 250));
        ui.close(true);
    }
    GLib.file_set_contents(`${GLib.getenv('ORACLE_OUT')}/s142.json`, JSON.stringify(results, null, 1));
    log('summary', {pass: results.filter(r => r.pass).length, total: results.length});
}
