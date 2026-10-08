#!/usr/bin/env node
// lumaui-conform, step 3: compare the GTK capture against the v70 spec.
//   node compare.js --scenario scenarios/contacts.json --dir OUT [--deviations accepted-deviations.json]
// Reads OUT/spec-<state>.{json,png} and OUT/gtk-<state>.{json,png}; writes
// OUT/REPORT.md, OUT/issues.json, OUT/inventory-<state>.json,
// OUT/side-<state>.png (spec | GTK | diff) and OUT/heat-<state>.png.
// Exit 0 = PASS, 1 = FAIL.
const fs = require('fs'), path = require('path');
const { chromium } = require('./lib/playwright');
const { matchesDeviation } = require('./lib/deviation-match');
const { calculatorShortcutPairs, acceptedShortcutLabels } = require('./lib/linux-shortcut-equivalence');
const { equivalentFlatGradient } = require('./lib/flat-gradient');
const { menuSurfacePairs } = require('./lib/menu-surface-pair');
const { calculatorDisplayTextBox, displayTrackingEm, calculatorDisplayPosition } = require('./lib/calc-display-tracking');
const { fontFallback } = require('./lib/font-fallback');

const args = process.argv.slice(2), opt = k => { const i = args.indexOf('--' + k); return i < 0 ? null : args[i + 1]; };
const scenario = JSON.parse(fs.readFileSync(opt('scenario'), 'utf8')), dir = opt('dir');
const deviations = opt('deviations') && fs.existsSync(opt('deviations')) ? JSON.parse(fs.readFileSync(opt('deviations'), 'utf8')).deviations : [];
const meta = JSON.parse(fs.readFileSync(path.join(dir, 'spec-meta.json'), 'utf8'));
const displayTracking = displayTrackingEm(scenario.app, __dirname);

// ── tolerances (README "What passes") ─────────────────────────────────────
const TOL = { pos: 2, size: 2, textWidthPct: 0.02, font: 0.5, dE: 3, radius: 1, majorPos: 8, majorDE: 10 };
const SEV = { critical: 4, major: 3, minor: 2, info: 1, accepted: 0 };

// ── colour ────────────────────────────────────────────────────────────────
const parseColor = s => { if (!s) return null; if (Array.isArray(s)) return { r: s[0], g: s[1], b: s[2], a: s[3] ?? 1 };
  const m = String(s).match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[\s,/]+/).filter(Boolean).map(parseFloat);
  return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
const over = (c, bg, extra = 1) => { const a = (c.a ?? 1) * extra; return { r: c.r * a + bg.r * (1 - a), g: c.g * a + bg.g * (1 - a), b: c.b * a + bg.b * (1 - a), a: 1 }; };
const hex = c => c ? '#' + [c.r, c.g, c.b].map(v => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, '0')).join('') + ((c.a ?? 1) < 0.995 ? ` @${(+c.a).toFixed(2)}` : '') : 'none';
function lab(c) { const f = v => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const R = f(c.r), G = f(c.g), B = f(c.b), X = (R * 0.4124 + G * 0.3576 + B * 0.1805) / 0.95047, Y = R * 0.2126 + G * 0.7152 + B * 0.0722, Z = (R * 0.0193 + G * 0.1192 + B * 0.9505) / 1.08883;
  const g = t => t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116; return [116 * g(Y) - 16, 500 * (g(X) - g(Y)), 200 * (g(Y) - g(Z))]; }
function dE00(c1, c2) { // CIEDE2000
  const [L1, a1, b1] = lab(c1), [L2, a2, b2] = lab(c2), rad = Math.PI / 180, C1 = Math.hypot(a1, b1), C2 = Math.hypot(a2, b2), Cb = (C1 + C2) / 2;
  const G = 0.5 * (1 - Math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7))), a1p = (1 + G) * a1, a2p = (1 + G) * a2, C1p = Math.hypot(a1p, b1), C2p = Math.hypot(a2p, b2);
  const h = (b, a) => { const x = Math.atan2(b, a) / rad; return x < 0 ? x + 360 : x; }, h1 = h(b1, a1p), h2 = h(b2, a2p);
  const dL = L2 - L1, dC = C2p - C1p; let dh = h2 - h1; if (C1p * C2p === 0) dh = 0; else if (dh > 180) dh -= 360; else if (dh < -180) dh += 360;
  const dH = 2 * Math.sqrt(C1p * C2p) * Math.sin(dh * rad / 2), Lb = (L1 + L2) / 2, Cbp = (C1p + C2p) / 2;
  let hb = h1 + h2; if (C1p * C2p !== 0) hb = Math.abs(h1 - h2) > 180 ? (h1 + h2 < 360 ? (h1 + h2 + 360) / 2 : (h1 + h2 - 360) / 2) : (h1 + h2) / 2;
  const T = 1 - 0.17 * Math.cos((hb - 30) * rad) + 0.24 * Math.cos(2 * hb * rad) + 0.32 * Math.cos((3 * hb + 6) * rad) - 0.2 * Math.cos((4 * hb - 63) * rad);
  const SL = 1 + 0.015 * (Lb - 50) ** 2 / Math.sqrt(20 + (Lb - 50) ** 2), SC = 1 + 0.045 * Cbp, SH = 1 + 0.015 * Cbp * T;
  const RT = -2 * Math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7)) * Math.sin(60 * Math.exp(-(((hb - 275) / 25) ** 2)) * rad);
  return Math.sqrt((dL / SL) ** 2 + (dC / SC) ** 2 + (dH / SH) ** 2 + RT * (dC / SC) * (dH / SH)); }

// The app icon (scenario.app_icon: spec <img> file name, GTK icon-name regex).
const appIconSrc = (scenario.app_icon || {}).spec_src || `${scenario.app}.svg`;
// Any Luma app's icon (the scenario's own, or another's on an Open-in button or a file face) is compared as an app icon box.
const appIconRe = new RegExp(`(?:${(scenario.app_icon || {}).gtk || '^application-x-executable$'})|^org\\.projectluma\\.|^io\\.luma\\.|^application-icon$`);
// Toolkit window controls: always info for now, wherever they sit (the phone title row has no spec box for them).
const WINDOW_CONTROLS = /window-(minimize|maximize|restore|close)|^(button|icon) "?(Minimize|Maximize|Zoom|Restore|Close)"?$|icon:window-/i;

// ── helpers ───────────────────────────────────────────────────────────────
const norm = t => (t || '').replace(/\s+/g, ' ').replace(/[…]$/, '').trim();
const alias = t => { t = t.replace(/ \(LumaUI preview\)$/, ''); return (scenario.text_aliases || {})[t] || t; };
const B = b => Array.isArray(b) ? { x: b[0], y: b[1], w: b[2], h: b[3] } : b;
const center = b => [b.x + b.w / 2, b.y + b.h / 2];
const inside = (b, o, m = 0) => { const [cx, cy] = center(b); return cx >= o.x - m && cx <= o.x + o.w + m && cy >= o.y - m && cy <= o.y + o.h + m; };
const dist = (a, b) => Math.abs(a.x - b.x) + Math.abs(a.y - b.y) + Math.abs(a.w - b.w) * 0.5 + Math.abs(a.h - b.h) * 0.5;
const f1 = v => (Math.round(v * 10) / 10).toString();
// A '-filled' glyph is the kit's form of v70's `svg.i { fill: currentColor }` on the same icon.
// A tooltip names its control and may add the shortcut ("Hide sidebar (F9)"); a C widget's accessible
// label is not readable from Python, so its tooltip stands in, less the shortcut (Settings 12).
const shortcutless = t => t ? t.replace(/\s*\((?:F\d{1,2}|(?:Ctrl|Shift|Alt|Super|⌘|⇧|⌥)[^)]*)\)$/, '') : t;
const iconName = n => (n || '').replace(/^lumaui-/, '').replace(/-symbolic$/, '').replace(/-filled$/, '');
const ROLE = { 'list-item': 'button', row: 'button', 'toggle-button': 'button', radio: 'button', 'column-header': 'button', 'menu-item': 'button', 'menu-item-radio': 'button', 'menu-item-checkbox': 'button', 'search-box': 'searchbox', 'text-box': 'textbox', 'check-box': 'checkbox', link: 'button' };
const CONTROL = new Set(['button', 'switch', 'searchbox', 'textbox', 'checkbox']);
const roleOf = r => ROLE[r] || r;
function greedy(A, Bs, key, cost, maxCost = Infinity) { // pair items with equal keys, nearest first
  const pairs = [], usedB = new Set(), byKey = new Map();
  Bs.forEach((b, j) => { const k = key(b); if (!byKey.has(k)) byKey.set(k, []); byKey.get(k).push(j); });
  const cand = []; A.forEach((a, i) => (byKey.get(key(a)) || []).forEach(j => cand.push([cost(a, Bs[j]), i, j])));
  cand.sort((p, q) => p[0] - q[0]); const usedA = new Set();
  for (const [c, i, j] of cand) { if (usedA.has(i) || usedB.has(j) || c > maxCost) continue; usedA.add(i); usedB.add(j); pairs.push([A[i], Bs[j]]); }
  return { pairs, lonelyA: A.filter((_, i) => !usedA.has(i)), lonelyB: Bs.filter((_, j) => !usedB.has(j)) };
}

// A component's label: the first text (or icon) inside it that is not inside a
// smaller component of its own (a row is "Ada Chen", not its avatar's "AC").
// Containers whose texts all belong to nested components get no label; the
// scenario's "pairs" compare those by name.
function labelMap(items, parentOf, isComp) {
  const direct = new Map();
  for (const [start, text] of items) for (let id = start; id != null; id = parentOf(id)) { if (!direct.has(id)) direct.set(id, text); if (isComp(id)) break; }
  return direct;
}

