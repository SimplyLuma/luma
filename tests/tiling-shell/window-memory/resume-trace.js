// SPDX-License-Identifier: Apache-2.0
// Resume and dock geometry trace, run inside a headless GNOME Shell:
//   gnome-shell --headless --virtual-monitor 1920x1200 --automation-script=resume-trace.js
// A 5120x1440 ultrawide at scale 1 above-right of the 1920x1200 panel at 1.25
// (mixed scale), like the ThinkPad at home. Three clients: a Chromium app
// window (Wayland, like Claude), GTK4 (Wayland) and GTK4 (XWayland). Every
// painted frame records where each window is drawn; a frame is bad when a
// window is not fully inside one monitor's work area. Default window memory
// timings, so the trace shows what a person sees.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import MetaTest from 'gi://MetaTest';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const OUT = GLib.getenv('WM_OUT') ?? '/wm/out';
const APP = '/wm/test/app.py';
const UUID = 'tilingshell@ferrarodomenico.com';
const IDENTITY = {
    // Logical sizes: the panel is 1920x1200 at 1.25.
    '1536x960': {vendor: 'LEN', product: 'X1 2-in-1 Gen 10 panel', serial: '', builtin: true},
    '5120x1440': {vendor: 'GSM', product: 'LG ULTRAWIDE', serial: 'HOME-ULTRAWIDE'},
};
// Kernel resumed (the wall clock jumped against the monotonic clock).
let resumed = false;
const hooks = {
    timeOffset: 0,
    resumed: () => resumed,
    timing: {dwell: 1500},
    monitorIdentity: (monitor, geometry) =>
        IDENTITY[`${geometry.width}x${geometry.height}`] ?? {vendor: 'X', product: 'unknown', serial: `${geometry.width}x${geometry.height}`},
};
const results = [];
const monitors = {};
const procs = [];
let mem, ext;

const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const log = msg => console.log(`[wmtrace] ${msg}`);
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
function refresh() {
    ext = Main.extensionManager.lookup(UUID)?.stateObj;
    mem = ext?._lumaWindowMemory ?? mem;
    return mem;
}
const frameOf = w => { const r = w.get_frame_rect(); return [r.x, r.y, r.width, r.height]; };
const indexOf = s => mem.debugState().monitors.find(m => m.key.includes(s))?.index ?? -1;

// ---- monitors ------------------------------------------------------------

function displayConfig(method, args, type) {
    return new Promise((resolve, reject) => {
        Gio.DBus.session.call('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
            'org.gnome.Mutter.DisplayConfig', method, args, type ? new GLib.VariantType(type) : null,
            Gio.DBusCallFlags.NONE, -1, null, (c, res) => {
                try {
                    resolve(c.call_finish(res));
                } catch (e) {
                    reject(e);
                }
            });
    });
}

// layout: [{size, x, y, scale, primary}] by monitor size.
async function applyLayout(layout, method = 2) {
    const state = (await displayConfig('GetCurrentState', null, '(ua((ssss)a(siiddada{sv})a{sv})a(iiduba(ssss)a{sv})a{sv})')).deep_unpack();
    const [serial, mons] = state;
    const logical = [];
    for (const l of layout) {
        const mon = mons.find(([, modes]) => modes.some(m => `${m[1]}x${m[2]}` === l.size));
        if (!mon) throw new Error(`no monitor ${l.size}`);
        const [spec, modes] = mon;
        const mode = modes.find(m => `${m[1]}x${m[2]}` === l.size);
        logical.push(new GLib.Variant('(iiduba(ssa{sv}))', [l.x, l.y, l.scale, 0, !!l.primary, [[spec[0], mode[0], {}]]]));
    }
    const args = GLib.Variant.new_tuple([
        new GLib.Variant('u', serial), new GLib.Variant('u', method),
        GLib.Variant.new_array(new GLib.VariantType('(iiduba(ssa{sv}))'), logical),
        new GLib.Variant('a{sv}', {'layout-mode': new GLib.Variant('u', 1)}),
    ]);
    await displayConfig('ApplyMonitorsConfig', args, null);
    if (method === 2) {
        // What pressing "Keep Changes" does; otherwise Mutter reverts later.
        await sleep(300);
        global.window_manager.complete_display_change(true);
        for (const dialog of Main.layoutManager.modalDialogGroup.get_children())
            dialog.close?.();
    }
}

function plug(sizes) {
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
}

