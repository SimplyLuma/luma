// SPDX-License-Identifier: GPL-2.0-or-later
// LumaUI presentation of the native Quick Options controls. No device models,
// settings proxies, audio mixers or notification stores are created here.
import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import St from 'gi://St';
import {QuickToggle} from 'resource:///org/gnome/shell/ui/quickSettings.js';
import {gettext as _} from 'gettext';

function move(actor, parent) {
    actor.get_parent()?.remove_child(actor);
    parent.add_child(actor);
}

function roundControl(item) {
    if (!item) return;
    item.add_style_class_name('lumaui-round-toggle');
    item.remove_effect_by_name('luma-control-clip');
    item.x_expand = true;
    const button = item._contents ?? item;
    if (!button._box || !button._icon) return;
    button.setPill(true);
    button._box.orientation = Clutter.Orientation.VERTICAL;
    button._box.x_align = Clutter.ActorAlign.CENTER;
    button._icon.x_align = Clutter.ActorAlign.CENTER;
    button._title.x_align = Clutter.ActorAlign.CENTER;
    button._title.get_parent().x_align = Clutter.ActorAlign.CENTER;
    button._title.get_parent().x_expand = false;
    button._title.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
    button._title.clutter_text.line_wrap = true;
    if (item._menuButton) item._menuButton.hide();
}

export class OptionsSurface {
    constructor(menu, indicators) {
        this.menu = menu;
        this.indicators = indicators;
        this._footer = indicators._system.quickSettingsItems[0];
        menu.box.add_style_class_name('lumaui-shell-options');
        // A background blur must sample the stage, not an empty intermediate
        // framebuffer belonging to the popup's parent.
        menu._boxPointer.set_offscreen_redirect(Clutter.OffscreenRedirect.AUTOMATIC);
        menu._dimEffect.enabled = false;
        menu._materialBinding.setCornerRadii(26);
        menu._materialBinding.setTranslucentRecipe({radius: 36, saturation: 1.2});
        menu._materialBinding.setFloatingRecipe({radius: 36, saturation: 1.2});
        menu._grid.hide();
        move(menu._lumaHeader, menu._mainPage);
        menu._mainPage.set_child_at_index(menu._lumaHeader, 0);
        menu._lumaHeader.show();
        menu._lumaHeader.get_first_child().orientation = Clutter.Orientation.HORIZONTAL;
        const system = indicators._system.quickSettingsItems[0];
        const power = new St.Button({can_focus: true, accessible_name: _('Power settings')});
        menu._lumaBattery.get_parent()?.remove_child(menu._lumaBattery);
        power.set_child(menu._lumaBattery);
        menu._lumaHeader.add_child(power);
        power.connect('clicked', () => system.menu.open());
        const levels = [indicators._volumeOutput.quickSettingsItems[0], indicators._brightness.quickSettingsItems[0]];
        for (const item of levels) this._fillLevel(item);
        const utilities = menu._studioUtilities;
        const oldRows = utilities.get_children();
        const row = () => {
            const result = new St.BoxLayout({style_class: 'lumaui-toggle-row', x_expand: true});
            result.layout_manager.homogeneous = true;
            utilities.add_child(result);
            return result;
        };
        const first = row();
        this._utilitySlots = [];
        const core = [indicators._doNotDisturb, indicators._nightLight, indicators._powerProfiles, indicators._darkMode]
            .map(indicator => indicator.quickSettingsItems[0]);
        core[2].iconName = 'lumaui-leaf-symbolic';
        for (const item of core) {
            const slot = new St.Bin({x_expand: true});first.add_child(slot);this._utilitySlots.push(slot);
            item.get_parent()?.remove_child(item);slot.set_child(item);roundControl(item);
        }
        const second = row();
        // Providers retain their native slots; visible controls pack from the left.
        this._secondarySlots = Array.from({length: 4}, () => {
            const slot = new St.Bin({x_expand: true});second.add_child(slot);return slot;
        });
        this._utilitySlots.push(...this._secondarySlots);
        this._utilityRows = [first, second];
        this._utilityParking = new St.BoxLayout({visible: false});
        utilities.add_child(this._utilityParking);
        for (const [item, index] of [[indicators._rfkill.quickSettingsItems[0], 0],
            [indicators._cast.quickSettingsItems[0], 2]]) this._put(item, index);
        indicators._cast.quickSettingsItems[0].title = _('Cast Screen');
        const hotspotSource = indicators._network?._wirelessToggle?._hotspot;
        if (hotspotSource) {
            // This is a view of NetworkManager's existing switch, including its
            // existing authorization path, not an independent hotspot controller.
            const hotspot = new QuickToggle({title: _('Hotspot'),
                iconName: 'lumaui-radio-tower-symbolic', toggle_mode: true});
            const binding = hotspotSource.bind_property('state', hotspot, 'checked', GObject.BindingFlags.SYNC_CREATE);
            hotspot.connect('clicked', () => hotspotSource.toggle());
            hotspot.connect('destroy', () => binding.unbind());
            this._put(hotspot, 3);
        }
        for (const oldRow of oldRows) {
            for (const item of oldRow.get_children()) {
                if (item.constructor.name === 'TilingToggle') this.adoptTiling(item);
                else if (item.menu?._studioTitle === _('Experience')) this.setModePicker(item);
                else move(item, utilities);
            }
            if (!oldRow.get_children().length) oldRow.destroy();
        }
        for (const item of [indicators._network?._wirelessToggle, indicators._bluetooth?.quickSettingsItems[0]]) {
            // The card's own rounded background is sufficient: its children
            // are transparent. A legacy offscreen mask clips the lower rim.
            item?.remove_effect_by_name('luma-control-clip');
            item?._contents?._icon.add_style_class_name('lumaui-connection-light');
        }
        system._identitySlot.hide();
        system._actions.x_align = Clutter.ActorAlign.END;
        system._actions.x_expand = true;
        const updateLevels = () => {
            const light = menu.box.has_style_class_name('luma-surface-light') ||
                menu.box.has_style_class_name('luma-surface-glass');
            for (const item of levels) {
                if (light) item.slider.add_style_class_name('lumaui-light-level');
                else item.slider.remove_style_class_name('lumaui-light-level');
                item.slider.queue_repaint();
            }
        };
        menu.box.connect('style-changed', updateLevels);
        updateLevels();
        for (const slot of this._utilitySlots) {
            slot.connectObject('notify::child', () => this._watchUtility(slot), menu.box);
            this._watchUtility(slot);
        }
    }

