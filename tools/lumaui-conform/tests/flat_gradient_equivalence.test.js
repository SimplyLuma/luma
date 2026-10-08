const assert = require('node:assert/strict');
const { isFlatCssGradient, isFlatGtkGradient, equivalentFlatGradient } = require('../lib/flat-gradient');

// These are the computed backgrounds captured from v70 Calculator, rather
// than simplified CSS authored for the test.
assert.equal(isFlatCssGradient('linear-gradient(rgba(255, 255, 255, 0.05), rgba(255, 255, 255, 0.05)), none'), true);
assert.equal(isFlatCssGradient('linear-gradient(rgba(110, 160, 232, 0.2), rgba(110, 160, 232, 0.2)), none'), true);
assert.equal(isFlatCssGradient('linear-gradient(rgba(0, 0, 0, 0), rgba(0, 0, 0, 0))'), true);
assert.equal(isFlatCssGradient('linear-gradient(rgb(231, 232, 234), rgb(206, 207, 210))'), false);
assert.equal(isFlatCssGradient('linear-gradient(rgb(1, 2, 3), rgb(1, 2, 3)), url(a.png)'), false);
assert.equal(isFlatGtkGradient({ stops: [[0, [80, 82, 86, 1]], [1, [80, 82, 86, 1]]] }), true);
assert.equal(isFlatGtkGradient({ stops: [[0, [80, 82, 86, 1]], [1, [90, 82, 86, 1]]] }), false);
const equalStops = 'linear-gradient(rgba(255, 255, 255, 0.05), rgba(255, 255, 255, 0.05)), none';
const ramp = 'linear-gradient(rgb(231, 232, 234), rgb(206, 207, 210))';
const distance = (a, b) => Math.abs(a.r - b.r);
assert.equal(equivalentFlatGradient(equalStops, null, { r: 80 }, { r: 81 }, distance, 3), true);
assert.equal(equivalentFlatGradient(equalStops, null, { r: 80 }, { r: 90 }, distance, 3), false);
assert.equal(equivalentFlatGradient(ramp, null, { r: 80 }, { r: 80 }, distance, 3), false);
assert.equal(equivalentFlatGradient('none', { stops: [[0, [80, 82, 86, 1]], [1, [80, 82, 86, 1]]] }, { r: 80 }, { r: 80 }, distance, 3), true);
console.log('flat gradient equivalence: 11 cases pass');
