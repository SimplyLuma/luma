# SPDX-License-Identifier: Apache-2.0
"""The page never reads as blank, and its page numbers stay sane.

Opens tests/fixtures.write_reader_epub() in the real application and checks,
on the page WebKit actually laid out:

- an SVG-wrapped cover is drawn, and opening it records a position, so the
  book is on Reading now with a place rather than "New";
- a chapter opener split from its text (CHAPTER ONE and a rule, two words)
  shows a sane page number, keeps Next, and shows Continue at rest;
- Continue leads to the chapter's text;
- a publisher's `color: #000 !important` and plain black on elements Leaf's
  own rule did not name both give way to the Night page's ink;
- a picture alone on its page is drawn at page size, without Continue;
- XHTML using HTML entities XML does not define is shown whole;
- a saved position past the end of the book opens on real content;
- the page-number arithmetic in pages.js.

    dbus-run-session -- xvfb-run -a env PYTHONPATH=. python3 tests/runtime_reader_pages.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

home = tempfile.TemporaryDirectory()
for variable, folder in (("HOME", ""), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"),
                         ("XDG_CONFIG_HOME", "config"), ("LEAF_BOOKS", "Books")):
    os.environ[variable] = str(Path(home.name) / folder)
    Path(os.environ[variable]).mkdir(parents=True, exist_ok=True)
os.environ.setdefault("GTK_A11Y", "none")
os.environ.setdefault("WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS", "1")

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from tests.fixtures import AFTER, BODY, COVER, OPENER, PLATE, write_reader_epub  # noqa: E402

write_reader_epub(Path(os.environ["LEAF_BOOKS"]) / "cases.epub")

# What is on the spread the reader sees, measured in the laid-out page.
PROBE = """(() => {
  const f = document.getElementById('frame'), d = f.contentDocument, v = document.getElementById('view')
  const W = v.clientWidth, H = v.clientHeight
  const inside = r => (r.width > 0 || r.height > 0) && r.right > 0 && r.left < W && r.bottom > 0 && r.top < H
  let text = ''
  if (d && d.body) {
    const walk = d.createTreeWalker(d.body, NodeFilter.SHOW_TEXT), range = d.createRange()
    for (let n = walk.nextNode(); n && text.length < 400; n = walk.nextNode()) {
      if (!n.nodeValue.trim()) continue
      range.selectNodeContents(n)
      if ([...range.getClientRects()].some(inside)) text += n.nodeValue.replace(/\\s+/g, ' ').trim() + ' | '
    }
  }
  const pictures = d ? [...d.querySelectorAll('img, svg')].map(e => e.getBoundingClientRect()).filter(inside).map(r => Math.round(r.height)) : []
  return { href: f.src.split('/').pop(), foot: document.getElementById('foot-right').textContent,
    next: document.getElementById('next').disabled, cont: !document.getElementById('continue').hidden,
    opening: !document.getElementById('status').hidden,
    brief: document.getElementById('page').dataset.brief, text, pictures, height: H,
    parsererror: !!(d && d.querySelector('parsererror')) }
})()"""

COLOURS = """(() => {
  const d = document.getElementById('frame').contentDocument, c = e => getComputedStyle(e)
  return { body: c(d.body).color, ink: c(d.querySelector('p.ink')).color, cite: c(d.querySelector('cite.plain')).color,
    shade: c(d.querySelector('.shade')).color, shadeBackground: c(d.querySelector('.shade')).backgroundColor }
})()"""

PAGES = """(async () => {
  const m = await import('./pages.js')
  return {
    // Steve Jobs, chapter 1's opener: 3,944 words before it, laid out in two columns.
    opener: m.pageRange({ before: m.pagesBefore({ spine: 2, sections: [{ linear: true }, { linear: true }],
      words: [0, 3944], laidOut: new Map(), perPage: m.wordsPerPage([{ words: 2, columns: 2 }, { words: 7783, columns: 84 }], 250) }),
      columns: 2, spread: 0, cols: 2 }),
    perPage: Math.round(m.wordsPerPage([{ words: 2, columns: 2 }, { words: 7783, columns: 84 }], 250)),
    fallback: Math.round(m.wordsPerPage([{ words: 2, columns: 2 }], 250)),
    before: m.pagesBefore({ spine: 3, sections: [{ linear: true }, { linear: false }, { linear: true }],
      words: [0, 500, 1000], laidOut: new Map([[2, { words: 1000, columns: 6 }]]), perPage: 250 }),
    measure: Math.round(m.measuredWordsPerPage({ columnWidth: 606, height: 316, fontSize: 19, lineHeight: 1.62 })),
  }
})()"""


def first_page(foot: str) -> int:
    match = re.search(r"Pages? (\d+)", foot)
    assert match, f"no page number in {foot!r}"
    return int(match.group(1))


def script(app, messages):
    """Yields ("until", predicate), ("js", expression) or ("wait", seconds); js results come back."""
    yield "until", lambda: app.window is not None and len(app.library.books()) == 1
    # Exercise reading at the desktop viewport even without an Xvfb window manager.
    app.window.set_size_request(1160, 760)
    yield "until", lambda: app.window.get_width() >= 1100 and app.window.get_height() >= 700
    book = app.library.books()[0]
    reader = app.window.reader
    reader.web.get_user_content_manager().connect(
        "script-message-received::leaf", lambda _m, value: messages.append(json.loads(value.to_string())))

    # The cover: drawn, and a place in the book.
    app.open_book(book.id)
    yield "until", lambda: reader.position.get("spine") == COVER
    state = yield "js", PROBE
    assert not state["opening"], f"opening label covered the loaded book: {state}"
    assert state["href"] == "cover.xhtml", state
    assert state["pictures"] and max(state["pictures"]) > state["height"] * 0.5, f"the cover is not drawn: {state}"
    assert first_page(state["foot"]) == 1, state
    yield "until", lambda: "!" in (app.library.book(book.id).position or "")
    stored = app.library.book(book.id)
    assert stored.shelf == "reading" and "!" in stored.position, (stored.shelf, stored.position)

    # The opener: CHAPTER ONE and a rule, two words in its own section.
    reader.run(f"leaf.goToChapter({OPENER}, null)")
    yield "until", lambda: reader.position.get("spine") == OPENER
    state = yield "js", PROBE
    assert "CHAPTER ONE" in state["text"], state
    assert state["next"] is False, f"Next is disabled on the opener: {state}"
    assert state["cont"] is True and state["brief"] == "true", f"no Continue on the opener: {state}"
    # 2,701 words of front matter are a few dozen pages, never 1,350 (2,701 / 2 words).
    assert 2 < first_page(state["foot"]) < 60, state

    # Continue goes on to the text.
    yield "js", "document.getElementById('continue').click()"
    yield "until", lambda: reader.position.get("spine") == BODY
    state = yield "js", PROBE
    assert "CHILDHOOD" in state["text"] and "Chapter the reader" in state["text"], state
    assert state["cont"] is False, state
    body_page = first_page(state["foot"])

    # Night: the page's ink beats the publisher's black, important or not.
    reader.set_pref("theme", "night")
    yield "wait", 0.5
    colours = yield "js", COLOURS
    night = "rgb(217, 209, 195)"
    assert colours["body"] == night, colours
    assert colours["ink"] == night and colours["cite"] == night and colours["shade"] == night, colours
    assert colours["shadeBackground"] in ("rgba(0, 0, 0, 0)", "transparent"), colours
    reader.set_pref("theme", "auto")

    # A picture alone on its page: drawn at page size; no Continue under it.
    reader.run(f"leaf.goToChapter({PLATE}, null)")
    yield "until", lambda: reader.position.get("spine") == PLATE
    state = yield "js", PROBE
    assert state["pictures"] and max(state["pictures"]) > state["height"] * 0.5, f"the plate is not drawn: {state}"
    assert state["cont"] is False and state["next"] is False, state
    assert first_page(state["foot"]) > body_page, (state, body_page)

    # HTML entities in XHTML: shown whole, not cut off at the first one.
    reader.run(f"leaf.goToChapter({AFTER}, null)")
    yield "until", lambda: reader.position.get("spine") == AFTER
    state = yield "js", PROBE
    assert state["parsererror"] is False, state
    # Everything from the first entity on used to be cut off.
    assert "Tester wrote to AT&T at once." in state["text"], state

    # A saved position past the end of the book opens on real content.
    app.window.show_library()
    app.library.set_position(book.id, "epubcfi(/6/80!/4/2/1:0)", device=reader.device, at=time.time() + 60)
    app.library.set_pref(f"here.{book.id}", "epubcfi(/6/80!/4/2/1:0)")
    yield "wait", 0.3
    reader.position = {}
    app.open_book(book.id)
    yield "until", lambda: reader.position.get("spine") == COVER
    state = yield "js", PROBE
    assert state["pictures"], f"a stale position opened on nothing: {state}"

    # The arithmetic.
    pages = yield "js", PAGES
    assert pages["perPage"] == 93, pages                      # 7,783 words over 84 columns; the opener is ignored
    assert pages["fallback"] == 250, pages                    # nothing dense laid out yet: the page's measure
    assert pages["opener"] == {"pageFirst": 45, "pageLast": 46}, pages   # never 3,945
    assert pages["before"] == 7, pages                        # one page, a skipped non-linear section, six laid out
    assert 90 <= pages["measure"] <= 140, pages

    blank = [m for m in messages if m.get("type") == "blank"]
    assert not blank, f"the page reported a blank spread: {blank}"


def main() -> int:
    # An exception inside a GTK callback is printed and swallowed; here it fails the run.
    raised: list[str] = []
    default_hook = sys.excepthook

    def hook(kind, error, trace):
        raised.append(f"{kind.__name__}: {error}")
        default_hook(kind, error, trace)
    sys.excepthook = hook

    from luma_leaf.application import LeafApplication
    app = LeafApplication()
    app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
    messages: list[dict] = []
    failure: list[BaseException] = []
    steps = script(app, messages)
    state = {"pending": None, "value": None, "waiting": False, "deadline": 0.0, "until": 0.0}

    def finish(error: BaseException | None = None) -> None:
        if error is not None:
            failure.append(error)
        app.quit()

    def advance(value=None) -> bool:
        try:
            request = steps.send(value)
        except StopIteration:
            finish()
            return GLib.SOURCE_REMOVE
        except BaseException as error:  # a failure must end the run, not hang it
            finish(error)
            return GLib.SOURCE_REMOVE
        kind, argument = request
        if kind == "wait":
            GLib.timeout_add(int(argument * 1000), lambda: advance() and False)
        elif kind == "js":
            app.window.reader.call(argument, lambda result: GLib.idle_add(lambda: advance(result) and False))
        elif kind == "until":
            deadline = time.monotonic() + 30

            def poll() -> bool:
                try:
                    ready = argument()
                except Exception:
                    ready = False
                if ready:
                    # Let the page settle: the relocate that set the spine precedes its layout's last paint.
                    GLib.timeout_add(400, lambda: advance() and False)
                    return GLib.SOURCE_REMOVE
                if time.monotonic() > deadline:
                    finish(AssertionError(f"timed out waiting in the reader test; page messages: {messages[-5:]}"))
                    return GLib.SOURCE_REMOVE
                return GLib.SOURCE_CONTINUE
            GLib.timeout_add(100, poll)
        return GLib.SOURCE_REMOVE

    app.connect_after("activate", lambda *_: GLib.timeout_add(200, lambda: advance() and False))
    status = app.run([])
    if failure:
        raise failure[0]
    if raised:
        raise AssertionError(f"exceptions in callbacks: {raised}")
    print("leaf: the page draws openers, pictures, entities and publisher black; page numbers stay sane")
    return status


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        home.cleanup()
