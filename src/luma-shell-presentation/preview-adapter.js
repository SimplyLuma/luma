// SPDX-License-Identifier: Apache-2.0
// Development adapter for an installed Shell predating setPresentationMode.
// Production uses the native Shelf method from the integration patch.
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Presentation} from './presentation.js';
import {installNotificationPulseGuard} from './notificationPulse.js';
import Gio from 'gi://Gio';
import St from 'gi://St';
import * as LumaGrid from 'resource:///org/gnome/shell/ui/lumaGrid.js';

export function start(directory, options = {}) {
    // Compatibility with the installed Shell predating the native network
    // constructor fix: an uninitialized radio must not paint an empty spinner.
    // The native provider takes over visibility as soon as its client is ready.
    const wireless = Main.panel.statusArea.quickSettings._network?._wirelessToggle;
    if (wireless && !wireless._client && !wireless.title) wireless.hide();
    const restorePulse = installNotificationPulseGuard();
    const cameraIndicator = Main.panel.statusArea.quickSettings._camera?._indicator;
    const cameraIcon = cameraIndicator?.icon_name;
    if (cameraIndicator) {
        cameraIndicator.icon_name = 'lumaui-camera-active-symbolic';
        cameraIndicator.add_style_class_name('luma-camera-privacy-indicator');
    }
    const restoreCameraIcon = () => {
        if (cameraIndicator) {
            cameraIndicator.icon_name = cameraIcon;
            cameraIndicator.remove_style_class_name('luma-camera-privacy-indicator');
        }
    };
    const shelf = Main.shelf;
    let original;
    let restorePanel;
    if (shelf && !shelf.setPresentationMode) {
        original = shelf._isDesktopSession;
        restorePanel = shelf._restorePanelActors;
        shelf._restorePanelActors = function () {
            if ((this._lumaPresentationMode ?? 'desktop') === 'desktop' ||
                Main.sessionMode.isLocked || Main.sessionMode.isGreeter)
                restorePanel.call(this);
        };
        shelf._isDesktopSession = function () {
            return (this._lumaPresentationMode ?? 'desktop') === 'desktop' && original.call(this);
        };
        shelf.setPresentationMode = function (mode) {
            if ((this._lumaPresentationMode ?? 'desktop') === 'desktop' && mode !== 'desktop')
                restorePanel.call(this);
            this._lumaPresentationMode = mode;
            this._sync();
        };
    }
    const theme = St.ThemeContext.get_for_stage(global.stage).get_theme();
    const stylesheet = Gio.File.new_for_path(`${directory}/presentation.css`);
    theme.load_stylesheet(stylesheet);
    let presentation;
    try {
        presentation = new Presentation(options);
    } catch (error) {
        restorePulse();
        restoreCameraIcon();
        if (original) {
            shelf._isDesktopSession = original;
            shelf._restorePanelActors = restorePanel;
            delete shelf.setPresentationMode;
        }
        theme.unload_stylesheet(stylesheet);
        throw error;
    }
    // Older installed Grid has no registration hook. Only development discovery
    // differs; cards use the same TabletTasks drag implementation as production.
    const gridAdded = !LumaGrid.setWindowDragHandler
        ? Main.uiGroup.connect('child-added', (_group, actor) => {
            if (actor.constructor.name === 'GridPopup') presentation._gridDrag.attach(actor);
        }) : 0;
    return {
        presentation,
        stop() {
            if (gridAdded) Main.uiGroup.disconnect(gridAdded);
            presentation.destroy();
            restorePulse();
            restoreCameraIcon();
            if (original) {
                shelf._isDesktopSession = original;
                shelf._restorePanelActors = restorePanel;
                delete shelf.setPresentationMode;
                delete shelf._lumaPresentationMode;
            }
            theme.unload_stylesheet(stylesheet);
        },
    };
}
