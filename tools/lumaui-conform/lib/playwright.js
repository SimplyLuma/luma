// Find Playwright without installing anything: $LUMAUI_CONFORM_PLAYWRIGHT,
// a normal require, or the newest copy in the npx cache.
const fs = require('fs'), path = require('path'), os = require('os');

function load() {
  const tries = [];
  if (process.env.LUMAUI_CONFORM_PLAYWRIGHT) tries.push(process.env.LUMAUI_CONFORM_PLAYWRIGHT);
  tries.push('playwright');
  const npx = path.join(os.homedir(), '.npm', '_npx');
  try {
    fs.readdirSync(npx).map(d => path.join(npx, d, 'node_modules', 'playwright'))
      .filter(p => fs.existsSync(path.join(p, 'package.json')))
      .map(p => [p, JSON.parse(fs.readFileSync(path.join(p, 'package.json'))).version])
      .sort((a, b) => b[1].localeCompare(a[1], undefined, { numeric: true }))
      .forEach(([p]) => tries.push(p));
  } catch (e) { /* no npx cache */ }
  for (const t of tries) { try { return require(t); } catch (e) { /* next */ } }
  throw new Error('lumaui-conform: Playwright not found. Run `npx playwright --version` once, or set LUMAUI_CONFORM_PLAYWRIGHT=/path/to/node_modules/playwright');
}
module.exports = load();