// ── spec model ────────────────────────────────────────────────────────────
function specModel(S) {
  const els = S.elements, byId = new Map(els.map(e => [e.id, e]));
  const texts = S.texts.map(t => ({ ...t, offscreen: !!t.offscreen, text: alias(norm(t.text)), aliased: alias(norm(t.text)) !== norm(t.text), box: B(t.box), color: parseColor(t.color), platformFonts: (S.platformFonts || {})[norm(t.text)] }));
  const icons = S.icons.map(i => ({ ...i, name: i.name, box: B(i.box), ink: i.ink ? B(i.ink) : B(i.box), color: parseColor(i.color) }));
  // The app's own icon is an <img> in v70 and a themed app icon in GTK: compared as a box.
  els.filter(e => e.tag === 'img' && (e.src === appIconSrc || e.src === `${scenario.app}.svg` || (e.src || '').endsWith(`-${scenario.app}.svg`) || /^luma-v3-[\w-]+\.svg$/.test(e.src || ''))).forEach(e => icons.push({ el: e.id, name: 'app-icon', box: B(e.visible), ink: B(e.visible), color: null, opacity: e.opacity }));
  const isComp = e => e.id !== 0 && e.visible.w >= 6 && e.visible.h >= 6 && !(e.tag === 'svg' || e.tag === 'img' && !e.cls) && (CONTROL.has(roleOf(e.role)) || /^menuitem(?:radio|checkbox)?$/.test(e.role) || e.paint.background || e.paint.backgroundImage || e.paint.border || (e.paint.boxShadow && !/^rgba\(0, 0, 0, 0\)/.test(e.paint.boxShadow)));
  const compIds = new Set(els.filter(isComp).map(e => e.id));
  const firstText = labelMap(texts.filter(t => !t.offscreen && t.el != null).map(t => [t.el, t.text]), id => byId.get(id)?.parent, id => compIds.has(id));
  const firstIcon = labelMap(icons.map(i => [i.el, i.name]), id => byId.get(id)?.parent, id => compIds.has(id));
  // A text field drawn as a painted wrapper (label.srch) around a bare <input> is one GTK entry.
  const kids = new Map(); els.forEach(e => { if (e.parent != null) { if (!kids.has(e.parent)) kids.set(e.parent, []); kids.get(e.parent).push(e); } });
  const descControls = id => (kids.get(id) || []).flatMap(k => [...(CONTROL.has(k.role) ? [k] : []), ...descControls(k.id)]);
  const merged = new Map();
  for (const e of els.filter(e => (e.role === 'searchbox' || e.role === 'textbox') && (e.tag === 'input' || e.tag === 'textarea'))) {
    for (let a = byId.get(e.parent), n = 0; a && n < 2; a = byId.get(a.parent), n++) if (isComp(a) && !CONTROL.has(a.role)) { if (descControls(a.id).filter(c => c.tag !== 'button' || c.visible.w > 0).length === 1 || descControls(a.id).every(c => c === e || c.tag === 'button')) { merged.set(a.id, e); compIds.delete(e.id); } break; }
  }
  const comps = els.filter(e => isComp(e) && compIds.has(e.id) || merged.has(e.id)).map(e => merged.has(e.id) ? { el: e, role: merged.get(e.id).role, label: norm(merged.get(e.id).placeholder || merged.get(e.id).value || ''), box: B(e.visible), full: B(e.box), paint: e.paint, opacity: e.opacity } : ({ el: e, role: e.role, label: norm(e.label || firstText.get(e.id) || (e.value || e.placeholder) || (firstIcon.get(e.id) ? 'icon:' + firstIcon.get(e.id) : '')), box: B(e.visible), full: B(e.box), paint: e.paint, opacity: e.opacity }));
  return { texts, icons, comps, groups: S.groups, window: S.window };
}

// ── GTK model ─────────────────────────────────────────────────────────────
function gtkModel(G) {
  const ws = G.widgets, byId = new Map(ws.map(w => [w.id, w])), vis = w => !w.offscreen;
  const box = w => B(w.visible || w.box);
  const textOps = new Map(); G.ops.filter(o => o.k === 'text' && o.widget != null && !o.hidden).forEach(o => { if (!textOps.has(o.widget)) textOps.set(o.widget, []); textOps.get(o.widget).push(o); });
  const texts = [];
  for (const w of ws) {
    let t = null, placeholder = false;
    if ((w.type === 'GtkLabel' || w.css === 'label') && w.text) t = w.text;
    else if (w.css === 'text' && w.text) t = w.text; else continue;
    if (!norm(t)) continue;
    const allOps = textOps.get(w.id) || [], ops = allOps.filter(o => (o.op ?? 1) > 0.02), b = B(w.box);
    if (!ops.length && !w.offscreen && G.ops.some(o => o.k === 'text')) continue; // never drawn (CSS opacity 0 skips the snapshot), so not visible
    let x, top, width, height, font = w.font, color = parseColor(w.color), lines = w.lines || 1, baseline = null;
    if (ops.length) { x = Math.min(...ops.map(o => o.x)); top = Math.min(...ops.map(o => o.baseline - o.ascent)); baseline = Math.min(...ops.map(o => o.baseline));
      const bottom = Math.max(...ops.map(o => o.baseline + o.descent)); height = bottom - top; lines = new Set(ops.map(o => Math.round(o.baseline))).size;
      // A scroller's edge culls the lines it clips from the snapshot; the layout still has them, as the spec's count does.
      if (w.visible && w.lines && (w.visible[3] < w.box[3] - 0.5 || w.visible[2] < w.box[2] - 0.5)) lines = Math.max(lines, w.lines);
      width = w.layout ? Math.min(w.layout[2], b.w) : Math.max(...ops.map(o => o.box[0] + o.box[2])) - x; font = ops[0].font; color = parseColor(ops[0].color); color && (color.a = (color.a ?? 1) * (ops[0].op ?? 1)); }
    else if (w.layout) { width = Math.min(w.layout[2], b.w); height = w.layout[3]; x = b.x + (w.xalign ?? 0.5) * (b.w - width); top = b.y + (b.h - height) / 2; }
    else { x = b.x; top = b.y; width = b.w; height = b.h; }
    if (color && w.opacity < 1 && !ops.length) color.a = (color.a ?? 1) * w.opacity;
    const textBox = calculatorDisplayTextBox(scenario.app, w, ws,
      { x, y: top, w: width, h: height }, displayTracking);
    texts.push({ w, text: alias(norm(t)), box: textBox, baseline, font, color, lines, offscreen: !!w.offscreen, placeholder });
  }
  // Placeholders of entries: GtkText draws them through a child label (already above).
  const glyphs = G.ops.filter(o => (o.k === 'glyph' || o.k === 'texture') && !o.hidden);
  // Glyphs are symbolic icons (Lucide, the kit's); an app's own full-colour icon is a picture, as the spec's <img> is.
  // App icons pair with the spec's <img> app icons as boxes (appIconRe); other full-colour pictures are left out.
  const icons = ws.filter(w => (w.css === 'image' || appIconRe.test(w.icon || '')) && w.icon && vis(w) && (/-symbolic$/.test(w.icon) || appIconRe.test(w.icon))).map(w => { const b = B(w.box), gs = glyphs.filter(o => inside(B(o.box), b, 1)), g = gs[0];
    if (!gs.length || gs.every(o => (o.op ?? 1) <= 0.02)) return null; // never drawn, or at CSS opacity 0 (v70's hover-only copy buttons)
    const c = g && g.color ? parseColor(g.color) : parseColor(w.color); if (c && g) c.a = (c.a ?? 1) * (g.op ?? 1);
    const ink = gs.length ? gs.map(o => B(o.geo || o.box)).reduce((a, o) => { const x = Math.min(a.x, o.x), y = Math.min(a.y, o.y); return { x, y, w: Math.max(a.x + a.w, o.x + o.w) - x, h: Math.max(a.y + a.h, o.y + o.h) - y }; }) : b;
    // A scroller's edge cuts an icon as it does in the spec (whose box and ink are the visible part): clip to what shows.
    const vb = w.visible ? B(w.visible) : null, cut = (r) => { if (!vb) return r; const x = Math.max(r.x, vb.x), y = Math.max(r.y, vb.y); return { x, y, w: Math.max(0, Math.min(r.x + r.w, vb.x + vb.w) - x), h: Math.max(0, Math.min(r.y + r.h, vb.y + vb.h) - y) }; };
    const app = appIconRe.test(w.icon); return { w, name: app ? 'app-icon' : iconName(w.icon), generic: app && /^application-x-executable$/.test(w.icon), box: cut(b), ink: cut(ink), clipped: !!vb && (vb.w < b.w - 0.5 || vb.h < b.h - 0.5), cutSide: vb ? { t: vb.y > b.y + 0.5, b: vb.y + vb.h < b.y + b.h - 0.5, l: vb.x > b.x + 0.5, r: vb.x + vb.w < b.x + b.w - 0.5 } : null, color: app ? null : c }; }).filter(Boolean);
  // Paint ops that sit exactly on a widget are that widget's own CSS box.
  const paintOps = G.ops.filter(o => ['fill', 'gradient', 'border', 'shadow'].includes(o.k) && !o.hidden);
  const near = (o, b, m) => Math.abs(o.x - b.x) <= m && Math.abs(o.y - b.y) <= m && Math.abs(o.w - b.w) <= m && Math.abs(o.h - b.h) <= m;
  // A decorative widget with the same box as a control and no text of its own (ModeSwitch's sliding
  // chip, v70 i.ind) owns the paint at that box; the control gets none of it.
  const decor = ws.filter(w => vis(w) && !w.text && /indicator/.test((w.classes || []).join(' ')));
  // Containers that draw nothing of their own (a scroller, an overlay, a stack, a picture) never take the paint of
  // an ancestor with the same box: the island's fill, radius and shadow stay the island's.
  const PASSIVE = new Set(['scrolledwindow', 'viewport', 'overlay', 'stack', 'picture', 'revealer']);
  const ancestorAt = (w, b) => { for (let a = byId.get(w.parent), n = 0; a && n < 6; a = byId.get(a.parent), n++) if (near(B(a.box), b, 1.5)) return true; return false; };
  const paintOf = w => { const b = B(w.box); if (!decor.includes(w) && decor.some(d => d !== w && near(B(d.box), b, 1.5))) return {};
    if (PASSIVE.has(w.css) && ancestorAt(w, b)) return {};
    let own = paintOps.filter(o => near(B(o.box), b, 1.5));
    // An edge (a border, an inset ring) at a box an ancestor exactly fills is the ancestor's: a square row
    // filling its 16-round card does not wear the card's ring (Settings' single-row .cfcard). A fill there
    // still stands for the child too (a button filling its painted well).
    let ancestors = 0;
    for (let a = byId.get(w.parent), n = 0; a && n < 6; a = byId.get(a.parent), n++) if (near(B(a.box), b, 1.5)) ancestors++;
    if (ancestors) own = own.filter(o => o.k !== 'border' && !(o.k === 'shadow' && o.inset));
    const p = {}; for (const o of own) { if (o.k === 'fill' && !o.inset) p.fill = p.fill || o; if (o.k === 'gradient') p.gradient = o; if (o.k === 'border' && o.widths.some(v => v > 0) && o.colors.some(c => c && c[3] > 0.01)) p.border = o;
      if (o.k === 'shadow' && !o.inset && o.color[3] > 0.02 && (o.blur > 0.5 || o.spread > 0.5)) (p.shadows = p.shadows || []).push(o);
      // An inset ring (spread, no blur, no offset: v70's `inset 0 0 0 1px var(--hair)`) is the surface's edge, as a border is.
      if (o.k === 'shadow' && o.inset && o.color[3] > 0.02 && o.spread >= 0.5 && o.blur <= 0.5 && Math.abs(o.dx) < 0.5 && Math.abs(o.dy) < 0.5) p.ring = p.ring || o; }
    const rc = G.ops.find(o => o.k === 'rclip' && near(B(o.box), b, 1.5)); p.radius = (p.fill && p.fill.radius) || (p.gradient && p.gradient.radius) || (p.border && p.border.radius) || (p.ring && p.ring.radius) || (rc && rc.radius) || null; return p; };
  const liveOps = G.ops.filter(o => !o.hidden && (o.op ?? 1) > 0.02 && o.k !== 'rclip');
  const shows = c => c.paint.fill || c.paint.gradient || c.paint.border || c.paint.ring || c.paint.shadows ? (c.paint.fill || c.paint.gradient || c.paint.border || c.paint.ring || c.paint.shadows[0]).op > 0.02 : liveOps.some(o => inside(B(o.box), B(c.w.box)));
  // GtkText inside an entry is the entry's own text area, not a control of its own (its selection is paint).
  const cands = ws.filter(w => w.parent != null && vis(w) && w.box[2] >= 6 && w.box[3] >= 6 && !((w.css === 'text' || w.css === 'textview') && byId.get(w.parent)?.css === 'entry' && !(byId.get(w.parent)?.classes || []).includes('lumaui-bar-search'))).map(w => ({ w, role: w.activatable === false ? 'generic' : roleOf(w.role), paint: paintOf(w) }))
    .filter(c => (CONTROL.has(c.role) || c.paint.fill || c.paint.gradient || c.paint.border || c.paint.ring || c.paint.shadows) && shows(c));
  // A bare text field inside a painted well (SidebarFoot's search, BarSearch) is one control, as the spec
  // merges label.srch around its <input>: the field takes the well's box and paint, and the well is not a
  // control of its own.
  // (A GtkEntry in a painted row is the same case: the action center's form fields, v70 .lacf input.)
  for (const c of cands.filter(c => (c.role === 'searchbox' || c.role === 'textbox') && (c.w.css === 'text' || c.w.css === 'textview' || (c.w.css === 'entry' && (byId.get(c.w.parent)?.classes || []).includes('lumaui-ac-field'))))) {
    for (let a = byId.get(c.w.parent), n = 0; a && n < 2; a = byId.get(a.parent), n++) {
      const well = cands.find(x => x.w === a && !CONTROL.has(x.role));
      if (well) { c.w = { ...c.w, box: well.w.box, visible: well.w.visible }; c.paint = well.paint; cands.splice(cands.indexOf(well), 1); break; }
    }
  }
  const compIds = new Set(cands.map(c => c.w.id));
  const firstText = labelMap(texts.filter(t => !t.offscreen).map(t => [t.w.id, t.text]), id => byId.get(id)?.parent, id => compIds.has(id));
  const firstIcon = labelMap(icons.map(i => [i.w.id, i.name]), id => byId.get(id)?.parent, id => compIds.has(id));
  const comps = cands
    .map(c => ({ ...c, label: norm(c.w.a11y_label || shortcutless(c.w.tooltip) || c.w.button_label || firstText.get(c.w.id) || (c.w.placeholder || '') || (firstIcon.get(c.w.id) ? 'icon:' + firstIcon.get(c.w.id) : '')), box: box(c.w), full: B(c.w.box), name: c.w.name }));
  return { texts, icons, comps, paintOf, widgets: ws, byName: new Map(ws.filter(w => w.name && !w.name.startsWith('Gtk')).map(w => [w.name, w])), window: G.window, notes: G.notes || [], overflow: G.overflow || [], ops: G.ops };
}

