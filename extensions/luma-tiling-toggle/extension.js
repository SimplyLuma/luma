// SPDX-License-Identifier: Apache-2.0

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';
import Pango from 'gi://Pango';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';

const TILING_SHELL_UUID = 'tilingshell@ferrarodomenico.com';
const TILING_SCHEMA_ID = 'org.gnome.shell.extensions.tilingshell';
const EXTENSIONS_BUS_NAME = 'org.gnome.Shell';
const EXTENSIONS_OBJECT_PATH = '/org/gnome/Shell';
const EXTENSIONS_INTERFACE = 'org.gnome.Shell.Extensions';
const TILING_BUS_NAME = 'org.gnome.Shell';
const TILING_OBJECT_PATH = '/org/gnome/Shell/Extensions/TilingShell';
const TILING_INTERFACE = 'org.gnome.Shell.Extensions.TilingShell';
const PREVIEW_WIDTH = 68;
const PREVIEW_HEIGHT = 40;
const MAX_PREVIEWS_PER_ROW = 3;
const PAGE_SIZE = 6;

// Company words a display's EDID name carries that a person never says.
const VENDOR_NOISE = /\b(electronics?|electric|company|corporation|corp|inc|co|ltd|limited|technology|technologies|computer|display|displays|international|group|gmbh|ag|s\.?a)\b\.?,?/gi;

// The name a person would use for a display: "ThinkPad" for the laptop's own
// panel, "LG 49″" for the monitor beside it -- never "Display 2", never a
// model number.
function builtinName() {
    for (const key of ['product_family', 'product_version', 'product_name']) {
        try {
            const [ok, bytes] = GLib.file_get_contents(`/sys/class/dmi/id/${key}`);
            const word = ok ? new TextDecoder().decode(bytes).trim().split(/\s+/)[0] : '';
            if (/^[A-Za-z][A-Za-z-]{2,}$/.test(word) && !/^(to|default|system|none|not)$/i.test(word))
                return word;
        } catch (_error) {
        }
    }
    return 'Built-in';
}

