// SPDX-License-Identifier: GPL-2.0-or-later
// Zone indicators with weights: faint at rest (20-25%), rising smoothly as
// the pointer (or the carried island) comes near, the zone a drop takes at
// full weight; the same with reduced motion. The edge line: faint, brighter
// near the pointer, and absent under every placeholder, group and the ghost.
// Frames of the approach are saved as fade-NN.png.
import Gio from 'gi://Gio';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as A from 'resource:///org/gnome/shell/ui/shelfArrangement.js';
import * as L from './lib.js';

async function approach(shelf, label, zone) {
    const [zx, zy] = L.centre(zone.rect);
    const opacities = [];
    const start = [zx, zy + 700];
    for (let i = 0; i <= 14; i++) {
        const t = i / 14;
        await L.move(start[0], start[1] + (zy - start[1]) * t, 60);
        const actor = shelf._arrange._zones.find(z => z._zone === zone);
        opacities.push(actor.opacity);
        if (i % 2 === 0)
            await L.shot(`fade-${label}-${String(i).padStart(2, '0')}`,
                {x: zx - 300, y: Math.max(0, zy - 60), width: 600, height: 760});
    }
    return opacities;
}

async function work() {
    const shelf = await L.waitForShelf();
    await L.sleep(1000);
    for (const [label, animations] of [['motion', true], ['reduced', false]]) {
        new Gio.Settings({schema_id: 'org.gnome.desktop.interface'}).set_boolean('enable-animations', animations);
        await L.sleep(800);
        await L.move(1280, 700, 200);
        shelf._arrange.begin({select: 'dock'});
        await L.sleep(800);
        const zones = shelf._arrange._zones;
        const rest = zones.map(z => z.opacity);
        L.check(`${label}: every zone faint at rest (20-25%)`,
            rest.every(o => o >= 0.2 * 255 - 1 && o <= 0.25 * 255 + 1), JSON.stringify(rest));
        const top = zones.find(z => z._zone.edge === 'top' && z._zone.anchor === 'center')._zone;
        const seq = await approach(shelf, label, top);
        L.log(`${label} opacities approaching the top centre zone: ${JSON.stringify(seq)}`);
        L.check(`${label}: the weight never drops as the pointer nears`, seq.every((o, i) => i === 0 || o >= seq[i - 1]), JSON.stringify(seq));
        L.check(`${label}: it rises gradually (several steps between rest and near)`,
            new Set(seq).size >= 5 && seq[0] <= 0.25 * 255 + 1 && seq.at(-1) > 0.6 * 255, JSON.stringify(seq));
        const far = zones.find(z => z._zone.edge === 'left' && z._zone.anchor === 'end');
        L.check(`${label}: a far zone stays faint`, far && far.opacity <= 0.25 * 255 + 1, `${far?.opacity}`);
        // .119: with free placement off the zones are the only marks; the
        // edge line belongs to free placement, where there is no zone grid.
        L.check(`${label}: no edge line beside the zones`, (shelf._arrange._edgeLines ?? []).length === 0);
        L.settings().set_boolean('shelf-free-placement', true);
        await L.sleep(500);
        const line = shelf._arrange._edgeLines?.find(l => l._band.edge === 'top');
        L.check(`${label}: free placement draws the edge line instead of zones`, !!line && !shelf._arrange._zones.length);
        L.settings().set_boolean('shelf-free-placement', false);
        await L.sleep(500);
        const glowNear = A.edgeGlow(L.centre(top.rect)[0], L.centre(top.rect)[0], 0);
        const glowFar = A.edgeGlow(100, L.centre(top.rect)[0], 0);
        L.check(`${label}: the edge line is faint away from the pointer and brighter at it`, glowFar <= 0.12 && glowNear > 0.4,
            `${glowFar} ${glowNear}`);
        await L.shot(`weights-${label}-near-top`);
        shelf._arrange.end();
        await L.sleep(700);
    }
    new Gio.Settings({schema_id: 'org.gnome.desktop.interface'}).set_boolean('enable-animations', true);
}
export function init() { L.start('weights', work); }
export async function run() { await new Promise(() => {}); }
