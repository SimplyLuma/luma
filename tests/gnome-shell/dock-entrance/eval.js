// Repro: Nick's home layout and scales, then dump dock icon geometry.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import Shell from 'gi://Shell';
const log = (m, o) => console.log(`[repro] ${m} ${JSON.stringify(o ?? {})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
function call(method, params) {
    return new Promise((resolve, reject) => Gio.DBus.session.call('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
        'org.gnome.Mutter.DisplayConfig', method, params, null, 0, -1, null, (c, r) => { try { resolve(c.call_finish(r)); } catch (e) { reject(e); } }));
}
export default async function ({Main, shot}) {
    const layout = GLib.getenv('R_LAYOUT') || 'nick';
    const [serial, monitors] = (await call('GetCurrentState', null)).deepUnpack();
    const info = monitors.map(([[connector], modes]) => ({connector,
        modes: modes.map(m => ({id: m[0], w: m[1], h: m[2], scales: m[5], current: m[6]?.['is-current']?.deepUnpack?.()}))}));
    log('monitors', info.map(m => ({c: m.connector, modes: m.modes.map(x => `${x.id} ${x.w}x${x.h} ${JSON.stringify(x.scales.map(s => Math.round(s * 1000) / 1000))}`)})));
    const pick = (i, want) => {
        const mode = info[i].modes.find(m => m.current) ?? info[i].modes[0];
        const scale = mode.scales.reduce((a, b) => Math.abs(b - want) < Math.abs(a - want) ? b : a, 1);
        return {connector: info[i].connector, mode: mode.id, w: mode.w, h: mode.h, scale};
    };
    let logical;
    if (layout === 'nick') {
        // ASUS 34" 3440x1440 @1 primary at (1440,1120); ASUS 27" portrait 1440x2560 @1 at (0,0); laptop 2400x1500 @1.25 at (4880,1469)
        const a = pick(0, 1), b = pick(1, 1), c = pick(2, 1.25);
        logical = [[1440, 1120, a.scale, 0, true, [[a.connector, a.mode, {}]]],
            [0, 0, b.scale, 0, false, [[b.connector, b.mode, {}]]],
            [4880, 1469, c.scale, 0, false, [[c.connector, c.mode, {}]]]];
    } else {
        const s = Number(GLib.getenv('R_SCALE') || 1);
        const a = pick(0, s);
        logical = [[0, 0, a.scale, 0, true, [[a.connector, a.mode, {}]]]];
    }
    try {
        await call('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})', [serial, 1, logical, {}]));
    } catch (e) {
        log('apply-error', {e: `${e}`});
    }
    await sleep(6000);
    const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
    log('state', {scale, views: global.stage.peek_stage_views().map(v => v.get_scale()),
        monitors: Main.layoutManager.monitors.map(m => [m.index, m.x, m.y, m.width, m.height, m.geometry_scale]), primary: Main.layoutManager.primaryIndex});
    const dash = Main.overview.dash;
    const items = dash._box.get_children().filter(c => c.visible && c.child);
    const rect = a => { const [x, y] = a.get_transformed_position(); const [w, h] = a.get_transformed_size(); return [x, y, w, h].map(v => Math.round(v * 10) / 10); };
    for (const it of items.slice(0, 6)) {
        const icon = it.child;
        const art = icon.icon?.icon;
        log('item', {app: icon.app?.get_id?.(), item: rect(it), child: rect(icon), iconBin: icon.icon && rect(icon.icon),
            iconSize: icon.icon?.iconSize, art: art && rect(art), artClass: art?.style_class, artType: art?.constructor?.name,
            artIconSize: art?.icon_size, artGicon: art?.gicon?.to_string?.(), resScale: art?.get_resource_scale?.(), fixed: dash.iconSize,
            children: art?.get_children?.().map(c => [c.constructor.name, ...rect(c)])});
    }
    const scales = () => items.map(it => Math.round(it.scale_x * 100) / 100);
    if (GLib.getenv('R_STRESS')) {
        const settings = St.Settings.get();
        if (!settings.enable_animations)
            settings.uninhibit_animations();
        const AppFavorites = await import('resource:///org/gnome/shell/ui/appFavorites.js');
        const mode = GLib.getenv('R_STRESS');
        // Every favourite re-added, as when the installed app list reloads at login.
        const dash2 = Main.overview.dash;
        for (const it of dash2._box.get_children().filter(c => c.child))
            it.destroy();
        dash2._redisplay();
        const fresh = () => dash2._box.get_children().filter(c => c.child);
        log('stress-start', {animations: settings.enable_animations, n: fresh().length, scales: fresh().slice(0, 4).map(i => i.scale_x)});
        if (mode === 'monitors') {
            await sleep(Number(GLib.getenv('R_DELAY') || 40));
            if (layout !== 'nick') {
                // One display: a scale change is what rebuilds its view.
                const cur = logical[0][2];
                const [s0, mons0] = (await call('GetCurrentState', null)).deepUnpack();
                const [[conn0], modes0] = mons0[0];
                const other = modes0[0][5].find(x => Math.abs(x - (cur === 1 ? 1.25 : 1)) < 0.01);
                try {
                    await call('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})', [s0, 1, [[0, 0, other, 0, true, [[conn0, modes0[0][0], {}]]]], {}]));
                } catch (e) { log('apply-other', {e: `${e}`}); }
                await sleep(1500);
            }
            const [serial2] = (await call('GetCurrentState', null)).deepUnpack();
            try {
                await call('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})', [serial2, 1, logical, {}]));
            } catch (e) { log('apply2', {e: `${e}`}); }
        }
        await sleep(3000);
        log('stress-end', {mode, scales: fresh().map(i => Math.round(i.scale_x * 100) / 100), transitions: fresh().filter(i => i.get_transition('scale-x')).length,
            mapped: fresh().filter(i => i.mapped).length});
    }
    const island = Main.shelf?._dockIsland;
    if (island) {
        const r = rect(island);
        const k = Math.max(1, ...global.stage.peek_stage_views().map(v => v.get_scale()));
        await shot('dock', (r[0] - 20) * k, (r[1] - 20) * k, (r[2] + 40) * k, (r[3] + 40) * k);
        log('dock', {island: r, k});
    }
}
