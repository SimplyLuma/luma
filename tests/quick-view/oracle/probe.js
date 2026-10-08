// SPDX-License-Identifier: GPL-2.0-or-later
// Quick View placement and render oracle: a headless Luma Shell automation
// script. It lays out mixed-scale monitors, opens Filer on each monitor in
// turn, presses Space on a real file selection, and checks where the
// compositor put the preview. Right steps through every file kind (screenshots
// are the renders); Escape must close the preview.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const OUT = GLib.getenv('QV_OUT');
const FIXTURES = GLib.getenv('QV_FIXTURES');
const LAYOUT = GLib.getenv('QV_LAYOUT') ?? 'desk';
const THEME = GLib.getenv('QV_THEME') ?? 'dark';
const PREVIEWER = 'org.gnome.NautilusPreviewer';

// Logical layouts, in the order of the --virtual-monitor arguments.
const LAYOUTS = {
    // Nick's desk: LG 5120x1440 at 1x, X1 panel 1920x1200 at 1.25x to its left.
    desk: [{w: 5120, h: 1440, x: 1536, y: 0, scale: 1, primary: true},
        {w: 1920, h: 1200, x: 0, y: 695, scale: 1.25}],
    // The panel above the ultrawide: a preview taller than Filer near the top
    // edge must not spill onto the monitor above.
    stacked: [{w: 5120, h: 1440, x: 0, y: 960, scale: 1, primary: true},
        {w: 1920, h: 1200, x: 1792, y: 0, scale: 1.25}],
    // A 2x HiDPI panel beside a 1x monitor.
    hidpi: [{w: 2880, h: 1800, x: 0, y: 0, scale: 2, primary: true},
        {w: 1920, h: 1080, x: 1440, y: 0, scale: 1}],
};

