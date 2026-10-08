// SPDX-License-Identifier: GPL-2.0-or-later
// The owner's findings on .120:
//   ends      every edge's start and end reach the corner (the gutter in),
//             by arrangement and by a real drag, with the top and bottom
//             edges holding groups at their centres
//   bars      without islands, bars on adjacent edges meet as one L: flush,
//             no overlap
//   editor    arrange mode shows the Live extensions and Notifications
//             placeholders, always, in their slot, and never live content
//   empty     an item with nothing to show takes no length, gap or divider,
//             at the start, middle and end of a group and as a group alone
//   hover     hovering the notifications island moves nothing, on every edge,
//             also after a click opened and closed the drawer
//   insert    dropping into a group opens the slot under the pointer, at
//             every index, for every anchor on every edge
//   trio      dragging Quick Options carries the clock and status icons with
//             it; Shift takes one part out; the group handle moves the group
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {getNotificationTray} from 'resource:///org/gnome/shell/ui/lumaNotificationBeacon.js';
import * as A from 'resource:///org/gnome/shell/ui/shelfArrangement.js';
import * as L from './lib.js';

const GUTTER = 14;
const V = e => A.isVertical(e);
const along = (r, e) => V(e) ? [r.y, r.y + r.height] : [r.x, r.x + r.width];
const mAlong = (m, e) => V(e) ? [m.y, m.y + m.height] : [m.x, m.x + m.width];
const shown = a => a?.visible && a.get_stage() && a.mapped;
const STATUS = ['well', 'quick-options', 'clock'];

async function ends(shelf, m) {
    for (const edge of A.EDGES) {
        for (const anchor of ['start', 'end']) {
            // The top and bottom edges hold groups at their centres, like
            // the owner's (the live family on top, Quick Options below).
            const groups = [{edge, anchor, islands: ['dock']}];
            if (edge !== 'top')
                groups.push({edge: 'top', anchor: 'center', islands: ['live', 'media']});
            if (edge !== 'bottom')
                groups.push({edge: 'bottom', anchor: 'center', islands: ['well', 'quick-options', 'clock', 'notifications']});
            else
                groups.push({edge: 'top', anchor: 'end', islands: ['well', 'quick-options', 'clock', 'notifications']});
            await L.arrange(groups, 1300);
            const [a0, a1] = along(L.rectOf(shelf.islandActorFor('dock')), edge);
            const [m0, m1] = mAlong(m, edge);
            const off = anchor === 'start' ? a0 - m0 : m1 - a1;
            L.check(`ends: ${edge} ${anchor} reaches the corner (${GUTTER} px in)`, Math.abs(off - GUTTER) <= 1, `${off}`);
            // The same by a real drag into the zone, for the side edges.
            if (!V(edge))
                continue;
            await L.arrange(groups.map(g => g.islands[0] === 'dock' ? {...g, edge: 'bottom', anchor: 'start'} : g), 1200);
            shelf._arrange.begin({select: 'dock'});
            await L.sleep(400);
            const zone = shelf._arrange._zones.map(z => z._zone).find(z => z.monitor === m.index && z.edge === edge && z.anchor === anchor);
            const r = L.rectOf(shelf.partActorFor('dock'));
            if (zone) {
                const [x0, y0] = L.centre(r);
                await L.move(x0, y0, 60); await L.press();
                await L.move(x0 + 14, y0 - 14, 60); await L.move(x0 + 24, y0 - 24, 80);
                const [tx, ty] = L.centre(zone.rect);
                for (let i = 1; i <= 18; i++)
                    await L.move(x0 + 24 + (tx - x0 - 24) * i / 18, y0 - 24 + (ty - y0 + 24) * i / 18, 14);
                await L.sleep(300);
                await L.release();
                await L.sleep(700);
            }
            shelf._arrange.end();
            await L.sleep(800);
            const [b0, b1] = along(L.rectOf(shelf.islandActorFor('dock')), edge);
            const dragged = anchor === 'start' ? b0 - m0 : m1 - b1;
            L.check(`ends: dragged to ${edge} ${anchor}, the dock sits in the corner (${GUTTER} px in)`,
                !!zone && Math.abs(dragged - GUTTER) <= 1, `${dragged} zone ${JSON.stringify(zone?.rect)}`);
            if (anchor === 'start')
                await L.shot(`r-ends-${edge}-${anchor}`, {x: m.x, y: m.y, width: m.width, height: 260});
        }
    }
}

