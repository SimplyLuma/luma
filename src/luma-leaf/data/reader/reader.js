// SPDX-License-Identifier: Apache-2.0
// Leaf's page: one section of the book at a time, laid out in CSS columns,
// one spread per turn, no animation.
//
// The page never stores a figure. It reports where the reader is as an EPUB
// CFI (the start of the first sentence on the spread) and the application
// derives chapter, percent and minutes from that one record, then hands the
// words for the foot back. Highlights, the spoken sentence and the spoken
// word are CSS Custom Highlights over Ranges: they sit behind the text, mix
// into the paper, and survive every reflow without touching the book's DOM —
// which matters, because a CFI counts the DOM's nodes.
import * as CFI from './vendor/foliate-js/epubcfi.js'
import { wirePageGestures } from './gestures.js'
import { measuredWordsPerPage, pageRange, pagesBefore, wordsPerPage } from './pages.js'

const $ = id => document.getElementById(id)
const page = $('page'), view = $('view'), frame = $('frame')

const post = (type, detail = {}) => {
  try { window.webkit.messageHandlers.leaf.postMessage(JSON.stringify({ type, ...detail })) } catch (_) { /* outside Leaf */ }
}
// Layout detail for LEAF_DEBUG=1. A blank spread is always reported (see checkDrawn).
const debug = (what, detail = {}) => { if (state.debug) post('debug', { what, ...detail }) }

// A page narrower or shorter than this has not been given its size yet: the
// page room is hidden, or on its way in. Nothing is laid out at that size.
const MIN_VIEW = 40
const viewSized = () => view.clientWidth >= MIN_VIEW && view.clientHeight >= MIN_VIEW

