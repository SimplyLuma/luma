// SPDX-License-Identifier: Apache-2.0
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Clutter from 'gi://Clutter';
import Shell from 'gi://Shell';
import St from 'gi://St';
import Pango from 'gi://Pango';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {start} from '../../../src/luma-shell-presentation/preview-adapter.js';
import {homeLayout, pageRange, formOf} from '../../../src/luma-shell-presentation/layout.js';
import {testEditor} from './editor.js';
import {testTabletTasks} from './tablet-tasks.js';
import {testWidgets} from './widgets.js';
import {testRealSplit} from './real-split.js';
import {testDesktopDrag} from './desktop-drag.js';

const results = [];
const out = GLib.getenv('PRESENTATION_OUT');
const source = GLib.getenv('PRESENTATION_SOURCE');
const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT,
    ms, () => { resolve(); return GLib.SOURCE_REMOVE; }));
function check(name, condition, detail = '') {
    results.push({name, ok: Boolean(condition), detail});
    console.log(`[presentation] ${condition ? 'PASS' : 'FAIL'} ${name} ${detail}`);
}
async function shot(name) {
    Gio._promisify(Shell.Screenshot.prototype, 'screenshot_stage_to_content');
    Gio._promisify(Shell.Screenshot, 'composite_to_stream');
    const [content, scale] = await new Shell.Screenshot().screenshot_stage_to_content();
    const stream = Gio.File.new_for_path(`${out}/${name}.png`).replace(null, false, Gio.FileCreateFlags.NONE, null);
    await Shell.Screenshot.composite_to_stream(content.get_texture(), 0, 0,
        global.stage.width, global.stage.height, scale, null, 0, 0, 1, stream);
    stream.close(null);
}

