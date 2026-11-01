// SPDX-License-Identifier: Apache-2.0
// Run inside an unlocked Mobile Shell session with at least one fixture app.
// Exercise real touch entry from an app, tap/swipe dismissal and scene cleanup.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
const sleep = ms => new Promise(resolve => GLib.timeout_add_once(GLib.PRIORITY_DEFAULT, ms, resolve));
const results = [], frames = [];
function check(name, ok, detail = null) {
    results.push({name, ok: Boolean(ok), detail});
    if (!ok) throw new Error(name);
}
async function run() {
    const p = Main.presentation, tasks = p?._tasks;
    check('Unlocked Mobile fixture', p?.mode === 'mobile' && tasks.navigationEnabled);
    check('Home is not being edited', !p._editor.dragging && !p._editor.resizing && !p._editor.savingResize);
    tasks._switcherPopup?.destroy();
    const window = [...p._windows.keys()].find(w => w.get_compositor_private());
    check('Running app fixture exists', Boolean(window));
    Main.activateWindow(window); await sleep(500);
    check('Entry begins in an active app', !p._home.visible && !window.minimized);
    const baseline = new Set(Main.uiGroup.get_children());
    let phase = 'app-entry';
    const paintId = global.stage.connect_after('after-paint', () => {
        const popup = tasks._switcherPopup;
        if (!popup || popup._destroyed) return;
        const children = Main.uiGroup.get_children();
        frames.push({phase, progress: popup._travelProgress, clones: children.filter(a =>
            a instanceof Clutter.Clone && !baseline.has(a)).length,
        lift: Boolean(tasks._navigationLift), returningHome: popup._returningHome,
        widths: popup._cards.map(c => c.get_transformed_size()[0]),
        backdrop: popup._wallpaper.opacity === 255 && popup._wallpaper.width >= global.stage.width &&
            popup._wallpaper.height >= global.stage.height && popup._wallpaper.get_parent() === Main.uiGroup,
        ordered: children.indexOf(popup._wallpaper) < children.indexOf(p._home) &&
            children.indexOf(p._home) < children.indexOf(popup),
        windowsHidden: !global.window_group.visible});
    });
    const seat = global.stage.context.get_backend().get_default_seat();
    const touch = seat.create_virtual_device(Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const m = tasks.monitor, x = m.x + m.width / 2;
    const swipe = async hold => {
        touch.notify_touch_down(GLib.get_monotonic_time(), 0, x, m.y + m.height - 6);
        await sleep(40);
        for (let i = 1; i <= 10; i++) {
            touch.notify_touch_motion(GLib.get_monotonic_time(), 0, x, m.y + m.height - 6 - i * 12 * p._scale);
            await sleep(18);
        }
        if (hold) await sleep(350);
        touch.notify_touch_up(GLib.get_monotonic_time(), 0);
    };
    const verifyFrames = label => {
        const samples = frames.filter(f => f.phase === label);
        check(`${label}: rendered transition frames`, samples.length > 0);
        check(`${label}: no fullscreen gesture copy or lift`, samples.every(f => !f.clones && !f.lift));
        check(`${label}: cards retain thumbnail geometry`, samples.every(f => f.widths.every(w => w < m.width * .8)));
        check(`${label}: opaque backdrop below Home and cards`, samples.every(f => f.backdrop && f.ordered));
        check(`${label}: live windows stay hidden`, samples.every(f => f.windowsHidden));
    };
    try {
        await swipe(true); await sleep(250);
        const entry = tasks._switcherPopup;
        check('Real swipe-and-hold opens switcher from active app', Boolean(entry));
        verifyFrames('app-entry');
        phase = 'background-tap';
        pointer.notify_absolute_motion(GLib.get_monotonic_time(), m.x + 10, m.y + 90 * p._scale);
        await sleep(40);
        touch.notify_touch_down(GLib.get_monotonic_time(), 0, m.x + 10, m.y + 90 * p._scale);
        await sleep(35); touch.notify_touch_up(GLib.get_monotonic_time(), 0);
        await sleep(80);
        check('Actual background tap uses Mobile return to Home', entry._returningHome && entry.leaving);
        await sleep(350);
        verifyFrames('background-tap');
        check('Background tap restores Home and removes popup/backdrop', entry._destroyed && p._home.visible &&
            p._home.opacity === 255 && !tasks._switcherPopup);
        Main.activateWindow(window); await sleep(450);
        phase = 'swipe-entry'; await swipe(true); await sleep(250);
        const exit = tasks._switcherPopup;
        check('Second app entry succeeds', Boolean(exit)); verifyFrames('swipe-entry');
        phase = 'swipe-exit'; await swipe(false); await sleep(400);
        verifyFrames('swipe-exit');
        check('Bottom swipe restores Home without another app animation', exit._destroyed && p._home.visible &&
            p._home.opacity === 255 && !tasks._switcherPopup && !tasks._navigationLift);
        const leaked = () => Main.uiGroup.get_children().some(a => a.name === 'luma-mobile-switcher-backdrop');
        check('Backdrop released after dismissal', !leaked());
        phase = 'home-entry'; tasks.openSwitcher(); await sleep(450); verifyFrames('home-entry');
        const selected = tasks._switcherPopup, card = selected._cards.find(c => c.window === window);
        phase = 'app-selection'; selected.activate(card); await sleep(500);
        check('Explicit card selection still activates its app', selected._destroyed && !window.minimized &&
            global.display.focus_window === window && !p._home.visible);
        check('App selection releases its backdrop', !leaked());
        phase = 'abort'; tasks.openSwitcher(); await sleep(80); tasks._switcherPopup.destroy(); await sleep(100);
        check('Interrupted teardown releases backdrop and restores opacity', !leaked() && p._home.opacity === 255);
    } finally {
        global.stage.disconnect(paintId);
        touch.run_dispose(); pointer.run_dispose();
        tasks._switcherPopup?.destroy(); p.showHome();
    }
}
GLib.timeout_add_once(GLib.PRIORITY_DEFAULT, 2400, () => run().catch(error => {
    results.push({name: 'Native test error', ok: false, detail: String(error.stack ?? error)});
}).finally(() => GLib.file_set_contents(`${GLib.getenv('PRESENTATION_OUT') ?? GLib.get_tmp_dir()}/mobile-switcher-handoff.json`,
    JSON.stringify({results, frames}))));
