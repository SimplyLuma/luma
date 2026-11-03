// SPDX-License-Identifier: Apache-2.0
// TV posture: the app switcher, the TV posture of the shell's own running
// apps (Shell.AppSystem, newest first in the desktop's Alt+Tab order) and of
// the desktop's Quit. Home pressed twice opens it over the TV home: the
// running apps as cards (the window's latest picture through a clone, as the
// window switcher draws it, else the app's TV tile), the shared bar staying
// above the scrim. Left/Right move, OK switches, Back returns to where it was
// opened. Up or a held OK drops the card's menu: Close asks the app to quit
// (an app's own save prompts keep their meaning); an app still running after
// tv.app_switcher.force_after_ms offers Force Close, which ends its windows as
// the desktop's "not responding" dialog does. TvItemMenu is the TV item menu
// (tv.menu) the TV home's tiles share. Rules in tvAppSwitcherModel.js;
// docs/developer-platform/tv-mode.md ("Closing apps").
import Clutter from 'gi://Clutter';
import Cogl from 'gi://Cogl';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Graphene from 'gi://Graphene';
import Meta from 'gi://Meta';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {tv as TOKENS} from './tvTokens.js';
import {remoteAction, repeatAllowed} from './tvModel.js';
import {okGate, okPress, okRelease} from './tvQuickPanelModel.js';
import {afterRemoval, cardMenu, closeState, rowScroll, shownApp, stepCard, switcherApps} from './tvAppSwitcherModel.js';
import {gettext as _} from 'gettext';

// Presses closer than this to the last one belong to the same held OK.
const OK_QUIET_MS = 300;
const now = () => GLib.get_monotonic_time() / 1000;

// The desktop's Quit (sessionApplications.js: D-Bus activated apps without a
// window count as running too); a shell without it asks the app directly.
let sessionApplications = null;
import('resource:///org/gnome/shell/ui/sessionApplications.js')
    .then(module => { sessionApplications = module.getDefault?.() ?? null; })
    .catch(() => {});

export function appRunning(app) {
    if (!app) return false;
    if (sessionApplications) return sessionApplications.isRunning(app);
    return app.state !== Shell.AppState.STOPPED;
}

// Close: the desktop's Quit. Never a kill: the app may ask to save first.
export function requestQuit(app) {
    try {
        const asked = sessionApplications ? sessionApplications.requestQuit(app) : app.request_quit();
        Promise.resolve(asked).catch(error => console.warn(`TV: quit ${app.get_id()} failed (${error.message})`));
    } catch (error) {
        console.warn(`TV: quit ${app.get_id()} failed (${error.message})`);
    }
}

// An app people open: never a system prompt (shownApp).
export function switcherApp(app) {
    return !!app && shownApp(app.get_app_info?.() ?? app.app_info ?? null);
}

function runningApps() {
    return sessionApplications?.getRunning() ?? Shell.AppSystem.get_default().get_running();
}

function normalWindow(window) {
    return window && window.get_window_type() === Meta.WindowType.NORMAL &&
        !window.is_override_redirect() && !window.is_skip_taskbar() && !window.get_transient_for();
}

// A card's rounded corners over a window's picture, as the shell rounds
// windows (luma window corners); a renderer without shaders keeps them square.
const CardCorners = GObject.registerClass(
class CardCorners extends Shell.GLSLEffect {
    vfunc_build_pipeline() {
        this.add_glsl_snippet(Cogl.SnippetHook.FRAGMENT, 'uniform vec2 size; uniform float radius;', `
vec2 point = cogl_tex_coord_in[0].st * size;
vec2 inner = abs(point - size * 0.5) - (size * 0.5 - vec2(radius));
float distance = length(max(inner, vec2(0.0))) + min(max(inner.x, inner.y), 0.0) - radius;
cogl_color_out *= clamp(0.5 - distance, 0.0, 1.0);
`, false);
    }
});

function roundCorners(actor, width, height, radius) {
    try {
        const effect = new CardCorners();
        actor.add_effect(effect);
        effect.set_uniform_float(effect.get_uniform_location('size'), 2, [width, height]);
        effect.set_uniform_float(effect.get_uniform_location('radius'), 1, [radius]);
    } catch (error) {
        console.warn(`TV app switcher: square card corners (${error.message})`);
    }
}

// Lengths and materials, the TV home's: `unit()` TV units to pixels, `opaque()`
// for Reduce Transparency and High Contrast.
class Look {
    constructor(unit, opaque) {
        this.unit = unit;
        this.opaque = opaque;
        this.u = 1;
    }

