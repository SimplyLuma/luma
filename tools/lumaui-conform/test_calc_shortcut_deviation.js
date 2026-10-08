const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { matchesDeviation } = require('./lib/deviation-match');
const { calculatorShortcutPairs, acceptedShortcutLabels } = require('./lib/linux-shortcut-equivalence');

const entries = JSON.parse(fs.readFileSync(path.join(__dirname, 'accepted-deviations.json'))).deviations;
const rules = Object.fromEntries(['spec', 'gtk', 'digit'].map(kind => [kind, entries.find(item => item.id === `calc-linux-shortcut-${kind}`)]));
assert.ok(Object.values(rules).every(Boolean));
const text = (value, x, y, w = 16, h = 14) => ({ text: value, box: { x, y, w, h } });
const spec = [text('⌘1', 143, 62), text('⌘2', 174, 98)];
const gtk = [text('Ctrl', 159, 52, 22, 19), text('1', 184, 52, 6, 19), text('Ctrl', 157, 88, 22, 19), text('2', 182, 88, 8, 19), text('1', 29, 609, 249, 48), text('2', 320, 609, 249, 48)];
const pairs = calculatorShortcutPairs('calc', 'menu', spec, gtk);
assert.equal(pairs.size, 2);
const issue = (subject, prop, box, group = 'menu') => ({ subject, prop, group, box });
const checks = [
  [rules.spec, issue('text "⌘1"', 'missing', spec[0].box)],
  [rules.spec, issue('text "⌘2"', 'missing', spec[1].box)],
  [rules.gtk, issue('text "Ctrl"', 'extra', gtk[0].box)],
  [rules.gtk, issue('text "Ctrl"', 'extra', gtk[2].box)],
  [rules.digit, issue('text "1"', 'extra', gtk[1].box)],
  [rules.digit, issue('text "2"', 'extra', gtk[3].box)],
];
for (const [rule, found] of checks) {
  assert.ok(matchesDeviation(rule, found, 'menu', 'calc', 0, pairs));
  assert.equal(matchesDeviation(rule, found, 'menu', 'calc', 2, pairs), false);
  assert.equal(matchesDeviation(rule, found, 'menu', 'term', 0, pairs), false);
  assert.equal(matchesDeviation(rule, found, 'idle', 'calc', 0, pairs), false);
  assert.equal(matchesDeviation(rule, { ...found, group: 'title row' }, 'menu', 'calc', 0, pairs), false);
  assert.equal(matchesDeviation(rule, { ...found, box: { ...found.box, y: found.box.y + 100 } }, 'menu', 'calc', 0, pairs), false);
  assert.equal(matchesDeviation(rule, found, 'menu', 'calc', 0), false);
}
for (const index of [4, 5]) assert.equal(matchesDeviation(rules.digit, issue(`text "${gtk[index].text}"`, 'extra', gtk[index].box), 'menu', 'calc', 0, pairs), false);
assert.equal(matchesDeviation(rules.digit, issue('text "3"', 'extra', gtk[1].box), 'menu', 'calc', 0, pairs), false);
assert.equal(matchesDeviation(rules.digit, issue('menu "Scientific"', 'size', gtk[1].box), 'menu', 'calc', 0, pairs), false);
assert.equal(calculatorShortcutPairs('term', 'menu', spec, gtk).size, 0);
assert.equal(calculatorShortcutPairs('calc', 'idle', spec, gtk).size, 0);
assert.equal(calculatorShortcutPairs('calc', 'menu', spec, gtk.filter(t => t !== gtk[1])).has('1'), false);
assert.equal(calculatorShortcutPairs('calc', 'menu', spec, gtk.map(t => t === gtk[1] ? { ...t, box: { ...t.box, x: 230 } } : t)).has('1'), false);
assert.equal(calculatorShortcutPairs('calc', 'menu', spec, gtk.map(t => t === gtk[1] ? { ...t, box: { ...t.box, y: 100 } } : t)).has('1'), false);
assert.deepEqual([...acceptedShortcutLabels(checks.slice(0, 2).map(([, found]) => ({ ...found, accepted: 'calc-linux-shortcut-spec' })))], ['⌘1', '⌘2']);
assert.equal(acceptedShortcutLabels(checks.slice(0, 2).map(([, found]) => found)).size, 0);
assert.equal(acceptedShortcutLabels([{ ...checks[0][1], accepted: 'calc-linux-shortcut-digit' }]).size, 0);
console.log('Calculator Ctrl+1/Ctrl+2 equivalence, caps, proximity and unrelated text PASS');