const results = [];
const log = (m, o) => console.log(`[quick-view-oracle] ${m} ${JSON.stringify(o ?? {})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
function dbusCall(name, path, iface, method, params, sig) {
    return new Promise((resolve, reject) => Gio.DBus.session.call(name, path, iface, method, params,
        sig ? new GLib.VariantType(sig) : null, 0, -1, null, (c, r) => { try { resolve(c.call_finish(r)); } catch (e) { reject(e); } }));
}
async function until(check, ms = 8000) {
    for (let waited = 0; waited < ms; waited += 100) {
        const value = check();
        if (value) return value;
        await sleep(100);
    }
    return null;
}

Gio._promisify(Shell.Screenshot.prototype, 'screenshot_area');
async function shot(name, r) {
    // Shell crops in logical coordinates and captures at each monitor's scale.
    const file = Gio.File.new_for_path(`${OUT}/${name}.png`);
    const stream = file.replace(null, false, Gio.FileCreateFlags.NONE, null);
    await new Shell.Screenshot().screenshot_area(r.x, r.y, r.width, r.height, stream);
    stream.close(null);
}

async function applyLayout() {
    const layout = LAYOUTS[LAYOUT];
    const [serial, monitors] = (await dbusCall('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
        'org.gnome.Mutter.DisplayConfig', 'GetCurrentState', null)).deepUnpack();
    const logical = layout.map((m, i) => {
        const [[connector], modes] = monitors[i];
        const mode = modes.find(md => md[1] === m.w && md[2] === m.h) ?? modes[0];
        return [m.x, m.y, m.scale, 0, !!m.primary, [[connector, mode[0], {}]]];
    });
    await dbusCall('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig', 'org.gnome.Mutter.DisplayConfig',
        'ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})', [serial, 1, logical, {}]));
    await sleep(4000);
    const n = global.display.get_n_monitors();
    const out = [];
    for (let i = 0; i < n; i++) {
        const g = global.display.get_monitor_geometry(i);
        const w = global.workspace_manager.get_active_workspace().get_work_area_for_monitor(i);
        out.push({index: i, scale: global.display.get_monitor_scale(i), geometry: [g.x, g.y, g.width, g.height],
            work: [w.x, w.y, w.width, w.height]});
    }
    log('layout', {name: LAYOUT, theme: THEME, monitors: out});
    return out;
}

const seat = Clutter.get_default_backend().get_default_seat();
const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
async function press(keyval) {
    const t = GLib.get_monotonic_time();
    keyboard.notify_keyval(t, keyval, Clutter.KeyState.PRESSED);
    keyboard.notify_keyval(t + 20000, keyval, Clutter.KeyState.RELEASED);
    await sleep(150);
}

const windows = () => global.display.list_all_windows();
const isPreview = w => (w.get_gtk_application_id?.() ?? w.get_wm_class()) === PREVIEWER || w.get_wm_class() === PREVIEWER;
const previewWindow = () => windows().find(w => isPreview(w) && w.get_compositor_private()?.visible);
const isFiler = w => (w.get_gtk_application_id?.() ?? '') === 'org.gnome.Nautilus' || w.get_wm_class() === 'org.gnome.Nautilus';
const rect = r => ({x: r.x, y: r.y, width: r.width, height: r.height});

async function openFiler(first) {
    const before = new Set(windows().filter(isFiler));
    Gio.Subprocess.new(['nautilus', '--new-window', '--select', `${FIXTURES}/${first}`], Gio.SubprocessFlags.NONE);
    return until(() => windows().find(w => isFiler(w) && !before.has(w) && w.get_compositor_private()?.visible), 20000);
}

async function measure(filer, monitors, label) {
    const preview = await until(previewWindow, 8000);
    if (!preview) {
        results.push({label, pass: false, reason: 'no preview window'});
        log('case', results.at(-1));
        return null;
    }
    await sleep(900);
    const frame = rect(preview.get_frame_rect());
    const parent = rect(filer.get_frame_rect());
    const filerMonitor = filer.get_monitor();
    const work = monitors[filerMonitor].work;
    const inside = frame.x >= work[0] && frame.y >= work[1] &&
        frame.x + frame.width <= work[0] + work[2] && frame.y + frame.height <= work[1] + work[3];
    const centreGap = Math.abs((frame.x + frame.width / 2) - (parent.x + parent.width / 2));
    const pushed = frame.x === work[0] || frame.x + frame.width === work[0] + work[2];
    const result = {label, title: preview.get_title(), frame, filer: parent,
        filerMonitor, previewMonitor: preview.get_monitor(), scale: monitors[filerMonitor].scale,
        transientForFiler: preview.get_transient_for() === filer,
        focused: global.display.focus_window === preview, inside, centreGap, pushed};
    result.pass = result.previewMonitor === filerMonitor && inside && result.transientForFiler && result.focused &&
        (centreGap <= 1 || pushed);
    results.push(result);
    log('case', result);
    return {preview, frame};
}

async function filerCase(monitors, index, where, sweep) {
    const m = monitors[index];
    const [wx, wy, ww, wh] = m.work;
    const size = where === 'centre' ? [Math.min(1000, ww - 40), Math.min(700, wh - 40)] : [640, 420];
    const pos = where === 'centre' ? [wx + Math.floor((ww - size[0]) / 2), wy + Math.floor((wh - size[1]) / 2)]
        : where === 'top-left' ? [wx, wy] : [wx + ww - size[0], wy + wh - size[1]];
    const filer = await openFiler('01 Landscape photo.png');
    if (!filer) {
        results.push({label: `monitor ${index} ${where}`, pass: false, reason: 'Filer did not open'});
        return;
    }
    await sleep(1500);
    filer.unmaximize?.(3);
    filer.move_to_monitor(index);
    filer.move_resize_frame(false, pos[0], pos[1], size[0], size[1]);
    await sleep(800);
    filer.activate(global.get_current_time());
    await sleep(800);
    const base = `${LAYOUT}-${THEME}-m${index}@${m.scale}x-${where}`;
    await press(Clutter.KEY_space);
    let seen = await measure(filer, monitors, `${base} photo`);
    if (seen) await shot(`${base}-01-photo`, pad(seen.frame, monitors[index].geometry));
    const steps = sweep ? 10 : 2;
    for (let step = 2; step <= steps + 1 && seen; step++) {
        await press(Clutter.KEY_Right);
        await sleep(700);
        seen = await measure(filer, monitors, `${base} step ${step}`);
        if (seen && sweep) await shot(`${base}-${String(step).padStart(2, '0')}`, pad(seen.frame, monitors[index].geometry));
    }
    await press(Clutter.KEY_Escape);
    const closed = await until(() => !previewWindow(), 4000);
    results.push({label: `${base} Escape closes`, pass: !!closed});
    log('case', results.at(-1));
    filer.delete(global.get_current_time());
    await sleep(1000);
}

function pad(frame, geometry) {
    const x = Math.max(geometry[0], frame.x - 32), y = Math.max(geometry[1], frame.y - 32);
    return {x, y, width: Math.min(geometry[0] + geometry[2], frame.x + frame.width + 32) - x,
        height: Math.min(geometry[1] + geometry[3], frame.y + frame.height + 32) - y};
}

async function work() {
    const env = {};
    for (const entry of GLib.get_environ()) {
        const at = entry.indexOf('=');
        if (at > 0) env[entry.slice(0, at)] = entry.slice(at + 1);
    }
    await dbusCall('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
        'UpdateActivationEnvironment', new GLib.Variant('(a{ss})', [env]));
    const monitors = await applyLayout();
    for (let index = 0; index < monitors.length; index++) {
        await filerCase(monitors, index, 'centre', true);
        await filerCase(monitors, index, 'top-left', false);
        await filerCase(monitors, index, 'bottom-right', false);
    }
    const failed = results.filter(r => !r.pass);
    GLib.file_set_contents(`${OUT}/results.json`, JSON.stringify(results, null, 1));
    log('summary', {layout: LAYOUT, theme: THEME, cases: results.length, failed: failed.length});
}

let started = false;
export function init() {
    GLib.mkdir_with_parents(OUT, 0o755);
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 6000, () => {
        if (!started) {
            started = true;
            work().catch(e => console.error(`[quick-view-oracle] ${e}\n${e.stack}`)).finally(() => global.context.terminate());
        }
        return GLib.SOURCE_REMOVE;
    });
}

export async function run() {
    await new Promise(() => {});
}
