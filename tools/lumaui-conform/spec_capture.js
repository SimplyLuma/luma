#!/usr/bin/env node
// lumaui-conform, step 1: capture the v70 spec for one app, per state.
//   node spec_capture.js --scenario scenarios/contacts.json --theme dark [--phone] --out DIR [--base URL]
// Writes DIR/spec-<state>.json (every visible element, text run and icon in the
// app window, relative to the window) and DIR/spec-<state>.png (the window at scale 1).
const fs = require('fs'), path = require('path');
const { chromium } = require('./lib/playwright');

const args = process.argv.slice(2), opt = k => { const i = args.indexOf('--' + k); return i < 0 ? null : (args[i + 1] && !args[i + 1].startsWith('--') ? args[i + 1] : true); };
const scenario = JSON.parse(fs.readFileSync(opt('scenario'), 'utf8'));
const theme = opt('theme') || 'dark', phone = !!opt('phone'), out = opt('out'), base = (opt('base') || process.env.LUMAUI_CONFORM_BASE || 'http://127.0.0.1:8787/').replace(/\/?$/, '/');
fs.mkdirSync(out, { recursive: true });
const studioRoot = process.env.LUMAUI_CONFORM_STUDIO_ROOT || path.join(require('os').homedir(), 'Downloads', 'Operating system window treatment');

