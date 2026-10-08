# SPDX-License-Identifier: Apache-2.0
"""Depot's "What's new": signed notes read into a sheet, never opened raw."""

import io
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError, URLError

from luma_depot import release_notes as rn

import minisign_signer as signer

URL = "https://dl.example.test/media/nightly/notes/20260917.10.json"

DOCUMENT = {
    "build_id": "20260917.10",
    "channel": "nightly",
    "display_name": "Luma (Prairie, Beta 0, Nightly 20260917)",
    "generated_utc": "2026-09-18T14:37:42Z",
    "nightly_date": "2026-09-17",
    "packages_changed": [
        {"from": "0.1.0-1.luma.31.preview20260916.5.fc44", "name": "luma-application-installer",
         "to": "0.1.0-1.luma.31.preview20260917.2.fc44"},
        {"from": None, "name": "luma-release", "to": "1-1.luma.2.fc44"},
    ],
    "schema": "org.projectluma.os-release-notes/v2",
    "section_labels": {"feature": "Features", "fix": "Fixes", "improvement": "Improvements",
                       "security": "Security"},
    "section_order": ["feature", "improvement", "fix", "security"],
    "sections": {
        "feature": [{"summary": "Search now finds files and folders",
                     "details": "Apps, settings, files and folders appear in one list."}],
        "fix": [{"summary": "Fixed Depot closing on open",
                 "details": "Depot no longer quits when it checks for firmware updates."}],
        "improvement": [],
        "security": [{"summary": "Updated the web engine", "details": "Fixes two issues."}],
    },
    "summary": "Search now finds files and folders, plus 2 more changes.",
    "version": "1.0.0-nightly.20260917.10",
}


def comment(build="20260917.10", channel="nightly"):
    return f"luma-release-notes channel={channel} build_id={build} generated_utc=2026-09-18T14:37:42Z"


class Server:
    """A fake opener: URL -> bytes, or an exception to raise."""

    def __init__(self, files):
        self.files = files
        self.requests = []

    def __call__(self, request, timeout):
        url = request.full_url
        self.requests.append(url)
        value = self.files.get(url)
        if value is None:
            raise HTTPError(url, 404, "Not Found", {}, None)
        if isinstance(value, Exception):
            raise value
        return io.BytesIO(value)


class Keys:
    def __enter__(self):
        self.temp = TemporaryDirectory()
        Path(self.temp.name, "luma-update-graph.pub").write_bytes(signer.public_key_file())
        return (Path(self.temp.name),)

    def __exit__(self, *_):
        self.temp.cleanup()


def published(document=DOCUMENT, trusted=None):
    body = json.dumps(document, sort_keys=True).encode()
    return {URL: body, URL + ".minisig": signer.signature_file(body, trusted_comment=trusted or comment())}


class ParseTests(unittest.TestCase):
    def test_reads_what_a_person_sees(self):
        notes = rn.parse(DOCUMENT)
        self.assertEqual(notes.display_name, "Luma (Prairie, Beta 0, Nightly 20260917)")
        self.assertEqual(notes.date, "September 17, 2026")
        self.assertEqual([s.label for s in notes.sections], ["New features", "Fixes", "Security"])
        self.assertEqual(notes.sections[0].notes[0],
                         rn.Note("Search now finds files and folders",
                                 "Apps, settings, files and folders appear in one list."))
        self.assertEqual(notes.count, 3)

    def test_package_list_is_only_technical(self):
        notes = rn.parse(DOCUMENT)
        words = " ".join(n.summary + " " + n.details for s in notes.sections for n in s.notes)
        self.assertNotIn("luma-application-installer", words)
        self.assertEqual(notes.packages[1], rn.PackageChange("luma-release", None, "1-1.luma.2.fc44"))
        self.assertEqual((notes.build_id, notes.channel), ("20260917.10", "nightly"))

    def test_schema_v1_sections_read_as_the_four_types(self):
        notes = rn.parse({"schema": "org.projectluma.os-release-notes/v1", "nightly_date": "2026-09-15",
                          "sections": {"added": [{"component": "Dock", "summary": "A", "details": "a"}],
                                       "fixed": [{"summary": "B", "details": "b"}], "removed": []}},
                         fallback_name="Luma Nightly")
        self.assertEqual(notes.display_name, "Luma Nightly")
        self.assertEqual([(s.key, s.label) for s in notes.sections],
                         [("feature", "New features"), ("fix", "Fixes")])

    def test_unknown_type_uses_the_document_label(self):
        notes = rn.parse({"schema": "org.projectluma.os-release-notes/v3", "section_labels": {"deprecation": "On the way out"},
                          "sections": {"deprecation": [{"summary": "Old thing", "details": ""}]}})
        self.assertEqual(notes.sections[0].label, "On the way out")

    def test_refuses_other_documents(self):
        for document in ([], {"schema": "something/v1", "sections": {}}, {"schema": rn.SCHEMA_PREFIX + "v2"}):
            with self.assertRaises(rn.NotesError):
                rn.parse(document)

    def test_generated_date_when_no_nightly_date(self):
        document = dict(DOCUMENT, nightly_date=None)
        self.assertEqual(rn.parse(document).date, "September 18, 2026")


