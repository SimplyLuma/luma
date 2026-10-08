// SPDX-License-Identifier: Apache-2.0
// Paginated book input. Native selections/handles take priority over a turn;
// only a deliberate single-finger horizontal movement changes the spread.
export function wirePageGestures(doc, { turn, selectionChanged, isActive = () => true }) {
  let contact = null, holdTimer = 0, suppressClickUntil = 0, selectionTimer = 0
  const selected = () => !!doc.getSelection()?.rangeCount && !doc.getSelection().isCollapsed
  const cancelHold = () => { clearTimeout(holdTimer); holdTimer = 0 }
  const cancel = () => { cancelHold(); contact = null }
  const publish = () => { if (isActive() && selected()) selectionChanged() }
  const selectWord = (x, y) => {
    const caret = doc.caretRangeFromPoint?.(x, y)
    if (!caret || caret.startContainer.nodeType !== 3) return false
    const node = caret.startContainer, text = node.textContent
    let start = caret.startOffset, end = start
    const word = char => !!char && /[\p{L}\p{N}'’\u2010-\u2011-]/u.test(char)
    if (!word(text[start]) && start > 0 && word(text[start - 1])) start--
    end = start
    while (start > 0 && word(text[start - 1])) start--
    while (end < text.length && word(text[end])) end++
    if (end <= start) return false
    const range = doc.createRange(); range.setStart(node, start); range.setEnd(node, end)
    const selection = doc.getSelection(); selection.removeAllRanges(); selection.addRange(range)
    return !selection.isCollapsed
  }
  doc.addEventListener('touchstart', event => {
    cancel()
    if (!isActive() || event.touches.length !== 1 || selected()) return
    const touch = event.touches[0]
    contact = { id: touch.identifier, x: touch.clientX, y: touch.clientY,
                dx: 0, dy: 0, held: false }
    // Buttons/links retain their normal tap/hold behavior. The actual book text
    // can select a word even when WebKit's context menu is owned by Leaf.
    if (!event.target.closest?.('button,a,input,textarea,[contenteditable]')) {
      holdTimer = setTimeout(() => {
        if (!contact || !isActive()) return
        contact.held = selected() || selectWord(contact.x, contact.y)
        if (contact.held) { suppressClickUntil = Date.now() + 450; publish() }
      }, 500)
    }
  }, { passive: true })
  doc.addEventListener('touchmove', event => {
    if (!contact) return
    if (event.touches.length !== 1) { cancel(); return }
    const touch = [...event.touches].find(t => t.identifier === contact.id)
    if (!touch) { cancel(); return }
    contact.dx = touch.clientX - contact.x; contact.dy = touch.clientY - contact.y
    if (Math.hypot(contact.dx, contact.dy) > 12) cancelHold()
    if (!contact.held && !selected() && Math.abs(contact.dx) >= 40 &&
        Math.abs(contact.dx) > Math.abs(contact.dy) * 1.4) event.preventDefault()
  }, { passive: false })
  doc.addEventListener('touchend', event => {
    cancelHold()
    if (!contact) { publish(); return }
    const gesture = contact; contact = null
    const last = [...event.changedTouches].find(t => t.identifier === gesture.id)
    if (last) { gesture.dx = last.clientX - gesture.x; gesture.dy = last.clientY - gesture.y }
    if (!isActive() || event.touches.length || gesture.held || selected()) { publish(); return }
    if (Math.abs(gesture.dx) >= 50 && Math.abs(gesture.dx) > Math.abs(gesture.dy) * 1.4) {
      suppressClickUntil = Date.now() + 450
      event.preventDefault()
      turn(gesture.dx < 0 ? 1 : -1)
    }
  }, { passive: false })
  doc.addEventListener('touchcancel', cancel, { passive: true })
  doc.addEventListener('selectionchange', () => {
    clearTimeout(selectionTimer)
    selectionTimer = setTimeout(publish, 60)
  })
  doc.addEventListener('click', event => {
    if (Date.now() < suppressClickUntil) { event.preventDefault(); event.stopImmediatePropagation() }
  }, true)
  doc.defaultView?.addEventListener('pagehide', () => { cancel(); clearTimeout(selectionTimer) }, { once: true })
}
