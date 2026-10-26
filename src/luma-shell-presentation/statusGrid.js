// SPDX-License-Identifier: Apache-2.0
// Geometry shared by the status island's native layout and its regression tests.
export function statusGrid(count, maxColumns = 4) {
    count = Math.max(0, Math.floor(Number(count) || 0));
    const overflow = count > 8 ? count - 7 : 0;
    const visible = Math.min(count, 8);
    const preferredRows = visible <= 3 ? 1 : 2;
    const columns = Math.max(1, Math.min(maxColumns, Math.ceil(visible / preferredRows)));
    const rows = Math.max(1, Math.ceil(visible / columns));
    const base = Math.floor(visible / rows), extra = visible % rows;
    const lengths = Array.from({length: rows}, (_, row) => base + (row < extra ? 1 : 0));
    const cells = [];
    lengths.forEach((length, row) => {
        const inset = (columns - length) / 2;
        for (let column = 0; column < length; column++)
            cells.push({row, column: inset + column, more: overflow > 0 && cells.length === 7});
    });
    return {columns, rows, cells, overflow};
}
