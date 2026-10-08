// Beam as a slider (Shell patch 0140): press to set, drag, scroll, mute from
// the glyph, the hide timer held while the pointer is on Beam, focus kept, and
// Quick Options showing the same value. B_LAYOUT=mixed puts a 1.25x display beside a 1x one.
import Atk from 'gi://Atk';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

const log = (m, o) => console.log(`[slider] ${m} ${JSON.stringify(o ?? {})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const rect = a => { const [x, y] = a.get_transformed_position(); const [w, h] = a.get_transformed_size(); return {x, y, w, h}; };
const r2 = v => Math.round(v * 100) / 100;
function call(method, params) {
    return new Promise((resolve, reject) => Gio.DBus.session.call('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
        'org.gnome.Mutter.DisplayConfig', method, params, null, 0, -1, null, (c, r) => { try { resolve(c.call_finish(r)); } catch (e) { reject(e); } }));
}

export default async function ({Main, shot}) {
    const layout = GLib.getenv('B_LAYOUT') || '';
    const want = Number(GLib.getenv('B_SCALE') || 1);
    if (layout === 'mixed' || want !== 1) {
        const [serial, monitors] = (await call('GetCurrentState', null)).deepUnpack();
        const pick = (i, target) => {
            const mode = monitors[i][1].find(m => m[6]?.['is-current']?.deepUnpack?.()) ?? monitors[i][1][0];
            return [monitors[i][0][0], mode[0], mode[5].reduce((a, b) => Math.abs(b - target) < Math.abs(a - target) ? b : a, 1)];
        };
        const logical = layout === 'mixed'
            ? (() => { const a = pick(0, 1), b = pick(1, 1.25); return [[0, 0, a[2], 0, true, [[a[0], a[1], {}]]], [3440, 0, b[2], 0, false, [[b[0], b[1], {}]]]]; })()
            : (() => { const a = pick(0, want); return [[0, 0, a[2], 0, true, [[a[0], a[1], {}]]]]; })();
        await call('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})', [serial, 1, logical, {}]));
        await sleep(5000);
    }
    await sleep(2500);
    if (Main.actionMode === 0) {
        const dummy = new St.Widget();
        Main.uiGroup.add_child(dummy);
        Main.popModal(Main.pushModal(dummy));
        dummy.destroy();
    }
    const settings = St.Settings.get();
    const k = Math.max(1, ...global.stage.peek_stage_views().map(v => v.get_scale())) / St.ThemeContext.get_for_stage(global.stage).scale_factor;
    const quick = Main.panel.statusArea.quickSettings;
    const output = quick._volumeOutput?._output;
    const stream = () => output?.stream;
    const norm = () => quick._volumeOutput._control.get_vol_max_norm();
    const osd = Main.osdWindowManager;
    const seat = Clutter.get_default_backend().get_default_seat();
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const now = () => GLib.get_monotonic_time();
    const move = async (x, y, ms = 120) => { pointer.notify_absolute_motion(now(), x, y); await sleep(ms); };
    const press = async () => { pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED); await sleep(60); };
    const release = async () => { pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED); await sleep(120); };
    const results = [];
    const record = (name, pass, evidence) => { results.push({name, pass}); log(pass ? 'PASS' : 'FAIL', {name, ...evidence}); };

    log('state', {scales: Main.layoutManager.monitors.map(m => m.geometry_scale), stream: stream()?.get_name?.() ?? null,
        brightness: Main.brightnessManager?.globalScale ? 'yes' : 'no', animations: settings.enable_animations});
    const window = global.display.focus_window;
    const focusBefore = window?.get_title() ?? null;

    for (const index of Main.layoutManager.monitors.map((_m, i) => i)) {
        const beam = osd._osdWindows[index];
        const tag = `monitor${index}@${Main.layoutManager.monitors[index].geometry_scale}`;
        stream().volume = Math.round(0.4 * norm());
        stream().push_volume();
        await sleep(300);
        output.showOSD();
        await sleep(500);
        const cap = rect(beam._capsule);
        const at = f => [cap.x + cap.w * f, cap.y + cap.h / 2];
        // Press to jump.
        await move(...at(0.25));
        await press();
        const afterPress = {stream: r2(stream().volume / norm()), quick: r2(output.slider.value), beam: beam._value.text};
        // Drag.
        for (const f of [0.35, 0.5, 0.65, 0.75])
            await move(...at(f), 40);
        await release();
        const afterDrag = {stream: r2(stream().volume / norm()), quick: r2(output.slider.value), beam: beam._value.text};
        record(`${tag} press jumps to the point, drag follows`, Math.abs(afterPress.stream - 0.25) <= 0.02 &&
            Math.abs(afterDrag.stream - 0.75) <= 0.02 && afterDrag.quick === afterDrag.stream && afterDrag.beam === `${Math.round(afterDrag.stream * 100)}%`,
            {afterPress, afterDrag});
        await shot(`${tag}-after-drag`, (cap.x - 20) * k, (cap.y - 20) * k, (cap.w + 40) * k, (cap.h + 90) * k);
        // Scroll.
        const seen = [];
        const probe = beam._capsule.connect('captured-event', (_a, event) => {
            if (event.type() === Clutter.EventType.SCROLL)
                seen.push([event.get_scroll_direction(), event.get_flags(), event.get_scroll_delta?.()]);
            return Clutter.EVENT_PROPAGATE;
        });
        for (let i = 0; i < 3; i++) {
            pointer.notify_discrete_scroll(now(), Clutter.ScrollDirection.UP, Clutter.ScrollSource.WHEEL);
            await sleep(120);
        }
        beam._capsule.disconnect(probe);
        const afterScroll = {stream: r2(stream().volume / norm()), beam: beam._value.text, events: seen};
        record(`${tag} scrolling steps the level`, Math.abs(afterScroll.stream - (afterDrag.stream + 0.06)) <= 0.011, {afterScroll});
        // Held while hovered.
        await sleep(2600);
        const heldVisible = beam.visible && beam.opacity === 255;
        await move(cap.x + cap.w / 2, cap.y - 300, 900);
        const shortlyAfterLeaving = beam.visible;
        await sleep(1200);
        const afterTimeout = beam.visible;
        record(`${tag} hide timer pauses on hover and resumes after leaving`, heldVisible && shortlyAfterLeaving && !afterTimeout,
            {heldVisible, shortlyAfterLeaving, afterTimeout});
        // Mute from the glyph.
        output.showOSD();
        await sleep(400);
        const glyph = rect(beam._glyph);
        await move(glyph.x + glyph.w / 2, glyph.y + glyph.h / 2);
        await press();
        await release();
        await sleep(200);
        const muted = {muted: stream().is_muted, glyph: beam._glyph._glyph, beam: beam._value.text, quick: r2(output.slider.value)};
        await press();
        await release();
        await sleep(200);
        const unmuted = {muted: stream().is_muted, glyph: beam._glyph._glyph, beam: beam._value.text};
        record(`${tag} the glyph toggles mute`, muted.muted && muted.glyph === 'muted' && !unmuted.muted && unmuted.glyph !== 'muted',
            {muted, unmuted});
        await move(cap.x + cap.w / 2, cap.y - 300, 200);
        const acc = beam._capsule.get_accessible();
        let value = null;
        try {
            const current = Atk.Value.prototype.get_current_value.call(acc);
            value = typeof current === 'number' ? current : current?.valueOf?.() ?? null;
        } catch (e) {
            value = `${e}`;
        }
        record(`${tag} exposed as a slider with its value`, acc.get_role() === Atk.Role.SLIDER && typeof value === 'number' &&
            Math.abs(value - Math.round(output.slider.value * 100)) <= 1, {role: acc.get_role(), slider: Atk.Role.SLIDER, value, name: acc.get_name(),
            focusable: beam._capsule.can_focus});
        await sleep(1800);
    }
    // Over-amplification: the stream's own limit.
    const sound = new Gio.Settings({schema_id: 'org.gnome.desktop.sound'});
    sound.set_boolean('allow-volume-above-100-percent', true);
    await sleep(500);
    stream().volume = norm();
    stream().push_volume();
    output.showOSD();
    await sleep(400);
    const beam = osd._osdWindows[0];
    const cap = rect(beam._capsule);
    await move(cap.x + cap.w / 2, cap.y + cap.h / 2);
    for (let i = 0; i < 40; i++) {
        pointer.notify_discrete_scroll(now(), Clutter.ScrollDirection.UP, Clutter.ScrollSource.WHEEL);
        await sleep(30);
    }
    await sleep(300);
    const amplified = {stream: r2(stream().volume / norm()), max: r2(output.slider.maximum_value), beam: beam._value.text, fill: beam._fill.value};
    record('over-amplification stops at the stream limit and shows the real value', amplified.stream > 1 &&
        amplified.stream <= amplified.max + 0.001 && amplified.fill === 1 && amplified.beam === `${Math.round(amplified.stream * 100)}%`, {amplified});
    await shot('amplified', (cap.x - 20) * k, (cap.y - 20) * k, (cap.w + 40) * k, (cap.h + 90) * k);
    sound.set_boolean('allow-volume-above-100-percent', false);
    await move(cap.x + cap.w / 2, cap.y - 300, 200);
    record('the active window keeps focus', (global.display.focus_window?.get_title() ?? null) === focusBefore,
        {before: focusBefore, after: global.display.focus_window?.get_title() ?? null});
    log('summary', {passed: results.filter(r => r.pass).length, total: results.length});
}
