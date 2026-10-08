// SPDX-License-Identifier: GPL-2.0-or-later
// What the Dash does with the width it has, measured: the owner's bottom
// edge on his laptop panel (1536x960 logical) and on his ultrawide, with
// his settings. Prints the band, every island's length and every gap, so a
// proposal about small screens can be made in numbers.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {DASH_TILE_SIZE, DIVIDER_WIDTH, SHELF_INSET} from 'resource:///org/gnome/shell/ui/shelfMetrics.js';
import * as A from 'resource:///org/gnome/shell/ui/shelfArrangement.js';
import * as L from './lib.js';

async function measure(name, shelf, m) {
    const groups = L.groups().filter(g => g.edge === 'bottom')
        .map(g => ({anchor: g.anchor, rect: g.rect, islands: g.islands.map(i => ({id: i.id, w: i.rect.width}))}))
        .sort((a, b) => a.rect.x - b.rect.x);
    const band = shelf._bands?.get(m.index)?.bottom;
    const parts = [];
    let cursor = band ? band.along[0] : m.x;
    for (const g of groups) {
        parts.push(`gap ${Math.round(g.rect.x - cursor)}`);
        parts.push(`[${g.islands.map(i => `${i.id} ${Math.round(i.w)}`).join(' | ')}] = ${Math.round(g.rect.width)}`);
        cursor = g.rect.x + g.rect.width;
    }
    parts.push(`gap ${Math.round((band ? band.along[1] : m.x + m.width) - cursor)}`);
    L.log(`${name}: monitor ${m.width}x${m.height} band ${band ? Math.round(band.length) : '?'} ` +
        `(along ${band ? band.along.map(Math.round).join('..') : '?'}) thickness ${shelf._thickness} ` +
        `padding ${shelf._padding} tile ${DASH_TILE_SIZE} inset ${SHELF_INSET} divider ${DIVIDER_WIDTH} ` +
        `separation ${A.GROUP_SEPARATION} island gap ${A.ISLAND_GAP}`);
    L.log(`${name}: ${parts.join('  ')}`);
    const dock = shelf.islandActorFor('dock');
    const tiles = shelf._dash?.get_children?.().filter(c => c.visible).length ?? 0;
    L.log(`${name}: dock ${Math.round(L.rectOf(dock).width)} wide with ${tiles} tiles, viewport ${Math.round(shelf._dockNatural ?? 0)} natural`);
    L.check(`${name} measured`, !!band);
}

async function spawn(n) {
    const T = GLib.path_get_dirname(GLib.getenv('SA_OUT')) + '/../tests';
    const titles = [];
    for (let i = 0; i < n; i++) {
        const title = `Space ${i}`;
        titles.push(title);
        GLib.spawn_async(null, ['python3', `${T}/win.py`, `org.oracle.space${i}`, title],
            null, GLib.SpawnFlags.SEARCH_PATH, null);
        await L.sleep(250);
    }
    for (let i = 0; i < 60; i++) {
        const seen = global.display.list_all_windows().filter(w => titles.includes(w.title)).length;
        if (seen >= n)
            break;
        await L.sleep(250);
    }
    await L.sleep(1500);
}

async function work() {
    const shelf = await L.waitForShelf();
    const s = L.settings();
    // The owner's dock: 26 favourites, so the dock asks for a real length.
    const desktop = new Gio.Settings({schema_id: 'org.gnome.shell'});
    // Whatever this image really has, up to 26: the dock must ask for a
    // length a short edge cannot give it.
    const apps = Shell.AppSystem.get_default().get_installed()
        .map(info => info.get_id())
        .filter(id => id && Shell.AppSystem.get_default().lookup_app(id))
        .slice(0, 26);
    desktop.set_strv('favorite-apps', apps);
    L.log(`favourites: ${apps.length} (${apps.slice(0, 3).join(' ')}...)`);
    await L.sleep(1500);
    // The dock shows what is running, so give it something to show.
    await spawn(12);
    s.set_int('shelf-padding', 10);
    s.set_string('shelf-edge-mode', 'protruding');
    s.set_string('shelf-surface-mode', 'separate');
    s.set_boolean('shelf-span-full', false);
    s.set_boolean('shelf-float-ends', false);
    await L.arrange([{edge: 'bottom', anchor: 'start', islands: ['live', 'media']},
        {edge: 'bottom', anchor: 'center', islands: ['dock']},
        {edge: 'bottom', anchor: 'end', islands: ['well', 'quick-options', 'clock', 'notifications']}], 2000);
    for (const [name, spec] of [['panel', '1536x960*'], ['ultrawide', '5120x1440*'], ['1080p', '1920x1080*']]) {
        try {
            await L.layoutMonitors([spec]);
        } catch (e) {
            L.log(`${name}: no such monitor here (${e})`);
            continue;
        }
        await L.sleep(1800);
        const live = shelf._liveIndicator, media = shelf.getIsland('media');
        for (const crowded of [true, false]) {
            if (live)
                live.visible = crowded;
            if (media)
                media.visible = crowded;
            await L.sleep(1500);
            const m = Main.layoutManager.primaryMonitor;
            await measure(`${name} ${crowded ? 'crowded' : 'sparse'}`, shelf, m);
            if (name === 'panel') {
                const band = shelf._bands.get(m.index).bottom;
                const gs = L.groups().filter(g => g.edge === 'bottom' && g.rect.width > 0)
                    .sort((a, b) => a.rect.x - b.rect.x);
                const dockGroup = gs.find(g => g.islands.some(i => i.id === 'dock'));
                const holes = [];
                let cursor = band.along[0];
                for (const g of gs) {
                    holes.push(g.rect.x - cursor);
                    cursor = g.rect.x + g.rect.width;
                }
                holes.push(band.along[1] - cursor);
                const biggest = Math.max(...holes);
                const natural = shelf._dockNatural ?? 0;
                if (crowded) {
                    L.check('panel crowded: no hole bigger than the separation while the dock is short',
                        dockGroup.rect.width >= natural - 2 || biggest <= A.GROUP_SEPARATION + 2,
                        `dock ${Math.round(dockGroup.rect.width)} natural ${Math.round(natural)} holes ${holes.map(Math.round).join(',')}`);
                } else {
                    const centre = (band.along[0] + band.along[1]) / 2;
                    L.check('panel sparse: with room to spare the dock is centred',
                        Math.abs(dockGroup.rect.x + dockGroup.rect.width / 2 - centre) <= 2,
                        `${Math.round(dockGroup.rect.x + dockGroup.rect.width / 2)} vs ${Math.round(centre)}`);
                }
            }
            await L.shot(`space-${name}-${crowded ? 'crowded' : 'sparse'}`, Main.layoutManager.primaryMonitor);
        }
        if (live)
            live.visible = true;
    }
    await L.reset(800);
}
export function init() { L.start('space', work); }
export async function run() { await new Promise(() => {}); }
