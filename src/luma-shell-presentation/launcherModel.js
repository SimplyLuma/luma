// SPDX-License-Identifier: GPL-2.0-or-later
// Desktop drawer geometry; no installed-app or file-system state lives here.
export const COLUMNS = 7;
export const ROWS = 4;
export const PAGE_SIZE = COLUMNS * ROWS;
export function catalogPage(items, query = '', page = 0) {
    const needle = query.trim().toLocaleLowerCase();
    const matches = needle ? items.filter(item => item.name.toLocaleLowerCase().includes(needle)) : items;
    const pages = Math.max(1, Math.ceil(matches.length / PAGE_SIZE));
    const selected = Math.max(0, Math.min(page, pages - 1));
    return {items: needle ? matches : matches.slice(selected * PAGE_SIZE, (selected + 1) * PAGE_SIZE),
        matches, pages, page: needle ? 0 : selected, searching: Boolean(needle)};
}
export function appCells(count, width, height, searching = false) {
    const rows = Math.ceil(count / COLUMNS);
    const top = searching ? Math.max(0, (height - rows * 122 + 6) / 2) : 0;
    const cell = width / COLUMNS;
    const inset = searching && count <= COLUMNS ? (width - count * cell) / 2 : 0;
    return Array.from({length: count}, (_, i) => ({x: inset + i % COLUMNS * cell,
        y: top + Math.floor(i / COLUMNS) * 122, width: cell, height: 116}));
}
export function folderWindow(value, width, count, stride = 116) {
    return {start: Math.max(0, Math.floor(value / stride) - 2),
        end: Math.min(count, Math.ceil((value + width) / stride) + 2)};
}
