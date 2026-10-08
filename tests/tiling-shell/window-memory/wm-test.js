// SPDX-License-Identifier: Apache-2.0
// Window placement memory (ADR-036) scenario test, run inside a headless
// GNOME Shell: gnome-shell --headless --virtual-monitor 1920x1200 --automation-script=wm-test.js
// Virtual monitors come and go with MetaTest.TestMonitor; each size stands for a
// physical monitor with a fake EDID, so re-created monitors get new connectors
// but the same identity, like a monitor moved to another port or dock.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import MetaTest from 'gi://MetaTest';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const OUT = GLib.getenv('WM_OUT') ?? '/wm/out';
const APP = '/wm/test/app.py';
const UUID = 'tilingshell@ferrarodomenico.com';
const IDENTITY = {
    '1920x1200': {vendor: 'LEN', product: 'X1 2-in-1 Gen 10 panel', serial: '', builtin: true},
    '2560x1440': {vendor: 'DEL', product: 'U2723QE', serial: 'HOME-MAIN'},
    '1600x900': {vendor: 'DEL', product: 'P2422H', serial: 'HOME-SIDE'},
    '3440x1440': {vendor: 'GSM', product: '34WN80C', serial: 'OFFICE'},
};
const HOUR = 3600e3;

Gio._promisify(Shell.Screenshot.prototype, 'screenshot_stage_to_content');
Gio._promisify(Shell.Screenshot, 'composite_to_stream');

const results = [];
const monitors = {};
const procs = {};
let mem;
let ext;
// Shared with every window memory instance, so a re-enabled Tiling Shell keeps them.
const hooks = {
    timeOffset: 0,
    timing: {settle: 300, stable: 800, quiet: 300, launchDelay: 120, dwell: 1200, save: 5000},
    monitorIdentity: (monitor, geometry) =>
        IDENTITY[`${geometry.width}x${geometry.height}`] ?? {vendor: 'X', product: 'unknown', serial: `${geometry.width}x${geometry.height}`},
};

function refresh() {
    ext = Main.extensionManager.lookup(UUID)?.stateObj;
    mem = ext?._lumaWindowMemory ?? mem;
    return mem;
}
let step = 0;

const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
function log(msg) { console.log(`[wmtest] ${msg}`); }

function check(name, ok, detail = '') {
    results.push({name, ok: !!ok, detail});
    log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`);
}

async function until(fn, ms = 8000, label = 'condition') {
    const end = Date.now() + ms;
    while (Date.now() < end) {
        const v = fn();
        if (v) return v;
        await sleep(50);
    }
    log(`timeout waiting for ${label}`);
    return null;
}

function monitorKeyOf(index) {
    return mem.debugState().monitors.find(m => m.index === index)?.key ?? null;
}

function indexOfIdentity(serialOrPanel) {
    const s = mem.debugState();
    return s.monitors.find(m => m.key.includes(serialOrPanel))?.index ?? -1;
}

async function setMonitors(sizes, {wait = true} = {}) {
    for (const size of Object.keys(monitors)) {
        if (!sizes.includes(size)) {
            monitors[size].destroy();
            delete monitors[size];
        }
    }
    for (const size of sizes) {
        if (!monitors[size]) {
            const [w, h] = size.split('x').map(Number);
            monitors[size] = MetaTest.TestMonitor.new(global.context, w, h, 60.0);
        }
    }
    if (wait) await waitStable();
}

async function waitStable() {
    await sleep(100);
    await until(() => mem._stable && !mem._stableId, 10000, 'stable setup');
    await sleep(mem.timing.quiet + 200);
}

function windowsOf(appId) {
    return global.display.list_all_windows().filter(w => (w.get_gtk_application_id() ?? w.get_wm_class()) === appId);
}

function mainWindow(appId) {
    return windowsOf(appId).filter(w => w.get_transient_for() === null && w.allows_resize() && w.get_window_type() === Meta.WindowType.NORMAL)
        .sort((a, b) => a.get_stable_sequence() - b.get_stable_sequence())[0] ?? null;
}

async function launch(appId, name, color, extra = [], {firstFrame = true} = {}) {
    const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.NONE});
    launcher.setenv('WAYLAND_DISPLAY', GLib.getenv('WAYLAND_DISPLAY') ?? 'wayland-0', true);
    launcher.setenv('GDK_BACKEND', 'wayland', true);
    launcher.setenv('GSK_RENDERER', 'cairo', true);
    launcher.setenv('NO_AT_BRIDGE', '1', true);
    launcher.unsetenv('DISPLAY');
    const before = new Set(windowsOf(appId));
    const proc = launcher.spawnv(['python3', APP, appId, name, color, ...extra]);
    procs[appId] = proc;
    const win = await until(() => windowsOf(appId).find(w => !before.has(w) && w.get_transient_for() === null && w.get_title() === name), 10000, `${appId} window`);
    if (win && firstFrame) {
        await until(() => mem._windows.get(win)?.mapped, 5000, `${appId} mapped`);
        await sleep(mem.timing.launchDelay + 300);
    }
    log(`launched ${appId} pid=${proc.get_identifier()} window=${win?.get_id()}`);
    return win;
}

async function quit(appId) {
    const proc = procs[appId];
    if (!proc) return;
    proc.send_signal(15);
    await until(() => windowsOf(appId).length === 0, 5000, `${appId} closed`);
    delete procs[appId];
    await sleep(200);
}

function managerFor(index) {
    return ext._tilingManagers[index];
}

// Tiling Shell off and on again (lock/unlock, or a Shell restart after reboot):
// the new memory object must come back from the file alone.
async function restartTiling(skipMs, disable = true) {
    if (disable) {
        Main.extensionManager.disableExtension(UUID);
        await sleep(300);
    }
    hooks.timeOffset += skipMs;
    Main.extensionManager.enableExtension(UUID);
    await sleep(100);
    refresh();
    mem._beginTransition('test-restart');
    await sleep(500);
    await waitStable();
}

// What a person does: tile from the window menu, or drag somewhere.
async function personTiles(win, monitorIndex, tile) {
    mem.touch(win, 'test-window-menu');
    const Tile = (await import(`file://${ext.path}/components/layout/Tile.js`)).default;
    win.move_to_monitor(monitorIndex);
    managerFor(monitorIndex).onTileFromWindowMenu(new Tile({...tile, groups: []}), win);
    await sleep(mem.timing.settle + 300);
}

