// The Well row and the shelf's hover rhythm (Shell patch 0151): one optical
// glyph size across Luma's own Well items, an extension's indicator and the
// system indicators, and one inset highlight box at rest, on hover and
// pressed, for the Well, the clock, Quick Options and the dock tiles.
//
// W_SCALE applies a monitor scale (1, 1.25, 2). Crops land in ORACLE_OUT as
// well-<state>-<tag>.png, and every measurement is logged as [well] lines.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

const log = (m, o) => console.log(`[well] ${m} ${JSON.stringify(o ?? {})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const r1 = v => Math.round(v * 10) / 10;
const box = actor => {
    const b = actor.get_allocation_box();
    return {x: r1(b.x1), y: r1(b.y1), w: r1(b.get_width()), h: r1(b.get_height())};
};
const radius = actor => {
    try {
        const node = actor.get_theme_node();
        return [0, 1, 2, 3].map(corner => r1(node.get_border_radius(corner)));
    } catch {
        return null;
    }
};
function call(method, params) {
    return new Promise((resolve, reject) => Gio.DBus.session.call('org.gnome.Mutter.DisplayConfig',
        '/org/gnome/Mutter/DisplayConfig', 'org.gnome.Mutter.DisplayConfig', method, params,
        null, 0, -1, null, (c, r) => { try { resolve(c.call_finish(r)); } catch (e) { reject(e); } }));
}
function descend(actor, out = []) {
    out.push(actor);
    actor.get_children?.().forEach(child => descend(child, out));
    return out;
}
// Every glyph drawn in the row, whatever drew it: an St icon, a pixmap the
// application sent, or an XEmbed tray icon cloned into place.
function glyphs(root, label) {
    return descend(root).filter(actor => actor instanceof St.Icon || actor instanceof Clutter.Clone ||
        (actor.content && actor.width > 0)).map(actor => ({
        where: label,
        type: actor.constructor.name,
        classes: actor.style_class ?? '',
        icon: actor instanceof St.Icon ? actor.icon_name ?? (actor.gicon?.to_string?.() ?? null) : null,
        size: actor instanceof St.Icon ? r1(actor.icon_size) : null,
        box: box(actor),
        // How the glyph is drawn: a theme symbolic, the silhouette shader, or
        // the one exception that keeps its own colours.
        ink: actor.get_effects?.().some(e => e.constructor.name === 'LumaWellSilhouette') ? 'silhouette'
            : (actor instanceof St.Icon && (actor.icon_name ?? '').endsWith('-symbolic') ? 'symbolic'
                : (actor.has_style_class_name?.('luma-well-colour-glyph') ? 'colour' : 'inherited')),
    }));
}

export default async function ({Main, shot: rawShot}) {
    // A crop that runs past the stage never completes; keep every crop on it.
    const stageScale = Math.max(1, ...global.stage.peek_stage_views().map(v => v.get_scale()));
    const shot = (name, x, y, w, h) => {
        x = Math.max(0, Math.round(x));
        y = Math.max(0, Math.round(y));
        return rawShot(name, x, y, Math.min(Math.round(w), Math.round(global.stage.width * stageScale) - x),
            Math.min(Math.round(h), Math.round(global.stage.height * stageScale) - y));
    };
    const tag = GLib.getenv('B_TAG');
    const want = Number(GLib.getenv('W_SCALE') || 1);
    const shelf = Main.shelf;
    for (let i = 0; i < 60 && !shelf?._well; i++) await sleep(500);
    if (!St.Settings.get().enable_animations)
        St.Settings.get().uninhibit_animations();
    if (want !== 1) {
        const [serial, monitors] = (await call('GetCurrentState', null)).deepUnpack();
        const mode = monitors[0][1].find(m => m[6]?.['is-current']?.deepUnpack?.()) ?? monitors[0][1][0];
        const scale = mode[5].reduce((a, b) => Math.abs(b - want) < Math.abs(a - want) ? b : a, 1);
        await call('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
            [serial, 1, [[0, 0, scale, 0, true, [[monitors[0][0][0], mode[0], {}]]]], {}]));
        await sleep(5000);
        log('scaled', {asked: want, scale});
    }
    await sleep(3000);

    const well = shelf._well;
    const cluster = shelf._actionsMaterial;
    const clock = cluster?._statusClock ?? descend(cluster).find(a => a.has_style_class_name?.('luma-status-clock'));
    const controls = cluster?._statusControls ?? descend(cluster).find(a => a.has_style_class_name?.('luma-status-controls'));
    const slots = descend(cluster).filter(a => a.has_style_class_name?.('luma-status-slot'));
    const wellItems = descend(well).filter(a => a.has_style_class_name?.('luma-well-item'));
    const extensions = descend(well).filter(a => a.has_style_class_name?.('luma-well-extension'));
    const treatment = shelf._mediaIsland?._content?.style_class?.match(/luma-surface-(\w+)/)?.[1] ?? GLib.getenv('B_THEME');
    const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
    const k = Math.max(1, ...global.stage.peek_stage_views().map(v => v.get_scale())) / scale;

    log('row', {treatment, scale, wellBox: box(well), wellRadius: radius(well),
        items: wellItems.length, extensions: extensions.length, slots: slots.length});
    const targets = [
        ...wellItems.map((a, i) => [`well item ${i}`, a]),
        ...extensions.map((a, i) => [`extension ${i}`, a]),
        ...(clock ? [['clock', clock]] : []),
        ...(controls ? [['quick options', controls]] : []),
    ];
    for (const [name, actor] of targets) {
        const target = actor.has_style_class_name?.('luma-well-extension')
            ? descend(actor).find(a => a.has_style_class_name?.('panel-button')) ?? actor : actor;
        const parent = well.contains(actor) ? well : cluster;
        const outer = box(parent), inner = box(target);
        log('target', {name, box: inner, radius: radius(target),
            insets: {top: r1(inner.y - outer.y), bottom: r1(outer.y + outer.h - (inner.y + inner.h))},
            height: inner.h});
    }
    for (const [label, root] of [['well', well], ['status', cluster]]) {
        for (const glyph of glyphs(root, label))
            log('glyph', glyph);
    }

    const seat = Clutter.get_default_backend().get_default_seat();
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const now = () => GLib.get_monotonic_time();
    const move = async (x, y, ms = 300) => { pointer.notify_absolute_motion(now(), x, y); await sleep(ms); };
    const crop = async name => {
        const group = shelf._group.get_transformed_extents();
        await shot(`well-${name}-${tag}`, (group.get_x() - 14) * k, (group.get_y() - 16) * k,
            (group.get_width() + 28) * k, (group.get_height() + 32) * k);
    };
    const dockTile = descend(shelf._dash).find(a => a.has_style_class_name?.('overview-tile'));

    await move(shelf._group.get_transformed_extents().get_x() - 200, 300, 500);
    await crop('rest');
    const hoverTarget = extensions[0] ?? wellItems[0] ?? clock;
    const centre = actor => {
        const e = actor.get_transformed_extents();
        return [e.get_x() + e.get_width() / 2, e.get_y() + e.get_height() / 2];
    };
    for (const [name, actor] of [['well', hoverTarget], ['clock', clock], ['dock', dockTile]]) {
        if (!actor) continue;
        const [x, y] = centre(actor);
        await move(x, y);
        log(`${name} hover`, {pseudo: actor.pseudo_class ?? '', box: box(actor)});
        await crop(`hover-${name}`);
        pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
        await sleep(220);
        log(`${name} pressed`, {pseudo: actor.pseudo_class ?? ''});
        await crop(`pressed-${name}`);
        pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
        await sleep(400);
        // A press can open a menu; close anything that opened before the next crop.
        Main.panel.statusArea.quickSettings?.menu?.close?.();
        for (const item of wellItems) item._menu?.close?.();
        await move(shelf._group.get_transformed_extents().get_x() - 200, 300, 400);
    }
    log('done', {});
}
