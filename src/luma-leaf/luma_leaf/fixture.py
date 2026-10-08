# SPDX-License-Identifier: Apache-2.0
"""Studio v70 books in a disposable, in-memory library.

LUMA_LEAF_FIXTURE points at the JSON copied into the conform run.  No part of
this source opens Leaf's library, scans Books, starts a folder watch, or reaches
a server.  Edits made while exercising the fixture live only until exit.
"""
from __future__ import annotations

import json
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile

from . import sentences
from .epub import Epub
from .position import count
from .store import Library


def _epub_bytes(record: dict, document: dict) -> bytes:
    """Make the mockup's public-domain excerpts readable without a disk file."""
    book_id = record["id"]
    excerpts = document["chapters"].get(book_id, [])
    groups = document["contents"].get(book_id)
    labels = [(part, title) for part, titles in groups for title in titles] if groups else [
        (chapter.get("part", ""), chapter.get("t") or chapter["n"]) for chapter in excerpts]
    if not labels:
        labels = [("", "A note")]
    sections = [
        ('title', f'<section><p>{escape(record["a"])}</p><h1>{escape(record["t"])}</h1>'
                  '<p>❦</p><p>Leaf Editions</p></section>')
    ]
    for index, (part, title) in enumerate(labels):
        chapter = excerpts[index] if index < len(excerpts) else None
        paragraphs = (chapter["ps"] if chapter else
                      ["Not typeset here. Every book opens in Leaf with the same reading controls."])
        rendered = []
        has_saved_sentence = any(paragraph.startswith("§") for paragraph in paragraphs)
        before_saved_sentence = has_saved_sentence
        for paragraph_index, paragraph in enumerate(paragraphs):
            here = paragraph.startswith("§")
            if here:
                before_saved_sentence = False
            spans = sentences.split(paragraph.lstrip("§"))
            marks = []
            for number, sentence in enumerate(spans):
                content = escape(sentence)
                if paragraph_index == 0 and number == 0 and sentence[:1].isupper():
                    # v70 floats an explicit initial inside the first sentence.
                    # Keep the character inside its sentence span so selection,
                    # speech and the saved CFI still refer to the full sentence.
                    content = f'<span class="leaf-drop">{escape(sentence[0])}</span>{escape(sentence[1:])}'
                marks.append(f'<span class="leaf-sentence{(" leaf-here" if here and number == 0 else "")}">'
                             f'{content}</span>')
            marks = ' '.join(marks)
            classes = []
            if paragraph_index == 0:
                classes.append('lf-first')
                if paragraphs[0][:1].isupper():
                    classes.append('lf-own-initial')
            if here:
                classes.append('leaf-fixture-here')
            elif before_saved_sentence:
                classes.append('leaf-fixture-before')
            marker = f' class="{" ".join(classes)}"' if classes else ''
            rendered.append(f'<p{marker}>{marks}</p>')
        number = chapter.get("n", "") if chapter else ""
        intro_class = ' class="leaf-fixture-intro leaf-fixture-target"' if has_saved_sentence else ' class="leaf-fixture-intro"'
        part_markup = (f'<span class="leaf-fixture-part">{escape(part)}</span>' if part else '')
        intro = (f'<header{intro_class}>{part_markup}'
                 f'<span class="leaf-fixture-number">{escape(number)}</span>'
                 f'<h2>{escape(title)}</h2><i class="leaf-fixture-ornament">❦</i></header>')
        body = f'<section>{intro}{"".join(rendered)}</section>'
        sections.append((f'chapter-{index + 1}', body))

    opf_items = '\n'.join(f'<item id="{name}" href="text/{name}.xhtml" media-type="application/xhtml+xml"/>'
                          for name, _body in sections)
    spine = ''.join(f'<itemref idref="{name}"/>' for name, _body in sections)
    navigation = ''.join(f'<li><a href="text/chapter-{index + 1}.xhtml">{escape(title)}</a></li>'
                         for index, (_part, title) in enumerate(labels))
    opf = ('<?xml version="1.0" encoding="utf-8"?>'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:identifier id="uid">fixture:{escape(book_id)}</dc:identifier>'
           f'<dc:title>{escape(record["t"])}</dc:title><dc:creator>{escape(record["a"])}</dc:creator>'
           '<dc:language>en</dc:language></metadata><manifest>'
           '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
           f'{opf_items}</manifest><spine>{spine}</spine></package>')
    nav = ('<?xml version="1.0" encoding="utf-8"?>'
           '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
           f'<head><title>Contents</title></head><body><nav epub:type="toc"><ol>{navigation}</ol>'
           '</nav></body></html>')
    memory = BytesIO()
    with ZipFile(memory, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", '<?xml version="1.0"?>'
                         '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                         '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                         'media-type="application/oebps-package+xml"/></rootfiles></container>')
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/nav.xhtml", nav)
        for name, body in sections:
            archive.writestr(f"OEBPS/text/{name}.xhtml", '<?xml version="1.0" encoding="utf-8"?>'
                             '<html xmlns="http://www.w3.org/1999/xhtml" lang="en">'
                             f'<head><title>{escape(record["t"])}</title></head><body>{body}</body></html>')
    return memory.getvalue()


