// SPDX-License-Identifier: Apache-2.0

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';

const TILING_SHELL_UUID = 'tilingshell@ferrarodomenico.com';
const TILING_SCHEMA_ID = 'org.gnome.shell.extensions.tilingshell';
const EXTENSIONS_BUS_NAME = 'org.gnome.Shell.Extensions';
const EXTENSIONS_OBJECT_PATH = '/org/gnome/Shell/Extensions';
const EXTENSIONS_INTERFACE = 'org.gnome.Shell.Extensions';
const TILING_BUS_NAME = 'org.gnome.Shell';
const TILING_OBJECT_PATH = '/org/gnome/Shell/Extensions/TilingShell';
const TILING_INTERFACE = 'org.gnome.Shell.Extensions.TilingShell';
const PREVIEW_WIDTH = 68;
const PREVIEW_HEIGHT = 40;
const MAX_PREVIEWS_PER_ROW = 4;

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
function createLayoutPreview(layout, selected, onClicked) {
    const button = new St.Button({
        accessible_name: layout.id,
        can_focus: true,
        checked: selected,
        reactive: true,
        style_class: 'luma-tiling-preview',
        toggle_mode: true,
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

    button.set_child(preview);
    button.connect('clicked', onClicked);
    return button;
}

const TilingToggle = GObject.registerClass(
class TilingToggle extends QuickSettings.QuickMenuToggle {
    constructor() {
        super({
            title: 'Tiling',
            iconName: 'view-grid-symbolic',
            toggleMode: true,
        });

        // Quick Options titles a sheet in sentence case, with a muted count
        // on the other side rather than a repeat of the pill's name and icon.
        this.menu.setHeader('view-grid-symbolic', 'Window layouts');

        this._layoutsItem = new PopupMenu.PopupBaseMenuItem({
            can_focus: false,
            reactive: false,
            style_class: 'luma-tiling-layouts',
        });
        this._layoutsBox = new St.BoxLayout({
            style_class: 'luma-tiling-layouts-box',
            vertical: true,
            x_expand: true,
        });
        this._layoutsItem.add_child(this._layoutsBox);
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
        const settingsItem = this.menu.addAction(
            'Tiling settings', () => this._openPreferences());
        // Plain rows under the hairline, like every other sheet's links.
        for (const item of [this._editLayoutsItem, this._newLayoutItem, settingsItem])
            item.add_style_class_name('luma-sheet-link');
        settingsItem.visible = Main.sessionMode.allowSettings;
        this._editLayoutsItem.visible = Main.sessionMode.allowSettings;
        this._newLayoutItem.visible = Main.sessionMode.allowSettings;
        this.menu._settingsActions[TILING_SHELL_UUID] = settingsItem;

        this._shellSettings = new Gio.Settings({schemaId: 'org.gnome.shell'});
        this._tilingSettings = createTilingSettings();
        this._syncing = false;
        this._settingsChangedId = this._shellSettings.connect(
            'changed::enabled-extensions', () => this._syncFromSettings());
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
        this.menu.connect('open-state-changed', () => this._syncSurface());
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
            null,
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
        const extensions = this._shellSettings.get_strv('enabled-extensions');

        this._syncing = true;
        this.checked = extensions.includes(TILING_SHELL_UUID);
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
            null,
            (connection, result) => {
                try {
                    const reply = connection.call_finish(result);
                    callback?.(reply);
                } catch (error) {
                    console.error(`Luma Tiling: ${method} failed: ${error.message}`);
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
            null,
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
            const [success] = reply.deepUnpack();
            if (!success) {
                console.error('Luma Tiling: EnableExtension was rejected by GNOME Shell');
                this._syncFromSettings();
                return;
            }

            this._syncFromSettings();
            callback();
        });
    }

    _applyRuntimeState() {
        if (this._syncing)
            return;

        const method = this.checked ? 'EnableExtension' : 'DisableExtension';
        this._callExtensionService(method, reply => {
            const [success] = reply.deepUnpack();
            if (!success) {
                console.error(`Luma Tiling: ${method} was rejected by GNOME Shell`);
                this._syncFromSettings();
            }
        });
    }

    _getLayouts() {
        try {
            const layouts = JSON.parse(this._tilingSettings.get_string('layouts-json'));
            return layouts.filter(layout =>
                typeof layout.id === 'string' &&
                Array.isArray(layout.tiles) &&
                layout.tiles.length > 0);
        } catch (error) {
            console.error(`Luma Tiling: invalid layouts-json: ${error.message}`);
            return [];
        }
    }

    _renderLayouts() {
        this._layoutsBox.destroy_all_children();
        const layouts = this._getLayouts();
        this.menu.setCount?.(layouts.length === 1
            ? '1 layout' : `${layouts.length} layouts`);
        if (layouts.length === 0) {
            this._layoutsBox.add_child(new St.Label({text: 'No layouts available'}));
            return;
        }

        const selectedLayouts = this._tilingSettings
            .get_value('selected-layouts').deepUnpack();
        const workspaceIndex = global.workspace_manager.get_active_workspace_index();
        const monitorCount = Math.max(1, Main.layoutManager.monitors.length);

        for (let monitorIndex = 0; monitorIndex < monitorCount; monitorIndex++) {
            if (monitorCount > 1) {
                this._layoutsBox.add_child(new St.Label({
                    style_class: 'luma-tiling-display-name',
                    text: this._displayNames?.[monitorIndex] || `Display ${monitorIndex + 1}`,
                }));
            }

            const selectedId = selectedLayouts[workspaceIndex]?.[monitorIndex]
                ?? layouts[0].id;
            // Rows are balanced so a last row never holds a single stray layout:
            // five layouts wrap as three and two, not four and one.
            const rows = Math.ceil(layouts.length / MAX_PREVIEWS_PER_ROW);
            const perRow = Math.ceil(layouts.length / rows);
            for (let start = 0; start < layouts.length; start += perRow) {
                const row = new St.BoxLayout({
                    style_class: 'luma-tiling-layouts-row',
                    x_align: Clutter.ActorAlign.CENTER,
                    x_expand: true,
                });
                for (const layout of layouts.slice(start, start + perRow)) {
                    row.add_child(createLayoutPreview(
                        layout,
                        layout.id === selectedId,
                        () => this._selectLayout(monitorIndex, layout.id)));
                }
                this._layoutsBox.add_child(row);
            }
        }
        // Display names arrive asynchronously and add a label per display, so
        // the grid can grow while the detail is open. Ask for a new layout at
        // once, so the actions move down with it instead of being overdrawn.
        this.menu.actor?.queue_relayout();
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
    }

    _selectLayout(monitorIndex, layoutId) {
        this._withTilingEnabled(() => this._callTilingShell(
            'selectLayout',
            new GLib.Variant('(is)', [monitorIndex, layoutId])));
        this.menu.close();
    }

    _invokeEditor(method) {
        this._withTilingEnabled(() => this._callTilingShell(method));
    }

    _openPreferences() {
        this._callExtensionService('LaunchExtensionPrefs');
    }

    destroy() {
        if (this._surfaceSyncId)
            GLib.source_remove(this._surfaceSyncId);
        this._surfaceSyncId = 0;
        Main.panel.statusArea.quickSettings?.menu?.box?.disconnectObject(this);
        if (this._settingsChangedId)
            this._shellSettings.disconnect(this._settingsChangedId);
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
