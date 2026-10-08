// SPDX-License-Identifier: GPL-2.0-or-later
// The owner's desk and his .118 findings: the ThinkPad panel, the 34"
// ultrawide and the 27" side by side (SA_ORDER, left to right, '*' for the
// primary; default the ultrawide in the middle).
//   1. centre means centre: the dock's visual centre on the monitor's centre
//      line (±1 px), alone, with neighbours and through a drag, top and bottom
//   2. start and end reach the true ends of every edge, arrange mode or not
//   3. with free placement off, exactly the three zones per edge
//   4. arrange mode shows a notifications ghost when none is showing
//   5. free placement on: an island lands where it is dropped; only soft
//      snaps (12 px) at the ends and centres, and attach right beside another
// Run with SA_MONITORS=1920x1200,3440x1440,2560x1440.
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as A from 'resource:///org/gnome/shell/ui/shelfArrangement.js';
import * as M from 'resource:///org/gnome/shell/ui/shelfMetrics.js';
import * as L from './lib.js';

const ORDER = (GLib.getenv('SA_ORDER') || '1920x1200,3440x1440*,2560x1440').split(',');
const REST = ['live', 'media', 'notifications', 'well', 'quick-options', 'clock'];
const GUTTER = 14;

const along = (r, edge) => A.isVertical(edge) ? [r.y, r.y + r.height] : [r.x, r.x + r.width];
const mid = (r, edge) => { const [a, b] = along(r, edge); return (a + b) / 2; };
const monitorAlong = (m, edge) => A.isVertical(edge) ? [m.y, m.y + m.height] : [m.x, m.x + m.width];

// The union of a group's visible islands.
function unionOf(rects) {
    const x1 = Math.min(...rects.map(r => r.x)), y1 = Math.min(...rects.map(r => r.y));
    const x2 = Math.max(...rects.map(r => r.x + r.width)), y2 = Math.max(...rects.map(r => r.y + r.height));
    return {x: x1, y: y1, width: x2 - x1, height: y2 - y1};
}

function islandRect(shelf, id) {
    return L.rectOf(shelf.islandActorFor(id));
}

// The live family as drawn: its visible members' run.
function family(shelf) {
    const rects = ['live', 'media'].map(id => shelf.islandActorFor(id)).filter(a => a?.visible && a.get_stage()).map(L.rectOf);
    return rects.length ? unionOf(rects) : null;
}

async function grabAndMove(shelf, id, to, {steps = 20} = {}) {
    const r = L.rectOf(shelf.partActorFor(id));
    const [x0, y0] = L.centre(r);
    await L.move(x0, y0, 60);
    await L.press();
    await L.move(x0 + 12, y0 - 12, 60);
    await L.move(x0 + 20, y0 - 20, 80);
    const drag = shelf._arrange._drag;
    if (!drag)
        return null;
    for (let i = 1; i <= steps; i++)
        await L.move(x0 + 20 + (to[0] - x0 - 20) * i / steps, y0 - 20 + (to[1] - y0 + 20) * i / steps, 14);
    await L.sleep(350);
    return drag;
}

async function dropAt(shelf, id, to) {
    const drag = await grabAndMove(shelf, id, to);
    const target = drag?.target ?? null;
    const dragged = L.rectOf(shelf.islandActorFor(id));
    const ghost = L.groups().find(g => g.islands.some(i => `${i.id}`.includes('gap')))?.rect ?? null;
    await L.release();
    await L.sleep(800);
    return {target, dragged, ghost};
}

