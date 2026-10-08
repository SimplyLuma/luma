// SPDX-License-Identifier: GPL-2.0-or-later
// The Quick Options island is one surface: at rest no inner segment (the Well's
// items, the status icons, the clock) has a fill of its own; hovered anywhere,
// the whole island highlights once; focused by keyboard, one ring goes round
// the whole island. Run with the owner's shell-state (island-segments.sh), in
// all four modes, and name every actor that paints if any segment does.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import Cogl from 'gi://Cogl';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';

const OUT = GLib.getenv('IS_OUT') ?? '/tmp/island-segments';
const results = [];
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const log = m => console.log(`[islandsegments] ${m}`);
const check = (name, ok, detail = '') => { results.push({name, ok: !!ok, detail}); log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`); };
const descend = (a, out = []) => { out.push(a); a.get_children?.().forEach(c => descend(c, out)); return out; };
Gio._promisify(Shell.Screenshot.prototype, 'screenshot_stage_to_content');
Gio._promisify(Shell.Screenshot, 'composite_to_stream');
async function shot(name, actor) {
    const e = actor.get_transformed_extents();
    let [x, y, w, h] = [e.get_x() - 14, e.get_y() - 14, e.get_width() + 28, e.get_height() + 28].map(Math.round);
    x = Math.max(0, x); y = Math.max(0, y);
    w = Math.min(w, global.stage.width - x); h = Math.min(h, global.stage.height - y);
    const [content, scale] = await new Shell.Screenshot().screenshot_stage_to_content();
    const stream = Gio.File.new_for_path(`${OUT}/${name}.png`).replace(null, false, Gio.FileCreateFlags.NONE, null);
    await Shell.Screenshot.composite_to_stream(content.get_texture(), x, y, w, h, scale, null, 0, 0, 1, stream);
    stream.close(null);
}
// What an actor paints from its style, if anything.
function paint(actor) {
    if (!(actor instanceof St.Widget) || !actor.get_stage() || !actor.visible || !actor.mapped || actor.get_paint_opacity() === 0)
        return null;
    // Scrollbars and the 1px dividers between segments are not fills.
    if (actor instanceof St.ScrollBar || actor.get_parent() instanceof St.ScrollBar || actor.has_style_class_name?.('luma-status-seam'))
        return null;
    const node = actor.get_theme_node();
    const out = {};
    const bg = node.get_background_color();
    if (bg.alpha > 0) out.background = bg.to_string();
    if (node.get_box_shadow()) out.boxShadow = true;
    if (node.get_border_width(St.Side.TOP) > 0 && node.get_border_color(St.Side.TOP).alpha > 0) out.border = node.get_border_color(St.Side.TOP).to_string();
    return Object.keys(out).length ? {classes: actor.style_class ?? actor.constructor.name, pseudo: actor.pseudo_class ?? '', ...out} : null;
}
async function setMode(mode) {
    new Gio.Settings({schema_id: 'org.gnome.desktop.interface'}).set_string('color-scheme', mode === 'dark' ? 'prefer-dark' : 'default');
    new Gio.Settings({schema_id: 'org.project_luma.shell-state'}).set_string('surface-treatment', mode);
    await sleep(2500);
}
async function work() {
    const shelf = Main.shelf;
    for (let i = 0; i < 60 && !shelf?._actionsIsland; i++) await sleep(500);
    await sleep(4000);
    // An extension indicator in the Well, like the owner's tiling glyph.
    const tray = new PanelMenu.Button(0.5, 'Tray glyph', false);
    tray.add_child(new St.Icon({icon_name: 'view-grid-symbolic', style_class: 'system-status-icon'}));
    Main.panel.addToStatusArea('island-segments-tray', tray);
    await sleep(2000);
    const island = shelf._actionsIsland;
    const material = island._content;
    const seat = Clutter.get_default_backend().get_default_seat();
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    const now = () => GLib.get_monotonic_time();
    const move = async (x, y) => { pointer.notify_absolute_motion(now(), x, y); await sleep(500); };
    const centre = a => { const e = a.get_transformed_extents(); return [e.get_x() + e.get_width() / 2, e.get_y() + e.get_height() / 2]; };
    // Everything inside the island except its own material, stroke, shadows
    // and wash layers: none of these may paint.
    const inner = () => descend(material).filter(a => a !== material).map(paint).filter(Boolean)
        .filter(p => !/luma-shelf-(material|shadow|stroke)|luma-status-wash|luma-island-wash|luma-media-art/.test(p.classes));
    const quick = Main.panel.statusArea.quickSettings;
    const clock = descend(quick).find(a => a.has_style_class_name?.('luma-status-clock'));
    const well = descend(material).find(a => a.has_style_class_name?.('luma-well'));
    const wellItem = () => well && descend(well).find(a => (a.has_style_class_name?.('luma-well-item') || a.has_style_class_name?.('panel-button')) && a.mapped && a.width > 0);
    const glyph = descend(quick).find(a => a.has_style_class_name?.('system-status-icon') && a.mapped);
    // What the Well shows: every icon, with its ink and size.
    for (const icon of descend(well ?? material).filter(a => a instanceof St.Icon && a.mapped)) {
        const e = icon.get_transformed_extents();
        log(`well icon ${icon.gicon?.to_string?.() ?? icon.icon_name} ${Math.round(e.get_width())}x${Math.round(e.get_height())} ` +
            `opacity ${icon.get_paint_opacity()} ink ${icon.get_theme_node().get_foreground_color().to_string()}`);
    }
    for (const mode of ['dark', 'light', 'frost', 'glass']) {
        await setMode(mode);
        await move(20, 20);
        const rest = inner();
        check(`${mode}: at rest no segment of the island has a fill of its own`, rest.length === 0, JSON.stringify(rest));
        await shot(`island-${mode}-rest`, island);
        for (const [name, target] of [['clock', clock], ['status icons', glyph], ['Well item', wellItem()]]) {
            if (!target) { log(`${mode}: no ${name} to hover`); continue; }
            await move(...centre(target));
            const hovered = inner();
            const whole = paint(material);
            check(`${mode}: hovering the ${name} paints no sub-highlight`, hovered.length === 0, JSON.stringify(hovered));
            check(`${mode}: hovering the ${name} washes the whole island once`, !!whole?.boxShadow && material.has_style_class_name('luma-island-hover'),
                JSON.stringify(whole));
            await shot(`island-${mode}-hover-${name.replace(/\W+/g, '-')}`, island);
        }
        await move(20, 20);
        quick.grab_key_focus();
        keyboard.notify_keyval(now(), Clutter.KEY_Tab, Clutter.KeyState.PRESSED);
        keyboard.notify_keyval(now(), Clutter.KEY_Tab, Clutter.KeyState.RELEASED);
        await sleep(200);
        quick.grab_key_focus();
        await sleep(500);
        const focused = inner();
        check(`${mode}: keyboard focus draws no ring on an inner segment`, focused.length === 0, JSON.stringify(focused));
        check(`${mode}: keyboard focus rings the whole island`, material.has_style_class_name('luma-island-focus') && !!paint(material)?.boxShadow,
            JSON.stringify(paint(material)));
        await shot(`island-${mode}-focus`, island);
        global.stage.set_key_focus(null);
        await sleep(300);
    }
}
export function init() {
    GLib.mkdir_with_parents(OUT, 0o755);
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 8000, () => {
        work().catch(e => check('scenario ran without errors', false, `${e}\n${e.stack}`)).finally(() => {
            GLib.file_set_contents(`${OUT}/island-segments.json`, JSON.stringify(results, null, 1));
            log(`DONE ${results.filter(r => r.ok).length}/${results.length} passed`);
            global.context.terminate();
        });
        return GLib.SOURCE_REMOVE;
    });
}
export async function run() { await new Promise(() => {}); }
