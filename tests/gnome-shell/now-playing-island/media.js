// Now-playing island style probe: opacity, paint opacity, style and pseudo
// classes and theme fonts of the media module and its island, against the clock.
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import St from 'gi://St';
function call(method, params) {
    return new Promise((resolve, reject) => Gio.DBus.session.call('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
        'org.gnome.Mutter.DisplayConfig', method, params, null, 0, -1, null, (c, r) => { try { resolve(c.call_finish(r)); } catch (e) { reject(e); } }));
}

const log = (m, o) => console.log(`[media] ${m} ${JSON.stringify(o ?? {})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const rect = a => { const [x, y] = a.get_transformed_position(); const [w, h] = a.get_transformed_size(); return {x, y, w, h}; };

function find(actor, pred) {
    if (pred(actor)) return actor;
    for (const c of actor.get_children()) { const f = find(c, pred); if (f) return f; }
    return null;
}
function describe(a) {
    const d = {type: a.constructor.name, style: a.style_class ?? '', pseudo: a.pseudo_class ?? '', opacity: a.opacity,
        paint: a.get_paint_opacity(), visible: a.visible, name: a.name ?? ''};
    if (a instanceof St.Widget && a.get_stage()) {
        const n = a.get_theme_node();
        d.font = n.get_font().to_string();
        d.color = n.get_foreground_color().to_string();
        d.inlineStyle = a.get_style() ?? '';
    }
    if (a instanceof St.Label) d.text = a.text;
    if (a.get_effects?.().length) d.effects = a.get_effects().map(e => e.constructor.name);
    return d;
}
function walk(a, out = [], depth = 0) {
    out.push({depth, ...describe(a)});
    for (const c of a.get_children()) walk(c, out, depth + 1);
    return out;
}

export default async function ({Main, shot}) {
    const wait = Number(GLib.getenv('M_WAIT') || 6000);
    const want = Number(GLib.getenv('M_SCALE') || 1);
    if (want !== 1) {
        const [serial, monitors] = (await call('GetCurrentState', null)).deepUnpack();
        const mode = monitors[0][1].find(m => m[6]?.['is-current']?.deepUnpack?.()) ?? monitors[0][1][0];
        const scale = mode[5].reduce((a, b) => Math.abs(b - want) < Math.abs(a - want) ? b : a, 1);
        await call('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})', [serial, 1, [[0, 0, scale, 0, true, [[monitors[0][0][0], mode[0], {}]]]], {}]));
        await sleep(4000);
        log('scaled', {scale});
    }
    let module = null;
    for (let i = 0; i < 40 && !module; i++) {
        module = find(Main.layoutManager.uiGroup, a => a.has_style_class_name?.('luma-media-module') && a.visible && a.get_stage());
        if (!module) await sleep(500);
    }
    if (!module) { log('no media module'); return; }
    await sleep(wait);
    {
        let isl = module;
        for (let p = module.get_parent(); p; p = p.get_parent()) if (p.has_style_class_name?.('luma-shelf-island')) { isl = p; break; }
        const samples = [];
        const until = Number(GLib.getenv('M_WATCH') || 0);
        for (let t = 0; t < until; t += 250) {
            samples.push([t, isl.visible, isl.opacity, Math.round(isl.width), isl.get_transition('opacity') ? 1 : 0, isl.get_transition('width') ? 1 : 0]);
            await sleep(250);
        }
        const [, natural] = isl.get_preferred_width(-1);
        log('island', {visible: isl.visible, opacity: isl.opacity, width: isl.width, natural, samples: samples.filter((s, i) => i === 0 || JSON.stringify(s.slice(1)) !== JSON.stringify(samples[i - 1].slice(1)))});
    }
    const isl0 = (() => { for (let p = module.get_parent(); p; p = p.get_parent()) if (p.has_style_class_name?.('luma-shelf-island')) return p; return module; })();
    const snap = label => {
        const [, natural] = isl0.get_preferred_width(-1);
        const op = [];
        for (let a = module; a; a = a.get_parent()) if (a.opacity !== 255) op.push([a.constructor.name, a.style_class, a.opacity]);
        log('step', {label, visible: isl0.visible, opacity: isl0.opacity, width: Math.round(isl0.width), natural: Math.round(natural),
            fixed: isl0.fixed_width_set ?? isl0.min_width_set, paint: module.get_paint_opacity(), notOpaque: op,
            offscreen: isl0.offscreen_redirect, modal: Main.modalCount});
    };
    snap('start');
    for (const step of (GLib.getenv('M_STEPS') || '').split(',').filter(Boolean)) {
        try {
            if (step === 'capture') { Main.screenshotUI.open().catch?.(e => log('err', {e: `${e}`})); await sleep(1500); snap('capture-open'); Main.screenshotUI.close(); await sleep(1200); }
            else if (step === 'capture-fast') { Main.screenshotUI.open(); await sleep(120); Main.screenshotUI.close(); await sleep(1200); }
            else if (step === 'overview') { Main.overview.show(); await sleep(1500); Main.overview.hide(); await sleep(1500); }
            else if (step === 'lock') { Main.screenShield?.lock?.(false); await sleep(2500); snap('locked'); Main.screenShield?.deactivate?.(false); await sleep(2500); }
            else if (step.startsWith('hid')) {
                const shelfActor = Main.layoutManager.uiGroup.get_children().find(a => a.name === 'lumaShelf') ?? module.get_parent();
                let root = isl0; while (root.get_parent() && root.get_parent() !== Main.layoutManager.uiGroup) root = root.get_parent();
                const flip = v => { module.eligible = v; module.emit('eligibility-changed'); };
                const seq = {hidA: [['hide'], ['off'], [100], ['on'], [500], ['show'], [1000]],
                    hidB: [['hide'], ['off'], [400], ['show'], [1000], ['on'], [1000]],
                    hidC: [['off'], [80], ['hide'], [500], ['on'], [60], ['show'], [1000]],
                    hidD: [['on'], [60], ['hide'], [600], ['show'], [1000]],
                    hidE: [['off'], [80], ['on'], [60], ['hide'], [300], ['show'], [1500]]}[step];
                for (const [op] of seq) {
                    if (typeof op === 'number') await sleep(op);
                    else if (op === 'hide') root.hide();
                    else if (op === 'show') root.show();
                    else if (op === 'off') flip(false);
                    else if (op === 'on') flip(true);
                }
            }
            else if (step === 'restyle') { St.ThemeContext.get_for_stage(global.stage).set_theme(St.ThemeContext.get_for_stage(global.stage).get_theme()); await sleep(1000); }
            else if (step === 'scale') { const s = St.ThemeContext.get_for_stage(global.stage); s.scale_factor = 2; await sleep(1500); s.scale_factor = 1; await sleep(1500); }
        } catch (e) { log('step-error', {step, e: `${e}`}); }
        snap(step);
    }
    const chain = [];
    for (let p = module.get_parent(); p && p !== Main.layoutManager.uiGroup; p = p.get_parent()) chain.push(describe(p));
    const clock = find(Main.layoutManager.uiGroup, a => a instanceof St.Label && a.get_stage() && /\d:\d\d/.test(a.text ?? '') && a.visible);
    const title = find(module, a => a.has_style_class_name?.('luma-media-title') && a.visible);
    log('module', {self: describe(module), chain, tree: walk(module), clock: clock ? describe(clock) : null,
        titleRect: title ? rect(title) : null});
    let island = module;
    for (let p = module.get_parent(); p; p = p.get_parent()) if (p.has_style_class_name?.('luma-shelf-island')) { island = p; break; }
    const r = rect(island), k = St.ThemeContext.get_for_stage(global.stage).scale_factor * (Main.layoutManager.primaryMonitor.geometry_scale ?? 1);
    await shot(`media-${GLib.getenv('B_TAG')}`, (r.x - 12) * k, (r.y - 12) * k, (r.w + 24) * k, (r.h + 24) * k);
    const mon = Main.layoutManager.primaryMonitor;
    await shot(`media-strip-${GLib.getenv('B_TAG')}`, mon.x, mon.y + mon.height - 110, mon.width, 110);
}
