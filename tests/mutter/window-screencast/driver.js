// SPDX-License-Identifier: Apache-2.0
// gnome-shell --automation-script for the window screencast test (run.sh).
// Records windows through org.gnome.Mutter.ScreenCast RecordWindow with a
// GStreamer pipewiresrc consumer (capture.c) and checks the stream's video
// crop against each window's frame: GTK 4 and libadwaita windows with
// client-side shadows, a client-decorated and a Mutter-framed Xwayland
// window, maximized, tiled, fullscreen, a resize while recording, at scale 1
// and at a fractional scale.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const out = GLib.getenv('LUMA_CAST_OUT');
const here = GLib.getenv('LUMA_CAST_HERE');
const captureBin = GLib.getenv('LUMA_CAST_CAPTURE');
const fixed = GLib.getenv('LUMA_CAST_EXPECT') !== 'stock';
const results = [];
const cases = [];

const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
    resolve();
    return GLib.SOURCE_REMOVE;
}));
const check = (name, ok, detail = '') => {
    results.push({name, ok: !!ok, detail: String(detail)});
    console.log(`${ok ? 'PASS' : 'FAIL'} ${name} ${detail}`);
};
const rect = r => ({x: r.x, y: r.y, width: r.width, height: r.height});
const now = () => GLib.get_monotonic_time();

const seat = Clutter.get_default_backend().get_default_seat();
const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
const key = (keyval, pressed) => keyboard.notify_keyval(now(), keyval,
    pressed ? Clutter.KeyState.PRESSED : Clutter.KeyState.RELEASED);

function spawn(argv, logName, env = {}) {
    const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.STDERR_MERGE});
    for (const [k, v] of Object.entries(env))
        launcher.setenv(k, v, true);
    launcher.set_stdout_file_path(`${out}/${logName}.log`);
    return launcher.spawnv(argv);
}

const wait = proc => new Promise(resolve => proc.wait_async(null, (p, res) => {
    p.wait_finish(res);
    resolve();
}));

async function ensureDesktop() {
    for (let i = 0; i < 20 && (Main.overview.visible || Main.overview.animationInProgress); i++) {
        Main.overview.hide();
        await sleep(300);
    }
}

async function openWindow(toolkit, title, env = {}) {
    const x11 = toolkit === 'gtk3';
    const proc = spawn(['python3', `${here}/window.py`, toolkit, title], `app-${title}`,
        {GDK_BACKEND: x11 ? 'x11' : 'wayland', ...env});
    for (let i = 0; i < 300; i++) {
        const actor = global.get_window_actors().find(a => a.meta_window.get_title() === title &&
            a.meta_window.get_frame_rect().width > 0);
        if (actor) {
            await ensureDesktop();
            await sleep(700);
            return {w: actor.meta_window, proc};
        }
        await sleep(100);
    }
    console.log(`window ${title} not found`);
    proc.force_exit();
    return {w: null, proc};
}

async function closeWindow({w, proc}) {
    proc.force_exit();
    await wait(proc);
    await sleep(300);
}

async function capture(w, name, during) {
    const stop = `${out}/${name}.stop`;
    GLib.unlink(stop);
    const proc = spawn([captureBin, String(w.get_id()), `${out}/${name}`, stop], `${name}-capture`);
    await sleep(1500);
    if (during)
        await during();
    await sleep(800);
    GLib.file_set_contents(stop, '');
    await wait(proc);
    const [, bytes] = GLib.file_get_contents(`${out}/${name}-capture.log`);
    const lines = new TextDecoder().decode(bytes).split('\n').filter(l => l.startsWith('{'))
        .map(l => JSON.parse(l));
    return {frames: lines.filter(l => 'frame' in l), final: lines.find(l => l.final), error: lines.find(l => l.error)};
}

function monitorScale(w) {
    return global.display.get_monitor_scale(w.get_monitor());
}

