// SPDX-License-Identifier: Apache-2.0
// The existing Desktop Quick Options menu, with a handheld status anchor and
// bounded viewport. Controls, models and detail pages remain shared.
import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import GLib from 'gi://GLib';
import Mtk from 'gi://Mtk';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {statusInsets} from './safeAreas.js';
import {PopupAnimation} from 'resource:///org/gnome/shell/ui/boxpointer.js';

// A scrollable page keeps each native footer/control at its natural height.
const NaturalPage = GObject.registerClass(class NaturalPage extends St.BoxLayout {
    vfunc_get_preferred_height(width) {
        const [, natural] = super.vfunc_get_preferred_height(width);
        return [natural, natural];
    }
});

export class HandheldOptions {
    constructor(home) {
        this.home = home;
        this.menu = Main.panel.statusArea.quickSettings.menu;
        this.enabled = false;
        this._reposition = this.menu._boxPointer._reposition;
        this._updateFlip = this.menu._boxPointer._updateFlip;
        this._pointerOpen = this.menu._boxPointer.open;
        this._pointerClose = this.menu._boxPointer.close;
        this._pointerStyle = this.menu._boxPointer.get_style();
        this._edgeActors = [this.menu._boxPointer._lumaShadow, this.menu._boxPointer._border]
            .filter(Boolean).map(actor => [actor, actor.visible]);
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
            headerStyle: this.menu._lumaHeader?.get_style(),
            detailScrollPolicy: this.menu._pageScroll?.vscrollbar_policy};
        parent.remove_child(main);
        this.viewport = new NaturalPage({orientation: Clutter.Orientation.VERTICAL, x_expand: true});
        this.viewport.add_child(main);
        this.scroll = new St.ScrollView({overlay_scrollbars: true,
            hscrollbar_policy: St.PolicyType.EXTERNAL, vscrollbar_policy: St.PolicyType.EXTERNAL,
            child: this.viewport});
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
        const key = JSON.stringify([monitor.x, monitor.y, monitor.width, monitor.height, scale, this.home._displayGeometry]);
        if (this._resizeKey === key) return;
        // Compact displays use the full available page; larger Tablet displays
        // retain the shared anchored menu. Controls and detail models are shared.
        const compact = monitor.width / scale <= 700;
        this._setFooterPinned(compact);
        for (const [actor, visible] of this._edgeActors) actor.visible = !compact && visible;
        this.menu.box.remove_style_class_name('lumaui-compact-options');
        if (compact) this.menu.box.add_style_class_name('lumaui-compact-options');
        if (this.menu._pageScroll) this.menu._pageScroll.vscrollbar_policy = compact
            ? St.PolicyType.EXTERNAL : this.original.detailScrollPolicy;
        const width = compact ? monitor.width / scale - 32
            : Math.max(160, Math.min(338, monitor.width / scale - 48));
        const height = Math.max(120, monitor.height / scale - 32);
        this.menu.box.set_style([this.original.style,
            `width: ${width}px; min-width: 0; max-width: ${width}px;`,
            compact ? `height: ${height}px; padding: 16px; border-width: 0; margin: 0; border-radius: 0; box-shadow: none;` : null].filter(Boolean).join('; '));
        this.scroll.y_expand = true;
        this.scroll.set_style(`max-height: ${compact ? height : Math.max(120, monitor.height / scale - 112)}px;`);
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
            pointer.clip_to_allocation = true;
            // Keep controls, text and material at their final allocation.
            // Revealing a clip avoids restyling/reflowing the whole menu at
            // every intermediate width, especially costly on handheld GPUs.
            const reveal = (opening, animation, complete) => {
                this._cancelRevealStart();
                this._clearRevealSnapshot();
                this._reveal?.stop(); this._reveal = null;
                pointer.remove_all_transitions();
                pointer.set_size(monitor.width, monitor.height);
                pointer.set_scale(1, 1);
                pointer.translation_x = pointer.translation_y = 0;
                pointer.opacity = 255;
                pointer._muteKeys = !opening;
                pointer._muteInput = true;
                const initialWidth = Math.min(96 * scale, monitor.width);
                const initialHeight = Math.min(44 * scale, monitor.height);
                const paint = progress => {
                    const amount = opening ? progress : 1 - progress;
                    const width = initialWidth + (monitor.width - initialWidth) * amount;
                    const height = initialHeight + (monitor.height - initialHeight) * amount;
                    (this._revealSnapshot ?? pointer).set_clip(monitor.width - width, 0, width, height);
                };
                const finish = () => {
                    this._clearRevealSnapshot();
                    if (opening) {pointer.remove_clip(); pointer._muteInput = false;}
                    else {pointer.hide(); pointer.remove_clip();}
                    complete?.();
                };
                paint(0); pointer.show();
                if (!animation || !St.Settings.get().enable_animations) {finish(); return;}
                const timeline = this._reveal = new Clutter.Timeline({actor: pointer,
                    duration: opening ? 220 : 160, progress_mode: Clutter.AnimationMode.EASE_OUT_QUART});
                timeline.connect('new-frame', () => paint(timeline.get_progress()));
                timeline.connect('completed', () => {
                    if (this._reveal === timeline) this._reveal = null;
                    finish();
                });
                // The first paint prepares native textures/materials. Starting
                // time before it finishes can consume the entire animation.
                this._revealStart = global.stage.connect_after('after-paint', () => {
                    this._cancelRevealStart();
                    this._revealIdle = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                        this._revealIdle = 0;
                        if (this._reveal === timeline) {
                            // Freeze the fully laid-out native page once. Moving
                            // a clip across its many live children otherwise
                            // rebuilds their paint pipelines on some GPUs.
                            pointer.remove_clip();
                            try {
                                const rect = new Mtk.Rectangle({x: monitor.x, y: monitor.y,
                                    width: monitor.width, height: monitor.height});
                                const [, , , captureScale] = global.stage.get_capture_final_size(rect);
                                const content = global.stage.paint_to_content(rect, captureScale, null, Clutter.PaintFlag.NO_CURSORS);
                                this._revealSnapshot = new Clutter.Actor({content,
                                    x: pointer.x, y: pointer.y, width: monitor.width,
                                    height: monitor.height, reactive: false});
                                this._revealChildren = pointer.get_children().map(child => [child, child.opacity]);
                                pointer.get_parent().add_child(this._revealSnapshot);
                                for (const [child] of this._revealChildren) child.opacity = 0;
                            } catch (error) {
                                this._clearRevealSnapshot();
                                console.warn(`Unable to cache Quick Options transition: ${error.message}`);
                            }
                            paint(0);
                            // Wait for the cached quad's first GPU paint too;
                            // its setup must not consume the animation clock.
                            this._revealStart = global.stage.connect_after('after-paint', () => {
                                this._cancelRevealStart();
                                if (this._reveal === timeline) timeline.start();
                            });
                            (this._revealSnapshot ?? pointer).queue_redraw();
                        }
                        return GLib.SOURCE_REMOVE;
                    });
                });
            };
            // Gesture and keyboard callers omit the flag; only explicit NONE
            // requests disable the transition (for teardown/presentation changes).
            pointer.open = (animation = PopupAnimation.FULL, complete) => reveal(true, animation, complete);
            pointer.close = (animation = PopupAnimation.FULL, complete) => {
                if (pointer.visible) reveal(false, animation, complete);
            };
        } else {
            this._cancelRevealStart();
            this._clearRevealSnapshot();
            this._reveal?.stop(); this._reveal = null;
            pointer.remove_clip();
            pointer.open = this._pointerOpen; pointer.close = this._pointerClose;
            pointer.set_size(-1, -1); pointer.clip_to_allocation = false;
            pointer._reposition = this._reposition;
            pointer._updateFlip = this._updateFlip;
            pointer.set_style(this._pointerStyle);
        }
        pointer.setPosition(this.home._statusRight, 1);
        pointer.queue_relayout();
        this._resizeKey = key;
    }

    open() { this.setEnabled(this.home.mode !== 'desktop'); if (this.enabled) this.menu.open(); }
    close() { this.menu.close(); }
    _setFooterPinned(pinned) {
        const footer = this.menu._surfaceLayout?._footer;
        if (!footer || pinned === Boolean(this._footerOriginal)) return;
        if (pinned) {
            const parent = footer.get_parent();
            this._footerOriginal = {parent, index: parent.get_children().indexOf(footer), visible: footer.visible};
            parent.remove_child(footer);
            this.menu.box.add_child(footer);
            this._footerBinding = this.menu._mainPage.bind_property('visible', footer, 'visible', GObject.BindingFlags.SYNC_CREATE);
        } else {
            this._footerBinding.unbind(); this._footerBinding = null;
            footer.get_parent()?.remove_child(footer);
            this._footerOriginal.parent.insert_child_at_index(footer, this._footerOriginal.index);
            footer.visible = this._footerOriginal.visible;
            this._footerOriginal = null;
        }
    }
    _clearRevealSnapshot() {
        this._revealSnapshot?.destroy(); this._revealSnapshot = null;
        for (const [child, opacity] of this._revealChildren ?? []) child.opacity = opacity;
        this._revealChildren = null;
    }
    _cancelRevealStart() {
        if (this._revealStart) global.stage.disconnect(this._revealStart);
        if (this._revealIdle) GLib.source_remove(this._revealIdle);
        this._revealStart = this._revealIdle = 0;
    }
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
        this._cancelRevealStart();
        this._clearRevealSnapshot();
        this._reveal?.stop(); this._reveal = null;
        this.menu._boxPointer.remove_clip();
        // Returning from a detail page starts the native main-page fade. A
        // presentation change can unmap it midway through that animation.
        // Finish that page transition before returning it to its native parent.
        const main = this.menu._mainPage;
        this._finishMainPageTransition();
        this._setFooterPinned(false);
        this.pageBinding.unbind();
        this.scroll.set_child(null);
        this.viewport.remove_child(main);
        this.viewport.destroy();
        this.original.parent.insert_child_at_index(main, this.original.index);
        this.scroll.destroy();
        this.menu.actor.disconnect(this._backId);
        this.menu._boxPointer.remove_action(this._dismissGesture);
        this.menu.box.set_style(this.original.style);
        for (const [actor, visible] of this._edgeActors) actor.visible = visible;
        this.menu._lumaHeader?.set_style(this.original.headerStyle);
        this.menu.box.remove_style_class_name('lumaui-touch-options');
        this.menu.box.remove_style_class_name('lumaui-compact-options');
        if (this.menu._pageScroll) this.menu._pageScroll.vscrollbar_policy = this.original.detailScrollPolicy;
        this.menu._boxPointer.setPosition(this.menu.sourceActor, .5);
        this.menu._boxPointer.open = this._pointerOpen;
        this.menu._boxPointer.close = this._pointerClose;
        this.menu._boxPointer.set_size(-1, -1);
        this.menu._boxPointer.clip_to_allocation = false;
        this.menu._boxPointer._reposition = this._reposition;
        this.menu._boxPointer._updateFlip = this._updateFlip;
        this.menu._boxPointer.set_style(this._pointerStyle);
        this.enabled = false;
        this._resizeKey = null;
    }
    destroy() {
        this._restore();
        this.menu.disconnect(this.openId);
        if (this.menu._showStudioPage === this._showStudioPageOverride)
            this.menu._showStudioPage = this._showStudioPage;
    }
}
