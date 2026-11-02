// SPDX-License-Identifier: Apache-2.0
// TV posture of the Shell's Quick Options and notification list: the quick
// panel. OK on the bar's weather | time cluster, or a long press of Home,
// opens it as Settings' floating island mirrored onto the right edge.
//
// It is a view, not a second control centre: every row drives the object the
// desktop's Quick Options already own (NetworkManager's Wi-Fi toggle,
// gnome-bluetooth's toggle, the Do Not Disturb setting, the output stream
// slider and its devices, MessageTray's notifications, SystemActions), and a
// row whose backend is absent is not drawn. Restart and Power Off ask in the
// TV dialog (tvDialogs.js). The rules are tvQuickPanelModel.js;
// docs/developer-platform/tv-mode.md ("The quick panel").
import AccountsService from 'gi://AccountsService';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';
import * as SystemActions from 'resource:///org/gnome/shell/misc/systemActions.js';
import {tv as TOKENS} from './tvTokens.js';
import {remoteAction, repeatAllowed} from './tvModel.js';
import {chipState, firstChip, firstStop, keepRow, levelPercent, mixRgba, noteAge, noteRecords, okGate, okPress,
    okRelease, panelRows, scrollTo, sleepAction, stepChip, stepLevel, stepRow, toggleMethod} from './tvQuickPanelModel.js';
import {gettext as _} from 'gettext';

const SETTINGS_APP = 'org.gnome.Settings.desktop';
// Presses closer than this to the last one belong to the same held OK.
const OK_QUIET_MS = 300;
const ACCOUNTS_APP = 'gnome-user-accounts-panel.desktop';
// userWidget.js's poster-avatar palettes: the same person, the same colour.
const AVATAR_PALETTES = [['#8fa5d7', '#596dab'], ['#a998d0', '#7563a5'], ['#81baa6', '#4e887b'],
    ['#d5a379', '#a57453'], ['#b894b6', '#896a8c'], ['#8db6cf', '#597f9f']];
const TOGGLES = [
    // The desktop's Wi-Fi toggle is checked while a network is connected; on a TV the chip
    // shows the radio itself (NetworkManager's wireless-enabled), which is what it switches.
    {key: 'wifi', icon: 'lumaui-wifi-symbolic', title: () => _('Wi-Fi'),
        find: qs => qs._network?._wirelessToggle,
        state: toggle => toggle._client ? {checked: toggle._client.wireless_enabled,
            subtitle: toggle.checked ? toggle.subtitle : ''} : null,
        source: toggle => toggle._client ?? null, signal: 'notify::wireless-enabled'},
    {key: 'bluetooth', icon: 'lumaui-bluetooth-symbolic', title: () => _('Bluetooth'),
        find: qs => qs._bluetooth?.quickSettingsItems?.[0]},
    {key: 'dnd', icon: 'lumaui-moon-symbolic', title: () => _('Do Not Disturb'), stateOnly: true,
        find: qs => qs._doNotDisturb?.quickSettingsItems?.[0]},
];

function closedStates() {
    const states = MessageTray.NotificationLifecycleState ?? {};
    return new Set([states.ACTED, states.DISMISSED, states.EXPIRED].filter(state => state !== undefined));
}

export class TvQuickPanel {
    // `unit` and `opaque` are the TV presentation's; `closed(origin)` runs
    // after the panel shuts, so the bar can take its focus back.
    // `cluster` lends the bar's weather | time cluster while the panel is open: take() hands
    // the actor over, give(actor) puts it back on the bar.
    constructor({unit, opaque, opened = null, closed, cluster = null}) {
        this._cluster = cluster;
        this._unit = unit;
        this._opaque = opaque;
        this._opened = opened;
        this._closed = closed;
        this.isOpen = false;
        this.origin = null;
        this._open = {outputs: false, power: false};
        this._row = 0;
        this._chip = 0;
        this._rows = [];
        this._actions = SystemActions.getDefault();
        this._appSystem = Shell.AppSystem.get_default();
    }

    // ── open and close ────────────────────────────────────────────────────

    toggle(origin) {
        if (this.isOpen) this.close();
        else this.open(origin);
    }

