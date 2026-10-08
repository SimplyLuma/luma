// SPDX-License-Identifier: GPL-2.0-or-later
// Dock badge evidence and behaviour checks. MODE: matrix | edges | interact | behaviour.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {getBadgeSource} from 'resource:///org/gnome/shell/ui/lumaDockBadges.js';
import {wait, test, setAgent, launcher, geometry, writeJson, dockBox, shellState, iconFor, deviceShot} from 'file:///oracle/t/lib.js';

const L = n => `org.projectluma.${n}.desktop`;
const MODE = GLib.getenv('MODE') ?? 'matrix';

function sampleBadges() {
    launcher(L('Calendar'), {urgent: true});
    launcher(L('Contacts'), {'count': 9, 'count-visible': true});
    launcher(L('Maps'), {'count': 12, 'count-visible': true});
    setAgent('org.projectluma.Messages', {'unread-count': 1, 'badge': 1});
    launcher(L('Notes'), {'count': 128, 'count-visible': true});
    setAgent('org.projectluma.Phone', {'missed-calls': 99, 'badge': 99});
    // The last icon: its badge reaches past the dock's content.
    launcher(L('Write'), {'count': 128, 'count-visible': true});
}
const SAMPLE_IDS = ['Calendar', 'Contacts', 'Maps', 'Messages', 'Notes', 'Phone', 'Write'].map(L);

async function matrix(shot) {
    sampleBadges();
    await wait(1200);
    const results = {};
    for (const treatment of ['light', 'dark', 'frost', 'glass']) {
        shellState.set_string('surface-treatment', treatment);
        await wait(1500);
        results[treatment] = SAMPLE_IDS.map(geometry);
        const box = dockBox();
        results[`${treatment}-screenshot`] = {...await deviceShot(`matrix-${treatment}`, ...box), box};
    }
    writeJson('matrix', results);
}

async function edges(shot) {
    sampleBadges();
    await wait(1200);
    const results = {};
    for (const edge of ['bottom', 'top', 'left', 'right']) {
        shellState.set_string('shelf-edge', edge);
        await wait(2500);
        results[edge] = SAMPLE_IDS.map(geometry);
        await shot(`edge-${edge}`, ...dockBox(40));
        const m = Main.layoutManager.primaryMonitor;
        await shot(`edge-${edge}-screen`, m.x, m.y, m.width, m.height);
    }
    writeJson('edges', results);
}

function center(actor) {
    const [x, y] = actor.get_transformed_position();
    const [w, h] = actor.get_transformed_size();
    return [x + w / 2, y + h / 2];
}

