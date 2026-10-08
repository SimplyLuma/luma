// SPDX-License-Identifier: GPL-2.0-or-later
// Every placeable island to every one of the twelve zones, and attached
// beside another group and detached again (ADR-044 verification 1), through
// real pointer drags in arrange mode. Each drop is checked in the stored
// arrangement and in the drawn geometry.
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as A from 'resource:///org/gnome/shell/ui/shelfArrangement.js';
import * as L from './lib.js';

const IDS = ['dock', 'live', 'media', 'notifications', 'well', 'quick-options', 'clock'];

async function dragTo(shelf, id, targetFor) {
    const arrange = shelf._arrange;
    const r = L.rectOf(shelf.partActorFor(id));
    const [x0, y0] = L.centre(r);
    await L.move(x0, y0, 60);
    await L.press();
    await L.move(x0 + 12, y0 - 12, 60);
    await L.move(x0 + 20, y0 - 20, 80);
    const drag = arrange._drag;
    if (!drag) {
        await L.release();
        return null;
    }
    // A target is where the island's centre goes, or {pointer} where the
    // pointer itself goes (a zone's drawn placeholder).
    const aim = targetFor(drag);
    const [ox, oy] = aim.pointer ? [0, 0] : drag.offset;
    const [tx, ty] = aim.pointer ?? aim;
    const px = tx + ox, py = ty + oy;
    const steps = 18;
    for (let i = 1; i <= steps; i++)
        await L.move(x0 + 20 + (px - x0 - 20) * i / steps, y0 - 20 + (py - y0 + 20) * i / steps, 14);
    await L.sleep(250);
    const target = drag.target;
    await L.release();
    await L.sleep(650);
    return target;
}

async function work() {
    const shelf = await L.waitForShelf();
    // A status icon, so the Well has something to show.
    const tray = new PanelMenu.Button(0.5, 'Tray glyph', false);
    tray.add_child(new St.Icon({icon_name: 'view-grid-symbolic', style_class: 'system-status-icon'}));
    Main.panel.addToStatusArea('zones-tray', tray);
    L.notify();
    await L.sleep(2500);
    const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
    let bad = 0, total = 0;
    for (const id of IDS) {
        // The live and media islands are one family (.119): they move together.
        const fam = A.familyOf(id);
        const rest = IDS.filter(x => !fam.includes(x));
        for (const edge of A.EDGES) {
            for (const anchor of A.ZONE_ANCHORS) {
                // Start each drop from the same layout: the island alone at
                // the top-left corner (or bottom-left for that zone itself),
                // everything else in one group at the bottom centre.
                const home = edge === 'top' && anchor === 'start'
                    ? {edge: 'bottom', anchor: 'start'} : {edge: 'top', anchor: 'start'};
                await L.arrange([{...home, islands: fam}, {edge: 'bottom', anchor: 'center', islands: rest}], 900);
                shelf._arrange.begin({select: id});
                await L.sleep(350);
                // A carried family is a long run: aim at the zone's drawn
                // placeholder, as a hand does (its centre point is covered by
                // targeting.js).
                // What travels with the island right now: its family, and
                // the dock's folders while they are married to it.
                const carried = A.familyOf(id, L.stored());
                const zone = carried.length > 1 ? shelf._arrange._zones.map(z => z._zone)
                    .find(z => z.monitor === 0 && z.edge === edge && z.anchor === anchor) : null;
                const target = await dragTo(shelf, id, drag => {
                    if (zone)
                        return {pointer: L.centre(zone.rect)};
                    // The bands as they are with the island picked up (a side
                    // band reaches a corner its island just left).
                    const band = shelf._bands.get(0)[edge];
                    const vertical = A.isVertical(edge);
                    const length = vertical ? drag.length.vertical : drag.length.horizontal;
                    const along = A.zonePoints(band, length)[anchor];
                    const cross = vertical ? band.rect.x + band.rect.width / 2 : band.rect.y + band.rect.height / 2;
                    return vertical ? [cross, along] : [along, cross];
                });
                shelf._arrange.end();
                await L.sleep(500);
                total++;
                const g = L.stored().find(x => x.islands.includes(id));
                const occupied = edge === 'bottom' && anchor === 'center';
                let ok;
                let detail = JSON.stringify(g);
                if (occupied) {
                    ok = g && g.islands.length > 1 && g.edge === 'bottom';
                } else {
                    ok = g && g.edge === edge && g.anchor === anchor && g.islands.length === carried.length;
                    const actors = carried.map(x => shelf.islandActorFor(x)).filter(a => a?.visible && a.get_stage());
                    if (ok && actors.length) {
                        const rs = actors.map(L.rectOf);
                        const x1 = Math.min(...rs.map(q => q.x)), y1 = Math.min(...rs.map(q => q.y));
                        const r = {x: x1, y: y1, width: Math.max(...rs.map(q => q.x + q.width)) - x1,
                            height: Math.max(...rs.map(q => q.y + q.height)) - y1};
                        const v = A.isVertical(edge);
                        const from = v ? r.y : r.x, to = from + (v ? r.height : r.width);
                        const b = shelf._bands.get(0)[edge];
                        const placed = anchor === 'start' ? Math.abs(from - b.along[0]) <= 2
                            : anchor === 'end' ? Math.abs(to - b.along[1]) <= 2
                                : Math.abs((from + to) / 2 - (b.along[0] + b.along[1]) / 2) <= 2;
                        const across = v ? Math.abs(r.x - b.rect.x) <= 2 : Math.abs(r.y - b.rect.y) <= 2;
                        ok = placed && across;
                        detail += ` ${JSON.stringify(r)} band ${JSON.stringify(b.along)}`;
                    }
                }
                if (!ok)
                    bad++;
                if (!ok || anchor === 'center')
                    L.check(`${id} → ${edge} ${anchor}${occupied ? ' (occupied: attaches)' : ''}`, ok, `${JSON.stringify(target)} ${detail}`);
            }
        }
        // Attach beside the dock-less group's first island, then detach.
        const other = rest[0];
        await L.arrange([{edge: 'top', anchor: 'start', islands: fam}, {edge: 'bottom', anchor: 'center', islands: rest}], 900);
        shelf._arrange.begin({select: id});
        await L.sleep(350);
        const firstRect = L.rectOf(shelf.islandActorFor(other));
        await dragTo(shelf, id, drag => [firstRect.x - 20, firstRect.y + firstRect.height / 2]);
        let g = L.stored().find(x => x.islands.includes(id));
        L.check(`${id}: attaches before ${other}`, g && g.islands.indexOf(fam.at(-1)) === g.islands.indexOf(other) - 1, JSON.stringify(g));
        await L.sleep(300);
        await dragTo(shelf, id, () => [global.stage.width * 0.3, 14 + shelf._thickness * scale / 2]);
        g = L.stored().find(x => x.islands.includes(id));
        L.check(`${id}: detaches to a free place on the top edge`, g && g.islands.length === fam.length && g.edge === 'top', JSON.stringify(g));
        shelf._arrange.end();
        await L.sleep(400);
    }
    L.check(`all ${total} zone drops landed`, bad === 0, `${bad} failed`);
    await L.reset(1000);
}
export function init() { L.start('zones', work); }
export async function run() { await new Promise(() => {}); }
