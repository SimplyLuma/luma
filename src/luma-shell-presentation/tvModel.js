// SPDX-License-Identifier: Apache-2.0
// TV posture: the pure parts of the shell's TV presentation (no GI imports, so
// tests run them under node or gjs). Lengths are TV units: one pixel of a
// 1920x1080 layout (tvTokens.js `layout`), so every rule holds at any size.

// One TV unit on a screen of this logical size.
export function tvUnit(width, height, base = {base_width: 1920, base_height: 1080}) {
    if (!(width > 0) || !(height > 0)) return 0;
    return Math.min(width / base.base_width, height / base.base_height);
}

// An application opts in to the TV home with three desktop-entry keys,
// installed from the [tv] table of its luma-app.toml. `entry` reads one key
// (`get(key)` returns a string or null). Returns null for an app that has not
// opted in: no X-Luma-TV, or anything but true.
export function tvEntry(id, name, get) {
    if ((get('X-Luma-TV') ?? '').trim().toLowerCase() !== 'true') return null;
    const tile = (get('X-Luma-TV-Tile') ?? '').trim();
    const presentation = (get('X-Luma-TV-Presentation') ?? '').trim() || 'fullscreen';
    return {id, name, tile: /^[A-Za-z0-9._-]{1,255}$/.test(tile) ? tile : '', presentation};
}

// A Flatpak's TV keys. Flatpak exports a desktop file with only the keys on its own allowlist
// (flatpak-dir.c allowed_desktop_entry_keys), so X-Luma-TV* never reach the export that the shell
// reads (Dell, 5 Nov: Connect, Tide and Weather missing from the TV home). The keys are read from
// the app's own deployed desktop file instead, in the installation whose export the shell found:
//   roots     [{exports, deploy}]: an installation's exports/share/applications/ folder, and its
//             deploy root (<installation>/app); the system's and the user's, nothing else
//   read      path => text or null
// Returns {keys, files} (files: the deploy's files/ folder, for the tile) or null: not a Flatpak,
// not one of those installations, a name that is not a plain app ID, or no TV keys.
const FLATPAK_ID = /^[A-Za-z_][A-Za-z0-9_-]*(\.[A-Za-z_][A-Za-z0-9_-]*){2,}$/;
const DESKTOP_ID = /^[A-Za-z_][A-Za-z0-9_.-]{0,240}\.desktop$/;
export function flatpakTv(desktopId, flatpakId, exportPath, roots, read) {
    if (typeof flatpakId !== 'string' || flatpakId.length > 255 || !FLATPAK_ID.test(flatpakId)) return null;
    if (typeof desktopId !== 'string' || !DESKTOP_ID.test(desktopId) || desktopId.includes('..')) return null;
    const root = roots.find(({exports}) => exports && exportPath === `${exports}${desktopId}`);
    if (!root) return null;
    const files = `${root.deploy}/${flatpakId}/current/active/files`;
    const text = read(`${files}/share/applications/${desktopId}`);
    if (typeof text !== 'string' || text.length > 65536) return null;
    const keys = desktopTvKeys(text);
    return keys['X-Luma-TV'] === undefined ? null : {keys, files};
}

// The X-Luma-TV* keys of a desktop file's [Desktop Entry] group (unlocalised, first wins).
export function desktopTvKeys(text) {
    const keys = {};
    let entry = false;
    for (const raw of text.split('\n')) {
        const line = raw.trim();
        if (!line || line.startsWith('#')) continue;
        if (line.startsWith('[')) {
            entry = line === '[Desktop Entry]';
            continue;
        }
        const eq = line.indexOf('=');
        if (!entry || eq <= 0) continue;
        const key = line.slice(0, eq).trim();
        if (/^X-Luma-TV(-Tile|-Presentation)?$/.test(key) && !(key in keys)) keys[key] = line.slice(eq + 1).trim();
    }
    return keys;
}

// A Flatpak's wide tile: share/luma-tv/<tile>.svg in its deploy (the export carries no such folder).
export function flatpakTile(files, tile) {
    return files && /^[A-Za-z0-9._-]{1,255}$/.test(tile ?? '') && !tile.includes('..') ? `${files}/share/luma-tv/${tile}.svg` : null;
}

// The TV home's Apps page: opted-in apps by name, as the desktop's catalogue
// sorts them, with duplicates by ID removed.
export function tvApps(entries) {
    const seen = new Set();
    return entries.filter(entry => entry && !seen.has(entry.id) && seen.add(entry.id))
        .sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
}

// Search: opted-in apps whose name contains every word typed, in order of
// how early the first word matches.
export function searchTvApps(apps, query) {
    const words = query.toLocaleLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return [];
    return apps.map(app => ({app, name: app.name.toLocaleLowerCase()}))
        .filter(({name}) => words.every(word => name.includes(word)))
        .sort((a, b) => a.name.indexOf(words[0]) - b.name.indexOf(words[0]))
        .map(({app}) => app);
}

// The Apps grid: as many tile columns as fit the safe area with at least the
// minimum gap, spread edge to edge (CSS repeat(auto-fill) + space-between).
export function tileGrid(count, unit, screenWidth, tokens) {
    const {tile, layout} = tokens;
    const available = screenWidth - 2 * layout.safe_x * unit;
    const width = tile.width * unit;
    const height = width * tile.aspect_height / tile.aspect_width;
    const minimumGap = tile.column_gap * unit;
    const columns = Math.max(1, Math.floor((available + minimumGap) / (width + minimumGap)));
    const gap = columns > 1 ? (available - columns * width) / (columns - 1) : 0;
    const label = (tile.label_gap + tile.label_size * 1.3) * unit;
    const rowHeight = height + label;
    const cells = [];
    for (let index = 0; index < count; index++) {
        const column = index % columns, row = Math.floor(index / columns);
        cells.push({x: layout.safe_x * unit + column * (width + gap),
            y: row * (rowHeight + tile.row_gap * unit), width, height, column, row});
    }
    return {columns, gap, width, height, rowHeight, cells};
}

