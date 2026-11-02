// SPDX-License-Identifier: Apache-2.0
// TV posture: the shell's TV presentation. The shared state service owns the
// posture (luma-shell-state `posture`); Presentation hands it here. While it
// is active this owns the screen: the TV home (Home, Apps, Search), apps full
// screen without chrome, and no overview, dock, panel or desktop gestures.
// Every length comes from tvTokens.js in TV units, so any screen size or
// aspect works; nothing here is specific to one device.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GnomeDesktop from 'gi://GnomeDesktop';
import Graphene from 'gi://Graphene';
import Meta from 'gi://Meta';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {getNotificationTray} from 'resource:///org/gnome/shell/ui/lumaNotificationBeacon.js';
import {tv as TOKENS} from './tvTokens.js';
import {TvDialogs} from './tvDialogs.js';
import {TvInput} from './tvInput.js';
import {TvQuickPanel} from './tvQuickPanel.js';
import {greetingPart, nearest, remoteAction, repeatAllowed, searchTvApps, tileGrid, tvApps, tvEntry, tvUnit,
    weatherGlyph} from './tvModel.js';
import {gettext as _} from 'gettext';

// The prism wallpaper, blurred right out ahead of time
// (scripts/developer/render-tv-backdrop.py): a still image, never a live blur.
const BACKDROP = 'resource:///org/gnome/shell/ui/lumaPresentation/tv-backdrop.jpg';
const PAGES = ['home', 'apps', 'search'];
const iconTheme = new St.IconTheme();

function normalWindow(window) {
    return window && window.get_window_type() === Meta.WindowType.NORMAL &&
        !window.is_override_redirect() && !window.is_skip_taskbar() && !window.get_transient_for();
}

function visibleAppWindows() {
    return global.get_window_actors().map(actor => actor.meta_window)
        .filter(window => normalWindow(window) && !window.minimized && window.showing_on_its_workspace());
}

export class TvPresentation {
    constructor(presentation) {
        this._presentation = presentation;
        this.active = false;
        this._signals = [];
        this._activeSignals = [];
        this._page = 'home';
        this._focus = {kind: 'tab', index: 0};
        this._query = '';
        this._apps = [];
        this._results = [];
        this._tiles = [];
        this._fullscreened = new Map();
        this._minimizedOnEnter = new Set();
        this._settings = presentation._settings;
        this._clock = new GnomeDesktop.WallClock({time_only: true});
        this._appSystem = Shell.AppSystem.get_default();
    }

    // ── posture ───────────────────────────────────────────────────────────

    setActive(active) {
        if (active === this.active || Main.sessionMode.isGreeter) return;
        this.active = active;
        if (active) this._enter();
        else this._leave();
    }

    _enter() {
        this._build();
        Main.shelf?.setPresentationMode?.('tv');
        Main.shelf?._trackWorkArea?.(false);
        if (Main.shelf) Main.layoutManager.setChromeHidden(Main.shelf, true);
        if (Main.stickyDock) Main.layoutManager.setChromeHidden(Main.stickyDock, true);
        Main.layoutManager.panelBox.hide();
        Main.panel.hide();
        Main.layoutManager.setChromeHidden(Main.layoutManager.panelBox, true);
        if (Main.overview.visible) Main.overview.hide();
        // What was open stays open, out of the way; the TV home comes first.
        for (const window of visibleAppWindows()) {
            this._minimizedOnEnter.add(window);
            window.minimize();
        }
        const watch = (object, signal, callback) =>
            this._activeSignals.push([object, object.connect(signal, callback)]);
        watch(global.window_manager, 'map', (_wm, actor) => this._windowShown(actor.meta_window));
        watch(global.window_manager, 'minimize', () => this._queueHomeCheck());
        watch(global.window_manager, 'destroy', () => this._queueHomeCheck());
        watch(global.display, 'notify::focus-window', () => {
            const window = global.display.focus_window;
            if (normalWindow(window) && !window.minimized) this._windowShown(window);
        });
        watch(Main.overview, 'showing', () => {
            // Super and the overview's gestures go home on a TV.
            Main.overview.hide();
            this.showHome('home');
        });
        watch(Main.layoutManager, 'monitors-changed', () => this._layout());
        watch(this._clock, 'notify::clock', () => this._tick());
        watch(this._appSystem, 'installed-changed', () => this._loadApps());
        watch(Gio.AppInfoMonitor.get(), 'changed', () => this._loadApps());
        watch(this._settings, 'changed::reduce-transparency', () => this._restyle());
        // No notification lip on a TV; notifications still arrive and wait. The
        // lip attaches itself lazily and re-shows on each notification, so keep
        // it hidden for as long as the posture lasts.
        this._notificationLip = getNotificationTray();
        const hideLip = () => {
            if (this.active && this._notificationLip?.visible) this._notificationLip.hide();
        };
        watch(this._notificationLip, 'notify::visible', hideLip);
        watch(this._notificationLip, 'parent-set', hideLip);
        hideLip();
        watch(St.Settings.get(), 'notify::high-contrast', () => this._restyle());
        const model = this._presentation._liveWidgets?.model;
        if (model) watch(model, 'changed', () => this._tick());
        this._loadApps();
        this.showHome('home');
        // The quick panel: the TV posture of Quick Options and notifications
        // (tvQuickPanel.js), from the bar's cluster or a long press of Home.
        this._quick = new TvQuickPanel({unit: () => this._unit(), opaque: () => this._opaque(),
            opened: () => this._draw(), closed: origin => this._quickClosed(origin)});
        // Every remote and gamepad becomes the TV's keys (tvInput.js).
        this._input = new TvInput({home: () => this.showHome('home'),
            quickPanel: () => this._quick?.toggle('remote')});
        // Every field, the Shell's and every app's, gets the TV keyboard.
        Main.keyboard?.setTvPosture?.(true);
        // System dialogs take the TV sheet and the remote (tvDialogs.js).
        this._dialogs = new TvDialogs({unit: () => this._unit(), opaque: () => this._opaque()});
    }