async function waitStable() {
    await sleep(100);
    await until(() => mem._stable && !mem._stableId, 15000, 'stable setup');
    await sleep(mem.timing.quiet + 300);
}

// ---- clients -------------------------------------------------------------

async function spawn(argv, env, match) {
    const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE});
    launcher.setenv('WAYLAND_DISPLAY', GLib.getenv('WAYLAND_DISPLAY') ?? 'wayland-0', true);
    launcher.setenv('GSK_RENDERER', 'cairo', true);
    launcher.setenv('NO_AT_BRIDGE', '1', true);
    launcher.unsetenv('DISPLAY');
    for (const [k, v] of Object.entries(env))
        launcher.setenv(k, v, true);
    const before = new Set(global.display.list_all_windows());
    procs.push(launcher.spawnv(argv));
    const win = await until(() => global.display.list_all_windows().find(w => !before.has(w) && !w.get_transient_for() &&
        w.get_window_type() === Meta.WindowType.NORMAL && match(w)), 40000, `${argv[0]} window`);
    if (win)
        await until(() => mem._windows.get(win)?.mapped, 15000, 'mapped');
    await sleep(1500);
    return win;
}

async function personTiles(win, monitorIndex, tile) {
    const Tile = (await import(`file://${ext.path}/components/layout/Tile.js`)).default;
    mem.touch(win, 'test-window-menu');
    win.move_to_monitor(monitorIndex);
    ext._tilingManagers[monitorIndex].onTileFromWindowMenu(new Tile({...tile, groups: []}), win);
    await sleep(mem.timing.settle + 500);
}

async function personDrags(win, monitorIndex, x, y) {
    global.display.emit('grab-op-begin', win, Meta.GrabOp.KEYBOARD_MOVING);
    win.assignedTile = undefined;
    const area = Main.layoutManager.getWorkAreaForMonitor(monitorIndex);
    win.move_frame(true, area.x + x, area.y + y);
    global.display.emit('grab-op-end', win, Meta.GrabOp.KEYBOARD_MOVING);
    await sleep(mem.timing.settle + 500);
}

// ---- trace ---------------------------------------------------------------

function inside(r, a, slack = 1) {
    return r[0] >= a.x - slack && r[1] >= a.y - slack && r[0] + r[2] <= a.x + a.width + slack && r[1] + r[3] <= a.y + a.height + slack;
}

function monitorsTouched(r) {
    return Main.layoutManager.monitors.filter(m => r[0] < m.x + m.width && r[0] + r[2] > m.x && r[1] < m.y + m.height && r[1] + r[3] > m.y).length;
}

// Where each window is drawn in every painted frame (the actor, which is what a
// person sees, including Tiling Shell's easing), from t=0.
function tracer(wins) {
    const start = GLib.get_monotonic_time();
    const rows = new Map(wins.map(w => [w, []]));
    let frames = 0;
    const id = global.stage.connect('after-paint', () => {
        frames++;
        const t = Math.round((GLib.get_monotonic_time() - start) / 1000);
        const areas = Main.layoutManager.monitors.map((_m, i) => Main.layoutManager.getWorkAreaForMonitor(i));
        for (const w of wins) {
            const actor = w.get_compositor_private();
            if (!actor || !actor.visible || actor.get_paint_opacity() === 0)
                continue;
            const f = w.get_frame_rect();
            const b = w.get_buffer_rect();
            const [ax, ay] = actor.get_position();
            const [tx, ty] = actor.get_translation ? actor.get_translation() : [0, 0];
            const [sx, sy] = actor.get_scale();
            const drawn = [Math.round(f.x - b.x + ax + tx), Math.round(f.y - b.y + ay + ty), Math.round(f.width * sx), Math.round(f.height * sy)];
            const ok = areas.some(a => inside(drawn, a));
            const list = rows.get(w);
            const last = list[list.length - 1];
            if (!last || last.drawn.join() !== drawn.join())
                list.push({t, frame: frames, drawn, ok, monitors: monitorsTouched(drawn), layout: Main.layoutManager.monitors.length});
            else
                last.until = t;
        }
    });
    return () => {
        global.stage.disconnect(id);
        return {frames, rows};
    };
}

