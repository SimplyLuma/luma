// SPDX-License-Identifier: GPL-2.0-or-later
// Run only in a disposable compositor with private settings and buses.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';
const out = GLib.getenv('LUMA_POLISH_OUTPUT'), results = [], observations = [];
const pause = async (ms = 200) => await Scripting.sleep(ms);
function check(name, ok) {
    results.push({name, ok: !!ok});
    if (!ok) throw new Error(name);
}
async function until(predicate) {
    for (let i = 0; i < 100; i++) { if (predicate()) return; await pause(100); }
    throw new Error('Dock scroll condition timed out');
}
function bounds(actor) {
    const [x, y] = actor.get_transformed_position();
    const [width, height] = actor.get_transformed_size();
    return {x, y, width, height, mapped: actor.mapped};
}
async function work() {
    await until(() => Main.shelf?._dockScroll && Main.shelf._dash?._box);
    const original = global.settings.get_strv('favorite-apps'), created = [];
    const pointer = global.stage.context.get_backend().get_default_seat()
        .create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    try {
        const ids = [];
        for (let i = 0; i < 32; i++) {
            const id = `org.projectluma.DockScroll${i}.desktop`;
            const path = `${GLib.getenv('XDG_DATA_HOME')}/applications/${id}`;
            if (GLib.file_test(path, GLib.FileTest.EXISTS)) throw new Error('Fixture app already exists');
            GLib.file_set_contents(path, `[Desktop Entry]\nType=Application\nName=Dock scroll ${i}\nExec=/usr/bin/true\nIcon=utilities-terminal\nTerminal=false\n`);
            created.push(path); ids.push(id);
        }
        const targetId = 'org.projectluma.DrawerProbe.desktop'; ids.push(targetId);
        await until(() => ids.every(id => Shell.AppSystem.get_default().lookup_app(id)));
        global.settings.set_strv('favorite-apps', ids);
        const shelf = Main.shelf, scroll = shelf._dockScroll;
        await until(() => shelf._dash._box.get_children().filter(c => c.child?._delegate?.app).length >= ids.length);
        await pause(1000);
        const target = shelf._dash._box.get_children().find(c => c.child?._delegate?.app?.get_id() === targetId);
        const sample = name => {
            const a = scroll.hadjustment;
            const value = {name, offset: a.value, upper: a.upper, page: a.page_size,
                step: a.step_increment, viewport: bounds(scroll), target: bounds(target)};
            observations.push(value); return value;
        };
        const first = sample('before');
        check('dock has a real overflow range', first.upper > first.page + 1);
        const viewport = bounds(scroll);
        pointer.notify_absolute_motion(GLib.get_monotonic_time(), viewport.x + viewport.width / 2,
            viewport.y + viewport.height / 2);
        await pause();
        const wheel = async (direction, count = 12) => {
            for (let i = 0; i < count; i++) {
                pointer.notify_discrete_scroll(GLib.get_monotonic_time(), direction, Clutter.ScrollSource.WHEEL);
                await pause(60);
            }
            await pause();
        };
        await wheel(Clutter.ScrollDirection.DOWN);
        const down = sample('wheel-down');
        check('ordinary wheel advances the horizontal dock', down.offset > first.offset + 1);
        check('wheel reaches the last pinned app', down.target.x >= down.viewport.x - 1 &&
            down.target.x + down.target.width <= down.viewport.x + down.viewport.width + 1);
        await wheel(Clutter.ScrollDirection.UP);
        check('reverse wheel returns to the first apps', sample('wheel-up').offset <= first.offset + 1);
        await wheel(Clutter.ScrollDirection.RIGHT, 1);
        const right = sample('one-horizontal-tick');
        check('horizontal wheel is applied once', Math.abs(right.offset - first.offset - first.step) < 1);
        pointer.notify_scroll_continuous(GLib.get_monotonic_time(), 0, 1,
            Clutter.ScrollSource.FINGER, Clutter.ScrollFinishFlags.NONE);
        await pause();
        check('smooth vertical input advances the horizontal dock', sample('smooth-down').offset > right.offset);
        await wheel(Clutter.ScrollDirection.DOWN);
        const last = bounds(target);
        pointer.notify_absolute_motion(GLib.get_monotonic_time(), last.x + last.width / 2, last.y + last.height / 2);
        await pause();
        pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
        await pause(50);
        pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
        const app = Shell.AppSystem.get_default().lookup_app(targetId);
        await until(() => app.get_windows().length > 0);
        check('pointer activates the formerly clipped pinned app', app.get_windows().length > 0);
        for (const window of app.get_windows()) window.delete(global.get_current_time());
        await until(() => app.get_windows().length === 0);
        check('activated fixture window closes normally', app.get_windows().length === 0);
    } finally {
        global.settings.set_strv('favorite-apps', original);
        for (const path of created) Gio.File.new_for_path(path).delete(null);
        await pause(); pointer.run_dispose();
    }
}
work().then(() => GLib.file_set_contents(`${out}/result.json`, JSON.stringify({pass: true, results, observations})))
    .catch(error => { logError(error); GLib.file_set_contents(`${out}/result.json`, JSON.stringify({pass: false, results, observations, error: error.message})); })
    .finally(() => global.context.terminate());
