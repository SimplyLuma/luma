// SPDX-License-Identifier: GPL-2.0-or-later
// TV posture of the Keycap keyboard: the pure parts (no GI imports, so tests
// run them under gjs or node). There is one on-screen keyboard; in TV posture
// the Shell's Keycap keyboard takes the layout, size and remote navigation
// below (Design Center mockups/tv/keyboard.js). Lengths are TV units
// (tvTokens.js `keyboard`): one pixel of a 1920x1080 layout.

const STRIPPED = new Set(['delete', 'hide', 'emoji', 'languageMenu']);
const RETURN = 0xff0d;

function isReturn(key) {
    return parseInt(key.keyval, 16) === RETURN || key.iconName === 'osk-enter-symbolic';
}

function first(key) {
    return key.strings?.[0] ?? '';
}

function width(row) {
    return row.reduce((sum, key) => sum + (key.width ?? 1) + (key.leftOffset ?? 0), 0);
}

function copy(key) {
    return {...key, strings: key.strings ? [...key.strings] : key.strings};
}

// The TV layout of one keyboard model (the person's language, from the
// Shell's osk-layouts): the same letters and levels, in Gboard on Google TV's
// arrangement. Letter rows lose delete, Enter, emoji, the language key and
// hide; the last letter row keeps one level switch (shift) and ends in delete,
// and on the letter levels drops the punctuation the symbols level has. The
// bottom row is the level switch (123 / ABC), space and the action key (Enter,
// labelled for the field). Rows are centred in half-key steps.
// `keepExtras` keeps the bottom row's own characters (the e-mail and URL
// layouts' @ and .com) between the level switch and space.
export function tvLevels(levels, {keepExtras = false} = {}) {
    return levels.map(level => {
        const letters = level.level === 'default' || level.level === 'shift';
        const rows = level.rows.map(row => row.map(copy));
        const bottom = rows.pop() ?? [];
        const body = rows.map(row => row.filter(key => !STRIPPED.has(key.action) && !isReturn(key))
            .map(key => ({...key, width: key.width ?? 1, leftOffset: 0})));
        if (body.length) {
            const last = body.length - 1;
            let switched = false;
            body[last] = body[last].filter(key => {
                if (key.action === 'levelSwitch') {
                    const keep = !switched;
                    switched = true;
                    return keep;
                }
                return !letters || key.action || key.keyval || /\p{L}/u.test(first(key));
            });
            body[last].push({action: 'delete', iconName: 'osk-delete-symbolic', width: 1, leftOffset: 0});
        }
        const span = Math.max(4, ...body.map(width));
        const mode = bottom.find(key => key.action === 'levelSwitch');
        const extras = keepExtras
            ? bottom.filter(key => !key.action && !key.keyval && first(key) !== ' ' && first(key) !== '')
            : [];
        const tail = [];
        if (mode)
            tail.push({...mode, label: mode.label === '?123' ? '123' : mode.label, width: 1.5, leftOffset: 0});
        for (const key of extras)
            tail.push({...key, width: 1, leftOffset: 0});
        const used = tail.reduce((sum, key) => sum + key.width, 0) + 2.5;
        tail.push({strings: [' '], width: Math.max(1, span - used), leftOffset: 0});
        tail.push({keyval: '0xff0d', label: 'Done', tvAction: true, width: 2.5, leftOffset: 0});
        const all = [...body, tail];
        const widest = Math.max(...all.map(width));
        // Centred in half-key steps (the grid places keys in halves of a key).
        for (const row of all) {
            if (row.length)
                row[0].leftOffset = Math.floor(widest - width(row)) / 2;
        }
        return {...level, rows: all};
    });
}

// The remote's next key from `current` (an index into `cells`, each
// {row, x1, x2}) in `direction`. Left and Right stay in the row and stop at
// its ends; Up and Down go to the key in the next row nearest under the
// current one's centre. Returns the new index, the same index at a row's
// end, or -1 for Up from the top row (which puts the keyboard away).
export function tvMove(cells, current, direction) {
    const here = cells[current];
    if (!here)
        return cells.length ? 0 : -1;
    const centre = cell => (cell.x1 + cell.x2) / 2;
    if (direction === 'left' || direction === 'right') {
        const row = cells.map((cell, index) => ({cell, index})).filter(({cell}) => cell.row === here.row)
            .sort((a, b) => a.cell.x1 - b.cell.x1);
        const at = row.findIndex(({index}) => index === current);
        const next = row[at + (direction === 'right' ? 1 : -1)];
        return next ? next.index : current;
    }
    const rows = [...new Set(cells.map(cell => cell.row))].sort((a, b) => a - b);
    const target = rows[rows.indexOf(here.row) + (direction === 'down' ? 1 : -1)];
    if (target === undefined)
        return direction === 'up' ? -1 : current;
    let best = -1, distance = Infinity;
    cells.forEach((cell, index) => {
        if (cell.row !== target)
            return;
        const d = Math.abs(centre(cell) - centre(here));
        if (d < distance - 0.5) {
            best = index;
            distance = d;
        }
    });
    return best;
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
export function tvKeyboardHeight(tokens, rows = 4) {
    const k = tokens.keyboard;
    return k.padding_top + rows * k.key_height + (rows - 1) * k.row_gap + k.padding_bottom;
}

// One key cell's size in TV units (a cap plus half the gap on every side):
// the key grid's aspect is columns x cell width by rows x cell height.
export function tvCell(tokens) {
    const k = tokens.keyboard;
    return {width: k.key_width + k.key_gap, height: k.key_height + k.row_gap};
}
