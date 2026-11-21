# SPDX-License-Identifier: Apache-2.0
"""Valet installs package files into the application store, with the capsule as the fallback."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from luma_installer import backends, capsule_refresh, closure, session_boundary, store_install, store_migration, store_refresh
from luma_installer.desktop import read_record, write_record
from luma_installer.errors import InstallerError

SHA = "ab" * 32
TREE = {"id": "deb-vendor-abababababab", "format": "deb", "userland": "debian-13", "architecture": "x86_64",
        "baseline_version": closure.BASELINE_VERSION, "windowed": True, "unresolved": [], "packages": [["vendor", "1.0", "amd64"]],
        "origin": {"package": "vendor", "version": "1.0", "sha256": SHA, "file": "vendor.deb", "added_repositories": [
            {"path": "etc/apt/sources.list.d/vendor.sources", "sha256": "x",
             "base64": "VVJJczogaHR0cHM6Ly9wYWNrYWdlcy52ZW5kb3IuZXhhbXBsZS9hcHQKU3VpdGVzOiBzdGFibGUK"},
            {"path": "usr/share/keyrings/vendor.gpg", "sha256": "y", "base64": "a2V5"}]},
        "exports": {"name": "Vendor", "comment": "An editor", "icon": "vendor", "command": "/usr/bin/vendor %U",
                    "desktop_path": "/usr/share/applications/vendor.desktop", "startup_wm_class": "Vendor",
                    "terminal": False, "mime_types": ["x-scheme-handler/vendor"], "chromium": True,
                    "bus_names": ["com.vendor.App"]}}


class Home(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.home = Path(self.folder.name).resolve()
        environment = {"HOME": str(self.home), "XDG_DATA_HOME": str(self.home / ".local/share"),
                       "XDG_RUNTIME_DIR": str(self.home / "run"), "LUMA_APP_SCRATCH": str(self.home / "scratch")}
        for patcher in (patch.dict(os.environ, environment), patch.object(Path, "home", return_value=self.home)):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop(store_install.SWITCH_VARIABLE, None)
        os.environ.pop(store_install.FAIL_VARIABLE, None)

    def report(self, kind="deb"):
        package = self.home / f"vendor.{kind}"
        package.write_bytes(b"package")
        return SimpleNamespace(kind=kind, path=package, sha256=SHA, title="vendor", summary="An editor", byte_size=7,
                               details={"Package name": "vendor"}, requires_system_change=False, version="1.0",
                               architecture="amd64")


class Install(Home):
    def install(self, report, **patches):
        with patch.object(backends, "fingerprint", return_value=(7, SHA)), \
                patch.object(backends, "stage_user_copy", return_value=report.path), \
                patch.object(store_install, "enabled", return_value=patches.get("enabled", True)):
            return backends.install(report)

    def test_a_package_goes_to_the_store_and_its_record_names_the_tree(self):
        built = {"commit": "c1", "tree": TREE, "why": "stayed up for 10 s", "graphics": {"route": "mesa"}}
        with patch.object(store_install, "build_tree", return_value=built) as build, \
                patch.object(store_install.closure_launcher, "deployment", return_value=(self.home / "checkout", TREE)), \
                patch.object(store_install, "export_icon", return_value="vendor-icon"), \
                patch.object(backends, "_install_deb") as capsule, \
                patch("luma_installer.desktop.subprocess.run"):
            name = self.install(self.report())
        self.assertEqual(name, "Vendor")
        capsule.assert_not_called()
        self.assertEqual(build.call_args.args[1:3], ("deb-vendor-abababababab", "deb"))
        record = read_record("deb-vendor-abababababab")
        self.assertEqual(record["store"]["id"], "deb-vendor-abababababab")
        self.assertEqual((record["store"]["commit"], record["store"]["userland"]), ("c1", "debian-13"))
        self.assertNotIn("image", record)
        self.assertEqual(record["command"], "/usr/bin/vendor %U")
        self.assertEqual(record["bus_names"], ["com.vendor.App"])
        self.assertRegex(record["mcs_level"], r"^s0:c\d+,c\d+$")
        self.assertEqual(record["keyring"], {"adopt_until": 0})
        self.assertEqual(record["data_home"], str(self.home / ".local/share/luma/apps-data/deb-vendor-abababababab"))
        # The vendor's own repository is recorded when the application is installed.
        self.assertEqual(record["vendor_repositories"],
                         [{"file": "/etc/apt/sources.list.d/vendor.sources", "uri": "https://packages.vendor.example/apt"}])
        self.assertEqual(capsule_refresh.summary(record)["vendor_repositories"], record["vendor_repositories"])
        launcher = (self.home / ".local/share/applications/org.projectluma.Installed.deb-vendor-abababababab.desktop").read_text()
        self.assertIn("Exec=/usr/bin/luma-capsule-launch deb-vendor-abababababab %U", launcher)
        self.assertIn("MimeType=x-scheme-handler/vendor;", launcher)

    def test_each_failing_stage_falls_back_to_the_capsule_and_the_record_says_why(self):
        def capsule(report):
            write_record("deb-vendor-abababababab", {"format": "deb", "name": "Vendor", "sha256": SHA, "image": "localhost/x:1"})
            return "Vendor"
        for stage in store_install.STAGES:
            with self.subTest(stage=stage), patch.dict(os.environ, {store_install.FAIL_VARIABLE: stage}), \
                    patch.object(store_install, "missing_tools", return_value=[]), \
                    patch.object(store_install.closure, "spec_for_file",
                                 return_value=(store_install.closure.Spec("x", "deb", "debian-13"), ["debian-13"])), \
                    patch.object(store_install.Store, "keep_package", return_value=Path("/kept.deb")), \
                    patch.object(store_install.Store, "ensure"), patch.object(store_install.Store, "resolve", return_value=""), \
                    patch.object(store_install.closure, "build_first",
                                 return_value=SimpleNamespace(root=self.home / "w/root", record=dict(TREE))), \
                    patch.object(store_install.closure, "remove_tree"), \
                    patch.object(store_install.Store, "commit_tree", return_value="c1"), \
                    patch.object(store_install.device_graphics, "ensure_layers", return_value={"route": "none"}), \
                    patch.object(store_install.closure_launcher, "deployment", return_value=(self.home, TREE)), \
                    patch.object(store_install, "discard") as discard, \
                    patch.object(backends, "_install_deb", side_effect=capsule):
                self.assertEqual(self.install(self.report()), "Vendor")
                record = read_record("deb-vendor-abababababab")
                self.assertEqual(record["store_fallback"]["stage"], stage)
                self.assertIn(store_install.FAIL_VARIABLE, record["store_fallback"]["reason"])
                self.assertNotIn("store", record)
                self.assertEqual(discard.called, stage == "launch")

    def test_a_program_that_cannot_find_its_libraries_is_not_stored(self):
        tree = {**TREE, "unresolved": ["libnever.so.1"]}
        with patch.object(store_install, "missing_tools", return_value=[]), \
                patch.object(store_install.closure, "spec_for_file",
                             return_value=(store_install.closure.Spec("x", "deb", "debian-13"), ["debian-13"])), \
                patch.object(store_install.Store, "keep_package", return_value=Path("/kept.deb")), \
                patch.object(store_install.Store, "ensure"), patch.object(store_install.Store, "resolve", return_value=""), \
                patch.object(store_install.closure, "build_first", return_value=SimpleNamespace(root=self.home / "w/root", record=tree)), \
                patch.object(store_install.closure, "remove_tree"), \
                patch.object(store_install.Store, "commit_tree") as commit:
            with self.assertRaises(store_install.Fallback) as raised:
                store_install.build_tree(store_install.Store(self.home / "store"), "x", "deb", package_file=Path("/x.deb"))
        self.assertEqual(raised.exception.stage, "libraries")
        self.assertIn("libnever.so.1", raised.exception.reason)
        commit.assert_not_called()

    def test_without_the_build_tools_or_with_the_switch_off_the_capsule_path_is_used(self):
        with patch.object(store_install.shutil, "which", return_value=None):
            with self.assertRaises(store_install.Fallback) as raised:
                store_install.build_tree(store_install.Store(self.home / "store"), "x", "deb", package_file=Path("/x.deb"))
        self.assertEqual(raised.exception.stage, "build")
        with patch.object(backends, "_install_deb", return_value="Vendor") as capsule, \
                patch.object(store_install, "install") as store:
            self.assertEqual(self.install(self.report(), enabled=False), "Vendor")
        capsule.assert_called_once()
        store.assert_not_called()
        with patch.dict(os.environ, {store_install.SWITCH_VARIABLE: "off"}):
            self.assertFalse(store_install.enabled())
        self.assertTrue(store_install.enabled())

    def test_a_system_package_never_goes_to_the_store(self):
        report = self.report("rpm")
        report.requires_system_change = True
        with patch.object(backends, "_install_system", return_value="Driver") as system, patch.object(store_install, "install") as store:
            self.assertEqual(self.install(report), "Driver")
        system.assert_called_once()
        store.assert_not_called()


class FakeStore:
    """The store's refs, without a repository."""
    refs_now: dict[str, str] = {}
    parents: dict[str, str] = {}

    def __init__(self, root=None, fsync=True):
        self.root = Path(os.environ["HOME"]) / ".local/share/luma/apps"

    app_ref = staticmethod(lambda app, architecture="": f"app/{app}/x86_64")
    pending_ref = staticmethod(lambda app, architecture="": f"pending/{app}/x86_64")

    def resolve(self, ref): return self.refs_now.get(ref, "")
    def has_commit(self, commit): return bool(commit)
    def read_record(self, ref): return dict(TREE)
    def promote(self, ref, commit): self.refs_now[ref] = commit
    def delete_ref(self, ref): self.refs_now.pop(ref, None)
    def collect(self): return {}
    def export(self, ref, destination): Path(destination).mkdir(parents=True)
    def kept_package(self, record): return "/kept.deb"
    def ensure(self): return self

    def rollback(self, ref):
        previous = self.parents.get(self.refs_now.get(ref, ""), "")
        if not previous:
            raise InstallerError("There is no earlier version of this application to return to.")
        self.refs_now[ref] = previous
        return previous

    def remove(self, app):
        self.refs_now.pop(self.app_ref(app), None)


