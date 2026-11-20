// SPDX-License-Identifier: Apache-2.0
// gnome-shell --automation-script for the luma_window_shape_v1 test (run.sh).
// Real pointer drags from every edge and corner through a Clutter virtual
// pointer, maximize, half tile (Super+Left), fullscreen, and ordinary GTK 3,
// GTK 4 and libadwaita windows. Screenshots are taken mid-drag, with the
// button still held.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const out = GLib.getenv('LUMA_SHAPE_OUT');
const here = GLib.getenv('LUMA_SHAPE_HERE');
const client = GLib.getenv('LUMA_SHAPE_CLIENT');
const expectEnforced = GLib.getenv('LUMA_SHAPE_EXPECT') !== 'stock';
const results = [];
const shots = [];
const RW = 4, RH = 3;

const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
    resolve();
    return GLib.SOURCE_REMOVE;
}));
const check = (name, ok, detail = '') => {
    results.push({name, ok: !!ok, detail: String(detail)});
    console.log(`${ok ? 'PASS' : 'FAIL'} ${name} ${detail}`);
};
const mark = text => console.log(`LUMA-SHAPE-MARK ${text}`);
const frame = w => {
    const r = w.get_frame_rect();
    return {x: r.x, y: r.y, width: r.width, height: r.height};
};
const now = () => GLib.get_monotonic_time();

const seat = Clutter.get_default_backend().get_default_seat();
const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
const motion = (x, y) => pointer.notify_absolute_motion(now(), x, y);
const button = pressed => pointer.notify_button(now(), Clutter.BUTTON_PRIMARY,
    pressed ? Clutter.ButtonState.PRESSED : Clutter.ButtonState.RELEASED);
const key = (keyval, pressed) => keyboard.notify_keyval(now(), keyval,
    pressed ? Clutter.KeyState.PRESSED : Clutter.KeyState.RELEASED);

function readLog(path) {
    try {
        const [, bytes] = GLib.file_get_contents(path);
        return new TextDecoder().decode(bytes).split('\n').filter(l => l.trim()).map(l => JSON.parse(l));
    } catch {
        return [];
    }
}

async function screenshot(name) {
    const path = `${out}/${name}.png`;
    const file = Gio.File.new_for_path(path);
    const stream = file.replace(null, false, Gio.FileCreateFlags.NONE, null);
    const shooter = new Shell.Screenshot();
    await new Promise((resolve, reject) => shooter.screenshot(true, stream, (o, res) => {
        try {
            o.screenshot_finish(res);
            resolve();
        } catch (e) {
            reject(e);
        }
    }));
    stream.close(null);
    shots.push(name);
}

const children = [];
function spawn(argv, logName) {
    const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.STDERR_MERGE});
    launcher.setenv('GDK_BACKEND', 'wayland', true);
    launcher.set_stdout_file_path(`${out}/${logName}.stdout`);
    const child = launcher.spawnv(argv);
    children.push(child);
    return child;
}

// GNOME Shell opens the overview at startup and when the last window goes
// away; the scenarios need the desktop.
async function ensureDesktop() {
    for (let i = 0; i < 20 && (Main.overview.visible || Main.overview.animationInProgress); i++) {
        Main.overview.hide();
        await sleep(300);
    }
}

async function waitWindow(title) {
    for (let i = 0; i < 400; i++) {
        const actor = global.get_window_actors().find(a => a.meta_window.get_title() === title &&
            a.meta_window.get_frame_rect().width > 0);
        if (actor) {
            await ensureDesktop();
            await sleep(400);
            return actor.meta_window;
        }
        await sleep(100);
    }
    console.log(`window ${title} not found; mapped: ${global.get_window_actors().map(a => a.meta_window.get_title()).join(', ')}`);
    return null;
}

// Whether a frame rectangle's content (frame less the client's chrome) is
// at 4:3, to within the rounding of one pixel on each side.
const contentRatioOk = (f, base) =>
    Math.abs((f.width - base.w) * RH - (f.height - base.h) * RW) <= RW + RH;

function place(w, x, y, width, height) {
    w.move_resize_frame(true, x, y, width, height);
}

// A press just outside the window geometry, in the client's resize border,
// then a drag in steps; checks run on every step while the button is held.
const EDGES = {
    n: [0.5, 0, 0, -1], s: [0.5, 1, 0, 1], w: [0, 0.5, -1, 0], e: [1, 0.5, 1, 0],
    nw: [0, 0, -1, -1], ne: [1, 0, 1, -1], sw: [0, 1, -1, 1], se: [1, 1, 1, 1],
};

