// SPDX-License-Identifier: Apache-2.0
// The existing Desktop Quick Options menu, with a handheld status anchor and
// bounded viewport. Controls, appearance, models and detail pages are unchanged.
import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import St from 'gi://St';
import {gettext as _} from 'gettext';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {statusInsets} from './safeAreas.js';
import {PopupAnimation} from 'resource:///org/gnome/shell/ui/boxpointer.js';

export class HandheldOptions {
    constructor(home) {
        this.home = home;
        this.menu = Main.panel.statusArea.quickSettings.menu;
        this.enabled = false;
        this._reposition = this.menu._boxPointer._reposition;
        this._updateFlip = this.menu._boxPointer._updateFlip;
        this._pointerStyle = this.menu._boxPointer.get_style();
        // Closing a native detail page while its popup is unmapped must not
        // leave a fade queued on the next presentation's main page.
        this._showStudioPage = this.menu._showStudioPage;
        this._showStudioPageOverride = (item, open) => {
            this._showStudioPage.call(this.menu, item, open);
            if (!open && !this.menu._activeMenu && !this.menu.isOpen)
                this._finishMainPageTransition();
        };
        this.menu._showStudioPage = this._showStudioPageOverride;
        this.openId = this.menu.connect('open-state-changed', (_menu, open) => {
            this.home._syncGestureVisibility();
            if (!open) return;
            if (!this.menu._activeMenu) this._finishMainPageTransition();
            if (this.enabled) this.resize();
        });
        if (!this.menu._activeMenu) this._finishMainPageTransition();
    }
    get isOpen() { return this.menu.isOpen; }
    setEnabled(enabled) {
        if (enabled === this.enabled) { if (enabled) this.resize(); return; }
        if (!enabled) { this._restore(); return; }
        const main = this.menu._mainPage;
        if (!main) return;
        this.menu.close(PopupAnimation.NONE);
        this._finishMainPageTransition();
        this.enabled = true;
        this.menu.box.add_style_class_name('lumaui-touch-options');
        const parent = main.get_parent();
        this.original = {parent, index: parent.get_children().indexOf(main), style: this.menu.box.get_style(),
            headerStyle: this.menu._lumaHeader?.get_style()};
        parent.remove_child(main);
        this.scroll = new St.ScrollView({overlay_scrollbars: true,
            hscrollbar_policy: St.PolicyType.EXTERNAL, vscrollbar_policy: St.PolicyType.AUTOMATIC,
            child: main});
        parent.insert_child_at_index(this.scroll, this.original.index);
        this.pageBinding = main.bind_property('visible', this.scroll, 'visible', GObject.BindingFlags.SYNC_CREATE);
        const pan = new Clutter.PanGesture({min_n_points: 1, max_n_points: 1, pan_axis: Clutter.PanAxis.Y});
        pan.set_begin_threshold(8);
        let start;
        pan.connect('may-recognize', () => this.scroll.vadjustment.upper > this.scroll.vadjustment.page_size);
        pan.connect('recognize', () => { start = this.scroll.vadjustment.value; });
        pan.connect('pan-update', gesture => {
            const adjustment = this.scroll.vadjustment;
            adjustment.value = Math.clamp(start - gesture.get_accumulated_delta().get_y(),
                adjustment.lower, Math.max(adjustment.lower, adjustment.upper - adjustment.page_size));
        });
        this.scroll.add_action(pan);
        this.closeButton = new St.Button({label: _('Close'), accessible_name: _('Close Quick Options'),
            can_focus: true, reactive: true, x_expand: true,
            style: 'min-height: 44px; padding: 0; border-radius: 16px; background-color: rgba(128,128,128,0.14);'});
        this.closeButton.connect('clicked', () => this.close());
        this.menu.box.add_child(this.closeButton);
        // Native gestures belong to the painted popup, not its zero-sized
        // wrapper. Only a drag beginning on the bottom dismissal area qualifies.
        this._dismissGesture = new Clutter.PanGesture({min_n_points: 1, max_n_points: 1,
            pan_axis: Clutter.PanAxis.Y});
        this._dismissGesture.set_begin_threshold(8);
        this._dismissGesture.connect('may-recognize', gesture => {
            const event = gesture.get_point_event(0);
            if (!event) return false;
            const monitor = Main.layoutManager.primaryMonitor;
            const startY = event.get_coords()[1] - gesture.get_accumulated_delta().get_y();
            return startY >= monitor.y + monitor.height - 64 * (this.home._scale ?? 1);
        });
        this._dismissGesture.connect('pan-update', gesture => {
            if (gesture.get_accumulated_delta().get_y() < -24 * (this.home._scale ?? 1)) this.close();
        });
        this.menu._boxPointer.add_action_full('LumaOptionsDismiss', Clutter.EventPhase.CAPTURE, this._dismissGesture);
        this._backId = this.menu.actor.connect('key-press-event', (_actor, event) => {
            const focus = global.stage.get_key_focus();
            if (focus instanceof St.Entry || focus?.get_parent() instanceof St.Entry) return Clutter.EVENT_PROPAGATE;
            if ([Clutter.KEY_BackSpace, Clutter.KEY_Escape, Clutter.KEY_Back].includes(event.get_key_symbol())) {
                this.close(); return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });
        this.resize();
    }
    resize() {
        if (!this.enabled) return;
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor) return;
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
        // Compact displays use the full available page; larger Tablet displays
        // retain the shared anchored menu. Controls and detail models are shared.
        const compact = monitor.width / scale <= 700;
        const width = compact ? monitor.width / scale - 32
            : Math.max(160, Math.min(338, monitor.width / scale - 48));
        const height = Math.max(120, monitor.height / scale - 32);
        this.menu.box.set_style([this.original.style,
            `width: ${width}px; min-width: 0; max-width: ${width}px;`,
            compact ? `height: ${height}px; padding: 16px; border-width: 0; margin: 0; border-radius: 0;` : null].filter(Boolean).join('; '));
        this.scroll.y_expand = true;
        this.closeButton.visible = compact;
        this.scroll.set_style(`max-height: ${compact ? height - 52 : Math.max(120, monitor.height / scale - 112)}px;`);
        if (compact && this.menu._lumaHeader) {
            const safe = statusInsets(this.home._displayGeometry, monitor, 44 * scale, 16 * scale, 8 * scale);
            this.menu._lumaHeader.set_style(`padding: 0 ${Math.max(0, safe.right / scale - 16)}px 0 ${Math.max(0, safe.left / scale - 16)}px;`);
        } else this.menu._lumaHeader?.set_style(this.original.headerStyle);
        const pointer = this.menu._boxPointer;
        if (compact) {
            pointer.set_style('-arrow-rise: 0; -arrow-base: 0; -arrow-border-width: 0; -boxpointer-gap: 0;');
            pointer._reposition = box => {
                box.x1 = monitor.x; box.y1 = monitor.y;
                box.x2 = monitor.x + monitor.width; box.y2 = monitor.y + monitor.height;
            };
            pointer._updateFlip = () => {};
        } else {
            pointer._reposition = this._reposition;
            pointer._updateFlip = this._updateFlip;
            pointer.set_style(this._pointerStyle);
        }
        pointer.setPosition(this.home._statusRight, 1);
        pointer.queue_relayout();
    }

