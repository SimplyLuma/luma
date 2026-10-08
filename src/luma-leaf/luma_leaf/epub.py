# SPDX-License-Identifier: Apache-2.0
"""What Leaf needs to know about an EPUB without laying it out.

Metadata, the reading order, the table of contents, the embedded cover, and
enough of each section's text to derive every figure the library shows —
chapter, percent, minutes left — from one saved position. The page itself is
rendered by WebKit; nothing here draws.

Section documents are parsed with a forgiving tree builder rather than an XML
parser: real EPUBs use HTML entities XML does not define, and a book must not
fall out of the library because its markup is imperfect. The tree keeps text
nodes exactly as a browser does, because an EPUB CFI counts them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import hashlib
import posixpath
import re
from urllib.parse import unquote, urldefrag
import xml.etree.ElementTree as ET
import zipfile

from . import sentences

_NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
}
_SKIP = {"head", "script", "style", "title", "template", "svg:title"}
_BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "dd", "dt",
          "section", "article", "header", "footer", "aside", "figcaption", "td", "th", "tr", "br", "hr",
          "table", "ul", "ol", "dl", "figure", "nav", "body"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
         "track", "wbr"}


class EpubError(ValueError):
    """The file is not an EPUB Leaf can read."""


# ── a small DOM ─────────────────────────────────────────────────────────────

@dataclass
class Node:
    tag: str | None                      # None for a text node
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)
    text: str = ""
    parent: "Node | None" = None

    def elements(self) -> list["Node"]:
        return [child for child in self.children if child.tag is not None]

    def find_id(self, identifier: str) -> "Node | None":
        if self.attrs.get("id") == identifier:
            return self
        for child in self.children:
            if child.tag is not None:
                found = child.find_id(identifier)
                if found is not None:
                    return found
        return None

    def first(self, tag: str) -> "Node | None":
        for child in self.children:
            if child.tag == tag:
                return child
            if child.tag is not None:
                found = child.first(tag)
                if found is not None:
                    return found
        return None


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag.lower(), {k.lower(): (v or "") for k, v in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(node)
        if tag.lower() not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag.lower(), {k.lower(): (v or "") for k, v in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        for depth in range(len(self.stack) - 1, 0, -1):
            if self.stack[depth].tag == tag:
                del self.stack[depth:]
                return

    def handle_data(self, data):
        parent = self.stack[-1]
        if parent.children and parent.children[-1].tag is None:
            parent.children[-1].text += data
        else:
            parent.children.append(Node(None, text=data, parent=parent))


def parse_document(data: bytes) -> Node:
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"<\?xml[^>]*\?>", "", text, count=1)
    builder = _TreeBuilder()
    builder.feed(text)
    builder.close()
    return builder.root


def document_element(root: Node) -> Node:
    """The <html> element: step /4 of a CFI's document part starts below it."""
    for child in root.children:
        if child.tag == "html":
            return child
    return root


# ── text model ──────────────────────────────────────────────────────────────

@dataclass
class SectionText:
    text: str
    offsets: dict[int, int]          # id(text node) -> offset of its first character
    anchors: dict[str, int]          # element id -> offset where it starts

    @property
    def words(self) -> int:
        return sentences.words(self.text)

    def words_before(self, offset: int) -> int:
        return sentences.words(self.text[:max(0, offset)])


def section_text(root: Node) -> SectionText:
    parts: list[str] = []
    offsets: dict[int, int] = {}
    anchors: dict[str, int] = {}
    length = 0

    def emit(value: str) -> None:
        nonlocal length
        parts.append(value)
        length += len(value)

    def walk(node: Node) -> None:
        if node.tag is None:
            offsets[id(node)] = length
            emit(node.text)
            return
        if node.tag in _SKIP:
            return
        if "id" in node.attrs:
            anchors.setdefault(node.attrs["id"], length)
        block = node.tag in _BLOCK
        if block and parts and not parts[-1].endswith((" ", "\n")):
            emit("\n")
        for child in node.children:
            walk(child)
        if block and parts and not parts[-1].endswith((" ", "\n")):
            emit("\n")

    walk(root)
    return SectionText("".join(parts), offsets, anchors)


# ── the package ─────────────────────────────────────────────────────────────

@dataclass
class ManifestItem:
    id: str
    href: str              # resolved against the archive root
    media_type: str
    properties: str = ""


@dataclass
class Section:
    index: int
    href: str
    media_type: str
    linear: bool


@dataclass
class TocEntry:
    label: str
    href: str
    depth: int
    spine: int = -1
    fragment: str = ""


@dataclass
class Metadata:
    title: str
    authors: list[str]
    language: str
    identifier: str
    publisher: str = ""
    description: str = ""