    _leave() {
        for (const [object, id] of this._activeSignals) object.disconnect(id);
        this._activeSignals = [];
        this._input?.destroy();
        this._input = null;
        this._quick?.destroy();
        this._quick = null;
        this._dialogs?.destroy();
        this._dialogs = null;
        Main.keyboard?.setTvPosture?.(false);
        if (this._homeCheck) GLib.source_remove(this._homeCheck);
        this._homeCheck = 0;
        if (this._home) {
            this._home.destroy();
            this._home = null;
        }
        this._tiles = [];
        for (const [window, wasFullscreen] of this._fullscreened) {
            window.disconnectObject(this);
            if (window.get_compositor_private() && !wasFullscreen) window.unmake_fullscreen();
        }
        this._fullscreened.clear();
        for (const window of this._minimizedOnEnter) {
            if (window.get_compositor_private()) window.unminimize();
        }
        this._minimizedOnEnter.clear();
        if (Main.stickyDock) Main.layoutManager.setChromeHidden(Main.stickyDock, false);
        this._notificationLip?.show();
        this._notificationLip = null;
        if (Main.shelf) Main.layoutManager.setChromeHidden(Main.shelf, false);
        Main.layoutManager.setChromeHidden(Main.layoutManager.panelBox, false);
        Main.layoutManager.panelBox.show();
        Main.panel.show();
        // The presentation underneath takes the chrome back: the Dash hides the
        // panel again on a desktop; a handheld presentation re-enters itself.
        Main.shelf?.setPresentationMode?.(this._presentation.mode);
    }

    // ── windows: full screen, no chrome; the TV home when none shows ───────

    _windowShown(window) {
        if (!this.active || !normalWindow(window) || Main.sessionMode.isLocked) return;
        if (!this._fullscreened.has(window)) {
            this._fullscreened.set(window, window.is_fullscreen());
            window.connectObject('unmanaged', () => this._fullscreened.delete(window), this);
        }
        if (!window.is_fullscreen()) window.make_fullscreen();
        this._minimizedOnEnter.delete(window);
        this._hideHome();
    }

