// SPDX-License-Identifier: Apache-2.0
// Every Quick Options detail sheet, in Light, Dark, Frost and Glass, with its
// toggle off and on. The sheet opens in flow under the row that owns it (the
// rows below move down; nothing is laid over them), shares one outline with
// its owner, and shows a plain sentence-case title with a muted count, no
// repeated icon: the title and count stand out from the sheet (>= 4.5:1).
// Before 0164 the sheet was an overlay with GNOME's icon header, whose glyph
// was once white on the white Light detail (0159).
// Run as the automation script of a headless Shell (quick-details.sh).
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const OUT = GLib.getenv('QD_OUT') ?? '/tmp/quick-details';
const results = [];
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const log = m => console.log(`[quickdetails] ${m}`);
const check = (name, ok, detail = '') => { results.push({name, ok: !!ok, detail}); log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`); };
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
// The sheet paints no fill of its own: the grid's frame does, under it.
function detailColour(menu, grid) {
    const frame = grid.get_children().find(c => c.has_style_class_name?.('luma-sheet-frame'));
    const [, fill] = frame.get_theme_node().lookup_color('-luma-sheet-fill', false);
    return over(fill, panelColour(grid).colour);
}

async function work() {
    for (let i = 0; i < 60 && !Main.panel.statusArea.quickSettings; i++) await sleep(500);
    await sleep(5000);
    const quick = Main.panel.statusArea.quickSettings;
    const grid = quick.menu._grid;
    // Every item of the grid that has a sheet (a card, a pill, a level, the
    // footer's power button).
    const toggles = grid.get_children().filter(a => a.menu && a.menu._titleLabel && typeof a.menu.open === 'function');
    check('Quick Options has details to test', toggles.length > 0, toggles.map(t => t.constructor.name).join(', '));
    for (const mode of ['light', 'dark', 'frost', 'glass']) {
        await setMode(mode);
        quick.menu.open();
        await sleep(1200);
        for (const toggle of toggles) {
            if (!toggle.visible)
                continue;
            const name = toggle.title || toggle.constructor.name;
            for (const on of [false, true]) {
                // Exercise both owner states without changing any real setting.
                const owner = toggle.getSheetAnchor?.() ?? toggle;
                const wasChecked = owner.checked;
                const below = grid.get_children().filter(c => c.visible && c.y > toggle.y + toggle.height && c !== toggle.menu.actor &&
                    !c.has_style_class_name?.('luma-sheet-frame'));
                const before = new Map(below.map(c => [c, c.y]));
                toggle.menu.open();
                await sleep(900);
                if (on) owner.add_style_pseudo_class('checked');
                await sleep(300);
                const opened = toggle.menu.box.mapped;
                if (!on)
                    check(`${mode}: ${name} detail opens`, opened);
                if (!opened) { toggle.menu.close(); await sleep(300); break; }
                const sheet = toggle.menu.actor;
                const inGrid = sheet.get_parent() === grid;
                const pushed = below.every(c => c.y >= sheet.y + sheet.height - 1);
                if (!on) {
                    check(`${mode}: ${name} detail is in flow (in the grid, rows below pushed down)`, inGrid && pushed,
                        `inGrid=${inGrid} moved=${below.map(c => `${Math.round(before.get(c))}->${Math.round(c.y)}`).join(',')}`);
                    check(`${mode}: ${name} owner joins its sheet`, owner.has_style_class_name('luma-sheet-owner'));
                }
                const header = toggle.menu._header;
                check(`${mode}: ${name} detail (${on ? 'on' : 'off'}) repeats no icon`, !header?.mapped);
                const bg = detailColour(toggle.menu, grid);
                for (const label of [toggle.menu._titleLabel, toggle.menu._countLabel].filter(l => l?.visible && l.text)) {
                    const c = contrast(over(label.get_theme_node().get_foreground_color(), bg), bg);
                    check(`${mode}: ${name} detail: "${label.text}" readable (>= 4.5:1)`, c >= 4.5, `${c.toFixed(2)}:1`);
                }
                if (on)
                    await shot(`detail-${mode}-${name.replace(/\W+/g, '-')}`, quick.menu.box);
                if (!wasChecked) owner.remove_style_pseudo_class('checked');
                toggle.menu.close();
                await sleep(400);
            }
        }
        quick.menu.close();
        await sleep(600);
    }
}

export function init() {
    GLib.mkdir_with_parents(OUT, 0o755);
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 8000, () => {
        work().catch(e => check('scenario ran without errors', false, `${e}\n${e.stack}`)).finally(() => {
            GLib.file_set_contents(`${OUT}/quick-details.json`, JSON.stringify(results, null, 1));
            log(`DONE ${results.filter(r => r.ok).length}/${results.length} passed`);
            global.context.terminate();
        });
        return GLib.SOURCE_REMOVE;
    });
}
export async function run() { await new Promise(() => {}); }