def _text(element) -> str:
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


class Epub:
    def __init__(self, path: str, *, max_resource_size: int | None = None,
                 max_archive_size: int | None = None, max_members: int | None = None) -> None:
        self.path = path
        try:
            self.zip = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as error:
            raise EpubError(f"Not a readable EPUB: {error}") from None
        try:
            members = self.zip.infolist()
            if max_members is not None and len(members) > max_members:
                raise EpubError('This book has too many archive members to preview.')
            if max_archive_size is not None and sum(item.file_size for item in members) > max_archive_size:
                raise EpubError('This book exceeds the EPUB preview limit.')
            names = set()
            for item in members:
                if max_resource_size is not None and item.file_size > max_resource_size:
                    raise EpubError('A section of this book exceeds the EPUB preview limit.')
                name = item.filename.casefold()
                if name in names:
                    raise EpubError('The book contains ambiguous duplicate sections.')
                names.add(name)
            self._load_package()
        except Exception:
            self.zip.close()
            raise

    def _load_package(self) -> None:
        self._names = {name: name for name in self.zip.namelist()}
        self._lower = {name.lower(): name for name in self.zip.namelist()}
        try:
            container = ET.fromstring(self.zip.read("META-INF/container.xml"))
        except (KeyError, ET.ParseError):
            raise EpubError("The EPUB has no container.") from None
        rootfile = container.find(".//c:rootfile", _NS)
        if rootfile is None or not rootfile.get("full-path"):
            raise EpubError("The EPUB names no package document.")
        self.opf_path = rootfile.get("full-path")
        try:
            self.opf = ET.fromstring(self.read(self.opf_path))
        except (KeyError, ET.ParseError) as error:
            raise EpubError(f"The package document is unreadable: {error}") from None
        self.base = posixpath.dirname(self.opf_path)
        self.manifest: dict[str, ManifestItem] = {}
        for item in self.opf.findall("opf:manifest/opf:item", _NS):
            href = self.resolve(self.base, item.get("href", ""))
            self.manifest[item.get("id", "")] = ManifestItem(item.get("id", ""), href,
                                                             item.get("media-type", ""), item.get("properties", ""))
        self.by_href = {item.href: item for item in self.manifest.values()}
        spine = self.opf.find("opf:spine", _NS)
        self.sections: list[Section] = []
        for itemref in (spine.findall("opf:itemref", _NS) if spine is not None else []):
            item = self.manifest.get(itemref.get("idref", ""))
            if item is None:
                continue
            self.sections.append(Section(len(self.sections), item.href, item.media_type,
                                         itemref.get("linear", "yes") != "no"))
        if not self.sections:
            raise EpubError("The EPUB has no reading order.")
        self._spine_ncx = spine.get("toc") if spine is not None else None
        self.metadata = self._metadata()
        self._texts: dict[int, SectionText] = {}
        self._trees: dict[int, Node] = {}

    # archive access
    @staticmethod
    def resolve(base: str, href: str) -> str:
        path, _fragment = urldefrag(href)
        return posixpath.normpath(posixpath.join(base, unquote(path))).lstrip("/")

    def read(self, name: str) -> bytes:
        actual = self._names.get(name) or self._lower.get(name.lower())
        if actual is None:
            raise KeyError(name)
        return self.zip.read(actual)

    def has(self, name: str) -> bool:
        return name in self._names or name.lower() in self._lower

    # metadata
    def _metadata(self) -> Metadata:
        meta = self.opf.find("opf:metadata", _NS)
        if meta is None:
            meta = ET.Element("metadata")
        title = _text(meta.find("dc:title", _NS)) or posixpath.splitext(posixpath.basename(self.path))[0]
        authors = [_text(e) for e in meta.findall("dc:creator", _NS) if _text(e)]
        language = _text(meta.find("dc:language", _NS)) or "en"
        unique = self.opf.get("unique-identifier")
        identifier = ""
        for element in meta.findall("dc:identifier", _NS):
            if unique and element.get("id") == unique:
                identifier = _text(element)
                break
            identifier = identifier or _text(element)
        return Metadata(title=title, authors=authors, language=language, identifier=identifier,
                        publisher=_text(meta.find("dc:publisher", _NS)),
                        description=_text(meta.find("dc:description", _NS)))

    def book_id(self) -> str:
        """Stable across devices: the book's own identifier, else its bytes."""
        # "urn:uuid:X" and "X" are the same identifier: Calibre's catalogue says
        # one and the EPUB it serves often says the other.
        source = re.sub(r"^urn:uuid:", "", self.metadata.identifier.strip(), flags=re.IGNORECASE)
        if source:
            return "b" + hashlib.sha256(("epub:" + source).encode()).hexdigest()[:24]
        digest = hashlib.sha256()
        with open(self.path, "rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
        return "f" + digest.hexdigest()[:24]

    def cover(self) -> tuple[bytes, str] | None:
        """The embedded cover image and its media type, if the book has one."""
        candidates = [item for item in self.manifest.values() if "cover-image" in item.properties.split()]
        meta = self.opf.find("opf:metadata", _NS)
        if not candidates and meta is not None:
            for element in meta.findall("opf:meta", _NS):
                if element.get("name") == "cover" and element.get("content") in self.manifest:
                    candidates.append(self.manifest[element.get("content")])
        if not candidates:
            for reference in self.opf.findall("opf:guide/opf:reference", _NS):
                if reference.get("type", "").lower() in ("cover", "cover-image"):
                    href = self.resolve(self.base, reference.get("href", ""))
                    item = self.by_href.get(href)
                    if item is not None and item.media_type.startswith("image/"):
                        candidates.append(item)
        for item in candidates:
            if item.media_type.startswith("image/") and self.has(item.href):
                return self.read(item.href), item.media_type
        return None

    # contents
    def toc(self) -> list[TocEntry]:
        entries = self._nav_toc() or self._ncx_toc()
        spine_of = {section.href: section.index for section in self.sections}
        for entry in entries:
            path, fragment = urldefrag(entry.href)
            entry.spine = spine_of.get(path, -1)
            entry.fragment = fragment
        entries = [entry for entry in entries if entry.spine >= 0]
        if not entries:
            entries = [TocEntry(f"Section {section.index + 1}", section.href, 0, section.index)
                       for section in self.sections if section.linear]
        return entries

    def _nav_toc(self) -> list[TocEntry]:
        nav = next((item for item in self.manifest.values() if "nav" in item.properties.split()), None)
        if nav is None or not self.has(nav.href):
            return []
        root = parse_document(self.read(nav.href))
        base = posixpath.dirname(nav.href)
        toc_nav = None
        stack = [root]
        while stack:
            node = stack.pop()
            if node.tag == "nav" and "toc" in node.attrs.get("epub:type", "").split():
                toc_nav = node
                break
            stack.extend(child for child in reversed(node.children) if child.tag is not None)
        if toc_nav is None:
            toc_nav = root.first("nav")
        if toc_nav is None:
            return []
        entries: list[TocEntry] = []

        def walk_list(ol: Node, depth: int) -> None:
            for li in ol.elements():
                if li.tag != "li":
                    continue
                link = next((c for c in li.elements() if c.tag in ("a", "span")), None)
                if link is not None and link.attrs.get("href"):
                    label = " ".join(_node_text(link).split())
                    entries.append(TocEntry(label, self.resolve(base, link.attrs["href"]) +
                                            ("#" + urldefrag(link.attrs["href"])[1] if "#" in link.attrs["href"] else ""),
                                            depth))
                for child in li.elements():
                    if child.tag in ("ol", "ul"):
                        walk_list(child, depth + 1)

        top = toc_nav.first("ol") or toc_nav.first("ul")
        if top is not None:
            walk_list(top, 0)
        return entries

    def _ncx_toc(self) -> list[TocEntry]:
        item = self.manifest.get(self._spine_ncx or "") or next(
            (i for i in self.manifest.values() if i.media_type == "application/x-dtbncx+xml"), None)
        if item is None or not self.has(item.href):
            return []
        try:
            ncx = ET.fromstring(self.read(item.href))
        except ET.ParseError:
            return []
        base = posixpath.dirname(item.href)
        entries: list[TocEntry] = []

        def walk(parent, depth):
            for point in parent.findall("ncx:navPoint", _NS):
                label = _text(point.find("ncx:navLabel/ncx:text", _NS))
                content = point.find("ncx:content", _NS)
                src = content.get("src", "") if content is not None else ""
                if src:
                    href = self.resolve(base, src) + ("#" + urldefrag(src)[1] if "#" in src else "")
                    entries.append(TocEntry(label, href, depth))
                walk(point, depth + 1)

        nav_map = ncx.find("ncx:navMap", _NS)
        if nav_map is not None:
            walk(nav_map, 0)
        return entries

    # section text
    def tree(self, index: int) -> Node:
        if index not in self._trees:
            self._trees[index] = parse_document(self.read(self.sections[index].href))
        return self._trees[index]

    def text(self, index: int) -> SectionText:
        if index not in self._texts:
            html = document_element(self.tree(index))
            body = html.first("body") or html
            self._texts[index] = section_text(body)
        return self._texts[index]


def _node_text(node: Node) -> str:
    if node.tag is None:
        return node.text
    return "".join(_node_text(child) for child in node.children)