    px(value) {
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor || 1;
        return `${(value * this.u / scale).toFixed(2)}px`;
    }

    m(name) {
        const value = TOKENS.materials[name];
        if (!this.opaque()) return value;
        return value.replace(/rgba\(([^,]+),([^,]+),([^,]+),[^)]+\)/, 'rgba($1,$2,$3, 1)');
    }

    font(size, weight) {
        return `font-family: Figtree, sans-serif; font-size: ${this.px(size)}; font-weight: ${weight};`;
    }

    // St reads background-gradient-start as the background colour: nothing may set
    // background-color after it.
    key() {
        return `background-gradient-direction: vertical; background-gradient-start: ${this.m('key_top')};
            background-gradient-end: ${this.m('key_bottom')}; border: ${this.px(1.5)} solid ${this.m('key_rim')};
            box-shadow: 0 ${this.px(3)} ${this.px(10)} ${this.m('key_drop')};`;
    }

    plain() {
        return `background-gradient-direction: none; background-color: transparent;
            border: ${this.px(1.5)} solid transparent; box-shadow: none;`;
    }
}

const ROWS = {
    open: {label: () => _('Open'), icon: 'lumaui-square-arrow-out-up-right-symbolic'},
    close: {label: () => _('Close'), icon: 'lumaui-x-symbolic'},
    force: {label: () => _('Force Close'), icon: 'lumaui-triangle-alert-symbolic', danger: true},
    wait: {label: () => _('Wait'), icon: 'lumaui-clock-symbolic'},
};

// An item's menu on TV (tv.menu): bar material, rows of 68, one of them the
// key. Anchored to its item, above or below it; the owner routes the remote.
export class TvItemMenu {
    constructor({unit, opaque}) {
        this._look = new Look(unit, opaque);
        this.isOpen = false;
        this.index = 0;
        this.keys = [];
    }

    // `keys` from ROWS; `anchor` {x, y, width, height} in `parent`'s coordinates.
    open(parent, keys, anchor, place, title = null, opener = null) {
        this.close();
        if (!keys.length) return false;
        const look = this._look;
        look.u = look.unit();
        const {menu} = TOKENS;
        this.keys = keys;
        this.index = 0;
        this.place = place;
        this._actor = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL});
        this._actor.style = `padding: ${look.px(menu.padding)}; spacing: ${look.px(2)};
            border-radius: ${look.px(menu.radius)}; min-width: ${look.px(menu.min_width)};
            background-color: ${look.m('sheet')}; border: ${look.px(1.5)} solid ${look.m('bar_rim')};
            box-shadow: 0 ${look.px(18)} ${look.px(44)} ${look.m('bar_drop')};`;
        if (title) {
            const label = new St.Label({text: title});
            label.style = `${look.font(TOKENS.type.meta.size, TOKENS.type.meta.weight)} color: ${look.m('ink_3')};
                padding: ${look.px(12)} ${look.px(menu.row_padding_x)} ${look.px(8)};`;
            this._actor.add_child(label);
        }
        this._rows = keys.map(key => {
            const box = new St.BoxLayout({y_align: Clutter.ActorAlign.CENTER});
            const icon = new St.Icon({icon_name: ROWS[key].icon, y_align: Clutter.ActorAlign.CENTER});
            const label = new St.Label({text: ROWS[key].label(), y_align: Clutter.ActorAlign.CENTER});
            box.add_child(icon);
            box.add_child(label);
            this._actor.add_child(box);
            return {key, box, icon, label};
        });
        parent.add_child(this._actor);
        this._draw();
        const [, width] = this._actor.get_preferred_width(-1);
        const [, height] = this._actor.get_preferred_height(width);
        const monitor = Main.layoutManager.primaryMonitor;
        const safe = TOKENS.layout.safe_x * look.u;
        const x = Math.max(safe, Math.min(monitor.width - safe - width, anchor.x + anchor.width / 2 - width / 2));
        const y = place === 'above' ? anchor.y - menu.gap * look.u - height : anchor.y + anchor.height + menu.gap * look.u;
        this._actor.set_position(Math.round(x), Math.round(Math.max(0, y)));
        // One action per OK press: the OK that opened it may still be down.
        this.gate = okGate(opener);
        this.isOpen = true;
        return true;
    }

    close() {
        this.isOpen = false;
        this._actor?.destroy();
        this._actor = null;
        this._rows = [];
    }

    get current() {
        return this.keys[this.index] ?? null;
    }

    // Up/Down between rows; false when the press leaves the menu.
    move(direction) {
        const next = this.index + (direction === 'down' ? 1 : direction === 'up' ? -1 : 0);
        if (next < 0 || next >= this.keys.length) return false;
        this.index = next;
        this._draw();
        return true;
    }

    _draw() {
        const look = this._look;
        const {menu} = TOKENS;
        this._rows.forEach(({key, box, icon, label}, index) => {
            const focused = index === this.index;
            const ink = focused ? ROWS[key].danger ? look.m('danger_on_key') : look.m('key_ink') : look.m('ink_2');
            box.style = `height: ${look.px(menu.row_height)}; padding: 0 ${look.px(menu.row_padding_x)};
                spacing: ${look.px(menu.icon_gap)}; border-radius: ${look.px(menu.row_radius)};` +
                (focused ? look.key() : look.plain());
            icon.style = `icon-size: ${look.px(menu.icon)}; color: ${ink};`;
            label.style = `${look.font(menu.row_size, menu.row_weight)} color: ${ink};`;
        });
    }
}