async function personDrags(win, monitorIndex, x, y) {
    global.display.emit('grab-op-begin', win, Meta.GrabOp.KEYBOARD_MOVING);
    win.assignedTile = undefined;
    const area = Main.layoutManager.getWorkAreaForMonitor(monitorIndex);
    win.move_frame(true, area.x + x, area.y + y);
    global.display.emit('grab-op-end', win, Meta.GrabOp.KEYBOARD_MOVING);
    await sleep(mem.timing.settle + 300);
}

function frameOf(win) {
    const r = win.get_frame_rect();
    return [r.x, r.y, r.width, r.height];
}

function sameFrame(a, b) {
    return a.every((v, i) => Math.abs(v - b[i]) <= 2);
}

// Every painted frame, compare each window with where it must be.
function frameSampler(expected) {
    const bad = [];
    let frames = 0;
    const id = global.stage.connect('after-paint', () => {
        frames++;
        for (const [win, frame] of expected) {
            if (!sameFrame(frameOf(win), frame) && bad.length < 20)
                bad.push(`${win.get_title()} at ${frameOf(win)} (expected ${frame}) in frame ${frames}`);
        }
    });
    return () => {
        global.stage.disconnect(id);
        return {frames, bad};
    };
}

let lockPath = null;
async function lockScreen() {
    try {
        Main.screenShield.lock(false);
    } catch (e) {
        log(`screenShield.lock: ${e}`);
    }
    lockPath = 'screen shield';
    if (!Main.sessionMode.isLocked) {
        lockPath = 'session mode';
        Main.sessionMode.pushMode('unlock-dialog');
    }
    await sleep(300);
    refresh();
    log(`locked via ${lockPath}`);
}

// Unlock the way the Shell does after authentication; capture the reveal.
async function unlockScreen(label) {
    // Connected after Tiling Shell, so this sees the state its handler left.
    let settledAtUnlock = null;
    const id = Main.sessionMode.connect('updated', () => {
        if (!Main.sessionMode.isLocked && settledAtUnlock === null)
            settledAtUnlock = refresh()._stable;
    });
    if (lockPath === 'screen shield')
        Main.screenShield.deactivate(true);
    else
        Main.sessionMode.popMode('unlock-dialog');
    await until(() => !Main.sessionMode.isLocked, 5000, 'unlocked');
    Main.sessionMode.disconnect(id);
    await sleep(60);
    await shot(`${label}-unlocking`);
    await sleep(900);
    refresh();
    return settledAtUnlock;
}

function isTiledAt(win) {
    return !!win.assignedTile && win.is_externally_tiled?.() !== false;
}

// Start any client; returns its first new main window.
async function spawnClient(argv, env, match, timeout = 30000) {
    const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE});
    launcher.setenv('WAYLAND_DISPLAY', GLib.getenv('WAYLAND_DISPLAY') ?? 'wayland-0', true);
    launcher.setenv('GSK_RENDERER', 'cairo', true);
    launcher.setenv('NO_AT_BRIDGE', '1', true);
    launcher.unsetenv('DISPLAY');
    for (const [k, v] of Object.entries(env))
        launcher.setenv(k, v, true);
    const before = new Set(global.display.list_all_windows());
    const proc = launcher.spawnv(argv);
    const win = await until(() => global.display.list_all_windows().find(w => !before.has(w) && w.get_transient_for() === null &&
        w.get_window_type() === Meta.WindowType.NORMAL && match(w)), timeout, `${argv[0]} window`);
    return {proc, win};
}

// Records every painted frame in which a matching window is visible.
function visibleFrameRecorder(match) {
    const seen = new Map();
    const id = global.stage.connect('after-paint', () => {
        for (const actor of global.get_window_actors()) {
            const win = actor.get_meta_window();
            if (!win || !match(win) || !actor.visible || actor.get_paint_opacity() === 0)
                continue;
            const frames = seen.get(win) ?? [];
            frames.push(frameOf(win));
            seen.set(win, frames);
        }
    });
    return () => {
        global.stage.disconnect(id);
        return seen;
    };
}

// A click at the middle of a window, through a virtual pointer as a person's
// would go: true if the app saw it (app.py --clicks logs every click).
async function clickLands(win, appId) {
    const path = GLib.build_filenamev([GLib.get_user_runtime_dir(), `clicks-${appId}`]);
    GLib.unlink(path);
    const seat = Clutter.get_default_backend().get_default_seat();
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const r = win.get_frame_rect();
    const now = () => GLib.get_monotonic_time();
    pointer.notify_absolute_motion(now(), r.x + r.width / 2, r.y + r.height / 2);
    await sleep(250);
    pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
    await sleep(60);
    pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
    return !!(await until(() => GLib.file_test(path, GLib.FileTest.EXISTS), 3000, `${appId} click`));
}