// ── checks ────────────────────────────────────────────────────────────────
function compareState(st, S, G, samples) {
  const issues = [], groups = S.groups;
  const frac = (b, o) => { const x = Math.max(0, Math.min(b.x + b.w, o.x + o.w) - Math.max(b.x, o.x)), y = Math.max(0, Math.min(b.y + b.h, o.y + o.h) - Math.max(b.y, o.y)); return b.w * b.h > 0 ? x * y / (b.w * b.h) : (inside(b, o) ? 1 : 0); };
  const groupOf = b => { for (const g of groups) for (const gb of g.boxes) if (frac(b, gb) >= 0.6) return g; return { name: 'window', info_only: false }; };
  const add = (sev, b, subject, prop, spec, gtk, fix, extra = {}) => { const g = groupOf(b); issues.push({ state: st, sev: (g.info_only || WINDOW_CONTROLS.test(subject)) && sev !== 'info' ? 'info' : sev, group: g.name, subject, prop, spec, gtk, fix, box: b, ...extra }); };
  const posSev = d => d > TOL.majorPos ? 'major' : 'minor';
  const bg = (which, b) => samples[which](b);

  // Window
  // A window is sized to the spec's; one that sizes itself (a part as the window, e.g. a menu card) may round
  // a fractional spec size, so only a difference beyond the position tolerance fails.
  if (Math.abs(G.window.w - S.window.w) > TOL.pos || Math.abs(G.window.h - S.window.h) > TOL.pos) add('critical', { x: 0, y: 0, w: 1, h: 1 }, 'window', 'size', `${S.window.w}×${S.window.h}`, `${G.window.w}×${G.window.h}`, `the window must open at ${S.window.w}×${S.window.h} (GTK ended at ${G.window.w}×${G.window.h})`);
  // A state may know its fixture is too wide for it (the gallery's own pages at 460): it reports, not fails.
  const overflowSev = (scenario.states || []).find(x => x.name === st)?.overflow === 'info' ? 'info' : 'major';
  (G.overflow || []).forEach(o => add(overflowSev, { x: 0, y: 0, w: 1, h: 1 }, 'window', 'overflow', 'fits the window', o, `content must fit the window: ${o} (a kit part's minimum width, or a label that neither ellipsizes nor wraps)`));
  G.notes.forEach(n => add('info', { x: 0, y: 0, w: 1, h: 1 }, 'harness', 'note', '', n.split('\n')[0], n.split('\n')[0]));

  // Components first: their offsets explain their children's.
  // 1. scenario.pairs: structure with no text of its own (spec #id -> GTK widget name).
  // 2. the rest by label (and role); 3. leftovers by near-identical bounds (a wrapper and its control).
  const moved = []; // [specBox, dx, dy]
  const rolesTxt = c => c.role === 'generic' || c.role === 'region' || c.role === 'main' || c.role === 'navigation' ? (c.paint && (c.paint.boxShadow || c.paint.border) ? 'card' : 'box') : c.role;
  const checkPair = (s, g, name) => {
    const sb = s.full, gb = g.full;
    const dx = gb.x - sb.x, dy = gb.y - sb.y, dw = gb.w - sb.w, dh = gb.h - sb.h;
    moved.push([s.box, dx, dy]);
    if (Math.abs(dx) > TOL.pos || Math.abs(dy) > TOL.pos) add(posSev(Math.max(Math.abs(dx), Math.abs(dy))), s.box, name, 'position', `${f1(sb.x)},${f1(sb.y)}`, `${f1(gb.x)},${f1(gb.y)}`, `${name}: at ${f1(sb.x)},${f1(sb.y)} in spec, ${f1(gb.x)},${f1(gb.y)} in GTK (move ${dx > 0 ? 'left' : 'right'} ${f1(Math.abs(dx))}, ${dy > 0 ? 'up' : 'down'} ${f1(Math.abs(dy))})`, { mag: Math.hypot(dx, dy) });
    if (Math.abs(dw) > TOL.size || Math.abs(dh) > TOL.size) add(posSev(Math.max(Math.abs(dw), Math.abs(dh))), s.box, name, 'size', `${f1(sb.w)}×${f1(sb.h)}`, `${f1(gb.w)}×${f1(gb.h)}`, `${name}: ${f1(sb.w)}×${f1(sb.h)} in spec, ${f1(gb.w)}×${f1(gb.h)} in GTK`, { mag: Math.hypot(dw, dh) });
    // Surface: background colour (sampled), gradient, radius, border, shadow.
    const sp = s.paint, gp = g.paint;
    const sFlat = sp.background && !/gradient/.test(sp.backgroundImage || ''), sGrad = /gradient/.test(sp.backgroundImage || '');
    // A surface's colour is its own paint when both sides paint it opaque: the pixel mode of the box
    // follows whatever covers most of it (a chosen chip wider in one than the other: Settings' .seg).
    const sFill = sp.background ? parseColor(sp.background) : null, gFill = gp.fill ? parseColor(gp.fill.color) : null;
    const opaque = c => c && (c.a ?? 1) >= 0.99;
    // Pixels that already agree settle it (Settings' accent swatches, whose paint data the capture reads
    // stale); only when they differ does each side's own paint decide.
    const px = { s: bg('spec', s.box), g: bg('gtk', g.box) };
    const byPaint = opaque(sFill) && opaque(gFill) && !(px.s && px.g && dE00(px.s, px.g) <= TOL.dE);
    // Pixels that differ over the whole box may agree around its edge: a surface around a chip that
    // covers more of it in one than the other (a two-choice switch's well, in light, where its paint is
    // translucent and cannot decide).
    const ex = { s: samples.specEdge(s.box), g: samples.gtkEdge(g.box) };
    const byEdge = !byPaint && px.s && px.g && dE00(px.s, px.g) > TOL.dE && ex.s && ex.g && dE00(ex.s, ex.g) <= TOL.dE;
    const cs = byPaint ? sFill : byEdge ? ex.s : px.s, cg = byPaint ? gFill : byEdge ? ex.g : px.g;
    const equivalentFlat = equivalentFlatGradient(sp.backgroundImage, gp.gradient, cs, cg, dE00, TOL.dE);
    if (sGrad !== !!gp.gradient && !equivalentFlat && (sp.background || sGrad || gp.fill || gp.gradient)) add('major', s.box, name, 'gradient', sGrad ? 'gradient' : 'flat', gp.gradient ? 'gradient' : 'flat',
      gp.gradient ? `${name}: GTK paints a gradient (${gp.gradient.stops.map(x => hex(parseColor(x[1]))).join(' → ')}); the spec is flat${sFlat ? ' ' + hex(parseColor(sp.background)) : ''}` : `${name}: the spec paints a gradient; GTK is flat`);
    if ((sp.background || sGrad) && !(gp.fill || gp.gradient)) add('major', s.box, name, 'background', hex(parseColor(sp.background)), 'none', `${name}: the spec fills it (${sp.background ? hex(parseColor(sp.background)) : 'gradient'}); GTK draws no background`);
    if (!(sp.background || sGrad) && (gp.fill || gp.gradient) && !CONTROL.has(s.role)) add('minor', s.box, name, 'background', 'none', hex(parseColor((gp.fill || {}).color)), `${name}: GTK fills it (${hex(parseColor((gp.fill || {}).color))}); the spec has no background`);
    const sr = (sp.radius || [0])[0], gr = (gp.radius || [0])[0];
    if ((sp.background || sGrad || sp.border || gp.fill || gp.border || gp.ring) && Math.abs(Math.min(sr, Math.min(sb.w, sb.h) / 2) - Math.min(gr, Math.min(gb.w, gb.h) / 2)) > TOL.radius)
      add(Math.abs(sr - gr) > 3 ? 'major' : 'minor', s.box, name, 'radius', f1(sr), f1(gr), `${name}: corner radius ${f1(Math.min(sr, sb.h / 2))} px in spec, ${f1(Math.min(gr, gb.h / 2))} px in GTK`);
    // An edge is a border or an inset ring, either way round (GTK can draw v70's ring as either).
    const sRing = parseShadows(sp.boxShadow).find(x => x.inset && x.color && x.color.a > 0.02 && x.spread >= 0.5 && x.blur <= 0.5 && Math.abs(x.dx) < 0.5 && Math.abs(x.dy) < 0.5);
    const sEdge = sp.border ? { w: Math.max(...sp.border.widths), c: parseColor(sp.border.colors[0]) } : sRing ? { w: sRing.spread, c: sRing.color } : null;
    const gEdge = gp.border ? { w: Math.max(...gp.border.widths), c: parseColor(gp.border.colors[0]) } : gp.ring ? { w: gp.ring.spread, c: parseColor(gp.ring.color) } : null;
    // A translucent hairline's colour is mostly what it sits on; only an opaque edge's colour is compared.
    if (sEdge && gEdge && Math.abs(sEdge.w - gEdge.w) <= 0.5 && sEdge.c && gEdge.c && (sEdge.c.a ?? 1) > 0.5 && (gEdge.c.a ?? 1) > 0.5) {
      const d = dE00(sEdge.c, gEdge.c); if (d > TOL.dE) add('minor', s.box, name, 'edge colour', hex(sEdge.c), hex(gEdge.c), `${name}: edge ${hex(sEdge.c)} in spec, ${hex(gEdge.c)} in GTK (ΔE ${f1(d)})`, { mag: d });
    }
    // Only a border is required to match; a ring stands in for a border on the other side (never demanded).
    const sbw = sp.border ? sEdge.w : gp.border && sRing ? sEdge.w : 0, gbw = gp.border ? gEdge.w : sp.border && gp.ring ? gEdge.w : 0;
    if (Math.abs(sbw - gbw) > 0.5) add('minor', s.box, name, 'border', sbw ? `${sbw}px ${hex(sEdge.c)}` : 'none', gbw ? `${gbw}px ${hex(gEdge.c)}` : 'none', `${name}: edge ${sbw ? sbw + ' px ' + hex(sEdge.c) : 'none'} in spec, ${gbw ? gbw + ' px ' + hex(gEdge.c) : 'none'} in GTK`);
    const sSh = (sp.boxShadow || '').split(/,(?![^()]*\))/).map(x => x.trim()).filter(x => x && !/inset/.test(x)).map(x => { const n = x.replace(/rgba?\([^)]*\)/, '').trim().split(/\s+/).map(parseFloat); const c = parseColor(x); return { blur: n[2] || 0, spread: n[3] || 0, a: c ? c.a : 0 }; }).filter(x => x.a > 0.02 && (x.blur > 0.5 || x.spread > 0.5));
    const gSh = gp.shadows || [], sMax = Math.max(0, ...sSh.map(x => x.blur)), gMax = Math.max(0, ...gSh.map(x => x.blur));
    if (!!sSh.length !== !!gSh.length || Math.abs(sMax - gMax) > 4) add(Math.abs(sMax - gMax) > 8 || !!sSh.length !== !!gSh.length ? 'major' : 'minor', s.box, name, 'shadow', sSh.length ? `${sSh.length} (blur ≤${sMax})` : 'none', gSh.length ? `${gSh.length} (blur ≤${gMax})` : 'none',
      !sSh.length ? `${name}: GTK drops a shadow (blur ${gMax}); the spec has none (it sits in, not floating)` : !gSh.length ? `${name}: the spec has a shadow (blur ${sMax}); GTK has none` : `${name}: shadow blur ${sMax} in spec, ${gMax} in GTK`);
    if (cs && cg && (sp.background || sGrad || gp.fill || gp.gradient)) { const d = dE00(cs, cg); if (d > TOL.dE) add(d > TOL.majorDE ? 'major' : 'minor', s.box, name, 'surface colour', hex(cs), hex(cg), `${name}: surface ${hex(cs)} in spec, ${hex(cg)} in GTK (ΔE ${f1(d)})`, { mag: d }); }
  };
  const pairedS = new Set(), pairedG = new Set();
  for (const p of scenario.pairs || []) {
    // "name>css": the first descendant with that CSS name (a popover's own "contents" node, inside its shadow).
    const [gName, gCss] = p.gtk.split('>'), gTop = G.byName.get(gName);
    const descend = (w, css) => { const q = [w]; while (q.length) { const x = q.shift(); if (x !== w && x.css === css) return x; G.widgets.filter(c => c.parent === x.id).forEach(c => q.push(c)); } return null; };
    const pid = (S.raw.pairIds || {})[p.spec], sEl = pid != null ? S.raw.elements.find(e => e.id === pid) : S.raw.elements.find(e => e.elId && ('#' + e.elId) === p.spec), gW = gTop && gCss ? descend(gTop, gCss) : gTop, nm = p.name || p.gtk;
    if (!sEl) continue;
    if (!gW || gW.offscreen) { add('critical', B(sEl.visible), nm, 'missing', 'present', 'absent', `${nm}: no visible widget named ${p.gtk} in GTK`); continue; }
    const sc = S.comps.find(c => c.el === sEl) || { el: sEl, role: sEl.role, paint: sEl.paint, box: B(sEl.visible), full: B(sEl.visible) };
    let gT = gW; const painted = w => { const p = G.paintOf(w); return p.fill || p.gradient || p.border || p.shadows; };
    // An unpainted widget stands for its painted child only when the spec element is painted itself; a
    // bare spec box (v70 .cfside) pairs with the bare widget named for it.
    const sPainted = !!(sEl.paint && (sEl.paint.background || sEl.paint.backgroundImage || sEl.paint.border || sEl.paint.boxShadow));
    if (!painted(gW) && sPainted) { const q = [gW]; while (q.length) { const w = q.shift(); if (w !== gW && painted(w) && !w.offscreen && w.box[2] * w.box[3] >= 0.8 * gW.box[2] * gW.box[3]) { gT = w; break; } G.widgets.filter(c => c.parent === w.id).forEach(c => q.push(c)); } }
    const gc = G.comps.find(c => c.w === gT) || { w: gT, role: roleOf(gT.role), paint: G.paintOf(gT), box: B(gT.visible || gT.box) };
    checkPair({ ...sc, full: B(sEl.visible) }, { ...gc, full: B(gT.visible || gT.box) }, nm); pairedS.add(sc.el); pairedG.add(gT);
  }
  // Match the painted popover surface before text labels. Otherwise a DOM
  // menu inherited from its "Scientific" child can pair with the 36px GTK
  // row instead of the popover's 200×167 contents box.
  for (const [s, w] of menuSurfacePairs(S.comps, G.widgets, pairedS, pairedG)) {
    const g = G.comps.find(c => c.w === w) || { w, paint: G.paintOf(w), box: B(w.visible || w.box) };
    checkPair(s, { ...g, full: B(w.visible || w.box) }, `menu "${s.label}"`);
    pairedS.add(s.el); pairedG.add(w);
  }
  const compRes = greedy(S.comps.filter(c => c.label && !pairedS.has(c.el)), G.comps.filter(c => c.label && !pairedG.has(c.w)), c => c.label.toLowerCase(), (a, b) => dist(a.full, b.full) + (roleOf(a.role) === b.role ? 0 : 40), 600);
  for (const [s, g] of compRes.pairs) checkPair(s, g, `${rolesTxt(s)} "${s.label}"`);
  const takenG = new Set(compRes.pairs.map(p => p[1]));
  const near3 = (a, b) => Math.abs(a.x - b.x) <= 3 && Math.abs(a.y - b.y) <= 3 && Math.abs(a.w - b.w) <= 3 && Math.abs(a.h - b.h) <= 3;
  const lonelyS = [];
  for (const s of compRes.lonelyA) {
    // Composite labels can have a different accessible name while still
    // occupying the exact same control. This geometry fallback already
    // checks its visual properties; retain the pair for feature inventory.
    // Or a name that goes on past the spec's first words (a live chip names its time too: "Paused 0:00").
    const g = G.comps.find(c => (near3(c.full, s.full) || (s.label && c.label.toLowerCase().startsWith(s.label.toLowerCase() + ' ') && dist(c.full, s.full) <= 8)) && !pairedG.has(c.w) && !takenG.has(c));
    if (g) { checkPair(s, g, `${rolesTxt(s)} "${s.label}"`); compRes.pairs.push([s, g]); takenG.add(g); }
    else lonelyS.push(s);
  }
  compRes.lonelyA = lonelyS; compRes.lonelyB = compRes.lonelyB.filter(g => !takenG.has(g) && !S.comps.some(s => near3(s.full, g.full)));
  for (const s of compRes.lonelyA) {
    if (!s.label) continue; const has = G.texts.some(t => t.text.toLowerCase() === s.label.toLowerCase()) || G.icons.some(i => 'icon:' + i.name === s.label);
    const name = `${rolesTxt(s)} "${s.label}"`;
    add(CONTROL.has(s.role) && !has ? 'critical' : 'major', s.box, name, has ? 'not a surface' : 'missing', 'present', has ? 'content only' : 'absent',
      has ? `${name}: GTK shows its content but not the ${CONTROL.has(s.role) ? s.role : 'card/surface'} itself (${f1(s.full.w)}×${f1(s.full.h)}${s.paint.background ? ', ' + hex(parseColor(s.paint.background)) : ''}${s.paint.radius ? ', radius ' + s.paint.radius[0] : ''})` : `${name}: missing in GTK`);
  }
  // The identity (icon, name, chevron) opens the app's menu: v70 draws it as a label with a chevron, LumaUI's
  // frame makes it the menu button it implies (F0). Its control is window chrome, reported as info.
  const gById = new Map(G.widgets.map(w => [w.id, w]));
  const inIdentity = w => { for (let a = w, n = 0; a && n < 4; a = gById.get(a.parent), n++) if ((a.classes || []).includes('luma-identity-button')) return true; return false; };
  for (const g of compRes.lonelyB) { if (!g.label || !CONTROL.has(g.role)) continue; add(inIdentity(g.w) ? 'info' : 'minor', g.box, `${g.role} "${g.label}"`, 'extra', 'absent', 'present', `${g.role} "${g.label}": GTK has it, the spec does not${inIdentity(g.w) ? ' (the identity menu, window chrome)' : ''}`); }
  const movedWith = b => moved.filter(([mb]) => inside(b, mb)).sort((p, q) => p[0].w * p[0].h - q[0].w * q[0].h)[0];

  // Texts
  const tRes = greedy(S.texts, G.texts, t => t.text.toLowerCase(), (a, b) => Math.abs(a.box.x - b.box.x) + Math.abs(a.box.y - b.box.y) + (a.offscreen !== b.offscreen ? 2000 : 0), 2400);
  for (const [s, g] of tRes.pairs) {
    const nm = `"${s.text.length > 40 ? s.text.slice(0, 39) + '…' : s.text}"`;
    if (s.offscreen && g.offscreen) continue;
    // A text one side shows only a sliver of at a scroller's edge (under 30% of it) and the other has just
    // scrolled out: the lists before it differ by a pixel or two, so it's info, as a sliver missing is.
    const sliverOf = t => { const f = t.full ? B(t.full) : t.w && t.w.visible ? B(t.w.box) : null, v = t.w && t.w.visible ? B(t.w.visible) : t.box; return !t.offscreen && f && f.w > 0 && f.h > 0 && (v.w < 0.3 * f.w || v.h < 0.3 * f.h); };
    if (s.offscreen !== g.offscreen && (sliverOf(s) || sliverOf(g))) { add('info', s.offscreen ? g.box : s.box, `text ${nm}`, 'sliver', s.offscreen ? 'scrolled out' : 'a sliver', g.offscreen ? 'scrolled out' : 'a sliver', `text ${nm}: ${s.offscreen ? 'GTK' : 'the spec'} shows only a sliver of it at a scroller's edge; the other has scrolled it out`); continue; }
    if (s.offscreen !== g.offscreen) { add('minor', s.offscreen ? g.box : s.box, `text ${nm}`, 'visibility', s.offscreen ? 'scrolled out' : 'visible', g.offscreen ? 'scrolled out/clipped' : 'visible', `text ${nm}: ${s.offscreen ? 'visible in GTK but scrolled out in spec' : 'visible in spec but scrolled out or clipped in GTK'} (the list above it has a different height)`); continue; }
    const sf = s.font, gf = g.font || {};
    const fallback = fontFallback(sf.family, sf.weight, gf.family, gf.weight, s.platformFonts);
    if (gf.size != null && Math.abs(sf.size - gf.size) > TOL.font) add('major', s.box, `text ${nm}`, 'font size', `${sf.size}px`, `${gf.size}px`, `text ${nm}: ${sf.size}px/${sf.weight} in spec, ${gf.size}px/${gf.weight} in GTK`);
    else if (gf.weight != null && sf.weight !== gf.weight && !fallback.fallbackRegularWeight) add('major', s.box, `text ${nm}`, 'font weight', `${sf.weight}`, `${gf.weight}`, `text ${nm}: weight ${sf.weight} in spec, ${gf.weight} in GTK`);
    if (gf.family && sf.family && !fallback.familyMatch) add('major', s.box, `text ${nm}`, 'font family', fallback.expectedFamily, gf.family, `text ${nm}: ${fallback.expectedFamily} in spec, ${gf.family} in GTK`);
    // An aliased text ("Just now" in the spec, the time in GTK) is a different string: its width differs by design,
    // so it is placed by whichever edge it keeps (start or end).
    const dxl = g.box.x - s.box.x, dxr = (g.box.x + g.box.w) - (s.box.x + s.box.w);
    const position = calculatorDisplayPosition(scenario.app, s, g, G.widgets);
    const dx = s.aliased && Math.abs(dxr) < Math.abs(dxl) ? dxr : dxl,
      dy = position.gtkY - position.specY, mw = movedWith(s.box);
    const explained = mw && Math.abs(mw[1] - dx) <= 1.5 && Math.abs(mw[2] - dy) <= 1.5 && (Math.abs(dx) > TOL.pos || Math.abs(dy) > TOL.pos);
    if (!explained && (Math.abs(dx) > TOL.pos || Math.abs(dy) > TOL.pos)) { const rx = mw ? dx - mw[1] : dx, ry = mw ? dy - mw[2] : dy;
      const vertical = position.baseline ? ' baseline' : '';
      add(posSev(Math.max(Math.abs(rx), Math.abs(ry))), s.box, `text ${nm}`, 'position', `${f1(s.box.x)},${f1(position.specY)}${vertical}`, `${f1(g.box.x)},${f1(position.gtkY)}${vertical}`, `text ${nm}: at ${f1(s.box.x)},${f1(position.specY)}${vertical} in spec, ${f1(g.box.x)},${f1(position.gtkY)}${vertical} in GTK${mw && (Math.abs(mw[1]) + Math.abs(mw[2]) > 0.5) ? ` (${f1(rx)},${f1(ry)} beyond its container's own offset)` : ''}`,
        // end_anchored: an end-aligned run that keeps its right edge, so its start moves by its own
        // width difference and nothing else (a hinted Figtree value in a row's trailing column).
        { mag: Math.hypot(rx, ry), family: (g.font && g.font.family) || '', ratio: s.box.w > 0 ? Math.abs(g.box.w - s.box.w) / s.box.w : null,
          end_anchored: Math.abs(ry) <= 1 && Math.abs((g.box.x + g.box.w) - (s.box.x + s.box.w) - (mw ? mw[1] : 0)) <= 1 && Math.abs(rx + (g.box.w - s.box.w)) <= 1 }); }
    const dw = g.box.w - s.box.w; if (!s.aliased && Math.abs(dw) > Math.max(TOL.size, s.box.w * TOL.textWidthPct) && !(s.ellipsis || g.w.ellipsized)) add('minor', s.box, `text ${nm}`, 'width', f1(s.box.w), f1(g.box.w), `text ${nm}: ${f1(s.box.w)} px wide in spec, ${f1(g.box.w)} in GTK (tracking, weight or a different string)`, { ratio: s.box.w > 0 ? Math.abs(dw) / s.box.w : null, family: (g.font && g.font.family) || '' });
    if ((s.lines || 1) !== (g.lines || 1)) add('major', s.box, `text ${nm}`, 'lines', s.lines, g.lines, `text ${nm}: ${s.lines} line(s) in spec, ${g.lines} in GTK`);
    if (s.color && g.color) { const sb = bg('spec', s.box) || { r: 0, g: 0, b: 0 }, gb2 = bg('gtk', g.box) || sb; const cs = over(s.color, sb, s.opacity ?? 1), cg = over(g.color, gb2); const d = dE00(cs, cg);
      if (d > TOL.dE) add(d > TOL.majorDE ? 'major' : 'minor', s.box, `text ${nm}`, 'colour', hex(cs), hex(cg), `text ${nm}: colour ${hex(cs)} in spec, ${hex(cg)} in GTK (ΔE ${f1(d)})`, { mag: d }); }
  }
  // A text the spec shows only a sliver of (under 30% of its line, at a scroller's edge) is where the
  // other side's 1-2 px of position tolerance can hide it entirely: noted, not a missing text.
  for (const s of tRes.lonelyA) if (!s.offscreen) {
    const sliver = s.font && s.font.size && s.box.h < 0.3 * s.font.size * 1.45;
    add(sliver ? 'info' : 'critical', s.box, `text "${s.text}"`, sliver ? 'sliver' : 'missing', 'present', 'absent',
        sliver ? `text "${s.text}": the spec shows only ${f1(s.box.h)} px of it at an edge; GTK none` : `text "${s.text}" (${s.font.size}px/${s.font.weight}) is missing in GTK`);
  }
  for (const g of tRes.lonelyB) if (!g.offscreen) add('major', g.box, `text "${g.text}"`, 'extra', 'absent', 'present', `text "${g.text}" is in GTK but not in the spec`);

  // Icons
  const iRes = greedy(S.icons, G.icons, i => i.name, (a, b) => Math.abs(a.ink.x - b.ink.x) + Math.abs(a.ink.y - b.ink.y), 160);
  for (const [s, g] of iRes.pairs) {
    const nm = `icon ${s.name}`, si = s.ink, gi = g.ink, mw = movedWith(s.box);
    // An icon a scroller's edge cuts is placed by the edge that still shows, not by its visible part's centre.
    const sCutV = Math.abs(s.box.w - s.box.h) > 1 && s.box.h < s.box.w, sCutH = Math.abs(s.box.w - s.box.h) > 1 && s.box.w < s.box.h, cs = g.cutSide || {};
    const cutV = cs.t || cs.b || sCutV && !(g.clipped), cutH = cs.l || cs.r || sCutH && !(g.clipped);
    const atBottom = cs.b || !cs.t && s.box.y + s.box.h / 2 > S.window.h / 2, atRight = cs.r || !cs.l && s.box.x + s.box.w / 2 > S.window.w / 2;
    const dx = cutH ? (atRight ? gi.x - si.x : gi.x + gi.w - (si.x + si.w)) : gi.x + gi.w / 2 - (si.x + si.w / 2);
    const dy = cutV ? (atBottom ? gi.y - si.y : gi.y + gi.h - (si.y + si.h)) : gi.y + gi.h / 2 - (si.y + si.h / 2);
    const explained = mw && Math.abs(mw[1] - dx) <= 1.5 && Math.abs(mw[2] - dy) <= 1.5;
    if (!explained && (Math.abs(dx) > TOL.pos || Math.abs(dy) > TOL.pos)) add(posSev(Math.max(Math.abs(dx), Math.abs(dy))), s.box, nm, 'position', `${f1(si.x)},${f1(si.y)}`, `${f1(gi.x)},${f1(gi.y)}`, `${nm} (${f1(s.box.x)},${f1(s.box.y)}): centre off by ${f1(dx)},${f1(dy)} px`, { mag: Math.hypot(dx, dy) });
    const k = s.box.w / 24; // Lucide artboard, for a readable size
    // An icon a scroller's edge cuts (in either) shows a different sliver when the lists above differ: info.
    const edgeCut = g.clipped || s.box.w > 0 && Math.abs(s.box.w - s.box.h) > 1 && Math.abs(g.box.w - g.box.h) <= 1 && Math.min(s.box.w, s.box.h) < Math.min(g.box.w, g.box.h) - 1;
    if (edgeCut && (Math.abs(gi.w - si.w) > 1 || Math.abs(gi.h - si.h) > 1)) { add('info', s.box, nm, 'sliver', `ink ${f1(si.w)}×${f1(si.h)}`, `ink ${f1(gi.w)}×${f1(gi.h)}`, `${nm}: cut by a scroller's edge (ink ${f1(si.w)}×${f1(si.h)} in spec, ${f1(gi.w)}×${f1(gi.h)} in GTK)`); }
    else if (Math.abs(gi.w - si.w) > 1 || Math.abs(gi.h - si.h) > 1) add('minor', s.box, nm, 'size', `${f1(s.box.w)}px (ink ${f1(si.w)}×${f1(si.h)})`, `ink ${f1(gi.w)}×${f1(gi.h)}`, `${nm}: ${f1(s.box.w)} px in spec, about ${f1(s.box.w * gi.w / Math.max(0.1, si.w))} px in GTK (ink ${f1(si.w)}×${f1(si.h)} vs ${f1(gi.w)}×${f1(gi.h)})`);
    if (s.color && g.color) { const sb = bg('spec', s.box) || { r: 0, g: 0, b: 0 }; const cs = over(s.color, sb, s.opacity ?? 1), cg = over(g.color, bg('gtk', g.box) || sb); const d = dE00(cs, cg);
      if (d > TOL.dE) add(d > TOL.majorDE ? 'major' : 'minor', s.box, nm, 'colour', hex(cs), hex(cg), `${nm}: colour ${hex(cs)} in spec, ${hex(cg)} in GTK (ΔE ${f1(d)})`, { mag: d }); }
  }
  for (const g of G.icons.filter(i => i.generic)) add('major', g.box, 'icon app-icon', 'generic', 'app icon', 'application-x-executable', 'icon app-icon: GTK shows the generic executable icon, not the app\'s own (the identity cannot find the app\'s icon)');
  for (const s of iRes.lonelyA) add('critical', s.box, `icon ${s.name}`, 'missing', 'present', 'absent', `icon ${s.name} at ${f1(s.box.x)},${f1(s.box.y)} is missing in GTK`);
  for (const g of iRes.lonelyB) add('major', g.box, `icon ${g.name}`, 'extra', 'absent', 'present', `icon ${g.name} at ${f1(g.box.x)},${f1(g.box.y)} is in GTK but not in the spec`);

  if (scenario.frame) frameChecks(S, G, add, samples);
  // A scenario may measure one part of the window (the frame): everything else is left to the app's own scenario.
  if (scenario.scope) { const sc = scenario.scope, subj = sc.subjects ? new RegExp(sc.subjects, 'i') : null;
    for (let i = issues.length - 1; i >= 0; i--) { const it = issues[i]; if ((sc.groups || []).includes(it.group) || (subj && subj.test(it.subject))) continue; issues.splice(i, 1); } }

  // Hinted-text propagation (coordinator's ruling, lumaui-deviations.md "Hinted glyph advances"): a
  // position or size drift explained by the width drift of hinted Figtree runs before it in its row (or
  // inside it, for a size), at most 1 px per run and 3 px in all, is accepted and names the runs.
  const hinted = tRes.pairs.map(([sp, gp]) => ({ s: sp, g: gp, dw: gp.box.w - sp.box.w }))
    .filter(r => /figtree/i.test((r.g.font && r.g.font.family) || '') && r.s.box.w > 0 && Math.abs(r.dw) <= Math.max(1.2, r.s.box.w * 0.03) && Math.abs(r.dw) > 0.05);
  const nums = t => String(t || '').match(/-?[0-9.]+/g)?.map(Number) || [];
  const rowOverlap = (a, b) => Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y) > Math.min(a.h, b.h) * 0.4;
  for (const it of issues) {
    if (it.sev === 'critical' || !it.box || !['position', 'size'].includes(it.prop)) continue;
    const [sa, sb] = nums(it.spec), [ga, gb] = nums(it.gtk);
    if ([sa, sb, ga, gb].some(v => v == null || Number.isNaN(v))) continue;
    const d1 = ga - sa, d2 = gb - sb, box = it.box;
    // Before it in its row (a start-aligned row pushes it) or after it (an end-aligned row pulls it).
    const self = r => r.s.box.x === box.x && r.s.box.y === box.y;
    const sides = it.prop === 'position'
      ? [hinted.filter(r => rowOverlap(r.s.box, box) && r.s.box.x + r.s.box.w <= box.x + 1 && !self(r)),
         // an end-aligned row: the element's start moves by its own run and every run after it
         hinted.filter(r => rowOverlap(r.s.box, box) && r.s.box.x + r.s.box.w > box.x - 1 && (r.s.box.x >= box.x - 1))]
      : [hinted.filter(r => inside(r.s.box, box, 1))];
    if (Math.abs(d2) > 1) continue;
    const budgetOf = rs => Math.min(3, rs.reduce((sum, r) => sum + Math.min(1, Math.abs(r.dw)), 0));
    const runs = sides.find(rs => rs.length && Math.abs(d1) <= budgetOf(rs) + 0.3);
    if (!runs) continue;
    it.accepted = 'hinted-text-propagation'; it.sevWas = it.sev; it.sev = 'accepted';
    it.explainedBy = runs.map(r => ({ text: r.s.text, dw: Math.round(r.dw * 10) / 10 }));
    it.fix += ` (accepted: hinted-text propagation from ${it.explainedBy.map(r => `"${r.text.slice(0, 30)}" ${r.dw > 0 ? '+' : ''}${r.dw}`).join(', ')})`;
  }

  // Accepted deviations
  const shortcutPairs = calculatorShortcutPairs(scenario.app, st, S.texts, G.texts);
  const deviationUses = new Map();
  for (const it of issues) for (const d of it.accepted ? [] : deviations) {
    const used = deviationUses.get(d.id) || 0;
    if (!matchesDeviation(d, it, st, scenario.app, used, shortcutPairs)) continue;
    it.accepted = d.id; it.sevWas = it.sev; it.sev = 'accepted';
    deviationUses.set(d.id, used + 1); break; }

  // Feature inventory
  const inv = [];
  const shortcutInventory = acceptedShortcutLabels(issues);
  const gText = new Set(G.texts.filter(t => !t.offscreen).map(t => t.text.toLowerCase())), gIcon = G.icons.reduce((m, i) => (m[i.name] = (m[i.name] || 0) + 1, m), {});
  const sIcon = S.icons.reduce((m, i) => (m[i.name] = (m[i.name] || 0) + 1, m), {});
  // pairedS contains only explicit or semantic pairs with a visible GTK surface.
  for (const c of S.comps.filter(c => c.label)) inv.push({ kind: rolesTxt(c), label: c.label, group: groupOf(c.box).name, gtk: (pairedS.has(c.el) || compRes.pairs.some(p => p[0] === c)) ? 'yes' : gText.has(c.label.toLowerCase()) ? 'content only' : 'no' });
  // A text the spec shows only a sliver of at a scroller's edge counts as present when GTK has it just scrolled out.
  const gAny = new Set(G.texts.map(t => t.text.toLowerCase())), specSliver = t => { const f = t.full ? B(t.full) : null; return f && (t.box.w < 0.3 * f.w || t.box.h < 0.3 * f.h); };
  for (const t of S.texts.filter(t => !t.offscreen && specSliver(t) && gAny.has(t.text.toLowerCase()))) gText.add(t.text.toLowerCase());
  for (const t of [...new Set(S.texts.filter(t => !t.offscreen).map(t => t.text))]) inv.push({ kind: 'text', label: t, group: groupOf(S.texts.find(x => x.text === t).box).name, gtk: gText.has(t.toLowerCase()) ? 'yes' : shortcutInventory.has(t) ? 'accepted' : 'no' });
  for (const [n, c] of Object.entries(sIcon)) inv.push({ kind: 'icon', label: n, count: c, gtk_count: gIcon[n] || 0, gtk: (gIcon[n] || 0) >= c ? 'yes' : gIcon[n] ? 'fewer' : 'no' });
  return { issues, inventory: inv };
}

