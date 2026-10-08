# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/luma-search"))

from luma_search.contract import SearchResult, normalize_terms, rank_results
from luma_search.files import FileIndex
from luma_search.providers import discover_providers


class RankingTests(unittest.TestCase):
    def test_normalizes_spacing_case_and_accents(self):
        self.assertEqual(normalize_terms("  NÓRA   Notes "), ("nora", "notes"))

    def test_exact_match_wins_across_kinds(self):
        results = (
            SearchResult("apps", "1", "app", "Notes archive", "apps.desktop"),
            SearchResult("files", "1", "file", "Notes", "files.desktop"),
            SearchResult("settings", "1", "setting", "Notes settings", "settings.desktop"),
        )
        self.assertEqual(
            [item.kind for item in rank_results(results, "notes")],
            ["file", "app", "setting"],
        )

    def test_exact_and_prefix_rank_before_substring(self):
        results = (
            SearchResult("apps", "substring", "app", "Field notes", "apps.desktop"),
            SearchResult("apps", "prefix", "app", "Notes archive", "apps.desktop"),
            SearchResult("apps", "exact", "app", "Notes", "apps.desktop"),
        )
        self.assertEqual(
            [item.result_id for item in rank_results(results, "notes")],
            ["exact", "prefix", "substring"],
        )

    def test_bounded_typo_match_follows_strong_matches(self):
        results = (
            SearchResult("apps", "exact", "app", "Calendar", "calendar.desktop"),
            SearchResult("apps", "typo", "app", "Calender", "calender.desktop"),
        )
        self.assertEqual(
            [item.result_id for item in rank_results(results, "calendar")],
            ["exact", "typo"],
        )

    def test_sensitive_snippets_are_private_by_default(self):
        item = SearchResult("mail", "1", "mail", "Dinner", "mail.desktop", snippet="Secret body")
        self.assertEqual(rank_results((item,), "dinner")[0].snippet, "")
        self.assertEqual(
            rank_results((item,), "dinner", allow_sensitive_snippets=True)[0].snippet,
            "Secret body",
        )

    def test_deduplicates_provider_result_identity(self):
        first = SearchResult("files", "same", "file", "Report", "files.desktop", score=1)
        better = SearchResult("files", "same", "file", "Report", "files.desktop", score=20)
        self.assertEqual(rank_results((first, better), "report"), (rank_results((better,), "report")[0],))


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)
        self._row = None

    def next(self, _cancellable):
        if not self._rows:
            return False
        self._row = self._rows.pop(0)
        return True

    def get_string(self, column):
        value = self._row[column]
        return value, len(value)

    def close(self):
        pass


class _FakeStatement:
    def __init__(self, connection):
        self._connection = connection

    def bind_string(self, name, value):
        self._connection.bound[name] = value

    def execute(self, _cancellable):
        match = self._connection.bound["match"]
        self._connection.queries.append(match)
        prefixes = [token.rstrip("*") for token in match.split()]
        rows = [row for row in self._connection.rows
                if all(any(word.startswith(prefix) for word in row[1].lower().replace("-", " ").replace(".", " ").split())
                       for prefix in prefixes)]
        return _FakeCursor(rows)


class _FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.bound = {}
        self.queries = []

    def query_statement(self, _sparql, _cancellable):
        return _FakeStatement(self)