    open(origin = 'remote') {
        if (this.isOpen || Main.sessionMode.isLocked || Main.sessionMode.isGreeter) return;
        // A Shell grab (a dialog, a menu) keeps the remote.
        if (Main.modalCount > 0) return;
        this._open = {outputs: false, power: false};
        this._scrollValue = 0;
        // Polkit's answers do not notify; ask again each time, as Quick Options do.
        this._actions.forceUpdate?.();
        try {
            this._build();
        } catch (error) {
            // Never left half open: no actor means not open.
            this._actor?.destroy();
            this._actor = null;
            throw error;
        }
        this.isOpen = true;
        this.origin = origin;
        // One action per OK press; the OK that opened the panel from the bar is still down.
        this._okGate = okGate(origin === 'bar' ? GLib.get_monotonic_time() / 1000 : null);
        this._watch();
        this._rebuild();
        this._row = Math.max(0, firstStop(this._rows));
        this._chip = this._rows[this._row]?.kind === 'chips' ? firstChip(this._rows[this._row].chips) : 0;
        this._draw();
        this._grab = Main.pushModal(this._actor, {actionMode: Shell.ActionMode.POPUP});
        this._actor.grab_key_focus();
        // The sheet slides in from the screen edge under the cluster, which never moves.
        this._island.translation_x = this._island.width;
        this._island.ease({translation_x: 0, duration: TOKENS.motion.sheet_ms * 1.6,
            mode: Clutter.AnimationMode.EASE_OUT_CUBIC});
        this._scrim.opacity = 0;
        this._scrim.ease({opacity: 255, duration: TOKENS.motion.sheet_ms, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        this._adopt();
        this._opened?.(origin);
    }

    // Shuts the panel; `then` runs once its grab is gone (an app, a dialog or
    // suspend may follow, and must get the keys).
    close(then = null, immediate = false) {
        if (!this.isOpen) {
            then?.();
            return;
        }
        this.isOpen = false;
        if (this._heldBack) console.log(`TV quick panel: ${this._heldBack} OK presses of a held OK were held back`);
        this._heldBack = 0;
        this._unwatch();
        if (this._grab) Main.popModal(this._grab);
        this._grab = null;
        const actor = this._actor, island = this._island, scrim = this._scrim;
        this._actor = null;
        const done = () => {
            this._returnCluster();
            actor?.destroy();
        };
        if (immediate || !island) {
            done();
        } else {
            // The sheet slides back out; the cluster stays where it is and goes back to the bar.
            scrim?.ease({opacity: 0, duration: TOKENS.motion.sheet_ms, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
            island.ease({translation_x: island.width, duration: TOKENS.motion.sheet_ms * 1.4,
                mode: Clutter.AnimationMode.EASE_IN_CUBIC, onStopped: done});
        }
        const origin = this.origin;
        this.origin = null;
        this._closed?.(origin);
        then?.();
    }

    destroy() {
        this.close(null, true);
    }

    // The bar's cluster joins the panel at the same place on screen (both cover the monitor),
    // above the sheet, as the right of its header.
    _adopt() {
        const now = this._cluster?.take();
        if (!now || !this._actor) return;
        now.get_parent()?.remove_child(now);
        this._actor.add_child(now);
        this._adopted = now;
    }

    _returnCluster() {
        const now = this._adopted;
        this._adopted = null;
        if (!now) return;
        now.get_parent()?.remove_child(now);
        this._cluster?.give(now);
    }

    // ── the backends: the desktop's own objects ───────────────────────────

    _quickSettings() {
        return Main.panel?.statusArea?.quickSettings ?? null;
    }

    _toggles() {
        const qs = this._quickSettings();
        if (!qs) return [];
        return TOGGLES.map(spec => {
            const toggle = spec.find(qs);
            if (!toggle) return null;
            const state = spec.state?.(toggle) ?? {checked: toggle.checked, subtitle: toggle.subtitle};
            return {key: spec.key, toggle, icon: spec.icon, title: spec.title(), visible: toggle.visible,
                reactive: toggle.reactive, checked: state.checked, subtitle: state.subtitle,
                toggleMode: toggle.toggle_mode, stateOnly: Boolean(spec.stateOnly),
                source: spec.source?.(toggle) ?? null, signal: spec.signal ?? null};
        }).filter(Boolean);
    }

    _volume() {
        return this._quickSettings()?._volumeOutput?.quickSettingsItems?.[0] ?? null;
    }

    _level() {
        const item = this._volume();
        if (!item?.visible || !item.slider || !item.stream) return {visible: false};
        const outputs = [...(item._deviceItems?.keys() ?? [])].map(id => {
            const device = item._lookupDevice?.(id);
            return device ? {id, name: device.description, gicon: item._deviceIcon?.(device) ?? null} : null;
        }).filter(Boolean);
        const current = item._activeDeviceId ?? null;
        const active = outputs.find(output => output.id === current);
        return {visible: true, item, outputs, current, name: active?.name ?? item.levelDetail ?? _('Volume'),
            gicon: active?.gicon ?? null, value: item.stream.is_muted ? 0 : item.slider.value,
            maximum: item.slider.maximum_value || 1, muted: item.stream.is_muted};
    }

    _notes() {
        const closed = closedStates();
        const time = note => note.datetime?.to_unix?.() ?? 0;
        const now = GLib.DateTime.new_now_local().to_unix();
        return noteRecords(Main.messageTray.getSources(), closed, TOKENS.quick_panel.notes_max, time)
            .map(note => {
                let body = (note.body ?? '').replace(/\s+/g, ' ').trim();
                if (note.useBodyMarkup) body = body.replace(/<[^>]*>/g, '');
                return {id: note.sortSequence, native: note, title: note.title || note.source?.title || '',
                    body, gicon: note.gicon ?? note.source?.icon ?? null, age: noteAge(now - time(note)),
                    clearable: note.urgency !== MessageTray.Urgency.CRITICAL};
            });
    }

    _user() {
        const user = AccountsService.UserManager.get_default().get_user(GLib.get_user_name());
        const name = (user?.is_loaded && user.get_real_name()) || GLib.get_real_name() || GLib.get_user_name();
        let picture = user?.is_loaded ? user.get_icon_file() : null;
        if (picture && !GLib.file_test(picture, GLib.FileTest.EXISTS)) picture = null;
        return {user, name, picture};
    }

    _state() {
        const accounts = this._appSystem.lookup_app(ACCOUNTS_APP);
        const settings = this._appSystem.lookup_app(SETTINGS_APP);
        this._levelState = this._level();
        this._noteList = this._notes();
        this._userState = this._user();
        return {
            me: {name: this._userState.name, focusable: Boolean(accounts) && Main.sessionMode.allowSettings},
            toggles: this._toggles(),
            level: this._levelState,
            notes: this._noteList,
            settings: Boolean(settings) && Main.sessionMode.allowSettings,
            sleep: true,
            power: {restart: this._actions.can_restart, powerOff: this._actions.can_power_off},
            open: this._open,
        };
    }

    // Anything the panel shows may change under it: redraw on the next idle.
    _watch() {
        const later = () => this._queueRebuild();
        for (const {toggle, source, signal} of this._toggles()) {
            toggle.connectObject('notify::checked', later, 'notify::subtitle', later, 'notify::visible', later,
                'notify::reactive', later, this);
            source?.connectObject(signal, later, this);
        }
        const item = this._volume();
        if (item) {
            item.connectObject('notify::visible', later, 'stream-updated', later, this);
            item.slider?.connectObject('notify::value', later, this);
            item._control?.connectObject('active-output-update', later, 'output-added', later,
                'output-removed', later, this);
        }
        const watchSource = source => source.connectObject('notification-added', later,
            'notification-removed', later, this);
        Main.messageTray.getSources().forEach(watchSource);
        Main.messageTray.connectObject('source-added', (_tray, source) => {
            watchSource(source);
            later();
        }, 'source-removed', later, this);
        this._actions.connectObject('notify::can-restart', later, 'notify::can-power-off', later, this);
        const user = AccountsService.UserManager.get_default().get_user(GLib.get_user_name());
        user?.connectObject('notify::is-loaded', later, 'changed', later, this);
        this._watched = {user, item};
    }

    _unwatch() {
        if (this._rebuildId) GLib.source_remove(this._rebuildId);
        this._rebuildId = 0;
        for (const {toggle, source} of this._toggles()) {
            toggle.disconnectObject(this);
            source?.disconnectObject(this);
        }
        const item = this._watched?.item;
        item?.disconnectObject(this);
        item?.slider?.disconnectObject(this);
        item?._control?.disconnectObject(this);
        for (const source of Main.messageTray.getSources()) source.disconnectObject(this);
        Main.messageTray.disconnectObject(this);
        this._actions.disconnectObject(this);
        this._watched?.user?.disconnectObject(this);
        this._watched = null;
    }

    _queueRebuild() {
        if (this._rebuildId || !this.isOpen) return;
        this._rebuildId = GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
            this._rebuildId = 0;
            if (this.isOpen) {
                this._rebuild();
                this._draw();
            }
            return GLib.SOURCE_REMOVE;
        });
    }

    // ── the look ──────────────────────────────────────────────────────────

    _px(value) {
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor || 1;
        return `${(value * this._u / scale).toFixed(2)}px`;
    }

    _m(name) {
        const value = TOKENS.materials[name];
        if (!this._opaque()) return value;
        return value.replace(/rgba\(([^,]+),([^,]+),([^,]+),[^)]+\)/, 'rgba($1,$2,$3, 1)');
    }

    _font(size, weight) {
        return `font-family: Figtree, sans-serif; font-size: ${this._px(size)}; font-weight: ${weight};`;
    }

    _keyLook() {
        // St reads background-gradient-start as the background colour: nothing may set
        // background-color after it, or the gradient starts from that colour.
        return `background-gradient-direction: vertical; background-gradient-start: ${this._m('key_top')};
            background-gradient-end: ${this._m('key_bottom')};
            border: ${this._px(1.5)} solid ${this._m('key_rim')};
            box-shadow: 0 ${this._px(3)} ${this._px(10)} ${this._m('key_drop')};`;
    }

    _plain() {
        return `background-gradient-direction: none; background-color: transparent;
            border: ${this._px(1.5)} solid transparent; box-shadow: none;`;
    }

    // ── building ──────────────────────────────────────────────────────────

    _build() {
        const monitor = Main.layoutManager.primaryMonitor;
        this._u = this._unit();
        this._actor = new St.Widget({name: 'lumaTvQuickPanel', reactive: true, can_focus: true,
            layout_manager: new Clutter.FixedLayout(), accessible_name: _('Quick panel')});
        Main.layoutManager.addTopChrome(this._actor);
        this._actor.set_position(monitor.x, monitor.y);
        this._actor.set_size(monitor.width, monitor.height);
        this._actor.connect('key-press-event', (_actor, event) => this._key(event));
        this._actor.connect('key-release-event', (_actor, event) => {
            if (remoteAction(Clutter.keyval_name(event.get_key_symbol()) ?? '') === 'ok') okRelease(this._okGate);
            return Clutter.EVENT_STOP;
        });
        // A click outside the island closes it.
        this._actor.connect('button-press-event', (_actor, event) => {
            const [x, y] = event.get_coords();
            const [ok, ix, iy] = this._island.transform_stage_point(x, y);
            if (!ok || ix < 0 || iy < 0 || ix > this._island.width || iy > this._island.height) this.close();
            return Clutter.EVENT_STOP;
        });

        const {quick_panel: q, layout} = TOKENS;
        const u = this._u;
        // The page under the panel steps back: darkened toward the panel's edge, as in the
        // mockup, so nothing under it reads as part of the panel or as live.
        this._scrim = new St.Widget({width: monitor.width, height: monitor.height});
        this._scrim.style = `background-gradient-direction: horizontal;
            background-gradient-start: ${this._m('scrim').replace(/[\d.]+\)$/, '0.12)')};
            background-gradient-end: ${this._m('scrim')};`;
        this._actor.add_child(this._scrim);
        // A full-height sheet from the right screen edge; its column ends on the side safe line.
        const width = q.sheet_width * u;
        this._island = new St.Widget({layout_manager: new Clutter.FixedLayout(), width, height: monitor.height});
        this._island.set_position(monitor.width - width, 0);
        this._island.style = `border-radius: ${this._px(q.radius)} 0 0 ${this._px(q.radius)};
            background-color: ${this._m('sheet')};
            box-shadow: ${this._px(-18)} 0 ${this._px(50)} ${this._m('bar_drop')};`;
        // The header: you, on the bar's line; the bar's cluster sits over its right.
        this._header = new St.Bin({width: q.header_width * u, x_align: Clutter.ActorAlign.START});
        this._header.set_position((q.column_left - q.row_padding_x) * u, q.header_top * u);
        this._island.add_child(this._header);
        this._list = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true, width: q.width * u});
        this._list.style = `spacing: ${this._px(q.gap)};`;
        this._scroll = new St.ScrollView({child: this._list, x_expand: true,
            hscrollbar_policy: St.PolicyType.NEVER, vscrollbar_policy: St.PolicyType.EXTERNAL,
            overlay_scrollbars: true, enable_mouse_scrolling: true});
        // Long, subtle fades, only at an edge where the rows continue.
        try {
            const margins = new Clutter.Margin();
            margins.top = q.fade_top * u;
            margins.bottom = q.fade_bottom * u;
            this._scroll.add_effect_with_name('luma-tv-fade',
                new St.ScrollViewFade({fade_margins: margins, fade_edges: false}));
        } catch (error) {
            console.warn(`TV quick panel: scroll fades unavailable (${error.message})`);
            this._scroll.style = `-st-vfade-offset: ${this._px(q.fade_top)};`;
        }
        this._scroll.set_position(q.column_left * u, (q.list_top + q.padding) * u);
        this._scroll.width = q.width * u;
        this._island.add_child(this._scroll);
        this._actor.add_child(this._island);
        this._maxHeight = monitor.height - (q.list_top + q.padding + layout.safe_y) * u;
    }

    _rebuild() {
        if (!this._list) return;
        const key = this._rows[this._row]?.key;
        this._rows = panelRows(this._state());
        this._row = keepRow(this._rows, key, this._row);
        const chips = this._rows.find(row => row.kind === 'chips')?.chips ?? [];
        this._chip = Math.min(this._chip, Math.max(0, chips.length - 1));
        if (chips[this._chip]?.reactive === false) this._chip = firstChip(chips);
        this._list.destroy_all_children();
        this._header.set_child(null);
        this._actors = this._rows.map(row => this._makeRow(row));
        this._actors.forEach((actor, index) => {
            if (this._rows[index].kind === 'me') {
                actor.width = TOKENS.quick_panel.header_width * this._u;
                this._header.set_child(actor);
            }
            else this._list.add_child(actor);
        });
        // Row tops from their natural heights: fresh rows have no allocation
        // yet, and the focused one must stay in view across a rebuild.
        const {quick_panel: q} = TOKENS;
        const width = q.width * this._u;
        let top = 0;
        this._tops = this._actors.map((actor, index) => {
            if (this._rows[index].kind === 'me') return null;
            // Natural height plus the CSS margins St puts on the actor (separators have them).
            const [, natural] = actor.get_preferred_height(width);
            const height = natural + actor.margin_top + actor.margin_bottom;
            const span = [top, top + height];
            top += height + q.gap * this._u;
            return span;
        });
        // The list's own natural height (spacing and margins as its layout counts them), so the
        // island holds every row and fades only when it really scrolls.
        this._contentHeight = Math.max(top - q.gap * this._u, this._list.get_preferred_height(width)[1]);
        this._scroll.height = Math.min(this._contentHeight, this._maxHeight);
    }

    _label(text, size, weight, color, extra = '') {
        const label = new St.Label({text, y_align: Clutter.ActorAlign.CENTER});
        label.clutter_text.ellipsize = Pango.EllipsizeMode.END;
        label.style = `${this._font(size, weight)} color: ${color}; ${extra}`;
        return label;
    }

    _icon(params, size, color) {
        const icon = new St.Icon({...params, y_align: Clutter.ActorAlign.CENTER});
        icon.style = `icon-size: ${this._px(size)}; color: ${color};`;
        return icon;
    }

    // A row: lead, a title (and a quiet second line), something at the end.
    _line({icon, gicon, lead, title, sub, end, endIcon, height, inset, size}) {
        const {quick_panel: q} = TOKENS;
        const row = new St.BoxLayout({x_expand: true, reactive: true});
        row._parts = [];
        row.style = `min-height: ${this._px(height ?? q.row_height)}; padding: 0 ${this._px(q.row_padding_x)}
            0 ${this._px(inset ?? q.row_padding_x)}; border-radius: ${this._px(q.row_radius)};
            spacing: ${this._px(q.icon_gap)};`;
        if (lead) row.add_child(lead);
        else if (icon || gicon) row.add_child(row._lead = this._icon(icon ? {icon_name: icon} : {gicon}, q.icon, this._m('ink_2')));
        const text = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true,
            y_align: Clutter.ActorAlign.CENTER});
        row._title = this._label(title, size ?? q.row_size, q.row_weight, this._m('ink'));
        text.add_child(row._title);
        if (sub) text.add_child(row._sub = this._label(sub, q.end_size, 500, this._m('ink_3')));
        row.add_child(text);
        if (end) row.add_child(row._end = this._label(end, q.end_size, 600, this._m('ink_3')));
        if (endIcon) row.add_child(row._endIcon = this._icon({icon_name: endIcon}, q.end_icon, this._m('ink_3')));
        return row;
    }

    _makeRow(row) {
        const {quick_panel: q} = TOKENS;
        switch (row.kind) {
        case 'sep': {
            const sep = new St.Widget({x_expand: true});
            sep.style = `height: ${this._px(1.5)}; margin: ${this._px(q.sep_margin)} ${this._px(q.sep_inset)};
                background-color: ${this._m('divider').replace(/[\d.]+\)$/, '0.1)')};`;
            return sep;
        }
        case 'label': {
            const box = new St.BoxLayout({x_expand: true});
            box.style = `padding: ${this._px(q.label_padding_top)} ${this._px(q.row_padding_x)}
                ${this._px(q.label_padding_bottom)};`;
            const spec = `letter-spacing: ${this._px(q.label_size * 0.05)};`;
            const heading = this._label(_('Notifications').toLocaleUpperCase(), q.label_size, q.label_weight,
                this._m('ink_3'), spec);
            heading.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
            box.add_child(heading);
            box.add_child(new St.Widget({x_expand: true}));
            box.add_child(this._label(String(row.count), q.label_size, q.label_weight, this._m('ink_3')));
            return box;
        }
        case 'me': return this._makeMe(row);
        case 'chips': return this._makeChips(row);
        case 'level': return this._makeLevel();
        case 'output':
            return this._line({title: row.output.name, height: q.sub_height, inset: q.sub_inset, size: q.sub_size,
                endIcon: row.current ? 'lumaui-check-symbolic' : null});
        case 'note': return this._makeNote(row.note);
        case 'clear': return this._line({icon: 'lumaui-x-symbolic', title: _('Clear all')});
        case 'settings':
            return this._line({icon: 'lumaui-cog-symbolic', title: _('All Settings'), endIcon: 'lumaui-chevron-right-symbolic'});
        case 'sleep': return this._line({icon: 'lumaui-moon-symbolic', title: _('Sleep')});
        case 'power':
            return this._line({icon: 'lumaui-power-symbolic', title: _('Power'),
                endIcon: row.open ? 'lumaui-chevron-up-symbolic' : 'lumaui-chevron-down-symbolic'});
        case 'restart':
            return this._line({title: _('Restart…'), height: q.sub_height, inset: q.sub_inset, size: q.sub_size});
        case 'power-off': {
            const line = this._line({title: _('Power Off…'), height: q.sub_height, inset: q.sub_inset, size: q.sub_size});
            line._danger = true;
            return line;
        }
        }
        return new St.Widget();
    }

    _makeMe() {
        const {quick_panel: q} = TOKENS;
        const {name, picture, user} = this._userState;
        const size = q.avatar;
        const avatar = new St.Bin({y_align: Clutter.ActorAlign.CENTER});
        const round = `width: ${this._px(size)}; height: ${this._px(size)}; border-radius: ${this._px(size / 2)};`;
        if (picture) {
            avatar.style = `${round} background-image: url("${picture}"); background-size: cover;`;
        } else {
            const login = user?.get_user_name?.() ?? GLib.get_user_name();
            const hash = [...login].reduce((value, char) => (value * 31 + char.codePointAt(0)) >>> 0, 0);
            const [light, dark] = AVATAR_PALETTES[hash % AVATAR_PALETTES.length];
            avatar.style = `${round} background-gradient-direction: vertical; background-gradient-start: ${light};
                background-gradient-end: ${dark};`;
            const initial = [...name.trim()][0]?.toLocaleUpperCase() ?? '';
            avatar.set_child(this._label(initial, size * 0.46, 700, '#ffffff', 'text-align: center;'));
            avatar.get_child().x_align = Clutter.ActorAlign.CENTER;
        }
        const line = this._line({lead: avatar, title: name, height: q.me_height, size: q.name_size,
            endIcon: this._rows.find(row => row.kind === 'me')?.focusable ? 'lumaui-chevron-right-symbolic' : null});
        line._title.style = `${this._font(q.name_size, q.name_weight)} color: ${this._m('ink')};`;
        return line;
    }

    _makeChips(row) {
        const {quick_panel: q} = TOKENS;
        const box = new St.BoxLayout({x_expand: true});
        box.layout_manager.homogeneous = true;
        box.style = `spacing: ${this._px(q.chip_gap)}; padding: ${this._px(4)} 0;`;
        box._chips = row.chips.map(chip => {
            const tile = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true, reactive: true});
            tile._chip = chip;
            tile._icon = this._icon({icon_name: chip.icon, x_align: Clutter.ActorAlign.START}, q.icon, this._m('ink_2'));
            tile.add_child(tile._icon);
            tile.add_child(new St.Widget({y_expand: true}));
            tile._title = this._label(chip.title, q.chip_title_size, q.chip_title_weight, this._m('ink_2'));
            tile._state = this._label(chipState(chip, {on: _('On'), off: _('Off')}), q.chip_state_size, 550,
                this._m('ink_3'));
            tile.add_child(tile._title);
            tile.add_child(tile._state);
            box.add_child(tile);
            return tile;
        });
        return box;
    }

    _makeLevel() {
        const {quick_panel: q} = TOKENS;
        const level = this._levelState;
        const u = this._u;
        const icon = level.muted ? {icon_name: 'lumaui-volume-x-symbolic'}
            : level.gicon ? {gicon: level.gicon} : {icon_name: 'lumaui-volume-2-symbolic'};
        const row = this._line({...('gicon' in icon ? {gicon: icon.gicon} : {icon: icon.icon_name}),
            title: level.name, height: q.level_height, size: q.end_size});
        row._title.style = `${this._font(q.end_size, 500)} color: ${this._m('ink_3')};`;
        const text = row._title.get_parent();
        text.style = `spacing: ${this._px(8)};`;
        const bar = new St.BoxLayout({x_expand: true});
        bar.style = `spacing: ${this._px(14)};`;
        // The track's width is the row's, less its lead, gaps and the value.
        const width = (q.width - 2 * q.padding - 2 * q.row_padding_x - q.icon - q.icon_gap - 14 -
            q.level_value_width) * u;
        const track = new St.Widget({layout_manager: new Clutter.FixedLayout(), width,
            height: q.level_track * u, y_align: Clutter.ActorAlign.CENTER});
        const fill = new St.Widget({height: q.level_track * u});
        fill.width = Math.max(0, Math.min(1, level.value / level.maximum)) * width;
        track.add_child(fill);
        row._track = track;
        row._fill = fill;
        row._hint = this._label('‹ ›', 20, 700, this._m('key_ink'), 'letter-spacing: 0.3em;');
        row._hint.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
        row._hint.visible = false;
        row._value = this._label(String(levelPercent(level.value)), q.level_value_size, 650, this._m('ink'),
            `width: ${this._px(q.level_value_width)}; text-align: right; font-feature-settings: "tnum";`);
        bar.add_child(track);
        bar.add_child(row._hint);
        bar.add_child(row._value);
        text.add_child(bar);
        row._styleTrack = focused => {
            track.style = `border-radius: ${this._px(q.level_track / 2)}; background-color:
                ${focused ? 'rgba(0, 0, 0, 0.14)' : this._m('well')};
                border: ${this._px(1.5)} solid ${focused ? 'transparent' : this._m('well_rim')};`;
            fill.style = `border-radius: ${this._px(q.level_track / 2)}; background-color: ${this._m('accent')};`;
            // The hint takes its room from the track while focused.
            track.width = width - (focused ? row._hint.get_preferred_width(-1)[1] + 14 * u : 0);
            fill.width = Math.max(0, Math.min(1, level.value / level.maximum)) * track.width;
        };
        return row;
    }

    _makeNote(note) {
        const {quick_panel: q} = TOKENS;
        const icon = note.gicon ? this._icon({gicon: note.gicon}, q.note_icon, this._m('ink_2'))
            : this._icon({icon_name: 'lumaui-bell-symbolic'}, q.note_icon * 0.7, this._m('ink_2'));
        const age = {now: _('now'), min: _('%d min'), h: _('%d h'), d: _('%d d')}[note.age.unit]
            .replace('%d', String(note.age.value));
        const row = this._line({lead: icon, title: note.title, sub: note.body || null, end: age,
            height: q.note_height, size: q.note_title_size});
        row._lead = icon;
        row._end.y_align = Clutter.ActorAlign.START;
        row._end.style += `margin-top: ${this._px(16)};`;
        if (row._sub) row._sub.style = `${this._font(q.note_body_size, 500)} color: ${this._m('ink_3')};`;
        return row;
    }

    // ── drawing the focus: exactly one bright thing ───────────────────────

    _draw() {
        if (!this._actors) return;
        const {quick_panel: q} = TOKENS;
        this._rows.forEach((row, index) => {
            const actor = this._actors[index];
            const focused = index === this._row;
            if (row.kind === 'chips') return this._drawChips(actor, focused);
            if (row.kind === 'sep' || row.kind === 'label') return;
            const base = actor.style.replace(/background-gradient-direction:[^]*$/, '');
            actor.style = base + (focused ? this._keyLook() : this._plain());
            const ink = focused ? (actor._danger ? this._m('danger_on_key') : this._m('key_ink')) : this._m('ink');
            const quiet = focused ? this._m('key_ink').replace(/[\d.]+\)$/, '0.62)') : this._m('ink_3');
            if (row.kind !== 'level') actor._title.style = actor._title.style.replace(/color:[^;]*;/, `color: ${ink};`);
            actor._sub && (actor._sub.style = actor._sub.style.replace(/color:[^;]*;/, `color: ${quiet};`));
            actor._end && (actor._end.style = actor._end.style.replace(/color:[^;]*;/, `color: ${quiet};`));
            actor._endIcon && (actor._endIcon.style = actor._endIcon.style.replace(/color:[^;]*;/, `color: ${quiet};`));
            if (actor._lead && row.kind !== 'note')
                actor._lead.style = actor._lead.style.replace(/color:[^;]*;/, `color: ${focused ? ink : this._m('ink_2')};`);
            if (row.kind === 'level') {
                actor._title.style = actor._title.style.replace(/color:[^;]*;/, `color: ${quiet};`);
                actor._value.style = actor._value.style.replace(/color:[^;]*;/, `color: ${focused ? ink : this._m('ink')};`);
                actor._hint.visible = focused;
                actor._styleTrack(focused);
            }
        });
        // The focused row stays in view, with room under it.
        const span = this._tops?.[this._row];
        const adjustment = this._scroll.vadjustment;
        if (adjustment && this._contentHeight > this._scroll.height && (span || this._scrollValue)) {
            // The header (you) is above the list: the list goes back to its top.
            const value = span ? scrollTo(span[0], span[1], this._scroll.height, this._contentHeight,
                q.scroll_margin * this._u, this._scrollValue ?? 0) : 0;
            this._scrollValue = value;
            adjustment.ease(value, {duration: TOKENS.motion.page_ms, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        }
    }

    _drawChips(box, focused) {
        const {quick_panel: q} = TOKENS;
        box._chips.forEach((tile, index) => {
            const chip = tile._chip;
            const key = focused && index === this._chip;
            const shape = `height: ${this._px(q.chip_height)}; padding: ${this._px(q.chip_padding)}
                ${this._px(q.chip_padding + 2)}; border-radius: ${this._px(q.chip_radius)};`;
            let look, ink, icon;
            if (key) {
                look = this._keyLook();
                ink = this._m('key_ink');
                icon = chip.checked ? this._m('accent_low') : ink;
            } else if (chip.checked) {
                // On: the chip material lit softly with the accent from above (St's radial
                // gradient is a centred disc, which reads as a spot, not light).
                look = `background-gradient-direction: vertical; background-gradient-start:
                    ${mixRgba(this._m('chip'), this._m('accent'), 0.34)}; background-gradient-end: ${this._m('chip')};
                    border: ${this._px(1.5)} solid ${this._m('chip_rim')};
                    box-shadow: 0 ${this._px(2)} ${this._px(5)} ${this._m('chip_drop')};`;
                ink = this._m('ink');
                icon = this._m('accent_focus');
            } else {
                look = `background-gradient-direction: none; background-color: ${this._m('well')};
                    border: ${this._px(1.5)} solid ${this._m('well_rim')}; box-shadow: none;`;
                ink = this._m('ink_2');
                icon = ink;
            }
            tile.style = shape + look;
            tile.opacity = chip.reactive === false ? 110 : 255;
            tile._icon.style = `icon-size: ${this._px(q.icon)}; color: ${icon};`;
            tile._title.style = tile._title.style.replace(/color:[^;]*;/, `color: ${ink};`);
            tile._state.style = tile._state.style.replace(/color:[^;]*;/,
                `color: ${key ? this._m('key_ink').replace(/[\d.]+\)$/, '0.62)') : this._m('ink_3')};`);
        });
    }

    // ── the remote ────────────────────────────────────────────────────────

    _key(event) {
        const action = remoteAction(Clutter.keyval_name(event.get_key_symbol()) ?? '');
        if (!action) return Clutter.EVENT_STOP;
        if (action === 'ok' && (event.get_flags() & Clutter.EventFlags.FLAG_REPEATED)) return Clutter.EVENT_STOP;
        if (action === 'ok' && !okPress(this._okGate, GLib.get_monotonic_time() / 1000, OK_QUIET_MS)) {
            // Presses of a held OK that arrive without the repeat flag: counted for the journal.
            this._heldBack = (this._heldBack ?? 0) + 1;
            return Clutter.EVENT_STOP;
        }
        if (!repeatAllowed(action) && (event.get_flags() & Clutter.EventFlags.FLAG_REPEATED)) return Clutter.EVENT_STOP;
        const row = this._rows[this._row];
        if (action === 'back') {
            if (this._open.outputs || this._open.power) {
                const back = this._open.power ? 'power' : 'level';
                this._open = {outputs: false, power: false};
                this._rebuild();
                this._row = Math.max(0, this._rows.findIndex(r => r.key === back));
            } else {
                this.close();
                return Clutter.EVENT_STOP;
            }
        } else if (action === 'up' || action === 'down') {
            const next = stepRow(this._rows, this._row, action);
            if (next !== this._row && this._rows[next].kind === 'chips') this._chip = firstChip(this._rows[next].chips);
            this._row = next;
        } else if (action === 'left' || action === 'right') {
            if (row?.kind === 'chips') {
                const next = stepChip(row.chips, this._chip, action);
                if (next < 0) {
                    this.close();
                    return Clutter.EVENT_STOP;
                }
                this._chip = next;
            } else if (row?.kind === 'level') {
                this._stepVolume(action);
            } else if (action === 'left') {
                // Left off the island's edge closes it.
                this.close();
                return Clutter.EVENT_STOP;
            }
        } else if (action === 'ok') {
            if (this._ok(row) === false) return Clutter.EVENT_STOP;
        }
        this._draw();
        return Clutter.EVENT_STOP;
    }

    _stepVolume(direction) {
        const level = this._level();
        if (!level.visible) return;
        const {slider} = level.item;
        const value = stepLevel(level.value, direction, TOKENS.quick_panel.level_step, level.maximum);
        // The slider's own handler sets the stream (and unmutes it).
        slider.value = value;
        this._rebuild();
    }

    // OK on a row. Returns false when the panel closed.
    _ok(row) {
        if (!row) return true;
        switch (row.kind) {
        case 'me':
            this.close(() => this._appSystem.lookup_app(ACCOUNTS_APP)?.activate());
            return false;
        case 'chips': {
            const chip = row.chips[this._chip];
            if (!chip || chip.reactive === false) return true;
            if (toggleMethod(chip) === 'flip') chip.toggle.checked = !chip.toggle.checked;
            else chip.toggle.emit('clicked', 0);
            this._queueRebuild();
            return true;
        }
        case 'level':
            if (this._levelState.outputs.length > 1) {
                this._open = {...this._open, outputs: !this._open.outputs};
                this._rebuild();
            }
            return true;
        case 'output': {
            const item = this._volume();
            const device = item?._lookupDevice?.(row.output.id);
            if (device) item._activateDevice(device);
            this._open = {...this._open, outputs: false};
            this._rebuild();
            this._row = Math.max(0, this._rows.findIndex(r => r.kind === 'level'));
            return true;
        }
        case 'note': {
            const native = row.note.native;
            // OK opens it: the notification's own default action, as a click
            // on its card in the tray does.
            this.close(() => native.activate());
            return false;
        }
        case 'clear':
            for (const note of this._noteList) {
                if (note.clearable) note.native.destroy(MessageTray.NotificationDestroyedReason.DISMISSED);
            }
            this._rebuild();
            // The focus goes to the row that takes the list's place.
            this._row = keepRow(this._rows, 'settings', this._row);
            return true;
        case 'settings':
            this.close(() => this._appSystem.lookup_app(SETTINGS_APP)?.activate());
            return false;
        case 'sleep':
            this.close(() => {
                if (sleepAction(this._actions.can_suspend) === 'suspend') this._actions.activateSuspend();
                else Main.screenShield?.activate(true);
            });
            return false;
        case 'power':
            this._open = {...this._open, power: !this._open.power};
            this._rebuild();
            return true;
        case 'restart':
            this.close(() => this._actions.activateRestart());
            return false;
        case 'power-off':
            this.close(() => this._actions.activatePowerOff());
            return false;
        }
        return true;
    }
}