// ── the frame: the window's own radius, shadow and rim, and sampled frame probes ──
// Used by scenarios with "frame": true (the window-chrome scenarios). The spec's window is element 0;
// GTK's are the paint ops that sit on the window's own outline.
const parseShadows = str => (str || '').split(/,(?![^()]*\))/).map(x => x.trim()).filter(Boolean).map(x => { const c = parseColor(x), n = x.replace(/rgba?\([^)]*\)/, '').replace('inset', '').trim().split(/\s+/).map(parseFloat);
  return { inset: /\binset\b/.test(x), dx: n[0] || 0, dy: n[1] || 0, blur: n[2] || 0, spread: n[3] || 0, color: c || { r: 0, g: 0, b: 0, a: 0 } }; }).filter(x => x.color.a > 0.005);
function frameChecks(S, G, add, samples) {
  const win = S.raw.elements[0], sp = win.paint || {}, W = G.window, at = { x: -8, y: -8, w: 1, h: 1 }; // outside every group: the window itself
  // Studio's phone is a device mock-up (a 42 px bezel); a phone draws no window frame at all.
  const frameOwn = !meta.phone;
  const onWin = o => { const b = B(o.box); return Math.abs(b.x) <= 2 && Math.abs(b.y) <= 2 && Math.abs(b.w - W.w) <= 2 && Math.abs(b.h - W.h) <= 2; };
  const gOps = G.ops || [];
  // Radius: the spec window's corner against the radius GTK clips or outlines the window with.
  const sr = (sp.radius || [0])[0], gr = ((gOps.find(o => (o.k === 'shadow' || o.k === 'rclip' || o.k === 'border') && onWin(o) && o.radius) || {}).radius || [0])[0];
  if (frameOwn && Math.abs(sr - gr) > TOL.radius) add(Math.abs(sr - gr) > 3 ? 'major' : 'minor', at, 'window', 'radius', f1(sr), f1(gr), `window: corner radius ${f1(sr)} px in spec, ${f1(gr)} px in GTK`);
  // Shadow and rim, layer by layer, in the order each declares them.
  const sSh = parseShadows(sp.boxShadow), gSh = gOps.filter(o => o.k === 'shadow' && onWin(o)).map(o => ({ inset: o.inset, dx: o.dx, dy: o.dy, blur: o.blur, spread: o.spread, color: parseColor(o.color) }));
  for (const inset of frameOwn ? [false, true] : []) {
    const what = inset ? 'rim' : 'shadow', a = sSh.filter(x => x.inset === inset), b = gSh.filter(x => x.inset === inset && x.color.a > 0.005);
    const txt = l => l.map(x => `${f1(x.dx)} ${f1(x.dy)} ${f1(x.blur)} ${f1(x.spread)} ${hex(x.color)}`).join(', ') || 'none';
    if (a.length !== b.length) { add('major', at, 'window', what, txt(a), txt(b), `window ${what}: ${a.length} layer(s) in spec (${txt(a)}), ${b.length} in GTK (${txt(b)})`); continue; }
    a.forEach((x, i) => { const y = b[i], d = Math.max(Math.abs(x.dx - y.dx), Math.abs(x.dy - y.dy), Math.abs(x.spread - y.spread)), db = Math.abs(x.blur - y.blur), da = Math.abs(x.color.a - y.color.a);
      const dc = x.color.a > 0.1 && y.color.a > 0.1 ? dE00({ ...x.color, a: 1 }, { ...y.color, a: 1 }) : 0;
      if (d > 1 || db > 1 || da > 0.03 || dc > TOL.dE) add(db > 4 || da > 0.1 || d > 4 ? 'major' : 'minor', at, 'window', what, txt([x]), txt([y]), `window ${what} layer ${i + 1}: ${txt([x])} in spec, ${txt([y])} in GTK`, { mag: Math.max(d, db, da * 40, dc) }); });
  }
  // Probes: sampled frame colour at fixed places (negative x/y count from the right/bottom edge).
  for (const pr of scenario.frame_probes || []) {
    const box = { x: pr.x < 0 ? W.w + pr.x : pr.x, y: pr.y < 0 ? W.h + pr.y : pr.y, w: pr.w, h: pr.h };
    const cs = samples.spec(box), cg = samples.gtk(box); if (!cs || !cg) continue; const d = dE00(cs, cg);
    if (d > TOL.dE) add(d > TOL.majorDE ? 'major' : 'minor', box, `frame ${pr.name}`, 'surface colour', hex(cs), hex(cg), `frame ${pr.name}: ${hex(cs)} in spec, ${hex(cg)} in GTK (ΔE ${f1(d)})`, { mag: d });
  }
}

