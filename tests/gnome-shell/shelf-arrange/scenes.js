// SPDX-License-Identifier: GPL-2.0-or-later
// The owner's scenarios, driven with a virtual pointer through the real
// hold-and-drag path: hold the clock to enter arrange mode; split the clock to
// the bottom-right corner; attach it right of the dock (with the attach
// preview captured); the dock to the bottom-left corner and back to the
// middle; the dock on the whole left side with music in the top-right corner;
// the clock alone at the top centre; Esc cancels a drag; Ctrl+Z undoes.
import Clutter from 'gi://Clutter';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

const shelf = () => Main.shelf;
const arrange = () => Main.shelf._arrange;
const rectOfId = id => L.rectOf(shelf().partActorFor(id));
const groupOf = id => L.stored().find(g => g.islands.includes(id));

async function enterByHold(id) {
    const [x, y] = L.centre(rectOfId(id));
    await L.move(x, y, 150);
    await L.press();
    await L.sleep(560);
    return [x, y];
}

async function dragIsland(id, to, {preview = null, hold = true} = {}) {
    const [x, y] = L.centre(rectOfId(id));
    await L.move(x, y, 120);
    await L.press();
    await L.sleep(hold ? 560 : 60);
    const steps = 28;
    for (let i = 1; i <= steps; i++)
        await L.move(x + (to[0] - x) * i / steps, y + (to[1] - y) * i / steps, 18);
    await L.sleep(400);
    if (preview)
        await L.shot(preview);
    const text = arrange().lastAnnouncement;
    await L.release();
    await L.sleep(900);
    return text;
}

async function work() {
    await L.waitForShelf();
    L.notify();
    await L.sleep(2500);
    const W = global.stage.width, H = global.stage.height;
    L.log(`start ${JSON.stringify(L.groups())}`);
    await L.shot('01-default');
    L.check('default: the owner spanning shelf, dock at the start and the rest at the end', L.groups().length === 2 && L.groups().every(g => g.edge === 'bottom') && L.groups()[0].rect.x === 14 && L.groups()[1].rect.x + L.groups()[1].rect.width === W - 14, JSON.stringify(L.groups().map(g => g.rect)));

    // Hold the clock: arrange mode, the clock lifted.
    await enterByHold('clock');
    L.check('hold 400 ms enters arrange mode', arrange().active);
    await L.release();
    await L.sleep(600);
    L.check('release without moving keeps arrange mode', arrange().active);
    L.check('the arrange card shows', arrange()._card?.visible && arrange()._card.mapped);
    L.check('empty zones are drawn', (arrange()._zones?.length ?? 0) >= 9, `${arrange()._zones?.length}`);
    await L.shot('02-arrange-mode');

    // "Take just my time and put it up at the top."
    let said = await dragIsland('clock', [W / 2, 42], {preview: '03-clock-to-top-preview'});
    L.check('snap preview spoken', /Top edge, centre/.test(said ?? ''), said);
    let g = groupOf('clock');
    L.check('clock alone at the top centre', g && g.edge === 'top' && g.anchor === 'center' && g.islands.length === 1, JSON.stringify(g));
    const clock = rectOfId('clock');
    L.check('clock drawn centred at the top, 14 down', Math.abs(clock.x + clock.width / 2 - W / 2) <= 2 && Math.abs(clock.y - 14) <= 2, JSON.stringify(clock));
    L.check('the clock is its own island', shelf().islandActorFor('clock') === shelf()._clockIsland && shelf()._clockIsland.mapped);
    await L.shot('04-clock-top-centre');

    // Attach it to the right of the dock.
    const dock = rectOfId('dock');
    said = await dragIsland('clock', [dock.x + dock.width + 30, dock.y + dock.height / 2], {preview: '05-attach-preview', hold: false});
    // "Bottom edge, left corner, after Dock": where it lands, in words.
    L.check('attach preview spoken', /edge/.test(said ?? '') && /after Dock/.test(said ?? ''), said);
    g = groupOf('clock');
    L.check('clock attached right of the dock', g && g.islands.indexOf('clock') === g.islands.indexOf('dock') + 1, JSON.stringify(g));
    await L.shot('06-clock-attached');
    // Undo puts it back in the corner.
    await L.key(Clutter.KEY_z, [Clutter.KEY_Control_L], 900);
    L.check('Ctrl+Z undoes the last drop', groupOf('clock')?.edge === 'top', JSON.stringify(groupOf('clock')));

    // The dock to the bottom-left corner, then back to the middle.
    await dragIsland('dock', [150, H - 42], {hold: false});
    g = groupOf('dock');
    L.check('dock at the bottom-left corner', g?.anchor === 'start' && g.edge === 'bottom', JSON.stringify(g));
    await L.shot('07-dock-bottom-left');
    await dragIsland('dock', [W / 2, H - 42], {hold: false});
    g = groupOf('dock');
    L.check('dock back to the middle', g && g.edge === 'bottom' && (g.anchor === 'center' || g.islands.length > 1), JSON.stringify(g));
    await L.shot('08-dock-middle');

    // Esc cancels a drag: nothing is written.
    const before = JSON.stringify(L.stored());
    const m = rectOfId('media');
    await L.move(...L.centre(m), 100);
    await L.press();
    for (let i = 1; i <= 10; i++)
        await L.move(m.x + m.width / 2, m.y - 30 * i, 16);
    await L.key(Clutter.KEY_Escape, [], 600);
    await L.release();
    await L.sleep(700);
    L.check('Esc cancels the drag', JSON.stringify(L.stored()) === before);
    L.check('Esc during a drag keeps arrange mode', arrange().active);

    // The dock on the whole left side, music in the top-right corner.
    await dragIsland('dock', [40, H / 2], {hold: false, preview: '09-dock-left-preview'});
    await dragIsland('media', [W - 150, 40], {hold: false});
    L.check('dock on the left edge', groupOf('dock')?.edge === 'left', JSON.stringify(groupOf('dock')));
    L.check('media at the top-right corner', groupOf('media')?.edge === 'top' && groupOf('media')?.anchor === 'end', JSON.stringify(groupOf('media')));
    await L.key(Clutter.KEY_Escape, [], 900);
    L.check('Esc leaves arrange mode', !arrange().active);
    await L.shot('10-dock-left-music-top-right');
    L.log(`groups ${JSON.stringify(L.groups())}`);
    L.log(`work area ${JSON.stringify(L.workArea())}`);
}
export function init() { L.start('scenes', work); }
export async function run() { await new Promise(() => {}); }
