// SPDX-License-Identifier: Apache-2.0
import Atk from 'gi://Atk';
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as DND from 'resource:///org/gnome/shell/ui/dnd.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {gettext as _} from 'gettext';
import {MAX_PAGES, moveItem, findItem, copyGrid, resizeItem, resizeCell} from './grid.js';

export class HomeEditor {
    constructor(presentation) {
        this.home = presentation;
        this.editing = false;
        this.items = new Map();
        this._previewTargets = new Map();
        this._done = new St.Button({label: _('Done'), accessible_name: _('Finish rearranging Home'),
            style_class: 'luma-home-edit-done', reactive: true, can_focus: true, visible: false});
        this._done.connect('clicked', () => this.setEditing(false));
        presentation._status.insert_child_below(this._done, presentation._statusRight);
        const blankTap = new Clutter.ClickGesture();
        blankTap.connect('may-recognize', gesture => {
            if (!this.editing || this.dragging) return false;
            const event = gesture.get_point_event(0);
            if (!event) return false;
            const [x, y] = event.get_coords();
            let actor = global.stage.get_actor_at_pos(Clutter.PickMode.REACTIVE, x, y);
            while (actor && actor !== presentation._home) {
                if (actor._delegate?.homeItemId || actor instanceof St.Button || actor instanceof St.Entry)
                    return false;
                actor = actor.get_parent();
            }
            return actor === presentation._home;
        });
        blankTap.connect('recognize', () => {
            this.setEditing(false);
            // DND returns keyboard focus to its source. A background tap
            // finishes that interaction too; otherwise :focus keeps the last
            // moved tile grey after its editing/selected classes are cleared.
            const focus = global.stage.get_key_focus();
            if (focus && [...this.items.values()].some(({actor}) => actor === focus || actor.contains(focus)))
                global.stage.set_key_focus(null);
        });
        presentation._home.add_action_full('LumaHomeFinishEditing', Clutter.EventPhase.CAPTURE, blankTap);
        this._blankTap = blankTap;
        this._monitor = {dragMotion: event => this._motion(event)};
        presentation._home._delegate = {
            handleDragOver: source => source.homeEditor === this
                ? DND.DragMotionResult.MOVE_DROP : DND.DragMotionResult.CONTINUE,
            acceptDrop: (source, _actor, x, y) => this._drop(source, x, y),
        };
    }

    attach(actor, definition, createDragActor) {
        const delegate = {homeEditor: this, homeItemId: definition.id,
            getDragActor: createDragActor, getDragActorSource: () => actor};
        actor._delegate = delegate;
        actor.can_focus = true;
        if (definition.kind === 'app') actor.accessible_role = Atk.Role.PUSH_BUTTON;
        const draggable = DND.makeDraggable(actor, {manualMode: !this.editing,
            timeoutThreshold: 450, dragActorMaxSize: 200});
        let grabPoint = null;
        actor.connect('captured-event', (_actor, event) => {
            if (event.type() === Clutter.EventType.TOUCH_BEGIN ||
                event.type() === Clutter.EventType.BUTTON_PRESS)
                grabPoint = event.get_coords();
            return Clutter.EVENT_PROPAGATE;
        });
        const press = new Clutter.LongPressGesture();
        press.can_not_cancel(draggable.startGesture);
        draggable.startGesture.can_not_cancel(press);
        press.connect('recognize', gesture => {
            this.setEditing(true);
            this.picked = definition.id;
            // Transfer the same held sequence to native DND; lifting and
            // pressing again is not required after the long press.
            draggable.startGesture.start_drag(gesture.get_point_event(0));
        });
        actor.add_action(press);
        actor.connect('popup-menu', () => {
            this.picked = definition.id;
            this.setEditing(true);
        });
        draggable.connect('drag-begin', () => {
            this.setEditing(true);
            this.dragging = delegate;
            this._activeDrag = draggable;
            this.picked = definition.id;
            const [actorX, actorY] = actor.get_transformed_position();
            const [pointerX, pointerY] = grabPoint ?? [actorX, actorY];
            this._grabOffset = {x: (pointerX - actorX) / this.home._scale,
                y: (pointerY - actorY) / this.home._scale};
            actor.add_style_class_name('dragging');
            this.home._pageSwipe.enabled = false;
            DND.addDragMonitor(this._monitor);
        });
        let alive = true;
        actor.connect('destroy', () => { alive = false; });
        draggable.connect('drag-end', () => {
            if (alive) actor.remove_style_class_name('dragging');
            this._endDrag();
        });
        this.items.set(definition.id, {actor, draggable});
        actor.accessible_name = actor.accessible_name || definition.id;
        const click = new Clutter.ClickGesture();
        click.connect('may-recognize', () => this.editing && !this.dragging);
        click.connect('recognize', () => this.select(definition.id));
        actor.add_action_full('LumaHomeEditSelect', Clutter.EventPhase.CAPTURE, click);
    }

