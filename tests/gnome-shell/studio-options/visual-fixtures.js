// SPDX-License-Identifier: GPL-2.0-or-later
// Disposable visual fixtures, loaded ONLY by the private headless harness.
// Hardware services are absent there. Sample rows are not functional evidence.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';
import * as Quick from 'resource:///org/gnome/shell/ui/quickSettings.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {getNotificationTray} from 'resource:///org/gnome/shell/ui/lumaNotificationBeacon.js';
export function reset() {
    delete getNotificationTray()._engaged;
    Main.panel.statusArea.quickSettings.menu.close(); getNotificationTray().close();
    Main.messageTray.getSources().forEach(s => s.destroy()); Main.overview.hide();
}
export function notify(kind = 'reply', quiet = false) {
    const app = kind === 'actions' ? 'Depot' : kind === 'call' ? 'Phone' : 'Messages';
    const appId = kind === 'actions' ? 'app-store' : kind === 'call' ? 'phone' : 'messages';
    const source = new MessageTray.Source({title: app, icon: new Gio.FileIcon({file: Gio.File.new_for_path(`/var/home/nick/Documents/LumaDesign/studio/icons/luma-v3-${appId}.svg`)})});
    Main.messageTray.add(source);
    const n = new MessageTray.Notification({source, title: kind === 'actions' ? 'Updates are ready' : 'Priya Raman',
        body: kind === 'actions' ? 'Viola and 3 other apps can update tonight.' : kind === 'call' ? 'Incoming voice call' : 'Pushed the new shelf build. Want to try it before dinner?',
        urgency: kind === 'call' ? MessageTray.Urgency.CRITICAL : MessageTray.Urgency.NORMAL,
        gicon: kind === 'actions' ? null : new Gio.FileIcon({file: Gio.File.new_for_path('/var/home/nick/Documents/.luma-dev/notifications-quick-options/fixtures/priya.png')})});
    if (kind === 'reply') n.inlineReply = {send: async () => ['fixture-only', 'sent']};
    if (kind === 'call' || kind === 'actions') for (const [label, id] of (kind === 'call' ? [['Accept', 'accept'], ['Decline', 'decline']] : [['Update', 'update'], ['Later', 'later']])) {
        const action = n.addAction(label, () => {}); action.id = id;
    }
    if (quiet) n.acknowledged = true;
    source.addNotification(n);
    if (quiet) n.lifecycleState = MessageTray.NotificationLifecycleState.WAITING;
    return n;
}
export function pill(big = false) {
    notify('reply', true); notify('actions', true); notify('call', true);
    const lip = getNotificationTray();
    lip._engaged = () => big; lip._render();
}
export function tray(empty = false) { if (!empty) pill(); getNotificationTray().open(); }
function sampleMenu(menu, rows) {
    menu.box.get_children().forEach(c => c.hide());
    const box = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, style_class: 'luma-options-fixture-groups'});
    menu.box.add_child(box);
    let section;
    for (const row of rows) {
        if (!row) { section = null; continue; }
        const [title, state, icon, current] = row;
        if (title === '#link') { menu.addSettingsAction(state, icon); continue; }
        if (title === '#nearby') {
            section = null;
            const heading = new PopupMenu.PopupBaseMenuItem({reactive: false, style_class: 'luma-options-nearby'});
            heading.add_child(new St.Label({text: 'Nearby', x_expand: true}));
            heading.add_child(new St.Label({text: state ?? 'Looking', style_class: 'luma-options-fixture-looking'}));
            box.add_child(heading); continue;
        }
        if (title === '#empty') {
            const empty = new PopupMenu.PopupMenuItem(state, {reactive: false, style_class: 'luma-options-off'});
            empty.label.clutter_text.line_wrap = true; box.add_child(empty); continue;
        }
        if (!section) {
            section = new PopupMenu.PopupMenuSection(); section.actor.add_style_class_name('luma-options-list');
            box.add_child(section.actor);
        }
        if (title === '#switch') {
            const item = new PopupMenu.PopupSwitchMenuItem(state, false);
            Quick.decorateStudioSwitchRow(item, icon, current ?? ''); section.addMenuItem(item); continue;
        }
        const item = new Quick.QuickSheetRow(); item.setTitle(title); item.setState(state); item.setIcon(icon); item.setCurrent(!!current);
        section.addMenuItem(item);
    }
    return box;
}
let configured = false;
export function controls() {
    const q = Main.panel.statusArea.quickSettings, menu = q.menu;
    if (!configured) {
        const radios = menu._mainPage.get_children().find(c => c.has_style_class_name('luma-options-radios'));
        if (!q._network?._wirelessToggle) {
            const wifi = new Quick.QuickMenuToggle({title: 'Wi-Fi', subtitle: 'Studio North', iconName: 'luma-wifi-symbolic', toggleMode: true});
            menu.addItem(wifi); wifi.get_parent().remove_child(wifi); radios.insert_child_at_index(wifi, 0);
            wifi.add_style_class_name('luma-options-radio');wifi.x_expand = true;wifi.checked = true;
            wifi.menu._studioTitle = 'Wi-Fi';wifi.menu._studioRadio = wifi;
            wifi._menuButton.child.child.icon_name = 'luma-chevron-right-symbolic';
            q._fixtureWifi = wifi;
        } else q._fixtureWifi = q._network._wirelessToggle;
        q._fixtureWifi.title = 'Wi-Fi'; q._fixtureWifi.visible = true; q._fixtureWifi.checked = true; q._fixtureWifi.subtitle = 'Studio North'; q._fixtureWifi.iconName = 'luma-wifi-symbolic';
        const bt = q._bluetooth.quickSettingsItems[0]; bt.visible = true; bt.checked = true; bt.subtitle = '2 devices'; bt.iconName = 'luma-bluetooth-symbolic';
        for (const i of [q._nightLight, q._powerProfiles]) i.quickSettingsItems[0].visible = true;
        q._nightLight.quickSettingsItems[0].checked = true;
        q._powerProfiles.quickSettingsItems[0].iconName = 'luma-leaf-symbolic';
        q._powerProfiles.quickSettingsItems[0].checked = false;
        q._darkMode.quickSettingsItems[0].checked = !menu.box.has_style_class_name('luma-surface-light');
        const output = q._volumeOutput.quickSettingsItems[0], brightness = q._brightness.quickSettingsItems[0];
        output.visible = true; output.menuEnabled = true; output._menuButton.child.child.icon_name = 'luma-headphones-symbolic'; output.iconName = 'luma-volume-2-symbolic'; output.slider.value = .62; output._valueLabel.text = '62%';
        brightness._menuSlot.child.checked = true;
        brightness.visible = true; brightness.slider.value = .72; brightness._valueLabel.text = '72%';
        const footer = q._system.quickSettingsItems[0];
        footer._identitySlot.child.visible = true;
        footer._identitySlot.child.child.get_last_child().clutter_text.set_markup('<span weight="650">74%</span><span alpha="40632"> · 5 h 40 min left</span>');
        footer._shutdownItem._items.push({visible: true});
        footer._shutdownItem.visible = true;
        const lock = new St.Button({can_focus: true, style_class: 'luma-quick-action', child: new St.Icon({icon_name: 'luma-lock-symbolic', icon_size: 16})});
        footer._actions.insert_child_at_index(lock, 1);
        q._fixtureWifi.menu._fixtureRows = sampleMenu(q._fixtureWifi.menu, [
            ['Studio North', 'Connected', 'luma-wifi-symbolic', true], ["Nick’s Phone", 'Hotspot', 'luma-wifi-symbolic'], ['Studio Guest', 'Secured', 'luma-wifi-symbolic'], null,
            ['#switch', 'Airplane mode', 'lumaui-plane-symbolic'], ['#switch', 'Hotspot', 'lumaui-hotspot-symbolic', 'Share this connection'],
            ['#link', 'Wi-Fi settings', 'gnome-wifi-panel.desktop']]);
        bt.menu._fixtureRows = sampleMenu(bt.menu, [['Fable Buds', 'Playing sound · 82%', 'luma-headphones-symbolic'],
            ['Studio Keys', 'Connected · 64%', 'lumaui-keyboard-symbolic'], ['Arc Mouse', 'Not connected', 'lumaui-mouse-symbolic'],
            ['#nearby', 'Looking'], ['Nova Keyboard', 'Tap to pair', 'lumaui-keyboard-symbolic'], ['#link', 'Bluetooth settings', 'gnome-bluetooth-panel.desktop']]);
        output.menu._fixtureRows = sampleMenu(output.menu, [['Fable Buds', 'Bluetooth', 'luma-headphones-symbolic', true], ['Speakers', 'Built in', 'luma-speaker-symbolic'], ['Studio Display', 'HDMI', 'luma-monitor-symbolic'], ['#link', 'Sound settings', 'gnome-sound-panel.desktop']]);
        footer.menu._fixtureRows = sampleMenu(footer.menu, [['Sleep', '', 'lumaui-moon-symbolic'], ['Restart…', '', 'lumaui-rotate-cw-symbolic'], ['Power off…', '', 'lumaui-power-symbolic'], ['Sign out…', '', 'lumaui-log-out-symbolic'], ['#link', 'Power settings', 'gnome-power-panel.desktop']]);
        configured = true;
    }
    menu.open();
}
export function page(name) {
    controls(); const q = Main.panel.statusArea.quickSettings;
    ({wifi: q._fixtureWifi, bluetooth: q._bluetooth.quickSettingsItems[0], output: q._volumeOutput.quickSettingsItems[0], power: q._system.quickSettingsItems[0]})[name].menu.open();
}
export function surface(quick = false) {
    const actor = quick ? Main.panel.statusArea.quickSettings.menu.box : getNotificationTray();
    const [x, y] = actor.get_transformed_position(), [w, h] = actor.get_transformed_size();
    return {x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h)};
}
export function diagnose(quick = false) {
    const root = quick ? Main.panel.statusArea.quickSettings.menu.box : getNotificationTray();
    function tree(a) {
        const node = a.get_theme_node?.();
        if (a.constructor.name === 'Slider' && (a.width <= 0 || a.width > root.width))
            throw Error('native slider allocation is empty or exceeds its surface');
        return {type: a.constructor.name, cls: a.style_class, visible: a.visible,
            allocation: [a.x, a.y, a.width, a.height], preferred: a.get_preferred_height(a.width),
            font: node?.get_font?.()?.to_string(), text: a.text,
            children: a.get_children().filter(c => c.visible).map(tree)};
    }
    log(`LUMA_LAYOUT ${JSON.stringify(tree(root))}`);
}
export function inventoryRecords() {
    const source = new MessageTray.Source({title: 'Calendar', icon: new Gio.FileIcon({file: Gio.File.new_for_path('/var/home/nick/Documents/LumaDesign/studio/icons/luma-v3-calendar.svg')})});
    Main.messageTray.add(source);
    const calendar = new MessageTray.Notification({source, title: 'Launch walkthrough', body: 'Today at 7:30 PM · Studio'});
    calendar.addAction('Snooze', () => {}); calendar.acknowledged = true;
    source.addNotification(calendar); calendar.lifecycleState = MessageTray.NotificationLifecycleState.WAITING;
    notify('actions', true);
}
export function waiting(big = false) {
    inventoryRecords(); const lip = getNotificationTray(); lip._engaged = () => big;
    // Allow the real source-change idle to collect all new records first.
}
export function collection(empty = false) {
    if (!empty) inventoryRecords(); getNotificationTray().open();
}
export function wireNotification() {
    const matches = Main.messageTray.getSources().flatMap(s => s.notifications).filter(n => n.title === 'Wire replacement');
    if (matches.length !== 1) throw Error(`replacement must leave one record, got ${matches.length}`);
    const n = matches[0];
    if (n.body !== 'Fixture body' || n.actions.length !== 2) throw Error('public body/actions did not survive replacement');
    return n;
}
export function wireActionNotification(kind) {
    const title = kind === 'activation' ? 'Wire activation' : 'Wire action';
    const matches = Main.messageTray.getSources().flatMap(s => s.notifications).filter(n => n.title === title);
    if (matches.length !== 1) throw Error(`wire action record missing or duplicated: ${title}`);
    return matches[0];
}
export async function wireReplyNotReady() {
    const n = wireNotification();
    if (!n.inlineReply) throw Error('real forwarded Messages reply capability missing');
    const card = getNotificationTray()._card;
    if (card?.notification !== n || !card._replyEntry) throw Error('real native reply editor missing');
    card._replyEntry.set_text('Fixture reply'); card._replyEntry.grab_key_focus();
    await card._submitInlineReply();
    const request = card._replyRequestId;
    if (!request || card._replyEntry?.get_text() !== 'Fixture reply' ||
        !card._replyEntry.reactive || card._replyButton?.label !== 'Try again')
        throw Error('pre-dispatch failure did not preserve an editable retry');
    global.wireRetryId = request;
}
export async function wireReply() {
    const n = wireNotification(), card = getNotificationTray()._card;
    const request = global.wireRetryId;
    if (!request || card?.notification !== n || card._replyRequestId !== request)
        throw Error('native retry editor or request ID changed');
    await card._submitInlineReply();
    if (card._replyRequestId !== request || card._replyEntry !== null ||
        card._inlineReplyRow.get_first_child()?.text !== 'Sent')
        throw Error('acknowledged retry did not retain its ID and show Sent');
}
export function wireDismissed() {
    if (Main.messageTray.getSources().flatMap(s => s.notifications).some(n => n.title === 'Wire replacement'))
        throw Error('acknowledged reply did not dismiss after the Sent interval');
}