function shortDisplayName(displayName, builtin) {
    if (builtin)
        return builtinName();
    const name = (displayName || '').replace(VENDOR_NOISE, '').replace(/"/g, '″')
        .replace(/\s+/g, ' ').trim();
    return name || displayName || '';
}

function createTilingSettings() {
    const schemaDirectories = [
        GLib.build_filenamev([
            GLib.get_user_data_dir(),
            'gnome-shell',
            'extensions',
            TILING_SHELL_UUID,
            'schemas',
        ]),
        `/usr/share/gnome-shell/extensions/${TILING_SHELL_UUID}/schemas`,
    ];

    for (const directory of schemaDirectories) {
        if (!GLib.file_test(directory, GLib.FileTest.IS_DIR))
            continue;

        const source = Gio.SettingsSchemaSource.new_from_directory(
            directory,
            Gio.SettingsSchemaSource.get_default(),
            false);
        const schema = source.lookup(TILING_SCHEMA_ID, true);
        if (schema)
            return new Gio.Settings({settings_schema: schema});
    }

    throw new Error(`GSettings schema ${TILING_SCHEMA_ID} not found`);
}

// The previews are drawn by stylesheet.css in the appearance mode Quick
// Options is showing (its luma-surface-* class), from the Shell's ink ledger:
// a neutral frame and tiles for every layout, and the one state slate for the
// chosen one (ADR-043). They used to carry white inline colours meant for a
// dark panel, which is why the picker read as dark in every mode.
function createLayoutPreview(layout, selected, onClicked, name) {
    const button = new St.Button({
        accessible_name: `${name}, ${layout.tiles.length} ${layout.tiles.length === 1 ? 'zone' : 'zones'}${selected ? ', current layout' : ''}`,
        can_focus: true,
        checked: selected,
        reactive: true,
        style_class: 'luma-tiling-preview',
        toggle_mode: false,
        track_hover: true,
    });
    const preview = new St.Widget({
        height: PREVIEW_HEIGHT,
        layout_manager: new Clutter.FixedLayout(),
        style_class: 'luma-tiling-preview-frame',
        width: PREVIEW_WIDTH,
    });

    for (const tile of layout.tiles) {
        const gap = 2;
        const x = Math.round(tile.x * PREVIEW_WIDTH) + gap;
        const y = Math.round(tile.y * PREVIEW_HEIGHT) + gap;
        const width = Math.max(3, Math.round(tile.width * PREVIEW_WIDTH) - gap * 2);
        const height = Math.max(3, Math.round(tile.height * PREVIEW_HEIGHT) - gap * 2);
        const actor = new St.Widget({style_class: 'luma-tiling-preview-tile'});
        actor.set_position(x, y);
        actor.set_size(width, height);
        preview.add_child(actor);
    }

    const card = new St.BoxLayout({vertical: true, style_class: 'luma-tiling-card', x_align: Clutter.ActorAlign.CENTER});
    card.add_child(preview);
    const label = new St.Label({text: name, width: PREVIEW_WIDTH,
        style_class: 'luma-tiling-card-name', x_align: Clutter.ActorAlign.CENTER});
    label.clutter_text.ellipsize = Pango.EllipsizeMode.END;
    card.add_child(label);
    button.set_child(card);
    button._layoutId = layout.id;
    button.connect('clicked', onClicked);
    return button;
}

const TilingToggle = GObject.registerClass(
class TilingToggle extends QuickSettings.QuickMenuToggle {
    constructor() {
        super({
            title: 'Tiling',
            iconName: 'lumaui-columns-2-symbolic',
            toggleMode: true,
        });

        // Quick Options titles a sheet in sentence case, with a muted count
        // on the other side rather than a repeat of the pill's name and icon.
        this.menu.setHeader('view-grid-symbolic', 'Window layouts');

        // A grid is a vertical section, not a single menu row. A row may
        // shrink to its minimum height while its tall child still paints at
        // natural height, putting the actions over the last monitor's tiles.
        this._monitorIndex = Main.layoutManager.primaryIndex;
        this._page = 0;
        this._query = '';
        this._searchItem = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        this._search = new St.Entry({hint_text: 'Find a layout…', can_focus: true,
            x_expand: true, style_class: 'luma-tiling-search', accessible_name: 'Find a layout'});
        this._search.set_primary_icon(new St.Icon({icon_name: 'edit-find-symbolic', icon_size: 16}));
        this._search.connect('secondary-icon-clicked', () => {
            this._search.set_text('');
            this._search.grab_key_focus();
        });
        this._searchItem.add_child(this._search);
        this.menu.addMenuItem(this._searchItem);
        this._search.clutter_text.connect('text-changed', () => {
            this._query = this._search.get_text().trim().toLocaleLowerCase();
            this._clearSearch = this._query ? new St.Icon({icon_name: 'edit-clear-symbolic',
                icon_size: 16, accessible_name: 'Clear search'}) : null;
            this._search.set_secondary_icon(this._clearSearch);
            this._page = 0;
            this._renderLayouts();
        });
        this._layoutsItem = new PopupMenu.PopupMenuSection();
        this._layoutsBox = this._layoutsItem.box;
        this._layoutsBox.add_style_class_name('luma-tiling-layouts-box');
        this._layoutsBox.x_expand = true;
        this._layoutsBox.set_style('background-color: transparent;');
        this.menu.addMenuItem(this._layoutsItem);
        // The actions below get their own band, set off by a hairline with
        // room on both sides, so they can never be drawn over the last row of
        // layouts (stylesheet.css, .luma-tiling-actions-rule).
        const rule = new PopupMenu.PopupSeparatorMenuItem();
        rule.add_style_class_name('luma-tiling-actions-rule');
        this.menu.addMenuItem(rule);

        this._editLayoutsItem = this.menu.addAction(
            'Edit layouts…', () => this._invokeEditor('openLayoutEditor'));
        this._newLayoutItem = this.menu.addAction(
            'New layout…', () => this._invokeEditor('newLayout'));
        const settingsItem = this._settingsItem = this.menu.addAction(
            'Tiling settings', () => this._openPreferences());
        // Plain rows under the hairline, like every other sheet's links.
        for (const item of [this._editLayoutsItem, this._newLayoutItem, settingsItem]) {
            item.add_style_class_name('luma-sheet-link');
            item.add_style_class_name('luma-tiling-action');
            item.set_style('min-height: 24px; padding: 8px 10px;');
        }
        for (const [item, icon] of [[this._newLayoutItem, 'list-add-symbolic'],
            [this._editLayoutsItem, 'document-edit-symbolic'], [settingsItem, 'emblem-system-symbolic']])
            item.insert_child_at_index(new St.Icon({icon_name: icon, icon_size: 16}), 0);
        settingsItem.visible = Main.sessionMode.allowSettings;
        this._editLayoutsItem.visible = Main.sessionMode.allowSettings;
        this._newLayoutItem.visible = Main.sessionMode.allowSettings;
        this.menu._settingsActions[TILING_SHELL_UUID] = settingsItem;

        this._shellSettings = new Gio.Settings({schemaId: 'org.gnome.shell'});
        this._tilingSettings = createTilingSettings();
        this._cancellable = new Gio.Cancellable();
        this._syncing = false;
        this._applying = false;
        this._settingsChangedId = this._shellSettings.connect(
            'changed::enabled-extensions', () => this._syncFromSettings());
        this._disabledChangedId = this._shellSettings.connect(
            'changed::disabled-extensions', () => this._syncFromSettings());
        this._autoTilingChangedId = this._tilingSettings.connect(
            'changed::enable-autotiling', () => this._syncFromSettings());
        this._layoutsChangedId = this._tilingSettings.connect(
            'changed::layouts-json', () => this._renderLayouts());
        this._selectionChangedId = this._tilingSettings.connect(
            'changed::selected-layouts', () => this._renderLayouts());
        this._workspaceChangedId = global.workspace_manager.connect(
            'active-workspace-changed', () => this._renderLayouts());
        this._monitorsChangedId = Main.layoutManager.connect(
            'monitors-changed', () => {
                this._renderLayouts();
                this._loadDisplayNames();
            });
        this._checkedChangedId = this.connect(
            'notify::checked', () => this._applyRuntimeState());

        // The detail opens in its own layer, outside the Quick Options surface
        // that carries the appearance mode's luma-surface-* class, so the
        // stylesheet's per-mode rules never reached the previews: their tiles
        // had no colour at all and every layout was blank. Carry the mode
        // class onto the detail, and follow it when the mode changes.
        this.menu.connect('open-state-changed', (_menu, open) => {
            if (open) {
                this._query = '';
                this._search.set_text('');
                this._showCurrentPage();
            }
            this._syncSurface();
        });
        this._surfaceSyncId = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._surfaceSyncId = 0;
            const quickBox = Main.panel.statusArea.quickSettings?.menu?.box;
            quickBox?.connectObject('notify::style-class', () => this._syncSurface(), this);
            this._syncSurface();
            return GLib.SOURCE_REMOVE;
        });

        this._displayNames = [];
        this._syncFromSettings();
        this._renderLayouts();
        this._loadDisplayNames();
    }

    _loadDisplayNames() {
        Gio.DBus.session.call(
            'org.gnome.Mutter.DisplayConfig',
            '/org/gnome/Mutter/DisplayConfig',
            'org.gnome.Mutter.DisplayConfig',
            'GetCurrentState',
            null,
            null,
            Gio.DBusCallFlags.NONE,
            -1,
            this._cancellable,
            (connection, result) => {
                try {
                    const [, monitors, logicalMonitors] =
                        connection.call_finish(result).deepUnpack();
                    const names = new Map();
                    for (const [[connector], , properties] of monitors) {
                        names.set(connector, shortDisplayName(
                            properties['display-name']?.deepUnpack(),
                            properties['is-builtin']?.deepUnpack()));
                    }
                    this._displayNames = Main.layoutManager.monitors.map(monitor => {
                        const logical = logicalMonitors.find(([x, y]) =>
                            x === monitor.x && y === monitor.y);
                        const connector = logical?.[5]?.[0]?.[0];
                        return names.get(connector) ?? '';
                    });
                    // Two identical monitors keep their numbers.
                    const counts = new Map();
                    for (const name of this._displayNames)
                        counts.set(name, (counts.get(name) ?? 0) + 1);
                    this._displayNames = this._displayNames.map((name, index) =>
                        counts.get(name) > 1 ? `${name} ${index + 1}` : name);
                    this._renderLayouts();
                } catch (error) {
                    console.error(`Luma Tiling: display names unavailable: ${error.message}`);
                }
            });
    }

    _syncFromSettings() {
        // The automatic-placement setting changes before the asynchronous
        // extension-manager reply. Preserve the requested switch state until
        // both settings describe the completed operation.
        if (this._applying)
            return;
        const extensions = this._shellSettings.get_strv('enabled-extensions');
        const disabled = this._shellSettings.get_strv('disabled-extensions');

        this._syncing = true;
        this.checked = extensions.includes(TILING_SHELL_UUID) &&
            !disabled.includes(TILING_SHELL_UUID) &&
            this._tilingSettings.get_boolean('enable-autotiling');
        this._syncing = false;
    }

    _callExtensionService(method, callback = null) {
        Gio.DBus.session.call(
            EXTENSIONS_BUS_NAME,
            EXTENSIONS_OBJECT_PATH,
            EXTENSIONS_INTERFACE,
            method,
            new GLib.Variant('(s)', [TILING_SHELL_UUID]),
            method === 'LaunchExtensionPrefs' ? null : new GLib.VariantType('(b)'),
            Gio.DBusCallFlags.NONE,
            -1,
            this._cancellable,
            (connection, result) => {
                try {
                    const reply = connection.call_finish(result);
                    callback?.(reply);
                } catch (error) {
                    if (!this._shellSettings || this._cancellable.is_cancelled())
                        return;
                    console.error(`Luma Tiling: ${method} failed: ${error.message}`);
                    callback?.(null);
                    this._syncFromSettings();
                }
            });
    }

    _callTilingShell(method, parameters = null) {
        Gio.DBus.session.call(
            TILING_BUS_NAME,
            TILING_OBJECT_PATH,
            TILING_INTERFACE,
            method,
            parameters,
            null,
            Gio.DBusCallFlags.NONE,
            -1,
            this._cancellable,
            (connection, result) => {
                try {
                    connection.call_finish(result);
                } catch (error) {
                    console.error(`Luma Tiling: ${method} failed: ${error.message}`);
                }
            });
    }

    _withTilingEnabled(callback) {
        if (this.checked) {
            callback();
            return;
        }

        this._callExtensionService('EnableExtension', reply => {
            const [success] = reply?.deepUnpack() ?? [false];
            if (!success) {
                console.error('Luma Tiling: EnableExtension was rejected by GNOME Shell');
                this._syncFromSettings();
                return;
            }

            if (!this._tilingSettings.set_boolean('enable-autotiling', true)) {
                this._syncFromSettings();
                return;
            }
            this._syncFromSettings();
            callback();
        });
    }

    _applyRuntimeState() {
        if (this._syncing || this._applying)
            return;

        const enabled = this.checked;
        const previous = this._tilingSettings.get_boolean('enable-autotiling');
        this._applying = true;
        if (!this._tilingSettings.set_boolean('enable-autotiling', enabled)) {
            this._applying = false;
            this._syncFromSettings();
            return;
        }
        const method = enabled ? 'EnableExtension' : 'DisableExtension';
        this._callExtensionService(method, reply => {
            const [success] = reply?.deepUnpack() ?? [false];
            if (!success) {
                console.error(`Luma Tiling: ${method} was rejected by GNOME Shell`);
                this._tilingSettings.set_boolean('enable-autotiling', previous);
                this._applying = false;
                this._syncFromSettings();
                return;
            }
            this._applying = false;
            if (this.checked !== enabled)
                this._applyRuntimeState();
            else
                this._syncFromSettings();
        });
    }

    _getLayouts() {
        try {
            const layouts = JSON.parse(this._tilingSettings.get_string('layouts-json'));
            return layouts.filter(layout =>
                layout && typeof layout.id === 'string' &&
                Array.isArray(layout.tiles) &&
                layout.tiles.length > 0 && layout.tiles.every(tile =>
                    tile && ['x', 'y', 'width', 'height'].every(key => Number.isFinite(tile[key])) &&
                    tile.x >= 0 && tile.y >= 0 && tile.width > 0 && tile.height > 0 &&
                    tile.x + tile.width <= 1.001 && tile.y + tile.height <= 1.001));
        } catch (error) {
            console.error(`Luma Tiling: invalid layouts-json: ${error.message}`);
            return [];
        }
    }

    _selectedId(layouts) {
        return this._tilingSettings.get_value('selected-layouts').deepUnpack()
            [global.workspace_manager.get_active_workspace_index()]?.[this._monitorIndex] ?? layouts[0]?.id;
    }

    _showCurrentPage() {
        const layouts = this._getLayouts();
        this._monitorIndex = Math.max(0, Math.min(this._monitorIndex, Main.layoutManager.monitors.length - 1));
        this._page = Math.floor(Math.max(0, layouts.findIndex(l => l.id === this._selectedId(layouts))) / PAGE_SIZE);
        this._renderLayouts();
    }

    _navButton(icon, name, callback, sensitive = true) {
        const button = new St.Button({style_class: 'luma-tiling-nav', can_focus: sensitive,
            reactive: sensitive, track_hover: true, accessible_name: name,
            child: new St.Icon({icon_name: icon, icon_size: 16})});
        if (!sensitive) button.add_style_pseudo_class('insensitive');
        button.opacity = sensitive ? 255 : 97;
        button.connect('clicked', callback);
        button.set_style('min-width: 24px; min-height: 24px; padding: 8px;');
        return button;
    }

    _renderLayouts(focus = null) {
        this._layoutsBox.destroy_all_children();
        const layouts = this._getLayouts();
        this._monitorIndex = Math.max(0, Math.min(this._monitorIndex, Main.layoutManager.monitors.length - 1));
        this.menu.setCount?.(layouts.length === 1 ? '1 layout' : `${layouts.length} layouts`);
        this._searchItem.visible = layouts.length >= 12 || !!this._query;
        const monitorCount = Main.layoutManager.monitors.length;
        const display = new St.BoxLayout({style_class: 'luma-tiling-display-row', x_expand: true});
        const changeDisplay = delta => {
            this._monitorIndex = (this._monitorIndex + delta + monitorCount) % monitorCount;
            if (this._query) this._renderLayouts();
            else this._showCurrentPage();
            this._displayNext?.grab_key_focus();
        };
        if (monitorCount > 1)
            display.add_child(this._navButton('go-previous-symbolic', 'Previous display', () => changeDisplay(-1)));
        const title = new St.BoxLayout({vertical: true, x_expand: true, y_align: Clutter.ActorAlign.CENTER});
        const displayLabel = new St.Label({style_class: 'luma-tiling-display-name', x_expand: true,
            text: this._displayNames?.[this._monitorIndex] || `Display ${this._monitorIndex + 1}`});
        displayLabel.clutter_text.ellipsize = Pango.EllipsizeMode.END;
        title.add_child(displayLabel);
        title.add_child(new St.Label({style_class: 'luma-tiling-caption',
            text: monitorCount > 1 ? `Display ${this._monitorIndex + 1} of ${monitorCount}` : 'Choose a window layout'}));
        display.add_child(title);
        this._displayNext = null;
        if (monitorCount > 1) {
            this._displayNext = this._navButton('go-next-symbolic', 'Next display', () => changeDisplay(1));
            display.add_child(this._displayNext);
        }
        this._layoutsBox.add_child(display);
        const entries = layouts.map((layout, i) => ({layout, name: layout.name?.trim?.() || `Layout ${i + 1}`}));
        const selectedId = this._selectedId(layouts);
        const current = entries.find(e => e.layout.id === selectedId);
        if (current) {
            const currentLabel = new St.Label({text: `Current · ${current.name}`, width: 220});
            currentLabel.clutter_text.ellipsize = Pango.EllipsizeMode.END;
            const currentButton = new St.Button({child: currentLabel, can_focus: true,
                reactive: true, track_hover: true, style_class: 'luma-tiling-current',
                accessible_name: `Show current layout, ${current.name}`});
            currentButton.connect('clicked', () => {
                this._query = ''; this._search.set_text(''); this._showCurrentPage();
                this._previewButtons.find(b => b.checked)?.grab_key_focus();
            });
            this._layoutsBox.add_child(currentButton);
        }
        const filtered = entries.filter(e => !this._query ||
            `${e.name} ${e.layout.tiles.length} zones`.toLocaleLowerCase().includes(this._query));
        const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
        this._page = Math.max(0, Math.min(this._page, pages - 1));
        const start = this._page * PAGE_SIZE;
        const shown = filtered.slice(start, start + PAGE_SIZE);
        this._previewButtons = [];
        if (!shown.length) {
            this._layoutsBox.add_child(new St.Label({style_class: 'luma-tiling-empty',
                text: layouts.length ? 'No matching layouts' : 'Create your first layout below'}));
        } else {
            const perRow = Math.ceil(shown.length / Math.ceil(shown.length / MAX_PREVIEWS_PER_ROW));
            for (let i = 0; i < shown.length; i += perRow) {
                const row = new St.BoxLayout({style_class: 'luma-tiling-layouts-row',
                    x_align: Clutter.ActorAlign.CENTER, x_expand: true});
                for (const {layout, name} of shown.slice(i, i + perRow)) {
                    const button = createLayoutPreview(layout, layout.id === selectedId,
                        () => this._selectLayout(this._monitorIndex, layout.id), name);
                    this._previewButtons.push(button);
                    row.add_child(button);
                }
                this._layoutsBox.add_child(row);
            }
        }
        const pager = new St.BoxLayout({style_class: 'luma-tiling-pager', x_expand: true});
        this._pagePrevious = this._navButton('go-previous-symbolic', 'Previous layouts',
            () => {this._page--; this._renderLayouts('previous');}, this._page > 0);
        this._pageNext = this._navButton('go-next-symbolic', 'Next layouts',
            () => {this._page++; this._renderLayouts('next');}, this._page < pages - 1);
        pager.add_child(this._pagePrevious);
        pager.add_child(new St.Label({style_class: 'luma-tiling-caption', x_expand: true,
            x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER,
            text: filtered.length ? `${start + 1}–${start + shown.length} of ${filtered.length}` : '0 layouts'}));
        pager.add_child(this._pageNext);
        pager.visible = filtered.length > PAGE_SIZE;
        this._layoutsBox.add_child(pager);
        this._editLayoutsItem.setSensitive(layouts.length > 0);
        this._editLayoutsItem.opacity = layouts.length ? 255 : 97;
        this.menu.actor?.queue_relayout();
        if (focus) {
            const preferred = focus === 'next' ? this._pageNext : this._pagePrevious;
            (preferred.can_focus ? preferred : (this._pageNext.can_focus ? this._pageNext :
                this._pagePrevious.can_focus ? this._pagePrevious : this._previewButtons[0]))?.grab_key_focus();
        }
    }

    _syncSurface() {
        const source = Main.panel.statusArea.quickSettings?.menu?.box;
        const mode = source?.style_class?.match(/\bluma-surface-(light|dark|frost|glass)\b/)?.[1] ?? null;
        for (const name of ['light', 'dark', 'frost', 'glass']) {
            if (name === mode)
                this.menu.box.add_style_class_name(`luma-surface-${name}`);
            else
                this.menu.box.remove_style_class_name(`luma-surface-${name}`);
        }
        const ink = this.menu.box.get_theme_node().get_foreground_color();
        const light = ink.red + ink.green + ink.blue < 384;
        const actionInk = light ? '#34383d' : '#e7e9e8';
        for (const item of [this._editLayoutsItem, this._newLayoutItem, this._settingsItem]) {
            // Older Shell palettes have highly specific sheet-link selectors.
            // Keep both label and glyph on the actual material's ink.
            item.set_style(`min-height: 24px; padding: 8px 10px; color: ${actionInk};`);
            item.label.set_style(`color: ${actionInk};`);
            for (const child of item.get_children())
                if (child instanceof St.Icon) child.set_style(`color: ${actionInk};`);
        }
        this.menu.box[light ? 'add_style_class_name' : 'remove_style_class_name']('luma-tiling-light-material');
        this.menu.box[light ? 'remove_style_class_name' : 'add_style_class_name']('luma-tiling-dark-material');
    }

    _selectLayout(monitorIndex, layoutId) {
        this._withTilingEnabled(() => this._callTilingShell(
            'selectLayout',
            new GLib.Variant('(is)', [monitorIndex, layoutId])));
        this.menu.close();
    }

    _invokeEditor(method) {
        this.menu.close();
        Main.panel.statusArea.quickSettings?.menu?.close();
        this._withTilingEnabled(() => this._callTilingShell(method));
    }

    _openPreferences() {
        this._callExtensionService('LaunchExtensionPrefs');
    }

    destroy() {
        this._cancellable.cancel();
        if (this._surfaceSyncId)
            GLib.source_remove(this._surfaceSyncId);
        this._surfaceSyncId = 0;
        Main.panel.statusArea.quickSettings?.menu?.box?.disconnectObject(this);
        if (this._settingsChangedId)
            this._shellSettings.disconnect(this._settingsChangedId);
        if (this._disabledChangedId)
            this._shellSettings.disconnect(this._disabledChangedId);
        if (this._autoTilingChangedId)
            this._tilingSettings.disconnect(this._autoTilingChangedId);
        if (this._layoutsChangedId)
            this._tilingSettings.disconnect(this._layoutsChangedId);
        if (this._selectionChangedId)
            this._tilingSettings.disconnect(this._selectionChangedId);
        if (this._workspaceChangedId)
            global.workspace_manager.disconnect(this._workspaceChangedId);
        if (this._monitorsChangedId)
            Main.layoutManager.disconnect(this._monitorsChangedId);
        if (this._checkedChangedId)
            this.disconnect(this._checkedChangedId);

        this._shellSettings = null;
        this._tilingSettings = null;
        super.destroy();
    }
});

const TilingIndicator = GObject.registerClass(
class TilingIndicator extends QuickSettings.SystemIndicator {
    constructor() {
        super();
        this.quickSettingsItems.push(new TilingToggle());
    }

    destroy() {
        this.quickSettingsItems.forEach(item => item.destroy());
        super.destroy();
    }
});

export default class LumaTilingToggleExtension extends Extension {
    enable() {
        this._indicator = new TilingIndicator();
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
