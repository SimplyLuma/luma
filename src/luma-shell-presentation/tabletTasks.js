// SPDX-License-Identifier: Apache-2.0
// Native two-window Tablet tasks. App content remains in real Mutter windows;
// clones are used only for drag feedback; Desktop Grid owns the switcher.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Mtk from 'gi://Mtk';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as LumaGrid from 'resource:///org/gnome/shell/ui/lumaGrid.js';
import * as DND from 'resource:///org/gnome/shell/ui/dnd.js';
import {PanelReveal} from './handheldOptions.js';
import {gettext as _} from 'gettext';

function control(name, run, icon = null) {
    const button = new St.Button({accessible_name: name, can_focus: true, reactive: true,
        style_class: 'luma-tablet-control'});
    if (icon) button.set_child(new St.Icon({icon_name: icon, icon_size: 20}));
    button.connect('clicked', run);
    return button;
}
const live = window => Boolean(window?.get_compositor_private());

export class TabletTasks {
    constructor(home) {
        this.home = home;
        this.groups = [];
        this._actions = new Map();
        this._placement = new Map();
        this._tracker = Shell.WindowTracker.get_default();
        this._trackerId = this._tracker.connect('tracked-windows-changed', () => {
            if (this.enabled && this.pending)
                for (const window of this.home._windows.keys()) this.manage(window);
        });
        this._windowGroupId = global.window_group.connect('notify::visible', () => {
            if (!global.window_group.visible || !this._returnHomeAfterSwitcher) return;
            this._returnHomeAfterSwitcher = false;
            if (this.navigationEnabled && ![...this.home._windows.keys()].some(window => live(window) && !window.minimized))
                this.home.showHome();
        });
        this.active = null;
        this.pickerSide = null;
        this._chrome = [];
        this._seam = this._add(control(_('Resize app pair'), () => {}, null));
        this._seam.style_class = 'luma-tablet-seam';
        this._seam.set_child(new St.Widget({style_class: 'luma-tablet-seam-grip',
            x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER}));
        this._bars = ['left', 'right'].map(side => {
            const bar = this._add(control(_('Home'), () => this.showPaneHome(side)));
            bar.style_class = 'luma-converged-home-control';
            bar.set_child(new St.Widget({style_class: 'luma-converged-home-pill',
                x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER}));
            this._gestures(bar, () => this.active?.[side], side);
            return bar;
        });
        this._gestures(home._gestureBar, () => this.focused());
        this._dragSurface = this._add(new St.Widget({reactive: true, style_class: 'luma-tablet-drop-surface'}));
        this._lane = new St.Widget({style_class: 'luma-tablet-drop-lane'});
        this._laneLabel = new St.Label({text: _('Open beside'), x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER});
        this._lane.add_child(this._laneLabel);
        this._dragSurface.add_child(this._lane);
        this._dragSurface._delegate = {
            handleDragOver: source => source.tabletTasks === this ? DND.DragMotionResult.MOVE_DROP : DND.DragMotionResult.CONTINUE,
            acceptDrop: (source, _actor, x, y) => {
                if (source.tabletTasks !== this) return false;
                if (source.drop) {
                    const [sx, sy] = this._dragSurface.get_transformed_position();
                    return source.drop(x+sx, y+sy);
                }
                const side = x < this.monitor.width / 2 ? 'left' : 'right';
                return source.window ? this.place(source.window, side, source.base)
                    : this.launchAt(source.info, side, source.base);
            },
        };
        this._monitor = {dragMotion: event => {
            if (event.source?.tabletTasks === this) {
                if (event.source.motion) return event.source.motion(event.x, event.y)
                    ? DND.DragMotionResult.MOVE_DROP : DND.DragMotionResult.NO_DROP;
                else this._showLane(event.x < this.monitor.x + this.monitor.width / 2 ? 'left' : 'right');
            }
            return DND.DragMotionResult.CONTINUE;
        }};
        this._pan(this._seam, Clutter.PanAxis.X, () => {
            this._split = this.monitor.width * (this.active?.ratio ?? .5);
        }, gesture => {
            this._split += gesture.get_delta_abs().get_x();
            this.layout(this._split);
        }, () => {
            const width = this.monitor.width;
            if (this._split < width * .18) this.removeSide('left');
            else if (this._split > width * .82) this.removeSide('right');
            else {
                if (this.active) this.active.ratio = this._layoutSplit / width;
                this.layout();
            }
        });
        this._bindings = [
            ['toggle-tiled-left', () => this.place(this.focused(), 'left')],
            ['toggle-tiled-right', () => this.place(this.focused(), 'right')],
            ['maximize', () => this.fill(this.focused())],
        ];
    }