export class TvAppSwitcher {
    // `host()` is {parent, below}: the actor the switcher joins (the TV home)
    // and the child it goes under (the bar), so the bar stays above the scrim.
    // `tile(app, width, height)` the app's TV tile, or null. `closed(reason,
    // returnTo)` runs after it shuts: 'back' (return to `returnTo`), 'switch',
    // 'empty' or 'home'.
    constructor({unit, opaque, host, tile, closed}) {
        this._look = new Look(unit, opaque);
        this._host = host;
        this._tile = tile;
        this._closed = closed;
        this._menu = new TvItemMenu({unit, opaque});
        this._appSystem = Shell.AppSystem.get_default();
        this._tracker = Shell.WindowTracker.get_default();
        this.isOpen = false;
        this._entries = [];
        this._index = 0;
        this._timers = new Set();
    }

    toggle(returnTo) {
        if (this.isOpen) this.close('home');
        else this.open(returnTo);
    }

    open(returnTo = null) {
        if (this.isOpen || Main.sessionMode.isLocked || Main.sessionMode.isGreeter || Main.modalCount > 0) return false;
        const {parent, below} = this._host();
        if (!parent) return false;
        this._look.u = this._look.unit();
        this._returnTo = returnTo;
        this._entries = this._collect();
        this._index = 0;
        this._okDownAt = null;
        try {
            this._build(parent, below);
        } catch (error) {
            // Never left half open: no actor means not open.
            this._actor?.destroy();
            this._actor = null;
            throw error;
        }
        this.isOpen = true;
        // The TV keyboard steps out of the way and comes back as it was (KeyboardManager.tvSuspend).
        this._keyboardState = Main.keyboard?.tvSuspend?.() ?? null;
        this._grab = Main.pushModal(this._actor, {actionMode: Shell.ActionMode.POPUP});
        this._actor.grab_key_focus();
        this._actor.opacity = 0;
        this._actor.ease({opacity: 255, duration: TOKENS.motion.sheet_ms, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        this._signals = [
            [this._appSystem, this._appSystem.connect('app-state-changed', () => this._sync())],
        ];
        if (sessionApplications?.connect)
            this._signals.push([sessionApplications, sessionApplications.connect('changed', () => this._sync())]);
        return true;
    }

    // Shuts it; `reason` goes to `closed`. `then` runs once the grab is gone.
    close(reason = 'back', then = null) {
        if (!this.isOpen) {
            then?.();
            return;
        }
        this.isOpen = false;
        for (const [object, id] of this._signals ?? []) object.disconnect(id);
        this._signals = [];
        for (const id of this._timers) GLib.source_remove(id);
        this._timers.clear();
        if (this._holdTimer) GLib.source_remove(this._holdTimer);
        this._holdTimer = 0;
        this._menu.close();
        if (this._grab) Main.popModal(this._grab);
        this._grab = null;
        Main.keyboard?.tvResume?.(this._keyboardState);
        this._keyboardState = null;
        this._actor?.destroy();
        this._actor = null;
        this._cards = [];
        const returnTo = this._returnTo;
        this._returnTo = null;
        this._entries = [];
        this._closed?.(reason, returnTo);
        then?.();
    }

    destroy() {
        this.close('home');
    }

    // Running apps, newest first: windows in Alt+Tab order, then apps with none.
    _collect() {
        const windows = global.display.get_tab_list(Meta.TabList.NORMAL_ALL, null)
            .filter(normalWindow)
            .map(window => ({window, app: this._tracker.get_window_app(window)}))
            .filter(entry => switcherApp(entry.app));
        // An app with no window is listed only if it is one people open (a player playing on).
        const others = runningApps().filter(app => appRunning(app) && switcherApp(app));
        const apps = switcherApps(windows, others, app => app.get_id());
        return apps.map(app => ({app, window: windows.find(entry => entry.app === app)?.window ?? null,
            requestedAt: null}));
    }

    // ── building ──────────────────────────────────────────────────────────

    _build(parent, below) {
        const look = this._look;
        const monitor = Main.layoutManager.primaryMonitor;
        this._actor = new St.Widget({name: 'lumaTvAppSwitcher', reactive: true, can_focus: true,
            layout_manager: new Clutter.FixedLayout(), width: monitor.width, height: monitor.height,
            accessible_name: _('Open apps')});
        if (below?.get_parent() === parent) parent.insert_child_below(this._actor, below);
        else parent.add_child(this._actor);
        this._actor.connect('key-press-event', (_actor, event) => this._key(event));
        this._actor.connect('key-release-event', (_actor, event) => this._keyUp(event));
        const scrim = new St.Widget({width: monitor.width, height: monitor.height});
        scrim.style = `background-color: ${look.m('scrim')};`;
        this._actor.add_child(scrim);
        this._row = new St.Widget({layout_manager: new Clutter.FixedLayout()});
        this._actor.add_child(this._row);
        const {app_switcher: s} = TOKENS;
        this._empty = new St.Label({text: _('No apps open'), width: monitor.width});
        this._empty.clutter_text.line_alignment = Pango.Alignment.CENTER;
        this._empty.style = `${look.font(s.empty_size, s.empty_weight)} color: ${look.m('ink_2')};`;
        this._empty.set_position(0, monitor.height / 2 - s.empty_size * look.u);
        this._actor.add_child(this._empty);
        this._buildCards();
    }

    _buildCards() {
        const look = this._look;
        const u = look.u;
        const {app_switcher: s, layout} = TOKENS;
        this._row.destroy_all_children();
        const width = s.card_width * u, height = width * 9 / 16;
        const top = (s.caption_height + s.caption_below) * u;
        this._cards = this._entries.map((entry, index) => {
            const card = new St.Widget({layout_manager: new Clutter.FixedLayout(), reactive: true});
            card.set_position((layout.safe_x + index * (s.card_width + s.card_gap)) * u, s.row_top * u);
            const caption = new St.BoxLayout({width, height: s.caption_height * u});
            caption.style = `spacing: ${look.px(s.caption_gap)};`;
            const icon = entry.app.create_icon_texture(Math.round(s.caption_icon * u /
                (St.ThemeContext.get_for_stage(global.stage).scale_factor || 1)));
            if (icon) {
                icon.y_align = Clutter.ActorAlign.CENTER;
                caption.add_child(icon);
            }
            const name = new St.Label({text: entry.app.get_name(), y_align: Clutter.ActorAlign.CENTER, x_expand: true});
            name.clutter_text.ellipsize = Pango.EllipsizeMode.END;
            const state = new St.Label({y_align: Clutter.ActorAlign.CENTER});
            caption.add_child(name);
            caption.add_child(state);
            const shot = new St.Widget({layout_manager: new Clutter.FixedLayout(), width, height,
                pivot_point: new Graphene.Point({x: 0.5, y: 0.5})});
            shot.set_position(0, top);
            this._fill(shot, entry, width, height);
            card.add_child(caption);
            card.add_child(shot);
            card.connect('button-release-event', () => {
                this._index = index;
                this._switch();
                return Clutter.EVENT_STOP;
            });
            this._row.add_child(card);
            return {card, caption, name, state, shot, entry};
        });
        this._empty.visible = !this._entries.length;
        this._draw();
    }

    // The window's latest picture, fitted to the card; else the app's TV tile;
    // else its icon on the bar material.
    _fill(shot, entry, width, height) {
        const look = this._look;
        const radius = TOKENS.app_switcher.card_radius * look.u;
        const actor = entry.window?.get_compositor_private();
        if (actor) {
            const frame = entry.window.get_frame_rect();
            const scale = Math.min(width / Math.max(1, frame.width), height / Math.max(1, frame.height));
            const clone = new Clutter.Clone({source: actor});
            // The frame, not the buffer: a client's shadow stays off the card.
            const buffer = entry.window.get_buffer_rect();
            const cloneWidth = actor.width * scale, cloneHeight = actor.height * scale;
            clone.set_size(cloneWidth, cloneHeight);
            const clip = new St.Widget({layout_manager: new Clutter.FixedLayout(), clip_to_allocation: true,
                width: frame.width * scale, height: frame.height * scale});
            clip.set_position((width - frame.width * scale) / 2, (height - frame.height * scale) / 2);
            clone.set_position((buffer.x - frame.x) * scale, (buffer.y - frame.y) * scale);
            clip.add_child(clone);
            roundCorners(clip, clip.width, clip.height, radius);
            shot.add_child(clip);
            shot.style = `background-color: black; border-radius: ${look.px(TOKENS.app_switcher.card_radius)};`;
            shot._radius = look.px(TOKENS.app_switcher.card_radius);
            return;
        }
        const tile = this._tile?.(entry.app, width, height);
        const {tile: tileTokens} = TOKENS;
        shot._radius = look.px(TOKENS.app_switcher.card_width * tileTokens.corner_x);
        if (tile) {
            shot.add_child(tile);
            shot.style = `border-radius: ${shot._radius};`;
            return;
        }
        const icon = entry.app.create_icon_texture(Math.round(height * 0.5 /
            (St.ThemeContext.get_for_stage(global.stage).scale_factor || 1)));
        if (icon) {
            icon.set_position((width - icon.width) / 2, height / 4);
            shot.add_child(icon);
        }
        shot.style = `background-color: ${look.m('bar')}; border-radius: ${shot._radius};`;
    }

    // One bright thing: the focused card lifts with the white ring; while its
    // menu is down it stays lifted without the ring, its caption under the menu.
    _draw() {
        if (!this._cards) return;
        const look = this._look;
        const {app_switcher: s, tile, motion} = TOKENS;
        const t = now();
        this._cards.forEach(({caption, name, state, shot, entry}, index) => {
            const focused = index === this._index;
            const held = focused && this._menu.isOpen;
            const lifted = focused ? tile.focus_scale : 1;
            shot.remove_all_transitions();
            shot.ease({scale_x: lifted, scale_y: lifted, duration: motion.focus_ms, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
            shot.style = (shot.style ?? '').replace(/box-shadow:[^;]*;/, '') + `box-shadow: ${focused && !held
                ? `0 0 0 ${look.px(tile.ring)} ${look.m('ring')}`
                : `0 ${look.px(14)} ${look.px(34)} ${look.m('tile_drop')}`};`;
            const phase = closeState(entry.requestedAt, t, s.force_after_ms);
            shot.opacity = phase === 'open' ? 255 : Math.round(255 * s.closing_opacity);
            caption.opacity = held ? 0 : 255;
            name.style = `${look.font(s.caption_size, s.caption_weight)} color: ${focused ? look.m('ink') : look.m('ink_2')};`;
            state.text = phase === 'closing' ? _('Closing…') : phase === 'stuck' ? _('Not responding') : '';
            state.style = `${look.font(s.state_size, s.state_weight)} color: ${look.m('ink_3')};`;
            if (focused) this._row.set_child_above_sibling(this._cards[index].card, null);
        });
        const monitor = Main.layoutManager.primaryMonitor;
        this._row.ease({translation_x: -rowScroll(this._index, TOKENS, monitor.width / look.u) * look.u,
            duration: motion.page_ms, mode: Clutter.AnimationMode.EASE_OUT_CUBIC});
    }

    // ── the remote ────────────────────────────────────────────────────────

    _key(event) {
        const name = Clutter.keyval_name(event.get_key_symbol()) ?? '';
        const repeated = Boolean(event.get_flags() & Clutter.EventFlags.FLAG_REPEATED);
        // Menu opens the card's menu, as a held OK does.
        if (name === 'Menu' && !repeated && !this._menu.isOpen) {
            this._openMenu(null);
            return Clutter.EVENT_STOP;
        }
        const action = remoteAction(name);
        if (!action) return Clutter.EVENT_STOP;
        if (repeated && !repeatAllowed(action)) return Clutter.EVENT_STOP;
        if (this._menu.isOpen) {
            if (action === 'ok') {
                if (okPress(this._menu.gate, now(), OK_QUIET_MS)) this._menuOk();
            } else if (action === 'back') {
                this._menu.close();
            } else if ((action === 'up' || action === 'down') && !this._menu.move(action)) {
                // Down past the last row (the menu is above the card) returns to it.
                if (action === 'down') this._menu.close();
            }
            this._draw();
            return Clutter.EVENT_STOP;
        }
        if (action === 'back') {
            this.close('back');
            return Clutter.EVENT_STOP;
        }
        if (action === 'left' || action === 'right') {
            this._index = stepCard(this._index, this._entries.length, action);
        } else if (action === 'up') {
            this._openMenu(null);
        } else if (action === 'ok' && this._entries.length) {
            // Let go: switch. Held: the card's menu, once.
            this._okDownAt = now();
            if (this._holdTimer) GLib.source_remove(this._holdTimer);
            this._holdTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, TOKENS.focus.hold_ms, () => {
                this._holdTimer = 0;
                this._okDownAt = null;
                this._openMenu(now());
                return GLib.SOURCE_REMOVE;
            });
        }
        this._draw();
        return Clutter.EVENT_STOP;
    }

    _keyUp(event) {
        if (remoteAction(Clutter.keyval_name(event.get_key_symbol()) ?? '') !== 'ok') return Clutter.EVENT_STOP;
        if (this._menu.isOpen) okRelease(this._menu.gate);
        if (this._holdTimer) GLib.source_remove(this._holdTimer);
        this._holdTimer = 0;
        if (this._okDownAt !== null && !this._menu.isOpen) {
            this._okDownAt = null;
            this._switch();
        }
        return Clutter.EVENT_STOP;
    }

    _openMenu(opener) {
        const card = this._cards[this._index];
        if (!card) return;
        const {entry} = card;
        const phase = closeState(entry.requestedAt, now(), TOKENS.app_switcher.force_after_ms);
        const keys = cardMenu(phase, entry.app.get_windows().length > 0);
        const look = this._look;
        const s = TOKENS.app_switcher;
        // Above the card, over its caption (Up opened it); the lifted card's top.
        const lift = (TOKENS.tile.focus_scale - 1) / 2 * card.shot.height;
        const monitor = Main.layoutManager.primaryMonitor;
        const scroll = -rowScroll(this._index, TOKENS, monitor.width / look.u) * look.u;
        const anchor = {x: card.card.x + scroll, width: card.shot.width,
            y: card.card.y + card.shot.y - lift - (s.menu_gap - TOKENS.menu.gap) * look.u, height: 0};
        const title = phase === 'stuck' ? _('%s is not responding').format(entry.app.get_name()) : null;
        this._menu.open(this._actor, keys, anchor, 'above', title, opener);
        this._draw();
    }

    _menuOk() {
        const card = this._cards[this._index];
        const key = this._menu.current;
        this._menu.close();
        if (!card) return;
        const {entry} = card;
        if (key === 'close' || key === 'wait') {
            if (key === 'close') requestQuit(entry.app);
            entry.requestedAt = now();
            this._watchClose(entry);
        } else if (key === 'force') {
            // The desktop's Force Quit for a window that will not close.
            for (const window of entry.app.get_windows()) window.kill();
        }
    }

    // After the grace time a card still running says so and, while focused,
    // offers Force Close.
    _watchClose(entry) {
        const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, TOKENS.app_switcher.force_after_ms + 50, () => {
            this._timers.delete(id);
            if (!this.isOpen || !this._entries.includes(entry)) return GLib.SOURCE_REMOVE;
            this._draw();
            if (this._entries[this._index] === entry && !this._menu.isOpen) this._openMenu(null);
            return GLib.SOURCE_REMOVE;
        });
        this._timers.add(id);
        this._draw();
    }

    // An app gone: its card leaves and the row closes the gap; the last one
    // gone closes the switcher.
    _sync() {
        if (!this.isOpen) return;
        const gone = this._entries.map((entry, index) => [entry, index]).filter(([entry]) => !appRunning(entry.app));
        if (!gone.length) return;
        for (const [, index] of gone.reverse()) {
            this._entries.splice(index, 1);
            this._index = afterRemoval(this._index, index, this._entries.length);
        }
        // The row moves: a menu anchored to the old row closes.
        this._menu.close();
        if (!this._entries.length) {
            this.close('empty');
            return;
        }
        this._buildCards();
    }

    _switch() {
        const entry = this._entries[this._index];
        if (!entry) return;
        this.close('switch', () => {
            if (entry.window && !entry.window.minimized) Main.activateWindow(entry.window);
            else entry.app.activate();
        });
    }
}
