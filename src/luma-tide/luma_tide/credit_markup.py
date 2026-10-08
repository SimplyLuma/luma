# SPDX-License-Identifier: Apache-2.0
"""Pango markup for clickable artist and album credits.

`model.py` carries the real, structured `Track.artists` / `Track.album_artists`
tuples (see `metadata.py`'s `_multi_text`) — the full, ordered set of artists a
file's tags genuinely, structurally gave it, never a delimiter-guess at a
display string. This module turns that data into Pango markup: each artist
that resolves to a known library `album_artist` grouping becomes a clickable
``<a href="artist:NAME">`` span (case-insensitively matched, then rendered with
its canonical library casing); a name Tide cannot yet navigate to (not (yet)
local, or the tag credits someone who only ever appears as a featured artist)
renders as plain, clearly non-clickable text instead of a dead link. A trailing
album name, when given, is always a clickable ``<a href="album:ARTIST\\x1fALBUM">``
span.

This module has no GTK dependency, so the markup and href logic is unit
testable without a display or a GLib main loop. `application.py` renders the
markup on a `Gtk.Label` and, on that label's own ``activate-link`` signal,
parses the href with `parse_link_uri` and opens the matching `Place` on the
window's existing `NavigationTrail` (see `_link_activated`); it does not
invent a second navigation mechanism.
"""
from __future__ import annotations

from typing import Iterable, Mapping

_MARKUP_ESCAPES = (
    ("&", "&amp;"),
    ("<", "&lt;"),
    (">", "&gt;"),
    ('"', "&quot;"),
    ("'", "&apos;"),
)


def escape_markup(text: str) -> str:
    for needle, replacement in _MARKUP_ESCAPES:
        text = text.replace(needle, replacement)
    return text


def artist_index(known_artists: Iterable[str]) -> dict[str, str]:
    """Known artists keyed for `resolve_artist_name`, built once per library
    refresh so rendering a row costs a lookup, not a scan."""
    index: dict[str, str] = {}
    for candidate in known_artists:
        index.setdefault(candidate.strip().casefold(), candidate)
    return index


def resolve_artist_name(name: str, known_artists: Iterable[str]) -> str | None:
    """Case-insensitive exact match of `name` against known library artists.

    Returns the canonical (library-cased) name to navigate to, or None if
    this name isn't (yet) a known `album_artist` grouping — callers should
    render those as plain, clearly non-clickable text rather than a link
    that goes nowhere.
    """
    target = name.strip().casefold()
    if not target:
        return None
    if isinstance(known_artists, Mapping):
        # A prepared index (see `artist_index`): one lookup, however large
        # the library.
        return known_artists.get(target)
    for candidate in known_artists:
        if candidate.strip().casefold() == target:
            return candidate
    return None


def _link(href: str, text: str) -> str:
    """A credit link that reads as the text around it: Pango underlines an
    `<a>` by itself, so the span turns that off. Colour comes from the style
    sheet, which keeps a link the colour of its own line."""
    return f'<a href="{href}"><span underline="none">{text}</span></a>'


def build_credit_markup(
    artists: Iterable[str],
    known_artists: Iterable[str],
    *,
    album: str | None = None,
    album_artist: str | None = None,
) -> str:
    """Pango markup for a "credited artist(s) [· album]" line.

    `artists` is the track's real, ordered artist list — `Track.artists`, or
    any other already-structural tuple/list of names — never a raw display
    string to guess apart. Each entry that resolves against `known_artists`
    (see `resolve_artist_name`) becomes a clickable
    ``<a href="artist:NAME">`` span; an unresolvable entry is left as plain
    text. Multiple names are joined with ", " in their given order — Tide
    does not attempt to reproduce whatever original tag separator (" & ",
    " feat. ", ...) a single-value string might have used, since a
    structural multi-value tag never contained one to begin with.

    The trailing album name, when given, is always a clickable
    ``<a href="album:ARTIST\\x1fALBUM">`` span, keyed by `album_artist` (the
    album's own grouping artist) when given, else the first credited artist.
    """
    known = known_artists if isinstance(known_artists, Mapping) else list(known_artists)
    names = list(artists)
    segments: list[str] = []
    for index, name in enumerate(names):
        if index:
            segments.append(", ")
        escaped = escape_markup(name)
        resolved = resolve_artist_name(name, known)
        if resolved:
            href = escape_markup(f"artist:{resolved}")
            segments.append(_link(href, escaped))
        else:
            segments.append(escaped)
    if album:
        target_artist = album_artist or (names[0] if names else "")
        href = escape_markup(f"album:{target_artist}\x1f{album}")
        if segments:
            segments.append(" · ")
        segments.append(_link(href, escape_markup(album)))
    return "".join(segments)


def parse_link_uri(uri: str) -> tuple[str, str, str]:
    """Parse an `artist:NAME` or `album:ARTIST\\x1fALBUM` link href.

    Returns `(scheme, artist, album)` — `album` is "" for an artist link.
    Raises ValueError for anything else, so callers can safely ignore hrefs
    they don't recognize.
    """
    scheme, sep, value = uri.partition(":")
    if not sep or not value:
        raise ValueError(f"not a Tide link uri: {uri!r}")
    if scheme == "artist":
        return scheme, value, ""
    if scheme == "album":
        artist, _, album = value.partition("\x1f")
        if not album:
            raise ValueError(f"malformed album link uri: {uri!r}")
        return scheme, artist, album
    raise ValueError(f"unknown Tide link scheme: {uri!r}")
