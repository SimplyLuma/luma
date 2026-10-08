// SPDX-License-Identifier: Apache-2.0
// Windows open straight into their tile: a headless GNOME Shell trace.
//   gnome-shell --headless --virtual-monitor 1920x1200 --automation-script=open-trace.js
// For each client, a first run learns a tile (the person tiles it, window
// memory records it) and a second run is traced: every painted stage frame is
// checked against the tile. A frame "at the wrong geometry" is one in which the
// window already has content (its first frame has arrived) and its frame rect
// is not the tile. Frames in which it is also visible (mapped, painted with
// opacity > 0) are what a person sees settle; the others are hidden by the hold
// but still mean the client drew a size it was never going to keep.
//
// Every client must end in its tile, fully visible, with no visible frame
// elsewhere; the strict set (GTK4 on Wayland and X11 clients) must not draw a
// single frame at the wrong geometry, hidden or not.
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const OUT = GLib.getenv('WM_OUT') ?? '/wm/out';
const HERE = GLib.getenv('OPEN_TRACE_DIR') ?? '/wm/test';
const APP = `${HERE}/app.py`;
const UUID = 'tilingshell@ferrarodomenico.com';
const ONLY = (GLib.getenv('OPEN_TRACE_ONLY') ?? '').split(',').filter(Boolean);

const results = [];
const summary = [];
let ext, mem;

