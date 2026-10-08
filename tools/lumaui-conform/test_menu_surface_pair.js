const assert = require('node:assert/strict');
const { menuSurfacePairs } = require('./lib/menu-surface-pair');

const spec = { el: { id: 52 }, role: 'menu', box: { x: 8, y: 45, w: 200, h: 167 } };
const widgets = [
  { id: 81, parent: null, css: 'popover', role: 'menu', box: [-29, 20, 274, 241] },
  { id: 82, parent: 81, css: 'contents', role: 'generic', box: [8, 45, 200, 167] },
  { id: 99, parent: 82, css: 'button', role: 'menu-item-checkbox', text: 'Scientific', box: [14, 87, 188, 36] },
  { id: 201, parent: null, css: 'popover', role: 'dialog', box: [8, 45, 200, 167] },
  { id: 202, parent: 201, css: 'contents', role: 'generic', box: [8, 45, 200, 167] },
];
assert.equal(menuSurfacePairs([spec], widgets)[0][1].id, 82);
assert.equal(menuSurfacePairs([spec], widgets, new Set([spec.el])).length, 0);
assert.equal(menuSurfacePairs([spec], widgets, new Set(), new Set([widgets[1]])).length, 0);
assert.equal(menuSurfacePairs([spec], widgets.map(w => w.id === 82 ? { ...w, offscreen: true } : w)).length, 0);
assert.equal(menuSurfacePairs([spec], widgets.filter(w => w.id !== 81)).length, 0);
const second = { el: { id: 252 }, role: 'menu', box: { x: 300, y: 45, w: 200, h: 167 } };
const another = [
  { id: 301, parent: null, css: 'popover', role: 'menu', box: [263, 20, 274, 241] },
  { id: 302, parent: 301, css: 'contents', role: 'generic', box: [300, 45, 200, 167] },
];
assert.deepEqual(menuSurfacePairs([spec, second], [...widgets, ...another]).map(pair => pair[1].id), [82, 302]);
console.log('GTK menu surface pairs use painted popover contents, not nested rows');
