// SPDX-License-Identifier: Apache-2.0
// Page numbers for a book that is only ever laid out one section at a time.
//
// A page number is the pages of the sections before this one, from words per
// page, plus the spreads turned inside it. Words per page is the book's, not the
// section's: a chapter opener that holds "CHAPTER ONE" and a rule is one page
// of two words, and dividing the eleven thousand words before it by two gave
// page 3,945 of a 600-page book. The figure comes from the sections laid out
// so far that hold a real page of text, and from the page's own measure until
// one has been.

// A section this short says nothing about how many words fit on a page.
export const DENSE_WORDS = 120

// Words that fit on one page of this measure, from the type alone: about 0.48em
// per character in a book face and 5.7 characters per word with its space.
export function measuredWordsPerPage({ columnWidth, height, fontSize, lineHeight }) {
  if (!(columnWidth > 0 && height > 0 && fontSize > 0)) return 0
  const lines = Math.max(1, Math.floor(height / (fontSize * (lineHeight || 1.6))))
  const perLine = Math.max(1, columnWidth / (fontSize * 0.48) / 5.7)
  return lines * perLine
}

// `sections` is every laid-out section as { words, columns }; only dense ones count.
export function wordsPerPage(sections, fallback) {
  let words = 0, columns = 0
  for (const s of sections) {
    if (s.words >= DENSE_WORDS && s.columns >= 1) { words += s.words; columns += s.columns }
  }
  if (columns > 0) return Math.max(1, words / columns)
  return Math.max(1, fallback || 250)
}

// Pages before a section. Every section of the reading order starts a new
// page, so each is at least one, a picture or an opener included; a section
// already laid out at this measure counts what it actually took.
export function pagesBefore({ spine, sections, words, laidOut, perPage }) {
  let pages = 0
  for (let i = 0; i < spine && i < sections.length; i++) {
    if (!sections[i].linear) continue
    const known = laidOut.get(i)
    pages += known ? known.columns : Math.max(1, Math.ceil((words[i] ?? 0) / Math.max(1, perPage)))
  }
  return pages
}

// The pages on screen: the first and last page of the spread.
export function pageRange({ before, columns, spread, cols }) {
  const first = before + spread * cols + 1
  const last = Math.min(before + Math.max(1, columns), first + cols - 1)
  return { pageFirst: first, pageLast: Math.max(first, last) }
}