// Runs in the page: dump everything visible inside the window.
function dump({ winSel, groups, exclude, pairSels }) {
  // A button whose words are hidden with font-size 0 (v70 folds a word away at phone width) is still named by
  // them, as its accessible name is; GTK keeps that name too.
  const hiddenName = el => { if (el.tagName !== 'BUTTON') return null; const t = el.textContent.replace(/\s+/g, ' ').trim(); if (!t) return null;
    const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT); let n, shown = false;
    while ((n = w.nextNode())) if (n.textContent.trim() && parseFloat(getComputedStyle(n.parentElement).fontSize) > 0) { shown = true; break; }
    return shown ? null : t; };
  const win = document.querySelector(winSel);
  // Text a script wrote a character at a time (Notes' nLive) is one run per paragraph, as the reader sees it.
  win.normalize();
  const W = win.getBoundingClientRect();
  const rel = r => ({ x: +(r.left - W.left).toFixed(2), y: +(r.top - W.top).toFixed(2), w: +r.width.toFixed(2), h: +r.height.toFixed(2) });
  const px = v => parseFloat(v) || 0;
  // Computed colours come back as oklch(); give the comparer plain rgba().
  const cv = document.createElement('canvas'); cv.width = cv.height = 1; const cx = cv.getContext('2d', { willReadFrequently: true });
  const one = c => { const m = c.match(/^(\w+)\((.*)\)$/s); if (!m || m[1] === 'rgb' || m[1] === 'rgba') return c; let body = m[2], a = 1;
    const sl = body.lastIndexOf('/'); if (sl >= 0) { const v = body.slice(sl + 1).trim(); a = v.endsWith('%') ? parseFloat(v) / 100 : parseFloat(v); body = body.slice(0, sl); }
    cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#000'; cx.fillStyle = `${m[1]}(${body})`; cx.fillRect(0, 0, 1, 1); const d = cx.getImageData(0, 0, 1, 1).data;
    return `rgba(${d[0]}, ${d[1]}, ${d[2]}, ${+a.toFixed(3)})`; };
  const col = s => typeof s === 'string' ? s.replace(/\b(oklch|oklab|lab|lch|color|hsl|hsla|hwb)\((?:[^()]|\([^()]*\))*\)/g, one) : s;
  const excluded = el => exclude.some(s => el.closest(s));
  // Visible rect after every clipping ancestor (overflow != visible) and the window itself.
  const clipOf = el => { let r = { l: W.left, t: W.top, r: W.right, b: W.bottom };
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) { const cs = getComputedStyle(a);
      if (cs.overflowX !== 'visible' || cs.overflowY !== 'visible' || cs.contain.includes('paint')) { const q = a.getBoundingClientRect();
        r = { l: Math.max(r.l, q.left), t: Math.max(r.t, q.top), r: Math.min(r.r, q.right), b: Math.min(r.b, q.bottom) }; }
      if (a === win) break; }
    return r; };
  const isect = (q, c) => { const l = Math.max(q.left, c.l), t = Math.max(q.top, c.t), r = Math.min(q.right, c.r), b = Math.min(q.bottom, c.b); return r - l > 0.5 && b - t > 0.5 ? { left: l, top: t, width: r - l, height: b - t } : null; };
  const opacityOf = el => { let o = 1; for (let a = el; a; a = a.parentElement) { o *= parseFloat(getComputedStyle(a).opacity); if (a === win) break; } return +o.toFixed(3); };
  const shown = el => { for (let a = el; a; a = a.parentElement) { const cs = getComputedStyle(a); if (cs.display === 'none' || a.hidden) return false; if (a === win) break; }
    const cs = getComputedStyle(el); return cs.visibility !== 'hidden'; };
  const roleOf = el => { const r = el.getAttribute('role'); if (r) return r; const t = el.tagName.toLowerCase();
    if (t === 'button' || t === 'summary') return el.classList.contains('sw') ? 'switch' : 'button';
    if (t === 'a') return 'link'; if (/^h[1-6]$/.test(t)) return 'heading'; if (t === 'input' && el.type === 'range') return 'slider'; if (t === 'input' || t === 'textarea') return (el.type === 'checkbox' ? 'checkbox' : el.type === 'search' || el.closest('.srch') ? 'searchbox' : 'textbox');
    if (el.isContentEditable && el.getAttribute('contenteditable') === 'true') return 'textbox';
    if (t === 'section' || t === 'article') return 'region'; if (t === 'nav') return 'navigation'; if (t === 'header') return 'banner'; if (t === 'main') return 'main';
    if (t === 'img' || (t === 'svg' && el.dataset.icon)) return 'img'; if (t === 'label') return 'label'; return 'generic'; };
  const tt = (s, cs) => cs.textTransform === 'uppercase' ? s.toUpperCase() : cs.textTransform === 'lowercase' ? s.toLowerCase() : cs.textTransform === 'capitalize' ? s.replace(/\b\w/g, c => c.toUpperCase()) : s;
  const font = cs => ({ family: cs.fontFamily.split(',')[0].replace(/["']/g, '').trim(), size: px(cs.fontSize), weight: +cs.fontWeight, style: cs.fontStyle,
    lineHeight: cs.lineHeight === 'normal' ? null : px(cs.lineHeight), letterSpacing: cs.letterSpacing === 'normal' ? 0 : px(cs.letterSpacing) });
  const paint = cs => { const p = paint0(cs); for (const k of Object.keys(p)) p[k] = k === 'border' ? { widths: p[k].widths, colors: p[k].colors.map(col) } : col(p[k]); return p; };
  const paint0 = cs => { const p = {};
    if (cs.backgroundColor && !/rgba\(0, 0, 0, 0\)|transparent/.test(cs.backgroundColor)) p.background = cs.backgroundColor;
    if (cs.backgroundImage && cs.backgroundImage !== 'none') p.backgroundImage = cs.backgroundImage.slice(0, 400);
    const bw = [cs.borderTopWidth, cs.borderRightWidth, cs.borderBottomWidth, cs.borderLeftWidth].map(px), bs = [cs.borderTopStyle, cs.borderRightStyle, cs.borderBottomStyle, cs.borderLeftStyle];
    if (bw.some((w, i) => w > 0 && bs[i] !== 'none')) p.border = { widths: bw, colors: [cs.borderTopColor, cs.borderRightColor, cs.borderBottomColor, cs.borderLeftColor] };
    const rad = [cs.borderTopLeftRadius, cs.borderTopRightRadius, cs.borderBottomRightRadius, cs.borderBottomLeftRadius].map(px); if (rad.some(r => r > 0)) p.radius = rad;
    if (cs.boxShadow && cs.boxShadow !== 'none') p.boxShadow = cs.boxShadow;
    if (cs.outlineStyle !== 'none' && px(cs.outlineWidth) > 0) p.outline = `${cs.outlineWidth} ${cs.outlineStyle} ${cs.outlineColor}`;
    if (cs.backdropFilter && cs.backdropFilter !== 'none') p.backdropFilter = cs.backdropFilter;
    return p; };
  const asc = cs => { cx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`; return cx.measureText('Hg').fontBoundingBoxAscent; };
  const elements = [], texts = [], icons = [], ids = new Map();
  const all = [win, ...win.querySelectorAll('*')];
  for (const el of all) {
    if (excluded(el) || !shown(el)) continue;
    const t = el.tagName.toLowerCase(); if (t === 'script' || t === 'style' || (el.closest('svg') && el.closest('svg') !== el)) continue;
    const q = el.getBoundingClientRect(); if (q.width < 0.5 || q.height < 0.5) continue;
    const clip = clipOf(el), vis = el === win ? q : isect(q, clip);
    const cs = getComputedStyle(el), op = opacityOf(el); if (op < 0.02) continue;
    if (!vis) { for (const n of el.childNodes) if (n.nodeType === 3 && n.textContent.trim()) { const r = document.createRange(); r.selectNodeContents(n); const u = r.getBoundingClientRect();
        if (u.width > 0.5) texts.push({ el: null, text: tt(n.textContent.replace(/\s+/g, ' ').trim(), cs), offscreen: true, box: rel(u), font: font(cs), color: col(cs.color), opacity: op, lines: 1 }); }
      continue; }
    const id = elements.length; ids.set(el, id);
    let parent = null; for (let a = el.parentElement; a; a = a.parentElement) { if (ids.has(a)) { parent = ids.get(a); break; } if (a === win) break; }
    const rec = { id, parent, tag: t, cls: typeof el.className === 'string' ? el.className : (el.getAttribute('class') || ''), elId: el.id || undefined, role: roleOf(el),
      label: el.getAttribute('aria-label') || el.getAttribute('title') || hiddenName(el) || undefined, box: rel(q), visible: rel(vis), opacity: op, color: col(cs.color), font: font(cs), paint: paint(cs) };
    if (t === 'svg' && el.dataset.icon) { rec.icon = el.dataset.icon; let ink = null; try {
        // The glyph's geometry as drawn: each shape's client rect is its bbox after every transform (a
        // rotated twisty, a flipped arrow), without stroke, as GTK's path bounds are.
        const shapes = [...el.querySelectorAll('path, circle, rect, ellipse, line, polyline, polygon')].map(p => p.getBoundingClientRect()).filter(r => r.width || r.height);
        if (shapes.length) { const l = Math.min(...shapes.map(r => r.left)), t = Math.min(...shapes.map(r => r.top)); ink = rel({ left: l, top: t, width: Math.max(...shapes.map(r => r.right)) - l, height: Math.max(...shapes.map(r => r.bottom)) - t }); }
        else { const bb = el.getBBox(), vb = el.viewBox.baseVal, k = vb && vb.width ? q.width / vb.width : 1; ink = rel({ left: q.left + (bb.x - (vb ? vb.x : 0)) * k, top: q.top + (bb.y - (vb ? vb.y : 0)) * k, width: bb.width * k, height: bb.height * k }); }
      } catch (e) { /* not rendered */ }
      icons.push({ el: id, name: el.dataset.icon, box: rel(vis), ink, color: col(cs.color), stroke: col(cs.stroke), opacity: op }); }
    if (t === 'img') rec.src = (el.getAttribute('src') || '').split('/').pop();
    if (rec.role === 'switch') rec.checked = el.classList.contains('on');
    if ((t === 'input' && el.type !== 'range') || t === 'textarea') { const v = el.value, ph = el.getAttribute('placeholder') || ''; rec.value = v; rec.placeholder = ph;
      const s = v || ph; if (s) { const c = document.createElement('canvas').getContext('2d'); c.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`; const m = c.measureText(s);
        const ascent = m.fontBoundingBoxAscent, descent = m.fontBoundingBoxDescent, ch = q.height - px(cs.paddingTop) - px(cs.paddingBottom) - px(cs.borderTopWidth) - px(cs.borderBottomWidth);
        const cw = q.width - px(cs.paddingLeft) - px(cs.paddingRight) - px(cs.borderLeftWidth) - px(cs.borderRightWidth), al = cs.textAlign === 'center' ? 0.5 : (cs.textAlign === 'right' || cs.textAlign === 'end') ? 1 : 0;
        const x = q.left + px(cs.paddingLeft) + px(cs.borderLeftWidth) + Math.max(0, cw - m.width) * al, y = q.top + px(cs.borderTopWidth) + px(cs.paddingTop) + (ch - ascent - descent) / 2;
        const tq = isect({ left: x, top: y, right: x + m.width, bottom: y + ascent + descent }, clip);
        if (tq) texts.push({ el: id, text: s, placeholder: !v, box: rel(tq), font: font(cs), color: col(v ? cs.color : (getComputedStyle(el, '::placeholder').color || cs.color)), opacity: op, lines: 1 }); } }
    elements.push(rec);
    // Own text runs: each direct text node, as the browser laid it out.
    for (const n of el.childNodes) { if (n.nodeType !== 3 || !n.textContent.trim()) continue;
      const r = document.createRange(); r.selectNodeContents(n); const rects = [...r.getClientRects()].filter(x => x.width > 0.5);
      if (!rects.length) continue; const u = rects.reduce((a, b) => ({ left: Math.min(a.left, b.left), top: Math.min(a.top, b.top), right: Math.max(a.right, b.right), bottom: Math.max(a.bottom, b.bottom) }), { left: 1e9, top: 1e9, right: -1e9, bottom: -1e9 });
      let tc = clip; if (cs.overflowX !== 'visible') tc = { l: Math.max(clip.l, q.left), t: Math.max(clip.t, q.top), r: Math.min(clip.r, q.right), b: Math.min(clip.b, q.bottom) };
      const tq = isect({ left: u.left, top: u.top, right: u.right, bottom: u.bottom }, tc); if (!tq) continue;
      const lines = new Set(rects.map(x => Math.round(x.top))).size;
      texts.push({ el: id, text: tt(n.textContent.replace(/\s+/g, ' ').trim(), cs), box: rel(tq), full: rel({ left: u.left, top: u.top, width: u.right - u.left, height: u.bottom - u.top }), font: font(cs), color: col(cs.color), opacity: op, lines, baseline: +(u.top - W.top + asc(cs)).toFixed(2),
        ellipsis: cs.textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth + 1 }); }
    if (el.isContentEditable && el.getAttribute('contenteditable') === 'true' && !el.textContent.trim() && el.dataset.ph) texts.push({ el: id, text: el.dataset.ph, placeholder: true, box: rel(vis), font: font(cs), color: col(cs.color), opacity: op, lines: 1 });
  }
  const grp = groups.map(g => { const els = g.spec ? [...document.querySelectorAll(g.spec)].filter(e => e.getClientRects().length && shown(e)) : [];
    return { name: g.name, info_only: !!g.info_only, boxes: els.map(e => rel(e.getBoundingClientRect())) }; });
  // Structure pairs named by any selector (not only #id): the element each one names, by dump id.
  const pairIds = {}; for (const sel of pairSels || []) { const e = document.querySelector(sel); if (e && ids.has(e)) pairIds[sel] = ids.get(e); }
  return { window: { w: W.width, h: W.height, radius: px(getComputedStyle(win).borderTopLeftRadius) }, mode: document.body.dataset.mode, mat: document.body.dataset.mat, groups: grp, elements, texts, icons, pairIds };
}