    _queueHomeCheck() {
        if (this._homeCheck) return;
        this._homeCheck = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._homeCheck = 0;
            // Back at an app's top level minimises it: the Apps page it came from.
            if (this.active && !this._home?.visible && !visibleAppWindows().length) this.showHome('apps');
            return GLib.SOURCE_REMOVE;
        });
    }

    showHome(page = 'home') {
        if (!this.active || !this._home) return;
        this._quick?.close();
        for (const window of visibleAppWindows()) window.minimize();
        this._open(page);
        this._focus = {kind: 'tab', index: PAGES.indexOf(page)};
        this._home.show();
        Main.uiGroup.set_child_above_sibling(this._home, global.window_group);
        // Never from under a system dialog or menu that holds the grab: its
        // keys would go nowhere. The focus comes here as the grab ends.
        if (!Main.modalCount) this._home.grab_key_focus();
        this._draw();
    }

    _hideHome() {
        if (!this._home?.visible) return;
        this._entry?.clutter_text.set_editable(false);
        this._home.hide();
    }

    // ── the TV home ───────────────────────────────────────────────────────

    _build() {
        this._home = new St.Widget({name: 'lumaTvHome', reactive: true, can_focus: true,
            layout_manager: new Clutter.FixedLayout(), accessible_name: _('TV home')});
        Main.layoutManager.addChrome(this._home, {affectsStruts: false, trackFullscreen: false});
        this._backdrop = new St.Widget({style: `background-image: url("${BACKDROP}"); background-size: cover;`});
        this._home.add_child(this._backdrop);

        // The bar: Home · Apps · Search in a recessed track; weather | time.
        this._track = new St.BoxLayout({});
        this._tabs = PAGES.map((page, index) => {
            const box = new St.BoxLayout({y_align: Clutter.ActorAlign.CENTER});
            if (page === 'search')
                box.add_child(new St.Icon({icon_name: 'lumaui-search-symbolic', y_align: Clutter.ActorAlign.CENTER}));
            const text = new St.Label({text: [_('Home'), _('Apps'), _('Search')][index],
                y_align: Clutter.ActorAlign.CENTER});
            box.add_child(text);
            const tab = new St.Button({child: box, can_focus: false, reactive: true,
                accessible_name: text.text});
            tab.connect('clicked', () => {
                this._open(page);
                this._focus = page === 'search' ? {kind: 'field'} : {kind: 'tab', index};
                this._draw();
            });
            this._track.add_child(tab);
            return {tab, box, text, icon: box.get_first_child() !== text ? box.get_first_child() : null};
        });
        this._home.add_child(this._track);
        this._now = new St.BoxLayout({});
        this._weather = new St.BoxLayout({y_align: Clutter.ActorAlign.CENTER});
        this._weatherIcon = new St.Icon({y_align: Clutter.ActorAlign.CENTER});
        this._weatherText = new St.Label({y_align: Clutter.ActorAlign.CENTER});
        this._weather.add_child(this._weatherIcon);
        this._weather.add_child(this._weatherText);
        this._divider = new St.Widget({y_align: Clutter.ActorAlign.CENTER});
        this._time = new St.Label({y_align: Clutter.ActorAlign.CENTER});
        this._now.add_child(this._weather);
        this._now.add_child(this._divider);
        this._now.add_child(this._time);
        this._home.add_child(this._now);

        // Home: the greeting, bottom left, its baseline on the safe line.
        this._greeting = new St.Label({});
        this._pages = {
            home: new St.Widget({layout_manager: new Clutter.FixedLayout()}),
            apps: new St.Widget({layout_manager: new Clutter.FixedLayout()}),
            search: new St.Widget({layout_manager: new Clutter.FixedLayout()}),
        };
        this._pages.home.add_child(this._greeting);
        // Apps: the grid of wide tiles, or the empty state.
        this._grid = new St.Widget({layout_manager: new Clutter.FixedLayout()});
        this._empty = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL});
        this._emptyTitle = new St.Label({text: _('No TV apps yet'), x_align: Clutter.ActorAlign.CENTER});
        this._emptyText = new St.Label({text: _('Apps that work on a TV appear here.'),
            x_align: Clutter.ActorAlign.CENTER});
        this._empty.add_child(this._emptyTitle);
        this._empty.add_child(this._emptyText);
        this._pages.apps.add_child(this._grid);
        this._pages.apps.add_child(this._empty);
        // Search: a recessed field; results are the same tiles.
        this._field = new St.BoxLayout({reactive: true});
        this._fieldIcon = new St.Icon({icon_name: 'lumaui-search-symbolic', y_align: Clutter.ActorAlign.CENTER});
        this._entry = new St.Entry({hint_text: _('Search apps'), can_focus: true, x_expand: true,
            y_align: Clutter.ActorAlign.CENTER});
        this._entry.clutter_text.connect('text-changed', () => {
            this._query = this._entry.get_text();
            this._drawResults();
        });
        this._entry.clutter_text.connect('key-press-event', (_actor, event) => this._fieldKey(event));
        // The TV keyboard's action key searches here.
        Main.keyboard?.setFieldAction?.(this._entry.clutter_text, _('Search'));
        this._field.add_child(this._fieldIcon);
        this._field.add_child(this._entry);
        this._field.connect('button-press-event', () => {
            this._focus = {kind: 'field'};
            this._draw();
            return Clutter.EVENT_STOP;
        });
        this._resultGrid = new St.Widget({layout_manager: new Clutter.FixedLayout()});
        this._noResults = new St.Label({x_align: Clutter.ActorAlign.CENTER});
        this._pages.search.add_child(this._field);
        this._pages.search.add_child(this._resultGrid);
        this._pages.search.add_child(this._noResults);
        for (const page of PAGES) this._home.add_child(this._pages[page]);
        this._home.connect('key-press-event', (_actor, event) => this._key(event));
        this._layout();
    }

    _unit() {
        const monitor = Main.layoutManager.primaryMonitor;
        return monitor ? tvUnit(monitor.width, monitor.height, TOKENS.layout) : 1;
    }

    // CSS lengths: TV units, in the theme's own pixels.
    _px(value) {
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor || 1;
        return `${(value * this._u / scale).toFixed(2)}px`;
    }

    _opaque() {
        return this._settings.get_boolean('reduce-transparency') || St.Settings.get().high_contrast;
    }

    // A material colour; Reduce Transparency and High Contrast make it opaque.
    _material(name) {
        const value = TOKENS.materials[name];
        if (!this._opaque()) return value;
        return value.replace(/rgba\(([^,]+),([^,]+),([^,]+),[^)]+\)/, 'rgba($1,$2,$3, 1)');
    }

    _font(size, weight) {
        return `font-family: Figtree, sans-serif; font-size: ${this._px(size)}; font-weight: ${weight};`;
    }

    _layout() {
        if (!this._home) return;
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor) return;
        this._u = this._unit();
        const u = this._u;
        const {bar, layout, home} = TOKENS;
        this._home.set_position(monitor.x, monitor.y);
        this._home.set_size(monitor.width, monitor.height);
        this._backdrop.set_size(monitor.width, monitor.height);
        for (const page of PAGES) this._pages[page].set_size(monitor.width, monitor.height);
        this._track.set_position(layout.safe_x * u, bar.top * u);
        this._track.set_height(bar.height * u);
        this._fieldWidth = Math.min(home.field_width * u, monitor.width - 2 * layout.safe_x * u);
        this._field.set_size(this._fieldWidth, home.field_height * u);
        this._field.set_position((monitor.width - this._fieldWidth) / 2, home.search_top * u);
        this._restyle();
    }

    _restyle() {
        if (!this._home) return;
        const {bar, home, tile} = TOKENS;
        const m = name => this._material(name);
        this._track.style = `padding: ${this._px(bar.track_padding)}; spacing: ${this._px(bar.tab_gap)};
            border-radius: ${this._px(bar.track_radius)}; background-color: ${m('well')};
            border: ${this._px(1.5)} solid ${m('well_rim')};
            box-shadow: inset 0 ${this._px(2)} ${this._px(4)} ${m('well_shade')};`;
        for (const {box, icon, text} of this._tabs) {
            box.style = `spacing: ${this._px(bar.tab_icon_gap)};`;
            text.style = this._font(bar.tab_size, bar.tab_weight);
            if (icon) icon.style = `icon-size: ${this._px(bar.tab_icon)};`;
        }
        this._now.style = `spacing: ${this._px(bar.now_gap)};`;
        this._weather.style = `spacing: ${this._px(bar.now_icon_gap)};`;
        const clock = `${this._font(bar.clock_size, bar.clock_weight)} color: ${m('ink')};
            font-feature-settings: "tnum";`;
        this._weatherText.style = clock;
        this._time.style = clock;
        this._divider.style = `width: ${this._px(bar.divider_width)}; height: ${this._px(bar.divider_height)};
            border-radius: ${this._px(1)}; background-color: ${m('divider')};`;
        this._greeting.style = `${this._font(home.greeting_size, home.greeting_weight)} color: ${m('ink')};
            letter-spacing: ${this._px(home.greeting_size * home.greeting_tracking)};`;
        this._emptyTitle.style = `${this._font(TOKENS.type.h1.size, 700)} color: ${m('ink')};`;
        this._emptyText.style = `${this._font(28, 500)} color: ${m('ink_3')};`;
        this._empty.style = `spacing: ${this._px(18)};`;
        this._field.style = `padding: 0 ${this._px(home.field_padding_x)}; spacing: ${this._px(home.field_gap)};
            border-radius: ${this._px(home.field_radius)}; background-color: ${m('well')};
            border: ${this._px(1.5)} solid ${m('well_rim')};
            box-shadow: inset 0 ${this._px(2)} ${this._px(4)} ${m('well_shade')};`;
        this._fieldIcon.style = `icon-size: ${this._px(home.field_icon)}; color: ${m('ink_3')};`;
        this._entry.style = `${this._font(home.field_size, 500)} color: ${m('ink')}; caret-color: ${m('ink')};
            selected-color: ${m('key_ink')}; background: none; border: 0; box-shadow: none; padding: 0;
            min-height: 0; caret-size: ${this._px(3)};`;
        this._noResults.style = `${this._font(28, 500)} color: ${m('ink_3')};`;
        this._tileStyle = {
            label: this._font(tile.label_size, tile.label_weight),
            radius: this._px(tile.width * tile.corner_x),
        };
        this._buildTiles();
        this._tick();
    }

    // ── content ───────────────────────────────────────────────────────────

    _loadApps() {
        const entries = this._appSystem.get_installed()
            .filter(info => info.should_show?.() !== false && info.get_string)
            .map(info => tvEntry(info.get_id(), info.get_display_name() ?? info.get_name(),
                key => info.get_string(key)));
        this._apps = tvApps(entries);
        this._buildTiles();
    }

    _tileFile(app) {
        if (!app.tile) return null;
        const path = iconTheme.lookup_icon(app.tile, Math.round(TOKENS.tile.width * this._u), 0)?.get_filename?.();
        return path ? Gio.File.new_for_path(path) : null;
    }

    _makeTile(app) {
        const {tile} = TOKENS;
        const u = this._u;
        const width = tile.width * u, height = width * tile.aspect_height / tile.aspect_width;
        const cell = new St.Widget({layout_manager: new Clutter.FixedLayout(), reactive: true});
        const plate = new St.Widget({layout_manager: new Clutter.BinLayout(), width, height,
            pivot_point: new Graphene.Point({x: 0.5, y: 0.5})});
        const file = this._tileFile(app);
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor || 1;
        if (file) {
            plate.add_child(St.TextureCache.get_default().load_file_async(file,
                Math.round(width / scale), Math.round(height / scale), scale, plate.get_resource_scale()));
        } else {
            // An opted-in app without a wide tile: its icon on the bar material.
            const shellApp = this._appSystem.lookup_app(app.id);
            const icon = shellApp?.create_icon_texture(Math.round(height * 0.5 / scale));
            if (icon) {
                icon.x_align = icon.y_align = Clutter.ActorAlign.CENTER;
                plate.add_child(icon);
            }
            plate.style = `background-color: ${this._material('bar')};`;
        }
        const label = new St.Label({text: app.name, width, x_align: Clutter.ActorAlign.CENTER});
        label.clutter_text.ellipsize = Pango.EllipsizeMode.END;
        label.clutter_text.line_alignment = Pango.Alignment.CENTER;
        label.set_position(0, height + tile.label_gap * u);
        cell.add_child(plate);
        cell.add_child(label);
        cell.connect('button-release-event', () => {
            this._launch(app);
            return Clutter.EVENT_STOP;
        });
        return {app, cell, plate, label, baseStyle: plate.style ?? ''};
    }

    _buildTiles() {
        if (!this._home || !this._u) return;
        const build = (parent, apps, top) => {
            parent.destroy_all_children();
            const monitor = Main.layoutManager.primaryMonitor;
            const grid = tileGrid(apps.length, this._u, monitor.width, TOKENS);
            return apps.map((app, index) => {
                const made = this._makeTile(app);
                const cell = grid.cells[index];
                made.cell.set_position(cell.x, top + cell.y);
                made.rect = {x: cell.x, y: top + cell.y, width: cell.width, height: cell.height};
                parent.add_child(made.cell);
                return made;
            });
        };
        const {home} = TOKENS;
        this._tiles = build(this._grid, this._apps, home.apps_top * this._u);
        const monitor = Main.layoutManager.primaryMonitor;
        this._empty.visible = !this._apps.length;
        this._empty.set_width(monitor.width);
        this._empty.set_position(0, (monitor.height - this._empty.get_preferred_height(-1)[1]) / 2);
        this._resultTop = (home.search_top + home.field_height + home.search_gap) * this._u;
        this._drawResults();
    }

    _drawResults() {
        if (!this._home) return;
        this._results = searchTvApps(this._apps, this._query);
        this._resultGrid.destroy_all_children();
        const monitor = Main.layoutManager.primaryMonitor;
        const grid = tileGrid(this._results.length, this._u, monitor.width, TOKENS);
        this._resultTiles = this._results.map((app, index) => {
            const made = this._makeTile(app);
            const cell = grid.cells[index];
            made.cell.set_position(cell.x, this._resultTop + cell.y);
            made.rect = {x: cell.x, y: this._resultTop + cell.y, width: cell.width, height: cell.height};
            this._resultGrid.add_child(made.cell);
            return made;
        });
        const nothing = Boolean(this._query.trim()) && !this._results.length;
        this._noResults.visible = nothing;
        if (nothing) {
            this._noResults.text = _('Nothing installed matches “%s”').format(this._query.trim());
            this._noResults.set_width(monitor.width);
            this._noResults.set_position(0, this._resultTop);
        }
        if (this._focus.kind === 'result' && !this._resultTiles.length) this._focus = {kind: 'field'};
        this._draw();
    }

    _tick() {
        if (!this._home) return;
        const now = GLib.DateTime.new_now_local();
        this._time.text = this._clock.clock.trim();
        const weather = this._presentation._liveWidgets?.snapshot?.weather;
        const value = weather?.value && weather.value !== '—' ? weather.value : '';
        this._weather.visible = this._divider.visible = Boolean(value);
        if (value) {
            const glyph = weatherGlyph(weather.description, now.get_hour());
            this._weatherIcon.icon_name = glyph.icon;
            this._weatherIcon.style = `icon-size: ${this._px(TOKENS.bar.tab_icon + 2)};
                color: ${glyph.tint ? this._material(glyph.tint) : this._material('ink')};`;
            this._weatherText.text = value;
        }
        const name = GLib.get_real_name()?.split(' ')[0] || GLib.get_user_name();
        const part = {morning: _('Good morning, %s'), afternoon: _('Good afternoon, %s'),
            evening: _('Good evening, %s')}[greetingPart(now.get_hour())].format(name);
        const accent = TOKENS.materials.greeting_accent;
        this._greeting.clutter_text.set_markup(
            `${GLib.markup_escape_text(part, -1)}<span foreground="${accent}">.</span>`);
        this._placeGreeting();
        this._placeNow();
    }

    _placeGreeting() {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor || !this._greeting) return;
        const {layout, home} = TOKENS;
        const baseline = this._greeting.clutter_text.get_layout().get_baseline() / Pango.SCALE;
        const [, labelHeight] = this._greeting.get_preferred_height(-1);
        const textHeight = this._greeting.clutter_text.get_layout().get_pixel_extents()[1].height;
        // The label may pad its text; the baseline sits where the eye measures it.
        const inset = Math.max(0, (labelHeight - textHeight) / 2);
        this._greeting.set_position(layout.safe_x * this._u,
            monitor.height - home.greeting_baseline * this._u - baseline - inset);
    }

    _placeNow() {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor) return;
        const {bar, layout} = TOKENS;
        const [, width] = this._now.get_preferred_width(-1);
        const [, height] = this._now.get_preferred_height(-1);
        // The cluster is a bar item with a key's padding; its text stays
        // exactly where the clock is on every TV screen.
        const pad = TOKENS.quick_panel.cluster_padding_x * this._u;
        this._now.set_position(monitor.width - layout.safe_x * this._u - width + pad,
            (bar.top + bar.height / 2) * this._u - height / 2);
    }

    // ── focus and the remote ──────────────────────────────────────────────

    _open(page) {
        if (this._page === 'search' && page !== 'search') this._entry.set_text('');
        this._page = page;
        for (const name of PAGES) {
            const actor = this._pages[name];
            const on = name === page;
            actor.remove_all_transitions();
            actor.reactive = on;
            if (on) actor.show();
            actor.ease({opacity: on ? 255 : 0, duration: TOKENS.motion.page_ms,
                mode: Clutter.AnimationMode.EASE_OUT_QUAD,
                onComplete: () => { if (!on && this._page !== name) actor.hide(); }});
        }
    }

    _items() {
        if (this._page === 'apps') return this._tiles.map((tile, index) => ({kind: 'tile', index, ...tile.rect}));
        if (this._page === 'search') {
            const field = {kind: 'field', x: this._field.x, y: this._field.y,
                width: this._field.width, height: this._field.height};
            return [field, ...(this._resultTiles ?? []).map((tile, index) => ({kind: 'result', index, ...tile.rect}))];
        }
        return [];
    }

    _focusedItem(items) {
        return items.find(item => item.kind === this._focus.kind && item.index === this._focus.index) ??
            items.find(item => item.kind === this._focus.kind && this._focus.kind === 'field');
    }

    _move(direction) {
        const tab = PAGES.indexOf(this._page);
        if (this._focus.kind === 'tab') {
            if (direction === 'right' && this._focus.index === PAGES.length - 1) {
                // Past the last tab: the cluster at the bar's far right.
                this._focus = {kind: 'cluster'};
            } else if (direction === 'left' || direction === 'right') {
                // Moving along the bar opens the page; no OK needed.
                const next = Math.max(0, Math.min(PAGES.length - 1, this._focus.index + (direction === 'right' ? 1 : -1)));
                if (next !== this._focus.index) {
                    this._open(PAGES[next]);
                    this._focus = {kind: 'tab', index: next};
                }
            } else if (direction === 'down') {
                const items = this._items();
                if (items.length) {
                    const from = {x: this._track.x + this._tabs[tab].tab.x, y: this._track.y,
                        width: this._tabs[tab].tab.width, height: this._track.height};
                    const target = nearest(items, from, 'down');
                    if (target) this._focus = {kind: target.kind, index: target.index};
                }
            }
            return;
        }
        if (this._focus.kind === 'cluster') {
            if (direction === 'left') {
                this._open(PAGES[PAGES.length - 1]);
                this._focus = {kind: 'tab', index: PAGES.length - 1};
            }
            return;
        }
        const items = this._items();
        const current = this._focusedItem(items);
        const target = current ? nearest(items, current, direction) : null;
        if (target) this._focus = {kind: target.kind, index: target.index};
        else if (direction === 'up') this._focus = {kind: 'tab', index: tab};
    }

    _ok() {
        const {kind, index} = this._focus;
        if (kind === 'cluster') this._quick?.open('bar');
        else if (kind === 'tab' && this._page === 'search') this._focus = {kind: 'field'};
        else if (kind === 'tile') this._launch(this._tiles[index].app);
        else if (kind === 'result') this._launch(this._resultTiles[index].app);
    }

    _back() {
        if (this._focus.kind === 'cluster') this._focus = {kind: 'tab', index: PAGES.indexOf(this._page)};
        else if (this._focus.kind !== 'tab') this._focus = {kind: 'tab', index: PAGES.indexOf(this._page)};
        else if (this._page !== 'home') {
            this._open('home');
            this._focus = {kind: 'tab', index: 0};
        }
    }

    _key(event) {
        const action = remoteAction(Clutter.keyval_name(event.get_key_symbol()) ?? '');
        if (!action) return Clutter.EVENT_PROPAGATE;
        // OK is held down for a long press: its key repeat is not more OKs (nor Back more Backs).
        if (action === 'ok' && (event.get_flags() & Clutter.EventFlags.REPEATED)) return Clutter.EVENT_STOP;
        if (!repeatAllowed(action) && (event.get_flags() & Clutter.EventFlags.REPEATED)) return Clutter.EVENT_STOP;
        if (action === 'ok') this._ok();
        else if (action === 'back') this._back();
        else this._move(action);
        this._draw();
        return Clutter.EVENT_STOP;
    }

    // The field types (a real keyboard types straight in); Up, Down and Escape
    // move the remote's focus, as everywhere else.
    _fieldKey(event) {
        const name = Clutter.keyval_name(event.get_key_symbol()) ?? '';
        if (name === 'Up') this._focus = {kind: 'tab', index: PAGES.indexOf('search')};
        else if (name === 'Down' && this._resultTiles?.length) this._focus = {kind: 'result', index: 0};
        else if (name === 'Escape') this._focus = {kind: 'tab', index: PAGES.indexOf('search')};
        else if (name === 'Return' || name === 'KP_Enter') {
            if (this._resultTiles?.length) this._launch(this._resultTiles[0].app);
        } else return Clutter.EVENT_PROPAGATE;
        this._draw();
        return Clutter.EVENT_STOP;
    }

    // One bright thing: the focus is the key; the open tab is a chip while the
    // focus is elsewhere; a focused tile lifts 1.06 with a white ring.
    _draw() {
        if (!this._home || !this._u) return;
        const {bar, tile} = TOKENS;
        const m = name => this._material(name);
        const open = PAGES.indexOf(this._page);
        this._tabs.forEach(({tab, text, icon}, index) => {
            const focused = this._focus.kind === 'tab' && this._focus.index === index;
            const chosen = index === open && !focused;
            const base = `height: ${this._px(bar.tab_height)}; padding: 0 ${this._px(bar.tab_padding_x)};
                border-radius: ${this._px(bar.tab_radius)};`;
            const ink = focused ? m('key_ink') : chosen ? m('ink') : m('ink_2');
            tab.style = base + (focused
                ? `background-gradient-direction: vertical; background-gradient-start: ${m('key_top')};
                   background-gradient-end: ${m('key_bottom')}; border: ${this._px(1.5)} solid ${m('key_rim')};
                   box-shadow: 0 ${this._px(3)} ${this._px(10)} ${m('key_drop')};`
                : chosen
                    ? `background-color: ${m('chip')}; border: ${this._px(1.5)} solid ${m('chip_rim')};
                       box-shadow: 0 ${this._px(2)} ${this._px(5)} ${m('chip_drop')};`
                    : `border: ${this._px(1.5)} solid transparent;`);
            text.style = `${this._font(bar.tab_size, bar.tab_weight)} color: ${ink};`;
            if (icon) icon.style = `icon-size: ${this._px(bar.tab_icon)}; color: ${ink};`;
        });
        const paint = (tiles, kind) => (tiles ?? []).forEach((made, index) => {
            const focused = this._focus.kind === kind && this._focus.index === index;
            made.plate.remove_all_transitions();
            made.plate.ease({scale_x: focused ? tile.focus_scale : 1, scale_y: focused ? tile.focus_scale : 1,
                duration: TOKENS.motion.focus_ms, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
            made.plate.style = `${made.baseStyle} border-radius: ${this._tileStyle.radius};
                box-shadow: ${focused ? `0 0 0 ${this._px(tile.ring)} ${m('ring')}`
        : `0 ${this._px(16)} ${this._px(40)} ${m('tile_drop')}`};`;
            made.label.style = `${this._tileStyle.label} color: ${focused ? m('ink') : m('ink_2')};`;
            if (focused) made.cell.get_parent()?.set_child_above_sibling(made.cell, null);
        });
        this._drawCluster();
        paint(this._tiles, 'tile');
        paint(this._resultTiles, 'result');
        const fieldFocused = this._focus.kind === 'field';
        this._field.style = this._field.style.replace(/box-shadow:[^;]*;/,
            fieldFocused ? `box-shadow: 0 0 0 ${this._px(4)} ${m('ink')};`
                : `box-shadow: inset 0 ${this._px(2)} ${this._px(4)} ${m('well_shade')};`);
        if (fieldFocused && this._page === 'search') {
            this._entry.clutter_text.set_editable(true);
            if (!Main.modalCount) this._entry.grab_key_focus();
        } else if (this._home.visible) {
            this._entry.clutter_text.set_editable(false);
            if (!Main.modalCount) this._home.grab_key_focus();
        }
    }

    // The cluster (weather | time; later the user's picture) is the quick
    // panel's bar item: the key while the remote is on it, a chip while the
    // panel it opened is up, plain otherwise.
    _drawCluster() {
        const {bar, quick_panel: q} = TOKENS;
        const m = name => this._material(name);
        const focused = this._focus.kind === 'cluster' && !this._quick?.isOpen;
        // The cluster owns the panel however it was opened (the cluster or a long press of Home).
        const owner = Boolean(this._quick?.isOpen);
        this._now.style = `spacing: ${this._px(bar.now_gap)}; min-height: ${this._px(q.cluster_height)};
            padding: 0 ${this._px(q.cluster_padding_x)}; border-radius: ${this._px(q.cluster_radius)};` + (focused
            ? `background-gradient-direction: vertical; background-gradient-start: ${m('key_top')};
               background-gradient-end: ${m('key_bottom')}; border: ${this._px(1.5)} solid ${m('key_rim')};
               box-shadow: 0 ${this._px(3)} ${this._px(10)} ${m('key_drop')};`
            : owner
                ? `background-gradient-direction: none; background-color: ${m('chip')};
                   border: ${this._px(1.5)} solid ${m('chip_rim')}; box-shadow: 0 ${this._px(2)} ${this._px(5)} ${m('chip_drop')};`
                : `background-gradient-direction: none; border: ${this._px(1.5)} solid transparent; box-shadow: none;`);
        const ink = focused ? m('key_ink') : m('ink');
        for (const label of [this._weatherText, this._time])
            label.style = label.style.replace(/color:[^;]*;/, `color: ${ink};`);
        this._divider.style = this._divider.style.replace(/background-color:[^;]*;/,
            `background-color: ${focused ? 'rgba(0, 0, 0, 0.25)' : m('divider')};`);
    }

    // The panel shut: opened from the cluster, the focus goes back to it.
    _quickClosed(origin) {
        if (!this.active || !this._home?.visible) return;
        if (origin === 'bar') this._focus = {kind: 'cluster'};
        this._draw();
    }

    _launch(app) {
        const shellApp = this._appSystem.lookup_app(app.id);
        if (!shellApp) return;
        // Raised if running (Back minimised it), started if not; the window
        // goes full screen as it maps.
        shellApp.activate();
    }

    destroy() {
        this.setActive(false);
        for (const [object, id] of this._signals) object.disconnect(id);
        this._signals = [];
    }
}