function gtkWireSource() {
    const sources = Main.messageTray.getSources().filter(s => s._appId === 'org.projectluma.ShellWireGTK');
    if (sources.length !== 1) throw Error(`GTK disposable source missing or duplicated: ${sources.length}`);
    return sources[0];
}
export function gtkWireNotification(id) {
    const matches = gtkWireSource().notifications.filter(n => n.id === id);
    if (matches.length !== 1) throw Error(`GTK record missing or duplicated: ${id}`);
    return matches[0];
}
export function gtkWireReplacement() {
    const source = gtkWireSource(), n = gtkWireNotification('replacement');
    if (source.notifications.length !== 4 || n.title !== 'GTK Replacement' ||
        n.body !== 'Disposable GTK fixture body' || source.notifications.some(n => n.title === 'GTK Before'))
        throw Error('GNotification replacement did not preserve one current record per ID');
}
export async function gtkWireReply() {
    const n = gtkWireNotification('reply');
    if (!n.inlineReply) throw Error('GTK opt-in parameterized reply capability missing');
    const lip = getNotificationTray();
    lip.receive(n);
    const card = lip._card;
    if (card?.notification !== n || !card._replyEntry) throw Error('GTK native reply editor missing');
    card._replyEntry.set_text('GTK fixture reply');
    card._replyEntry.grab_key_focus();
    await card._submitInlineReply();
    if (card._replyEntry !== null || card._inlineReplyRow.get_first_child()?.text !== 'Sent')
        throw Error('GTK reply action dispatch did not show Sent');
}
export function gtkWireDismissed() {
    if (gtkWireSource().notifications.some(n => n.id === 'reply'))
        throw Error('GTK reply did not dismiss after its Sent interval');
}