// ── the sentence rule — the same as luma_leaf/sentences.py ────────────────
const TITLE = /(?:^|[\s“"(])(?:Mr|Mrs|Ms|Dr|St|Mt|Jr|Sr|Messrs|Capt|Col|Rev|No)\.$/
export function sentenceSpans(text) {
  const spans = [], re = /[.!?]+[”’")\]]*(?=\s+[“‘"(]?[A-Z0-9])/g
  let start = 0, m
  while ((m = re.exec(text))) {
    if (TITLE.test(text.slice(Math.max(start, m.index - 9), m.index + 1))) continue
    spans.push([start, m.index + m[0].length]); start = m.index + m[0].length
  }
  spans.push([start, text.length])
  return spans.map(([a, b]) => {
    while (a < b && /\s/.test(text[a])) a++
    while (b > a && /\s/.test(text[b - 1])) b--
    return [a, b]
  }).filter(([a, b]) => b > a)
}

// Nodes Leaf adds (note dots) are invisible to CFIs.
const filter = node => node.nodeType === 1 && node.hasAttribute('data-leaf')
  ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT

const LINE_SPACING = { tight: 1.45, normal: 1.62, loose: 1.85 }
const MARGINS = { narrow: '4%', normal: '5.5%', wide: '12%' }
const COLOURS = {
  y: 'color-mix(in srgb, #e9bf6a 34%, var(--lf-paper))',
  g: 'color-mix(in srgb, #8fc27a 30%, var(--lf-paper))',
  b: 'color-mix(in srgb, #7fa7d6 30%, var(--lf-paper))',
  r: 'color-mix(in srgb, #d98ea1 30%, var(--lf-paper))',
}

const state = {
  book: null, spine: -1, doc: null, token: 0,
  sentences: [], cols: 1, gap: 0, stride: 1, spreads: 1, spread: 0, width: 0, height: 0,
  prefs: { theme: 'auto', face: 'serif', size: 19, spacing: 'normal', margins: 'normal', pages: 'auto', justify: true },
  highlights: [], bookmarks: [], speaking: null, lastReported: '', place: null, dink: false,
  debug: false, measure: '', laidOut: new Map(),
}

// ── section styles ────────────────────────────────────────────────────────
function sectionStyle() {
  const p = state.prefs
  const faces = { literata: "'Literata', Georgia, serif", serif: "'Literata', Georgia, serif",
    news: "'Newsreader', Georgia, serif", legible: "'Atkinson Hyperlegible', system-ui, sans-serif",
    sans: 'Figtree, system-ui, sans-serif' }
  const face = faces[p.face] ?? faces.literata
  const size = p.face === 'sans' ? p.size * 0.93 : p.size
  const hl = Object.entries(COLOURS).map(([c, v]) => `::highlight(lf-${c}) { background-color: ${v}; }`).join('\n')
  return `
  @font-face { font-family: Literata; src: url('leaf://reader/app/fonts/Literata.ttf'); font-style: normal; font-weight: 100 900; }
  @font-face { font-family: Literata; src: url('leaf://reader/app/fonts/Literata-Italic.ttf'); font-style: italic; font-weight: 100 900; }
  @font-face { font-family: Newsreader; src: url('leaf://reader/app/fonts/Newsreader.ttf'); font-style: normal; font-weight: 100 900; }
  @font-face { font-family: "Atkinson Hyperlegible"; src: url('leaf://reader/app/fonts/AtkinsonHyperlegible-Regular.ttf'); font-weight: 400; }
  @font-face { font-family: "Atkinson Hyperlegible"; src: url('leaf://reader/app/fonts/AtkinsonHyperlegible-Bold.ttf'); font-weight: 700; }
  html { height: ${state.height}px !important; width: ${state.width}px !important; margin: 0 !important; padding: 0 !important;
    column-width: ${Math.floor((state.width - state.gap * (state.cols - 1)) / state.cols)}px !important; column-count: auto !important;
    column-gap: ${state.gap}px !important; column-fill: auto !important;
    overflow: hidden !important; background: transparent !important; color: var(--lf-text) !important; }
  body { margin: 0 !important; padding: 0 !important; background: transparent !important; color: var(--lf-text) !important;
    font-family: ${face} !important; font-size: ${size}px !important; line-height: ${LINE_SPACING[p.spacing] ?? 1.62} !important;
    font-optical-sizing: auto; font-kerning: normal; font-variant-ligatures: common-ligatures; font-feature-settings: "onum";
    hyphens: auto; -webkit-hyphens: auto; overflow-wrap: break-word; text-rendering: optimizeLegibility;
    text-align: ${p.justify ? 'justify' : 'start'} !important; -webkit-user-select: text; user-select: text; }
  body :where(p, div, span, li, blockquote, em, i, b, strong, small, sup, sub, a, td, th, dd, dt, h1, h2, h3, h4, h5, h6, section, article) {
    font-family: inherit !important; }
  /* The paper theme owns the colour of every word. A publisher's own rule,
     ".calibre22 { color: #000 !important }" say, outranks anything inside
     :where(), and black on Night reads as a blank page; two :not(#id) raise
     this above any class or id a book can write. Drawings keep their own. */
  :root:not(#lf-a):not(#lf-b) body { color: var(--lf-text) !important; }
  :root:not(#lf-a):not(#lf-b) body *:not(svg, svg *, math, math *, [data-leaf]) {
    color: inherit !important; -webkit-text-fill-color: currentColor !important; background-color: transparent !important; }
  body :where(p, div, li, blockquote, dd) { line-height: inherit !important; }
  p { margin-top: 0 !important; margin-bottom: 0 !important; text-indent: 1.35em; orphans: 2; widows: 2; }
  :where(h1, h2, h3, h4, h5, h6) + p, blockquote p:first-child, li p { text-indent: 0; }
  h1, h2, h3, h4, h5, h6 { text-align: start !important; hyphens: manual; break-after: avoid; line-height: 1.2 !important; }
  .lf-ch { margin: 0 0 2em !important; padding-top: 10% !important; font-weight: 500 !important; font-size: 1.5em !important;
    line-height: 1.15 !important; letter-spacing: -.01em; text-indent: 0 !important; }
  .lf-ch::after { content: ''; display: block; width: 1.47em; height: 1px; margin-top: .7em; background: var(--lf-quiet); opacity: .55; }
  p.lf-first { text-indent: 0 !important; }
  header.leaf-fixture-intro { text-align: center; padding-top: 14%; margin-bottom: 2.4em; }
  .leaf-fixture-intro .leaf-fixture-part { display: block; margin-bottom: 3.4em; font-size: .6em; letter-spacing: .24em; text-transform: uppercase; color: var(--lf-quiet) !important; }
  .leaf-fixture-intro .leaf-fixture-number { display: block; font-size: .78em; letter-spacing: .16em; text-transform: uppercase; color: var(--lf-text) !important; }
  .leaf-fixture-intro h2 { margin: .35em 0 0 !important; font-size: 2.05em !important; font-weight: 400 !important; font-style: italic; line-height: 1.15 !important; letter-spacing: -.01em; text-align: center !important; }
  .leaf-fixture-intro .leaf-fixture-ornament { display: block; margin-top: .8em; font-size: .9em; font-style: normal; color: var(--lf-quiet) !important; }
  p.leaf-fixture-here { text-indent: 1.3em; }
  :root:not(#lf-a):not(#lf-b):not(#lf-c) body .leaf-here {
    background: color-mix(in srgb, #d0a766 22%, transparent) !important; }
  p.lf-first:not(.lf-own-initial)::first-letter { float: left; font-size: 3.7em; line-height: .78; font-weight: 400; margin: .08em .1em 0 -.02em; }
  p.lf-first .leaf-drop { float: left; font-size: 3.7em; line-height: .78; font-weight: 400; margin: .08em .1em 0 -.02em; }
  p.lf-first::first-line { font-variant-caps: all-small-caps; letter-spacing: .035em; }
  img, svg, video { max-width: 100% !important; max-height: ${Math.max(40, state.height - 8)}px !important; height: auto; object-fit: contain; break-inside: avoid; }
  figure, table, pre { break-inside: avoid; }
  a { color: inherit !important; text-decoration-color: color-mix(in srgb, var(--lf-text) 35%, transparent); }
  ::selection { background: color-mix(in srgb, var(--lf-text) 17%, transparent); }
  ${hl}
  ::highlight(lf-speaking) { background-color: color-mix(in srgb, var(--lf-text) 12%, var(--lf-paper)); }
  ::highlight(lf-word) { text-decoration: underline; text-decoration-thickness: 2px; text-underline-offset: .2em; }
  [data-leaf="note"] { position: absolute; width: .32em; height: .32em; border-radius: 50%; background: var(--lf-text); opacity: .5; pointer-events: none; }
  ${state.dink ? '::highlight(lf-y), ::highlight(lf-g), ::highlight(lf-b), ::highlight(lf-r) { background-color: transparent; text-decoration: underline; text-decoration-thickness: 1.5px; }' +
    '::highlight(lf-g) { text-decoration-style: double; } ::highlight(lf-b) { text-decoration-style: dashed; } ::highlight(lf-r) { text-decoration-style: wavy; }' : ''}
  `
}

function applyPageAttributes() {
  const p = state.prefs
  page.dataset.theme = state.fixture && p.theme === 'auto' ? (state.fixtureDark ? 'night' : 'paper') : p.theme
  page.dataset.face = p.face
  page.dataset.justify = String(!!p.justify)
  page.dataset.dink = String(state.dink)
  page.style.setProperty('--lf-mx', MARGINS[p.margins] ?? '9%')
}

function paintVariables() {
  if (!state.doc) return
  const computed = getComputedStyle(page)
  for (const name of ['--lf-paper', '--lf-text', '--lf-quiet'])
    state.doc.documentElement.style.setProperty(name, computed.getPropertyValue(name).trim())
}

// ── layout ────────────────────────────────────────────────────────────────
function measure() {
  const stageWidth = page.clientWidth
  const two = stageWidth > 880 && state.prefs.pages === 'two'
  const normalMargin = stageWidth < 560 ? 24 : two ? Math.max(64, (stageWidth - 1144) / 2) : Math.max(48, (stageWidth - 640) / 2)
  page.style.setProperty('--lf-mx', state.prefs.margins === 'normal' ? normalMargin + 'px' : MARGINS[state.prefs.margins] ?? '9%')
  state.width = Math.floor(view.clientWidth)
  state.height = Math.floor(view.clientHeight)
  const pages = state.prefs.pages
  state.cols = stageWidth < 880 ? 1 : pages === 'one' ? 1 : pages === 'two' ? 2 : (state.width >= 640 ? 2 : 1)
  state.gap = state.cols === 2 ? 104 : 0
}

function layout() {
  const doc = state.doc
  if (!doc) return
  measure()
  // The paper theme changes no layout; everything else that sets type does.
  const { theme: _theme, ...type } = state.prefs
  const signature = [state.width, state.height, state.cols, JSON.stringify(type)].join('|')
  if (signature !== state.measure) { state.measure = signature; state.laidOut.clear() }
  let style = doc.getElementById('leaf-style')
  if (!style) {
    style = doc.createElementNS('http://www.w3.org/1999/xhtml', 'style')
    style.id = 'leaf-style'
    style.setAttribute('data-leaf', 'style')
    ;(doc.head ?? doc.documentElement).append(style)
  }
  style.textContent = sectionStyle()
  paintVariables()
  // Take the stride from the layout, never from arithmetic: a page width
  // computed as (width − gap) / 2 is a fraction of a pixel short of what
  // the engine laid out, which is ten pixels of clipped text by page ten.
  const scroller = doc.scrollingElement ?? doc.documentElement
  const total = scroller.scrollWidth
  const column = (state.width - state.gap * (state.cols - 1)) / state.cols
  const columns = Math.max(1, Math.round((total + state.gap) / (column + state.gap)))
  state.stride = (total + state.gap) / columns * state.cols
  state.spreads = Math.max(1, Math.ceil(columns / state.cols))
  state.columns = columns
  state.laidOut.set(state.spine, { words: sectionWords(state.spine), columns })
  debug('layout', { spine: state.spine, width: state.width, height: state.height, cols: state.cols, columns, stride: state.stride })
  placeNotes()
}

const sectionWords = index => state.book?.sectionWords?.[index] ?? 0

// Resolves once the page has a real size; at once when it already has one.
function whenSized() {
  if (viewSized()) return Promise.resolve()
  debug('waiting for size', { width: view.clientWidth, height: view.clientHeight, spine: state.spine })
  return new Promise(resolve => {
    const watch = new ResizeObserver(() => { if (viewSized()) { watch.disconnect(); resolve() } })
    watch.observe(view)
  })
}

function scrollTo(spread) {
  state.spread = Math.max(0, Math.min(spread, state.spreads - 1))
  const scroller = state.doc.scrollingElement ?? state.doc.documentElement
  scroller.scrollLeft = Math.round(state.spread * state.stride)
  updateTurns()
}

function updateTurns() {
  const first = state.spine <= firstLinear() && state.spread === 0
  const last = state.spine >= lastLinear() && state.spread >= state.spreads - 1
  $('prev').disabled = first
  $('next').disabled = last
  // A chapter opener, a dedication, an epigraph: a page with a line or two on
  // it and nothing else. The turn is shown at rest there, with a quiet
  // Continue, or the page reads as blank.
  const brief = !last && state.spread >= state.spreads - 1 && sectionWords(state.spine) < BRIEF_WORDS && !largePicture()
  page.dataset.brief = String(brief)
  $('continue').hidden = !brief
}

const BRIEF_WORDS = 60

function largePicture() {
  const doc = state.doc
  if (!doc) return false
  const area = state.width * state.height
  for (const element of doc.querySelectorAll('img, svg, video')) {
    const r = element.getBoundingClientRect()
    if (r.right > 0 && r.left < state.width && r.width * r.height > area * 0.2) return true
  }
  return false
}

const linear = () => state.book.sections.map((s, i) => s.linear ? i : -1).filter(i => i >= 0)
const firstLinear = () => linear()[0] ?? 0
const lastLinear = () => linear().at(-1) ?? state.book.sections.length - 1

// ── sentences ─────────────────────────────────────────────────────────────
const BLOCK = 'p,li,h1,h2,h3,h4,h5,h6,blockquote,dd,dt,td,th,figcaption,pre,div,section,article,body'
const SKIP = new Set(['SCRIPT', 'STYLE', 'RT', 'RP', 'TITLE', 'svg:title'])

function buildSentences(doc) {
  const out = []
  const walker = doc.createTreeWalker(doc.body ?? doc.documentElement, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
    acceptNode: node => node.nodeType === 1
      ? (SKIP.has(node.nodeName.toUpperCase()) || node.hasAttribute('data-leaf') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_SKIP)
      : NodeFilter.FILTER_ACCEPT,
  })
  let group = null
  const flush = () => {
    if (!group) return
    const text = group.nodes.map(n => n.nodeValue).join('')
    for (const [a, b] of sentenceSpans(text)) {
      const range = doc.createRange()
      const start = locate(group, a), end = locate(group, b)
      range.setStart(start.node, start.offset)
      range.setEnd(end.node, end.offset)
      out.push({ range, text: text.slice(a, b).replace(/\s+/g, ' '), raw: text.slice(a, b), words: null })
    }
    group = null
  }
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const block = node.parentElement?.closest(BLOCK)
    if (!group || group.block !== block) { flush(); group = { block, nodes: [], starts: [] } }
    group.starts.push(group.nodes.reduce((t, n) => t + n.nodeValue.length, 0))
    group.nodes.push(node)
  }
  flush()
  return out
}

