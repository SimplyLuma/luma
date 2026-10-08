# SPDX-License-Identifier: Apache-2.0
"""Pure-logic tests for the clickable artist/album markup
(`luma_tide.credit_markup`).

This module deliberately has no GTK dependency (see its docstring), so these
tests exercise it directly without a display, a GLib main loop, or `gi`. It
takes the track's real, structured `artists`/`album_artists` tuples directly
(as `model.py`/`metadata.py` now provide — see `test_metadata.py` and
`test_library.py`'s multi-artist coverage), never a raw string to guess apart.
"""
from __future__ import annotations

import unittest

from luma_tide.credit_markup import (
    build_credit_markup,
    escape_markup,
    parse_link_uri,
    resolve_artist_name,
)


class ResolveArtistNameTests(unittest.TestCase):
    def test_resolve_artist_name_is_case_insensitive(self) -> None:
        known = ["Radiohead", "Fable Radio"]
        self.assertEqual(resolve_artist_name("radiohead", known), "Radiohead")
        self.assertEqual(resolve_artist_name("RADIOHEAD", known), "Radiohead")

    def test_resolve_artist_name_returns_none_when_unknown(self) -> None:
        self.assertIsNone(resolve_artist_name("Some Guest", ["Radiohead"]))

    def test_resolve_artist_name_returns_none_for_blank(self) -> None:
        self.assertIsNone(resolve_artist_name("   ", ["Radiohead"]))


class CreditMarkupTests(unittest.TestCase):
    def test_known_artist_becomes_a_link(self) -> None:
        markup = build_credit_markup(["Radiohead"], ["Radiohead"])
        self.assertEqual(markup, '<a href="artist:Radiohead"><span underline="none">Radiohead</span></a>')

    def test_unknown_artist_is_plain_non_clickable_text(self) -> None:
        markup = build_credit_markup(["Some Guest"], ["Radiohead"])
        self.assertEqual(markup, "Some Guest")
        self.assertNotIn("<a", markup)

    def test_each_artist_in_a_real_multi_artist_list_is_its_own_link(self) -> None:
        # The structural case this module exists for: `Track.artists` already
        # carries the full ordered list (see metadata.py's `_multi_text`), so
        # every one of them gets its own link — no delimiter-guessing here.
        markup = build_credit_markup(
            ["Artist A", "Artist B"], ["Artist A", "Artist B"]
        )
        self.assertEqual(
            markup,
            '<a href="artist:Artist A"><span underline="none">Artist A</span></a>, <a href="artist:Artist B"><span underline="none">Artist B</span></a>',
        )

    def test_the_comma_separator_between_artists_is_not_inside_a_link(self) -> None:
        markup = build_credit_markup(["Artist A", "Artist B"], ["Artist A", "Artist B"])
        artist_a, separator, artist_b = markup.partition(", ")
        self.assertEqual(separator, ", ")
        self.assertNotIn("<a", separator)

    def test_a_mix_of_known_and_unknown_artists_links_only_the_known_one(self) -> None:
        # e.g. a featured artist who has never been an `album_artist`
        # grouping on their own is correctly not (yet) a real destination.
        markup = build_credit_markup(["Artist A", "Guest Feature"], ["Artist A"])
        self.assertIn('<a href="artist:Artist A"><span underline="none">Artist A</span></a>', markup)
        self.assertIn(", Guest Feature", markup)
        self.assertNotIn('href="artist:Guest Feature"', markup)

    def test_album_is_appended_as_a_link_with_a_middle_dot(self) -> None:
        markup = build_credit_markup(
            ["Radiohead"], ["Radiohead"], album="OK Computer", album_artist="Radiohead"
        )
        artist_part, _, album_part = markup.partition(" · ")
        self.assertEqual(artist_part, '<a href="artist:Radiohead"><span underline="none">Radiohead</span></a>')
        scheme, artist, name = parse_link_uri(
            album_part.removeprefix('<a href="').split('"', 1)[0]
        )
        self.assertEqual((scheme, artist, name), ("album", "Radiohead", "OK Computer"))

    def test_falls_back_to_first_artist_for_album_link_when_no_album_artist(self) -> None:
        markup = build_credit_markup(["Some Guest"], [], album="Mixtape")
        self.assertIn('href="album:Some Guest\x1fMixtape"', markup)

    def test_ampersand_in_artist_name_is_escaped(self) -> None:
        markup = build_credit_markup(["Simon & Garfunkel"], [])
        self.assertNotIn("Simon & Garfunkel", markup)
        self.assertIn("Simon &amp; Garfunkel", markup)

    def test_quote_in_album_link_href_is_escaped(self) -> None:
        markup = build_credit_markup(["Weird Al"], [], album='"Weird Al" Yankovic')
        self.assertIn("&quot;Weird Al&quot; Yankovic", markup)

    def test_escape_markup_covers_all_five_xml_entities(self) -> None:
        self.assertEqual(
            escape_markup("""&<>"'"""), "&amp;&lt;&gt;&quot;&apos;"
        )

    def test_no_artists_and_no_album_is_empty_markup(self) -> None:
        self.assertEqual(build_credit_markup([], []), "")


class LinkUriParsingTests(unittest.TestCase):
    def test_parses_an_artist_link(self) -> None:
        self.assertEqual(parse_link_uri("artist:Radiohead"), ("artist", "Radiohead", ""))

    def test_parses_an_album_link(self) -> None:
        self.assertEqual(
            parse_link_uri("album:Radiohead\x1fOK Computer"),
            ("album", "Radiohead", "OK Computer"),
        )

    def test_rejects_an_unknown_scheme(self) -> None:
        with self.assertRaises(ValueError):
            parse_link_uri("https://example.invalid")

    def test_rejects_a_malformed_album_link_missing_the_album(self) -> None:
        with self.assertRaises(ValueError):
            parse_link_uri("album:Radiohead")


if __name__ == "__main__":
    unittest.main()
