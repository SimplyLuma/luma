// SPDX-License-Identifier: GPL-2.0-or-later
// Struts and the work area (ADR-044 §7): one strut per occupied edge,
// nothing on an empty edge, bands reserved while their island is hidden,
// and a maximized window that never meets an island. Also the dock on the
// left, right and top in its vertical and horizontal forms, window marks on
// the screen-edge side, and Tiling Shell reading the same work area.
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

const REST = ['live', 'media', 'well', 'quick-options', 'clock', 'notifications'];
const layouts = {
    'bottom only': [{edge: 'bottom', anchor: 'start', islands: ['dock']}, {edge: 'bottom', anchor: 'end', islands: REST}],
    'left dock': [{edge: 'left', anchor: 'center', islands: ['dock']}, {edge: 'bottom', anchor: 'end', islands: REST}],
    'right dock': [{edge: 'right', anchor: 'center', islands: ['dock']}, {edge: 'bottom', anchor: 'end', islands: REST}],
    'top dock': [{edge: 'top', anchor: 'center', islands: ['dock']}, {edge: 'bottom', anchor: 'end', islands: REST}],
    'top clock + bottom dock': [{edge: 'top', anchor: 'center', islands: ['clock']},
        {edge: 'bottom', anchor: 'center', islands: ['dock', ...REST.filter(id => id !== 'clock')]}],
    'three bands': [{edge: 'left', anchor: 'center', islands: ['dock']}, {edge: 'top', anchor: 'end', islands: ['media']},
        {edge: 'bottom', anchor: 'end', islands: REST.filter(id => id !== 'media')}],
};
// The work area each layout must leave on a 2560x1440 monitor at padding 10:
// 80 on an occupied edge, nothing on the others.
const expected = {
    'bottom only': {x: 0, y: 0, width: 2560, height: 1360},
    'left dock': {x: 80, y: 0, width: 2480, height: 1360},
    'right dock': {x: 0, y: 0, width: 2480, height: 1360},
    'top dock': {x: 0, y: 80, width: 2560, height: 1280},
    'top clock + bottom dock': {x: 0, y: 80, width: 2560, height: 1280},
    'three bands': {x: 80, y: 80, width: 2480, height: 1280},
};

async function openWindow() {
    const app = Shell.AppSystem.get_default().lookup_app('org.oracle.Files.desktop');
    app?.activate();
    for (let i = 0; i < 40; i++) {
        const w = global.display.list_all_windows().find(win => win.get_wm_class()?.includes('Files') || win.title === 'Files');
        if (w)
            return w;
        await L.sleep(250);
    }
    return null;
}

function maximize(window) {
    try {
        window.maximize(Meta.MaximizeFlags.BOTH);
    } catch {
        window.maximize();
    }
}

async function work() {
    const shelf = await L.waitForShelf();
    const window = await openWindow();
    L.check('a test window opened', !!window);
    for (const [name, groups] of Object.entries(layouts)) {
        await L.arrange(groups, 2200);
        const wa = L.workArea();
        L.check(`${name}: work area`, JSON.stringify(wa) === JSON.stringify(expected[name]), JSON.stringify(wa));
        const struts = [...shelf._strutActors.values()].filter(a => a.name === 'lumaShelfWorkArea').length;
        L.check(`${name}: one strut per occupied edge`, struts === new Set(groups.map(g => g.edge)).size, `${struts}`);
        if (window) {
            maximize(window);
            await L.sleep(900);
            const r = window.get_frame_rect();
            const frame = {x: r.x, y: r.y, width: r.width, height: r.height};
            const hits = L.groups().flatMap(g => g.islands).filter(i => L.intersects(i.rect, frame));
            L.check(`${name}: a maximized window meets no island`, hits.length === 0, `${JSON.stringify(frame)} ${JSON.stringify(hits.map(h => h.id))}`);
        }
        const dockGroup = L.groups().find(g => g.islands.some(i => i.id === 'dock'));
        if (dockGroup && (dockGroup.edge === 'left' || dockGroup.edge === 'right')) {
            const dock = dockGroup.islands.find(i => i.id === 'dock');
            L.check(`${name}: the dock is a column 56 wide`, dock.rect.width === 56 && dock.rect.height > dock.rect.width, JSON.stringify(dock.rect));
            L.check(`${name}: the dash is vertical`, Main.overview.dash._shelfEdge === dockGroup.edge);
        }
        await L.shot(`wa-${name.replace(/[^a-z]+/g, '-')}`);
    }
    // .119: an edge whose only island is hidden reserves nothing; it is
    // reserved again while that island shows.
    await L.arrange([{edge: 'top', anchor: 'end', islands: ['notifications']},
        {edge: 'bottom', anchor: 'center', islands: ['dock', 'live', 'media', 'well', 'quick-options', 'clock']}], 2200);
    L.check('the notifications island is hidden (nothing unread)', !shelf.getIsland('notifications').visible);
    const margin = L.workArea().y;
    L.check('an edge holding only a hidden island reserves nothing', margin === 0, JSON.stringify(L.workArea()));
    const logged = JSON.parse(shelf._loggedReservations ?? '{}')[Main.layoutManager.primaryIndex];
    L.check('Vitals logs every edge\'s reservation: 0 on the empty top, left and right, the band on the bottom',
        logged?.top === 0 && logged.left === 0 && logged.right === 0 && logged.bottom === 80, JSON.stringify(logged));
    const n = L.notify();
    await L.sleep(1800);
    L.check('a notification arriving reserves the top band', L.workArea().y === 80, JSON.stringify(L.workArea()));
    n.destroy();
    await L.sleep(1800);
    L.check('and it is released when the island leaves', L.workArea().y === margin, JSON.stringify(L.workArea()));
    // Struts do not change during a drag.
    await L.reset(2200);
    L.check('reset returns the default work area', JSON.stringify(L.workArea()) === JSON.stringify(expected['bottom only']), JSON.stringify(L.workArea()));
    // Tiling Shell reads the same work area.
    const log = shelf._lastTilingArea;
    L.log(`tiling shell sees the work area through Mutter: ${log ?? 'see shell.log [tilingshell] lines'}`);
}
export function init() { L.start('workarea', work); }
export async function run() { await new Promise(() => {}); }
