// SPDX-License-Identifier: GPL-2.0-or-later
// The LumaUI media row in St: one playing app as its content (artwork badged
// with the app, title and line, a round play key, the volume under the
// words). Its metrics are the tokens LumaMediaRow reads in GTK
// (mediaRowTokens.js, generated from design-tokens.d/media-row.json); the
// compact row sits in a panel's well, the touch row under a finger.
// Rendering and input only: it never talks to a player or a mixer.
import Atk from 'gi://Atk';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import St from 'gi://St';
import {Slider} from 'resource:///org/gnome/shell/ui/slider.js';
import {mediaRow as tokens} from './mediaRowTokens.js';
import {stepVolume} from './soundPageModel.js';
import {gettext as _} from 'gettext';

const C = tokens.compact;

export const MediaRow = GObject.registerClass({
    Signals: {
        'raise': {},
        'mute-toggled': {param_types: [GObject.TYPE_BOOLEAN]},
        'play-pause': {},
        'volume-changed': {param_types: [GObject.TYPE_DOUBLE]},
    },
}, class LumaMediaRow extends St.BoxLayout {
    _init(params = {}) {
        const {touch = false, ...rest} = params;
        super._init({
            style_class: 'lumaui-media-row',
            reactive: true,
            can_focus: true,
            track_hover: true,
            x_expand: true,
            y_expand: false,
            accessible_role: Atk.Role.PANEL,
            ...rest,
        });
        this._touch = false;
        this._state = {};
        this._raisedOnce = false;
        this._maximum = 1;

        // The artwork: a picture, or the app's icon on a neutral tile; the
        // badge hangs off its bottom corner.
        this._art = new St.Bin({style_class: 'lumaui-media-row-art', reactive: true, track_hover: true});
        this._tileIcon = new St.Icon({style_class: 'lumaui-media-row-tile-icon'});
        this._badgeIcon = new St.Icon({style_class: 'lumaui-media-row-badge-icon', icon_size: C.badge});
        this._badge = new St.Bin({style_class: 'lumaui-media-row-badge', child: this._badgeIcon,
            reactive: true, track_hover: true});
        this._artBox = new St.Widget({style_class: 'lumaui-media-row-art-box',
            layout_manager: new Clutter.FixedLayout(), y_align: Clutter.ActorAlign.CENTER,
            width: C.art, height: C.art});
        this._artBox.add_child(this._art);
        this._artBox.add_child(this._badge);
        this._art.set_size(C.art, C.art);
        const ring = C.badge_ring, at = C.art + C.badge_offset - C.badge - ring;
        this._badge.set_position(at, at);
        this.add_child(this._artBox);

        this._title = new St.Label({style_class: 'lumaui-media-row-title'});
        this._title.clutter_text.ellipsize = Pango.EllipsizeMode.END;
        this._line = new St.Label({style_class: 'lumaui-media-row-line'});
        this._line.clutter_text.ellipsize = Pango.EllipsizeMode.END;
        this.slider = new Slider(0);
        this.slider.add_style_class_name('lumaui-media-row-slider');
        this.slider.x_expand = true;
        this.slider.can_focus = false;
        this._dragging = false;
        this.slider.connect('drag-begin', () => { this._dragging = true; });
        this.slider.connect('drag-end', () => { this._dragging = false; });
        this._sliderChanged = this.slider.connect('notify::value', () => {
            if (this._settingValue)
                return;
            this.emit('volume-changed', this.slider.value);
        });
        // The words dim together when the row is paused; the slider does not.
        this._words = new St.BoxLayout({style_class: 'lumaui-media-row-words',
            orientation: Clutter.Orientation.VERTICAL, x_expand: true});
        this._words.add_child(this._title);
        this._words.add_child(this._line);
        const column = new St.BoxLayout({style_class: 'lumaui-media-row-text',
            orientation: Clutter.Orientation.VERTICAL, x_expand: true, y_align: Clutter.ActorAlign.CENTER});
        column.add_child(this._words);
        column.add_child(this.slider);
        this.add_child(column);

        this._playIcon = new St.Icon({style_class: 'lumaui-media-row-play-icon'});
        this._play = new St.Button({style_class: 'lumaui-media-row-play', child: this._playIcon,
            can_focus: true, y_align: Clutter.ActorAlign.CENTER, visible: false});
        this._play.connect('clicked', () => this.emit('play-pause'));
        this.add_child(this._play);

        const press = (actor, handler) => {
            const click = new Clutter.ClickGesture();
            click.connect('recognize', handler);
            actor.add_action(click);
        };
        // The first press on the artwork shows the app; the second mutes.
        press(this._art, () => {
            if (this._raisedOnce) {
                this.emit('mute-toggled', !this._state.muted);
            } else {
                this._raisedOnce = true;
                this.emit('raise');
            }
        });
        press(this._badge, () => this.emit('mute-toggled', !this._state.muted));
        this.connect('notify::hover', () => {
            if (!this.hover)
                this._raisedOnce = false;
        });
        this.setTouch(touch);
    }

    // Under a finger: at least 56 px tall with a 44 px key.
    setTouch(touch) {
        this._touch = !!touch;
        if (this._touch)
            this.add_style_class_name('touch');
        else
            this.remove_style_class_name('touch');
    }

    setMaximum(maximum) {
        this._maximum = maximum;
        this.slider.maximum_value = maximum;
        this.slider.overdrive_start = Math.min(1, maximum);
    }

    /**
     * @param {object} state - {title, line, name, gicon, art, playing,
     *   muted, volume, canPlayPause}; art is a URL or ''
     */
    update(state) {
        const was = this._state;
        this._state = {...state};
        this._title.text = state.title ?? '';
        this._line.text = state.line ?? '';
        this._line.visible = !!state.line;
        this.accessible_name = state.name && state.name !== state.title
            ? `${state.title}, ${state.name}` : state.title ?? '';
        if (state.gicon !== was.gicon) {
            this._badgeIcon.gicon = state.gicon ?? null;
            this._tileIcon.gicon = state.gicon ?? null;
        }
        this._badge.visible = !!state.gicon;
        if (state.art !== was.art || was.art === undefined)
            this._setArt(state.art);
        this._play.visible = !!state.canPlayPause;
        this._playIcon.icon_name = state.playing ? 'lumaui-pause-symbolic' : 'lumaui-play-symbolic';
        this._play.accessible_name = state.playing ? _('Pause') : _('Play');
        for (const [name, on] of [['paused', !state.playing], ['muted', !!state.muted]]) {
            if (on)
                this.add_style_class_name(name);
            else
                this.remove_style_class_name(name);
        }
        // Paused, the words and the artwork step back (not the badge).
        const dim = state.playing ? 255 : Math.round(255 * C.paused_opacity);
        this._words.opacity = dim;
        this._art.opacity = dim;
        // While the person drags, their hand is the volume.
        if (!this._dragging) {
            this._settingValue = true;
            this.slider.value = Math.clamp(state.volume ?? 1, 0, this._maximum);
            this._settingValue = false;
        }
    }

    _setArt(url) {
        let file = null;
        try {
            file = url ? Gio.File.new_for_uri(url) : null;
        } catch {}
        if (file?.is_native()) {
            // Cover the square, whatever the picture's shape.
            this._art.child = null;
            this._art.remove_style_class_name('tile');
            this._art.style = `background-image: url("${file.get_uri()}"); background-size: cover;`;
            return;
        }
        this._art.style = null;
        if (file) {
            const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
            this._art.remove_style_class_name('tile');
            this._art.child = St.TextureCache.get_default().load_file_async(file, C.art, C.art, scale, 1);
            return;
        }
        this._art.add_style_class_name('tile');
        this._art.child = this._tileIcon;
    }

    vfunc_key_press_event(event) {
        const symbol = event.get_key_symbol();
        const rtl = this.get_text_direction() === Clutter.TextDirection.RTL;
        switch (symbol) {
        case Clutter.KEY_Left:
        case Clutter.KEY_Right: {
            const up = (symbol === Clutter.KEY_Right) !== rtl;
            this.slider.value = stepVolume(this.slider.value, up ? 1 : -1, this._maximum);
            return Clutter.EVENT_STOP;
        }
        case Clutter.KEY_space:
        case Clutter.KEY_KP_Space:
            if (this._state.canPlayPause) {
                this.emit('play-pause');
                return Clutter.EVENT_STOP;
            }
            break;
        case Clutter.KEY_m:
        case Clutter.KEY_M:
            this.emit('mute-toggled', !this._state.muted);
            return Clutter.EVENT_STOP;
        }
        return super.vfunc_key_press_event(event);
    }
});