    get navigationEnabled() { return this.home.mode !== 'desktop' && !Main.sessionMode.isLocked && !Main.sessionMode.isGreeter; }
    get enabled() { return this.home.mode === 'tablet' && !Main.sessionMode.isLocked && !Main.sessionMode.isGreeter; }
    get monitor() { return Main.layoutManager.primaryMonitor; }
    focused() {
        const focused = global.display.focus_window;
        return this.home._windows.has(focused) ? focused : this.home._lastFocusedWindow;
    }
    _add(actor) {
        Main.layoutManager.addChrome(actor, {affectsStruts: false, trackFullscreen: false});
        actor.hide(); this._chrome.push(actor); return actor;
    }
    _pan(actor, axis, begin, update, end) {
        const gesture = new Clutter.PanGesture({min_n_points: 1, max_n_points: 1, pan_axis: axis});
        gesture.set_begin_threshold(4);
        gesture.connect('may-recognize', () => this.enabled);
        gesture.connect('recognize', pan => { this.beginNavigation(actor, pan); begin(pan); });
        gesture.connect('pan-update', update);
        gesture.connect('end', () => { this.endNavigation(); end(); });
        gesture.connect('cancel', () => {
            this.endNavigation();
            if (this._hold) GLib.source_remove(this._hold);
            this._hold = 0; this._held = false;
            this.hideLane(); this.layout();
        });
        actor.add_action_full(`LumaTablet${axis}`, Clutter.EventPhase.CAPTURE, gesture);
        return gesture;
    }
    beginNavigation(actor, gesture) {
        const event = gesture.get_point_event(0);
        // A touchscreen keeps its implicit sequence grab. A mouse can cross
        // from chrome into a Wayland client after its first movement; retain
        // the native modal grab until navigation ends or is cancelled.
        if (event && !event.get_event_sequence() && !this._navigationGrab)
            this._navigationGrab = Main.pushModal(actor, {actionMode: Shell.ActionMode.NORMAL});
    }
    endNavigation() {
        if (this._navigationGrab) Main.popModal(this._navigationGrab);
        this._navigationGrab = null;
    }
    _gestures(bar, window, side = null) {
        this._pan(bar, Clutter.PanAxis.X, () => {
            this._lifted = window(); this._slide = 0;
            const source = this._lifted?.get_compositor_private();
            if (source) {
                this._liftGhost = new Clutter.Clone({source, opacity: 220});
                this._dragSurface.add_child(this._liftGhost);
                this._liftGhost.set_scale(.85, .85);
                this._liftGhost.set_position(source.x - this.monitor.x + source.width * .075,
                    source.y - this.monitor.y + source.height * .075);
                this._liftOriginX = this._liftGhost.x;
            }
        }, gesture => {
            this._slide += gesture.get_delta_abs().get_x();
            if (this._liftGhost) this._liftGhost.x = this._liftOriginX + this._slide;
            if (Math.abs(this._slide) > 24 * this.home._scale)
                this._showLane(this._slide < 0 ? 'left' : 'right');
        }, () => {
            this.hideLane();
            if (Math.abs(this._slide) < 64 * this.home._scale || !live(this._lifted)) return;
            const side = this._slide < 0 ? 'left' : 'right';
            const group = this.groupFor(this._lifted);
            if (group?.left && group?.right) {
                const current = group.left === this._lifted ? 'left' : 'right';
                if (side === current) this.removeSide(current);
                else this.swap(group);
            } else this.place(this._lifted, side, null);
        });
        // The full-width control already has the common Mobile/Home pan.
        if (bar === this.home._gestureBar) return;
        this._pan(bar, Clutter.PanAxis.Y, () => { this._vertical = 0; }, gesture => {
            this._vertical += gesture.get_delta_abs().get_y();
            this.verticalUpdate(this._vertical);
        }, () => this.verticalEnd(this._vertical, side));
    }
    beginLift() {
        if (this._navigationLift || this.home._home.visible) return;
        const actor = this.focused()?.get_compositor_private();
        if (!actor) return;
        const [x, y] = actor.get_transformed_position();
        const [width, height] = actor.get_transformed_size();
        const clone = new Clutter.Clone({source: actor, x, y, width, height, reactive: false});
        clone.set_pivot_point(.5, .5);
        Main.uiGroup.add_child(clone);
        this._navigationLift = clone;
        this._navigationSource = actor;
        actor.hide();
    }
    finishLift(home) {
        const clone = this._navigationLift;
        this._navigationLift = null;
        const actor = this._navigationSource;
        this._navigationSource = null;
        if (actor && live(actor.meta_window) && !actor.meta_window.minimized) actor.show();
        if (!clone) return;
        clone.ease({scale_x: home ? .78 : 1, scale_y: home ? .78 : 1,
            translation_y: home ? -64 * this.home._scale : 0, opacity: home ? 0 : 255,
            duration: St.Settings.get().enable_animations ? 120 : 0,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD, onStopped: () => clone.destroy()});
    }
    verticalUpdate(distance) {
        if (!this.navigationEnabled) return;
        const progress = Math.clamp(-distance / (this.monitor.height * .6), 0, 1);
        if (this._navigationLift) {
            this._navigationLift.set_scale(1 - progress * .18, 1 - progress * .18);
            this._navigationLift.translation_y = -progress * 40 * this.home._scale;
            // Fade follows the finger from the first movement, not only release.
            this._navigationLift.opacity = Math.round(255 * (1 - progress * .9));
        }
        if (distance > -56 * this.home._scale || this._hold || this._held) return;
        this._hold = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 500, () => {
            this._hold = 0; this._held = true; this.openSwitcher(); return GLib.SOURCE_REMOVE;
        });
    }
    verticalEnd(distance, side = null) {
        if (this._hold) GLib.source_remove(this._hold);
        this._hold = 0;
        if (this._held) { this._held = false; return; }
        if (distance < -(this.home.mode === 'mobile' ? 48 : 140) * this.home._scale) {
            if (side) this.showPaneHome(side);
            else this.home.showHome();
        }
        else { this.finishLift(false); if (distance < -24 * this.home._scale) this.peek(); }
    }

    _ink(window) {
        const bus = window?.get_gtk_unique_bus_name();
        const path = window?.get_gtk_window_object_path();
        if (bus && path && !this._actions.has(window)) {
            const actions = Gio.DBusActionGroup.get(Gio.DBus.session, bus, path);
            const signals = ['action-added', 'action-removed', 'action-state-changed'].map(signal =>
                actions.connect(signal, () => this.home._updateStatusStyle()));
            this._actions.set(window, {actions, signals});
        }
        const hint = this._actions.get(window)?.actions.get_action_state('luma-status-ink')?.deep_unpack();
        return ['light', 'dark'].includes(hint) ? hint : this.home._statusInk;
    }
    updateInk() {
        const color = ink => ink === 'light' ? '#ffffff' : '#191b1f';
        const visible = this.enabled && this.active && !this.home._home.visible;
        const left = visible ? this._ink(this.active.left) : this.home._statusInk;
        const right = visible ? this._ink(this.active.right) : this.home._statusInk;
        this.home._clockButton?.set_style(`color: ${color(left)};`);
        this.home._statusRight.set_style(`color: ${color(right)};`);
        this.home._gestureBar.get_child().set_style(`background-color: ${color(this.home._statusInk)};`);
        this._bars.forEach((bar, i) => bar.get_child().set_style(`background-color: ${color(i ? right : left)};`));
    }

    setMode() {
        const enabled = this.enabled;
        if (enabled === this._bound) { this.sync(); return; }
        this._bound = enabled;
        for (const [name, run] of this._bindings) {
            if (enabled) Main.wm.setCustomKeybindingHandler(name, Shell.ActionMode.NORMAL, run);
            else Meta.keybindings_set_custom_handler(name, null);
        }
        if (!enabled) {
            this._switcherPopup?.destroy();
            this.endNavigation();
            this._trackPlacement([]);
            this._returnHomeAfterSwitcher = false;
            this._activeDrag?._cancelDrag(global.get_current_time());
            if (this._hold) GLib.source_remove(this._hold);
            this._hold = 0; this._held = false;
            this.closeOverlay(); this.hideLane(); this.pickerSide = null;
            this._clearPending();
            for (const group of this.groups) {
                for (const window of [group.left, group.right].filter(live)) {
                    if (this.home.mode === 'mobile') window.make_fullscreen();
                }
            }
            if (this.home.mode !== 'tablet') { this.groups = []; this.active = null; }
        }
        this.sync();
    }

    groupFor(window) { return this.groups.find(group => group.left === window || group.right === window); }
    _detach(window) {
        const group = this.groupFor(window);
        if (!group) return;
        if (group.left === window) group.left = null;
        if (group.right === window) group.right = null;
        this.groups = this.groups.filter(item => item.left || item.right);
    }
    _minimumFrameWidth(window) {
        const [known, width, height] = window.get_min_size();
        if (!known) return 0;
        const frame = window.client_rect_to_frame_rect(new Mtk.Rectangle({x: 0, y: 0, width, height}));
        return Math.max(0, frame.width);
    }
    canFit(window) {
        if (!live(window)) return false;
        // Size hints are client bounds; move_resize_frame uses visible bounds.
        // Mutter accounts for both CSD shadows and X11 frame decorations here.
        const client = window.frame_rect_to_client_rect(new Mtk.Rectangle({x: 0, y: 0,
            width: Math.floor(this.monitor.width / 2), height: this.monitor.height}));
        const [hasMinimum, width, height] = window.get_min_size();
        const [hasMaximum, maxWidth, maxHeight] = window.get_max_size();
        if (hasMaximum && (maxWidth > 0 && maxWidth < client.width ||
            maxHeight > 0 && maxHeight < client.height)) return false;
        return !hasMinimum || width <= client.width && height <= client.height;
    }
    place(window, side, base = this.focused()) {
        if (!this.enabled || !live(window)) return false;
        const existing = this.groupFor(window);
        if (existing && (existing.left && existing.right || this.active === existing)) {
            if (existing[side] !== window) this.swap(existing);
            this.activate(existing); return true;
        }
        const otherSide = side === 'left' ? 'right' : 'left';
        const current = this.active;
        let other = current && (current.left === base || current.right === base)
            ? current[otherSide] : base;
        if (other === window) other = null;
        if (!this.canFit(window) || other && !this.canFit(other)) {
            Main.notify(_('Cannot show these apps side by side'), _('An app requires more space than half of this display.'));
            return false;
        }
        this._detach(window);
        if (other) this._detach(other);
        const group = {left: null, right: null}; group[side] = window; group[otherSide] = other;
        this.groups.unshift(group); this.activate(group);
        return true;
    }
    swap(group = this.active) {
        if (!group) return;
        [group.left, group.right] = [group.right, group.left];
        this.active = group; this.layout(); this._showPicker();
    }
    fill(window) {
        if (!this.enabled || !live(window)) return;
        const group = this.groupFor(window);
        for (const other of [group?.left, group?.right].filter(other => other && other !== window)) {
            this.home._homeMinimized.add(other); other.minimize();
        }
        this._detach(window); this.active = null; this.pickerSide = null;
        this._trackPlacement([]);
        this.home._home.hide(); this.closeOverlay(); window.make_fullscreen();
        window.unminimize(); window.activate(global.get_current_time()); this.sync();
    }
    removeSide(side) {
        if (!this.active) return;
        const removed = this.active[side];
        const other = this.active[side === 'left' ? 'right' : 'left'];
        if (removed) { this._detach(removed); this.home._homeMinimized.add(removed); removed.minimize(); }
        if (other) this.fill(other);
        else { this.active = null; this.pickerSide = null; this.home.showHome(); this.sync(); }
    }
    showPaneHome(side) {
        if (!this.enabled || !this.active) { this.home.showHome(); return; }
        const group = this.active;
        const window = group[side];
        if (window) {
            group[side] = null;
            this.home._homeMinimized.add(window);
            window.minimize();
        }
        if (!group.left && !group.right) { this.home.showHome(); return; }
        this.closeOverlay();
        this.layout();
        this._showPicker();
        this.sync();
    }
    activate(group) {
        if (!this.enabled) return;
        this.closeOverlay(); this.active = group;
        for (const window of [group.left, group.right].filter(live)) {
            window.unminimize(); window.raise();
        }
        this.layout();
        (group.left ?? group.right)?.activate(global.get_current_time());
        this._showPicker(); this.sync();
    }
    focus(window) {
        if (!this.enabled || !this.home._windows.has(window)) return;
        const group = this.groupFor(window);
        this.active = group ?? null;
        if (group) {
            for (const member of [group.left, group.right].filter(live)) {
                if (member.minimized) member.unminimize();
                member.raise();
            }
            this.layout(); this._showPicker();
        } else { this.pickerSide = null; this.home._home.hide(); }
        this.sync();
    }
    manage(window) {
        if (!this.enabled) return false;
        if (this.pending && this._appId(window) === this.pending.id) {
            const pending = this.pending;
            if (this.canFit(window) && (!live(pending.base) || this.canFit(pending.base))) {
                this._clearPending();
                this.place(window, pending.side, pending.base);
            } else {
                // A first map can still carry Desktop/CSD size hints while
                // its client adopts the current presentation. Retain intent;
                // retry on native state/geometry changes, never a guessed delay.
                this._watchPending(window);
            }
        }
        if (this.groupFor(window)) { this.layout(); return true; }
        return false;
    }
    _clearPending() {
        for (const [window, ids] of this._pendingSignals ?? []) ids.forEach(id => window.disconnect(id));
        this._pendingSignals = [];
        if (this._pendingTimer) GLib.source_remove(this._pendingTimer);
        this._pendingTimer = 0; this.pending = null;
    }
    _watchPending(window) {
        if (this.pending.window === window) return;
        this.pending.window = window;
        this._pendingSignals = [window, this.pending.base].filter(live).map(member => [member,
            ['size-changed', 'position-changed', 'notify::decorated', 'notify::fullscreen'].map(signal =>
                member.connect(signal, () => { if (this.pending) this.manage(window); }))]);
    }
    unmanaged(window) {
        if (this.pending?.window === window || this.pending?.base === window) this._clearPending();
        if (this._placement.has(window)) {
            this._placement.get(window).forEach(id => window.disconnect(id)); this._placement.delete(window);
        }
        const record = this._actions.get(window);
        if (record) record.signals.forEach(id => record.actions.disconnect(id));
        this._actions.delete(window);
        const group = this.groupFor(window);
        if (!group) return;
        this._detach(window);
        if (this.active === group) {
            const other = group.left ?? group.right;
            if (live(other)) this.fill(other);
            else { this.active = null; this.pickerSide = null; this.sync(); }
        }
    }
    _appId(window) {
        const tracked = this._tracker.get_window_app(window)?.get_id();
        if (tracked && !tracked.startsWith('window:')) return tracked;
        const native = window.get_gtk_application_id();
        return native ? (native.endsWith('.desktop') ? native : `${native}.desktop`) : tracked;
    }
    launchAt(info, side, base = this.focused()) {
        if (!this.enabled) return false;
        const app = this.home._appSystem.lookup_app(info.get_id());
        const existing = app?.get_windows().find(window => this.home._windows.has(window));
        if (existing) return this.place(existing, side, base);
        this._clearPending();
        this.pending = {id: info.get_id(), side, base};
        this._pendingTimer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 15, () => {
            this._pendingTimer = 0;
            const opened = Boolean(this.pending?.window);
            this._clearPending();
            if (opened) Main.notify(_('Cannot show these apps side by side'), _('An app requires more space than half of this display.'));
            else Main.notify(_('Application did not open'), _('Try opening it again.'));
            return GLib.SOURCE_REMOVE;
        });
        try {
            if (app) app.activate(); else info.launch([], global.create_app_launch_context(0, -1));
            return true;
        } catch (error) {
            this._clearPending();
            Main.notifyError(_('Unable to open application'), error.message); return false;
        }
    }
    launchFromHome(info) {
        return this.enabled && this.pickerSide ? this.launchAt(info, this.pickerSide,
            this.active?.left ?? this.active?.right) : false;
    }

    _trackPlacement(windows) {
        for (const [window, ids] of this._placement) {
            if (windows.includes(window)) continue;
            ids.forEach(id => window.disconnect(id)); this._placement.delete(window);
        }
        for (const window of windows) {
            if (this._placement.has(window)) continue;
            const schedule = () => {
                if (this._layoutLater || !this.enabled || !this.active) return;
                this._layoutLater = global.compositor.get_laters().add(Meta.LaterType.BEFORE_REDRAW, () => {
                    this._layoutLater = 0;
                    if (this.enabled && this.active) this.layout(this._layoutSplit);
                    return GLib.SOURCE_REMOVE;
                });
            };
            this._placement.set(window, ['size-changed', 'position-changed', 'notify::fullscreen',
                'notify::maximized-horizontally', 'notify::maximized-vertically'].map(signal => window.connect(signal, schedule)));
        }
        if (!windows.length && this._layoutLater) {
            global.compositor.get_laters().remove(this._layoutLater); this._layoutLater = 0;
        }
    }

    layout(split = this.monitor.width * (this.active?.ratio ?? .5)) {
        if (!this.enabled || !this.active) { this.sync(); return; }
        const m = this.monitor, gap = 0;
        this._trackPlacement([this.active.left, this.active.right].filter(live));
        // Live resize is constrained to feasible client sizes. Closing is decided
        // by the finger's raw edge position, never by violating a client's limits.
        if ([this.active.left, this.active.right].filter(live).some(window => !this.canFit(window))) {
            this.fill(this.active.left ?? this.active.right);
            return;
        }
        const minimum = Math.max(160 * this.home._scale,
            ...[this.active.left, this.active.right].filter(live).map(window => this._minimumFrameWidth(window) + gap / 2));
        const seam = Math.round(Math.clamp(split, minimum, m.width - minimum));
        this._layoutSplit = seam;
        for (const side of ['left', 'right']) {
            const window = this.active[side]; if (!live(window)) continue;
            if (window.is_fullscreen()) window.unmake_fullscreen();
            if (window.get_maximize_flags()) window.unmaximize();
            const x = side === 'left' ? m.x : m.x + seam + gap / 2;
            const width = side === 'left' ? seam - gap / 2 : m.width - seam - gap / 2;
            const rect = window.get_frame_rect();
            if (rect.x !== Math.round(x) || rect.y !== m.y || rect.width !== Math.round(width) || rect.height !== m.height)
                window.move_resize_frame(false, Math.round(x), m.y, Math.round(width), m.height);
        }
        this._seam.set_position(m.x + seam - 22 * this.home._scale, m.y + 44 * this.home._scale);
        this._seam.set_size(44 * this.home._scale, m.height - 72 * this.home._scale);
        this._bars.forEach((bar, i) => {
            bar.set_position(m.x + (i ? seam : 0), m.y + m.height - 28 * this.home._scale);
            bar.set_size(i ? m.width - seam : seam, 28 * this.home._scale);
        });
        this.sync();
    }
    _showPicker() {
        this.pickerSide = this.active && !this.active.left ? 'left'
            : this.active && !this.active.right ? 'right' : null;
        this.home._rebuild();
        this.home._home.visible = Boolean(this.pickerSide);
    }
    homeRect() {
        if (!this.enabled || !this.pickerSide) return null;
        const m = this.monitor, half = Math.round(this._layoutSplit ?? m.width * (this.active?.ratio ?? .5));
        return {x: m.x + (this.pickerSide === 'right' ? half : 0), y: m.y,
            width: this.pickerSide === 'right' ? m.width - half : half, height: m.height};
    }
    beforeHome() { this._switcherPopup?.destroy(); this.pickerSide = null; this.closeOverlay(); }
    sync() {
        const pairVisible = this.enabled && this.active && !this._switcherPopup && !Main.sessionMode.isLocked &&
            (this.pickerSide || !this.home._home.visible);
        this._seam.visible = Boolean(pairVisible);
        this._bars.forEach(bar => { bar.visible = Boolean(pairVisible); });
        if (pairVisible) {
            const m = this.monitor, scale = this.home._scale;
            this.home._gestureBar.set_position(m.x + m.width / 2 - 32 * scale, m.y + m.height - 28 * scale);
            this.home._gestureBar.set_size(64 * scale, 28 * scale);
            Main.uiGroup.set_child_above_sibling(this.home._gestureBar, null);
        } else if (this.enabled) {
            const m = this.monitor;
            this.home._gestureBar.set_position(m.x, m.y + m.height - 28 * this.home._scale);
            this.home._gestureBar.set_size(m.width, 28 * this.home._scale);
        }
        if (this.home._gestureBar) this.home._gestureBar.visible = this.enabled
            ? !Main.sessionMode.isLocked && !this._switcherPopup : this.home.mode === 'mobile' && !Main.sessionMode.isLocked;
        if (!this.enabled) this.closeOverlay();
        this.updateInk();
    }

    _showLane(side) {
        const m = this.monitor;
        this._dragSurface.set_position(m.x, m.y); this._dragSurface.set_size(m.width, m.height);
        this._lane.set_position(side === 'left' ? 12 : m.width / 2 + 4, 48 * this.home._scale);
        this._lane.set_size(m.width / 2 - 16, m.height - 84 * this.home._scale);
        this._laneLabel.set_position(24, 24);
        Main.uiGroup.set_child_above_sibling(this._dragSurface, null);
        this._dragSurface.show();
    }
    hideLane() { this._liftGhost?.destroy(); this._liftGhost = null; this._dragSurface.hide(); }
    attachDrag(actor, info = null, window = null, lifecycle = null) {
        const source = {tabletTasks: this, info, window,
            getDragActor: () => {
                if (info) return new St.Icon({gicon: info.get_icon(), icon_size: 64});
                const [width, height] = actor.get_transformed_size();
                const limit = Math.min(1, 300 / Math.max(1, width, height));
                const clone = new Clutter.Clone({source: actor, width: actor.width, height: actor.height});
                // Grid lays out cards at window size and transforms them into
                // thumbnails. Clone does not inherit that source transform.
                clone.set_scale(width / Math.max(1, actor.width) * limit,
                    height / Math.max(1, actor.height) * limit);
                return clone;
            },
            getDragActorSource: () => actor};
        source.motion = lifecycle?.motion;
        source.drop = lifecycle?.drop;
        actor._delegate = source;
        const draggable = DND.makeDraggable(actor, {manualMode: Boolean(lifecycle),
            timeoutThreshold: 350, dragActorMaxSize: 300});
        const hold = new Clutter.LongPressGesture();
        hold.can_not_cancel(draggable.startGesture);
        draggable.startGesture.can_not_cancel(hold);
        hold.connect('recognize', gesture => draggable.startGesture.start_drag(gesture.get_point_event(0)));
        actor.add_action(hold);
        draggable.connect('drag-begin', () => {
            source.base = lifecycle ? lifecycle.base : this.focused(); this._activeDrag = draggable;
            lifecycle?.begin();
            DND.addDragMonitor(this._monitor);
            if (!source.motion) this._showLane('right');
        });
        draggable.connect('drag-end', (_drag, _time, success) => {
            if (this._destroyed) { lifecycle?.end(false); return; }
            DND.removeDragMonitor(this._monitor); this.hideLane(); this._activeDrag = null;
            const overlay = this._overlay;
            GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                lifecycle?.end(success);
                if (!this._destroyed && this._overlay === overlay) this.closeOverlay();
                return GLib.SOURCE_REMOVE;
            });
        });
    }
    attachSwitcher(popup) {
        if (!this.navigationEnabled || this._switcherPopup === popup) return;
        this._switcherPopup = popup;
        const returnHome = this.home._home.visible || this._returnHomeAfterSwitcher;
        this._returnHomeAfterSwitcher = returnHome;
        this.home._home.hide();
        this.sync();
        const base = returnHome ? null : this.focused();
        popup.connect('destroy', () => {
            if (this._switcherPopup === popup) this._switcherPopup = null;
            GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                if (!this._destroyed && this.navigationEnabled && !this._switcherPopup &&
                    ![...this.home._windows.keys()].some(window => live(window) && !window.minimized))
                    this.home.showHome();
                if (!this._destroyed) this.sync();
                return GLib.SOURCE_REMOVE;
            });
        });
        // The modal picker owns this sequence, including drags beginning
        // in the bottom navigation area; chrome outside its grab cannot.
        const home = new Clutter.PanGesture({min_n_points: 1, max_n_points: 1, pan_axis: Clutter.PanAxis.Y});
        home.set_begin_threshold(8);
        home.connect('may-recognize', gesture => {
            const event = gesture.get_point_event(0);
            if (!event || popup.leaving) return false;
            const delta = gesture.get_accumulated_delta();
            const start = event.get_coords()[1] - delta.get_y();
            return start >= this.monitor.y + this.monitor.height - 28 * this.home._scale &&
                delta.get_y() < 0 && Math.abs(delta.get_y()) > Math.abs(delta.get_x());
        });
        home.connect('end', gesture => {
            if (gesture.get_accumulated_delta().get_y() >= -24 * this.home._scale) return;
            popup.destroy();
            this.home.showHome();
        });
        popup.add_action_full('LumaGridHome', Clutter.EventPhase.CAPTURE, home);
        if (!this.enabled) return; // Split placement belongs to Tablet only.
        for (const card of popup._cards) {
            if (!this.home._windows.has(card.window)) continue;
            this.attachDrag(card, null, card.window, {base,
                begin: () => {
                    this._returnHomeAfterSwitcher = false;
                    popup.opacity = 0;
                    Main.layoutManager.setWindowsHiddenForGrid(false);
                },
                end: success => {
                    if (popup._destroyed) return;
                    if (success || !this.enabled) popup.destroy();
                    else {
                        popup.opacity = 255;
                        this._returnHomeAfterSwitcher = returnHome;
                        Main.layoutManager.setWindowsHiddenForGrid(true);
                        popup.grab_key_focus();
                    }
                },
            });
        }
    }
    peek() {
        if (!this.enabled) return;
        this.closeOverlay();
        this._overlay = this._add(new St.Widget({reactive: false}));
        const m = this.monitor;
        this._overlay.set_position(m.x, m.y); this._overlay.set_size(m.width, m.height);
        this.home._buildDock(m, this._overlay, true);
        this._overlay.show();
        this._dismiss = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 8, () => {
            this._dismiss = 0;
            if (!this._activeDrag) this.closeOverlay();
            return GLib.SOURCE_REMOVE;
        });
    }
    _panel(title) {
        this.closeOverlay();
        const m = this.monitor, scale = this.home._scale;
        const backdrop = this._add(new St.Widget({reactive: true, style_class: 'luma-tablet-task-backdrop'}));
        backdrop.set_position(m.x, m.y); backdrop.set_size(m.width, m.height);
        const panel = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL,
            reactive: true, style_class: 'luma-tablet-task-panel', can_focus: true});
        backdrop.add_child(panel);
        panel.set_position(24 * scale, 52 * scale);
        panel.set_size(m.width - 48 * scale, m.height - 100 * scale);
        const header = new St.BoxLayout({style_class: 'luma-tablet-task-header'});
        header.add_child(new St.Label({text: title, x_expand: true, y_align: Clutter.ActorAlign.CENTER}));
        header.add_child(control(_('Close'), () => this.closeOverlay(), 'window-close-symbolic'));
        panel.add_child(header); this._overlay = backdrop; this._panelActor = panel;
        backdrop.connect('button-press-event', (_actor, event) => {
            if (event.get_source() !== backdrop) return Clutter.EVENT_PROPAGATE;
            this.closeOverlay(); return Clutter.EVENT_STOP;
        });
        backdrop.connect('touch-event', (_actor, event) => {
            if (event.type() !== Clutter.EventType.TOUCH_BEGIN || event.get_source() !== backdrop)
                return Clutter.EVENT_PROPAGATE;
            this.closeOverlay(); return Clutter.EVENT_STOP;
        });
        panel.connect('key-press-event', (_a, event) => {
            if (event.get_key_symbol() !== Clutter.KEY_Escape) return Clutter.EVENT_PROPAGATE;
            this.closeOverlay(); return Clutter.EVENT_STOP;
        });
        backdrop.show();
        this._modalGrab = Main.pushModal(backdrop, {actionMode: Shell.ActionMode.POPUP});
        panel.grab_key_focus(); return panel;
    }
    openLibrary() {
        if (!this.enabled) return;
        const panel = this._panel(_('Applications'));
        const search = new St.Entry({hint_text: _('Search apps'), can_focus: true,
            style_class: 'luma-tablet-app-search', accessible_name: _('Search apps')});
        panel.add_child(search);
        const scroll = new St.ScrollView({x_expand: true, y_expand: true,
            hscrollbar_policy: St.PolicyType.NEVER, vscrollbar_policy: St.PolicyType.AUTOMATIC});
        const list = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL});
        scroll.set_child(list); panel.add_child(scroll);
        const render = () => {
            list.destroy_all_children();
            const query = search.get_text().trim().toLocaleLowerCase();
            const apps = this.home._apps.filter(info => info.get_display_name().toLocaleLowerCase().includes(query));
            const columns = Math.max(1, Math.floor((this.monitor.width / this.home._scale - 80) / 120));
            let row;
            apps.forEach((info, i) => {
                if (i % columns === 0) { row = new St.BoxLayout(); list.add_child(row); }
                const item = this.home._appButton(info);
                item.set_size(120 * this.home._scale, 108 * this.home._scale);
                row.add_child(item); this.attachDrag(item, info);
            });
            if (!apps.length) list.add_child(new St.Label({text: _('No matching apps')}));
        };
        search.clutter_text.connect('text-changed', render); render(); search.grab_key_focus();
    }
    openSwitcher() {
        if (!this.navigationEnabled) return;
        this.finishLift(false);
        this.closeOverlay();
        this._returnHomeAfterSwitcher = this.home._home.visible;
        this.home._home.hide();
        LumaGrid.show();
    }
    closeOverlay() {
        if (this._dismiss) GLib.source_remove(this._dismiss);
        this._dismiss = 0;
        if (!this._overlay || this._activeDrag) return;
        const overlay = this._overlay; this._overlay = null; this._panelActor = null;
        if (this._modalGrab) Main.popModal(this._modalGrab);
        this._modalGrab = null;
        this._chrome = this._chrome.filter(actor => actor !== overlay);
        Main.layoutManager.removeChrome(overlay); overlay.destroy();
    }
    destroy() {
        this.endNavigation();
        this.finishLift(false);
        this._destroyed = true;
        global.window_group.disconnect(this._windowGroupId);
        this._tracker.disconnect(this._trackerId);
        this._activeDrag?._cancelDrag(global.get_current_time());
        this._activeDrag = null;
        this.home.mode = 'desktop'; this.setMode();
        if (this._hold) GLib.source_remove(this._hold);
        DND.removeDragMonitor(this._monitor);
        for (const actor of this._chrome) { Main.layoutManager.removeChrome(actor); actor.destroy(); }
        this._chrome = [];
        for (const {actions, signals} of this._actions.values()) signals.forEach(id => actions.disconnect(id));
        this._actions.clear();
    }
}

