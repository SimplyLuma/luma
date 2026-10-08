// SPDX-License-Identifier: GPL-2.0-or-later
// All four appearance modes (ADR-044 §10), in and out of arrange mode: the
// default shelf, arrange mode with a snap preview under way, and the owner's
// arranged layout (dock on the left, music top-right, the clock at the top).
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

const arranged = [{edge: 'left', anchor: 'center', islands: ['dock']},
    {edge: 'top', anchor: 'end', islands: ['media']},
    {edge: 'top', anchor: 'center', islands: ['clock']},
    {edge: 'bottom', anchor: 'end', islands: ['live', 'well', 'quick-options', 'notifications']}];

async function work() {
    const shelf = await L.waitForShelf();
    L.notify();
    await L.sleep(2000);
    const W = global.stage.width, H = global.stage.height;
    for (const mode of ['dark', 'light', 'frost', 'glass']) {
        await L.setMode(mode);
        await L.reset(1500);
        await L.shot(`m-${mode}-1-default`);
        // Arrange mode, dragging the clock towards the top centre.
        shelf._arrange.begin({select: 'clock'});
        await L.sleep(700);
        L.check(`${mode}: arrange mode draws its card`, shelf._arrange._card?.mapped);
        const card = shelf._arrange._card;
        L.check(`${mode}: the card takes the treatment's surface`, card.has_style_class_name(`luma-surface-${mode}`) ||
            mode === 'glass' || mode === 'frost', card.style_class);
        const c = L.rectOf(shelf.partActorFor('clock'));
        await L.move(...L.centre(c), 100);
        await L.press();
        for (let i = 1; i <= 20; i++)
            await L.move(c.x + c.width / 2 + (W / 2 - c.x - c.width / 2) * i / 20, c.y + c.height / 2 + (42 - c.y - c.height / 2) * i / 20, 16);
        await L.sleep(500);
        await L.shot(`m-${mode}-2-arrange-snap`);
        await L.release();
        await L.sleep(800);
        // An attach preview beside the dock.
        const d = L.rectOf(shelf.partActorFor('dock'));
        const m = L.rectOf(shelf.partActorFor('media'));
        await L.move(...L.centre(m), 100);
        await L.press();
        for (let i = 1; i <= 20; i++)
            await L.move(m.x + m.width / 2 + (d.x + d.width + 40 - m.x - m.width / 2) * i / 20, m.y + m.height / 2, 16);
        await L.sleep(500);
        await L.shot(`m-${mode}-3-arrange-attach`);
        await L.release();
        await L.sleep(800);
        shelf._arrange.end();
        await L.sleep(600);
        await L.arrange(arranged, 2000);
        await L.shot(`m-${mode}-4-arranged`);
    }
    await L.setMode('dark');
    await L.reset(1000);
}
export function init() { L.start('modes', work); }
export async function run() { await new Promise(() => {}); }
