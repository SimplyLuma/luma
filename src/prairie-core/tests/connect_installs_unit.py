#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Connect sync reads and writes the install of each app that is in use.

A disposable home holds a system data directory, a Flatpak installation (user
and system), exported and ordinary desktop entries, and each app's data in
both places. The checks: which install is chosen and why, that the system
install wins when both exist, that a planted symlink is refused, that Notes,
Weather places, world clocks and Leaf's library are read from the chosen
install, that changing install starts a list over instead of deleting the
account's items, that Notes refuses to replace a library synced from the other
install, and that Tide stays out of a Flatpak's sandboxed keyring.

    PYTHONPATH=src/prairie-core python3 src/prairie-core/tests/connect_installs_unit.py
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prairie_apps import app_installs  # noqa: E402
from prairie_apps.app_installs import (  # noqa: E402
    CLOCK, FLATPAK, LEAF, NONE, NOTES, SYSTEM, TIDE, WEATHER, app_environment, resolve,
)
from prairie_apps.connect_collections import COLLECTIONS, sync_collection  # noqa: E402

PLACES = next(c for c in COLLECTIONS if c.name == "weather-places")
CLOCKS = next(c for c in COLLECTIONS if c.name == "world-clocks")
BOOKS = next(c for c in COLLECTIONS if c.name == "leaf-books")


class Home:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.system_data = root / "usr-share"
        self.system_flatpak = root / "var-lib-flatpak"
        self.env = {
            "HOME": str(self.home),
            "XDG_DATA_HOME": str(self.home / ".local/share"),
            "XDG_DATA_DIRS": ":".join((str(self.home / ".local/share/flatpak/exports/share"),
                                       str(self.system_flatpak / "exports/share"), str(self.system_data))),
            "FLATPAK_SYSTEM_DIR": str(self.system_flatpak),
        }
        (self.home / ".local/share").mkdir(parents=True)

    def system_app(self, app_id: str) -> None:
        path = self.system_data / "applications" / f"{app_id}.desktop"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"[Desktop Entry]\nType=Application\nName={app_id}\nExec=true\n")

    def flatpak_app(self, app_id: str, *, system: bool = False, export: bool = True) -> Path:
        installation = self.system_flatpak if system else self.home / ".local/share/flatpak"
        metadata = installation / "app" / app_id / "current/active/metadata"
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(f"[Application]\nname={app_id}\nruntime=org.projectluma.Platform/x86_64/44\n")
        if export:
            entry = installation / "exports/share/applications" / f"{app_id}.desktop"
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_text(f"[Desktop Entry]\nType=Application\nName={app_id}\nExec=flatpak run {app_id}\n"
                             f"X-Flatpak={app_id}\n")
        data = self.home / ".var/app" / app_id / "data"
        data.mkdir(parents=True, exist_ok=True)
        return data


