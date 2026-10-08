// SPDX-License-Identifier: GPL-2.0-or-later
// Drag and idle cost (ADR-044 verification 9): while an island is dragged
// along the bottom band, a group on another band is never laid out again;
// a drop writes the arrangement once and moves the struts once; an idle
// shelf does no work.
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

async function work() {
    const shelf = await L.waitForShelf();
    await L.arrange([{edge: 'top', anchor: 'center', islands: ['clock']},
        {edge: 'bottom', anchor: 'start', islands: ['dock']},
        {edge: 'bottom', anchor: 'end', islands: ['live', 'media', 'well', 'quick-options', 'notifications']}], 2500);
    const top = shelf._groups.find(g => g.placement?.edge === 'top');
    const counts = {relayout: 0, writes: 0, struts: 0};
    const ids = [];
    ids.push([top, top.connect('queue-relayout', () => counts.relayout++)]);
    for (const island of top.row.get_children())
        ids.push([island, island.connect('queue-relayout', () => counts.relayout++)]);
    const settings = shelf._settings;
    const sid = settings.connect('changed::shelf-arrangement', () => counts.writes++);
    for (const actor of shelf._strutActors.values())
        ids.push([actor, actor.connect('notify::allocation', () => counts.struts++)]);

    // Idle: nothing happens for three seconds.
    await L.sleep(3000);
    L.check('idle: no relayout, no write, no strut change', counts.relayout + counts.writes + counts.struts === 0, JSON.stringify(counts));

    shelf._arrange.begin({select: 'media'});
    await L.sleep(800);
    Object.assign(counts, {relayout: 0, writes: 0, struts: 0});
    const m = L.rectOf(shelf.partActorFor('media'));
    await L.move(...L.centre(m), 100);
    await L.press();
    // Across the bottom band: several attach and free targets.
    for (let i = 1; i <= 60; i++)
        await L.move(m.x + m.width / 2 - i * 30, m.y + m.height / 2, 16);
    await L.sleep(300);
    L.check('during the drag the top group is never laid out again', counts.relayout === 0, JSON.stringify(counts));
    L.check('during the drag nothing is written', counts.writes === 0, JSON.stringify(counts));
    L.check('during the drag the struts do not move', counts.struts === 0, JSON.stringify(counts));
    const previews = shelf._arrange._drag ? 'dragging' : 'not dragging';
    await L.release();
    await L.sleep(1200);
    L.check('one write per drop', counts.writes === 1, JSON.stringify(counts));
    L.check('struts unchanged by a drop on the same band', counts.struts === 0, `${previews} ${JSON.stringify(counts)}`);
    // A drop onto a new band moves the struts once.
    Object.assign(counts, {relayout: 0, writes: 0, struts: 0});
    const d = L.rectOf(shelf.partActorFor('dock'));
    await L.move(...L.centre(d), 100);
    await L.press();
    for (let i = 1; i <= 30; i++)
        await L.move(d.x + d.width / 2 + (40 - d.x - d.width / 2) * i / 30, d.y + d.height / 2 + (720 - d.y - d.height / 2) * i / 30, 16);
    await L.sleep(300);
    L.check('dragging onto a new band: struts still unchanged', counts.struts === 0, JSON.stringify(counts));
    await L.release();
    await L.sleep(1500);
    L.check('dock dropped on the left', L.stored().find(g => g.islands.includes('dock'))?.edge === 'left');
    L.check('one write for that drop', counts.writes === 1, JSON.stringify(counts));
    L.log(`strut allocation changes on the drop: ${counts.struts}`);
    shelf._arrange.end();
    settings.disconnect(sid);
    ids.forEach(([a, id]) => a.disconnect(id));
}
export function init() { L.start('cost', work); }
export async function run() { await new Promise(() => {}); }
