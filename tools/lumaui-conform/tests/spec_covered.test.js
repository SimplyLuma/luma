// spec_capture's dump leaves out elements wholly under a later opaque sibling (a phone's full-screen list
// over the page it pushes) and keeps the words of a block ::before (its large title). Needs Playwright: node tools/lumaui-conform/tests/spec_covered.test.js
const assert = require('node:assert/strict');
const { chromium } = require('../lib/playwright');
const { dump } = require('../spec_capture');

const html = `<!doctype html><style>
  body { margin: 0; font: 14px sans-serif; color: #fff; background: #123 }
  #win { position: relative; width: 400px; height: 900px; overflow: hidden; background: #222 }
  #stack, #phone { position: relative; height: 150px }
  #stack .card, #phone .pane { position: absolute; inset: 0 }
  #stack .card { background: #345; border-radius: 12px; padding: 12px; box-sizing: border-box }
  #phone .pane.list { background: #456 }
  #phone .pane.page { background: none }
  #titled { padding: 20px 16px; display: flex; flex-direction: column }
  #titled::before { content: "Memos"; display: block; padding: 4px 0 12px; font: 750 32px/1.1 sans-serif }
</style><div id="win">
  <div id="stack"><div class="card"><b>under card</b></div><div class="card"><b>over card</b></div></div>
  <div id="phone"><div class="pane list" style="z-index:2"><b>list words</b></div><div class="pane page"><b>page words</b></div></div>
  <div style="position:relative;height:60px"><div style="position:absolute;left:0;top:0;width:200px;height:60px;background:#567"><b>peeking</b></div><div style="position:absolute;left:0;top:0;width:20px;height:60px;background:#678"></div></div>
  <div style="position:relative;height:60px"><div style="position:absolute;inset:0;background:#789"><b>top-z words</b></div><div style="position:absolute;inset:0;background:#89a;z-index:-1"><b>under-z words</b></div></div>
  <div style="position:relative;height:60px"><div style="position:absolute;inset:0"><b>glass words</b></div><div style="position:absolute;inset:0;background:rgba(0,0,0,.5)"></div></div>
  <div style="position:relative;height:60px"><div style="position:absolute;inset:0"><b>frosted words</b></div><div style="position:absolute;inset:0;background:rgba(255,255,255,.88);backdrop-filter:blur(24px)"></div></div>
  <div style="position:relative;height:60px"><div style="position:absolute;inset:0"><b>headed words</b></div><header style="position:absolute;inset:0;z-index:3"><div style="height:60px;background:#345"></div></header></div>
  <div style="position:relative;height:60px"><div style="position:absolute;inset:0"><b>ramp words</b></div><div style="position:absolute;inset:0;background-image:linear-gradient(#fff,#f7f8fa)"></div></div>
  <nav id="titled"><b>first row</b></nav>
</div>`;

(async () => {
  const browser = await chromium.launch(), page = await browser.newPage({ viewport: { width: 420, height: 1000 } });
  await page.setContent(html);
  const d = await page.evaluate(dump, { winSel: '#win', groups: [], exclude: [], pairSels: [] });
  await browser.close();
  const words = d.texts.map(t => t.text);
  const has = w => assert.ok(words.includes(w), `${w} is dumped`), no = w => assert.ok(!words.includes(w), `${w} is not dumped`);
  no('under card'); has('over card');
  no('page words'); has('list words');   // a higher z-index covers a later sibling
  has('peeking');                        // only partly covered
  no('under-z words'); has('top-z words');
  has('glass words');                    // a translucent cover does not hide
  no('frosted words');                   // frosted glass does
  no('ramp words');                      // an opaque gradient covers
  no('headed words');                    // an opaque part inside a later sibling covers too
  const title = d.texts.find(t => t.text === 'Memos'), row = d.texts.find(t => t.text === 'first row');
  assert.ok(title && title.font.size === 32 && title.font.weight === 750, 'a block ::before is dumped with its font');
  assert.ok(Math.abs(title.box.x - 16) < 0.5 && Math.abs(title.box.y - (row.box.y - 35.2 - 12)) < 1, 'at its place above the first row');
  console.log('spec covered: 13 checks pass');
})().catch(e => { console.error(e.stack || e); process.exit(1); });
