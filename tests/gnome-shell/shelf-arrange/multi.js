// SPDX-License-Identifier: GPL-2.0-or-later
// Two monitors (ADR-044 §8): a group dropped on the second display is pinned
// to its identity and reserves work area there; an edge shared between the
// displays offers no zone; a pinned display that is absent falls back to the
// primary at the same edge and anchor, and the group goes back when the
// display is there again. (Virtual monitors cannot be unplugged, so absence
// is a pin to an identity no connected display has.)
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

async function work() {
    const shelf = await L.waitForShelf();
    // The second virtual monitor can arrive a moment after the shelf.
    for (let i = 0; i < 40 && shelf.monitors.length < 2; i++)
        await L.sleep(250);
    await L.sleep(1000);
    const monitors = shelf.monitors;
    L.log(`monitors ${JSON.stringify(monitors)}`);
    L.check('two monitors', monitors.length === 2);
    const second = monitors.find(m => !m.primary);
    const primary = monitors.find(m => m.primary);
    L.check('the shared edges are known', primary.sharedEdges.length === 1 && second.sharedEdges.length === 1,
        JSON.stringify([primary.sharedEdges, second.sharedEdges]));
    L.check('each display has an identity', !!second.id && second.id !== primary.id, second.id);

    // Arrange mode offers no zone on a shared edge.
    shelf._arrange.begin({select: 'media'});
    await L.sleep(800);
    const zones = shelf._arrange._zones.map(z => z._zone);
    L.check('zones on both monitors', new Set(zones.map(z => z.monitor)).size === 2, `${zones.length}`);
    if (shelf.canReserveSharedEdges)
        L.check('with Mutter reservations, shared edges offer zones', zones.some(z => monitors[z.monitor].sharedEdges.includes(z.edge)));
    else
        L.check('no zone on a shared edge', !zones.some(z => monitors[z.monitor].sharedEdges.includes(z.edge)));
    await L.shot('mm-1-arrange-two-monitors');
    // Drag the media island to the second display's top centre.
    const m = L.rectOf(shelf.partActorFor('media'));
    const to = [second.x + second.width / 2, second.y + 42];
    await L.move(...L.centre(m), 100);
    await L.press();
    for (let i = 1; i <= 30; i++)
        await L.move(m.x + m.width / 2 + (to[0] - m.x - m.width / 2) * i / 30, m.y + m.height / 2 + (to[1] - m.y - m.height / 2) * i / 30, 16);
    await L.sleep(500);
    await L.release();
    await L.sleep(1000);
    shelf._arrange.end();
    await L.sleep(800);
    const g = L.stored().find(x => x.islands.includes('media'));
    L.check('media pinned to the second display', g?.display === second.id && g.edge === 'top', JSON.stringify(g));
    const drawn = L.groups().find(x => x.islands.some(i => i.id === 'media'));
    L.check('media drawn on the second display', drawn?.monitor === second.index, JSON.stringify(drawn));
    L.check('the second display reserves its top band', L.workArea(second.index).y === second.y + 80, JSON.stringify(L.workArea(second.index)));
    L.check('the primary keeps its own work area (nothing on its empty top)', L.workArea(primary.index).y === primary.y, JSON.stringify(L.workArea(primary.index)));
    await L.shot('mm-2-media-on-second');

    // Absent display: the same group falls back to the primary.
    const stored = L.stored();
    const away = stored.map(x => x.islands.includes('media') ? {...x, display: 'DP-99|NOPE|0x0|gone'} : x);
    await L.arrange(away, 2000);
    const fell = L.groups().find(x => x.islands.some(i => i.id === 'media'));
    L.check('absent display: drawn on the primary, same edge and anchor', fell?.monitor === primary.index && fell.edge === 'top' && fell.anchor === 'center', JSON.stringify(fell));
    L.check('the stored pin is kept', L.stored().find(x => x.islands.includes('media')).display === 'DP-99|NOPE|0x0|gone');
    await L.shot('mm-3-fallback');
    await L.arrange(stored, 2000);
    const back = L.groups().find(x => x.islands.some(i => i.id === 'media'));
    L.check('display back: the group returns', back?.monitor === second.index, JSON.stringify(back));
    // A saved edge that became shared falls back to the bottom.
    const sharedEdge = second.sharedEdges[0];
    await L.arrange(stored.map(x => x.islands.includes('media') ? {...x, edge: sharedEdge} : x), 2000);
    const sh = L.groups().find(x => x.islands.some(i => i.id === 'media'));
    if (shelf.canReserveSharedEdges) {
        L.check('a shared saved edge is kept (reserved through Mutter)', sh?.edge === sharedEdge && sh.monitor === second.index, JSON.stringify(sh));
        const wa = L.workArea(second.index);
        const depth = sharedEdge === 'left' ? wa.x - second.x : second.x + second.width - (wa.x + wa.width);
        L.check('and its band is reserved', depth >= 70, JSON.stringify(wa));
    } else {
        L.check('a shared saved edge falls back to the bottom', sh?.edge === 'bottom' && sh.monitor === second.index, JSON.stringify(sh));
    }
    await L.reset(1500);
}
export function init() { L.start('multi', work); }
export async function run() { await new Promise(() => {}); }
