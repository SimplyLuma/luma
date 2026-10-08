// SPDX-License-Identifier: GPL-2.0-or-later
// Dock icons can be rearranged: press an icon, drag it, a gap opens where it
// will land and follows the pointer, and letting go puts it there. The order
// is favorite-apps, and it is what the dock shows after the Shell restarts
// (restart.js, the second half of this scenario). A running app that is not
// pinned is pinned where it is dropped.
//
// Run with the movable-islands harness, restart included:
//   SA_RESTART=1 SA_SCRIPT2=../dock-reorder/restart.js \
//     bash tests/gnome-shell/shelf-arrange/run.sh OUT ../dock-reorder/reorder.js
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from '../shelf-arrange/lib.js';

const shell = new Gio.Settings({schema_id: 'org.gnome.shell'});
const short = id => id.replace(/^org\.(projectluma|oracle)\./, '').replace(/\.desktop$/, '');
const favorites = () => shell.get_strv('favorite-apps').map(short);
const dash = () => Main.overview.dash;
const tiles = () => dash()._box.get_children().filter(c => c.child?._delegate?.app && c.visible && !c.animatingOut);
const tile = name => tiles().find(c => short(c.child._delegate.app.get_id()) === name) ?? null;
// What the dock draws, in order: app names, GAP for the insertion gap, | for
// the separator between pinned and running apps.
const drawn = () => dash()._box.get_children().filter(c => c.visible).map(c =>
    c === dash()._dragPlaceholder ? 'GAP' : c === dash()._separator ? '|'
        : c.child?._delegate?.app ? short(c.child._delegate.app.get_id()) : null).filter(Boolean);
const gapAt = () => drawn().indexOf('GAP');

let dragEvents = [];
Main.overview.connect('item-drag-begin', () => dragEvents.push('begin'));
Main.overview.connect('item-drag-end', () => dragEvents.push('end'));
Main.overview.connect('item-drag-cancelled', () => dragEvents.push('cancelled'));

// Press `name`, lift it the way a hand does, and move toward `toward` until
// `stop(drawn)` holds (the gap is where the person wants it), then let go.
// Returns what was seen on the way: the gap positions, whether the icon left
// behind was dimmed, and the drag events.
async function dragIcon(name, toward, stop = () => false, {hold = 120, steps = null} = {}) {
    dragEvents = [];
    const source = tile(name);
    if (!source)
        return {missing: true, gaps: [], events: []};
    const from = L.centre(L.rectOf(source));
    // About 4 px each 40 ms: a slower hand would hold still long enough
    // (400 ms within 8 px) to enter arrange mode, which is not a drag.
    steps ??= Math.max(4, Math.round(Math.hypot(toward[0] - from[0], toward[1] - from[1]) / 4));
    const gaps = [];
    let dimmed = false, stopped = false;
    await L.move(...from, 100);
    if (GLib.getenv('DOCK_REORDER_DEBUG')) {
        const picked = global.stage.get_actor_at_pos(0, ...from);
        L.log(`drag ${name} from ${from} picks ${picked?.constructor.name} ${picked?.name ?? ''} ${picked?.style_class ?? ''}`);
    }
    await L.press();
    await L.sleep(hold);
    for (let i = 1; i <= steps && !stopped; i++) {
        const t = i / steps;
        await L.move(from[0] + (toward[0] - from[0]) * t, from[1] + (toward[1] - from[1]) * t, 40);
        const gap = gapAt();
        if (gap !== gaps.at(-1))
            gaps.push(gap);
        dimmed ||= source.child.icon.opacity < 128;
        stopped = stop(drawn());
    }
    await L.sleep(300);
    const beforeRelease = drawn();
    await L.release();
    await L.sleep(900);
    // A press held still for 400 ms is arrange mode (ADR-044), not a drag:
    // a scenario that wandered into it says so instead of failing later.
    const arranging = !!Main.shelf._arrange?.active;
    if (arranging) {
        L.log(`drag of ${name} entered arrange mode instead`);
        Main.shelf._arrange.end?.();
        await L.sleep(800);
    }
    return {arranging, gaps: gaps.filter(g => g >= 0), dimmed, stopped, beforeRelease, events: [...dragEvents]};
}

// The gap sits just before `name` in what the dock draws.
const gapBefore = name => d => d.indexOf('GAP') >= 0 && d[d.indexOf('GAP') + 1] === name;
const gapAfter = name => d => d.indexOf('GAP') > 0 && d[d.indexOf('GAP') - 1] === name;

