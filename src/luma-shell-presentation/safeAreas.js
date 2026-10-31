// SPDX-License-Identifier: Apache-2.0
// Display exclusions are device metadata in native pixels, never a mode heuristic.
function validDisplay(value) {
    if (!value || typeof value !== 'object' || !Number.isFinite(value.nativeWidth) ||
        !Number.isFinite(value.nativeHeight) || value.nativeWidth <= 0 || value.nativeHeight <= 0 ||
        !Array.isArray(value.cutouts) || value.cutouts.length > 16 ||
        (value.connector !== undefined && (typeof value.connector !== 'string' || !value.connector)))
        return false;
    return value.cutouts.every(r => ['x', 'y', 'width', 'height'].every(k => Number.isFinite(r[k])) &&
        r.x >= 0 && r.y >= 0 && r.width > 0 && r.height > 0 &&
        r.x + r.width <= value.nativeWidth && r.y + r.height <= value.nativeHeight);
}

// Schema 1: one display profile (its connector is optional).
export function readDisplayGeometry(text) {
    try {
        const value = JSON.parse(text);
        return value.schema === 1 && validDisplay(value) ? value : null;
    } catch { return null; }
}

// Every display profile in a file. Schema 2 lists several displays (a
// foldable's inner and outer panels), each naming its connector; schema 1
// files are read as a one-entry list.
export function readDisplayProfiles(text) {
    try {
        const value = JSON.parse(text);
        if (value.schema === 1)
            return validDisplay(value) ? [value] : null;
        if (value.schema !== 2 || !Array.isArray(value.displays) ||
            value.displays.length === 0 || value.displays.length > 8)
            return null;
        const connectors = value.displays.map(display => display?.connector);
        if (!value.displays.every(display => validDisplay(display) && typeof display.connector === 'string') ||
            new Set(connectors).size !== connectors.length)
            return null;
        return value.displays;
    } catch { return null; }
}

// The profile for the current primary display, or null. Nothing is
// discarded: the next monitor change selects again from all profiles.
export function selectDisplayProfile(profiles, connector, builtIn, transform = 0) {
    if (!profiles || !builtIn)
        return null;
    const profile = profiles.find(entry => entry.connector && entry.connector === connector) ??
        profiles.find(entry => !entry.connector);
    return profile ? {...profile, transform} : null;
}

export function displayExclusions(profile, monitor) {
    if (!profile || (profile.connector && monitor.connector && profile.connector !== monitor.connector)) return [];
    const transform = profile.transform ?? 0;
    const rotation = transform % 4;
    const nw = profile.nativeWidth, nh = profile.nativeHeight;
    return profile.cutouts.map(rect => {
        let {x, y, width: w, height: h} = rect;
        if (transform >= 4) x = nw - x - w;
        let rw = nw, rh = nh;
        if (rotation === 1) { [x, y, w, h] = [nh - y - h, x, h, w]; [rw, rh] = [nh, nw]; }
        else if (rotation === 2) [x, y] = [nw - x - w, nh - y - h];
        else if (rotation === 3) { [x, y, w, h] = [y, nw - x - w, h, w]; [rw, rh] = [nh, nw]; }
        return {x: x * monitor.width / rw, y: y * monitor.height / rh,
            width: w * monitor.width / rw, height: h * monitor.height / rh};
    });
}

export function statusInsets(profile, monitor, height, edge = 12, gap = 8) {
    let left = edge, right = edge;
    for (const r of displayExclusions(profile, monitor)) {
        if (r.y >= height) continue;
        if (r.x < monitor.width / 3) left = Math.max(left, r.x + r.width + gap);
        if (r.x + r.width > monitor.width * 2 / 3) right = Math.max(right, monitor.width - r.x + gap);
    }
    return {left, right};
}

export function avoidDisplayExclusions(profile, monitor, box, gap = 8) {
    const obstacles = displayExclusions(profile, monitor).filter(r => r.y < box.y + box.height && r.y + r.height > box.y);
    let spans = [[0, monitor.width]];
    for (const r of obstacles) {
        spans = spans.flatMap(([start, end]) => r.x - gap >= end || r.x + r.width + gap <= start ? [[start, end]] :
            [[start, Math.max(start, r.x - gap)], [Math.min(end, r.x + r.width + gap), end]]);
    }
    const fits = spans.filter(([start, end]) => end - start >= box.width)
        .map(([start, end]) => Math.max(start, Math.min(box.x, end - box.width)))
        .sort((a, b) => Math.abs(a - box.x) - Math.abs(b - box.x));
    return fits.length ? {...box, x: fits[0]} : {...box,
        y: Math.max(box.y, ...obstacles.map(r => r.y + r.height + gap))};
}

// Insets for content inside a surface whose edge/center anchor stays fixed.
export function surfaceInsets(profile, monitor, box, edge = 16, gap = 8) {
    const insets = {left: edge, right: edge, top: edge};
    for (const r of displayExclusions(profile, monitor)) {
        if (r.x >= box.x + box.width || r.x + r.width <= box.x ||
            r.y >= box.y + box.height || r.y + r.height <= box.y) continue;
        if (r.x < monitor.width / 3)
            insets.left = Math.max(insets.left, r.x + r.width + gap - box.x);
        else if (r.x + r.width > monitor.width * 2 / 3)
            insets.right = Math.max(insets.right, box.x + box.width - r.x + gap);
        else insets.top = Math.max(insets.top, r.y + r.height + gap - box.y);
    }
    return insets;
}
