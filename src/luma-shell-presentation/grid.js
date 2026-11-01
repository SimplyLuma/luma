// SPDX-License-Identifier: Apache-2.0
// Presentation-independent occupancy and transactions, in logical grid cells.
export const MAX_PAGES = 64;

export function overlaps(a, b) {
    return a.x < b.x + b.w && b.x < a.x + a.w &&
        a.y < b.y + b.h && b.y < a.y + a.h;
}

export function fits(grid, page, item) {
    return item.x >= 0 && item.y >= 0 && item.x + item.w <= grid.columns &&
        item.y + item.h <= grid.rows && !page.some(other => overlaps(other, item));
}

export function firstFree(grid, page, item) {
    for (let y = 0; y <= grid.rows - item.h; y++) {
        for (let x = 0; x <= grid.columns - item.w; x++) {
            const placed = {...item, x, y};
            if (fits(grid, page, placed)) return placed;
        }
    }
    return null;
}

function append(grid, item, startPage = grid.pages.length - 1) {
    for (let index = Math.max(0, startPage); index < MAX_PAGES; index++) {
        if (!grid.pages[index]) grid.pages[index] = [];
        const placed = firstFree(grid, grid.pages[index], item);
        if (placed) {
            grid.pages[index].push(placed);
            return index;
        }
    }
    throw new Error('Home has reached its page limit');
}

export function copyGrid(grid) {
    return {columns: grid.columns, rows: grid.rows,
        pages: grid.pages.map(page => page.map(item => ({...item})))};
}

export function trimGrid(grid) {
    while (grid.pages.length > 1 && !grid.pages.at(-1).length) grid.pages.pop();
    return grid;
}

// Definitions come from the actual app catalog and registered widget hosts.
// Missing apps are excluded; new installs append without moving existing items.
export function reconcileGrid(saved, definitions, columns, rows) {
    const grid = {columns, rows, pages: [[]]};
    const available = new Map(definitions.map(item => [item.id, item]));
    const seen = new Set();
    const sameSize = saved?.columns === columns && saved?.rows === rows;
    for (const [pageIndex, page] of (saved?.pages ?? []).entries()) {
        if (sameSize && !grid.pages[pageIndex]) grid.pages[pageIndex] = [];
        for (const original of page) {
            const definition = available.get(original.id);
            if (!definition || seen.has(original.id)) continue;
            const item = definedItem(definition, columns, rows, original);
            seen.add(item.id);
            if (sameSize && fits(grid, grid.pages[pageIndex], item))
                grid.pages[pageIndex].push(item);
            else
                append(grid, item, sameSize ? pageIndex : 0);
        }
    }
    for (const definition of definitions) {
        if (!seen.has(definition.id)) {
            append(grid, definedItem(definition, columns, rows));
            seen.add(definition.id);
        }
    }
    return trimGrid(grid);
}

function definedItem(definition, columns, rows, saved = null) {
    const widget = definition.kind === 'widget';
    const span = (key, bound, minimum, maximum) => {
        const low = Math.min(bound, minimum ?? definition[key]);
        const high = Math.max(low, Math.min(bound, maximum ?? bound));
        const requested = saved?.kind === definition.kind && Number.isInteger(saved[key])
            ? saved[key] : definition[key];
        return widget ? Math.max(low, Math.min(high, requested)) : 1;
    };
    // Provider constraints are not persisted as item fields. The state service
    // accepts only the public placement contract, and app icons stay one cell.
    return {id: definition.id, kind: definition.kind, x: saved?.x ?? 0, y: saved?.y ?? 0,
        w: span('w', columns, definition.minW, definition.maxW),
        h: span('h', rows, definition.minH, definition.maxH)};
}

// A little hysteresis keeps a thumb resting near a cell boundary from
// repeatedly reordering its neighbours. Motion remains reversible.
export function resizeCell(start, delta, cell, minimum, maximum, previous = start) {
    const value = start + delta / Math.max(1, cell);
    return Math.max(minimum, Math.min(maximum,
        Math.abs(value - previous) < .6 ? previous : Math.round(value)));
}

