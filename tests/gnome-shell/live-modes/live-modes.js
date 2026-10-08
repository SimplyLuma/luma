// SPDX-License-Identifier: GPL-2.0-or-later
// The live islands survive every appearance mode change: with a Live Extension
// (a timer) and a playing browser tab, switch Dark -> Light -> Frost -> Glass ->
// Dark and, in each mode, assert the live and now-playing islands are on the
// stage, visible, sized, and their text readable against the island.
// Run as the automation script of a headless Shell (live-modes.sh).
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Shell from 'gi://Shell';
import Clutter from 'gi://Clutter';
import Cogl from 'gi://Cogl';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';

const OUT = GLib.getenv('LM_OUT') ?? '/tmp/live-modes';
const results = [];
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const log = m => console.log(`[livemodes] ${m}`);
const check = (name, ok, detail = '') => { results.push({name, ok: !!ok, detail}); log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`); };
const descend = (a, out = []) => { out.push(a); a.get_children?.().forEach(c => descend(c, out)); return out; };
Gio._promisify(Shell.Screenshot.prototype, 'screenshot_stage_to_content');
Gio._promisify(Shell.Screenshot, 'composite_to_stream');
async function shot(name, x, y, w, h) {
    // A crop past the stage never completes: keep it on the stage.
    x = Math.max(0, Math.round(x)); y = Math.max(0, Math.round(y));
    w = Math.min(Math.round(w), global.stage.width - x); h = Math.min(Math.round(h), global.stage.height - y);
    const [content, scale] = await new Shell.Screenshot().screenshot_stage_to_content();
    const stream = Gio.File.new_for_path(`${OUT}/${name}.png`).replace(null, false, Gio.FileCreateFlags.NONE, null);
    await Shell.Screenshot.composite_to_stream(content.get_texture(), x, y, w, h, scale, null, 0, 0, 1, stream);
    stream.close(null);
}
const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c.red) + 0.7152 * f(c.green) + 0.0722 * f(c.blue); };
const over = (fg, bg) => ({red: fg.red * fg.alpha / 255 + bg.red * (1 - fg.alpha / 255), green: fg.green * fg.alpha / 255 + bg.green * (1 - fg.alpha / 255), blue: fg.blue * fg.alpha / 255 + bg.blue * (1 - fg.alpha / 255)});
const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
// Worst case backdrop for a translucent island: the harness wallpaper is mid blue.
const BACKDROP = {red: 0, green: 60, blue: 140, alpha: 255};

function inspect(island, mode, label) {
    if (!island) { check(`${mode}: ${label} island exists`, false); return; }
    const onStage = !!island.get_stage();
    const e = island.get_transformed_extents();
    const material = island._content ?? island;
    const labels = descend(island).filter(a => typeof a.get_theme_node === 'function' && typeof a.text === 'string' && a.text && a.visible && a.mapped);
    check(`${mode}: ${label} island on the stage, visible and sized`,
        onStage && island.visible && island.mapped && island.opacity > 0 && e.get_width() > 20 && e.get_height() > 20,
        `stage=${onStage} visible=${island.visible} mapped=${island.mapped} opacity=${island.opacity} size=${Math.round(e.get_width())}x${Math.round(e.get_height())} classes=${island.style_class}`);
    check(`${mode}: ${label} island shows its text`, labels.length > 0, labels.map(l => l.text).join(' | '));
    if (!labels.length || !onStage) return;
    const bg = material.get_theme_node().get_background_color();
    const plate = over(bg, BACKDROP);
    const worst = Math.min(...labels.map(l => contrast(over(l.get_theme_node().get_foreground_color(), plate), plate)));
    check(`${mode}: ${label} island text is readable (>= 4.5:1 on its surface)`, worst >= 4.5,
        `worst ${worst.toFixed(2)}:1, surface ${bg.to_string()}`);
    return e;
}

async function setMode(mode) {
    const state = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});
    const iface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
    iface.set_string('color-scheme', mode === 'dark' ? 'prefer-dark' : 'default');
    state.set_string('surface-treatment', mode);
    await sleep(2500);
}

let newGlyph = () => {};
async function work() {
    const shelf = Main.shelf;
    for (let i = 0; i < 60 && !shelf?._group; i++) await sleep(500);
    for (let i = 0; i < 40 && !(shelf._liveIsland?.visible && shelf._mediaIsland?.visible); i++) await sleep(500);
    // An extension whose tray glyph is a plain Clutter actor with content, as
    // an XEmbed or pixmap tray icon is: the Well inks it, and on the owner's
    // ThinkPad inking one threw during every shelf rebuild, which dropped the
    // live island at each appearance change.
    const tray = new PanelMenu.Button(0.5, 'Pixmap tray icon', true);
    // A tray icon redraws itself as a new actor when the theme changes, as the
    // AppIndicator extension's pixmap icons do.
    newGlyph = () => {
        const image = new St.ImageContent({preferred_width: 16, preferred_height: 16});
        const cogl = global.stage.context.get_backend().get_cogl_context();
        image.set_data(cogl, new Uint8Array(16 * 16 * 4).fill(200), Cogl.PixelFormat.RGBA_8888, 16, 16, 16 * 4);
        tray.remove_all_children();
        tray.add_child(new Clutter.Actor({content: image, width: 16, height: 16}));
    };
    newGlyph();
    Main.panel.addToStatusArea('live-modes-pixmap-tray', tray);
    await sleep(1500);
    for (const mode of ['dark', 'light', 'frost', 'glass', 'dark']) {
        newGlyph();
        await setMode(mode);
        const live = inspect(shelf._liveIsland, mode, 'live');
        inspect(shelf._mediaIsland, mode, 'now-playing');
        const g = shelf._group.get_transformed_extents();
        await shot(`shelf-${mode}-${results.length}`, g.get_x() - 16, g.get_y() - 16, g.get_width() + 32, g.get_height() + 32);
        const stray = descend(global.stage).length;
        log(`mode ${mode}: actors ${stray} live=${live ? 'ok' : 'missing'}`);
    }
}

export function init() {
    GLib.mkdir_with_parents(OUT, 0o755);
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 8000, () => {
        work().catch(e => check('scenario ran without errors', false, `${e}\n${e.stack}`)).finally(() => {
            GLib.file_set_contents(`${OUT}/live-modes.json`, JSON.stringify(results, null, 1));
            log(`DONE ${results.filter(r => r.ok).length}/${results.length} passed`);
            global.context.terminate();
        });
        return GLib.SOURCE_REMOVE;
    });
}
export async function run() { await new Promise(() => {}); }
