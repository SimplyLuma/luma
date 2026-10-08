# SPDX-License-Identifier: Apache-2.0
"""v70 Notes data. The database and every edit stay in memory."""
from __future__ import annotations

from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import sqlite3

from .notes_backend import NotesStore


class DocumentParser(HTMLParser):
    """Convert fixture HTML to the existing text/range model.

    Original HTML remains in raw_notes, including checklist and presence
    semantics needed by the document view.
    """
    def __init__(self, base: Path):
        super().__init__(convert_charrefs=True)
        self.base, self.text = base, ""
        self.runs, self.stack, self.lists = [], [], []
        self.pictures = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "br":
            self.text += "\n"
            return
        if tag == "img":
            path = (self.base / attrs["src"]).resolve()
            if not path.is_relative_to(self.base.resolve()):
                raise ValueError("fixture image escapes the fixture directory")
            name = hashlib.sha256(path.read_bytes()).hexdigest() + path.suffix
            self.pictures[name] = path
            start = len(self.text)
            self.text += "\ufffc"
            self.runs.append({"start": start, "end": start + 1, "style": "image", "src": name, "width": 620})
            return
        if tag in ("ul", "ol"):
            self.lists.append("numbered" if tag == "ol" else "checklist" if attrs.get("class") == "check" else "bulleted")
        style = {"b": "bold", "strong": "bold", "i": "italic", "em": "italic", "u": "underline",
                 "h2": "heading", "blockquote": "quote", "a": "link"}.get(tag)
        if tag == "li" and self.lists:
            style = self.lists[-1]
        self.stack.append((tag, len(self.text), style, attrs))

    def handle_endtag(self, tag):
        if tag in ("p", "h2", "li", "blockquote", "figure") and not self.text.endswith("\n"):
            self.text += "\n"
        for i in range(len(self.stack) - 1, -1, -1):
            item, start, style, attrs = self.stack[i]
            if item != tag:
                continue
            self.stack.pop(i)
            if style and len(self.text) > start:
                run = {"start": start, "end": len(self.text), "style": style}
                if style == "link":
                    run["href"] = attrs.get("href", "")
                self.runs.append(run)
                if tag == "li" and "done" in attrs.get("class", "").split():
                    self.runs.append({"start": start, "end": len(self.text), "style": "checked"})
            break
        if tag in ("ul", "ol") and self.lists:
            self.lists.pop()

    def handle_data(self, data):
        self.text += data


class FixtureStore(NotesStore):
    """Disposable memory library. Input JSON and images are only read."""
    fixture = True

    def __init__(self, path: Path):
        self.path = Path(path)
        self.document = json.loads(self.path.read_text(encoding="utf-8"))
        self.selected = str(self.document["selected"])
        self.raw_notes = {str(n["id"]): n for n in self.document["notes"]}
        import os
        if os.environ.get('LUMA_NOTES_LIVE_SETTLED') == '1':
            for raw in self.raw_notes.values():
                if raw.get('live') and 'id="n-live"' in raw['html']:
                    before, closing = raw['html'].rsplit('</p>', 1)
                    raw['html'] = before + self.document['live_text'] + '</p>' + closing
                    raw['at'] = 'Just now'
        self.folder_meta, self.people = self.document["folders"], self.document["people"]
        self.picture_paths = {}
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()
        with self.connection:
            for order, (key, raw) in enumerate(self.folder_meta.items()):
                self.connection.execute("INSERT INTO folders VALUES(?,?,?,?,?)",
                                        (key, raw["name"], 0, order, "f" + key in self.document["open"]))
            for order, raw in enumerate(self.document["notes"]):
                parser = DocumentParser(self.path.parent)
                parser.feed(raw["html"])
                # HTML block endings separate blocks, not an extra final paragraph.
                if parser.text.endswith('\n'):
                    parser.text = parser.text[:-1]
                    for run in parser.runs:
                        run['end'] = min(run['end'], len(parser.text))
                self.picture_paths.update(parser.pictures)
                self.connection.execute("INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?,?)",
                                        (str(raw["id"]), raw["t"], parser.text, json.dumps(parser.runs),
                                         raw["at"], raw["at"], raw.get("f"), raw.get("pinned", False), order, None))
            for raw in self.document["notes"]:
                if raw.get("parent"):
                    self.connection.execute("INSERT INTO note_hierarchy VALUES(?,?)",
                                            (str(raw["id"]), str(raw["parent"])))
            for folder_id, raw in self.folder_meta.items():
                if raw.get('parent'):
                    self.connection.execute('INSERT INTO metadata VALUES(?,?)',
                                            ('folder-parent:' + folder_id, raw['parent']))


def source_from_environment(environment=None):
    import os
    env = os.environ if environment is None else environment
    path = env.get("LUMA_NOTES_FIXTURE")
    return FixtureStore(Path(path)) if path else NotesStore()