function nearestFree(grid, page, item) {
    let nearest = null, distance = Infinity;
    for (let y = 0; y <= grid.rows - item.h; y++) {
        for (let x = 0; x <= grid.columns - item.w; x++) {
            const candidate = {...item, x, y};
            const nextDistance = Math.abs(x - item.x) + Math.abs(y - item.y);
            if (nextDistance < distance && fits(grid, page, candidate)) {
                nearest = candidate;
                distance = nextDistance;
            }
        }
    }
    return nearest;
}

// Each preview starts from the original snapshot, not the preceding preview.
// Prefer nearby cells. A large displaced widget may push a few adjacent icons
// aside to stay on this page; do not eject it just because icons fragment the
// remaining space. Whole widgets are never shrunk or discarded.
// An impossible transaction throws without changing the caller's saved layout.
export function resizeItem(original, id, width, height, limits = {}) {
    const current = findItem(original, id);
    if (!current || current.kind !== 'widget') throw new Error('Only widgets can be resized');
    const minW = Math.min(original.columns, limits.minW ?? 1);
    const minH = Math.min(original.rows, limits.minH ?? 1);
    const maxW = Math.min(original.columns - current.x, limits.maxW ?? original.columns);
    const maxH = Math.min(original.rows - current.y, limits.maxH ?? original.rows);
    if (![width, height].every(Number.isInteger) ||
        width < minW || height < minH || width > maxW || height > maxH)
        throw new Error('Widget size does not fit here');
    const grid = copyGrid(original);
    const placed = {id: current.id, kind: current.kind, x: current.x, y: current.y,
        w: width, h: height};
    const page = grid.pages[current.page].filter(item => item.id !== id);
    const displaced = page.filter(item => overlaps(item, placed));
    grid.pages[current.page] = page.filter(item => !overlaps(item, placed));
    grid.pages[current.page].push(placed);
    // Place larger footprints before icons, retaining stable order for ties.
    displaced.sort((a, b) => b.w * b.h - a.w * a.h);
    for (const item of displaced) {
        const nearby = nearestFree(grid, grid.pages[current.page], item);
        if (nearby) grid.pages[current.page].push(nearby);
        else if (item.kind === 'widget') {
            const widgets = grid.pages[current.page].filter(other => other.kind === 'widget');
            const slot = nearestFree(grid, widgets, item);
            if (slot) {
                const icons = grid.pages[current.page].filter(other => overlaps(other, slot));
                grid.pages[current.page] = grid.pages[current.page].filter(other => !overlaps(other, slot));
                grid.pages[current.page].push(slot);
                displaced.push(...icons);
            } else append(grid, item, 0);
        } else append(grid, item, 0);
    }
    return trimGrid(grid);
}

// A move is atomic. Colliding items are displaced into free cells, preserving
// their identities and whole-cell spans. The caller commits only after drop.
export function moveItem(original, id, targetPage, x, y) {
    if (!Number.isInteger(targetPage) || targetPage < 0 || targetPage >= MAX_PAGES)
        throw new Error('Invalid Home page');
    const grid = copyGrid(original);
    const origin = findItem(grid, id);
    const item = origin && grid.pages[origin.page].find(record => record.id === id);
    if (!item) throw new Error('Home item no longer exists');
    if (!Number.isInteger(x) || !Number.isInteger(y) || x < 0 || y < 0 ||
        x + item.w > grid.columns || y + item.h > grid.rows)
        throw new Error('Home item does not fit here');
    grid.pages = grid.pages.map(page => page.filter(record => record.id !== id));
    while (grid.pages.length <= targetPage) grid.pages.push([]);
    const placed = {...item, x, y};
    const displaced = grid.pages[targetPage].filter(other => overlaps(other, placed));
    grid.pages[targetPage] = grid.pages[targetPage].filter(other => !overlaps(other, placed));
    grid.pages[targetPage].push(placed);
    for (const other of displaced) {
        const swapped = {...other, x: origin.x, y: origin.y};
        if (fits(grid, grid.pages[origin.page], swapped))
            grid.pages[origin.page].push(swapped);
        else
            append(grid, other, 0);
    }
    return trimGrid(grid);
}

export function findItem(grid, id) {
    for (const [page, items] of grid.pages.entries()) {
        const item = items.find(record => record.id === id);
        if (item) return {page, ...item};
    }
    return null;
}
