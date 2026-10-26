// SPDX-License-Identifier: Apache-2.0
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {breathe} from '../../../src/luma-shell-presentation/notificationPulse.js';
import {peekNotificationLip} from 'resource:///org/gnome/shell/ui/lumaNotificationBeacon.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
export async function testWidgets(p, check, sleep, shot, mode, source) {
    const run = arg => new Promise((resolve, reject) => {
        const child = Gio.Subprocess.new(['python3', `${source}/tests/gnome-shell/presentation/widget-data.py`, arg], Gio.SubprocessFlags.NONE);
        child.wait_check_async(null, (process, result) => {
            try { process.wait_check_finish(result); resolve(); } catch (error) { reject(error); }
        });
    });
    const menu = Main.panel.statusArea.quickSettings.menu;
    const original = {main: menu._mainPage, parent: menu._mainPage.get_parent(), classes: menu.box.style_class,
        children: menu._mainPage.get_children()};
    await run('seed'); await mode('tablet'); p.showHome();
    const lip = peekNotificationLip();
    const savedPulse = {state: lip._state, fresh: lip._fresh, visible: lip.visible, ease: lip._glow.ease};
    let pulses = 0;
    try {
        lip.hide(); lip._state = 'pill'; lip._fresh = true;
        lip._glow.ease = () => { pulses++; throw new Error('unmapped pulse attempted'); };
        lip._breathe();
        check('hidden notification glow does not schedule recursive animations', pulses === 0 && lip._glow.opacity === 0);
    } finally {
        lip._glow.ease = savedPulse.ease; lip._state = savedPulse.state;
        lip._fresh = savedPulse.fresh; lip.visible = savedPulse.visible;
    }
    let boundedPulses = 0;
    const pendingLip = {mapped: true, _state: 'pill', _fresh: true, dnd: false,
        _glow: {mapped: true, opacity: 0, remove_all_transitions() {},
            ease(options) { boundedPulses++; if (boundedPulses > 6) throw new Error('unbounded unread pulse'); options.onComplete(); }}};
    breathe.call(pendingLip);
    check('unread notification settles to a steady indicator after bounded pulses',
        boundedPulses <= 6 && pendingLip._glow.opacity === 128);
    const idle = new Gio.Settings({schema_id: 'org.gnome.desktop.session'});
    check('Tablet applies a two-minute idle default', idle.get_uint('idle-delay') === 120);
    for (let i = 0; i < 60 && p._liveWidgets.snapshot.weather.value !== '20°'; i++) await sleep(100);
    check('real Weather cache populates native widget', p._liveWidgets.snapshot.weather.title === 'Widget integration city' && p._liveWidgets.snapshot.weather.value === '20°');
    check('Weather and Calendar occupy whole 2-by-2 grid cells', ['weather', 'calendar'].every(id =>
        p._grid.pages.flat().some(item => item.id === id && item.kind === 'widget' && item.w === 2 && item.h === 2)));
    for (let i = 0; i < 100 && !p._liveWidgets._actors.get('calendar')?.detail.text.startsWith('Widget integration event'); i++) await sleep(100);
    check('native Calendar widget reads event saved through real EDS', p._liveWidgets._actors.get('calendar')?.detail.text.startsWith('Widget integration event'));
    await run('imperial');
    for (let i = 0; i < 50 && p._liveWidgets.snapshot.weather.value !== '68°'; i++) await sleep(100);
    check('Weather units follow app settings through file monitor', p._liveWidgets.snapshot.weather.value === '68°');
    const seat = Clutter.get_default_backend().get_default_seat();
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const time = () => GLib.get_monotonic_time();
    const click = async actor => {
        const [x, y] = actor.get_transformed_position(); const [w, h] = actor.get_transformed_size();
        pointer.notify_absolute_motion(time(), x+w/2, y+h/2);
        pointer.notify_button(time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
        pointer.notify_button(time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
        await sleep(350);
    };
    await click(p._clockButton);
    check('clock opens Quick Options, never GNOME calendar', menu.isOpen && !Main.panel.statusArea.dateMenu.menu.isOpen);
    check('Tablet uses the identical Desktop menu, styles and controls', menu._mainPage === original.main && menu.box.style_class.split(/\s+/).filter(name => name !== 'lumaui-touch-options').sort().join(' ') === original.classes.split(/\s+/).sort().join(' ') &&
        original.children.every(actor => actor.get_parent() === original.main));
    const [menuX, menuY] = menu.box.get_transformed_position();
    check('Quick Options is a bounded top-right popup', menuY < 100 && menuX+menu.box.width > Main.layoutManager.primaryMonitor.width-80 && menu.box.height < Main.layoutManager.primaryMonitor.height);
    check('Tablet status uses shared app-frame edge inset', p._statusInset === 12);
    const scale = p._scale;
    check('localized 12-hour clock has no alignment padding', p._time.text === p._clock.clock.trim() &&
        !/^\s/.test(p._time.text));
    const icon = Main.panel.statusArea.quickSettings._system._indicator;
    check('clock content starts at the inner app frame edge', Math.abs(p._time.get_transformed_position()[0] - 12*scale) < 1,
        JSON.stringify({clock: p._time.get_transformed_position(), button: p._clockButton.get_transformed_position(),
            status: p._status.get_transformed_position(), inset: p._statusInset, scale}));
    check('last status icon ends at the inner app frame edge', Math.abs(icon.get_transformed_position()[0] + icon.get_transformed_size()[0] - (Main.layoutManager.primaryMonitor.width - 12*scale)) < 1);
    await shot('tablet-desktop-quick-options');
    await click(p._options._menuButton);
    check('Experience opens the same native detail picker as Wi-Fi/Bluetooth', menu._activeMenu === p._options.menu && p._modeButtons.size === 4 && [...p._modeButtons.values()].every(item => item.mapped));
    check('Experience detail has no empty main-page spacer', !p._handheldOptions.scroll.visible);
    await shot('tablet-experience-picker');
    await click(menu._back);
    check('native Back returns from Experience to Quick Options', !menu._activeMenu && menu._mainPage.visible);
    pointer.notify_absolute_motion(time(), 2, Main.layoutManager.primaryMonitor.height/2);
    pointer.notify_button(time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
    pointer.notify_button(time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED); await sleep(250);
    check('outside click dismisses native popup', !menu.isOpen);
    await click(Main.panel.statusArea.quickSettings);
    check('right status opens the same native menu', menu.isOpen); menu.close();
    const touch = seat.create_virtual_device(Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);
    const [sx, sy] = p._status.get_transformed_position();
    touch.notify_touch_down(time(), 0, sx+p._status.width/2, sy+8); await sleep(100);
    for (let i = 1; i <= 12; i++) { touch.notify_touch_motion(time(), 0, sx+p._status.width/2, sy+8+i*5); await sleep(30); }
    touch.notify_touch_up(time(), 0); await sleep(300);
    check('downward status swipe opens the same menu', menu.isOpen); menu.close();
    await click(p._clockButton); await click(p._options._menuButton); await click(p._modeButtons.get('mobile'));
    await sleep(1800);
    check('native Experience picker changes the actual presentation', p.mode === 'mobile' && p._options.title === 'Mobile' && !p._options.subtitle);
    await click(p._clockButton);
    check('Mobile uses the same native Quick Options', menu.isOpen && menu._mainPage === original.main && menu.box.style_class.split(/\s+/).filter(name => name !== 'lumaui-touch-options').sort().join(' ') === original.classes.split(/\s+/).sort().join(' '));
    menu.close(); await run('remove');
    for (let i = 0; i < 50 && p._liveWidgets.snapshot.weather.title !== 'Weather'; i++) await sleep(100);
    check('removing saved city clears Weather', p._liveWidgets.snapshot.weather.title === 'Weather' && p._liveWidgets.snapshot.weather.value === '—');
    await click(p._clockButton);
    await click(p._options._menuButton);
    await click(p._modeButtons.get('desktop'));
    await sleep(1800);
    check('Desktop restores the native viewport without moving any controls', !p._handheldOptions.enabled &&
        menu._mainPage.get_parent() === original.parent && original.children.every(actor => actor.get_parent() === original.main));
    // Surface evidence: the native page can retain opacity0 while the closed
    // popup is unmapped. Opening must complete that interrupted page fade.
    menu._mainPage.opacity = 0;
    menu.open(); await sleep(400);
    check('Desktop menu and Experience picker still work', menu.isOpen && p._options.mapped);
    check('Desktop controls remain painted after choosing Desktop inside Experience',
        menu._mainPage.visible && menu._mainPage.mapped && menu._mainPage.opacity === 255 &&
        menu._mainPage.get_transformed_size()[1] > 100,
        JSON.stringify({visible: menu._mainPage.visible, mapped: menu._mainPage.mapped,
            opacity: menu._mainPage.opacity, size: menu._mainPage.get_transformed_size()}));
    await shot('desktop-after-experience-selection');
    menu.close();
    check('Desktop restores the five-minute default', idle.get_uint('idle-delay') === 300);
    p._call('SetIdleDelay', new GLib.Variant('(u)', [600])); await sleep(200);
    await mode('mobile');
    check('explicit idle preference survives presentation changes', idle.get_uint('idle-delay') === 600);
    idle.reset('idle-delay');
    new Gio.Settings({schema_id: 'org.project_luma.shell-state'}).reset('presentation-idle-default');
    const launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
    launcher.setenv('PYTHONPATH', `${source}/src/luma-continuity:${source}/src/prairie-core:${source}/src/luma-platform/appkit`, true);
    const child = launcher.spawnv(['python3', `${source}/src/luma-continuity/tests/native_cloud_recovery_smoke.py`]);
    await new Promise(resolve => child.communicate_utf8_async(null, null, (process, result) => {
        try {
            const [, out, err] = process.communicate_utf8_finish(result);
            check('Connect retries unavailable status without requesting enrollment', process.get_successful(), out + err);
        } catch (error) { check('Connect native recovery scenario', false, error.message); }
        resolve();
    }));
    await mode('automatic');
}
