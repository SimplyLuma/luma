// SPDX-License-Identifier: GPL-2.0-or-later
// Capture thumbnail oracle: throws, drags and drops the floating thumbnail with
// a virtual pointer, opens its menu and runs every item, and uses its keys, in
// a headless Shell. Cases are chosen with CT_ONLY (comma separated); each
// result is logged as "[ct] <case> {...}" with a pass flag, and all of them are
// written to results.json.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

const OUT = GLib.getenv('ORACLE_OUT');
const HARNESS = GLib.getenv('CT_HARNESS');
const CAPTURE_HARNESS = GLib.getenv('CT_CAPTURE_HARNESS');
const log = (m, o) => console.log(`[ct] ${m} ${JSON.stringify(o ?? {})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
    r();
    return GLib.SOURCE_REMOVE;
}));
const rect = a => {
    const [x, y] = a.get_transformed_position();
    const [w, h] = a.get_transformed_size();
    return {x, y, w, h};
};
const exists = path => GLib.file_test(path, GLib.FileTest.EXISTS);
const readText = path => exists(path) ? new TextDecoder().decode(GLib.file_get_contents(path)[1]) : '';
const readLines = path => readText(path).split('\n').filter(Boolean);

export default async function ({Main, shot}) {
    const Capture = await import('resource:///org/gnome/shell/ui/lumaCapture.js');
    await sleep(3000);
    if (Main.actionMode === 0) {
        const dummy = new St.Widget();
        Main.uiGroup.add_child(dummy);
        Main.popModal(Main.pushModal(dummy));
        dummy.destroy();
    }
    const ui = Main.screenshotUI;
    const t = ui._thumbnail;
    const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
    const mon = Main.layoutManager.primaryMonitor;
    const theme = GLib.getenv('CT_THEME') || 'light';
    const only = (GLib.getenv('CT_ONLY') || '').split(',').filter(Boolean);
    const results = {theme, cases: {}};

    const seat = Clutter.get_default_backend().get_default_seat();
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    const now = () => GLib.get_monotonic_time();
    const move = async (x, y, wait = 120) => {
        pointer.notify_absolute_motion(now(), x, y);
        await sleep(wait);
    };
    const press = (b = Clutter.BUTTON_PRIMARY) => pointer.notify_button(now(), b, Clutter.ButtonState.PRESSED);
    const release = (b = Clutter.BUTTON_PRIMARY) => pointer.notify_button(now(), b, Clutter.ButtonState.RELEASED);
    const clickAt = async (x, y, wait = 700, b = Clutter.BUTTON_PRIMARY) => {
        await move(x, y, 250);
        press(b);
        await sleep(60);
        release(b);
        await sleep(wait);
    };
    const click = async (actor, wait = 700, b) => {
        const r = rect(actor);
        await clickAt(r.x + r.w / 2, r.y + r.h / 2, wait, b);
    };
    const key = async (keyvals, wait = 500) => {
        for (const kv of keyvals) {
            keyboard.notify_keyval(now(), kv, Clutter.KeyState.PRESSED);
            await sleep(30);
        }
        for (const kv of [...keyvals].reverse()) {
            keyboard.notify_keyval(now(), kv, Clutter.KeyState.RELEASED);
            await sleep(30);
        }
        await sleep(wait);
    };
    const typeText = async text => {
        for (const ch of text)
            await key([Clutter.unicode_to_keysym(ch.codePointAt(0))], 40);
    };
    const snap = async (name, actors, pad = 36) => {
        const rs = actors.filter(a => a?.visible).map(rect);
        const x1 = Math.max(0, Math.min(...rs.map(r => r.x)) - pad * scale);
        const y1 = Math.max(0, Math.min(...rs.map(r => r.y)) - pad * scale);
        const x2 = Math.min(global.stage.width, Math.max(...rs.map(r => r.x + r.w)) + pad * scale);
        const y2 = Math.min(global.stage.height, Math.max(...rs.map(r => r.y + r.h)) + pad * scale);
        Capture.setPaintInCaptures(true);
        try {
            await shot(name, x1, y1, x2 - x1, y2 - y1);
        } finally {
            Capture.setPaintInCaptures(false);
        }
        return `${OUT}/${name}.png`;
    };
    const waitFor = async (fn, ms = 12000) => {
        for (let i = 0; i < ms; i += 100) {
            const v = fn();
            if (v)
                return v;
            await sleep(100);
        }
        return null;
    };
    const visible = () => t.visible && !t._leaving;
    const reset = async () => {
        t.menu?.close();
        if (ui.visible)
            ui.close(true);
        t.dismiss();
        global.stage.set_key_focus(null);
        await move(mon.x + mon.width * 0.5, mon.y + 40, 700);
    };
    // A screenshot of the whole screen and its thumbnail, with the drag
    // helper over it.
    const shoot = async () => {
        t.dismiss();
        await sleep(400);
        await ui.open(0, 'screen');
        await sleep(700);
        await click(ui._bar.captureButton, 600);
        const active = await waitFor(() => t.dragBridge.active && visible());
        await sleep(300);
        return {active: !!active, path: t.file?.get_path() ?? null};
    };
    const centre = () => {
        const r = rect(t);
        return [r.x + r.w / 2, r.y + r.h * 0.4];
    };
    const run = async (name, fn) => {
        if (only.length && !only.includes(name))
            return;
        log('case', {name});
        try {
            results.cases[name] = await fn();
        } catch (e) {
            results.cases[name] = {pass: false, error: `${e}`};
            log('error', {name, error: `${e}`, stack: `${e.stack}`.split('\n').slice(0, 6)});
        }
        log(name, results.cases[name]);
        await reset();
    };
    const menuItems = menu => menu._getMenuItems().filter(i => i.label).map(i => i.label.text);
    const findItem = (menu, text) => menu._getMenuItems().find(i => i.label?.text === text);
    const openMenu = async () => {
        const [x, y] = centre();
        await clickAt(x, y, 700, Clutter.BUTTON_SECONDARY);
        return t.menu?.isOpen ? t.menu : null;
    };
    const chooseItem = async text => {
        const menu = await openMenu();
        if (!menu)
            throw new Error('menu did not open');
        const item = findItem(menu, text);
        if (!item)
            throw new Error(`no ${text} in ${menuItems(menu)}`);
        await click(item, 900);
    };
    // The bytes are only valid inside the callback.
    const clipboard = mime => new Promise(resolve => St.Clipboard.get_default().get_content(St.ClipboardType.CLIPBOARD,
        mime, (_c, bytes) => resolve(bytes ? {size: bytes.get_size(), text: new TextDecoder().decode(bytes.toArray())} : null)));
    const fakebus = () => readLines(`${OUT}/fakebus.jsonl`).map(l => JSON.parse(l));

    await move(mon.x + mon.width * 0.5, mon.y + 40, 300);

    // Thrown fast toward the right edge: the thumbnail goes, the file stays.
    await run('fling', async () => {
        const s = await shoot();
        const [x, y] = centre();
        await move(x, y, 400);
        press();
        await sleep(90);
        const path = [];
        for (let i = 1; i <= 8; i++) {
            await move(x + i * 32, y + i * 1.5, 16);
            path.push(global.get_pointer()[0]);
        }
        release();
        await sleep(120);
        const midway = {translationX: t.translation_x, opacity: t.opacity};
        await sleep(700);
        const out = {shot: s, path, midway, visibleAfter: t.visible, fileKept: s.path ? exists(s.path) : false};
        out.pass = s.active && !t.visible && out.fileKept;
        return out;
    });

    // A slow drag and a short quick one both spring back.
    for (const [name, steps, step, wait] of [['slow', 14, 5, 100], ['short', 3, 8, 16]]) {
        await run(name, async () => {
            const s = await shoot();
            const [x, y] = centre();
            await move(x, y, 400);
            press();
            await sleep(90);
            for (let i = 1; i <= steps; i++)
                await move(x + i * step, y, wait);
            release();
            await sleep(900);
            const out = {shot: s, visible: visible(), opacity: t.opacity, fileKept: s.path ? exists(s.path) : false};
            out.pass = s.active && out.visible && out.opacity === 255 && out.fileKept;
            return out;
        });
    }

    // Dropped into an app (droptarget.js): the app gets the file.
    await run('drop', async () => {
        const logPath = `${OUT}/drop-gtk4.log`;
        const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.NONE});
        launcher.setenv('GDK_BACKEND', 'wayland', true);
        launcher.spawnv(['gjs', '-m', `${CAPTURE_HARNESS}/droptarget.js`, logPath, 'gtk4']);
        const win = await waitFor(() => global.get_window_actors().map(a => a.metaWindow)
            .find(w => w.get_title()?.startsWith('gtk4')), 20000);
        if (!win)
            return {pass: false, error: 'no drop target window'};
        win.move_resize_frame(false, mon.x + 200, mon.y + 150, 900, 600);
        win.activate(global.get_current_time());
        await sleep(1500);
        const s = await shoot();
        const f = win.get_frame_rect();
        const [x, y] = centre();
        await move(x, y, 400);
        press();
        await sleep(120);
        const tx = f.x + f.width / 2, ty = f.y + f.height / 2;
        for (let i = 1; i <= 30; i++)
            await move(x + (tx - x) * i / 30, y + (ty - y) * i / 30, 30);
        await sleep(400);
        release();
        await sleep(2000);
        const drops = readLines(logPath).map(l => JSON.parse(l)).filter(e => e.event === 'drop');
        const uri = drops[0]?.got?.['text/uri-list']?.text ?? '';
        const out = {shot: s, drops, thumbnailGone: !visible(), fileKept: s.path ? exists(s.path) : false};
        out.pass = s.active && !!s.path && uri.trim() === Gio.File.new_for_path(s.path).get_uri() && out.thumbnailGone &&
            out.fileKept;
        win.delete(global.get_current_time());
        await sleep(800);
        return out;
    });

    // The menu in this appearance mode, the Open With list, the rename
    // field and the Undo pill, each saved as a crop.
    await run('look', async () => {
        const out = {crops: []};
        const s = await shoot();
        const menu = await openMenu();
        out.items = menu ? menuItems(menu) : null;
        if (menu) {
            out.crops.push(await snap(`look-${theme}-menu`, [t, menu.actor]));
            await click(findItem(menu, 'Open With'), 600);
            out.openWith = menu._getMenuItems().find(i => i.label?.text === 'Open With')?.menu._getMenuItems()
                .map(i => i.label?.text);
            out.crops.push(await snap(`look-${theme}-open-with`, [t, menu.actor]));
            // Held open past the thumbnail's five seconds while the menu is up.
            await sleep(6500);
            out.visibleWithMenuOpen = visible();
            await key([Clutter.KEY_Escape], 600);
            out.menuClosedByEscape = !menu.isOpen;
        }
        await chooseItem('Rename…');
        await sleep(300);
        out.renaming = t.renaming;
        out.crops.push(await snap(`look-${theme}-rename`, [t]));
        await key([Clutter.KEY_Escape], 500);
        out.renameCancelled = !t.renaming && exists(s.path);
        await chooseItem('Move to Trash');
        await sleep(500);
        out.undoPill = !!t.trashed;
        out.crops.push(await snap(`look-${theme}-undo`, [t]));
        await click(t._undoButton, 1200);
        out.restored = exists(s.path);
        out.pass = s.active && !!menu && out.visibleWithMenuOpen && out.menuClosedByEscape && out.renaming &&
            out.renameCancelled && out.undoPill && out.restored && out.openWith?.length >= 3;
        return out;
    });

    // Every item does what it says.
    await run('actions', async () => {
        const out = {};
        const opened = () => readLines(`${OUT}/opened.log`);

        let s = await shoot();
        let before = opened().length;
        await chooseItem('Open');
        await waitFor(() => opened().length > before, 5000);
        out.open = {line: opened().at(-1) ?? null, gone: !visible()};
        out.open.pass = !!out.open.line?.startsWith('Opener') && out.open.line.includes(GLib.path_get_basename(s.path)) &&
            out.open.gone;

        s = await shoot();
        before = opened().length;
        {
            const menu = await openMenu();
            await click(findItem(menu, 'Open With'), 600);
            const sub = findItem(menu, 'Open With').menu;
            const viewer = sub._getMenuItems().find(i => i.label?.text === 'Viewer');
            out.openWithList = sub._getMenuItems().map(i => i.label?.text);
            await click(viewer, 900);
        }
        await waitFor(() => opened().length > before, 5000);
        out.openWith = {line: opened().at(-1) ?? null};
        out.openWith.pass = out.openWithList[0] === 'Opener' && !!out.openWith.line?.startsWith('Viewer') &&
            out.openWith.line.includes(GLib.path_get_basename(s.path));

        s = await shoot();
        {
            const menu = await openMenu();
            await click(findItem(menu, 'Open With'), 600);
            const sub = findItem(menu, 'Open With').menu;
            await click(sub._getMenuItems().find(i => i.label?.text === 'Other Application…'), 900);
        }
        await sleep(600);
        out.other = {call: fakebus().filter(e => e.method === 'OpenFile').at(-1) ?? null};
        out.other.pass = out.other.call?.file === s.path && out.other.call?.options?.ask === true;

        s = await shoot();
        St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD, 'empty');
        await chooseItem('Copy');
        const png = await clipboard('image/png');
        const size = Gio.File.new_for_path(s.path).query_info('standard::size', 0, null).get_size();
        out.copy = {bytes: png?.size ?? 0, fileSize: size, where: t.whereText, stillShown: visible()};
        out.copy.pass = out.copy.bytes === size && out.copy.stillShown;

        await chooseItem('Copy File');
        const uris = await clipboard('text/uri-list');
        out.copyFile = {text: uris?.text.trim() ?? null};
        out.copyFile.pass = out.copyFile.text === Gio.File.new_for_path(s.path).get_uri();

        await chooseItem('Show in Filer');
        await sleep(600);
        out.showInFiler = {call: fakebus().filter(e => e.method === 'ShowItems').at(-1) ?? null, gone: !visible()};
        out.showInFiler.pass = out.showInFiler.call?.uris?.[0] === Gio.File.new_for_path(s.path).get_uri() &&
            out.showInFiler.gone;

        s = await shoot();
        await chooseItem('Rename…');
        await sleep(300);
        const selected = t._nameEntry.clutter_text.get_selection();
        await typeText('Mountain view');
        await key([Clutter.KEY_Return], 700);
        const renamed = GLib.build_filenamev([GLib.path_get_dirname(s.path), 'Mountain view.png']);
        out.rename = {selected, name: t.nameText, oldGone: !exists(s.path), newThere: exists(renamed),
            fileIs: t.file?.get_path(), stillShown: visible(), helperBack: t.dragBridge.active};
        out.rename.pass = out.rename.oldGone && out.rename.newThere && out.rename.name === 'Mountain view.png' &&
            out.rename.fileIs === renamed;

        // Renamed, then dragged: the drag carries the new name (helper shown again).
        await chooseItem('Move to Trash');
        await sleep(600);
        const trashInfo = GLib.build_filenamev([GLib.get_user_data_dir(), 'Trash', 'info', 'Mountain view.png.trashinfo']);
        out.trash = {gone: !exists(renamed), trashed: !!t.trashed, trashInfo: exists(trashInfo), label: t._undoLabel.text};
        await click(t._undoButton, 1200);
        out.trash.restored = exists(renamed);
        out.trash.infoRemoved = !exists(trashInfo);
        out.trash.thumbnailBack = visible() && !t.trashed && t.file?.get_path() === renamed;
        out.trash.pass = out.trash.gone && out.trash.trashed && out.trash.restored && out.trash.thumbnailBack;

        out.pass = Object.values(out).every(v => typeof v !== 'object' || !('pass' in v) || v.pass);
        return out;
    });

    // Keys on the focused thumbnail: Delete (and Undo), Escape, Return.
    await run('keys', async () => {
        const out = {};
        let s = await shoot();
        t._button.grab_key_focus();
        await sleep(200);
        out.focused = global.stage.key_focus === t._button;
        await snap(`keys-${theme}-focus`, [t]);
        await key([Clutter.KEY_Delete], 900);
        out.del = {gone: !exists(s.path), undoPill: !!t.trashed, focusOnUndo: global.stage.key_focus === t._undoButton};
        await key([Clutter.KEY_Return], 1200);
        out.del.restored = exists(s.path) && !t.trashed;
        out.del.pass = out.del.gone && out.del.undoPill && out.del.focusOnUndo && out.del.restored;

        s = await shoot();
        t._button.grab_key_focus();
        await sleep(200);
        await key([Clutter.KEY_Escape], 900);
        out.esc = {gone: !t.visible, fileKept: exists(s.path)};
        out.esc.pass = out.esc.gone && out.esc.fileKept;

        s = await shoot();
        const before = readLines(`${OUT}/opened.log`).length;
        t._button.grab_key_focus();
        await sleep(200);
        await key([Clutter.KEY_Return], 1500);
        const line = readLines(`${OUT}/opened.log`).slice(before)[0] ?? null;
        out.enter = {line, gone: !visible()};
        out.enter.pass = !!line?.includes(GLib.path_get_basename(s.path)) && out.enter.gone;

        s = await shoot();
        t._button.grab_key_focus();
        await sleep(200);
        await key([Clutter.KEY_Shift_L, Clutter.KEY_F10], 800);
        out.menuKey = {open: !!t.menu?.isOpen, focusInMenu: t.menu?.actor.contains(global.stage.key_focus) ?? false};
        await key([Clutter.KEY_Escape], 500);
        out.menuKey.pass = out.menuKey.open && out.menuKey.focusInMenu;
        out.pass = out.focused && out.del.pass && out.esc.pass && out.enter.pass && out.menuKey.pass;
        return out;
    });

    // The verdict on its own, from made-up pointer paths (µs, stage px).
    await run('verdict', async () => {
        const path = (n, dx, dtUs, dy = 0) => Array.from({length: n}, (_, i) => ({t: i * dtUs, x: 100 + i * dx, y: 100 + i * dy}));
        const edgeHeld = [...path(6, 30, 16000), ...Array.from({length: 10}, (_, i) => ({t: 96000 + i * 16000, x: 250, y: 100}))];
        const cases = {
            fast: [Capture.flingVerdict(path(8, 24, 16000), {x: 100, y: 100}), true],
            slow: [Capture.flingVerdict(path(15, 5, 100000), {x: 100, y: 100}), false],
            short: [Capture.flingVerdict(path(3, 8, 16000), {x: 100, y: 100}), false],
            left: [Capture.flingVerdict(path(8, -24, 16000), {x: 100, y: 100}), false],
            steep: [Capture.flingVerdict(path(8, 24, 16000, 30), {x: 100, y: 100}), false],
            edgeHeld: [Capture.flingVerdict(edgeHeld, {x: 100, y: 100}, {rightEdge: 252}), true],
            heldLong: [Capture.flingVerdict(path(8, 24, 16000), {x: 100, y: 100}, {endTime: 112000 + 400000}), false],
        };
        const out = Object.fromEntries(Object.entries(cases).map(([k, [v, want]]) => [k, {...v, want, ok: v.fling === want}]));
        out.pass = Object.values(out).every(v => v.ok);
        return out;
    });

    GLib.file_set_contents(`${OUT}/results.json`, JSON.stringify(results, null, 1));
    const cases = Object.entries(results.cases);
    log('summary', {theme, passed: cases.filter(([, v]) => v?.pass).map(([k]) => k),
        failed: cases.filter(([, v]) => !v?.pass).map(([k]) => k)});
}
