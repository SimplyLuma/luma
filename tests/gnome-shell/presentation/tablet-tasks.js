// SPDX-License-Identifier: Apache-2.0
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import * as LumaGrid from 'resource:///org/gnome/shell/ui/lumaGrid.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
export async function testTabletTasks(p, check, sleep, shot, mode) {
    const tasks = p._tasks;
    const windows = [];
    for (const suffix of ['A', 'B']) {
        const info = p._apps.find(item => item.get_id() === `org.example.Split${suffix}.desktop`);
        p._launch(info);
        for (let i = 0; i < 30; i++) {
            const window = global.get_window_actors().map(actor => actor.meta_window).find(item => item.get_title() === `Split${suffix}`);
            if (window) { windows.push(window); break; }
            await sleep(100);
        }
    }
    check('two real GTK client windows opened', windows.length === 2);
    const [a, b] = windows;
    if (!a || !b) throw new Error('Missing live split clients');
    b.activate(global.get_current_time()); await sleep(200);
    check('put two real clients in a pair', tasks.place(b, 'right', a));
    await sleep(500);
    const fits = () => {
        const left = tasks.active.left.get_frame_rect(), right = tasks.active.right.get_frame_rect();
        const m = Main.layoutManager.primaryMonitor;
        return left.x === m.x && right.x + right.width === m.x + m.width &&
            left.x + left.width === right.x && left.height === m.height && right.height === m.height;
    };
    check('pair uses two real non-overlapping client allocations', fits());
    check('paired clients keep identities and leave fullscreen', !a.is_fullscreen() && !b.is_fullscreen() && a.get_compositor_private() && b.get_compositor_private());
    check('pair has pane gesture targets and a bounded global Home target', tasks._seam.visible && p._gestureBar.visible && p._gestureBar.width === 64*p._scale && tasks._bars.every(bar => bar.visible));
    await shot('tablet-two-live-apps');
    // Exercise real monitor rotation through Mutter's asynchronous API.
    const displayCall = (method, parameters = null) => new Promise((resolve, reject) => {
        Gio.DBus.session.call('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
            'org.gnome.Mutter.DisplayConfig', method, parameters, null, Gio.DBusCallFlags.NONE,
            5000, null, (connection, result) => {
                try { resolve(connection.call_finish(result).deep_unpack()); } catch (error) { reject(error); }
            });
    });
    const rotate = async transform => {
        const [serial, monitors] = await displayCall('GetCurrentState');
        const modeId = monitors[0][1].find(item => {
            const current = item[6]['is-current'];
            return typeof current?.deep_unpack === 'function' ? current.deep_unpack() : current;
        })[0];
        await displayCall('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
            [serial, 1, [[0, 0, 1, transform, true, [[monitors[0][0][0], modeId, {}]]]], {}]));
        await sleep(700);
    };
    const pair = tasks.active;
    await rotate(1);
    check('rotation retains the same live pair in portrait halves', tasks.active === pair && fits());
    await shot('tablet-pair-rotated');
    await rotate(0);
    check('rotation back retains the same app identities and allocation', tasks.active === pair && fits());
    p.showHome(); await sleep(200);
    check('Home preserves both paired app processes', a.minimized && b.minimized && tasks.groups.length === 1);
    tasks.openSwitcher(); await sleep(200);
    check('Tablet invokes the existing Desktop Grid switcher', Main.uiGroup.get_children().some(actor => actor.constructor.name === 'GridPopup'));
    await shot('tablet-desktop-switcher');
    LumaGrid.toggle(); await sleep(400);
    tasks.activate(tasks.groups[0]); await sleep(300);
    check('activating pair restores both clients', !a.minimized && !b.minimized && fits());
    tasks.swap(); await sleep(250);
    check('swap exchanges actual app sides', tasks.active.left === b && tasks.active.right === a && fits());
    tasks.fill(a); await sleep(250);
    check('fill restores one fullscreen app and preserves partner', a.is_fullscreen() && b.minimized && Boolean(b.get_compositor_private()));
    tasks.place(a, 'left', null); await sleep(300);
    check('lifting one app leaves a real half Home picker', p._home.visible && tasks.pickerSide === 'right' && p._home.width === tasks.monitor.width / 2);
    const infoB = p._apps.find(item => item.get_id() === 'org.example.SplitB.desktop');
    check('tapping an app fills the empty half', tasks.launchFromHome(infoB));
    await sleep(300);
    check('half picker becomes a live complete pair', tasks.active.left === a && tasks.active.right === b && !p._home.visible && fits());
    const seat = Clutter.get_default_backend().get_default_seat();
    const touch = seat.create_virtual_device(Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);
    const time = () => GLib.get_monotonic_time();
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const pointerSwitcher = async actor => {
        const [x, y] = actor.get_transformed_position();
        const [w, h] = actor.get_transformed_size();
        const cx = x+w/2, cy = y+h/2;
        pointer.notify_absolute_motion(time(), cx, cy);
        await sleep(100);
        const picked = global.stage.get_actor_at_pos(Clutter.PickMode.REACTIVE, cx, cy);
        pointer.notify_button(time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
        await sleep(550);
        for (let i = 1; i <= 8; i++) {
            pointer.notify_absolute_motion(time(), cx, cy-100*i/8);
            await sleep(45);
        }
        await sleep(650);
        pointer.notify_button(time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
        await sleep(350);
        return JSON.stringify({picked: picked?.toString(), mapped: actor.mapped,
            vertical: tasks._vertical, common: p._homePanDistance,
            actions: actor.get_actions().map(action => [action.get_name(), action.state])});
    };
    const pan = async (actor, dx, dy, hold = 0) => {
        const [x, y] = actor.get_transformed_position();
        const [w, h] = actor.get_transformed_size();
        const cx = x + w / 2, cy = y + h / 2;
        touch.notify_touch_down(time(), 0, cx, cy); await sleep(100);
        for (let i = 1; i <= 8; i++) {
            touch.notify_touch_motion(time(), 0, cx + dx * i / 8, cy + dy * i / 8);
            await sleep(45);
        }
        if (hold) await sleep(hold);
        touch.notify_touch_up(time(), 0); await sleep(350);
    };
    await pan(tasks._bars[0], 150, 0);
    check('native sideways half-bar gesture swaps the pair', tasks.active.left === b && tasks.active.right === a);
    const pairedPointer = await pointerSwitcher(tasks._bars[0]);
    check('pointer hold and upward drag opens Desktop Grid from a paired app', Main.uiGroup.get_children().some(actor => actor.constructor.name === 'GridPopup'), pairedPointer);
    if (Main.uiGroup.get_children().some(actor => actor.constructor.name === 'GridPopup')) LumaGrid.toggle();
    await sleep(350);
    tasks.activate(tasks.groups[0]); await sleep(250);
    await pan(tasks._seam, 80, 0);
    check('divider drag retains its chosen ratio on release', fits() && Math.abs(a.get_frame_rect().width - b.get_frame_rect().width) > 100);
    const ratio = tasks.active.ratio;
    tasks.layout(); await sleep(150);
    check('later layout retains the pair ratio', fits() && tasks.active.ratio === ratio && Math.abs(tasks._layoutSplit/tasks.monitor.width-ratio) < .002);
    await pan(tasks._bars[0], 0, -170);
    check('left Home swipe leaves the right app in its pane', tasks.active?.left === null && tasks.active?.right === a && !a.minimized && b.minimized && p._home.visible && tasks.pickerSide === 'left');
    tasks.launchFromHome(p._apps.find(item => item.get_id() === 'org.example.SplitB.desktop')); await sleep(300);
    await pan(p._gestureBar, 0, -170);
    check('central Home swipe minimizes both apps without closing them', a.minimized && b.minimized && p._home.visible);
    tasks.activate(tasks.groups[0]); await sleep(250);
    await pan(tasks._seam, -tasks.monitor.width / 2 + 4, 0);
    check('divider edge drag removes one side without destroying documents', a.is_fullscreen() && b.minimized && Boolean(b.get_compositor_private()));
    await pan(p._gestureBar, 0, -50);
    check('short native upward swipe peeks the dock over app', Boolean(tasks._overlay?.visible) && !p._home.visible);
    tasks.closeOverlay();
    const fullscreenPointer = await pointerSwitcher(p._gestureBar);
    check('pointer hold and upward drag opens Desktop Grid from a fullscreen app', Main.uiGroup.get_children().some(actor => actor.constructor.name === 'GridPopup'), fullscreenPointer);
    if (Main.uiGroup.get_children().some(actor => actor.constructor.name === 'GridPopup')) LumaGrid.toggle();
    await sleep(350);
    await pan(p._gestureBar, 0, -100, 650);
    check('upward swipe and hold opens Desktop Grid', Main.uiGroup.get_children().some(actor => actor.constructor.name === 'GridPopup'));
    LumaGrid.toggle(); await sleep(400);
    // Native pointer DND from both the peek dock and the all-app library.
    p._call('SetFavorites', new GLib.Variant('(as)', [['org.example.SplitB.desktop']]));
    await sleep(400);
    const drag = async actor => {
        const [x, y] = actor.get_transformed_position();
        const [w, h] = actor.get_transformed_size();
        pointer.notify_absolute_motion(time(), x + w / 2, y + h / 2);
        pointer.notify_button(time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
        await sleep(actor._delegate?.window ? 650 : 450);
        for (let step = 1; step <= 20; step++) {
            pointer.notify_absolute_motion(time(), x + w / 2 + (tasks.monitor.width * .8 - x - w / 2) * step / 20,
                y + h / 2 + (tasks.monitor.height * .5 - y - h / 2) * step / 20);
            await sleep(20);
        }
        await sleep(150);
        check('native pointer drag shows a side landing lane', Boolean(tasks._activeDrag) && tasks._dragSurface.visible);
        pointer.notify_button(time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
        await sleep(500);
    };
    tasks.peek(); await sleep(150);
    const dockItem = tasks._overlay.get_first_child().get_children().find(actor => actor._delegate?.info?.get_id() === 'org.example.SplitB.desktop');
    await drag(dockItem);
    check('drag from peek dock pairs the selected real app', tasks.active?.left === a && tasks.active?.right === b && fits());
    tasks.fill(a); await sleep(200);
    tasks.openLibrary(); await sleep(150);
    const search = tasks._panelActor.get_children()[1];
    search.set_text('Split fixture B'); await sleep(150);
    const libraryItem = tasks._panelActor.get_last_child().get_child().get_first_child().get_first_child();
    await drag(libraryItem);
    check('drag from searchable library pairs the selected real app', tasks.active?.left === a && tasks.active?.right === b && fits());
    tasks.fill(a); await sleep(250);
    tasks.openSwitcher(); await sleep(500);
    const grid = Main.uiGroup.get_children().find(actor => actor.constructor.name === 'GridPopup');
    const cardB = grid?._cards.find(card => card.window === b);
    check('Desktop Grid cards gain native Tablet drag actions', Boolean(cardB?._delegate?.tabletTasks === tasks));
    if (!cardB) throw new Error('Missing native Grid card');
    await drag(cardB);
    check('long press and side drop from Desktop Grid creates a real app pair', tasks.active?.left === a && tasks.active?.right === b && fits() && grid._destroyed);
    await shot('tablet-grid-card-side-drop');
    const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    tasks.fill(a); await sleep(250);
    tasks.openSwitcher(); await sleep(500);
    const touchGrid = Main.uiGroup.get_children().find(actor => actor.constructor.name === 'GridPopup');
    const touchCard = touchGrid._cards.find(card => card.window === b);
    const [tx, ty] = touchCard.get_transformed_position();
    const [tw, th] = touchCard.get_transformed_size();
    touch.notify_touch_down(time(), 0, tx+tw/2, ty+th/2);
    await sleep(650);
    for (let step = 1; step <= 20; step++) {
        touch.notify_touch_motion(time(), 0,
            tx+tw/2+(tasks.monitor.width*.2-tx-tw/2)*step/20,
            ty+th/2+(tasks.monitor.height*.5-ty-th/2)*step/20);
        await sleep(20);
    }
    touch.notify_touch_up(time(), 0); await sleep(500);
    check('touch long press and left-side drop from Grid places the real window', tasks.active?.left === b && tasks.active?.right === a && fits() && touchGrid._destroyed);
    tasks.fill(a); await sleep(250);
    tasks.openSwitcher(); await sleep(500);
    const cancelledGrid = Main.uiGroup.get_children().find(actor => actor.constructor.name === 'GridPopup');
    const cancelledCard = cancelledGrid._cards.find(card => card.window === b);
    const [cx, cy] = cancelledCard.get_transformed_position();
    const [cw, ch] = cancelledCard.get_transformed_size();
    pointer.notify_absolute_motion(time(), cx+cw/2, cy+ch/2);
    pointer.notify_button(time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
    await sleep(650);
    pointer.notify_absolute_motion(time(), cx+cw/2+30, cy+ch/2+30);
    await sleep(100);
    keyboard.notify_keyval(time(), Clutter.KEY_Escape, Clutter.KeyState.PRESSED);
    keyboard.notify_keyval(time(), Clutter.KEY_Escape, Clutter.KeyState.RELEASED);
    pointer.notify_button(time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
    await sleep(700);
    check('cancelled Grid drag restores the switcher and preserves app placement', !cancelledGrid._destroyed && cancelledGrid.opacity === 255 &&
        !tasks._activeDrag && !tasks._dragSurface.visible && !global.window_group.visible && a.is_fullscreen() && b.minimized);
    LumaGrid.toggle(); await sleep(400);
    const shortcut = async key => {
        keyboard.notify_keyval(time(), Clutter.KEY_Super_L, Clutter.KeyState.PRESSED);
        keyboard.notify_keyval(time(), key, Clutter.KeyState.PRESSED);
        keyboard.notify_keyval(time(), key, Clutter.KeyState.RELEASED);
        keyboard.notify_keyval(time(), Clutter.KEY_Super_L, Clutter.KeyState.RELEASED);
        await sleep(350);
    };
    a.activate(global.get_current_time()); await sleep(100);
    await shortcut(Clutter.KEY_Up);
    check('native Super+Up fills the focused app', a.is_fullscreen() && tasks.active === null);
    await shortcut(Clutter.KEY_Left);
    check('native Super+Left places focused app in a half', tasks.active?.left === a && tasks.pickerSide === 'right');
    await shortcut(Clutter.KEY_Right);
    check('native Super+Right moves focused app to the other half', tasks.active?.right === a);
    await mode('mobile');
    check('Mobile has no split divider or dock peek', !tasks._seam.visible && !tasks.enabled && a.is_fullscreen());
    tasks.peek(); check('Mobile refuses Tablet-only peek', !tasks._overlay);
    await mode('tablet');
    tasks.place(a, 'left', b); await sleep(250);
    await mode('desktop');
    check('Desktop restores original window geometry and decorations policy', !a.is_fullscreen() && !b.is_fullscreen() && !tasks._seam.visible,
        JSON.stringify({mode: p.mode, a: a.is_fullscreen(), b: b.is_fullscreen(), seam: tasks._seam.visible}));
    await mode('tablet'); await sleep(200);
    p.showHome(); await sleep(150);
    Main.sessionMode.pushMode('unlock-dialog'); await sleep(200);
    check('locking hides handheld chrome without unminimizing Home apps', !p._home.visible && a.minimized && b.minimized && p.mode === 'tablet');
    const lockedClient = Gio.Subprocess.new(['python3', `${GLib.getenv('PRESENTATION_SOURCE')}/tests/gnome-shell/presentation/window.py`,
        '--application-id', 'org.example.LockedClient', '--title', 'Locked client'], Gio.SubprocessFlags.NONE);
    let lockedWindow;
    for (let attempt = 0; attempt < 30 && !lockedWindow; attempt++) {
        await sleep(100);
        lockedWindow = global.get_window_actors().map(actor => actor.meta_window)
            .find(window => window.get_title() === 'Locked client');
    }
    check('client mapped while locked defers handheld management', lockedWindow && !p._windows.has(lockedWindow));
    Main.sessionMode.popMode('unlock-dialog'); await sleep(250);
    check('unlock restores Home and preserves minimized apps', p._home.visible && a.minimized && b.minimized && p.mode === 'tablet');
    check('unlock adopts newly mapped client without exposing it over Home',
        lockedWindow && p._windows.has(lockedWindow) && lockedWindow.is_fullscreen() && lockedWindow.minimized && p._homeMinimized.has(lockedWindow));
    lockedClient.force_exit(); await sleep(200);
    // Direct Desktop Grid entry (e.g. three-finger swipe) must hide Home too.
    p.showHome(); await sleep(150);
    LumaGrid.show(); await sleep(450);
    const closingGrid = Main.uiGroup.get_children().find(actor => actor.constructor.name === 'GridPopup');
    check('direct shared switcher entry hides Home', !p._home.visible && Boolean(closingGrid));
    closingGrid._cards.find(card => card.window === b).closeWindow(); await sleep(400);
    check('closing a minimized card keeps remaining switcher interactive', !closingGrid._destroyed && !p._home.visible && closingGrid._cards.some(card => card.window === a));
    const closingCard = closingGrid._cards.find(card => card.window === a);
    await pan(closingCard, 0, -190);
    check('upward card swipe requests close and returns to Home after the last app', !a.get_compositor_private() && !b.get_compositor_private() && p._home.visible);

    await sleep(150);
    await mode('automatic');
}
