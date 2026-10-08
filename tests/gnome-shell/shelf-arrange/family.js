// SPDX-License-Identifier: GPL-2.0-or-later
// Live extensions are one family in one slot. Run with SA_LIVE=0 (a player
// is playing, no live activity yet):
//   - arrange mode shows one Live extensions placeholder in the family's
//     slot and never what is playing
//   - dragging it moves the family
//   - with a live activity too, both sit side by side in the one slot and
//     dragging either moves both
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

const FAMILY = ['live', 'media'];

function familyItems(shelf) {
    return shelf._groups.flatMap(g => g.row.get_children())
        .filter(a => a.visible && a.get_stage() && FAMILY.includes(a.islandId) && !a._isGhost);
}

async function dragTo(shelf, id, to) {
    const r = L.rectOf(shelf.partActorFor(id));
    const [x0, y0] = L.centre(r);
    await L.move(x0, y0, 60);
    await L.press();
    await L.move(x0 + 12, y0 - 12, 60);
    await L.move(x0 + 20, y0 - 20, 80);
    for (let i = 1; i <= 20; i++)
        await L.move(x0 + 20 + (to[0] - x0 - 20) * i / 20, y0 - 20 + (to[1] - y0 + 20) * i / 20, 14);
    await L.sleep(350);
    await L.release();
    await L.sleep(900);
}

async function work() {
    const shelf = await L.waitForShelf();
    const m = Main.layoutManager.primaryMonitor;
    for (let i = 0; i < 40 && !shelf.getIsland('media')?.visible; i++)
        await L.sleep(250);
    L.check('a player is playing (the media island shows)', !!shelf.getIsland('media')?.visible);
    L.check('no live activity yet', !shelf.getIsland('live')?.visible);
    shelf._arrange.begin({select: 'media'});
    await L.sleep(900);
    const items = familyItems(shelf);
    L.check('arrange mode shows no live content', items.length === 0, items.map(a => a.name).join());
    L.check('and one Live extensions placeholder', !!shelf._liveGhost?.mapped);
    await L.shot('family-one-item');
    await dragTo(shelf, 'media', [m.x + m.width - 160, m.y + 40]);
    const placed = L.stored().find(g => g.islands.includes('media'));
    L.check('dragging it moves the family (live goes with it)', placed?.edge === 'top' && placed.islands.includes('live'),
        JSON.stringify(placed));
    shelf._arrange.end();
    await L.sleep(800);
    // A live activity starts.
    GLib.spawn_async(null, ['python3', `${GLib.path_get_dirname(GLib.getenv('SA_OUT'))}/../tests/fixtures.py`],
        ['SA_MEDIA=0', 'SA_LIVE=1', `HOME=${GLib.get_home_dir()}`, `DBUS_SESSION_BUS_ADDRESS=${GLib.getenv('DBUS_SESSION_BUS_ADDRESS')}`],
        GLib.SpawnFlags.SEARCH_PATH, null);
    for (let i = 0; i < 40 && !shelf.getIsland('live')?.visible; i++)
        await L.sleep(250);
    await L.sleep(1000);
    L.check('a live activity shows', !!shelf.getIsland('live')?.visible);
    shelf._arrange.begin({select: 'live'});
    await L.sleep(900);
    L.check('with both live, arrange mode still shows only the placeholder',
        familyItems(shelf).length === 0 && !!shelf._liveGhost?.mapped);
    shelf._arrange.end();
    await L.sleep(900);
    const both = familyItems(shelf).map(L.rectOf).sort((a, b) => a.x - b.x);
    L.check('outside arrange mode both sit side by side in the one slot', both.length === 2 &&
        both[1].x - (both[0].x + both[0].width) <= 12 && Math.abs(both[0].y - both[1].y) <= 1, JSON.stringify(both));
    shelf._arrange.begin({select: 'live'});
    await L.sleep(900);
    await L.shot('family-two-items');
    await dragTo(shelf, 'live', [m.x + 160, m.y + m.height - 40]);
    const p2 = L.stored().find(g => g.islands.includes('live'));
    L.check('dragging the live island moves both', p2?.edge === 'bottom' && p2.islands.includes('media') &&
        L.stored().filter(g => g.islands.some(id => FAMILY.includes(id))).length === 1, JSON.stringify(L.stored()));
    shelf._arrange.end();
    await L.sleep(600);
    await L.reset(800);
}
export function init() { L.start('family', work); }
export async function run() { await new Promise(() => {}); }