function locate(group, offset) {
  for (let i = group.nodes.length - 1; i >= 0; i--)
    if (group.starts[i] <= offset) return { node: group.nodes[i], offset: Math.min(offset - group.starts[i], group.nodes[i].nodeValue.length) }
  return { node: group.nodes[0], offset: 0 }
}

function wordRanges(sentence) {
  if (sentence.words) return sentence.words
  const doc = state.doc, out = []
  const r = sentence.range
  // Walk the sentence's own text nodes to map word offsets onto ranges.
  const nodes = []
  const walker = doc.createTreeWalker(r.commonAncestorContainer.nodeType === 3 ? r.commonAncestorContainer.parentNode : r.commonAncestorContainer, NodeFilter.SHOW_TEXT)
  for (let n = walker.nextNode(); n; n = walker.nextNode()) if (r.intersectsNode(n)) nodes.push(n)
  let text = '', starts = []
  nodes.forEach(n => {
    const from = n === r.startContainer ? r.startOffset : 0
    const to = n === r.endContainer ? r.endOffset : n.nodeValue.length
    starts.push({ node: n, base: text.length - from })
    text += n.nodeValue.slice(from, to)
  })
  const at = offset => {
    for (let i = starts.length - 1; i >= 0; i--) {
      const node = starts[i].node, local = offset - starts[i].base
      if (local >= (node === r.startContainer ? r.startOffset : 0)) return { node, offset: Math.min(local, node.nodeValue.length) }
    }
    return { node: r.startContainer, offset: r.startOffset }
  }
  for (const m of text.matchAll(/\S+/g)) {
    const range = doc.createRange()
    const a = at(m.index), b = at(m.index + m[0].length)
    range.setStart(a.node, a.offset); range.setEnd(b.node, b.offset)
    out.push({ range, text: m[0] })
  }
  sentence.words = out
  return out
}

