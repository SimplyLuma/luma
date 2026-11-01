// SPDX-License-Identifier: Apache-2.0
// Native Mobile regression: drag an open Quick Options panel with real touch.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {PopupAnimation} from 'resource:///org/gnome/shell/ui/boxpointer.js';
const sleep = ms => new Promise(resolve => GLib.timeout_add_once(GLib.PRIORITY_DEFAULT, ms, resolve));
const results = [], frames = [];
function check(name, ok, detail = null) {
    results.push({name, ok: Boolean(ok), detail});
    if (!ok) throw new Error(name);
}
async function run() {
    const p = Main.presentation, q = p?._handheldOptions;
    check('Unlocked Mobile options fixture', p?.mode === 'mobile' && !Main.sessionMode.isLocked);
    q.open(); await sleep(500);
    check('Native panel starts fully open', q.isOpen && q.menu._boxPointer.mapped && !q._panelReveal);
    const m = Main.layoutManager.primaryMonitor;
    const touch = global.stage.context.get_backend().get_default_seat()
        .create_virtual_device(Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);
    let phase = 'idle';
    const paintId = global.stage.connect_after('after-paint', () => {
        const r = q._panelReveal;
        if (r?.mask && phase !== 'idle') frames.push({phase, bounds: [...r.mask.bounds],
            progress: r.progress, start: r.start, warming: r._warming,
            visible: q.menu._boxPointer.mapped, ready: r.ready});
    });
    const point = delta => [m.x + m.width / 2, m.y + m.height - 12 + delta];
    try {
        for (const distance of [40, 96, 160]) {
            phase = `play-${distance}`;
            touch.notify_touch_down(GLib.get_monotonic_time(), 0, ...point(0)); await sleep(40);
            for (let i = 1; i <= 6; i++) {
                touch.notify_touch_motion(GLib.get_monotonic_time(), 0, ...point(-distance * i / 6));
                await sleep(25);
            }
            await sleep(100);
            check(`${phase}: open-panel drag begins at full extent`, q._panelReveal?.start === 1);
            touch.notify_touch_motion(GLib.get_monotonic_time(), 0, ...point(-8)); await sleep(100);
            touch.notify_touch_up(GLib.get_monotonic_time(), 0); await sleep(400);
            const samples = frames.filter(f => f.phase === phase);
            check(`${phase}: actual gesture produced painted frames`, samples.length > 0);
            check(`${phase}: no hidden preparation frame`, samples.every(f =>
                !f.warming && f.ready && f.visible && f.bounds[2] > 0 && f.bounds[3] > 0));
            check(`${phase}: reversing restores the open panel`, q.isOpen && !q._panelReveal);
        }
        phase = 'cancel'; q.beginPull(true); q.updatePull(-140); await sleep(80); q.endPull(true); await sleep(350);
        check('Cancelled dismissal restores full open panel', q.isOpen && !q._panelReveal && q.menu._boxPointer.visible);
        phase = 'dismiss';
        touch.notify_touch_down(GLib.get_monotonic_time(), 0, ...point(0)); await sleep(40);
        for (let i = 1; i <= 10; i++) {
            touch.notify_touch_motion(GLib.get_monotonic_time(), 0, ...point(-(m.height - 90) * i / 10));
            await sleep(25);
        }
        touch.notify_touch_up(GLib.get_monotonic_time(), 0); await sleep(400);
        check('Full touch dismissal closes native panel', !q.isOpen && !q.menu._boxPointer.mapped && !q._panelReveal);
        check('Dismissal keeps nonzero coverage until closure', frames.filter(f => f.phase === 'dismiss')
            .every(f => !f.warming && f.bounds[2] > 0 && f.bounds[3] > 0));
        phase = 'idle';
        q.beginPull(false); q.updatePull(220); await sleep(120);
        const before = q._panelReveal.progress;
        q.beginPull(true);
        check('Taking over a partial reveal preserves progress', Math.abs(q._panelReveal.progress - before) < .001 &&
            q._panelReveal.start === before && !q._panelReveal._warming);
        q.endPull(); await sleep(400);
        q.menu.close(PopupAnimation.NONE);
        check('Teardown releases effect and pending reveal', !q._panelReveal &&
            !q.menu._boxPointer.get_effect('luma-panel-reveal'));
    } finally {
        global.stage.disconnect(paintId); touch.run_dispose(); q.open();
    }
}
GLib.timeout_add_once(GLib.PRIORITY_DEFAULT, 2400, () => run().catch(error => {
    results.push({name: 'Native test error', ok: false, detail: String(error.stack ?? error)});
}).finally(() => GLib.file_set_contents(`${GLib.getenv('PRESENTATION_OUT') ?? GLib.get_tmp_dir()}/options-drag.json`,
    JSON.stringify({results, frames}))));