async function launchHoldScenarios() {
    const home = () => ({main: indexOfIdentity('HOME-MAIN'), side: indexOfIdentity('HOME-SIDE'), panel: indexOfIdentity('panel:')});
    const Tile = (await import(`file://${ext.path}/components/layout/Tile.js`)).default;
    const x11Display = GLib.getenv('DISPLAY');
    const clients = [
        {label: 'GTK4 (Wayland)', argv: ['python3', APP, 'org.projectluma.Reel', 'Reel', '#5e35b1', '--clicks'], clicks: 'org.projectluma.Reel', env: {GDK_BACKEND: 'wayland'},
            match: w => w.get_gtk_application_id() === 'org.projectluma.Reel', monitor: () => home().main, tile: {x: 0.5, y: 0, width: 0.5, height: 0.5}},
        {label: 'Chromium app window (Wayland, like Viola and Claude)',
            argv: ['chromium-browser', '--no-sandbox', '--ozone-platform=wayland', '--user-data-dir=/tmp/wm-chromium', '--no-first-run',
                '--disable-gpu', '--class=org.projectluma.ChromiumApp', '--app=data:text/html,<body style="background:%23d97757"><h1>Chromium app</h1>'],
            env: {}, match: w => /chrom/i.test(`${w.get_wm_class()} ${w.get_sandboxed_app_id()} ${w.get_wm_class_instance()}`),
            monitor: () => home().main, tile: {x: 0, y: 0.5, width: 0.5, height: 0.5}, slow: true},
        {label: 'GTK4 (XWayland)', argv: ['python3', APP, 'org.projectluma.XNotes', 'XNotes', '#00838f'], env: {GDK_BACKEND: 'x11', DISPLAY: x11Display ?? ':0'},
            match: w => w.get_client_type() === Meta.WindowClientType.X11 && /xnotes/i.test(`${w.get_gtk_application_id()} ${w.get_wm_class()}`),
            monitor: () => home().side, tile: {x: 0.5, y: 0.5, width: 0.5, height: 0.5}, needsX11: true},
        // Maps and keeps resizing itself without waiting for the window
        // manager, like Electron (ChatGPT took 12 s to appear when only
        // Mutter's "shown" could end the hold).
        {label: 'X11 client that resizes itself (like Electron)', argv: ['python3', '/wm/test/xclient.py', 'XWiggle', '120', '90', '900', '640', '--wiggle'],
            env: {DISPLAY: x11Display ?? ':0'}, match: w => w.get_client_type() === Meta.WindowClientType.X11 && w.get_wm_class() === 'XWiggle',
            monitor: () => home().main, tile: {x: 0, y: 0, width: 0.5, height: 1}, needsX11: true, slow: true},
        // The late path: shown before it is in place, then moved there. It
        // must end visible and in its tile (Tide opened invisible here).
        {label: 'GTK4 placed after it is shown', argv: ['python3', APP, 'org.projectluma.LateTide', 'LateTide', '#2e7d32', '--clicks'], clicks: 'org.projectluma.LateTide', env: {GDK_BACKEND: 'wayland'},
            match: w => w.get_gtk_application_id() === 'org.projectluma.LateTide', monitor: () => home().main, tile: {x: 0.5, y: 0.5, width: 0.5, height: 0.5}, forceLate: true},
    ];
    for (const client of clients) {
        if (client.needsX11 && !x11Display)
            log(`no DISPLAY yet; Xwayland starts on demand`);
        // First run: no history, so it opens where the client puts it, undelayed.
        let {proc, win} = await spawnClient(client.argv, client.env, client.match);
        if (!win) {
            check(`${client.label}: client started`, false);
            continue;
        }
        await until(() => mem._windows.get(win)?.mapped, 15000, `${client.label} mapped`);
        check(`${client.label}: an app with no history is not held back`, !mem._windows.get(win)?.held);
        await sleep(client.slow ? 2500 : 800);
        mem.touch(win, 'test-window-menu');
        win.move_to_monitor(client.monitor());
        managerFor(client.monitor()).onTileFromWindowMenu(new Tile({...client.tile, groups: []}), win);
        await sleep(mem.timing.dwell + mem.timing.settle + 600);
        const tiledFrame = frameOf(win);
        proc.send_signal(15);
        await until(() => !global.display.list_all_windows().includes(win), 8000, `${client.label} closed`);
        await sleep(client.slow ? 1500 : 400);

        // Second run: it must appear already in its tile, in every painted frame
        // (except for a client forced down the late path, placed after it is shown).
        const savedTiming = {...mem.timing};
        if (client.forceLate)
            Object.assign(mem.timing, {holdAfterShown: 1, holdMax: 60});
        const stop = visibleFrameRecorder(client.match);
        ({proc, win} = await spawnClient(client.argv, client.env, client.match));
        await until(() => win && mem._windows.get(win)?.mapped, 15000, `${client.label} revealed`);
        Object.assign(mem.timing, savedTiming);
        await sleep(1200);
        const seen = stop();
        const frames = (win && seen.get(win)) ?? [];
        const wrong = frames.filter(f => !sameFrame(f, tiledFrame));
        check(`${client.label}: held until in place, then shown`, !!win && mem._windows.get(win)?.held);
        // A window is never held noticeably: at most 1 s in any case, and
        // well under half a second when the client behaves.
        const heldFor = win ? mem._windows.get(win)?.revealedAfter : undefined;
        const limit = client.slow || client.forceLate ? 1000 : 400;
        check(`${client.label}: shown within ${limit} ms`, heldFor !== undefined && heldFor <= limit,
            `held ${heldFor} ms (${win ? mem._windows.get(win)?.revealedWhy : '-'})`);
        // Revealed means visible on the desktop, not only in the overview's
        // clones, and nothing that clears the actor's transitions (the tiling
        // manager's own move does) can leave it transparent or shrunk.
        const actor = win?.get_compositor_private();
        actor?.remove_all_transitions();
        await sleep(450);
        check(`${client.label}: fully visible after its reveal, even with its transitions cleared`,
            !!actor && actor.opacity === 255 && actor.scale_x === 1 && actor.scale_y === 1,
            actor ? `opacity ${actor.opacity}, scale ${actor.scale_x}x${actor.scale_y}` : 'no actor');
        // And it takes a click right after launch (after 0012, Settings and
        // Notes showed hover but took no clicks).
        if (client.clicks && win)
            check(`${client.label}: a click right after launch lands`, await clickLands(win, client.clicks));
        check(`${client.label}: no painted frame shows it outside its tile`, frames.length > 0 && (client.forceLate || wrong.length === 0),
            `${frames.length} visible frames, ${wrong.length} elsewhere (${wrong.slice(0, 2).join(' | ')}); tile ${tiledFrame}`);
        check(`${client.label}: it ends in its tile`, !!win && sameFrame(frameOf(win), tiledFrame) && !!win.assignedTile, `${win ? frameOf(win) : '-'} vs ${tiledFrame}`);
        await shot(`launch-${client.label.split(' ')[0].toLowerCase()}-in-tile`);
        proc.send_signal(15);
        await until(() => !win || !global.display.list_all_windows().includes(win), 8000, `${client.label} closed`);
        await sleep(400);
    }
}