async function drag(w, edge, dx, dy, opts = {}) {
    const [fx, fy, nx, ny] = EDGES[edge];
    const f = frame(w);
    const px = f.x + fx * f.width + nx * 3;
    const py = f.y + fy * f.height + ny * 3;
    const steps = 10;
    const samples = [];
    // Approach the border the way a hand does, so the client has seen the
    // pointer over its resize border before the press.
    motion(px - nx * 12, py - ny * 12);
    await sleep(100);
    motion(px - nx * 4, py - ny * 4);
    await sleep(100);
    motion(px, py);
    await sleep(150);
    mark(`drag-begin ${opts.label ?? edge}`);
    button(true);
    await sleep(150);
    for (let i = 1; i <= steps; i++) {
        motion(px + dx * i / steps, py + dy * i / steps);
        await sleep(90);
        samples.push(frame(w));
        if (opts.shot && i === Math.round(steps * 0.6))
            await screenshot(opts.shot);
    }
    await sleep(200);
    const held = frame(w);
    button(false);
    mark(`drag-end ${opts.label ?? edge}`);
    await sleep(500);
    return {start: f, samples, held, end: frame(w)};
}

function configuresBetween(begin, end) {
    const [, bytes] = GLib.file_get_contents(`${out}/shell.log`);
    const text = new TextDecoder().decode(bytes);
    const a = text.indexOf(`LUMA-SHAPE-MARK ${begin}`);
    const b = text.indexOf('LUMA-SHAPE-MARK', a + 1);
    const segment = text.slice(a, end === 'eof' ? text.length : b < 0 ? text.length : b);
    return [...segment.matchAll(/Configuring xdg_toplevel#(\d+) \(wl_surface#(\d+)\): serial=(\d+) state=([^,]*), size=(\d+)x(\d+)/g)]
        .map(m => ({toplevel: m[1], surface: m[2], states: m[4], width: +m[5], height: +m[6]}));
}

async function work() {
    // A headless seat has no keyboard until a key is pressed once.
    key(Clutter.KEY_Shift_L, true);
    key(Clutter.KEY_Shift_L, false);
    Main.overview.hide();
    await sleep(500);
    const monitor = Main.layoutManager.primaryMonitor;
    const area = Main.layoutManager.getWorkAreaForMonitor(Main.layoutManager.primaryIndex);
    const workArea = {x: area.x, y: area.y, width: area.width, height: area.height};
    check('setup: monitor', monitor.width === 1920 && monitor.height === 1080, `${monitor.width}x${monitor.height}`);

    const lockedLog = `${out}/locked.log`;
    spawn([client, '--name', 'locked', '--log', lockedLog, '--ratio', `${RW}:${RH}`, '--size', '800x520'], 'locked');
    const locked = await waitWindow('locked');
    check('locked window mapped', locked);
    if (!locked)
        return;
    const ready = readLog(lockedLog).find(e => e.ev === 'ready');
    check(`client reports enforced=${expectEnforced}`, ready && ready.enforced === expectEnforced, JSON.stringify(ready));
    const sizes = () => readLog(lockedLog).filter(e => e.ev === 'size');
    const first = sizes().at(-1);
    const base = {w: first.w - first.cw, h: first.h - first.ch};
    check('base is the header bar', base.w === 0 && base.h > 20 && base.h < 80, JSON.stringify(base));
    const initial = frame(locked);
    if (expectEnforced) {
        check('initial size keeps content at 4:3 (app asked 800x520)', contentRatioOk(initial, base), JSON.stringify(initial));
        check('client content is 4:3 at first map',
            Math.abs(first.cw * RH - first.ch * RW) <= RW + RH, `${first.cw}x${first.ch}`);
    }
    await screenshot('00-initial');

    // -- interactive resizes from every edge and corner ---------------------
    // The first button press of a fresh headless seat can be taken before
    // the client has its pointer focus; one unchecked drag settles it.
    place(locked, 520, 260, 720, 540 + base.h);
    await sleep(600);
    await drag(locked, 'se', 20, 10, {label: 'warm-up'});
    const plan = [
        ['se', 140, 30], ['s', 0, 120], ['e', 160, 0], ['n', 0, -110],
        ['w', -150, 0], ['nw', -90, -40], ['ne', 120, -60], ['sw', -40, 110],
        ['se', -200, -20],
    ];
    let i = 0;
    for (const [edge, dx, dy] of plan) {
        const label = `${edge}${i}`;
        await ensureDesktop();
        place(locked, 520, 260, 720, 540 + base.h);
        await sleep(600);
        const logMark = sizes().length;
        const r = await drag(locked, edge, dx, dy, {label, shot: `drag-${String(i).padStart(2, '0')}-${edge}`});
        const during = sizes().slice(logMark);
        const moved = r.end.width !== r.start.width || r.end.height !== r.start.height;
        check(`${label}: drag resized the window`, moved, `${JSON.stringify(r.start)} -> ${JSON.stringify(r.end)}`);
        if (expectEnforced) {
            const bad = r.samples.filter(f => !contentRatioOk(f, base));
            check(`${label}: 4:3 held on every step while the button was held (${r.samples.length} steps)`,
                bad.length === 0, bad.length ? JSON.stringify(bad[0]) : JSON.stringify(r.held));
            const badClient = during.filter(e => Math.abs(e.cw * RH - e.ch * RW) > RW + RH);
            check(`${label}: every size the client laid out had 4:3 content (${during.length} layouts)`,
                during.length > 0 && badClient.length === 0, badClient.length ? JSON.stringify(badClient[0]) : '');
        }
        const configures = configuresBetween(`drag-begin ${label}`, 'eof');
        const resizing = configures.filter(c => c.states.includes('resizing'));
        const surface = resizing[0]?.surface;
        const mine = configures.filter(c => c.surface === surface);
        const lastMine = mine.at(-1);
        check(`${label}: the configure after the drag is not marked resizing`,
            lastMine && !lastMine.states.includes('resizing'), JSON.stringify(lastMine));
        const last = sizes().at(-1);
        check(`${label}: client ends at the compositor's size`,
            last.w === r.end.width && last.h === r.end.height, `client ${last.w}x${last.h} frame ${r.end.width}x${r.end.height}`);
        i++;
    }

    // -- maximize -------------------------------------------------------------
    place(locked, 300, 200, 640, 480 + base.h);
    await sleep(500);
    locked.maximize();
    await sleep(900);
    const m = frame(locked);
    await screenshot('maximized');
    if (expectEnforced) {
        check('maximize: full work-area height', m.y === workArea.y && m.height === workArea.height, `${JSON.stringify(m)} work ${JSON.stringify(workArea)}`);
        check('maximize: width matches 4:3 content', contentRatioOk(m, base), JSON.stringify(m));
        check('maximize: centred', Math.abs((m.x - workArea.x) - (workArea.x + workArea.width - m.x - m.width)) <= 1, JSON.stringify(m));
    }
    const lm = sizes().at(-1);
    check('maximize: client is maximized at that size', lm.maximized && lm.w === m.width && lm.h === m.height, JSON.stringify(lm));
    locked.unmaximize();
    await sleep(800);
    check('unmaximize: restores the floating size', frame(locked).width === 640 || contentRatioOk(frame(locked), base), JSON.stringify(frame(locked)));

    // -- half tile with Super+Left --------------------------------------------
    locked.activate(global.get_current_time());
    await sleep(300);
    key(Clutter.KEY_Super_L, true);
    key(Clutter.KEY_Left, true);
    key(Clutter.KEY_Left, false);
    key(Clutter.KEY_Super_L, false);
    await sleep(900);
    const t = frame(locked);
    await screenshot('tiled-left');
    check('tile: fills the left half of the work area',
        t.x === workArea.x && t.y === workArea.y && t.height === workArea.height &&
        Math.abs(t.width - workArea.width / 2) <= 1, `${JSON.stringify(t)} work ${JSON.stringify(workArea)}`);
    const lt = sizes().at(-1);
    check('tile: client fills the tile (it letterboxes inside)', lt.w === t.width && lt.h === t.height && lt.tiled, JSON.stringify(lt));
    key(Clutter.KEY_Super_L, true);
    key(Clutter.KEY_Left, true);
    key(Clutter.KEY_Left, false);
    key(Clutter.KEY_Super_L, false);
    await sleep(800);
    if (expectEnforced)
        check('untile: floating again at 4:3', contentRatioOk(frame(locked), base), JSON.stringify(frame(locked)));

    // -- Luma: a tiling manager's tile and the shell's maximize inset --------
    if (typeof locked.set_externally_tiled === 'function') {
        const zone = {x: workArea.x + 10, y: workArea.y + 10,
            width: Math.round(workArea.width * 2 / 3) - 20, height: workArea.height - 20};
        // Tiling Shell resizes first and marks the window tiled after.
        locked.move_resize_frame(false, zone.x, zone.y, zone.width, zone.height);
        locked.set_externally_tiled(true);
        await sleep(900);
        const z = frame(locked);
        await screenshot('external-tile');
        check('external tile: fills the zone it was moved to',
            z.x === zone.x && z.y === zone.y && z.width === zone.width && z.height === zone.height,
            `${JSON.stringify(z)} zone ${JSON.stringify(zone)}`);
        locked.set_externally_tiled(false);
        place(locked, 300, 200, 640, 480 + base.h);
        await sleep(800);
        if (expectEnforced)
            check('external tile released: floating at 4:3 again', contentRatioOk(frame(locked), base), JSON.stringify(frame(locked)));

        const index = locked.get_monitor();
        global.display.set_maximize_inset(index, Meta.Side.TOP, 16);
        global.display.set_maximize_inset(index, Meta.Side.BOTTOM, 16);
        global.display.set_maximize_inset(index, Meta.Side.LEFT, 16);
        global.display.set_maximize_inset(index, Meta.Side.RIGHT, 16);
        locked.maximize();
        await sleep(900);
        const mi = frame(locked);
        await screenshot('maximized-with-inset');
        check('maximize with the shell inset: full inset height, 4:3, centred',
            mi.y === workArea.y + 16 && mi.height === workArea.height - 32 && contentRatioOk(mi, base) &&
            Math.abs((mi.x - workArea.x) - (workArea.x + workArea.width - mi.x - mi.width)) <= 1,
            `${JSON.stringify(mi)} work ${JSON.stringify(workArea)}`);
        locked.unmaximize();
        global.display.clear_maximize_insets();
        await sleep(800);
    }

    // -- fullscreen -------------------------------------------------------------
    locked.make_fullscreen();
    await sleep(900);
    const fs = frame(locked);
    await screenshot('fullscreen');
    check('fullscreen: whole monitor, ratio not applied',
        fs.x === monitor.x && fs.y === monitor.y && fs.width === monitor.width && fs.height === monitor.height, JSON.stringify(fs));
    const lf = sizes().at(-1);
    check('fullscreen: client is fullscreen at the monitor size', lf.fullscreen && lf.w === monitor.width && lf.h === monitor.height, JSON.stringify(lf));
    locked.unmake_fullscreen();
    await sleep(800);
    if (expectEnforced)
        check('leave fullscreen: back at 4:3', contentRatioOk(frame(locked), base), JSON.stringify(frame(locked)));
    locked.minimize();
    await sleep(400);

    // -- ordinary windows are unaffected ---------------------------------------
    const others = [
        ['plain-gtk4', [client, '--name', 'plain-gtk4', '--log', `${out}/plain-gtk4.log`, '--size', '600x400']],
        ['adw', ['python3', `${here}/other.py`, 'adw', 'adw', `${out}/adw.log`]],
        ['gtk3', ['python3', `${here}/other.py`, 'gtk3', 'gtk3', `${out}/gtk3.log`]],
    ];
    for (const [name, argv] of others) {
        spawn(argv, name);
        const w = await waitWindow(name);
        check(`${name}: mapped`, w);
        if (!w)
            continue;
        await ensureDesktop();
        place(w, 400, 250, 600, 400);
        await sleep(600);
        for (const [edge, dx, dy] of [['se', 150, 37], ['n', 0, -53], ['w', -71, 0]]) {
            const label = `${name}-${edge}`;
            const r = await drag(w, edge, dx, dy, {label, shot: name === 'plain-gtk4' && edge === 'se' ? 'plain-gtk4-drag-se' : null});
            const [, , nx, ny] = EDGES[edge];
            const ew = r.start.width + Math.abs(nx) * Math.sign(nx) * dx;
            const eh = r.start.height + Math.abs(ny) * Math.sign(ny) * dy;
            check(`${label}: follows the pointer exactly`,
                Math.abs(r.end.width - ew) <= 1 && Math.abs(r.end.height - eh) <= 1,
                `${JSON.stringify(r.start)} -> ${JSON.stringify(r.end)} expected ${ew}x${eh}`);
            const configures = configuresBetween(`drag-begin ${label}`, 'eof');
            const resizing = configures.filter(c => c.states.includes('resizing'));
            const mine = configures.filter(c => c.surface === resizing[0]?.surface);
            if (name !== 'gtk3' || mine.length) {
                check(`${label}: the configure after the drag is not marked resizing`,
                    mine.length && !mine.at(-1).states.includes('resizing'), JSON.stringify(mine.at(-1)));
            }
        }
        w.maximize();
        await sleep(800);
        const mw = frame(w);
        check(`${name}: maximizes to the whole work area`,
            mw.x === workArea.x && mw.y === workArea.y && mw.width === workArea.width && mw.height === workArea.height,
            JSON.stringify(mw));
        w.unmaximize();
        await sleep(500);
        w.delete(global.get_current_time());
        await sleep(400);
    }
}

async function finish() {
    for (const child of children)
        child.force_exit();
    GLib.file_set_contents(`${out}/result.json`,
        JSON.stringify({pass: results.every(r => r.ok), results, shots}, null, 2));
    global.context.terminate();
}

// GNOME Shell's perf/automation runner calls run() once the shell is up.
export async function run() {
    await sleep(3000);
    try {
        await work();
    } catch (error) {
        check('scenario completed', false, String(error.stack));
    }
    await finish();
}
