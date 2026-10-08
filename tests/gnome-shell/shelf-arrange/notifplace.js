// SPDX-License-Identifier: GPL-2.0-or-later
// Notification cards and the drawer come from wherever the notifications
// island is placed: every edge at start, centre and end. An urgent card sits
// beside the island and arrives from the island's edge; clicking the island
// opens the drawer on the island's inner side, moving in from that edge.
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';
import {getNotificationTray} from 'resource:///org/gnome/shell/ui/lumaNotificationBeacon.js';
import * as A from 'resource:///org/gnome/shell/ui/shelfArrangement.js';
import * as L from './lib.js';

const REST = A.ISLAND_IDS.filter(id => id !== 'notifications');
const inward = {bottom: [0, 1], top: [0, -1], left: [-1, 0], right: [1, 0]};

async function work() {
    const shelf = await L.waitForShelf();
    const m = Main.layoutManager.primaryMonitor;
    // A source closes with its last notification: a fresh one each time.
    const fresh = () => {
        const source = new MessageTray.Source({title: 'Clock', iconName: 'alarm-symbolic'});
        Main.messageTray.add(source);
        return source;
    };
    const tray = getNotificationTray();
    for (const edge of A.EDGES) {
        for (const anchor of A.ZONE_ANCHORS) {
            const restEdge = edge === 'bottom' ? 'top' : 'bottom';
            await L.arrange([{edge, anchor, islands: ['notifications']}, {edge: restEdge, anchor: 'center', islands: REST}], 1200);
            const name = `${edge} ${anchor}`;
            // An urgent card.
            const s1 = fresh();
            const n = new MessageTray.Notification({source: s1, title: 'Low battery', body: '5% left',
                urgency: MessageTray.Urgency.CRITICAL});
            s1.addNotification(n);
            await L.sleep(60);
            const entry = Main.messageTray._cards.find(e => e.notification === n);
            const t = entry ? [Math.sign(Math.round(entry.frame.translation_x)), Math.sign(Math.round(entry.frame.translation_y))] : null;
            await L.sleep(1200);
            const island = shelf.getIsland('notifications');
            // An empty notifications island is not in its row at all: its
            // slot is the group that holds it, where the Shell puts the card.
            const slot = shelf._groups.find(g => g.placement?.islands.includes('notifications') && !g.placement.drag);
            const ir = L.rectOf(island.visible && island.get_stage() ? island : slot ?? island);
            const card = entry && L.rectOf(entry.frame);
            const dir = inward[edge];
            L.check(`${name}: the card arrives from the island's edge`, t && t[0] === dir[0] && t[1] === dir[1], JSON.stringify(t));
            // Beside the island when it shows; at its slot when it does not.
            const shown = island.visible && ir.width > 0 && ir.height > 0;
            const depth = 14 + 56 + 14;
            const atSlot = card && ({
                bottom: m.y + m.height - (card.y + card.height) <= depth,
                top: card.y - m.y <= depth,
                left: card.x - m.x <= depth,
                right: m.x + m.width - (card.x + card.width) <= depth,
            })[edge];
            const beside = card && !shown ? atSlot : card && ({
                bottom: card.y + card.height <= ir.y + 1,
                top: card.y >= ir.y + ir.height - 1,
                left: card.x >= ir.x + ir.width - 1,
                right: card.x + card.width <= ir.x + 1,
            })[edge];
            const vertical = A.isVertical(edge);
            const near = card && (vertical
                ? Math.abs((card.y + card.height / 2) - (ir.y + ir.height / 2)) < m.height / 2
                : Math.abs((card.x + card.width / 2) - (ir.x + ir.width / 2)) < m.width / 3);
            L.check(`${name}: the card sits beside the island, on its side of the screen`, beside && near,
                `card ${JSON.stringify(card)} island ${JSON.stringify(ir)}`);
            if (edge === 'left' || anchor === 'end')
                await L.shot(`np-${edge}-${anchor}-card`, m);
            n.destroy();
            await L.sleep(500);
            // The drawer, from a click on the island (a waiting notification
            // shows it).
            const s2 = fresh();
            const w = new MessageTray.Notification({source: s2, title: 'Tea is ready', body: 'Your timer finished'});
            s2.addNotification(w);
            await L.sleep(1200);
            const r = L.rectOf(island);
            await L.move(r.x + r.width / 2, r.y + r.height / 2, 200);
            await L.press(); await L.sleep(40); await L.release();
            const tt = [Math.sign(Math.round(tray.translation_x)), Math.sign(Math.round(tray.translation_y))];
            await L.sleep(600);
            const d = L.rectOf(tray);
            L.check(`${name}: a click opens the drawer`, tray.isOpen);
            L.check(`${name}: the drawer moves in from the island's edge`, tt[0] === dir[0] && tt[1] === dir[1], JSON.stringify(tt));
            const inside = ({
                bottom: d.y + d.height <= r.y + 1,
                top: d.y >= r.y + r.height - 1,
                left: d.x >= r.x + r.width - 1,
                right: d.x + d.width <= r.x + 1,
            })[edge];
            L.check(`${name}: the drawer opens on the island's inner side`, inside, `drawer ${JSON.stringify(d)} island ${JSON.stringify(r)}`);
            if (anchor === 'center')
                await L.shot(`np-${edge}-drawer`, m);
            tray.close();
            await L.sleep(300);
            w.destroy();
            await L.sleep(400);
        }
    }
    await L.reset(800);
}
export function init() { L.start('notifplace', work); }
export async function run() { await new Promise(() => {}); }
