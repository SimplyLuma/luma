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

// Keyboard height in logical pixels for a monitor (logical size), a layout
// of `rows` rows whose widest row spans `columns` key widths, and a bottom
// inset the presentation keeps clear (the home gesture). Caps keep the
// design's proportion (portrait_cap tall at cap_width wide) within the width
// each key actually gets, never shorter than a touch row: on a narrow phone
// that is shorter than the design's 46 px. Phone landscape uses compact rows;
// screens at least compact_width wide keep a proportional height.
export function keyboardHeight(monitor, rows, columns = 10, inset = 0) {
    const g = keycap.geometry;
    const gutters = g.gutter_top + g.gutter_bottom;
    const landscape = monitor.width > monitor.height;
    const compact = landscape && monitor.height < g.compact_landscape_height;
    const capWidth = monitor.width / Math.max(1, columns) - g.gutter_x;
    const cap = Math.min(g.portrait_cap, Math.round(capWidth * g.portrait_cap / g.cap_width));
    const row = Math.max(g.min_row, cap + gutters);
    let keys;
    if (compact)
        keys = rows * Math.max(g.landscape_row, g.landscape_cap + gutters);
    else if (monitor.width < g.compact_width)
        keys = rows * row;
    else
        keys = Math.max(rows * row, monitor.height / (landscape ? 3 : 4));
    // A phone on its side cannot fit touch rows in half its height; it may
    // use up to two thirds, everything else at most half.
    const limit = monitor.height * (compact ? 2 / 3 : 1 / 2);
    return Math.round(Math.min(keys + g.strip_height + inset, limit));
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

// Word suggestions and autocorrection, computed off the key path: a tap
// commits its character at once, and analysis of the word follows when the
// Shell is idle. Words come from a dictionary (Hunspell) and a frequency
// list; candidates are ranked by frequency times how likely the edit is as
// a touch typo (keycap.prediction). Case follows the typed word.
const WORD = /^[\p{L}\p{M}'’]+$/u;
const LETTERS = 'abcdefghijklmnopqrstuvwxyz\'';
const BOUNDARY = /^[\s.,!?;:)\]}"”]$/u;

export function isWordCharacter(character) {
    return WORD.test(character);
}

// Characters that end a word: where autocorrection may apply.
export function isWordBoundary(character) {
    return BOUNDARY.test(character);
}

const ROWS = ['qwertyuiop', 'asdfghjkl', 'zxcvbnm'];
const POSITIONS = new Map([...ROWS.entries()].flatMap(([row, keys]) =>
    [...keys].map((key, column) => [key, [row, column + row * 0.5]])));

// Keys that touch on a QWERTY touch keyboard.
function neighbours(a, b) {
    const [pa, pb] = [POSITIONS.get(a), POSITIONS.get(b)];
    return !!pa && !!pb && a !== b && Math.abs(pa[0] - pb[0]) <= 1 && Math.abs(pa[1] - pb[1]) <= 1;
}

function matchCase(candidate, typed) {
    if (typed.length > 1 && typed === typed.toUpperCase() && typed !== typed.toLowerCase())
        return candidate.toUpperCase();
    if (typed[0] !== typed[0].toLowerCase())
        return candidate[0].toUpperCase() + candidate.slice(1);
    return candidate;
}

export class WordSuggester {
    // words: dictionary entries; frequencies: Map(word -> corpus count). A
    // large vocabulary is added in slices instead (addWords/addFrequencies),
    // so building it never holds a frame.
    constructor(words = [], frequencies = new Map()) {
        this._frequency = new Map();
        this._known = new Set();
        this._buckets = new Map();
        this._kept = new Set();
        this._accepted = new Map();
        this.addFrequencies(frequencies);
        this.addWords(words);
    }

    _add(lower) {
        if (this._known.has(lower))
            return;
        this._known.add(lower);
        const key = lower[0];
        let bucket = this._buckets.get(key);
        if (!bucket)
            this._buckets.set(key, bucket = {words: [], sorted: true});
        bucket.words.push(lower);
        bucket.sorted = false;
    }

    addWords(words) {
        for (const word of words) {
            const lower = word.toLowerCase();
            if (WORD.test(lower))
                this._add(lower);
        }
    }

    addFrequencies(entries) {
        const minimum = keycap.prediction.known_min_count;
        for (const [word, count] of entries) {
            const lower = word.toLowerCase();
            if (!WORD.test(lower) || count < minimum)
                continue;
            this._frequency.set(lower, Math.max(count, this._frequency.get(lower) ?? 0));
            this._add(lower);
        }
    }

    get size() {
        return this._known.size;
    }

    // Sort the prefix index one initial letter at a time (each a short step).
    *sortSlices() {
        for (const bucket of this._buckets.values()) {
            if (!bucket.sorted) {
                bucket.words.sort();
                bucket.sorted = true;
                yield;
            }
        }
    }

    has(word) {
        const lower = word.toLowerCase();
        return this._known.has(lower) || this._kept.has(lower);
    }

    // How common a word is, including the person's accepted choices.
    frequency(word) {
        const lower = word.toLowerCase();
        return (this._frequency.get(lower) ?? (this._known.has(lower) ? 1 : 0)) +
            (this._accepted.get(lower) ?? 0) * 1000;
    }

    // The person's own words: one they kept by undoing a correction is never
    // corrected again; accepted suggestions rank higher.
    keep(word) {
        this._kept.add(word.toLowerCase());
    }

    accept(word) {
        const lower = word.toLowerCase();
        if (WORD.test(lower))
            this._accepted.set(lower, (this._accepted.get(lower) ?? 0) + 1);
    }

    get learned() {
        return {kept: [...this._kept], accepted: Object.fromEntries(this._accepted)};
    }

