#!/usr/bin/env node
// Replay a captured Calculator report with the same split layout as the server:
// /w/tools is copied out of the kit, whose tokens remain under /w/src/kit.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const archive = process.argv[2], scenario = process.argv[3];
if (!archive || !scenario) throw new Error('usage: test_calc_display_tracking_staged.js ARCHIVED_REPORT CALC_SCENARIO');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'calc-tracking-stage-'));
try {
  const tools = path.join(root, 'w/tools');
  const kit = path.join(root, 'w/src/kit');
  const output = path.join(root, 'out');
  fs.mkdirSync(tools, { recursive: true });
  fs.mkdirSync(path.join(kit, 'config/shared'), { recursive: true });
  fs.cpSync(path.join(__dirname, 'compare.js'), path.join(tools, 'compare.js'));
  fs.cpSync(path.join(__dirname, 'lib'), path.join(tools, 'lib'), { recursive: true });
  fs.cpSync(path.join(__dirname, '../../config/shared/design-tokens.json'),
    path.join(kit, 'config/shared/design-tokens.json'));
  fs.cpSync(archive, output, { recursive: true });
  const stub = path.join(root, 'playwright-stub.js');
  fs.writeFileSync(stub, `const blank = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9NLikAAAAASUVORK5CYII=';
module.exports = { chromium: { launch: async () => ({ newPage: async () => ({ evaluate: async (_f, input) => Array.isArray(input) ? input.map(() => null) : ({ heat: blank, side: blank, over10: 0, mean: 0, regions: [] }) }), close: async () => {} }) } };
`);
  const run = spawnSync(process.execPath, [path.join(tools, 'compare.js'),
    '--scenario', scenario, '--dir', output], { encoding: 'utf8',
    env: { ...process.env, LUMAUI_CONFORM_KIT: kit, LUMAUI_CONFORM_REPO: path.join(root, 'w/src/wt'),
      LUMAUI_CONFORM_PLAYWRIGHT: stub } });
  assert.equal(run.status, 1, run.stderr || run.stdout); // Other archived differences remain.
  const before = JSON.parse(fs.readFileSync(path.join(archive, 'issues.json')));
  const after = JSON.parse(fs.readFileSync(path.join(output, 'issues.json')));
  const issue = (items, state, subject, prop) => items.find(i =>
    i.state === state && i.group === 'display' && i.subject === subject && i.prop === prop);
  assert.ok(issue(before, 'idle', 'text "0"', 'width'));
  assert.equal(issue(after, 'idle', 'text "0"', 'width'), undefined);
  assert.ok(issue(before, 'preview', 'text "+"', 'width'));
  assert.equal(issue(after, 'preview', 'text "+"', 'width'), undefined);
  assert.equal(issue(before, 'preview', 'text "2"', 'position').gtk, '190,337');
  // This older capture predates the shared line-box repair. Its actual
  // baseline is 12.2px high, so the vertical issue must remain visible.
  for (const [subject, oldX, newX] of [
    ['text "2"', '190,337', '197.6,405.4 baseline'],
    ['text "+"', '251,337', '256,405.4 baseline'],
    ['text "3"', '297,337', '299.5,405.4 baseline'],
  ]) {
    assert.equal(issue(before, 'preview', subject, 'position').gtk, oldX);
    assert.equal(issue(after, 'preview', subject, 'position').gtk, newX);
  }
  const title = items => items.find(i => i.state === 'idle' && i.subject === 'text "Calculator"' && i.prop === 'position');
  assert.deepEqual(title(after), title(before));
  assert.deepEqual(after.filter(i => i.group === 'tape'), before.filter(i => i.group === 'tape'));
  console.log('staged Calculator compare: single glyph, multi-label positions, unrelated text PASS');
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}
