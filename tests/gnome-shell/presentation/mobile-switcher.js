// SPDX-License-Identifier: Apache-2.0
// Native Shell-session check: import this module in a Mobile test session.
// Uses the installed presentation and real main loop; never changes layouts.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
const sleep = ms => new Promise(resolve => GLib.timeout_add_once(GLib.PRIORITY_DEFAULT, ms, resolve));
const results = [];
function check(name, ok, detail = null) {
    results.push({name, ok: Boolean(ok), detail});
    if (!ok) throw new Error(name);
}
async function run() {
    const p = Main.presentation, tasks = p?._tasks;
    check('Mobile test session is unlocked', p?.mode === 'mobile' && tasks.navigationEnabled);
    check('Home is not being edited', !p._editor.dragging && !p._editor.resizing && !p._editor.savingResize);
    tasks._switcherPopup?.destroy();
    const fake = Object.create(tasks);
    fake._hold = 0; fake._held = false; fake._navigationLift = null;
    let opens = 0, ticks = 0, elapsed = 0;
    const start = GLib.get_monotonic_time();
    fake.openSwitcher = () => { opens++; elapsed = (GLib.get_monotonic_time() - start) / 1000; };
    fake._switcherFeedback = () => ticks++;
    fake.verticalUpdate(-80 * p._scale);
    await sleep(170);
    check('Hold does not trigger before 220ms', opens === 0);
    await sleep(160);
    check('Hold triggers once near 220ms with one feedback request', opens === 1 && ticks === 1 && elapsed >= 205 && elapsed < 370, elapsed);
    fake._held = false;
    fake.verticalUpdate(-80 * p._scale); await sleep(60);
    fake.verticalUpdate(-20 * p._scale); await sleep(330);
    check('Retreat below threshold cancels hold', opens === 1 && !fake._hold);
    fake.verticalUpdate(-80 * p._scale); await sleep(60);
    fake.verticalEnd(0); await sleep(330);
    check('Release cancels hold', opens === 1 && !fake._hold);
    // Continuing motion beyond the hold time must not count as a pause.
    fake.verticalUpdate(-80 * p._scale);
    for (let i = 1; i <= 9; i++) {
        await sleep(35);
        fake.verticalUpdate((-80 - i * 12) * p._scale);
    }
    check('Continuing upward motion does not open switcher', opens === 1 && ticks === 1);
    await sleep(85);
    check('Settling after motion activates once', opens === 2 && ticks === 2);
    const aboveHome = popup => {
        const siblings = Main.uiGroup.get_children();
        return siblings.indexOf(popup) > siblings.indexOf(p._home);
    };
    p.showHome(); tasks.openSwitcher();
    const popup = tasks._switcherPopup;
    await sleep(500);
    check('Switcher is stacked above Home', aboveHome(popup));
    check('Mobile cards have titles without state, close or lift', popup._cards.every(c =>
        !c._time.visible && !c._close.reactive && c._close.opacity === 0 && c.lift === 0 && c.translation_y === 0));
    // Exercise the actual bottom-edge pan, separately from card dismissal.
    const touch = global.stage.context.get_backend().get_default_seat()
        .create_virtual_device(Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);
    const m = tasks.monitor, x = m.x + m.width / 2;
    try {
        touch.notify_touch_down(GLib.get_monotonic_time(), 0, x, m.y + m.height - 6);
        await sleep(40);
        for (let i = 1; i <= 8; i++) {
            touch.notify_touch_motion(GLib.get_monotonic_time(), 0, x, m.y + m.height - 6 - i * 10 * p._scale);
            await sleep(20);
        }
        touch.notify_touch_up(GLib.get_monotonic_time(), 0);
        await sleep(30);
        check('Bottom swipe starts animated return to Home', popup.leaving && !popup._destroyed);
        check('Returning Home stays below switcher', aboveHome(popup));
        check('Home icons wait until cards are mostly faded',
            popup._travelProgress < .35 || p._home.opacity === 0);
        const frames = [];
        const paintId = global.stage.connect('after-paint', () => {
            if (!popup._destroyed) frames.push({above: aboveHome(popup),
                progress: popup._travelProgress, home: p._home.opacity});
        });
        await sleep(300);
        global.stage.disconnect(paintId);
        check('Every sampled exit frame retains layering and phased icons', frames.length > 0 &&
            frames.every(f => f.above && (f.progress < .35 || f.home === 0)));
        await sleep(350);
        check('Bottom swipe restores Home and releases modal popup', popup._destroyed && !tasks._switcherPopup && p._home.visible && p._home.opacity === 255);
    } finally { touch.run_dispose(); }
    tasks.openSwitcher(); await sleep(80);
    const interrupted = tasks._switcherPopup, before = interrupted._travelProgress;
    interrupted.dismiss(null, true);
    check('Interrupted entry reverses current progress', Math.abs(before - interrupted._travelProgress) < .02);
    await sleep(350);
    check('Interrupted entry restores Home', interrupted._destroyed && p._home.visible && p._home.opacity === 255);
}
GLib.timeout_add_once(GLib.PRIORITY_DEFAULT, 2400, () => run().catch(error => {
    results.push({name: 'Native test error', ok: false, detail: String(error.stack ?? error)});
}).finally(() => {
    Main.presentation?._tasks._switcherPopup?.destroy();
    Main.presentation?.showHome();
    GLib.file_set_contents(`${GLib.getenv('PRESENTATION_OUT') ?? GLib.get_tmp_dir()}/mobile-switcher.json`, JSON.stringify(results));
}));
