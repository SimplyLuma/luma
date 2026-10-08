// Beam / shelf surface oracle: opens every shelf surface, measures the gap
// from the painted island edge to the surface's painted edge, and crops.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import Shell from 'gi://Shell';

const log = (m, o) => console.log(`[beam] ${m} ${JSON.stringify(o ?? {})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
function dbusCall(name, path, iface, method, params, sig) {
    return new Promise((resolve, reject) => Gio.DBus.session.call(name, path, iface, method, params,
        sig ? new GLib.VariantType(sig) : null, 0, -1, null, (c, r) => { try { resolve(c.call_finish(r)); } catch (e) { reject(e); } }));
}
const rect = a => { const [x, y] = a.get_transformed_position(); const [w, h] = a.get_transformed_size(); return {x, y, w, h}; };
const r2 = v => Math.round(v * 100) / 100;

function islandOf(actor) {
    for (let p = actor?.get_parent?.(); p; p = p.get_parent()) {
        if (!p.has_style_class_name?.('luma-shelf-island'))
            continue;
        if (p.has_style_class_name('luma-shelf-root'))
            return null;
        for (let root = p.get_parent(); root; root = root.get_parent())
            if (root.has_style_class_name?.('luma-shelf-root'))
                return root.has_style_class_name('luma-shelf-connected') ? root : p;
        return p;
    }
    return null;
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
    await sleep(3000);
    const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
    const k = Math.max(1, ...global.stage.peek_stage_views().map(v => v.get_scale())) / scale;
    const edge = Main.shelf?._edge;
    const mon = Main.layoutManager.primaryMonitor;
    const only = (GLib.getenv('B_ONLY') || '').split(',').filter(Boolean);
    const results = [];
    log('state', {scale, k, edge, mon: [mon.x, mon.y, mon.width, mon.height],
        running: Shell.AppSystem.get_default().get_running().map(a => a.get_id())});

    const measure = async (name, source, painted, extra = {}) => {
        const island = islandOf(source);
        const s = rect(painted);
        const res = {name, extra};
        if (island) {
            const i = rect(island);
            const gap = {bottom: i.y - (s.y + s.h), top: s.y - (i.y + i.h),
                left: s.x - (i.x + i.w), right: i.x - (s.x + s.w)}[edge];
            const vertical = edge === 'left' || edge === 'right';
            const a = rect(source);
            const along = vertical
                ? {surfaceCentre: s.y + s.h / 2, controlCentre: a.y + a.h / 2, surfaceStart: s.y, surfaceEnd: s.y + s.h, islandStart: i.y, islandEnd: i.y + i.h, monStart: mon.y, monEnd: mon.y + mon.height}
                : {surfaceCentre: s.x + s.w / 2, controlCentre: a.x + a.w / 2, surfaceStart: s.x, surfaceEnd: s.x + s.w, islandStart: i.x, islandEnd: i.x + i.w, monStart: mon.x, monEnd: mon.x + mon.width};
            const insetStart = (along.surfaceStart - along.monStart) / scale;
            const insetEnd = (along.monEnd - along.surfaceEnd) / scale;
            const centred = Math.abs(along.surfaceCentre - along.controlCentre) <= 1;
            const alignedStart = Math.abs(along.surfaceStart - along.islandStart) <= 1;
            const alignedEnd = Math.abs(along.surfaceEnd - along.islandEnd) <= 1;
            Object.assign(res, {island: island.style_class.split(' ').find(c => c !== 'luma-shelf-island'),
                islandRect: [i.x, i.y, i.w, i.h].map(r2), surface: [s.x, s.y, s.w, s.h].map(r2),
                control: [a.x, a.y, a.w, a.h].map(r2), gapStage: r2(gap), gapDevicePx: r2(gap * k * scale), gapLogical: r2(gap / scale), viewScale: k * scale,
                centred, alignedStart, alignedEnd, insetStart: r2(insetStart), insetEnd: r2(insetEnd),
                insideInset: insetStart >= 14 - 0.5 && insetEnd >= 14 - 0.5,
                pass: Math.abs(gap / scale - 8) <= 0.5 / scale + 0.01 && (centred || alignedStart || alignedEnd) &&
                    insetStart >= 13.5 && insetEnd >= 13.5});
        } else {
            Object.assign(res, {island: null, surface: [s.x, s.y, s.w, s.h].map(r2)});
        }
        results.push(res);
        log('measure', res);
        const i = island ? rect(island) : s;
        const x1 = Math.max(mon.x, Math.min(i.x, s.x) - 40 * scale), y1 = Math.max(mon.y, Math.min(i.y, s.y) - 40 * scale);
        const x2 = Math.min(mon.x + mon.width, Math.max(i.x + i.w, s.x + s.w) + 40 * scale);
        const y2 = Math.min(mon.y + mon.height, Math.max(i.y + i.h, s.y + s.h) + 40 * scale);
        await shot(name, x1 * k, y1 * k, (x2 - x1) * k, (y2 - y1) * k);
    };
    const run = async (name, fn) => {
        if (only.length && !only.includes(name))
            return;
        try {
            await fn();
        } catch (e) {
            log('error', {name, error: `${e}`, stack: `${e.stack}`.split('\n').slice(0, 4)});
        }
    };

    const seat = Clutter.get_default_backend().get_default_seat();
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const now = () => GLib.get_monotonic_time();
    const click = async (actor, button = Clutter.BUTTON_PRIMARY, at = [0.5, 0.5]) => {
        const r = rect(actor);
        pointer.notify_absolute_motion(now(), r.x + r.w * at[0], r.y + r.h * at[1]);
        await sleep(250);
        pointer.notify_button(now(), button, Clutter.ButtonState.PRESSED);
        await sleep(60);
        pointer.notify_button(now(), button, Clutter.ButtonState.RELEASED);
        await sleep(900);
    };
    const park = async () => {
        pointer.notify_absolute_motion(now(), mon.x + mon.width / 2, mon.y + 20);
        await sleep(300);
    };
    const escape = async () => {
        for (const menu of [Main.panel.statusArea.quickSettings.menu])
            menu.close(false);
        await park();
        await sleep(400);
    };

    const quick = Main.panel.statusArea.quickSettings;
    await run('quick-options', async () => {
        await click(quick._statusControls ?? quick);
        await measure('quick-options', quick, quick.menu.box, {openedBy: 'click on status controls', open: quick.menu.isOpen});
        await escape();
    });

    await run('clock', async () => {
        await click(quick._statusClock);
        const open = [quick.menu, Main.panel.statusArea.dateMenu.menu].find(m => m.isOpen);
        await measure('clock', open?.sourceActor ?? quick, (open ?? quick.menu).box,
            {openedBy: 'click on status clock', menu: open === quick.menu ? 'quick-options' : open ? 'date-menu' : 'none'});
        open?.close(false);
        await escape();
    });

    const dash = Main.overview.dash;
    const items = dash._box.get_children().filter(c => c.visible && c.child && c.showLabel);
    log('dash-items', items.map(it => ({app: it.child?.app?.get_id?.(), rect: Object.values(rect(it)).map(r2),
        child: Object.values(rect(it.child)).map(r2)})));

    for (const [n, item] of items.slice(0, 3).entries()) {
        await run('tooltip', async () => {
            item.setLabelText?.(item.child?.app?.get_name?.() ?? `Item ${n}`);
            item.showLabel();
            await sleep(500);
            await measure(`tooltip-${n}`, item, item.label, {app: item.child?.app?.get_id?.(), itemSize: [item.width, item.height]});
            item.hideLabel();
            await sleep(300);
        });
        await run('dock-menu', async () => {
            const icon = item.child;
            await click(icon, Clutter.BUTTON_SECONDARY);
            await measure(`dock-menu-${n}`, icon, icon._menu.box, {app: icon.app?.get_id?.()});
            icon._menu.close(false);
            await park();
        });
        await run('preview', async () => {
            const icon = item.child;
            const preview = (await import('resource:///org/gnome/shell/ui/lumaDockWindows.js')).getDockPreview();
            preview._open(item, icon);
            await sleep(1500);
            const pop = preview._popover;
            if (!pop?.mapped) {
                log('preview-not-open', {n});
                return;
            }
            await measure(`preview-${n}`, icon, pop._surface ?? pop, {app: icon.app?.get_id?.()});
            preview.close(true);
            await sleep(500);
        });
    }

    await run('well', async () => {
        const well = Main.shelf._well;
        log('well', {visible: well.visible, children: well.get_children().map(c => c.constructor.name), chip: well._chip?.visible, rest: well._rest?.length});
        if (well._chip?.get_parent() === well) {
            await click(well._chip);
            await measure('well-popover', well, well._popover.box, {openedBy: 'click on count', open: well._popover.isOpen});
            well._popover.close(false);
            await park();
        }
        const item = well.get_children().find(c => c.source?.indicator);
        if (item) {
            await click(item, Clutter.BUTTON_SECONDARY);
            const menu = item.source?.indicator?.menu;
            const box = menu?.box;
            const bp = menu?._boxPointer;
            log('well-item-debug', {bp: bp && Object.values(rect(bp)).map(r2), bin: bp && Object.values(rect(bp.bin)).map(r2),
                cls: bp?.style_class, surface: bp?._lumaShelfSurface, src: menu?.sourceActor?.constructor?.name,
                rise: bp?.get_theme_node().get_length('-arrow-rise'), side: bp?._arrowSide, userSide: bp?._userArrowSide,
                boxClass: box?.style_class, translation: bp && [bp.translation_x, bp.translation_y],
                pref: bp?.get_preferred_size(), alloc: bp && [bp.allocation.x1, bp.allocation.y1, bp.allocation.x2, bp.allocation.y2],
                parent: bp?.get_parent()?.constructor?.name, parentRect: bp?.get_parent() && Object.values(rect(bp.get_parent())).map(r2),
                actor: menu?.actor?.constructor?.name, actorRect: menu?.actor && Object.values(rect(menu.actor)).map(r2)});
            await measure('well-item-menu', item, box, {openedBy: 'right click on item', open: menu?.isOpen});
            menu?.close(false);
            await park();
        }
    });

    await run('osd-paths', async () => {
        // The brightness manager's call (show with per-monitor levels) and a
        // level-less request as Mutter's show-osd sends it.
        const osd = Main.osdWindowManager;
        osd.show(Gio.Icon.new_for_string('display-brightness-symbolic'), null, {[Main.layoutManager.primaryIndex]: {level: 0.35}});
        await sleep(450);
        let w = osd._osdWindows[Main.layoutManager.primaryIndex];
        log('osd-path', {path: 'brightnessManager show()', visible: w.visible, value: w._value.text, spoken: w._capsule.accessible_name, glyph: w._glyph._glyph});
        osd.hideAll();
        await sleep(400);
        global.display.emit('show-osd', Main.layoutManager.primaryIndex, 'input-touchpad-symbolic', 'Touchpad off');
        await sleep(450);
        w = osd._osdWindows[Main.layoutManager.primaryIndex];
        log('osd-path', {path: 'Mutter show-osd', visible: w.visible, value: w._value.text, spoken: w._capsule.accessible_name, icon: w._icon.visible});
        const b = rect(w._capsule);
        await shot('osd-mutter-show-osd', (b.x - 60) * k, (b.y - 60) * k, (b.w + 120) * k, (b.h + 160) * k);
        osd.hideAll();
        await sleep(400);
    });

    await run('live', async () => {
        const live = Main.panel.statusArea.liveExtensions;
        log('live', {visible: live?.visible, mapped: live?.mapped, title: live?._title?.text});
        if (!live?.mapped)
            return;
        // Away from the island's action buttons, which take their own clicks.
        let menu;
        for (const at of [[0.5, 0.5], [0.5, 0.12], [0.12, 0.5], [0.5, 0.88]]) {
            await click(live, Clutter.BUTTON_SECONDARY, at);
            menu = live._options;
            if (menu?.isOpen)
                break;
            await park();
        }
        log('live-click', {pointerAt: menu?.isOpen, actions: live._actions?.get_children().length});
        await measure('live-island-menu', live, menu.box, {openedBy: 'right click on live island', open: menu.isOpen});
        menu.close(false);
        await park();
    });

    await run('beam', async () => {
        const osd = Main.osdWindowManager;
        const levels = (GLib.getenv('B_LEVELS') || 'volume:0.48').split(',');
        for (const spec of levels) {
            const [kind, value, max] = spec.split(':');
            const icon = {volume: 'audio-volume-medium-symbolic', muted: 'audio-volume-muted-symbolic',
                brightness: 'display-brightness-symbolic', caps: 'input-keyboard-symbolic'}[kind];
            const level = value === 'none' ? undefined : Number(value);
            if (level === undefined)
                osd.showAll(new Gio.ThemedIcon({name: icon}), kind === 'caps' ? 'Caps Lock on' : null);
            else
                osd.showAll(new Gio.ThemedIcon({name: icon}), null, level, Number(max ?? 1));
            await sleep(GLib.getenv('B_A11Y') === 'true' ? 1200 : 450);
            const win = osd._osdWindows[0];
            const body = win._capsule ?? win._hbox;
            await measure(`beam-${kind}-${value}`, quick, body, {
                value: win._value?.text ?? win._label?.text, accessible: body.accessible_name,
                glyph: win._glyph?._glyph, fill: win._fill?.value ?? win._level?.value, visible: win.visible, opacity: win.opacity,
                treatment: (body.style_class ?? '').split(' ').find(c => c.startsWith('luma-surface-')),
                blur: body.get_effect?.('luma-surface-backdrop')?.enabled ?? null,
                size: [body.width / scale, body.height / scale]});
        }
        // Opening Quick Options dismisses Beam, and Beam does not show while it is open.
        Main.panel.toggleQuickSettings();
        await sleep(300);
        const dismissed = !osd._osdWindows[0].visible;
        osd.showAll(new Gio.ThemedIcon({name: 'audio-volume-high-symbolic'}), null, 0.9, 1);
        await sleep(300);
        log('beam-quick-options', {dismissedOnOpen: dismissed, shownWhileOpen: osd._osdWindows[0].visible});
        quick.menu.close(false);
        await sleep(300);
    });

    // Beam where no shelf is shown: a monitor without the shelf, a fullscreen
    // window, the lock screen.
    const noShelf = async (name, index) => {
        const osd = Main.osdWindowManager;
        osd.showAll(new Gio.ThemedIcon({name: 'audio-volume-high-symbolic'}), null, 0.64, 1);
        await sleep(450);
        const win = osd._osdWindows[index];
        const body = win._capsule ?? win._hbox;
        const b = rect(body);
        const m = Main.layoutManager.monitors[index];
        const work = Main.layoutManager.getWorkAreaForMonitor(index);
        const res = {name, monitor: [m.x, m.y, m.width, m.height], work: [work.x, work.y, work.width, work.height],
            beam: [b.x, b.y, b.w, b.h].map(r2), centreOffset: r2((b.x + b.w / 2) - (m.x + m.width / 2)),
            gapToWorkBottomLogical: r2((work.y + work.height - (b.y + b.h)) / scale), visible: win.visible,
            shelfMapped: Main.shelf?._actionsIsland?.mapped ?? null};
        const shelfEdge = Main.shelf?._edge ?? 'bottom';
        const vertical = shelfEdge === 'left' || shelfEdge === 'right';
        res.edge = shelfEdge;
        res.centreOffsetAlong = r2(vertical ? (b.y + b.h / 2) - (m.y + m.height / 2) : res.centreOffset);
        res.gapToShelfWorkEdgeLogical = r2({bottom: work.y + work.height - (b.y + b.h), top: b.y - work.y,
            left: b.x - work.x, right: work.x + work.width - (b.x + b.w)}[shelfEdge] / scale);
        res.pass = Math.abs(res.centreOffsetAlong) <= 1 && Math.abs(res.gapToShelfWorkEdgeLogical - 8) <= 0.5;
        results.push(res);
        log('noshelf', res);
        const y1 = Math.max(m.y, b.y - 60 * scale);
        await shot(name, m.x * k, y1 * k, m.width * k, (m.y + m.height - y1) * k);
        osd.hideAll();
        await sleep(400);
    };
    await run('second-monitor', async () => {
        if (Main.layoutManager.monitors.length > 1)
            await noShelf('noshelf-second-monitor', 1);
    });
    await run('fullscreen', async () => {
        const window = global.get_window_actors().map(a => a.meta_window).find(w => w.get_wm_class()?.includes('Claude') || w.get_title() === 'Claude');
        window.make_fullscreen();
        await sleep(2000);
        await noShelf('noshelf-fullscreen', Main.layoutManager.primaryIndex);
        window.unmake_fullscreen();
        await sleep(1000);
    });
    await run('motion', async () => {
        // The headless oracle renders in software, where the Shell inhibits
        // animations; B_MOTION=1 lifts that inhibition to observe the motion.
        const settings = St.Settings.get();
        const lift = GLib.getenv('B_MOTION') === '1' && !settings.enable_animations;
        if (lift)
            settings.uninhibit_animations();
        const osd = Main.osdWindowManager;
        const win = osd._osdWindows[0];
        osd.showAll(new Gio.ThemedIcon({name: 'audio-volume-high-symbolic'}), null, 0.2, 1);
        const entering = {translation: [win.translation_x, win.translation_y], opacity: win.opacity,
            transitions: ['opacity', 'translation-y'].filter(t => win.get_transition(t))};
        await sleep(40);
        const mid = {translation: [r2(win.translation_x), r2(win.translation_y)], opacity: win.opacity};
        await sleep(400);
        osd.showAll(new Gio.ThemedIcon({name: 'audio-volume-high-symbolic'}), null, 0.8, 1);
        const update = {translation: [win.translation_x, win.translation_y], opacity: win.opacity,
            fillTransition: !!win._fill?.get_transition('value'), fillNow: r2(win._fill?.value ?? -1)};
        await sleep(40);
        update.fillAfter40ms = r2(win._fill?.value ?? -1);
        await sleep(1700);
        const hidden = {visible: win.visible, opacity: win.opacity};
        log('motion', {animations: St.Settings.get().enable_animations, lifted: lift, entering, mid, update, hiddenAfter: hidden});
        if (lift)
            settings.inhibit_animations();
    });
    await run('lock', async () => {
        // No logind or GDM in the oracle, so no screen shield: enter the
        // lock screen's session mode directly, as the shield does.
        if (Main.screenShield)
            Main.screenShield.lock(false);
        else
            Main.sessionMode.pushMode('unlock-dialog');
        await sleep(4000);
        log('lock-state', {mode: Main.sessionMode.currentMode, locked: Main.screenShield?.locked ?? null});
        await noShelf('noshelf-lock-screen', Main.layoutManager.primaryIndex);
    });

    GLib.file_set_contents(`${GLib.getenv('ORACLE_OUT')}/measure.json`, JSON.stringify(results, null, 1));
}