    _watchUtility(slot) {
        slot._visibleSource?.disconnectObject(slot);
        slot._visibleSource = slot.get_child();
        slot._visibleSource?.connectObject('notify::visible', () => this._packUtilities(),
            'destroy', () => { slot._visibleSource = null; }, slot);
        this._packUtilities();
    }

    _packUtilities() {
        if (!this._utilityRows || this._packingUtilities) return;
        this._packingUtilities = true;
        const visible = this._utilitySlots.filter(slot => slot.get_child()?.visible);
        for (const slot of this._utilitySlots) slot.get_parent()?.remove_child(slot);
        for (const row of this._utilityRows) row.destroy_all_children();
        for (const [index, slot] of visible.entries()) this._utilityRows[Math.floor(index / 4)].add_child(slot);
        for (const slot of this._utilitySlots.filter(slot => !visible.includes(slot))) this._utilityParking.add_child(slot);
        for (const [index, row] of this._utilityRows.entries()) {
            row.visible = visible.length > index * 4;
            while (row.visible && row.get_n_children() < 4)
                row.add_child(new St.Widget({x_expand: true}));
        }
        this._packingUtilities = false;
    }

    _fillLevel(item) {
        item._filled = true;
        item._levelHeader.hide();
        const track = new St.Widget({style_class: 'lumaui-level-track',
            layout_manager: new Clutter.BinLayout(), x_expand: true, y_align: Clutter.ActorAlign.CENTER});
        const bin = item.slider.get_parent();
        move(bin, track);
        bin.y_align = Clutter.ActorAlign.FILL;
        bin.y_expand = true;
        item.slider.y_expand = true;
        item.slider.add_style_class_name('lumaui-filled-slider');
        const foreground = new St.BoxLayout({style_class: 'lumaui-level-foreground',
            reactive: false, x_expand: true, y_expand: true});
        track.add_child(foreground);
        move(item._iconButton, foreground);
        item._iconButton.reactive = false;
        item._iconButton.can_focus = false;
        foreground.add_child(new St.Widget({x_expand: true}));
        move(item._valueLabel, foreground);
        item._valueLabel.y_align = Clutter.ActorAlign.CENTER;
        item.child.insert_child_at_index(track, 0);
        item.add_style_class_name('lumaui-filled-level');
    }

    _put(item, index) {
        if (!item) return;
        item.get_parent()?.remove_child(item);
        this._secondarySlots[index].set_child(item);
        roundControl(item);
    }

    adoptTiling(item) {
        const wrapper = item._lumaStudioRow;
        if (wrapper && item.get_parent() === wrapper) {
            wrapper.get_parent()?.remove_child(wrapper);
            this._secondarySlots[1].set_child(wrapper);
            roundControl(item);
        } else {
            this._put(item, 1);
        }
        item.iconName = 'lumaui-columns-2-symbolic';
        item.menu._studioTitle = _('Window layouts');
        // The native item owns its menu and tears itself down. A fixed shared
        // slot survives removal; no wrapper destroys the child recursively.
    }

    setModePicker(item) {
        this._modePicker = item;
        item.get_parent()?.remove_child(item);
        this._footer.child.insert_child_at_index(item, 0);
        item.x_expand = false;
        item._contents.x_expand = false;
        item.x_align = Clutter.ActorAlign.START;
        item.add_style_class_name('lumaui-mode-picker');
        item.add_style_class_name('lumaui-footer-mode');
        item._menuButton.show();
        const copy = item._contents._title.get_parent();
        copy.orientation = Clutter.Orientation.HORIZONTAL;
        copy.set_style('spacing: 8px;');
    }
}
