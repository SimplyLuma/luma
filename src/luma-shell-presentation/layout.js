// SPDX-License-Identifier: Apache-2.0
// Logical geometry adapts presentation; it never chooses the presentation mode.
export function formOf(width, height) {
    const short = Math.min(width, height);
    const long = Math.max(width, height);
    if (long < 600)
        return 'pocket';
    if (short >= 760 && long >= 1024)
        return 'tablet';
    return short >= 560 ? 'fold' : 'phone';
}

export function homeLayout(width, height, mode, count, metrics = {}) {
    // Widgets and apps share the same whole icon-cell grid.
    // Density depends on usable geometry, independently of device or mode.
    if (width <= 480 && height >= 280) {
        const inset = 8;
        const top = 44;
        const dockHeight = 56;
        const dockBottom = 6;
        const dotsHeight = 16;
        const bottom = dockHeight + dockBottom + dotsHeight + 4;
        const availableHeight = Math.max(1, height - top - bottom);
        const minimumRow = Math.max(56, metrics.cellHeight ?? 0);
        const rows = Math.max(2, Math.min(32, Math.floor(availableHeight / minimumRow)));
        const rowHeight = availableHeight / rows;
        const iconSize = Math.max(28, Math.min(40, rowHeight - 24, (width - 2 * inset) / 5 - 12));
        const iconColumns = Math.max(3, Math.min(5, Math.floor((width - 2 * inset) / 56)));
        const columns = iconColumns;
        const capacity = rows * iconColumns;
        return {width, height, form: formOf(width, height), compact: true, inset,
            contentWidth: width - 2 * inset, columns, iconColumns, appColumns: 1,
            widgetColumns: Math.floor(iconColumns / 2), top, bottom, dockHeight, dockBottom,
            dotsHeight, iconSize, cellHeight: rowHeight - 4, rowHeight,
            firstRows: rows, otherRows: rows, firstCapacity: capacity,
            laterCapacity: capacity, pages: Math.max(1, Math.ceil(count / capacity))};
    }
    const tablet = mode === 'tablet';
    const inset = tablet ? Math.min(56, width / 16) : 20;
    const contentWidth = Math.max(1, width - 2 * inset);
    const cellWidth = Math.max(tablet ? 112 : 84, metrics.minimumCellWidth ?? 0);
    const columns = Math.max(1, Math.floor(contentWidth / cellWidth));
    const top = height < 440 ? 44 : 64;
    const bottom = 156;
    const cellHeight = Math.max(100, metrics.cellHeight ?? 0);
    const rowHeight = cellHeight + 8;
    const firstRows = Math.max(0, Math.floor((height - top - bottom + 8) / rowHeight));
    const otherRows = Math.max(1, Math.floor((height - top - bottom + 8) / rowHeight));
    const firstCapacity = firstRows * columns;
    const laterCapacity = otherRows * columns;
    const pages = Math.max(1, 1 + Math.ceil(Math.max(0, count - firstCapacity) / laterCapacity));
    return {width, height, form: formOf(width, height), inset, contentWidth, columns, top, bottom, cellHeight, rowHeight, firstRows, otherRows,
        firstCapacity, laterCapacity, pages};
}

export function pageRange(layout, page, count) {
    const start = page === 0 ? 0 : layout.firstCapacity + (page - 1) * layout.laterCapacity;
    const capacity = page === 0 ? layout.firstCapacity : layout.laterCapacity;
    return [start, Math.min(count, start + capacity)];
}
