// SPDX-License-Identifier: Apache-2.0
// TV posture of the Shell's system dialogs. Every ModalDialog (Wi-Fi and VPN
// secrets, keyring and administrator prompts, end session, mounts, the run
// dialog, access prompts, ...) takes the TV sheet's look and the remote while
// it is open: one adapter here, no TV copy of any dialog.
//
//   - the TV sheet: TV scale and type, the sheet material, no live blur;
//     entries are recessed fields, buttons TV actions, the focus the one key
//   - the remote: arrows move the focus, OK activates, Back cancels; the
//     focus never leaves an open dialog, so its keys can never go dead
//   - typing: entries open the TV keyboard, its action key labelled with the
//     dialog's default button; the panel rises above the keyboard
//
// It exists only while the posture is TV; outside it dialogs are untouched.
// The rules are tvDialogsModel.js; docs/developer-platform/tv-mode.md
// ("System dialogs").
import Atk from 'gi://Atk';
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {State} from 'resource:///org/gnome/shell/ui/modalDialog.js';
import {tv as TOKENS} from './tvTokens.js';
import {backButton, dialogKey, keyboardHeight, labelRole, panelShift, panelWidth, foreignWidth, sameValue} from './tvDialogsModel.js';
import {repeatAllowed} from './tvModel.js';

const DIRECTIONS = {
    up: St.DirectionType.UP, down: St.DirectionType.DOWN,
    left: St.DirectionType.LEFT, right: St.DirectionType.RIGHT,
};

function walk(actor, callback) {
    if (callback(actor) === false) return;
    for (const child of actor.get_children()) walk(child, callback);
}

function inside(ancestor, actor) {
    return Boolean(actor) && (ancestor === actor || ancestor.contains(actor));
}

// Shown within `root`, whether or not the dialog itself is on screen yet.
function shownIn(root, actor) {
    for (let a = actor; a && a !== root; a = a.get_parent()) {
        if (!a.visible) return false;
    }
    return true;
}

// A button that says something: its label, or a label among its parts (a
// glyph and a word, as a Details toggle is).
function textButton(actor) {
    if (!(actor instanceof St.Button)) return false;
    if (actor.label) return true;
    let found = false;
    walk(actor, child => {
        if (child instanceof St.Label) found = true;
        return !found;
    });
    return found;
}

export class TvDialogs {
    // `unit` gives the TV unit for the primary monitor; `opaque` whether
    // Reduce Transparency or High Contrast asks for opaque materials.
    constructor({unit, opaque}) {
        this._unitFor = unit;
        this._opaque = opaque;
        this._dialogs = new Map();      // ModalDialog -> its TV record, while open
        this._watched = new Set();
        const group = Main.layoutManager.modalDialogGroup;
        group.connectObject('child-added', (_group, actor) => this._watch(actor), this);
        for (const actor of group.get_children()) this._watch(actor);
        global.stage.connectObject('notify::key-focus', () => this._focusChanged(), this);
        Main.keyboard?.connectObject?.('visibility-changed', () => this._placeAll(), this);
        Main.layoutManager.connectObject('monitors-changed', () => this._restyleAll(), this);
    }

    destroy() {
        for (const dialog of [...this._dialogs.keys()]) this._release(dialog);
        for (const dialog of this._watched) dialog.disconnectObject(this);
        this._watched.clear();
        Main.layoutManager.modalDialogGroup.disconnectObject(this);
        global.stage.disconnectObject(this);
        Main.keyboard?.disconnectObject?.(this);
        Main.layoutManager.disconnectObject(this);
        if (this._focusId) GLib.source_remove(this._focusId);
        this._focusId = 0;
    }

    // ── which dialogs ─────────────────────────────────────────────────────

    // A ModalDialog joins the group before it builds its Dialog, so what it
    // is shows only as it opens.
    _watch(dialog) {
        if (!('state' in dialog) || this._watched.has(dialog)) return;
        this._watched.add(dialog);
        this._connect(dialog);
        this._stateChanged(dialog);
    }

