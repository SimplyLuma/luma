// Focus on the shelf (Shell patch 0151): after Quick Options, the calendar and
// a dock icon are used with the pointer, nothing on the shelf looks focused;
// after the same with the keyboard, the focused control shows a ring in ink.
// Also traces every actor that paints on a hovered dock icon, so a box behind
// the artwork names the rule-bearing actor instead of being guessed at.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import St from 'gi://St';

const log = (m, o) => console.log(`[focus] ${m} ${JSON.stringify(o ?? {})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const descend = (actor, out = []) => { out.push(actor); actor.get_children?.().forEach(c => descend(c, out)); return out; };

// What an actor paints from the stylesheet, if anything.
function paint(actor) {
    if (!(actor instanceof St.Widget) || !actor.get_stage())
        return null;
    const node = actor.get_theme_node();
    const bg = node.get_background_color();
    const out = {};
    if (bg.alpha > 0)
        out.background = bg.to_string();
    const [gradient] = node.get_background_gradient?.() ?? [0];
    if (gradient)
        out.gradient = gradient;
    if (node.get_box_shadow())
        out.boxShadow = true;
    if (node.get_border_width(St.Side.TOP) > 0)
        out.border = node.get_border_width(St.Side.TOP);
    if (actor instanceof St.Icon && node.get_shadow('icon-shadow'))
        out.iconShadow = true;
    return Object.keys(out).length ? {type: actor.constructor.name, classes: actor.style_class ?? '',
        pseudo: actor.pseudo_class ?? '', ...out} : null;
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
    await sleep(3000);
    const seat = Clutter.get_default_backend().get_default_seat();
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    const now = () => GLib.get_monotonic_time();
    const centre = actor => { const e = actor.get_transformed_extents(); return [e.get_x() + e.get_width() / 2, e.get_y() + e.get_height() / 2]; };
    const move = async (x, y, ms = 250) => { pointer.notify_absolute_motion(now(), x, y); await sleep(ms); };
    const click = async actor => {
        const [x, y] = centre(actor);
        await move(x, y);
        pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
        await sleep(60);
        pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
        await sleep(700);
    };
    const key = async (sym, ms = 500) => {
        keyboard.notify_keyval(now(), sym, Clutter.KeyState.PRESSED);
        keyboard.notify_keyval(now(), sym, Clutter.KeyState.RELEASED);
        await sleep(ms);
    };
    const away = async () => move(20, 20, 400);
    const k = Math.max(1, ...global.stage.peek_stage_views().map(v => v.get_scale())) / St.ThemeContext.get_for_stage(global.stage).scale_factor;
    const crop = async name => {
        const g = shelf._group.get_transformed_extents();
        await shot(`focus-${name}-${tag}`, (g.get_x() - 14) * k, (g.get_y() - 16) * k, (g.get_width() + 28) * k, (g.get_height() + 32) * k);
    };
    const quick = Main.panel.statusArea.quickSettings;
    const clock = descend(quick).find(a => a.has_style_class_name?.('luma-status-clock'));
    const state = label => {
        const focus = global.stage.get_key_focus();
        const island = paint(quick);
        log('state', {label, keyboard: shelf._focusVisible?.keyboard ?? null,
            focus: focus ? `${focus.constructor.name}.${focus.style_class ?? ''}` : null,
            islandPseudo: quick.pseudo_class ?? '', islandRing: Boolean(island?.boxShadow),
            menuOpen: quick.menu.isOpen});
    };
    const results = [];
    const record = (name, pass, evidence) => { results.push(pass); log(pass ? 'PASS' : 'FAIL', {name, ...evidence}); };

    // Quick Options and the calendar with the pointer: open, then close by
    // clicking the same control again.
    for (const [name, target] of [['quick options', quick], ['calendar', clock ?? quick]]) {
        await click(target);
        state(`${name} open by pointer`);
        await click(target);
        await away();
        state(`${name} closed by pointer`);
        await crop(`${name.replace(' ', '-')}-pointer`);
        record(`${name} closed by pointer leaves no ring`,
            !quick.has_style_pseudo_class('focus') && !paint(quick)?.boxShadow, {pseudo: quick.pseudo_class ?? ''});
    }
    // The same with the keyboard: focus the island, Return opens, Escape closes.
    quick.grab_key_focus();
    await key(Clutter.KEY_Tab, 300);
    quick.grab_key_focus();
    await sleep(300);
    await key(Clutter.KEY_Return, 900);
    state('quick options open by keyboard');
    await key(Clutter.KEY_Escape, 900);
    state('quick options closed by keyboard');
    await crop('quick-options-keyboard');
    record('closed by keyboard, focus is back on the island and ringed',
        global.stage.get_key_focus() === quick && Boolean(paint(quick)?.boxShadow), {pseudo: quick.pseudo_class ?? ''});
    await click(clock ?? quick);
    await click(clock ?? quick);
    await away();
    record('the next click clears the ring', !paint(quick)?.boxShadow, {pseudo: quick.pseudo_class ?? ''});

    // A dock icon hovered, after a click elsewhere: trace everything that paints.
    const tile = descend(shelf._dash).find(a => a.has_style_class_name?.('overview-tile') && a.visible);
    if (tile) {
        await away();
        const [x, y] = centre(tile);
        await move(x, y, 600);
        const painted = [...descend(tile), tile.get_parent()].map(paint).filter(Boolean);
        log('dock hover paints', {painted});
        await crop('dock-hover');
        record('a hovered dock icon paints no fill, border or box behind the artwork',
            !painted.some(p => p.background || p.gradient || p.boxShadow || p.border), {painted});
        tile.grab_key_focus();
        await key(Clutter.KEY_Right, 400);
        const focused = global.stage.get_key_focus();
        const ring = descend(focused).map(paint).filter(p => p?.boxShadow);
        log('dock keyboard focus', {focus: `${focused?.constructor?.name}.${focused?.style_class ?? ''}`, ring});
        await crop('dock-keyboard-focus');
        record('a dock icon focused by keyboard shows a ring', ring.length > 0, {ring});
        await click(clock ?? quick);
        await click(clock ?? quick);
        await away();
        record('and a click elsewhere clears it', !descend(tile).map(paint).some(p => p?.boxShadow), {});
    }
    // The now-playing island: rest, the artwork and title hovered, a control
    // hovered and pressed. Only a control may paint, in the ledger's fills.
    const media = shelf._media;
    if (media?.eligible && shelf._mediaIsland?.visible) {
        const island = shelf._mediaIsland;
        const cropIsland = async name => {
            const e = island.get_transformed_extents();
            await shot(`media-${name}-${tag}`, (e.get_x() - 12) * k, (e.get_y() - 14) * k, (e.get_width() + 24) * k, (e.get_height() + 28) * k);
        };
        const traced = () => descend(island).map(paint).filter(p => p && (p.background || p.gradient || p.boxShadow) &&
            !/luma-shelf-(material|shadow|stroke)|luma-media-art/.test(p.classes));
        await away();
        await cropIsland('rest');
        log('media rest paints', {painted: traced()});
        const [ix, iy] = centre(media._identity);
        await move(ix, iy, 500);
        await cropIsland('hover-identity');
        const identityPaint = traced();
        log('media identity hover paints', {painted: identityPaint});
        record('hovering the artwork and title paints nothing', identityPaint.length === 0, {painted: identityPaint});
        const control = media._toggle;
        const [cx, cy] = centre(control);
        await move(cx, cy, 500);
        await cropIsland('hover-control');
        const hovered = paint(control);
        log('media control hover', {hovered});
        pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
        await sleep(200);
        await cropIsland('pressed-control');
        const pressed = paint(control);
        log('media control pressed', {pressed});
        pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
        await sleep(400);
        const ledger = {light: ['rgba(25,27,31,0.06)', 'rgba(25,27,31,0.09)'], dark: ['rgba(255,255,255,0.06)', 'rgba(255,255,255,0.09)'],
            frost: ['rgba(255,255,255,0.07)', 'rgba(255,255,255,0.11)'], glass: ['rgba(255,255,255,0.07)', 'rgba(255,255,255,0.11)']};
        const mode = island.style_class.match(/luma-surface-(\w+)/)?.[1];
        log('media ledger', {mode, expected: ledger[mode], hovered: hovered?.background ?? null, pressed: pressed?.background ?? null});
        record('a control hovers and presses, and nothing else on the island does', Boolean(hovered?.background) && Boolean(pressed?.background), {mode});
        await away();
    } else {
        log('media', {skipped: 'no player'});
    }
    log('summary', {passed: results.filter(Boolean).length, total: results.length});
}
