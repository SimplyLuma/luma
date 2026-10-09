// SPDX-License-Identifier: GPL-2.0-or-later
// Run only in a disposable compositor with private settings and buses.
import Gio from 'gi://Gio';
import Shell from 'gi://Shell';
import * as AppFavorites from 'resource:///org/gnome/shell/ui/appFavorites.js';
import * as ParentalControlsManager from 'resource:///org/gnome/shell/misc/parentalControlsManager.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

export async function run() {
    const settings = new Gio.Settings({schema_id: 'org.gnome.shell'});
    const original = settings.get_strv('favorite-apps');
    const favorites = AppFavorites.getAppFavorites();
    const before = favorites._getIds();
    const system = Shell.AppSystem.get_default();
    const app = system.get_installed().map(info => system.lookup_app(info.get_id()))
        .filter(candidate => candidate !== null)
        .find(candidate => !before.includes(candidate.get_id()) &&
            ParentalControlsManager.getDefault().shouldShowApp(candidate.app_info));
    if (!app) throw new Error('An installed, unpinned application is required');
    const source = MessageTray.getSystemSource();
    let notifications = 0;
    const signal = source.connect('notification-added', () => notifications++);
    try {
        const position = Math.min(1, before.length);
        favorites.addFavoriteAtPos(app.get_id(), position);
        await Scripting.sleep(200);
        const expected = [...before];
        expected.splice(position, 0, app.get_id());
        if (JSON.stringify(favorites._getIds()) !== JSON.stringify(expected))
            throw new Error(`Pin order mismatch: ${JSON.stringify({expected, actual: favorites._getIds(), raw: global.settings.get_strv('favorite-apps')})}`);
        favorites.removeFavorite(app.get_id());
        await Scripting.sleep(200);
        if (JSON.stringify(favorites._getIds()) !== JSON.stringify(before))
            throw new Error('Unpinning must preserve every other favorite');
        if (notifications !== 0)
            throw new Error(`Routine pin/unpin emitted ${notifications} system notifications`);
        print(`QUIET_PIN_PASS ${JSON.stringify({app: app.get_id(), position, notifications,
            favoriteOrderPreserved: true})}`);
    } finally {
        source.disconnect(signal);
        settings.set_strv('favorite-apps', original);
    }
}
