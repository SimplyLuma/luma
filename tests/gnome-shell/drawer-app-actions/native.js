// SPDX-License-Identifier: GPL-2.0-or-later
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as AppFavorites from 'resource:///org/gnome/shell/ui/appFavorites.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';
const out = GLib.getenv('LUMA_POLISH_OUTPUT'), results = [];
const pause = async (ms = 200) => await Scripting.sleep(ms);
function check(name, ok) {
    results.push({name, ok: !!ok});
    if (!ok) throw new Error(name);
    print(`PASS ${name}`);
}
async function until(predicate) {
    for (let i = 0; i < 60; i++) { if (predicate()) return; await pause(100); }
    throw new Error('Native drawer condition timed out');
}
async function shot(name) {
    const stream = Gio.File.new_for_path(`${out}/${name}.png`).replace(null, false, Gio.FileCreateFlags.NONE, null);
    await new Shell.Screenshot().screenshot(false, stream); stream.close(null);
}
async function work() {
    const folders = Main.shelf.dockFolders;
    await until(() => folders?._desktopUI?._drawer && folders._models.get('applications:///')?.loaded);
    const drawer = folders._desktopUI._drawer;
    const pointer = global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const keyboard = global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    const key = async symbol => {
        keyboard.notify_keyval(GLib.get_monotonic_time(), symbol, Clutter.KeyState.PRESSED);
        await pause(40);
        keyboard.notify_keyval(GLib.get_monotonic_time(), symbol, Clutter.KeyState.RELEASED);
        await pause();
    };
    const move = async actor => {
        const [x, y] = actor.get_transformed_position(), [w, h] = actor.get_transformed_size();
        check('target is mapped and has bounds', actor.is_mapped() && w > 0 && h > 0);
        pointer.notify_absolute_motion(GLib.get_monotonic_time(), x + w / 2, y + h / 2);
        await pause();
    };
    const click = async (actor, button = Clutter.BUTTON_PRIMARY, hold = 50) => {
        await move(actor);
        pointer.notify_button(GLib.get_monotonic_time(), button, Clutter.ButtonState.PRESSED);
        await pause(hold);
        pointer.notify_button(GLib.get_monotonic_time(), button, Clutter.ButtonState.RELEASED);
        await pause();
    };
    const id = 'org.projectluma.DrawerProbe.desktop';
    const favorites = AppFavorites.getAppFavorites();
    const original = global.settings.get_strv('favorite-apps');
    try {
        drawer.open(); await pause();
        drawer._search.set_text('Luma Drawer Probe'); await pause();
        check('real installed application has one drawer tile', drawer._tiles.length === 1);
        let tile = drawer._tiles[0];
        await click(tile, Clutter.BUTTON_SECONDARY);
        check('right click opens the native app menu', drawer._appMenu?.isOpen);
        check('right click leaves the drawer open', drawer.visible);
        check('native Pin action is visible', drawer._appMenu._toggleFavoriteItem.visible && !favorites.isFavorite(id));
        await shot('drawer-pin-menu');
        await click(drawer._appMenu._toggleFavoriteItem);
        await until(() => favorites.isFavorite(id));
        check('pointer Pin adds the actual desktop identity', global.settings.get_strv('favorite-apps').includes(id));
        await click(tile, Clutter.BUTTON_SECONDARY);
        check('native menu offers Unpin for the pinned app', drawer._appMenu._toggleFavoriteItem.label.text === 'Unpin');
        await click(drawer._appMenu._toggleFavoriteItem);
        await until(() => !favorites.isFavorite(id));
        check('pointer Unpin removes the actual favorite', !global.settings.get_strv('favorite-apps').includes(id));
        tile.grab_key_focus(); await key(Clutter.KEY_Menu);
        check('keyboard Menu opens the native app menu', drawer._appMenu?.isOpen);
        await key(Clutter.KEY_Escape);
        check('Escape dismisses only the app menu', !drawer._appMenu?.isOpen && drawer.visible);
        check('Escape returns keyboard focus to the app tile', global.stage.get_key_focus() === tile || tile.contains(global.stage.get_key_focus()));
        await key(Clutter.KEY_Return);
        print(JSON.stringify({afterEnter: {drawerVisible: drawer.visible, menuOpen: drawer._appMenu?.isOpen, suppressed: drawer._suppressClick}}));
        const launched = Shell.AppSystem.get_default().lookup_app(id);
        await until(() => launched.get_windows().length > 0);
        check('Enter still launches after dismissing the menu', !drawer.visible && launched.get_windows().length > 0);
        for (const window of launched.get_windows()) window.delete(global.get_current_time());
        await until(() => launched.get_windows().length === 0);
        drawer.open(); await pause();
        drawer._search.set_text('Luma Drawer Probe'); await pause();
        tile = drawer._tiles[0];
        await click(tile, Clutter.BUTTON_PRIMARY, 1200);
        check('pointer hold opens the native app menu', drawer._appMenu?.isOpen);
        check('hold does not launch the app', Shell.AppSystem.get_default().lookup_app(id).get_windows().length === 0);
        await key(Clutter.KEY_Escape);
        await click(tile, Clutter.BUTTON_SECONDARY);
        const oldMenu = drawer._appMenu;
        drawer._search.set_text('Luma Drawer'); await pause();
        check('search rebuild destroys the previous menu', drawer._appMenu === null && !oldMenu.isOpen);
        tile = drawer._tiles[0]; await click(tile, Clutter.BUTTON_SECONDARY);
        drawer.close(); await pause();
        check('closing drawer destroys its app menu', !drawer.visible && drawer._appMenu === null);
        check('closing drawer releases the modal grab', !drawer._menuManager.activeMenu);
        drawer.open(); await pause();
        drawer._search.set_text('Luma Drawer Probe'); await pause();
        tile = drawer._tiles[0]; await click(tile);
        const app = Shell.AppSystem.get_default().lookup_app(id);
        await until(() => app.get_windows().length > 0);
        check('ordinary primary click still launches the app', !drawer.visible && app.get_windows().length > 0);
        for (const window of app.get_windows()) window.delete(global.get_current_time());
    } finally {
        drawer.close(); global.settings.set_strv('favorite-apps', original);
    }
}
export function init() {
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 5000, () => {
        work().catch(error => { results.push({name: 'native drawer app actions complete', ok: false, detail: `${error}\n${error.stack}`}); logError(error); })
            .finally(() => { GLib.file_set_contents(`${out}/result.json`, JSON.stringify({pass: results.length > 0 && results.every(r => r.ok), results}, null, 2)); global.context.terminate(); });
        return GLib.SOURCE_REMOVE;
    });
}
