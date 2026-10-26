// SPDX-License-Identifier: Apache-2.0
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import {findItem} from '../../../src/luma-shell-presentation/grid.js';

export async function testEditor(presentation, mode, check, sleep) {
    const seat = Clutter.get_default_backend().get_default_seat();
    const touch = seat.create_virtual_device(Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);
    const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    const settings = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});
    const time = () => GLib.get_monotonic_time();
    const start = async (id, centre = false) => {
        const item = findItem(presentation._grid, id);
        presentation._settlePage(item.page, 0);
        await sleep(200);
        const actor = presentation._editor.items.get(id).actor;
        const [x, y] = actor.get_transformed_position();
        const [width, height] = actor.get_transformed_size();
        touch.notify_touch_down(time(), 0, x + width / 2, y + (centre ? height / 2 : Math.min(height / 2, 35)));
        await sleep(1000);
        check(`${mode} long press enters editing and keeps the held drag`,
            presentation._editor.editing && presentation._editor.dragging?.homeItemId === id);
        return item;
    };
    const point = (x, y) => ({
        x: (presentation._layout.inset + (x + .5) *
            presentation._layout.contentWidth / presentation._grid.columns) * presentation._scale,
        y: (presentation._layout.top + (y + .5) * presentation._layout.rowHeight) * presentation._scale,
    });
    for (const kind of ['app', 'widget']) {
        const item = presentation._grid.pages.flat().find(record => record.kind === kind);
        if (!item) throw new Error(`Missing ${kind} fixture`);
        const original = await start(item.id);
        const targetX = original.x === 0 ? 1 : 0;
        const target = point(targetX, 0);
        touch.notify_touch_motion(time(), 0, target.x, target.y);
        await sleep(150);
        touch.notify_touch_up(time(), 0);
        await sleep(600);
        const moved = findItem(presentation._grid, item.id);
        check(`${mode} native touch drop moves ${kind} into an integer cell`,
            moved.x === targetX && moved.y === 0 && moved.w === item.w && moved.h === item.h);
        const saved = JSON.parse(settings.get_string('home-layouts'))[presentation._gridKey];
        check(`${mode} ${kind} move is persisted by the shared service`,
            Boolean(saved?.pages.flat().find(record => record.id === item.id && record.x === targetX && record.y === 0)));
    }
    const widget = presentation._grid.pages.flat().find(record => record.kind === 'widget');
    const grabbed = await start(widget.id, true);
    touch.notify_touch_up(time(), 0);
    await sleep(600);
    const held = findItem(presentation._grid, widget.id);
    check(`${mode} grabbing a widget at its centre preserves the original cell`,
        held.page === grabbed.page && held.x === grabbed.x && held.y === grabbed.y);
    const id = presentation._grid.pages.at(-1)[0].id;
    await start(id);
    const before = settings.get_string('home-layouts');
    const oldPages = presentation._grid.pages.length;
    const edge = point(presentation._grid.columns - 1, 0);
    touch.notify_touch_motion(time(), 0, presentation._layout.width * presentation._scale - 3, edge.y);
    await sleep(1000);
    check(`${mode} holding at the screen edge previews a new Home page`,
        presentation._pageViews.length === oldPages + 1 && presentation._page === oldPages &&
        presentation._dots.get_n_children() > 0 &&
        presentation._dots.width <= presentation._layout.width * presentation._scale);
    keyboard.notify_keyval(time(), Clutter.KEY_Escape, Clutter.KeyState.PRESSED);
    keyboard.notify_keyval(time(), Clutter.KEY_Escape, Clutter.KeyState.RELEASED);
    touch.notify_touch_up(time(), 0);
    await sleep(600);
    check(`${mode} cancelled drag does not create a saved page or move an item`,
        settings.get_string('home-layouts') === before && presentation._grid.pages.length === oldPages);
    await start(id);
    touch.notify_touch_motion(time(), 0, presentation._layout.width * presentation._scale - 3, edge.y);
    await sleep(1000);
    touch.notify_touch_up(time(), 0);
    await sleep(600);
    check(`${mode} edge drop creates and saves the next Home page`,
        findItem(presentation._grid, id).page === oldPages && presentation._grid.pages.length === oldPages + 1);
    const saved = settings.get_string('home-layouts');
    presentation._rebuild();
    check(`${mode} rebuilding Home restores saved positions`,
        findItem(presentation._grid, id).page === oldPages && settings.get_string('home-layouts') === saved);
    // Keyboard uses the same cell transaction as touch, without launching apps.
    presentation._editor.setEditing(true);
    const selected = findItem(presentation._grid, id);
    presentation._settlePage(selected.page, 0);
    presentation._editor.items.get(id).actor.grab_key_focus();
    const key = selected.x === 0 ? Clutter.KEY_Right : Clutter.KEY_Left;
    keyboard.notify_keyval(time(), Clutter.KEY_Control_L, Clutter.KeyState.PRESSED);
    keyboard.notify_keyval(time(), key, Clutter.KeyState.PRESSED);
    keyboard.notify_keyval(time(), key, Clutter.KeyState.RELEASED);
    keyboard.notify_keyval(time(), Clutter.KEY_Control_L, Clutter.KeyState.RELEASED);
    await sleep(600);
    check(`${mode} keyboard rearranges the focused item`,
        findItem(presentation._grid, id).x === selected.x + (selected.x === 0 ? 1 : -1));
    keyboard.notify_keyval(time(), Clutter.KEY_Escape, Clutter.KeyState.PRESSED);
    keyboard.notify_keyval(time(), Clutter.KEY_Escape, Clutter.KeyState.RELEASED);
    await sleep(150);
    check(`${mode} Escape exits editing`, !presentation._editor.editing && !presentation._editor._done.visible);
    presentation._editor.setEditing(true);
    // Empty margin belongs to Home rather than an icon, widget or dock button.
    presentation._editor.items.get(id).actor.grab_key_focus();
    touch.notify_touch_down(time(), 0, 2, presentation._home.height/2);
    await sleep(80);
    touch.notify_touch_up(time(), 0); await sleep(200);
    check(`${mode} tapping blank Home exits editing without a keyboard`, !presentation._editor.editing);
    check(`${mode} blank Home tap releases the last moved icon focus`,
        global.stage.get_key_focus() !== presentation._editor.items.get(id).actor);
    presentation._settlePage(0, 0);
}
