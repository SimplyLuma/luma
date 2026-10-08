// SPDX-License-Identifier: GPL-2.0-or-later
// Blur motion oracle: per-frame compositor update time (before-update to
// after-update, per view) while a blurred window is dragged across both
// monitors, resized, maximized and snapped, and while the overview opens.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';

const log = (m, o) => console.log(`[blur-motion] ${m} ${JSON.stringify(o ?? {})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const nextFrame = () => new Promise(r => {
    const id = global.stage.connect('after-update', () => { global.stage.disconnect(id); r(); });
    global.stage.queue_redraw();
});
function dbusCall(name, path, iface, method, params, sig) {
    return new Promise((resolve, reject) => Gio.DBus.session.call(name, path, iface, method, params,
        sig ? new GLib.VariantType(sig) : null, 0, -1, null, (c, r) => { try { resolve(c.call_finish(r)); } catch (e) { reject(e); } }));
}

async function applyScales(scale) {
    const DC = ['org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig', 'org.gnome.Mutter.DisplayConfig'];
    const [serial, monitors] = (await dbusCall(...DC, 'GetCurrentState', null)).deepUnpack();
    let x = 0;
    const logical = monitors.map(([[connector], modes], i) => {
        const mode = modes.find(m => m[6]?.['is-current']?.deepUnpack?.()) ?? modes[0];
        const s = i === 0 ? scale : 1;
        const entry = [x, 0, s, 0, i === 0, [[connector, mode[0], {}]]];
        x += Math.round(mode[1] / s);
        return entry;
    });
    await dbusCall(...DC, 'ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
        [serial, 1, logical, {}]));
    await sleep(3000);
}

// Frame recorder: update duration per view, in ms.
function record() {
    const start = new Map(), samples = [];
    const ids = [
        global.stage.connect('before-update', (s, view) => start.set(view, GLib.get_monotonic_time())),
        global.stage.connect('after-update', (s, view) => {
            const t0 = start.get(view);
            if (t0 !== undefined)
                samples.push({view: view.get_name?.() ?? '', ms: (GLib.get_monotonic_time() - t0) / 1000});
            start.delete(view);
        }),
    ];
    const began = GLib.get_monotonic_time();
    return () => {
        ids.forEach(id => global.stage.disconnect(id));
        const secs = (GLib.get_monotonic_time() - began) / 1e6;
        const ms = samples.map(s => s.ms).sort((a, b) => a - b);
        const q = p => ms.length ? ms[Math.min(ms.length - 1, Math.floor(p * ms.length))] : 0;
        const r = v => Math.round(v * 100) / 100;
        return {frames: ms.length, secs: r(secs), fps: r(ms.length / secs),
            mean: r(ms.reduce((a, b) => a + b, 0) / Math.max(1, ms.length)),
            p50: r(q(0.5)), p95: r(q(0.95)), p99: r(q(0.99)), max: r(ms.at(-1) ?? 0)};
    };
}

const seat = Clutter.get_default_backend().get_default_seat();
const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
const now = () => GLib.get_monotonic_time();
const moveTo = (x, y) => pointer.notify_absolute_motion(now(), x, y);
const button = pressed => pointer.notify_button(now(), Clutter.BUTTON_PRIMARY,
    pressed ? Clutter.ButtonState.PRESSED : Clutter.ButtonState.RELEASED);
const key = (keyval, pressed) => keyboard.notify_keyval(now(), keyval,
    pressed ? Clutter.KeyState.PRESSED : Clutter.KeyState.RELEASED);

async function drag(from, to, frames) {
    moveTo(from.x, from.y); await nextFrame();
    button(true); await nextFrame();
    for (let i = 1; i <= frames; i++) {
        const t = i / frames;
        moveTo(from.x + (to.x - from.x) * t, from.y + (to.y - from.y) * t);
        await nextFrame();
    }
    button(false); await nextFrame();
}

export default async function ({Main}) {
    await applyScales(Number(GLib.getenv('M_SCALE') || 1.25));
    await sleep(2000);
    const windows = global.get_window_actors().map(a => a.meta_window)
        .filter(w => w.get_wm_class()?.startsWith('org.oracle.Blur'))
        .sort((a, b) => a.get_wm_class().localeCompare(b.get_wm_class()));
    const monitors = Main.layoutManager.monitors.map(m => [m.x, m.y, m.width, m.height, m.geometry_scale]);
    const views = global.stage.peek_stage_views().map(v => {
        const l = v.get_layout(); return [l.x, l.y, l.width, l.height, v.get_scale()];
    });
    log('state', {theme: GLib.getenv('M_THEME'), cache: GLib.getenv('LUMA_MUTTER_BLUR_CACHE'),
        windows: windows.map(w => w.get_wm_class()), monitors, views});
    const results = {theme: GLib.getenv('M_THEME'), cache: GLib.getenv('LUMA_MUTTER_BLUR_CACHE'), monitors, views, actions: {}};
    if (windows.length < 3) {
        log('missing windows', {count: windows.length});
        results.error = 'missing windows';
    } else {
        const [a, b, c] = windows;
        const m0 = Main.layoutManager.monitors[0], m1 = Main.layoutManager.monitors[1] ?? m0;
        // A stack: b maximized on the ultrawide, c behind a on the laptop panel.
        b.move_to_monitor(m1.index); b.maximize(Meta.MaximizeFlags.BOTH);
        c.move_frame(true, m0.x + 200, m0.y + 150);
        a.move_frame(true, m0.x + 400, m0.y + 300);
        a.activate(global.get_current_time());
        await sleep(1500);

        const measure = async (name, fn) => {
            await sleep(700);
            const stop = record();
            await fn();
            results.actions[name] = stop();
            log(name, results.actions[name]);
        };
        const titlebar = w => { const f = w.get_frame_rect(); return {x: f.x + f.width / 2, y: f.y + 18}; };

        await measure('drag-across-monitors', async () => {
            const p = titlebar(a);
            await drag(p, {x: m1.x + m1.width / 2, y: p.y + 60}, 120);
            await drag(titlebar(a), p, 120);
        });
        await measure('resize', async () => {
            const f = a.get_frame_rect();
            for (let i = 0; i <= 90; i++) {
                const d = Math.round(300 * Math.sin(Math.PI * i / 90));
                a.move_resize_frame(true, f.x, f.y, f.width + d, f.height + Math.round(d * 0.6));
                await nextFrame();
            }
        });
        await measure('maximize-unmaximize', async () => {
            a.maximize(Meta.MaximizeFlags.BOTH); await sleep(900);
            a.unmaximize(Meta.MaximizeFlags.BOTH); await sleep(900);
        });
        await measure('tile-snap', async () => {
            a.activate(global.get_current_time());
            key(Clutter.KEY_Super_L, true); key(Clutter.KEY_Left, true);
            key(Clutter.KEY_Left, false); key(Clutter.KEY_Super_L, false);
            await sleep(900);
            key(Clutter.KEY_Super_L, true); key(Clutter.KEY_Down, true);
            key(Clutter.KEY_Down, false); key(Clutter.KEY_Super_L, false);
            await sleep(900);
        });
        await measure('overview', async () => {
            Main.overview.show(); await sleep(1200);
            Main.overview.hide(); await sleep(1200);
        });
    }
    GLib.file_set_contents(`${GLib.getenv('ORACLE_OUT')}/blur-motion.json`, JSON.stringify(results, null, 1));
    log('done', {});
}
