// Capture oracle: drives Luma's screenshot tool with real pointer and keyboard
// events in a headless Shell, measures where it sits, and saves what it shows
// and what it captures. Cases are chosen with C_ONLY (comma separated).
import Clutter from 'gi://Clutter';
import GdkPixbuf from 'gi://GdkPixbuf';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';

const OUT = GLib.getenv('ORACLE_OUT');
const log = (m, o) => console.log(`[capture] ${m} ${JSON.stringify(o ?? {})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
    r();
    return GLib.SOURCE_REMOVE;
}));
const r2 = v => Math.round(v * 100) / 100;
const rect = a => {
    const [x, y] = a.get_transformed_position();
    const [w, h] = a.get_transformed_size();
    return {x, y, w, h};
};
function dbusCall(name, path, iface, method, params, sig) {
    return new Promise((resolve, reject) => Gio.DBus.session.call(name, path, iface, method, params,
        sig ? new GLib.VariantType(sig) : null, 0, -1, null, (c, r) => {
            try {
                resolve(c.call_finish(r));
            } catch (e) {
                reject(e);
            }
        }));
}

export default async function ({Main, shot}) {
    const Capture = await import('resource:///org/gnome/shell/ui/lumaCapture.js');
    const Surface = await import('resource:///org/gnome/shell/ui/lumaShelfSurface.js');
    const want = Number(GLib.getenv('C_SCALE') || 1);
    if (want !== 1) {
        const [serial, monitors] = (await dbusCall('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
            'org.gnome.Mutter.DisplayConfig', 'GetCurrentState', null)).deepUnpack();
        const [[connector], modes] = monitors[0];
        const current = modes.find(m => m[6]?.['is-current']?.deepUnpack?.()) ?? modes[0];
        await dbusCall('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig', 'org.gnome.Mutter.DisplayConfig',
            'ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
                [serial, 1, [[0, 0, want, 0, true, [[connector, current[0], {}]]]], {}]));
        await sleep(5000);
    }
    await sleep(3000);
    if (Main.actionMode === 0) {
        // Automation starts can leave the action mode unset, so no keybinding
        // would run; end an empty modal as startup would.
        const dummy = new St.Widget();
        Main.uiGroup.add_child(dummy);
        Main.popModal(Main.pushModal(dummy));
        dummy.destroy();
    }

    const ui = Main.screenshotUI;
    const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
    const k = Math.max(1, ...global.stage.peek_stage_views().map(v => v.get_scale())) / scale;
    const edge = Main.shelf?._edge;
    const mon = Main.layoutManager.primaryMonitor;
    const home = GLib.get_home_dir();
    const only = (GLib.getenv('C_ONLY') || '').split(',').filter(Boolean);
    const results = {scale, k, edge, theme: GLib.getenv('C_THEME'), cases: {}};
    log('state', {scale, k, edge, mon: [mon.x, mon.y, mon.width, mon.height],
        monitors: Main.layoutManager.monitors.map(m => [m.x, m.y, m.width, m.height])});

    const seat = Clutter.get_default_backend().get_default_seat();
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    const now = () => GLib.get_monotonic_time();
    const move = async (x, y, wait = 120) => {
        pointer.notify_absolute_motion(now(), x, y);
        await sleep(wait);
    };
    const press = () => pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
    const release = () => pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
    const clickAt = async (x, y, wait = 700) => {
        await move(x, y, 250);
        press();
        await sleep(60);
        release();
        await sleep(wait);
    };
    const click = async (actor, wait = 700) => {
        const r = rect(actor);
        await clickAt(r.x + r.w / 2, r.y + r.h / 2, wait);
    };
    const drag = async (x1, y1, x2, y2, steps = 12) => {
        await move(x1, y1, 200);
        press();
        await sleep(80);
        for (let i = 1; i <= steps; i++)
            await move(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps, 25);
        await sleep(80);
        release();
        await sleep(400);
    };
    const key = async (keyvals, wait = 500) => {
        for (const kv of keyvals) {
            keyboard.notify_keyval(now(), kv, Clutter.KeyState.PRESSED);
            await sleep(30);
        }
        for (const kv of [...keyvals].reverse()) {
            keyboard.notify_keyval(now(), kv, Clutter.KeyState.RELEASED);
            await sleep(30);
        }
        await sleep(wait);
    };
    const chord = n => key([Clutter.KEY_Control_L, Clutter.KEY_Shift_L, Clutter[`KEY_${n}`]], 1200);
    const typeText = async text => {
        for (const ch of text)
            await key([Clutter.unicode_to_keysym(ch.codePointAt(0))], 60);
    };
    const snap = async (name, x, y, w, h) => {
        Capture.setPaintInCaptures(true);
        try {
            const sx = Math.max(0, x), sy = Math.max(0, y);
            await shot(name, sx * k, sy * k, Math.min(w, global.stage.width - sx) * k, Math.min(h, global.stage.height - sy) * k);
        } finally {
            Capture.setPaintInCaptures(false);
        }
    };
    const snapMonitor = (name, m = mon) => snap(name, m.x, m.y, m.width, m.height);
    const snapAround = (name, actors, pad = 40) => {
        const rs = actors.filter(a => a?.visible).map(rect);
        const x1 = Math.min(...rs.map(r => r.x)) - pad * scale, y1 = Math.min(...rs.map(r => r.y)) - pad * scale;
        const x2 = Math.max(...rs.map(r => r.x + r.w)) + pad * scale, y2 = Math.max(...rs.map(r => r.y + r.h)) + pad * scale;
        return snap(name, x1, y1, x2 - x1, y2 - y1);
    };
    const state = () => ({
        visible: ui.visible,
        mode: ui._captureMode,
        hasSelection: ui._areaSelector.hasSelection,
        selection: ui._areaSelector.hasSelection ? ui._areaSelector.getGeometry() : null,
        checkedWindow: ui._checkedWindow()?.metaWindow?.get_title() ?? null,
        captureSensitive: ui._bar.captureButton.reactive,
        hint: ui._hint.visible ? ui._hint.text : null,
        sizeLabel: ui._areaSelector._sizeLabel.visible ? ui._areaSelector._sizeLabel.text : null,
        recording: ui.screencast_in_progress,
        countdown: ui._countdown.running,
        menu: ui._menu.visible,
        focus: global.stage.key_focus?.accessible_name ?? global.stage.key_focus?.constructor?.name ?? null,
    });
    const newest = dir => {
        const d = Gio.File.new_for_path(dir);
        if (!d.query_exists(null))
            return null;
        const e = d.enumerate_children('standard::name,time::modified', 0, null);
        let best = null;
        for (let info = e.next_file(null); info; info = e.next_file(null)) {
            const t = info.get_modification_date_time().to_unix_usec();
            if (!best || t > best.t)
                best = {t, path: `${dir}/${info.get_name()}`};
        }
        return best?.path ?? null;
    };
    const imageInfo = path => {
        if (!path)
            return null;
        try {
            const pb = GdkPixbuf.Pixbuf.new_from_file(path);
            return {path, width: pb.width, height: pb.height};
        } catch (e) {
            return {path, error: `${e}`};
        }
    };
    const copyOut = (path, name) => {
        if (path)
            Gio.File.new_for_path(path).copy(Gio.File.new_for_path(`${OUT}/${name}`), Gio.FileCopyFlags.OVERWRITE, null, null);
    };
    const reset = async () => {
        if (ui.screencast_in_progress)
            await ui.stopScreencast();
        if (ui.visible)
            ui.close(true);
        ui._thumbnail.dismiss();
        await move(mon.x + mon.width * 0.8, mon.y + 30 * scale, 600);
    };
    const measureBar = (name, actor = ui._bar) => {
        const island = Surface.shelfDockIsland();
        const s = rect(actor);
        const res = {name, surface: [s.x, s.y, s.w, s.h].map(r2)};
        if (island) {
            const i = rect(island);
            const gap = {bottom: i.y - (s.y + s.h), top: s.y - (i.y + i.h)}[edge];
            Object.assign(res, {island: [i.x, i.y, i.w, i.h].map(r2), gapLogical: gap === undefined ? null : r2(gap / scale),
                gapDevicePx: gap === undefined ? null : r2(gap * k * scale),
                centreOffset: r2((s.x + s.w / 2) - (mon.x + mon.width / 2)),
                pass: gap === undefined ? null : Math.abs(gap / scale - 8) <= 0.5 / scale + 0.01});
        }
        const work = Main.layoutManager.getWorkAreaForMonitor(Main.layoutManager.primaryIndex);
        if (edge === 'left' || edge === 'right')
            Object.assign(res, {aboveWorkBottom: r2((work.y + work.height - (s.y + s.h)) / scale)});
        log('measure', res);
        return res;
    };
    const run = async (name, fn) => {
        if (only.length && !only.includes(name))
            return;
        log('case', {name});
        try {
            results.cases[name] = await fn() ?? 'done';
        } catch (e) {
            results.cases[name] = {error: `${e}`};
            log('error', {name, error: `${e}`, stack: `${e.stack}`.split('\n').slice(0, 5)});
        }
        await reset();
    };
    const windowByTitle = title => global.get_window_actors().map(a => a.metaWindow).find(w => w.get_title() === title);
    const shotsDir = `${home}/Pictures/Screenshots`;
    const videosDir = `${home}/Videos/Screencasts`;

    await move(mon.x + mon.width * 0.8, mon.y + 30 * scale, 300);

    // 1, 2: the bar in each mode and the Options menu, measured against the dock.
    await run('bar', async () => {
        const out = [];
        await ui.open();
        await sleep(800);
        const modes = [...ui._bar.modeButtons.keys()];
        for (const mode of modes) {
            await click(ui._bar.modeButtons.get(mode), 600);
            await move(mon.x + mon.width * 0.8, mon.y + 30 * scale, 500);
            const m = measureBar(`bar-${mode}`);
            m.state = state();
            out.push(m);
            await snapAround(`bar-${mode}`, [ui._bar, Surface.shelfDockIsland()]);
        }
        await snapMonitor('bar-full');
        await click(ui._bar.optionsButton, 700);
        const menu = rect(ui._menu), bar = rect(ui._bar), options = rect(ui._bar.optionsButton);
        const menuGap = edge === 'top' ? menu.y - (bar.y + bar.h) : bar.y - (menu.y + menu.h);
        out.push({name: 'menu', menu: [menu.x, menu.y, menu.w, menu.h].map(r2), gapLogical: r2(menuGap / scale),
            centredOnOptions: Math.abs((menu.x + menu.w / 2) - (options.x + options.w / 2)) <= 1,
            items: ui._menu.items.map(i => `${i._lumaChecked ? '[x] ' : ''}${i.accessible_name}`)});
        log('menu', out.at(-1));
        await snapAround('menu', [ui._bar, ui._menu, Surface.shelfDockIsland()]);
        // Tooltip on hover, placed from the bar.
        await key([Clutter.KEY_Escape], 400);
        await move(rect(ui._bar.modeButtons.get('window')).x + 20 * scale, rect(ui._bar).y + 29 * scale, 900);
        const tip = ui._bar.tooltip;
        if (tip.visible) {
            const t = rect(tip);
            out.push({name: 'tooltip', text: tip._label.text, gapLogical: r2((edge === 'top' ? t.y - (bar.y + bar.h) : bar.y - (t.y + t.h)) / scale)});
            log('tooltip', out.at(-1));
            await snapAround('tooltip', [ui._bar, tip]);
        }
        return out;
    });

    // Shortcuts: 1 no preselection, 2 window awaiting a pick, 3 drag, 4 recording and stop.
    await run('shortcuts', async () => {
        const out = {};
        const desktop = [mon.x + mon.width * 0.85, mon.y + mon.height * 0.2];
        ui._options.lastMode = 'selection';
        await move(...desktop, 400);
        await chord(1);
        out.ctrlShift1 = state();
        await snapMonitor('shortcut-1');
        await key([Clutter.KEY_Escape], 800);
        await chord(2);
        out.ctrlShift2 = state();
        await snapMonitor('shortcut-2');
        const claude = windowByTitle('Claude')?.get_frame_rect();
        if (claude) {
            await move(claude.x + claude.width / 2, claude.y + claude.height / 2, 500);
            out.ctrlShift2Hover = state();
            await snapMonitor('shortcut-2-hover');
        }
        await key([Clutter.KEY_Escape], 800);
        await move(...desktop, 300);
        await chord(3);
        out.ctrlShift3 = state();
        await snapMonitor('shortcut-3');
        await drag(mon.x + mon.width * 0.3, mon.y + mon.height * 0.3, mon.x + mon.width * 0.55, mon.y + mon.height * 0.6);
        out.ctrlShift3Dragged = state();
        await key([Clutter.KEY_Escape], 800);
        await chord(4);
        out.ctrlShift4 = state();
        await snapMonitor('shortcut-4');
        if (ui._screencastSupported) {
            await click(ui._bar.captureButton, 3000);
            out.ctrlShift4Recording = state();
            await chord(4);
            await sleep(2500);
            out.ctrlShift4Stopped = state();
        } else {
            out.screencastSupported = false;
        }
        log('shortcuts', out);
        return out;
    });

    // What the screen paints and what a screenshot or recording paints, as
    // the recording pill's own check sees them.
    await run('paintprobe', async () => {
        const GObject = (await import('gi://GObject')).default;
        const counts = {onScreen: 0, captured: 0};
        const Probe = GObject.registerClass(class Probe extends St.Widget {
            vfunc_paint(ctx) {
                counts[Capture.paintsOnScreen(ctx) ? 'onScreen' : 'captured']++;
                super.vfunc_paint(ctx);
            }
        });
        const probe = new Probe({width: 40, height: 40, x: 10, y: 10, style: 'background-color: red;'});
        global.stage.add_child(probe);
        await sleep(1500);
        const beforeShot = {...counts};
        const shooter = new (await import('gi://Shell')).default.Screenshot();
        await new Promise(resolve => shooter.screenshot_stage_to_content((o, res) => {
            o.screenshot_stage_to_content_finish(res);
            resolve();
        }));
        const afterShot = {...counts};
        probe.destroy();
        const out = {beforeShot, afterShot};
        log('paintprobe', out);
        return out;
    });

    // 3: draw, move, resize, capture.
    await run('selection', async () => {
        const out = {};
        await chord(3);
        const L = mon.x + mon.width * 0.25, T = mon.y + mon.height * 0.22;
        await drag(L, T, L + mon.width * 0.3, T + mon.height * 0.3);
        out.drawn = state();
        await snapMonitor('selection-drawn');
        const g1 = ui._areaSelector.getGeometry();
        await drag(g1[0] + g1[2] / 2, g1[1] + g1[3] / 2, g1[0] + g1[2] / 2 + 120 * scale, g1[1] + g1[3] / 2 + 60 * scale);
        out.moved = state();
        await snapMonitor('selection-moved');
        const g2 = ui._areaSelector.getGeometry();
        await drag(g2[0] + g2[2] - 1, g2[1] + g2[3] - 1, g2[0] + g2[2] + 140 * scale, g2[1] + g2[3] + 70 * scale);
        out.resized = state();
        await snapMonitor('selection-resized');
        const g = ui._areaSelector.getGeometry();
        // C_SELECTION_KEY=enter takes it with Return instead of the button.
        if (GLib.getenv('C_SELECTION_KEY') === 'enter')
            await key([Clutter.KEY_Return], 1800);
        else
            await click(ui._bar.captureButton, 1800);
        const file = newest(shotsDir);
        out.file = imageInfo(file);
        out.expected = {width: Math.round(g[2] * scale * k), height: Math.round(g[3] * scale * k)};
        copyOut(file, 'selection-capture.png');
        ui._thumbnail.dismiss();
        await move(mon.x + mon.width * 0.95, mon.y + 10, 1200);
        await shot('selection-live', g[0] * k, g[1] * k, g[2] * k, g[3] * k);
        out.geometry = g;
        out.viewScales = global.stage.peek_stage_views().map(v => v.get_scale());
        log('selection', out);
        return out;
    });

    // 4: a window partly off screen and behind another, then the other one behind.
    await run('window', async () => {
        const out = {};
        const claude = windowByTitle('Claude'), second = windowByTitle('Second');
        if (!claude || !second)
            throw new Error('test windows missing');
        claude.move_frame(true, mon.x - 300 * scale, mon.y + 180 * scale);
        second.move_frame(true, mon.x + 300 * scale, mon.y + 360 * scale);
        second.activate(global.get_current_time());
        await sleep(1200);
        await move(mon.x + mon.width * 0.85, mon.y + mon.height * 0.2, 300);
        await chord(2);
        out.awaiting = state();
        const cf = claude.get_frame_rect();
        await move(cf.x + 350 * scale, cf.y + 90 * scale, 600);
        out.hoverClaude = state();
        await snapMonitor('window-hover-offscreen-behind');
        await clickAt(cf.x + 350 * scale, cf.y + 90 * scale, 1800);
        out.claudeFile = imageInfo(newest(shotsDir));
        out.claudeFrame = [cf.x, cf.y, cf.width, cf.height];
        out.claudeBuffer = (() => {
            const b = claude.get_buffer_rect();
            return [b.x, b.y, b.width, b.height];
        })();
        copyOut(out.claudeFile?.path, 'window-offscreen-behind.png');
        await reset();

        claude.activate(global.get_current_time());
        claude.move_frame(true, mon.x + 200 * scale, mon.y + 150 * scale);
        await sleep(1200);
        await chord(2);
        const sf = second.get_frame_rect();
        await move(sf.x + sf.width - 80 * scale, sf.y + sf.height - 60 * scale, 600);
        out.hoverSecond = state();
        await snapMonitor('window-hover-behind');
        await clickAt(sf.x + sf.width - 80 * scale, sf.y + sf.height - 60 * scale, 1800);
        out.secondFile = imageInfo(newest(shotsDir));
        out.secondFrame = [sf.x, sf.y, sf.width, sf.height];
        copyOut(out.secondFile?.path, 'window-behind.png');
        log('window', out);
        return out;
    });

    // 5: the 5-second timer, and Escape cancelling it.
    await run('timer', async () => {
        const out = {};
        await ui.open();
        await sleep(700);
        await click(ui._bar.modeButtons.get('screen'), 500);
        await click(ui._bar.optionsButton, 600);
        const five = ui._menu.items.find(i => i.accessible_name === '5 seconds');
        await snapAround('timer-menu', [ui._bar, ui._menu]);
        await click(five, 600);
        out.barLabel = ui._bar.captureButton.accessible_name;
        await snapAround('timer-bar', [ui._bar]);
        const before = newest(shotsDir);
        await click(ui._bar.captureButton, 1300);
        out.counting = {...state(), seconds: ui._countdown.seconds};
        await snapMonitor('timer-countdown');
        await sleep(5000);
        const after = newest(shotsDir);
        out.captured = after !== before ? imageInfo(after) : null;
        await reset();
        await ui.open();
        await sleep(700);
        await click(ui._bar.captureButton, 1500);
        out.secondCountdown = {...state(), seconds: ui._countdown.seconds};
        await key([Clutter.KEY_Escape], 1200);
        out.afterEscape = state();
        await snapMonitor('timer-cancelled');
        await sleep(5000);
        out.noCaptureAfterCancel = newest(shotsDir) === after;
        ui._options.timer = 0;
        ui._bar.setTimer(0);
        log('timer', out);
        return out;
    });

    // 6: the thumbnail after a screenshot, and clicking it opens the file.
    await run('thumbnail', async () => {
        const out = {};
        await ui.open(0, 'screen');
        await sleep(700);
        await click(ui._bar.captureButton, 900);
        const t = ui._thumbnail;
        out.visible = t.visible;
        out.name = t.nameText;
        out.where = t.whereText;
        const tr = rect(t);
        const work = Main.layoutManager.getWorkAreaForMonitor(Main.layoutManager.primaryIndex);
        out.rightInsetLogical = r2((work.x + work.width - (tr.x + tr.w)) / scale);
        const island = Surface.shelfDockIsland();
        if (island) {
            const i = rect(island);
            out.gapLogical = r2((edge === 'top' ? tr.y - (i.y + i.h) : i.y - (tr.y + tr.h)) / scale);
        }
        await snapAround('thumbnail-screenshot', [t, island], 30);
        await click(t, 2000);
        out.opened = GLib.file_test(`${OUT}/opened.log`, GLib.FileTest.EXISTS)
            ? new TextDecoder().decode(GLib.file_get_contents(`${OUT}/opened.log`)[1]).trim() : null;
        log('thumbnail', out);
        return out;
    });

    // 7: a selection recording with the pill, stopped by Stop, the shortcut and Escape.
    await run('record', async () => {
        const out = {stops: []};
        if (!ui._screencastSupported)
            return {screencastSupported: false};
        const L = mon.x + mon.width * 0.2, T = mon.y + mon.height * 0.7;
        ui._options.lastSelection = null;
        const recordOnce = async (how, n) => {
            await chord(4);
            await click(ui._bar.modeButtons.get('record-selection'), 500);
            if (!ui._areaSelector.hasSelection || n === 0)
                await drag(L, T, mon.x + mon.width * 0.7, mon.y + mon.height * 0.99);
            const g = ui._areaSelector.getGeometry();
            if (n === 0)
                await snapMonitor('record-selection-ready');
            await click(ui._bar.captureButton, 3200);
            const s = {how, geometry: g, recording: ui.screencast_in_progress, pill: ui._pill.visible ? ui._pill.elapsedText : null,
                gnomeIndicator: Main.panel.statusArea.screenRecording?.visible ?? null};
            if (n === 0) {
                const p = rect(ui._pill);
                s.pillRect = [p.x, p.y, p.w, p.h].map(r2);
                await snapMonitor('record-pill');
                await snapAround('record-pill-crop', [ui._pill, Surface.shelfDockIsland()]);
            }
            if (how === 'stop')
                await click(ui._pill.stopButton, 300);
            else if (how === 'shortcut')
                await chord(4);
            else
                await key([Clutter.KEY_Escape], 500);
            await sleep(2500);
            s.stopped = !ui.screencast_in_progress;
            s.path = ui._screencastPath;
            s.thumbnail = ui._thumbnail.visible ? {name: ui._thumbnail.nameText, where: ui._thumbnail.whereText} : null;
            if (n === 0 && ui._thumbnail.visible) {
                await snapAround('thumbnail-recording', [ui._thumbnail], 30);
                await click(ui._thumbnail, 2000);
                s.opened = GLib.file_test(`${OUT}/opened.log`, GLib.FileTest.EXISTS)
                    ? new TextDecoder().decode(GLib.file_get_contents(`${OUT}/opened.log`)[1]).trim() : null;
            }
            copyOut(s.path, `record-${how}.webm`);
            out.stops.push(s);
            await reset();
        };
        await recordOnce('stop', 0);
        await recordOnce('shortcut', 1);
        await recordOnce('escape', 2);
        // Whole screen, to show the pill is not in the video.
        await chord(4);
        await click(ui._bar.modeButtons.get('record-screen'), 500);
        await clickAt(mon.x + mon.width * 0.5, mon.y + mon.height * 0.4, 3500);
        const p = rect(ui._pill);
        out.screen = {recording: ui.screencast_in_progress, pillRect: [p.x, p.y, p.w, p.h].map(r2)};
        await snapMonitor('record-screen-pill');
        await click(ui._pill.stopButton, 3000);
        out.screen.path = ui._screencastPath;
        copyOut(out.screen.path, 'record-screen.webm');
        log('record', out);
        return out;
    });

    // 8: each Save to destination, and the Options kept for the next session.
    await run('saveto', async () => {
        const out = {};
        const clip = () => new Promise(resolve => St.Clipboard.get_default().get_content(St.ClipboardType.CLIPBOARD,
            'image/png', (_c, bytes) => resolve(bytes?.get_size?.() ?? 0)));
        const dests = [['pictures', 'Pictures'], ['desktop', 'Desktop'], ['clipboard', 'Clipboard'], ['other', null]];
        ui._options.otherLocation = `${home}/Elsewhere`;
        for (const [dest, label] of dests) {
            St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD, 'empty');
            await ui.open(0, 'screen');
            await sleep(700);
            await click(ui._bar.optionsButton, 600);
            const item = ui._menu.items.find(i => label ? i.accessible_name === label : i.accessible_name === 'Elsewhere');
            await snapAround(`saveto-menu-${dest}`, [ui._bar, ui._menu]);
            await click(item, 600);
            const dirs = {pictures: shotsDir, desktop: `${home}/Desktop`, clipboard: null, other: `${home}/Elsewhere`};
            const before = Object.fromEntries(Object.entries(dirs).filter(([, d]) => d).map(([n, d]) => [n, newest(d)]));
            await click(ui._bar.captureButton, 1800);
            const after = Object.fromEntries(Object.entries(dirs).filter(([, d]) => d).map(([n, d]) => [n, newest(d)]));
            out[dest] = {saveTo: ui._options.saveTo, newFiles: Object.keys(after).filter(n => after[n] !== before[n]).map(n => after[n]),
                clipboardPngBytes: await clip(), thumbnail: ui._thumbnail.whereText};
            await reset();
        }
        // Options chosen now are read back by the "persist" run.
        ui._options.saveTo = 'desktop';
        ui._options.timer = 10;
        ui._options.showThumbnail = false;
        ui._options.rememberSelection = false;
        ui._options.showPointer = true;
        ui._options.lastMode = 'window';
        Gio.Settings.sync();
        out.written = {saveTo: 'desktop', timer: 10, thumbnail: false, remember: false, pointer: true, lastMode: 'window'};
        log('saveto', out);
        return out;
    });

    await run('persist', async () => {
        const o = new Capture.CaptureOptions();
        const out = {backend: o.settings ? 'gsettings' : 'memory', saveTo: o.saveTo, timer: o.timer, thumbnail: o.showThumbnail,
            remember: o.rememberSelection, pointer: o.showPointer, lastMode: o.lastMode};
        await ui.open();
        await sleep(800);
        out.opened = state();
        await click(ui._bar.optionsButton, 600);
        out.menu = ui._menu.items.map(i => `${i._lumaChecked ? '[x] ' : ''}${i.accessible_name}`);
        await snapAround('persist-menu', [ui._bar, ui._menu]);
        log('persist', out);
        return out;
    });

    // 9: opened from Luma's search; the search is not in the capture.
    await run('search', async () => {
        const out = {};
        await shot('search-baseline', mon.x * k, mon.y * k, mon.width * k, mon.height * k);
        Main.lumaSearch.open();
        await sleep(1200);
        await typeText('screenshot');
        await sleep(2000);
        await snapMonitor('search-results');
        await key([Clutter.KEY_Return], 300);
        const actions = (await import('resource:///org/gnome/shell/misc/systemActions.js')).getDefault();
        out.afterReturn = {modalCount: Main.modalCount, waiting: !!actions._screenshotWaitId,
            dialogs: Main.layoutManager.modalDialogGroup.get_children().filter(d => d.visible).map(d => d.constructor.name)};
        // A headless stage paints only on damage; a moving pointer gives the
        // wait for "one frame painted without the dialog" its frames.
        for (let i = 0; i < 16; i++)
            await move(mon.x + mon.width * (0.6 + (i % 2) * 0.01), mon.y + mon.height * 0.1, 200);
        out.later = {modalCount: Main.modalCount, waiting: !!actions._screenshotWaitId};
        out.opened = state();
        await snapMonitor('search-capture-open');
        await click(ui._bar.modeButtons.get('screen'), 500);
        await clickAt(mon.x + mon.width * 0.5, mon.y + mon.height * 0.4, 1800);
        const file = newest(shotsDir);
        out.file = imageInfo(file);
        copyOut(file, 'search-capture.png');
        log('search', out);
        return out;
    });

    // 10: keyboard only, and every control's name.
    await run('keyboard', async () => {
        const out = {steps: []};
        const step = async (label, keys, wait = 450) => {
            await key(keys, wait);
            out.steps.push({label, ...state()});
        };
        await move(mon.x + mon.width * 0.85, mon.y + 20 * scale, 300);
        await chord(1);
        out.steps.push({label: 'Ctrl+Shift+1', ...state()});
        out.controls = [ui._bar.closeButton, ...ui._bar.modeButtons.values(), ui._bar.optionsButton, ui._bar.captureButton].map(c => ({name: c.get_accessible()?.get_name(), role: c.get_accessible()?.get_role?.(),
            tooltip: c.accessible_name}));
        out.bar = {name: ui._bar.surface.get_accessible()?.get_name(), role: ui._bar.surface.get_accessible()?.get_role?.()};
        await step('Tab', [Clutter.KEY_Tab]);
        await step('Tab', [Clutter.KEY_Tab]);
        await step('Space', [Clutter.KEY_space]);
        await snapAround('keyboard-mode', [ui._bar]);
        for (let i = 0; i < 5; i++)
            await step('Tab', [Clutter.KEY_Tab], 250);
        await step('Return (Options)', [Clutter.KEY_Return], 600);
        for (let i = 0; i < 5; i++)
            await step('Down', [Clutter.KEY_Down], 200);
        await snapAround('keyboard-menu', [ui._bar, ui._menu]);
        await step('Return (5 seconds)', [Clutter.KEY_Return], 600);
        await step('Tab', [Clutter.KEY_Tab]);
        const focused = global.stage.key_focus;
        const shadow = focused?.get_theme_node?.().get_box_shadow?.();
        out.focusRing = {pseudo: focused?.get_style_pseudo_class?.() ?? null,
            boxShadow: shadow ? {inset: shadow.inset, spread: shadow.spread, color: shadow.color?.to_string?.()} : null};
        await snapAround('keyboard-capture-focus', [ui._bar]);
        const before = newest(shotsDir);
        await step('Return (Capture)', [Clutter.KEY_Return], 1200);
        await sleep(5500);
        const after = newest(shotsDir);
        out.captured = after !== before ? imageInfo(after) : null;
        out.menuNames = null;
        log('keyboard', out);
        ui._options.timer = 0;
        return out;
    });

    // 11: two monitors, a selection across both, the bar on the shelf's monitor.
    await run('multimonitor', async () => {
        const out = {};
        const monitors = Main.layoutManager.monitors;
        await ui.open(0, 'selection');
        await sleep(700);
        if (monitors.length > 1) {
            const a = monitors[0], b = monitors[1];
            await drag(a.x + a.width * 0.75, a.y + a.height * 0.3, b.x + b.width * 0.3, b.y + b.height * 0.6, 20);
        }
        out.state = state();
        out.barMonitor = ui._barPlace?.monitorIndex;
        out.shelfMonitor = Main.layoutManager.findIndexForActor(Surface.shelfDockIsland());
        out.bar = measureBar('bar-multimonitor');
        await snap('multimonitor', 0, 0, global.stage.width, global.stage.height);
        log('multimonitor', out);
        return out;
    });

    // The thumbnail dragged into apps (C_DROP_TARGETS: gtk4, gtk4-x11,
    // chromium, chromium-x11, nautilus), a click still opening the file, and
    // a drag let go over nothing leaving the thumbnail where it was.
    await run('drag', async () => {
        const out = {targets: {}};
        const harness = '/oracle/capture/harness';
        const spawn = (argv, env = {}) => {
            const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.NONE});
            for (const [k, v] of Object.entries(env))
                launcher.setenv(k, v, true);
            return launcher.spawnv(argv);
        };
        const waitFor = async (fn, ms = 15000) => {
            for (let t = 0; t < ms; t += 200) {
                const v = fn();
                if (v)
                    return v;
                await sleep(200);
            }
            return null;
        };
        const byTitle = t => global.get_window_actors().map(a => a.metaWindow).find(w => w.get_title()?.startsWith(t));
        const readLog = path => GLib.file_test(path, GLib.FileTest.EXISTS)
            ? new TextDecoder().decode(GLib.file_get_contents(path)[1]).trim().split('\n').filter(Boolean).map(l => JSON.parse(l)) : [];
        const shoot = async () => {
            ui._thumbnail.dismiss();
            await sleep(400);
            await ui.open(0, 'screen');
            await sleep(700);
            await click(ui._bar.captureButton, 600);
            const bridge = ui._thumbnail.dragBridge;
            const active = await waitFor(() => bridge.active && ui._thumbnail.visible, 12000);
            return {active: !!active, file: ui._thumbnail.file?.get_path() ?? null};
        };
        const dragTo = async (x, y) => {
            const t = rect(ui._thumbnail);
            await move(t.x + t.w / 2, t.y + t.h * 0.4, 400);
            press();
            await sleep(120);
            for (let i = 1; i <= 30; i++)
                await move(t.x + t.w / 2 + (x - t.x - t.w / 2) * i / 30, t.y + t.h * 0.4 + (y - t.y - t.h * 0.4) * i / 30, 30);
            await sleep(400);
            out.midDrag ??= {thumbnailOpacity: ui._thumbnail.opacity};
            release();
            await sleep(1500);
        };

        // The helper window: not in the window list, above, never focused.
        const before = global.display.focus_window?.get_title() ?? null;
        const first = await shoot();
        await sleep(1500);
        const helper = global.get_window_actors().map(a => a.metaWindow).find(w => ui._thumbnail.dragBridge._client?.owns_window(w));
        const hf = helper?.get_frame_rect();
        const tr = rect(ui._thumbnail);
        out.helper = {first, found: !!helper, skipTaskbar: helper?.skip_taskbar ?? null, above: helper?.is_above?.() ?? null,
            onAllWorkspaces: helper?.is_on_all_workspaces?.() ?? null,
            frame: hf ? [hf.x, hf.y, hf.width, hf.height] : null, thumbnail: [tr.x, tr.y, tr.w, tr.h].map(r2),
            inTabList: helper ? global.display.get_tab_list(0, null).includes(helper) : null,
            trackerApp: helper ? Shell.WindowTracker.get_default().get_window_app(helper)?.get_id?.() ?? null : null,
            focusBefore: before, focusAfter: global.display.focus_window?.get_title() ?? null,
            dockApps: (() => {
                const ids = new Set();
                const walk = actor => {
                    const app = actor._delegate?.app ?? actor.app;
                    if (app?.get_id && actor.visible)
                        ids.add(app.get_id());
                    for (const child of actor.get_children())
                        walk(child);
                };
                const shelf = Main.layoutManager.uiGroup.get_children().find(a => a.name === 'lumaShelf');
                if (shelf)
                    walk(shelf);
                return [...ids];
            })()};
        await snapAround('drag-thumbnail', [ui._thumbnail], 40);
        log('drag-helper', out.helper);

        const targets = (GLib.getenv('C_DROP_TARGETS') || 'gtk4').split(',');
        for (const target of targets) {
            const logPath = `${OUT}/drop-${target}.log`;
            let win = null;
            const res = {};
            if (target === 'gtk4' || target === 'gtk4-x11') {
                const env = target === 'gtk4-x11' ? {GDK_BACKEND: 'x11'} : {GDK_BACKEND: 'wayland'};
                spawn(['gjs', '-m', `${harness}/droptarget.js`, logPath, target], env);
                win = await waitFor(() => byTitle(target));
            } else if (target.startsWith('chromium')) {
                const ozone = target === 'chromium-x11' ? 'x11' : 'wayland';
                spawn(['chromium-browser', `--ozone-platform=${ozone}`, '--no-first-run', '--no-sandbox', '--disable-gpu',
                    `--user-data-dir=/tmp/capture-chromium-${ozone}`, '--new-window', `file://${harness}/droppage.html#${target}`]);
                win = await waitFor(() => byTitle(`drop ${target}`), 30000);
            } else if (target === 'nautilus') {
                const dir = `${home}/Dropped`;
                GLib.mkdir_with_parents(dir, 0o755);
                // Files will not open windows for root: run it as another user
                // on the same Wayland display, with a session bus of its own.
                GLib.chmod(dir, 0o777);
                spawn(['sh', '-c', 'chmod 711 "$XDG_RUNTIME_DIR" && chmod 666 "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" && ' +
                    'mkdir -p /tmp/filer-run && chown filer /tmp/filer-run && chmod 700 /tmp/filer-run && ' +
                    `exec runuser -u filer -- env WAYLAND_DISPLAY="$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" XDG_RUNTIME_DIR=/tmp/filer-run ` +
                    `HOME=/home/filer GDK_BACKEND=wayland dbus-run-session -- nautilus --new-window "${dir}"`]);
                win = await waitFor(() => global.get_window_actors().map(a => a.metaWindow)
                    .find(w => w.get_wm_class()?.includes('Nautilus') && w.get_title()), 30000);
                res.dir = dir;
            }
            if (!win) {
                out.targets[target] = {error: 'target window did not appear',
                    windows: global.get_window_actors().map(a => `${a.metaWindow.get_wm_class()}: ${a.metaWindow.get_title()}`)};
                continue;
            }
            win.move_resize_frame(false, mon.x + 200, mon.y + 150, 900, 600);
            win.activate(global.get_current_time());
            await sleep(1500);
            const shot1 = await shoot();
            const f = win.get_frame_rect();
            await dragTo(f.x + f.width / 2, f.y + f.height / 2);
            res.shot = shot1;
            res.thumbnailAfterDrop = {visible: ui._thumbnail.visible, leaving: ui._thumbnail._leaving};
            res.fileStillThere = shot1.file ? GLib.file_test(shot1.file, GLib.FileTest.EXISTS) : null;
            if (target.startsWith('gtk4'))
                res.log = readLog(logPath).filter(e => e.event === 'drop');
            else if (target.startsWith('chromium'))
                res.title = (await waitFor(() => win.get_title()?.includes('dropped') && win.get_title(), 5000)) ?? win.get_title();
            else if (target === 'nautilus')
                res.copied = shot1.file ? GLib.file_test(`${res.dir}/${GLib.path_get_basename(shot1.file)}`, GLib.FileTest.EXISTS) : null;
            await snapMonitor(`drop-${target}`);
            out.targets[target] = res;
            log('drop', {target, res});
            win.delete(global.get_current_time());
            await sleep(800);
        }

        // A click on the thumbnail (through the helper) still opens the file.
        const clickShot = await shoot();
        await click(ui._thumbnail, 2000);
        out.click = {shot: clickShot, opened: GLib.file_test(`${OUT}/opened.log`, GLib.FileTest.EXISTS)
            ? new TextDecoder().decode(GLib.file_get_contents(`${OUT}/opened.log`)[1]).trim().split('\n').pop() : null};

        // Let go over the empty desktop: nothing takes it, the thumbnail stays.
        const cancelShot = await shoot();
        await dragTo(mon.x + mon.width * 0.5, mon.y + 60);
        out.cancel = {shot: cancelShot, thumbnailVisible: ui._thumbnail.visible && !ui._thumbnail._leaving,
            opacity: ui._thumbnail.opacity};
        log('drag', out);
        return out;
    });

    // Frost and glass surfaces in screenshots. The reference is what the screen
    // shows: a screen recording copies the stage view's own pixels, while a
    // screenshot paints the stage again into a framebuffer of its own (where the
    // backdrop blur went wrong). Both are saved with the islands' rectangles;
    // analyze.sh compares them. Run with C_THEME=glass or frost.
    await run('blur', async () => {
        const out = {islands: []};
        const shelf = Main.layoutManager.uiGroup.get_children().find(a => a.name === 'lumaShelf');
        const walk = actor => {
            if (actor.visible && actor.has_style_class_name?.('luma-shelf-island')) {
                const r = rect(actor);
                out.islands.push({style: actor.style_class, rect: [r.x, r.y, r.w, r.h].map(v => Math.round(v * k))});
            }
            for (const child of actor.get_children())
                walk(child);
        };
        if (shelf)
            walk(shelf);
        // A bright window above the shelf, as on Nick's screen.
        const second = windowByTitle('Second');
        second?.move_frame(true, mon.x + mon.width - 1000 * scale, mon.y + mon.height - 760 * scale);
        await sleep(1500);
        await shot('blur-screenshot', mon.x * k, mon.y * k, mon.width * k, mon.height * k);
        const [, path] = (await dbusCall('org.gnome.Shell.Screencast', '/org/gnome/Shell/Screencast',
            'org.gnome.Shell.Screencast', 'Screencast',
            new GLib.Variant('(sa{sv})', [`${OUT}/blur-screen`, {'draw-cursor': new GLib.Variant('b', false)}]),
            '(bs)')).deepUnpack();
        await sleep(2500);
        await dbusCall('org.gnome.Shell.Screencast', '/org/gnome/Shell/Screencast',
            'org.gnome.Shell.Screencast', 'StopScreencast', null, '(b)');
        out.recording = path;
        // The older D-Bus Screenshot API, whole screen and an area over the shelf.
        const area = out.islands.reduce((a, i) => [Math.min(a[0], i.rect[0]), Math.min(a[1], i.rect[1]),
            Math.max(a[2], i.rect[0] + i.rect[2]), Math.max(a[3], i.rect[1] + i.rect[3])], [1e9, 1e9, 0, 0]);
        for (const [name, method, params] of [
            ['blur-dbus-screen', 'Screenshot', new GLib.Variant('(bbs)', [false, false, `${OUT}/blur-dbus-screen.png`])],
            ['blur-dbus-area', 'ScreenshotArea', new GLib.Variant('(iiiibs)', [area[0] / k, area[1] / k,
                (area[2] - area[0]) / k, (area[3] - area[1]) / k, false, `${OUT}/blur-dbus-area.png`])]]) {
            try {
                await dbusCall('org.gnome.Shell.Screenshot', '/org/gnome/Shell/Screenshot',
                    'org.gnome.Shell.Screenshot', method, params, '(bs)');
            } catch (e) {
                out[name] = `${e}`;
            }
        }
        out.area = area;
        // Capture's own screenshot of the whole screen.
        await ui.open(0, 'screen');
        await sleep(700);
        await click(ui._bar.captureButton, 1500);
        copyOut(newest(shotsDir), 'blur-capture.png');
        log('blur', out);
        return out;
    });

    // What Capture leaves on screen while it works stays out of recordings and
    // screen shares: record the whole screen while a timed screenshot counts
    // down and flashes, then look for the countdown disc and the flash in the
    // frames (analysed by hiddencheck.py).
    await run('hidden', async () => {
        const out = {};
        const [, path] = (await dbusCall('org.gnome.Shell.Screencast', '/org/gnome/Shell/Screencast',
            'org.gnome.Shell.Screencast', 'Screencast',
            new GLib.Variant('(sa{sv})', [`${OUT}/hidden-screen`, {'draw-cursor': new GLib.Variant('b', false)}]),
            '(bs)')).deepUnpack();
        await sleep(1000);
        ui._options.timer = 5;
        await ui.open(0, 'screen');
        await sleep(700);
        await click(ui._bar.captureButton, 1500);
        const c = rect(ui._countdown._disc ?? ui._countdown);
        out.countdown = {visible: ui._countdown.visible, rect: [c.x, c.y, c.w, c.h].map(v => Math.round(v * k))};
        await snapMonitor('hidden-countdown-on-screen');
        await sleep(5500);
        // The flash on its own, over the whole monitor, twice. Software
        // rendering turns animations off, and the flash with them.
        out.animations = St.Settings.get().enable_animations;
        global.force_animations = true;
        out.animationsForced = St.Settings.get().enable_animations;
        ui._thumbnail.dismiss();
        await sleep(600);
        for (let i = 0; i < 2; i++) {
            Capture.flash({x: mon.x, y: mon.y, width: mon.width, height: mon.height});
            await sleep(960);
        }
        await dbusCall('org.gnome.Shell.Screencast', '/org/gnome/Shell/Screencast',
            'org.gnome.Shell.Screencast', 'StopScreencast', null, '(b)');
        // What the screen showed, taken after the recording: the harness
        // photographs the stage with Capture's own paint turned on, which
        // would otherwise also reach a recording running at the same time.
        Capture.flash({x: mon.x, y: mon.y, width: mon.width, height: mon.height});
        await sleep(60);
        await snapMonitor('hidden-flash-on-screen');
        ui._options.timer = 0;
        out.recording = path;
        out.monitor = [mon.x, mon.y, mon.width, mon.height].map(v => Math.round(v * k));
        log('hidden', out);
        return out;
    });

    GLib.file_set_contents(`${OUT}/results.json`, JSON.stringify(results, null, 1));
    log('done', {});
}