    open() { this.setEnabled(this.home.mode !== 'desktop'); if (this.enabled) this.menu.open(); }
    close() { this.menu.close(); }
    _finishMainPageTransition() {
        const main = this.menu._mainPage;
        if (!main) return;
        main.remove_all_transitions();
        main.opacity = 255;
        main.translation_x = 0;
        main.show();
    }
    _restore() {
        if (!this.enabled) return;
        this.menu.close(PopupAnimation.NONE);
        // Returning from a detail page starts the native main-page fade. A
        // presentation change can unmap it midway through that animation.
        // Finish that page transition before returning it to its native parent.
        const main = this.menu._mainPage;
        this._finishMainPageTransition();
        this.pageBinding.unbind();
        this.scroll.set_child(null);
        this.original.parent.insert_child_at_index(main, this.original.index);
        this.scroll.destroy();
        this.closeButton.destroy();
        this.menu.actor.disconnect(this._backId);
        this.menu._boxPointer.remove_action(this._dismissGesture);
        this.menu.box.set_style(this.original.style);
        this.menu._lumaHeader?.set_style(this.original.headerStyle);
        this.menu.box.remove_style_class_name('lumaui-touch-options');
        this.menu._boxPointer.setPosition(this.menu.sourceActor, .5);
        this.menu._boxPointer._reposition = this._reposition;
        this.menu._boxPointer._updateFlip = this._updateFlip;
        this.menu._boxPointer.set_style(this._pointerStyle);
        this.enabled = false;
    }
    destroy() {
        this._restore();
        this.menu.disconnect(this.openId);
        if (this.menu._showStudioPage === this._showStudioPageOverride)
            this.menu._showStudioPage = this._showStudioPage;
    }
}
