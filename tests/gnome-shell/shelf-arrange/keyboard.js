// SPDX-License-Identifier: GPL-2.0-or-later
// Keyboard and screen readers (ADR-044 §5), the Shelf menu, surfaces opening
// away from their island's edge, and the first half of the persistence check
// (restart.js reads back what this leaves).
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {getNotificationTray} from 'resource:///org/gnome/shell/ui/lumaNotificationBeacon.js';
import * as L from './lib.js';

const arrange = () => Main.shelf._arrange;
const groupOf = id => L.stored().find(g => g.islands.includes(id));

async function work() {
    const shelf = await L.waitForShelf();
    // Announcements are Atk notifications on the card or the shelf.
    const spoken = [];
    const hook = actor => actor.get_accessible()?.connect('notification', (_a, text) => spoken.push(text));

    // The Shelf menu: right-click the clock.
    const clock = L.rectOf(shelf.partActorFor('clock'));
    await L.move(...L.centre(clock), 150);
    await L.press(Clutter.BUTTON_SECONDARY);
    await L.release(Clutter.BUTTON_SECONDARY);
    await L.sleep(700);
    const menu = arrange()._menu;
    const items = menu?._getMenuItems?.().filter(i => i.label?.text) ?? [];
    const labels = items.map(i => i.label.text);
    const byLabel = text => items.find(i => i.label.text === text);
    L.check('right-click shows the Shelf menu', menu?.isOpen, JSON.stringify(labels));
    L.check('menu items', JSON.stringify(labels) === JSON.stringify(['Dash', 'Arrange Islands…', 'Reset Island Layout', 'Dash Settings…']), JSON.stringify(labels));
    L.check('Reset is off while the layout is the default', byLabel('Reset Island Layout')?.sensitive === false);
    await L.shot('k-01-shelf-menu', {x: clock.x - 300, y: clock.y - 260, width: 600, height: 330});
    // Arrange Islands… from the menu.
    byLabel('Arrange Islands…').activate(Clutter.get_current_event());
    await L.sleep(800);
    L.check('Arrange Islands… enters arrange mode', arrange().active);
    hook(arrange()._card);
    hook(shelf);

    // Tab selects islands; Ctrl+Alt+arrow moves the selected one.
    arrange()._selected = 'clock';
    await L.key(Clutter.KEY_Up, [Clutter.KEY_Control_L, Clutter.KEY_Alt_L], 900);
    let g = groupOf('clock');
    L.check('Ctrl+Alt+Up from the bottom-right: the right edge end', g?.edge === 'right' && g.anchor === 'end', JSON.stringify(g));
    L.check('the step was spoken', spoken.some(t => /^Clock, right edge/.test(t)), JSON.stringify(spoken.slice(-2)));
    await L.key(Clutter.KEY_Up, [Clutter.KEY_Control_L, Clutter.KEY_Alt_L], 900);
    g = groupOf('clock');
    L.check('Ctrl+Alt+Up along the right edge steps up', g?.edge === 'right' && g.anchor !== 'end', JSON.stringify(g));
    await L.key(Clutter.KEY_z, [Clutter.KEY_Control_L], 900);
    await L.key(Clutter.KEY_z, [Clutter.KEY_Control_L], 900);
    L.check('two undos return the clock', JSON.stringify(L.stored()) === '[]' || groupOf('clock')?.edge === 'bottom', JSON.stringify(groupOf('clock')));
    // Shift moves the whole group.
    arrange()._selected = 'dock';
    await L.key(Clutter.KEY_Right, [Clutter.KEY_Control_L, Clutter.KEY_Alt_L, Clutter.KEY_Shift_L], 900);
    g = groupOf('dock');
    L.check('Ctrl+Alt+Shift+Right moves the dock group along the bottom', g && g.edge === 'bottom' && g.anchor !== 'start', JSON.stringify(g));
    await L.key(Clutter.KEY_Tab, [], 400);
    L.check('Tab selects the next island', arrange()._selected && arrange()._selected !== 'dock', arrange()._selected);
    L.check('the selection draws the focus ring', !!arrange()._selectedMaterial?.has_style_class_name('luma-island-focus'));
    await L.key(Clutter.KEY_Escape, [], 800);
    L.check('Esc leaves arrange mode', !arrange().active);
    L.check('"Islands arranged" was spoken', spoken.includes('Islands arranged'), JSON.stringify(spoken.slice(-1)));

    // Outside arrange mode, Ctrl+Alt+arrow on a keyboard-focused island.
    await L.reset(1500);
    const media = shelf.getIsland('media');
    media._content.get_children()[0]?.grab_key_focus?.() ?? media._content.grab_key_focus();
    let focus = global.stage.get_key_focus();
    if (!focus || !shelf.contains(focus)) {
        shelf._media.can_focus = true;
        shelf._media.grab_key_focus();
        focus = global.stage.get_key_focus();
    }
    L.check('an island holds key focus', !!focus && shelf.contains(focus), `${focus}`);
    await L.key(Clutter.KEY_Up, [Clutter.KEY_Control_L, Clutter.KEY_Alt_L], 1200);
    g = groupOf('media');
    L.check('outside arrange mode Ctrl+Alt+Up moves the focused island', g && g.edge !== 'bottom', JSON.stringify(g));
    L.check('and does not switch workspaces', global.workspace_manager.get_active_workspace_index() === 0);

    // Surfaces open away from their island's edge: the notification tray
    // from a notifications island on the top, left and right.
    L.notify('Build finished', 'Shell .115 is ready');
    await L.sleep(2000);
    for (const [edge, anchor] of [['top', 'end'], ['left', 'center'], ['right', 'start'], ['bottom', 'end']]) {
        const rest = ['dock', 'live', 'media', 'well', 'quick-options', 'clock'];
        await L.arrange([{edge: 'bottom', anchor: 'center', islands: rest}, {edge, anchor, islands: ['notifications']}], 2000);
        const island = shelf.getIsland('notifications');
        const ir = L.rectOf(island);
        const tray = getNotificationTray();
        tray.open(shelf._beacon);
        await L.sleep(900);
        const tr = L.rectOf(tray);
        const away = {top: tr.y >= ir.y + ir.height, bottom: tr.y + tr.height <= ir.y,
            left: tr.x >= ir.x + ir.width, right: tr.x + tr.width <= ir.x}[edge];
        L.check(`the tray opens away from a ${edge}-edge notifications island`, away, `${JSON.stringify(ir)} ${JSON.stringify(tr)}`);
        await L.shot(`k-tray-${edge}`);
        tray.close();
        await L.sleep(500);
    }

    // Leave an arrangement for restart.js.
    await L.arrange([{edge: 'left', anchor: 'center', islands: ['dock']},
        {edge: 'top', anchor: 'end', islands: ['media']},
        {edge: 'top', anchor: 'center', islands: ['clock']},
        {edge: 'bottom', anchor: 'end', islands: ['live', 'well', 'quick-options', 'notifications']}], 2200);
    const snapshot = {stored: L.stored(), groups: L.groups().map(x => ({edge: x.edge, anchor: x.anchor,
        islands: x.islands.map(i => [i.id, i.rect])})), work: L.workArea()};
    GLib.file_set_contents(`${L.OUT}/before-restart.json`, JSON.stringify(snapshot));
    await L.shot('k-before-restart');
}
export function init() { L.start('keyboard', work); }
export async function run() { await new Promise(() => {}); }
