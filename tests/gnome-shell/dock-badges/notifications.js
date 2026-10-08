// SPDX-License-Identifier: GPL-2.0-or-later
// What an app's Notifications page actually controls, per app and per
// notification path. For each app it posts a notification the way that app
// posts one, then reads back what the Shell did with it under each switch.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {wait, writeJson, test} from 'file:///oracle/t/lib.js';

const APP_SCHEMA = 'org.gnome.desktop.notifications.application';
const APP_PATH = '/org/gnome/desktop/notifications/application/';

// desktop id -> how that app posts: 'fdo' (org.freedesktop.Notifications with
// a desktop-entry hint) or 'gtk' (org.gtk.Notifications from the app itself).
const APPS = [
    ['org.projectluma.Messages', 'fdo'],
    ['org.projectluma.Phone', 'fdo'],
    ['org.projectluma.Calendar', 'fdo'],
    ['org.projectluma.Clock', 'fdo'],
    ['org.projectluma.Connect', 'fdo'],
    ['org.projectluma.Weather', 'gtk'],
    ['org.projectluma.Tide', 'gtk'],
    ['org.projectluma.Depot', 'gtk'],
];

function settingsFor(appId) {
    // Settings canonicalizes the id the same way for the schema path.
    const canonical = appId.replace(/[^A-Za-z0-9-]/g, '-').toLowerCase();
    return new Gio.Settings({schema_id: APP_SCHEMA, path: `${APP_PATH}${canonical}/`});
}

// The stand-in service posts it, because the Shell owns both notification
// services and a synchronous call to itself would deadlock.
const post = (appId, path, title) => test('Notify', appId, path, title);

function sourceFor(appId) {
    return Main.messageTray.getSources().find(source => {
        const app = source.app ?? source._app;
        return app?.get_id?.() === `${appId}.desktop`;
    });
}

const listed = (appId, title) => {
    const source = sourceFor(appId);
    return !!source?.notifications?.some(n => n.title === title);
};
const bannerShowing = () => !!Main.messageTray._banner;

export default async function ({shot}) {
    const results = [];
    const expect = (name, ok, detail = '') => results.push({name, ok: !!ok, detail: String(detail)});
    for (const [appId, path] of APPS) {
        const settings = settingsFor(appId);
        for (const key of ['enable', 'show-banners'])
            settings.reset(key);
        await wait(300);

        // Everything on: the notification banners and joins the list.
        post(appId, path, 'on');
        await wait(900);
        expect(`${appId}: appears with the switches on`, listed(appId, 'on'));
        expect(`${appId}: banners with the switches on`, bannerShowing());
        Main.messageTray._banner?.close?.();
        await wait(600);

        // Show Banners off: it still joins the list, with no banner.
        settings.set_boolean('show-banners', false);
        await wait(300);
        post(appId, path, 'no-banner');
        await wait(900);
        expect(`${appId}: Show Banners off keeps it in the list`, listed(appId, 'no-banner'));
        expect(`${appId}: Show Banners off shows no banner`, !bannerShowing(), bannerShowing());
        settings.set_boolean('show-banners', true);
        await wait(300);

        // Notifications off: nothing at all.
        settings.set_boolean('enable', false);
        await wait(300);
        post(appId, path, 'off');
        await wait(900);
        expect(`${appId}: Notifications off drops it`, !listed(appId, 'off'));
        expect(`${appId}: Notifications off shows no banner`, !bannerShowing());
        settings.set_boolean('enable', true);
        await wait(300);

        // The lock-screen switch is the policy the Shell reads when locked.
        const source = sourceFor(appId);
        expect(`${appId}: its notifications are its own`, !!source, source ? '' : 'no source for this app');
        if (source) {
            expect(`${appId}: the lock-screen switch reaches its policy`,
                source.policy?.showInLockScreen === settings.get_boolean('show-in-lock-screen'),
                `${source.policy?.showInLockScreen}`);
        }
        source?.destroy?.();
        await wait(300);
    }
    writeJson('notifications', results);
    const failed = results.filter(r => !r.ok);
    console.log(`[badges] notifications ${results.length - failed.length}/${results.length} passed`);
    for (const r of failed)
        console.log(`[badges] FAIL ${r.name}: ${r.detail}`);
    await shot('notifications-end', 0, 0, Math.min(1920, global.stage.width), 200);
}
