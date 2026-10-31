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
            const item = {...original, kind: definition.kind,
                w: Math.min(columns, definition.w), h: Math.min(rows, definition.h)};
            seen.add(item.id);
            if (sameSize && fits(grid, grid.pages[pageIndex], item))
                grid.pages[pageIndex].push(item);
            else
                append(grid, item, sameSize ? pageIndex : 0);
        }
    }
    for (const definition of definitions) {
        if (!seen.has(definition.id)) {
            append(grid, {...definition, w: Math.min(columns, definition.w),
                h: Math.min(rows, definition.h)});
            seen.add(definition.id);
        }
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
