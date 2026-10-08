// SPDX-License-Identifier: GPL-2.0-or-later
// The Dash menu (right-click an island) is a kit menu: the same font, sizes,
// weights, row heights, rule and colours as the dock's app menus, in all four
// modes, and it says "Dash Settings…". Crops of both menus are saved side by
// side for each mode.
import Clutter from 'gi://Clutter';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as BoxPointer from 'resource:///org/gnome/shell/ui/boxpointer.js';
import * as L from './lib.js';

function rows(menu) {
    return L.descend(menu.box).filter(a => a.visible && a.mapped && a.has_style_class_name?.('popup-menu-item'));
}

function metrics(menu) {
    const all = rows(menu);
    const row = all.find(i => !i.has_style_class_name('luma-menu-title') &&
        !i.has_style_class_name('popup-separator-menu-item') && i.reactive);
    const label = L.descend(row).find(c => c instanceof St.Label);
    const font = label.get_theme_node().get_font();
    const rule = all.find(i => i.has_style_class_name('popup-separator-menu-item'));
    return {
        rowHeight: Math.round(row.height),
        family: font.get_family(),
        size: Math.round(font.get_size() / 1024),
        weight: font.get_weight(),
        ink: label.get_theme_node().get_foreground_color().to_string(),
        background: menu.box.get_theme_node().get_background_color().to_string(),
        rule: rule ? Math.round(rule.height) : null,
    };
}

async function work() {
    const shelf = await L.waitForShelf();
    await L.sleep(1000);
    for (const mode of ['dark', 'light', 'frost', 'glass']) {
        await L.setMode(mode);
        // The Dash menu on the clock.
        const clock = L.rectOf(shelf.partActorFor('clock'));
        await L.move(...L.centre(clock), 120);
        await L.press(Clutter.BUTTON_SECONDARY);
        await L.release(Clutter.BUTTON_SECONDARY);
        await L.sleep(900);
        const dash = shelf._arrange._menu;
        const labels = dash ? rows(dash).map(i => i.label?.text).filter(Boolean) : [];
        L.check(`${mode}: the Dash menu reads Dash, Arrange Islands…, Reset Island Layout, Dash Settings…`,
            JSON.stringify(labels) === JSON.stringify(['Dash', 'Arrange Islands…', 'Reset Island Layout', 'Dash Settings…']), JSON.stringify(labels));
        const dm = dash && metrics(dash);
        const titleRow = dash && rows(dash).find(i => i.has_style_class_name('luma-menu-title'));
        L.check(`${mode}: the Dash menu title takes the rows' ink`, titleRow &&
            titleRow.label.get_theme_node().get_foreground_color().to_string() === dm.ink,
            `${titleRow?.label.get_theme_node().get_foreground_color().to_string()} vs ${dm?.ink}`);
        const dashRect = dash && L.rectOf(dash.actor);
        await L.shot(`menu-${mode}-dash`, {x: dashRect.x - 10, y: dashRect.y - 10, width: dashRect.width + 20, height: dashRect.height + 20});
        dash?.close(BoxPointer.PopupAnimation.NONE);
        await L.sleep(500);
        // A dock app menu (the kit).
        const item = Main.overview.dash._box.get_children().find(c => c.child?._delegate?.app);
        const icon = item.child;
        const r = L.rectOf(icon);
        await L.move(...L.centre(r), 120);
        await L.press(Clutter.BUTTON_SECONDARY);
        await L.release(Clutter.BUTTON_SECONDARY);
        await L.sleep(900);
        const app = icon._menu;
        const am = app && metrics(app);
        const appRect = app && L.rectOf(app.actor);
        if (appRect)
            await L.shot(`menu-${mode}-app`, {x: appRect.x - 10, y: appRect.y - 10, width: appRect.width + 20, height: appRect.height + 20});
        app?.close(BoxPointer.PopupAnimation.NONE);
        await L.sleep(500);
        L.log(`${mode}: dash ${JSON.stringify(dm)} app ${JSON.stringify(am)}`);
        for (const key of ['rowHeight', 'family', 'size', 'weight', 'ink', 'background', 'rule'])
            L.check(`${mode}: Dash menu ${key} matches the app menu`, dm && am && dm[key] === am[key], `${dm?.[key]} vs ${am?.[key]}`);
    }
    await L.setMode('dark');
}
export function init() { L.start('menus', work); }
export async function run() { await new Promise(() => {}); }