// An anchor is often an empty element: measure the first thing drawn from it on.
function visibleRangeFrom(element) {
  const doc = state.doc
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT)
  walker.currentNode = element
  for (let node = element; node; node = walker.nextNode()) {
    const range = doc.createRange()
    if (node.nodeType === 3) {
      if (!node.nodeValue.trim()) continue
      range.selectNodeContents(node)
    } else if (node.matches('img,svg,video')) range.selectNode(node)
    else continue
    if ([...range.getClientRects()].some(r => r.width > 0 || r.height > 0)) return range
  }
  const range = doc.createRange()
  range.selectNode(element)
  return range
}

function spreadOf(range) {
  const rects = [...range.getClientRects()].filter(r => r.width > 0 || r.height > 0)
  const rect = rects[0] ?? range.getBoundingClientRect()
  const scroller = state.doc.scrollingElement ?? state.doc.documentElement
  const x = rect.left + scroller.scrollLeft
  return Math.max(0, Math.min(state.spreads - 1, Math.floor((x + state.gap / 2) / state.stride)))
}

function firstSentenceOn(spread) {
  const list = state.sentences
  let lo = 0, hi = list.length - 1, found = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    const s = spreadOf(list[mid].range)
    if (s >= spread) { found = mid; hi = mid - 1 } else lo = mid + 1
  }
  if (found >= 0 && spreadOf(list[found].range) === spread) return found
  // A spread holding only an image, or the tail of a long sentence: the
  // reader is still in the sentence that began before it.
  return found > 0 ? found - 1 : (found === 0 ? 0 : list.length - 1)
}

// ── chapters inside a section ─────────────────────────────────────────────
function markChapters(doc) {
  const entries = state.book.toc.filter(e => e.spine === state.spine)
  const heads = new Set()
  for (const entry of entries) {
    let anchor = entry.fragment ? doc.getElementById(entry.fragment) : null
    if (!anchor && !entry.fragment) anchor = doc.body?.querySelector('h1,h2,h3')
    if (!anchor) continue
    const heading = anchor.matches('h1,h2,h3,h4') ? anchor : (anchor.querySelector('h1,h2,h3,h4') ?? anchor.closest('h1,h2,h3,h4'))
    const block = (heading ?? anchor).closest('div,section,article,h1,h2,h3,h4,p') ?? anchor
    // A chapter always starts a new page. Empty elements just before it (a
    // page-number marker, a spacer) go with it, or they strand a blank page.
    let breaker = block
    while (breaker.previousElementSibling && isEmpty(breaker.previousElementSibling)) breaker = breaker.previousElementSibling
    if (breaker !== doc.body && hasContentBefore(doc, breaker)) breaker.style.setProperty('break-before', 'column')
    if (heading && !heads.has(heading)) {
      heads.add(heading)
      heading.classList.add('lf-ch')
      const first = nextParagraph(heading)
      if (first) {
        first.classList.add('lf-first')
        // A book that sets its own initial letter keeps it; two drop caps is none.
        const lead = first.firstElementChild
        if (lead && first.firstChild === lead && lead.textContent.trim().length <= 2) first.classList.add('lf-own-initial')
      }
    }
  }
}

