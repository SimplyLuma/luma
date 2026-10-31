// SPDX-License-Identifier: Apache-2.0
// The existing Desktop Quick Options menu, with a handheld status anchor and
// bounded viewport. Controls, models and detail pages remain shared.
import Clutter from 'gi://Clutter';
import Cogl from 'gi://Cogl';
import Shell from 'gi://Shell';
import GObject from 'gi://GObject';
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {panelProgress, panelSnap} from './layout.js';
import {statusInsets} from './safeAreas.js';
import {smoothOffscreenSampling} from 'resource:///org/gnome/shell/ui/iconMask.js';
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
            materialEnabled: this.menu._materialBinding?._enabled ?? true,
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
            adjustment.value = Math.clamp(start - gesture.get_accumulated_delta_abs().get_y(),
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
            const startY = event.get_coords()[1] - gesture.get_accumulated_delta_abs().get_y();
            return startY >= monitor.y + monitor.height - 64 * (this.home._scale ?? 1);
        });
        this._dismissGesture.connect('recognize', () => {if (this.home.mode === 'mobile') this.beginPull(true);});
        this._dismissGesture.connect('pan-update', gesture => {
            const delta = gesture.get_accumulated_delta_abs().get_y();
            if (this.home.mode === 'mobile') this.updatePull(delta);
            else if (delta < -24 * (this.home._scale ?? 1)) this.close();
        });
        this._dismissGesture.connect('end', () => this.endPull());
        this._dismissGesture.connect('cancel', () => this.endPull(true));
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
        const key = JSON.stringify([monitor.x, monitor.y, monitor.width, monitor.height, scale, this.home.mode, this.home._displayGeometry]);
        if (this._resizeKey === key) return;
        // Compact displays use the full available page; larger Tablet displays
        // retain the shared anchored menu. Controls and detail models are shared.
        const compact = monitor.width / scale <= 700;
        this._setFooterPinned(compact);
        const mobile = compact && this.home.mode === 'mobile';
        if (mobile) this.menu.box.add_style_class_name('luma-mobile-options');
        else this.menu.box.remove_style_class_name('luma-mobile-options');
        this.menu._materialBinding?.setEnabled(mobile ? false : this.original.materialEnabled);
        this.syncAccessibility();
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
            compact ? `height: ${height}px; padding: ${mobile ? 15 : 16}px; border-width: ${mobile ? 1 : 0}px; margin: 0; border-radius: 0; box-shadow: none;` : null].filter(Boolean).join('; '));
        this.scroll.y_expand = true;
        this.scroll.set_style(`max-height: ${compact ? height : Math.max(120, monitor.height / scale - 112)}px;`);
        if (compact && this.menu._lumaHeader) {
            const safe = statusInsets(this.home._displayGeometry, monitor, 44 * scale, 16 * scale, 8 * scale);
            this.menu._lumaHeader.set_style(`padding: 0 ${Math.max(0, safe.right / scale - (mobile ? 15 : 16))}px 0 ${Math.max(0, safe.left / scale - (mobile ? 15 : 16))}px;`);
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
                const progress = this._panelReveal && !this._panelReveal.cancelled
                    ? this._panelReveal.progress : opening ? 0 : 1;
                this._panelReveal?.cancel(); this._panelReveal = null;
                pointer.remove_all_transitions();
                pointer.set_size(monitor.width, monitor.height);
                pointer.set_scale(1, 1);
                pointer.translation_x = pointer.translation_y = 0;
                pointer.opacity = 255;
                pointer._muteKeys = !opening;
                pointer._muteInput = !this._pullRequested;
                const finish = target => {
                    this._panelReveal = null;
                    if (target) {pointer.show(); pointer._muteInput = false;}
                    else pointer.hide();
                    complete?.();
                    if (!target && this.menu.isOpen) this.menu.close(PopupAnimation.NONE);
                };
                pointer.show();
                if (!animation) {finish(opening ? 1 : 0); return;}
                this._panelReveal = new PanelReveal(pointer, {
                    width: monitor.width, height: monitor.height,
                    initialWidth: Math.min(96 * scale, monitor.width),
                    initialHeight: Math.min(44 * scale, monitor.height), right: true,
                    contents: this.menu.box.get_children(), done: finish});
                this._panelReveal.begin(progress, this._pullRequested ? null : opening ? 1 : 0);
                this._pullRequested = false;
            };
            // Gesture and keyboard callers omit the flag; only explicit NONE
            // requests disable the transition (for teardown/presentation changes).
            pointer.open = (animation = PopupAnimation.FULL, complete) => reveal(true, animation, complete);
            pointer.close = (animation = PopupAnimation.FULL, complete) => {
                if (pointer.visible) reveal(false, animation, complete);
            };
        } else {
            this._panelReveal?.cancel(); this._panelReveal = null;
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
    syncAccessibility() {
        const highContrast = St.Settings.get().high_contrast;
        const opaque = highContrast || this.home._settings.get_boolean('reduce-transparency');
        for (const [name, active] of [['high-contrast', highContrast], ['opaque', opaque]]) {
            if (active) this.menu.box.add_style_class_name(name);
            else this.menu.box.remove_style_class_name(name);
        }
    }
    beginPull(closing = false) {
        if (this.home.mode !== 'mobile' || !this.enabled || !this.menu._boxPointer.clip_to_allocation) {this.open(); return;}
        this._pullRequested = true;
        if (!this.menu.isOpen) this.menu.open();
        else this.menu._boxPointer.open(PopupAnimation.FULL);
        if (closing && this._panelReveal) {
            this._panelReveal.start = 1;
            this._panelReveal.seek(1);
        }
    }
    updatePull(delta) {this._panelReveal?.update(delta);}
    endPull(cancel = false) {this._panelReveal?.release(cancel);}
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
        this._panelReveal?.cancel(); this._panelReveal = null;
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
        this.menu.box.remove_style_class_name('luma-mobile-options');
        this.menu._materialBinding?.setEnabled(this.original.materialEnabled);
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

// Keep the native page at its final allocation. Only this GPU mask changes;
// there is no stage capture/readback or exposed full-page preparation frame.
const PanelMask = GObject.registerClass(class PanelMask extends Shell.GLSLEffect {
    _init() {super._init(); this.bounds = [0, 0, 1, 1]; this.radius = 0;}
    vfunc_build_pipeline() {
        this.add_glsl_snippet(Cogl.SnippetHook.FRAGMENT,
            `uniform vec2 panel_extent;
             uniform vec2 panel_origin;
             uniform vec4 panel_bounds;
             uniform float panel_radius;`,
            `vec2 point = cogl_tex_coord_in[0].st * panel_extent + panel_origin - panel_bounds.xy;
             vec2 half_size = panel_bounds.zw * 0.5;
             float radius = min(panel_radius, min(half_size.x, half_size.y));
             vec2 q = abs(point - half_size) - (half_size - vec2(radius));
             float distance = length(max(q, vec2(0.0))) + min(max(q.x, q.y), 0.0) - radius;
             cogl_color_out *= 1.0 - smoothstep(-0.5, 0.5, distance);`, false);
    }
    vfunc_paint_target(node, context) {
        const actor = this.actor, texture = this.get_texture();
        if (actor && texture) {
            const scale = Math.max(1, Math.ceil(actor.get_resource_scale()));
            const volume = actor.get_paint_volume(), origin = volume?.get_origin() ?? {x: 0, y: 0};
            const enlarge = (start, size) => Math.trunc(Math.ceil(start + size + .75) - Math.round(size) - 3);
            const set = (name, values) => this.set_uniform_float(this.get_uniform_location(name), values.length, values);
            set('panel_extent', [texture.get_width() / scale, texture.get_height() / scale]);
            set('panel_origin', [enlarge(origin.x, volume?.get_width() ?? actor.width),
                enlarge(origin.y, volume?.get_height() ?? actor.height)]);
            set('panel_bounds', this.bounds); set('panel_radius', [this.radius]);
        }
        smoothOffscreenSampling(this);
        super.vfunc_paint_target(node, context);
    }
});

export class PanelReveal {
    constructor(actor, {width, height, initialWidth, initialHeight, right = false, contents = [], done}) {
        Object.assign(this, {actor, width, height, initialWidth, initialHeight, right, contents, done});
        this.progress = 0;
        this.ready = false;
        this.cancelled = false;
    }
    begin(start, target = null) {
        this.start = this.progress = start;
        this.target = target;
        this._lastTime = GLib.get_monotonic_time(); this._lastDelta = this._velocity = 0;
        this._contentState = this.contents.map(child =>
            [child, child.opacity, child.translation_x, child.get_offscreen_redirect()]);
        this.mask = new PanelMask();
        this.actor.add_effect_with_name('luma-panel-reveal', this.mask);
        // First map can upload icons/text and allocate the native controls.
        // Warm their cached layers before starting the opening clock, otherwise
        // that first expensive paint consumes most of the animation's duration.
        this._warming = start === 0 && St.Settings.get().enable_animations;
        for (const [child] of this._contentState)
            child.set_offscreen_redirect(Clutter.OffscreenRedirect.ALWAYS);
        this.ready = !this._warming;
        this.seek(start);
        if (this._warming) {
            this._warmPaint = global.stage.connect_after('after-paint', () => {
                global.stage.disconnect(this._warmPaint); this._warmPaint = 0;
                this._warmIdle = GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
                    this._warmIdle = 0;
                    if (this.cancelled) return GLib.SOURCE_REMOVE;
                    this._warming = false; this.ready = true;
                    this.seek(this.progress);
                    if (this.target !== null) this.settle(this.target);
                    return GLib.SOURCE_REMOVE;
                });
            });
        } else if (target !== null) this.settle(target);
    }
    seek(progress) {
        if (this.cancelled) return;
        this.progress = Math.max(0, Math.min(1, progress));
        if (this._warming) {
            // Populate the offscreen texture without flashing the full page.
            // Finger updates are retained and applied after this first paint.
            this.mask.bounds = [0, 0, 0, 0]; this.mask.radius = 0;
            this.mask.queue_repaint();
            return;
        }
        const w = this.initialWidth + (this.width - this.initialWidth) * this.progress;
        const h = this.initialHeight + (this.height - this.initialHeight) * this.progress;
        const left = this.right ? this.width - w : (this.width - w) / 2;
        const fade = Math.max(0, Math.min(1, (this.progress - .18) / .45));
        for (const [child, opacity, translation] of this._contentState ?? []) {
            // Text enters from the growing surface's leading edge, rather
            // than exposing the middle of a fixed full-width heading.
            child.translation_x = translation + left;
            child.opacity = Math.round(opacity * fade * fade * (3 - 2 * fade));
        }
        if (this.mask) {
            this.mask.bounds = [left, 0, w, h];
            const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
            this.mask.radius = Math.min(24 * scale, h / 2) * Math.min(1, (1 - this.progress) / .08);
            this.mask.queue_repaint();
        }
    }
    update(delta) {
        if (this.cancelled) return;
        this.timeline?.stop(); this.timeline = null; this.target = null;
        const now = GLib.get_monotonic_time();
        const elapsed = (now - this._lastTime) / 1000000;
        if (elapsed > 0) this._velocity = (delta - this._lastDelta) / elapsed;
        this._lastTime = now; this._lastDelta = delta;
        this.seek(panelProgress(this.start, delta, this.height - this.initialHeight));
    }
    release(cancel = false) {
        const velocity = (GLib.get_monotonic_time() - this._lastTime) < 120000 ? this._velocity : 0;
        this.settle(cancel ? this.start : panelSnap(this.progress, velocity));
    }
    settle(target) {
        if (this.cancelled) return;
        this.target = target;
        if (!this.ready) return;
        this.timeline?.stop(); this.timeline = null;
        const from = this.progress;
        const finish = () => {
            this.seek(target);
            const done = this.done;
            this.cancel();
            done?.(target);
        };
        if (!St.Settings.get().enable_animations || Math.abs(from - target) < .001) {finish(); return;}
        this.timeline = new Clutter.Timeline({actor: this.actor,
            duration: Math.max(70, 220 * Math.abs(target - from)),
            progress_mode: Clutter.AnimationMode.EASE_OUT_CUBIC});
        this.timeline.connect('new-frame', () => this.seek(from + (target - from) * this.timeline.get_progress()));
        this.timeline.connect('completed', finish);
        this.timeline.start();
    }
    cancel() {
        if (this.cancelled) return;
        this.cancelled = true;
        if (this._warmPaint) global.stage.disconnect(this._warmPaint);
        if (this._warmIdle) GLib.source_remove(this._warmIdle);
        this._warmPaint = this._warmIdle = 0;
        this.timeline?.stop(); this.timeline = null;
        if (this.mask) this.actor.remove_effect(this.mask);
        this.mask = null;
        for (const [child, opacity, translation, redirect] of this._contentState ?? []) {
            child.opacity = opacity; child.translation_x = translation;
            child.set_offscreen_redirect(redirect);
        }
        this._contentState = null;
        this.actor.remove_clip();
    }
}
