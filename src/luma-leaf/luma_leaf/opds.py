# SPDX-License-Identifier: Apache-2.0
"""Libraries on a server: a Calibre content server or any OPDS catalogue.

A server library's books are listed under Where beside this device's. They are
never hidden when the server is out of reach — they dim, and say so. Opening
one streams it once into the cache; "Download to keep" copies it into the
books folder, where it becomes this device's.

Passwords are kept in the login keyring, never in Leaf's library file.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from .importer import import_file
from .store import Library, Source, cache_directory

ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/terms/}"
ACQUISITION = "http://opds-spec.org/acquisition"
IMAGE = ("http://opds-spec.org/image", "http://opds-spec.org/cover", "http://opds-spec.org/image/thumbnail",
         "http://opds-spec.org/thumbnail")
EPUB = "application/epub+zip"
MAX_FEEDS = 60
TIMEOUT = 15


class SourceError(RuntimeError):
    pass


@dataclass
class RemoteBook:
    entry_id: str
    title: str
    authors: list[str]
    download: str
    cover: str | None

    @property
    def book_id(self) -> str:
        # The same derivation Leaf uses for an EPUB's own identifier, so a book
        # downloaded from the server is recognised as the same book.
        identifier = re.sub(r"^urn:uuid:", "", self.entry_id.strip(), flags=re.IGNORECASE)
        return "b" + hashlib.sha256(("epub:" + identifier).encode()).hexdigest()[:24]


# ── credentials ─────────────────────────────────────────────────────────────

def _secret():
    import gi
    gi.require_version("Secret", "1")
    from gi.repository import Secret
    schema = Secret.Schema.new("org.projectluma.Leaf.Library", Secret.SchemaFlags.NONE,
                               {"source": Secret.SchemaAttributeType.STRING})
    return Secret, schema


def store_password(source_id: str, password: str) -> None:
    Secret, schema = _secret()
    Secret.password_store_sync(schema, {"source": source_id}, Secret.COLLECTION_DEFAULT, "Leaf library password", password, None)


def password_for(source_id: str) -> str | None:
    try:
        Secret, schema = _secret()
        return Secret.password_lookup_sync(schema, {"source": source_id}, None)
    except Exception:
        return None


def forget_password(source_id: str) -> None:
    try:
        Secret, schema = _secret()
        Secret.password_clear_sync(schema, {"source": source_id}, None)
    except Exception:
        pass


# ── the catalogue ───────────────────────────────────────────────────────────

def catalogue_url(kind: str, url: str) -> str:
    url = url.strip().rstrip("/")
    if not urlparse(url).scheme:
        url = "http://" + url
    if kind == "calibre" and not url.endswith("/opds"):
        url += "/opds"
    return url


def _fetch(url: str, username: str, password: str | None) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SourceError("A library address must start with http:// or https://.")
    headers = {"User-Agent": "Leaf/0.1 (Project Luma)", "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.1"}
    if username:
        token = base64.b64encode(f"{username}:{password or ''}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    try:
        with urlopen(Request(url, headers=headers), timeout=TIMEOUT) as response:
            return response.read(32 * 1024 * 1024)
    except HTTPError as error:
        if error.code == 401:
            raise SourceError("The library did not accept this name and password.") from None
        raise SourceError(f"The library answered with an error ({error.code}).") from None
    except (URLError, TimeoutError, OSError) as error:
        raise SourceError(f"The library could not be reached ({getattr(error, 'reason', error)}).") from None


def browse(source: Source) -> list[RemoteBook]:
    """Every EPUB the catalogue offers, following navigation and next pages."""
    password = password_for(source.id)
    start = catalogue_url(source.kind, source.url)
    queue, seen, books = [start], set(), {}
    while queue and len(seen) < MAX_FEEDS:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            feed = ET.fromstring(_fetch(url, source.username, password))
        except ET.ParseError:
            continue
        for link in feed.findall(f"{ATOM}link"):
            if link.get("rel") == "next" and link.get("href"):
                queue.append(urljoin(url, link.get("href")))
        for entry in feed.findall(f"{ATOM}entry"):
            download = cover = None
            for link in entry.findall(f"{ATOM}link"):
                rel, kind, href = link.get("rel", ""), link.get("type", ""), link.get("href")
                if not href:
                    continue
                if rel.startswith(ACQUISITION) and kind.split(";")[0] == EPUB:
                    download = urljoin(url, href)
                elif rel in IMAGE and (cover is None or "thumbnail" not in rel):
                    cover = urljoin(url, href)
                elif "atom+xml" in kind and "acquisition" not in rel and rel not in ("self", "up", "start", "search"):
                    # a navigation entry: a shelf, an author, "By newest"
                    target = urljoin(url, href)
                    if urlparse(target).netloc == urlparse(start).netloc:
                        queue.append(target)
            if download:
                entry_id = (entry.findtext(f"{ATOM}id") or download).strip()
                authors = [a.findtext(f"{ATOM}name", "").strip() for a in entry.findall(f"{ATOM}author")]
                books[entry_id] = RemoteBook(entry_id, (entry.findtext(f"{ATOM}title") or "Untitled").strip(),
                                             [a for a in authors if a], download, cover)
    if not seen:
        raise SourceError("The library could not be read.")
    return list(books.values())


def refresh(library: Library, source: Source) -> int:
    """List a server's books in the library; mark it out of reach if it is."""
    try:
        books = browse(source)
    except SourceError:
        library.set_reachable(source.id, False)
        raise
    library.set_reachable(source.id, True)
    covers = cache_directory() / "covers"
    covers.mkdir(parents=True, exist_ok=True)
    password = password_for(source.id)
    for remote in books:
        existing = library.book(remote.book_id)
        if existing is not None and existing.source == "device":
            continue   # this device already holds it
        cover = existing.cover if existing else None
        if remote.cover and not cover:
            target = covers / f"{remote.book_id}.jpg"
            try:
                target.write_bytes(_fetch(remote.cover, source.username, password))
                cover = str(target)
            except (SourceError, OSError):
                cover = None
        library.upsert_book(id=remote.book_id, title=remote.title, authors=remote.authors, language="",
                            identifier=remote.entry_id, path=None, source=source.id, remote_url=remote.download,
                            cover=cover, stats=None)
    return len(books)