function summarize(label, wins, trace, target) {
    const out = {};
    for (const w of wins) {
        const list = trace.rows.get(w);
        const name = /^data:/.test(w.get_title()) ? 'Chromium' : w.get_title();
        const bad = list.filter(r => !r.ok);
        const final = frameOf(w);
        const areas = Main.layoutManager.monitors.map((_m, i) => Main.layoutManager.getWorkAreaForMonitor(i));
        const lastBad = bad.length ? bad[bad.length - 1] : null;
        const moves = list.length - 1;
        out[name] = {moves, bad: bad.length, finalOk: areas.some(a => inside(final, a)), final, lastBadAt: lastBad ? (lastBad.until ?? lastBad.t) : 0, rows: list};
        log(`${label}: ${name}: ${moves} position(s) after the first, ${bad.length} not inside a work area` +
            `${lastBad ? ` (last until ${lastBad.until ?? lastBad.t} ms, e.g. ${lastBad.drawn} over ${lastBad.monitors} monitor(s))` : ''}; final ${final}`);
        for (const r of list.slice(0, 12))
            log(`   t=${r.t}${r.until ? `..${r.until}` : ''} ms ${r.drawn} ${r.ok ? 'in a work area' : 'OUTSIDE'} monitors=${r.monitors} layout=${r.layout}`);
    }
    GLib.file_set_contents(`${OUT}/${label}.json`, JSON.stringify({frames: trace.frames, windows: out}, null, 1));
    if (target) {
        check(`${label}: every window ends fully inside a work area`, Object.values(out).every(o => o.finalOk), Object.entries(out).map(([n, o]) => `${n} ${o.final}`).join('; '));
        check(`${label}: no painted frame shows a window outside a work area once the new layout is visible`,
            Object.values(out).every(o => o.rows.every(r => r.ok || r.layout !== target)),
            Object.entries(out).map(([n, o]) => `${n}: ${o.rows.filter(r => !r.ok && r.layout === target).length}`).join('; '));
        check(`${label}: each window moves at most once`, Object.values(out).every(o => o.moves <= 1), Object.entries(out).map(([n, o]) => `${n}: ${o.moves}`).join('; '));
    }
    return out;
}

// ---- scenario ------------------------------------------------------------

