// SPDX-License-Identifier: GPL-2.0-or-later
// Notifications live in the Shelf: an arriving notification shows no card at
// the top of the screen; it becomes (or joins) the notification island at the
// end of the shelf row, after Quick Options. The island shows the newest one
// with a count, offers its actions and a dismiss button on hover, dismisses on
// a swipe or fling to the right (a short slow drag springs back), opens the
// stack from the count, and leaves when the last is cleared. Urgent ones still
// show a card, above the island. Renders 0, 1 and 3 notifications in all four
// modes, and the arrival as a frame sequence.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';
import {getNotificationTray} from 'resource:///org/gnome/shell/ui/lumaNotificationBeacon.js';

const OUT = GLib.getenv('NI_OUT') ?? '/tmp/notification-island';
const results = [];
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const log = m => console.log(`[notifisland] ${m}`);
const check = (name, ok, detail = '') => { results.push({name, ok: !!ok, detail}); log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`); };
Gio._promisify(Shell.Screenshot.prototype, 'screenshot_stage_to_content');
Gio._promisify(Shell.Screenshot, 'composite_to_stream');
async function shotRect(name, x, y, w, h) {
    x = Math.max(0, Math.round(x)); y = Math.max(0, Math.round(y));
    w = Math.min(Math.round(w), global.stage.width - x); h = Math.min(Math.round(h), global.stage.height - y);
    const [content, scale] = await new Shell.Screenshot().screenshot_stage_to_content();
    const stream = Gio.File.new_for_path(`${OUT}/${name}.png`).replace(null, false, Gio.FileCreateFlags.NONE, null);
    await Shell.Screenshot.composite_to_stream(content.get_texture(), x, y, w, h, scale, null, 0, 0, 1, stream);
    stream.close(null);
}
const extents = a => { const e = a.get_transformed_extents(); return {x: e.get_x(), y: e.get_y(), w: e.get_width(), h: e.get_height()}; };

async function setMode(mode) {
    new Gio.Settings({schema_id: 'org.gnome.desktop.interface'}).set_string('color-scheme', mode === 'dark' ? 'prefer-dark' : 'default');
    new Gio.Settings({schema_id: 'org.project_luma.shell-state'}).set_string('surface-treatment', mode);
    await sleep(2500);
}

let source;
const sent = [];
function send(title, body, {critical = false, actions = []} = {}) {
    if (!source) {
        // A source goes away with its last notification; start a new one.
        source = new MessageTray.Source({title: 'Messages', iconName: 'mail-unread-symbolic'});
        source.connect('destroy', () => (source = null));
        Main.messageTray.add(source);
    }
    const n = new MessageTray.Notification({source, title, body});
    if (critical)
        n.urgency = MessageTray.Urgency.CRITICAL;
    for (const label of actions)
        n.addAction(label, () => log(`action ${label} ran`));
    source.addNotification(n);
    sent.push(n);
    return n;
}
function clearAll() {
    for (const n of source?.notifications ? [...source.notifications] : [])
        n.destroy(MessageTray.NotificationDestroyedReason.DISMISSED);
}

async function work() {
    const shelf = Main.shelf;
    for (let i = 0; i < 60 && !shelf?._beaconIsland; i++) await sleep(500);
    await sleep(3000);
    const island = shelf._beaconIsland;
    const beacon = shelf._beacon;
    const group = shelf._group;
    const seat = Clutter.get_default_backend().get_default_seat();
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const now = () => GLib.get_monotonic_time();
    const move = async (x, y, ms = 250) => { pointer.notify_absolute_motion(now(), x, y); await sleep(ms); };
    const press = () => pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
    const release = () => pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
    const waiting = () => source?.notifications?.length ?? 0;
    const cardsShown = () => (Main.messageTray._cards?.length ?? 0) + (Main.messageTray._leavingCards?.size ?? 0);
    const shelfCrop = async name => {
        const g = extents(group);
        await shotRect(name, g.x - 20, g.y - 90, g.w + 40, g.h + 110);
    };

    // Arrival, frame by frame, from no notifications.
    await setMode('dark');
    await move(20, 20);
    const actionsBefore = extents(shelf._actionsIsland).x;
    send('Dinner at 7?', 'Sam: are we still on for tonight?', {actions: ['Reply', 'Mark as Read']});
    for (const t of [0, 40, 80, 120, 160, 200, 280]) {
        await sleep(t === 0 ? 0 : 40);
        await shelfCrop(`arrival-${String(t).padStart(3, '0')}ms`);
    }
    await sleep(400);
    check('an arriving notification shows no card at the top of the screen', cardsShown() === 0 && !Main.messageTray.visible,
        `cards ${cardsShown()}`);
    const children = group.get_children().filter(c => c.visible);
    check('the notification island is the last island of the row, after Quick Options',
        island.visible && children[children.length - 1] === island && extents(island).x > extents(shelf._actionsIsland).x,
        `island x ${Math.round(extents(island).x)}, Quick Options x ${Math.round(extents(shelf._actionsIsland).x)}`);
    check('Quick Options moved towards the dock to make room', extents(shelf._actionsIsland).x < actionsBefore - 10,
        `${Math.round(actionsBefore)} -> ${Math.round(extents(shelf._actionsIsland).x)}`);
    check('the island shows the title and one line of the body', beacon._title.text === 'Dinner at 7?' && beacon._summary.text.startsWith('Sam:'),
        `${beacon._title.text} / ${beacon._summary.text}`);
    check('the island shares the island height', Math.abs(extents(island).h - extents(shelf._actionsIsland).h) < 1.5,
        `${extents(island).h} vs ${extents(shelf._actionsIsland).h}`);

    // Hover: actions and dismiss; the width does not change.
    const w0 = extents(island).w;
    const c = extents(island);
    await move(c.x + c.w * 0.35, c.y + c.h / 2, 500);
    check('hovered, the island offers the actions and a dismiss button',
        beacon._actions.visible && beacon._actions.get_n_children() === 2 && beacon._dismiss.opacity === 255,
        `actions ${beacon._actions.get_n_children()}, dismiss opacity ${beacon._dismiss.opacity}`);
    check('hovering does not change the island width', Math.abs(extents(island).w - w0) < 1, `${w0} -> ${extents(island).w}`);
    await shelfCrop('hover-dark');
    await move(20, 20, 400);

    // Modes x 0, 1, 3 notifications.
    for (const mode of ['light', 'dark', 'frost', 'glass']) {
        await setMode(mode);
        clearAll();
        await sleep(700);
        check(`${mode}: with no notifications there is no island`, !island.visible);
        await shelfCrop(`${mode}-0`);
        send('Build finished', 'luma-shell 50.3 passed every check.');
        await sleep(700);
        check(`${mode}: one notification, one island, no count`, island.visible && !beacon._count.visible);
        await shelfCrop(`${mode}-1`);
        send('Sam', 'On my way.');
        send('Invoice ready', 'September invoice is attached.', {actions: ['Open']});
        await sleep(700);
        check(`${mode}: three notifications show the newest and a count of 3`,
            beacon._count.visible && beacon._count.label === '3' && beacon._title.text === 'Invoice ready', beacon._count.label);
        await shelfCrop(`${mode}-3`);
    }

    // Dismiss button, count opens the stack, swipe and fling.
    await setMode('dark');
    clearAll();
    await sleep(600);
    for (let i = 1; i <= 4; i++) send(`Message ${i}`, `Body ${i}`);
    await sleep(800);
    let e = extents(island);
    await move(e.x + e.w * 0.3, e.y + e.h / 2, 400);
    const d = extents(beacon._dismiss);
    await move(d.x + d.w / 2, d.y + d.h / 2, 300);
    press(); await sleep(60); release(); await sleep(600);
    check('the dismiss button clears the newest', waiting() === 3, `${waiting()} left`);
    const k = extents(beacon._count);
    await move(k.x + k.w / 2, k.y + k.h / 2, 300);
    press(); await sleep(60); release(); await sleep(700);
    const tray = getNotificationTray();
    check('the count opens the whole stack', tray.visible && tray.mapped);
    await shotRect('stack-open', 0, 0, global.stage.width, global.stage.height);
    tray.close();
    await move(20, 20, 600);
    // A slow, short drag springs back.
    e = extents(island);
    await move(e.x + e.w * 0.3, e.y + e.h / 2, 300);
    press();
    for (let i = 1; i <= 10; i++) await move(e.x + e.w * 0.3 + i * 3, e.y + e.h / 2, 60);
    release(); await sleep(600);
    check('a slow short drag springs back', waiting() === 3 && Math.abs(island.translation_x) < 1, `${waiting()} left`);
    // A fast fling to the right dismisses.
    e = extents(island);
    await move(e.x + e.w * 0.3, e.y + e.h / 2, 300);
    press();
    for (let i = 1; i <= 5; i++) await move(e.x + e.w * 0.3 + i * 20, e.y + e.h / 2, 16);
    release(); await sleep(700);
    check('a fling to the right dismisses the newest', waiting() === 2, `${waiting()} left`);
    // Clicking the island opens the notification drawer (.119: the owner's
    // expectation; the newest opens from its card there), and again closes it.
    let activated = false;
    source.notifications[source.notifications.length - 1]?.connect('activated', () => (activated = true));
    await move(20, 20, 400);
    e = extents(island);
    await move(e.x + 20, e.y + e.h / 2, 300);
    press(); await sleep(60); release(); await sleep(700);
    check('clicking the island opens the notification drawer', tray.visible && tray.mapped && tray.isOpen && !activated);
    tray.close();
    await sleep(500);
    // Clearing the last removes the island, and Quick Options returns.
    clearAll();
    await sleep(700);
    check('clearing the last removes the island', !island.visible);
    // Urgent: a card above the island.
    send('Low battery', '5% left. Plug in now.', {critical: true});
    await sleep(900);
    const card = Main.messageTray._cards?.[0]?.frame;
    // Urgent notifications are not held by the island, so measure against the
    // row: the card sits above the shelf, at its end, never at the top.
    const ie = extents(shelf._actionsIsland);
    const ce = card ? extents(card) : null;
    check('an urgent notification shows a card above the shelf at its end, not at the top',
        !!ce && ce.y + ce.h <= ie.y + 1 && ce.y > global.stage.height / 3 && ce.x + ce.w > ie.x,
        ce ? `card ${Math.round(ce.x)},${Math.round(ce.y)} ${Math.round(ce.w)}x${Math.round(ce.h)}, row top ${Math.round(ie.y)}` : 'no card');
    await shotRect('urgent-card', 0, global.stage.height / 2, global.stage.width, global.stage.height / 2);
    Main.messageTray._cards?.forEach(entry => entry.notification.destroy(MessageTray.NotificationDestroyedReason.DISMISSED));
    await sleep(600);

    // The island is a managed shelf island with the ADR-044 id.
    check('the island is the shelf\'s "notifications" island', shelf.getIsland('notifications') === island &&
        island.islandId === 'notifications' && island.name === 'lumaShelfIsland-notifications',
        `${island.islandId} ${island.name}`);

    // Reduced motion: it fades, and neither grows nor slides.
    new Gio.Settings({schema_id: 'org.gnome.desktop.interface'}).set_boolean('enable-animations', false);
    await sleep(600);
    clearAll();
    await sleep(600);
    const frames = [];
    send('Quiet arrival', 'Reduced motion fades the island in.');
    for (let i = 0; i < 8; i++) {
        await sleep(30);
        frames.push({opacity: island.opacity, scale: island.scale_x, qx: Math.round(shelf._actionsIsland.translation_x)});
        if (i === 2)
            await shelfCrop('reduced-motion-fading');
    }
    await sleep(300);
    check('with reduced motion the island fades in without growing or sliding',
        frames.every(f => f.scale === 1 && f.qx === 0) && frames.some(f => f.opacity > 0 && f.opacity < 255) &&
        island.opacity === 255, JSON.stringify(frames));
    clearAll();
    await sleep(600);
    check('with reduced motion the island leaves', !island.visible);
    new Gio.Settings({schema_id: 'org.gnome.desktop.interface'}).set_boolean('enable-animations', true);
}

export function init() {
    GLib.mkdir_with_parents(OUT, 0o755);
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 8000, () => {
        work().catch(e => check('scenario ran without errors', false, `${e}\n${e.stack}`)).finally(() => {
            GLib.file_set_contents(`${OUT}/notification-island.json`, JSON.stringify(results, null, 1));
            log(`DONE ${results.filter(r => r.ok).length}/${results.length} passed`);
            global.context.terminate();
        });
        return GLib.SOURCE_REMOVE;
    });
}
export async function run() { await new Promise(() => {}); }
