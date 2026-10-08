// Window motion (Shell patch 0143): minimize, restore, a restore that cuts a
// minimize short, open from a dock icon, close; painted geometry per frame,
// frame intervals, and slowed frame strips for review.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';

const log = (m, o) => console.log(`[motion] ${m} ${JSON.stringify(o ?? {})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const r1 = v => Math.round(v * 10) / 10;

export default async function ({Main, shot}) {
    await sleep(3500);
    if (Main.actionMode === 0) {
        const dummy = new St.Widget();
        Main.uiGroup.add_child(dummy);
        Main.popModal(Main.pushModal(dummy));
        dummy.destroy();
    }
    const settings = St.Settings.get();
    const reduced = GLib.getenv('B_ANIM') === 'false';
    if (!reduced && !settings.enable_animations)
        settings.uninhibit_animations();
    const want = Number(GLib.getenv('B_SCALE') || 1);
    if (want !== 1) {
        const Gio = (await import('gi://Gio')).default;
        const call = (method, params) => new Promise((resolve, reject) => Gio.DBus.session.call('org.gnome.Mutter.DisplayConfig',
            '/org/gnome/Mutter/DisplayConfig', 'org.gnome.Mutter.DisplayConfig', method, params, null, 0, -1, null,
            (c, r) => { try { resolve(c.call_finish(r)); } catch (e) { reject(e); } }));
        const [serial, monitors] = (await call('GetCurrentState', null)).deepUnpack();
        const [[connector], modes] = monitors[0];
        const current = modes.find(m => m[6]?.['is-current']?.deepUnpack?.()) ?? modes[0];
        await call('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
            [serial, 1, [[0, 0, want, 0, true, [[connector, current[0], {}]]]], {}]));
        await sleep(5000);
    }
    const mon = Main.layoutManager.primaryMonitor;
    const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
    const k = Math.max(1, ...global.stage.peek_stage_views().map(v => v.get_scale())) / scale;
    const tracker = Shell.WindowTracker.get_default();
    const windowOf = id => global.get_window_actors().map(a => a.meta_window).find(w => tracker.get_window_app(w)?.get_id() === id);
    const results = [];
    const record = (name, pass, evidence) => { results.push(pass); log(pass ? 'PASS' : 'FAIL', {name, ...evidence}); };

    // Paint log: the window's drawn rectangle at every painted frame.
    const paint = actor => {
        const frames = [];
        const t0 = GLib.get_monotonic_time();
        const id = global.stage.connect('after-paint', () => {
            const [x, y] = actor.get_transformed_position();
            const [w, h] = actor.get_transformed_size();
            frames.push({t: Math.round((GLib.get_monotonic_time() - t0) / 100) / 10, x: r1(x), y: r1(y), w: r1(w), h: r1(h), o: actor.opacity, v: actor.visible});
        });
        return () => { global.stage.disconnect(id); return frames; };
    };
    const intervals = frames => {
        const d = frames.slice(1).map((f, i) => f.t - frames[i].t).sort((a, b) => a - b);
        const q = p => d.length ? r1(d[Math.min(d.length - 1, Math.floor(p * d.length))]) : null;
        return {frames: frames.length, p50: q(0.5), p95: q(0.95), max: d.length ? r1(d[d.length - 1]) : null,
            intervals: frames.slice(1).map((f, i) => r1(f.t - frames[i].t))};
    };
    const strip = async (label, run, count = 8, span = 3200) => {
        // GNOME's slow-down makes the motion long enough to photograph evenly.
        settings.slow_down_factor = 10;
        const done = run();
        for (let i = 0; i < count; i++) {
            await sleep(span / count);
            await shot(`${label}-${String(i).padStart(2, '0')}`, mon.x * k, mon.y * k, mon.width * k, mon.height * k);
        }
        await done;
        await sleep(600);
        settings.slow_down_factor = 1;
    };

    let window = windowOf('org.oracle.Claude.desktop');
    const actor = window.get_compositor_private();
    if (GLib.getenv('B_ALONE')) {
        windowOf('org.oracle.Second.desktop')?.delete(global.get_current_time());
        await sleep(1000);
    }
    if (GLib.getenv('B_NOFOCUS'))
        global.display.connect('notify::focus-window', () => log('focus', {t: GLib.get_monotonic_time() / 1000}));
    // Let the scene settle so the first frames measured are the motion's own.
    await sleep(1500);
    const icon = Main.overview.dash.getShelfIconGeometry(window) ?? (() => {
        const item = Main.overview.dash._box.get_children().find(c => c.child?.app === tracker.get_window_app(window));
        const a = item?.child?.icon?.icon;
        if (!a) return null;
        const [x, y] = a.get_transformed_position(); const [width, height] = a.get_transformed_size();
        return {x, y, width, height, fromItem: true};
    })();
    log('state', {scale, reduced, animations: settings.enable_animations, edge: Main.shelf?._edge, icon,
        window: [window.get_frame_rect().x, window.get_frame_rect().y, window.get_frame_rect().width, window.get_frame_rect().height]});

    // Minimize.
    let stop = paint(actor);
    window.minimize();
    await sleep(900);
    let frames = stop();
    const visible = frames.filter(f => f.v && f.o > 0);
    const last = visible[visible.length - 1];
    const end = icon && last ? {dx: r1((last.x + last.w / 2) - (icon.x + icon.width / 2)), dy: r1((last.y + last.h / 2) - (icon.y + icon.height / 2)), w: last.w} : null;
    const shrinking = visible.every((f, i) => i === 0 || f.w <= visible[i - 1].w + 0.5);
    const distance = f => Math.hypot(f.x + f.w / 2 - (icon.x + icon.width / 2), f.y + f.h / 2 - (icon.y + icon.height / 2));
    const closing = visible.every((f, i) => i === 0 || distance(f) <= distance(visible[i - 1]) + 0.5);
    const lastT = frames.length ? frames[frames.length - 1].t : 0;
    // Software rendering here stalls ~150ms once as focus moves to the next
    // window (also with GNOME's own minimize); a stall may cut the last frames.
    record('minimize travels into the dock icon', reduced ? visible.length <= 1 : !!icon && shrinking && closing && visible.length >= 8 && window.minimized && lastT <= 420,
        {timing: intervals(frames), end, visibleFrames: visible.length, minimized: window.minimized, lastT});

    // Restore.
    stop = paint(actor);
    window.unminimize();
    window.activate(global.get_current_time());
    await sleep(900);
    frames = stop();
    const first = frames.find(f => f.v && f.o > 0);
    const settled = frames[frames.length - 1];
    const rect = window.get_buffer_rect();
    record('restore comes back out of the icon to the window', reduced ? true : !!first && first.w < 400 && Math.abs(settled.x - rect.x) <= 1 && Math.abs(settled.w - rect.width) <= 1,
        {timing: intervals(frames), first, settled, rect: [rect.x, rect.y, rect.width, rect.height]});

    // Restore that cuts a minimize short: the size never jumps.
    if (!reduced) {
        stop = paint(actor);
        window.minimize();
        await sleep(140);
        window.unminimize();
        window.activate(global.get_current_time());
        await sleep(900);
        frames = stop().filter(f => f.v);
        let jump = 0;
        for (let i = 1; i < frames.length; i++)
            jump = Math.max(jump, Math.abs(frames[i].w - frames[i - 1].w));
        record('a restore mid-minimize reverses without jumping', jump < rect.width * 0.35 && !window.minimized,
            {largestStep: jump, frames: frames.length, minimized: window.minimized});
    }

    // Open from a dock icon: a pinned app that is not running.
    const third = Main.overview.dash._box.get_children().find(c => c.child?.app?.get_id() === 'org.oracle.Third.desktop');
    if (third) {
        const from = third.child.icon.icon;
        const [ix, iy] = from.get_transformed_position();
        const created = new Promise(resolve => {
            const id = global.display.connect('window-created', (_d, w) => {
                global.display.disconnect(id);
                resolve(w);
            });
        });
        third.child.activate(Clutter.BUTTON_PRIMARY);
        const opened = await created;
        const openedActor = opened.get_compositor_private();
        stop = paint(openedActor);
        await sleep(1200);
        frames = stop().filter(f => f.v && f.o > 0);
        const f0 = frames[0];
        const ow = opened.get_buffer_rect();
        record('an app opened from its dock icon grows out of the icon', reduced ? true : !!f0 && f0.w < ow.width * 0.6 && Math.abs((f0.y + f0.h / 2) - (iy + 18 * scale)) < ow.height * 0.6,
            {timing: intervals(frames), first: f0, icon: [r1(ix), r1(iy)], window: [ow.x, ow.y, ow.width, ow.height]});
        // Close: a quick settle, in place.
        const closing = opened.get_compositor_private();
        const centre = [ow.x + ow.width / 2, ow.y + ow.height / 2];
        stop = paint(closing);
        opened.delete(global.get_current_time());
        await sleep(800);
        frames = stop().filter(f => f.v && f.o > 0);
        const drift = frames.reduce((m, f) => Math.max(m, Math.abs(f.x + f.w / 2 - centre[0]), Math.abs(f.y + f.h / 2 - centre[1])), 0);
        const lasting = frames.length ? frames[frames.length - 1].t - frames[0].t : 0;
        record('close fades and settles in place, quickly', drift <= 2 && lasting <= 260 && frames.every(f => f.w >= ow.width * 0.94 - 1),
            {frames: frames.length, lastingMs: lasting, drift});
    }

    // Recordings.
    if (GLib.getenv('B_STRIP') && !reduced) {
        window = windowOf('org.oracle.Claude.desktop');
        await strip('minimize', async () => { window.minimize(); await sleep(3600); });
        await strip('restore', async () => { window.unminimize(); window.activate(global.get_current_time()); await sleep(3600); });
    }
    log('summary', {passed: results.filter(Boolean).length, total: results.length});
}
