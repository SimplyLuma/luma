// SPDX-License-Identifier: GPL-2.0-or-later
// TV posture of the Keycap keyboard: the pure parts (no GI imports, so tests
// run them under gjs or node). There is one on-screen keyboard; in TV posture
// the Shell's Keycap keyboard takes the layout, size and remote navigation
// below (Design Center mockups/tv/keyboard.js). Lengths are TV units
// (tvTokens.js `keyboard`): one pixel of a 1920x1080 layout.

const RETURN = 0xff0d;
const DIGITS = [...'1234567890'];
// What fills a letter row out to the grid's width, in order (Android TV's
// Leanback keyboard: q-p @ / a-l _ & / z-m , . - ?), then more for
// languages with shorter rows.
const FILL = ['@', '_', '&', ',', '.', '-', '?', '\'', '!', '/', ':', ';', '"'];

function first(key) {
    return key.strings?.[0] ?? '';
}

function isLetter(key) {
    return !key.action && !key.keyval && /^\p{L}$/u.test(first(key));
}

function isSymbol(key) {
    const text = first(key);
    return !key.action && !key.keyval && text !== '' && text !== ' ' && !/\p{L}|\p{N}/u.test(text);
}

const char = text => ({strings: [text], width: 1});

// The letter rows of one level: the first three rows of the model that hold
// letters, letters only.
function letterRows(level) {
    return (level?.rows ?? []).map(row => row.filter(isLetter).map(first)).filter(row => row.length).slice(0, 3);
}

// The TV layout of one keyboard model (the person's language, from the
// Shell's osk-layouts): an aligned grid, every key the same size and every
// row on the same columns, as Android TV's keyboard (Leanback) and the
// tvOS grid keyboard, so the remote moves in straight lines.
//
//     1 2 3 4 5 6 7 8 9 0 ⌫
//     q w e r t y u i o p @
//     a s d f g h j k l _ &
//     z x c v b n m , . - ?
//     123 ⇧ [  space (7)  ] [action (2)]
//
// The letters are the model's own (its shift level for capitals); short
// rows are filled from FILL. 123 shows the model's symbols in the same three
// rows; digits and delete stay on top. The
// action key is Enter, labelled for the field. `keepExtras` puts the bottom
// row's own characters (the e-mail and URL layouts' @, .com) first in FILL.
export function tvLevels(levels, {keepExtras = false} = {}) {
    const byName = new Map(levels.map(level => [level.level, level]));
    const base = byName.get('default') ?? levels[0];
    const extras = keepExtras
        ? (base?.rows.at(-1) ?? []).filter(key => !key.action && !key.keyval && first(key).trim()).map(first)
        : [];
    const fill = [...new Set([...extras, ...FILL])];
    const lowers = letterRows(base);
    const width = Math.max(11, ...lowers.map(row => row.length + 1));
    // Each filler is used once, across the rows in order.
    const pad = rows => {
        const left = fill.filter(text => !rows.flat().includes(text));
        return rows.map(row => [...row, ...left.splice(0, Math.max(0, width - row.length))]);
    };
    const symbols = [];
    for (const name of ['opt', 'opt+shift']) {
        for (const row of byName.get(name)?.rows ?? []) {
            for (const key of row) {
                if (isSymbol(key) && !symbols.includes(first(key)))
                    symbols.push(first(key));
            }
        }
    }
    const page = width * 3;
    const top = [...DIGITS.map(char), {action: 'delete', iconName: 'osk-delete-symbolic', width: width - 10}];
    const space = width - 4;
    const bottom = (mode, modeLevel, shift) => [
        {action: 'levelSwitch', level: modeLevel, label: mode, width: 1},
        shift,
        {strings: [' '], width: space},
        {keyval: '0xff0d', label: 'Done', tvAction: true, width: 2},
    ];
    const shiftKey = level => ({action: 'levelSwitch', level, iconName: 'osk-shift-symbolic', width: 1});
    const out = [];
    const letters = (name, rows, toggle) => out.push({
        level: name, mode: name === 'shift' ? 'latched' : 'default',
        rows: [top, ...pad(rows).map(row => row.map(char)), bottom('123', 'opt', shiftKey(toggle))],
    });
    letters('default', lowers, 'shift');
    const capitals = letterRows(byName.get('shift'));
    letters('shift', capitals.length === lowers.length ? capitals
        : lowers.map(row => row.map(text => text.toLocaleUpperCase())), 'default');
    // 123: one page of the model's symbols in the letter rows (as
    // Leanback's symbol layout), common ones first; ⇧ has nothing to do
    // there, so its cell stays empty.
    if (symbols.length) {
        const chunk = symbols.slice(0, page);
        const rows = [0, 1, 2].map(r => chunk.slice(r * width, (r + 1) * width).map(char));
        out.push({level: 'opt', mode: 'locked',
            rows: [top, ...rows, bottom('ABC', 'default', {strings: [''], width: 1, blank: true})]});
    }
    // Empty cells hold their column: the next key starts after them.
    for (const level of out) {
        level.rows = level.rows.map(row => {
            const keys = [];
            let offset = 0;
            for (const key of row) {
                if (key.blank) {
                    offset += key.width;
                    continue;
                }
                keys.push({...key, leftOffset: offset});
                offset = 0;
            }
            return keys;
        });
    }
    return out;
}

