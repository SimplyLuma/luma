#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""What a note is on disk: its formatting runs, its pictures, its Markdown."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from prairie_apps.notes_attachments import PictureError, attachments_directory, picture_kind, store_picture
from prairie_apps.notes_backend import NotesStore
from prairie_apps.notes_export import to_markdown

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64
NAME_RUN = {"start": 0, "end": 1, "style": "image"}


class ModelCase(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="notes-model-"))
        self.patch = mock.patch.dict(os.environ, {"HOME": str(self.home), "XDG_DATA_HOME": str(self.home / "d")})
        self.patch.start()
        self.store = NotesStore(self.home / "d" / "luma" / "notes" / "notes.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.patch.stop()


class PictureStorageTests(ModelCase):
    def test_a_picture_is_stored_once_under_its_content_address(self) -> None:
        first = store_picture(PNG)
        second = store_picture(PNG)
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}\.png$")
        self.assertEqual(sorted(path.name for path in attachments_directory().iterdir()), [first])
        self.assertEqual(os.stat(attachments_directory() / first).st_mode & 0o777, 0o600)

    def test_only_pictures_are_kept(self) -> None:
        self.assertEqual(picture_kind(b"RIFF\0\0\0\0WEBPVP8 "), "webp")
        with self.assertRaises(PictureError):
            store_picture(b"#!/bin/sh\necho not a picture\n")


class RunTests(ModelCase):
    def test_lists_levels_alignment_and_pictures_are_valid_runs(self) -> None:
        name = store_picture(PNG)
        note = self.store.create_note(title="T")
        body = "￼\none\ntwo"
        runs = (dict(NAME_RUN, src=name, width=320), {"start": 0, "end": 2, "style": "align-center"},
                {"start": 2, "end": 9, "style": "numbered"}, {"start": 6, "end": 9, "style": "indent-1"},
                {"start": 9, "end": 9, "style": "bulleted"})
        saved = self.store.update_note(note.id, title="T", body=body, runs=runs)
        self.assertEqual(saved.runs, runs)
        self.assertEqual(saved.preview, "one")

    def test_a_picture_run_must_name_a_stored_picture_on_an_object_character(self) -> None:
        note = self.store.create_note(title="T")
        for body, run in (("x", dict(NAME_RUN, src="a" * 64 + ".png")),
                          ("￼", dict(NAME_RUN, src="../../etc/passwd")),
                          ("￼", dict(NAME_RUN, src="a" * 64 + ".png", width=-3))):
            with self.assertRaises(ValueError):
                self.store.update_note(note.id, title="T", body=body, runs=(run,))

    def test_a_page_of_only_a_picture_previews_as_a_picture(self) -> None:
        note = self.store.create_note(title="T")
        saved = self.store.update_note(note.id, title="T", body="￼",
                                       runs=(dict(NAME_RUN, src=store_picture(PNG)),))
        self.assertEqual(saved.preview, "Picture")


class MarkdownTests(ModelCase):
    def test_markdown_carries_structure_and_inline_styles(self) -> None:
        name = store_picture(PNG)
        body = "Groceries\nApples\nPears\nFirst\nNested\nSecond\nA quote\n￼\nLink here"
        note = self.store.create_note(title="Weekend")
        runs = ({"start": 0, "end": 10, "style": "heading"}, {"start": 0, "end": 9, "style": "bold"},
                {"start": 10, "end": 23, "style": "bulleted"}, {"start": 23, "end": 43, "style": "numbered"},
                {"start": 29, "end": 36, "style": "indent-1"}, {"start": 43, "end": 51, "style": "quote"},
                dict(NAME_RUN, start=51, end=52, src=name),
                {"start": 53, "end": 57, "style": "link", "href": "https://example.com"})
        note = self.store.update_note(note.id, title="Weekend", body=body, runs=runs)
        text, pictures = to_markdown(note, "Weekend pictures")
        self.assertEqual(text, (
            "# Weekend\n\n## **Groceries**\n- Apples\n- Pears\n1. First\n   1. Nested\n2. Second\n"
            f"> A quote\n![](Weekend%20pictures/{name})\n[Link](https://example.com) here\n"))
        self.assertEqual(pictures, (name,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
