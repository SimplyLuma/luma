const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { calculatorDisplayTextBox, displayTrackingEm } = require('./lib/calc-display-tracking');

const tokens = JSON.parse(fs.readFileSync(path.join(__dirname, '../../config/shared/design-tokens.json')));
const tracking = tokens.lumaui.type_scale.display.tracking_em;
assert.equal(tracking, -0.035);
assert.equal(displayTrackingEm('calc', __dirname, {}), tracking);
const parent = { id: 16, classes: ['horizontal', 'calc-expression'] };
const label = (id, x, text, width) => ({ id, parent: 16, classes: ['lumaui-t-display'], text,
  box: [x, 328, width, 80], font: { size: 72 } });
const zero = label(18, 312, '0', 46);
const single = calculatorDisplayTextBox('calc', zero, [parent, zero], { x: 312, y: 337, w: 46, h: 80 }, tracking);
assert.equal(single.x, 314.52);
assert.equal(single.w, 43.48);
assert.ok(Math.abs(single.x - 315.22) < 2 && Math.abs(single.w - 42.78) < 2);

const two = label(18, 190, '2 ', 61), plus = label(19, 251, '+', 46), three = label(20, 297, ' 3', 61);
const siblings = [parent, two, plus, three];
for (const [widget, wantX, wantW] of [[two, 197.56, 58.48], [plus, 256.04, 43.48], [three, 299.52, 58.48]]) {
  const actual = calculatorDisplayTextBox('calc', widget, siblings, { x: widget.box[0], y: 337, w: widget.box[2], h: 80 }, tracking);
  assert.ok(Math.abs(actual.x - wantX) < 0.0001);
  assert.ok(Math.abs(actual.w - wantW) < 0.0001);
}
const original = { x: 312, y: 337, w: 46, h: 80 };
assert.equal(calculatorDisplayTextBox('clock', zero, [parent, zero], original, tracking), original);
assert.equal(calculatorDisplayTextBox('calc', { ...zero, classes: ['lumaui-t-numeric'] }, [parent, zero], original, tracking), original);
assert.equal(calculatorDisplayTextBox('calc', { ...zero, ellipsized: true }, [parent, zero], original, tracking), original);
assert.equal(calculatorDisplayTextBox('calc', zero, [{ ...parent, classes: ['keypad'] }, zero], original, tracking), original);
assert.equal(calculatorDisplayTextBox('calc', { ...zero, text: '' }, [parent, zero], original, tracking), original);
console.log('Calculator display advance normalization and unrelated text scope PASS');