class FetchTests(unittest.TestCase):
    def test_verified_notes(self):
        with Keys() as keys:
            notes = rn.fetch(URL, opener=Server(published()), key_dirs=keys)
        self.assertTrue(notes.verified)
        self.assertEqual(notes.sections[0].label, "New features")

    def test_altered_notes_are_refused(self):
        files = published()
        files[URL] = files[URL].replace(b"Search now", b"Search never")
        with Keys() as keys, self.assertRaises(rn.NotesError) as caught:
            rn.fetch(URL, opener=Server(files), key_dirs=keys)
        self.assertEqual(caught.exception.message, "These release notes could not be verified.")

    def test_notes_signed_for_another_build_are_refused(self):
        with Keys() as keys, self.assertRaises(rn.NotesError) as caught:
            rn.fetch(URL, opener=Server(published(trusted=comment(build="20260916.4"))), key_dirs=keys)
        self.assertIn("build_id=20260916.4", caught.exception.detail)

    def test_unsigned_notes_are_refused(self):
        files = published()
        del files[URL + ".minisig"]
        with Keys() as keys, self.assertRaises(rn.NotesError):
            rn.fetch(URL, opener=Server(files), key_dirs=keys)

    def test_no_trusted_key(self):
        with TemporaryDirectory() as empty, self.assertRaises(rn.NotesError) as caught:
            rn.fetch(URL, opener=Server(published()), key_dirs=(Path(empty),))
        self.assertEqual(caught.exception.message, "Luma could not check these release notes.")

    def test_messages_are_for_people(self):
        cases = {
            HTTPError(URL, 404, "Not Found", {}, None): "not published yet",
            HTTPError(URL, 503, "Busy", {}, None): "did not send",
            URLError("Name or service not known"): "Check your connection",
            TimeoutError("timed out"): "Check your connection",
        }
        for error, words in cases.items():
            with Keys() as keys, self.assertRaises(rn.NotesError) as caught:
                rn.fetch(URL, opener=Server({URL: error}), key_dirs=keys)
            self.assertIn(words, caught.exception.message)
            self.assertNotIn("http", caught.exception.message.lower())

    def test_only_https(self):
        for url in ("http://dl.example.test/n.json", "file:///etc/passwd", ""):
            with self.assertRaises(rn.NotesError):
                rn.fetch(url, opener=Server({}), key_dirs=())

    def test_oversized_notes_are_refused(self):
        files = {URL: b" " * (rn.NOTES_MAX_BYTES + 1)}
        with Keys() as keys, self.assertRaises(rn.NotesError):
            rn.fetch(URL, opener=Server(files), key_dirs=keys)


class EntryPointTests(unittest.TestCase):
    """Every "What's new" opens the sheet; none hands the notes URL to a browser."""

    ROOT = Path(__file__).resolve().parents[1]
    # The package build puts luma_depot beside tests/; the repository keeps it in src/luma-depot.
    SOURCE = next(path for path in (ROOT / "luma_depot/window.py", ROOT.parent / "luma-depot/luma_depot/window.py")
                  if path.exists())

    def test_no_notes_link_launches_a_browser(self):
        source = self.SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("_link_button", source)
        for line in source.splitlines():
            if "UriLauncher" in line:
                self.assertNotIn("notes", line)
        self.assertEqual(len(re.findall(r"_notes_button\(state\.notes_url", source)), 3)
        self.assertIn("show_release_notes(", source)

    def test_preview_notes_use_the_same_reader(self):
        try:
            from luma_depot.preview import preview_release_notes
        except (ImportError, ValueError) as error:  # the preview module needs GTK's typelibs
            self.skipTest(f"PyGObject unavailable: {error}")
        notes = preview_release_notes()
        self.assertEqual([s.key for s in notes.sections], ["feature", "improvement", "fix", "security"])


if __name__ == "__main__":
    unittest.main()
