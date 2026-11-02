// SPDX-License-Identifier: Apache-2.0
// The TV quick panel's pure rules: which rows it shows, how the remote moves
// through them, and how the shell's own Quick Options and notifications read
// on a TV. tvQuickPanel.js draws them; nothing here touches the Shell.
// docs/developer-platform/tv-mode.md ("The quick panel").

// Rows the remote never stops on.
const PASSIVE = new Set(['sep', 'label']);

// The panel, top to bottom. `state` holds only what the shell really has:
//   me        {name, focusable}: the account (its picture and name)
//   toggles   [{key, visible, ...}]: Quick Options toggles; hidden ones drop out
//   level     {visible, outputs: [{id, name}], current}: the output volume
//   notes     [notification records], newest first
//   settings  true when Settings can be opened
//   sleep     true when the TV can sleep (suspend or screen off)
//   power     {restart, powerOff}: what logind allows
//   open      {outputs, power}: which rows are dropped open in place
export function panelRows(state) {
    const rows = [];
    const section = () => {
        if (rows.length && rows[rows.length - 1].kind !== 'sep') rows.push({kind: 'sep', key: `sep${rows.length}`});
    };
    if (state.me) rows.push({kind: 'me', key: 'me', focusable: Boolean(state.me.focusable)});
    const toggles = (state.toggles ?? []).filter(toggle => toggle.visible);
    if (toggles.length) {
        section();
        rows.push({kind: 'chips', key: 'chips', chips: toggles});
    }
    if (state.level?.visible) {
        section();
        rows.push({kind: 'level', key: 'level'});
        if (state.open?.outputs && (state.level.outputs?.length ?? 0) > 1) {
            for (const output of state.level.outputs)
                rows.push({kind: 'output', key: `output:${output.id}`, output, current: output.id === state.level.current});
        }
    }
    const notes = state.notes ?? [];
    if (notes.length) {
        section();
        rows.push({kind: 'label', key: 'label', count: notes.length});
        notes.forEach((note, index) => rows.push({kind: 'note', key: `note:${note.id ?? index}`, note}));
        if (notes.some(note => note.clearable)) rows.push({kind: 'clear', key: 'clear'});
    }
    const restart = Boolean(state.power?.restart), powerOff = Boolean(state.power?.powerOff);
    if (state.settings || state.sleep || restart || powerOff) section();
    if (state.settings) rows.push({kind: 'settings', key: 'settings'});
    if (state.sleep) rows.push({kind: 'sleep', key: 'sleep'});
    if (restart || powerOff) {
        rows.push({kind: 'power', key: 'power', open: Boolean(state.open?.power)});
        if (state.open?.power) {
            if (restart) rows.push({kind: 'restart', key: 'restart'});
            if (powerOff) rows.push({kind: 'power-off', key: 'power-off'});
        }
    }
    if (rows.length && rows[rows.length - 1].kind === 'sep') rows.pop();
    return rows;
}

export function focusable(row) {
    if (!row || PASSIVE.has(row.kind)) return false;
    if (row.kind === 'me') return row.focusable;
    if (row.kind === 'chips') return row.chips.some(chip => chip.reactive !== false);
    return true;
}

// The row the panel opens on: the first one the remote can stop on.
export function firstStop(rows) {
    return rows.findIndex(focusable);
}

// Up/Down: the next row the remote can stop on; at either end it stays.
export function stepRow(rows, index, direction) {
    const step = direction === 'up' ? -1 : 1;
    for (let i = index + step; i >= 0 && i < rows.length; i += step) {
        if (focusable(rows[i])) return i;
    }
    return index;
}

// The same row after a rebuild, found by its key; else the nearest stop.
export function keepRow(rows, key, fallback) {
    const index = rows.findIndex(row => row.key === key);
    if (index >= 0 && focusable(rows[index])) return index;
    const from = Math.max(0, Math.min(rows.length - 1, fallback));
    if (focusable(rows[from])) return from;
    const down = stepRow(rows, from, 'down');
    return down !== from ? down : stepRow(rows, from, 'up');
}

// Left/Right between chips, skipping any the hardware has switched off.
// -1 past the left edge: the panel closes, as Left off any island does.
export function stepChip(chips, index, direction) {
    const step = direction === 'left' ? -1 : 1;
    for (let i = index + step; i >= 0 && i < chips.length; i += step) {
        if (chips[i].reactive !== false) return i;
    }
    return direction === 'left' ? -1 : index;
}

