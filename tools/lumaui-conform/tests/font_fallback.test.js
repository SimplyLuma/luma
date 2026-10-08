const assert = require('node:assert/strict');
const { fontFallback } = require('../lib/font-fallback');

// Actual CSS.getPlatformFontsForNode results from the dark Calculator v70
// scientific state captured by conform on 2026-09-26.
const latin = { familyName: 'Figtree Light', postScriptName: 'Figtree-Light', isCustomFont: true, glyphCount: 1 };
const liberation = { familyName: 'Liberation Sans', postScriptName: 'LiberationSans', isCustomFont: false, glyphCount: 1 };
const math = { familyName: 'Noto Sans Math', postScriptName: 'NotoSansMath-Regular', isCustomFont: false, glyphCount: 1 };
assert.equal(fontFallback('Figtree', 500, 'Cantarell', 500, [liberation]).familyMatch, false); // π
assert.equal(fontFallback('Figtree', 500, 'Cantarell', 500, [liberation, latin]).familyMatch, false); // √x
assert.equal(fontFallback('Figtree', 500, 'Noto Sans Math', 400, [math, latin]).familyMatch, true); // ∛x
assert.equal(fontFallback('Figtree', 500, 'Noto Sans Math', 400, [math, latin]).fallbackRegularWeight, true);
assert.equal(fontFallback('Figtree', 500, 'Liberation Sans', 400, [liberation, latin]).fallbackRegularWeight, true);
assert.equal(fontFallback('Figtree', 500, 'Liberation Sans', 400, [{ ...liberation, isCustomFont: true }]).fallbackRegularWeight, false);
assert.equal(fontFallback('Figtree', 500, 'Cantarell', 400, [liberation]).fallbackRegularWeight, false);
assert.equal(fontFallback('Figtree', 500, 'Noto Sans Math', 500, [math, latin]).fallbackRegularWeight, false);
assert.equal(fontFallback('Figtree', 500, 'Cantarell', 400, [math, latin]).fallbackRegularWeight, false);
assert.equal(fontFallback('Figtree', 500, 'Noto Sans Math', 400, []).familyMatch, false);
assert.equal(fontFallback('Figtree', 500, 'Figtree', 400, []).fallbackRegularWeight, false);
console.log('probed font fallback: 11 cases pass');