(async () => {
  // Unhinted glyphs everywhere: Linux headless Chromium otherwise rounds each advance to whole pixels and
  // Figtree comes out about 10% wide (I1); the Mac never hints, so both routes measure the same text.
  const browser = await chromium.launch({ args: ['--font-render-hinting=none'] });
  // A state may hold for some themes only (Luma's Frost is one light appearance).
  const states = (phone ? scenario.states.filter(s => (scenario.phone_states || []).includes(s.name)) : scenario.states).filter(s => !s.themes || s.themes.includes(theme));
  const meta = { app: scenario.app, theme, phone, url: base + scenario.spec.url, states: [] };
  for (const st of states) {
    // A state may ask for its own viewport (a maximized window fills the desk, so the desk sets its size).
    const vp = st.viewport || scenario.spec.viewport || { width: 1600, height: 1000 };
    const ctx = await browser.newContext({ viewport: vp, deviceScaleFactor: 1, colorScheme: theme, reducedMotion: 'reduce' });
    const page = await ctx.newPage();
    // Serve the Studio's files from disk when its root is known (the dev server drops bursts
    // of requests), and name every inline Lucide icon: v70 fetches icons/<name>.svg and inlines it.
    const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp', '.woff2': 'font/woff2', '.json': 'application/json' };
    await page.route(u => u.href.startsWith(base), async route => { const u = new URL(route.request().url()), local = path.join(studioRoot, decodeURIComponent(u.pathname));
      let body = fs.existsSync(local) && fs.statSync(local).isFile() ? fs.readFileSync(local) : null;
      if (body == null) { for (let i = 0; i < 4 && body == null; i++) { try { const r = await route.fetch(); if (r.status() >= 400) return route.fulfill({ response: r }); body = await r.body(); } catch (e) { await new Promise(r => setTimeout(r, 150 * (i + 1))); } } }
      if (body == null) return route.abort();
      if (/\/icons\/[^/]+\.svg$/.test(u.pathname)) body = Buffer.from(body.toString('utf8').replace('<svg', `<svg data-icon="${decodeURIComponent(u.pathname.split('/').pop().replace(/\.svg$/, ''))}"`));
      route.fulfill({ status: 200, contentType: TYPES[path.extname(u.pathname).toLowerCase()] || 'application/octet-stream', body }); });
    // A fixed clock (scenario.spec.clock, e.g. "2026-09-25T22:54:00") for pages that show the time.
    if (scenario.spec.clock) await page.clock.setFixedTime(new Date(scenario.spec.clock));
    // spec.init_script runs before the page's own scripts (a fixed Math.random, a frozen clock, a hook into an IIFE).
    for (const script of [].concat(scenario.spec.init_script || [])) await page.addInitScript(script);
    await page.goto(meta.url, { waitUntil: 'networkidle' });
    // The app's own window is up first (spec.ready, default the window); a surface that only exists after
    // the state's actions (a popover, a drawer, a pane named as the window) is waited for after them.
    // Studio is up (spec.ready, or its review chrome); each action waits for its own target, and the window
    // (which may be a popover or pane that only a click opens) is waited for after the actions.
    await page.waitForSelector(scenario.spec.ready || '.review', { state: 'attached' });
    await page.evaluate(() => document.fonts.ready);
    // Studio's own switch sets body[data-mode]; colorScheme alone does not.
    await page.evaluate(t => { const b = document.querySelector(`.review [data-set-mode="${t}"]`); if (!b) throw new Error('no Studio mode switch for ' + t); b.click(); }, theme);
    if (phone) await page.evaluate(() => document.querySelector('.review #phone, #phone').click());
    await page.waitForTimeout(500);
    // Pointer states (hover, press) keep the pointer where the state put it; the rest park it off the window.
    let pointer = false;
    const target = sel => sel && page.waitForSelector(sel, { state: 'attached', timeout: 15000 });
    for (const a of [...(scenario.spec.before || []), ...(st.spec || [])]) {
      if (a.waitFor) await page.waitForSelector(a.waitFor, { state: 'visible', timeout: 15000 });  // late content (Depot's monogram)
      await target(a.click || a.dblclick || a.hover || a.press || a.focus);
      if (a.click) await page.evaluate(s => { const e = document.querySelector(s); if (!e) throw new Error('no element ' + s); e.click(); }, a.click);
      if (a.dblclick) await page.evaluate(s => { const e = document.querySelector(s); if (!e) throw new Error('no element ' + s); e.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true })); }, a.dblclick);
      if (a.hover) { await page.mouse.move(2, 2); await page.hover(a.hover); pointer = true; }
      if (a.press) { await page.hover(a.press); await page.mouse.down(); pointer = true; }
      if (a.focus) { await page.keyboard.press('Shift'); await page.focus(a.focus); }
      if (a.key) await page.keyboard.press(a.key);
      if (a.eval) await page.evaluate(a.eval);
      await page.waitForTimeout(a.wait || 350);
    }
    await page.waitForSelector(st.window || scenario.spec.window, { state: 'visible', timeout: 15000 });
    // A window that is a part inside a scrolled page (Depot's monogram) is brought into view first.
    await page.locator(st.window || scenario.spec.window).first().scrollIntoViewIfNeeded();
    for (const sel of [].concat(scenario.spec.wait_for || [], st.wait_for || [])) await page.waitForSelector(sel, { state: 'visible', timeout: 15000 });
    // One accent for every scenario: Studio tints --acc with the wallpaper in light (168); v70's :root
    // --acc is 250, Luma blue, the kit's accent in both modes. A scenario may name another (spec.accent_hue).
    await page.evaluate(([sel, hue]) => { for (const e of [document.documentElement, document.body, ...document.querySelectorAll(sel)]) e.style.setProperty('--acc', String(hue)); },
      [st.window || scenario.spec.window, scenario.spec.accent_hue ?? 250]);
    if (!pointer) await page.mouse.move(2, 2);
    await page.waitForTimeout(700);
    const d = await page.evaluate(dump, { winSel: st.window || scenario.spec.window, groups: scenario.groups || [], exclude: scenario.spec.exclude || [], pairSels: (scenario.pairs || []).map(p => p.spec) });
    // Optional exact text probes report the faces Chromium actually used. CSS
    // computed font-family only names the requested family, not fallback runs.
    if ((scenario.spec.font_probes || []).length) {
      const cdp = await ctx.newCDPSession(page);
      await cdp.send('DOM.enable'); await cdp.send('CSS.enable');
      const dom = await cdp.send('DOM.getDocument');
      d.platformFonts = {};
      for (const probe of scenario.spec.font_probes) {
        const node = await cdp.send('DOM.querySelector', { nodeId: dom.root.nodeId, selector: probe.selector });
        if (node.nodeId) d.platformFonts[probe.text] = (await cdp.send('CSS.getPlatformFontsForNode', { nodeId: node.nodeId })).fonts;
      }
    }
    if (d.mode !== theme) throw new Error(`Studio mode is ${d.mode}, wanted ${theme}`);
    const box = await page.locator(st.window || scenario.spec.window).first().boundingBox();  // the first match, as the dump takes it
    await page.screenshot({ path: path.join(out, `spec-${st.name}.png`), clip: { x: box.x, y: box.y, width: Math.round(box.width), height: Math.round(box.height) } });
    d.state = st.name; d.about = st.about;
    fs.writeFileSync(path.join(out, `spec-${st.name}.json`), JSON.stringify(d));
    meta.states.push({ name: st.name, window: d.window, elements: d.elements.length, texts: d.texts.length, icons: d.icons.length });
    await ctx.close();
  }
  fs.writeFileSync(path.join(out, 'spec-meta.json'), JSON.stringify(meta, null, 1));
  await browser.close();
  console.log(`spec: ${meta.states.map(s => `${s.name} ${s.window.w}x${s.window.h} (${s.texts} texts, ${s.icons} icons)`).join(', ')}`);
})().catch(e => { console.error(e.message || e); process.exit(1); });
