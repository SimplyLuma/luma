// SPDX-License-Identifier: GPL-2.0-or-later
// Every Dash setting against the arrangement model (owner, 2026-09-19): Use
// islands (off: one surface per group, and a full-width bar when spanning,
// arranged groups included), Span full edge, Float outer edges, Protrude
// from edge (windows keep their gap), Padding, Material, the dock and status
// visibility switches, Keep windows out of the Dash, and Position.
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

const REST = ['live', 'media', 'well', 'quick-options', 'clock', 'notifications'];
const set = async (key, value, wait = 1500) => {
    const s = L.settings();
    if (typeof value === 'boolean') s.set_boolean(key, value);
    else if (typeof value === 'number') s.set_int(key, value);
    else s.set_string(key, value);
    await L.sleep(wait);
};
const resetKeys = async keys => {
    keys.forEach(k => L.settings().reset(k));
    await L.sleep(1500);
};

async function work() {
    const shelf = await L.waitForShelf();
    L.notify();
    await L.sleep(1500);
    const W = global.stage.width, H = global.stage.height;
    const bottomGroups = () => shelf._groups.filter(g => g.placement?.edge === 'bottom' && !g.placement.drag);

    // --- Use islands off, spanning: one bar along the bottom.
    await set('shelf-span-full', true);
    await set('shelf-surface-mode', 'connected');
    let groups = bottomGroups();
    L.check('Use islands off + span: one bar holds the bottom band', groups.length === 1 && groups[0].placement.merged,
        JSON.stringify(groups.map(g => g.placement.islands)));
    const bar = groups[0] && L.rectOf(groups[0]);
    L.check('the bar runs the full width (floating ends 14)', bar && bar.x === 14 && bar.x + bar.width === W - 14, JSON.stringify(bar));
    L.check('the bar is one surface: the group paints, the islands do not', groups[0]?.surface._stroke.visible &&
        groups[0].row.get_children().filter(a => a._stroke).every(a => !a._stroke.visible));
    await L.shot('st-bar', {x: 0, y: H - 110, width: W, height: 110});
    // ...with an arranged group elsewhere.
    await L.arrange([{edge: 'top', anchor: 'center', islands: ['clock']},
        {edge: 'bottom', anchor: 'start', islands: ['dock']},
        {edge: 'bottom', anchor: 'end', islands: REST.filter(i => i !== 'clock')}], 2000);
    groups = bottomGroups();
    L.check('arranged: the bottom groups still form one bar', groups.length === 1 && groups[0].placement.merged);
    const topGroup = shelf._groups.find(g => g.placement?.edge === 'top');
    L.check('arranged: the top clock has its own surface', !!topGroup?.surface._stroke.visible);
    await L.shot('st-bar-arranged');
    // Islands on: separate surfaces again.
    await set('shelf-surface-mode', 'separate');
    groups = bottomGroups();
    L.check('Use islands on: separate groups, separate surfaces', groups.length === 2 &&
        groups.every(g => !g.surface._stroke.visible));
    await L.reset(1500);

    // --- Float outer edges (with span).
    await set('shelf-float-ends', false);
    groups = bottomGroups();
    const rects = groups.map(g => L.rectOf(g)).sort((a, b) => a.x - b.x);
    L.check('Float outer edges off: the end islands meet the screen ends', rects[0]?.x === 0 && rects.at(-1).x + rects.at(-1).width === W,
        JSON.stringify(rects));
    const firstIsland = groups.flatMap(g => g.row.get_children()).find(a => a.islandId === 'dock');
    L.check('and their outer corners are square', firstIsland?._radii?.[0] === 0 && firstIsland._radii[3] === 0, JSON.stringify(firstIsland?._radii));
    await L.shot('st-float-off', {x: 0, y: H - 110, width: W, height: 110});
    await set('shelf-float-ends', true);
    const rects2 = bottomGroups().map(g => L.rectOf(g)).sort((a, b) => a.x - b.x);
    L.check('Float outer edges on: 14 from the screen ends', rects2[0]?.x === 14 && rects2.at(-1).x + rects2.at(-1).width === W - 14,
        JSON.stringify(rects2));
    await resetKeys(['shelf-span-full', 'shelf-float-ends', 'shelf-surface-mode']);

    // --- Protrude from edge: islands touch the edge, windows keep the gap.
    await set('shelf-edge-mode', 'protruding', 2000);
    const pr = bottomGroups().map(g => L.rectOf(g));
    L.check('Protrude: the islands touch the bottom edge', pr.every(r => r.y + r.height === H), JSON.stringify(pr));
    let wa = L.workArea();
    const padding = L.settings().get_int('shelf-padding');
    const thickness = 36 + 2 * padding;
    L.check('Protrude: windows keep the padding from the Dash and reach every empty edge',
        wa.y === 0 && wa.x === 0 && W - (wa.x + wa.width) === 0 && H - (wa.y + wa.height) === thickness + padding,
        JSON.stringify(wa));
    await L.shot('st-protrude', {x: 0, y: H - 110, width: W, height: 110});
    await resetKeys(['shelf-edge-mode']);

    // --- Padding.
    await set('shelf-padding', 16, 2000);
    wa = L.workArea();
    L.check('Padding 16: the band is 14 + 68 + 16 deep, the empty edges nothing', H - (wa.y + wa.height) === 98 && wa.y === 0,
        JSON.stringify(wa));
    const dock = L.rectOf(shelf.islandActorFor('dock'));
    L.check('Padding 16: islands are 68 thick', dock.height === 68, JSON.stringify(dock));
    await resetKeys(['shelf-padding']);

    // --- Material.
    await set('shelf-material', 'glass');
    L.check('Material: every group row carries it', shelf._groups.every(g => g.row.has_style_class_name('luma-shelf-material-glass')));
    await resetKeys(['shelf-material']);

    // --- Dock and status visibility.
    await set('shelf-dock-visible', false);
    L.check('Dock hidden: no dock is placed', !shelf.islandActorFor('dock').get_parent() || !shelf.islandActorFor('dock').mapped);
    await resetKeys(['shelf-dock-visible']);
    await set('shelf-actions-visible', false);
    L.check('Status hidden: no Quick Options is placed', !shelf._actionsIsland.mapped);
    L.check('Status hidden: the dock stays', shelf.islandActorFor('dock').mapped);
    await resetKeys(['shelf-actions-visible']);

    // --- Keep windows out of the Dash (reserve work area).
    await set('shelf-reserve-work-area', false, 2000);
    wa = L.workArea();
    L.check('Reserve off: windows may go under the Dash, keeping only the margin', H - (wa.y + wa.height) === padding, JSON.stringify(wa));
    await resetKeys(['shelf-reserve-work-area']);
    wa = L.workArea();
    L.check('Reserve on again: the band is reserved', H - (wa.y + wa.height) === 80, JSON.stringify(wa));

    // --- Position, once islands are arranged, moves the dock's edge.
    await L.arrange([{edge: 'bottom', anchor: 'center', islands: ['dock', 'clock']},
        {edge: 'top', anchor: 'end', islands: REST.filter(i => i !== 'clock')}], 2000);
    await set('shelf-edge', 'left', 2000);
    const moved = L.stored().find(g => g.islands.includes('dock'));
    L.check('Position left: the dock group moves to the left edge', moved?.edge === 'left' && moved.islands.includes('clock'), JSON.stringify(moved));
    L.check('Position left: other groups stay', L.stored().find(g => g.islands.includes('media'))?.edge === 'top');
    await resetKeys(['shelf-edge']);
    await L.reset(1500);
}
export function init() { L.start('settings', work); }
export async function run() { await new Promise(() => {}); }