// ── images: sampling, heatmap, side by side (in Chromium's canvas) ───────
async function imaging(page, st, S) {
  const sp = path.join(dir, `spec-${st}.png`), gp = path.join(dir, `gtk-${st}.png`);
  const data = f => fs.existsSync(f) ? 'data:image/png;base64,' + fs.readFileSync(f).toString('base64') : null;
  return page.evaluate(async ({ a, b, radius, groups }) => {
    const load = src => new Promise((ok, no) => { const i = new Image(); i.onload = () => ok(i); i.onerror = no; i.src = src; });
    const [ia, ib] = await Promise.all([load(a), load(b)]); const W = ia.width, H = ia.height;
    const cv = (w, h) => { const c = document.createElement('canvas'); c.width = w; c.height = h; return c; };
    const ca = cv(W, H), cb = cv(W, H), xa = ca.getContext('2d', { willReadFrequently: true }), xb = cb.getContext('2d', { willReadFrequently: true });
    xa.drawImage(ia, 0, 0); xb.drawImage(ib, 0, 0); const A = xa.getImageData(0, 0, W, H).data, Bd = xb.getImageData(0, 0, W, H).data;
    const inCorner = (x, y) => { const r = radius; const cx = x < r ? r : x >= W - r ? W - r - 1 : -1, cy = y < r ? r : y >= H - r ? H - r - 1 : -1; return cx >= 0 && cy >= 0 && Math.hypot(x - cx, y - cy) > r - 1; };
    const lin = v => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }, g = t => t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116;
    const LAB = (r, gg, bb) => { const R = lin(r), G = lin(gg), Bb = lin(bb); const X = (R * 0.4124 + G * 0.3576 + Bb * 0.1805) / 0.95047, Y = R * 0.2126 + G * 0.7152 + Bb * 0.0722, Z = (R * 0.0193 + G * 0.1192 + Bb * 0.9505) / 1.08883; return [116 * g(Y) - 16, 500 * (g(X) - g(Y)), 200 * (g(Y) - g(Z))]; };
    const dE = new Float32Array(W * H); let over10 = 0, n = 0;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) { const i = (y * W + x) * 4; if (inCorner(x, y)) { dE[y * W + x] = -1; continue; }
      const ab = Bd[i + 3] / 255, br = Bd[i] * ab, bg = Bd[i + 1] * ab, bbb = Bd[i + 2] * ab; // GTK corners are transparent: over black
      const p = LAB(A[i], A[i + 1], A[i + 2]), q = LAB(br, bg, bbb); const d = Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]); dE[y * W + x] = d; n++; if (d > 10) over10++; }
    // heat: dimmed spec + red where it differs
    const ch = cv(W, H), xh = ch.getContext('2d'), H2 = xh.createImageData(W, H);
    for (let k = 0; k < W * H; k++) { const i = k * 4, l = (A[i] * 0.3 + A[i + 1] * 0.59 + A[i + 2] * 0.11) * 0.35, d = dE[k];
      if (d < 0) { H2.data[i + 3] = 0; continue; } const t = Math.min(1, Math.max(0, (d - 3) / 27)); H2.data[i] = l + (255 - l) * t; H2.data[i + 1] = l * (1 - t) + 40 * t; H2.data[i + 2] = l * (1 - t) + 40 * t; H2.data[i + 3] = 255; }
    xh.putImageData(H2, 0, 0);
    const side = cv(W * 3 + 32, H + 28), xs = side.getContext('2d'); xs.fillStyle = '#16171a'; xs.fillRect(0, 0, side.width, side.height);
    xs.font = '600 14px sans-serif'; xs.fillStyle = '#e4e8ea'; ['v70 spec', 'GTK (LumaUI)', 'difference (red = ΔE up to 30)'].forEach((t, i) => xs.fillText(t, i * (W + 16) + 4, 18));
    xs.drawImage(ca, 0, 28); xs.drawImage(cb, W + 16, 28); xs.drawImage(ch, 2 * (W + 16), 28);
    const region = gs => gs.map(gr => { let s = 0, c = 0, o = 0; for (const bx of gr.boxes) for (let y = Math.max(0, Math.floor(bx.y)); y < Math.min(H, bx.y + bx.h); y++) for (let x = Math.max(0, Math.floor(bx.x)); x < Math.min(W, bx.x + bx.w); x++) { const d = dE[y * W + x]; if (d < 0) continue; s += d; c++; if (d > 10) o++; }
      return { name: gr.name, mean: c ? s / c : 0, over10: c ? o / c : 0, px: c }; });
    window.__img = { A, Bd, W, H };
    return { heat: ch.toDataURL('image/png'), side: side.toDataURL('image/png'), over10: over10 / n, mean: dE.reduce((s, v) => s + (v > 0 ? v : 0), 0) / n, regions: region(groups) };
  }, { a: data(sp), b: data(gp), radius: S.window.radius || 0, groups: S.groups });
}
async function sampleAll(page, reqs) { // mode colour of a box, per image; reqs: [{img:'spec'|'gtk', b}]
  return page.evaluate(reqs => { const { A, Bd, W, H } = window.__img; return reqs.map(({ img, b, edge }) => { const D = img === 'spec' ? A : Bd, m = edge ? 1 : Math.max(1, Math.min(4, Math.floor(Math.min(b.w, b.h) / 6)));
    const x0 = Math.max(0, Math.round(b.x + m)), y0 = Math.max(0, Math.round(b.y + m)), x1 = Math.min(W, Math.round(b.x + b.w - m)), y1 = Math.min(H, Math.round(b.y + b.h - m)); if (x1 <= x0 || y1 <= y0) return null;
    // edge: only the frame 1 to 3 px inside the box, where a surface shows around whatever it holds (a
    // well's 3 px around its chosen chip).
    const inner = (x, y) => edge && x >= x0 + 2 && x < x1 - 2 && y >= y0 + 2 && y < y1 - 2;
    const bins = new Map(); for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) { if (inner(x, y)) continue; const i = (y * W + x) * 4; const a = D[i + 3] / 255; const r = D[i] * a, g = D[i + 1] * a, bb = D[i + 2] * a;
      const k = (r >> 3) << 10 | (g >> 3) << 5 | (bb >> 3); const e = bins.get(k) || [0, 0, 0, 0]; e[0]++; e[1] += r; e[2] += g; e[3] += bb; bins.set(k, e); }
    let best = null; for (const e of bins.values()) if (!best || e[0] > best[0]) best = e; return best ? { r: best[1] / best[0], g: best[2] / best[0], b: best[3] / best[0], a: 1 } : null; }); }, reqs);
}