async function lockScenarios({tide, viola, claude}) {
    const home = () => ({main: indexOfIdentity('HOME-MAIN'), side: indexOfIdentity('HOME-SIDE'), panel: indexOfIdentity('panel:')});
    const terms = windowsOf('org.gnome.Ptyxis').filter(w => w.get_transient_for() === null).sort((a, b) => a.get_stable_sequence() - b.get_stable_sequence());
    const [term1, term2] = terms;
    mem.touch(term1, 'test-maximize');
    term1.move_to_monitor(home().panel);
    term1.get_maximized ? term1.maximize(Meta.MaximizeFlags.BOTH) : term1.maximize();
    await personDrags(term2, home().panel, 160, 120);
    await sleep(mem.timing.settle + 500);
    const five = [tide, viola, claude, term1, term2];
    const expected = new Map(five.map(w => [w, frameOf(w)]));
    const placements = new Map(five.map(w => [w, JSON.stringify(placementOf(w))]));
    await shot('home-five-windows-before-lock');
    log(`five windows: ${five.map(w => `${w.get_title()} ${placementOf(w)?.k} on ${placementOf(w)?.m}`).join('; ')}`);

    // 1. Lock and unlock, nothing changes: nothing moves, not for one frame.
    const memBefore = mem;
    const stopLocked = frameSampler(expected);
    await lockScreen();
    await sleep(2500);
    refresh();
    check('Tiling Shell stays enabled on the lock screen', Main.extensionManager.lookup(UUID).state === 1 && !!ext._lumaWindowMemory);
    log(`window memory ${mem === memBefore ? 'kept' : 're-created (GNOME re-enabled later extensions)'} at lock`);
    check('while locked every tiled window is still tiled', [tide, viola, claude].every(isTiledAt));
    const stopUnlock = frameSampler(expected);
    await unlockScreen('lock-no-change');
    const lockedRun = stopLocked();
    const unlockRun = stopUnlock();
    check('lock and unlock with no monitor change: no window out of place in any frame',
        lockedRun.bad.length === 0 && lockedRun.frames > 0, `${lockedRun.frames} frames; ${lockedRun.bad.slice(0, 3).join('; ')}`);
    check('unlock reveal: every frame shows every window in place', unlockRun.bad.length === 0 && unlockRun.frames > 0, `${unlockRun.frames} frames; ${unlockRun.bad.slice(0, 3).join('; ')}`);
    check('after unlock the maximized, tiled and floating windows are unchanged',
        five.every(w => JSON.stringify(placementOf(w)) === placements.get(w)) && term1.maximizedHorizontally && [tide, viola, claude].every(isTiledAt),
        five.map(w => `${w.get_title()}: ${JSON.stringify(placementOf(w))}`).join('; '));

    // 2. Lock, sleep, the side monitor drops out and comes back: restored while
    //    still locked, so the reveal shows every window in place.
    await lockScreen();
    mem.onPrepareForSleep(true);
    await setMonitors(['2560x1440'], {wait: false});
    await sleep(1500);
    await shot('asleep-side-monitor-gone');
    await setMonitors(['2560x1440', '1600x900'], {wait: false});
    await sleep(200);
    mem.onPrepareForSleep(false);
    await until(() => mem._stable && !mem._stableId, 10000, 'restored while locked');
    await sleep(800);
    await shot('woke-restored-behind-lock-screen');
    check('suspend with a monitor change: windows are back before unlock', five.every(w => sameFrame(frameOf(w), expected.get(w))),
        five.map(w => `${w.get_title()} ${frameOf(w)} vs ${expected.get(w)}`).join('; '));
    const stopReveal = frameSampler(expected);
    await unlockScreen('resume-monitor-change');
    const reveal = stopReveal();
    check('resume reveal: every frame shows every window in place', reveal.bad.length === 0 && reveal.frames > 0, `${reveal.frames} frames; ${reveal.bad.slice(0, 3).join('; ')}`);

    // 3. Unlocking at once after the monitors come back (fingerprint): the
    //    restore runs at unlock, before the desktop is revealed.
    await lockScreen();
    mem.onPrepareForSleep(true);
    await setMonitors(['2560x1440'], {wait: false});
    await sleep(1500);
    await setMonitors(['2560x1440', '1600x900'], {wait: false});
    await sleep(150);
    mem.onPrepareForSleep(false);
    check('quick unlock: monitors not yet settled when unlocking', !mem._stable);
    const settledAtUnlock = await unlockScreen('quick-unlock');
    check('quick unlock: restore ran synchronously at unlock, before the reveal', settledAtUnlock);
    await sleep(500);
    check('quick unlock: every window back in place', five.every(w => sameFrame(frameOf(w), expected.get(w))),
        five.map(w => `${w.get_title()} ${frameOf(w)} vs ${expected.get(w)}`).join('; '));
    await shot('quick-unlock-settled');
}

