// SPDX-License-Identifier: Apache-2.0
// A freshly launched window takes input: run inside a headless GNOME Shell
// (gnome-shell --headless --virtual-monitor 1920x1200 --automation-script=launch-input.js).
// For GTK4 and libadwaita stand-ins, with and without a remembered place, and
// with window memory forced down its late path (shown, then placed): after the
// launch the window is fully visible, a click lands, and its header bar drags it.
// After Tiling Shell 17.3-1.luma.16, Settings and Notes showed hover but took no
// clicks, Notes could not be moved and Filer and Tide opened invisible.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const OUT = GLib.getenv('WM_OUT') ?? '/wm/out';
const APP = GLib.getenv('WM_APP') ?? '/wm/test/app.py';
const UUID = 'tilingshell@ferrarodomenico.com';
const results = [];
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const log = msg => console.log(`[launchinput] ${msg}`);
function check(name, ok, detail = '') {
    results.push({name, ok: !!ok, detail});
    log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`);
}
async function until(fn, ms = 8000) {
    const end = Date.now() + ms;
    while (Date.now() < end) {
        const v = fn();
        if (v) return v;
        await sleep(50);
    }
    return null;
}
const frameOf = w => { const r = w.get_frame_rect(); return [r.x, r.y, r.width, r.height]; };
let pointer;
const now = () => GLib.get_monotonic_time();

async function spawn(appId, name, extra) {
    const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.NONE});
    launcher.setenv('WAYLAND_DISPLAY', GLib.getenv('WAYLAND_DISPLAY') ?? 'wayland-0', true);
    launcher.setenv('GDK_BACKEND', 'wayland', true);
    launcher.setenv('GSK_RENDERER', 'cairo', true);
    launcher.setenv('NO_AT_BRIDGE', '1', true);
    launcher.unsetenv('DISPLAY');
    const proc = launcher.spawnv(['python3', APP, appId, name, '#1565c0', '--clicks', ...extra]);
    const win = await until(() => global.display.list_all_windows().find(w =>
        w.get_gtk_application_id() === appId && w.get_window_type() === Meta.WindowType.NORMAL), 15000);
    return {proc, win};
}
async function close(proc, appId) {
    proc.send_signal(15);
    await until(() => !global.display.list_all_windows().some(w => w.get_gtk_application_id() === appId), 6000);
    await sleep(300);
}
async function click(x, y) {
    pointer.notify_absolute_motion(now(), x, y);
    await sleep(250);
    pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
    await sleep(60);
    pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
}
async function clickLands(win, appId) {
    const path = GLib.build_filenamev([GLib.get_user_runtime_dir(), `clicks-${appId}`]);
    GLib.unlink(path);
    const r = win.get_frame_rect();
    await click(r.x + r.width / 2, r.y + r.height * 0.6);
    return !!(await until(() => GLib.file_test(path, GLib.FileTest.EXISTS), 3000));
}
async function dragMoves(win) {
    const before = frameOf(win);
    const r = win.get_frame_rect();
    const [x, y] = [r.x + r.width * 0.3, r.y + 18];
    pointer.notify_absolute_motion(now(), x, y);
    await sleep(250);
    pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
    for (let i = 1; i <= 12; i++) {
        await sleep(30);
        pointer.notify_absolute_motion(now(), x + i * 15, y + i * 10);
    }
    await sleep(100);
    pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
    await sleep(600);
    const after = frameOf(win);
    return {moved: after[0] !== before[0] || after[1] !== before[1], before, after};
}
function actorState(win) {
    const a = win.get_compositor_private();
    return a ? {opacity: a.opacity, scale: [a.scale_x, a.scale_y], visible: a.visible} : null;
}
const fullyShown = s => s && s.visible && s.opacity === 255 && s.scale[0] === 1 && s.scale[1] === 1;

async function probe(label, win, appId, mem) {
    await sleep(900);
    const s = actorState(win);
    const data = mem?._windows.get(win);
    log(`${label}: held=${!!data?.held} reveal=${data?.revealedWhy ?? '-'} after=${data?.revealedAfter ?? '-'} actor=${JSON.stringify(s)} frame=${frameOf(win)}`);
    check(`${label}: fully visible`, fullyShown(s), JSON.stringify(s));
    check(`${label}: a click lands`, await clickLands(win, appId));
    // A tiled window's first drag is Tiling Shell's to untile; only a free
    // window is dragged here.
    if (!win.assignedTile) {
        const d = await dragMoves(win);
        check(`${label}: its header bar drags it`, d.moved, `${d.before} -> ${d.after}`);
    }
}

async function work() {
    const seat = Clutter.get_default_backend().get_default_seat();
    pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const ext = await until(() => Main.extensionManager.lookup(UUID)?.stateObj, 10000);
    const mem = await until(() => ext?._lumaWindowMemory, 10000);
    check('Tiling Shell is running with window memory', !!mem);
    if (mem)
        Object.assign(mem.timing, {settle: 300, stable: 800, quiet: 300, dwell: 1200});
    await sleep(1500);
    const Tile = mem ? (await import(`file://${ext.path}/components/layout/Tile.js`)).default : null;
    const cases = [
        {label: 'GTK4', appId: 'org.projectluma.PlainGtk', extra: []},
        {label: 'libadwaita', appId: 'org.projectluma.AdwNotes', extra: ['--adw']},
        {label: 'libadwaita placed late', appId: 'org.projectluma.AdwLate', extra: ['--adw'], late: true},
    ];
    for (const c of cases) {
        // First run: no history.
        let {proc, win} = await spawn(c.appId, c.label, c.extra);
        if (!win) { check(`${c.label}: started`, false); continue; }
        await probe(`${c.label} first launch`, win, c.appId, mem);
        if (mem) {
            // The person tiles it; the memory learns the place.
            mem.touch(win, 'test-window-menu');
            ext._tilingManagers[win.get_monitor()].onTileFromWindowMenu(new Tile({x: 0.5, y: 0, width: 0.5, height: 1, groups: []}), win);
            await sleep(mem.timing.dwell + mem.timing.settle + 800);
        }
        await close(proc, c.appId);
        // Second run: held until in place (or forced to be placed late).
        const saved = mem ? {...mem.timing} : null;
        if (mem && c.late)
            Object.assign(mem.timing, {holdAfterShown: 1, holdMax: 40});
        ({proc, win} = await spawn(c.appId, c.label, c.extra));
        if (mem)
            await until(() => mem._windows.get(win)?.mapped, 8000);
        if (saved)
            Object.assign(mem.timing, saved);
        if (!win) { check(`${c.label}: restarted`, false); continue; }
        await probe(`${c.label} remembered launch`, win, c.appId, mem);
        if (mem && c.late) {
            // The late path itself, with the map animation on: a window shown
            // away from its place and then moved there must end fully visible
            // (Tide opened invisible when the move cut the reveal ease short).
            const target = mem._placementOf(win, mem._monitors());
            const data = mem._windows.get(win);
            const r = win.get_frame_rect();
            win.move_resize_frame(false, r.x - 400, r.y + 100, Math.round(r.width * 0.8), Math.round(r.height * 0.8));
            await sleep(500);
            const actor = win.get_compositor_private();
            actor.opacity = 0;
            data.hold = {decision: {target, why: 'test'}, start: Date.now(), attempts: 0, ids: [], x11: false,
                drawn: true, shown: true, shownBy: 'test', expected: null};
            mem._revealHold(win, data, 'safety timeout', {placeAfter: true});
            await sleep(900);
            const st = actorState(win);
            check(`${c.label}: placed after its reveal and fully visible`, fullyShown(st) && !!win.assignedTile,
                `${JSON.stringify(st)} frame=${frameOf(win)} tiled=${!!win.assignedTile}`);
            check(`${c.label}: placed after its reveal, a click lands`, await clickLands(win, c.appId));
        }
        await close(proc, c.appId);
    }
}

export function init() {
    GLib.mkdir_with_parents(OUT, 0o755);
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 5000, () => {
        work().catch(e => check('scenario ran without errors', false, `${e}\n${e.stack}`))
            .finally(() => {
                GLib.file_set_contents(`${OUT}/launch-input.json`, JSON.stringify(results, null, 1));
                log(`DONE ${results.filter(r => r.ok).length}/${results.length} passed`);
                global.context.terminate();
            });
        return GLib.SOURCE_REMOVE;
    });
}

export async function run() {
    await new Promise(() => {});
}