    _connect(dialog) {
        dialog.connectObject(
            'notify::state', () => this._stateChanged(dialog),
            'destroy', () => {
                this._watched.delete(dialog);
                this._release(dialog, false);
            }, this);
    }

    _stateChanged(dialog) {
        if (!dialog.dialogLayout) return;
        const open = dialog.state === State.OPENING || dialog.state === State.OPENED;
        if (open && !this._dialogs.has(dialog)) this._adopt(dialog);
        else if (dialog.state === State.CLOSED) this._release(dialog);
    }

    // The card the person sees: the dialog's own (a prompt that draws its
    // card, as the administrator prompt does, marks it with the dialog role),
    // else the shared Dialog's. A dialog with no buttons (a search surface)
    // is not a system dialog and keeps its own presentation.
    _surface(dialog) {
        let card = null;
        walk(dialog, actor => {
            // The dialog itself shows only after its state says opening.
            if (card || (actor !== dialog && !actor.visible)) return false;
            if (actor !== dialog && actor.accessible_role === Atk.Role.DIALOG) {
                card = actor;
                return false;
            }
            return true;
        });
        if (card) return card;
        const surface = dialog.dialogLayout._dialog;
        const buttons = dialog.buttonLayout?.get_children().filter(button => button.visible) ?? [];
        return dialog.dialogLayout.visible && surface && buttons.length ? surface : null;
    }

    // ── adopting a dialog ─────────────────────────────────────────────────

