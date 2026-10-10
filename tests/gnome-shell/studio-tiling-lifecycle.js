// SPDX-License-Identifier: GPL-2.0-or-later
// Owned disposable Shell session: native packaged extension lifecycle.
import Gio from 'gi://Gio';
import System from 'system';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {sleep} from 'resource:///org/gnome/shell/ui/scripting.js';
const uuid = 'tiling-toggle@project-luma.local';
async function until(predicate, message) {
    for (let i = 0; i < 100; i++) {
        if (predicate()) return;
        await sleep(50);
    }
    const ext = Main.extensionManager.lookup(uuid);
    throw new Error(`${message}: state=${ext?.state} errors=${JSON.stringify(ext?.errors)}`);
}
function toggle() {
    return Main.extensionManager.lookup(uuid)?.stateObj?._indicator?.quickSettingsItems?.[0];
}
export async function run() {
    new Gio.Settings({schema_id: 'org.gnome.desktop.interface'}).set_boolean('enable-animations', false);
    global.settings.set_boolean('disable-user-extensions', false);
    if (!Main.extensionManager.enableExtension('tilingshell@ferrarodomenico.com') ||
        !Main.extensionManager.enableExtension(uuid)) throw new Error('Packaged extensions missing');
    await until(() => !!toggle()?._lumaStudioRow, 'Packaged toggle did not enable and adopt');
    print('NATIVE PACKAGED TILING START');
    for (let i = 0; i < 10; i++) {
        const row = toggle()._lumaStudioRow;
        Main.extensionManager.disableExtension(uuid);
        await until(() => !toggle(), 'Packaged toggle did not disable');
        if (Main.panel.statusArea.quickSettings.menu._studioUtilities.get_children().includes(row))
            throw new Error('Destroyed toggle left a wrapper');
        Main.extensionManager.enableExtension(uuid);
        await until(() => !!toggle()?._lumaStudioRow, 'Packaged toggle did not re-enable');
    }
    for (let i = 0; i < 20; i++) {
        Main.screenShield.activate(false);
        await until(() => Main.sessionMode.currentMode === 'unlock-dialog' && !toggle(), 'Lock did not disable packaged toggle');
        await sleep(150);
        System.gc();
        Main.screenShield._continueDeactivate(false);
        await until(() => Main.sessionMode.currentMode === 'user' && !!toggle()?._lumaStudioRow, 'Unlock did not re-enable packaged toggle');
        await until(() => Main.screenShield._dialog === null, 'Dialog teardown did not finish');
        System.gc();
    }
    print('NATIVE PACKAGED TILING PASS: 10 enable/disable + 20 lock/unlock cycles');
}
