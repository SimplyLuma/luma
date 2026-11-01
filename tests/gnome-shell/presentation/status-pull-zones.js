// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import {statusPullZone} from '../../../src/luma-shell-presentation/layout.js';
for (const width of [360, 390, 540, 700]) {
    const status = {x: 50, y: 20, width, height: 44};
    const clock = {x: 64, y: 20, width: 72, height: 44};
    const island = {x: 50 + width / 2 - 22, y: 20, width: 44, height: 5};
    const right = {x: 50 + width - 90, y: 20, width: 90, height: 44};
    const pick = (x, y = 42) => statusPullZone(x, y, {status, clock, island, right});
    assert.equal(pick(clock.x + 36), 'options');
    assert.equal(pick(right.x + 40), 'options');
    assert.equal(pick(island.x - 16), 'notifications');
    assert.equal(pick(island.x + island.width + 16), 'notifications');
    assert.equal(pick(island.x + 22, 60), 'notifications', 'padding extends below the tiny painted nib');
    assert.equal(pick(100, 90), null, 'app content below status is not intercepted');
    assert.equal(pick(10), null); assert.equal(pick(800), null);
}
console.log('PASS clock/right pulls, generous nib target, independent regions and screen bounds');