async function bars(shelf, m) {
    const s = L.settings();
    s.set_string('shelf-surface-mode', 'connected');
    for (const [protrude, float] of [[true, false], [false, false], [false, true]]) {
        s.set_string('shelf-edge-mode', protrude ? 'protruding' : 'floating');
        s.set_boolean('shelf-float-ends', float);
        for (const [side, flat] of [['left', 'top'], ['right', 'top'], ['left', 'bottom'], ['right', 'bottom']]) {
            await L.arrange([{edge: side, anchor: 'center', islands: ['dock']},
                {edge: flat, anchor: 'center', islands: ['live', 'media', 'well', 'quick-options', 'clock', 'notifications']}], 1500);
            const gs = L.groups();
            const sideBar = gs.find(g => g.edge === side)?.rect, flatBar = gs.find(g => g.edge === flat)?.rect;
            const name = `bars ${protrude ? 'protrude' : 'float'}${float ? '+float ends' : ''} ${flat}-${side}`;
            if (!sideBar || !flatBar) {
                L.check(`${name}: both bars drawn`, false);
                continue;
            }
            const meet = flat === 'top' ? sideBar.y - (flatBar.y + flatBar.height) : flatBar.y - (sideBar.y + sideBar.height);
            const outer = side === 'left' ? flatBar.x - sideBar.x : (sideBar.x + sideBar.width) - (flatBar.x + flatBar.width);
            const overlap = L.intersects(sideBar, flatBar);
            L.check(`${name}: the bars meet flush at the corner, as one L`, meet === 0 && outer === 0 && !overlap,
                `meet ${meet} outer ${outer} side ${JSON.stringify(sideBar)} flat ${JSON.stringify(flatBar)}`);
            // One L: no rim across the joint (the side bar's joined end and
            // the flat bar's inner side over the side bar), one rim elsewhere.
            const sideG = Main.shelf._groups.find(g => g.placement?.edge === side);
            const flatG = Main.shelf._groups.find(g => g.placement?.edge === flat);
            const sideStyle = sideG?.surface._stroke.get_style() ?? '';
            const [cx, , cw] = flatG?.surface._stroke.get_clip() ?? [0, 0, 0];
            const copy = flatG?.surface._joinStrokes?.find(c => c.visible);
            L.check(`${name}: no rim across the joint`, sideStyle.includes(`border-${flat}-width: 0px`) &&
                flatG?.surface._stroke.has_clip && !!copy && (side === 'left' ? cx > 0 : cx === 0 && cw < flatBar.width),
                `side "${sideStyle}" flat clip ${cx},${cw} copy ${!!copy}`);
            if (side === 'left' && flat === 'top')
                await L.shot(`r-bars-${protrude ? 'p' : 'f'}${float ? 'e' : ''}`, {x: m.x, y: m.y, width: 600, height: 400});
        }
    }
    for (const key of ['shelf-surface-mode', 'shelf-edge-mode', 'shelf-float-ends'])
        s.reset(key);
    await L.sleep(800);
}

async function editor(shelf, m) {
    await L.arrange([{edge: 'bottom', anchor: 'center', islands: ['dock']},
        {edge: 'bottom', anchor: 'end', islands: ['well', 'quick-options', 'clock']},
        {edge: 'top', anchor: 'center', islands: ['live', 'media', 'notifications']}], 1400);
    for (const withContent of [true, false]) {
        const n = withContent ? L.notify('Mouse battery low', '10% left') : null;
        if (!withContent)
            shelf._liveIndicator.visible = false;
        if (withContent)
            L.check('editor: the notification comes to rest in the island', await L.until(() => shelf._beacon.visible, 10000));
        await L.sleep(1500);
        shelf._arrange.begin({select: 'dock'});
        await L.sleep(900);
        const nGhost = shelf._beaconGhost, lGhost = shelf._liveGhost;
        const name = withContent ? 'editor, with a notification and a live activity' : 'editor, with nothing live';
        L.check(`${name}: the Notifications placeholder shows`, shown(nGhost), `${nGhost?.visible} ${nGhost?.mapped}`);
        L.check(`${name}: the Live extensions placeholder shows`, shown(lGhost), `${lGhost?.visible} ${lGhost?.mapped}`);
        L.check(`${name}: no live content is drawn`,
            !shown(shelf._beaconIsland) && !shown(shelf._liveIsland) && !shown(shelf.getIsland('media')),
            `${shown(shelf._beaconIsland)} ${shown(shelf._liveIsland)} ${shown(shelf.getIsland('media'))}`);
        const top = L.groups().find(g => g.edge === 'top');
        const ids = top?.islands.map(i => i.id) ?? [];
        L.check(`${name}: the placeholders stand in their slot, nothing else there`,
            top && nGhost && lGhost && top.islands.length === 2 && L.inside(L.rectOf(nGhost), top.rect, 1) &&
            L.inside(L.rectOf(lGhost), top.rect, 1), JSON.stringify(ids));
        await L.shot(`r-editor-${withContent ? 'content' : 'empty'}`, {x: m.x, y: m.y, width: m.width, height: 140});
        shelf._arrange.end();
        await L.sleep(900);
        L.check(`${name}: the placeholders leave with arrange mode`, !shown(nGhost) && !shown(lGhost));
        if (withContent) {
            L.check(`${name}: the notification shows again outside it`, shown(shelf._beaconIsland));
            n.destroy();
            Main.messageTray._cards?.forEach(entry => entry.notification.destroy());
            await L.sleep(900);
        }
    }
    shelf._liveIndicator.visible = true;
    await L.sleep(800);
}

