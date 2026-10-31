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

// Key timing is decided from event times (milliseconds since the press), never
// from when a timer happened to run: a stalled frame cannot turn a tap into a
// hold. A key held `heldMs` is a long press from keycap.timing.long_press.
export function isLongPress(heldMs) {
    return heldMs >= keycap.timing.long_press;
}

// Deletions a held delete key owes after `heldMs`: the first at the repeat
// delay, then one per repeat interval.
export function repeatsDue(heldMs, delay = keycap.timing.delete_repeat_delay,
    interval = keycap.timing.delete_repeat_interval) {
    return heldMs < delay ? 0 : 1 + Math.floor((heldMs - delay) / interval);
}

// When the next repeat falls due, in milliseconds since the press.
export function nextRepeatAt(sent, delay = keycap.timing.delete_repeat_delay,
    interval = keycap.timing.delete_repeat_interval) {
    return sent === 0 ? delay : delay + sent * interval;
}

// Hit testing. A key's target is its whole cell (cap plus gutters); a touch
// that lands in no cell (the keyboard's own padding, an aspect margin) goes to
// the nearest key. Cells are {x1, y1, x2, y2} in one coordinate space.
export function cellDistance(cell, x, y) {
    const dx = Math.max(cell.x1 - x, 0, x - cell.x2);
    const dy = Math.max(cell.y1 - y, 0, y - cell.y2);
    return Math.hypot(dx, dy);
}

export function nearestCell(cells, x, y) {
    // Almost every touch lands inside a cell: find it without measuring.
    for (const cell of cells) {
        if (x >= cell.x1 && x < cell.x2 && y >= cell.y1 && y < cell.y2)
            return cell;
    }
    let best = null, bestDistance = Infinity, bestCentre = Infinity;
    for (const cell of cells) {
        const dx = Math.max(cell.x1 - x, 0, x - cell.x2);
        const dy = Math.max(cell.y1 - y, 0, y - cell.y2);
        const distance = dx * dx + dy * dy;
        const cx = x - (cell.x1 + cell.x2) / 2, cy = y - (cell.y1 + cell.y2) / 2;
        const centre = cx * cx + cy * cy;
        if (distance < bestDistance || (distance === bestDistance && centre < bestCentre)) {
            best = cell;
            bestDistance = distance;
            bestCentre = centre;
        }
    }
    return best;
}

// Which key a touch means. Geometry decides, except near a boundary between
// two letters, where the word being typed may move that boundary by at most
// `reach` of a key (iOS/LatinIME-style dynamic targets, bounded so a clear tap
// is never overruled). prior(cell) is the relative likelihood of the cell's
// letter coming next (any positive scale); cells with `letter` take part.
export function chooseCell(cells, x, y, prior = null) {
    const p = keycap.prediction;
    const geometric = nearestCell(cells, x, y);
    if (!geometric || !prior || !geometric.letter || cellDistance(geometric, x, y) > 0)
        return geometric;
    const width = geometric.x2 - geometric.x1, height = geometric.y2 - geometric.y1;
    const candidates = cells.filter(cell => cell.letter && (cell === geometric ||
        cellDistance(cell, x, y) <= p.target_reach * Math.min(width, height)));
    if (candidates.length < 2)
        return geometric;
    const score = cell => {
        const nx = (x - (cell.x1 + cell.x2) / 2) / (cell.x2 - cell.x1);
        const ny = (y - (cell.y1 + cell.y2) / 2) / (cell.y2 - cell.y1);
        const sigma = p.target_sigma;
        return Math.exp(-(nx * nx + ny * ny) / (2 * sigma * sigma)) *
            Math.pow(Math.max(prior(cell), 0) + p.target_floor, p.target_weight);
    };
    let best = geometric, bestScore = score(geometric);
    for (const cell of candidates) {
        const value = score(cell);
        if (value > bestScore) {
            best = cell;
            bestScore = value;
        }
    }
    return best;
}

// Touch-position correction (LatinIME's sweet spots, learned per row): people
// hit consistently off the cap centres, so each row keeps a small bias, in
// fractions of a key, from taps that were committed and not deleted right
// after. The bias is clamped so a correction never crosses half a gutter.
export class TouchCorrection {
    constructor({rate = keycap.prediction.correction_rate, limit = keycap.prediction.correction_limit} = {}) {
        this._rate = rate;
        this._limit = limit;
        this._rows = new Map();
    }

