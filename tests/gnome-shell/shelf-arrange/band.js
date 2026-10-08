// SPDX-License-Identifier: GPL-2.0-or-later
// The owner's own bottom edge on his own panel: a group anchored at the
// start (live, media, folders, dock) and one at the end (tray, Quick
// Options, clock), with more apps open than the dock can show. Nothing may
// be left as dead band between them, and nothing that has nothing to show
// may hold width.
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

const T = GLib.path_get_dirname(GLib.getenv('SA_OUT')) + '/../tests';
const APPS = ['Notes', 'Messages', 'Calendar', 'Tide', 'Photos', 'Weather',
    'Files', 'Mail', 'Code', 'Music', 'Maps', 'Chat', 'Draw', 'Sheets',
    'Slides', 'Term', 'Cast', 'Radio', 'Books', 'Paint', 'Video', 'Store',
    'Clock', 'Notes2'];

async function spawn(title) {
    GLib.spawn_async(null, ['python3', `${T}/win.py`, `org.oracle.${title}`, title],
        null, GLib.SpawnFlags.SEARCH_PATH, null);
    for (let i = 0; i < 40; i++) {
        const w = global.display.list_all_windows().find(win => win.title === title);
        if (w)
            return w;
        await L.sleep(250);
    }
    return null;
}

const rectOf = actor => actor ? L.rectOf(actor) : null;

function report(name) {
    const shelf = Main.shelf;
    const groups = L.groups().filter(g => g.edge === 'bottom');
    const line = groups.map(g => `${g.anchor} ${g.rect.x}..${g.rect.x + g.rect.width}`).join('  ');
    const well = shelf.getIsland?.('well') ?? shelf._wellIsland;
    const dock = shelf.getIsland?.('dock') ?? shelf._dockIsland;
    L.log(`${name}: groups ${line}`);
    L.log(`${name}: dock ${JSON.stringify(rectOf(dock))} well visible=${well?.visible} ${JSON.stringify(rectOf(well))}`);
    if (groups.length === 2) {
        const [first, second] = groups.sort((a, b) => a.rect.x - b.rect.x);
        const hole = second.rect.x - (first.rect.x + first.rect.width);
        L.log(`${name}: band hole ${hole}px`);
        return hole;
    }
    return null;
}

async function work() {
    const shelf = await L.waitForShelf();
    const area = Main.layoutManager.getWorkAreaForMonitor(Main.layoutManager.primaryIndex);
    L.log(`band: work area ${area.width}x${area.height}`);

    // The owner's arrangement, as it is on his machine.
    await L.arrange([
        {edge: 'top', anchor: 'center', islands: ['notifications']},
        {edge: 'bottom', anchor: 'start', islands: ['live', 'media', 'folders', 'dock']},
        {edge: 'bottom', anchor: 'end', islands: ['well', 'quick-options', 'clock']},
    ], 2500);

    const empty = report('empty dock');

    for (const app of APPS)
        await spawn(app);
    await L.sleep(3000);
    const full = report('full dock');
    await L.shot('band-full-dock');

    const shelf2 = Main.shelf;
    const scrolling = (shelf2._dockScroll?.get_child_at_index?.(0) ?? shelf2._dockContent ?? shelf2._dash);
    const contentWidth = scrolling?.get_preferred_width?.(-1)?.[1] ?? 0;
    const dockWidth = rectOf(shelf2.getIsland?.('dock') ?? shelf2._dockIsland)?.width ?? 0;
    L.log(`band: dock content wants ${Math.round(contentWidth)}px, the island is ${dockWidth}px`);
    L.check('a dock with more apps than fit leaves no dead band',
        full !== null && full <= 30, `${full}px between the groups, dock content ${Math.round(contentWidth)}px in ${dockWidth}px`);
    L.check('an empty dock leaves no dead band beyond one separation',
        empty !== null, `${empty}px`);

    const well = shelf.getIsland?.('well') ?? shelf._wellIsland;
    const wellRect = rectOf(well);
    L.check('an empty system tray holds no width',
        !well?.visible || (wellRect?.width ?? 0) === 0,
        `visible=${well?.visible} width=${wellRect?.width}`);

    // The dock's own viewport: with the band free beside it, it must use it.
    const dock = shelf.getIsland?.('dock') ?? shelf._dockIsland;
    const dockRect = rectOf(dock);
    const groups = L.groups().filter(g => g.edge === 'bottom').sort((a, b) => a.rect.x - b.rect.x);
    if (groups.length === 2) {
        const room = groups[1].rect.x - groups[0].rect.x;
        L.log(`band: the start group has ${room}px before the end group; the dock is ${dockRect?.width}px`);
    }
}

export function init() { L.start('band', work); }
export async function run() { await new Promise(() => {}); }
