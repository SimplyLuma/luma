// SPDX-License-Identifier: GPL-2.0-or-later
// What an idle desktop costs, inside a headless Luma Shell.
//
// After the session settles, with two still windows open and the pointer
// parked, nothing on screen changes, so nothing should be dispatched or
// painted. Measured over IDLE_MS (default 10 s):
//
//   dispatches/s   stage updates per view (frame clock dispatches)
//   paints/s       views painted
//   wakeups/s      context switches of the Shell's main thread
//   cpu            the Shell's CPU time, every thread
//
// MODE=check  the desktop at rest, then other resting states: the pointer on
//             the shelf, after a notification, after Quick Settings and the
//             overview close, and a window animating on one monitor, which
//             must leave the other monitors at rest (idle.json,
//             scenarios.json, checks.json)
// MODE=idle   the desktop at rest only
//
// Finding what keeps a clock running: HOLD=<scenario label prefix> stays in
// that state for HOLD_MS after measuring it, and DIAG (comma separated) runs
// there:
//   find         hide each piece of top chrome and each window in turn
//   transitions  actors with a transition in flight
//   clips        what each painted frame repaints
//   sources      main-loop sources, laters and redraws queued from JS
//   allocations  actors whose allocation keeps changing
// Each writes <name>.json.
//
// ORACLE_SCALES (e.g. 1,1,1.25) gives the virtual monitors those scales.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Mtk from 'gi://Mtk';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const OUT = GLib.getenv('ORACLE_OUT') ?? '/oracle/out';
const HERE = GLib.getenv('IDLE_HARNESS') ?? '/oracle/t';
const MODE = GLib.getenv('MODE') ?? 'check';
const IDLE_MS = Number(GLib.getenv('IDLE_MS') ?? 10000);
// Ceilings for a desktop where nothing changes. A minute-boundary clock tick
// or a late settle can cost a frame or two in a window; a running clock costs
// the refresh rate.
const MAX_DISPATCHES_PER_VIEW = Number(GLib.getenv('IDLE_MAX_DISPATCHES') ?? 0.5);
const MAX_WAKEUPS = Number(GLib.getenv('IDLE_MAX_WAKEUPS') ?? 15);
const checks = [];

const now = () => GLib.get_monotonic_time();
const log = text => console.log(`[idle-probe] ${text}`);
const wait = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
    r();
    return GLib.SOURCE_REMOVE;
}));