// Spatial focus: the nearest focusable in the pressed direction. `items` are
// {x, y, width, height}; `from` is one of them. Candidates must lie beyond the
// current item's edge in that direction; distance along the direction counts
// once and sideways offset twice, so a row is preferred to a diagonal jump.
export function nearest(items, from, direction) {
    const centre = r => [r.x + r.width / 2, r.y + r.height / 2];
    const [fx, fy] = centre(from);
    let best = null, bestScore = Infinity;
    for (const item of items) {
        if (item === from) continue;
        const [x, y] = centre(item);
        let along, across;
        if (direction === 'left') { along = from.x - (item.x + item.width); across = Math.abs(y - fy); }
        else if (direction === 'right') { along = item.x - (from.x + from.width); across = Math.abs(y - fy); }
        else if (direction === 'up') { along = from.y - (item.y + item.height); across = Math.abs(x - fx); }
        else if (direction === 'down') { along = item.y - (from.y + from.height); across = Math.abs(x - fx); }
        else return null;
        if (along < -1) continue;
        const score = Math.max(0, along) + 2 * across;
        if (score < bestScore) { best = item; bestScore = score; }
    }
    return best;
}

// The greeting the desktop login uses, by the hour.
export function greetingPart(hour) {
    return hour < 12 ? 'morning' : hour < 18 ? 'afternoon' : 'evening';
}

// The bar's weather glyph (Lucide, as the Prairie theme ships it) for the
// shared weather model's condition, and whether it is the sun or the moon
// (the glyph carries its own colour).
export function weatherGlyph(condition, hour) {
    const day = hour >= 6 && hour < 19;
    const text = (condition ?? '').toLowerCase();
    if (text.includes('thunder')) return {icon: 'lumaui-cloud-lightning-symbolic', tint: null};
    if (text.includes('snow') || text.includes('sleet')) return {icon: 'lumaui-snowflake-symbolic', tint: null};
    if (text.includes('rain')) return {icon: 'lumaui-cloud-rain-symbolic', tint: null};
    if (text.includes('fog')) return {icon: 'lumaui-cloud-fog-symbolic', tint: null};
    if (text.startsWith('cloudy')) return {icon: 'lumaui-cloud-symbolic', tint: null};
    if (text.includes('partly') || text.includes('fair'))
        return {icon: day ? 'lumaui-cloud-sun-symbolic' : 'lumaui-cloud-moon-symbolic', tint: day ? 'sun' : 'moon'};
    return {icon: day ? 'lumaui-sun-symbolic' : 'lumaui-moon-symbolic', tint: day ? 'sun' : 'moon'};
}

// The TV's keys, by keysym name, onto its actions. Remotes and gamepads
// arrive as these keys (tvInput.js turns XF86Back, XF86OK, CEC keys and pad
// buttons into them, and opens the TV home for Home), so a keyboard's are
// the only names here.
// A held key repeats. Only moving the focus (and stepping a slider) may follow a repeat;
// OK and Back act once per press, so holding a key never flips a switch, opens a menu twice
// or confirms what the press opened.
export function repeatAllowed(action) {
    return action === 'up' || action === 'down' || action === 'left' || action === 'right';
}

export function remoteAction(name) {
    switch (name) {
    case 'Up': case 'Down': case 'Left': case 'Right':
        return name.toLowerCase();
    case 'Return': case 'KP_Enter': case 'ISO_Enter': case 'space':
        return 'ok';
    case 'Escape': case 'BackSpace':
        return 'back';
    default:
        return null;
    }
}

// The colour at an icon's edge, for an app with no wide TV tile: its icon
// fills the tile's height on this colour, so the tile reads as one banner.
// Walking in from both ends of every row and column to the first opaque pixel
// (alpha >= 200), it averages the two pixels just inside the outline. Returns
// [r, g, b], or null when the icon has no opaque edge (an empty image).
export function edgeColour(pixels, width, height, channels, stride, hasAlpha) {
    const opaque = (x, y) => !hasAlpha || pixels[y * stride + x * channels + 3] >= 200;
    let r = 0, g = 0, b = 0, count = 0;
    const scan = (length, across, at) => {
        for (let line = 0; line < across; line++) {
            for (const forward of [true, false]) {
                for (let step = 0; step < length; step++) {
                    const pos = forward ? step : length - 1 - step;
                    if (!opaque(...at(pos, line))) continue;
                    for (const inward of [1, 2]) {
                        const inner = forward ? pos + inward : pos - inward;
                        if (inner < 0 || inner >= length) continue;
                        const [x, y] = at(inner, line);
                        if (!opaque(x, y)) continue;
                        const i = y * stride + x * channels;
                        r += pixels[i]; g += pixels[i + 1]; b += pixels[i + 2]; count++;
                    }
                    break;
                }
            }
        }
    };
    scan(width, height, (pos, line) => [pos, line]);
    scan(height, width, (pos, line) => [line, pos]);
    return count ? [Math.round(r / count), Math.round(g / count), Math.round(b / count)] : null;
}
