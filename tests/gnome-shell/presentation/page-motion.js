// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import {pageProgress, visiblePageRange} from '../../../src/luma-shell-presentation/layout.js';
for (const width of [360, 420, 1280]) for (const rtl of [false, true]) {
    for (const progress of [0, .2, .75, 1, 1.6, 3]) {
        const x = (rtl ? progress - 3 : -progress) * width;
        assert.ok(Math.abs(pageProgress(x, width, 4, rtl) - progress) < 1e-10,
            'a new gesture continues from the painted fractional position, including RTL');
    }
}
assert.deepEqual(visiblePageRange(1.4, 8), [1, 2]);
assert.deepEqual(visiblePageRange(3, 8), [3, 3]);
assert.deepEqual(visiblePageRange(-.2, 8), [0, 0]);
assert.deepEqual(visiblePageRange(8.2, 8), [7, 7]);
assert.equal(pageProgress(-360, 0, 8), 0);
assert.equal(pageProgress(25, 360, 1), 0);
console.log('PASS interrupted Home motion, RTL, resize and bounded visible pages');