class FileIndexTests(unittest.TestCase):
    def _home(self, directory, names):
        home = Path(directory) / "home"
        rows = []
        for name in names:
            path = home / name
            if name.endswith("/"):
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x")
            rows.append((path.as_uri(), path.name, "2026-09-01T00:00:00Z"))
        return home, rows

    def test_clean_folder_outranks_backup_and_missing_items_are_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            home, rows = self._home(directory, [
                "depot-1840284-backup-x2/", "depot/", "Projects/depot/build/depot/",
            ])
            rows.append(((home / "depot-gone").as_uri(), "depot-gone", ""))
            connection = _FakeConnection(rows)
            index = FileIndex(home=str(home), connection_factory=lambda: connection,
                              recent_path=str(Path(directory) / "none.xbel"),
                              picks_file=Path(directory) / "picks.json", folders=frozenset())
            paths = [item["path"] for item in index.search(("depot",))]
        self.assertEqual(paths[0], str(home / "depot"))
        self.assertLess(paths.index(str(home / "depot")), paths.index(str(home / "depot-1840284-backup-x2")))
        self.assertNotIn(str(home / "depot-gone"), paths)
        self.assertEqual(connection.queries, ["depot*", "depot*"])

    def test_documents_folder_found_by_prefix_and_by_typo(self):
        with tempfile.TemporaryDirectory() as directory:
            home, rows = self._home(directory, ["Documents/", "Projects/docutils.txt"])
            connection = _FakeConnection(rows)
            index = FileIndex(home=str(home), connection_factory=lambda: connection,
                              recent_path=str(Path(directory) / "none.xbel"),
                              picks_file=Path(directory) / "picks.json",
                              folders=frozenset({str(home / "Documents")}))
            self.assertEqual(index.search(("docu",))[0]["path"], str(home / "Documents"))
            self.assertEqual(index.search(("documnets",))[0]["path"], str(home / "Documents"))
            self.assertEqual(index.short_parent(str(home / "Documents")), "Home")

    def test_picks_rank_higher_and_respect_privacy(self):
        with tempfile.TemporaryDirectory() as directory:
            home, rows = self._home(directory, ["notes-a.txt", "notes-b.txt"])
            connection = _FakeConnection(rows)
            remember = [True]
            picks = Path(directory) / "state" / "picks.json"
            index = FileIndex(home=str(home), connection_factory=lambda: connection,
                              recent_path=str(Path(directory) / "none.xbel"), picks_file=picks,
                              remember_recent=lambda: remember[0], folders=frozenset())
            index.record_pick(str(home / "notes-b.txt"))
            self.assertEqual(index.search(("notes",))[0]["path"], str(home / "notes-b.txt"))
            self.assertEqual(oct(picks.stat().st_mode & 0o777), "0o600")
            remember[0] = False
            self.assertEqual(index.search(("notes",))[0]["path"], str(home / "notes-a.txt"))
            index.record_pick(str(home / "notes-a.txt"))
            self.assertNotIn("notes-a", picks.read_text())

    def test_missing_index_keeps_search_working(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "Downloads").mkdir()

            def broken():
                raise RuntimeError("no LocalSearch")

            index = FileIndex(home=str(home), connection_factory=broken,
                              recent_path=str(home / "none.xbel"), picks_file=home / "p.json",
                              folders=frozenset({str(home / "Downloads")}))
            self.assertEqual(index.search(("down",))[0]["path"], str(home / "Downloads"))


class ProviderDiscoveryTests(unittest.TestCase):
    def test_reads_search_provider_two_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "org.example.Calendar.search-provider.ini").write_text(
                """[Shell Search Provider]\nVersion=2\nDesktopId=org.example.Calendar.desktop\nBusName=org.example.Calendar\nObjectPath=/org/example/Calendar/Search\nX-Luma-Kind=calendar\n""",
                encoding="utf-8",
            )
            providers = discover_providers((root,))
        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0].kind, "calendar")
        self.assertIn("org.example.Calendar", providers[0].description)

    def test_excludes_recursive_luma_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "luma.search-provider.ini").write_text(
                """[Shell Search Provider]\nVersion=2\nDesktopId=org.projectluma.Search.desktop\nBusName=org.projectluma.Search\nObjectPath=/org/projectluma/Search\n""",
                encoding="utf-8",
            )
            self.assertEqual(discover_providers((root,)), ())


class ContentKindTests(unittest.TestCase):
    def test_mail_app_results_are_mail_and_follow_settings(self):
        from luma_search.providers import kind_from_categories
        from luma_search import ranking

        self.assertEqual(kind_from_categories("Network;Email;GTK;"), "mail")
        self.assertEqual(kind_from_categories("Utility;"), "app")
        terms = ranking.split_terms("about")
        mail = ranking.score_provider_result("mail", "About the Viola release", terms, 0, "org.projectluma.Charlie.desktop")
        setting = ranking.score_setting_page({"title": "About This Computer", "path": "System", "keywords": ["about"]}, terms)
        folder = ranking.rank_files([{"path": "/home/a/About Viola", "name": "About Viola"}], terms, home="/home/a", now=0)[0]["score"]
        self.assertLess(mail, setting)
        self.assertLess(mail, folder)


class ServiceContractTests(unittest.TestCase):
    def test_provider_proxy_construction_cannot_autostart_or_load_properties(self):
        source = (ROOT / "src/luma-search/luma-search-service").read_text(
            encoding="utf-8"
        )
        proxy_block = source.split("def _provider_proxy", 1)[1].split(
            "def _query_provider", 1
        )[0]
        self.assertIn("DO_NOT_LOAD_PROPERTIES", proxy_block)
        self.assertIn("DO_NOT_CONNECT_SIGNALS", proxy_block)
        self.assertIn("DO_NOT_AUTO_START_AT_CONSTRUCTION", proxy_block)
        self.assertIn("DO_NOT_AUTO_START", proxy_block)
        self.assertIn("PROVIDER_TIMEOUT_MS", source)