async function empty(shelf, m) {
    const IG = A.ISLAND_GAP;
    const gapBetween = (a, b) => { const ra = L.rectOf(a), rb = L.rectOf(b); return rb.x - (ra.x + ra.width); };
    L.check('empty: no notification is waiting', !shelf._beacon.visible);
    const cases = [
        ['start', [{edge: 'bottom', anchor: 'center', islands: ['notifications', 'dock']}]],
        ['middle', [{edge: 'bottom', anchor: 'center', islands: ['dock', 'notifications', 'well', 'quick-options', 'clock']}]],
        ['end', [{edge: 'bottom', anchor: 'center', islands: ['dock', 'notifications']}]],
    ];
    for (const [where, groups] of cases) {
        await L.arrange([...groups, {edge: 'top', anchor: 'center', islands: ['live', 'media']}, ...(where === 'middle' ? []
            : [{edge: 'bottom', anchor: 'end', islands: ['well', 'quick-options', 'clock']}])], 1400);
        const dock = L.rectOf(shelf.islandActorFor('dock'));
        if (where === 'middle') {
            const g = gapBetween(shelf.islandActorFor('dock'), shelf.islandActorFor('quick-options'));
            L.check(`empty ${where}: dock and Quick Options are one gap apart (${IG})`, g === IG, `${g}`);
        } else {
            const c = dock.x + dock.width / 2 - (m.x + m.width / 2);
            L.check(`empty ${where}: the dock alone is centred (no space for the empty item)`, Math.abs(c) <= 1, `${c}`);
        }
    }
    // A group of its own with nothing to show, beside Quick Options at the end.
    await L.arrange([{edge: 'bottom', anchor: 'center', islands: ['dock']},
        {edge: 'bottom', anchor: 'end', islands: ['well', 'quick-options', 'clock']},
        {edge: 'bottom', anchor: 'end', islands: ['notifications']},
        {edge: 'top', anchor: 'center', islands: ['live', 'media']}], 1400);
    const qo = L.rectOf(shelf.islandActorFor('quick-options'));
    const endGap = m.x + m.width - (qo.x + qo.width);
    L.check('empty: a group with nothing to show takes no length or separation', Math.abs(endGap - GUTTER) <= 1, `${endGap}`);
    // It opens its space when a notification comes.
    const n = L.notify();
    L.check('empty: the notification comes to rest in the island', await L.until(() => shelf._beacon.visible, 10000));
    await L.sleep(800);
    const qo2 = L.rectOf(shelf.islandActorFor('quick-options'));
    const nr = L.rectOf(shelf.islandActorFor('notifications'));
    L.check('empty: a notification arriving opens its space', qo2.x < qo.x - 40 || nr.x + nr.width < qo2.x - 20,
        `${qo.x} -> ${qo2.x}; notifications ${JSON.stringify(nr)}; groups ${JSON.stringify(L.groups().filter(g => g.edge === 'bottom').map(g => [g.rect, g.islands.map(i => i.id)]))}`);
    n.destroy();
    await L.sleep(1500);
    // A live extension that starts beside the dock opens its space: it
    // grows in while the dock glides aside, and takes nothing when gone.
    await L.arrange([{edge: 'bottom', anchor: 'center', islands: ['dock', 'live', 'media']},
        {edge: 'bottom', anchor: 'end', islands: ['well', 'quick-options', 'clock', 'notifications']}], 1400);
    const liveInd = shelf._liveIndicator;
    liveInd.visible = false;
    const mediaIsland = shelf.getIsland('media');
    if (mediaIsland)
        mediaIsland.visible = false;
    await L.sleep(1000);
    const d0 = L.rectOf(shelf.islandActorFor('dock'));
    L.check('empty: with no live extension the dock alone is centred', Math.abs(d0.x + d0.width / 2 - (m.x + m.width / 2)) <= 1,
        JSON.stringify(d0));
    liveInd.visible = true;
    await L.sleep(40);
    const island = shelf._liveIsland;
    const growing = island.scale_x < 1 || island.opacity < 255;
    const drawnX = () => Math.round(shelf.islandActorFor('dock').get_transformed_position()[0]);
    const early = drawnX();
    await L.sleep(700);
    const dockMoving = early !== drawnX() && early !== d0.x ? true : early !== drawnX();
    L.check('empty: a live extension arriving grows in and the dock glides aside', growing && dockMoving &&
        island.scale_x === 1 && island.opacity === 255, `${growing} ${dockMoving} ${island.scale_x} ${island.opacity}`);
}

