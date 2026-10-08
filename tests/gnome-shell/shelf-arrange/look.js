// SPDX-License-Identifier: GPL-2.0-or-later
// Renders of arranged layouts, every island in its side-edge form included:
// SA_MODE picks the appearance mode (dark by default).
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as L from './lib.js';

const layouts = {
    'dock-left-music-top-right': [{edge: 'left', anchor: 'center', islands: ['dock']},
        {edge: 'top', anchor: 'end', islands: ['media']},
        {edge: 'bottom', anchor: 'end', islands: ['live', 'well', 'quick-options', 'clock', 'notifications']}],
    'all-on-the-right': [{edge: 'right', anchor: 'center', islands: ['dock']},
        {edge: 'right', anchor: 'start', islands: ['media', 'live']},
        {edge: 'right', anchor: 'end', islands: ['well', 'quick-options', 'clock', 'notifications']}],
    'clock-attached-to-dock': [{edge: 'bottom', anchor: 'center', islands: ['dock', 'clock']},
        {edge: 'bottom', anchor: 'end', islands: ['live', 'media', 'well', 'quick-options', 'notifications']}],
    'top-bar': [{edge: 'top', anchor: 'start', islands: ['dock']},
        {edge: 'top', anchor: 'center', islands: ['clock']},
        {edge: 'top', anchor: 'end', islands: ['live', 'media', 'well', 'quick-options', 'notifications']}],
};

async function work() {
    const shelf = await L.waitForShelf();
    const tray = new PanelMenu.Button(0.5, 'Tray glyph', false);
    tray.add_child(new St.Icon({icon_name: 'view-grid-symbolic', style_class: 'system-status-icon'}));
    Main.panel.addToStatusArea('look-tray', tray);
    L.notify();
    await L.setMode(GLib.getenv('SA_MODE') || 'dark');
    for (const [name, groups] of Object.entries(layouts)) {
        await L.arrange(groups, 2500);
        // Every island's content inside its island.
        for (const g of shelf._groups) {
            for (const island of g.row.get_children().filter(a => a.visible && a._content)) {
                const outer = L.rectOf(island);
                const spill = L.descend(island._content).filter(a => a.visible && a.mapped && a.width > 0 && a !== island._content)
                    .map(a => L.rectOf(a)).filter(r => !L.inside(r, outer, 2));
                L.check(`${name}: ${island.islandId ?? island.style_class} content stays inside its island`, spill.length === 0,
                    `${JSON.stringify(outer)} ${JSON.stringify(spill.slice(0, 2))}`);
            }
        }
        await L.shot(`look-${name}`);
        shelf._arrange.begin({select: 'media'});
        await L.sleep(900);
        await L.shot(`look-${name}-arrange`);
        shelf._arrange.end();
        await L.sleep(600);
    }
    await L.reset(1000);
}
export function init() { L.start('look', work); }
export async function run() { await new Promise(() => {}); }