def reachable(source: Source) -> bool:
    try:
        _fetch(catalogue_url(source.kind, source.url), source.username, password_for(source.id))
        return True
    except SourceError:
        return False


def stream(library: Library, book_id: str) -> str:
    """Fetch a server book into the cache for reading. Returns the book's id,
    which may change to the EPUB's own identifier once it is read."""
    book = library.book(book_id)
    if book is None or not book.remote_url:
        raise SourceError("This book is not on a server.")
    source = next((s for s in library.sources() if s.id == book.source), None)
    if source is None:
        raise SourceError("The library this book came from was removed.")
    target = cache_directory() / "stream" / f"{book_id}.epub"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        data = _fetch(book.remote_url, source.username, password_for(source.id))
        temporary = target.with_suffix(".part")
        temporary.write_bytes(data)
        os.replace(temporary, target)
    opened = import_file(library, target, source=source.id, remote_url=book.remote_url)
    if opened is None:
        raise SourceError("The downloaded book could not be read.")
    if opened != book_id and library.book(book_id) and library.book(book_id).path is None:
        with library.db:
            library.db.execute("DELETE FROM books WHERE id=? AND path IS NULL", (book_id,))
    return opened


def keep(library: Library, book_id: str, books_folder: Path) -> str:
    """Download to keep: the book joins this device's folder."""
    opened = stream(library, book_id) if not (library.book(book_id) and library.book(book_id).path) else book_id
    book = library.book(opened)
    source = next((s for s in library.sources() if s.id == book.source), None)
    folder = books_folder / re.sub(r"[^\w .-]", "_", source.name if source else "Downloads")
    folder.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^\w .-]", "_", f"{book.title} - {book.author}")[:150] + ".epub"
    destination = folder / name
    destination.write_bytes(Path(book.path).read_bytes())
    return import_file(library, destination) or opened

