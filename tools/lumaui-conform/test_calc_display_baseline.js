const assert = require('node:assert/strict');
const { calculatorDisplayPosition } = require('./lib/calc-display-tracking');

const parent = { id: 16, classes: ['horizontal', 'calc-expression'] };
const widget = { id: 18, parent: 16, classes: ['lumaui-t-display'], text: '0' };
const spec = { box: { x: 315.22, y: 354.81 }, baseline: 418.81,
  font: { size: 72, weight: 600 } };
const gtk = { w: widget, box: { x: 314.52, y: 349.4 }, baseline: 417.8,
  font: { size: 72, weight: 600 } };
assert.deepEqual(calculatorDisplayPosition('calc', spec, gtk, [parent, widget]),
  { specY: 418.81, gtkY: 417.8, baseline: true });
assert.ok(Math.abs(gtk.baseline - spec.baseline) < 2);
assert.ok(Math.abs(gtk.box.y - spec.box.y) > 5);

// A real three-pixel vertical displacement must still fail the same threshold.
const shifted = calculatorDisplayPosition('calc', spec, { ...gtk, baseline: 421.8 }, [parent, widget]);
assert.ok(Math.abs(shifted.gtkY - shifted.specY) > 2);
assert.deepEqual(calculatorDisplayPosition('clock', spec, gtk, [parent, widget]),
  { specY: spec.box.y, gtkY: gtk.box.y, baseline: false });
assert.deepEqual(calculatorDisplayPosition('calc', spec, { ...gtk, w: { ...widget, classes: ['lumaui-t-body'] } }, [parent, widget]),
  { specY: spec.box.y, gtkY: gtk.box.y, baseline: false });
assert.deepEqual(calculatorDisplayPosition('calc', spec, gtk, [{ ...parent, classes: ['tape'] }, widget]),
  { specY: spec.box.y, gtkY: gtk.box.y, baseline: false });
assert.deepEqual(calculatorDisplayPosition('calc', spec, { ...gtk, baseline: null }, [parent, widget]),
  { specY: spec.box.y, gtkY: gtk.box.y, baseline: false });
assert.deepEqual(calculatorDisplayPosition('calc', spec, { ...gtk, font: { size: 68, weight: 600 } }, [parent, widget]),
  { specY: spec.box.y, gtkY: gtk.box.y, baseline: false });
console.log('Calculator display baseline pairing and negative scope PASS');
