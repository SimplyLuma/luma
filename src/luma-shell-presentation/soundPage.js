// SPDX-License-Identifier: GPL-2.0-or-later
// Quick Options' Sound page: the master volume, where sound goes (a grid of
// output chips) and what is playing (one LumaUI media row per app), with a
// link to Settings' Volume mixer. It draws the per-app mixer's model
// (status/volume.js AppMixer) and holds it only while it is on screen; the
// rules are soundPageModel.js. No mixer, player or device is created here.
import Atk from 'gi://Atk';
import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import St from 'gi://St';
import {FillSlider} from './fillSlider.js';
import {MediaRow} from './mediaRow.js';
import {mediaRow as tokens} from './mediaRowTokens.js';
import * as Model from './soundPageModel.js';
import {gettext as _, ngettext} from 'gettext';

const C = tokens.compact;

export const SoundPage = GObject.registerClass({
    Signals: {
        // The header's "N playing"; '' hides it.
        'summary-changed': {param_types: [GObject.TYPE_STRING]},
        'more-outputs': {},
        'choose-receiver': {param_types: [GObject.TYPE_STRING]},
        'open-mixer': {},
    },
}, class LumaSoundPage extends St.BoxLayout {
    /**
     * @param {object} params
     * @param {AppMixer} params.mixer - the shared model
     * @param {Clutter.Actor} params.presence - the page's sheet: the model is
     *   held, and nothing else runs, only while it is mapped
     * @param {Slider} params.master - the main page's volume slider
     * @param {St.Icon} params.masterIcon - its glyph
     * @param {St.Label} params.masterValue - its "62%"
     */
    _init({mixer, presence, master, masterIcon, masterValue}) {
        super._init({
            style_class: 'lumaui-sound-page',
            orientation: Clutter.Orientation.VERTICAL,
            x_expand: true,
            y_expand: false,
        });
        this._mixer = mixer;
        this._presence = presence;
        this._rows = new Map();
        this._chips = [];
        this._shown = false;
        this._touch = false;
        this._receivers = [];
        this._maxHeight = 0;

        this._master = this._buildMaster(master, masterIcon, masterValue);
        this.add_child(this._master);

        const grid = new Clutter.GridLayout({column_homogeneous: true});
        this._outputs = new St.Widget({style_class: 'lumaui-sound-outputs', x_expand: true,
            layout_manager: grid});
        grid.hookup_style(this._outputs);
        this.add_child(this._outputs);

        // St has no negative margins: the label and its list are one group,
        // closer to each other than the page's parts are.
        this._playing = new St.BoxLayout({style_class: 'lumaui-sound-playing',
            orientation: Clutter.Orientation.VERTICAL, x_expand: true});
        this.add_child(this._playing);
        this._label = new St.Label({style_class: 'lumaui-sound-label', text: _('Playing')});
        this._playing.add_child(this._label);
        this._list = new St.BoxLayout({style_class: 'lumaui-sound-list',
            orientation: Clutter.Orientation.VERTICAL, x_expand: true});
        this._scroll = new St.ScrollView({style_class: 'lumaui-sound-well vfade', x_expand: true,
            overlay_scrollbars: true, hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC, child: this._list});
        this._playing.add_child(this._scroll);
        this._nothing = new St.Label({style_class: 'lumaui-sound-nothing', text: _('Nothing playing')});
        this.add_child(this._nothing);

        // Every output, for "More…": the same names, with how each connects.
        this._all = new St.BoxLayout({style_class: 'lumaui-sound-well lumaui-sound-all',
            orientation: Clutter.Orientation.VERTICAL, x_expand: true, visible: false});
        this.add_child(this._all);
        this._showingAll = false;

        const foot = new St.BoxLayout({style_class: 'lumaui-sound-foot', x_expand: true});
        this._foot = foot;
        this._link = new St.Button({style_class: 'lumaui-sound-link', label: _('Volume mixer'),
            can_focus: true, x_align: Clutter.ActorAlign.START});
        this._link.connect('clicked', () => this.emit('open-mixer'));
        foot.add_child(this._link);
        this.add_child(foot);

        this._mixer.connectObject('changed', () => this._render(),
            'availability-changed', () => this._render(), this);
        this._presence.connectObject('notify::mapped', () => this._setShown(this._presence.mapped), this);
        this.connect('destroy', () => {
            this._setShown(false);
            this._presence.disconnectObject(this);
            this._mixer.disconnectObject(this);
        });
        this._render();
    }

    // The same pill as the main page, following the same volume.
    _buildMaster(master, masterIcon, masterValue) {
        const slider = new FillSlider(0);
        slider.add_style_class_name('lumaui-filled-slider');
        slider.y_expand = true;
        slider.accessible_name = _('Volume');
        for (const property of ['maximum-value', 'overdrive-start'])
            master.bind_property(property, slider, property, GObject.BindingFlags.SYNC_CREATE);
        master.bind_property('value', slider, 'value',
            GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.BIDIRECTIONAL);
        const bin = new St.Bin({style_class: 'slider-bin', child: slider, reactive: true, can_focus: true,
            x_expand: true, y_expand: true, y_align: Clutter.ActorAlign.FILL});
        bin.set_accessible(slider.get_accessible());
        bin.connect('event', (actor, event) => slider.event(event, false));
        // The page's own glyph, 16 px from the Lucide set: sound, or none.
        const icon = new St.Icon({style_class: 'lumaui-sound-master-icon', y_align: Clutter.ActorAlign.CENTER});
        const glyph = () => {
            icon.icon_name = /muted/.test(masterIcon.icon_name ?? masterIcon.gicon?.to_string() ?? '')
                ? 'lumaui-volume-x-symbolic' : 'lumaui-volume-2-symbolic';
        };
        masterIcon.connectObject('notify::icon-name', glyph, 'notify::gicon', glyph, icon);
        glyph();
        const value = new St.Label({style_class: 'luma-quick-level-value', y_align: Clutter.ActorAlign.CENTER});
        masterValue.bind_property('text', value, 'text', GObject.BindingFlags.SYNC_CREATE);
        const foreground = new St.BoxLayout({style_class: 'lumaui-level-foreground', reactive: false,
            x_expand: true, y_expand: true});
        foreground.add_child(icon);
        foreground.add_child(new St.Widget({x_expand: true}));
        foreground.add_child(value);
        const track = new St.Widget({style_class: 'lumaui-level-track',
            layout_manager: new Clutter.BinLayout(), x_expand: true, y_align: Clutter.ActorAlign.CENTER});
        track.add_child(bin);
        track.add_child(foreground);
        this.masterSlider = slider;
        // The filled level reads its ink from the surface it is on, as the
        // main page's levels do.
        const syncSurface = () => {
            let light = false;
            for (let actor = this.get_parent(); actor && !light; actor = actor.get_parent()) {
                light = !!actor.has_style_class_name?.('luma-surface-light') ||
                    !!actor.has_style_class_name?.('luma-surface-glass');
            }
            if (light === slider.has_style_class_name('lumaui-light-level'))
                return;
            if (light)
                slider.add_style_class_name('lumaui-light-level');
            else
                slider.remove_style_class_name('lumaui-light-level');
            slider.queue_repaint();
        };
        this.connect('style-changed', syncSurface);
        this.connect('notify::mapped', syncSurface);
        const box = new St.BoxLayout({style_class: 'lumaui-filled-level lumaui-sound-master', x_expand: true});
        box.add_child(track);
        return box;
    }

    // AirPlay speakers found nearby (GetAirPlayReceivers entries): chips
    // among the outputs.
    setReceivers(receivers) {
        this._receivers = receivers ?? [];
        if (this._shown)
            this._renderOutputs();
    }

    // The full output list in place of the page ("More…"), or the page again.
    showAll(all) {
        this._showingAll = !!all;
        this._render();
        if (all)
            this._all.get_first_child()?.grab_key_focus();
    }

    _renderAll(outputs) {
        this._all.destroy_all_children();
        for (const output of outputs) {
            const words = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true,
                y_align: Clutter.ActorAlign.CENTER});
            words.add_child(new St.Label({style_class: 'lumaui-media-row-title', text: Model.outputLabel(output)}));
            if (output.detail)
                words.add_child(new St.Label({style_class: 'lumaui-media-row-line', text: output.detail}));
            const content = new St.BoxLayout({style_class: 'lumaui-sound-output-content', x_expand: true});
            content.add_child(new St.Icon({style_class: 'lumaui-sound-chip-icon', icon_name: Model.outputGlyph(output),
                y_align: Clutter.ActorAlign.CENTER}));
            content.add_child(words);
            content.add_child(new St.Icon({style_class: 'lumaui-sound-output-check', icon_name: 'lumaui-check-symbolic',
                y_align: Clutter.ActorAlign.CENTER, opacity: output.default ? 255 : 0}));
            const row = new St.Button({style_class: 'lumaui-sound-output', child: content, can_focus: true,
                x_expand: true, accessible_name: Model.outputLabel(output)});
            row.x_align = Clutter.ActorAlign.FILL;
            if (output.default)
                row.add_accessible_state(Atk.StateType.CHECKED);
            row.connect('clicked', () => this._mixer.setDefaultOutput(output.name));
            this._all.add_child(row);
        }
    }

    // On screen the model is held; off screen nothing is fetched or tracked.
    _setShown(shown) {
        if (shown === this._shown)
            return;
        this._shown = shown;
        if (shown) {
            this._mixer.hold();
            this._render();
        } else {
            this._mixer.release();
        }
    }

    // A phone's or tablet's drawer: rows under a finger.
    setTouch(touch) {
        this._touch = !!touch;
        for (const row of this._rows.values())
            row.setTouch(this._touch);
        this._syncHeight();
    }

    // The tallest the whole page may be, in logical px: past that the
    // playing list gives up rows and scrolls.
    setMaxHeight(height) {
        this._maxHeight = height;
        this._syncHeight();
    }

    setMaximum(maximum) {
        this._maximum = maximum;
        for (const row of this._rows.values())
            row.setMaximum(maximum);
    }

    get summary() {
        return this._summary ?? '';
    }

    _render() {
        const all = this._showingAll && this._mixer.available;
        this._all.visible = all;
        for (const part of [this._master, this._outputs, this._playing, this._nothing, this._foot])
            part.visible = !all;
        if (all) {
            this._renderAll(this._mixer.outputs);
            return;
        }
        this._renderOutputs();
        const apps = this._mixer.available ? Model.playingRows(this._mixer.applications) : [];
        const seen = new Set();
        let index = 0;
        for (const app of apps) {
            seen.add(app.id);
            let row = this._rows.get(app.id);
            if (!row) {
                row = new MediaRow({touch: this._touch});
                row.setMaximum(this._maximum ?? 1);
                row.connect('volume-changed', (r, value) => {
                    this._mixer.setVolume(app.id, value);
                    if (r._state.muted && value > 0)
                        this._mixer.setMuted(app.id, false);
                });
                row.connect('mute-toggled', (r, muted) => this._mixer.setMuted(app.id, muted));
                row.connect('play-pause', () => this._mixer.playPause(app.id));
                row.connect('raise', () => this._mixer.raise(app.id));
                this._rows.set(app.id, row);
                this._list.add_child(row);
            }
            this._list.set_child_at_index(row, index++);
            row.update({
                title: Model.rowTitle(app, app.windowTitle),
                line: Model.rowLine(app, this._mixer.outputs, {
                    muted: _('Muted'), paused: _('Paused'),
                    // Translators: where an app plays, after its name: "on WH-1000XM5"
                    on: name => _('on %s').format(name),
                }),
                name: app.name,
                gicon: app.gicon,
                art: app.art ?? '',
                playing: !!app.playing,
                muted: !!app.muted,
                volume: app.volume ?? 1,
                canPlayPause: !!(app.can_play || app.can_pause),
            });
        }
        for (const [id, row] of this._rows) {
            if (!seen.has(id)) {
                this._rows.delete(id);
                row.destroy();
            }
        }
        const any = this._rows.size > 0;
        this._playing.visible = any;
        this._nothing.visible = !any;
        this._syncHeight();
        const count = Model.playingCount(apps);
        const summary = count > 0
            // Translators: in the Sound page's header, apps that are not paused
            ? ngettext('%d playing', '%d playing', count).format(count) : '';
        if (summary !== this._summary) {
            this._summary = summary;
            this.emit('summary-changed', summary);
        }
    }

    // Three rows, then the list scrolls inside its well.
    _syncHeight() {
        const row = this._touch ? Math.max(tokens.touch.row, C.row) : C.row;
        const fixed = 2 * C.list_padding + 2;
        let room = Infinity;
        if (this._maxHeight && this._playing.visible) {
            // Everything on the page but the list.
            const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
            const [, page] = this.get_preferred_height(-1);
            const [, list] = this._scroll.get_preferred_height(-1);
            room = this._maxHeight - (page - list) / scale - fixed;
        }
        const height = Model.listHeight(this._rows.size, row, C.list_gap, room) + fixed;
        this._scroll.style = `max-height: ${height}px;`;
    }

    _renderOutputs() {
        const outputs = this._mixer.available ? this._mixer.outputs : [];
        const {chips, more} = Model.outputChips(outputs, this._mixer.available ? this._receivers : []);
        const wanted = chips.map(output => ({key: output.name, output}));
        if (more)
            wanted.push({key: '\0more'});
        const layout = this._outputs.layout_manager;
        const signature = wanted.map(w => `${w.key}|${w.output ? Model.outputLabel(w.output) : ''}|` +
            `${w.output?.kind ?? ''}|${w.output?.default ? 1 : 0}|${w.output?.busy ? 1 : 0}`).join('\n');
        if (signature === this._chipSignature)
            return;
        this._chipSignature = signature;
        const focused = this._chips.find(chip => chip.has_key_focus())?._lumaKey;
        this._outputs.destroy_all_children();
        this._chips = [];
        const columns = tokens.output.columns_count;
        wanted.forEach((entry, index) => {
            const {output} = entry;
            const content = new St.BoxLayout({style_class: 'lumaui-sound-chip-content',
                x_align: Clutter.ActorAlign.START, x_expand: true});
            content.add_child(new St.Icon({style_class: 'lumaui-sound-chip-icon',
                icon_name: output ? Model.outputGlyph(output) : 'lumaui-ellipsis-symbolic'}));
            const label = new St.Label({y_align: Clutter.ActorAlign.CENTER, x_expand: true,
                text: output ? Model.outputLabel(output) : _('More…')});
            label.clutter_text.ellipsize = Pango.EllipsizeMode.END;
            content.add_child(label);
            const chip = new St.Button({style_class: 'lumaui-sound-chip', child: content, can_focus: true,
                x_expand: true, accessible_name: label.text});
            chip.x_align = Clutter.ActorAlign.FILL;
            chip._lumaKey = entry.key;
            if (output?.default) {
                chip.add_style_class_name('on');
                chip.add_accessible_state(Atk.StateType.CHECKED);
            }
            // A speaker being connected waits: dimmed, not pressable.
            if (output?.busy) {
                chip.reactive = false;
                chip.opacity = 150;
            }
            chip.connect('clicked', () => {
                if (output?.receiver)
                    this.emit('choose-receiver', output.receiver);
                else if (output)
                    this._mixer.setDefaultOutput(output.name);
                else
                    this.emit('more-outputs');
            });
            layout.attach(chip, index % columns, Math.floor(index / columns), 1, 1);
            this._chips.push(chip);
            if (entry.key === focused)
                chip.grab_key_focus();
        });
        this._outputs.visible = wanted.length > 0;
    }
});
