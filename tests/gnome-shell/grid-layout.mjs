import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {gridLayout, gridNeighbor, gridCardHeight, GRID_GAP, GRID_LABEL, GRID_MAX_WIDTH_SHARE} =
    await import(pathToFileURL(process.argv[2]));
const near = (a, b, e = 1e-6) => Math.abs(a - b) < e;
let checked = 0;
for (const n of [1, 2, 5, 6, 7, 12, 30]) {
    for (const [width, height] of [[1168, 556], [1623, 1026], [3328, 1300], [5008, 1302], [360, 640], [1328, 2400]]) {
        const windows = Array.from({length: n}, (_, i) => ({width: 300 + (i * 137) % 1900, height: 200 + (i * 79) % 1100}));
        windows.push({width: 5000, height: 600}, {width: 360, height: 1300});
        const out = gridLayout(windows, width, height);
        assert.equal(out.rows.length, 1);
        const H = gridCardHeight(width, height);
        // The card height depends on the display alone.
        assert.ok(near(out.cardHeight, H));
        const first = out.items[0], last = out.items.at(-1);
        // The row is centred, so both ends keep the same margin.
        assert.ok(near(first.x, width - last.x - last.width, 1e-4));
        out.items.forEach((v, i) => {
            const w = windows[i];
            // One height and one title line for every card.
            assert.ok(near(v.height, H));
            assert.ok(near(v.labelY, first.labelY));
            assert.ok(near(v.y - v.labelY, GRID_LABEL));
            // The preview keeps the window's shape, fits its card, and is never enlarged.
            assert.ok(near(v.previewWidth / v.previewHeight, w.width / w.height, 1e-6));
            assert.ok(v.previewWidth <= v.width + 1e-6 && v.previewHeight <= v.height + 1e-6);
            assert.ok(v.previewHeight <= w.height + 1e-6);
            // A card is its window's shape unless the width cap applies.
            const capped = H * w.width / w.height > Math.max(H, width * GRID_MAX_WIDTH_SHARE);
            if (!capped)
                assert.ok(near(v.width / v.height, w.width / w.height, 1e-6));
            else
                assert.ok(near(v.width, Math.max(H, width * GRID_MAX_WIDTH_SHARE)));
            // A window at least as tall as the row fills its card exactly unless capped.
            if (!capped && w.height >= H)
                assert.ok(!v.letterboxed);
            if (i > 0)
                assert.ok(near(v.x - out.items[i - 1].x - out.items[i - 1].width, GRID_GAP, 1e-4));
        });
        assert.equal(gridNeighbor(out, 0, 'Left'), out.items.length - 1);
        checked++;
    }
}
console.log(`Grid geometry: ${checked} viewport/count matrices PASS`);