    restore({kept = [], accepted = {}} = {}) {
        const limit = keycap.prediction.learned_limit;
        kept.slice(-limit).forEach(word => this.keep(word));
        Object.entries(accepted).slice(-limit).forEach(([word, count]) =>
            this._accepted.set(word.toLowerCase(), count));
    }

    _completions(lower, limit) {
        const bucket = this._buckets.get(lower[0]);
        if (!bucket)
            return [];
        if (!bucket.sorted) {
            bucket.words.sort();
            bucket.sorted = true;
        }
        const sorted = bucket.words;
        let low = 0, high = sorted.length;
        while (low < high) {
            const middle = (low + high) >> 1;
            if (sorted[middle] < lower) low = middle + 1;
            else high = middle;
        }
        const found = [];
        for (let i = low; i < sorted.length && found.length < 400 && sorted[i].startsWith(lower); i++) {
            if (sorted[i] !== lower)
                found.push(sorted[i]);
        }
        return found.sort((a, b) => this.frequency(b) - this.frequency(a) || a.length - b.length || (a < b ? -1 : 1))
            .slice(0, limit);
    }

    // Single-edit corrections, each weighted by frequency and by how people
    // mistype on a touch keyboard: swapped or doubled letters, a
    // neighbouring key, then any other edit.
    _corrections(lower) {
        const p = keycap.prediction;
        const weights = new Map();
        const add = (candidate, weight) => {
            if (candidate !== lower && this.has(candidate) && !(weights.get(candidate) >= weight))
                weights.set(candidate, weight);
        };
        const chars = [...lower];
        const join = parts => parts.join('');
        for (let i = 0; i < chars.length - 1; i++)
            add(join([...chars.slice(0, i), chars[i + 1], chars[i], ...chars.slice(i + 2)]), p.transpose);
        for (let i = 0; i < chars.length; i++) {
            add(join([...chars.slice(0, i + 1), chars[i], ...chars.slice(i + 1)]), p.double);
            if (chars[i] === chars[i + 1])
                add(join([...chars.slice(0, i), ...chars.slice(i + 1)]), p.double);
            for (const letter of LETTERS) {
                add(join([...chars.slice(0, i), letter, ...chars.slice(i + 1)]),
                    neighbours(chars[i], letter) ? p.neighbour : p.other);
            }
        }
        for (let i = 0; i <= chars.length; i++) {
            for (const letter of LETTERS) {
                const near = neighbours(chars[i - 1], letter) || neighbours(chars[i], letter);
                add(join([...chars.slice(0, i), letter, ...chars.slice(i)]), near ? p.near_insert : p.other);
            }
        }
        for (let i = 0; i < chars.length; i++)
            add(join([...chars.slice(0, i), ...chars.slice(i + 1)]), p.other);
        return [...weights].map(([candidate, weight]) => [candidate, weight * Math.max(1, this.frequency(candidate))])
            .sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
    }

    // Suggestions for the word being typed and, when one correction is
    // decisive, the autocorrection to apply at the word's end.
    analyse(word, limit = 3) {
        if (!word || !WORD.test(word))
            return {suggestions: [], correction: null};
        const p = keycap.prediction;
        const lower = word.toLowerCase();
        const known = this.has(lower);
        const corrections = known ? [] : this._corrections(lower);
        // Corrections and completions share one ranking: a completion counts
        // as an edit weighted `complete`.
        const scores = new Map(corrections.slice(0, 20));
        for (const candidate of this._completions(lower, 20)) {
            const score = p.complete * Math.max(1, this.frequency(candidate));
            if (!(scores.get(candidate) >= score))
                scores.set(candidate, score);
        }
        const ranked = [...scores].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))
            .slice(0, limit).map(([candidate]) => candidate);
        let correction = null;
        const [best, runnerUp] = corrections;
        // Never correct a known or kept word, a single letter, or a word
        // typed in capitals beyond its first letter.
        const shaped = [...word].length > 1 && word.slice(1) === word.slice(1).toLowerCase();
        if (!known && shaped && best && best[1] >= p.autocorrect_min &&
            (!runnerUp || best[1] >= runnerUp[1] * p.autocorrect_lead))
            correction = matchCase(best[0], word);
        return {suggestions: ranked.map(candidate => matchCase(candidate, word)), correction};
    }

    suggest(word, limit = 3) {
        return this.analyse(word, limit).suggestions;
    }
}

// Word counts from a frequency list in pyspellchecker's format: a JSON object
// of word -> count. Parsed in slices so a large list never holds a frame.
export function* frequencyEntries(text, slice = 2000) {
    const pattern = /"((?:[^"\\]|\\.)*)"\s*:\s*(\d+)/g;
    let batch = [];
    for (let match = pattern.exec(text); match; match = pattern.exec(text)) {
        batch.push([match[1], Number(match[2])]);
        if (batch.length >= slice) {
            yield batch;
            batch = [];
        }
    }
    if (batch.length)
        yield batch;
}

// Words of a Hunspell dictionary (.dic): the first line is a count, each
// entry may carry affix flags after a slash.
export function hunspellWords(text) {
    return [...hunspellSlices(text, Infinity)].flat();
}

export function* hunspellSlices(text, slice = 2000) {
    let start = text.indexOf('\n') + 1;
    let batch = [];
    while (start > 0 && start < text.length) {
        let end = text.indexOf('\n', start);
        if (end < 0)
            end = text.length;
        const word = text.slice(start, end).split('/')[0].trim();
        if (word)
            batch.push(word);
        if (batch.length >= slice) {
            yield batch;
            batch = [];
        }
        start = end + 1;
    }
    if (batch.length)
        yield batch;
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