export function firstChip(chips) {
    return Math.max(0, chips.findIndex(chip => chip.reactive !== false));
}

// A toggle in toggle mode (Do Not Disturb) flips its own `checked`, which its
// settings binding carries; any other toggle acts on `clicked` (Wi-Fi and
// Bluetooth drive NetworkManager and gnome-bluetooth there).
export function toggleMethod(toggle) {
    return toggle.toggleMode ? 'flip' : 'click';
}

// A chip's second line: what it is connected to while on, else On or Off.
export function chipState(toggle, words = {on: 'On', off: 'Off'}) {
    if (!toggle.checked) return words.off;
    const subtitle = (toggle.subtitle ?? '').trim();
    if (toggle.stateOnly || !subtitle || subtitle === toggle.title) return words.on;
    return subtitle;
}

// Left/Right on the volume row: one step, inside the slider's range.
export function stepLevel(value, direction, step, maximum = 1) {
    const next = value + (direction === 'left' ? -step : step);
    return Math.round(Math.max(0, Math.min(maximum, next)) * 1000) / 1000;
}

export function levelPercent(value) {
    return Math.round(Math.max(0, value) * 100);
}

// The notification list, as the shell's own tray reads it: open
// notifications from enabled sources, newest first. `timeOf` reads a
// notification's time in seconds (the Shell's are GLib.DateTime).
export function noteRecords(sources, closed, max = Infinity, timeOf = note => note.time ?? 0) {
    const notes = sources.filter(source => source.policy?.enable !== false)
        .flatMap(source => source.notifications ?? [])
        .filter(note => !closed.has(note.lifecycleState))
        .sort((a, b) => timeOf(b) - timeOf(a) || (b.sortSequence ?? 0) - (a.sortSequence ?? 0));
    return notes.slice(0, max);
}

// How long ago, in the words the TV shows: now, 4 min, 2 h, 3 d.
export function noteAge(seconds) {
    if (!(seconds >= 60)) return {unit: 'now', value: 0};
    if (seconds < 3600) return {unit: 'min', value: Math.floor(seconds / 60)};
    if (seconds < 86400) return {unit: 'h', value: Math.floor(seconds / 3600)};
    return {unit: 'd', value: Math.floor(seconds / 86400)};
}

// Sleep: suspend where logind allows it; otherwise the screen goes dark the
// way the session's screensaver does it (a Raspberry Pi cannot suspend).
export function sleepAction(canSuspend) {
    return canSuspend ? 'suspend' : 'screen-off';
}

// A long press of Home opens the quick panel; a short one goes home.
export function homePress(heldMs, holdMs) {
    return heldMs >= holdMs ? 'quick-panel' : 'home';
}

// Scroll so the focused row and `margin` below it are in view, never past
// either end of the content.
export function scrollTo(rowTop, rowBottom, view, content, margin, current = 0) {
    const max = Math.max(0, content - view);
    let value = current;
    if (rowBottom + margin > value + view) value = rowBottom + margin - view;
    if (rowTop - margin < value) value = rowTop - margin;
    return Math.max(0, Math.min(max, value));
}

// One colour toward another (rgba() strings), keeping the first one's alpha:
// soft accent light on a chip, drawn as a radial gradient.
export function mixRgba(from, to, amount) {
    const parse = value => (value.match(/rgba?\(([^)]+)\)/)?.[1] ?? '0,0,0,1').split(',').map(Number);
    const [r1, g1, b1, a1 = 1] = parse(from), [r2, g2, b2] = parse(to);
    const mix = (x, y) => Math.round(x + (y - x) * amount);
    return `rgba(${mix(r1, r2)}, ${mix(g1, g2)}, ${mix(b1, b2)}, ${a1})`;
}

// OK acts once per press. A held OK can arrive as more presses (a remote that repeats on its
// own, a virtual key held down) without the repeat flag: while one press is held, further
// presses do nothing until it is let go or the presses stop for `quietMs`. A panel opened
// by OK starts with that press held.
export function okGate(heldAt = null) {
    return {held: heldAt !== null, last: heldAt ?? 0};
}

export function okPress(gate, now, quietMs) {
    if (gate.held && now - gate.last < quietMs) {
        gate.last = now;
        return false;
    }
    gate.held = true;
    gate.last = now;
    return true;
}

export function okRelease(gate) {
    gate.held = false;
}
