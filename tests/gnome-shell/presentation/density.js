// SPDX-License-Identifier: Apache-2.0
// Home density from the panel's physical size; runs under gjs -m or node.
import {displayDensity, homeLayout, inkFor} from '../../../src/luma-shell-presentation/layout.js';
function assert(value, message) { if (!value) throw Error(message); }

const SCALES = [1, 1.25, 1.3333333, 1.5, 1.75, 2, 2.25, 2.5, 2.6666667, 3, 4];
// DisplayConfig replies as deep_unpack() leaves them: one built-in panel.
function replies(nativeWidth, nativeHeight, widthMm, heightMm, scale, supported = SCALES) {
    const id = ['DSI-1', 'unknown', 'unknown', 'unknown'];
    const state = [1, [[id, [['other', nativeWidth, nativeHeight, 120, 9, supported, {}],
        ['current', nativeWidth, nativeHeight, 60, scale, supported, {'is-current': true}]], {}]],
    [[0, 0, scale, 0, true, [id], {}]], {}];
    const resources = [1, [], [[0, 0, 0, [], 'DSI-1', [], [], {'width-mm': widthMm, 'height-mm': heightMm}]], [], 0, 0];
    return [state, resources];
}
function device(nativeWidth, nativeHeight, widthMm, heightMm, scale, mode = 'mobile', preferences = {}) {
    const density = displayDensity(...replies(nativeWidth, nativeHeight, widthMm, heightMm, scale), 0, 0);
    const width = Math.round(nativeWidth / scale), height = Math.round(nativeHeight / scale);
    const layout = homeLayout(width, height, mode, 40, {}, preferences, density);
    return {density, layout, iconMm: density ? layout.iconSize * density.mmPerPx : null};
}

// The panel's size, not the reported preferred scale (Mutter echoes the last applied one).
const pixel = device(1080, 2424, 65, 146, 3);
assert(Math.abs(pixel.density.mmPerPx - 0.181) < 0.002 && pixel.density.zoom === 1, 'Pixel 9 density at its default scale');
assert(pixel.layout.columns === 4 && pixel.layout.otherRows === 7, 'a 6.3 inch phone gets four columns of seven rows');
assert(pixel.iconMm > 9.5 && pixel.iconMm < 10.5 && pixel.layout.labelSize === 11, 'Pixel 9 icons are 10 mm with 11 px labels');
assert(pixel.layout.dockHeight >= pixel.layout.iconSize + 16, 'the dock reserve fits buttons sized to Home icons');

const zoomed = device(1080, 2424, 65, 146, 4);
assert(Math.abs(zoomed.density.zoom - 4 / 3) < 0.01, '400% is a zoom over the panel default');
assert(zoomed.layout.columns === 3 && zoomed.iconMm > pixel.iconMm * 1.25, 'zoom gives fewer, larger icons');
assert(zoomed.layout.width === 270 && zoomed.layout.compact, 'below 360 logical px Home stays compact');

const titan = device(1080, 1200, 69, 76, 3);
assert(titan.layout.columns === 5 && titan.layout.otherRows === 4 && titan.layout.iconSize === 44 &&
    titan.layout.labelSize === 10, 'a short square keyboard phone keeps its dense five by four grid');
const ikko = device(1080, 1240, 67, 77, 3);
assert(ikko.layout.columns === 5 && ikko.layout.otherRows >= 4, 'square phones fit five columns');
const compact = device(1080, 2340, 57, 124, 4);
assert(compact.layout.columns === 4, 'a 5.4 inch phone still gets four columns');
const foldOuter = device(1080, 2424, 65, 146, 2.6666667);
assert(foldOuter.layout.columns === 4, 'the same panel at a lower scale keeps the same columns');
const foldInner = device(2076, 2152, 141, 146, 2.6666667, 'tablet');
assert(!foldInner.layout.compact && foldInner.iconMm > 10 && foldInner.iconMm < 12, 'an inner fold screen keeps 10-12 mm icons');
const surface = device(2880, 1920, 274, 183, 2, 'tablet');
assert(surface.layout.iconSize === 60 && surface.layout.columns ===
    homeLayout(1440, 960, 'tablet', 40, {}, {}).columns, 'a tablet at its default scale keeps its layout');

const override = device(1080, 2424, 65, 146, 3, 'mobile', {columns: 5, rows: 8});
assert(override.layout.columns === 5 && override.layout.otherRows === 8, 'Settings rows and columns override');
assert(device(1080, 2424, 65, 146, 3, 'mobile', {iconSize: 'small'}).layout.iconSize === 32, 'Settings icon size overrides');

// Implausible reports keep the logical-pixel rules.
assert(displayDensity(...replies(1080, 1200, 286, 318, 3), 0, 0) === null, '96 dpi placeholder sizes are ignored');
assert(displayDensity(...replies(1920, 1080, 16, 9, 1), 0, 0) === null, 'aspect-ratio EDIDs are ignored');
assert(displayDensity(...replies(1080, 2424, 0, 0, 3), 0, 0) === null, 'panels without a size are ignored');
assert(displayDensity(...replies(1080, 2424, 65, 146, 3), 10, 0) === null, 'only the primary monitor is used');
assert(JSON.stringify(homeLayout(360, 808, 'mobile', 40, {}, {}, null)) ===
    JSON.stringify(homeLayout(360, 808, 'mobile', 40, {}, {})), 'no density, unchanged layout');

for (const [width, height, wmm, hmm, scale, mode] of [[1080, 2424, 65, 146, 3], [1080, 2424, 65, 146, 4],
    [1080, 1200, 69, 76, 3], [1080, 1240, 67, 77, 3], [1080, 2340, 57, 124, 3], [2880, 1920, 274, 183, 2, 'tablet']]) {
    const {layout} = device(width, height, wmm, hmm, scale, mode);
    assert(layout.top + layout.otherRows * layout.rowHeight <= layout.height - layout.bottom + 8, 'grid overlaps dock');
    assert(layout.iconSize <= layout.contentWidth / layout.columns, 'icon wider than its cell');
}
console.log('PASS physical density: phones, square phones, folds, tablets, zoom, overrides, implausible sizes');

const white = {red: 255, green: 255, blue: 255}, paper = {red: 241, green: 243, blue: 244};
const black = {red: 0, green: 0, blue: 0}, grey = {red: 120, green: 120, blue: 120};
assert(inkFor([white, paper, paper, white]) === 'dark', 'light content gets dark ink');
assert(inkFor([black, black, {red: 32, green: 33, blue: 36}]) === 'light', 'dark content gets light ink');
assert(inkFor([grey]) === 'light' && inkFor([{red: 140, green: 140, blue: 140}]) === 'dark', 'mid grey splits at equal contrast');
assert(inkFor([black, white, white]) === 'dark' && inkFor([black, black, white]) === 'light', 'the median sample decides');
assert(inkFor([]) === null, 'no samples, no opinion');
console.log('PASS status ink: light, dark, mid-grey threshold, mixed samples');
