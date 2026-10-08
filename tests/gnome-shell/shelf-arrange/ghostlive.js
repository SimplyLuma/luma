// SPDX-License-Identifier: GPL-2.0-or-later
// The live extensions slot: with no live extension running, arrange mode
// shows a ghost "Live extensions" island in the live slot; it can be moved
// like any island, and live extensions then appear where it was put. Run
// with SA_LIVE=0 (no broker until the script starts one).
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

async function work() {
    const shelf = await L.waitForShelf();
    await L.sleep(1000);
    L.check('no live extension is running', !shelf._liveIsland?.visible);
    L.check('no ghost outside arrange mode', !shelf._liveGhost?.mapped);
    shelf._arrange.begin({select: 'live'});
    await L.sleep(900);
    const ghost = shelf._liveGhost;
    L.check('arrange mode shows the live slot', !!ghost?.mapped && ghost.islandId === 'live');
    L.check('the ghost says what it is', ghost?._label?.text === 'Live extensions');
    await L.shot('ghost-live-slot');
    const g = L.rectOf(ghost);
    const W = global.stage.width;
    await L.move(...L.centre(g), 80);
    await L.press();
    for (let i = 1; i <= 24; i++)
        await L.move(g.x + g.width / 2 + (W - 120 - g.x - g.width / 2) * i / 24, g.y + g.height / 2 + (42 - g.y - g.height / 2) * i / 24, 16);
    await L.sleep(500);
    await L.shot('ghost-live-drag');
    await L.release();
    await L.sleep(1000);
    const placed = L.stored().find(x => x.islands.includes('live'));
    L.check('the live slot moves to the top-right corner', placed?.edge === 'top' && placed.anchor === 'end', JSON.stringify(placed));
    shelf._arrange.end();
    await L.sleep(800);
    L.check('the ghost leaves with arrange mode', !shelf._liveGhost?.mapped);
    // A live extension now appears in that slot.
    const [ok] = GLib.spawn_async(null, ['python3', `${GLib.path_get_dirname(GLib.getenv('SA_OUT'))}/../tests/fixtures.py`],
        ['SA_MEDIA=0', 'SA_LIVE=1', `HOME=${GLib.get_home_dir()}`, `DBUS_SESSION_BUS_ADDRESS=${GLib.getenv('DBUS_SESSION_BUS_ADDRESS')}`],
        GLib.SpawnFlags.SEARCH_PATH, null);
    for (let i = 0; i < 40 && !shelf._liveIsland?.visible; i++)
        await L.sleep(250);
    await L.sleep(1200);
    L.check('a live extension appears', !!shelf._liveIsland?.visible, `${ok}`);
    const live = shelf._liveIsland && L.rectOf(shelf._liveIsland);
    L.check('in the slot it was given (top-right corner)', live && live.y < 80 && live.x + live.width > W - 40, JSON.stringify(live));
    await L.shot('ghost-live-real');
    await L.reset(800);
}
export function init() { L.start('ghostlive', work); }
export async function run() { await new Promise(() => {}); }