    _adopt(dialog) {
        const surface = this._surface(dialog);
        if (!surface) return;
        // The actor directly under the dialog that carries the card: it
        // moves the card above the keyboard.
        let mover = surface;
        while (mover.get_parent() && mover.get_parent() !== dialog) mover = mover.get_parent();
        const record = {
            dialog, surface, mover, saved: new Map(), blurs: [], fields: [], focusables: [], containers: new Set(),
            standard: surface === dialog.dialogLayout._dialog, lastFocus: null, spacing: null,
        };
        this._dialogs.set(dialog, record);
        // No live blur on a TV: the sheet is its own material.
        walk(dialog, actor => {
            for (const effect of actor.get_effects()) {
                if (effect instanceof Shell.BlurEffect && effect.enabled) {
                    effect.enabled = false;
                    record.blurs.push(effect);
                }
            }
        });
        dialog.connectObject('captured-event', (_actor, event) => this._captured(record, event), this);
        surface.connectObject(
            'child-added', () => this._queueRestyle(record),
            'notify::allocation', () => this._queuePlace(record), this);
        this._restyle(record);
        const focus = global.stage.key_focus;
        if (inside(surface, focus)) record.lastFocus = focus;
        else this._refocus(record);
        // Again once the dialog has placed its own focus as it opens.
        this._safeFocus(record);
        record._safeId = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            record._safeId = 0;
            if (this._dialogs.get(dialog) === record) this._safeFocus(record);
            return GLib.SOURCE_REMOVE;
        });
    }

    // The button that cancels: the shared Dialog's Escape button, or on a
    // dialog's own card a button classed as cancel.
    _cancelButton(record) {
        if (record.standard)
            return record.dialog.dialogLayout._buttonKeys?.[Clutter.KEY_Escape]?.button ?? null;
        let found = null;
        walk(record.surface, actor => {
            if (found) return false;
            if (actor instanceof St.Button && /cancel/.test(actor.get_style_class_name() ?? '')) found = actor;
            return !found;
        });
        return found;
    }

    // A stray OK must never confirm: a dialog with nothing to type (end
    // session, power off, restart, a question, an access prompt) opens on
    // Cancel, never on its action. A dialog with a field opens on the field,
    // where OK only opens the keyboard.
    _safeFocus(record) {
        if (record.fields.some(entry => shownIn(record.surface, entry))) return;
        const cancel = this._cancelButton(record);
        if (cancel && shownIn(record.surface, cancel) && cancel.reactive && cancel.can_focus && !inside(cancel, global.stage.key_focus))
            cancel.grab_key_focus();
    }

    // `alive` is false as the dialog is destroyed: nothing to give back.
    _release(dialog, alive = true) {
        const record = this._dialogs.get(dialog);
        if (!record) return;
        this._dialogs.delete(dialog);
        for (const id of ['_restyleId', '_placeId', '_safeId']) {
            if (record[id]) GLib.source_remove(record[id]);
        }
        for (const field of record.fields) Main.keyboard?.setFieldAction?.(field.clutter_text, '');
        if (!alive) return;
        dialog.disconnectObject(this);
        // Still watched for its next opening.
        this._connect(dialog);
        record.surface.disconnectObject(this);
        for (const actor of record.containers) actor.disconnectObject(this);
        for (const effect of record.blurs) effect.enabled = true;
        for (const [actor, value] of record.saved) {
            for (const [key, original] of Object.entries(value)) actor[key] = original;
        }
        if (record.spacing !== null && record.dialog.buttonLayout?.layout_manager)
            record.dialog.buttonLayout.layout_manager.spacing = record.spacing;
        record.mover.translation_y = 0;
    }

    // Set a property, remembering what it was for when the dialog lets go.
    _set(record, actor, key, value) {
        let saved = record.saved.get(actor);
        if (!saved) record.saved.set(actor, saved = {});
        // A width the actor never fixed goes back to its natural width.
        if (key === 'width' && !(key in saved) && !actor.natural_width_set) saved.width = -1;
        if (!(key in saved)) saved[key] = actor[key];
        if (!sameValue(actor[key], value)) actor[key] = value;
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

    _key() {
        return `background-gradient-direction: vertical; background-gradient-start: ${this._m('key_top')};
            background-gradient-end: ${this._m('key_bottom')};
            border: ${this._px(1.5)} solid ${this._m('key_rim')};
            box-shadow: 0 ${this._px(3)} ${this._px(10)} ${this._m('key_drop')}; color: ${this._m('key_ink')};`;
    }

    _chip() {
        return `background-gradient-direction: none; background-color: ${this._m('chip')};
            border: ${this._px(1.5)} solid ${this._m('chip_rim')};
            box-shadow: 0 ${this._px(2)} ${this._px(5)} ${this._m('chip_drop')}; color: ${this._m('ink')};`;
    }

    _restyleAll() {
        for (const record of this._dialogs.values()) this._restyle(record);
    }

    _queueRestyle(record) {
        if (record._restyleId) return;
        record._restyleId = GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
            record._restyleId = 0;
            if (this._dialogs.get(record.dialog) === record) this._restyle(record);
            return GLib.SOURCE_REMOVE;
        });
    }

    // Every part of the card by what it is: the card, its title and text,
    // fields, buttons, check boxes and icon buttons.
    _restyle(record) {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor) return;
        this._u = this._unitFor();
        const {sheet, button, type} = TOKENS;
        const {surface, dialog} = record;
        const width = panelWidth(monitor.width, this._u, TOKENS);
        const inner = width - 2 * sheet.padding * this._u;
        record.fields = [];
        record.focusables = [];
        record.fieldText = new Map();
        // St lengths are the content box: the padding and rim go around it.
        const w = this._px(width / this._u - 2 * sheet.padding - 3);
        this._set(record, surface, 'style', `width: ${w}; min-width: ${w}; max-width: ${w}; padding: ${this._px(sheet.padding)};
            spacing: ${this._px(sheet.gap)}; border-radius: ${this._px(sheet.radius)};
            background-gradient-direction: none; background-color: ${this._m('sheet')};
            border: ${this._px(1.5)} solid ${this._m('bar_rim')};
            box-shadow: 0 ${this._px(18)} ${this._px(44)} ${this._px(-10)} ${this._m('bar_drop')};
            color: ${this._m('ink')};`);
        this._placeMover(record, monitor, width);
        if (record.standard) {
            const layout = dialog.buttonLayout.layout_manager;
            if (record.spacing === null) record.spacing = layout.spacing;
            layout.spacing = Math.round(14 * this._u);
        }
        walk(surface, actor => {
            if (actor === surface) return true;
            // Rows a dialog adds later (the sessions end session lists, a
            // second field) are styled as they arrive.
            if (!record.containers.has(actor) && !(actor instanceof St.Entry) && !(actor instanceof St.Button) &&
                !(actor instanceof St.Label) && !(actor instanceof St.Icon)) {
                record.containers.add(actor);
                actor.connectObject('child-added', () => this._queueRestyle(record), this);
            }
            if (actor instanceof St.Entry) {
                this._styleField(record, actor, inner);
                return false;
            }
            if (actor instanceof St.Button) {
                record.focusables.push(actor);
                if (actor.has_style_class_name('check-box')) {
                    walk(actor, child => {
                        if (child instanceof St.Label)
                            this._set(record, child, 'style', `${this._font(type.body.size, type.body.weight)} color: inherit;`);
                    });
                } else {
                    const text = textButton(actor);
                    walk(actor, child => {
                        if (child === actor) return true;
                        if (child instanceof St.Icon && !text) this._set(record, child, 'style', `icon-size: ${this._px(button.icon)};`);
                        else if (child instanceof St.Label && !actor.label)
                            this._set(record, child, 'style', `${this._font(TOKENS.sheet.action_size, 650)} color: inherit;`);
                        else this._scaleFixed(record, child);
                        return true;
                    });
                }
                return false;
            }
            if (actor instanceof St.Label) {
                const role = labelRole(actor.get_style_class_name()?.split(/\s+/) ?? []);
                const font = role === 'title' ? this._font(sheet.title_size, sheet.title_weight) +
                    ` letter-spacing: ${this._px(-0.88)};`
                    : role === 'heading' ? this._font(type.h2.size, type.h2.weight)
                        : role === 'meta' ? this._font(type.meta.size, type.meta.weight)
                            : this._font(type.body.size, type.body.weight);
                const colour = role === 'title' || role === 'heading' ? this._m('ink')
                    : role === 'warning' ? this._m('danger') : this._m('ink_2');
                this._set(record, actor, 'style', `${font} color: ${colour};`);
                // A width the dialog balanced for its desktop type.
                if (actor.natural_width_set) this._set(record, actor, 'width', -1);
                return false;
            }
            if (actor instanceof St.Icon) {
                // Its desktop size, measured once: a restyle must not grow it.
                record.iconSizes ??= new Map();
                if (!record.iconSizes.has(actor)) {
                    const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor || 1;
                    record.iconSizes.set(actor, actor.icon_size > 0 ? actor.icon_size
                        : actor.get_theme_node().get_length('icon-size') / scale);
                }
                const size = record.iconSizes.get(actor);
                if (size > 0) {
                    this._set(record, actor, 'icon_size', -1);
                    this._set(record, actor, 'style', `icon-size: ${this._px(Math.round(size * 1.5))};`);
                }
                return false;
            }
            // A glyph or a badge drawn at a size its stylesheet fixes.
            if (this._scaleFixed(record, actor)) return !(actor instanceof St.DrawingArea);
            if (actor instanceof St.BoxLayout && actor.orientation === Clutter.Orientation.VERTICAL) {
                this._set(record, actor, 'style', `margin: 0; padding: 0; spacing: ${this._px(record.standard &&
                    (actor === dialog.contentLayout || actor.get_parent() === dialog.contentLayout) ? sheet.gap : 12)};`);
            } else if (actor === dialog.buttonLayout) {
                this._set(record, actor, 'style', 'margin: 0; padding: 0;');
            }
            return true;
        });
        this._drawFocus(record);
        this._labelFields(record);
        this._queuePlace(record);
    }

    // An actor its stylesheet gives a fixed size (a drawn glyph, the disc
    // behind it) grows as icons do: its desktop size, measured once, half
    // again in TV units, corners with it. False for anything sized by its
    // content.
    _scaleFixed(record, actor) {
        if (!(actor instanceof St.Widget)) return false;
        record.fixedSizes ??= new Map();
        if (!record.fixedSizes.has(actor)) {
            const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor || 1;
            const node = actor.get_theme_node();
            const width = node.get_width(), height = node.get_height();
            record.fixedSizes.set(actor, width > 0 || height > 0 ? {
                width: width > 0 ? width / scale : 0, height: height > 0 ? height / scale : 0,
                radius: node.get_border_radius(St.Corner.TOPLEFT) / scale,
            } : null);
        }
        const size = record.fixedSizes.get(actor);
        if (!size) return false;
        const grow = value => this._px(Math.round(value * 1.5));
        this._set(record, actor, 'style', `${size.width ? `width: ${grow(size.width)};` : ''}
            ${size.height ? `height: ${grow(size.height)};` : ''} ${size.radius ? `border-radius: ${grow(size.radius)};` : ''}`);
        return true;
    }

    // A field is the entry, or the row that holds it with its own buttons
    // (reveal, fingerprint) when the entry sits in one.
    _styleField(record, entry, inner) {
        const {sheet} = TOKENS;
        const parent = entry.get_parent();
        const row = parent instanceof St.BoxLayout && parent.orientation === Clutter.Orientation.HORIZONTAL &&
            parent.get_n_children() > 1 ? parent : null;
        record.fields.push(entry);
        record.fieldRows = record.fieldRows ?? new Map();
        record.fieldRows.set(entry, row);
        const text = `${this._font(sheet.field_size, 500)} color: ${this._m('ink')}; caret-color: ${this._m('ink')};
            selected-color: ${this._m('key_ink')}; selection-background-color: ${this._m('key_top')};`;
        if (row) {
            this._set(record, entry, 'style', `${text} background: none; border: 0; box-shadow: none;
                padding: 0; min-height: 0;`);
            walk(row, child => {
                if (child instanceof St.Button && child !== entry) record.focusables.push(child);
                if (child instanceof St.Icon) this._set(record, child, 'style', `icon-size: ${this._px(28)};`);
            });
        } else {
            const vertical = parent instanceof St.BoxLayout && parent.orientation === Clutter.Orientation.VERTICAL;
            record.fieldText.set(entry, `${text}${vertical ? ` width: ${this._px(inner / this._u)};` : ''}`);
            walk(entry, child => {
                if (child instanceof St.Icon) this._set(record, child, 'style', `icon-size: ${this._px(28)};`);
            });
        }
    }

    _fieldStyle(focused) {
        const {sheet, focus} = TOKENS;
        // The ring is the field's own border (St anti-aliases a rounded border;
        // a box-shadow spread is stair-stepped at the corners). The rim grows
        // inward into the padding, so the field never changes size.
        const rim = focused ? focus.ring : 1.5;
        return `min-height: ${this._px(sheet.field_height - 2 * rim)}; padding: 0 ${this._px(26 - rim)};
            border-radius: ${this._px(sheet.field_radius)}; background-gradient-direction: none;
            border: ${this._px(rim)} solid ${this._m(focused ? 'key_top' : 'well_rim')};
            ${focused
        // A dark field with a white ring: the well made opaque.
        ? `background-color: ${this._m('well').replace(/rgba\(([^,]+),([^,]+),([^,]+),[^)]+\)/, 'rgba($1,$2,$3, 1)')};
           box-shadow: none;`
        : `background-color: ${this._m('well')}; box-shadow: inset 0 ${this._px(2)} ${this._px(4)} ${this._m('well_shade')};`}`;
    }

    // One bright thing: the focused button is the key, the others chips; a
    // focused field takes the ring.
    _drawFocus(record) {
        const focus = global.stage.key_focus;
        const {sheet, menu} = TOKENS;
        for (const actor of record.focusables) {
            const focused = inside(actor, focus);
            const kind = actor.has_style_class_name('check-box') ? 'check' : textButton(actor) ? 'action' : 'icon';
            const shape = kind === 'action'
                ? `min-height: ${this._px(sheet.action_height)}; border-radius: ${this._px(sheet.action_radius)};
                   padding: 0 ${this._px(TOKENS.button.padding_x)}; ${this._font(sheet.action_size, 650)}`
                : kind === 'check'
                    ? `min-height: ${this._px(menu.row_height)}; border-radius: ${this._px(menu.row_radius)};
                       padding: 0 ${this._px(menu.row_padding_x)};`
                    : `min-width: ${this._px(56)}; min-height: ${this._px(56)}; border-radius: ${this._px(28)};
                       padding: 0;`;
            const danger = actor.has_style_class_name('destructive-action') || actor.has_style_class_name('danger');
            const look = focused ? this._key() + (danger ? ` color: ${this._m('danger_on_key')};` : '')
                : kind === 'action' ? this._chip() + (danger ? ` color: ${this._m('danger')};` : '')
                    : `background-gradient-direction: none; background-color: transparent; border: ${this._px(1.5)} solid transparent;
                       box-shadow: none; color: ${this._m('ink')};`;
            this._set(record, actor, 'style', `${shape} ${look}`);
        }
        for (const entry of record.fields) {
            const focused = inside(entry, focus);
            const row = record.fieldRows?.get(entry);
            if (row)
                this._set(record, row, 'style', `${this._fieldStyle(focused)} spacing: ${this._px(16)};`);
            else
                this._set(record, entry, 'style', `${record.fieldText.get(entry)} ${this._fieldStyle(focused)}`);
        }
    }

    // The TV keyboard's action key says what Enter does here: the dialog's
    // default button (Connect, Unlock, Allow, Authenticate).
    _labelFields(record) {
        const {dialog, surface} = record;
        let label = dialog.dialogLayout?._buttonKeys?.[Clutter.KEY_Return]?.button?.label ?? '';
        if (!record.standard || !label) {
            const buttons = [];
            walk(surface, actor => {
                // Buttons that name their action (not a Details toggle).
                if (actor instanceof St.Button && actor.label && actor.visible) buttons.push(actor);
                return !(actor instanceof St.Button);
            });
            label = buttons.find(button => button.has_style_pseudo_class('default'))?.label ?? buttons.at(-1)?.label ?? '';
        }
        for (const entry of record.fields) Main.keyboard?.setFieldAction?.(entry.clutter_text, label.replace(/_/g, ''));
    }

    // ── above the keyboard ────────────────────────────────────────────────

    _placeAll() {
        for (const record of this._dialogs.values()) this._queuePlace(record);
    }

    _queuePlace(record) {
        if (record._placeId) return;
        record._placeId = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            record._placeId = 0;
            if (this._dialogs.get(record.dialog) === record) this._place(record);
            return GLib.SOURCE_REMOVE;
        });
    }

    // A card its dialog places itself (the administrator prompt sizes and
    // positions its holder as it opens, after the dialog says it is opening)
    // takes the sheet's width, centred. Checked on every placement, so the
    // dialog's own placement never wins. True when the card is placed so.
    _placeMover(record, monitor, width) {
        const {mover} = record;
        if (!mover.fixed_position_set) return false;
        // Its own placement balanced the labels for its desktop width.
        // One restyle per width its own placement gives it; never one for the sheet's own width.
        const foreign = foreignWidth(mover.width, width);
        if (foreign !== null && foreign !== record.foreignWidth) {
            record.foreignWidth = foreign;
            this._queueRestyle(record);
        }
        this._set(record, mover, 'width', width);
        this._set(record, mover, 'x', monitor.x + Math.round((monitor.width - width) / 2));
        return true;
    }

    _place(record) {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor) return;
        const u = this._unitFor();
        const {surface, mover} = record;
        const height = surface.height;
        if (this._placeMover(record, monitor, panelWidth(monitor.width, u, TOKENS)))
            this._set(record, mover, 'y', monitor.y + Math.round(Math.max(0, (monitor.height - height) / 2)));
        const [, y] = surface.get_transformed_position();
        const top = y - mover.translation_y;
        const keyboardTop = Main.keyboard?.visible
            ? monitor.y + monitor.height - keyboardHeight(TOKENS.keyboard) * u : null;
        const shift = Math.round(panelShift({
            top, bottom: top + height, keyboardTop,
            safeTop: monitor.y + TOKENS.layout.safe_y * u, gap: 24 * u,
        }));
        if (mover.translation_y === shift) return;
        mover.remove_transition('translation-y');
        mover.ease({translation_y: shift, duration: TOKENS.motion.keyboard_ms, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
    }

    // ── the remote ────────────────────────────────────────────────────────

    // The dialog that has the grab: the one the remote is talking to.
    _top() {
        const grab = global.stage.get_grab_actor?.();
        return grab ? this._dialogs.get(grab) ?? null : null;
    }

    // The focus never stays outside the dialog that holds the grab: there the
    // dialog's keys (Return, Escape, the arrows) would go nowhere and the TV
    // keyboard would close. Whatever took it (the TV home, a window mapping
    // underneath), it comes back to where it was in the dialog.
    _focusChanged() {
        const focus = global.stage.key_focus;
        for (const record of this._dialogs.values()) {
            if (inside(record.surface, focus)) record.lastFocus = focus;
            this._drawFocus(record);
        }
        const top = this._top();
        if (!top || inside(top.surface, focus) || this._focusId) return;
        this._focusId = GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
            this._focusId = 0;
            const record = this._top();
            if (record && !inside(record.surface, global.stage.key_focus)) this._refocus(record);
            return GLib.SOURCE_REMOVE;
        });
    }

    _refocus(record) {
        const {dialog, surface} = record;
        const usable = actor => actor && inside(surface, actor) && shownIn(surface, actor) && actor.reactive !== false;
        const target = [record.lastFocus, dialog._initialKeyFocus, dialog.dialogLayout?.initialKeyFocus]
            .find(usable);
        if (target) target.grab_key_focus();
        else surface.navigate_focus(null, St.DirectionType.TAB_FORWARD, false);
    }

    _captured(record, event) {
        if (event.type() !== Clutter.EventType.KEY_PRESS) return Clutter.EVENT_PROPAGATE;
        const action = dialogKey(Clutter.keyval_name(event.get_key_symbol()) ?? '');
        if (!action) return Clutter.EVENT_PROPAGATE;
        // A held OK or Back repeats: the repeat never presses a button or cancels again.
        if (!repeatAllowed(action) && (event.get_flags() & Clutter.EventFlags.FLAG_REPEATED)) return Clutter.EVENT_STOP;
        const {dialog, surface} = record;
        const focus = global.stage.key_focus;
        const focused = inside(surface, focus);
        if (action === 'back') {
            // A dialog that draws its own card answers Escape itself (on the
            // dialog, which has the grab, wherever the focus is).
            if (!record.standard) return Clutter.EVENT_PROPAGATE;
            const keys = dialog.dialogLayout._buttonKeys ?? {};
            const buttons = dialog.buttonLayout.get_children().filter(button => button.visible).map(button => ({
                button, reactive: button.reactive,
                escape: keys[Clutter.KEY_Escape]?.button === button,
                action: Object.values(keys).find(info => info.button === button)?.action ?? (() => button.emit('clicked', 1)),
            }));
            const back = backButton(buttons);
            // The shared Dialog acts on Escape's release, once the focus is in
            // it; otherwise Back acts here, at once.
            if (back?.escape && focused) return Clutter.EVENT_PROPAGATE;
            if (back) back.action();
            return Clutter.EVENT_STOP;
        }
        if (!focused) {
            this._refocus(record);
            return Clutter.EVENT_STOP;
        }
        // Up and Down leave a one-line field (the keyboard is away); Left and
        // Right still move its cursor.
        if ((action === 'up' || action === 'down') && focus instanceof Clutter.Text) {
            surface.navigate_focus(focus, DIRECTIONS[action], false);
            return Clutter.EVENT_STOP;
        }
        return Clutter.EVENT_PROPAGATE;
    }
}
