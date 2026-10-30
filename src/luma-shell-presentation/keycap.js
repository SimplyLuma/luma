// SPDX-License-Identifier: GPL-2.0-or-later
// Keycap input policy for the Shell's on-screen keyboard (js/ui/keyboard.js).
// The same behaviour as Stevia's Keycap series (patches/stevia/0004), written
// for the Shell. Nothing here imports GI, so the policy is tested under gjs
// and node; keyboard.js passes Clutter's enums in.
import {keycap} from './keycapTokens.js';

export {keycap};

const LETTER_OR_NUMBER = /[\p{L}\p{N}]/u;
const SENTENCE_END = new Set(['.', '!', '?']);

export class KeycapPolicy {
    // enums: {purpose: Clutter.InputContentPurpose, hint: Clutter.InputContentHintFlags}
    constructor({purpose, hint}) {
        this._purpose = purpose;
        this._hint = hint;
    }

    // Password, PIN, hidden and sensitive-data fields never receive
    // suggestions, clipboard history, automatic case or double-space periods.
    isSensitive(purpose, hints) {
        const P = this._purpose, H = this._hint;
        return purpose === P.PASSWORD || (P.PIN !== undefined && purpose === P.PIN) ||
            (hints & (H.HIDDEN_TEXT | H.SENSITIVE_DATA)) !== 0;
    }

    isNumeric(purpose) {
        const P = this._purpose;
        return [P.DIGITS, P.NUMBER, P.PHONE, P.PIN].filter(value => value !== undefined).includes(purpose);
    }

    // cursor counts characters, as Clutter's surrounding text does.
    shouldCapitalise(purpose, hints, text, cursor) {
        const P = this._purpose, H = this._hint;
        if (this.isSensitive(purpose, hints) || (hints & H.LOWERCASE) !== 0)
            return false;
        if (![P.NORMAL, P.ALPHA, P.NAME].includes(purpose))
            return false;
        const before = [...(text ?? '')];
        if (!Number.isInteger(cursor) || cursor < 0 || cursor > before.length)
            return false;
        if (cursor === 0 || (hints & H.UPPERCASE) !== 0)
            return true;
        if ((hints & H.TITLECASE) !== 0 && /\s/u.test(before[cursor - 1]))
            return true;
        for (let i = cursor - 1; i >= 0; i--) {
            if (before[i] === '\n')
                return true;
            if (!/\s/u.test(before[i]))
                return SENTENCE_END.has(before[i]);
        }
        return true;
    }
}

// Two spaces after a word end the sentence: `before` is the text before the
// cursor once the first space is in.
export function canEndSentence(before) {
    const chars = [...(before ?? '')];
    return chars.length >= 2 && chars[chars.length - 1] === ' ' &&
        LETTER_OR_NUMBER.test(chars[chars.length - 2]);
}

export class TapTracker {
    constructor(window = keycap.timing.shift_double_tap) {
        this._window = window;
        this._last = 0;
    }

    // True on the second tap inside the window; the pair is then consumed.
    tap(now) {
        const double = this._last > 0 && now - this._last <= this._window;
        this._last = double ? 0 : now;
        return double;
    }

    reset() {
        this._last = 0;
    }
}

// Delete repeats characters, then whole words once held long enough; a
// sensitive field only ever deletes characters.
export function deleteMode(heldMs, sensitive) {
    return !sensitive && heldMs >= keycap.timing.delete_word_after ? 'word' : 'char';
}

// Suggestion strip: the literal word (quoted) left, the backend's first
// choice bold in the centre, the next different candidate right. Without a
// literal word the candidates keep their order and the first is the choice.
export function suggestionSlots(literal, candidates) {
    const list = candidates.map((text, index) => ({text, index}));
    if (!literal) {
        return list.slice(0, 3).map((item, i) =>
            ({...item, label: item.text, kind: i === 0 ? 'suggested' : 'alternative'}));
    }
    const chosen = list[0] ?? null;
    const other = list.find(item => item !== chosen && item.text !== literal) ?? null;
    return [
        {text: literal, index: -1, label: `“${literal}”`, kind: 'literal'},
        chosen && chosen.text !== literal ? {...chosen, label: chosen.text, kind: 'suggested'} : null,
        other ? {...other, label: other.text, kind: 'alternative'} : null,
    ].filter(Boolean);
}

