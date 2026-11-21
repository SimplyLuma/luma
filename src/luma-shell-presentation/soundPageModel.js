// SPDX-License-Identifier: GPL-2.0-or-later
// The Sound page's rules, without gi imports: which outputs are chips, which
// apps are playing rows and what each says. The page (soundPage.js), the TV
// quick panel and tests/gnome-shell/sound-page-policy.js read them.

// More outputs than this: the most recently used, then a "More…" chip.
export const MAX_OUTPUT_CHIPS = 4;
// Rows shown before the playing list scrolls inside its well.
export const VISIBLE_ROWS = 3;
// Left and Right on a row move its volume by this much.
export const VOLUME_STEP = 0.05;

const GLYPHS = {
    speakers: 'lumaui-volume-2-symbolic',
    headphones: 'lumaui-headphones-symbolic',
    usb: 'lumaui-speaker-symbolic',
    airplay: 'lumaui-airplay-symbolic',
    display: 'lumaui-monitor-symbolic',
};

/**
 * @param {object} output - an entry from GetOutputs (version 3)
 * @returns {string} its Lucide glyph
 */
export function outputGlyph(output) {
    return GLYPHS[output?.kind] ?? GLYPHS.speakers;
}

/**
 * @param {object} output - an entry from GetOutputs
 * @returns {string} what a person calls it; never a card or profile name
 */
export function outputLabel(output) {
    return output?.label || output?.description || '';
}

/**
 * AirPlay speakers found nearby that are not outputs yet: each is an output
 * like any other (its own name, the AirPlay glyph); choosing one connects it.
 *
 * @param {object[]} outputs - GetOutputs entries
 * @param {object[]} receivers - GetAirPlayReceivers entries
 * @returns {object[]} entries shaped like outputs, with `receiver` (its id)
 */
export function airPlayOutputs(outputs, receivers) {
    const names = new Set((outputs ?? []).map(output => output.name));
    return (receivers ?? [])
        .filter(r => r.state !== 'unavailable' && r.supported !== false &&
            !(r.node_name && names.has(r.node_name)))
        .map(r => ({name: `airplay:${r.id}`, kind: 'airplay', label: r.name, receiver: r.id,
            busy: !!r.busy || r.state === 'connecting'}));
}

/**
 * The chips of the output grid. Up to four outputs are all shown, in the
 * service's order, AirPlay speakers found nearby after them. With more, the
 * three most recently used (the current one always among them) in that same
 * order, and `more` asks for a "More…" chip.
 *
 * @param {object[]} outputs - GetOutputs entries
 * @param {object[]} [receivers] - GetAirPlayReceivers entries
 * @returns {{chips: object[], more: boolean}}
 */
export function outputChips(outputs, receivers = []) {
    const list = [...outputs ?? [], ...airPlayOutputs(outputs, receivers)];
    if (list.length <= MAX_OUTPUT_CHIPS)
        return {chips: [...list], more: false};
    const recent = [...list].sort((a, b) =>
        Number(!!b.default) - Number(!!a.default) ||
        Number(b.last_used ?? 0) - Number(a.last_used ?? 0));
    const keep = new Set(recent.slice(0, MAX_OUTPUT_CHIPS - 1).map(o => o.name));
    return {chips: list.filter(o => keep.has(o.name)), more: true};
}

/**
 * @param {object[]} applications - GetApplications entries
 * @returns {object[]} the playing list: apps with a stream (or one in the
 *   last ten seconds), never the other sounds while they are silent
 */
export function playingRows(applications) {
    return (applications ?? []).filter(app =>
        app.kind !== 'system' && (app.listed ?? (app.streams?.length > 0)));
}

/**
 * @param {object[]} rows - playingRows()
 * @returns {number} apps that are not paused: the header's "N playing"
 */
export function playingCount(rows) {
    return rows.filter(app => app.playing).length;
}

/**
 * @param {object} app - a playing row
 * @param {string} [windowTitle] - the app's window or tab title, if known
 * @returns {string} the player's title, else the window's, else the app's name
 */
export function rowTitle(app, windowTitle = '') {
    if (app.player)
        return app.title || windowTitle || app.name || app.id;
    return app.name || app.id;
}

/**
 * The words under a row's title: the app, then where it plays when that is
 * not the main output, else that it is paused. Muted says only that. A
 * browser playing in several tabs ("2 tabs playing") names their sites.
 *
 * @param {object} app - a playing row
 * @param {object[]} outputs - GetOutputs entries
 * @param {{muted: string, paused: string, on: Function}} words - translated:
 *   "Muted", "Paused" and (name) => "on name"
 * @returns {string}
 */
export function rowLine(app, outputs, words) {
    if (app.muted)
        return words.muted;
    const name = app.name || app.id;
    // With the player's title above, the app is named here; without one the
    // title already is the app.
    const lead = app.player && (app.title || '') !== '' ? name : '';
    const away = app.output
        ? (outputs ?? []).find(output => output.name === app.output) : null;
    let state = '';
    if (away && !away.default)
        state = words.on(outputLabel(away));
    else if (!app.playing)
        state = words.paused;
    const sites = Number(app.tabs ?? 0) >= 2 ? app.artist || '' : '';
    return [lead, sites, state].filter(Boolean).join(' · ');
}

/**
 * @param {number} value - a volume, 0..maximum
 * @param {number} direction - -1 or 1
 * @param {number} maximum - 1, or 1.5 with amplification allowed
 * @returns {number} the volume one step on, on the 5 % grid
 */
export function stepVolume(value, direction, maximum = 1) {
    const steps = Math.round(value / VOLUME_STEP) + Math.sign(direction);
    return Math.min(maximum, Math.max(0, steps * VOLUME_STEP));
}

/**
 * @param {number} rows - rows in the list
 * @param {number} rowHeight - one row
 * @param {number} gap - between two rows
 * @param {number} room - what the panel can give the rows, or Infinity
 * @returns {number} the list's height: every row up to three, fewer when
 *   the screen is short (never less than one), then it scrolls
 */
export function listHeight(rows, rowHeight, gap, room = Infinity) {
    const tall = count => count * rowHeight + Math.max(0, count - 1) * gap;
    let shown = Math.min(rows, VISIBLE_ROWS);
    while (shown > 1 && tall(shown) > room)
        shown--;
    return tall(shown);
}
