# SPDX-License-Identifier: Apache-2.0
"""Bring books from the watched folder into the library.

The folder (default ~/Books) is read, never written. A book that disappears
from it is marked missing rather than removed, so its highlights and position
survive a moved folder or an unplugged drive.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .epub import Epub, EpubError
from .position import count
from .store import Library, cache_directory

EXTENSIONS = (".epub",)


def default_folder() -> Path:
    override = os.environ.get("LEAF_BOOKS")
    return Path(override) if override else Path.home() / "Books"


def cover_path(book_id: str, media_type: str) -> Path:
    extension = {"image/png": ".png", "image/gif": ".gif", "image/webp": ".webp", "image/svg+xml": ".svg"}.get(media_type, ".jpg")
    return cache_directory() / "covers" / f"{book_id}{extension}"


def import_file(library: Library, path: Path, *, source: str = "device", remote_url: str | None = None) -> str | None:
    try:
        book = Epub(str(path))
        book_id = book.book_id()
        existing = library.book(book_id)
        stats = existing.stats if existing and existing.stats and existing.path == str(path) else count(book).to_json()
        cover = None
        found = book.cover()
        if found is not None:
            data, media_type = found
            target = cover_path(book_id, media_type)
            if not target.exists() or hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(data).digest():
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix(target.suffix + ".part")
                temporary.write_bytes(data)
                os.replace(temporary, target)
            cover = str(target)
        library.upsert_book(id=book_id, title=book.metadata.title, authors=book.metadata.authors,
                            language=book.metadata.language, identifier=book.metadata.identifier,
                            path=str(path), source=source, remote_url=remote_url, cover=cover, stats=stats)
        return book_id
    except (EpubError, OSError, KeyError, ValueError):
        return None


def scan(library: Library, folder: Path | None = None) -> tuple[int, int]:
    """Import every book under the folder; returns (found, newly missing)."""
    folder = folder or default_folder()
    seen: set[str] = set()
    paths: list[Path] = []
    if folder.is_dir():
        for directory, names, files in os.walk(folder):
            names[:] = [name for name in names if not name.startswith(".")]
            for name in files:
                if name.lower().endswith(EXTENSIONS) and not name.startswith("."):
                    paths.append(Path(directory) / name)
    for path in sorted(paths):
        book_id = import_file(library, path)
        if book_id:
            seen.add(book_id)
    gone = {book.id for book in library.books()
            if book.source == "device" and book.path and book.id not in seen and not Path(book.path).exists()}
    library.mark_missing(gone)
    return len(seen), len(gone)