async function hover(shelf, m) {
    const tray = getNotificationTray();
    for (const edge of A.EDGES) {
        const rest = edge === 'bottom' ? 'top' : 'bottom';
        await L.arrange([{edge, anchor: 'center', islands: ['dock', 'notifications']},
            {edge: rest, anchor: 'center', islands: ['live', 'media', 'well', 'quick-options', 'clock']}], 1300);
        const n = L.notify('Tea is ready', 'Your timer finished');
        await L.until(() => shelf._beacon.visible, 10000);
        await L.sleep(800);
        const island = shelf.getIsland('notifications');
        L.check(`hover ${edge}: the notification island shows`, shown(island));
        const snap = () => [island, shelf.islandActorFor('dock'), shelf.groupForActor(island)].filter(Boolean)
            .map(a => [Math.round(a.translation_x), Math.round(a.translation_y), JSON.stringify(L.rectOf(a))]);
        for (const afterClick of [false, true]) {
            await L.move(m.x + m.width / 2, m.y + m.height / 2, 300);
            if (afterClick) {
                const r = L.rectOf(island);
                await L.move(r.x + r.width / 2, r.y + r.height / 2, 200);
                await L.press(); await L.sleep(40); await L.release();
                await L.sleep(600);
                tray.close();
                await L.sleep(500);
                await L.move(m.x + m.width / 2, m.y + m.height / 2, 300);
            }
            const before = JSON.stringify(snap());
            const r = L.rectOf(island);
            const seen = [];
            for (let i = 0; i <= 10; i++) {
                const t = i / 10;
                await L.move(r.x + 4 + (r.width - 8) * t, r.y + 4 + (r.height - 8) * t, 40);
                seen.push(JSON.stringify(snap()));
            }
            await L.sleep(400);
            seen.push(JSON.stringify(snap()));
            L.check(`hover ${edge}${afterClick ? ', after a click': ''}: nothing moves or grows`,
                seen.every(x => x === before), `${before} / ${seen.find(x => x !== before)}`);
        }
        n.destroy();
        await L.sleep(900);
    }
}

async function insert(shelf, m) {
    for (const edge of A.EDGES) {
        for (const anchor of A.ZONE_ANCHORS) {
            // The host group, with the status island's three parts as the
            // separate items they are.
            const host = ['dock', 'live', 'media', 'well', 'quick-options', 'clock'];
            // What can be dropped between: the live family counts once.
            const items = ['dock', 'live', 'well', 'quick-options', 'clock'];
            const spans = {live: ['live', 'media']};
            const other = edge === 'bottom' ? 'top' : 'bottom';
            const v = V(edge);
            for (let k = 0; k <= items.length; k++) {
                await L.arrange([{edge, anchor, islands: host},
                    {edge: other, anchor: 'center', islands: ['notifications']}], 1200);
                shelf._arrange.begin({select: 'notifications'});
                await L.sleep(700);
                const group = shelf.groupForActor(shelf.islandActorFor('dock'));
                // The seam before item k, as the group is drawn right now
                // (the slot the drag opens moves the items along).
                const seam = () => {
                    const rs = items.map(id => {
                        const parts = (spans[id] ?? [id]).map(x => L.rectOf(shelf.partActorFor(x)));
                        const x = Math.min(...parts.map(r => r.x)), y = Math.min(...parts.map(r => r.y));
                        return {x, y, width: Math.max(...parts.map(r => r.x + r.width)) - x,
                            height: Math.max(...parts.map(r => r.y + r.height)) - y};
                    });
                    const at = i => v ? [rs[i].y, rs[i].y + rs[i].height] : [rs[i].x, rs[i].x + rs[i].width];
                    const n = items.length;
                    const gr = L.rectOf(group);
                    const span = v ? [gr.y, gr.y + gr.height] : [gr.x, gr.x + gr.width];
                    // Three quarters into the last item (a quarter into the
                    // first): past its midpoint, and well inside the group,
                    // where the join and separate bands meet.
                    const at0 = at(0), atLast = at(n - 1);
                    const pos = k === 0 ? at0[0] + (at0[1] - at0[0]) * 0.25
                        : k === n ? atLast[1] - (atLast[1] - atLast[0]) * 0.25
                            : (at(k - 1)[1] + at(k)[0]) / 2;
                    const cross = v ? gr.x + gr.width / 2 : gr.y + gr.height / 2;
                    return v ? [cross, pos] : [pos, cross];
                };
                const src = L.rectOf(shelf.partActorFor('notifications'));
                const [x0, y0] = L.centre(src);
                await L.move(x0, y0, 60); await L.press();
                await L.move(x0 + 14, y0 + (edge === 'top' ? 14 : -14), 60);
                await L.move(x0 + 24, y0 + (edge === 'top' ? 24 : -24), 80);
                const steps = 16;
                const [sx, sy] = [x0 + 24, y0 + (edge === 'top' ? 24 : -24)];
                // The group as it sits with the island lifted out: the
                // bands beside a group are measured against that, so the
                // aim is taken once, before any slot opens.
                await L.sleep(500);
                const target = seam();
                for (let i = 1; i <= steps; i++)
                    await L.move(sx + (target[0] - sx) * i / steps, sy + (target[1] - sy) * i / steps, 16);
                await L.sleep(500);
                const hint = shelf._arrange._hint?.visible ? shelf._arrange._hint.text : '';
                const ctxg = shelf._arrange._context(shelf._arrange._drag?.ids ?? []).groups
                    .find(gg => gg.islands.some(i => i.id === 'dock'));
                const probe = `pointer ${Math.round(target[v ? 1 : 0])} rect ${JSON.stringify(ctxg?.rect)} band ${JSON.stringify(ctxg?.bandRect)} rest ${shelf._arrange._drag?.rest?.size}`;
                await L.release();
                await L.sleep(700);
                shelf._arrange.end();
                await L.sleep(500);
                const g = L.stored().find(x => x.islands.includes('dock'));
                const expected = [...host];
                const before = items[k] ?? null;
                expected.splice(before ? expected.indexOf(before) : expected.length, 0, 'notifications');
                const got = g?.islands ?? [];
                L.check(`insert ${edge} ${anchor}: at index ${k} of ${items.length}, the slot follows the pointer`,
                    JSON.stringify(got) === JSON.stringify(expected),
                    `${JSON.stringify(got)} want ${JSON.stringify(expected)} hint "${hint}" ${probe}`);
            }
        }
    }
}

