#!/usr/bin/env node
// lumaui-conform, pixel mode: the spec PNG of each state against an app PNG the caller
// supplies (Viola, the Shell: anything that is not a GTK widget tree). No element matching.
//   node pixel_compare.js --scenario scenarios/<app>.json --dir DIR --pngs PNGDIR
// PNGDIR holds <state>.png (or <variant>/<state>.png, e.g. dark/ light-phone/). The app PNG is
// scaled to the spec window's size when it differs (and the report says so). Writes
// DIR/pixel-<state>.png (spec | app | heat), DIR/heat-<state>.png, DIR/REPORT.md and
// DIR/pixel.json; exits 0 when every state passes.
// A state passes when its mean ΔE (CIE76, over the whole window) is at most max_mean (3) and
// at most max_over10 (2%) of its pixels differ by more than ΔE 10; scenario.pixel overrides both.
// Region scores: the scenario's groups (spec selectors) and a 4×3 grid of the window.
const fs = require('fs'), path = require('path');
const { chromium } = require('./lib/playwright');

const args = process.argv.slice(2), opt = k => { const i = args.indexOf('--' + k); return i < 0 ? null : args[i + 1]; };
const scenario = JSON.parse(fs.readFileSync(opt('scenario'), 'utf8')), dir = opt('dir'), pngs = opt('pngs');
const meta = JSON.parse(fs.readFileSync(path.join(dir, 'spec-meta.json'), 'utf8'));
const variant = `${meta.theme}${meta.phone ? '-phone' : ''}`;
const limits = Object.assign({ max_mean: 3, max_over10: 0.02 }, scenario.pixel || {});
const appPng = st => [path.join(pngs, variant, `${st}.png`), path.join(pngs, `${st}.png`)].find(f => fs.existsSync(f));
const data = f => 'data:image/png;base64,' + fs.readFileSync(f).toString('base64');

