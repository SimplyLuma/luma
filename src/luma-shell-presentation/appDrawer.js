// SPDX-License-Identifier: GPL-2.0-or-later
// LumaUI Desktop drawer. The existing FolderModel owns the real app catalog.
import Atk from 'gi://Atk';
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {AppMenu} from 'resource:///org/gnome/shell/ui/appMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as SurfaceMaterials from 'resource:///org/gnome/shell/ui/lumaSurfaceMaterials.js';
import {appCells, catalogPage, PAGE_SIZE} from './launcherModel.js';

export const AppDrawer = GObject.registerClass(
class AppDrawer extends St.Widget {
    _init(owner) {
        super._init({name: 'lumaDesktopAppDrawer', style_class: 'lumaui-app-drawer',
            reactive: true, can_focus: true, visible: false, accessible_role: Atk.Role.DIALOG});
        this.accessible_name = _('All apps');
        this._owner = owner;
        this._page = 0;
        this._model = null;
        this._catalogSignal = 0;
        this._tiles = [];
        this._menuManager = new PopupMenu.PopupMenuManager(this);
        this._appMenu = null;
        this._appMenuSource = null;
        this._wheelAt = 0;
        this._background = new St.Widget({style_class: 'lumaui-drawer-backdrop'});
        this.add_child(this._background);
        this._binding = SurfaceMaterials.bindSurface(this._background, {backdrop: true, enabled: false,
            cornerRadii: 0, classActors: [this], translucentRecipe: {radius: 44, saturation: 1.3}});
        this._binding.setFloatingRecipe?.({radius: 44, saturation: 1.3});
        this._search = new St.Entry({style_class: 'lumaui-drawer-search', can_focus: true});
        this._search.set_primary_icon(new St.Icon({icon_name: 'lumaui-search-symbolic', icon_size: 16}));
        this.add_child(this._search);
        this._search.clutter_text.connect('text-changed', () => {
            this._page = 0;
            if (this.visible) this._render();
        });
        this._viewport = new St.ScrollView({style_class: 'lumaui-drawer-viewport',
            hscrollbar_policy: St.PolicyType.EXTERNAL, vscrollbar_policy: St.PolicyType.EXTERNAL,
            enable_mouse_scrolling: false, overlay_scrollbars: true});
        this._pages = new St.Viewport({layout_manager: new Clutter.FixedLayout()});
        this._viewport.set_child(this._pages);
        this._viewport.update_fade_effect(new Clutter.Margin({left: 64, right: 64}));
        this.add_child(this._viewport);
        this._dots = new St.BoxLayout({style_class: 'lumaui-drawer-dots'});
        this.add_child(this._dots);
        this.connect('key-press-event', (_actor, event) => this._key(event));
        this.connect('scroll-event', (_actor, event) => this._wheel(event));
        this.connect('button-release-event', (_actor, event) => {
            const source = event.get_source();
            if (source === this || source === this._background) this.close();
            return Clutter.EVENT_PROPAGATE;
        });
        this._search.clutter_text.connect('activate', () => this._tiles[0]?.emit('clicked', 1));
        Main.layoutManager.addTopChrome(this, {trackFullscreen: false});
        if (this.get_parent() === Main.shelf?.get_parent())
            this.get_parent().set_child_below_sibling(this, Main.shelf);
        global.focus_manager.add_group(this);
        this._monitorSignal = Main.layoutManager.connect('monitors-changed', () => {
            if (this.visible) { this._layout(); this._render(); }
        });
        this._focusSignal = global.display.connect('notify::focus-window', () => {
            if (this.visible && global.display.focus_window) this.close();
        });
        this._sessionSignal = Main.sessionMode.connect('updated', () => {
            if (Main.sessionMode.isLocked || Main.sessionMode.isGreeter) this.close();
        });
    }

    setModel(model) {
        if (model === this._model) return;
        if (this._catalogSignal) this._model.disconnect(this._catalogSignal);
        this._model = model;
        this._catalogSignal = model?.connect('changed', () => { if (this.visible) this._render(); }) ?? 0;
        if (!model) this.close();
    }

    toggle() { this.visible ? this.close() : this.open(); }

    open() {
        if (!this._model || Main.sessionMode.isLocked) return;
        this._owner._controller.close();
        // Match the native controller: refresh after Shell startup and on every open.
        this._model.reload();
        this._previousFocus = global.stage.get_key_focus();
        this._page = 0;
        this._search.set_text('');
        this._layout();
        this._render();
        this.show();
        if (this.get_parent() === Main.shelf?.get_parent()) this.get_parent().set_child_below_sibling(this, Main.shelf);
        this._binding.setEnabled(true);
        this._owner.setDrawerOpen(true);
        this._search.grab_key_focus();
        if (this._capture) global.stage.disconnect(this._capture);
        this._capture = global.stage.connect('captured-event', (_stage, event) => this._dragEvent(event));
        const animated = St.Settings.get().enable_animations;
        this.opacity = animated ? 0 : 255;
        this.ease({opacity: 255, duration: animated ? 180 : 0, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        this._tiles.forEach((tile, i) => {
            if (!animated) return;
            tile.opacity = 0;
            tile.translation_y = 12;
            tile.ease({opacity: 255, translation_y: 0, delay: Math.min(i, 30) * 10,
                duration: 260, mode: Clutter.AnimationMode.EASE_OUT_CUBIC});
        });
    }

    close() {
        this._destroyAppMenu();
        if (!this.visible) return;
        this.remove_all_transitions();
        this.hide();
        this._binding.setEnabled(false);
        this._drag = null;
        if (this._capture) global.stage.disconnect(this._capture);
        this._capture = 0;
        this._owner.setDrawerOpen(false);
        if (this._previousFocus?.get_stage()) this._previousFocus.grab_key_focus();
        this._previousFocus = null;
        this._pages.destroy_all_children();
        this._tiles = [];
    }

    _layout() {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor) return;
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
        this._scale = scale;
        this.set_position(monitor.x, monitor.y);
        this.set_size(monitor.width, monitor.height);
        this._background.set_size(monitor.width, monitor.height);
        this._gridWidth = Math.min(980 * scale, Math.max(350 * scale, monitor.width - 160 * scale));
        this._gridHeight = 488 * scale;
        const bottom = Math.max(113 * scale, (Main.shelf?._thickness ?? 67) * scale + 46 * scale);
        const blockHeight = (44 + 26 + 488 + 18 + 19) * scale;
        const top = Math.max(20 * scale, (monitor.height - bottom - blockHeight) / 2);
        const cx = monitor.width / 2;
        this._search.set_position(Math.round(cx - 180 * scale), Math.round(top));
        this._search.set_size(360 * scale, 44 * scale);
        this._viewport.set_position(Math.round(cx - this._gridWidth / 2 - 64 * scale), Math.round(top + 70 * scale));
        this._viewport.set_size(this._gridWidth + 128 * scale, this._gridHeight);
        this._dots.set_position(Math.round(cx), Math.round(top + (70 + 488 + 18) * scale));
        this._dots.set_height(19 * scale);
    }

    _render() {
        this._destroyAppMenu();
        const items = this._model?.items ?? [];
        const query = this._search.get_text();
        this._state = catalogPage(items, query, this._page);
        this._page = this._state.page;
        this._search.hint_text = _('Search %d apps').format(items.length);
        this._pages.destroy_all_children();
        this._dots.destroy_all_children();
        this._tiles = [];
        const scale = this._scale;
        const step = this._gridWidth + 128 * scale;
        const {searching, pages, matches} = this._state;
        const height = searching ? Math.max(this._gridHeight, Math.ceil(matches.length / 7) * 122 * scale) : this._gridHeight;
        this._pages.set_size(searching ? step : step * pages, height);
        // Only the current page and its neighbours own icon actors.
        const first = searching ? 0 : Math.max(0, this._page - 1);
        const last = searching ? 0 : Math.min(pages - 1, this._page + 1);
        for (let page = first; page <= last; page++) {
            const entries = searching ? matches : items.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
            const cells = appCells(entries.length, this._gridWidth / scale, height / scale, searching);
            entries.forEach((item, index) => {
                const tile = this._tile(item);
                const cell = cells[index];
                tile.set_position(Math.round(page * step + 64 * scale + cell.x * scale), Math.round(cell.y * scale));
                tile.set_size(Math.round(cell.width * scale), Math.round(cell.height * scale));
                this._pages.add_child(tile);
                if (searching || page === this._page) this._tiles.push(tile);
            });
        }
        if (!matches.length) {
            const empty = new St.Label({text: query ? _('No app called “%s”').format(query) : _('No applications available'), style_class: 'lumaui-drawer-empty', style: 'text-align: center;'});
            this._pages.add_child(empty);
            empty.set_position(64 * scale, Math.round(this._gridHeight / 2));
            empty.set_width(this._gridWidth);
            empty.clutter_text.line_alignment = Pango.Alignment.CENTER;
        }
        for (let page = 0; page < pages && !searching; page++) {
            const dot = new St.Button({style_class: 'lumaui-drawer-page', can_focus: true,
                accessible_name: _('Page %d').format(page + 1), child: new St.Widget({style_class: 'lumaui-drawer-dot'})});
            if (page === this._page) dot.add_style_pseudo_class('checked');
            dot.connect('clicked', () => this.page(page));
            this._dots.add_child(dot);
        }
        this._dots.opacity = searching || pages < 2 ? 0 : 255;
        const [, width] = this._dots.get_preferred_width(-1);
        this._dots.x = Math.round((this.width - width) / 2);
        this._viewport.hadjustment.value = searching ? 0 : this._page * step;
        this._viewport.vadjustment.value = 0;
    }

    _tile(item) {
        const tile = new St.Button({style_class: 'lumaui-app-tile', can_focus: true,
            accessible_name: item.name, accessible_role: Atk.Role.PUSH_BUTTON});
        const box = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL,
            x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.START});
        const art = item.app.create_icon_texture(64);
        box.add_child(art);
        const label = new St.Label({text: item.name, style_class: 'lumaui-app-name',
            x_align: Clutter.ActorAlign.CENTER});
        label.clutter_text.single_line_mode = true;
        label.clutter_text.ellipsize = Pango.EllipsizeMode.END;
        box.add_child(label);
        tile.child = box;
        tile.connect('clicked', () => {
            if (this._suppressClick || this._appMenu?.isOpen) return;
            this.close();
            item.app.activate();
        });
        const longPress = new Clutter.LongPressGesture();
        longPress.connect('recognize', () => this._popupAppMenu(tile, item.app));
        tile.add_action(longPress);
        const rightClick = new Clutter.ClickGesture({
            required_button: Clutter.BUTTON_SECONDARY, recognize_on_press: true});
        rightClick.connect('recognize', () => this._popupAppMenu(tile, item.app));
        tile.add_action(rightClick);
        tile.connect('key-press-event', (_actor, event) => {
            const key = event.get_key_symbol();
            if (key !== Clutter.KEY_Menu && !(key === Clutter.KEY_F10 &&
                (event.get_state() & Clutter.ModifierType.SHIFT_MASK)))
                return Clutter.EVENT_PROPAGATE;
            this._popupAppMenu(tile, item.app);
            this._appMenu.actor.navigate_focus(null, St.DirectionType.TAB_FORWARD, false);
            return Clutter.EVENT_STOP;
        });
        return tile;
    }

    _popupAppMenu(tile, app) {
        this._drag = null;
        this._suppressClick = true;
        if (this._appMenuSource !== tile) {
            this._destroyAppMenu();
            this._appMenu = new AppMenu(tile, St.Side.TOP, {
                favoritesSection: true, showSingleWindows: true});
            this._appMenuSource = tile;
            this._appMenu.setApp(app);
            Main.uiGroup.add_child(this._appMenu.actor);
            this._menuManager.addMenu(this._appMenu);
        }
        this._appMenu.open();
    }

    _destroyAppMenu() {
        this._appMenu?.close();
        this._appMenu?.destroy();
        this._appMenu = null;
        this._appMenuSource = null;
    }

    page(value) {
        if (this._state.searching) return;
        const page = Math.max(0, Math.min(this._state.pages - 1, value));
        const adjustment = this._viewport.hadjustment;
        const before = adjustment.value;
        this._page = page;
        this._render();
        const target = this._page * (this._gridWidth + 128 * this._scale);
        adjustment.value = before;
        adjustment.ease(target, {duration: St.Settings.get().enable_animations ? 550 : 0,
            mode: Clutter.AnimationMode.EASE_OUT_CUBIC});
    }

    _wheel(event) {
        if (this._state.searching) return Clutter.EVENT_PROPAGATE;
        const now = GLib.get_monotonic_time();
        if (now - this._wheelAt < 450000) return Clutter.EVENT_STOP;
        let direction = event.get_scroll_direction();
        let delta = 0;
        if (direction === Clutter.ScrollDirection.SMOOTH) {
            const [dx, dy] = event.get_scroll_delta();
            delta = Math.abs(dx) > Math.abs(dy) ? dx : dy;
        } else delta = [Clutter.ScrollDirection.DOWN, Clutter.ScrollDirection.RIGHT].includes(direction) ? 1 : -1;
        if (Math.abs(delta) > 0.1) { this._wheelAt = now; this.page(this._page + Math.sign(delta)); }
        return Clutter.EVENT_STOP;
    }

    _key(event) {
        const key = event.get_key_symbol();
        if (key === Clutter.KEY_Escape) { this.close(); return Clutter.EVENT_STOP; }
        if (!this._state.searching && [Clutter.KEY_Left, Clutter.KEY_Right].includes(key)) {
            this.page(this._page + (key === Clutter.KEY_Right ? 1 : -1));
            return Clutter.EVENT_STOP;
        }
        return Clutter.EVENT_PROPAGATE;
    }

    _dragEvent(event) {
        if (this._appMenu?.isOpen) return Clutter.EVENT_PROPAGATE;
        const type = event.type();
        const [x] = event.get_coords();
        const source = event.get_source();
        const pressed = type === Clutter.EventType.TOUCH_BEGIN ||
            (type === Clutter.EventType.BUTTON_PRESS && event.get_button() === Clutter.BUTTON_PRIMARY);
        if (pressed && this._viewport.contains(source)) {
            this._suppressClick = false;
            if (!this._state.searching)
                this._drag = {x, page: this._page, moved: false};
        } else if (this._drag && [Clutter.EventType.MOTION, Clutter.EventType.TOUCH_UPDATE].includes(type)) {
            const dx = x - this._drag.x;
            if (Math.abs(dx) > 8) this._drag.moved = true;
            if (this._drag.moved) {
                this._viewport.hadjustment.value = this._drag.page * (this._gridWidth + 128 * this._scale) - dx;
                return Clutter.EVENT_STOP;
            }
        } else if (this._drag && [Clutter.EventType.BUTTON_RELEASE, Clutter.EventType.TOUCH_END, Clutter.EventType.TOUCH_CANCEL].includes(type)) {
            const drag = this._drag;
            this._drag = null;
            if (drag.moved) {
                this._suppressClick = true;
                this.page(drag.page + (type !== Clutter.EventType.TOUCH_CANCEL && Math.abs(x - drag.x) > this._gridWidth * .15 ? Math.sign(drag.x - x) : 0));
                return Clutter.EVENT_STOP;
            }
        }
        return Clutter.EVENT_PROPAGATE;
    }

    destroy() {
        this.close();
        this.setModel(null);
        Main.layoutManager.disconnect(this._monitorSignal);
        global.display.disconnect(this._focusSignal);
        Main.sessionMode.disconnect(this._sessionSignal);
        global.focus_manager.remove_group(this);
        super.destroy();
    }
});