class FakeHub:
    """The list-merge rules of the hub's collections, reduced to what these checks need."""

    def __init__(self, items=None) -> None:
        self.items = list(items or [])
        self.revision = 1
        self.posts = []

    def get_json(self, url, *, token="", timeout=None):
        return {"revision": self.revision, "items": [dict(item) for item in self.items]}

    def post_json(self, url, payload, *, token=""):
        self.posts.append(payload)
        for change in payload["changes"]:
            existing = next((item for item in self.items if item["uid"] == change["uid"]), None)
            if change["op"] == "delete":
                if existing:
                    self.items.remove(existing)
            elif existing:
                existing["data"] = change["data"]
            else:
                self.items.append({"uid": change["uid"], "data": change["data"]})
        self.revision += 1
        return self.get_json(url)


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.h = Home(Path(self.directory.name))

    def tearDown(self):
        self.directory.cleanup()

    def test_nothing_installed_keeps_the_system_location(self):
        install = resolve(NOTES, self.h.env)
        self.assertEqual((install.kind, install.data_home), (NONE, self.h.home / ".local/share"))

    def test_the_system_package_is_found_by_its_desktop_entry(self):
        self.h.system_app(WEATHER)
        self.assertEqual(resolve(WEATHER, self.h.env).kind, SYSTEM)

    def test_a_user_flatpak_is_found_by_its_deployment_and_uses_its_sandbox_data(self):
        data = self.h.flatpak_app(CLOCK)
        install = resolve(CLOCK, self.h.env)
        self.assertEqual((install.kind, install.data_home), (FLATPAK, data))
        self.assertEqual(app_environment(CLOCK, self.h.env)["XDG_DATA_HOME"], str(data))

    def test_a_system_wide_flatpak_counts_and_its_export_is_not_a_system_install(self):
        self.h.flatpak_app(LEAF, system=True)
        self.assertEqual(resolve(LEAF, self.h.env).kind, FLATPAK)

    def test_an_exported_entry_alone_is_not_an_install(self):
        entry = self.h.system_data / "applications" / f"{TIDE}.desktop"
        entry.parent.mkdir(parents=True)
        entry.write_text(f"[Desktop Entry]\nType=Application\nName=Tide\nExec=true\nX-Flatpak={TIDE}\n")
        self.assertEqual(resolve(TIDE, self.h.env).kind, NONE)

    def test_the_system_install_wins_when_both_exist(self):
        self.h.system_app(NOTES)
        self.h.flatpak_app(NOTES)
        install = resolve(NOTES, self.h.env)
        self.assertEqual((install.kind, install.data_home), (SYSTEM, self.h.home / ".local/share"))

    def test_a_symlinked_sandbox_directory_is_refused(self):
        self.h.flatpak_app(WEATHER)
        target = self.h.root / "elsewhere"
        target.mkdir()
        app_dir = self.h.home / ".var/app" / WEATHER
        data = app_dir / "data"
        data.rmdir()
        data.symlink_to(target)
        self.assertEqual(resolve(WEATHER, self.h.env).kind, NONE)

    def test_only_the_synced_apps(self):
        with self.assertRaises(ValueError):
            resolve("org.example.Other", self.h.env)
        with self.assertRaises(ValueError):
            app_installs.flatpak_data_home("../../etc", self.h.env)

    def test_signed_os_role_wins_and_removal_keeps_active_data_ownership(self):
        import gi
        gi.require_version('Flatpak', '1.0')
        from gi.repository import Flatpak
        from luma_installer import native_app_roles
        self.h.system_app(NOTES)
        data = self.h.flatpak_app(NOTES, system=True)
        (data / 'kept-note').write_text('sandbox edits survive removal')
        state = {'present': True}
        ref = SimpleNamespace(get_name=lambda: NOTES, format_ref=lambda:'app/'+NOTES+'/x86_64/beta')
        installation = SimpleNamespace(list_installed_refs=lambda _: [ref] if state['present'] else [])
        with patch.object(native_app_roles, 'required', return_value=True), \
             patch.object(native_app_roles, 'installed', return_value=ref) as admission, \
             patch.object(Flatpak.Installation, 'new_system', return_value=installation):
            self.assertEqual(resolve(NOTES, self.h.env), app_installs.Install(NOTES, FLATPAK, data))
            admission.assert_called_once_with(installation, NOTES)
            state['present'] = False
            self.assertEqual(resolve(NOTES, self.h.env), app_installs.Install(NOTES, NONE, data))
            self.assertEqual((data / 'kept-note').read_text(), 'sandbox edits survive removal')
            self.assertEqual(app_environment(NOTES, self.h.env)['XDG_DATA_HOME'], str(data))

    def test_signed_role_never_falls_back_on_bad_trust_or_symlink(self):
        import gi
        gi.require_version('Flatpak', '1.0')
        from gi.repository import Flatpak
        from luma_installer import native_app_roles
        self.h.system_app(NOTES)
        data = self.h.flatpak_app(NOTES, system=True)
        ref = SimpleNamespace(get_name=lambda: NOTES, format_ref=lambda:'app/'+NOTES+'/x86_64/beta')
        installation = SimpleNamespace(list_installed_refs=lambda _: [ref])
        with patch.object(native_app_roles, 'required', return_value=True), \
             patch.object(Flatpak.Installation, 'new_system', return_value=installation), \
             patch.object(native_app_roles, 'installed', side_effect=ValueError('invalid actual signed deployment')):
            with self.assertRaises(ValueError): resolve(NOTES, self.h.env)
            data.rmdir(); data.symlink_to(self.h.system_data)
            with self.assertRaises(ValueError): resolve(NOTES, self.h.env)


class SyncTargetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.h = Home(Path(self.directory.name))

    def tearDown(self):
        self.directory.cleanup()

    def sync(self, collection, hub, state):
        return sync_collection(collection, address="https://hub.test", token="t", http=hub, state=state,
                               scope="s", environment=self.h.env, remote_changed=True)

    def test_world_clocks_and_places_come_from_the_flatpak_when_that_is_the_install(self):
        data = self.h.flatpak_app(CLOCK)
        (data / "prairie/clock").mkdir(parents=True)
        (data / "prairie/clock/state.json").write_text(json.dumps(
            {"world": [{"uid": "u1", "label": "Tokyo", "zone": "Asia/Tokyo"}], "alarms": [{"uid": "a"}], "timers": []}))
        self.assertEqual(CLOCKS.path(self.h.env), data / "prairie/clock/state.json")
        hub = FakeHub([{"uid": "u2", "data": {"label": "Oslo", "zone": "Europe/Oslo"}}])
        state = {}
        print(self.sync(CLOCKS, hub, state))
        written = json.loads((data / "prairie/clock/state.json").read_text())
        self.assertEqual(sorted(city["label"] for city in written["world"]), ["Oslo", "Tokyo"])
        self.assertEqual(written["alarms"], [{"uid": "a"}], "alarms stay on the device")
        self.assertFalse((self.h.home / ".local/share/prairie/clock").exists(), "nothing written natively")
        self.assertEqual(state["s|collection|world-clocks"]["path"], str(data / "prairie/clock/state.json"))

        places = self.h.flatpak_app(WEATHER)
        self.assertEqual(PLACES.path(self.h.env), places / "prairie/weather/places.json")
        self.h.flatpak_app(LEAF)
        self.assertEqual(BOOKS.path(self.h.env), self.h.home / ".var/app" / LEAF / "data/leaf/library.sqlite3")

    def test_changing_install_starts_the_list_over_instead_of_deleting_it(self):
        native = self.h.home / ".local/share/prairie/weather"
        native.mkdir(parents=True)
        place = {"name": "Oslo", "region": "", "country": "NO", "latitude": 59.9, "longitude": 10.7, "timezone": "Europe/Oslo"}
        (native / "places.json").write_text(json.dumps([{"uid": "p1", **place}]))
        hub = FakeHub()
        state = {}
        self.sync(PLACES, hub, state)
        # A base written before sync knew about installs has no path.
        del state["s|collection|weather-places"]["path"]
        self.assertEqual([item["uid"] for item in hub.items], ["p1"])

        # The system Weather is removed and its Flatpak installed, empty.
        data = self.h.flatpak_app(WEATHER)
        status = self.sync(PLACES, hub, state)
        self.assertEqual([item["uid"] for item in hub.items], ["p1"], status)
        self.assertFalse(any(change["op"] == "delete" for post in hub.posts for change in post["changes"]))
        written = json.loads((data / "prairie/weather/places.json").read_text())
        self.assertEqual([record["uid"] for record in written], ["p1"], "the account's places reach the Flatpak")

    def test_a_legacy_base_for_the_same_install_is_kept(self):
        self.h.system_app(WEATHER)
        native = self.h.home / ".local/share/prairie/weather"
        native.mkdir(parents=True)
        (native / "places.json").write_text("[]")
        hub = FakeHub([{"uid": "p1", "data": {"name": "Oslo", "region": "", "country": "", "latitude": 1.0,
                                                "longitude": 2.0, "timezone": ""}}])
        state = {"s|collection|weather-places": {"revision": 1, "items": [["p1", hub.items[0]["data"]]]}}
        self.sync(PLACES, hub, state)
        # The place is gone locally since the base: a real removal, still sent.
        self.assertEqual(hub.items, [])


class NotesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.h = Home(Path(self.directory.name))
        from prairie_apps import connect_sync
        self.sync = connect_sync

    def tearDown(self):
        self.directory.cleanup()

    def library(self, data_home: Path, titles):
        from prairie_apps.notes_backend import NotesStore
        path = data_home / "luma/notes/notes.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        store = NotesStore(path)
        for title in titles:
            note = store.create_note(title=title)
            store.update_note(note.id, title=title, body=title)
        store.close()
        return path

    def test_the_snapshot_reads_the_flatpak_library(self):
        data = self.h.flatpak_app(NOTES)
        self.library(data, ["From the Flatpak"])
        self.assertEqual(self.sync.notes_store_path(self.h.env), data / "luma/notes/notes.sqlite3")
        payload = self.sync.notes_snapshot("device", environment=self.h.env)
        self.assertEqual([item["title"] for item in payload["items"]], ["From the Flatpak"])

    def test_a_library_synced_from_the_other_install_is_not_replaced_without_force(self):
        from prairie_apps.connect_sync import ConnectError, DeviceIdentity, save_identity
        from prairie_apps.connect_services import save_enabled
        save_identity(DeviceIdentity("device", "token", "https://hub.test", "Test", "2026-09-15T00:00:00Z"), self.h.env)
        # Only Notes is under test; the address book has no server here.
        save_enabled(self.sync.connect_data_directory(self.h.env), {"notes": True, "contacts": False})
        native = self.h.home / ".local/share"
        self.library(native, ["Native one", "Native two"])
        sent = []

        def transport(url, payload, *, token="", timeout=None):
            sent.append((url.rsplit("/", 1)[1], [item["title"] for item in payload["items"]]))
            return {"accepted": len(payload["items"])}

        self.sync.push(environment=self.h.env, transport=transport, contacts_loader=lambda: (), out=io.StringIO())
        self.assertIn(("notes", ["Native one", "Native two"]), sent)

        # The system Notes goes; the Flatpak comes, with a library of its own.
        (native / "luma/notes/notes.sqlite3").rename(native / "luma/notes/moved.sqlite3")
        data = self.h.flatpak_app(NOTES)
        self.library(data, ["Flatpak only"])
        sent.clear()
        # Installing a second profile alone never activates host sync. Even
        # --force cannot bypass the maintained migration readiness boundary.
        self.sync.push(environment=self.h.env, transport=transport, contacts_loader=lambda: (), out=io.StringIO(), force=True)
        self.assertFalse([entry for entry in sent if entry[0] == "notes"])
        before = json.loads(self.sync.state_file(self.h.env).read_text())
        self.assertEqual(before["https://hub.test|device|notes"]["path"], str(native / "luma/notes/notes.sqlite3"))
        # Explicit unit fixture for a completed host handoff. Actual signed
        # migration and marker publication are qualified by Installer's tests.
        ready = self.h.home / ".local/state/luma/app-migration/org.projectluma.Notes.ready.json"
        ready.parent.mkdir(mode=0o700, parents=True)
        ready.write_text(json.dumps({"schema": "org.projectluma.app-data-ready/v1",
            "app_id": NOTES, "result": "PASS", "receipt_sha256": "a" * 64}))
        ready.chmod(0o600)
        with self.assertRaises(ConnectError) as refused:
            self.sync.push(environment=self.h.env, transport=transport, contacts_loader=lambda: (), out=io.StringIO())
        self.assertIn("--force", str(refused.exception))
        self.assertFalse([entry for entry in sent if entry[0] == "notes"])
        self.sync.push(environment=self.h.env, transport=transport, contacts_loader=lambda: (), out=io.StringIO(),
                       force=True)
        self.assertIn(("notes", ["Flatpak only"]), sent)
        state = json.loads(self.sync.state_file(self.h.env).read_text())
        self.assertEqual(state["https://hub.test|device|notes"]["path"], str(data / "luma/notes/notes.sqlite3"))


class TideTests(unittest.TestCase):
    def test_a_flatpak_tide_is_left_out_of_server_sources(self):
        from prairie_apps.connect_tide import sync_tide
        with tempfile.TemporaryDirectory() as root:
            h = Home(Path(root))
            h.flatpak_app(TIDE)
            status = sync_tide(address="https://hub.test", token="t", http=None, state={}, scope="s",
                               environment=h.env, remote_changed=True)
        self.assertIn("Flatpak", status)


if __name__ == "__main__":
    unittest.main(verbosity=2)
