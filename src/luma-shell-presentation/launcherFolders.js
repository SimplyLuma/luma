// SPDX-License-Identifier: GPL-2.0-or-later
// Desktop presentation of the existing DockFolders controller; no second file/app model.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {FolderModel} from 'resource:///org/gnome/shell/ui/lumaDockFolders.js';
import {AppDrawer} from './appDrawer.js';
import {FileFace, makeFileCard} from './fileFaces.js';
import {folderWindow} from './launcherModel.js';

const APPS = 'applications:///';
const PALETTES = {
    downloads: ['#294a77', '#233553', '#6aa5e9', '#3d78b4', 'lumaui-download-symbolic'],
    documents: ['#785234', '#573a29', '#e7ae77', '#b87843', 'lumaui-file-text-symbolic'],
    pictures: ['#2b614b', '#254837', '#81c89b', '#4b9a72', 'lumaui-image-symbolic'],
};
function folderPalette(model) {
    const path = model.file?.get_path();
    if (path === GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS)) return PALETTES.documents;
    if (path === GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)) return PALETTES.pictures;
    return PALETTES.downloads;
}
const StackMark = GObject.registerClass({GTypeName: 'LumaUIFolderStackMark'},
class StackMark extends St.Widget {
    _init(controller, tile) {
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
        super._init({width: 45 * scale, height: 45 * scale, layout_manager: new Clutter.FixedLayout()});
        this._controller = controller;
        this._tile = tile;
        this._back = new St.Widget({style_class: 'lumaui-folder-stack-back', width: 45 * scale, height: 45 * scale});
        this.add_child(this._back);
        this._files = [];
        this._pocket = new St.Widget({style_class: 'lumaui-folder-stack-pocket', width: 45 * scale, height: 27 * scale,
            layout_manager: new Clutter.BinLayout()});
        this._pocket.set_position(0, 18 * scale);
        this._symbol = new St.Icon({style_class: 'lumaui-folder-stack-symbol', icon_size: 14,
            x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER});
        this._pocket.add_child(this._symbol);
        this.add_child(this._pocket);
        tile.connectObject('notify::hover', () => this._animate(), 'notify::pseudo-class', () => this._animate(), this);
        this.setFolder(tile.model);
    }
    setFolder(model) {
        this.model = model;
        const [back1, back2, front1, front2, icon] = folderPalette(model);
        this._back.set_style(`background-gradient-direction: vertical; background-gradient-start: ${back1}; background-gradient-end: ${back2};`);
        this._pocket.set_style(`background-gradient-direction: vertical; background-gradient-start: ${front1}; background-gradient-end: ${front2};`);
        this._symbol.icon_name = icon;
        const files = model.items.filter(item => !item.isDirectory).slice(0, 3);
        const signature = files.map(item => `${item.uri}:${item.modifiedUnix}`).join('|');
        if (signature !== this._signature) {
            this._signature = signature;
            this._files.forEach(file => file.destroy());
            this._files = files.map((item, i) => {
                const face = new FileFace(item, {miniature: true});
                const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
                face.set_position((14 + (i - (files.length - 1) / 2) * 8) * scale, 6 * scale);
                face.set_pivot_point(.5, 1);
                face.rotation_angle_z = (i - (files.length - 1) / 2) * 9;
                this.insert_child_below(face, this._pocket);
                return face;
            });
        }
        for (const face of this._files) {
            const path = this._controller.previewFor?.(face._item, 32) ?? this._controller._thumbs.get(face._item.uri);
            if (path && face._shown !== path) face.showThumbnail(path);
        }
        this._animate();
    }
    _animate() {
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
        const open = this._controller.openFolder?.uri === this.model?.uri;
        this._files.forEach((face, i) => {
            const index = i - (this._files.length - 1) / 2;
            face.ease({translation_x: (this._tile.hover ? index * 4 : open ? index * 3 : 0) * scale,
                translation_y: (this._tile.hover ? index === 0 ? -19 : -15 : open ? -11 : 0) * scale,
                rotation_angle_z: index * (this._tile.hover ? 14 : open ? 12 : 9),
                duration: St.Settings.get().enable_animations ? 380 : 0,
                mode: Clutter.AnimationMode.EASE_OUT_BACK});
        });
    }
});
const StackTileLayout = GObject.registerClass({GTypeName: 'LumaUIFolderStackTileLayout'},
class StackTileLayout extends Clutter.LayoutManager {
    vfunc_get_preferred_width() { const n = 45 * St.ThemeContext.get_for_stage(global.stage).scale_factor; return [n, n]; }
    vfunc_get_preferred_height() { return this.vfunc_get_preferred_width(); }
    vfunc_allocate(_container, box) {
        const tile = this.tile;
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
        tile._mark.allocate(new Clutter.ActorBox({x1: 0, y1: 0, x2: box.get_width(), y2: box.get_height()}));
        const [, bw] = tile._badge.get_preferred_width(-1), [, bh] = tile._badge.get_preferred_height(bw);
        tile._badge.allocate(new Clutter.ActorBox({x1: box.get_width() - bw + 5 * scale,
            y1: -4 * scale, x2: box.get_width() + 5 * scale, y2: bh - 4 * scale}));
        const edge = tile._controller.edge;
        const vertical = edge === 'left' || edge === 'right';
        const center = (vertical ? box.get_height() : box.get_width()) / 2;
        const side = edge === 'left' || edge === 'top' ? 49 * scale : -7 * scale;
        tile._bar.allocate(new Clutter.ActorBox(vertical
            ? {x1: side, x2: side + 4 * scale, y1: center - 8 * scale, y2: center + 8 * scale}
            : {x1: center - 8 * scale, x2: center + 8 * scale, y1: side, y2: side + 4 * scale}));
    }
});