async function work() {
    mem = await until(() => refresh()?._lumaWindowMemory ?? refresh(), 10000, 'window memory');
    refresh();
    check('Tiling Shell is running with window memory', !!mem);
    if (!mem) return;
    mem.constructor.testHooks = hooks;
    Object.assign(mem.timing, hooks.timing);
    log(`timing ${JSON.stringify(mem.timing)}`);
    mem._monitorsCache = null;

    // Laptop alone at 1.25.
    plug([]);
    await sleep(500);
    await applyLayout([{size: '1920x1200', x: 0, y: 0, scale: 1.25, primary: true}]);
    await sleep(500);
    mem._beginTransition('test-start');
    await waitStable();

    // Docked at home: ultrawide right, panel below-left, mixed scale; stored.
    plug(['5120x1440']);
    await sleep(800);
    await applyLayout([{size: '5120x1440', x: 1536, y: 0, scale: 1, primary: true}, {size: '1920x1200', x: 0, y: 695, scale: 1.25}]);
    await waitStable();
    log(`docked layout: ${Main.layoutManager.monitors.map(m => `${m.width}x${m.height}+${m.x}+${m.y}@${m.geometry_scale}`).join(' ')}`);

    const chromium = await spawn(['chromium-browser', '--no-sandbox', '--ozone-platform=wayland', '--user-data-dir=/tmp/wm-chromium', '--no-first-run',
        '--disable-gpu', '--class=org.projectluma.ChromiumApp', '--app=data:text/html,<body style="background:%23d97757"><h1>Claude stand-in</h1>'],
    {}, w => /chrom/i.test(`${w.get_wm_class()} ${w.get_sandboxed_app_id()} ${w.get_wm_class_instance()}`));
    const notes = await spawn(['python3', APP, 'org.projectluma.Notes', 'Notes', '#2d6cdf'], {GDK_BACKEND: 'wayland'},
        w => w.get_gtk_application_id() === 'org.projectluma.Notes');
    const xnotes = await spawn(['python3', APP, 'org.projectluma.XNotes', 'XNotes', '#00838f'], {GDK_BACKEND: 'x11', DISPLAY: GLib.getenv('DISPLAY') ?? ':0'},
        w => w.get_client_type() === Meta.WindowClientType.X11);
    check('three clients started', chromium && notes && xnotes, `${!!chromium} ${!!notes} ${!!xnotes}`);
    const wins = [chromium, notes, xnotes].filter(Boolean);
    wins.forEach(w => log(`client ${w.get_title()} ${w.get_client_type() === Meta.WindowClientType.X11 ? 'X11' : 'Wayland'}`));

    const uw = () => indexOf('HOME-ULTRAWIDE');
    const panel = () => indexOf('panel:');
    if (chromium)
        await personTiles(chromium, uw(), {x: 0.75, y: 0, width: 0.25, height: 0.5});
    if (notes)
        await personTiles(notes, uw(), {x: 0.25, y: 0, width: 0.5, height: 1});
    if (xnotes)
        await personDrags(xnotes, panel(), 200, 150);
    await sleep(mem.timing.dwell + mem.timing.settle + 500);
    const docked = new Map(wins.map(w => [w, frameOf(w)]));
    // Monitor and kind, with the rect to the nearest percent.
    const placement = w => { const p = mem._placementOf(w, mem._monitors()); return p ? `${p.k} [${p.r.map(v => v.toFixed(2)).join(' ')}] on ${p.m}` : 'none'; };
    const dockedPlacements = new Map(wins.map(w => [w, placement(w)]));
    log(`docked frames: ${wins.map(w => `${w.get_title()} ${frameOf(w)}`).join('; ')}`);

    // Undock: the ultrawide goes away while awake.
    let stop = tracer(wins);
    plug([]);
    await waitStable();
    await sleep(2500);
    summarize('undock', wins, stop(), 1);
    // On the laptop the person drags the Chromium window to the right edge and
    // lets it live there (a float that spans the panel's right half).
    if (chromium)
        await personDrags(chromium, panel(), 700, 0);
    await sleep(mem.timing.dwell + mem.timing.settle + 500);
    log(`laptop frames: ${wins.map(w => `${w.get_title()} ${frameOf(w)}`).join('; ')}`);

    // Sleep, dock while asleep, wake: the stored docked configuration returns,
    // and the monitors change before logind's wake signal, as on the ThinkPad.
    resumed = false;
    mem.onPrepareForSleep(true);
    await sleep(1000);
    stop = tracer(wins);
    resumed = true;
    plug(['5120x1440']);
    await sleep(150);
    mem.onPrepareForSleep(false);
    await sleep(9000);
    const resume = summarize('resume-docked', wins, stop(), 2);
    log(`resume layout: ${Main.layoutManager.monitors.map(m => `${m.width}x${m.height}+${m.x}+${m.y}@${m.geometry_scale}`).join(' ')}`);
    log(`after resume: ${wins.map(w => `${w.get_title()} ${frameOf(w)} (docked ${docked.get(w)})`).join('; ')}`);
    check('resume: windows return to their docked places', wins.every(w => placement(w) === dockedPlacements.get(w)),
        wins.map(w => `${w.get_title()} ${placement(w)} vs ${dockedPlacements.get(w)}`).join('; '));
    void resume;

    // A client slow to wake acknowledges an old configure seconds later: the
    // window lands mostly off its work area, top above the panel (Claude on
    // 2026-09-17). It must be brought back before that frame is painted.
    if (chromium) {
        stop = tracer([chromium]);
        const panelArea = Main.layoutManager.getWorkAreaForMonitor(panel());
        chromium.move_frame(false, panelArea.x + 783, panelArea.y - 696);
        await sleep(3000);
        summarize('late-client-jump', [chromium], stop(), 2);
    }

    await sweep('sweep-tiling-on', uw, panel);
    // The guarantee is Mutter's: it holds with Tiling Shell switched off.
    Main.extensionManager.disableExtension(UUID);
    await sleep(800);
    await sweep('sweep-tiling-off', uw, panel);
    Main.extensionManager.enableExtension(UUID);
    await sleep(1500);
    refresh();
    Object.assign(mem.timing, hooks.timing);
    await waitStable();

    // Undock by sleeping, wake on the laptop alone.
    resumed = false;
    mem.onPrepareForSleep(true);
    await sleep(1000);
    stop = tracer(wins);
    resumed = true;
    plug([]);
    await sleep(150);
    mem.onPrepareForSleep(false);
    await sleep(9000);
    summarize('resume-undocked', wins, stop(), 1);
}