async function trio(shelf, m) {
    await L.arrange([{edge: 'bottom', anchor: 'center', islands: ['dock']},
        {edge: 'bottom', anchor: 'end', islands: ['well', 'quick-options', 'clock']},
        {edge: 'top', anchor: 'center', islands: ['live', 'media', 'notifications']}], 1300);
    const carry = async (id, shift, to) => { // shift: hold Shift during the drag
        shelf._arrange.begin({select: id});
        await L.sleep(500);
        const [x0, y0] = L.centre(L.rectOf(shelf.partActorFor(id)));
        await L.move(x0, y0, 60);
        const kb = L.input().keyboard;
        const t = () => GLib.get_monotonic_time();
        if (shift)
            kb.notify_keyval(t(), 0xffe1, Clutter.KeyState.PRESSED);
        await L.sleep(50);
        await L.press();
        await L.move(x0 - 14, y0 - 14, 60); await L.move(x0 - 24, y0 - 24, 80);
        for (let i = 1; i <= 18; i++)
            await L.move(x0 - 24 + (to[0] - x0 + 24) * i / 18, y0 - 24 + (to[1] - y0 + 24) * i / 18, 14);
        await L.sleep(300);
        await L.release();
        if (shift)
            kb.notify_keyval(t(), 0xffe1, Clutter.KeyState.RELEASED);
        await L.sleep(700);
        shelf._arrange.end();
        await L.sleep(600);
    };
    const leftStart = [m.x + 20, m.y + m.height / 2 - 200];
    // An island dragged by itself moves alone; the handle moves the group.
    await carry('clock', false, leftStart);
    let g = L.stored().find(x => x.islands.includes('clock'));
    const q = L.stored().find(x => x.islands.includes('quick-options'));
    L.check('trio: dragging the clock takes only the clock', g?.edge === 'left' && g.islands.length === 1 &&
        q?.edge === 'bottom', `${JSON.stringify(g)} ${JSON.stringify(q)}`);
    // The group handle: easy to hit (at least 40 x 20) and it moves the group.
    await L.arrange([{edge: 'bottom', anchor: 'center', islands: ['dock', 'well', 'quick-options', 'clock']},
        {edge: 'top', anchor: 'center', islands: ['live', 'media', 'notifications']}], 1300);
    shelf._arrange.begin({select: 'dock'});
    await L.sleep(700);
    const grip = shelf._arrange._grips?.find(gp => gp._group.islands.some(i => i.id === 'dock'));
    const gr = grip && L.rectOf(grip);
    L.check('trio: the group handle is easy to hit', gr && gr.width >= 40 && gr.height >= 20, JSON.stringify(gr));
    if (grip) {
        const [x0, y0] = L.centre(gr);
        await L.move(x0, y0, 200);
        await L.sleep(300);
        L.check('trio: hovering the handle says what it does', !!shelf._arrange._hint?.visible &&
            /group/i.test(shelf._arrange._hint.text ?? ''), shelf._arrange._hint?.text);
        await L.shot('r-grip-hover', {x: m.x, y: m.y + m.height - 200, width: m.width, height: 200});
        await L.press();
        await L.move(x0 - 14, y0 - 14, 60); await L.move(x0 - 30, y0 - 30, 80);
        const to = [m.x + 20, m.y + m.height / 2];
        for (let i = 1; i <= 18; i++)
            await L.move(x0 - 30 + (to[0] - x0 + 30) * i / 18, y0 - 30 + (to[1] - y0 + 30) * i / 18, 14);
        await L.sleep(300);
        await L.release();
        await L.sleep(700);
    }
    shelf._arrange.end();
    await L.sleep(600);
    g = L.stored().find(x => x.islands.includes('dock'));
    L.check('trio: the handle moves the whole group', g?.edge === 'left' && STATUS.every(id => g.islands.includes(id)),
        JSON.stringify(g));
}