function isEmpty(element) {
  return !element.textContent.trim() && !element.querySelector('img,svg,video,hr,table')
}

function hasContentBefore(doc, element) {
  const range = doc.createRange()
  range.setStart(doc.body, 0)
  range.setEndBefore(element)
  return range.toString().trim().length > 0 || range.cloneContents().querySelector?.('img,svg') != null
}

function nextParagraph(heading) {
  const walker = heading.ownerDocument.createTreeWalker(heading.ownerDocument.body, NodeFilter.SHOW_ELEMENT)
  walker.currentNode = heading
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (heading.contains(n)) continue
    if (n.matches('h1,h2,h3,h4')) return null
    if (n.matches('p') && n.textContent.trim().length > 1) return n
  }
  return null
}

// ── highlights, the voice, notes ──────────────────────────────────────────
function rangeFor(cfi) {
  try {
    const parts = CFI.parse(cfi)
    const index = CFI.fake.toIndex((parts.parent ?? parts).shift())
    if (index !== state.spine) return null
    return CFI.toRange(state.doc, parts, filter)
  } catch (_) { return null }
}

function paintHighlights() {
  const win = state.doc?.defaultView
  if (!win?.CSS?.highlights) return
  const groups = { y: [], g: [], b: [], r: [] }
  state.ranges = []
  for (const h of state.highlights) {
    const range = rangeFor(h.range)
    if (!range || !groups[h.colour]) continue
    groups[h.colour].push(range)
    state.ranges.push({ id: h.id, range, note: h.note })
  }
  for (const [c, ranges] of Object.entries(groups)) win.CSS.highlights.set(`lf-${c}`, new win.Highlight(...ranges))
  placeNotes()
}

function placeNotes() {
  const doc = state.doc
  if (!doc) return
  doc.querySelectorAll('[data-leaf="note"]').forEach(n => n.remove())
  const scroller = doc.scrollingElement ?? doc.documentElement
  for (const item of state.ranges ?? []) {
    if (!item.note) continue
    // The dot follows the last word of the note's first sentence.
    const first = state.sentences.find(s => intersects(s.range, item.range))
    const rects = [...(first?.range ?? item.range).getClientRects()].filter(r => r.width > 0)
    const last = rects.at(-1)
    if (!last) continue
    const dot = doc.createElementNS('http://www.w3.org/1999/xhtml', 'span')
    dot.setAttribute('data-leaf', 'note')
    dot.setAttribute('aria-hidden', 'true')
    const size = parseFloat(getComputedStyle(doc.body).fontSize) * 0.32
    dot.style.left = `${last.right + scroller.scrollLeft + size * 0.5}px`
    dot.style.top = `${last.top + scroller.scrollTop + last.height * 0.2}px`
    doc.body.append(dot)
  }
}

function paintVoice() {
  const win = state.doc?.defaultView
  if (!win?.CSS?.highlights) return
  const s = state.speaking
  const sentence = s && s.spine === state.spine ? state.sentences[s.index] : null
  if (sentence) win.CSS.highlights.set('lf-speaking', new win.Highlight(sentence.range))
  else win.CSS.highlights.delete('lf-speaking')
  const word = sentence && s.word >= 0 ? wordRanges(sentence)[s.word] : null
  if (word) win.CSS.highlights.set('lf-word', new win.Highlight(word.range))
  else win.CSS.highlights.delete('lf-word')
}

// ── where the reader is ───────────────────────────────────────────────────
function cfiAt(range, collapseToStart = true) {
  const r = range.cloneRange()
  if (collapseToStart) r.collapse(true)
  return CFI.joinIndir(CFI.fake.fromIndex(state.spine), CFI.fromRange(r, filter))
}

function report() {
  if (!state.doc || !state.sentences.length) {
    // A cover, a plate, a section of pictures: still a place in the book, so
    // opening it records one. Without it the book moved to Reading now with
    // no position, and the library called it New.
    let cfi = CFI.fake.fromIndex(state.spine) + ''
    if (state.doc?.body) {
      try {
        const start = state.doc.createRange()
        start.setStart(state.doc.body, 0)
        cfi = cfiAt(start)
      } catch (_) { /* keep the section's own */ }
    }
    post('relocate', { spine: state.spine, cfi, ...pageNumbers(), atStart: $('prev').disabled, atEnd: $('next').disabled, text: '' })
    return
  }
  const index = firstSentenceOn(state.spread)
  const sentence = state.sentences[index]
  let cfi = cfiAt(sentence.range)
  if (spreadOf(sentence.range) !== state.spread) {
    // Nothing begins on this spread in a sentence — an illustration, a
    // chapter's opening image. Where the reader is, is what is drawn first.
    const caret = state.doc.caretRangeFromPoint?.(2, 2)
    if (caret && state.doc.body.contains(caret.startContainer)) {
      try { cfi = cfiAt(caret) } catch (_) { /* keep the sentence */ }
    }
  }
  const marked = state.bookmarks.find(b => { const r = rangeFor(b.position); return r && spreadOf(r) === state.spread })
  post('relocate', {
    spine: state.spine, cfi, sentence: index, text: sentence.text.slice(0, 280),
    bookmark: marked?.id ?? null, ...pageNumbers(), atStart: $('prev').disabled, atEnd: $('next').disabled,
  })
}

