// Dock previews and menus (Shell patch 0136): a window closed from the preview,
// the right-click menu's groups and title, and Open at Login from that menu.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import Shell from 'gi://Shell';

const log = (m, o) => console.log(`[dock94] ${m} ${JSON.stringify(o ?? {})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const rect = a => { const [x, y] = a.get_transformed_position(); const [w, h] = a.get_transformed_size(); return {x, y, w, h}; };
const r1 = v => Math.round(v * 10) / 10;
function dbusCall(name, path, iface, method, params) {
    return new Promise((resolve, reject) => Gio.DBus.session.call(name, path, iface, method, params, null, 0, -1, null,
        (c, r) => { try { resolve(c.call_finish(r)); } catch (e) { reject(e); } }));
}

export default async function ({Main, shot}) {
    const want = Number(GLib.getenv('B_SCALE') || 1);
    if (want !== 1) {
        const [serial, monitors] = (await dbusCall('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
            'org.gnome.Mutter.DisplayConfig', 'GetCurrentState', null)).deepUnpack();
        const [[connector], modes] = monitors[0];
        const current = modes.find(m => m[6]?.['is-current']?.deepUnpack?.()) ?? modes[0];
        await dbusCall('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig', 'org.gnome.Mutter.DisplayConfig',
            'ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
                [serial, 1, [[0, 0, want, 0, true, [[connector, current[0], {}]]]], {}]));
        await sleep(5000);
    }
    const settings = St.Settings.get();
    if (GLib.getenv('B_MOTION') === '1' && !settings.enable_animations)
        settings.uninhibit_animations();
    const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
    const k = Math.max(1, ...global.stage.peek_stage_views().map(v => v.get_scale())) / scale;
    const mon = Main.layoutManager.primaryMonitor;
    const only = (GLib.getenv('B_ONLY') || '').split(',').filter(Boolean);
    const run = async (name, fn) => {
        if (only.length && !only.includes(name))
            return;
        try {
            await fn();
        } catch (e) {
            log('error', {name, error: `${e}`, stack: `${e.stack}`.split('\n').slice(0, 5)});
        }
    };
    const seat = Clutter.get_default_backend().get_default_seat();
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const now = () => GLib.get_monotonic_time();
    const move = async (x, y, ms = 250) => {
        pointer.notify_absolute_motion(now(), x, y);
        await sleep(ms);
    };
    const click = async (actor, button = Clutter.BUTTON_PRIMARY, wait = 900) => {
        const r = rect(actor);
        await move(r.x + r.w / 2, r.y + r.h / 2);
        pointer.notify_button(now(), button, Clutter.ButtonState.PRESSED);
        await sleep(60);
        pointer.notify_button(now(), button, Clutter.ButtonState.RELEASED);
        await sleep(wait);
    };
    const cropAround = async (name, actors) => {
        const rs = actors.filter(Boolean).map(rect);
        const x1 = Math.max(mon.x, Math.min(...rs.map(r => r.x)) - 30 * scale);
        const y1 = Math.max(mon.y, Math.min(...rs.map(r => r.y)) - 30 * scale);
        const x2 = Math.min(mon.x + mon.width, Math.max(...rs.map(r => r.x + r.w)) + 30 * scale);
        const y2 = Math.min(mon.y + mon.height, Math.max(...rs.map(r => r.y + r.h)) + 30 * scale);
        await shot(name, x1 * k, y1 * k, (x2 - x1) * k, (y2 - y1) * k);
    };

    await sleep(2500);
    const appSystem = Shell.AppSystem.get_default();
    const claude = appSystem.lookup_app('org.oracle.Claude.desktop');
    // Three windows of one app.
    for (let i = 0; i < 2; i++) {
        GLib.spawn_async(null, ['python3', '/oracle/live-island/harness/win.py', 'org.oracle.Claude', 'Claude'], null,
            GLib.SpawnFlags.SEARCH_PATH, null);
        await sleep(2500);
    }
    const dash = Main.overview.dash;
    const itemFor = app => dash._box.get_children().find(c => c.child?.app === app);
    log('state', {scale, windows: claude?.get_windows().length, animations: settings.enable_animations});

    await run('preview', async () => {
        const Dock = await import('resource:///org/gnome/shell/ui/lumaDockWindows.js');
        const preview = Dock.getDockPreview();
        const item = itemFor(claude);
        await move(rect(item.child).x + rect(item.child).w / 2, rect(item.child).y + rect(item.child).h / 2, 1500);
        if (!preview.isOpen)
            preview._open(item, item.child);
        await sleep(800);
        const popover = preview._popover;
        const island = Main.shelf._dockIsland;
        if (GLib.getenv('B_DEBUG')) {
            const closeFn = preview.close.bind(preview);
            preview.close = immediate => {
                log('close-called', {immediate, still: preview._stillSince, hover: popover.hover,
                    stack: new Error().stack.split('\n').slice(1, 6)});
                closeFn(immediate);
            };
        }
        const measure = () => {
            const s = rect(popover._surface), i = rect(island), icon = rect(item.child);
            const edge = Main.shelf._edge;
            const gap = {bottom: i.y - (s.y + s.h), top: s.y - (i.y + i.h), left: s.x - (i.x + i.w), right: i.x - (s.x + s.w)}[edge];
            const vertical = edge === 'left' || edge === 'right';
            return {surface: [s.x, s.y, s.w, s.h].map(r1), gapLogical: r1(gap / scale),
                centreOffset: r1(vertical ? (s.y + s.h / 2) - (icon.y + icon.h / 2) : (s.x + s.w / 2) - (icon.x + icon.w / 2)),
                insetLeft: r1((s.x - mon.x) / scale),
                cards: popover.cards.length, transition: !!preview._resizeTimeline,
                opacity: popover.opacity, open: preview.isOpen};
        };
        log('preview-open', measure());
        await cropAround('preview-3-windows', [popover._surface, island]);
        const closeOne = async (label) => {
            const card = popover.cards[popover.cards.length - 1];
            await move(rect(card).x + rect(card).w / 2, rect(card).y + rect(card).h / 2, 500);
            const before = measure();
            await click(card._close, Clutter.BUTTON_PRIMARY, 0);
            const frames = [];
            const t0 = GLib.get_monotonic_time();
            let shotTaken = false;
            for (let i = 0; i < 24; i++) {
                await sleep(20);
                const m = measure();
                frames.push({t: Math.round((GLib.get_monotonic_time() - t0) / 1000), ...m});
                if (!shotTaken && m.transition) {
                    shotTaken = true;
                    await cropAround(`${label}-mid`, [popover._surface, island]);
                }
                if (!m.open)
                    break;
            }
            await sleep(1200);
            log(label, {before, frames, after: measure()});
            if (preview.isOpen)
                await cropAround(`${label}-end`, [popover._surface, island]);
        };
        if (GLib.getenv('B_PAINTS')) {
            // Every painted frame of one resize: surface, its deep shadow and the popover.
            const painted = [];
            const card = popover.cards[popover.cards.length - 1];
            await move(rect(card).x + rect(card).w / 2, rect(card).y + rect(card).h / 2, 500);
            const icon = rect(itemFor(claude).child);
            for (const name of ['holdSize', '_refresh', '_populate', '_finishResize']) {
                const original = preview[name].bind(preview);
                preview[name] = (...args) => {
                    painted.push({t: Math.round(GLib.get_monotonic_time() / 1000), call: name,
                        width: popover._surface.natural_width_set ? popover._surface.natural_width : -1});
                    return original(...args);
                };
            }
            const id = global.stage.connect('after-paint', () => {
                // Allocations are what was drawn; sizes read now may already be the next frame's.
                const p = popover.get_allocation_box(), sa = popover._surface.get_allocation_box();
                const ha = popover._shadow.get_allocation_box();
                const px = p.x1 + popover.translation_x;
                painted.push({t: Math.round(GLib.get_monotonic_time() / 1000),
                    surface: [px + sa.x1, sa.x2 - sa.x1].map(r1), shadow: [px + ha.x1, ha.x2 - ha.x1].map(r1),
                    centre: r1(px + sa.x1 + (sa.x2 - sa.x1) / 2 - (icon.x + icon.w / 2))});
            });
            await click(card._close, Clutter.BUTTON_PRIMARY, 0);
            await sleep(700);
            global.stage.disconnect(id);
            const t0 = painted.find(f => f.surface)?.t ?? 0;
            log('painted-frames', painted.map(f => ({...f, t: f.t - t0})));
            return;
        }
        await closeOne('close-1-of-3');
        await closeOne('close-2-of-3');
        await closeOne('close-last');
        await move(mon.x + mon.width / 2, mon.y + 40, 800);
    });

    await run('menu', async () => {
        const second = appSystem.lookup_app('org.oracle.Second.desktop');
        for (const [label, app] of [['menu-one-window', second], ['menu-claude', claude]]) {
            const item = itemFor(app);
            if (!item)
                continue;
            await click(item.child, Clutter.BUTTON_SECONDARY);
            const menu = item.child._menu;
            const rows = menu._getMenuItems().flatMap(entry => entry._getMenuItems ? entry._getMenuItems() : [entry])
                .flatMap(entry => entry._getMenuItems ? entry._getMenuItems() : [entry])
                .filter(entry => entry.actor?.visible ?? entry.visible)
                .map(entry => ({type: entry.constructor.name, text: entry.label?.text ?? '', cls: entry.style_class}));
            log(label, {title: menu._titleItem.label.text, rows,
                separators: rows.filter(r => r.type.includes('Separator')).length,
                login: menu._login, loginVisible: menu._loginItem.visible, background: menu._backgroundItem.visible,
                box: Object.values(rect(menu.box)).map(r1)});
            await cropAround(label, [menu.box, item.child]);
            if (label === 'menu-one-window' && GLib.getenv('B_TOGGLE') === '1') {
                const before = menu._login?.enabled;
                await click(menu._loginItem, Clutter.BUTTON_PRIMARY, 1500);
                const autostart = GLib.build_filenamev([GLib.get_home_dir(), '.config/autostart', 'org.oracle.Second.desktop']);
                const [ok, contents] = GLib.file_test(autostart, GLib.FileTest.EXISTS)
                    ? GLib.file_get_contents(autostart) : [false, null];
                log('toggle-login', {before, after: menu._login?.enabled, switch: menu._loginItem.state,
                    autostart: ok ? new TextDecoder().decode(contents) : null});
                await cropAround('menu-after-toggle', [menu.box, item.child]);
            }
            menu.close(false);
            await move(mon.x + mon.width / 2, mon.y + 40, 500);
        }
    });
}