async function work() {
    for (const [width, height, expected] of [[1280,800,'tablet'], [800,1280,'tablet'],
        [791,820,'fold'], [1100,757,'fold'], [390,844,'phone'], [480,480,'pocket']]) {
        check(`form ${width}x${height}`, formOf(width, height) === expected);
        for (const mode of ['tablet', 'mobile']) {
            const layout = homeLayout(width, height, mode, 125);
            const covered = [];
            for (let page = 0; page < layout.pages; page++) {
                const [start, end] = pageRange(layout, page, 125);
                for (let index = start; index < end; index++) covered.push(index);
            }
            check(`exact paging ${width}x${height} ${mode}`, covered.length === 125 && new Set(covered).size === 125);
        }
    }
    const widgetsOnly = Boolean(GLib.getenv('PRESENTATION_WIDGETS_ONLY'));
    const tasksOnly = Boolean(GLib.getenv('PRESENTATION_TASKS_ONLY'));
    const editorOnly = Boolean(GLib.getenv('PRESENTATION_EDITOR_ONLY'));
    if (widgetsOnly)
        new Gio.Settings({schema_id: 'org.gnome.desktop.interface'}).set_string('clock-format', '12h');
    const optionsMenu = Main.panel.statusArea.quickSettings.menu;
    const delayedOptionsPage = widgetsOnly ? optionsMenu._mainPage : null;
    if (delayedOptionsPage) delete optionsMenu._mainPage;
    const adapter = start(`${source}/src/luma-shell-presentation`, {widgets: editorOnly ? [{
        id: 'org.example.WidgetFixture', columns: 2, rows: 2,
        createActor: () => new St.Button({label: 'Widget fixture', accessible_name: 'Widget fixture',
            reactive: true, can_focus: true, style: 'background: #444; border-radius: 20px;'}),
    }] : widgetsOnly ? null : []});
    const presentation = adapter.presentation;
    if (delayedOptionsPage) {
        check('presentation starts before native Quick Options providers finish', !presentation._optionsInstalled);
        optionsMenu._mainPage = delayedOptionsPage;
        const readyActor = new St.Widget();
        optionsMenu.box.add_child(readyActor);
        for (let i = 0; i < 30 && !presentation._optionsInstalled; i++) await sleep(100);
        readyActor.destroy();
        check('Experience follows native Quick Options page creation', presentation._optionsInstalled &&
            presentation._options.get_parent() === presentation._modeUtilityRow,
            JSON.stringify({installed: presentation._optionsInstalled,
                parent: presentation._options.get_parent()?.style_class}));
    }
    const settings = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});
    const mode = async value => {
        presentation._call('SetPresentation', new GLib.Variant('(s)', [value]));
        await sleep(1800);
    };
    await sleep(1000);
    if (GLib.getenv('PRESENTATION_STATUS_COMPOSITING_ONLY')) {
        let acquired=0,released=0;
        const compositor=global.compositor;
        const disable=compositor.disable_unredirect;
        const enable=compositor.enable_unredirect;
        compositor.disable_unredirect=function() { if(new Error().stack.includes('_syncStatusCompositing')) acquired++;disable.call(this); };
        compositor.enable_unredirect=function() { if(new Error().stack.includes('_syncStatusCompositing')) released++;enable.call(this); };
        const state=()=>JSON.stringify({acquired,released,owned:presentation._statusComposited,mode:presentation.mode,visible:presentation._status.visible});
        try {
            await mode('tablet');
            check('Tablet status owns a compositing hold', presentation._statusComposited && acquired===1 && released===0, state());
            presentation._syncVisibility();presentation._syncVisibility();
            check('repeated visibility sync does not acquire extra holds', acquired===1 && released===0, state());
            await mode('mobile');
            check('Mobile retains the same status compositing hold', presentation._statusComposited && acquired===1 && released===0, state());
            Main.sessionMode.pushMode('unlock-dialog');await sleep(250);
            check('lock releases status compositing', !presentation._statusComposited && acquired===released, state());
            Main.sessionMode.popMode('unlock-dialog');await sleep(250);
            check('unlock reacquires status compositing once', presentation._statusComposited && acquired===released+1, state());
            await mode('desktop');
            check('Desktop releases the status compositing hold', !presentation._statusComposited && acquired===released, state());
            await mode('tablet');
            presentation._status.hide();
            check('hidden status releases compositing', !presentation._statusComposited && acquired===released, state());
            presentation._status.show();
            check('shown status reacquires compositing', presentation._statusComposited && acquired===released+1, state());
            await sleep(1000);
            let frames=0;const paint=global.stage.connect('after-paint',()=>frames++);await sleep(1100);global.stage.disconnect(paint);
            check('status compositing does not request continuous frames', frames<=3, state());
            adapter.stop();
            check('preview teardown balances all status holds', acquired===released, `${acquired}/${released}`);
        } finally {
            compositor.disable_unredirect=disable;compositor.enable_unredirect=enable;
        }
        return;
    }
    if (GLib.getenv('PRESENTATION_OPTIONS_ONLY')) {
        for (let i = 0; i < 30 && !presentation._modeTilingRow; i++) await sleep(100);
        let nativeRow = presentation._modeTilingRow;
        check('Experience shares the native Tiling utility row', Boolean(nativeRow) &&
            nativeRow.get_parent() === presentation._modeUtilityRow);
        check('Experience uses utility styling without radio tile background',
            presentation._options.has_style_class_name('luma-options-utility') &&
            !presentation._options.has_style_class_name('luma-options-radio') &&
            presentation._options.checked);
        for (const choice of ['desktop', 'tablet', 'mobile']) {
            await mode(choice);
            optionsMenu.open(); await sleep(300);
            check(`${choice} equal utility widths`, nativeRow &&
                Math.abs(nativeRow.width - presentation._options.width) <= 1 &&
                nativeRow.width > 0,
                `${nativeRow?.width} / ${presentation._options.width}`);
            check(`${choice} Experience title fits`,
                !presentation._options._contents._title.clutter_text.get_layout().is_ellipsized());
            check(`${choice} experience icon`, presentation._options.iconName === {
                desktop:'luma-monitor-symbolic', tablet:'luma-tablet-symbolic',
                mobile:'luma-smartphone-symbolic'}[choice]);
            presentation._options.menu.open(); await sleep(300);
            check(`${choice} native Experience detail page opens`,
                optionsMenu._activeMenu === presentation._options.menu);
            presentation._options.menu.close(); optionsMenu.close();
        }
        Main.extensionManager.disableExtension('tiling-toggle@project-luma.local');
        await sleep(500);
        check('Experience survives Tiling provider removal',
            !presentation._modeTilingRow && presentation._options.get_parent() === presentation._modeUtilityRow);
        Main.extensionManager.enableExtension('tiling-toggle@project-luma.local');
        for (let i = 0; i < 30 && !presentation._modeTilingRow; i++) await sleep(100);
        nativeRow = presentation._modeTilingRow;
        check('late Tiling provider rejoins the same row', nativeRow &&
            nativeRow.get_parent() === presentation._modeUtilityRow);
        await mode('tablet'); optionsMenu.open(); await sleep(300);
        const cameraIndicator = Main.panel.statusArea.quickSettings._camera;
        cameraIndicator._indicator.show(); cameraIndicator.show();
        await sleep(100);
        check('camera privacy glyph uses compact native sizing',
            cameraIndicator._indicator.get_theme_node().get_length('icon-size') === 16);
        await shot('experience-tiling-row'); optionsMenu.close();
        adapter.stop();
        check('unload preserves the native Tiling row', nativeRow &&
            nativeRow.get_parent() === optionsMenu._studioUtilities);
        return;
    }
    if (GLib.getenv('PRESENTATION_DESKTOP_DRAG_ONLY')) {
        await testDesktopDrag(presentation, check, sleep, shot, source);
        adapter.stop();
        return;
    }
    if (!GLib.getenv('PRESENTATION_APPKIT_ONLY') && !editorOnly && !tasksOnly && !widgetsOnly) {
    check('starts in desktop', presentation.mode === 'desktop');
    await mode('tablet');
    check('shared service enters Tablet', presentation.mode === 'tablet');
    check('native Home visible', presentation._home.visible && presentation._home.mapped);
    check('Home has a stable native chrome layer', presentation._home.get_parent() === Main.uiGroup);
    check('native status visible', presentation._status.visible && presentation._status.mapped);
    const loans = [...presentation._statusLoans];
    presentation._releaseStatus();
    check('status returns native indicators and restores their layout', loans.every(([actor, properties]) =>
        actor.get_parent() === Main.panel._rightBox &&
        Object.entries(properties).every(([key, value]) => actor[key] === value)));
    presentation._loanStatus();
    check('status reacquires indicators after releasing ownership', loans.every(([actor]) =>
        actor.get_parent() === presentation._statusRight));
    check('desktop Shelf hidden', !Main.shelf.visible);
    check('desktop panel unmapped', !Main.panel.mapped);
    for (const [x, y] of [[25,20], [1240,20], [630,1]]) {
        let actor = global.stage.get_actor_at_pos(Clutter.PickMode.ALL, x, y);
        const names = [];
        while (actor) {
            names.push(`${actor.name}:${actor.style_class}:${actor.visible}:${actor.mapped}`);
            actor = actor.get_parent();
        }
        console.log(`[presentation] pick ${x},${y} ${names.join(' -> ')}`);
    }
    for (const actor of presentation._panelChildren)
        console.log(`[presentation] status ${actor} ${actor.width}x${actor.height} pref=${actor.get_preferred_width(44)}`);
    check('Home contains only real installed apps', presentation._grid.pages.flat().every(item => item.kind === 'app' && presentation._apps.some(app => app.get_id() === item.id)));
    check('four accessible experience choices', presentation._modeButtons.size === 4);
    check('Tablet choice checked', presentation._modeButtons.get('tablet').checked);
    const theme = St.ThemeContext.get_for_stage(global.stage);
    const originalScale = theme.scale_factor;
    const originalFont = theme.get_font().copy();
    theme.set_scale_factor(2);
    await sleep(500);
    check('Home measures logical geometry at scale two',
        presentation._layout.width === Main.layoutManager.primaryMonitor.width / 2 &&
        presentation._status.height === 88);
    check('scaled app cells fit within the Home viewport', presentation._pageViews.every(page =>
        page.get_children().every(actor => actor.x >= 0 && actor.x + actor.width <= page.width)));
    await shot('tablet-home-scale-two');
    theme.set_scale_factor(originalScale);
    theme.set_font(Pango.FontDescription.from_string('Sans 24'));
    await sleep(500);
    check('large text increases the Home cell height', presentation._layout.cellHeight > 100);
    check('large text keeps page rows above the dock', presentation._pageViews.every((page, index) =>
        page.get_children()
            .every(actor => actor.y + actor.height <=
                Main.layoutManager.primaryMonitor.height - presentation._layout.bottom)));
    await shot('tablet-home-large-text');
    theme.set_font(originalFont);
    await sleep(500);
    const properties = Gio.DBus.session.call_sync('org.project_luma.ShellState1',
        '/org/project_luma/ShellState1', 'org.freedesktop.DBus.Properties', 'GetAll',
        new GLib.Variant('(s)', ['org.project_luma.ShellState1']), null,
        Gio.DBusCallFlags.NONE, 3000, null).deep_unpack()[0];
    check('typed application context reports Tablet', properties.Presentation.deep_unpack() === 'tablet');
    const titles = presentation._apps.map(info => info.get_display_name());
    check('Search is not an application', !titles.some(title => /^search$/i.test(title)));
    check('catalog contains graphical fixture', titles.includes('Graphical fixture'));
    check('catalog excludes terminal command', !titles.includes('Terminal command fixture'));
    check('catalog excludes NoDisplay fixture', !titles.includes('Hidden fixture'));
    check('catalog automatically creates multiple Home pages', presentation._layout.pages > 1);
    check('native swipe tracker enabled', presentation._pageSwipe.enabled);
    const keyboard = Clutter.get_default_backend().get_default_seat()
        .create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    presentation._home.grab_key_focus();
    keyboard.notify_keyval(GLib.get_monotonic_time(), Clutter.KEY_Page_Down, Clutter.KeyState.PRESSED);
    keyboard.notify_keyval(GLib.get_monotonic_time(), Clutter.KEY_Page_Down, Clutter.KeyState.RELEASED);
    await sleep(500);
    check('Page Down navigates Home', presentation._page === 1 && presentation._pageViews[1].visible);
    keyboard.notify_keyval(GLib.get_monotonic_time(), Clutter.KEY_Page_Up, Clutter.KeyState.PRESSED);
    keyboard.notify_keyval(GLib.get_monotonic_time(), Clutter.KEY_Page_Up, Clutter.KeyState.RELEASED);
    await sleep(500);
    check('Page Up returns to first Home', presentation._page === 0 && presentation._pageViews[0].visible);
    const appPath = `${GLib.get_user_data_dir()}/applications/org.example.Dynamic.desktop`;
    GLib.file_set_contents(appPath, '[Desktop Entry]\nType=Application\nName=Dynamic fixture\nExec=true\nIcon=application-x-executable\n');
    // Older supported Shell caches batch filesystem notifications for five
    // seconds. Wait for its canonical cache instead of bypassing it.
    for (let attempt = 0; attempt < 100 && !presentation._apps.some(info => info.get_id() === 'org.example.Dynamic.desktop'); attempt++)
        await sleep(100);
    check('new GUI app appears without restart', presentation._apps.some(info => info.get_id() === 'org.example.Dynamic.desktop'));
    Gio.File.new_for_path(appPath).delete(null);
    for (let attempt = 0; attempt < 100 && presentation._apps.some(info => info.get_id() === 'org.example.Dynamic.desktop'); attempt++)
        await sleep(100);
    check('removed app disappears without restart', !presentation._apps.some(info => info.get_id() === 'org.example.Dynamic.desktop'));
    await shot('tablet-home');
    check('Home stays mapped after capture', presentation._home.mapped);
    Main.panel.statusArea.quickSettings.menu.open();
    await sleep(700);
    check('Experience picker is in Quick Options', presentation._options.mapped);
    await shot('tablet-options');
    Main.panel.statusArea.quickSettings.menu.close();
    const launcher = Gio.Subprocess.new(['python3', `${source}/tests/gnome-shell/presentation/window.py`], Gio.SubprocessFlags.NONE);
    await sleep(1800);
    let window = global.get_window_actors().map(actor => actor.meta_window)
        .find(item => item.get_title() === 'Presentation document fixture');
    check('fixture window opened', Boolean(window));
    window?.activate(global.get_current_time());
    await sleep(250);
    check('Tablet app fullscreen', window?.is_fullscreen());
    check('Home yields to app', !presentation._home.visible);
    if (window) {
        const sequence = window.get_stable_sequence();
        await mode('desktop');
        check('same app survives Desktop switch', window.get_stable_sequence() === sequence && Boolean(window.get_compositor_private()));
        check('Desktop restores windowed app', !window.is_fullscreen());
        check('Desktop Shelf returns', Main.shelf.visible);
        await mode('mobile');
        check('Mobile keeps same app', window.get_stable_sequence() === sequence && window.is_fullscreen());
        presentation.showHome();
        await sleep(300);
        check('Home preserves running app', presentation._home.visible && window.minimized && Boolean(window.get_compositor_private()));
        await mode('desktop');
        check('Desktop restores Home-minimized app', !window.minimized && !window.is_fullscreen());
    }
    launcher.force_exit();
    await mode('automatic');
    check('Automatic restores desktop profile', presentation.mode === 'desktop');
    }
    if (widgetsOnly) await testWidgets(presentation, check, sleep, shot, mode, source);
    if (tasksOnly) {
        await mode('tablet');
        if (GLib.getenv('PRESENTATION_REAL_APPS'))
            await testRealSplit(presentation, check, sleep, shot, source, GLib.getenv('PRESENTATION_PLATFORM'));
        await testTabletTasks(presentation, check, sleep, shot, mode);
    }
    if (editorOnly) {
        for (const value of ['tablet', 'mobile']) {
            await mode(value);
            presentation.showHome();
            await testEditor(presentation, value, check, sleep);
            await shot(`${value}-home-editing`);
        }
        await mode('automatic');
    }
    const platform = GLib.getenv('PRESENTATION_PLATFORM');
    if (platform) {
        const launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDERR_MERGE);
        launcher.setenv('PYTHONPATH', `${source}/src/luma-platform/appkit`, true);
        launcher.setenv('GI_TYPELIB_PATH', `${platform}/ui:${platform}/semantics:${platform}/appearance`, true);
        launcher.setenv('LD_LIBRARY_PATH', `${platform}/ui:${platform}/semantics:${platform}/appearance`, true);
        launcher.set_stdout_file_path(`${out}/appkit-window.log`);
        const child = launcher.spawnv(['python3', `${source}/tests/gnome-shell/presentation/appkit-window.py`]);
        await new Promise(resolve => child.wait_check_async(null, (process, result) => {
            try {
                process.wait_check_finish(result);
                check('live C and Python AppKit frames preserve drafts', true);
            } catch (error) {
                check('live C and Python AppKit frames preserve drafts', false, error.message);
            }
            resolve();
        }));
        if (GLib.getenv('PRESENTATION_VIEWER_CONTROLS')) {
            await mode('tablet');
            launcher.setenv('PYTHONPATH', `${source}/src/luma-platform/appkit:${source}/src/luma-viewer`, true);
            launcher.setenv('LUMA_VIEWER_STYLE_PATH', `${source}/src/luma-viewer/viewer.css`, true);
            launcher.setenv('PYTHONWARNINGS', 'ignore::DeprecationWarning', true);
            launcher.set_stdout_file_path(`${out}/viewer-mode.log`);
            const viewer = launcher.spawnv(['python3', `${source}/src/luma-viewer/tests/tablet_mode_runtime.py`]);
            await new Promise(resolve => viewer.wait_check_async(null, (process, result) => {
                try { process.wait_check_finish(result); check('Viewer mode controls retain Tablet safe area', true); }
                catch (error) { check('Viewer mode controls retain Tablet safe area', false, error.message); }
                resolve();
            }));
        }
        if (GLib.getenv('PRESENTATION_EFFICIENCY_LAYOUT')) {
            await mode('desktop');
            launcher.setenv('PYTHONPATH', `${source}/src/luma-platform/appkit:${source}/src/luma-tide`, true);
            launcher.set_stdout_file_path(`${out}/efficiency-layout.log`);
            const layout = launcher.spawnv(['python3', `${source}/src/luma-platform/tests/idle_layout_runtime.py`]);
            await new Promise(resolve => layout.wait_check_async(null, (process, result) => {
                try { process.wait_check_finish(result); check('passive layout stays idle and adapts after resizing', true); }
                catch (error) { check('passive layout stays idle and adapts after resizing', false, error.message); }
                resolve();
            }));
        }
        await mode('automatic');
    }
    adapter.stop();
    check('preview adapter restores native Shelf owner', !Main.shelf.setPresentationMode);
    check('settings persist automatic choice', settings.get_string('presentation-mode') === 'automatic');
}

export function init() {
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 6000, () => {
        work().catch(error => check('scenario completed', false, `${error}\n${error.stack}`)).finally(() => {
            GLib.file_set_contents(`${out}/results.json`, JSON.stringify(results, null, 2));
            global.context.terminate();
        });
        return GLib.SOURCE_REMOVE;
    });
}
export async function run() { await new Promise(() => {}); }