async function tray(shelf, m) {
    const GROUP = ['dock', 'quick-options', 'clock'];
    for (const edge of A.EDGES) {
        const other = edge === 'bottom' ? 'top' : 'bottom';
        for (let k = 0; k <= 2; k++) {
            await L.arrange([{edge, anchor: 'end', islands: GROUP},
                {edge: other, anchor: 'center', islands: ['live', 'media', 'notifications']}], 1300);
            shelf._arrange.begin({select: 'clock'});
            await L.sleep(700);
            const v = V(edge);
            const [x0, y0] = L.centre(L.rectOf(shelf.partActorFor('clock')));
            await L.move(x0, y0, 60);
            await L.press();
            const away = edge === 'top' ? 22 : -22;
            await L.move(x0 + 12, y0 + away / 2, 60);
            await L.move(x0 + 22, y0 + away, 800);
            // Where the group's items sit now that the clock is carried and
            // the group has closed up behind it.
            const rest = ['dock', 'quick-options'].map(id => L.rectOf(shelf.partActorFor(id)));
            const at = i => v ? [rest[i].y, rest[i].y + rest[i].height] : [rest[i].x, rest[i].x + rest[i].width];
            const gr = L.rectOf(shelf.groupForActor(shelf.islandActorFor('quick-options')));
            const span = v ? [gr.y, gr.y + gr.height] : [gr.x, gr.x + gr.width];
            // Aim at the group's start, between its two items, and at its
            // end: the slot opens where the pointer is.
            const pos = k === 0 ? at(0)[0] + 6 : k === 1 ? (at(0)[1] + at(1)[0]) / 2 : at(1)[1] - 6;
            const cross = v ? gr.x + gr.width / 2 : gr.y + gr.height / 2;
            const target = v ? [cross, pos] : [pos, cross];
            for (let i = 1; i <= 20; i++)
                await L.move(x0 + 22 + (target[0] - x0 - 22) * i / 20, y0 + away + (target[1] - y0 - away) * i / 20, 16);
            await L.sleep(400);
            const ctx = shelf._arrange._context(shelf._arrange._drag?.ids ?? []).groups
                .filter(gg => gg.edge === edge)
                .map(gg => gg.islands.map(i => [i.id, Math.round(v ? i.rect.y + i.rect.height / 2 : i.rect.x + i.rect.width / 2)]));
            const seen = JSON.stringify(shelf._arrange._drag?.target ?? null);
            const hint = shelf._arrange._hint?.visible ? shelf._arrange._hint.text : '';
            await L.release();
            await L.sleep(700);
            shelf._arrange.end();
            await L.sleep(500);
            const g = L.stored().find(x => x.islands.includes('clock'));
            const want = ['dock', 'quick-options'];
            want.splice(k, 0, 'clock');
            L.check(`tray ${edge}: dropped at index ${k}, the group reads ${want.join(', ')}`,
                JSON.stringify(g?.islands) === JSON.stringify(want) && g.edge === edge,
                `${JSON.stringify(g)} hint "${hint}" pointer ${Math.round(target[v ? 1 : 0])} mids ${JSON.stringify(ctx)} target ${seen}`);
            // The tooltip names the edge, the corner and the place.
            L.check(`tray ${edge}: the tooltip names where it will land (index ${k})`,
                /edge/.test(hint) && /^.*Join (before|after|above|below) /.test(hint) &&
                hint.includes(k === 2 ? 'Quick Options' : 'Dock'), `"${hint}"`);
        }
    }
    // The group handle: one per group of two or more, above the group, and
    // the status island's parts each count as an item.
    await L.arrange([{edge: 'bottom', anchor: 'end', islands: ['well', 'quick-options', 'clock']},
        {edge: 'bottom', anchor: 'center', islands: ['dock']}], 1300);
    shelf._arrange.begin({select: 'dock'});
    await L.sleep(800);
    const grips = shelf._arrange._grips ?? [];
    const statusGrip = grips.find(gp => gp._group.islands.some(i => i.id === 'quick-options'));
    const sr = statusGrip && L.rectOf(statusGrip);
    const island = L.rectOf(shelf.islandActorFor('quick-options'));
    L.check('tray: the status group has a handle above it', !!statusGrip && sr.width >= 40 && sr.height >= 20 &&
        sr.y + sr.height <= island.y && sr.x >= island.x - 40 && sr.x + sr.width <= island.x + island.width + 40,
        `${JSON.stringify(sr)} island ${JSON.stringify(island)} grips ${grips.length}`);
    L.check('tray: the handle is drawn, not clipped', !!statusGrip?.mapped && !!statusGrip?.get_paint_visibility(),
        `${statusGrip?.mapped}`);
    shelf._arrange.end();
    await L.sleep(600);
}