// The frame in buffer pixels: the frame's offset in the buffer and its size,
// times the buffer pixels per logical pixel.
// Mutter clips the crop to the stream (the monitor's size), which a window
// reaching past the monitor edge can exceed.
function expectedCrop(w, s, stream) {
    const f = w.get_frame_rect(), b = w.get_buffer_rect();
    let [x1, y1, x2, y2] = fixed
        ? [(f.x - b.x) * s, (f.y - b.y) * s, (f.x + f.width - b.x) * s, (f.y + f.height - b.y) * s]
        : [0, 0, b.width * s, b.height * s];
    [x1, y1, x2, y2] = [x1, y1, x2, y2].map(Math.round);
    x2 = Math.min(x2, stream.width);
    y2 = Math.min(y2, stream.height);
    return [x1, y1, x2 - x1, y2 - y1];
}

const near = (a, b, slack) => a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) <= slack);

function verify(label, w, cap, {scales, shadow}) {
    const f = cap.final;
    const geometry = {frame: rect(w.get_frame_rect()), buffer: rect(w.get_buffer_rect())};
    cases.push({label, ...geometry, final: f, frames: cap.frames.length, error: cap.error});
    if (!f) {
        check(`${label}: frames recorded`, false, JSON.stringify(cap.error ?? 'none'));
        return;
    }
    check(`${label}: alpha format negotiated`, f.format === 'BGRA', f.format);
    const candidates = scales.map(s => expectedCrop(w, s, f));
    const slack = scales.some(s => s % 1) ? 1 : 0;
    check(`${label}: crop is the ${fixed ? 'frame' : 'whole buffer'}`,
        candidates.some(c => near(f.crop, c, slack)),
        `crop ${JSON.stringify(f.crop)} expected ${JSON.stringify(candidates)} ${JSON.stringify(geometry)}`);
    if (!fixed) {
        if (shadow)
            check(`${label}: the crop holds the client's shadow`, f.inside.some(a => a < 250), JSON.stringify(f.inside));
        return;
    }
    // Implementation-independent: one pixel inside every edge is the opaque
    // window, two pixels outside is shadow or transparent, never window.
    check(`${label}: opaque one pixel inside every crop edge`, f.inside.every(a => a >= 250), JSON.stringify(f.inside));
    check(`${label}: nothing opaque two pixels outside the crop`, f.outside.every(a => a < 250), JSON.stringify(f.outside));
    if (shadow)
        check(`${label}: shadow margin is outside the crop`, f.crop[0] > 0 && f.crop[1] > 0 &&
            f.drawn[0] > f.crop[0] + f.crop[2], JSON.stringify({crop: f.crop, drawn: f.drawn}));
}

async function setScale(target) {
    const call = (method, params) => new Promise((resolve, reject) => Gio.DBus.session.call(
        'org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
        'org.gnome.Mutter.DisplayConfig', method, params, null, Gio.DBusCallFlags.NONE, -1, null,
        (c, res) => {
            try {
                resolve(c.call_finish(res));
            } catch (e) {
                reject(e);
            }
        }));
    const [serial, monitors] = (await call('GetCurrentState', null)).recursiveUnpack();
    const [spec, modes] = monitors[0];
    const mode = modes.find(m => m[6]['is-current']) ?? modes[0];
    const scale = mode[5].reduce((a, b) => Math.abs(b - target) < Math.abs(a - target) ? b : a);
    await call('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
        [serial, 1, [[0, 0, scale, 0, true, [[spec[0], mode[0], {}]]]], {}]));
    await sleep(2000);
    return scale;
}