class Refresh(Home):
    APP = "deb-vendor-abababababab"

    def setUp(self):
        super().setUp()
        FakeStore.refs_now = {f"app/{self.APP}/x86_64": "c1"}
        FakeStore.parents = {"c2": "c1"}
        for module in (store_refresh, store_migration):
            patcher = patch.object(module, "Store", FakeStore)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, value in ((store_refresh.closure_launcher, "deployment"), (store_refresh.closure, "remove_tree"),
                              (store_refresh, "require_free_space")):
            patcher = patch.object(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        write_record(self.APP, {"format": "deb", "name": "Vendor", "sha256": SHA, "store": {"id": self.APP, "commit": "c1"}})

    def refresh(self, changes, running=False, works=True, **options):
        def build(store, app, kind, **arguments):
            self.assertEqual((arguments["ref"], arguments["parent"]), (f"pending/{app}/x86_64", "c1"))
            if not works:
                raise store_install.Fallback("launch", "It did not start in the hidden test: exited with status 1")
            FakeStore.refs_now[arguments["ref"]] = "c2"
            return {"commit": "c2", "tree": TREE, "why": "stayed up for 10 s"}
        with patch.object(store_refresh.closure, "resolve_changes", return_value={"changes": changes, "download_bytes": 1}), \
                patch.object(store_refresh.closure, "spec_from_record"), \
                patch.object(store_refresh.store_install, "build_tree", side_effect=build) as built, \
                patch.object(store_refresh, "is_running", return_value=running):
            status = capsule_refresh.refresh(self.APP, **options)
        return status, built

    def test_nothing_changed_builds_nothing(self):
        status, built = self.refresh([])
        self.assertEqual(status, "current")
        built.assert_not_called()
        self.assertEqual(capsule_refresh.summary(read_record(self.APP))["status"], "current")

    def test_a_tried_tree_replaces_the_one_in_use_and_can_be_rolled_back(self):
        status, _built = self.refresh([["libssl3", "3.0.1", "3.0.2"]])
        self.assertEqual(status, "updated")
        record = read_record(self.APP)
        self.assertEqual(FakeStore.refs_now, {f"app/{self.APP}/x86_64": "c2"})
        self.assertEqual((record["store"]["commit"], record["refresh"]["previous_commit"]), ("c2", "c1"))
        self.assertEqual(record["refresh"]["packages"], ["libssl3 3.0.1 -> 3.0.2"])
        self.assertTrue(store_refresh.unconfirmed(record))
        summary = capsule_refresh.summary(record)
        self.assertTrue(summary["can_roll_back"] and summary["in_store"])
        rolled = capsule_refresh.rollback(self.APP, "on request")
        self.assertEqual((rolled["store"]["commit"], FakeStore.refs_now[f"app/{self.APP}/x86_64"]), ("c1", "c1"))
        self.assertEqual(rolled["refresh"]["status"], "rolled back")
        # The same package set is not built again.
        status, built = self.refresh([["libssl3", "3.0.1", "3.0.2"]])
        built.assert_not_called()
        self.assertEqual(status, "rolled back")

    def test_a_running_application_keeps_its_tree_until_it_is_closed(self):
        status, _built = self.refresh([["libssl3", "3.0.1", "3.0.2"]], running=True)
        self.assertEqual(status, "update waiting")
        self.assertEqual(FakeStore.refs_now[f"app/{self.APP}/x86_64"], "c1")
        self.assertEqual(read_record(self.APP)["refresh"]["pending_commit"], "c2")
        with patch.object(store_refresh, "is_running", return_value=False):
            record = store_refresh.before_start(read_record(self.APP))
        self.assertEqual(record["store"]["commit"], "c2")
        self.assertEqual(FakeStore.refs_now, {f"app/{self.APP}/x86_64": "c2"})

    def test_a_tree_that_fails_its_hidden_start_is_never_used_or_offered_again(self):
        status, _built = self.refresh([["libssl3", "3.0.1", "3.0.2"]], works=False)
        self.assertEqual(status, "update failed")
        self.assertEqual(FakeStore.refs_now, {f"app/{self.APP}/x86_64": "c1"})
        self.assertIn("hidden test", read_record(self.APP)["refresh"]["detail"])
        _status, built = self.refresh([["libssl3", "3.0.1", "3.0.2"]])
        built.assert_not_called()

    def test_a_held_application_is_left_alone_until_released(self):
        capsule_refresh.hold(self.APP)
        status, built = self.refresh([["libssl3", "3.0.1", "3.0.2"]])
        self.assertEqual(status, "held")
        built.assert_not_called()
        self.assertFalse(capsule_refresh.due(read_record(self.APP)))
        capsule_refresh.hold(self.APP, False)
        self.assertTrue(capsule_refresh.due(read_record(self.APP)))

    def rebuilt_spec(self, changes):
        """The spec a refresh builds from, for an application that arrived as a file and added no source."""
        spec = closure.Spec(app_id=self.APP, kind="deb", userland="debian-13", package="vendor", package_file="/kept/vendor.deb")
        seen = {}

        def build(store, app, kind, **arguments):
            seen.update(file=arguments["spec"].package_file, by_name=arguments["spec"].from_repository)
            FakeStore.refs_now[arguments["ref"]] = "c2"
            return {"commit": "c2", "tree": TREE, "why": "stayed up for 10 s"}
        with patch.object(store_refresh.closure, "resolve_changes", return_value={"changes": changes, "download_bytes": 1}), \
                patch.object(store_refresh.closure, "spec_from_record", return_value=spec), \
                patch.object(store_refresh.store_install, "build_tree", side_effect=build), \
                patch.object(store_refresh, "is_running", return_value=False):
            self.assertEqual(capsule_refresh.refresh(self.APP), "updated")
        return seen

    def test_a_newer_build_of_the_package_itself_comes_from_the_repository_not_the_kept_file(self):
        # Rebuilt from the kept file, the old version would come back and the same update be found at every check.
        self.assertEqual(self.rebuilt_spec([["vendor:amd64", "1.0", "1.1"]]), {"file": "", "by_name": True})

    def test_other_packages_changing_rebuilds_from_the_kept_file(self):
        self.assertEqual(self.rebuilt_spec([["libssl3", "3.0.1", "3.0.2"]]), {"file": "/kept/vendor.deb", "by_name": False})

    def test_a_moved_baseline_rebuilds_the_tree(self):
        moved = {"version": closure.BASELINE_VERSION + 1, "packages": [], "toolkit": []}
        with patch.object(store_refresh.closure, "baseline_for", return_value=moved):
            status, built = self.refresh([])
        built.assert_called_once()
        self.assertEqual(status, "updated")

    def test_a_failed_first_start_goes_back_and_starts_the_tree_that_worked(self):
        self.refresh([["libssl3", "3.0.1", "3.0.2"]])
        record = read_record(self.APP)
        failure = store_refresh.launch_guard.Failure("exit", 1, 0.4, stderr=["boom"])
        with patch.object(store_refresh.closure_launcher, "launch", return_value=0) as launch:
            self.assertEqual(store_refresh.after_watched_start(record, 1, failure, ["file.txt"]), 0)
        self.assertEqual(FakeStore.refs_now[f"app/{self.APP}/x86_64"], "c1")
        self.assertEqual(launch.call_args.args[1:], (self.APP, ["file.txt"]))
        self.assertEqual(read_record(self.APP)["refresh"]["status"], "rolled back")
        # A start that opened keeps the tree.
        self.refresh([["zlib1g", "1", "2"]])
        self.assertEqual(store_refresh.after_watched_start(read_record(self.APP), 0, None, []), 0)
        self.assertFalse(store_refresh.unconfirmed(read_record(self.APP)))


class Migration(Home):
    APP = "deb-vendor-abababababab"

    def setUp(self):
        super().setUp()
        FakeStore.refs_now, FakeStore.parents = {}, {}
        for module in (store_refresh, store_migration):
            patcher = patch.object(module, "Store", FakeStore)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.capsule = {"format": "deb", "name": "Vendor", "sha256": SHA, "image": "localhost/luma-deb-vendor:abab",
                        "command": "/usr/bin/vendor", "package": "vendor", "mcs_level": "s0:c5,c9",
                        "keyring": {"adopt_until": 0}, "runtime_version": 5, "icon": "old-icon", "installed_unix": 100}
        write_record(self.APP, self.capsule)
        self.old_data = self.home / ".local/share/luma/installer/data" / SHA
        (self.old_data / ".config").mkdir(parents=True)
        (self.old_data / ".config/profile").write_text("signed in")
        staging = self.home / ".local/share/luma/installer/staging" / SHA
        staging.mkdir(parents=True)
        (staging / "vendor.deb").write_bytes(b"package")
        self.new_data = self.home / ".local/share/luma/apps-data" / self.APP

    def migrate(self, running=False, failure=None):
        def build(store, app, kind, **arguments):
            self.assertEqual(arguments["package_file"].name, "vendor.deb")
            if failure:
                raise store_install.Fallback(*failure)
            FakeStore.refs_now[f"app/{app}/x86_64"] = "c1"
            return {"commit": "c1", "tree": TREE, "why": "stayed up", "graphics": {"route": "mesa"}}
        with patch.object(store_migration.store_install, "build_tree", side_effect=build), \
                patch.object(store_migration, "capsule_running", return_value=running), \
                patch.object(store_migration, "require_free_space"), \
                patch.object(store_migration.closure_launcher, "deployment", return_value=(self.home / "checkout", TREE)), \
                patch.object(store_migration.store_install, "export_icon", return_value="new-icon"), \
                patch("luma_installer.desktop.subprocess.run"):
            return store_migration.migrate(self.APP)

    def test_a_failed_build_leaves_the_capsule_untouched(self):
        result = self.migrate(failure=("libraries", "Its program needs libraries its distribution does not provide: libx.so"))
        self.assertIn("stays a capsule (libraries)", result)
        record = read_record(self.APP)
        self.assertEqual({key: record[key] for key in self.capsule}, self.capsule)
        self.assertEqual(record["migration"]["state"], "failed")
        self.assertTrue(self.old_data.is_dir())
        self.assertFalse(store_migration.eligible(record))
        with patch.object(store_migration, "installer_release", return_value=999):
            self.assertTrue(store_migration.eligible(record))  # tried again once the installer has changed

    def test_a_closed_application_is_switched_and_keeps_its_identity_data_and_capsule(self):
        self.assertTrue(self.migrate().startswith("moved"))
        record = read_record(self.APP)
        self.assertEqual(record["store"]["id"], self.APP)
        self.assertNotIn("image", record)
        self.assertEqual((record["mcs_level"], record["keyring"], record["sha256"]), ("s0:c5,c9", {"adopt_until": 0}, SHA))
        self.assertEqual(record["installed_unix"], 100)
        from luma_installer import capsule_identity
        self.assertEqual(capsule_identity.keyring_tag(record), capsule_identity.keyring_tag(self.capsule))
        self.assertEqual((self.new_data / ".config/profile").read_text(), "signed in")
        self.assertFalse(self.old_data.exists())
        self.assertEqual(record["migration"]["capsule"]["image"], "localhost/luma-deb-vendor:abab")
        self.assertTrue(store_migration.unconfirmed(record) and store_refresh.unconfirmed(record))
        self.assertTrue(record["legacy_home"])

    def test_the_capsule_goes_only_after_the_first_start_from_the_store(self):
        self.migrate()
        with patch.object(store_migration.subprocess, "run") as run:
            store_refresh.after_watched_start(read_record(self.APP), 0, None, [])
        self.assertIn(["podman", "image", "rm", "--force", "localhost/luma-deb-vendor:abab"], [call.args[0] for call in run.call_args_list])
        record = read_record(self.APP)
        self.assertEqual(record["migration"]["state"], "done")
        self.assertFalse((self.home / ".local/share/luma/installer/staging" / SHA).exists())
        self.assertFalse(store_refresh.unconfirmed(record))

    def test_a_removal_cut_short_is_finished_at_the_next_start(self):
        self.migrate()
        with patch.object(store_migration.subprocess, "run", side_effect=SystemExit(143)), self.assertRaises(SystemExit):
            store_refresh.after_watched_start(read_record(self.APP), 0, None, [])
        record = read_record(self.APP)
        # The record no longer names a capsule it could go back to, and knows what is still to be deleted.
        self.assertEqual(record["migration"]["state"], "done")
        self.assertEqual(record["migration"]["leaving"]["images"], ["localhost/luma-deb-vendor:abab"])
        self.assertFalse(store_refresh.unconfirmed(record))
        with patch.object(store_migration.subprocess, "run") as run:
            record = store_migration.before_start(record)
        self.assertIn(["podman", "image", "rm", "--force", "localhost/luma-deb-vendor:abab"], [call.args[0] for call in run.call_args_list])
        self.assertNotIn("leaving", read_record(self.APP)["migration"])
        self.assertFalse((self.home / ".local/share/luma/installer/staging" / SHA).exists())

    def test_a_failed_first_start_puts_the_capsule_back(self):
        self.migrate()
        failure = store_refresh.launch_guard.Failure("exit", 127, 0.2, stderr=["error while loading shared libraries"])
        with patch("luma_installer.launcher.main", return_value=0) as capsule_start, \
                patch.object(store_migration.closure_launcher, "remove_runtime"):
            self.assertEqual(store_refresh.after_watched_start(read_record(self.APP), 127, failure, ["x"]), 0)
        capsule_start.assert_called_once_with([self.APP, "x"])
        record = read_record(self.APP)
        self.assertEqual({key: record[key] for key in self.capsule}, self.capsule)
        self.assertNotIn("store", record)
        self.assertEqual((self.old_data / ".config/profile").read_text(), "signed in")
        self.assertFalse(self.new_data.exists())
        self.assertEqual(FakeStore.refs_now, {})
        self.assertEqual(record["migration"]["stage"], "first start")

    def test_a_running_capsule_is_switched_at_its_next_start(self):
        self.assertIn("waits until the application is closed", self.migrate(running=True))
        record = read_record(self.APP)
        self.assertEqual((record["image"], record["migration"]["state"]), (self.capsule["image"], "ready"))
        self.assertTrue(self.old_data.is_dir())
        with patch.object(store_migration, "capsule_running", return_value=False), \
                patch.object(store_migration.closure_launcher, "deployment", return_value=(self.home / "checkout", TREE)), \
                patch.object(store_migration.store_install, "export_icon", return_value="new-icon"), \
                patch("luma_installer.desktop.subprocess.run"):
            switched = store_refresh.before_start(record)
        self.assertEqual(switched["store"]["id"], self.APP)
        self.assertTrue(self.new_data.is_dir())
        # The start that follows is watched and ends in a confirmation: it reads the identity from this record.
        self.assertEqual(switched["application_id"], self.APP)
        with patch.object(store_migration.subprocess, "run"):
            store_refresh.after_watched_start(switched, 0, None, [])
        self.assertEqual(read_record(self.APP)["migration"]["state"], "done")

    def test_only_capsules_are_candidates(self):
        self.assertTrue(store_migration.eligible(self.capsule))
        for other in ({"format": "flatpak"}, {**self.capsule, "store": {"id": "x"}}, {**self.capsule, "system_receipt": "r"},
                      {"format": "appimage", "root": "/x", "kept": False}, {"format": "rpm", "external_layered": True}):
            self.assertFalse(store_migration.eligible(other), other)


class Boundary(unittest.TestCase):
    def test_one_description_serves_podman_and_bubblewrap(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            (runtime / "pulse").mkdir()
            for name in ("wayland-0", "pipewire-0", "pipewire-0-application", "pulse/native", "a.a11y", "a.bus"):
                (runtime / name).touch()
            inside = Path("/run/user/1000")
            session = session_boundary.view(runtime, inside, {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0", "XAUTHORITY": "/x"},
                                            accessibility=runtime / "a.a11y", bus_socket=runtime / "a.bus")
            sources = {mount.source.name: str(mount.inside) for mount in session.mounts}
            self.assertEqual(sources, {"wayland-0": "/run/user/1000/wayland-0", "pipewire-0-application": "/run/user/1000/pipewire-0",
                                       "native": "/run/user/1000/pulse/native", "a.a11y": "/run/user/1000/at-spi/bus",
                                       "a.bus": "/run/user/1000/a.bus"})
            podman, bubblewrap = session.podman(), session.bubblewrap()
            for mount in session.mounts:
                self.assertIn(f"{mount.source}:{mount.inside}:rw", podman)
                self.assertIn(str(mount.source), bubblewrap)
            self.assertNotIn("DISPLAY", session.environment)
            self.assertNotIn("XAUTHORITY", session.environment)
            self.assertEqual(session_boundary.cleaned({"XAUTHORITY": "/x", "DISPLAY": ":0", "LANG": "C"}), {"LANG": "C"})
            quiet = session_boundary.view(runtime, inside, {"WAYLAND_DISPLAY": "wayland-0"}, sound=False)
            self.assertEqual([mount.source.name for mount in quiet.mounts], ["wayland-0"])

class TwoArchitectures(unittest.TestCase):
    def test_a_32_bit_debian_package_is_let_through_only_when_the_store_can_take_it(self):
        from luma_installer import inspectors
        report = SimpleNamespace(kind="deb", architecture="i386")
        with patch.object(inspectors, "host_architecture", return_value="x86_64"), \
                patch.object(store_install, "missing_tools", return_value=[]):
            with patch.object(store_install, "enabled", return_value=True):
                self.assertTrue(inspectors.architecture_is_compatible(report))
            with patch.object(store_install, "enabled", return_value=False):
                self.assertFalse(inspectors.architecture_is_compatible(report))
        with patch.object(inspectors, "host_architecture", return_value="x86_64"), \
                patch.object(store_install, "enabled", return_value=True), \
                patch.object(store_install, "missing_tools", return_value=["debootstrap"]):
            self.assertFalse(inspectors.architecture_is_compatible(report))
        with patch.object(inspectors, "host_architecture", return_value="aarch64"):
            self.assertFalse(inspectors.architecture_is_compatible(report))
            self.assertFalse(inspectors.needs_two_architectures(report))


class HiddenDisplay(unittest.TestCase):
    def test_the_hidden_x_server_is_read_from_what_the_compositor_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            self.assertIsNone(capsule_refresh.hidden_x11(runtime, "luma-smoke-1"))
            (runtime / "luma-smoke-1.x11").write_text(":1\n/run/user/1000/.mutter-Xwaylandauth.ABC\n")
            self.assertEqual(capsule_refresh.hidden_x11(runtime, "luma-smoke-1"), (":1", "/run/user/1000/.mutter-Xwaylandauth.ABC"))
            (runtime / "luma-smoke-1.x11").write_text("\n\n")
            self.assertIsNone(capsule_refresh.hidden_x11(runtime, "luma-smoke-1"))


class DesktopSchemas(unittest.TestCase):
    SCHEMA = """<schemalist><schema id="org.gnome.settings-daemon.plugins.%s" path="/org/gnome/settings-daemon/plugins/%s/">
<key name="x" type="b"><default>true</default></key></schema></schemalist>"""

    @unittest.skipUnless(__import__("shutil").which("glib-compile-schemas"), "needs glib-compile-schemas")
    def test_the_devices_schemas_are_compiled_once_without_xsettings(self):
        with tempfile.TemporaryDirectory() as directory:
            source, holder = Path(directory) / "schemas", Path(directory) / "holder"
            source.mkdir()
            self.assertIsNone(session_boundary.desktop_schemas(holder, source))
            for name in ("media-keys", "xsettings"):
                (source / f"org.gnome.settings-daemon.plugins.{name}.gschema.xml").write_text(self.SCHEMA % (name, name))
            (source / "org.gnome.desktop.interface.gschema.xml").write_text("<schemalist/>")
            made = session_boundary.desktop_schemas(holder, source)
            self.assertEqual(sorted(path.name for path in made.iterdir()),
                             ["gschemas.compiled", "org.gnome.settings-daemon.plugins.media-keys.gschema.xml"])
            self.assertEqual(session_boundary.desktop_schemas(holder, source), made)
            self.assertEqual([path.name for path in holder.iterdir()], [made.name])


class Exports(unittest.TestCase):
    def test_a_hidden_link_handler_is_not_the_applications_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launchers = root / "usr/share/applications"
            launchers.mkdir(parents=True)
            (launchers / "com.vendor.Editor.UrlHandler.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=Editor - URL Handler\nExec=/usr/share/editor/editor --open-url %U\n"
                "NoDisplay=true\nMimeType=x-scheme-handler/editor;\n")
            (launchers / "com.vendor.Editor.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=Editor\nExec=/usr/share/editor/editor %F\nIcon=editor\n")
            spec = closure.Spec(app_id="deb-editor-1", kind="deb", userland="debian-13", package="editor")
            files = "/usr/share/applications/com.vendor.Editor.UrlHandler.desktop\n/usr/share/applications/com.vendor.Editor.desktop\n"
            exports = closure._exports(root, spec, files)
            self.assertEqual(exports["desktop_path"], "/usr/share/applications/com.vendor.Editor.desktop")
            self.assertNotIn("--open-url", exports["command"])


class ReviewCache(unittest.TestCase):
    def test_removal_drops_what_reviewing_the_package_cached(self):
        import base64
        import hashlib
        from luma_installer import facts
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"XDG_CACHE_HOME": directory}):
            root = Path(directory) / "luma/installer/artwork"
            root.mkdir(parents=True)
            picture = b"picture"
            (root / f"{SHA}-v2.json").write_text(json.dumps({"entry": {}, "artwork": base64.b64encode(picture).decode(), "suffix": ".png"}))
            (root / (hashlib.sha256(picture).hexdigest() + ".png")).write_bytes(picture)
            facts.forget_artwork(SHA)
            self.assertFalse((Path(directory) / "luma").exists() and any((Path(directory) / "luma").rglob("*")))
            facts.forget_artwork(SHA)  # nothing cached: nothing to do


class SocketFolder(unittest.TestCase):
    def test_the_launchers_socket_folder_goes_only_when_no_launcher_has_a_socket_in_it(self):
        from luma_installer import manager
        with tempfile.TemporaryDirectory() as runtime, patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime}):
            folder = Path(runtime) / "luma-installer-bus"
            folder.mkdir()
            (folder / "abc-123.bus").touch()
            manager._forget_socket_folder()
            self.assertTrue(folder.exists())
            (folder / "abc-123.bus").unlink()
            manager._forget_socket_folder()
            self.assertFalse(folder.exists())
            manager._forget_socket_folder()  # nothing there: nothing to do


class BuildLogs(unittest.TestCase):
    def test_a_removed_application_leaves_no_build_log_and_the_last_no_folder(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Path, "home", return_value=Path(directory)):
            first, second = store_install.log_path("deb-one-1"), store_install.log_path("deb-two-2")
            first.write_text("log")
            second.write_text("log")
            store_install.forget_log("deb-one-1")
            self.assertEqual([path.name for path in first.parent.iterdir()], ["deb-two-2.log"])
            store_install.forget_log("deb-two-2")
            self.assertFalse(first.parent.exists())
            store_install.forget_log("deb-two-2")  # nothing there: nothing made


class RefFolders(unittest.TestCase):
    def test_a_deleted_ref_leaves_no_folder_named_for_its_application(self):
        from luma_installer.app_store import Store
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            heads = store.repo / "refs/heads"
            (heads / "app/gone").mkdir(parents=True)
            (heads / "app/kept").mkdir(parents=True)
            (heads / "app/kept/x86_64").write_text("c1\n")
            store._drop_empty_ref_folders()
            self.assertEqual(sorted(str(p.relative_to(heads)) for p in heads.rglob("*")), ["app", "app/kept", "app/kept/x86_64"])



if __name__ == "__main__":
    unittest.main()
