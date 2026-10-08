// Headless oracle for Shell .143 (0184): a glass surface must never blur its
// own last output. Hovering a dock icon damages a few pixels inside the dock
// island, so the frame's redraw clip does not cover the island and the copy
// the blur takes of "what is behind it" would be the island itself.
//
// With the fix the effect keeps its last blur for such a frame and asks for a
// repaint (reused > 0, stale unchanged). With
// LUMA_SHELL_BLUR_KEEP_STALE_BACKGROUND=1 it takes the copy, which is the bug
// (stale > 0) -- run it both ways, or the check proves nothing.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
const log = (m, o) => { const line = `[glass] ${m} ${JSON.stringify(o ?? {})}`; console.log(line); printerr(line); };
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const call = (method, params, sig) => new Promise((resolve, reject) => Gio.DBus.session.call('org.gnome.Mutter.DisplayConfig',
    '/org/gnome/Mutter/DisplayConfig', 'org.gnome.Mutter.DisplayConfig', method, params,
    sig ? new GLib.VariantType(sig) : null, 0, -1, null, (c, r) => { try { resolve(c.call_finish(r)); } catch (e) { reject(e); } }));

export default async function ({Main, shot}) {
    const out = GLib.getenv('ORACLE_OUT');
    const scale = Number(GLib.getenv('GLASS_SCALE') || 1);
    if (scale !== 1) {
        const [serial, monitors] = (await call('GetCurrentState', null)).deepUnpack();
        const modes = monitors[0][1];
        const current = (modes.find(m => m[6]?.['is-current']?.deepUnpack?.()) ?? modes[0])[0];
        await call('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
            [serial, 1, [[0, 0, scale, 0, true, [[monitors[0][0][0], current, {}]]]], {}]));
        await sleep(5000);
    }
    const Materials = await import('resource:///org/gnome/shell/ui/lumaSurfaceMaterials.js');
    const treatment = Materials.effectiveTreatment();
    const surfaces = [];
    // The backdrop effect sits on the island's content, not the island.
    const add = (name, island) => {
        const effect = island?._content?.get_effect?.('luma-surface-backdrop');
        if (effect?.get_background_paint_counts)
            surfaces.push({name, actor: island, effect});
    };
    add('dock', Main.shelf?._dockIsland);
    add('clock', Main.shelf?._clockIsland);
    add('quick-options', Main.shelf?._actionsIsland);
    add('well', Main.shelf?._wellIsland);
    const counts = () => Object.fromEntries(surfaces.map(s => {
        const [stale, reused] = s.effect.get_background_paint_counts();
        return [s.name, {stale, reused}];
    }));
    log('setup', {treatment, scale, surfaces: surfaces.map(s => s.name),
        keepStale: !!GLib.getenv('LUMA_SHELL_BLUR_KEEP_STALE_BACKGROUND')});
    if (!surfaces.length) {
        log('no-surfaces');
        return;
    }
    const seat = global.stage.context.get_backend().get_default_seat();
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const move = async (x, y, wait = 400) => {
        pointer.notify_absolute_motion(GLib.get_monotonic_time(), x, y);
        await sleep(wait);
    };
    const dock = surfaces.find(s => s.name === 'dock') ?? surfaces[0];
    const [dx, dy] = dock.actor.get_transformed_position();
    const [dw, dh] = dock.actor.get_transformed_size();
    const park = [dx + dw + 240, dy - 320];
    await move(...park, 2500);
    const before = counts();
    // Hover along the icons: each step damages a few pixels inside the island.
    for (let i = 0; i < 12; i++)
        await move(dx + 12 + (i % 4) * (dw / 5), dy + dh / 2, 320);
    await move(...park, 1200);
    log('hovered');
    const after = counts();
    const delta = Object.fromEntries(surfaces.map(s => [s.name, {
        stale: after[s.name].stale - before[s.name].stale,
        reused: after[s.name].reused - before[s.name].reused,
    }]));
    // Clamped to the screen: a capture rectangle that leaves it hangs.
    const {width: sw, height: sh} = global.stage;
    const sx = Math.max(0, Math.round(dx - 40)), sy = Math.max(0, Math.round(dy - 40));
    await shot('dock-glass', sx, sy,
        Math.min(Math.round(dw + 80), sw - sx), Math.min(Math.round(dh + 80), sh - sy));
    const d = delta[dock.name];
    const keepStale = !!GLib.getenv('LUMA_SHELL_BLUR_KEEP_STALE_BACKGROUND');
    const pass = keepStale ? d.stale > 0 && d.reused === 0 : d.reused > 0 && d.stale === 0;
    log('result', {treatment, delta, pass});
    GLib.file_set_contents(`${out}/glass-hover.json`, JSON.stringify({treatment, before, after, delta, keepStale, pass}, null, 1));
}
