# SPDX-License-Identifier: Apache-2.0
"""Files and folders for Luma Search, read from the LocalSearch index.

The Python twin of GNOME Shell's js/ui/lumaFileIndex.js. LocalSearch already
crawls the places chosen in Settings → Search and skips hidden and excluded
folders; this module only asks it for names starting with the typed words,
adds recently used files, Search picks and the top of the home folder, and
ranks them with luma_search.ranking.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from typing import Callable
from urllib.parse import unquote, urlparse

from . import ranking

LOCALSEARCH_BUS_NAME = "org.freedesktop.LocalSearch3"
CANDIDATE_LIMIT = 200
RESULT_LIMIT = 24
PICKS_LIMIT = 300
HIDDEN_URL_PARTS = ("/node_modules/", "/__pycache__/", "/site-packages/", "/.git/")

SHALLOW_LIMIT = 100


def _sparql(order: str, limit: int) -> str:
    return (
        "SELECT ?url ?name ?modified WHERE { GRAPH tracker:FileSystem { "
        "?file fts:match ~match ; nfo:fileName ?name ; nie:url ?url ; "
        "nfo:fileLastModified ?modified . "
        + " ".join(f'FILTER (!CONTAINS(?url, "{part}"))' for part in HIDDEN_URL_PARTS)
        + f" }} }} ORDER BY {order} LIMIT {limit}"
    )


# The shortest names (exact and prefix matches) and the shortest paths
# (shallow places), so neither crowds the other out of a large index.
QUERIES = (_sparql("STRLEN(?name)", CANDIDATE_LIMIT), _sparql("STRLEN(?url)", SHALLOW_LIMIT))


def uri_to_path(uri: str) -> str | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    return unquote(parsed.path)


def path_to_uri(path: str) -> str:
    return Path(path).as_uri()


def _iso_to_seconds(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        from datetime import datetime

        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def picks_path() -> Path:
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(state) / "luma" / "search-picks.json"


def xdg_folders(home: str) -> frozenset[str]:
    try:
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import GLib
    except (ImportError, ValueError):
        return frozenset()
    folders = set()
    for key in ranking.XDG_FOLDER_KEYS:
        path = GLib.get_user_special_dir(getattr(GLib.UserDirectory, f"DIRECTORY_{key}"))
        if path and path != home:
            folders.add(path)
    return frozenset(folders)


class FileIndex:
    """Thread-safe: the service queries from its request pool."""

    def __init__(
        self,
        *,
        home: str | None = None,
        connection_factory: Callable[[], object] | None = None,
        recent_path: str | None = None,
        picks_file: Path | None = None,
        remember_recent: Callable[[], bool] = lambda: True,
        recent_max_age_days: Callable[[], int] = lambda: -1,
        now: Callable[[], float] = time.time,
        folders: frozenset[str] | None = None,
    ) -> None:
        self.home = home or str(Path.home())
        self._connection_factory = connection_factory
        data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
        self._recent_path = recent_path or os.path.join(data_home, "recently-used.xbel")
        self._picks_file = picks_file or picks_path()
        self._remember_recent = remember_recent
        self._recent_max_age_days = recent_max_age_days
        self._now = now
        self._xdg = folders if folders is not None else xdg_folders(self.home)
        self._lock = threading.Lock()
        self._connection = None
        self._unavailable_until = 0.0
        self._recent: dict[str, dict] = {}
        self._recent_stamp = -1
        self._picks: dict[str, dict] = {}
        self._picks_loaded = False

    # -- sources -----------------------------------------------------------

    def _connect(self):
        if self._connection is not None:
            return self._connection
        if self._now() < self._unavailable_until:
            return None
        try:
            if self._connection_factory is not None:
                self._connection = self._connection_factory()
            else:
                import gi

                gi.require_version("Tsparql", "3.0")
                from gi.repository import Tsparql

                self._connection = Tsparql.SparqlConnection.bus_new(LOCALSEARCH_BUS_NAME, None, None)
        except Exception:  # LocalSearch missing or not running: retry later.
            self._unavailable_until = self._now() + 30
            self._connection = None
        return self._connection

    def _query(self, match: str) -> list[dict]:
        if not match:
            return []
        connection = self._connect()
        if connection is None:
            return []
        rows = []
        for query in QUERIES:
            try:
                statement = connection.query_statement(query, None)
                statement.bind_string("match", match)
                cursor = statement.execute(None)
            except Exception:
                continue
            try:
                while cursor.next(None):
                    url = cursor.get_string(0)[0]
                    name = cursor.get_string(1)[0]
                    modified = cursor.get_string(2)[0]
                    path = uri_to_path(url or "")
                    if path and name:
                        rows.append({"path": path, "name": name, "modified": _iso_to_seconds(modified)})
            finally:
                cursor.close()
        return rows

    def _refresh_recent(self) -> None:
        if not self._remember_recent():
            self._recent = {}
            self._recent_stamp = -1
            return
        try:
            stamp = os.stat(self._recent_path).st_mtime_ns
        except OSError:
            self._recent = {}
            return
        if stamp == self._recent_stamp:
            return
        try:
            import gi

            gi.require_version("GLib", "2.0")
            from gi.repository import GLib

            bookmarks = GLib.BookmarkFile()
            bookmarks.load_from_file(self._recent_path)
        except Exception:
            return
        now = self._now()
        max_days = self._recent_max_age_days()
        max_age = float("inf") if max_days < 0 else max_days * 86400
        recent = {}
        uris = bookmarks.get_uris()
        if isinstance(uris, tuple):
            uris = uris[0]
        for uri in uris:
            path = uri_to_path(uri)
            if not path:
                continue

            def stamp_of(read):
                try:
                    value = read(uri)
                    return value.to_unix() if value else 0
                except Exception:
                    return 0

            visited = max(stamp_of(bookmarks.get_visited_date_time), stamp_of(bookmarks.get_modified_date_time))
            if now - visited > max_age:
                continue
            visits = 0
            try:
                apps = bookmarks.get_applications(uri)
                apps = apps[0] if isinstance(apps, tuple) else apps
                for app in apps:
                    info = bookmarks.get_application_info(uri, app)
                    visits += int(info[2]) if len(info) > 2 else 0
            except Exception:
                pass
            recent[path] = {"path": path, "name": os.path.basename(path), "visited": visited, "visits": visits}
        self._recent = recent
        self._recent_stamp = stamp

    def _load_picks(self) -> None:
        if self._picks_loaded:
            return
        self._picks_loaded = True
        try:
            data = json.loads(self._picks_file.read_text())
            for path, value in (data.get("picks") or {}).items():
                if isinstance(value.get("count"), (int, float)) and isinstance(value.get("last"), (int, float)):
                    self._picks[path] = value
        except (OSError, ValueError, AttributeError):
            pass

    def record_pick(self, path: str | None) -> None:
        if not path or not self._remember_recent():
            return
        with self._lock:
            self._load_picks()
            existing = self._picks.pop(path, None) or {}
            self._picks[path] = {"count": int(existing.get("count", 0)) + 1, "last": round(self._now())}
            while len(self._picks) > PICKS_LIMIT:
                self._picks.pop(next(iter(self._picks)))
            try:
                self._picks_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                temporary = self._picks_file.with_suffix(".tmp")
                temporary.write_text(json.dumps({"version": 1, "picks": self._picks}))
                os.chmod(temporary, 0o600)
                temporary.replace(self._picks_file)
            except OSError:
                pass

    def _home_entries(self) -> list[dict]:
        entries = []
        try:
            with os.scandir(self.home) as iterator:
                for entry in iterator:
                    if entry.name.startswith(".") or entry.name.endswith("~"):
                        continue
                    entries.append({"path": os.path.join(self.home, entry.name), "name": entry.name})
                    if len(entries) > 2000:
                        break
        except OSError:
            pass
        return entries

    # -- search ------------------------------------------------------------

    def search(self, terms: tuple[str, ...]) -> list[dict]:
        terms = tuple(ranking.fold_text(term) for term in terms if term)
        if not terms:
            return []
        with self._lock:
            self._refresh_recent()
            self._load_picks()
            remember = self._remember_recent()
            recent = dict(self._recent) if remember else {}
            picks = dict(self._picks) if remember else {}

        def with_usage(candidate: dict) -> dict:
            used = recent.get(candidate["path"], {})
            pick = picks.get(candidate["path"], {})
            return {
                **candidate,
                "visited": used.get("visited", 0),
                "visits": used.get("visits", 0),
                "picked": pick.get("last", 0),
                "picks": pick.get("count", 0),
            }

        local = [
            *self._home_entries(),
            *({"path": path, "name": os.path.basename(path)} for path in self._xdg),
            *recent.values(),
            *({"path": path, "name": os.path.basename(path)} for path in picks),
        ]
        # One letter matches a large share of any home folder; the index is
        # asked from the second letter, local sources answer the first.
        hits = self._query(ranking.fts_query(terms)) if len("".join(terms)) >= 2 else []
        context = dict(home=self.home, now=self._now(), xdg_folders=self._xdg, limit=RESULT_LIMIT)
        ranked = ranking.rank_files([with_usage(c) for c in (*local, *hits)], terms, **context)
        if any(len(term) >= 5 for term in terms) and not any(item["tier"] >= ranking.TIER_WORD for item in ranked):
            more = self._query(ranking.fts_query(terms, shorten=True))
            ranked = ranking.rank_files([with_usage(c) for c in (*local, *hits, *more)], terms, **context)
        return [item for item in ranked if os.path.lexists(item["path"])]

    def short_parent(self, path: str) -> str:
        parent = os.path.dirname(path)
        if parent == self.home:
            return "Home"
        display = "~/" + parent[len(self.home) + 1:] if parent.startswith(self.home + "/") else parent
        parts = display.split("/")
        if len(parts) > 4:
            display = f"{parts[0]}/…/{'/'.join(parts[-2:])}"
        return display