// Handheld windows fade at final geometry; Desktop keeps dock travel.
export class HandheldMotion {
    constructor(home) {
        this.home = home;
        this.wm = Main.wm;
        this._open = this.wm._lumaPlayOpen;
        this._target = this.wm._lumaDockTarget;
        this._actors = new Set();
        this._surfaceReveals = new Map();
        this._openOverride = async (actor, done) => {
            if (!this._handles(actor)) return this._open.call(this.wm, actor, done);
            actor._lumaLaunchRect = null;
            actor.remove_all_transitions();
            actor.set({scale_x: 1, scale_y: 1, translation_x: 0, translation_y: 0, opacity: 0});
            actor.show();
            this._actors.add(actor);
            // Same native completion contract, including interrupted effects.
            actor.ease({opacity: 255,
                duration: St.Settings.get().enable_animations ? 160 : 0,
                mode: Clutter.AnimationMode.EASE_OUT_QUAD,
                onStopped: () => {this._actors.delete(actor); done();}});
        };
        this._targetOverride = window => this._handles(window.get_compositor_private())
            ? null : this._target.call(this.wm, window);
        if (this._open) this.wm._lumaPlayOpen = this._openOverride;
        if (this._target) this.wm._lumaDockTarget = this._targetOverride;
    }
    // Full-page notifications keep their final layout throughout the reveal.
    // Reflowing every card at every intermediate width is costly and changes
    // text wrapping in flight. Only the cached frame's centered clip moves.
    revealNotification(actor, startWidth, startHeight) {
        this.cancelReveal(actor);
        const reveal = new PanelReveal(actor, {
            width: actor.width, height: actor.height, initialWidth: startWidth,
            initialHeight: startHeight, contents: [actor._surface.child], done: target => {
                this._surfaceReveals.delete(actor);
                if (!target) {
                    actor.set_size(startWidth, startHeight);
                    actor.close();
                }
            }});
        this._surfaceReveals.set(actor, reveal);
        reveal.begin(actor._pullStart ?? 0, actor._pulling ? null : 1);
    }
    updateNotificationPull(actor, delta) {this._surfaceReveals.get(actor)?.update(delta);}
    endNotificationPull(actor, cancel = false) {this._surfaceReveals.get(actor)?.release(cancel);}
    cancelReveal(actor) {
        this._surfaceReveals.get(actor)?.cancel();
        this._surfaceReveals.delete(actor);
    }
    _handles(actor) {
        return this.home.mode !== 'desktop' && !Main.sessionMode.isLocked &&
            !Main.sessionMode.isGreeter && actor?.meta_window.get_window_type() === Meta.WindowType.NORMAL;
    }
    destroy() {
        if (this.wm._lumaPlayOpen === this._openOverride) this.wm._lumaPlayOpen = this._open;
        if (this.wm._lumaDockTarget === this._targetOverride) this.wm._lumaDockTarget = this._target;
        for (const actor of [...this._surfaceReveals.keys()]) this.cancelReveal(actor);
        for (const actor of [...this._actors]) actor.remove_all_transitions();
        this._actors.clear();
    }
}
