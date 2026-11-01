// SPDX-License-Identifier: Apache-2.0
// Native session checks for shared full-width Mobile drawers and Home search.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {peekNotificationLip} from 'resource:///org/gnome/shell/ui/lumaNotificationBeacon.js';
import {PopupAnimation} from 'resource:///org/gnome/shell/ui/boxpointer.js';
const sleep = ms => new Promise(resolve => GLib.timeout_add_once(GLib.PRIORITY_DEFAULT, ms, resolve));
const results = [], frames = [];
function check(name, ok, detail = null) {results.push({name, ok: Boolean(ok), detail}); if (!ok) throw new Error(name);}
async function run() {
    const p = Main.presentation, q = p._handheldOptions, lip = peekNotificationLip();
    check('Unlocked Mobile fixture', p.mode === 'mobile' && !Main.sessionMode.isLocked);
    check('Native notification island and shared search exist', Boolean(lip && Main.lumaSearch));
    check('Home is not being edited', !p._editor.editing && !p._editor.dragging && !p._editor.resizing);
    const m = Main.layoutManager.primaryMonitor;
    const touch = global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);
    let phase = 'idle', current;
    const paintId = global.stage.connect_after('after-paint', () => {
        const r = current === 'notifications' ? p._motion._surfaceReveals.get(lip) : q._panelReveal;
        if (phase !== 'idle' && r?.mask && !r._warming) frames.push({phase, bounds: [...r.mask.bounds], progress: r.progress, drawer: r.drawer, coverage: r.mask.coverage});
    });
    const move = async (x, y, delta, steps = 6) => {
        touch.notify_touch_down(GLib.get_monotonic_time(), 0, x, y); await sleep(40);
        for (let i = 1; i <= steps; i++) {touch.notify_touch_motion(GLib.get_monotonic_time(), 0, x, y + delta * i / steps); await sleep(20);}
    };
    const closeAll = async () => {q.menu.close(PopupAnimation.NONE); lip.close(); await sleep(350);};
    const rect = actor => {const [x,y] = actor.get_transformed_position();const [width,height] = actor.get_transformed_size();return {x,y,width,height};};
    try {
        for (const which of ['right', 'clock', 'island-padding', 'island-native']) {
            await closeAll(); current = which.startsWith('island') ? 'notifications' : 'options'; phase = which;
            const r = rect(which === 'clock' ? p._clockButton : which === 'right' ? p._statusRight : lip);
            const x = which === 'island-padding' ? r.x - 14 * p._scale : r.x + r.width / 2;
            const y = which === 'island-native' ? r.y + r.height / 2 : m.y + 22 * p._scale;
            await move(x, y, 140); await sleep(80);
            const reveal = current === 'notifications' ? p._motion._surfaceReveals.get(lip) : q._panelReveal;
            check(`${which}: real touch opens intended drawer`, Boolean(reveal?.drawer), {state: lip._state, q: q.isOpen});
            check(`${which}: surface is mapped on screen`, reveal.actor.mapped);
            check(`${which}: full width from top`, reveal.mask.bounds[0] === 0 && reveal.mask.bounds[1] === 0 && reveal.mask.bounds[2] === m.width);
            check(`${which}: finger controls backdrop without cropping`, reveal.mask.bounds[3] === m.height &&
                Math.abs(reveal.progress - 140 / m.height) < .005 && reveal.mask.coverage > 0 && reveal.mask.coverage < 1);
            const opacity = reveal.mask.coverage;
            touch.notify_touch_motion(GLib.get_monotonic_time(), 0, x, y + 70); await sleep(80);
            check(`${which}: reversing softens backdrop immediately`, reveal.mask.coverage < opacity);
            touch.notify_touch_motion(GLib.get_monotonic_time(), 0, x, y + 140); await sleep(20);
            touch.notify_touch_up(GLib.get_monotonic_time(), 0); await sleep(400);
            check(`${which}: release settles fully open`, current === 'notifications' ? lip.isOpen : q.isOpen);
            check(`${which}: settled surface remains mapped`, current === 'notifications' ? lip.mapped : q.menu._boxPointer.mapped);
            phase = `${which}-close`;
            await move(m.x + m.width / 2, m.y + m.height - 12, -(m.height - 90), 10);
            touch.notify_touch_up(GLib.get_monotonic_time(), 0); await sleep(400);
            check(`${which}: bottom swipe closes drawer`, current === 'notifications' ? !lip.isOpen : !q.isOpen);
        }
        check('All sampled drawer frames remain full width at x0', frames.length > 0 && frames.every(f => f.drawer && f.bounds[0] === 0 && f.bounds[2] === m.width));
        phase = 'idle'; await closeAll(); p.showHome(); await sleep(250);
        await move(m.x + m.width / 2, m.y + m.height - 6, -120 * p._scale, 6);
        touch.notify_touch_up(GLib.get_monotonic_time(), 0); await sleep(400);
        check('Short swipe on Home opens the shared master search', Main.lumaSearch._dialog.visible && !p._tasks._switcherPopup);
        Main.lumaSearch.close(); await sleep(250); p.showHome();
        await move(m.x + m.width / 2, m.y + m.height - 6, -120 * p._scale, 6); await sleep(350);
        touch.notify_touch_up(GLib.get_monotonic_time(), 0); await sleep(300);
        check('Holding Home swipe still opens app switcher', Boolean(p._tasks._switcherPopup) && !Main.lumaSearch._dialog.visible);
        p._tasks._switcherPopup?.dismiss(null, true); await sleep(350);
        const window = [...p._windows.keys()].find(w => w.get_compositor_private());
        check('Running app available for Home navigation regression', Boolean(window));
        Main.activateWindow(window); await sleep(450);
        await move(m.x + m.width / 2, m.y + m.height - 6, -120 * p._scale, 6);
        touch.notify_touch_up(GLib.get_monotonic_time(), 0); await sleep(400);
        check('Short swipe from an app returns Home without search', p._home.visible && !Main.lumaSearch._dialog.visible);
    } finally {
        global.stage.disconnect(paintId); touch.run_dispose();
        Main.lumaSearch.close(); p._tasks._switcherPopup?.destroy(); lip.close(); p.showHome();
        q.open();
    }
}
GLib.timeout_add_once(GLib.PRIORITY_DEFAULT,2400,()=>run().catch(error=>{
    results.push({name:'Native test error',ok:false,detail:String(error.stack??error)});
}).finally(()=>GLib.file_set_contents(`${GLib.getenv('PRESENTATION_OUT')??GLib.get_tmp_dir()}/mobile-drawers.json`,JSON.stringify({results,frames}))));
