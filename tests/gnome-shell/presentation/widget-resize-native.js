// SPDX-License-Identifier: Apache-2.0
// Run in an owned Shell session. Always restore the pre-test Home layout.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import {findItem} from '../../../src/luma-shell-presentation/grid.js';

export async function testWidgetResize(p, check, sleep, shot = async () => {}) {
    const settings = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});
    const interfaceSettings = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
    const saved = settings.get_string('home-layouts');
    const animations = interfaceSettings.get_user_value('enable-animations');
    const seat = global.stage.context.get_backend().get_default_seat();
    const touch = seat.create_virtual_device(Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);
    const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    const time = () => GLib.get_monotonic_time();
    let touching = false;
    try {
        p.showHome(); await sleep(400);
        const first = p._grid.pages.flat().find(item => item.kind === 'widget');
        if (!first) throw new Error('Widget resize test requires a real widget host');
        if (first.x || first.y || first.page) {
            p._editor.move(first.id, 0, 0, 0);
            p._queueRebuild(); await sleep(700);
        }
        const original = findItem(p._grid, first.id);
        const horizontal = original.w < p._grid.columns;
        if (!horizontal && original.h >= p._grid.rows)
            throw new Error('Widget resize test needs a spare column or row');
        const expectedW = original.w + (horizontal ? 1 : 0);
        const expectedH = original.h + (horizontal ? 0 : 1);
        const drag = async () => {
            p._editor.setEditing(true); p._editor.select(first.id);
            p._settlePage(0, 0); await sleep(160);
            const handle = p._editor._resizeHandle;
            check('resize handle is visible, named and has a44px touch target',
                handle?.visible && handle.can_focus && Boolean(handle.accessible_name) &&
                handle.width >= 44 * p._scale && handle.height >= 44 * p._scale);
            handle.grab_key_focus();
            const [x, y] = handle.get_transformed_position();
            const sx = x + handle.width / 2, sy = y + handle.height / 2;
            const rtl = Clutter.get_default_text_direction() === Clutter.TextDirection.RTL;
            const dx = horizontal ? p._layout.contentWidth / p._grid.columns * p._scale * (rtl ? -1 : 1) : 0;
            const dy = horizontal ? 0 : p._layout.rowHeight * p._scale;
            touch.notify_touch_down(time(), 0, sx, sy); touching = true;
            await sleep(50);
            for (let step = 1; step <= 8; step++) {
                touch.notify_touch_motion(time(), 0, sx + dx * step / 8, sy + dy * step / 8);
                await sleep(16);
            }
        };
        const before = settings.get_string('home-layouts');
        await drag();
        const preview = p._editor.resizing && findItem(p._editor.resizing.preview, first.id);
        check('native touch snaps a reversible preview to whole widget cells', preview?.w === expectedW && preview.h === expectedH);
        check('dragging does not write Home state', settings.get_string('home-layouts') === before);
        const neighbours = [...p._editor.items].filter(([id]) => id !== first.id).map(([, {actor}]) => actor);
        const moving = neighbours.filter(actor => Math.abs(actor.translation_x) + Math.abs(actor.translation_y) > .1);
        check('displaced neighbours animate from intermediate positions without stretching',
            moving.length > 0 && moving.every(actor => actor.scale_x === 1 && actor.scale_y === 1));
        const positions = moving.map(actor => actor.get_transformed_position());
        await sleep(60);
        check('neighbours make visible progress while the finger is still held',
            moving.some((actor, i) => actor.get_transformed_position().some((coordinate, axis) => Math.abs(coordinate - positions[i][axis]) > .1)));
        await shot('widget-resize-preview');
        keyboard.notify_keyval(time(), Clutter.KEY_Escape, Clutter.KeyState.PRESSED);
        keyboard.notify_keyval(time(), Clutter.KEY_Escape, Clutter.KeyState.RELEASED);
        touch.notify_touch_up(time(), 0); touching = false;
        await sleep(700);
        check('Escape restores the original layout and clears the gesture',
            !p._editor.resizing && !p._editor.editing &&
            settings.get_string('home-layouts') === before && findItem(p._grid, first.id).w === original.w);
        await drag(); touch.notify_touch_up(time(), 0); touching = false;
        await sleep(900);
        const resized = findItem(p._grid, first.id);
        const stored = JSON.parse(settings.get_string('home-layouts'))[p._gridKey]?.pages.flat().find(item => item.id === first.id);
        check('touch release persists the new span through the real state service',
            resized.w === expectedW && resized.h === expectedH && stored?.w === expectedW && stored.h === expectedH);
        p._rebuild(); await sleep(200);
        check('rebuilding Home retains the chosen widget size', findItem(p._grid, first.id).w === expectedW);
        await shot('widget-resize-saved');
        // Exercise the same transaction with keyboard and reduced motion.
        interfaceSettings.set_boolean('enable-animations', false); await sleep(150);
        p._editor.setEditing(true); p._editor.select(first.id);
        p._editor._resizeHandle.grab_key_focus();
        const key = horizontal
            ? (Clutter.get_default_text_direction() === Clutter.TextDirection.RTL ? Clutter.KEY_Right : Clutter.KEY_Left)
            : Clutter.KEY_Up;
        keyboard.notify_keyval(time(), key, Clutter.KeyState.PRESSED);
        keyboard.notify_keyval(time(), key, Clutter.KeyState.RELEASED);
        await sleep(750);
        check('focused resize handle supports keyboard with reduced motion',
            findItem(p._grid, first.id).w === original.w && findItem(p._grid, first.id).h === original.h &&
            !p._editor.items.get(first.id).actor.get_transition('width'));
        // A refused asynchronous save must not replace the authoritative grid.
        const save = p._saveHomeGrid;
        const beforeFailure = settings.get_string('home-layouts');
        try {
            p._saveHomeGrid = (_grid, _saved, failed) => {
                GLib.timeout_add_once(GLib.PRIORITY_DEFAULT, 50, failed);
            };
            p._editor.setEditing(true); p._editor.select(first.id);
            p._editor.beginResize(first.id); p._editor.previewResize(expectedW, expectedH);
            p._editor.finishResize(true); await sleep(350);
            check('asynchronous save refusal rolls back without losing or overwriting layout',
                !p._editor.savingResize && findItem(p._grid, first.id).w === original.w &&
                settings.get_string('home-layouts') === beforeFailure);
        } finally { p._saveHomeGrid = save; }
        check('resize leaves native animation preference under user control', St.Settings.get().enable_animations === false);
    } finally {
        if (touching) touch.notify_touch_up(time(), 0);
        p._editor.setEditing(false);
        settings.set_string('home-layouts', saved);
        if (animations) interfaceSettings.set_value('enable-animations', animations);
        else interfaceSettings.reset('enable-animations');
        p._refresh(); await sleep(650); p._rebuild();
        touch.run_dispose(); keyboard.run_dispose();
    }
}
