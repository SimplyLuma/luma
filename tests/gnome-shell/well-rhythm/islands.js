// Every live island type at rest, hovered and pressed (Shell patch 0151).
// A single-action island (a notification on the beacon, a Live Extension)
// washes edge to edge at its own radius; its buttons highlight on top; the
// now-playing island never washes, only its controls do. B_LIVE picks the
// Live Extension (calendar: one action; call-app: one action with buttons),
// B_CHROME adds a player, and a Messages-style notification feeds the beacon.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';

const log = (m, o) => console.log(`[islands] ${m} ${JSON.stringify(o ?? {})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const descend = (actor, out = []) => { out.push(actor); actor.get_children?.().forEach(c => descend(c, out)); return out; };

function call(method, params) {
    return new Promise((resolve, reject) => Gio.DBus.session.call('org.gnome.Mutter.DisplayConfig',
        '/org/gnome/Mutter/DisplayConfig', 'org.gnome.Mutter.DisplayConfig', method, params,
        null, 0, -1, null, (c, r) => { try { resolve(c.call_finish(r)); } catch (e) { reject(e); } }));
}

function paint(actor) {
    if (!(actor instanceof St.Widget) || !actor.get_stage())
        return null;
    const node = actor.get_theme_node();
    const out = {};
    const bg = node.get_background_color();
    if (bg.alpha > 0 && !actor.has_style_class_name('luma-shelf-material'))
        out.background = bg.to_string();
    const shadow = node.get_box_shadow();
    if (shadow)
        out.boxShadow = `${shadow.inset ? 'inset ' : ''}${shadow.spread} ${shadow.color.to_string()}`;
    return Object.keys(out).length ? {classes: actor.style_class ?? '', pseudo: actor.pseudo_class ?? '', ...out} : null;
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
    const shelf = Main.shelf;
    for (let i = 0; i < 60 && !shelf?._well; i++) await sleep(500);
    if (!St.Settings.get().enable_animations)
        St.Settings.get().uninhibit_animations();
    const want = Number(GLib.getenv('W_SCALE') || 1);
    if (want !== 1) {
        const [serial, monitors] = (await call('GetCurrentState', null)).deepUnpack();
        const mode = monitors[0][1].find(m => m[6]?.['is-current']?.deepUnpack?.()) ?? monitors[0][1][0];
        const scale = mode[5].reduce((a, b) => Math.abs(b - want) < Math.abs(a - want) ? b : a, 1);
        await call('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
            [serial, 1, [[0, 0, scale, 0, true, [[monitors[0][0][0], mode[0], {}]]]], {}]));
        await sleep(5000);
    }
    // A Messages-style notification, so the beacon island shows.
    try {
        const source = new MessageTray.Source({title: 'Messages', iconName: 'mail-message-new-symbolic'});
        Main.messageTray.add(source);
        source.addNotification(new MessageTray.Notification({source, title: 'Charles in charge', body: 'Messages'}));
    } catch (e) {
        log('notification', {error: `${e}`});
    }
    await sleep(5000);
    const seat = Clutter.get_default_backend().get_default_seat();
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const now = () => GLib.get_monotonic_time();
    const k = Math.max(1, ...global.stage.peek_stage_views().map(v => v.get_scale())) / St.ThemeContext.get_for_stage(global.stage).scale_factor;
    const centre = actor => { const e = actor.get_transformed_extents(); return [e.get_x() + e.get_width() / 2, e.get_y() + e.get_height() / 2]; };
    const move = async (x, y, ms = 400) => { pointer.notify_absolute_motion(now(), x, y); await sleep(ms); };
    const results = [];
    const record = (name, pass, evidence) => { results.push(pass); log(pass ? 'PASS' : 'FAIL', {name, ...evidence}); };
    const islands = [
        ['beacon', shelf._beaconIsland, shelf._beacon, 'single'],
        ['live', shelf._liveIsland, Main.panel.statusArea.liveExtensions, 'single'],
        ['media', shelf._mediaIsland, shelf._media?._identity, 'controls'],
    ];
    for (const [name, island, body, kind] of islands) {
        if (!island?.visible || !body?.visible) {
            log('skipped', {name, reason: 'not shown'});
            continue;
        }
        const material = island._content;
        const crop = async state => {
            const e = island.get_transformed_extents();
            await shot(`island-${name}-${state}-${tag}`, (e.get_x() - 12) * k, (e.get_y() - 14) * k, (e.get_width() + 24) * k, (e.get_height() + 28) * k);
        };
        await move(20, 20);
        await crop('rest');
        const rest = paint(material);
        const [x, y] = centre(body);
        await move(x, y);
        await crop('hover');
        const hover = {material: paint(material), body: paint(body)};
        pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
        await sleep(200);
        await crop('pressed');
        const pressed = {material: paint(material), body: paint(body)};
        pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
        await sleep(500);
        // Anything the press opened (the tray, an application) is closed again.
        Main.messageTray?.close?.();
        global.stage.set_key_focus(null);
        log('states', {name, kind, rest, hover, pressed});
        if (kind === 'single') {
            record(`${name}: the whole island washes on hover and press, the body paints nothing`,
                !rest?.boxShadow && /inset 200/.test(hover.material?.boxShadow ?? '') &&
                /inset 200/.test(pressed.material?.boxShadow ?? '') && !hover.body?.background && !pressed.body?.background,
                {rest, hover, pressed});
        } else {
            record(`${name}: the island never washes`, !hover.material?.boxShadow && !pressed.material?.boxShadow, {hover, pressed});
        }
        // A button inside a single-action island highlights on top of the wash.
        const button = descend(island).find(a => a !== body && a instanceof St.Button && a.visible &&
            body.contains?.(a));
        if (kind === 'single' && button) {
            const [bx, by] = centre(button);
            await move(bx, by);
            await crop('hover-button');
            const both = {material: paint(material), button: paint(button)};
            log('button over wash', {name, both});
            record(`${name}: a button hovers on top of the island's wash`,
                /inset 200/.test(both.material?.boxShadow ?? '') && Boolean(both.button?.background), {both});
        }
    }
    log('summary', {passed: results.filter(Boolean).length, total: results.length});
}
