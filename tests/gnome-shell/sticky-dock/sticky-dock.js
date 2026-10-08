// SPDX-License-Identifier: GPL-2.0-or-later
// The sticky note stack is drawn only while Sticky Notes is installed (0163).
// Starts with the application absent: no stack and no error. Installs its
// desktop entry: the stack appears. Removes it: the stack goes. Then the same
// with only a D-Bus service file (activatable name, no desktop entry).
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const OUT = GLib.getenv('SD_OUT') ?? '/tmp/sticky-dock';
const results = [];
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const log = m => console.log(`[stickydock] ${m}`);
const check = (name, ok, detail = '') => { results.push({name, ok: !!ok, detail}); log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`); };
const home = GLib.get_home_dir();
const desktop = `${home}/.local/share/applications/org.projectluma.StickyNotes.desktop`;
const service = `${GLib.get_user_data_dir()}/dbus-1/services/org.projectluma.StickyNotes.service`;
const onStage = () => Main.uiGroup.get_children().filter(a => a.has_style_class_name?.('luma-sticky-dock'));
const waitFor = async (fn, ms = 15000) => { for (let i = 0; i < ms; i += 250) { if (fn()) return true; await sleep(250); } return !!fn(); };
const write = (path, text) => { GLib.mkdir_with_parents(GLib.path_get_dirname(path), 0o755); GLib.file_set_contents(path, text); };

async function work() {
    await sleep(3000);
    const manager = Main.stickyDockManager;
    check('the shell has a sticky dock manager', !!manager);
    check('absent: no stack is drawn', Main.stickyDock === null && onStage().length === 0, `${onStage().length} on stage`);

    // The app system says installed-changed when its file monitor sees the
    // entry. Some containers have no file monitoring at all; there the test
    // raises the same signal itself, and says so.
    const appeared = () => Main.stickyDock && onStage().length === 1 && Main.stickyDock.mapped;
    const gone = () => Main.stickyDock === null && onStage().length === 0;
    const changed = async (fn, what) => {
        if (await waitFor(fn, 12000))
            return 'by the app system';
        log(`no installed-changed after ${what} (no file monitoring here): raising it`);
        Shell.AppSystem.get_default().emit('installed-changed');
        return await waitFor(fn, 5000) ? 'by a raised installed-changed' : null;
    };
    write(desktop, '[Desktop Entry]\nType=Application\nName=Sticky Notes\nExec=true\nIcon=accessories-text-editor\n');
    let how = await changed(appeared, 'installing');
    check('installed: the stack appears on installed-changed', !!how, `${how}, ${onStage().length} on stage`);
    GLib.unlink(desktop);
    how = await changed(gone, 'removing');
    check('removed: the stack goes on installed-changed', !!how, `${how}, ${onStage().length} on stage`);

    write(service, '[D-BUS Service]\nName=org.projectluma.StickyNotes\nExec=/bin/false\n');
    Gio.DBus.session.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'ReloadConfig',
        null, null, Gio.DBusCallFlags.NONE, -1, null);
    await sleep(500);
    await manager.check();
    check('activatable only: the stack appears', !!Main.stickyDock && onStage().length === 1, `${onStage().length} on stage`);
    GLib.unlink(service);
    Gio.DBus.session.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'ReloadConfig',
        null, null, Gio.DBusCallFlags.NONE, -1, null);
    await sleep(500);
    await manager.check();
    check('activatable name gone: the stack goes', Main.stickyDock === null && onStage().length === 0, `${onStage().length} on stage`);
}

export function init() {
    GLib.mkdir_with_parents(OUT, 0o755);
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 6000, () => {
        work().catch(e => check('scenario ran without errors', false, `${e}\n${e.stack}`)).finally(() => {
            GLib.file_set_contents(`${OUT}/sticky-dock.json`, JSON.stringify(results, null, 1));
            log(`DONE ${results.filter(r => r.ok).length}/${results.length} passed`);
            global.context.terminate();
        });
        return GLib.SOURCE_REMOVE;
    });
}
export async function run() { await new Promise(() => {}); }