const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const log = msg => console.log(`[opentrace] ${msg}`);
function check(name, ok, detail = '') {
    results.push({name, ok: !!ok, detail});
    log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`);
}
async function until(fn, ms = 8000, label = 'condition') {
    const end = Date.now() + ms;
    while (Date.now() < end) {
        const v = fn();
        if (v) return v;
        await sleep(20);
    }
    log(`timeout waiting for ${label}`);
    return null;
}
const frameOf = w => { const r = w.get_frame_rect(); return [r.x, r.y, r.width, r.height]; };
const sameFrame = (a, b) => a.every((v, i) => Math.abs(v - b[i]) <= 2);

function refresh() {
    ext = Main.extensionManager.lookup(UUID)?.stateObj;
    mem = ext?._lumaWindowMemory ?? mem;
    return mem;
}

function spawn(argv, env) {
    const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE});
    launcher.setenv('WAYLAND_DISPLAY', GLib.getenv('WAYLAND_DISPLAY') ?? 'wayland-0', true);
    launcher.setenv('GSK_RENDERER', 'cairo', true);
    launcher.setenv('NO_AT_BRIDGE', '1', true);
    launcher.unsetenv('DISPLAY');
    for (const [k, v] of Object.entries(env))
        launcher.setenv(k, v, true);
    return launcher.spawnv(argv);
}

// Traces one launch from the moment its window is created.
function traceLaunch(match) {
    const t = {win: null, created: 0, firstFrame: 0, shownAt: 0, frames: 0, content: 0, wrong: 0, wrongVisible: 0,
        sizes: [], firstBuffer: null, samples: []};
    let tile = null;
    const ids = [];
    const createdId = global.display.connect('window-created', (_d, win) => {
        if (t.win)
            return;
        // Identity may arrive a moment later on Wayland: adopt on the first frame.
        const adopt = () => {
            if (t.win || !match(win) || win.get_window_type() !== Meta.WindowType.NORMAL || win.get_transient_for())
                return false;
            t.win = win;
            return true;
        };
        const actor = win.get_compositor_private();
        t.created ||= GLib.get_monotonic_time();
        const fid = actor?.connect('first-frame', () => {
            if (!adopt() && t.win !== win)
                return;
            t.firstFrame = GLib.get_monotonic_time();
            t.firstBuffer = frameOf(win);
        });
        const sid = win.connect('size-changed', () => {
            if (t.win !== win && !adopt())
                return;
            const f = frameOf(win);
            const last = t.sizes[t.sizes.length - 1];
            if (!last || last[2] !== f[2] || last[3] !== f[3])
                t.sizes.push(f);
        });
        ids.push([actor, fid], [win, sid]);
    });
    const paintId = global.stage.connect('after-paint', () => {
        const win = t.win;
        if (!win || !tile)
            return;
        const actor = win.get_compositor_private();
        if (!actor)
            return;
        t.frames++;
        // Content: the client has committed a buffer. "first-frame" is emitted
        // after the paint that first showed it, so it cannot be used here.
        const [, bw, bh] = actor.get_texture?.()?.get_preferred_size?.() ?? [false, 0, 0];
        if (!(bw > 0 && bh > 0))
            return;
        t.content++;
        const f = frameOf(win);
        t.buffers ??= [];
        if (!t.buffers.length || t.buffers[t.buffers.length - 1] !== `${bw}x${bh}`)
            t.buffers.push(`${bw}x${bh}`);
        const visible = actor.visible && actor.mapped && actor.get_paint_opacity() > 0;
        if (visible && !t.shownAt)
            t.shownAt = GLib.get_monotonic_time();
        if (!sameFrame(f, tile)) {
            t.wrong++;
            if (visible)
                t.wrongVisible++;
            if (t.samples.length < 6)
                t.samples.push(`${f.join(',')}${visible ? ' visible' : ' hidden'} op=${actor.get_paint_opacity()}`);
        }
    });
    return {
        t,
        setTile(r) { tile = r; },
        stop() {
            global.display.disconnect(createdId);
            global.stage.disconnect(paintId);
            for (const [o, id] of ids) {
                try { o?.disconnect(id); } catch (e) {}
            }
            return t;
        },
    };
}

async function runClient(client, Tile) {
    const manager = ext._tilingManagers[0];
    // First run: learn a tile.
    let proc = spawn(client.argv, client.env);
    let win = await until(() => global.display.list_all_windows().find(w => client.match(w) && w.get_window_type() === Meta.WindowType.NORMAL && !w.get_transient_for()), 30000, `${client.label} first window`);
    if (!win) {
        check(`${client.label}: client started`, false);
        proc.force_exit();
        return;
    }
    await until(() => mem._windows.get(win)?.mapped, 15000, `${client.label} mapped`);
    await sleep(client.slow ? 2500 : 600);
    mem.touch(win, 'test-window-menu');
    if (client.tile === 'max')
        win.maximize(Meta.MaximizeFlags.BOTH);
    else
        manager.onTileFromWindowMenu(new Tile({...client.tile, groups: []}), win);
    await sleep(mem.timing.dwell + mem.timing.settle + 800);
    const tile = frameOf(win);
    log(`${client.label}: learned tile ${tile}`);
    proc.send_signal(15);
    await until(() => !global.display.list_all_windows().includes(win), 8000, `${client.label} closed`);
    await sleep(client.slow ? 1500 : 500);

    // Second run: traced from window creation.
    const trace = traceLaunch(client.match);
    trace.setTile(tile);
    proc = spawn(client.argv, client.env);
    win = await until(() => trace.t.win && trace.t.firstFrame && trace.t.win, 30000, `${client.label} second window`);
    await until(() => trace.t.shownAt, 3000, `${client.label} visible`);
    await sleep(1500);
    const t = trace.stop();
    const shownMs = t.shownAt && t.firstFrame ? Math.round((t.shownAt - t.firstFrame) / 1000) : -1;
    const endOk = !!win && sameFrame(frameOf(win), tile);
    const line = `${client.label}: ${t.content} frames with content, ${t.wrong} at the wrong geometry ` +
        `(${t.wrongVisible} visible); first buffer ${t.firstBuffer}; buffers ${(t.buffers ?? []).join(' > ')}; sizes ${t.sizes.map(s => `${s[2]}x${s[3]}`).join(' > ')}; ` +
        `visible ${shownMs} ms after its first frame; ends in tile ${endOk} (tile ${tile})` +
        `${t.samples.length ? `; e.g. ${t.samples.slice(0, 3).join(' | ')}` : ''}`;
    log(line);
    summary.push({client: client.key, label: client.label, frames: t.content, wrong: t.wrong, wrongVisible: t.wrongVisible,
        firstBuffer: t.firstBuffer, tile, shownMs, endOk, sizes: t.sizes, heldFor: win ? mem._windows.get(win)?.revealedAfter : null,
        revealedWhy: win ? mem._windows.get(win)?.revealedWhy : null});
    check(`${client.label}: painted frames were traced`, t.content > 0, `${t.content} frames`);
    check(`${client.label}: ends in its tile`, endOk, `${win ? frameOf(win) : '-'} vs ${tile}`);
    if (client.strict) {
        check(`${client.label}: no frame at the wrong geometry, hidden or visible`, t.content > 0 && t.wrong === 0,
            `${t.wrong} of ${t.content} (${t.wrongVisible} visible); first buffer ${t.firstBuffer} vs tile ${tile}`);
        // One placement, one size: the window never takes another size on the
        // way to its tile (an X11 frame arriving late used to add a title
        // bar and send it back).
        check(`${client.label}: sized once, at its tile`, t.sizes.length === 1 && sameFrame(t.sizes[0], tile),
            `sizes ${t.sizes.map(f => f.join(',')).join(' > ')}`);
    }
    check(`${client.label}: no visible frame at the wrong geometry`, t.content > 0 && t.wrongVisible === 0 || !client.strict,
        `${t.wrongVisible} visible`);
    const actor = win?.get_compositor_private();
    check(`${client.label}: fully visible after it opened`, !!actor && actor.opacity === 255 && actor.scale_x === 1,
        actor ? `opacity ${actor.opacity} scale ${actor.scale_x}` : 'no actor');
    proc.send_signal(15);
    await until(() => !win || !global.display.list_all_windows().includes(win), 8000, `${client.label} closed`);
    await sleep(500);
}

async function main() {
    await until(() => refresh() && mem._stable, 20000, 'window memory ready');
    if (!mem) {
        check('window memory is running', false);
        return;
    }
    mem.constructor.testHooks = {timing: {settle: 300, stable: 800, quiet: 300, dwell: 800, save: 5000}};
    Object.assign(mem.timing, mem.constructor.testHooks.timing);
    log(`open-in-place hook: ${typeof mem._onInitialConfigure === 'function' ? 'present' : 'absent'}`);
    const Tile = (await import(`file://${ext.path}/components/layout/Tile.js`)).default;
    // Xwayland starts on demand; DISPLAY is set once it is up.
    const display = () => GLib.getenv('DISPLAY') ?? ':0';
    const clients = [
        {key: 'gtk4-wayland', label: 'GTK4 (Wayland)', strict: true,
            argv: ['python3', APP, 'org.projectluma.Reel', 'Reel', '#5e35b1', '--adw'], env: {GDK_BACKEND: 'wayland'},
            match: w => w.get_gtk_application_id() === 'org.projectluma.Reel', tile: {x: 0.5, y: 0, width: 0.5, height: 1}},
        // Takes 400 ms to lay out at any size but its first, like a heavy app
        // relayouting: shown by the hold's safety timeout at the wrong size
        // unless its first size is already the tile.
        {key: 'gtk4-wayland-slow', label: 'GTK4 (Wayland), slow to lay out again', strict: true,
            argv: ['python3', APP, 'org.projectluma.Tide', 'Tide', '#2e7d32', '--adw', '--slow-relayout', '400'], env: {GDK_BACKEND: 'wayland'},
            match: w => w.get_gtk_application_id() === 'org.projectluma.Tide', tile: {x: 0, y: 0, width: 0.5, height: 0.5}},
        {key: 'gtk4-wayland-max', label: 'GTK4 (Wayland), maximized', strict: true,
            argv: ['python3', APP, 'org.projectluma.Write', 'Write', '#6d4c41', '--adw'], env: {GDK_BACKEND: 'wayland'},
            match: w => w.get_gtk_application_id() === 'org.projectluma.Write', tile: 'max'},
        {key: 'gtk4-xwayland', label: 'GTK4 (XWayland)', strict: true,
            argv: ['python3', APP, 'org.projectluma.XNotes', 'XNotes', '#00838f'], env: {GDK_BACKEND: 'x11', DISPLAY: display()},
            match: w => w.get_client_type() === Meta.WindowClientType.X11 && /xnotes/i.test(`${w.get_gtk_application_id()} ${w.get_wm_class()}`),
            tile: {x: 0, y: 0, width: 0.5, height: 1}},
        {key: 'x11-plain', label: 'Plain X11 client (server-side frame)', strict: true,
            argv: ['python3', `${HERE}/xclient.py`, 'XPlain', '120', '90', '900', '640'], env: {DISPLAY: display()},
            match: w => w.get_client_type() === Meta.WindowClientType.X11 && w.get_wm_class() === 'XPlain',
            tile: {x: 0.5, y: 0.5, width: 0.5, height: 0.5}},
        {key: 'electron-wayland', label: 'Chromium/Electron (Wayland)', slow: true,
            argv: ['chromium-browser', '--no-sandbox', '--ozone-platform=wayland', '--user-data-dir=/tmp/ot-chromium-w', '--no-first-run',
                '--disable-gpu', '--class=org.projectluma.ChromiumApp', '--app=data:text/html,<body style="background:%23d97757"><h1>App</h1>'],
            env: {}, match: w => w.get_client_type() === Meta.WindowClientType.WAYLAND && /chrom/i.test(`${w.get_wm_class()} ${w.get_gtk_application_id()} ${w.get_sandboxed_app_id()}`),
            tile: {x: 0, y: 0, width: 0.5, height: 1}},
        {key: 'electron-x11', label: 'Chromium/Electron (XWayland)', slow: true,
            argv: ['chromium-browser', '--no-sandbox', '--ozone-platform=x11', '--user-data-dir=/tmp/ot-chromium-x', '--no-first-run',
                '--disable-gpu', '--class=org.projectluma.ChromiumX', '--app=data:text/html,<body style="background:%23d97757"><h1>App</h1>'],
            env: {DISPLAY: display()}, match: w => w.get_client_type() === Meta.WindowClientType.X11 && /chrom/i.test(`${w.get_wm_class()} ${w.get_wm_class_instance()}`),
            tile: {x: 0.5, y: 0, width: 0.5, height: 1}},
    ];
    for (const client of clients) {
        if (ONLY.length && !ONLY.includes(client.key))
            continue;
        if (client.env.DISPLAY !== undefined)
            client.env.DISPLAY = display();
        try {
            await runClient(client, Tile);
        } catch (e) {
            check(`${client.label}: ran`, false, `${e}\n${e.stack}`);
        }
    }
}

function finish() {
    const failed = results.filter(r => !r.ok);
    try {
        GLib.file_set_contents(`${OUT}/open-trace.json`, JSON.stringify({summary, results}, null, 1));
    } catch (e) {
        log(`could not write results: ${e}`);
    }
    // Nothing checked is a failure, never a pass.
    if (!results.length)
        log('FAIL nothing was checked');
    log(`DONE ${results.length - failed.length}/${results.length} passed`);
    global.context.terminate();
}

export function init() {
    GLib.mkdir_with_parents(OUT, 0o755);
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 6000, () => {
        main().catch(e => check('trace ran', false, `${e}\n${e.stack}`)).finally(finish);
        return GLib.SOURCE_REMOVE;
    });
}

export async function run() {
    await new Promise(() => {});
}
