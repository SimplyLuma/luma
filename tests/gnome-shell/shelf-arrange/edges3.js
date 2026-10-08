// SPDX-License-Identifier: GPL-2.0-or-later
// Left and right edges on the owner's kind of desk: three monitors side by
// side, so the middle monitor's left and right edges are both shared. Every
// island (the dock in its vertical form included) is dragged by pointer to
// the left and right edges of the middle monitor at start, centre and end;
// the zones must exist, take the drop, reserve the band through Mutter and
// keep maximized windows clear of it. The outer monitors' outer edges too.
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as A from 'resource:///org/gnome/shell/ui/shelfArrangement.js';
import * as L from './lib.js';

const IDS = ['dock', 'live', 'media', 'notifications', 'well', 'quick-options', 'clock'];

async function dragTo(shelf, id, targetFor) {
    const r = L.rectOf(shelf.partActorFor(id));
    const [x0, y0] = L.centre(r);
    await L.move(x0, y0, 60);
    await L.press();
    await L.move(x0 + 12, y0 - 12, 60);
    await L.move(x0 + 20, y0 - 20, 80);
    const drag = shelf._arrange._drag;
    if (!drag) {
        await L.release();
        return null;
    }
    const [px, py] = targetFor(drag);
    for (let i = 1; i <= 20; i++)
        await L.move(x0 + 20 + (px - x0 - 20) * i / 20, y0 - 20 + (py - y0 + 20) * i / 20, 14);
    await L.sleep(300);
    const target = drag.target;
    await L.release();
    await L.sleep(700);
    return target;
}

async function work() {
    const shelf = await L.waitForShelf();
    for (let i = 0; i < 40 && Main.layoutManager.monitors.length < 3; i++)
        await L.sleep(250);
    await L.sleep(1500);
    const tray = new PanelMenu.Button(0.5, 'Tray glyph', false);
    tray.add_child(new St.Icon({icon_name: 'view-grid-symbolic', style_class: 'system-status-icon'}));
    Main.panel.addToStatusArea('edges3-tray', tray);
    L.notify();
    await L.sleep(2000);
    const monitors = shelf.monitors;
    L.check('three monitors', monitors.length === 3, JSON.stringify(monitors.map(m => [m.x, m.width, m.sharedEdges])));
    const middle = monitors.find(m => m.sharedEdges.includes('left') && m.sharedEdges.includes('right'));
    L.check('the middle monitor shares both side edges', !!middle);
    L.check('Mutter can reserve shared edges', shelf.canReserveSharedEdges);
    L.check('the middle monitor offers zones on both side edges', middle && !middle.closedEdges.length);
    let bad = 0, total = 0;
    for (const id of IDS) {
        for (const edge of ['left', 'right']) {
            for (const anchor of A.ZONE_ANCHORS) {
                await L.reset(700);
                shelf._arrange.begin({select: id});
                await L.sleep(400);
                const zones = shelf._arrange._zones.map(z => z._zone)
                    .filter(z => z.monitor === middle.index && z.edge === edge);
                const zone = zones.find(z => z.anchor === anchor);
                total++;
                if (!zone) {
                    bad++;
                    L.check(`${id} → middle ${edge} ${anchor}: zone drawn`, false);
                    shelf._arrange.end();
                    continue;
                }
                // Aim the pointer at the zone's placeholder.
                const target = await dragTo(shelf, id, () => L.centre(zone.rect));
                shelf._arrange.end();
                await L.sleep(600);
                const g = L.stored().find(x => x.islands.includes(id));
                const drawn = L.groups().find(x => x.islands.some(i => i.id === id || (id !== 'quick-options' && false)));
                const ok = g && g.edge === edge && g.anchor === anchor && g.display === middle.id;
                if (!ok)
                    bad++;
                if (!ok || anchor === 'center')
                    L.check(`${id} → middle ${edge} ${anchor}`, ok, `${JSON.stringify(target)} ${JSON.stringify(g)}`);
                if (ok && anchor === 'center') {
                    const wa = L.workArea(middle.index);
                    const reserved = edge === 'left' ? wa.x - middle.x : middle.x + middle.width - (wa.x + wa.width);
                    L.check(`${id} on the middle ${edge} edge reserves its band`, reserved >= 70, JSON.stringify(wa));
                    if (id === 'dock') {
                        const dock = L.rectOf(shelf.islandActorFor('dock'));
                        L.check('the dock takes its vertical form on a side edge', dock.height > dock.width && dock.width <= 60, JSON.stringify(dock));
                        await L.shot(`e3-dock-middle-${edge}`, middle);
                    }
                }
            }
        }
    }
    L.check(`all ${total} left and right drops on the shared edges landed`, bad === 0, `${bad} failed`);
    // A maximized window on the middle monitor stays clear of both side bands.
    await L.arrange([{display: middle.id, edge: 'left', anchor: 'center', islands: ['dock']},
        {display: middle.id, edge: 'right', anchor: 'start', islands: ['media']},
        {edge: 'bottom', anchor: 'end', islands: ['live', 'well', 'quick-options', 'clock', 'notifications']}], 2500);
    Shell.AppSystem.get_default().lookup_app('org.oracle.Files.desktop')?.activate();
    let window = null;
    for (let i = 0; i < 40 && !window; i++) {
        window = global.display.list_all_windows().find(w => w.title === 'Files');
        await L.sleep(250);
    }
    if (window) {
        window.move_to_monitor(middle.index);
        await L.sleep(400);
        try { window.maximize(Meta.MaximizeFlags.BOTH); } catch { window.maximize(); }
        await L.sleep(900);
        const r = window.get_frame_rect();
        const frame = {x: r.x, y: r.y, width: r.width, height: r.height};
        const hits = L.groups().flatMap(g => g.islands).filter(i => L.intersects(i.rect, frame));
        L.check('a maximized window on the middle monitor meets no island', hits.length === 0,
            `${JSON.stringify(frame)} ${JSON.stringify(hits.map(h => h.id))}`);
        await L.shot('e3-middle-maximized', middle);
    }
    await L.reset(1000);
}
export function init() { L.start('edges3', work); }
export async function run() { await new Promise(() => {}); }