    setEditing(editing) {
        this.editing = editing;
        if (!editing) {
            this.cancel();
            this.picked = null;
        }
        for (const {actor, draggable} of this.items.values()) {
            draggable.startGesture.set_manual_mode(!editing);
            if (editing) actor.add_style_class_name('editing');
            else {
                actor.remove_style_class_name('editing');
                actor.remove_style_class_name('selected');
                actor.remove_style_class_name('dragging');
            }
        }
        this.syncVisibility();
    }

    syncVisibility() {
        if (this._destroyed) return;
        this._done.visible = this.editing && this.home._home.visible &&
            this.home.mode !== 'desktop' && !Main.sessionMode.isLocked;
        this._syncResizeHandle();
    }

    select(id) {
        this.picked = id;
        for (const [key, {actor}] of this.items)
            (key === id ? actor.add_style_class_name : actor.remove_style_class_name)
                .call(actor, 'selected');
        this._syncResizeHandle();
    }

    resetItems() {
        this._clearReflow();
        this._watchResizeActor(null);
        this._resizeHandle?.destroy();
        this._resizeHandle = null;
        this._previewTargets.clear();
        this.items.clear();
    }

    _limits(id) {
        const host = this.home._widgetHosts.get(id);
        const grid = this.home._grid;
        return {minW: Math.min(grid.columns, host?.minColumns ?? host?.columns ?? 2),
            minH: Math.min(grid.rows, host?.minRows ?? host?.rows ?? 2),
            maxW: Math.min(grid.columns, host?.maxColumns ?? grid.columns),
            maxH: Math.min(grid.rows, host?.maxRows ?? grid.rows)};
    }

    _watchResizeActor(actor) {
        if (actor === this._resizeActor) return;
        if (this._resizeActor) {
            for (const signal of this._resizeActorSignals) this._resizeActor.disconnect(signal);
        }
        this._resizeActor = actor;
        this._resizeActorSignals = actor ? ['notify::width', 'notify::height']
            .map(signal => actor.connect(signal, () => this._syncResizeHandle())) : [];
    }

    _syncResizeHandle() {
        const p = this.home;
        const item = this.picked && p._grid && findItem(p._grid, this.picked);
        const actor = item && this.items.get(item.id)?.actor;
        const visible = this._done.visible && !this.dragging && !this.savingResize &&
            item?.kind === 'widget' && item.page === p._page && actor;
        if (!visible) {
            this._resizeHandle?.hide();
            this._watchResizeActor(null);
            return;
        }
        if (!this._resizeHandle) {
            const handle = new St.Button({style_class: 'luma-home-resize-handle',
                reactive: true, can_focus: true, track_hover: true,
                child: new St.Icon({icon_name: 'view-fullscreen-symbolic', icon_size: 18})});
            handle.connect('clicked', () => handle.grab_key_focus());
            handle.get_accessible().set_description(_('Drag to resize. Arrow keys change width and height; Escape cancels.'));
            const pan = new Clutter.PanGesture({min_n_points: 1, max_n_points: 1});
            pan.set_begin_threshold(4);
            pan.connect('may-recognize', () => this.editing && !this.dragging && !this.savingResize);
            pan.connect('recognize', () => this.beginResize(this.picked));
            pan.connect('pan-update', gesture => {
                const delta = gesture.get_accumulated_delta_abs();
                this.updateResize(delta.get_x(), delta.get_y());
            });
            pan.connect('end', () => this.finishResize(true));
            pan.connect('cancel', () => this.finishResize(false));
            handle.add_action_full('LumaWidgetResize', Clutter.EventPhase.CAPTURE, pan);
            p._home.add_child(handle);
            this._resizeHandle = handle;
        }
        this._watchResizeActor(actor);
        const size = 44 * p._scale;
        const [x, y] = actor.get_transformed_position();
        const [homeX, homeY] = p._home.get_transformed_position();
        const rtl = Clutter.get_default_text_direction() === Clutter.TextDirection.RTL;
        this._resizeHandle.set_position(x - homeX + (rtl ? 0 : actor.width - size), y - homeY + actor.height - size);
        this._resizeHandle.set_size(size, size);
        this._resizeHandle.accessible_name = _('Resize %s widget').format(
            p._widgetHosts.get(item.id)?.name ?? item.id);
        this._resizeHandle.show();
        actor.add_style_class_name('selected');
    }