    bias(row) {
        return this._rows.get(row) ?? [0, 0];
    }

    // Where a touch at (x, y) is aimed, for keys `width` x `height` in `row`.
    correct(row, x, y, width, height) {
        const [bx, by] = this.bias(row);
        return [x - bx * width, y - by * height];
    }

    // A committed tap on a key of `row`, (nx, ny) from its centre in key
    // sizes. Returns a token that retract() takes when the tap is undone.
    learn(row, nx, ny) {
        if (!Number.isFinite(nx) || !Number.isFinite(ny) || Math.abs(nx) > 0.5 || Math.abs(ny) > 0.5)
            return null;
        const before = this.bias(row);
        const clamp = value => Math.max(-this._limit, Math.min(this._limit, value));
        this._rows.set(row, [clamp(before[0] + this._rate * (nx - before[0])),
            clamp(before[1] + this._rate * (ny - before[1]))]);
        return {row, before};
    }

    retract(token) {
        if (token)
            this._rows.set(token.row, token.before);
    }

    reset() {
        this._rows.clear();
    }
}

// What the keyboard knows of the text before the caret: the application's
// last report plus every change this keyboard sent since, so decisions
// (sentence case, double-space period) never wait for a round trip. Reports
// that echo a state this keyboard produced are ignored; any other report is an
// outside change (the caret moved, the application edited) and replaces it.
const MIRROR_TAIL = 48;
const MIRROR_SENT = 64;

const SURROGATE = /[\uD800-\uDFFF]/;
const NOT_HIGH_SURROGATE = /[^\uD800-\uDBFF]+/g;

// The last `max` code units of `text`, never splitting a surrogate pair.
function tailUnits(text, max) {
    if (text.length <= max)
        return text;
    let start = text.length - max;
    const unit = text.charCodeAt(start);
    if (unit >= 0xDC00 && unit <= 0xDFFF)
        start++;
    return text.slice(start);
}

// The text just before the caret, at most `max` code units of it, for a
// surrounding text whose caret counts characters (code points), as Clutter's
// does. It neither walks nor copies a long document: a text without
// surrogates is indexed directly, and only one with them is scanned (without
// building arrays) to find the caret.
export function textBeforeCursor(text, cursor, max = 4 * MIRROR_TAIL) {
    if (!text || !(cursor > 0))
        return '';
    let end;
    if (!SURROGATE.test(text)) {
        end = Math.min(cursor, text.length);
    } else {
        // Characters in the text, counted natively; then walk to the caret
        // from whichever end is nearer (usually zero steps from the end).
        const total = text.length - text.replace(NOT_HIGH_SURROGATE, '').length;
        if (cursor >= total) {
            end = text.length;
        } else if (total - cursor < cursor) {
            end = text.length;
            for (let points = total; points > cursor && end > 0; points--) {
                const unit = text.charCodeAt(end - 1);
                end -= unit >= 0xDC00 && unit <= 0xDFFF && end > 1 ? 2 : 1;
            }
        } else {
            end = 0;
            for (let points = 0; end < text.length && points < cursor; points++) {
                const unit = text.charCodeAt(end);
                end += unit >= 0xD800 && unit <= 0xDBFF && end + 1 < text.length ? 2 : 1;
            }
        }
    }
    return tailUnits(text.slice(Math.max(0, end - max - 1), end), max);
}

export class TextMirror {
    constructor() {
        this.reset();
    }

    reset(before = '') {
        this.before = tailUnits(before ?? '', 4 * MIRROR_TAIL);
        this._sent = [];
    }

    // Bounded whatever the text's length (reports can be whole documents).
    _tail(text) {
        return [...tailUnits(text, 2 * MIRROR_TAIL)].slice(-MIRROR_TAIL).join('');
    }