export class LauncherFolders {
    constructor(controller) {
        this._controller = controller;
        this._stylesheet = Gio.File.new_for_uri('resource:///org/gnome/shell/ui/lumaPresentation/launcherFolders.css');
        St.ThemeContext.get_for_stage(global.stage).get_theme().load_stylesheet(this._stylesheet);
        this._shelf = controller._shelf;
        this.enabled = true;
        this._marks = new Map();
        this._sorts = new Map();
        this._cards = new Map();
        this._path = [];
        this._signals = [];
        this._drawer = new AppDrawer(this);
        this._button = new St.Button({style_class: 'lumaui-launcher-button', can_focus: true,
            accessible_name: _('All apps'), width: 45, height: 45,
            child: new St.Icon({gicon: new Gio.FileIcon({file: Gio.File.new_for_uri(
                'resource:///org/gnome/shell/ui/lumaPresentation/launcher.svg')}), icon_size: 45})});
        this._button.connect('clicked', () => this._drawer.toggle());
        this._separator = new St.Widget({style_class: 'lumaui-launcher-divider',
            x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER});
        this._shelf._dockStrip.insert_child_at_index(this._button, 0);
        this._shelf._dockStrip.insert_child_at_index(this._separator, 1);
        const surface = controller._surface;
        this._surface = surface;
        this._nameStyle = surface._name.style;
        this._metaStyle = surface._meta.style;
        this._sortButton = new St.Button({style_class: 'lumaui-folder-sort', can_focus: true,
            accessible_name: _('Order files')});
        this._sortBox = new St.BoxLayout({y_align: Clutter.ActorAlign.CENTER});
        this._sortLabel = new St.Label({text: _('Newest'), y_align: Clutter.ActorAlign.CENTER});
        this._sortBox.add_child(this._sortLabel);
        this._sortBox.add_child(new St.Icon({icon_name: 'lumaui-chevrons-up-down-symbolic', icon_size: 13}));
        this._sortButton.child = this._sortBox;
        const filer = surface._actions.get('filer');
        surface.header.insert_child_below(this._sortButton, filer);
        this._sortMenu = new PopupMenu.PopupMenu(this._sortButton, .5, St.Side.TOP);
        Main.uiGroup.add_child(this._sortMenu.actor); this._sortMenu.actor.hide();
        this._menuManager = new PopupMenu.PopupMenuManager(this._sortButton);
        this._menuManager.addMenu(this._sortMenu);
        this._sortButton.connect('clicked', () => this._sortMenu.toggle());
        this._sortItems = new Map();
        for (const [id, text, icon] of [['newest', _('Newest'), 'lumaui-clock-symbolic'],
            ['name', _('Name'), 'lumaui-arrow-down-a-z-symbolic'], ['kind', _('Kind'), 'lumaui-shapes-symbolic']]) {
            const option = new PopupMenu.PopupImageMenuItem(text, icon);
            option.connect('activate', () => { this._sorts.set(this._root?.uri, id); this.render(); });
            this._sortMenu.addMenuItem(option); this._sortItems.set(id, option);
        }
        this._originalFilerChild = filer.child;
        this._originalFilerStyle = filer.style;
        filer.child = null;
        const open = new St.BoxLayout({y_align: Clutter.ActorAlign.CENTER});
        const app = Shell.AppSystem.get_default().lookup_app('org.gnome.Nautilus.desktop');
        open.add_child(app ? app.create_icon_texture(20) : new St.Icon({icon_name: 'folder', icon_size: 20}));
        open.add_child(new St.Label({text: _('Open'), y_align: Clutter.ActorAlign.CENTER}));
        this._openChild = open;
        filer.child = open;
        filer.add_style_class_name('lumaui-folder-open');
        filer.accessible_name = _('Open in Filer');
        filer.set_style('width: 76px; height: 30px; padding: 0 10px;');
        this._back = new St.Button({child: new St.Icon({icon_name: 'lumaui-chevron-left-symbolic', icon_size: 16}),
            can_focus: true, visible: false, accessible_name: _('Back'), style_class: 'luma-dockf-act'});
        this._back.connect('clicked', () => this.back());
        surface.header.insert_child_at_index(this._back, 0);
        this._notch = new St.Widget({style_class: 'lumaui-folder-notch', width: 14, height: 14,
            x_align: Clutter.ActorAlign.START, y_align: Clutter.ActorAlign.START, rotation_angle_z: 45, visible: false});
        surface.insert_child_below(this._notch, surface.surface);
        this._arrows = [-1, 1].map(direction => {
            const button = new St.Button({style_class: 'lumaui-folder-arrow', width: 30, height: 44,
                can_focus: true, x_align: Clutter.ActorAlign.START, y_align: Clutter.ActorAlign.START,
                accessible_name: direction < 0 ? _('Earlier files') : _('More files'),
                child: new St.Icon({icon_name: direction < 0 ? 'lumaui-chevron-left-symbolic' : 'lumaui-chevron-right-symbolic', icon_size: 15})});
            button.connect('clicked', () => {
                const a = surface.scroll.hadjustment;
                a.ease(Math.max(a.lower, Math.min(a.upper - a.page_size, a.value + direction * 348 * this._scale())),
                    {duration: St.Settings.get().enable_animations ? 220 : 0, mode: Clutter.AnimationMode.EASE_OUT_CUBIC});
            });
            surface.add_child(button); return button;
        });
        this._signals.push([surface, surface.connect('notify::allocation', () => this.position())]);
        this._signals.push([surface.scroll.hadjustment, surface.scroll.hadjustment.connect('notify::value', () => this.onScrolled())]);
        this._signals.push([surface.scroll.hadjustment, surface.scroll.hadjustment.connect('changed', () => this.onScrolled())]);
        this._signals.push([surface, surface.connect('notify::visible', () => {
            if (!surface.visible) { this._sortMenu.close(); this._notch.hide(); this._arrows.forEach(a => a.hide()); this.willClose(); }
        })]);
        for (const property of ['width', 'height']) this._signals.push([this._shelf._dockStrip,
            this._shelf._dockStrip.connect(`notify::${property}`, () => this._shelf._queueDockWidth())]);
        this.sync();
        this._shelf._queueDockWidth();
    }
    _scale() { return St.ThemeContext.get_for_stage(global.stage).scale_factor; }
    sync() {
        this.setEnabled((this._shelf._lumaPresentationMode ?? 'desktop') === 'desktop');
        const appModel = this._controller._models.get(APPS);
        this._drawer.setModel(appModel);
        const wasVisible = this._button.visible;
        this._button.visible = this.enabled && Boolean(appModel) && this._controller.visible;
        this._separator.visible = this._button.visible;
        // A hidden strip keeps its old allocation. Visibility can change its
        // preferred length without notifying width (e.g. return from tablet).
        if (wasVisible !== this._button.visible) this._shelf._queueDockWidth();
        for (const [uri, tile] of this._controller._tiles) {
            if (uri === APPS) { tile.visible = !this.enabled; continue; }
            if (!this.enabled) continue;
            if (!this._marks.has(tile)) {
                const original = tile._mark, layout = tile.child.layout_manager;
                tile.child.remove_child(original);
                const mark = new StackMark(this._controller, tile);
                tile._mark = mark; tile.child.add_child(mark);
                const replacement = new StackTileLayout(); replacement.tile = tile; tile.child.layout_manager = replacement;
                const destroyId = tile.connect('destroy', () => {
                    original.destroy(); this._marks.delete(tile);
                });
                this._marks.set(tile, {original, layout, mark, destroyId, width: tile.width, height: tile.height, barOpacity: tile._bar.opacity});
            }
            tile._bar.opacity = 0;
            tile.set_size(45 * this._scale(), 45 * this._scale());
            tile._mark.setFolder(tile.model);
        }
        this.syncOrientation();
    }
    syncOrientation() {
        const edge = this._shelf.edgeOfIsland?.('dock') ?? this._shelf._edge;
        const vertical = edge === 'left' || edge === 'right';
        this._button.set_size(45 * this._scale(), 45 * this._scale());
        this._separator.set_size((vertical ? 24 : 1) * this._scale(), (vertical ? 1 : 24) * this._scale());
        this._separator.set_style(vertical ? 'margin: 9px 0;' : 'margin: 0 9px;');
    }
    setEnabled(enabled) {
        if (enabled === this.enabled) return;
        this.enabled = enabled;
        if (!enabled) {
            this._drawer.close(); this._controller.close();
            this._sortButton.hide(); this._back.hide(); this._notch.hide(); this._arrows.forEach(a => a.hide());
            this._surface._headMark.show();
            this._restoreMarks();
            this.render();
        }
        const filer = this._surface._actions.get('filer');
        filer.child = enabled ? this._openChild : this._originalFilerChild;
        filer.set_style(enabled ? 'width: 76px; height: 30px; padding: 0 10px;' : this._originalFilerStyle);
        if (enabled) filer.add_style_class_name('lumaui-folder-open');
        else filer.remove_style_class_name('lumaui-folder-open');
    }
    _restoreMarks() {
        for (const [tile, {original, layout, mark, destroyId, width, height, barOpacity}] of this._marks) {
            tile.disconnect(destroyId); tile.set_size(width, height); tile._bar.opacity = barOpacity;
            tile.child.remove_child(mark); mark.destroy(); tile._mark = original; tile.child.add_child(original); tile.child.layout_manager = layout;
        }
        this._marks.clear();
    }
    setDrawerOpen(open) {
        this._button.checked = open;
        const beacon = this._shelf._beacon;
        if (open && beacon) { this._beaconOpacity = beacon.opacity; beacon.opacity = 0; }
        else if (beacon && this._beaconOpacity !== undefined) { beacon.opacity = this._beaconOpacity; delete this._beaconOpacity; }
    }
    openApplications(model) {
        if (!this.enabled || !model.isApplications) return false;
        this._drawer.setModel(model); this._drawer.toggle(); return true;
    }
    get panelActive() { return this.enabled && Boolean(this._controller.openFolder) && !this._controller.openFolder.isApplications && this._controller.viewFor(this._controller.openFolder) === 'stack'; }
    render() {
        const c = this._controller, s = this._surface;
        if (!this.panelActive) {
            this._sortButton.hide(); this._back.hide(); s._headMark.show();
            if (s.has_style_class_name('lumaui-folder-panel')) {
                s.body.layout_manager = new Clutter.BoxLayout({orientation: Clutter.Orientation.VERTICAL});
                s.body.set_size(-1, -1); s.scroll.set_style(null);
                s.scroll.hscrollbar_policy = St.PolicyType.NEVER;
                s.scroll.vscrollbar_policy = St.PolicyType.AUTOMATIC;
                s.remove_style_class_name('lumaui-folder-panel');
                s._name.set_style(this._nameStyle); s._meta.set_style(this._metaStyle);
                s._material.setCornerRadii(18);
            }
            return false;
        }
        if (!this._root) this._root = c.openFolder;
        const model = c.openFolder;
        this._drawer.close();
        s.add_style_class_name('lumaui-folder-panel');
        s._headMark.hide(); s._views.hide(); s.search.hide();
        this._sortButton.show(); this._back.visible = this._path.length > 0;
        s.setHeader(model, 'stack', this._root.newCount);
        const total = model.items.reduce((sum, item) => sum + Math.max(0, item.size), 0);
        const count = ngettext('%d item', '%d items', model.count).format(model.count);
        s._meta.text = model.loaded && !model.count ? count : `${count} · ${GLib.format_size(total)}`;
        if (this._root.newCount) s._meta.clutter_text.set_markup(GLib.markup_escape_text(s._meta.text, -1) + ' · <span foreground="#80bfff">' + ngettext('%d new', '%d new', this._root.newCount).format(this._root.newCount) + '</span>');
        if (!model.loaded) s._meta.text = _('Reading folder…');
        if (this._path.length) s._meta.text = [this._root.name, ...this._path.slice(1).map(m => m.name)].join(' › ');
        s._name.set_style('font-size: 14px; font-weight: 650;');
        s._meta.set_style('font-size: 11.5px;');
        s.surface.set_width(Math.min(660 * this._scale(), Main.layoutManager.primaryMonitor.width - 32 * this._scale()));
        s._material.setCornerRadii(24);
        s._material.setFloatingRecipe?.({radius: 24, saturation: 1.5});
        s.scroll.hscrollbar_policy = St.PolicyType.EXTERNAL; s.scroll.vscrollbar_policy = St.PolicyType.NEVER;
        s.scroll.set_style('height: 144px; max-height: 144px;');
        s.scroll.enable_mouse_scrolling = true;
        s.scroll.update_fade_effect(new Clutter.Margin({left: 40 * this._scale(), right: 50 * this._scale()}));
        s.body.remove_style_class_name('luma-dockf-grid');
        s.body.layout_manager = new Clutter.FixedLayout();
        s.body.destroy_all_children(); this._cards.clear();
        const sort = this._sorts.get(this._root.uri) ?? 'newest';
        this._sortLabel.text = {newest: _('Newest'), name: _('Name'), kind: _('Kind')}[sort];
        for (const [id, item] of this._sortItems) item.setOrnament(id === sort ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
        this._items = [...model.items].sort((a, b) => sort === 'name' ? a.name.localeCompare(b.name)
            : sort === 'kind' ? a.contentType.localeCompare(b.contentType) || a.name.localeCompare(b.name)
            : b.sortTime - a.sortTime || a.name.localeCompare(b.name));
        s.body.set_size(Math.max(1, this._items.length * 116) * this._scale(), 144 * this._scale());
        // StViewport computes its adjustment from layout children. A transparent
        // extent preserves the full scroll range while file cards are recycled.
        s.body.add_child(new St.Widget({width: Math.max(1, this._items.length * 116) * this._scale(), height: 1}));
        if (!this._items.length) {
            const width = Math.max(1, s.surface.width - 30 * this._scale());
            const empty = new St.Bin({width, height: 144 * this._scale()});
            const copy = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL,
                x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER,
                style_class: 'lumaui-folder-empty'});
            copy.add_child(new St.Icon({icon_name: model.loaded ? folderPalette(model)[4] : 'lumaui-folder-symbolic',
                icon_size: 28, style_class: 'lumaui-folder-empty-icon', x_align: Clutter.ActorAlign.CENTER}));
            copy.add_child(new St.Label({text: model.loaded ? _('No files yet') : _('Loading files…'),
                style_class: 'lumaui-folder-empty-title', x_align: Clutter.ActorAlign.CENTER}));
            copy.add_child(new St.Label({text: _('Files added to this folder will appear here.'),
                style_class: 'lumaui-folder-empty-description', x_align: Clutter.ActorAlign.CENTER}));
            empty.child = copy; s.body.add_child(empty);

        }
        this.onScrolled();
        this._notch.visible = !c.detached;
        this.position();
        c._queueThumbnails();
        return true;
    }
    itemActors() { return [...this._cards].sort((a, b) => a[0] - b[0]).map(([, card]) => card); }
    onKey(event) {
        if (!this.panelActive) return false;
        const key = event.get_key_symbol();
        if (![Clutter.KEY_Left, Clutter.KEY_Right, Clutter.KEY_Up, Clutter.KEY_Down].includes(key)) return false;
        if (!this._items?.length) return true;
        const focus = global.stage.get_key_focus();
        const current = this._items.indexOf(focus?.item);
        const step = [Clutter.KEY_Left, Clutter.KEY_Up].includes(key) ? -1 : 1;
        const next = current < 0 ? 0 : Math.max(0, Math.min(this._items.length - 1, current + step));
        const a = this._surface.scroll.hadjustment, x = next * 116 * this._scale();
        a.value = Math.max(a.lower, Math.min(a.upper - a.page_size,
            x < a.value ? x : x + 116 * this._scale() > a.value + a.page_size ? x + 116 * this._scale() - a.page_size : a.value));
        this.onScrolled(); this._cards.get(next)?.grab_key_focus(); return true;
    }
    onScrolled() {
        if (!this.panelActive || !this._items) return;
        const s = this._surface, a = s.scroll.hadjustment, scale = this._scale();
        const width = a.page_size || Math.min(632 * scale, s.surface.width);
        const range = folderWindow(a.value / scale, width / scale, this._items.length);
        for (const [index, actor] of this._cards) {
            if (index < range.start || index >= range.end) { actor.destroy(); this._cards.delete(index); }
        }
        for (let index = range.start; index < range.end; index++) {
            if (this._cards.has(index)) continue;
            const item = this._items[index];
            const metadata = item.isDirectory ? item.childCount >= 0 ? ngettext('%d item', '%d items', item.childCount).format(item.childCount) : _('Folder') : GLib.format_size(Math.max(0, item.size));
            const card = makeFileCard(item, metadata, () => this.activate(item));
            card.set_position(index * 116 * scale, 0); card.set_size(116 * scale, 140 * scale);
            this._controller._hookItemMenu(card, item);
            s.body.add_child(card); this._cards.set(index, card);
        }
        const end = Math.max(0, a.upper - a.page_size);
        this._arrows[0].visible = s.visible && a.value > 2;
        this._arrows[1].visible = s.visible && a.value < end - 2;
        this._controller._queueThumbnails();
    }
    position() {
        if (!this.panelActive || !this._surface.visible) return;
        const s = this._surface, scale = this._scale();
        const tile = this._controller._tiles.get(this._root?.uri);
        if (!tile) return;
        const [tx, ty] = tile.get_transformed_position(), [tw, th] = tile.get_transformed_size();
        const [x, y] = s.get_transformed_position();
        const width = s.width, height = s.height;
        const edge = this._controller.edge;
        const cx = Math.max(28 * scale, Math.min(width - 28 * scale, tx + tw / 2 - x));
        const cy = Math.max(28 * scale, Math.min(height - 28 * scale, ty + th / 2 - y));
        this._notch.set_translation((edge === 'left' ? -7 * scale : edge === 'right' ? width - 7 * scale : cx - 7 * scale),
            edge === 'top' ? -7 * scale : edge === 'bottom' ? height - 7 * scale : cy - 7 * scale, 0);
        this._notch.visible = !this._controller.detached;
        this._arrows.forEach((arrow, index) => arrow.set_translation(index ? width - 31 * scale : 1 * scale, 105 * scale, 0));
    }
    activate(item) {
        if (!item.isDirectory) { this._controller._activate(item); return; }
        this._path.push(this._controller.openFolder);
        const model = new FolderModel(item.uri);
        model.connect('changed', () => { if (this._controller.openFolder === model) { this.render(); this._controller._place(); } });
        this._controller.openFolder = model; model.start();
        this._surface.scroll.hadjustment.value = 0; this.render(); this._controller._place();
    }
    back() {
        if (!this._path.length) return;
        const model = this._controller.openFolder;
        this._controller.openFolder = this._path.pop();
        model.destroy(); this.render(); this._controller._place();
    }
    willClose() {
        if (!this._root) return;
        if (this._path.length) {
            const owned = [...this._path.slice(1), this._controller.openFolder];
            this._controller.openFolder = this._root;
            owned.forEach(model => model?.destroy());
        }
        this._root = null; this._path = []; this._items = null;
        this._sortMenu.close();
    }
    destroy() {
        this._controller.close(); this.setEnabled(false); this.willClose(); this._drawer.destroy();
        for (const [actor, id] of this._signals) actor.disconnect(id);
        this._sortMenu.destroy(); this._sortButton.destroy(); this._back.destroy(); this._notch.destroy(); this._arrows.forEach(a => a.destroy());
        const filer = this._surface._actions.get('filer');
        filer.child = this._originalFilerChild; filer.set_style(this._originalFilerStyle); filer.remove_style_class_name('lumaui-folder-open');
        this._openChild.destroy();
        this._restoreMarks();
        this._controller._tiles.get(APPS)?.show();
        this._button.destroy(); this._separator.destroy(); this._surface.remove_style_class_name('lumaui-folder-panel');
        St.ThemeContext.get_for_stage(global.stage).get_theme().unload_stylesheet(this._stylesheet);
    }
}
