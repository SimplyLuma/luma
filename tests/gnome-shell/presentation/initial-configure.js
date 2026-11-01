// SPDX-License-Identifier: Apache-2.0
// Native-session policy checks; real cold-launch geometry is measured separately.
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

function run() {
    const results = [];
    const check = (name, ok) => {
        results.push({name, ok: Boolean(ok)});
        if (!ok) throw new Error(name);
    };
    try {
        check('unlocked native session', !Main.sessionMode.isLocked && !Main.sessionMode.isGreeter);
        const configure = Main.presentation._configureMobileWindow;
        check('initial placement hook installed', typeof configure === 'function');
        const monitor = Main.layoutManager.primaryMonitor;
        for (const [mode, transient, managed, expected] of [
            ['desktop', false, true, false], ['tablet', false, true, false],
            ['mobile', true, true, false], ['mobile', false, false, false],
            ['mobile', false, true, true],
        ]) {
            const writes = {};
            const context = {mode, _manage: () => managed};
            const window = {get_transient_for: () => transient ? {} : null,
                get_monitor: () => Main.layoutManager.primaryIndex};
            configure.call(context, window, {
                set_is_fullscreen: value => {writes.fullscreen = value;},
                set_position: (x, y) => {writes.position = [x, y];},
                set_size: (width, height) => {writes.size = [width, height];},
            });
            check(`${mode}/${transient}/${managed}: placement scope`, expected ?
                writes.fullscreen === true && writes.size[0] === monitor.width &&
                writes.size[1] === monitor.height && writes.position[0] === monitor.x &&
                writes.position[1] === monitor.y : Object.keys(writes).length === 0);
        }
        // The prepared runtime owns realized, deliberately unmapped windows.
        // They may still receive an initial configure if presented later.
        check('mapped windows release configure hooks', [...Main.presentation._initialWindows.keys()]
            .every(window => !window.get_compositor_private()?.mapped));
    } catch (error) {
        results.push({error: String(error.stack ?? error)});
    } finally {
        GLib.mkdir_with_parents(`${GLib.get_user_state_dir()}/project-titan`, 0o700);
        GLib.file_set_contents(`${GLib.get_user_state_dir()}/project-titan/initial-configure.json`,
            JSON.stringify(results));
    }
}
GLib.timeout_add_once(GLib.PRIORITY_DEFAULT, 2400, run);