function pageNumbers() {
  const words = state.book.sectionWords ?? []
  const column = (state.width - state.gap * (state.cols - 1)) / state.cols
  const body = state.doc?.body ? getComputedStyle(state.doc.body) : null
  const fontSize = body ? parseFloat(body.fontSize) : state.prefs.size
  const lineHeight = body ? parseFloat(body.lineHeight) / fontSize : LINE_SPACING[state.prefs.spacing]
  const fallback = measuredWordsPerPage({ columnWidth: column, height: state.height, fontSize, lineHeight })
  const perPage = wordsPerPage([...state.laidOut.values()], fallback)
  const before = pagesBefore({ spine: state.spine, sections: state.book.sections, words, laidOut: state.laidOut, perPage })
  return pageRange({ before, columns: state.columns ?? 1, spread: state.spread, cols: state.cols })
}

function showFoot() {
  const place = state.place
  if (!place) return
  if (state.fixture && state.book?.id === 'totc') {
    const single = state.cols === 1
    $('foot-left').textContent = single ? '31' : '27'
    $('foot-right').textContent = single ? '' : '28'
    $('running-left').textContent = single ? 'THE MAIL' : 'A TALE OF TWO CITIES'
    $('running-right').textContent = single ? '' : 'THE MAIL'
    document.querySelector('.lf-running').style.gridTemplateColumns = single ? '1fr' : '1fr 1fr'
    $('rail').style.width = '66%'
    return
  }
  $('foot-left').textContent = place.left
  const numbers = pageNumbers()
  const pages = numbers.pageLast > numbers.pageFirst ? `Pages ${numbers.pageFirst}–${numbers.pageLast}` : `Page ${numbers.pageFirst}`
  $('foot-right').textContent = `${pages} · ${place.percent}%`
  $('running-left').textContent = state.book?.title?.toUpperCase() ?? ''
  $('running-right').textContent = place.left.split(' · ')[0].toUpperCase()
  $('rail').style.width = `${(place.fraction * 100).toFixed(2)}%`
}

// ── loading ───────────────────────────────────────────────────────────────
function sectionUrl(index) {
  const href = state.book.sections[index].href.split('/').map(encodeURIComponent).join('/')
  return `leaf://reader/book/${encodeURIComponent(state.book.id)}/${href}`
}

async function loadSection(index, target) {
  const token = ++state.token
  state.spine = index
  state.doc = null
  state.sentences = []
  await new Promise(resolve => {
    frame.addEventListener('load', resolve, { once: true })
    frame.src = sectionUrl(index)
  })
  if (token !== state.token) return false
  const doc = frame.contentDocument
  if (!doc) { post('blank', { reason: 'no document', spine: index, href: state.book.sections[index]?.href }); return false }
  state.doc = doc
  doc.documentElement.setAttribute('lang', doc.documentElement.getAttribute('lang') || doc.documentElement.getAttribute('xml:lang') || state.book.language || 'en')
  markChapters(doc)
  // Opened from the library, the page room may not be showing yet. Columns
  // laid out at no width are one word wide, and the place found in them is
  // somewhere in the thousandth column; wait for the page's real size.
  await whenSized()
  if (token !== state.token) return false
  layout()
  // The book faces are app resources. The first layout loads @font-face;
  // repaginate after the face arrives so the saved CFI lands on its real page.
  if (state.prefs.face === 'literata' || state.prefs.face === 'serif')
    await doc.fonts?.load(`${state.prefs.size}px Literata`)
  await doc.fonts?.ready
  if (token !== state.token) return false
  layout()
  state.sentences = buildSentences(doc)
  wire(doc)
  paintHighlights()
  paintVoice()
  go(target)
  return true
}

function go(target) {
  if (!target || target === 'start') scrollTo(0)
  else if (target === 'end') scrollTo(state.spreads - 1)
  else if (target.cfi) {
    const range = rangeFor(target.cfi)
    scrollTo(range ? spreadOf(range) : 0)
  } else if (target.sentence != null) {
    const s = state.sentences[Math.min(target.sentence, state.sentences.length - 1)]
    scrollTo(s ? spreadOf(s.range) : 0)
  } else if (target.fragment) {
    const el = state.doc.getElementById(target.fragment)
    const r = el ? visibleRangeFrom(el) : null
    scrollTo(el ? spreadOf(r) : 0)
  }
  report()
  checkDrawn()
}

// After every move: is anything drawn on this spread? A spread with no text
// and no picture is reported with everything needed to explain it, so the
// next "sometimes it opens blank" explains itself in the journal.
function checkDrawn() {
  const doc = state.doc
  if (!doc?.body) return
  const inside = r => (r.width > 0 || r.height > 0) && r.right > 0 && r.left < state.width && r.bottom > 0 && r.top < state.height
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT)
  const range = doc.createRange()
  let text = null
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!n.nodeValue.trim()) continue
    range.selectNodeContents(n)
    if ([...range.getClientRects()].some(inside)) { text = n; break }
  }
  const picture = [...doc.querySelectorAll('img, svg, video')].some(e => inside(e.getBoundingClientRect()))
  const colour = text ? getComputedStyle(text.parentElement).color : ''
  if (text || picture) {
    if (text && colour === getComputedStyle(page).backgroundColor) post('blank', { reason: 'text in the paper colour', ...drawnDetail(colour) })
    return
  }
  post('blank', { reason: 'nothing drawn on this spread', ...drawnDetail(colour) })
}

function drawnDetail(colour) {
  return {
    spine: state.spine, href: state.book.sections[state.spine]?.href, words: sectionWords(state.spine),
    columns: state.columns, stride: Math.round(state.stride), spread: state.spread, spreads: state.spreads,
    width: state.width, height: state.height, view: [view.clientWidth, view.clientHeight],
    colour: colour || getComputedStyle(state.doc.body).color, theme: state.prefs.theme,
  }
}