async function work() {
    const shelf = await L.waitForShelf();
    for (let i = 0; i < 40 && Main.layoutManager.monitors.length < 3; i++)
        await L.sleep(250);
    await L.layoutMonitors(ORDER);
    await L.sleep(1000);
    const monitors = [...shelf.monitors].sort((a, b) => a.x - b.x);
    L.check(`the owner's layout ${ORDER.join(' | ')}`,
        monitors.map(m => `${m.width}x${m.height}${m.primary ? '*' : ''}`).join() === ORDER.join(),
        JSON.stringify(monitors.map(m => [m.x, m.width, m.height, m.primary, m.closedEdges])));
    const primary = monitors.find(m => m.primary);
    const other = m => monitors.find(x => x !== m);

    // ---------------------------------------------------- 1. centre
    for (const m of monitors) {
        for (const edge of ['bottom', 'top']) {
            const centre = (m.x + m.width / 2);
            // Alone: everything else on another monitor.
            await L.arrange([{display: m.id, edge, anchor: 'center', islands: ['dock']},
                {display: other(m).id, edge: 'bottom', anchor: 'end', islands: REST}]);
            const alone = islandRect(shelf, 'dock');
            L.check(`${m.width} ${edge}: the dock alone is centred (±1)`, Math.abs(mid(alone, edge) - centre) <= 1,
                `${mid(alone, edge)} vs ${centre} ${JSON.stringify(alone)}`);
            // With neighbours on the same edge, a notification showing.
            const n = L.notify();
            await L.arrange([{display: m.id, edge, anchor: 'start', islands: ['live', 'media', 'notifications']},
                {display: m.id, edge, anchor: 'center', islands: ['dock']},
                {display: m.id, edge, anchor: 'end', islands: ['well', 'quick-options', 'clock']}]);
            const withN = islandRect(shelf, 'dock');
            L.check(`${m.width} ${edge}: the dock is centred with neighbours (±1)`, Math.abs(mid(withN, edge) - centre) <= 1,
                `${mid(withN, edge)} vs ${centre}`);
            // A centred group of two: the group's visual centre.
            await L.arrange([{display: m.id, edge, anchor: 'center', islands: ['dock', 'media']},
                {display: m.id, edge, anchor: 'end', islands: ['live', 'notifications', 'well', 'quick-options', 'clock']}]);
            const pair = unionOf(['dock', 'live', 'media'].map(id => islandRect(shelf, id)).filter(r => r.width > 0));
            L.check(`${m.width} ${edge}: a centred group's visual centre is centred (±1)`, Math.abs(mid(pair, edge) - centre) <= 1,
                `${mid(pair, edge)} vs ${centre}`);
            n.destroy();
            await L.sleep(600);
        }
    }
    // Through a drag, the owner's way: the dock from the left edge to the
    // bottom centre of the primary, the rest where it was.
    for (const m of [primary, ...monitors.filter(x => x !== primary)]) {
        await L.arrange([{display: primary.id, edge: primary.closedEdges.includes('left') ? 'right' : 'left', anchor: 'center', islands: ['dock']},
            {display: primary.id, edge: 'bottom', anchor: 'end', islands: REST}]);
        shelf._arrange.begin({select: 'dock'});
        await L.sleep(500);
        const zone = shelf._arrange._zones.map(z => z._zone).find(z => z.monitor === m.index && z.edge === 'bottom' && z.anchor === 'center');
        const res = await dropAt(shelf, 'dock', zone ? L.centre(zone.rect) : [m.x + m.width / 2, m.y + m.height - 40]);
        const centre = m.x + m.width / 2;
        if (res.ghost)
            L.check(`${m.width}: the ghost for bottom centre is centred (±1)`, Math.abs(mid(res.ghost, 'bottom') - centre) <= 1,
                `${JSON.stringify(res.ghost)} vs ${centre}`);
        shelf._arrange.end();
        await L.sleep(700);
        const placed = L.stored().find(g => g.islands.includes('dock'));
        const dock = islandRect(shelf, 'dock');
        L.check(`${m.width}: dragged to bottom centre, the dock is centred (±1)`,
            placed?.anchor === 'center' && placed.edge === 'bottom' && Math.abs(mid(dock, 'bottom') - centre) <= 1,
            `${JSON.stringify(placed)} ${mid(dock, 'bottom')} vs ${centre}`);
        await L.shot(`o-drag-centre-${m.width}`, {x: m.x, y: m.y + m.height - 140, width: m.width, height: 140});
    }

    // ---------------------------------------------------- 2. true ends
    for (const m of monitors) {
        for (const edge of A.EDGES) {
            if (m.closedEdges.includes(edge))
                continue;
            for (const anchor of ['start', 'end']) {
                await L.arrange([{display: m.id, edge, anchor, islands: ['clock']},
                    {display: other(m).id, edge: 'bottom', anchor: 'center', islands: A.ISLAND_IDS.filter(i => i !== 'clock')}], 1200);
                const r = islandRect(shelf, 'clock');
                const [s, e] = monitorAlong(m, edge);
                const [a, b] = along(r, edge);
                const ok = anchor === 'start' ? Math.abs(a - (s + GUTTER)) <= 1 : Math.abs(b - (e - GUTTER)) <= 1;
                L.check(`${m.width} ${edge} ${anchor}: reaches the end (${GUTTER} px in)`, ok,
                    `${a}..${b} in ${s}..${e}`);
            }
        }
        // Arrange mode: the zones at the ends sit at the ends too, card or not.
        await L.arrange([{display: other(m).id, edge: 'bottom', anchor: 'center', islands: A.ISLAND_IDS}], 1000);
        shelf._arrange.begin({select: 'dock'});
        await L.sleep(600);
        const zones = shelf._arrange._zones.map(z => z._zone).filter(z => z.monitor === m.index);
        let bad = [];
        for (const z of zones) {
            const [s, e] = monitorAlong(m, z.edge);
            const [a, b] = along(z.rect, z.edge);
            if (z.anchor === 'start' && Math.abs(a - (s + GUTTER)) > 1)
                bad.push(`${z.edge} start ${a}`);
            if (z.anchor === 'end' && Math.abs(b - (e - GUTTER)) > 1)
                bad.push(`${z.edge} end ${b}`);
        }
        L.check(`${m.width}: in arrange mode every edge's start and end zones reach its ends`, zones.length && !bad.length,
            bad.join(', '));
        await L.shot(`o-arrange-${m.width}`, {x: m.x, y: m.y, width: m.width, height: m.height});
        shelf._arrange.end();
        await L.sleep(500);
    }

    // ---------------------------------------------------- 3. three zones
    await L.reset(1000);
    shelf._arrange.begin({select: 'dock'});
    await L.sleep(600);
    {
        const zones = shelf._arrange._zones.map(z => z._zone);
        const counts = {};
        for (const z of zones)
            counts[`${z.monitor}:${z.edge}`] = (counts[`${z.monitor}:${z.edge}`] ?? 0) + 1;
        L.check('free placement off: at most three zones per edge', Object.values(counts).every(c => c <= 3),
            JSON.stringify(counts));
        const anchors = new Set(zones.map(z => `${z.monitor}:${z.edge}:${z.anchor}`));
        L.check('one zone per anchor', anchors.size === zones.length);
        // Nothing else drawn along an edge but the zones, the groups and one line.
        const marks = shelf._arrange._decorations.get_children().filter(a => a.visible &&
            !a._zone && !a.has_style_class_name?.('luma-arrange-outline') && !a.has_style_class_name?.('luma-arrange-grip') &&
            a.constructor.name !== 'EdgeLine' && a !== shelf._arrange._card);
        L.check('no other marks along the edges', marks.length === 0, marks.map(a => a.style_class ?? a.constructor.name).join());
        L.check('free placement off: no edge lines, zones only', (shelf._arrange._edgeLines ?? []).length === 0);
        for (const m of monitors)
            await L.shot(`o-three-zones-${m.width}`, {x: m.x, y: m.y, width: m.width, height: m.height});
    }
    shelf._arrange.end();
    await L.sleep(500);

    // ---------------------------------------------------- 4. notifications ghost
    Main.messageTray.getSources?.().forEach(s => s.destroy());
    await L.reset(1200);
    L.check('no notification is showing', !shelf.getIsland('notifications')?.visible);
    shelf._arrange.begin({select: 'notifications'});
    await L.sleep(800);
    const nghost = shelf.islandActorFor('notifications');
    L.check('arrange mode shows a notifications ghost', !!nghost?.mapped && nghost !== shelf.getIsland('notifications') &&
        nghost?._label?.text === 'Notifications', `${nghost?.name} ${nghost?._label?.text}`);
    await L.shot('o-notifications-ghost', {x: primary.x, y: primary.y + primary.height - 160, width: primary.width, height: 160});
    {
        const zone = shelf._arrange._zones.map(z => z._zone).find(z => z.monitor === primary.index && z.edge === 'top' && z.anchor === 'start');
        await dropAt(shelf, 'notifications', L.centre(zone.rect));
        const placed = L.stored().find(g => g.islands.includes('notifications'));
        L.check('the notifications ghost can be placed (top start)', placed?.edge === 'top' && placed.anchor === 'start',
            JSON.stringify(placed));
        await L.shot('o-notifications-ghost-placed', {x: primary.x, y: primary.y, width: primary.width, height: 160});
    }
    shelf._arrange.end();
    await L.sleep(700);
    L.check('the notifications ghost leaves with arrange mode', !shelf._beaconGhost?.mapped);
    const n2 = L.notify();
    await L.sleep(1200);
    const beacon = L.rectOf(shelf.getIsland('notifications'));
    L.check('a notification then shows in that slot (top left)', beacon.y < primary.y + 90 && beacon.x < primary.x + 400,
        JSON.stringify(beacon));
    n2.destroy();
    await L.sleep(600);

    // ---------------------------------------------------- 5. free placement
    L.settings().set_boolean('shelf-free-placement', true);
    await L.arrange([{display: primary.id, edge: 'bottom', anchor: 'center', islands: ['dock']},
        {display: primary.id, edge: 'bottom', anchor: 'end', islands: ['live', 'notifications', 'well', 'quick-options', 'clock']},
        {display: primary.id, edge: 'top', anchor: 'center', islands: ['media']}]);
    shelf._arrange.begin({select: 'media'});
    await L.sleep(600);
    L.check('free placement: no zone grid', (shelf._arrange._zones ?? []).length === 0,
        `${(shelf._arrange._zones ?? []).length}`);
    L.check('free placement: one faint line per open edge', (shelf._arrange._edgeLines ?? []).length ===
        monitors.reduce((n, m) => n + 4 - m.closedEdges.length, 0));
    const topY = primary.y + 36;
    const pc = primary.x + primary.width / 2;
    const cases = [
        ['exactly where dropped (30%)', primary.x + primary.width * 0.3, 'free', primary.x + primary.width * 0.3],
        ['exactly where dropped (30 px right of centre)', pc + 30, 'free', pc + 30],
        ['a soft snap to the centre (8 px off)', pc + 8, 'center', pc],
        ['exactly where dropped (70%)', primary.x + primary.width * 0.7, 'free', primary.x + primary.width * 0.7],
    ];
    for (const [name, x, anchor, expected] of cases) {
        // Grab the media island at its centre so the drop point is its centre.
        // The live family's run is carried: put its centre at x.
        const r = L.rectOf(shelf.islandActorFor('media'));
        const f0 = family(shelf);
        const [x0, y0] = L.centre(r);
        const px = x + (x0 - (f0.x + f0.width / 2));
        await L.move(x0, y0, 60);
        await L.press();
        await L.move(x0 + 10, y0 + 10, 60);
        await L.move(x0 + 20, y0 + 20, 80);
        for (let i = 1; i <= 16; i++)
            await L.move(x0 + 20 + (px - x0 - 20) * i / 16, y0 + 20 + (topY - y0 - 20) * i / 16, 14);
        await L.sleep(300);
        const lifted = family(shelf);
        const dropCentre = lifted.x + lifted.width / 2;
        await L.release();
        await L.sleep(900);
        const placed = L.stored().find(g => g.islands.includes('media'));
        const landed = family(shelf);
        const want = anchor === 'free' ? dropCentre : expected;
        L.check(`free placement: ${name}`, placed?.anchor === anchor && placed.edge === 'top' &&
            Math.abs(landed.x + landed.width / 2 - want) <= 1,
            `${JSON.stringify(placed)} landed ${landed.x + landed.width / 2} want ${want} (pointer ${x})`);
    }
    // Soft snap at the start corner.
    {
        const r = L.rectOf(shelf.islandActorFor('media'));
        const f = family(shelf);
        const [x0, y0] = L.centre(r);
        // The run's start 9 px from the edge's start: the media island's
        // centre is where the pointer goes.
        const tx = primary.x + GUTTER + 9 + (r.x - f.x) + r.width / 2;
        await L.move(x0, y0, 60); await L.press();
        await L.move(x0 - 10, y0 + 10, 60); await L.move(x0 - 20, y0 + 20, 80);
        for (let i = 1; i <= 16; i++)
            await L.move(x0 - 20 + (tx - x0 + 20) * i / 16, y0 + 20 + (topY - y0 - 20) * i / 16, 14);
        await L.sleep(300);
        await L.release();
        await L.sleep(900);
        const placed = L.stored().find(g => g.islands.includes('media'));
        L.check('free placement: a soft snap to the start corner (9 px off)', placed?.anchor === 'start', JSON.stringify(placed));
    }
    // Attach right beside the dock.
    {
        const dock = islandRect(shelf, 'dock');
        const r = L.rectOf(shelf.islandActorFor('media'));
        const [x0, y0] = L.centre(r);
        const f = family(shelf);
        const tx = dock.x + dock.width + A.ISLAND_GAP + 4 + (r.x - f.x) + r.width / 2;
        const ty = dock.y + dock.height / 2;
        await L.move(x0, y0, 60); await L.press();
        await L.move(x0 + 10, y0 + 10, 60); await L.move(x0 + 20, y0 + 20, 80);
        for (let i = 1; i <= 20; i++)
            await L.move(x0 + 20 + (tx - x0 - 20) * i / 20, y0 + 20 + (ty - y0 - 20) * i / 20, 14);
        await L.sleep(300);
        await L.release();
        await L.sleep(900);
        const placed = L.stored().find(g => g.islands.includes('media'));
        L.check('free placement: dropped right beside the dock, it attaches', placed?.islands.includes('dock'),
            JSON.stringify(placed));
        // Away from the dock (well beyond the soft radius), it stays free.
        const r2 = L.rectOf(shelf.islandActorFor('media'));
        const [x1, y1] = L.centre(r2);
        const fx = primary.x + primary.width * 0.2;
        await L.move(x1, y1, 60); await L.press();
        await L.move(x1 - 10, y1 - 10, 60); await L.move(x1 - 20, y1 - 20, 80);
        for (let i = 1; i <= 20; i++)
            await L.move(x1 - 20 + (fx - x1 + 20) * i / 20, y1 - 20 + (ty - y1 + 20) * i / 20, 14);
        await L.sleep(300);
        const lifted = family(shelf);
        await L.release();
        await L.sleep(900);
        const p2 = L.stored().find(g => g.islands.includes('media'));
        const landed = family(shelf);
        L.check('free placement: on the bottom edge away from others, exactly where dropped',
            p2?.anchor === 'free' && p2.edge === 'bottom' && Math.abs(landed.x + landed.width / 2 - (lifted.x + lifted.width / 2)) <= 1,
            `${JSON.stringify(p2)} ${landed.x + landed.width / 2} vs ${lifted.x + lifted.width / 2}`);
    }
    await L.shot('o-free-placement', {x: primary.x, y: primary.y + primary.height - 160, width: primary.width, height: 160});
    shelf._arrange.end();
    await L.sleep(500);
    L.settings().set_boolean('shelf-free-placement', false);
    await L.reset(800);

    // ---------------------------------------------------- 6. no fighting
    // The owner's settings: Protrude, Span, no Float, no free placement, no
    // islands. The live family dragged slowly into the top-left corner with
    // a trembling hand: the target never flips back and forth.
    const s = L.settings();
    s.set_string('shelf-edge-mode', 'protruding');
    s.set_boolean('shelf-span-full', true);
    s.set_boolean('shelf-float-ends', false);
    s.set_string('shelf-surface-mode', 'connected');
    await L.arrange([{display: primary.id, edge: 'bottom', anchor: 'center', islands: ['dock', 'live', 'media']},
        {display: primary.id, edge: 'bottom', anchor: 'end', islands: ['notifications', 'well', 'quick-options', 'clock']}], 1500);
    shelf._arrange.begin({select: 'live'});
    await L.sleep(700);
    {
        const r = L.rectOf(shelf.partActorFor(shelf.getIsland('live')?.visible ? 'live' : 'media'));
        const [x0, y0] = L.centre(r);
        await L.move(x0, y0, 60);
        await L.press();
        await L.move(x0 - 10, y0 - 10, 60);
        await L.move(x0 - 20, y0 - 20, 80);
        const seen = [];
        const tx = primary.x + 120, ty = primary.y + 30;
        for (let i = 1; i <= 60; i++) {
            const t = i / 60;
            const wobble = (i % 2 ? 1 : -1) * 6;
            await L.move(x0 - 20 + (tx - x0 + 20) * t + wobble, y0 - 20 + (ty - y0 + 20) * t - wobble, 16);
            const tg = shelf._arrange._drag?.target;
            const key = tg ? `${tg.kind}:${tg.edge}:${tg.anchor ?? tg.group}:${tg.index ?? ''}` : 'none';
            if (seen.at(-1) !== key)
                seen.push(key);
        }
        // Tremble in place at the corner.
        for (let i = 0; i < 30; i++) {
            await L.move(tx + (i % 3 - 1) * 5, ty + ((i + 1) % 3 - 1) * 5, 16);
            const tg = shelf._arrange._drag?.target;
            const key = tg ? `${tg.kind}:${tg.edge}:${tg.anchor ?? tg.group}:${tg.index ?? ''}` : 'none';
            if (seen.at(-1) !== key)
                seen.push(key);
        }
        const flips = seen.filter((k, i) => i >= 2 && seen[i - 2] === k).length;
        L.check('dragging into the top-left corner: no target flips back and forth', flips === 0, seen.join(' > '));
        await L.release();
        await L.sleep(900);
        const placed = L.stored().find(g => g.islands.includes('live'));
        L.check('the live family lands in the top-left corner, together', placed?.edge === 'top' && placed.anchor === 'start' &&
            placed.islands.includes('media'), JSON.stringify(placed));
    }
    shelf._arrange.end();
    await L.sleep(900);

    // ---------------------------------------------------- 7. what you arrange is what you get
    // Committed through the editor, every edge and anchor: the dock where the
    // preview showed it, after Done too (islands off: in the bar).
    for (const edge of A.EDGES) {
        for (const anchor of A.ZONE_ANCHORS) {
            await L.arrange([{display: primary.id, edge: edge === 'bottom' ? 'top' : 'bottom', anchor: 'end',
                islands: ['live', 'media', 'notifications', 'well', 'quick-options', 'clock']},
            {display: primary.id, edge: edge === 'bottom' ? 'top' : 'bottom', anchor: 'start', islands: ['dock']}], 1300);
            shelf._arrange.begin({select: 'dock'});
            await L.sleep(600);
            const zone = shelf._arrange._zones.map(z => z._zone).find(z => z.monitor === primary.index && z.edge === edge && z.anchor === anchor);
            if (!zone) {
                L.check(`editor ${edge} ${anchor}: zone drawn`, false);
                shelf._arrange.end();
                continue;
            }
            const res = await dropAt(shelf, 'dock', L.centre(zone.rect));
            await L.sleep(500);
            const inEditor = islandRect(shelf, 'dock');
            shelf._arrange.end();
            await L.sleep(1000);
            const onDesktop = islandRect(shelf, 'dock');
            const [a0, a1] = along(inEditor, edge), [b0, b1] = along(onDesktop, edge);
            L.check(`editor ${edge} ${anchor}: the desktop shows the dock where the editor put it`,
                Math.abs(a0 - b0) <= 1 && Math.abs(a1 - b1) <= 1 && (!res.ghost || Math.abs(mid(res.ghost, edge) - (a0 + a1) / 2) <= 1),
                `ghost ${JSON.stringify(res.ghost)} editor ${JSON.stringify(inEditor)} desktop ${JSON.stringify(onDesktop)}`);
        }
    }
    await L.shot('o-bar-dividers', {x: primary.x, y: primary.y + primary.height - 120, width: primary.width, height: 120});
    // Dividers: between the parts of the bar, full height, the token's width.
    {
        const bar = L.groups().find(g => g.islands.length > 1);
        const islands = shelf._groups.find(g => g.placement?.merged)?.row.get_children().filter(a => a.visible) ?? [];
        const withDivider = islands.filter(a => a._divider?.visible);
        const d = withDivider[0]?._divider;
        L.check('a bar draws a divider after every part but the last', withDivider.length === islands.length - 1 && islands.length > 1,
            `${withDivider.length}/${islands.length}`);
        L.check(`each divider is the divider token (${M.DIVIDER_WIDTH} px) and the full height of the bar`,
            d && Math.round(d.width) === M.DIVIDER_WIDTH && Math.abs(d.height - (bar?.rect.height ?? 0)) <= 1,
            `${d?.width}x${d?.height} bar ${bar?.rect.height}`);
    }
    for (const key of ['shelf-edge-mode', 'shelf-span-full', 'shelf-float-ends', 'shelf-surface-mode'])
        s.reset(key);
    await L.reset(800);
}
export function init() { L.start('owner', work); }
export async function run() { await new Promise(() => {}); }
