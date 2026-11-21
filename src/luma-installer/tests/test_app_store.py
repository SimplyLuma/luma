# SPDX-License-Identifier: Apache-2.0
"""The application store (ADR-052): one repository, hard-link checkouts, rollback by ref.

Runs against a real OSTree repository when ostree is installed; the store has
no behaviour of its own worth testing without one.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from luma_installer import closure
from luma_installer.app_store import Store
from luma_installer.errors import InstallerError


def tree(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text)
    return root


def record(app_id: str, version: str = "1") -> dict[str, object]:
    return {"schema": 1, "kind": "application", "id": app_id, "format": "deb", "userland": "debian-12",
            "architecture": "x86_64", "origin": {"package": app_id, "version": version, "sha256": ""}, "packages": []}


@unittest.skipUnless(shutil.which("ostree"), "ostree is not installed")
class StoreBehaviour(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.base = Path(self.folder.name)
        patcher = patch.object(closure, "userns_prefix", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = Store(self.base / "store", fsync=False)
        self.shared = {"usr/lib/libc.so.6": "c library " * 500, "usr/share/doc/copyright": "text"}

    def commit(self, app_id: str, own: dict[str, str], version: str = "1") -> str:
        root = tree(self.base / f"build-{app_id}-{version}", {**self.shared, **own})
        return self.store.commit_tree(root, self.store.app_ref(app_id, "x86_64"), record(app_id, version),
                                      subject=f"{app_id} {version}")

    def test_a_file_two_applications_contain_is_stored_once(self):
        self.commit("one", {"usr/bin/one": "1" * 100})
        self.commit("two", {"usr/bin/two": "2" * 300})
        sharing = self.store.sharing()
        shared = sum(len(text) for text in self.shared.values())
        rows = {row["ref"]: row for row in sharing["trees"]}
        self.assertEqual(rows["app/one/x86_64"]["shared_bytes"], shared)
        self.assertEqual(sharing["sum_bytes"] - sharing["stored_bytes"], shared)
        objects = [path for path in (self.store.repo / "objects").rglob("*.file")]
        contents = [path.read_text() for path in objects]
        self.assertEqual(contents.count(self.shared["usr/lib/libc.so.6"]), 1)

    def test_a_checkout_is_hard_links_into_the_repository(self):
        commit = self.commit("one", {"usr/bin/one": "payload"})
        checkout = self.store.compose([commit])
        linked = os.stat(checkout / "usr/bin/one")
        self.assertGreaterEqual(linked.st_nlink, 2)
        inodes = {os.stat(path).st_ino for path in (self.store.repo / "objects").rglob("*.file")}
        self.assertIn(linked.st_ino, inodes)
        self.assertEqual(self.store.compose([commit]), checkout)
        self.assertEqual(self.store.read_record(commit)["id"], "one")

    def test_finishing_a_checkout_never_writes_through_a_link(self):
        commit = self.commit("one", {"etc/passwd": "root:x:0:0::/root:/bin/sh\n"})

        def finish(root: Path) -> None:
            (root / "etc/passwd").unlink()
            os.symlink("/run/luma/passwd", root / "etc/passwd")

        checkout = self.store.compose([commit], "salt", finish=finish)
        self.assertTrue((checkout / "etc/passwd").is_symlink())
        plain = self.store.compose([commit])
        self.assertEqual((plain / "etc/passwd").read_text(), "root:x:0:0::/root:/bin/sh\n")

    def test_read_only_directories_in_a_tree_can_be_finished_and_removed(self):
        root = tree(self.base / "build-ro", {**self.shared, "usr/bin/tool": "x"})
        (root / "usr/bin").chmod(0o555)
        root.chmod(0o555)
        try:
            commit = self.store.commit_tree(root, self.store.app_ref("ro", "x86_64"), record("ro"))
        finally:
            root.chmod(0o755)
            (root / "usr/bin").chmod(0o755)
        checkout = self.store.compose([commit], "salt", finish=lambda path: (path / "added-at-the-root").write_text("x"))
        self.assertTrue((checkout / "added-at-the-root").is_file())
        (checkout / "usr/bin").chmod(0o555)
        self.store.remove("ro")
        self.assertFalse(checkout.exists())
        self.assertFalse(self.store.root.exists())

    def test_a_new_version_is_a_commit_and_rollback_is_a_ref_move(self):
        ref = self.store.app_ref("one", "x86_64")
        first = self.commit("one", {"usr/bin/one": "version 1"}, "1")
        second = self.commit("one", {"usr/bin/one": "version 2"}, "2")
        self.assertEqual([entry["commit"] for entry in self.store.history(ref)], [second, first])
        self.assertEqual([entry["subject"] for entry in self.store.history(ref)], ["one 2", "one 1"])
        delta = self.store.delta(first, second)
        self.assertEqual(delta["new_bytes"] > 0, True)
        self.assertEqual(delta["kept_bytes"], sum(len(text) for text in self.shared.values()))
        self.assertEqual(self.store.rollback(ref), first)
        self.assertEqual(self.store.resolve(ref), first)
        self.assertEqual(self.store.read_record(ref)["origin"]["version"], "1")
        with self.assertRaises(InstallerError):
            self.store.rollback(ref)

    def test_removal_collects_by_ref_and_the_last_one_leaves_nothing(self):
        one = self.commit("one", {"usr/bin/one": "1" * 4096})
        self.commit("two", {"usr/bin/two": "2" * 4096})
        self.store.compose([one])
        kept = self.store.keep_package(tree(self.base / "pkg", {"one.deb": "deb"}) / "one.deb", "ab" * 32)
        self.assertTrue(kept.exists())
        self.store.remove("one")
        self.assertEqual(self.store.refs("app/"), ["app/two/x86_64"])
        self.assertEqual([path for path in self.store.checkouts.iterdir() if not path.name.startswith(".")], [])
        contents = [path.read_text() for path in (self.store.repo / "objects").rglob("*.file")]
        self.assertNotIn("1" * 4096, contents)
        self.assertIn(self.shared["usr/lib/libc.so.6"], contents)
        self.store.remove("two")
        self.assertFalse(self.store.root.exists())
        with self.assertRaises(InstallerError):
            self.store.remove("two")

    def test_a_mode_changed_behind_the_stores_back_is_detected_and_not_pulled(self):
        self.commit("one", {"usr/bin/one": "payload"})
        self.assertTrue(self.store.verify())
        other = Store(self.base / "other", fsync=False)
        self.assertEqual(other.pull(self.store.repo), ["app/one/x86_64"])
        self.assertEqual(other.read_record("app/one/x86_64")["id"], "one")
        for path in (self.store.repo / "objects").rglob("*.file"):
            path.chmod(0o775)
        self.assertFalse(self.store.verify())
        third = Store(self.base / "third", fsync=False)
        with self.assertRaises(InstallerError):
            third.pull(self.store.repo)

    def test_every_application_gets_its_own_category_pair(self):
        self.store.ensure()
        levels = {self.store.categories(f"app-{number}") for number in range(40)}
        self.assertEqual(len(levels), 40)
        self.assertEqual(self.store.categories("app-3"), self.store.categories("app-3"))
        for level in levels:
            low, high = (int(part[1:]) for part in level.removeprefix("s0:").split(","))
            self.assertTrue(0 <= low < high <= 1023)


class Categories(unittest.TestCase):
    def test_a_collision_moves_to_the_next_free_pair(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "store")
            first = store.categories("one")
            state = store._state()
            state["categories"] = {"other": store.categories("two"), "one": first}
            store._write_state(state)
            store._write_state({"categories": {"other": first}})
            self.assertNotEqual(store.categories("one"), first)


if __name__ == "__main__":
    unittest.main()