// Keyboard height in logical pixels for a monitor and a layout's row count.
// Phone landscape uses compact rows, phone portrait full Keycap rows, and
// larger screens keep the platform's proportional height with touch-size
// rows; the strip sits above the keys.
export function keyboardHeight(monitor, rows) {
    const g = keycap.geometry;
    const gutters = g.gutter_top + g.gutter_bottom;
    const landscape = monitor.width > monitor.height;
    const compact = landscape && monitor.height < g.compact_landscape_height;
    let keys;
    if (compact)
        keys = rows * Math.max(g.landscape_row, g.landscape_cap + gutters);
    else if (landscape)
        keys = Math.max(rows * g.min_row, monitor.height / 3);
    else
        keys = Math.max(rows * (g.portrait_cap + gutters), monitor.height / 4);
    keys = Math.max(keys, rows * g.min_row);
    // A phone on its side cannot fit touch rows in half its height; it may
    // use up to two thirds, everything else at most half.
    const limit = monitor.height * (compact ? 2 / 3 : 1 / 2);
    return Math.round(Math.min(keys + g.strip_height, limit));
}

// Horizontal drag on space moves the caret one character per step once the
// drag is clearly sideways; returns the caret steps not yet sent.
export function spaceCursorSteps(dx, dy, sent) {
    const t = keycap.timing;
    if (sent === 0 && (Math.abs(dx) <= t.space_cursor_threshold || Math.abs(dy) >= t.space_cursor_vertical_limit))
        return 0;
    return Math.trunc(dx / t.space_cursor_step) - sent;
}

// Dragging left from delete selects whole words backwards.
export function deleteSelectWords(dx) {
    const t = keycap.timing;
    if (dx > -t.delete_select_threshold)
        return 0;
    return Math.min(32, Math.max(1, Math.trunc(-dx / t.delete_select_word)));
}

// Copied text kept in memory only: at most max_clips, max_bytes each, gone
// after ttl_seconds unless pinned. Times are monotonic milliseconds.
export class ClipboardHistory {
    constructor(limits = keycap.clipboard) {
        this._max = limits.max_clips;
        this._bytes = limits.max_bytes;
        this._ttl = limits.ttl_seconds * 1000;
        this._items = [];
    }

    _prune(now) {
        this._items = this._items.filter(item => item.pinned || now - item.copied < this._ttl);
    }

    add(text, now) {
        if (typeof text !== 'string' || text.length === 0 ||
            new TextEncoder().encode(text).length > this._bytes || text.includes('�'))
            return false;
        this._prune(now);
        const existing = this._items.find(item => item.text === text);
        const pinned = existing?.pinned ?? false;
        if (existing)
            this._items.splice(this._items.indexOf(existing), 1);
        if (this._items.length >= this._max) {
            const oldest = this._items.findIndex(item => !item.pinned);
            if (oldest < 0)
                return false;
            this._items.splice(oldest, 1);
        }
        this._items.push({text, copied: now, pinned});
        return true;
    }

    list(now) {
        this._prune(now);
        return this._items.map(item => item.text).reverse();
    }

    has(text, now) {
        return this.list(now).includes(text);
    }

    pin(text, pinned) {
        const item = this._items.find(candidate => candidate.text === text);
        if (item)
            item.pinned = pinned;
    }

    isPinned(text) {
        return this._items.some(item => item.text === text && item.pinned);
    }

    remove(text) {
        this._items = this._items.filter(item => item.text !== text);
    }
}

// One history per Shell process: it survives the keyboard being rebuilt when
// a hardware keyboard comes and goes, and is never written anywhere.
export const clipboardHistory = new ClipboardHistory();

// Password managers mark secrets with this target; such copies are skipped.
export const SECRET_CLIPBOARD_TARGET = 'x-kde-passwordManagerHint';
