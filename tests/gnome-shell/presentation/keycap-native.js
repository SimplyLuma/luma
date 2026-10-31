// SPDX-License-Identifier: Apache-2.0
// Native Keycap and Tablet status check for the installed Shell. Run through
// run.sh: PRESENTATION_AUTOMATION=<this file> PRESENTATION_MONITOR=360x800.
// Captures land in PRESENTATION_OUT; results.json records the checks.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const results = [];
const out = GLib.getenv('PRESENTATION_OUT');
const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT,
    ms, () => { resolve(); return GLib.SOURCE_REMOVE; }));
function check(name, condition, detail = '') {
    results.push({name, ok: Boolean(condition), detail});
    console.log(`[keycap] ${condition ? 'PASS' : 'FAIL'} ${name} ${detail}`);
}
async function shot(name) {
    Gio._promisify(Shell.Screenshot.prototype, 'screenshot_stage_to_content');
    Gio._promisify(Shell.Screenshot, 'composite_to_stream');
    const [content, scale] = await new Shell.Screenshot().screenshot_stage_to_content();
    const stream = Gio.File.new_for_path(`${out}/${name}.png`).replace(null, false, Gio.FileCreateFlags.NONE, null);
    await Shell.Screenshot.composite_to_stream(content.get_texture(), 0, 0,
        global.stage.width, global.stage.height, scale, null, 0, 0, 1, stream);
    stream.close(null);
}
function descendants(actor, test, found = []) {
    for (const child of actor.get_children()) {
        if (test(child))
            found.push(child);
        descendants(child, test, found);
    }
    return found;
}
function painted(actor) {
    if (!(actor instanceof St.Widget) || !actor.mapped || actor.opacity === 0)
        return null;
    const node = actor.get_theme_node();
    const colour = node.get_background_color();
    const effects = actor.get_effects().filter(effect => effect.enabled).map(effect => effect.constructor.name);
    if (colour.alpha > 0)
        return colour.to_string() + (effects.length ? ` +${effects}` : '');
    return effects.length ? `effects ${effects}` : null;
}

async function keyboardChecks() {
    const monitor = Main.layoutManager.primaryMonitor;
    const tag = `${monitor.width}x${monitor.height}`;
    const a11y = new Gio.Settings({schema_id: 'org.gnome.desktop.a11y.applications'});
    a11y.set_boolean('screen-keyboard-enabled', true);
    await sleep(500);
    const keyboard = Main.keyboard.keyboardActor;
    check('explicit accessibility keyboard is created', !!keyboard);
    if (!keyboard)
        return;
    Main.keyboard.open(Main.layoutManager.primaryIndex);
    await sleep(1200);
    check('keyboard opens', keyboard.visible, `${keyboard.width}x${keyboard.height}`);
    const keys = descendants(keyboard, actor => actor.has_style_class_name?.('keyboard-key') && actor.mapped);
    const keyHeights = keys.map(key => key.height);
    const rows = keyboard._currentPage?.getRatio()[1] ?? 0;
    check(`Keycap rows are touch size (${tag})`, rows === 4 && Math.min(...keyHeights) >= 34,
        `rows ${rows}, cap heights ${Math.min(...keyHeights)}-${Math.max(...keyHeights)}, keyboard ${keyboard.height}`);
    check('light variant follows the Shell', keyboard.has_style_class_name('luma-keycap-light'));
    check('Enter and space carry Keycap roles', keys.some(k => k.has_style_class_name('luma-keycap-enter')) &&
        keys.some(k => k.has_style_class_name('luma-keycap-space')));
    check('top row shows ten number hints',
        descendants(keyboard, a => a.has_style_class_name?.('luma-keycap-hint') && a.mapped).length === 10);
    check('tool strip shows five tools', keyboard._tools.visible &&
        keyboard._toolButtons.filter(button => button.visible).length === 5);
    const cap = keys.find(k => k.get_label?.() === 'q' || descendants(k, a => a.text === 'q').length);
    check('caps paint the token gradient', cap && painted(cap), cap ? painted(cap) : 'no q key');
    await shot(`keycap-light-${tag}`);
    for (const panel of ['clipboard', 'cursor', 'emoji']) {
        keyboard._togglePanel(panel);
        await sleep(300);
        check(`${panel} panel replaces the letters`, keyboard._panel === panel && !keyboard._currentPage.visible);
        await shot(`keycap-${panel}-${tag}`);
        keyboard._setPanel(null);
    }
    const iface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
    iface.set_string('color-scheme', 'prefer-dark');
    await sleep(1500);
    check('dark variant follows the Shell', keyboard.has_style_class_name('luma-keycap-dark'),
        `variant ${Main.getStyleVariant()}`);
    await shot(`keycap-dark-${tag}`);
    iface.reset('color-scheme');
    const contrast = new Gio.Settings({schema_id: 'org.gnome.desktop.a11y.interface'});
    contrast.set_boolean('high-contrast', true);
    await sleep(1500);
    check('High Contrast keeps the platform keyboard', !keyboard.has_style_class_name('luma-keycap-light') &&
        !keyboard.has_style_class_name('luma-keycap-dark'));
    await shot(`keycap-high-contrast-${tag}`);
    contrast.reset('high-contrast');
    await sleep(800);
    Main.keyboard.close();
    a11y.reset('screen-keyboard-enabled');
    await sleep(800);
}