    beginResize(id) {
        if (!this.editing || this.dragging || this.resizing || this.savingResize) return false;
        const item = findItem(this.home._grid, id);
        if (!item || item.kind !== 'widget') return false;
        this.select(id);
        this.resizing = {id, item, original: copyGrid(this.home._grid),
            preview: this.home._grid, limits: this._limits(id), w: item.w, h: item.h};
        this._previewTargets.clear();
        this.home._pageSwipe.enabled = false;
        for (const {draggable} of this.items.values()) draggable.startGesture.set_manual_mode(true);
        return true;
    }

    updateResize(dx, dy) {
        const r = this.resizing;
        if (!r) return;
        const p = this.home;
        const rtl = Clutter.get_default_text_direction() === Clutter.TextDirection.RTL;
        const width = resizeCell(r.item.w, dx / p._scale * (rtl ? -1 : 1),
            p._layout.contentWidth / p._grid.columns, r.limits.minW,
            Math.min(r.limits.maxW, p._grid.columns - r.item.x), r.w);
        const height = resizeCell(r.item.h, dy / p._scale, p._layout.rowHeight,
            r.limits.minH, Math.min(r.limits.maxH, p._grid.rows - r.item.y), r.h);
        this.previewResize(width, height);
    }

    previewResize(width, height) {
        const r = this.resizing;
        if (!r || (r.w === width && r.h === height)) return false;
        try {
            const next = resizeItem(r.original, r.id, width, height, r.limits);
            r.preview = next;
            r.w = width; r.h = height;
            this._applyResizePreview(next);
            return true;
        } catch {
            // Keep the last fitting preview when all64 pages are full. Neither
            // saved state nor other widgets' spans may be sacrificed to fit.
            return false;
        }
    }

    _clearReflow() {
        if (this._reflowSource) GLib.source_remove(this._reflowSource);
        this._reflowSource = 0;
        this.reflowing = false;
    }

    _applyResizePreview(grid, rebuildAfter = false) {
        const p = this.home, layout = p._layout, scale = p._scale;
        const cell = layout.contentWidth / grid.columns;
        const rtl = Clutter.get_default_text_direction() === Clutter.TextDirection.RTL;
        const duration = St.Settings.get().enable_animations ? 180 : 0;
        const before = new Map([...this.items].map(([id, {actor}]) => [id, actor.get_transformed_position()]));
        while (p._pageViews.length < grid.pages.length) p._appendHomePage();
        p._strip.translation_x = p._pageOffset(p._page);
        for (const [page, records] of grid.pages.entries()) {
            const parent = p._pageViews[page];
            for (const record of records) {
                const actor = this.items.get(record.id)?.actor;
                if (!actor) continue;
                const host = p._widgetHosts.get(record.id);
                const inset = record.kind === 'widget' ? (layout.compact ? 3 : (host.gap ?? 12) / 2) : 0;
                const x = (layout.inset + (rtl ? grid.columns - record.x - record.w : record.x) * cell + inset) * scale;
                const y = (layout.top + record.y * layout.rowHeight + inset) * scale;
                const width = (cell * record.w - 2 * inset) * scale;
                const height = (layout.rowHeight * record.h - 8 - 2 * inset) * scale;
                const target = `${page}:${x}:${y}:${width}:${height}`;
                if (this._previewTargets.get(record.id) === target) continue;
                this._previewTargets.set(record.id, target);
                // FLIP from the *painted* position when retargeting. Neighbours
                // only translate; their content never stretches or rebuilds.
                const [oldX, oldY] = before.get(record.id);
                const [px, py] = parent.get_transformed_position();
                if (actor.get_parent() === parent && Math.abs(oldX - px - x) < .1 &&
                    Math.abs(oldY - py - y) < .1 && Math.abs(actor.width - width) < .1 &&
                    Math.abs(actor.height - height) < .1)
                    continue; // no timelines or paints for unaffected neighbours
                actor.remove_all_transitions();
                if (actor.get_parent() !== parent) {
                    actor.get_parent().remove_child(actor);
                    parent.add_child(actor);
                }
                parent.show(); // allow an outgoing item to glide to a new page
                parent.set_offscreen_redirect(Clutter.OffscreenRedirect.AUTOMATIC_FOR_OPACITY);
                actor.set_position(x, y);
                actor.set({translation_x: oldX - px - x, translation_y: oldY - py - y});
                actor.ease({translation_x: 0, translation_y: 0, width, height, duration,
                    mode: Clutter.AnimationMode.EASE_OUT_CUBIC});
            }
        }
        this._clearReflow();
        this.reflowing = true;
        this._reflowSource = GLib.timeout_add(GLib.PRIORITY_DEFAULT, duration + 16, () => {
            this._reflowSource = 0;
            this.reflowing = false;
            p._pageVisibilityKey = null;
            p._syncPageViews(p._page);
            this._syncResizeHandle();
            if (rebuildAfter) p._queueRebuild();
            return GLib.SOURCE_REMOVE;
        });
        this._syncResizeHandle();
    }

