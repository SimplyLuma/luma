// SPDX-License-Identifier: GPL-2.0-or-later
// Run inside the actual packaged Shell in a private compositor/profile.
// Synthetic networks exercise real St actors, not radio hardware discovery.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import NM from 'gi://NM';
import Meta from 'gi://Meta';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as AppDisplay from 'resource:///org/gnome/shell/ui/appDisplay.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as NetworkAgent from 'resource:///org/gnome/shell/ui/components/networkAgent.js';
import * as PrairieLogin from 'resource:///org/gnome/shell/ui/prairieLogin.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';
import {BaselineClockRow} from 'resource:///org/gnome/shell/ui/lumaClockLayout.js';

const PosterLayout = GObject.registerClass(class PosterFixtureLayout extends Clutter.LayoutManager {
    vfunc_allocate(container, box) {
        container.get_first_child().allocatePoster(box.get_width(), box.get_height(), 360, 1, false);
    }
});
let assertions = 0;
function check(value, message) {
    if (!value) throw new Error(message);
    assertions++;
    print(`PASS ${message}`);
}
async function pause() { await Scripting.sleep(350); }
function baseline(label) {
    return label.clutter_text.get_transformed_position()[1] +
        label.clutter_text.get_layout().get_baseline() / Pango.SCALE;
}
async function capture(name) {
    const width = global.stage.width;
    const file = Gio.File.new_for_path(`/var/tmp/luma-shell100-native/captures/${width}-${name}.png`);
    const stream = file.replace(null, false, Gio.FileCreateFlags.NONE, null);
    try { await new Shell.Screenshot().screenshot(false, stream); }
    finally { stream.close(null); }
}
export async function run() {
    const iface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
    const appearance = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});
    const shell = new Gio.Settings({schema_id: 'org.gnome.shell'});
    const a11y = new Gio.Settings({schema_id: 'org.gnome.desktop.a11y.interface'});
    iface.set_boolean('enable-animations', false);
    iface.set_string('clock-format', '12h');
    appearance.set_string('surface-treatment', 'light');
    shell.set_strv('enabled-extensions', []);
    Main.overview.hide();
    await pause();
    const qs = Main.panel.statusArea.quickSettings;
    check(qs._statusTimeRow instanceof BaselineClockRow, 'packaged clock uses its native baseline owner');
    for (const rtl of [false, true]) {
        qs._statusTimeRow.set_text_direction(rtl ? Clutter.TextDirection.RTL : Clutter.TextDirection.LTR);
        for (const textScale of [1, 1.3]) {
            iface.set_double('text-scaling-factor', textScale);
            await pause();
            check(qs._statusAmPm.visible, `AM/PM survives RTL=${rtl}, text scale=${textScale}`);
            const delta = Math.abs(baseline(qs._statusTime) - baseline(qs._statusAmPm));
            print(`ACTUAL clock baseline delta ${delta}`);
            check(delta <= 1, `actual clock text baselines align RTL=${rtl}, scale=${textScale}`);
        }
    }
    iface.set_double('text-scaling-factor', 1);
    qs._statusTimeRow.set_text_direction(Clutter.TextDirection.LTR);
    await pause();
    check(qs._statusDateLabel.get_transformed_position()[1] >=
        qs._statusTimeRow.get_transformed_position()[1] + qs._statusTimeRow.height + 1,
    'date keeps a real gap below time row');
    await capture('clock');
    const poster = new PrairieLogin.Clock();
    const posterHost = new St.Widget({layout_manager: new PosterLayout(),
        width: global.stage.width, height: global.stage.height});
    posterHost.add_child(poster);
    Main.uiGroup.add_child(posterHost);
    await pause();
    check(poster._ampm.mapped && poster._ampm.width > 0 && poster._weekday.width > 0,
        'Poster fixture uses real parent-owned allocation with visible AM/PM');
    check(poster._ampm.visible && Math.abs(baseline(poster._time) - baseline(poster._ampm)) <= 1,
        'native Poster clock aligns actual AM/PM baseline');
    await capture('poster');
    posterHost.destroy();

    const shelf = Main.shelf;
    check(!!shelf, 'actual Shelf owns work-area geometry');
    shelf._settings.set_int('shelf-padding', 14);
    const monitor = Main.layoutManager.primaryMonitor;
    for (const reserve of [true, false]) {
        shelf._settings.set_boolean('shelf-reserve-work-area', reserve);
        await pause();
        const area = global.workspace_manager.get_active_workspace().get_work_area_for_monitor(monitor.index);
        print(`ACTUAL work area reserve=${reserve} ${area.x},${area.y} ${area.width}x${area.height}`);
        check(area.x - monitor.x >= 14 && area.y - monitor.y >= 14 &&
            monitor.x + monitor.width - area.x - area.width >= 14,
        `tiling-off work area preserves all empty-edge padding reserve=${reserve}`);
    }
    shelf._settings.set_boolean('shelf-reserve-work-area', true);
    await pause();

    // Populate only this disposable profile. Existing installed app metadata
    // supplies icons; absent fixtures model geometry, never bundled-app claims.
    const favorites = JSON.parse(GLib.getenv('LUMA_FOLLOWUP_FAVORITES'));
    check(favorites.length === 22, 'factory-layout fixture has exactly 22 identities');
    for (const [index, id] of favorites.entries()) {
        if (!Shell.AppSystem.get_default().lookup_app(id)) {
            const path = `${GLib.getenv('XDG_DATA_HOME')}/applications/${id}`;
            GLib.file_set_contents(path, `[Desktop Entry]\nType=Application\nName=Dock fixture ${index + 1}\nExec=/usr/bin/true\nIcon=application-x-executable\n`);
        }
    }
    for (let retry = 0; retry < 20 && favorites.some(id =>
        !Shell.AppSystem.get_default().lookup_app(id)); retry++)
        await pause();
    check(favorites.every(id => Shell.AppSystem.get_default().lookup_app(id)),
        'all fixture desktop identities register before favorites are applied');
    shell.set_strv('favorite-apps', favorites);
    await Scripting.sleep(1200);
    const dock = shelf._dockScroll;
    const [dockX, dockY] = dock.get_transformed_position();
    print(`ACTUAL dock ${dockX},${dockY} ${dock.width}x${dock.height} range=${dock.hadjustment.upper} page=${dock.hadjustment.page_size}`);
    check(dock.width > 100 && dockX >= 0 && dockX + dock.width <= global.stage.width,
        '22-favorite dock viewport stays within screen');
    const favoriteActors = shelf._dash._box.get_children().filter(child => child.child?._delegate?.app);
    print(`ACTUAL mapped favorite actor count ${favoriteActors.length}: ${favoriteActors.map(actor => actor.child._delegate.app.get_id()).join(',')}`);
    print(`ACTUAL lookup ${favorites.map(id => `${id}:${Shell.AppSystem.get_default().lookup_app(id)?.get_id()}`).join(',')}`);
    check(favoriteActors.length === 22, 'all 22 favorites have real dock actors');
    if (dock.hadjustment.upper > dock.hadjustment.page_size) {
        dock.hadjustment.value = dock.hadjustment.upper - dock.hadjustment.page_size;
        await pause();
        const finalActor = favoriteActors.at(-1);
        const [finalX] = finalActor.get_transformed_position();
        check(finalX >= dockX && finalX + finalActor.width <= dockX + dock.width + 1,
            'last favorite is reachable in actual horizontal viewport');
        dock.hadjustment.value = 0;
    }
    await capture('dock-22');
    const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.NONE});
    launcher.setenv('WAYLAND_DISPLAY', 'wayland-shell100', true);
    const child = launcher.spawnv(['/usr/bin/python3', '/var/tmp/luma-shell100-native/window-fixture.py']);
    let window;
    for (let retry = 0; retry < 30 && !window; retry++) {
        await pause();
        window = global.get_window_actors().map(actor => actor.meta_window)
            .find(candidate => candidate.get_title() === 'Creator native pin fixture');
    }
    check(!!window, 'real GTK Wayland window maps');
    try {
        window.maximize(Meta.MaximizeFlags.BOTH); await pause();
        const frame = window.get_frame_rect(), buffer = window.get_buffer_rect();
        print(`ACTUAL maximized frame ${[frame.x,frame.y,frame.width,frame.height].join(',')} buffer ${[buffer.x,buffer.y,buffer.width,buffer.height].join(',')}`);
        check(frame.x - monitor.x >= 14 && frame.y - monitor.y >= 14 &&
            monitor.x + monitor.width - frame.x - frame.width >= 14,
            'real maximized window keeps native left/top/right work-area padding');
        // The frame stays inside the work area; the actual shadow buffer must
        // extend past every frame edge. The display can clip a long shadow at
        // its physical edge, but clipping to the frame creates corner shards.
        check(buffer.x < frame.x && buffer.y < frame.y &&
            buffer.x + buffer.width > frame.x + frame.width &&
            buffer.y + buffer.height > frame.y + frame.height,
            'actual maximized LumaUI shadow buffer extends every frame edge');
        await capture('maximized');
        window.make_fullscreen(); await pause();
        const full = window.get_frame_rect();
        const fullBuffer = window.get_buffer_rect();
        check(full.x === monitor.x && full.y === monitor.y && full.width === monitor.width && full.height === monitor.height,
            'true fullscreen retains entire monitor');
        check(fullBuffer.x === full.x && fullBuffer.y === full.y &&
            fullBuffer.width === full.width && fullBuffer.height === full.height,
            'true fullscreen retains zero shadow extents');
        window.unmake_fullscreen();
    } finally { child.force_exit(); }

    const iconRow = new St.BoxLayout({style: 'padding: 16px; spacing: 12px; background-color: #f1f3f5;',
        x: 30, y: 140});
    Main.uiGroup.add_child(iconRow);
    const theme = new St.IconTheme();
    for (const id of ['org.gnome.Nautilus.desktop','org.projectluma.Disks.Preview.desktop','viola-browser.desktop','org.projectluma.Camera.desktop']) {
        const app = Shell.AppSystem.get_default().lookup_app(id);
        check(!!app, `native drawer identity exists: ${id}`);
        const gicon = app.get_app_info().get_icon();
        const info = theme.lookup_by_gicon(gicon, 64, St.IconLookupFlags.FORCE_SIZE);
        print(`ACTUAL drawer ${id} gicon=${gicon.to_string()} file=${info?.get_filename()}`);
        const item = new AppDisplay.AppIcon(app);
        item.icon.setIconSize(64);
        iconRow.add_child(item);
        await pause();
        check(item.icon.icon.width === 64 && item.icon.icon.height === 64,
            `actual native drawer canvas is square64: ${id}`);
    }
    await capture('drawer-icons');
    iconRow.destroy();

    // Exact canonical glyph names/styles used by Poster and Quick Options.
    // This renders battery state artwork; it does not invent hardware telemetry.
    const glyphs = new St.BoxLayout({style: 'padding:16px; spacing:20px; background-color:#f1f3f5; color:#30353c;', x:40, y:80});
    Main.uiGroup.add_child(glyphs);
    for (const name of ['lumaui-corner-down-left-symbolic','lumaui-cog-symbolic','network-wireless-symbolic','battery-full-symbolic','system-shutdown-symbolic']) {
        const glyph = new St.Icon({icon_name:name, icon_size:24});
        glyphs.add_child(glyph); await pause();
        check(glyph.width === 24 && glyph.height === 24, `native glyph has matching24px optical canvas: ${name}`);
    }
    await capture('glyph-family'); glyphs.destroy();

    const menu = qs.menu;
    const toggle = new QuickSettings.QuickMenuToggle({title: 'Wi-Fi fixture', checked: true,
        icon_name: 'network-wireless-symbolic'});
    toggle.menu.setHeader('network-wireless-symbolic', 'Wi-Fi fixture');
    toggle.menu._studioRadio = toggle;
    menu.addItem(toggle);
    const radioRow = menu._mainPage.get_children().find(actor =>
        actor.has_style_class_name('luma-options-radios'));
    toggle.get_parent().remove_child(toggle);
    radioRow.add_child(toggle);
    toggle._menuButton.child.child.icon_name = 'lumaui-chevron-right-symbolic';
    toggle._menuButton.child.child.icon_size = 18;
    toggle.add_style_class_name('luma-options-radio');
    const rows = [];
    for (let index = 0; index < 30; index++) {
        const row = new QuickSettings.QuickSheetRow();
        row.setTitle(`Network fixture ${index + 1}`); row.setState('Secure');
        toggle.menu.addMenuItem(row); rows.push(row);
    }
    menu.open(); await pause();
    const pointerNode = menu._boxPointer.get_theme_node();
    print(`ACTUAL popup outer stroke ${pointerNode.get_length('-arrow-border-width')} inner border ${menu.box.get_theme_node().get_border_width(St.Side.TOP)}`);
    check(pointerNode.get_length('-arrow-border-width') === 0,
        'native Quick Options frame has no duplicate stock BoxPointer stroke');
    const descendants = actor => [actor, ...actor.get_children().flatMap(descendants)];
    const keys = descendants(menu._mainPage).filter(actor =>
        actor instanceof St.Widget && actor.has_style_class_name('luma-options-level-key'));
    check(keys.length >= 2, 'actual output/Auto controls retain shared native material identity');
    // The VM has no ambient-light or physical output device. Map the real
    // provider-owned controls for this paint fixture without faking telemetry.
    for (const key of keys) {
        for (let actor = key; actor && actor !== menu._mainPage; actor = actor.get_parent())
            actor.show();
    }
    await pause();
    for (const key of keys) {
        check(key.mapped, 'material fixture measures a mapped real level control');
        const shadow = key.get_theme_node().get_box_shadow();
        print(`ACTUAL level key ${key.toString()} mapped=${key.mapped} checked=${key.checked} shadow=${shadow ? [shadow.yoffset,shadow.blur,shadow.inset].join(',') : 'none'}`);
        check(!!shadow && !shadow.inset && shadow.yoffset > 0,
            'actual level key has a raised native shadow');
    }
    check(toggle.mapped, 'material fixture measures the actual mapped radio card');
    const radioShadow = toggle.get_theme_node().get_box_shadow();
    check(!!radioShadow && !radioShadow.inset && radioShadow.yoffset > 0,
        'actual radio card uses the same raised native shadow');
    await capture('quick-main');
    for (const treatment of ['light','dark','frost','glass']) {
        check(appearance.set_string('surface-treatment', treatment), `${treatment}: actual appearance setting accepted`); await pause();
        check(menu._boxPointer.get_theme_node().get_length('-arrow-border-width') === 0,
            `${treatment}: material remains the sole popup frame`);
        for (const control of [...keys, toggle]) {
            const raised = control.get_theme_node().get_box_shadow();
            check(!!raised && !raised.inset && raised.yoffset > 0,
                `${treatment}: actual mapped Studio control remains raised`);
            control.add_style_pseudo_class('active'); await pause();
            const pressed = control.get_theme_node().get_box_shadow();
            check(!!pressed && pressed.inset,
                `${treatment}: native pressed material remains inset`);
            control.remove_style_pseudo_class('active');
        }
        await pause(); await capture(`quick-${treatment}`);
    }
    appearance.set_string('surface-treatment', 'light'); await pause();
    toggle.menu.open(); await pause();
    check(!menu._pageScroll.overlay_scrollbars, 'native scrollbar owns a separate gutter');
    const adjustment = menu._pageScroll.vadjustment;
    check(adjustment.upper > adjustment.page_size, '30 native network rows overflow');
    check(adjustment.page_size > 100 && adjustment.page_size <= 420,
        'network viewport has a usable bounded actual height');
    const scrollbar = menu._pageScroll.get_children().find(child =>
        child instanceof St.ScrollBar && child.orientation === Clutter.Orientation.VERTICAL);
    check(scrollbar.mapped, 'overflow scrollbar remains usable');
    const [barX] = scrollbar.get_transformed_position();
    const [rowsX] = menu._pages.get_transformed_position();
    check(rowsX + menu._pages.width <= barX, 'cards never overlap the scrollbar');
    rows.at(-1).grab_key_focus(); await pause();
    check(adjustment.value > 0, 'keyboard can reach the last network');
    adjustment.value = 0;
    const [x,y] = menu._pageScroll.get_transformed_position();
    const pointer = global.stage.context.get_backend().get_default_seat()
        .create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    pointer.notify_absolute_motion(GLib.get_monotonic_time(), x + 40, y + 80);
    await pause();
    pointer.notify_scroll_continuous(GLib.get_monotonic_time(), 0, 60, Clutter.ScrollSource.FINGER, 0);
    await pause();
    pointer.notify_scroll_continuous(GLib.get_monotonic_time(), 0, 0,
        Clutter.ScrollSource.FINGER, Clutter.ScrollFinishFlags.VERTICAL);
    check(adjustment.value > 0, 'real smooth finger input scrolls the list');
    await capture('network');
    toggle.menu.close(); menu.close(); toggle.destroy();

    const connection = NM.SimpleConnection.new();
    connection.add_setting(new NM.SettingConnection({id: 'Native auth fixture',
        uuid: GLib.uuid_string_random(), type: '802-11-wireless'}));
    connection.add_setting(new NM.SettingWireless({ssid: GLib.Bytes.new(new TextEncoder().encode('Native fixture'))}));
    connection.add_setting(new NM.SettingWirelessSecurity({key_mgmt: 'wpa-psk'}));
    let submitted = 0;
    const responses = [];
    const component = Object.create(NetworkAgent.Component.prototype);
    component._dialogs = {};
    component._native = {set_password() { submitted++; },
        respond(request, response) { responses.push([request,response]); }};
    for (const treatment of ['light','dark','frost','glass']) {
        appearance.set_string('surface-treatment', treatment); await pause();
        component._handleRequest(treatment, connection, '802-11-wireless-security', [], 0);
        const dialog = component._dialogs[treatment]; await pause();
        const surface = dialog.dialogLayout._dialog;
        check(surface.has_style_class_name('luma-system-prompt'), `${treatment}: shared native prompt`);
        const colour = surface.get_theme_node().get_background_color();
        print(`ACTUAL prompt ${treatment} background ${`RGBA ${colour.get_red()},${colour.get_green()},${colour.get_blue()},${colour.get_alpha()}`}`);
        check(colour.get_alpha() === 1, `${treatment}: opaque authentication surface`);
        const secret = dialog._content.secrets[0];
        secret.entry.set_text('short'); await pause();
        check(!dialog._okButton.button.reactive, `${treatment}: invalid WPA input rejected`);
        secret.entry.set_text('synthetic-fixture-only'); await pause();
        check(dialog._okButton.button.reactive, `${treatment}: valid fixture enables Connect`);
        a11y.set_boolean('high-contrast', true); await pause();
        check(surface.has_style_class_name('luma-prompt-high-contrast'), `${treatment}: native high contrast`);
        a11y.set_boolean('high-contrast', false); await pause();
        await capture(`prompt-${treatment}`);
        dialog.cancel(); await pause();
        check(responses.at(-1)[1] === Shell.NetworkAgentResponse.USER_CANCELED,
            `${treatment}: cancellation keeps authentication boundary`);
    }
    check(submitted === 0, 'cancelled fixtures submit no password');
    print(`NATIVE SHELL FOLLOWUP PASS ${assertions} assertions width=${global.stage.width}`);
}