function check(name, ok, detail = null) {
    checks.push({name, ok: Boolean(ok), detail});
    log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` -- ${typeof detail === 'string' ? detail : JSON.stringify(detail)}` : ''}`);
}

function write(name, data) {
    GLib.file_set_contents(`${OUT}/${name}.json`, JSON.stringify(data, null, 2));
}

function spawn(argv, logName) {
    const quoted = argv.map(a => GLib.shell_quote(a)).join(' ');
    GLib.spawn_command_line_async(`sh -c ${GLib.shell_quote(`${quoted} >> ${OUT}/${logName}.log 2>&1`)}`);
}

async function until(what, fn, ms = 20000) {
    const end = now() + ms * 1000;
    while (now() < end) {
        if (fn())
            return true;
        await wait(100);
    }
    log(`timed out waiting for ${what}`);
    return false;
}

const read = path => new TextDecoder().decode(GLib.file_get_contents(path)[1]);

const CLOCK_TICKS = 100;
function cpuMs() {
    const stat = read('/proc/self/stat');
    const fields = stat.slice(stat.lastIndexOf(')') + 2).split(' ');
    return (Number(fields[11]) + Number(fields[12])) * 1000 / CLOCK_TICKS;
}

// The main thread's tid is the process id.
function mainSwitches() {
    const status = read('/proc/thread-self/status');
    const n = k => Number(status.match(new RegExp(`^${k}:\\s+(\\d+)`, 'm'))[1]);
    return n('voluntary_ctxt_switches') + n('nonvoluntary_ctxt_switches');
}

function viewName(view) {
    const r = new Mtk.Rectangle();
    view.get_layout(r);
    return `${r.width}x${r.height}+${r.x}+${r.y}@${view.get_scale()}`;
}

async function measure(label, ms = IDLE_MS) {
    const updates = new Map(), paints = new Map();
    const bump = (map, view) => map.set(view, (map.get(view) ?? 0) + 1);
    const ids = [
        global.stage.connect('before-update', (_s, view) => bump(updates, view)),
        global.stage.connect('after-paint', (_s, view) => bump(paints, view)),
    ];
    const cpu0 = cpuMs(), sw0 = mainSwitches(), t0 = now();
    await wait(ms);
    const seconds = (now() - t0) / 1e6;
    const cpu = cpuMs() - cpu0, sw = mainSwitches() - sw0;
    ids.forEach(id => global.stage.disconnect(id));
    const rate = n => Math.round(10 * n / seconds) / 10;
    const views = global.stage.peek_stage_views().map(v => ({
        view: viewName(v),
        dispatches: rate(updates.get(v) ?? 0),
        paints: rate(paints.get(v) ?? 0),
    }));
    const result = {
        label,
        seconds: Math.round(seconds * 10) / 10,
        cpuPercent: Math.round(1000 * cpu / (seconds * 1000)) / 10,
        wakeups: rate(sw),
        dispatches: Math.round(10 * views.reduce((s, v) => s + v.dispatches, 0)) / 10,
        paints: Math.round(10 * views.reduce((s, v) => s + v.paints, 0)) / 10,
        views,
    };
    log(`measure ${JSON.stringify(result)}`);
    return result;
}

// Monitor scales from ORACLE_SCALES, through the call Settings makes.
async function applyScales() {
    const wanted = (GLib.getenv('ORACLE_SCALES') ?? '').split(',').filter(Boolean).map(Number);
    if (!wanted.length)
        return;
    const call = (method, params) => new Promise((resolve, reject) =>
        Gio.DBus.session.call('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
            'org.gnome.Mutter.DisplayConfig', method, params, null, Gio.DBusCallFlags.NONE, -1, null,
            (bus, res) => {
                try {
                    resolve(bus.call_finish(res).deepUnpack());
                } catch (e) {
                    reject(e);
                }
            }));
    const [serial, monitors] = await call('GetCurrentState', null);
    let x = 0;
    const logical = monitors.map(([[connector], modes], i) => {
        const mode = modes.find(m => m[6]['is-current']?.deepUnpack?.() ?? m[6]['is-current']) ?? modes[0];
        const scale = mode[5].reduce((best, s) =>
            Math.abs(s - (wanted[i] ?? 1)) < Math.abs(best - (wanted[i] ?? 1)) ? s : best, 1);
        const entry = [x, 0, scale, 0, i === 0, [[connector, mode[0], {}]]];
        x += Math.round(mode[1] / scale);
        return entry;
    });
    await call('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
        [serial, 1, logical, {}]));
    await wait(1500);
    log(`views ${global.stage.peek_stage_views().map(viewName).join(' ')}`);
}

function describe(actor) {
    const name = actor.get_name?.() || actor.constructor?.name || 'actor';
    const style = actor.get_style_class_name?.();
    return style ? `${name}.${style.split(' ').join('.')}` : name;
}

// Who adds main-loop sources and laters from JavaScript over two seconds.
async function sources() {
    const counts = new Map();
    const note = kind => {
        const stack = new Error().stack.split('\n').slice(2, 6).join(' | ');
        const key = `${kind} ${stack}`;
        counts.set(key, (counts.get(key) ?? 0) + 1);
    };
    const laters = global.compositor.get_laters();
    const saved = {idle_add: GLib.idle_add, timeout_add: GLib.timeout_add, later: laters.add,
        queue_redraw: Clutter.Actor.prototype.queue_redraw, queue_relayout: Clutter.Actor.prototype.queue_relayout};
    GLib.idle_add = (...a) => (note('idle_add'), saved.idle_add(...a));
    GLib.timeout_add = (...a) => (note('timeout_add'), saved.timeout_add(...a));
    laters.add = (...a) => (note('later'), saved.later.call(laters, ...a));
    Clutter.Actor.prototype.queue_redraw = function (...a) {
        note(`queue_redraw ${describe(this)}`);
        return saved.queue_redraw.call(this, ...a);
    };
    Clutter.Actor.prototype.queue_relayout = function (...a) {
        note(`queue_relayout ${describe(this)}`);
        return saved.queue_relayout.call(this, ...a);
    };
    await saved_wait(2000, saved.timeout_add);
    GLib.idle_add = saved.idle_add;
    GLib.timeout_add = saved.timeout_add;
    laters.add = saved.later;
    Clutter.Actor.prototype.queue_redraw = saved.queue_redraw;
    Clutter.Actor.prototype.queue_relayout = saved.queue_relayout;
    const top = [...counts].sort((a, b) => b[1] - a[1]).slice(0, 15);
    log(`sources ${JSON.stringify(top, null, 1)}`);
    write('sources', top);
}

function saved_wait(ms, timeoutAdd) {
    return new Promise(r => timeoutAdd(GLib.PRIORITY_DEFAULT, ms, () => {
        r();
        return GLib.SOURCE_REMOVE;
    }));
}

// What each painted frame repaints: the redraw clip and what is there.
async function clips() {
    const seen = [];
    const id = global.stage.connect('paint-view', (_s, view, region) => {
        if (seen.length >= 12)
            return;
        const rects = [];
        if (region) {
            for (let i = 0; i < Math.min(4, region.num_rectangles()); i++) {
                const r = region.get_rectangle(i);
                const at = global.stage.get_actor_at_pos(Clutter.PickMode.ALL,
                    r.x + r.width / 2, r.y + r.height / 2);
                rects.push({rect: [r.x, r.y, r.width, r.height], at: at ? describe(at) : null});
            }
        }
        seen.push({view: viewName(view), full: !region, rects});
    });
    await wait(1500);
    global.stage.disconnect(id);
    log(`clips ${JSON.stringify(seen, null, 1)}`);
    write('clips', seen);
}

// Actors with a transition in flight.
function transitions() {
    const names = ['opacity', 'x', 'y', 'width', 'height', 'scale-x', 'scale-y', 'translation-x',
        'translation-y', 'translation-z', 'rotation-angle-z', 'background-color', 'margin-left', 'margin-top',
        'pivot-point', 'z-position'];
    const found = [];
    const walk = actor => {
        for (const n of names) {
            const t = actor.get_transition(n);
            if (t)
                found.push({actor: describe(actor), parent: actor.get_parent() ? describe(actor.get_parent()) : null,
                    property: n, playing: t.is_playing(), duration: t.get_duration(), repeat: t.get_repeat_count(),
                    elapsed: t.get_elapsed_time(), mapped: actor.mapped});
        }
        for (const c of actor.get_children())
            walk(c);
    };
    walk(global.stage);
    log(`transitions ${JSON.stringify(found, null, 1)}`);
    write('transitions', found);
}

// Which actors' allocations keep changing over two seconds.
async function allocations() {
    const seen = new Map();
    const ids = [];
    const walk = actor => {
        ids.push([actor, actor.connect('notify::allocation', a => {
            const e = seen.get(a) ?? {n: 0, boxes: []};
            e.n++;
            const b = a.get_allocation_box();
            e.boxes.push([b.x1, b.y1, b.x2, b.y2].map(v => Math.round(v * 100) / 100).join(','));
            if (e.boxes.length > 4)
                e.boxes.shift();
            seen.set(a, e);
        })]);
        for (const c of actor.get_children())
            walk(c);
    };
    walk(global.stage);
    let workareas = 0;
    const wid = global.display.connect('workareas-changed', () => workareas++);
    await wait(2000);
    ids.forEach(([a, id]) => a.disconnect(id));
    global.display.disconnect(wid);
    const chain = a => {
        const parts = [];
        for (let p = a; p && parts.length < 5; p = p.get_parent())
            parts.push(describe(p));
        return parts.join(' < ');
    };
    const top = [...seen].sort((x, y) => y[1].n - x[1].n).slice(0, 20)
        .map(([a, e]) => ({n: e.n, actor: chain(a), boxes: e.boxes}));
    log(`allocations workareas=${workareas} ${JSON.stringify(top, null, 1)}`);
    write('allocations', {workareas, top});
}

// Hide each candidate for a short window and see what the clock does.
async function find(base) {
    const found = [];
    const candidates = [
        ...Main.layoutManager.uiGroup.get_children().filter(a => a.visible && a !== global.window_group),
        ...global.window_group.get_children().filter(a => a.visible),
    ];
    for (const actor of candidates) {
        actor.hide();
        await wait(500);
        const r = await measure(`without ${describe(actor)}`, 3000);
        actor.show();
        await wait(500);
        found.push({order: found.length, actor: describe(actor), dispatches: r.dispatches, paints: r.paints, wakeups: r.wakeups});
    }
    write('find', {base, found});
}

// Other resting states: each should come back to rest once its own motion
// ends, and a window that animates should cost only its own monitor.
async function scenarios(pointer) {
    const results = [];
    const rest = async (label, settle = 4000) => {
        await wait(settle);
        const r = await measure(label, 8000);
        results.push(r);
        // HOLD=label prefix: stay in that state for HOLD_MS (to attach a
        // profiler); hold.ready is written when it starts.
        const hold = GLib.getenv('HOLD');
        if (hold && label.startsWith(hold)) {
            GLib.file_set_contents(`${OUT}/hold.ready`, `${new Date().toISOString()}\n`);
            const tools = {find: () => find(r), transitions, clips, sources, allocations};
            for (const name of (GLib.getenv('DIAG') ?? '').split(',').filter(Boolean))
                await tools[name]?.();
            await wait(Number(GLib.getenv('HOLD_MS') ?? 60000));
        }
        return r;
    };
    // The pointer resting on the dock.
    const dock = Main.layoutManager.uiGroup.get_children().find(a => a.visible && /shelf|dock/i.test(describe(a)));
    if (dock) {
        const [x, y] = dock.get_transformed_position();
        const [w, h] = dock.get_transformed_size();
        pointer.notify_absolute_motion(now(), x + w / 2, y + h / 2);
    }
    await rest('pointer resting on the shelf');
    pointer.notify_absolute_motion(now(), 700, 500);
    // A notification: its beacon breathes a few times, then rests.
    spawn(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.Notifications',
        '--object-path', '/org/freedesktop/Notifications', '--method', 'org.freedesktop.Notifications.Notify',
        'idle-probe', '0', '', 'Hello', 'A notification at rest', '[]', '{}', '5000'], 'notify');
    await rest('after a notification', 20000);
    // Quick Settings opened and closed.
    Main.panel.statusArea.quickSettings?.menu.open(false);
    await wait(1500);
    Main.panel.statusArea.quickSettings?.menu.close(false);
    await rest('after Quick Settings opened and closed', 1000);
    // The overview opened and closed.
    Main.overview.show();
    await wait(1500);
    Main.overview.hide();
    await rest('after the overview opened and closed', 1500);
    // A window that repaints every frame on the first monitor.
    spawn(['python3', `${HERE}/window.py`, 'org.luma.test.Reel', 'Reel', '--live'], 'windows');
    await until('window Reel', () => global.get_window_actors().some(a => a.meta_window.get_title() === 'Reel'), 30000);
    const reel = global.get_window_actors().find(a => a.meta_window.get_title() === 'Reel').meta_window;
    reel.move_frame(true, 100, 100);
    const live = await rest('a window animating on the first monitor');
    const others = live.views.slice(1);
    check(`a window animating on one monitor leaves the others at rest (${others.map(v => v.dispatches).join(', ')} dispatches/s)`,
        others.every(v => v.dispatches <= MAX_DISPATCHES_PER_VIEW));
    reel.delete(global.get_current_time());
    await rest('after the animating window closed');
    write('scenarios', results);
    for (const r of results.filter(r => r !== live)) {
        check(`${r.label}: at rest (${r.dispatches} dispatches/s, ${r.wakeups} wakeups/s)`,
            r.views.every(v => v.dispatches <= MAX_DISPATCHES_PER_VIEW) && r.wakeups <= MAX_WAKEUPS);
    }
}

async function work() {
    await applyScales();
    for (const [id, title] of [['org.luma.test.Photos', 'Library'], ['org.luma.test.Notes', 'Prairie']]) {
        spawn(['python3', `${HERE}/window.py`, id, title], 'windows');
        await until(`window ${title}`,
            () => global.get_window_actors().some(a => a.meta_window.get_title() === title), 30000);
    }
    // Park the pointer in the middle of the first monitor and let the
    // windows' first frames, the shelf's entrance and any startup work end.
    const seat = Clutter.get_default_backend().get_default_seat();
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    pointer.notify_absolute_motion(now(), 700, 500);
    await wait(Number(GLib.getenv('IDLE_SETTLE') ?? 8000));

    const idle = await measure('idle desktop, two still windows');
    write('idle', idle);
    for (const v of idle.views) {
        check(`${v.view}: the frame clock rests (${v.dispatches} dispatches/s, ${v.paints} paints/s)`,
            v.dispatches <= MAX_DISPATCHES_PER_VIEW);
    }
    check(`the main thread rests (${idle.wakeups} wakeups/s)`, idle.wakeups <= MAX_WAKEUPS);
    if (MODE !== 'idle')
        await scenarios(pointer);
}

let started = false;
export function init() {
    GLib.mkdir_with_parents(OUT, 0o755);
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, Number(GLib.getenv('ORACLE_SETTLE') ?? 9000), () => {
        if (!started) {
            started = true;
            work().catch(e => check('the probe ran to the end', false, `${e}\n${e.stack}`))
                .finally(() => {
                    write('checks', checks);
                    log(`${checks.filter(c => !c.ok).length} failed of ${checks.length}`);
                    global.context.terminate();
                });
        }
        return GLib.SOURCE_REMOVE;
    });
}

export async function run() {
    await new Promise(() => {});
}