    finishResize(commit) {
        const r = this.resizing;
        if (!r) return;
        this.resizing = null;
        const p = this.home;
        for (const {draggable} of this.items.values()) draggable.startGesture.set_manual_mode(!this.editing);
        p._pageSwipe.enabled = p._home.visible && p._pageViews.length > 1;
        if (!commit || (r.w === r.item.w && r.h === r.item.h)) {
            this._applyResizePreview(r.original, true); // retire unsaved pages after settling
            return;
        }
        this.savingResize = true;
        this._syncResizeHandle();
        const failed = () => {
            this.savingResize = false;
            if (!this._destroyed) this._applyResizePreview(r.original, true);
        };
        try {
            p._saveHomeGrid(r.preview, () => {
                this.savingResize = false;
                p._grid = r.preview;
                this._syncResizeHandle();
            }, failed);
        } catch (error) {
            failed();
            Main.notifyError(_('Unable to resize widget'), error.message);
        }
    }

    _target(x, y, id) {
        const p = this.home;
        const current = findItem(p._grid, id);
        if (!current) return null;
        x /= p._scale;
        y /= p._scale;
        const layout = p._layout;
        if (y < layout.top || y >= layout.top + p._grid.rows * layout.rowHeight)
            return null;
        const offset = this._grabOffset ?? {x: 0, y: 0};
        let column = Math.round((x - layout.inset - offset.x) / (layout.contentWidth / layout.columns));
        if (Clutter.get_default_text_direction() === Clutter.TextDirection.RTL)
            column = layout.columns - current.w - column;
        return {page: p._page,
            x: Math.max(0, Math.min(p._grid.columns - current.w, column)),
            y: Math.max(0, Math.min(p._grid.rows - current.h, Math.round((y - layout.top - offset.y) / layout.rowHeight)))};
    }

    _motion(event) {
        if (event.source?.homeEditor !== this) return DND.DragMotionResult.CONTINUE;
        const p = this.home;
        const monitor = Main.layoutManager.primaryMonitor;
        const x = event.x - monitor.x;
        const y = event.y - monitor.y;
        const rtl = Clutter.get_default_text_direction() === Clutter.TextDirection.RTL;
        const edge = x < 32 * p._scale ? (rtl ? 1 : -1)
            : x > monitor.width - 32 * p._scale ? (rtl ? -1 : 1) : 0;
        if (edge !== this._edge) {
            this._clearEdge();
            this._edge = edge;
            if (!edge) this._createdPage = false;
        }
        if (edge && !this._edgeTimer) this._queueEdge();
        const target = this._target(x, y, event.source.homeItemId);
        this._showTarget(target, event.source.homeItemId);
        return DND.DragMotionResult.CONTINUE;
    }