// The grid column a key covers, as x positions: a move up or down keeps the
// column it started from (the anchor), so passing through space and back
// returns to the same letter.
function covers(cell, x) {
    return cell.x1 <= x && x < cell.x2;
}

// The remote's next key from `current` (an index into `cells`, each
// {row, x1, x2}) in `direction`. Left and Right stay in the row and wrap at
// its ends; Up and Down go to the key in the next row under `anchor` (the x
// the column started from; the current key's centre by default). Returns
// {index, anchor}: index -1 for Up from the top row (which puts the
// keyboard away); Down from the bottom row stays.
export function tvMove(cells, current, direction, anchor = null) {
    const here = cells[current];
    if (!here)
        return {index: cells.length ? 0 : -1, anchor: null};
    const centre = cell => (cell.x1 + cell.x2) / 2;
    if (direction === 'left' || direction === 'right') {
        const row = cells.map((cell, index) => ({cell, index})).filter(({cell}) => cell.row === here.row)
            .sort((a, b) => a.cell.x1 - b.cell.x1);
        const at = row.findIndex(({index}) => index === current);
        const next = row[(at + (direction === 'right' ? 1 : -1) + row.length) % row.length];
        return {index: next.index, anchor: centre(next.cell)};
    }
    const x = anchor ?? centre(here);
    const rows = [...new Set(cells.map(cell => cell.row))].sort((a, b) => a - b);
    const target = rows[rows.indexOf(here.row) + (direction === 'down' ? 1 : -1)];
    if (target === undefined)
        return {index: direction === 'up' ? -1 : current, anchor: x};
    let best = -1, distance = Infinity;
    cells.forEach((cell, index) => {
        if (cell.row !== target)
            return;
        const d = covers(cell, x) ? -1 : Math.abs(centre(cell) - x);
        if (d < distance) {
            best = index;
            distance = d;
        }
    });
    return {index: best, anchor: x};
}

// Where the remote's focus starts: the first letter (q), as on Android TV.
export function tvStart(cells) {
    const rows = [...new Set(cells.map(cell => cell.row))].sort((a, b) => a - b);
    const row = rows.length > 2 ? rows[1] : rows[0];
    const first = cells.map((cell, index) => ({cell, index})).filter(({cell}) => cell.row === row)
        .sort((a, b) => a.cell.x1 - b.cell.x1)[0];
    return first ? first.index : 0;
}

// What a remote key does while the TV keyboard is open. Arrows move, OK types
// the focused key, Back puts the keyboard away. Everything else (letters
// typed on a real keyboard, Backspace) goes straight to the field.
export function tvRemoteKey(name) {
    switch (name) {
    case 'Up': case 'Down': case 'Left': case 'Right':
        return name.toLowerCase();
    case 'Return': case 'KP_Enter': case 'ISO_Enter': case 'Select':
        return 'ok';
    case 'Escape': case 'Back':
        return 'back';
    default:
        return null;
    }
}

// The keys the TV keyboard takes from every window while it is open (Mutter
// accelerators, so the field keeps its focus and everything else still
// reaches it). A remote's Back and OK and a gamepad's buttons arrive as these
// keys (tvInput.js), so there is nothing remote-specific to take.
export const TV_REMOTE_KEYS = ['Up', 'Down', 'Left', 'Right', 'Return', 'KP_Enter', 'Escape'];

// The action key's label: what the application asked for (LumaUI's
// keyboard action, e.g. "Search" or "Add"), else what the field's purpose
// says (Go for an address), else Done. `purposes` is Clutter's
// InputContentPurpose. Returns a message id for the Shell to translate.
export function tvActionLabel(requested, purpose, purposes) {
    const label = typeof requested === 'string' ? requested.trim() : '';
    if (label)
        return label.slice(0, 24);
    if (purpose === purposes.URL)
        return 'Go';
    return 'Done';
}

// The keyboard's height in TV units: padding, rows and the gaps between them.
export function tvKeyboardHeight(tokens, rows = 5) {
    const k = tokens.keyboard;
    return k.padding_top + rows * k.key_height + (rows - 1) * k.row_gap + k.padding_bottom;
}

// One key cell's size in TV units (a cap plus half the gap on every side):
// the key grid's aspect is columns x cell width by rows x cell height.
export function tvCell(tokens) {
    const k = tokens.keyboard;
    return {width: k.key_width + k.key_gap, height: k.key_height + k.row_gap};
}