async function tabletStatusChecks() {
    const presentation = Main.presentation;
    if (!presentation)
        return;
    const settings = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});
    const treatment = GLib.getenv('KEYCAP_TREATMENT');
    if (treatment)
        settings.set_string('surface-treatment', treatment);
    settings.set_string('presentation-mode', 'tablet');
    for (let i = 0; i < 40 && presentation.mode !== 'tablet'; i++)
        await sleep(250);
    check('Tablet presentation', presentation.mode === 'tablet');
    const wm = new Gio.Settings({schema_id: 'org.gnome.desktop.wm.preferences'});
    const desktopLayout = settings.settings_schema.has_key('presentation-button-layout');
    for (let i = 0; i < 20 && desktopLayout && wm.get_string('button-layout') !== ':'; i++)
        await sleep(250);
    check('Tablet hides window controls', !desktopLayout || wm.get_string('button-layout') === ':',
        wm.get_string('button-layout'));
    presentation.showHome?.();
    await sleep(1500);
    await shot('tablet-home-status');
    // The status band over an application, where the corner must stay clear.
    Gio.DesktopAppInfo.new('org.example.SplitA.desktop')?.launch([], null);
    for (let i = 0; i < 40 && !global.display.focus_window; i++)
        await sleep(250);
    await sleep(1500);
    check('application opens in Tablet', !!global.display.focus_window);
    const monitor = Main.layoutManager.primaryMonitor;
    const frame = global.display.focus_window?.get_frame_rect();
    // Everything painting a background in the top-right status corner.
    const corner = descendants(Main.uiGroup, actor => {
        if (!actor.mapped || !(actor instanceof St.Widget))
            return false;
        const [x, y] = actor.get_transformed_position();
        const [width, height] = actor.get_transformed_size();
        return x + width > monitor.x + monitor.width * 0.6 && y < monitor.y + 60 &&
            height < monitor.height / 2 && painted(actor) !== null;
    }).map(actor => `${actor.constructor.name}.${actor.style_class ?? ''}${actor.name ? `#${actor.name}` : ''}=${painted(actor)}`);
    check('Tablet status corner paints no backgrounds', corner.length === 0,
        `${corner.join('; ')} window ${frame?.x},${frame?.y} ${frame?.width}x${frame?.height}`);
    await shot(`tablet-status-${treatment ?? 'default'}`);
    settings.reset('presentation-mode');
    settings.reset('surface-treatment');
    for (let i = 0; i < 20 && presentation.mode !== 'desktop'; i++)
        await sleep(250);
    await sleep(500);
    check('Desktop restores window controls', !desktopLayout || wm.get_string('button-layout') !== ':',
        wm.get_string('button-layout'));
}

async function work() {
    await keyboardChecks();
    if (GLib.getenv('KEYCAP_TABLET'))
        await tabletStatusChecks();
}

export function init() {
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 6000, () => {
        work().catch(error => check('scenario completed', false, `${error}\n${error.stack}`)).finally(() => {
            GLib.file_set_contents(`${out}/results.json`, JSON.stringify(results, null, 2));
            global.context.terminate();
        });
        return GLib.SOURCE_REMOVE;
    });
}
export async function run() { await new Promise(() => {}); }