// Join or separate: beside any group, on both sides, the inner band puts
// the island in the group and the outer band gives it its own island there.
// The preview says which, and the tooltip names it.
async function bands(shelf, m) {
    const HOST = ['quick-options', 'clock'];
    for (const edge of A.EDGES) {
        const other = edge === 'bottom' ? 'top' : 'bottom';
        const v = V(edge);
        for (const side of ['before', 'after']) {
            for (const kind of ['join', 'separate']) {
                await L.arrange([{edge, anchor: 'center', islands: HOST},
                    {edge: other, anchor: 'center', islands: ['dock', 'live', 'media', 'notifications']}], 1300);
                shelf._arrange.begin({select: 'dock'});
                await L.sleep(700);
                const host = shelf.groupForActor(shelf.islandActorFor('quick-options'));
                const hr = L.rectOf(host);
                const span = v ? [hr.y, hr.y + hr.height] : [hr.x, hr.x + hr.width];
                const out = kind === 'join' ? 14 : 60;
                const pos = side === 'before' ? span[0] - out : span[1] + out;
                const cross = v ? hr.x + hr.width / 2 : hr.y + hr.height / 2;
                const target = v ? [cross, pos] : [pos, cross];
                const [x0, y0] = L.centre(L.rectOf(shelf.partActorFor('dock')));
                await L.move(x0, y0, 60);
                await L.press();
                await L.move(x0 + 14, y0 - 14, 60);
                await L.move(x0 + 24, y0 - 24, 80);
                for (let i = 1; i <= 20; i++)
                    await L.move(x0 + 24 + (target[0] - x0 - 24) * i / 20, y0 - 24 + (target[1] - y0 + 24) * i / 20, 16);
                await L.sleep(500);
                const hint = shelf._arrange._hint?.visible ? shelf._arrange._hint.text : '';
                const kindSeen = shelf._arrange._drag?.target?.kind ?? null;
                // The preview: one island outline for a join, two for a
                // separate one (the carried island beside the group).
                const previewGroups = Main.shelf._groups.filter(g => g.placement && !g.dragging &&
                    g.visible && g.placement.edge === edge).length;
                await L.release();
                await L.sleep(800);
                shelf._arrange.end();
                await L.sleep(500);
                const stored = L.stored().filter(g => g.edge === edge);
                const withDock = stored.find(g => g.islands.includes('dock'));
                const name = `bands ${edge} ${side} ${kind}`;
                if (kind === 'join') {
                    const want = [...HOST];
                    want.splice(side === 'before' ? 0 : HOST.length, 0, 'dock');
                    L.check(`${name}: the island joins the group`, stored.length === 1 &&
                        JSON.stringify(withDock?.islands) === JSON.stringify(want),
                        `${JSON.stringify(stored)} hint "${hint}" kind ${kindSeen} preview groups ${previewGroups}`);
                    L.check(`${name}: the tooltip offers to join`, /Join/.test(hint), `"${hint}"`);
                    L.check(`${name}: the preview shows one island`, previewGroups === 1, `${previewGroups}`);
                } else {
                    const host2 = stored.find(g => g.islands.includes('quick-options'));
                    const order = stored.indexOf(withDock) < stored.indexOf(host2);
                    L.check(`${name}: the island stands on its own beside the group`, stored.length === 2 &&
                        JSON.stringify(withDock?.islands) === JSON.stringify(['dock']) &&
                        JSON.stringify(host2?.islands) === JSON.stringify(HOST) &&
                        order === (side === 'before'),
                        `${JSON.stringify(stored)} hint "${hint}" kind ${kindSeen} preview groups ${previewGroups}`);
                    L.check(`${name}: the tooltip offers a new island`, /New island/.test(hint), `"${hint}"`);
                    L.check(`${name}: the preview shows two islands`, previewGroups === 2, `${previewGroups}`);
                }
            }
        }
    }
}