function placementOf(win) {
    return mem._placementOf(win, mem._monitors());
}

function near(a, b) {
    return a.every((v, i) => Math.abs(v - b[i]) <= 0.021);
}

function expectPlacement(name, win, serial, kind, rect) {
    const p = win ? placementOf(win) : null;
    const ok = p && p.m.includes(serial) && p.k === kind && (!rect || near(p.r, rect));
    check(name, ok, `got ${p ? `${p.k} [${p.r.join(', ')}] on ${p.m}` : 'nothing'}`);
}

async function shot(label) {
    step++;
    const shooter = new Shell.Screenshot();
    const [content, scale] = await shooter.screenshot_stage_to_content();
    const texture = content.get_texture();
    const name = `${String(step).padStart(2, '0')}-${label}`;
    const file = Gio.File.new_for_path(`${OUT}/${name}.png`);
    const stream = file.replace(null, false, Gio.FileCreateFlags.NONE, null);
    // Screenshots are evidence, not checks: a container whose image loader
    // sandbox cannot start still runs the scenario (the JSON state is kept).
    try {
        await Shell.Screenshot.composite_to_stream(texture, 0, 0, global.stage.width, global.stage.height, scale, null, 0, 0, 1, stream);
    } catch (e) {
        log(`screenshot ${name} not written: ${e.message.split('\n')[0]}`);
    }
    stream.close(null);
    const state = {
        label,
        monitors: Main.layoutManager.monitors.map((m, i) => ({index: i, x: m.x, y: m.y, width: m.width, height: m.height, key: monitorKeyOf(i)})),
        windows: global.display.list_all_windows().filter(w => w.get_window_type() === Meta.WindowType.NORMAL).map(w => {
            const r = w.get_frame_rect();
            return {title: w.get_title(), transient: w.get_transient_for() !== null, rect: [r.x, r.y, r.width, r.height], monitor: w.get_monitor()};
        }),
    };
    GLib.file_set_contents(`${OUT}/${name}.json`, JSON.stringify(state, null, 1));
}

export function init() {
    GLib.mkdir_with_parents(OUT, 0o755);
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 6000, () => {
        work().catch(e => { check('scenario ran without errors', false, `${e}\n${e.stack}`); })
            .finally(() => {
                GLib.file_set_contents(`${OUT}/results.json`, JSON.stringify(results, null, 1));
                try {
                    GLib.file_set_contents(`${OUT}/memory.json`, JSON.stringify(mem?._memory ?? {}, null, 1));
                } catch (e) {}
                log(`DONE ${results.filter(r => r.ok).length}/${results.length} passed`);
                Object.values(procs).forEach(p => p.send_signal(9));
                global.context.terminate();
            });
        return GLib.SOURCE_REMOVE;
    });
}

export async function run() {
    await new Promise(() => {});
}