class FixtureLibrary(Library):
    def __init__(self, path: str | Path) -> None:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        records = document.get("books")
        if not isinstance(records, list) or not records:
            raise ValueError("Leaf's fixture needs its v70 books")
        ids = [str(record["id"]) for record in records]
        if len(ids) != len(set(ids)):
            raise ValueError("Leaf's fixture has duplicate book IDs")
        # SQLite's special :memory: name never creates a file.  In particular,
        # it cannot open or migrate Nick's library.sqlite3.
        super().__init__(Path(":memory:"))
        self.document = document
        self.samples = {str(record["id"]): record for record in records}
        self.artwork = Path(path).parent / "leaf-covers"
        self._books = {}
        for index, record in enumerate(records):
            book_id = str(record["id"])
            self._books[book_id] = BytesIO(_epub_bytes(record, document))
            stats = count(Epub(self._books[book_id])).to_json()
            self.upsert_book(id=book_id, title=record["t"], authors=[record["a"]],
                             language="en", identifier=f"fixture:{book_id}", path=None,
                             cover=None, stats=stats)
            with self.db:
                self.db.execute("UPDATE books SET shelf=?, added_at=? WHERE id=?",
                                (record["st"], float(len(records) - index), book_id))
        for index, raw in enumerate(document.get("collections", ())):
            with self.db:
                self.db.execute("INSERT INTO collections (id, name, created_at) VALUES (?,?,?)",
                                (f"fixture-collection-{index}", raw["name"], 0.0))
        # Chapter II is the opening state in v70. This CFI names its first
        # paragraph in the in-memory archive (title page, Chapter I, II).
        self.set_position("totc", "epubcfi(/6/6!/4/2/10/2/1:0)", device="fixture")

    def books(self):
        return [replace(book, path=self._books[book.id]) if book.id in self._books else book
                for book in super().books()]

    def book(self, book_id: str):
        book = super().book(book_id)
        return replace(book, path=self._books[book_id]) if book_id in self._books else book

    def sample(self, book_id: str) -> dict | None:
        record = self.samples.get(book_id)
        if record is None:
            return None
        picture = self.artwork / f"{book_id}.png"
        return {**record, "art": str(picture) if picture.is_file() else None}


class SilentSpeaker:
    """The conformance fixture never opens the desktop's speech service."""

    voices = ()
    voice = None
    error = "No fixture voice"
    available = False

    def refresh_voices(self, _preferred_id=None):
        return self.voices

    def set_speed(self, _speed: float) -> None:
        pass

    def set_voice(self, _voice) -> None:
        pass

    def cancel(self) -> None:
        pass

    def close(self) -> None:
        pass