// A part separated from the status island stays separate: after the drop,
// after a rebuild of the shelf, and after the arrangement is read again as
// it would be at the next login. Nudging it back merges it again.
async function apart(shelf, m) {
    for (const edge of A.EDGES) {
        const other = edge === 'bottom' ? 'top' : 'bottom';
        const v = V(edge);
        await L.arrange([{edge, anchor: 'end', islands: ['well', 'quick-options', 'clock']},
            {edge: other, anchor: 'center', islands: ['dock', 'live', 'media', 'notifications']}], 1400);
        shelf._arrange.begin({select: 'clock'});
        await L.sleep(700);
        const [x0, y0] = L.centre(L.rectOf(shelf.partActorFor('clock')));
        await L.move(x0, y0, 60);
        await L.press();
        const away = edge === 'top' ? 20 : -20;
        await L.move(x0 + 12, y0 + away / 2, 60);
        await L.move(x0 + 22, y0 + away, 400);
        // Well outside the group's inner end (an end group has the screen's
        // corner on its other side): its own island there.
        const hr = L.rectOf(shelf.groupForActor(shelf.islandActorFor('quick-options')));
        const span = v ? [hr.y, hr.y + hr.height] : [hr.x, hr.x + hr.width];
        const pos = span[0] - 60;
        const cross = v ? hr.x + hr.width / 2 : hr.y + hr.height / 2;
        const target = v ? [cross, pos] : [pos, cross];
        for (let i = 1; i <= 20; i++)
            await L.move(x0 + 22 + (target[0] - x0 - 22) * i / 20, y0 + away + (target[1] - y0 - away) * i / 20, 16);
        await L.sleep(400);
        const hint = shelf._arrange._hint?.visible ? shelf._arrange._hint.text : '';
        await L.release();
        await L.sleep(800);
        shelf._arrange.end();
        await L.sleep(800);
        const stored = () => L.stored().filter(g => g.edge === edge);
        const two = () => stored().length === 2 &&
            JSON.stringify(stored().find(g => g.islands.includes('clock'))?.islands) === JSON.stringify(['clock']);
        L.check(`apart ${edge}: the drop leaves two islands`, two(), `${JSON.stringify(stored())} hint "${hint}"`);
        // Drawn as two: the clock is not part of the status island.
        const drawnApart = () => shelf.islandActorFor('clock') === shelf._clockIsland &&
            !(shelf._actionsParts ?? []).includes('clock');
        L.check(`apart ${edge}: the clock is drawn as its own island`, drawnApart(),
            `${JSON.stringify(shelf._actionsParts)}`);
        // A rebuild of the shelf (what a monitor change does).
        shelf._sync();
        await L.sleep(600);
        L.check(`apart ${edge}: it survives a rebuild`, two() && drawnApart(), JSON.stringify(stored()));
        // As it would be read at the next login.
        const read = A.normalizeArrangement(L.stored(), {}, A.DEFAULT_ORDER, {freePlacement: false}).groups
            .filter(g => g.edge === edge);
        L.check(`apart ${edge}: it survives being read again`, read.length === 2 &&
            JSON.stringify(read.find(g => g.islands.includes('clock'))?.islands) === JSON.stringify(['clock']),
            JSON.stringify(read));
    }
    // Nudged back: one island again, with the divider between the parts.
    shelf._arrange.begin({select: 'clock'});
    await L.sleep(700);
    const clock = L.rectOf(shelf.partActorFor('clock'));
    const qo = L.rectOf(shelf.islandActorFor('quick-options'));
    const [x0, y0] = L.centre(clock);
    await L.move(x0, y0, 60);
    await L.press();
    await L.move(x0 - 12, y0 - 10, 60);
    await L.move(x0 - 22, y0 - 20, 300);
    const to = [qo.x + 12, qo.y + qo.height / 2];
    for (let i = 1; i <= 20; i++)
        await L.move(x0 - 22 + (to[0] - x0 + 22) * i / 20, y0 - 20 + (to[1] - y0 + 20) * i / 20, 16);
    await L.sleep(400);
    const hint2 = shelf._arrange._hint?.visible ? shelf._arrange._hint.text : '';
    await L.release();
    await L.sleep(800);
    shelf._arrange.end();
    await L.sleep(800);
    const one = L.stored().filter(g => g.edge === 'right');
    L.check('apart: nudged back, the parts are one island again', one.length === 1 &&
        one[0].islands.includes('clock') && one[0].islands.includes('quick-options') &&
        (shelf._actionsParts ?? []).includes('clock'),
        `${JSON.stringify(L.stored())} hint "${hint2}" parts ${JSON.stringify(shelf._actionsParts)}`);
}

async function work() {
    const shelf = await L.waitForShelf();
    const m = Main.layoutManager.primaryMonitor;
    const only = (GLib.getenv('SA_ONLY') || 'ends,bars,editor,empty,hover,insert,trio,tray,bands,apart').split(',');
    for (const [name, fn] of [['ends', ends], ['bars', bars], ['editor', editor], ['empty', empty],
        ['hover', hover], ['insert', insert], ['trio', trio], ['tray', tray], ['bands', bands], ['apart', apart]]) {
        if (!only.includes(name))
            continue;
        try {
            await fn(shelf, m);
        } catch (e) {
            L.check(`${name} ran without errors`, false, `${e}\n${e.stack}`);
            try { shelf._arrange.end(); } catch {}
            await L.release().catch(() => {});
        }
    }
    await L.reset(800);
}
export function init() { L.start('round121', work); }
export async function run() { await new Promise(() => {}); }