async function interact(shot) {
    sampleBadges();
    await wait(1200);
    const seat = Clutter.get_default_backend().get_default_seat();
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const now = () => GLib.get_monotonic_time();
    const out = {};
    const icon = iconFor(L('Messages'));
    const before = geometry(L('Messages'));
    out.before = before;
    await shot('interact-rest', ...dockBox());
    // Hover: the badge rides the icon's lift.
    const [cx, cy] = center(icon);
    pointer.notify_absolute_motion(now(), cx, cy);
    await wait(700);
    out.hover = geometry(L('Messages'));
    out.hovered = icon.hover;
    out.hoverLift = {artwork: before.artwork.y - out.hover.artwork.y, badge: before.badge.y - out.hover.badge.y};
    await shot('interact-hover', ...dockBox());
    // A new message while hovered: no pop.
    setAgent('org.projectluma.Messages', {'unread-count': 2, 'badge': 2});
    await wait(80);
    out.popWhileHovered = icon._badge._popTimeline !== null && icon._badge._popTimeline !== undefined;
    await wait(500);
    // Drag to rearrange: the drag actor carries the badge.
    pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
    for (let i = 1; i <= 12; i++) {
        pointer.notify_absolute_motion(now(), cx + i * 6, cy - i * 3);
        await wait(40);
    }
    await wait(400);
    const dragActor = Main.uiGroup.get_children().find(a => a.get_children?.().some(c => c.constructor.name.includes('DockBadge')));
    out.dragActorHasBadge = !!dragActor;
    if (dragActor) {
        const badge = dragActor.get_children().find(c => c.constructor.name.includes('DockBadge'));
        const [bx, by] = badge.get_transformed_position();
        const [bw, bh] = badge.get_transformed_size();
        const [dx, dy] = dragActor.get_transformed_position();
        const [dw, dh] = dragActor.get_transformed_size();
        out.drag = {actor: [dx, dy, dw, dh], badge: [bx, by, bw, bh], visible: badge.visible, text: badge._label.text};
        await shot('interact-drag', Math.max(0, Math.round(dx - 40)), Math.max(0, Math.round(dy - 40)), Math.round(dw + 80), Math.round(dh + 80));
        const m = Main.layoutManager.primaryMonitor;
        await shot('interact-drag-screen', m.x, m.y + m.height - 200, m.width, 200);
    }
    pointer.notify_absolute_motion(now(), cx, cy);
    await wait(200);
    pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
    await wait(800);
    pointer.notify_absolute_motion(now(), cx, cy - 400);
    await wait(600);
    // Launch zoom: the animated clone includes the badge.
    icon.animateLaunch();
    await wait(90);
    const clone = Main.uiGroup.get_children().filter(a => a instanceof Clutter.Clone).pop();
    out.launchClone = clone ? {source: clone.source?.constructor.name, sourceHasBadge: !!clone.source?.get_children?.().some(c => c.get_children?.().some(g => g.constructor.name.includes('DockBadge')))} : null;
    await shot('interact-launch', ...dockBox(60));
    writeJson('interact', out);
}