(async () => {
  const browser = await chromium.launch(), page = await browser.newPage();
  const rows = [], out = [];
  for (const st of meta.states.map(s => s.name)) {
    const sp = path.join(dir, `spec-${st}.png`), ap = appPng(st);
    if (!ap) { rows.push({ state: st, missing: true }); continue; }
    const spec = JSON.parse(fs.readFileSync(path.join(dir, `spec-${st}.json`), 'utf8'));
    const r = await page.evaluate(async ({ a, b, groups }) => {
      const load = src => new Promise((ok, no) => { const i = new Image(); i.onload = () => ok(i); i.onerror = no; i.src = src; });
      const [ia, ib] = await Promise.all([load(a), load(b)]); const W = ia.width, H = ia.height;
      const cv = (w, h) => { const c = document.createElement('canvas'); c.width = w; c.height = h; return c; };
      const ca = cv(W, H), cb = cv(W, H), xa = ca.getContext('2d', { willReadFrequently: true }), xb = cb.getContext('2d', { willReadFrequently: true });
      xa.drawImage(ia, 0, 0); xb.fillStyle = '#000'; xb.fillRect(0, 0, W, H); xb.drawImage(ib, 0, 0, W, H);
      const A = xa.getImageData(0, 0, W, H).data, Bd = xb.getImageData(0, 0, W, H).data;
      const lin = v => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }, g = t => t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116;
      const LAB = (r, gg, bb) => { const R = lin(r), G = lin(gg), Bb = lin(bb); const X = (R * 0.4124 + G * 0.3576 + Bb * 0.1805) / 0.95047, Y = R * 0.2126 + G * 0.7152 + Bb * 0.0722, Z = (R * 0.0193 + G * 0.1192 + Bb * 0.9505) / 1.08883; return [116 * g(Y) - 16, 500 * (g(X) - g(Y)), 200 * (g(Y) - g(Z))]; };
      const dE = new Float32Array(W * H); let over10 = 0, sum = 0;
      for (let k = 0; k < W * H; k++) { const i = k * 4, p = LAB(A[i], A[i + 1], A[i + 2]), q = LAB(Bd[i], Bd[i + 1], Bd[i + 2]);
        const d = Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]); dE[k] = d; sum += d; if (d > 10) over10++; }
      const ch = cv(W, H), xh = ch.getContext('2d'), H2 = xh.createImageData(W, H);
      for (let k = 0; k < W * H; k++) { const i = k * 4, l = (A[i] * 0.3 + A[i + 1] * 0.59 + A[i + 2] * 0.11) * 0.35, t = Math.min(1, Math.max(0, (dE[k] - 3) / 27));
        H2.data[i] = l + (255 - l) * t; H2.data[i + 1] = l * (1 - t) + 40 * t; H2.data[i + 2] = l * (1 - t) + 40 * t; H2.data[i + 3] = 255; }
      xh.putImageData(H2, 0, 0);
      const side = cv(W * 3 + 32, H + 28), xs = side.getContext('2d'); xs.fillStyle = '#16171a'; xs.fillRect(0, 0, side.width, side.height);
      xs.font = '600 14px sans-serif'; xs.fillStyle = '#e4e8ea'; ['v70 spec', 'app', 'difference (red = ΔE up to 30)'].forEach((t, i) => xs.fillText(t, i * (W + 16) + 4, 18));
      xs.drawImage(ca, 0, 28); xs.drawImage(cb, W + 16, 28); xs.drawImage(ch, 2 * (W + 16), 28);
      const score = (name, boxes) => { let s = 0, c = 0, o = 0; for (const bx of boxes) for (let y = Math.max(0, Math.floor(bx.y)); y < Math.min(H, bx.y + bx.h); y++) for (let x = Math.max(0, Math.floor(bx.x)); x < Math.min(W, bx.x + bx.w); x++) { const d = dE[y * W + x]; s += d; c++; if (d > 10) o++; }
        return { name, mean: c ? s / c : 0, over10: c ? o / c : 0, px: c }; };
      const regions = groups.filter(gr => gr.boxes.length).map(gr => score(gr.name, gr.boxes));
      for (let gy = 0; gy < 3; gy++) for (let gx = 0; gx < 4; gx++) regions.push(score(`grid ${'top middle bottom'.split(' ')[gy]} ${gx + 1}/4`, [{ x: gx * W / 4, y: gy * H / 3, w: W / 4, h: H / 3 }]));
      return { W, H, appW: ib.width, appH: ib.height, mean: sum / (W * H), over10: over10 / (W * H), regions,
        heat: ch.toDataURL('image/png'), side: side.toDataURL('image/png') };
    }, { a: data(sp), b: data(ap), groups: spec.groups || [] });
    fs.writeFileSync(path.join(dir, `heat-${st}.png`), Buffer.from(r.heat.split(',')[1], 'base64'));
    fs.writeFileSync(path.join(dir, `pixel-${st}.png`), Buffer.from(r.side.split(',')[1], 'base64'));
    delete r.heat; delete r.side;
    r.state = st; r.app = path.relative(pngs, ap); r.pass = r.mean <= limits.max_mean && r.over10 <= limits.max_over10;
    rows.push(r);
  }
  await browser.close();
  const pass = rows.length > 0 && rows.every(r => r.pass);
  const pct = v => (v * 100).toFixed(2) + '%', f1 = v => v.toFixed(2);
  out.push(`# ${scenario.title || scenario.app} (pixel mode, ${variant}): ${pass ? 'PASS' : 'FAIL'}`, '',
    `Spec PNG against the app PNGs in \`${pngs}\`. A state passes at mean ΔE ≤ ${limits.max_mean} and ≤ ${pct(limits.max_over10)} of pixels over ΔE 10.`, '',
    '| state | app PNG | mean ΔE | pixels over ΔE 10 | verdict |', '|---|---|---|---|---|');
  for (const r of rows) out.push(r.missing ? `| ${r.state} | missing (${r.state}.png) | | | FAIL |`
    : `| ${r.state} | ${r.app}${r.appW !== r.W || r.appH !== r.H ? ` (${r.appW}×${r.appH} scaled to ${r.W}×${r.H})` : ''} | ${f1(r.mean)} | ${pct(r.over10)} | ${r.pass ? 'PASS' : 'FAIL'} |`);
  for (const r of rows.filter(x => !x.missing)) {
    out.push('', `## ${r.state}`, '', `![side by side](pixel-${r.state}.png)`, '', '| region | mean ΔE | over ΔE 10 |', '|---|---|---|');
    for (const g of [...r.regions].sort((a, b) => b.mean - a.mean)) out.push(`| ${g.name} | ${f1(g.mean)} | ${pct(g.over10)} |`);
  }
  fs.writeFileSync(path.join(dir, 'REPORT.md'), out.join('\n') + '\n');
  fs.writeFileSync(path.join(dir, 'pixel.json'), JSON.stringify({ variant, limits, pass, states: rows }, null, 1));
  console.log(`${pass ? 'PASS' : 'FAIL'} (pixel): ${rows.map(r => r.missing ? `${r.state} missing` : `${r.state} ΔE ${f1(r.mean)}, ${pct(r.over10)} over 10`).join('; ')}. Report: ${path.join(dir, 'REPORT.md')}`);
  process.exit(pass ? 0 : 1);
})().catch(e => { console.error(e.message || e); process.exit(3); });
