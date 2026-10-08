#!/usr/bin/env node
// Replay a post-line-box Calculator capture from the server's split layout.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const archive = process.argv[2], scenario = process.argv[3];
if (!archive || !scenario) throw new Error('usage: test_calc_display_baseline_staged.js ARCHIVED_REPORT CALC_SCENARIO');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'calc-baseline-stage-'));
try {
  const tools = path.join(root, 'w/tools');
  const kit = path.join(root, 'w/src/kit');
  fs.mkdirSync(tools, { recursive: true });
  fs.mkdirSync(path.join(kit, 'config/shared'), { recursive: true });
  fs.cpSync(path.join(__dirname, 'compare.js'), path.join(tools, 'compare.js'));
  fs.cpSync(path.join(__dirname, 'lib'), path.join(tools, 'lib'), { recursive: true });
  fs.cpSync(path.join(__dirname, '../../config/shared/design-tokens.json'),
    path.join(kit, 'config/shared/design-tokens.json'));
  const stub = path.join(root, 'playwright-stub.js');
  fs.writeFileSync(stub, `const blank = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9NLikAAAAASUVORK5CYII=';
module.exports = { chromium: { launch: async () => ({ newPage: async () => ({ evaluate: async (_f, input) => Array.isArray(input) ? input.map(() => null) : ({ heat: blank, side: blank, over10: 0, mean: 0, regions: [] }) }), close: async () => {} }) } };
`);
  const run = name => {
    const out = path.join(root, name);
    fs.cpSync(archive, out, { recursive: true });
    return out;
  };
  const compare = out => {
    const result = spawnSync(process.execPath, [path.join(tools, 'compare.js'),
      '--scenario', scenario, '--dir', out], { encoding: 'utf8',
      env: { ...process.env, LUMAUI_CONFORM_KIT: kit,
        LUMAUI_CONFORM_REPO: path.join(root, 'w/src/wt'), LUMAUI_CONFORM_PLAYWRIGHT: stub } });
    assert.equal(result.status, 1, result.stderr || result.stdout); // Other app issues remain.
    return JSON.parse(fs.readFileSync(path.join(out, 'issues.json')));
  };
  const issue = (items, state, subject, prop) => items.find(i => i.state === state &&
    i.group === 'display' && i.subject === subject && i.prop === prop);
  const before = JSON.parse(fs.readFileSync(path.join(archive, 'issues.json')));
  const normal = compare(run('normal'));
  assert.ok(issue(before, 'idle', 'text "0"', 'position'));
  assert.equal(issue(normal, 'idle', 'text "0"', 'position'), undefined);
  assert.equal(issue(normal, 'result', 'text "5"', 'position'), undefined);
  assert.equal(issue(normal, 'menu', 'text "0"', 'position'), undefined);
  assert.equal(issue(normal, 'scientific', 'text "0"', 'position'), undefined);
  assert.equal(issue(normal, 'preview', 'text "+"', 'position'), undefined);
  assert.equal(issue(normal, 'preview', 'text "3"', 'position'), undefined);
  assert.equal(issue(normal, 'preview', 'text "2"', 'position').gtk, '987.6,337.8 baseline');
  const title = items => items.find(i => i.state === 'idle' && i.subject === 'text "Calculator"' && i.prop === 'position');
  assert.deepEqual(title(normal), title(before));
  assert.deepEqual(normal.filter(i => i.group === 'tape'), before.filter(i => i.group === 'tape'));

  const shifted = run('shifted');
  const gtkFile = path.join(shifted, 'gtk-idle.json');
  const gtk = JSON.parse(fs.readFileSync(gtkFile));
  const label = gtk.widgets.find(w => w.classes?.includes('lumaui-t-display') && w.text === '0');
  assert.ok(label);
  label.box[1] += 5;
  label.box[2] += 6;
  label.layout[2] += 6;
  for (const op of gtk.ops.filter(op => op.k === 'text' && op.widget === label.id)) {
    op.baseline += 5;
    op.box[1] += 5;
  }
  fs.writeFileSync(gtkFile, JSON.stringify(gtk));
  const negative = compare(shifted);
  assert.equal(issue(negative, 'idle', 'text "0"', 'position').gtk, '1104.5,342.8 baseline');
  assert.ok(issue(negative, 'idle', 'text "0"', 'width'));
  assert.deepEqual(title(negative), title(before));
  console.log('staged Calculator baseline compare: aligned glyphs pass, real vertical/width errors remain, unrelated text unchanged PASS');
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}