async function round(tag, scale) {
    const s = scale;
    // Floating windows with client-side shadows.
    for (const toolkit of ['adw', 'gtk4']) {
        const win = await openWindow(toolkit, `${toolkit}-${tag}`);
        if (!win.w) {
            check(`${tag} ${toolkit}: window mapped`, false);
            continue;
        }
        win.w.move_resize_frame(true, 200, 150, 640, 420);
        await sleep(700);
        verify(`${tag} ${toolkit} floating`, win.w, await capture(win.w, `${tag}-${toolkit}-floating`),
            {scales: [s], shadow: true});
        if (toolkit === 'gtk4') {
            await closeWindow(win);
            continue;
        }

        // Resize while recording: the crop follows every frame.
        const cap = await capture(win.w, `${tag}-adw-resize`, async () => {
            const f = win.w.get_frame_rect();
            for (let i = 1; i <= 4; i++) {
                win.w.move_resize_frame(true, f.x, f.y, f.width + 60 * i, f.height + 40 * i);
                await sleep(300);
            }
        });
        verify(`${tag} adw resized while recording`, win.w, cap, {scales: [s], shadow: true});
        const sizes = new Set(cap.frames.map(fr => `${fr.crop[2]}x${fr.crop[3]}`));
        check(`${tag} adw resize: crop changed with the window`, sizes.size >= 3, [...sizes].join(' '));

        win.w.maximize();
        await sleep(1000);
        verify(`${tag} adw maximized`, win.w, await capture(win.w, `${tag}-adw-maximized`), {scales: [s]});
        win.w.unmaximize();
        await sleep(1000);

        win.w.activate(global.get_current_time());
        await sleep(300);
        key(Clutter.KEY_Super_L, true);
        key(Clutter.KEY_Left, true);
        key(Clutter.KEY_Left, false);
        key(Clutter.KEY_Super_L, false);
        await sleep(1000);
        const area = win.w.get_work_area_current_monitor();
        const tile = win.w.get_frame_rect();
        check(`${tag} adw tiled left`, tile.width <= area.width / 2 + 2 && tile.height >= area.height - 40,
            JSON.stringify({tile: rect(tile), area: rect(area)}));
        verify(`${tag} adw tiled`, win.w, await capture(win.w, `${tag}-adw-tiled`), {scales: [s]});
        win.w.unmaximize();
        await sleep(800);

        win.w.make_fullscreen();
        await sleep(1000);
        verify(`${tag} adw fullscreen`, win.w, await capture(win.w, `${tag}-adw-fullscreen`), {scales: [s]});
        await closeWindow(win);
    }

    // Xwayland: client-decorated (_GTK_FRAME_EXTENTS) and Mutter-framed.
    // With Xwayland scaling an X11 buffer may be at the integer scale above.
    const x11Scales = [...new Set([s, Math.ceil(s)])];
    for (const [kind, env, shadow] of [['csd', {}, true], ['ssd', {GTK_CSD: '0'}, false]]) {
        const win = await openWindow('gtk3', `x11-${kind}-${tag}`, env);
        if (!win.w) {
            check(`${tag} x11 ${kind}: window mapped`, false);
            continue;
        }
        win.w.move_resize_frame(true, 200, 150, 640, 420);
        await sleep(700);
        verify(`${tag} x11 ${kind} floating`, win.w, await capture(win.w, `${tag}-x11-${kind}-floating`),
            {scales: x11Scales, shadow});
        await closeWindow(win);
    }
}

async function work() {
    key(Clutter.KEY_Shift_L, true);
    key(Clutter.KEY_Shift_L, false);
    // The capture client talks to org.gnome.Mutter.ScreenCast directly
    // instead of through the portal.
    global.context.unsafe_mode = true;
    await ensureDesktop();
    await sleep(500);

    await round('s1', await setScale(1.0));
    const fractional = await setScale(1.5);
    check('fractional scale applied', fractional > 1 && fractional < 2, fractional);
    await round('s150', fractional);
}

// GNOME Shell's perf/automation runner calls run() once the shell is up.
export async function run() {
    await sleep(3000);
    try {
        await work();
    } catch (error) {
        check('scenario completed', false, String(error.stack));
    }
    GLib.file_set_contents(`${out}/result.json`, JSON.stringify({results, cases}, null, 1));
    global.context.terminate();
}
