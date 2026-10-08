# SPDX-License-Identifier: Apache-2.0
"""The page's only way to reach anything: the `leaf:` URI scheme.

`leaf://reader/app/…` serves the page's own files; `leaf://reader/book/<id>/…`
serves a file from inside that book's archive. One host, so the page can reach
into the section it laid out; nothing on the network, no file:// access. Every
file from a book carries a Content-Security-Policy that forbids scripts, plugins
and network access, so an EPUB's own JavaScript never runs. (The frame cannot
simply be sandboxed without scripts: WebKit then refuses the page's own event
listeners on the book's document too.)
"""
from __future__ import annotations

from html.entities import html5
import mimetypes
import posixpath
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse
from xml.parsers import expat

import gi

gi.require_version("Soup", "3.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gio, GLib, Soup, WebKit

from .epub import Epub, EpubError
from .data_paths import data_directory

BOOK_POLICY = ("default-src leaf: data:; script-src 'none'; object-src 'none'; connect-src 'none'; "
               "frame-src 'none'; form-action 'none'; style-src leaf: 'unsafe-inline'; img-src leaf: data:")

_TYPES = {
    ".xhtml": "application/xhtml+xml", ".xht": "application/xhtml+xml", ".html": "application/xhtml+xml",
    ".htm": "application/xhtml+xml", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
    ".otf": "font/otf", ".ttf": "font/ttf", ".woff": "font/woff", ".woff2": "font/woff2", ".ncx": "application/xml",
    ".opf": "application/xml", ".xml": "application/xml", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
}


def reader_directory() -> Path:
    candidate = data_directory() / "reader"
    if (candidate / "reader.html").is_file():
        return candidate
    raise FileNotFoundError("Leaf's page is not installed")


_XHTML = {"application/xhtml+xml", "application/xml", "text/xml"}
_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}
_ENTITY = re.compile(r"&(?:([A-Za-z][A-Za-z0-9]*);|(#[0-9]+;|#[xX][0-9A-Fa-f]+;))?")


def _well_formed(data: bytes) -> str | None:
    """None when the document parses as XML; otherwise the parser's complaint."""
    parser = expat.ParserCreate()
    parser.UseForeignDTD(False)
    try:
        parser.Parse(data, True)
    except expat.ExpatError as error:
        return str(error)
    return None


def _repair_entities(text: str) -> str:
    """HTML's named entities as numbers, and a bare & as &amp;: what XML can read."""
    def replace(match: re.Match) -> str:
        name, numeric = match.group(1), match.group(2)
        if numeric:
            return match.group(0)
        if name is None:
            return "&amp;"
        if name in _XML_ENTITIES:
            return match.group(0)
        value = html5.get(name + ";")
        return "".join(f"&#{ord(c)};" for c in value) if value else "&amp;" + name + ";"
    return _ENTITY.sub(replace, text)


def section_document(data: bytes, media: str, name: str = "") -> tuple[bytes, str]:
    """A book's XHTML as WebKit can show it whole.

    WebKit reads XHTML as XML and stops at the first error, drawing its error
    box and nothing after it: one `&nbsp;` near the top of a chapter, or an
    unescaped "AT&T", and the page is empty below it. Such books are common.
    A section that is not well-formed has its entities repaired; if it still
    is not, it is served as HTML, whose parser recovers the way a browser does.
    A well-formed section is served byte for byte.
    """
    if media not in _XHTML:
        return data, media
    problem = _well_formed(data)
    if problem is None:
        return data, media
    text = data.decode("utf-8", errors="replace")
    repaired = _repair_entities(text).encode("utf-8")
    if _well_formed(repaired) is None:
        print(f"leaf: repaired entities in {name}: {problem}", file=sys.stderr, flush=True)
        return repaired, media
    print(f"leaf: {name} is not well-formed XML ({problem}); serving it as HTML", file=sys.stderr, flush=True)
    return data, "text/html"


class LeafScheme:
    def __init__(self, resolve_book) -> None:
        """`resolve_book(book_id)` returns the path of a readable EPUB or None."""
        self.resolve_book = resolve_book
        self.root = reader_directory()
        self._open: dict[str, Epub] = {}

    def register(self, context: WebKit.WebContext) -> None:
        context.register_uri_scheme("leaf", self._request)
        security = context.get_security_manager()
        security.register_uri_scheme_as_secure("leaf")
        security.register_uri_scheme_as_cors_enabled("leaf")

    def _book(self, book_id: str) -> Epub | None:
        if book_id not in self._open:
            path = self.resolve_book(book_id)
            if not path:
                return None
            try:
                self._open[book_id] = Epub(path)
            except EpubError:
                return None
            if len(self._open) > 4:
                self._open.pop(next(iter(self._open)))
        return self._open[book_id]

    def forget(self, book_id: str) -> None:
        self._open.pop(book_id, None)

    def _request(self, request: WebKit.URISchemeRequest) -> None:
        uri = urlparse(request.get_uri())
        parts = [unquote(part) for part in uri.path.split("/") if part]
        try:
            if uri.netloc != "reader" or not parts:
                raise FileNotFoundError(uri.path)
            if parts[0] == "app":
                relative = posixpath.normpath("/".join(parts[1:]))
                if relative.startswith("..") or relative.startswith("/"):
                    raise FileNotFoundError(relative)
                target = (self.root / relative).resolve()
                if self.root.resolve() not in target.parents and target != self.root.resolve():
                    raise FileNotFoundError(relative)
                data = target.read_bytes()
                media = _TYPES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                if target.suffix == ".html":
                    media = "text/html"
            elif parts[0] == "book" and len(parts) >= 3:
                book = self._book(parts[1])
                if book is None:
                    raise FileNotFoundError(parts[1])
                name = posixpath.normpath("/".join(parts[2:]))
                if name.startswith(".."):
                    raise FileNotFoundError(name)
                data = book.read(name)
                media = book.by_href.get(name).media_type if name in book.by_href else None
                media = media or _TYPES.get(posixpath.splitext(name)[1].lower(), "application/octet-stream")
                data, media = section_document(data, media, name)
            else:
                raise FileNotFoundError(uri.path)
            from_book = parts[0] == "book"
        except (OSError, KeyError) as error:
            # A section that cannot be served opens as an empty page; say which.
            print(f"leaf: cannot serve {request.get_uri()}: {error}", file=sys.stderr, flush=True)
            request.finish_error(GLib.Error.new_literal(Gio.io_error_quark(), f"Not found: {error}",
                                                        Gio.IOErrorEnum.NOT_FOUND))
            return
        stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
        response = WebKit.URISchemeResponse.new(stream, len(data))
        response.set_content_type(media)
        if from_book:
            headers = Soup.MessageHeaders.new(Soup.MessageHeadersType.RESPONSE)
            headers.append("Content-Security-Policy", BOOK_POLICY)
            response.set_http_headers(headers)
        request.finish_with_response(response)