// Apps that choose where their window goes (Electron restoring saved bounds,
// the ChatGPT case on 2026-09-17), on the docked mixed-scale layout: the
// ultrawide at 1536,0 and the 1536x960 panel at 0,695. Every visible frame of
// every window, from its first, must be fully inside one work area.
const REQUESTS = [
    {label: 'above the panel, in the empty space', rect: [200, 100, 900, 700]},
    {label: 'above the top of every monitor', rect: [-400, -800, 1100, 900]},
    {label: 'straddling the empty space and the ultrawide', rect: [1100, 300, 1000, 600]},
    {label: 'past the bottom-right of the ultrawide', rect: [6200, 1100, 900, 700]},
    {label: 'below the panel', rect: [100, 1500, 800, 600]},
    {label: 'bigger than any monitor', rect: [0, 0, 7000, 2000]},
    {label: 'panel, top edge above it (Claude after resume)', rect: [783, 9, 743, 874]},
    {label: 'ultrawide, top 700 px above it (ChatGPT)', rect: [5000, -700, 1100, 860]},
];

async function sweep(label, uw, panel) {
    const x11 = GLib.getenv('DISPLAY') ?? ':0';
    let n = 0;
    const failures = [];
    // XWayland may scale X11 coordinates (native scaling on mixed scales):
    // measure it, so every request means the logical place listed.
    const probe = await spawn(['python3', '/wm/test/xclient.py', 'XProbe', '0', '0', '1000', '600'], {DISPLAY: x11}, w => w.get_wm_class() === 'XProbe');
    const xscale = probe ? Math.max(1, Math.round(1000 / probe.get_buffer_rect().width * 4) / 4) : 1;
    log(`${label}: X11 coordinates are ${xscale}x logical`);
    procs.pop()?.send_signal(9);
    await until(() => !probe || !global.display.list_all_windows().includes(probe), 5000, 'probe closed');
    for (const request of REQUESTS) {
        for (const mode of ['hints', 'move']) {
            n++;
            const cls = `XReq${n}`;
            const argv = ['python3', '/wm/test/xclient.py', cls, ...request.rect.map(v => String(Math.round(v * xscale))), ...(mode === 'move' ? ['--move'] : [])];
            const stopAll = allWindowsTracer();
            const win = await spawn(argv, {DISPLAY: x11}, w => w.get_wm_class() === cls);
            await sleep(mode === 'move' ? 1200 : 400);
            const rows = stopAll().get(win) ?? [];
            const bad = rows.filter(r => !r.ok);
            const final = win ? frameOf(win) : null;
            const ok = !!win && rows.length > 0 && bad.length === 0;
            log(`${label}: X11 ${mode} ${request.label} [${request.rect}] -> first ${rows[0]?.drawn} final ${final}; ${rows.length} position(s), ${bad.length} outside`);
            if (!ok)
                failures.push(`${mode} ${request.label}: ${bad.slice(0, 2).map(r => r.drawn).join(' | ') || 'no window'}`);
            procs.pop()?.send_signal(9);
            await until(() => !win || !global.display.list_all_windows().includes(win), 5000, 'closed');
        }
    }
    // Chromium on XWayland choosing its own bounds, as Electron apps do.
    for (const [x, y, w, h] of [[5000, -700, 1100, 860], [200, 100, 900, 700]]) {
        const stopAll = allWindowsTracer();
        const win = await spawn(['chromium-browser', '--no-sandbox', '--ozone-platform=x11', `--user-data-dir=/tmp/wm-chromium-x-${x}`, '--no-first-run',
            '--disable-gpu', `--window-position=${Math.round(x * xscale)},${Math.round(y * xscale)}`, `--window-size=${Math.round(w * xscale)},${Math.round(h * xscale)}`, '--class=org.projectluma.ChromiumX',
            '--app=data:text/html,<body style="background:%23444">x11</body>'], {DISPLAY: x11},
        w2 => w2.get_client_type() === Meta.WindowClientType.X11 && /chrom/i.test(`${w2.get_wm_class()} ${w2.get_wm_class_instance()}`));
        await sleep(1500);
        const rows = stopAll().get(win) ?? [];
        const bad = rows.filter(r => !r.ok);
        log(`${label}: Chromium X11 at [${x},${y},${w},${h}] -> first ${rows[0]?.drawn} final ${win ? frameOf(win) : null}; ${bad.length} outside`);
        if (!win || !rows.length || bad.length)
            failures.push(`Chromium X11 [${x},${y}]: ${bad.slice(0, 2).map(r => r.drawn).join(' | ') || 'no window'}`);
        procs.pop()?.send_signal(9);
        await until(() => !win || !global.display.list_all_windows().includes(win), 5000, 'closed');
    }
    // A Wayland app asking for a size bigger than the monitor.
    {
        const stopAll = allWindowsTracer();
        const win = await spawn(['python3', APP, 'org.projectluma.Huge', 'Huge', '#6a1b9a', '--size', '7000x2600'], {GDK_BACKEND: 'wayland'},
            w => w.get_gtk_application_id() === 'org.projectluma.Huge');
        await sleep(800);
        const rows = stopAll().get(win) ?? [];
        const bad = rows.filter(r => !r.ok);
        log(`${label}: GTK4 Wayland 7000x2600 -> first ${rows[0]?.drawn} final ${win ? frameOf(win) : null}; ${bad.length} outside`);
        if (!win || !rows.length || bad.length)
            failures.push(`GTK4 Wayland huge: ${bad.slice(0, 2).map(r => r.drawn).join(' | ') || 'no window'}`);
        // Minimized, then restored: still inside.
        if (win) {
            win.minimize();
            await sleep(300);
            const stopRestore = allWindowsTracer();
            win.unminimize();
            await sleep(600);
            const restoreBad = (stopRestore().get(win) ?? []).filter(r => !r.ok);
            if (restoreBad.length)
                failures.push(`un-minimize: ${restoreBad[0].drawn}`);
        }
        procs.pop()?.send_signal(9);
        await until(() => !win || !global.display.list_all_windows().includes(win), 5000, 'closed');
    }
    check(`${label}: every app-requested position and size is shown fully inside one work area from its first frame`, failures.length === 0,
        failures.join('; '));
    // The person may still put a window partly off screen.
    const notes = mainWindowOf('org.projectluma.Notes');
    if (notes) {
        global.display.emit('grab-op-begin', notes, Meta.GrabOp.KEYBOARD_MOVING);
        notes.move_frame(true, 1200, 400);
        global.display.emit('grab-op-end', notes, Meta.GrabOp.KEYBOARD_MOVING);
        await sleep(300);
        const f = frameOf(notes);
        check(`${label}: a window the person drags partly off a work area stays there`, f[0] === 1200 && f[1] === 400, `${f}`);
        // A monitor change makes it fit again.
        if (label === 'sweep-tiling-off') {
            plug([]);
            await sleep(1500);
            const g = frameOf(notes);
            const a = Main.layoutManager.getWorkAreaForMonitor(notes.get_monitor());
            check(`${label}: after the monitors change, that window is inside a work area again`, inside(g, a), `${g}`);
            plug(['5120x1440']);
            await sleep(2500);
        } else {
            await personTiles(notes, uw(), {x: 0.25, y: 0, width: 0.5, height: 1});
        }
    }
}