    _queueEdge() {
        const p = this.home;
        if (p._page + this._edge < 0 || p._page + this._edge >= MAX_PAGES) return;
        if (this._createdPage && p._page + this._edge >= p._grid.pages.length) return;
        this._edgeTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 700, () => {
            this._edgeTimer = 0;
            if (!this.dragging || !this._edge) return GLib.SOURCE_REMOVE;
            const page = p._page + this._edge;
            if (page >= p._pageViews.length) {
                p._appendHomePage();
                this._createdPage = true;
            }
            p._settlePage(page, St.Settings.get().enable_animations ? 180 : 0);
            this._indicator?.hide();
            if (!this._createdPage) this._queueEdge();
            return GLib.SOURCE_REMOVE;
        });
    }

    _clearEdge() {
        if (this._edgeTimer) GLib.source_remove(this._edgeTimer);
        this._edgeTimer = 0;
        this._edge = 0;
    }

    _showTarget(target, id) {
        const p = this.home;
        if (!target) { this._indicator?.hide(); return; }
        if (!this._indicator) {
            this._indicator = new St.Widget({style_class: 'luma-home-drop-target', reactive: false});
            p._home.add_child(this._indicator);
        }
        const item = findItem(p._grid, id);
        const cellWidth = p._layout.contentWidth / p._grid.columns;
        const rtl = Clutter.get_default_text_direction() === Clutter.TextDirection.RTL;
        this._indicator.set_position((p._layout.inset + (rtl ? p._grid.columns - target.x - item.w : target.x) * cellWidth) * p._scale,
            (p._layout.top + target.y * p._layout.rowHeight) * p._scale);
        this._indicator.set_size(cellWidth * item.w * p._scale, p._layout.rowHeight * item.h * p._scale);
        this._indicator.show();
    }

    _drop(source, x, y) {
        if (source.homeEditor !== this) return false;
        const target = this._target(x, y, source.homeItemId);
        if (!target) return false;
        return this.move(source.homeItemId, target.page, target.x, target.y);
    }

    move(id, page, x, y) {
        if (this.resizing || this.savingResize) return false;
        try {
            const next = moveItem(this.home._grid, id, page, x, y);
            this.home._saveHomeGrid(next);
            this.home._grid = next;
            this.home._page = findItem(next, id).page;
            return true;
        } catch (error) {
            Main.notifyError(_('Unable to move Home item'), error.message);
            return false;
        }
    }

    keyboard(event) {
        if (event.get_key_symbol() === Clutter.KEY_Escape && this.editing) {
            this.setEditing(false);
            return true;
        }
        if (!this.editing) return false;
        const state = event.get_state();
        const handleFocused = global.stage.get_key_focus() === this._resizeHandle;
        if (!handleFocused && !(state & Clutter.ModifierType.CONTROL_MASK)) return false;
        let actor = global.stage.get_key_focus();
        while (actor && !actor._delegate?.homeItemId) actor = actor.get_parent();
        const id = actor?._delegate.homeItemId ?? this.picked;
        const current = id && findItem(this.home._grid, id);
        if (!current) return false;
        const keys = new Map([[Clutter.KEY_Left, [-1, 0]], [Clutter.KEY_Right, [1, 0]],
            [Clutter.KEY_Up, [0, -1]], [Clutter.KEY_Down, [0, 1]]]);
        const delta = keys.get(event.get_key_symbol());
        if (delta && current.kind === 'widget' &&
            (handleFocused || (state & Clutter.ModifierType.SHIFT_MASK))) {
            if (this.beginResize(id)) {
                const direction = Clutter.get_default_text_direction() === Clutter.TextDirection.RTL ? -1 : 1;
                this.previewResize(current.w + delta[0] * direction, current.h + delta[1]);
                this.finishResize(true);
            }
            return true;
        }
        let moved = false;
        if (delta) moved = this.move(id, current.page,
            Math.max(0, Math.min(this.home._grid.columns - current.w, current.x + delta[0] *
                (Clutter.get_default_text_direction() === Clutter.TextDirection.RTL ? -1 : 1))),
            Math.max(0, Math.min(this.home._grid.rows - current.h, current.y + delta[1])));
        else if (event.get_key_symbol() === Clutter.KEY_Page_Down)
            moved = this.move(id, current.page + 1, current.x, current.y);
        else if (event.get_key_symbol() === Clutter.KEY_Page_Up && current.page > 0)
            moved = this.move(id, current.page - 1, current.x, current.y);
        else return false;
        if (moved) this.home._queueRebuild();
        return true;
    }

    _endDrag() {
        this._clearEdge();
        this._createdPage = false;
        DND.removeDragMonitor(this._monitor);
        this._indicator?.destroy();
        this._indicator = null;
        this.dragging = null;
        this._grabOffset = null;
        this._activeDrag = null;
        if (!this._destroyed) this._syncResizeHandle();
        if (!this._destroyed) this.home._queueRebuild();
    }

    cancel() {
        this.finishResize(false);
        if (this._activeDrag) this._activeDrag._cancelDrag(global.get_current_time());
        this._clearEdge();
    }

    destroy() {
        this._destroyed = true;
        this.cancel();
        this._clearReflow();
        this._watchResizeActor(null);
        this._resizeHandle?.destroy();
        DND.removeDragMonitor(this._monitor);
        this.home._home.remove_action(this._blankTap);
        this._done.destroy();
    }
}
