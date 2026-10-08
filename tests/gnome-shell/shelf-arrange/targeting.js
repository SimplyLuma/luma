// SPDX-License-Identifier: GPL-2.0-or-later
// Drop targeting (owner, 2026-09-19): a wide dock grabbed by its right end
// and aimed with the pointer at an empty zone lands in that zone, although
// its centre is far away; the ghost shown before the drop is exactly where
// the island lands; with free placement off nothing lands at a free place;
// with it on, a free place is kept.
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

const REST = ['live', 'media', 'well', 'quick-options', 'clock', 'notifications'];

async function holdAndCarry(shelf, from, to, steps = 24) {
    await L.move(...from, 80);
    await L.press();
    for (let i = 1; i <= steps; i++)
        await L.move(from[0] + (to[0] - from[0]) * i / steps, from[1] + (to[1] - from[1]) * i / steps, 16);
    await L.sleep(450);
}

function ghostRect(shelf) {
    const gap = shelf._gap;
    return gap?.visible && gap.mapped ? L.rectOf(gap) : null;
}

async function work() {
    const shelf = await L.waitForShelf();
    L.notify();
    await L.sleep(1500);
    const W = global.stage.width;
    // The dock alone at the bottom-left; the rest at the bottom-right.
    await L.arrange([{edge: 'bottom', anchor: 'start', islands: ['dock']},
        {edge: 'bottom', anchor: 'end', islands: REST}], 2000);
    for (const [zone, aim] of [['top', 'center'], ['top', 'start'], ['top', 'end'], ['bottom', 'center']]) {
        shelf._arrange.begin({select: 'dock'});
        await L.sleep(500);
        const z = shelf._arrange._zones.map(x => x._zone).find(x => x.edge === zone && x.anchor === aim);
        if (!z) {
            L.check(`${zone} ${aim}: zone drawn`, false);
            shelf._arrange.end();
            continue;
        }
        const dock = L.rectOf(shelf.islandActorFor('dock'));
        // Grab the dock 12px from its right end.
        const grab = [dock.x + dock.width - 12, dock.y + dock.height / 2];
        const aimAt = L.centre(z.rect);
        await holdAndCarry(shelf, grab, aimAt);
        const target = shelf._arrange._drag?.target;
        const ghost = ghostRect(shelf);
        const centre = shelf._arrange._drag ? (() => {
            const g = shelf._arrange._dragGroup();
            return g ? L.centre(L.rectOf(g)) : null;
        })() : null;
        L.check(`dock grabbed by its right end, pointer in the ${zone} ${aim} zone: that zone`,
            target?.kind === 'snap' && target.edge === zone && target.anchor === aim,
            `${JSON.stringify(target)} centre ${JSON.stringify(centre)} pointer ${JSON.stringify(aimAt)}`);
        await L.shot(`tg-dock-${zone}-${aim}-preview`);
        await L.release();
        await L.sleep(1200);
        shelf._arrange.end();
        await L.sleep(800);
        const landed = L.rectOf(shelf.islandActorFor('dock'));
        L.check(`${zone} ${aim}: the ghost was exactly where the dock landed`,
            ghost && Math.abs(ghost.x - landed.x) <= 2 && Math.abs(ghost.y - landed.y) <= 2 &&
            Math.abs(ghost.width - landed.width) <= 2 && Math.abs(ghost.height - landed.height) <= 2,
            `ghost ${JSON.stringify(ghost)} landed ${JSON.stringify(landed)}`);
        const g = L.stored().find(x => x.islands.includes('dock'));
        L.check(`${zone} ${aim}: stored`, g?.edge === zone && g.anchor === aim, JSON.stringify(g));
    }
    // A side edge: the ghost of a vertical form is exact too.
    shelf._arrange.begin({select: 'media'});
    await L.sleep(500);
    const media = L.rectOf(shelf.islandActorFor('media'));
    const leftCentre = shelf._arrange._zones.map(x => x._zone).find(x => x.edge === 'left' && x.anchor === 'center');
    await holdAndCarry(shelf, L.centre(media), L.centre(leftCentre.rect));
    const vghost = ghostRect(shelf);
    await L.release();
    await L.sleep(1200);
    // Arrange mode draws the family as its Live extensions placeholder: the
    // drop preview is that slot.
    const vslot = shelf._liveGhost?.mapped ? L.rectOf(shelf._liveGhost) : null;
    shelf._arrange.end();
    await L.sleep(800);
    // The live family lands together: its visible run.
    const vr = ['live', 'media'].map(id => shelf.islandActorFor(id)).filter(a => a?.visible && a.get_stage()).map(L.rectOf);
    const vlanded = {x: Math.min(...vr.map(r => r.x)), y: Math.min(...vr.map(r => r.y)),
        width: Math.max(...vr.map(r => r.x + r.width)) - Math.min(...vr.map(r => r.x)),
        height: Math.max(...vr.map(r => r.y + r.height)) - Math.min(...vr.map(r => r.y))};
    L.check('left centre: the ghost of the column form is where the slot opened',
        vghost && vslot && Math.abs(vghost.y - vslot.y) <= 2 && Math.abs(vghost.height - vslot.height) <= 2 &&
        Math.abs(vghost.x - vslot.x) <= 2, `ghost ${JSON.stringify(vghost)} slot ${JSON.stringify(vslot)}`);
    L.check('left centre: the family lands there together',
        vlanded.x <= 80 && Math.abs(vlanded.y + vlanded.height / 2 - global.stage.height / 2) <= 60,
        JSON.stringify(vlanded));
    // Free placement off: a drop between zones goes to the nearest anchor.
    await L.arrange([{edge: 'bottom', anchor: 'center', islands: ['dock', ...REST]}], 1500);
    shelf._arrange.begin({select: 'clock'});
    await L.sleep(500);
    const clock = L.rectOf(shelf.partActorFor('clock'));
    await holdAndCarry(shelf, L.centre(clock), [W * 0.38, 42]);
    await L.release();
    await L.sleep(1000);
    let c = L.stored().find(x => x.islands.includes('clock'));
    L.check('free placement off: the drop goes to an anchor', c && c.anchor !== 'free', JSON.stringify(c));
    // Free placement on: the free place is kept.
    L.settings().set_boolean('shelf-free-placement', true);
    await L.sleep(800);
    const clock2 = L.rectOf(shelf.partActorFor('clock'));
    await holdAndCarry(shelf, L.centre(clock2), [W * 0.38, 42]);
    await L.release();
    await L.sleep(1000);
    c = L.stored().find(x => x.islands.includes('clock'));
    L.check('free placement on: a free place is kept', c?.anchor === 'free' && Math.abs(c.position - 0.38) < 0.03, JSON.stringify(c));
    // Turning it off draws the free group at its nearest anchor.
    shelf._arrange.end();
    L.settings().set_boolean('shelf-free-placement', false);
    await L.sleep(1200);
    const drawn = shelf._resolved.find(p => p.islands.includes('clock'));
    L.check('free placement turned off: the free group is drawn at its nearest anchor', drawn?.anchor === 'center', JSON.stringify(drawn));
    await L.reset(1000);
}
export function init() { L.start('targeting', work); }
export async function run() { await new Promise(() => {}); }