(async () => {
  const browser = await chromium.launch(), page = await browser.newPage();
  const all = [], summary = [], invAll = {};
  for (const ms of meta.states) {
    const st = ms.name, sj = path.join(dir, `spec-${st}.json`), gj = path.join(dir, `gtk-${st}.json`);
    if (!fs.existsSync(gj) || !fs.existsSync(path.join(dir, `gtk-${st}.png`))) { const err = path.join(dir, `gtk-${st}.error.txt`); all.push({ state: st, sev: 'critical', group: 'window', subject: 'GTK capture', prop: 'missing', spec: '', gtk: '', fix: `no GTK capture for ${st}${fs.existsSync(err) ? ': ' + fs.readFileSync(err, 'utf8').trim().split('\n').pop() : ' (see remote.log)'}` }); summary.push({ st, missing: true }); continue; }
    const Sraw = JSON.parse(fs.readFileSync(sj, 'utf8')), Graw = JSON.parse(fs.readFileSync(gj, 'utf8'));
    const S = specModel(Sraw), G = gtkModel(Graw); S.raw = Sraw;
    const img = await imaging(page, st, S);
    fs.writeFileSync(path.join(dir, `heat-${st}.png`), Buffer.from(img.heat.split(',')[1], 'base64'));
    fs.writeFileSync(path.join(dir, `side-${st}.png`), Buffer.from(img.side.split(',')[1], 'base64'));
    // Pre-sample every box the checks may ask for.
    const probes = (scenario.frame_probes || []).map(pr => ({ x: pr.x < 0 ? G.window.w + pr.x : pr.x, y: pr.y < 0 ? G.window.h + pr.y : pr.y, w: pr.w, h: pr.h }));
    const boxes = [...probes.flatMap(b => [['spec', b], ['gtk', b]]), ...S.comps.map(c => ['spec', c.box]), ...G.comps.map(c => ['gtk', c.box]), ...S.texts.map(t => ['spec', t.box]), ...G.texts.map(t => ['gtk', t.box]), ...S.icons.map(i => ['spec', i.box]), ...G.icons.map(i => ['gtk', i.box])];
    const key = (w, b) => `${w}|${Math.round(b.x)}|${Math.round(b.y)}|${Math.round(b.w)}|${Math.round(b.h)}`;
    const uniq = [...new Map(boxes.map(([w, b]) => [key(w, b), { img: w, b: { x: b.x - (b.h < 30 ? 3 : 0), y: b.y - (b.h < 30 ? 3 : 0), w: b.w + (b.h < 30 ? 6 : 0), h: b.h + (b.h < 30 ? 6 : 0) } }])).entries()];
    const edges = uniq.map(([k, u]) => ['e' + k, { ...u, edge: true }]);
    const got = await sampleAll(page, [...uniq, ...edges].map(u => u[1])), cache = new Map([...uniq, ...edges].map((u, i) => [u[0], got[i]]));
    const samples = { spec: b => cache.get(key('spec', b)) || null, gtk: b => cache.get(key('gtk', b)) || null,
                      specEdge: b => cache.get('e' + key('spec', b)) || null, gtkEdge: b => cache.get('e' + key('gtk', b)) || null };
    const { issues, inventory } = compareState(st, S, G, samples);
    fs.writeFileSync(path.join(dir, `inventory-${st}.json`), JSON.stringify(inventory, null, 1)); invAll[st] = inventory;
    all.push(...issues);
    const count = s => issues.filter(i => i.sev === s).length;
    summary.push({ st, critical: count('critical'), major: count('major'), minor: count('minor'), info: count('info'), accepted: count('accepted'), over10: img.over10, mean: img.mean, regions: img.regions,
      inv: { total: inventory.length, missing: inventory.filter(i => i.gtk === 'no').length, partial: inventory.filter(i => i.gtk === 'content only' || i.gtk === 'fewer').length } });
  }
  await browser.close();
  fs.writeFileSync(path.join(dir, 'issues.json'), JSON.stringify(all, null, 1));
  const failing = all.filter(i => ['critical', 'major', 'minor'].includes(i.sev)), pass = failing.length === 0;
  // Top fixes: most severe, largest first, one per subject.
  const rank = i => SEV[i.sev] * 1000 + Math.min(999, i.mag || (i.prop === 'missing' ? 500 : 50));
  const seen = new Map(), top = [], rest = [], perKind = new Map();
  for (const i of failing.slice().sort((a, b) => rank(b) - rank(a))) { // one line per kind of fix: same group, property and values, or same subject
    const k1 = ['missing', 'extra', 'not a surface'].includes(i.prop) ? i.group + '|' + i.subject + '|' + i.prop : i.group + '|' + i.prop + '|' + i.spec + '|' + i.gtk, k2 = i.group + '|' + i.subject + '|' + i.prop, hit = seen.get(k1) || seen.get(k2);
    if (hit) { hit.more = (hit.more || 0) + 1; continue; } const e = { ...i }; seen.set(k1, e); seen.set(k2, e);
    const kind = i.group + '|' + i.prop; perKind.set(kind, (perKind.get(kind) || 0) + 1); if (perKind.get(kind) <= 2) top.push(e); else rest.push(e); }
  top.push(...rest); // variety first: at most two of one kind (group + property) before the rest
  top.splice(15);
  const L = [];
  L.push(`# lumaui-conform: ${scenario.app}, ${meta.theme}${meta.phone ? ', phone' : ''}: **${pass ? 'PASS' : 'FAIL'}**`, '');
  L.push(`Spec: ${meta.url} (${meta.phone ? 'phone' : 'window'} ${meta.states[0] ? meta.states[0].window.w + '×' + meta.states[0].window.h : ''}). Tolerances: position/size ±${TOL.pos}px (text width ±max(${TOL.size}px, ${TOL.textWidthPct * 100}%)), font size ±${TOL.font}px, weight exact, colour ΔE2000 ≤ ${TOL.dE}, radius ±${TOL.radius}px. Accepted deviations: accepted-deviations.json.`, '');
  L.push('| state | critical | major | minor | info | accepted | features missing / partial | pixels ΔE>10 |', '|---|---|---|---|---|---|---|---|');
  for (const s of summary) L.push(s.missing ? `| ${s.st} | no GTK capture | | | | | | |` : `| ${s.st} | ${s.critical} | ${s.major} | ${s.minor} | ${s.info} | ${s.accepted} | ${s.inv.missing} / ${s.inv.partial} of ${s.inv.total} | ${(s.over10 * 100).toFixed(1)}% |`);
  L.push('', '## Top fixes', '');
  top.forEach((i, n) => L.push(`${n + 1}. **${i.sev}** [${i.state} · ${i.group}] ${i.fix}${i.more ? ` (+${i.more} like it)` : ''}`));
  for (const s of summary) {
    if (s.missing) continue; const its = all.filter(i => i.state === s.st);
    L.push('', `## ${s.st}`, '', `Images: side-${s.st}.png (spec | GTK | diff), heat-${s.st}.png. Inventory: inventory-${s.st}.json.`, '');
    L.push('| region | mean ΔE | pixels ΔE>10 |', '|---|---|---|'); for (const r of s.regions.filter(r => r.px)) L.push(`| ${r.name} | ${r.mean.toFixed(1)} | ${(r.over10 * 100).toFixed(1)}% |`);
    const miss = invAll[s.st].filter(i => i.gtk !== 'yes' && i.gtk !== 'accepted'); if (miss.length) { L.push('', `### Missing or partial features (${miss.length})`, ''); miss.forEach(i => L.push(`- ${i.kind} "${i.label}"${i.count ? ` ×${i.count} (GTK ${i.gtk_count})` : ''}: ${i.gtk === 'no' ? 'missing' : i.gtk}${i.group ? ` (${i.group})` : ''}`)); }
    const acceptedFeatures = invAll[s.st].filter(i => i.gtk === 'accepted'); if (acceptedFeatures.length) { L.push('', `### Accepted feature equivalents (${acceptedFeatures.length})`, ''); acceptedFeatures.forEach(i => L.push(`- ${i.kind} "${i.label}": Linux Ctrl+${i.label.slice(-1)} keycaps (${i.group})`)); }
    const byGroup = new Map(); its.forEach(i => { if (!byGroup.has(i.group)) byGroup.set(i.group, []); byGroup.get(i.group).push(i); });
    for (const [g, list] of [...byGroup.entries()].sort((a, b) => Math.max(...b[1].map(i => SEV[i.sev])) - Math.max(...a[1].map(i => SEV[i.sev])))) {
      L.push('', `### ${g} (${list.filter(i => SEV[i.sev] >= 2).length} to fix)`, '');
      list.sort((a, b) => rank(b) - rank(a)).forEach(i => L.push(`- **${i.sev}**${i.accepted ? ` (${i.accepted}, was ${i.sevWas})` : ''} ${i.fix}`));
    }
  }
  fs.writeFileSync(path.join(dir, 'REPORT.md'), L.join('\n') + '\n');
  const tot = k => all.filter(i => i.sev === k).length;
  console.log(`${pass ? 'PASS' : 'FAIL'}: ${tot('critical')} critical, ${tot('major')} major, ${tot('minor')} minor, ${tot('accepted')} accepted, ${tot('info')} info. Report: ${path.join(dir, 'REPORT.md')}`);
  process.exit(pass ? 0 : 1);
})().catch(e => { console.error(e.stack || e); process.exit(3); });
