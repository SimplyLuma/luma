// SPDX-License-Identifier: GPL-2.0-or-later
// Real compiled Shell actors/window tracker and isolated favorite settings.
// GTK client and desktop metadata are explicit test fixtures, with executable
// launchers. Viola can use the admitted package in an isolated test namespace.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as AppFavorites from 'resource:///org/gnome/shell/ui/appFavorites.js';
import * as AppMenu from 'resource:///org/gnome/shell/ui/appMenu.js';
import * as DND from 'resource:///org/gnome/shell/ui/dnd.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';
function check(value, text) { if (!value) throw new Error(text); print(`PASS native pin: ${text}`); }
async function waitFor(test, label) {
    for (let attempt = 0; attempt < 120; attempt++) {
        const value = test(); if (value) return value;
        await Scripting.sleep(200);
    }
    throw new Error(`timed out: ${label}`);
}
async function menuAndDrop(app, expected, persist) {
    check(app.get_id() === expected && !app.is_window_backed(), `tracked real window belongs to ${expected}`);
    check(app.app_info?.should_show(), 'tracked launcher is a visible actual desktop application');
    const favorites = AppFavorites.getAppFavorites();
    if (favorites.isFavorite(expected)) favorites.removeFavorite(expected);
    await Scripting.sleep(300);
    const dash = Main.shelf._dash;
    const source = await waitFor(() => dash._box.get_children()
        .map(child => child.child?._delegate).find(icon => icon?.app?.get_id() === expected), 'actual running Dash icon');
    const menu = new AppMenu.AppMenu(source, St.Side.TOP, {favoritesSection: true});
    if (!menu.actor.get_parent()) Main.uiGroup.add_child(menu.actor);
    menu.setApp(app);
    menu.open();
    await Scripting.sleep(300);
    check(menu._toggleFavoriteItem.visible && menu._toggleFavoriteItem.mapped,
        'actual context-menu Pin to Dash row is mapped');
    check(menu._toggleFavoriteItem.label.text === 'Pin to Dash', 'unpinned menu offers Pin to Dash');
    menu._toggleFavoriteItem.emit('activate', null);
    await Scripting.sleep(300);
    check(favorites.isFavorite(expected), 'activating actual menu row saves visible launcher');
    menu.open();
    check(menu._toggleFavoriteItem.label.text === 'Unpin', 'favorite menu changes to Unpin');
    menu._toggleFavoriteItem.emit('activate', null);
    await Scripting.sleep(300);
    check(!favorites.isFavorite(expected), 'actual Unpin removes favorite');
    const liveSource = await waitFor(() => dash._box.get_children()
        .map(child => child.child?._delegate).find(icon => icon?.app?.get_id() === expected), 'unfavorite running icon');
    const result = dash.handleDragOver(liveSource, liveSource, 1, dash.height / 2, 0);
    check(result !== DND.DragMotionResult.NO_DROP && !!dash._dragPlaceholder,
        'actual running AppIcon creates native Dash drop placeholder');
    check(dash.acceptDrop(liveSource, liveSource, 1, dash.height / 2, 0), 'native Dash accepts running app drop');
    await Scripting.sleep(300);
    check(favorites.isFavorite(expected), 'native drop persists canonical launcher favorite');
    dash._clearDragPlaceholder();
    const again = dash._box.get_children().map(child => child.child?._delegate)
        .find(icon => icon?.app?.get_id() === expected);
    dash.handleDragOver(again, again, 1, dash.height / 2, 0);
    check(dash.acceptDrop(again, again, 1, dash.height / 2, 0), 'repeated native drop accepted');
    await Scripting.sleep(300);
    dash._clearDragPlaceholder();
    check(global.settings.get_strv('favorite-apps').filter(id => id === expected).length === 1,
        'repeated drop deduplicates saved favorite ID');
    favorites.reload();
    check(favorites.isFavorite(expected), 'favorite survives real AppFavorites reload');
    if (persist) {
        const proc = Gio.Subprocess.new(['gsettings', 'get', 'org.gnome.shell', 'favorite-apps'], Gio.SubprocessFlags.STDOUT_PIPE);
        const [, output] = proc.communicate_utf8(null, null);
        check(proc.get_successful() && output.includes(expected), 'independent process reads persisted keyfile favorite');
    }
    menu.destroy();
}
export async function run({includeInstalledViola = true} = {}) {
    const original = global.settings.get_strv('favorite-apps');
    Main.overview.hide();
    const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.STDOUT_SILENCE});
    launcher.setenv('WAYLAND_DISPLAY', GLib.getenv('WAYLAND_DISPLAY') || 'wayland-0', true);
    launcher.setenv('GDK_BACKEND', 'wayland', true);
    print(`Candidate client environment DISPLAY=${GLib.getenv('DISPLAY')} WAYLAND=${GLib.getenv('WAYLAND_DISPLAY')}`);
    const fixture = launcher.spawnv(['/usr/bin/python3', '/var/tmp/luma-auth-native-20261005/pin-fixture.py']);
    try {
        const window = await waitFor(() => global.get_window_actors().map(actor => actor.meta_window)
            .find(win => win.get_gtk_application_id() === 'org.example.CreatorPin.Integration'), 'real GTK fixture window');
        print(`Native fixture metadata GTK=${window.get_gtk_application_id()} WM=${window.get_wm_class()}`);
        const app = Shell.WindowTracker.get_default().get_window_app(window);
        await menuAndDrop(app, 'luma-creator-pin-fixture.desktop', true);
        if (includeInstalledViola) {
        launcher.unsetenv('LD_LIBRARY_PATH');
        launcher.unsetenv('GI_TYPELIB_PATH');
        launcher.set_stderr_file_path('/var/tmp/luma-auth-native-20261005/viola-native-stderr.log');
        const violaCommand = GLib.getenv('LUMA_PIN_VIOLA_EXEC') || '/usr/bin/viola-browser';
        const viola = launcher.spawnv([violaCommand, 'about:blank']);
        try {
            const violaWindow = await waitFor(() => global.get_window_actors().map(actor => actor.meta_window)
                .find(win => win.get_gtk_application_id() === 'org.projectluma.Viola.NativeIntegration'), 'real installed Viola window');
            print(`Native Viola metadata GTK=${violaWindow.get_gtk_application_id()} WM=${violaWindow.get_wm_class()}`);
            await menuAndDrop(Shell.WindowTracker.get_default().get_window_app(violaWindow), 'viola-browser.desktop', true);
        } catch (error) {
            for (const actor of global.get_window_actors()) {
                const win = actor.meta_window;
                print(`Window diagnostics title=${win.title} GTK=${win.get_gtk_application_id()} WM=${win.get_wm_class()}`);
            }
            throw error;
        } finally { viola.send_signal(15); }
        } else print("SCOPE native pin: GTK fixture only; installed old Viola engine failure retained separately");
    } finally {
        fixture.force_exit();
        global.settings.set_strv('favorite-apps', original);
    }
    print('NATIVE PIN COMPLETE');
}
