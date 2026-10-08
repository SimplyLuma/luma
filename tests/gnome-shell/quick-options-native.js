// SPDX-License-Identifier: GPL-2.0-or-later
// Native compositor/actor test using Shell's supported --automation-script.
// Network rows and the secret recipient are explicit test fixtures. This is
// not evidence that real nearby hardware or an AP accepted a connection.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import NM from 'gi://NM';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as NetworkAgent from 'resource:///org/gnome/shell/ui/components/networkAgent.js';
import * as NotificationLip from 'resource:///org/gnome/shell/ui/lumaNotificationBeacon.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

export const METRICS = {};
function check(value, message) {
    if (!value) throw new Error(message);
    print(`PASS native: ${message}`);
}
async function pause() { await Scripting.sleep(300); }
export function init() { print('NATIVE INIT LOADED'); }

export async function run() {
    const iface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
    const appearance = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});
    const a11y = new Gio.Settings({schema_id: 'org.gnome.desktop.a11y.interface'});
    appearance.reset('surface-treatment');
    iface.set_boolean('enable-animations', false);
    Main.overview.hide();
    await pause();

    const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE});
    launcher.setenv('PYTHONPATH', '/var/tmp/luma-native-memory-20261005/payload/usr/lib/python3.14/site-packages', true);
    const process = launcher.spawnv(['/usr/bin/python3', '/var/tmp/luma-native-memory-20261005/native-vitals-notification-20261005.py']);
    const output = await new Promise((resolve, reject) => process.communicate_utf8_async(null, null, (proc, result) => {
        try { resolve(proc.communicate_utf8_finish(result)); } catch (error) { reject(error); }
    }));
    print(`VITALS NATIVE DELIVERY ${output[1]} ${output[2]}`);
    check(process.get_successful(), 'actual packaged Vitals receives desktop notification acknowledgement');
    await pause();
    const record = Main.messageTray.getSources().flatMap(source => source.notifications)
        .find(notification => notification.title.includes('closed because memory ran out'));
    check(!!record, 'the real native notification store receives the verified OOM notice');
    const lip = NotificationLip.peekNotificationLip();
    check(lip?._current === record && lip.mapped && lip._card?.mapped,
        'the actual Luma notification owner maps the verified OOM card');
    check(lip._card.width > 100 && lip._card.height > 30,
        'the native OOM card has a usable allocation');
    record.destroy();
    await pause();

    const menu = Main.panel.statusArea.quickSettings.menu;
    check(!!menu._mainPage, 'the real native Quick Options owner has its Studio layout');
    const toggle = new QuickSettings.QuickMenuToggle({title: 'Wi-Fi', checked: true,
        icon_name: 'network-wireless-symbolic'});
    toggle.menu.setHeader('network-wireless-symbolic', 'Wi-Fi fixture');
    toggle.menu._studioRadio = toggle;
    menu.addItem(toggle);
    const rows = [];
    for (let index = 0; index < 30; index++) {
        const row = new QuickSettings.QuickSheetRow();
        row.setTitle(`Network fixture ${index + 1}`); row.setState('Secure');
        toggle.menu.addMenuItem(row); rows.push(row);
    }
    menu.open();
    check(menu.isOpen, 'the enclosing native popup actually opens');
    toggle.menu.open();
    await pause();
    const adjustment = menu._pageScroll.vadjustment;
    check(rows.every(row => row.visible && row.mapped), 'all 30 detail rows are present');
    check(adjustment.upper > adjustment.page_size, 'native detail allocation actually overflows');
    print(`VIEWPORT ${adjustment.page_size} upper ${adjustment.upper} height ${menu._pageScroll.height} style ${menu._pageScroll.get_style()} menu ${menu.actor.height}`);
    check(adjustment.page_size > 100 && adjustment.page_size < 800,
        'viewport has a bounded usable native allocation');
    const last = rows.at(-1);
    last.grab_key_focus(); await pause();
    check(adjustment.value > 0, 'keyboard focus brings the last network into view');
    adjustment.value = 0;
    const [x, y] = menu._pageScroll.get_transformed_position();
    print(`SCROLL GEOMETRY ${x},${y} ${menu._pageScroll.width}x${menu._pageScroll.height} adjustment ${adjustment.upper}/${adjustment.page_size} reactive ${menu._pageScroll.reactive}`);
    const events = global.stage.connect('captured-event', (_stage, event) => { if (event.type() === Clutter.EventType.SCROLL) print(`SCROLL EVENT ${event.get_scroll_direction()} at ${event.get_coords()} pick ${global.stage.get_actor_at_pos(Clutter.PickMode.REACTIVE, ...event.get_coords())}`); return Clutter.EVENT_PROPAGATE; });
    menu._pageScroll.connect('scroll-event', () => { print('SCROLL BUBBLE'); return Clutter.EVENT_PROPAGATE; });
    const pointer = global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    pointer.notify_absolute_motion(GLib.get_monotonic_time(), x + menu._pageScroll.width / 2, y + Math.min(100, menu._pageScroll.height / 2));
    await pause();
    pointer.notify_discrete_scroll(GLib.get_monotonic_time(), Clutter.ScrollDirection.DOWN, Clutter.ScrollSource.WHEEL);
    await pause();
    global.stage.disconnect(events);
    check(adjustment.value > 0, 'real native wheel input scrolls the detail viewport');
    adjustment.value = 0;
    pointer.notify_scroll_continuous(GLib.get_monotonic_time(), 0, 60, Clutter.ScrollSource.FINGER, 0);
    await pause();
    pointer.notify_scroll_continuous(GLib.get_monotonic_time(), 0, 0, Clutter.ScrollSource.FINGER, Clutter.ScrollFinishFlags.VERTICAL);
    check(adjustment.value > 0, 'real native smooth finger input scrolls the detail viewport');
    toggle.menu.close(); menu.close(); toggle.destroy();
    await pause();

    const connection = NM.SimpleConnection.new();
    connection.add_setting(new NM.SettingConnection({id: 'Preview auth fixture',
        uuid: GLib.uuid_string_random(), type: '802-11-wireless'}));
    connection.add_setting(new NM.SettingWireless({ssid: GLib.Bytes.new(new TextEncoder().encode('Preview fixture'))}));
    connection.add_setting(new NM.SettingWirelessSecurity({key_mgmt: 'wpa-psk'}));
    const responses = [];
    let submitted = 0;
    const component = Object.create(NetworkAgent.Component.prototype);
    component._dialogs = {};
    component._native = {
        set_password() { submitted++; },
        respond(request, response) { responses.push([request, response]); },
    };
    for (const scheme of ['prefer-light', 'prefer-dark']) {
        iface.set_string('color-scheme', scheme); await pause();
        component._handleRequest(scheme, connection, '802-11-wireless-security', [], 0);
        const dialog = component._dialogs[scheme]; await pause();
        const surface = dialog.dialogLayout._dialog;
        check(surface.has_style_class_name('luma-system-prompt'), `${scheme}: shared prompt owns presentation`);
        const expected = scheme === 'prefer-dark' ? 'luma-surface-dark' : 'luma-surface-light';
        check(surface.has_style_class_name(expected), `${scheme}: actual appearance binding follows mode`);
        const secret = dialog._content.secrets[0];
        check(secret.entry instanceof St.PasswordEntry, `${scheme}: password stays in native masked entry`);
        secret.entry.set_text('short'); await pause();
        check(!dialog._okButton.button.reactive, `${scheme}: invalid WPA secret cannot activate Connect`);
        secret.entry.set_text('preview-fixture-only'); await pause();
        check(dialog._okButton.button.reactive, `${scheme}: valid WPA fixture enables Connect`);
        const cancel = dialog.buttonLayout.get_children()[0];
        check(cancel.can_focus && dialog._okButton.button.can_focus, `${scheme}: both actions remain keyboard accessible`);
        const entryBox = secret.entry.get_allocation_box();
        check(entryBox.get_width() > 150 && entryBox.get_height() >= 36,
            `${scheme}: native password field has usable allocation`);
        a11y.set_boolean('high-contrast', true); await pause();
        check(surface.has_style_class_name('luma-prompt-high-contrast'), `${scheme}: high contrast follows native setting`);
        a11y.set_boolean('high-contrast', false); await pause();
        if (scheme === 'prefer-light') {
            dialog._onOk(); await pause();
            check(responses.at(-1)[1] === Shell.NetworkAgentResponse.CONFIRMED,
                'native NetworkSecretDialog confirms the fixture recipient');
        } else {
            dialog.cancel(); await pause();
            check(responses.at(-1)[1] === Shell.NetworkAgentResponse.USER_CANCELED,
                'native NetworkSecretDialog preserves cancellation');
        }
    }
    check(submitted === 1, 'cancellation submits no password');
    print('NATIVE QUICK OPTIONS AND PROMPT CHECKS COMPLETE');
}
export function finish() {}
