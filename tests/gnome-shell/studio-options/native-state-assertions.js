// SPDX-License-Identifier: GPL-2.0-or-later
// Test-only: run after each settled state in the private, stamped Shell.
// These assert actual native actors; visual samples never prove hardware services.
import Atk from 'gi://Atk';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';
import {getNotificationTray} from 'resource:///org/gnome/shell/ui/lumaNotificationBeacon.js';

export const STATES = ['idle', 'pill-small', 'pill-big', 'card-reply', 'card-actions',
    'card-call', 'tray-full', 'tray-empty', 'quick-main', 'quick-wifi',
    'quick-bluetooth', 'quick-output', 'quick-power'];

function require(ok, message) {
    if (!ok) throw new Error(`Native state assertion: ${message}`);
}

function descendants(actor) {
    return [actor, ...actor.get_children().flatMap(descendants)];
}

function extent(actor, label) {
    require(actor.has_allocation(), `${label} allocated`);
    const [width, height] = actor.get_transformed_size();
    require(Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0,
        `${label} finite positive extent`);
    return [width, height];
}

export function assertState(name, viewport) {
    require(STATES.includes(name), 'known required state');
    const saved = Main.shelf._settings.get_value('shelf-arrangement').recursiveUnpack();
    const resolved = Main.shelf._groups.map(g => ({visible: g.visible,
        edge: g.placement?.edge, monitor: g.placement?.monitor,
        islands: g.placement?.islands ?? [], rowIslands: g.islands.map(a => a.islandId)}));
    require(saved.some(g => g.edge === 'top' && g.anchor === 'center' &&
        g.islands.length === 1 && g.islands[0] === 'notifications'),
    'saved exact top-center notifications-only group');
    require(resolved.some(g => g.visible && g.edge === 'top' &&
        g.islands.length === 1 && g.islands[0] === 'notifications'),
    'resolved exact top notifications-only group');
    const monitor = Main.layoutManager.primaryMonitor;
    require(monitor.width === viewport[0] && monitor.height === viewport[1],
        'actual virtual monitor equals requested desktop/phone geometry');
    const quick = name.startsWith('quick-');
    const lip = getNotificationTray();
    const menu = Main.panel.statusArea.quickSettings.menu;
    const actor = quick ? menu.box : lip;
    const [width, height] = extent(actor, name);
    const [x, y] = actor.get_transformed_position();
    require(x >= monitor.x - 1 && y >= monitor.y - 1 &&
        x + width <= monitor.x + monitor.width + 1 &&
        y + height <= monitor.y + monitor.height + 1, 'surface contained in actual monitor');
    require(actor.visible && actor.mapped, 'surface actually visible and mapped');
    const assertions = ['exact-saved-top-center-layout', 'exact-resolved-notifications-only-layout',
        'real-monitor-geometry', 'positive-allocation', 'contained', 'mapped'];
    if (name === 'idle') {
        require(lip._state === 'idle' && !lip._current, 'idle has no active arrival');
        assertions.push('idle-lifecycle');
    } else if (name.startsWith('pill-')) {
        require(lip._state === 'pill' && lip._records.length === 2, 'two waiting records');
        require(lip._records.every(n => n.lifecycleState === MessageTray.NotificationLifecycleState.WAITING),
            'waiting lifecycle is retained');
        assertions.push('waiting-records', 'waiting-lifecycle');
    } else if (name.startsWith('card-')) {
        require(lip._state === 'card' && lip._current && lip._card, 'arrival is a native card');
        require(lip._current.lifecycleState === MessageTray.NotificationLifecycleState.ARRIVING,
            'arrival lifecycle');
        require((name === 'card-call') === (lip._current.urgency === MessageTray.Urgency.CRITICAL),
            'critical call stays distinct from ordinary arrivals');
        if (name === 'card-reply')
            require(descendants(lip._card).some(a => a instanceof St.Entry && a.visible),
                'reply uses a visible native Entry');
        assertions.push('arrival-lifecycle', 'critical-policy', 'native-card');
    } else if (name.startsWith('tray-')) {
        require(lip.isOpen && lip._state === 'tray' && lip._grab, 'actual modal collection');
        const column = lip._surface.child;
        const [columnWidth] = extent(column, 'collection column');
        require(Math.abs(columnWidth - width) <= 1, 'collection fills available surface width');
        const empty = name === 'tray-empty';
        require(lip._empty.visible === empty && lip._scroll.visible === !empty,
            'empty and populated content are mutually exclusive');
        require(lip._records.length === (empty ? 0 : 2), 'actual collection record inventory');
        require(lip._clear.visible === !empty, 'Clear availability follows actual records');
        assertions.push('modal-collection', 'full-width-column', 'empty-record-policy', 'clear-availability');
    } else {
        require(menu.isOpen, 'native Quick Options menu open');
        if (name === 'quick-main') {
            require(menu._mainPage.visible && !menu._pageHost.visible, 'main/detail exclusivity');
            const controls = Main.panel.statusArea.quickSettings;
            for (const indicator of [controls._volumeOutput, controls._brightness]) {
                const slider = indicator.quickSettingsItems[0].slider;
                extent(slider, 'actual native slider');
                require(slider.value >= 0 && slider.value <= 1, 'bounded actual Slider value');
            }
            assertions.push('main-page', 'native-sliders');
        } else {
            require(menu._pageHost.visible && !menu._mainPage.visible, 'detail/main exclusivity');
            const titles = {'quick-wifi': 'Wi-Fi', 'quick-bluetooth': 'Bluetooth',
                'quick-output': 'Sound output', 'quick-power': 'Power'};
            require(menu._pageHeading.text === titles[name], 'actual page title');
            const hasSwitch = ['quick-wifi', 'quick-bluetooth'].includes(name);
            require(menu._pageSwitch.visible === hasSwitch, 'switch availability follows provider');
            if (hasSwitch) {
                const parent = menu._pageSwitch;
                require(parent.child instanceof PopupMenu.Switch, 'actual native Switch child');
                require(parent.child.state === parent.checked, 'checked state reaches native Switch');
                require(parent.accessible_role === Atk.Role.TOGGLE_BUTTON &&
                    parent.accessible_name === titles[name], 'named accessible toggle parent');
                const handles = descendants(parent.child).filter(a =>
                    a instanceof St.Widget && a.has_style_class_name('handle'));
                require(handles.length === 1, 'exactly one native switch thumb');
                extent(handles[0], 'native switch thumb');
            }
            assertions.push('detail-page', 'page-title', 'switch-provider-policy');
            if (hasSwitch) assertions.push('native-switch', 'state-binding', 'toggle-accessibility', 'allocated-thumb');
        }
    }
    const receipt = {name, viewport: [monitor.width, monitor.height], saved, resolved,
        bounds: {x, y, width, height},
        assertions, hardware_services_verified: false, temporal_privacy_verified: false};
    console.log(`LUMA_NATIVE_STATE ${JSON.stringify(receipt)}`);
    return receipt;
}