function relayout() {
  if (!state.doc || !viewSized()) return
  // Every change keeps your place: return to the spread holding the
  // sentence the reader was on, not the same page number.
  const anchor = state.sentences.length ? firstSentenceOn(state.spread) : -1
  applyPageAttributes()
  layout()
  paintHighlights()
  paintVoice()
  if (anchor >= 0) scrollTo(spreadOf(state.sentences[anchor].range))
  else scrollTo(Math.min(state.spread, state.spreads - 1))
  report()
}

async function turn(direction) {
  if (!state.doc) return
  const list = linear()
  if (direction > 0) {
    if (state.spread < state.spreads - 1) { scrollTo(state.spread + 1); report(); checkDrawn() }
    else { const next = list.find(i => i > state.spine); if (next != null) await loadSection(next, 'start') }
  } else {
    if (state.spread > 0) { scrollTo(state.spread - 1); report(); checkDrawn() }
    else { const prev = [...list].reverse().find(i => i < state.spine); if (prev != null) await loadSection(prev, 'end') }
  }
}

// ── the reader's hands ────────────────────────────────────────────────────
function frameRect(rect) {
  const outer = frame.getBoundingClientRect()
  return { x: Math.round(outer.left + rect.left), y: Math.round(outer.top + rect.top), width: Math.round(rect.width), height: Math.round(rect.height) }
}

// Whether two ranges share any text: neither ends before the other begins.
function intersects(a, b) {
  const endsBefore = b.comparePoint(a.endContainer, a.endOffset) < 0 ||
    (a.endContainer === b.startContainer && a.endOffset === b.startOffset)
  const startsAfter = b.comparePoint(a.startContainer, a.startOffset) > 0 ||
    (a.startContainer === b.endContainer && a.startOffset === b.endOffset)
  return !endsBefore && !startsAfter
}

function selectionDetail(selection) {
  const range = selection.getRangeAt(0)
  const touched = []
  state.sentences.forEach((s, i) => { if (intersects(s.range, range)) touched.push(i) })
  if (!touched.length) return null
  const first = state.sentences[touched[0]], last = state.sentences[touched.at(-1)]
  const whole = state.doc.createRange()
  whole.setStart(first.range.startContainer, first.range.startOffset)
  whole.setEnd(last.range.endContainer, last.range.endOffset)
  const rects = [...range.getClientRects()].filter(r => r.width > 0)
  return {
    range: CFI.joinIndir(CFI.fake.fromIndex(state.spine), CFI.fromRange(whole, filter)),
    start: cfiAt(first.range), sentence: touched[0], spine: state.spine,
    firstRange: CFI.joinIndir(CFI.fake.fromIndex(state.spine), CFI.fromRange(first.range, filter)),
    firstText: first.text,
    text: touched.map(i => state.sentences[i].text).join(' '),
    selected: selection.toString(),
    rect: frameRect(rects[0] ?? range.getBoundingClientRect()),
  }
}

function wire(doc) {
  wirePageGestures(doc, { turn, isActive: () => state.doc === doc,
    selectionChanged() {
      const selection = doc.getSelection()
      try {
        const detail = selectionDetail(selection)
        if (detail) post('select', detail)
      } catch (error) { console.error('leaf: touch selection', error) }
    },
  })
  doc.addEventListener('mouseup', () => setTimeout(() => {
    const selection = doc.getSelection()
    if (selection && !selection.isCollapsed && selection.rangeCount) {
      try {
        const detail = selectionDetail(selection)
        if (detail) post('select', detail)
      } catch (error) { console.error('leaf: selection', error) }
    }
  }, 0))
  doc.addEventListener('click', event => {
    const link = event.target.closest?.('a[href]')
    if (link) { event.preventDefault(); followLink(link); return }
    const selection = doc.getSelection()
    if (selection && !selection.isCollapsed) return
    const caret = doc.caretRangeFromPoint?.(event.clientX, event.clientY)
    const hit = caret && (state.ranges ?? []).find(item => item.range.isPointInRange(caret.startContainer, caret.startOffset))
    if (hit) {
      const rects = [...hit.range.getClientRects()].filter(r => r.width > 0)
      const rect = rects.find(r => event.clientX >= r.left && event.clientX <= r.right && event.clientY >= r.top && event.clientY <= r.bottom) ?? rects[0]
      post('highlight', { id: hit.id, rect: frameRect(rect) })
    } else post('dismiss')
  })
  doc.addEventListener('keydown', keys)
  doc.addEventListener('dragstart', event => event.preventDefault())
}

function followLink(link) {
  const href = link.getAttribute('href')
  let url
  try { url = new URL(href, state.doc.baseURI) } catch (_) { return }
  if (url.protocol !== 'leaf:') { post('open-uri', { uri: url.href }); return }
  const prefix = `/book/${encodeURIComponent(state.book.id)}/`
  const path = decodeURIComponent(url.pathname.startsWith(prefix) ? url.pathname.slice(prefix.length) : url.pathname)
  const index = state.book.sections.findIndex(s => s.href === path)
  if (index < 0) return
  const fragment = url.hash ? decodeURIComponent(url.hash.slice(1)) : null
  if (index === state.spine) go(fragment ? { fragment } : 'start')
  else loadSection(index, fragment ? { fragment } : 'start')
}

