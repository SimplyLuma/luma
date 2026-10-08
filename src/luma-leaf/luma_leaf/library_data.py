# SPDX-License-Identifier: Apache-2.0
"""Leaf library filtering and sorting, independent of GTK and storage."""
from __future__ import annotations

from collections.abc import Iterable

from .store import Book


def on_shelf(book: Book, shelf: str) -> bool:
    if shelf == "all":
        return True
    if shelf == "want":
        return book.shelf in ("want", "new")
    return book.shelf == shelf


def matches(book: Book, query: str) -> bool:
    words = query.casefold().split()
    haystack = " ".join((book.title, book.author, *book.authors)).casefold()
    return all(word in haystack for word in words)


def ordered(books: Iterable[Book], sort: str) -> list[Book]:
    result = list(books)
    if sort == "title":
        result.sort(key=lambda book: (book.sort_title, book.author.casefold()))
    elif sort == "author":
        result.sort(key=lambda book: (book.surname, book.sort_title))
    else:
        result.sort(key=lambda book: (-(book.last_read or 0), -book.added_at))
    return result


def counts(books: Iterable[Book], highlights: int = 0) -> dict[str, int]:
    records = list(books)
    return {"reading": sum(on_shelf(book, "reading") for book in records),
            "want": sum(on_shelf(book, "want") for book in records),
            "finished": sum(on_shelf(book, "finished") for book in records),
            "all": len(records), "hl": highlights}