class FilerPlacesTests(unittest.TestCase):
    """Typing a Filer place's name, or a word people use for it, offers it."""

    def setUp(self):
        from luma_search import places

        self.places = places
        self.table = places.load_table([ROOT / "src/luma-search/filer-places.json"])
        self.assertGreaterEqual(len(self.table["places"]), 13, "the places table did not load")
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        home = Path(self.home.name)
        self.special = {}
        for key, name in (("DESKTOP", "Desktop"), ("DOCUMENTS", "Documents"),
                          ("DOWNLOAD", "Downloads"), ("PICTURES", "Pictures"),
                          ("VIDEOS", "Videos")):
            (home / name).mkdir()
            self.special[key] = str(home / name)
        # Music is configured but was deleted: Filer has no such place.
        self.special["MUSIC"] = str(home / "Music")
        (home / "Projects").mkdir()

    def env(self, **overrides):
        values = dict(
            home=self.home.name,
            special_dirs=self.special,
            bookmarks=f"{(Path(self.home.name) / 'Projects').as_uri()} Work\n"
                      f"file://{self.home.name}/Gone\n"
                      "sftp://files.example.org/srv shared\n",
        )
        values.update(overrides)
        return self.places.PlaceEnvironment(**values)

    def top(self, query, **overrides):
        resolved = self.places.resolve_places(self.table, self.env(**overrides))
        ranked = self.places.rank_places(resolved, normalize_terms(query))
        self.assertTrue(ranked, f"{query!r} offered no Filer place")
        return ranked[0]["place"]

    def test_applications_opens_filers_applications_page(self):
        place = self.top("Applications")
        self.assertEqual(place["uri"], "applications:///")
        self.assertEqual(place["title"], "Applications")

    def test_apps_finds_applications(self):
        self.assertEqual(self.top("apps")["uri"], "applications:///")
        self.assertEqual(self.top("app")["uri"], "applications:///")

    def test_trash_and_bin_find_the_trash(self):
        self.assertEqual(self.top("trash")["uri"], "trash:///")
        self.assertEqual(self.top("bin")["uri"], "trash:///")

    def test_every_listed_place_is_found_by_its_name(self):
        for query, uri in (
            ("home", Path(self.home.name).as_uri()),
            ("desktop", self.special["DESKTOP"]),
            ("documents", self.special["DOCUMENTS"]),
            ("downloads", self.special["DOWNLOAD"]),
            ("pictures", self.special["PICTURES"]),
            ("videos", self.special["VIDEOS"]),
            ("recent", "recent:///"),
            ("starred", "starred:///"),
            ("network", "x-network-view:///"),
            ("other locations", "x-network-view:///"),
            ("system disk", "file:///"),
            ("work", (Path(self.home.name) / "Projects").as_uri()),
            ("shared", "sftp://files.example.org/srv"),
        ):
            expected = uri if "://" in uri else Path(uri).as_uri()
            self.assertEqual(self.top(query)["uri"], expected, query)

    def test_exact_and_prefix_matches_rank_first(self):
        resolved = self.places.resolve_places(self.table, self.env())
        ranked = self.places.rank_places(resolved, normalize_terms("do"), limit=10)
        self.assertEqual(ranked[0]["place"]["id"], "documents")
        ranked = self.places.rank_places(resolved, normalize_terms("recent"), limit=10)
        self.assertEqual(ranked[0]["place"]["id"], "recent")

    def test_places_filer_does_not_show_are_not_offered(self):
        resolved = self.places.resolve_places(
            self.table, self.env(remember_recent=False, personal_storage_only=True))
        uris = {place["uri"] for place in resolved}
        self.assertNotIn("recent:///", uris)
        self.assertNotIn("file:///", uris)
        self.assertNotIn(Path(self.special["MUSIC"]).as_uri(), uris)
        self.assertNotIn(Path(self.home.name, "Gone").as_uri(), uris)
        self.assertEqual(len(uris), len(resolved), "a place was listed twice")

    def test_titles_are_filers_translated_labels(self):
        spanish = {"Trash": "Papelera", "Applications": "Aplicaciones"}
        env = dict(translate=lambda text: spanish.get(text, text))
        self.assertEqual(self.top("papelera", **env)["title"], "Papelera")
        self.assertEqual(self.top("trash", **env)["title"], "Papelera")
        self.assertEqual(self.top("aplic", **env)["uri"], "applications:///")

    def test_service_opens_the_place_in_filer_and_shows_a_folder_once(self):
        source = (ROOT / "src/luma-search/luma-search-service").read_text(encoding="utf-8")
        activate = source.split("def _activate", 1)[1].split("def _finish_query", 1)[0]
        self.assertIn("PLACES_PROVIDER_ID", activate)
        self.assertIn('launch_uris([place["uri"]]', activate)
        self.assertIn("self._query_places(terms, generation)", source)
        self.assertIn("not in place_uris", source)


if __name__ == "__main__":
    unittest.main()
