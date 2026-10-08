// SPDX-License-Identifier: Apache-2.0
// Quick Options > Tiling on two or three monitors, in Light, Dark, Frost and
// Glass: every layout thumbnail is on the stage, its tiles stand out from the
// panel (>= 3:1), the current layout is marked, and a click selects a layout.
// Run as the automation script of a headless Shell (tiling-picker.sh).
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const OUT = GLib.getenv('TP_OUT') ?? '/tmp/tiling-picker';
const results = [];
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const log = m => console.log(`[tilingpicker] ${m}`);
const check = (name, ok, detail = '') => { results.push({name, ok: !!ok, detail}); log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`); };
const descend = (a, out = []) => { out.push(a); a.get_children?.().forEach(c => descend(c, out)); return out; };
Gio._promisify(Shell.Screenshot.prototype, 'screenshot_stage_to_content');
Gio._promisify(Shell.Screenshot, 'composite_to_stream');
async function shot(name, actor) {
    const e = actor.get_transformed_extents();
    let [x, y, w, h] = [e.get_x() - 12, e.get_y() - 12, e.get_width() + 24, e.get_height() + 24].map(Math.round);
    x = Math.max(0, x); y = Math.max(0, y);
    w = Math.min(w, global.stage.width - x); h = Math.min(h, global.stage.height - y);
    const [content, scale] = await new Shell.Screenshot().screenshot_stage_to_content();
    const stream = Gio.File.new_for_path(`${OUT}/${name}.png`).replace(null, false, Gio.FileCreateFlags.NONE, null);
    await Shell.Screenshot.composite_to_stream(content.get_texture(), x, y, w, h, scale, null, 0, 0, 1, stream);
    stream.close(null);
}
const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c.red) + 0.7152 * f(c.green) + 0.0722 * f(c.blue); };
const over = (fg, bg) => ({red: fg.red * fg.alpha / 255 + bg.red * (1 - fg.alpha / 255), green: fg.green * fg.alpha / 255 + bg.green * (1 - fg.alpha / 255), blue: fg.blue * fg.alpha / 255 + bg.blue * (1 - fg.alpha / 255), alpha: 255});
const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
// The panel over the harness wallpaper: its own fill composited over mid blue.
const BACKDROP = {red: 0, green: 60, blue: 140, alpha: 255};
function panelColour(actor) {
    for (let a = actor; a; a = a.get_parent()) {
        if (typeof a.get_theme_node !== 'function' || !a.get_stage())
            continue;
        const bg = a.get_theme_node().get_background_color();
        if (bg.alpha > 200 || a.has_style_class_name?.('luma-quick-options') || a.has_style_class_name?.('popup-menu-content'))
            return {colour: over(bg, BACKDROP), owner: a.style_class};
    }
    return {colour: BACKDROP, owner: 'wallpaper'};
}

async function setMode(mode) {
    new Gio.Settings({schema_id: 'org.gnome.desktop.interface'}).set_string('color-scheme', mode === 'dark' ? 'prefer-dark' : 'default');
    new Gio.Settings({schema_id: 'org.project_luma.shell-state'}).set_string('surface-treatment', mode);
    await sleep(2500);
}

async function work() {
    for (let i = 0; i < 60 && !Main.panel.statusArea.quickSettings; i++) await sleep(500);
    await sleep(4000);
    const quick = Main.panel.statusArea.quickSettings;
    check(`${Main.layoutManager.monitors.length} monitors`, Main.layoutManager.monitors.length >= 2);
    const tiling = await (async () => {
        for (let i = 0; i < 40; i++) {
            const t = descend(quick.menu.box).find(a => a.constructor.name.includes('TilingToggle'));
            if (t) return t;
            await sleep(500);
        }
        return null;
    })();
    check('the Tiling toggle is in Quick Options', !!tiling);
    if (!tiling) return;
    const tilingSettings = tiling._tilingSettings;
    for (const mode of (GLib.getenv('TP_MODES') ?? 'light,dark,frost,glass').split(',')) {
        await setMode(mode);
        quick.menu.open();
        await sleep(1200);
        tiling.menu.open();
        await sleep(1500);
        const previews = descend(tiling.menu.actor ?? tiling.menu.box).filter(a => a.has_style_class_name?.('luma-tiling-preview'));
        const shown = previews.filter(p => p.get_stage() && p.mapped && p.visible && p.width > 10 && p.height > 10);
        const labels = descend(tiling.menu.actor ?? tiling.menu.box).filter(a => a.has_style_class_name?.('luma-tiling-display-name'));
        check(`${mode}: a thumbnail per layout per display is on the stage`, previews.length > 0 && shown.length === previews.length,
            `${shown.length}/${previews.length} shown under ${labels.length} display headings`);
        if (!shown.length) { tiling.menu.close(); quick.menu.close(); continue; }
        const first = shown[0];
        const ancestors = [];
        for (let a = first; a; a = a.get_parent()) if (a.style_class) ancestors.push(a.style_class);
        const {colour: panel, owner} = panelColour(first);
        let worst = Infinity, worstDetail = '';
        for (const p of shown) {
            const tile = descend(p).find(a => a.has_style_class_name?.('luma-tiling-preview-tile'));
            if (!tile) { worst = 0; worstDetail = 'no tiles'; break; }
            const node = tile.get_theme_node();
            const edge = over(node.get_border_color(0), panel);
            const c = contrast(edge, panel);
            if (c < worst) { worst = c; worstDetail = `tile edge ${node.get_border_color(0).to_string()} fill ${node.get_background_color().to_string()} opacity ${tile.get_paint_opacity()}`; }
        }
        check(`${mode}: layout tiles stand out from the panel (>= 3:1)`, worst >= 3,
            `worst ${worst.toFixed(2)}:1 on ${owner}; ${worstDetail}; ancestors ${ancestors.join(' < ')}`);
        const chosen = shown.filter(p => p.checked);
        check(`${mode}: the current layout is marked`, chosen.length >= 1, `${chosen.length} checked`);
        await shot(`picker-${mode}`, tiling.menu.box);
        // A click selects: pick an unchecked thumbnail on the first display.
        const target = shown.find(p => !p.checked);
        if (target && tilingSettings) {
            const before = JSON.stringify(tilingSettings.get_value('selected-layouts').deepUnpack());
            const seat = Clutter.get_default_backend().get_default_seat();
            const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
            const e = target.get_transformed_extents();
            const now = () => GLib.get_monotonic_time();
            pointer.notify_absolute_motion(now(), e.get_x() + e.get_width() / 2, e.get_y() + e.get_height() / 2);
            await sleep(300);
            pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
            await sleep(60);
            pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
            await sleep(800);
            const after = JSON.stringify(tilingSettings.get_value('selected-layouts').deepUnpack());
            check(`${mode}: a click on a thumbnail selects that layout`, before !== after, `${before} -> ${after}`);
        }
        tiling.menu.close();
        quick.menu.close();
        await sleep(600);
    }
}

export function init() {
    GLib.mkdir_with_parents(OUT, 0o755);
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 8000, () => {
        work().catch(e => check('scenario ran without errors', false, `${e}\n${e.stack}`)).finally(() => {
            GLib.file_set_contents(`${OUT}/tiling-picker.json`, JSON.stringify(results, null, 1));
            log(`DONE ${results.filter(r => r.ok).length}/${results.length} passed`);
            global.context.terminate();
        });
        return GLib.SOURCE_REMOVE;
    });
}
export async function run() { await new Promise(() => {}); }