    // This keyboard replaced `count` characters before the caret with `text`.
    // An application may report the deletion and the insertion separately;
    // both states are its own echoes.
    apply(count, text) {
        const chars = [...this.before];
        const kept = chars.slice(0, Math.max(0, chars.length - count)).join('');
        if (count > 0 && text)
            this._sent.push(this._tail(kept));
        this.before = kept + text;
        if ([...this.before].length > 4 * MIRROR_TAIL)
            this.before = [...this.before].slice(-2 * MIRROR_TAIL).join('');
        this._sent.push(this._tail(this.before));
        if (this._sent.length > MIRROR_SENT)
            this._sent.shift();
    }

    // The application reported `before` (its text before the caret). Returns
    // true when that is an outside change.
    report(before) {
        const tail = this._tail(before ?? '');
        const index = this._sent.lastIndexOf(tail);
        if (index >= 0) {
            this._sent.splice(0, index + 1);
            if (this._sent.length === 0)
                this.before = tailUnits(before ?? '', 4 * MIRROR_TAIL);
            return false;
        }
        if (this._sent.length === 0 && tail === this._tail(this.before))
            return false;
        this.reset(before ?? '');
        return true;
    }

    get pending() {
        return this._sent.length > 0;
    }
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
        return this._frequencyOf(word.toLowerCase());
    }

    _frequencyOf(lower) {
        return (this._frequency.get(lower) ?? (this._known.has(lower) ? 1 : 0)) +
            (this._accepted.get(lower) ?? 0) * 1000;
    }

    _hasLower(lower) {
        return this._known.has(lower) || this._kept.has(lower);
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
        const weight = new Map(found.map(word => [word, this._frequencyOf(word)]));
        return found.sort((a, b) => weight.get(b) - weight.get(a) || a.length - b.length || (a < b ? -1 : 1))
            .slice(0, limit);
    }

    // Single-edit corrections, each weighted by frequency and by how people
    // mistype on a touch keyboard: swapped or doubled letters, a
    // neighbouring key, then any other edit.
    _corrections(lower) {
        const p = keycap.prediction;
        const weights = new Map();
        const add = (candidate, weight) => {
            if (candidate !== lower && this._hasLower(candidate) && !(weights.get(candidate) >= weight))
                weights.set(candidate, weight);
        };
        // Candidates are joined from prefixes and suffixes computed once per
        // word: no array is built or joined per candidate.
        const chars = [...lower];
        const n = chars.length;
        const prefix = [''], suffix = [];
        for (let i = 0; i < n; i++)
            prefix.push(prefix[i] + chars[i]);
        suffix[n] = '';
        for (let i = n - 1; i >= 0; i--)
            suffix[i] = chars[i] + suffix[i + 1];
        for (let i = 0; i < n - 1; i++)
            add(prefix[i] + chars[i + 1] + chars[i] + suffix[i + 2], p.transpose);
        for (let i = 0; i < n; i++) {
            add(prefix[i + 1] + chars[i] + suffix[i + 1], p.double);
            if (chars[i] === chars[i + 1])
                add(prefix[i] + suffix[i + 1], p.double);
            for (const letter of LETTERS) {
                add(prefix[i] + letter + suffix[i + 1],
                    neighbours(chars[i], letter) ? p.neighbour : p.other);
            }
        }
        for (let i = 0; i <= n; i++) {
            for (const letter of LETTERS) {
                const near = neighbours(chars[i - 1], letter) || neighbours(chars[i], letter);
                add(prefix[i] + letter + suffix[i], near ? p.near_insert : p.other);
            }
        }
        for (let i = 0; i < n; i++)
            add(prefix[i] + suffix[i + 1], p.other);
        return [...weights].map(([candidate, weight]) => [candidate, weight * Math.max(1, this._frequencyOf(candidate))])
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

    // How likely `prefix` continues into a word: the summed frequency of up
    // to `cap` known words starting with it (a bounded scan of the sorted
    // index, cheap enough for the touch that asks).
    prefixMass(prefix, cap = 64) {
        const lower = (prefix ?? '').toLowerCase();
        if (!lower)
            return 0;
        const bucket = this._buckets.get(lower[0]);
        if (!bucket)
            return 0;
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
        let mass = 0;
        for (let i = low, n = 0; i < sorted.length && n < cap && sorted[i].startsWith(lower); i++, n++)
            mass += Math.max(1, this._frequencyOf(sorted[i]));
        return mass;
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