function keys(event) {
  const target = event.target
  if (target?.closest?.('input, textarea, [contenteditable]')) return
  if (event.ctrlKey || event.metaKey || event.altKey) return
  switch (event.key) {
    case 'ArrowRight': case 'PageDown': case ' ':
      event.preventDefault(); turn(1); break
    case 'ArrowLeft': case 'PageUp':
      event.preventDefault(); turn(-1); break
    case 'Escape':
      state.doc?.getSelection()?.removeAllRanges(); post('dismiss'); break
    case 'l': case 'L':
      event.preventDefault(); post('key', { key: 'l' }); break
  }
}

wirePageGestures(document, { turn, selectionChanged() {} })
document.addEventListener('keydown', keys)
$('prev').addEventListener('click', () => turn(-1))
$('next').addEventListener('click', () => turn(1))
$('continue').addEventListener('click', () => turn(1))
new ResizeObserver(() => {
  if (state.doc && viewSized() && (Math.floor(view.clientWidth) !== state.width || Math.floor(view.clientHeight) !== state.height)) relayout()
}).observe(view)

// ── the application's side ────────────────────────────────────────────────
window.leaf = {
  async open(payload) {
    state.fixture = !!payload.fixture
    state.fixtureDark = !!payload.fixtureDark
    page.dataset.fixture = String(state.fixture)
    state.book = payload.book
    state.debug = !!payload.debug
    state.laidOut.clear()
    state.prefs = { ...state.prefs, ...(payload.prefs ?? {}) }
    state.highlights = payload.highlights ?? []
    state.bookmarks = payload.bookmarks ?? []
    state.place = null
    state.speaking = null
    this.colours(payload.colours ?? {})
    applyPageAttributes()
    const position = payload.position
    let index = firstLinear()
    if (position) {
      try { index = CFI.fake.toIndex([...(CFI.parse(position).parent ?? CFI.parse(position))][0]) } catch (_) { index = firstLinear() }
    }
    if (!(index >= 0 && index < state.book.sections.length)) index = firstLinear()
    await loadSection(index, position ? { cfi: position } : 'start')
    this.status(null)
    if (state.fixture && state.book.id === 'totc') {
      const here = state.doc?.querySelector('.leaf-here')
      if (here) {
        const range = state.doc.createRange()
        range.selectNodeContents(here)
        scrollTo(spreadOf(range))
      }
    }
    page.focus()
  },
  prefs(prefs) { state.prefs = { ...state.prefs, ...prefs }; relayout() },
  colours({ paper, text, quiet, dink }) {
    const root = document.documentElement.style
    if (paper) root.setProperty('--auto-paper', paper)
    if (text) root.setProperty('--auto-text', text)
    if (quiet) root.setProperty('--auto-quiet', quiet)
    if (dink != null) state.dink = !!dink
    applyPageAttributes()
    if (state.doc) { const s = state.doc.getElementById('leaf-style'); if (s) s.textContent = sectionStyle(); paintVariables() }
  },
  place(place) { state.place = place; showFoot() },
  highlights(list) { state.highlights = list; paintHighlights() },
  bookmarks(list) { state.bookmarks = list; report() },
  next() { return turn(1) },
  prev() { return turn(-1) },
  async goTo(cfi) {
    try {
      const parts = CFI.parse(cfi)
      const index = CFI.fake.toIndex([...(parts.parent ?? parts)][0])
      if (index === state.spine) go({ cfi }); else await loadSection(index, { cfi })
    } catch (_) { /* a stale position is not an error */ }
  },
  async goToChapter(spine, fragment) {
    if (spine === state.spine) go(fragment ? { fragment } : 'start')
    else await loadSection(spine, fragment ? { fragment } : 'start')
  },
  clearSelection() { state.doc?.getSelection()?.removeAllRanges() },
  fixtureSelect() {
    if (!state.fixture || !state.doc) return
    const target = state.doc.querySelector('.leaf-here')
    if (!target) return
    const range = state.doc.createRange()
    range.selectNodeContents(target)
    const selection = state.doc.getSelection()
    selection.removeAllRanges()
    selection.addRange(range)
    const detail = selectionDetail(selection)
    if (detail) post('select', detail)
  },
  focus() { page.focus() },
  status(text) { const s = $('status'); s.textContent = text ?? ''; s.hidden = !text },

  // read aloud: the application drives, the page shows where the voice is
  async sentence(spine, index) {
    if (spine !== state.spine) { if (!(await loadSection(spine, { sentence: index }))) return null }
    if (index < 0) index = state.sentences.length + index
    const s = state.sentences[index]
    if (!s) return { spine, index, count: state.sentences.length, done: true }
    state.speaking = { spine, index, word: -1 }
    paintVoice()
    // The page follows the voice.
    const spread = spreadOf(s.range)
    if (spread !== state.spread) { scrollTo(spread); report() }
    return { spine, index, count: state.sentences.length, text: s.text, words: wordRanges(s).map(w => w.text), cfi: cfiAt(s.range) }
  },
  sentenceAt(cfi) {
    const range = rangeFor(cfi)
    if (!range) return null
    // The first sentence that has not ended before the point.
    const i = state.sentences.findIndex(s => range.comparePoint(s.range.endContainer, s.range.endOffset) > 0 ||
      s.range.comparePoint(range.startContainer, range.startOffset) === 0)
    return i < 0 ? null : { spine: state.spine, index: i }
  },
  currentSentence() { return state.sentences.length ? { spine: state.spine, index: firstSentenceOn(state.spread) } : { spine: state.spine, index: 0 } },
  word(index) { if (state.speaking) { state.speaking.word = index; paintVoice() } },
  stopVoice() { state.speaking = null; paintVoice() },
}

post('ready')