async function scratchApp() {
    // A running app that is not pinned: an ordinary GTK window whose
    // launcher is written now, so the dock shows it in its running part.
    const dir = `${GLib.get_home_dir()}/.local/share/applications`;
    const here = GLib.path_get_dirname(GLib.filename_from_uri(import.meta.url)[0]);
    const win = GLib.canonicalize_filename('../shelf-arrange/win.py', here);
    GLib.file_set_contents(`${dir}/org.oracle.Scratch.desktop`,
        `[Desktop Entry]\nType=Application\nName=Scratch\nExec=python3 ${win} org.oracle.Scratch Scratch\nIcon=org.projectluma.Weather\n`);
    const system = Shell.AppSystem.get_default();
    await L.until(() => system.lookup_app('org.oracle.Scratch.desktop'), 8000);
    const app = system.lookup_app('org.oracle.Scratch.desktop');
    app?.activate();
    await L.until(() => app?.get_state() === Shell.AppState.RUNNING && tile('Scratch'), 15000);
    await L.sleep(800);
    return app;
}

async function work() {
    await L.waitForShelf();
    const start = favorites();
    L.log(`start ${start} | drawn ${drawn()}`);
    L.check('the dock shows the pinned apps in their saved order',
        tiles().map(c => short(c.child._delegate.app.get_id())).join() === start.join(), `${drawn()}`);
    await L.shot('dock-start', L.rectOf(Main.shelf._dockIsland));
    const [A, B, C] = start;
    const first = L.rectOf(tile(A));

    // 1. C is dragged in front of A: the saved order is C, A, B, ...
    let seen = await dragIcon(C, [first.x - first.width, first.y + first.height / 2], gapBefore(A));
    L.log(`C before A: ${JSON.stringify(seen)}`);
    L.check('pressing and dragging a dock icon starts a drag', seen.events[0] === 'begin', seen.events.join());
    L.check('a gap opens and follows the pointer', seen.gaps.length >= 2 && seen.gaps.every((g, i) => i === 0 || g < seen.gaps[i - 1]),
        `gaps ${seen.gaps}`);
    L.check('the icon left behind is dimmed while it is dragged', seen.dimmed);
    L.check('dropping in the gap is accepted, not snapped back', seen.events.join() === 'begin,end', seen.events.join());
    L.check('C dropped before A is saved as C, A, B, ...',
        favorites().join() === [C, A, B, ...start.slice(3)].join(), `${favorites()}`);
    L.check('the dock draws the new order at once', tiles().map(c => short(c.child._delegate.app.get_id())).slice(0, 3).join() === [C, A, B].join(), `${drawn()}`);
    await L.shot('dock-c-before-a', L.rectOf(Main.shelf._dockIsland));

    // 2. A is dragged rightwards, past B and D, and dropped after D.
    const D = start[3];
    let order = favorites();
    const last = L.rectOf(tiles().at(-1));
    seen = await dragIcon(A, [last.x + last.width * 2, last.y + last.height / 2], gapAfter(D));
    const expected = order.filter(n => n !== A);
    expected.splice(expected.indexOf(D) + 1, 0, A);
    L.check('A dragged rightwards and dropped after D lands after D', favorites().join() === expected.join(),
        `${favorites()} (gaps ${seen.gaps}; ${seen.events})`);

    // 3. Dropping an icon back where it was changes nothing.
    order = favorites();
    const own = L.rectOf(tile(order[2]));
    // Past the drag threshold (8 px), still over its own tile.
    seen = await dragIcon(order[2], [own.x + own.width / 2 + 14, own.y + own.height / 2], () => false, {steps: 4});
    L.check('an icon dropped back on its own place leaves the order alone',
        seen.events[0] === 'begin' && !seen.arranging && favorites().join() === order.join(), `${favorites()} (${seen.events})`);

    // 4. Past the separator, into the running apps: it stays pinned, last.
    const scratch = await scratchApp();
    L.log(`scratch running=${scratch?.get_state() === Shell.AppState.RUNNING} drawn ${drawn()}`);
    if (L.check('a running app that is not pinned shows after the separator',
        drawn().indexOf('Scratch') > drawn().indexOf('|') && drawn().includes('|'), `${drawn()}`)) {
        order = favorites();
        const s = L.rectOf(tile('Scratch'));
        seen = await dragIcon(order[0], [s.x + s.width / 2, s.y + s.height / 2], () => false);
        L.check('a pinned icon dragged past the separator stays pinned, last',
            favorites().join() === [...order.slice(1), order[0]].join(), `${favorites()} (gaps ${seen.gaps}; ${seen.events})`);

        // 5. The running app dragged into the pinned apps is pinned there.
        order = favorites();
        seen = await dragIcon('Scratch', [L.rectOf(tile(order[0])).x - 20, s.y + s.height / 2], gapAfter(order[0]));
        L.check('a running app dragged in among the pinned apps is pinned where it was dropped',
            favorites().join() === [order[0], 'Scratch', ...order.slice(1)].join(), `${favorites()} (gaps ${seen.gaps}; ${seen.events})`);
        await L.shot('dock-pinned-by-drag', L.rectOf(Main.shelf._dockIsland));
    }

    // 6. The folders rail is not a place for an app: dropped there, the drag
    // is refused and the order is untouched.
    const downloads = `${GLib.get_home_dir()}/Downloads`;
    GLib.mkdir_with_parents(downloads, 0o755);
    L.settings().set_strv('dock-folders', [GLib.filename_to_uri(downloads, null)]);
    L.settings().set_boolean('dock-folders-separate', false);
    await L.until(() => Main.shelf.dockFolders?.rail?.visible && Main.shelf.dockFolders.rail.width > 0, 5000);
    await L.sleep(800);
    const rail = Main.shelf.dockFolders?.rail;
    if (rail?.visible && rail.width > 0) {
        order = favorites();
        const r = L.rectOf(rail);
        seen = await dragIcon(order.at(-1), [r.x + r.width / 2, r.y + r.height / 2], () => false);
        L.check('an app dropped on the folders rail is refused and the order kept',
            favorites().join() === order.join() && seen.events.includes('cancelled'), `${favorites()} ${seen.events}`);
        // And an icon carried over the rail and back still lands in the gap.
        order = favorites();
        const back = L.rectOf(tile(order[0]));
        dragEvents = [];
        const from = L.centre(L.rectOf(tile(order.at(-1))));
        await L.move(...from, 100);
        await L.press();
        await L.sleep(120);
        const path = [[r.x + r.width / 2, from[1]], [back.x - 20, from[1]]];
        let at = from;
        for (const to of path) {
            for (let i = 1; i <= 40; i++) {
                await L.move(at[0] + (to[0] - at[0]) * i / 40, at[1] + (to[1] - at[1]) * i / 40, 30);
                if (to === path[1] && gapBefore(order[0])(drawn()))
                    break;
            }
            at = to;
        }
        await L.sleep(300);
        await L.release();
        await L.sleep(900);
        L.check('an icon carried across the folders rail and back is dropped where the gap is',
            favorites().join() === [order.at(-1), ...order.slice(0, -1)].join(), `${favorites()} ${dragEvents}`);
        L.check('the folder stays pinned', L.settings().get_strv('dock-folders').length === 1);
        await L.shot('dock-with-folders', L.rectOf(Main.shelf._dockIsland));
    } else {
        L.check('the folders rail is shown to drag across', false, `visible ${rail?.visible} width ${rail?.width}`);
    }
    L.settings().reset('dock-folders');
    await L.sleep(800);

    // 7. Arrange mode keeps its meanings: holding an icon still for 400 ms
    // enters it (ADR-044), and while arranging a drag moves the island,
    // never an icon.
    order = favorites();
    const held = L.centre(L.rectOf(tile(order[1])));
    await L.move(...held, 100);
    await L.press();
    await L.sleep(900);
    const entered = !!Main.shelf._arrange?.active;
    await L.release();
    await L.sleep(500);
    L.check('holding a dock icon still still enters arrange mode', entered);
    L.check('entering arrange mode does not reorder the dock', favorites().join() === order.join(), `${favorites()}`);
    Main.shelf._arrange?.end?.();
    await L.sleep(800);
    L.settings().reset('shelf-arrangement');
    await L.sleep(1200);

    // 8. After arranging, reordering still works.
    order = favorites();
    seen = await dragIcon(order[1], [L.rectOf(tile(order[0])).x - 20, held[1]], gapBefore(order[0]));
    L.check('after arrange mode, a drag still reorders',
        favorites().join() === [order[1], order[0], ...order.slice(2)].join(), `${favorites()} (gaps ${seen.gaps}; ${seen.events})`);

    GLib.file_set_contents(`${L.OUT}/saved-order.json`, JSON.stringify({start, saved: favorites()}));
    L.log(`saved ${favorites()}`);
}

L.start('reorder', work, 6000);