async function behaviour(shot) {
    const source = getBadgeSource();
    const results = [];
    const expect = (name, ok, detail = '') => results.push({name, ok: !!ok, detail: String(detail)});
    const badgeOf = id => source.get(id);
    const text = id => {
        const b = badgeOf(id);
        return b ? (b.kind === 'dot' ? 'dot' : String(b.count)) : 'none';
    };
    const shell = new Gio.Settings({schema_id: 'org.gnome.shell'});

    // LauncherEntry.
    launcher(L('Notes'), {'count': 5, 'count-visible': true});
    await wait(500);
    expect('LauncherEntry count shows', text(L('Notes')) === '5', text(L('Notes')));
    launcher(L('Notes'), {'count': 6});
    await wait(400);
    expect('LauncherEntry partial update keeps count-visible', text(L('Notes')) === '6', text(L('Notes')));
    launcher(L('Notes'), {'count-visible': false});
    await wait(400);
    expect('LauncherEntry hidden count clears', text(L('Notes')) === 'none', text(L('Notes')));
    launcher(L('Notes'), {'urgent': true});
    await wait(400);
    expect('LauncherEntry urgent is a dot', text(L('Notes')) === 'dot', text(L('Notes')));
    launcher(L('Notes'), {'urgent': false, 'count': 128, 'count-visible': true});
    await wait(400);
    expect('LauncherEntry 128', text(L('Notes')) === '128' && iconFor(L('Notes'))._badge._label.text === '99+', iconFor(L('Notes'))._badge._label.text);
    expect('accessible name has the real number', iconFor(L('Notes')).accessible_name === 'Notes, 128 unread', iconFor(L('Notes')).accessible_name);
    await shot('behaviour-launcher', ...dockBox());
    launcher('not-a-real-app.desktop', {'count': 3, 'count-visible': true});
    launcher(L('Maps'), {'count': 4, 'count-visible': true});
    await wait(400);
    test('QuitLauncher');
    await wait(800);
    expect('app quitting clears its LauncherEntry badges', text(L('Notes')) === 'none' && text(L('Maps')) === 'none', `${text(L('Notes'))} ${text(L('Maps'))}`);

    // Agents.
    setAgent('org.projectluma.Messages', {'unread-count': 3, 'badge': 3});
    await wait(500);
    expect('agent badge shows', text(L('Messages')) === '3', text(L('Messages')));
    expect('accessible name', iconFor(L('Messages')).accessible_name === 'Messages, 3 unread', iconFor(L('Messages')).accessible_name);
    launcher(L('Messages'), {'count': 50, 'count-visible': true});
    await wait(400);
    expect('agent wins over LauncherEntry', text(L('Messages')) === '3', text(L('Messages')));
    setAgent('org.projectluma.Messages', {'unread-count': 5, 'badge': 5});
    await wait(90);
    const popping = iconFor(L('Messages'))._badge;
    const scaleMid = popping.scale_x;
    expect('pop on increase', !!popping._popTimeline && scaleMid !== 1, scaleMid);
    await wait(500);
    expect('pop settles at 1', popping.scale_x === 1 && !popping._popTimeline, popping.scale_x);
    setAgent('org.projectluma.Messages', {'unread-count': 4, 'badge': 4});
    await wait(60);
    expect('no pop on decrease', !popping._popTimeline && popping.scale_x === 1 && text(L('Messages')) === '4', `${popping.scale_x} ${text(L('Messages'))}`);
    setAgent('org.projectluma.Messages', {'unread-count': 0, 'badge': 0});
    await wait(400);
    expect('agent zero owns the badge (LauncherEntry ignored)', text(L('Messages')) === 'none', text(L('Messages')));
    expect('name without badge', iconFor(L('Messages')).accessible_name === 'Messages', iconFor(L('Messages')).accessible_name);
    setAgent('org.projectluma.Messages', {'unread-count': 0});
    await wait(400);
    expect('agent without a badge value falls to LauncherEntry', text(L('Messages')) === '50', text(L('Messages')));
    setAgent('org.projectluma.Messages', {'unread-count': 2, 'badge': 2});
    setAgent('org.projectluma.Phone', {'missed-calls': 1, 'badge': 'dot'});
    setAgent('org.projectluma.Weather', {'badge': 7});
    await wait(500);
    expect('agent dot', text(L('Phone')) === 'dot', text(L('Phone')));
    expect('Calendar-style name for a dot', iconFor(L('Phone')).accessible_name === 'Phone, new activity', iconFor(L('Phone')).accessible_name);
    expect('agent that does not declare badge is ignored', text(L('Weather')) === 'none', text(L('Weather')));

    // Settings switch key.
    shell.set_strv('dock-badges-disabled-apps', ['org.projectluma.Phone']);
    await wait(500);
    expect('badge off removes it', text(L('Phone')) === 'none' && !iconFor(L('Phone'))._badge.visible, text(L('Phone')));
    expect('badge off stops listening', !source._agents.has('org.projectluma.Phone'));
    setAgent('org.projectluma.Phone', {'missed-calls': 2, 'badge': 2});
    await wait(300);
    expect('no badge while off', text(L('Phone')) === 'none', text(L('Phone')));
    shell.set_strv('dock-badges-disabled-apps', []);
    await wait(800);
    expect('badge on listens again', text(L('Phone')) === '2' && source._agents.has('org.projectluma.Phone'), text(L('Phone')));

    // Agent stops: its badge clears.
    test('StopAgent', 'org.projectluma.Phone');
    await wait(800);
    expect('agent gone clears its badge', text(L('Phone')) === 'none', text(L('Phone')));

    // Do Not Disturb does not hide badges.
    new Gio.Settings({schema_id: 'org.gnome.desktop.notifications'}).set_boolean('show-banners', false);
    await wait(400);
    expect('Do Not Disturb keeps badges', text(L('Messages')) === '2', text(L('Messages')));
    await shot('behaviour-end', ...dockBox());

    writeJson('behaviour', results);
    const failed = results.filter(r => !r.ok);
    console.log(`[badges] behaviour ${results.length - failed.length}/${results.length} passed`);
    for (const r of failed)
        console.log(`[badges] FAIL ${r.name}: ${r.detail}`);
}

export default async function ({shot}) {
    await wait(1500);
    if (MODE === 'matrix')
        await matrix(shot);
    else if (MODE === 'edges')
        await edges(shot);
    else if (MODE === 'interact')
        await interact(shot);
    else
        await behaviour(shot);
}