function mainWindowOf(appId) {
    return global.display.list_all_windows().find(w => w.get_gtk_application_id() === appId && !w.get_transient_for()) ?? null;
}

// Every painted frame of every normal window, from the moment it is visible.
function allWindowsTracer() {
    const rows = new Map();
    const id = global.stage.connect('after-paint', () => {
        const areas = Main.layoutManager.monitors.map((_m, i) => Main.layoutManager.getWorkAreaForMonitor(i));
        for (const actor of global.get_window_actors()) {
            const w = actor.get_meta_window();
            if (!w || w.get_window_type() !== Meta.WindowType.NORMAL || !actor.visible || actor.get_paint_opacity() === 0)
                continue;
            const f = w.get_frame_rect();
            const drawn = [f.x, f.y, f.width, f.height];
            const list = rows.get(w) ?? [];
            if (!list.length || list[list.length - 1].drawn.join() !== drawn.join())
                list.push({drawn, ok: areas.some(a => inside(drawn, a))});
            rows.set(w, list);
        }
    });
    return () => {
        global.stage.disconnect(id);
        return rows;
    };
}

export function init() {
    GLib.mkdir_with_parents(OUT, 0o755);
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 6000, () => {
        work().catch(e => check('scenario ran without errors', false, `${e}\n${e.stack}`))
            .finally(() => {
                GLib.file_set_contents(`${OUT}/results.json`, JSON.stringify(results, null, 1));
                log(`DONE ${results.filter(r => r.ok).length}/${results.length} passed`);
                procs.forEach(p => p.send_signal(9));
                global.context.terminate();
            });
        return GLib.SOURCE_REMOVE;
    });
}

export async function run() {
    await new Promise(() => {});
}
