// SPDX-License-Identifier: Apache-2.0
// One native Grid interaction; each presentation supplies its placement policy.
import Clutter from 'gi://Clutter';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as LumaGrid from 'resource:///org/gnome/shell/ui/lumaGrid.js';
import {ExtensionState} from 'resource:///org/gnome/shell/misc/extensionUtils.js';

export class SwitcherDrag {
    constructor(home) {
        this.home = home;
        this.handler = popup => this.attach(popup);
        LumaGrid.setWindowDragHandler?.(this.handler);
    }
    attach(popup) {
        for (const card of popup._cards) this.attachDismiss(card, popup);
        if (this.home.mode !== 'desktop') {
            this.home._tasks.attachSwitcher(popup);
            return;
        }
        if (this.home.mode !== 'desktop') return;
        const extension = Main.extensionManager.lookup('tilingshell@ferrarodomenico.com');
        const tiler = extension?.state === ExtensionState.ACTIVE ? extension.stateObj : null;
        if (!tiler?.beginWindowPreviewDrag) return;
        this.desktopPopup = popup;
        popup.connect('destroy', () => {
            if (this.desktopPopup === popup) this.desktopPopup = null;
        });
        for (const card of popup._cards) {
            const window = card.window;
            if (!window.allows_move() || !window.allows_resize()) continue;
            let session, actor, opacity;
            this.home._tasks.attachDrag(card, null, window, {
                base: null,
                begin: () => {
                    session = tiler.beginWindowPreviewDrag(window);
                    popup.opacity = 0;
                    Main.layoutManager.setWindowsHiddenForGrid(false);
                    actor = window.get_compositor_private();
                    if (actor) { opacity = actor.opacity; actor.opacity = 0; }
                    const monitors = Main.layoutManager.monitors;
                    const x = Math.min(...monitors.map(m => m.x));
                    const y = Math.min(...monitors.map(m => m.y));
                    const right = Math.max(...monitors.map(m => m.x+m.width));
                    const bottom = Math.max(...monitors.map(m => m.y+m.height));
                    const tasks = this.home._tasks;
                    tasks._lane.hide();
                    tasks._dragSurface.set_position(x, y);
                    tasks._dragSurface.set_size(right-x, bottom-y);
                    Main.uiGroup.set_child_above_sibling(tasks._dragSurface, null);
                    tasks._dragSurface.show();
                },
                motion: (x, y) => session?.update(x, y),
                drop: (x, y) => {
                    session?.update(x, y);
                    return Boolean(session?.drop());
                },
                end: success => {
                    session?.cancel(); session = null;
                    if (actor && window.get_compositor_private() === actor) actor.opacity = opacity;
                    if (popup._destroyed) return;
                    if (success || this.home.mode !== 'desktop') popup.destroy();
                    else {
                        popup.opacity = 255;
                        Main.layoutManager.setWindowsHiddenForGrid(true);
                        popup.grab_key_focus();
                    }
                },
            });
        }
    }
    attachDismiss(card, popup) {
        const pan = new Clutter.PanGesture({min_n_points: 1, max_n_points: 1,
            pan_axis: Clutter.PanAxis.Y});
        pan.set_begin_threshold(12);
        let origin = 0, opacity = 255, distance = 0;
        const restore = () => {
            if (popup._destroyed || !card.get_parent()) return;
            card.ease({translation_y: origin, opacity, duration: St.Settings.get().enable_animations ? 160 : 0,
                mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        };
        pan.connect('may-recognize', () => !popup.travelling && !popup.leaving &&
            !this.home._tasks._activeDrag && card.window.can_close());
        pan.connect('recognize', () => {
            card.remove_all_transitions();
            origin = card.translation_y; opacity = card.opacity; distance = 0;
        });
        pan.connect('pan-update', gesture => {
            distance = Math.min(0, gesture.get_accumulated_delta().get_y());
            card.translation_y = origin + distance;
            card.opacity = Math.max(80, Math.round(opacity * (1 + distance / 500)));
        });
        pan.connect('cancel', restore);
        pan.connect('end', gesture => {
            const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
            if (distance < -100 * scale || distance < -40 * scale && gesture.get_velocity().get_y() < -600) {
                // The normal close request preserves each application's
                // unsaved-document confirmation. It never kills its process.
                card.closeWindow();
            }
            restore();
        });
        card.add_action_full('LumaGridDismissCard', Clutter.EventPhase.CAPTURE, pan);
    }
    setMode() {
        if (this.home.mode !== 'desktop') this.desktopPopup?.destroy();
    }
    destroy() {
        this.desktopPopup?.destroy();
        LumaGrid.setWindowDragHandler?.(null);
    }
}
