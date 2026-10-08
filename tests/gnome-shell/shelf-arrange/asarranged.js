// SPDX-License-Identifier: GPL-2.0-or-later
// The owner's arrangement as stored on his machine (the dock at the start,
// the live island stored beside the Well, Quick Options and the clock at the
// end, notifications and what is playing at the centre), in every mode of
// Use islands and Span full edge: what arrange mode shows is exactly what
// the desktop shows apart from the two placeholders arrange mode opens in
// the empty slots (Live extensions, Notifications), which move nothing
// else; Quick Options and the clock stay at the end, and a
// hidden island (notifications with nothing unread, no live activity) takes
// no length and moves nothing. Then the same with a notification showing.
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

const OWNER = [
    {edge: 'bottom', anchor: 'start', islands: ['dock']},
    {edge: 'bottom', anchor: 'end', islands: ['well', 'quick-options', 'clock']},
    {edge: 'bottom', anchor: 'end', islands: ['live']},
    {edge: 'bottom', anchor: 'center', islands: ['notifications']},
    {edge: 'bottom', anchor: 'center', islands: ['media']},
];
const IDS = ['dock', 'live', 'media', 'well', 'quick-options', 'clock', 'notifications'];

function rects(shelf) {
    const out = {};
    for (const id of IDS) {
        const a = shelf.islandActorFor(id);
        if (a?.visible && a.get_stage() && !a._isGhost && a.width > 0)
            out[id] = L.rectOf(a);
    }
    return out;
}

async function work() {
    const shelf = await L.waitForShelf();
    const s = L.settings();
    const m = Main.layoutManager.primaryMonitor;
    for (const notified of [false, true]) {
        const n = notified ? L.notify() : null;
        for (const [mode, span, protrude, float] of [
            ['connected', true, true, false], ['connected', true, false, true], ['connected', false, true, false],
            ['separate', true, true, false], ['separate', true, false, false], ['separate', false, false, true]]) {
            s.set_string('shelf-surface-mode', mode);
            s.set_boolean('shelf-span-full', span);
            s.set_string('shelf-edge-mode', protrude ? 'protruding' : 'floating');
            s.set_boolean('shelf-float-ends', float);
            await L.arrange(OWNER, 1500);
            const name = `${mode}${span ? '+span' : ''}${protrude ? '+protrude' : ''}${float ? '+float' : ''}${notified ? ' +notification' : ''}`;
            const live0 = rects(shelf);
            shelf._arrange.begin({select: 'clock'});
            await L.sleep(900);
            const editor = rects(shelf);
            // How much length arrange mode's placeholders take on each
            // island's own edge: the groups of that edge share the band, so
            // a placeholder in one of them may move the others along it.
            const perEdge = {};
            const edgeOf = {};
            for (const g of shelf._groups) {
                const edge = g.placement?.edge;
                if (!edge)
                    continue;
                for (const id of g.placement.islands)
                    edgeOf[id] = edge;
                for (const a of g.row.get_children().filter(x => x.visible && x.get_stage() && x._isGhost && x.width > 0))
                    perEdge[edge] = (perEdge[edge] ?? 0) + L.rectOf(a).width + 9 + 24;
            }
            const ghostLength = Object.fromEntries(IDS.map(id => [id, perEdge[edgeOf[id]] ?? 0]));
            shelf._arrange.end();
            await L.sleep(1100);
            const live = rects(shelf);
            const differ = (a, b) => !a || !b || ['x', 'y', 'width', 'height'].some(k => Math.abs(a[k] - b[k]) > 1);
            // Arrange mode opens the empty slots' placeholders in place:
            // an island may be along its edge by their length, no more, and
            // nothing changes size or leaves its band.
            const off = (id, a, b) => !a || !b || a.width !== b.width || a.height !== b.height ||
                Math.abs(a.y - b.y) > 1 || Math.abs(a.x - b.x) > (ghostLength[id] ?? 0) + 1;
            // Live extensions, what is playing and notifications stand as
            // their placeholder while arranging; everything else is drawn
            // in both.
            const stood = ['live', 'media', 'notifications', 'well'];
            const moved = Object.keys({...editor, ...live})
                .filter(id => !(stood.includes(id) && !editor[id]))
                .filter(id => off(id, editor[id], live[id]));
            L.check(`${name}: the desktop shows what arrange mode showed, but for the placeholders`, !moved.length,
                `placeholders ${JSON.stringify(ghostLength)}; ` +
                moved.map(id => `${id} ${JSON.stringify(editor[id])} -> ${JSON.stringify(live[id])}`).join('; '));
            const back = Object.keys(live0).filter(id => differ(live0[id], live[id]));
            L.check(`${name}: and nothing moved for arrange mode`, !back.length,
                back.map(id => `${id} ${JSON.stringify(live0[id])} -> ${JSON.stringify(live[id])}`).join('; '));
            // Quick Options and the clock at the end: nothing of the end
            // group's to their right but the screen's end (and the gutter).
            const end = (mode === 'connected' || span) && !float ? 0 : 14;
            const right = Math.max(...['quick-options', 'clock', 'live'].map(id => live[id]).filter(Boolean).map(r => r.x + r.width));
            L.check(`${name}: the end group reaches the end (${end} px in)`, Math.abs(m.x + m.width - end - right) <= 1,
                `${right} vs ${m.x + m.width - end}`);
            // Quick Options and the clock are one island: the last thing on
            // the edge, the live family just before it.
            const qo = live['quick-options'] ?? live.clock;
            const fam = ['live', 'media'].map(id => live[id]).filter(Boolean);
            L.check(`${name}: Quick Options and the clock end the edge, the live family before them`, qo &&
                Math.abs(qo.x + qo.width - right) <= 1 && fam.every(r => r.x + r.width <= qo.x + 1),
                `${JSON.stringify(qo)} ${JSON.stringify(fam)}`);
            if (!notified)
                L.check(`${name}: hidden notifications take no length`, !live.notifications);
            if (mode === 'connected' || (span && !float))
                await L.shot(`aa-${name.replace(/[^a-z]+/g, '-')}`, {x: m.x, y: m.y + m.height - 120, width: m.width, height: 120});
        }
        n?.destroy();
        await L.sleep(600);
    }
    for (const key of ['shelf-surface-mode', 'shelf-span-full', 'shelf-edge-mode', 'shelf-float-ends'])
        s.reset(key);
    await L.reset(800);
}
export function init() { L.start('asarranged', work); }
export async function run() { await new Promise(() => {}); }