async function work() {
    ext = Main.extensionManager.lookup(UUID)?.stateObj;
    mem = await until(() => ext?._lumaWindowMemory ?? (ext = Main.extensionManager.lookup(UUID)?.stateObj)?._lumaWindowMemory, 10000, 'window memory');
    check('Tiling Shell is running with window memory', !!mem);
    if (!mem) return;
    mem.constructor.testHooks = hooks;
    Object.assign(mem.timing, hooks.timing);
    mem._monitorsCache = null;
    mem._beginTransition('test-start');
    await waitStable();

    // ---- laptop only ----
    await shot('laptop-only-start');
    const panelKey = monitorKeyOf(0);
    check('laptop panel is identified by EDID, not connector', panelKey?.startsWith('panel:LEN|'), panelKey);

    // ---- home: laptop + two externals; the person arranges ----
    await setMonitors(['2560x1440', '1600x900']);
    const home = () => ({main: indexOfIdentity('HOME-MAIN'), side: indexOfIdentity('HOME-SIDE'), panel: indexOfIdentity('panel:')});
    check('home setup has three monitors', Main.layoutManager.monitors.length === 3);

    let tide = await launch('org.projectluma.Tide', 'Tide', '#2d6cdf', ['--dialog', '--splash']);
    let viola = await launch('org.projectluma.Viola', 'Viola', '#8e44ad');
    let claude = await launch('com.anthropic.Claude', 'Claude', '#d97757');
    let terminal = await launch('org.gnome.Ptyxis', 'Terminal', '#333333', ['--windows', '2']);
    await sleep(1200);
    const dialog = windowsOf('org.projectluma.Tide').find(w => w.get_transient_for() !== null);
    const splash = windowsOf('org.projectluma.Tide').find(w => !w.allows_resize());
    check('stand-in dialog and splash exist', !!dialog && !!splash);

    await personTiles(tide, home().side, {x: 0, y: 0, width: 0.5, height: 0.5});
    await personTiles(viola, home().main, {x: 0, y: 0, width: 0.5, height: 1});
    await personTiles(claude, home().main, {x: 0.5, y: 0, width: 0.5, height: 1});
    await sleep(mem.timing.dwell + 400);
    await shot('home-arranged');
    const dialogRect = dialog.get_frame_rect();
    const tideApp = mem._memory.setups[mem._setupKey]?.apps?.['org.projectluma.tide'] ?? {};
    check('only the main Tide window is remembered (dialog and splash never)', Object.keys(tideApp).join() === 'main', Object.keys(tideApp).join());
    const termApp = mem._memory.setups[mem._setupKey]?.apps?.['org.gnome.ptyxis'] ?? {};
    check('two Terminal windows get stable roles main and main-2', Object.keys(termApp).sort().join() === 'main,main-2', Object.keys(termApp).join());
    check('desktop-file-less app is identified by its app id', !!mem._memory.setups[mem._setupKey]?.apps?.['com.anthropic.claude']);

    // ---- launch memory: close, reopen (new PID and window id) ----
    const oldViolaId = viola.get_id();
    await quit('org.projectluma.Viola');
    viola = await launch('org.projectluma.Viola', 'Viola', '#8e44ad');
    check('relaunched Viola is a new window', viola.get_id() !== oldViolaId);
    expectPlacement('relaunched Viola opens in its last tile (identity survives PID/window churn)', viola, 'HOME-MAIN', 'tile', [0, 0, 0.5, 1]);
    await sleep(mem.timing.dwell + 400);
    // A Luma window asks for an opening size of its own (the kit's shared rule,
    // ADR-042). A remembered tile outranks it: the app's request never wins
    // over where the person left the window.
    await quit('org.projectluma.Viola');
    viola = await launch('org.projectluma.Viola', 'Viola', '#8e44ad', ['--size', '1336x941']);
    expectPlacement('a remembered tile outranks the size the app asks to open at', viola, 'HOME-MAIN', 'tile', [0, 0, 0.5, 1]);
    await sleep(mem.timing.dwell + 400);
    // Overnight: the last placement is 14 hours old and there are only two sessions of habit.
    await quit('org.projectluma.Viola');
    hooks.timeOffset += 14 * HOUR;
    viola = await launch('org.projectluma.Viola', 'Viola', '#8e44ad');
    expectPlacement('overnight (14 h, 2 sessions): Viola opens in its last tile', viola, 'HOME-MAIN', 'tile', [0, 0, 0.5, 1]);
    await sleep(mem.timing.dwell + 400);
    for (let i = 0; i < 2; i++) {
        await quit('org.projectluma.Viola');
        viola = await launch('org.projectluma.Viola', 'Viola', '#8e44ad');
        await sleep(mem.timing.dwell + 400);
    }
    await quit('org.projectluma.Viola');
    hooks.timeOffset += 9 * HOUR;
    viola = await launch('org.projectluma.Viola', 'Viola', '#8e44ad');
    expectPlacement('after 9 more hours Viola opens in its tile (last placement and habit agree)', viola, 'HOME-MAIN', 'tile', [0, 0, 0.5, 1]);
    await sleep(mem.timing.dwell + 400);

    // Scattered history: Claude lived in three different places, then 9 hours pass.
    for (const tile of [{x: 0, y: 0, width: 0.5, height: 0.5}, {x: 0.5, y: 0.5, width: 0.5, height: 0.5}]) {
        await quit('com.anthropic.Claude');
        claude = await launch('com.anthropic.Claude', 'Claude', '#d97757');
        await personTiles(claude, home().panel, tile);
        await sleep(mem.timing.dwell + 400);
    }
    await quit('com.anthropic.Claude');
    hooks.timeOffset += 9 * HOUR;
    claude = await launch('com.anthropic.Claude', 'Claude', '#d97757');
    expectPlacement('Claude with no clear habit opens at its last placement', claude, 'panel:', 'tile', [0.5, 0.5, 0.5, 0.5]);
    // No history at all: left exactly where it opened.
    const calculator = await launch('org.gnome.Calculator', 'Calculator', '#2e7d32', [], {firstFrame: false});
    const openedAt = await until(() => mem._windows.get(calculator)?.mapped && calculator.get_frame_rect().copy(), 5000, 'calculator mapped');
    await sleep(mem.timing.launchDelay + 600);
    const now = calculator.get_frame_rect();
    check('an app with no history is left where it opened', openedAt && now.x === openedAt.x && now.y === openedAt.y && now.width === openedAt.width,
        `${openedAt?.x},${openedAt?.y} -> ${now.x},${now.y}`);
    await quit('org.gnome.Calculator');
    await personTiles(claude, home().main, {x: 0.5, y: 0, width: 0.5, height: 1});
    await sleep(mem.timing.dwell + 400);

    // A window the person touches before it is placed is never moved.
    const touchId = global.display.connect('window-created', (_d, w) => {
        GLib.idle_add(GLib.PRIORITY_DEFAULT, () => { mem.touch(w, 'test-early-drag'); return GLib.SOURCE_REMOVE; });
    });
    await quit('org.projectluma.Viola');
    hooks.timeOffset += 9 * HOUR;
    viola = await launch('org.projectluma.Viola', 'Viola', '#8e44ad');
    global.display.disconnect(touchId);
    const vp = placementOf(viola);
    check('Viola touched before placement stays where the person has it', vp && !(vp.k === 'tile' && vp.m.includes('HOME-MAIN')), vp ? `${vp.k} on ${vp.m}` : 'nothing');
    await personTiles(viola, home().main, {x: 0, y: 0, width: 0.5, height: 1});
    await sleep(mem.timing.dwell + 400);
    await shot('home-learned');

    // ---- office: laptop + one ultrawide (never seen) ----
    await setMonitors(['3440x1440']);
    await shot('office-first-time');
    expectPlacement('first office visit: Viola follows its role to the new external (same tile)', viola, 'OFFICE', 'tile', [0, 0, 0.5, 1]);
    expectPlacement('first office visit: Claude follows its role to the new external (same tile)', claude, 'OFFICE', 'tile', [0.5, 0, 0.5, 1]);
    const tp = placementOf(tide);
    check('first office visit: Tide (from a monitor with no counterpart) stays where Mutter put it', tp && !tp.m.includes('OFFICE'), tp?.m);
    check('dialog never remembered after hotplug', !Object.keys(mem._memory.setups).some(k => Object.keys(mem._memory.setups[k].apps?.['org.projectluma.tide'] ?? {}).some(r => r !== 'main')));
    const office = () => indexOfIdentity('OFFICE');
    await personTiles(tide, office(), {x: 0.5, y: 0, width: 0.5, height: 0.5});
    await personTiles(viola, office(), {x: 0, y: 0, width: 0.34, height: 1});
    await sleep(mem.timing.dwell + 400);
    await shot('office-arranged');

    // ---- laptop alone ----
    await setMonitors([]);
    await shot('laptop-only-again');
    check('laptop alone: every window is on the panel', [tide, viola, claude].every(w => w.get_monitor() === 0));

    // ---- back home: known setup, exact restore ----
    await setMonitors(['2560x1440', '1600x900']);
    await shot('home-restored');
    expectPlacement('home again: Tide back in the top-left tile of the side monitor', tide, 'HOME-SIDE', 'tile', [0, 0, 0.5, 0.5]);
    expectPlacement('home again: Viola back in the left tile of the main monitor', viola, 'HOME-MAIN', 'tile', [0, 0, 0.5, 1]);
    expectPlacement('home again: Claude back in the right tile of the main monitor', claude, 'HOME-MAIN', 'tile', [0.5, 0, 0.5, 1]);
    const d2 = dialog.get_transient_for() ? dialog : null;
    check('dialog was not placed by memory', !!d2 && !mem._windows.get(dialog)?.touchedWhy && dialog[Object.getOwnPropertySymbols(dialog).find(s => s.description === 'luma-window-memory-own')] === undefined);

    // ---- office again: known setup ----
    await setMonitors(['3440x1440']);
    await shot('office-restored');
    expectPlacement('office again: Tide back in the top-right tile', tide, 'OFFICE', 'tile', [0.5, 0, 0.5, 0.5]);
    expectPlacement('office again: Viola back in its narrow left tile', viola, 'OFFICE', 'tile', [0, 0, 0.34, 1]);
    expectPlacement('office again: Claude back in the right tile', claude, 'OFFICE', 'tile', [0.5, 0, 0.5, 1]);

    // ---- suspend at the office, monitors drop out and come back ----
    const beforeSleep = JSON.stringify(mem._memory.setups[mem._setupKey]);
    mem.onPrepareForSleep(true);
    await setMonitors([], {wait: false});
    await sleep(1500);
    await shot('asleep-monitor-gone');
    await setMonitors(['3440x1440'], {wait: false});
    await sleep(300);
    mem.onPrepareForSleep(false);
    await waitStable();
    await shot('resumed-office');
    expectPlacement('resume: Tide restored', tide, 'OFFICE', 'tile', [0.5, 0, 0.5, 0.5]);
    expectPlacement('resume: Viola restored', viola, 'OFFICE', 'tile', [0, 0, 0.34, 1]);
    expectPlacement('resume: Claude restored', claude, 'OFFICE', 'tile', [0.5, 0, 0.5, 1]);
    const afterSleep = JSON.parse(beforeSleep);
    const nowRec = mem._memory.setups[mem._setupKey];
    check('nothing was recorded while asleep', ['org.projectluma.tide', 'org.projectluma.viola', 'com.anthropic.claude'].every(a => JSON.stringify(afterSleep.apps[a].main.last.p) === JSON.stringify(nowRec.apps[a].main.last.p)));

    // ---- the person moves a window while the monitors settle: it wins ----
    await setMonitors(['2560x1440', '1600x900'], {wait: false});
    await sleep(200);
    const panelIdx = Main.layoutManager.monitors.findIndex((m, i) => monitorKeyOf(i)?.startsWith('panel:'));
    await personDrags(viola, panelIdx >= 0 ? panelIdx : 0, 40, 40);
    await waitStable();
    await shot('home-person-moved-viola');
    const vp2 = placementOf(viola);
    check('Viola moved by the person during settling is not restored', vp2 && !vp2.m.includes('HOME-MAIN'), vp2 ? `${vp2.k} on ${vp2.m}` : '');
    expectPlacement('others still restored around it: Tide', tide, 'HOME-SIDE', 'tile', [0, 0, 0.5, 0.5]);
    expectPlacement('others still restored around it: Claude', claude, 'HOME-MAIN', 'tile', [0.5, 0, 0.5, 1]);

    // ---- restart every app: identity churn, recent placements ----
    for (const id of ['org.projectluma.Tide', 'com.anthropic.Claude', 'org.projectluma.Viola'])
        await quit(id);
    tide = await launch('org.projectluma.Tide', 'Tide', '#2d6cdf');
    claude = await launch('com.anthropic.Claude', 'Claude', '#d97757');
    viola = await launch('org.projectluma.Viola', 'Viola', '#8e44ad');
    await shot('home-apps-restarted');
    expectPlacement('restarted Tide opens where it lives at home', tide, 'HOME-SIDE', 'tile', [0, 0, 0.5, 0.5]);
    expectPlacement('restarted Claude opens where it lives at home', claude, 'HOME-MAIN', 'tile', [0.5, 0, 0.5, 1]);
    expectPlacement('restarted Viola: its strong habit overrides one stray last placement', viola, 'HOME-MAIN', 'tile', [0, 0, 0.5, 1]);

    // ---- months of habit, a one-off detour, then a real move ----
    const DAY = 24 * HOUR;
    const Tile = (await import(`file://${ext.path}/components/layout/Tile.js`)).default;
    const tileBy = async (win, monitorIndex, tile) => {
        mem.touch(win, 'test-window-menu');
        win.move_to_monitor(monitorIndex);
        managerFor(monitorIndex).onTileFromWindowMenu(new Tile({...tile, groups: []}), win);
        await sleep(mem.timing.settle + 300);
    };
    const canvasSession = async (hours, tile, monitor) => {
        const win = await launch('org.projectluma.Canvas', 'Canvas', '#00897b');
        await sleep(600);
        if (tile) await tileBy(win, monitor, tile);
        hooks.timeOffset += hours * HOUR;
        await quit('org.projectluma.Canvas');
        hooks.timeOffset += 7 * DAY;
        return win;
    };
    const HABIT_TILE = {x: 0, y: 0, width: 0.5, height: 1};
    const DETOUR_TILE = {x: 0, y: 0, width: 0.5, height: 1};
    const NEW_TILE = {x: 0, y: 0.5, width: 0.5, height: 0.5};
    await canvasSession(4, HABIT_TILE, home().main);
    for (let i = 0; i < 13; i++)
        await canvasSession(4);
    let canvas = await launch('org.projectluma.Canvas', 'Canvas', '#00897b');
    expectPlacement('after 14 weeks of use Canvas opens in its long-term tile', canvas, 'HOME-MAIN', 'tile', [0, 0, 0.5, 1]);
    await quit('org.projectluma.Canvas');
    await canvasSession(2, DETOUR_TILE, home().panel);
    canvas = await launch('org.projectluma.Canvas', 'Canvas', '#00897b');
    expectPlacement('a one-off detour does not erode months of habit: Canvas opens in its long-term tile', canvas, 'HOME-MAIN', 'tile', [0, 0, 0.5, 1]);
    await quit('org.projectluma.Canvas');
    for (let i = 0; i < 4; i++)
        await canvasSession(5, NEW_TILE, home().side);
    canvas = await launch('org.projectluma.Canvas', 'Canvas', '#00897b');
    await shot('home-canvas-moved-for-good');
    expectPlacement('a move kept for 4 sessions wins over months of habit', canvas, 'HOME-SIDE', 'tile', [0, 0.5, 0.5, 0.5]);
    await quit('org.projectluma.Canvas');

    // ---- one observation, then a reboot seconds later ----
    let notes = await launch('org.gnome.TextEditor', 'Notes', '#f9a825');
    mem.touch(notes, 'test-window-menu');
    notes.move_to_monitor(home().side);
    managerFor(home().side).onTileFromWindowMenu(new Tile({x: 0.5, y: 0, width: 0.5, height: 1, groups: []}), notes);
    await sleep(mem.timing.touchedSettle + mem.timing.touchedSave + 300);
    const onDisk = JSON.parse(new TextDecoder().decode(GLib.file_get_contents(GLib.build_filenamev([GLib.get_user_state_dir(), 'luma', 'window-memory.json']))[1]));
    const savedNotes = Object.values(onDisk.setups).map(x => x.apps?.['org.gnome.texteditor']?.main?.last).find(Boolean);
    check('a window tiled seconds after launch is saved to disk within a second', savedNotes?.p?.k === 'tile' && savedNotes.p.m.includes('HOME-SIDE'), JSON.stringify(savedNotes?.p ?? null));
    await quit('org.gnome.TextEditor');
    await restartTiling(14 * HOUR);
    notes = await launch('org.gnome.TextEditor', 'Notes', '#f9a825');
    await shot('home-after-reboot');
    expectPlacement('after a reboot and a night, Notes (one observation) opens in its tile', notes, 'HOME-SIDE', 'tile', [0.5, 0, 0.5, 1]);
    await quit('org.gnome.TextEditor');

    await lockScenarios({tide, viola, claude});
    await launchHoldScenarios();

    // ---- Quick Settings switch: off, a window moved by hand, on again ----
    {
        const term2 = windowsOf('org.gnome.Ptyxis').filter(w => w.get_transient_for() === null).sort((a, b) => a.get_stable_sequence() - b.get_stable_sequence())[1];
        const before = new Map([tide, viola, claude].map(w => [w, frameOf(w)]));
        Main.extensionManager.disableExtension(UUID);
        await sleep(1500);
        const area = Main.layoutManager.getWorkAreaForMonitor(term2.get_monitor());
        term2.move_frame(true, area.x + 420, area.y + 300);
        const handMoved = frameOf(term2);
        Main.extensionManager.enableExtension(UUID);
        await sleep(100);
        refresh();
        await waitStable();
        check('switching tiling off and on keeps every tiled window exactly in its tile',
            [...before].every(([w, f]) => sameFrame(frameOf(w), f) && w.assignedTile && w.is_externally_tiled?.() !== false),
            [...before].map(([w, f]) => `${w.get_title()} ${f} -> ${frameOf(w)}`).join('; '));
        check('a window moved by hand while tiling was off is not pulled back', sameFrame(frameOf(term2), handMoved), `${handMoved} -> ${frameOf(term2)}`);
    }

    // ---- Tiling Shell off, carried to the office, on again ----
    Main.extensionManager.disableExtension(UUID);
    await sleep(300);
    await setMonitors(['3440x1440'], {wait: false});
    await sleep(1500);
    await shot('locked-moved-to-office');
    await restartTiling(0, false);
    await shot('unlocked-office');
    expectPlacement('unlock after moving desks: Tide restored from the saved memory', tide, 'OFFICE', 'tile', [0.5, 0, 0.5, 0.5]);
    expectPlacement('unlock after moving desks: Claude restored', claude, 'OFFICE', 'tile', [0.5, 0, 0.5, 1]);
    expectPlacement('unlock after moving desks: Viola restored', viola, 'OFFICE', 'tile', [0, 0, 0.34, 1]);

    // ---- forget ----
    mem._settings.set_int64('luma-window-memory-reset', GLib.get_real_time());
    await sleep(300);
    check('Forget Window Positions clears the memory', Object.values(mem._memory.setups).every(s => Object.keys(s.apps ?? {}).length === 0));
    const saved = GLib.file_get_contents(GLib.build_filenamev([GLib.get_user_state_dir(), 'luma', 'window-memory.json']));
    check('memory file lives under XDG state and is versioned', saved[0] && JSON.parse(new TextDecoder().decode(saved[1])).version === 1);
}
