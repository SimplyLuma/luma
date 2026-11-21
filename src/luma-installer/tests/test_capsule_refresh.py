"""A capsule's libraries are updated, tried out of sight, and can be taken back."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from luma_installer import capsule_refresh as refresh
from luma_installer import depot_inventory, launch_guard, launcher, manager
from luma_installer.desktop import read_record, write_record
from luma_installer.errors import InstallerError

IMAGE = "localhost/luma-deb-deb-fixture-abc:abc"
OLD = "libssl3 3.5.1-1\nfixture 1.0\n"
NEW = "libssl3 3.5.1-1+deb13u2\nfixture 1.0\n"
SOURCES = ("/etc/apt/sources.list.d/debian.sources:URIs: http://deb.debian.org/debian\n"
           "/etc/apt/sources.list.d/google-chrome.sources:URIs: https://dl.google.com/linux/chrome/deb/\n")


class Engine:
    """Podman, as far as a refresh uses it."""

    def __init__(self, after: str = NEW, running: bool = False):
        self.after, self.running = after, running
        self.index_error = ""
        self.images = {IMAGE}
        self.upgraded = False
        self.commands: list[list[str]] = []

    def __call__(self, arguments, *, timeout=600, check=True):
        self.commands.append(list(arguments))
        code, out = 0, ""
        if arguments[:2] == ["image", "exists"]:
            code = 0 if arguments[2] in self.images else 1
        elif arguments[:2] == ["image", "rm"]:
            self.images.discard(arguments[2])
        elif arguments[0] == "create":
            out = "builder\n"
        elif arguments[0] == "commit":
            self.images.add(arguments[2])
        elif arguments[0] == "exec":
            command = arguments[2:]
            if command == refresh.UPGRADE["deb"]:
                self.upgraded = True
            elif command == refresh.INDEX["deb"]:
                code, out = (100, self.index_error) if self.index_error else (0, "")
                if code:
                    return subprocess.CompletedProcess(arguments, code, out, "")
            elif command == refresh.PACKAGE_SET["deb"]:
                out = self.after if self.upgraded else OLD
            elif command == refresh.SOURCES["deb"]:
                out = SOURCES
        if check and code:
            raise InstallerError("engine failed")
        return subprocess.CompletedProcess(arguments, code, out, "")


class Base(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.engine = Engine()
        for patcher in (patch.dict(os.environ, {"XDG_DATA_HOME": self.directory.name}),
                        patch.object(refresh, "_podman", self.engine),
                        patch.object(refresh, "require_free_space"),
                        patch.object(refresh, "is_running", lambda record: self.engine.running),
                        patch.object(refresh.capsule_runtime, "package_files", return_value=""),
                        patch.object(refresh.time, "time", return_value=1_800_000_000.0)):
            patcher.start()
            self.addCleanup(patcher.stop)
        write_record("fixture", {"format": "deb", "sha256": "a" * 64, "image": IMAGE, "package": "fixture",
                                 "command": "fixture", "name": "Fixture"})
        self.started: list[str] = []

    def start(self, works: bool = True):
        def run(record, image):
            self.started.append(image)
            return works, "stayed up for 10 s" if works else "error while loading shared libraries: libfoo.so.1"
        return run

    def record(self):
        return read_record("fixture")


class Refreshing(Base):
    def test_nothing_changes_when_no_package_moved(self):
        self.engine.after = OLD
        self.assertEqual(refresh.refresh("fixture", start=self.start()), "current")
        self.assertEqual(self.record()["image"], IMAGE)
        self.assertEqual(self.started, [])
        self.assertFalse(any(c[0] == "commit" for c in self.engine.commands))
        self.assertEqual(refresh.summary(self.record())["status"], "current")
        self.assertEqual(refresh.summary(self.record())["checked_unix"], 1_800_000_000)
        self.assertIn(["rm", "-f", "--time=0", "builder"], self.engine.commands)

    def test_an_update_is_tried_out_of_sight_then_used_and_the_previous_image_kept(self):
        self.assertEqual(refresh.refresh("fixture", start=self.start()), "updated")
        record = self.record()
        new = "localhost/luma-deb-deb-fixture-abc:abc-r20270115080000"
        self.assertEqual(record["image"], new)
        self.assertEqual(self.started, [new])
        info = refresh.state(record)
        self.assertEqual(info["previous_image"], IMAGE)
        self.assertEqual(info["packages"], ["libssl3 3.5.1-1 -> 3.5.1-1+deb13u2"])
        self.assertIs(info["confirmed"], False)
        self.assertEqual(self.engine.images, {IMAGE, new})
        self.assertEqual(refresh.label(record), "Updated")
        self.assertTrue(refresh.summary(record)["can_roll_back"])

    def test_the_repositories_a_package_added_are_recorded_as_its_origin(self):
        refresh.refresh("fixture", start=self.start())
        self.assertEqual(refresh.summary(self.record())["vendor_repositories"],
                         [{"file": "/etc/apt/sources.list.d/google-chrome.sources",
                           "uri": "https://dl.google.com/linux/chrome/deb"}])

    def test_a_vendor_source_that_cannot_be_read_does_not_keep_the_other_updates_out(self):
        self.engine.index_error = ("Err:4 https://packages.example/repos/code stable InRelease\n"
                                   "E: The repository 'https://packages.example/repos/code stable InRelease' is not signed.\n")
        self.assertEqual(refresh.refresh("fixture", start=self.start()), "updated")
        detail = refresh.summary(self.record())["detail"]
        self.assertIn("could not be read", detail)
        self.assertIn("packages.example", detail)
        self.assertEqual(refresh.state(self.record())["packages"], ["libssl3 3.5.1-1 -> 3.5.1-1+deb13u2"])

    def test_an_update_that_does_not_start_is_thrown_away_and_not_tried_again(self):
        self.assertEqual(refresh.refresh("fixture", start=self.start(False)), "update failed")
        self.assertEqual(self.record()["image"], IMAGE)
        self.assertEqual(self.engine.images, {IMAGE})
        self.assertIn("libfoo.so.1", refresh.summary(self.record())["detail"])
        self.engine.upgraded, self.engine.commands, self.started = False, [], []
        refresh.refresh("fixture", start=self.start())
        self.assertEqual(self.started, [])
        self.assertFalse(any(c[0] == "commit" for c in self.engine.commands))

    def test_a_library_that_went_missing_fails_the_update_before_any_start(self):
        with patch.object(refresh.capsule_runtime, "package_files", return_value="/usr/bin/fixture\n"), \
             patch.object(refresh.capsule_runtime, "missing_libraries", return_value=["libgone.so.3"]):
            self.assertEqual(refresh.refresh("fixture", start=self.start()), "update failed")
        self.assertEqual(self.started, [])
        self.assertIn("libgone.so.3", refresh.summary(self.record())["detail"])

    def test_a_running_application_keeps_its_image_until_it_is_closed(self):
        self.engine.running = True
        self.assertEqual(refresh.refresh("fixture", start=self.start()), "update waiting")
        record = self.record()
        self.assertEqual(record["image"], IMAGE)
        waiting = refresh.state(record)["pending_image"]
        self.assertEqual(refresh.apply_pending(record)["image"], IMAGE)
        self.engine.running = False
        self.assertEqual(refresh.apply_pending(record)["image"], waiting)
        self.assertEqual(self.record()["image"], waiting)
        self.assertEqual(refresh.state(self.record())["previous_image"], IMAGE)
        self.assertNotIn("pending_image", refresh.state(self.record()))

    def test_only_one_previous_image_is_kept(self):
        refresh.refresh("fixture", start=self.start())
        first = self.record()["image"]
        self.engine.upgraded, self.engine.after = False, NEW + "zlib1g 1.3-2\n"
        with patch.object(refresh.time, "time", return_value=1_800_100_000.0):
            refresh.refresh("fixture", start=self.start())
        second = self.record()["image"]
        self.assertNotEqual(first, second)
        self.assertEqual(refresh.state(self.record())["previous_image"], first)
        self.assertEqual(self.engine.images, {first, second})

    def test_a_held_application_is_left_alone_until_released(self):
        refresh.hold("fixture")
        self.assertEqual(refresh.label(self.record()), "Held")
        self.assertFalse(refresh.due(self.record(), now=2_000_000_000))
        self.assertEqual(refresh.refresh("fixture", start=self.start()), "held")
        self.assertEqual(self.engine.commands, [])
        refresh.hold("fixture", False)
        self.assertTrue(refresh.due(self.record(), now=2_000_000_000))
        self.assertEqual(refresh.refresh("fixture", start=self.start()), "updated")

    def test_going_back_restores_the_previous_image_and_refuses_that_update(self):
        refresh.refresh("fixture", start=self.start())
        refused = self.record()["image"]
        rolled = refresh.rollback("fixture", "Rolled back on request.")
        self.assertEqual(rolled["image"], IMAGE)
        self.assertEqual(refresh.label(self.record()), "Rolled back")
        self.assertEqual(self.engine.images, {IMAGE})
        self.assertFalse(refresh.summary(self.record())["can_roll_back"])
        self.engine.upgraded, self.engine.commands, self.started = False, [], []
        refresh.refresh("fixture", start=self.start())
        self.assertEqual(self.record()["image"], IMAGE)
        self.assertNotIn(refused, self.engine.images)
        with self.assertRaises(InstallerError):
            refresh.rollback("fixture")

    def test_going_back_also_goes_back_to_the_runtime_rules_that_image_has(self):
        write_record("fixture", {**self.record(), "runtime_version": 4})
        refresh.refresh("fixture", start=self.start())
        # The image in use is brought up to newer rules after the update.
        write_record("fixture", {**self.record(), "runtime_version": 5})
        self.assertEqual(refresh.rollback("fixture")["runtime_version"], 4)
        self.assertEqual(self.record()["runtime_version"], 4)

    def test_a_capsule_is_due_once_a_day(self):
        self.assertTrue(refresh.due(self.record(), now=1_800_000_000))
        refresh.refresh("fixture", start=self.start())
        self.assertFalse(refresh.due(self.record(), now=1_800_000_000 + 3600))
        self.assertTrue(refresh.due(self.record(), now=1_800_000_000 + 86400))
        self.assertFalse(refresh.due({"format": "appimage", "root": "/x"}, now=0))

    def test_removal_deletes_the_images_kept_besides_the_one_in_use(self):
        refresh.refresh("fixture", start=self.start())
        record = self.record()
        self.assertEqual(refresh.images(record), [record["image"], IMAGE])
        refresh.forget(record)
        self.assertEqual(self.engine.images, {record["image"]})


class Reading(unittest.TestCase):
    def test_tags_keep_the_package_part_and_carry_the_time(self):
        self.assertEqual(refresh.next_tag("localhost/luma-rpm-x:abc", 0), "localhost/luma-rpm-x:abc-r19700101000000")
        self.assertEqual(refresh.next_tag("localhost/luma-rpm-x:abc-r20270101000000", 60),
                         "localhost/luma-rpm-x:abc-r19700101000100")

    def test_fedora_repositories_owned_by_no_package_are_the_vendors(self):
        text = ("/etc/yum.repos.d/google-chrome.repo:baseurl=https://dl.google.com/linux/chrome/rpm/stable/x86_64\n"
                "/etc/yum.repos.d/vscode.repo:baseurl=https://packages.microsoft.com/yumrepos/vscode\n")
        self.assertEqual([entry["uri"] for entry in refresh.parse_vendor_repositories("rpm", text)],
                         ["https://dl.google.com/linux/chrome/rpm/stable/x86_64",
                          "https://packages.microsoft.com/yumrepos/vscode"])
        old_style = "/etc/apt/sources.list.d/slack.list:deb [signed-by=/k.gpg] https://packagecloud.io/slacktechnologies/slack/debian/ jessie main\n"
        self.assertEqual(refresh.parse_vendor_repositories("deb", old_style)[0]["uri"],
                         "https://packagecloud.io/slacktechnologies/slack/debian")

    def test_changes_name_every_package_that_moved(self):
        self.assertEqual(refresh.changed_packages({"a": "1", "b": "1", "c": "1"}, {"a": "2", "b": "1", "d": "1"}),
                         ["a 1 -> 2", "c 1 -> removed", "d absent -> 1"])

    def test_the_hidden_start_shares_nothing_of_the_person(self):
        record = {"format": "deb", "application_id": "fixture", "command": "fixture --flag", "mcs_level": "s0:c1,c2"}
        home = Path("/scratch/home")
        arguments = refresh.smoke_arguments(record, "localhost/new:tag", home, (Path("/run/user/1/smoke"), "smoke"),
                                            "luma-smoke-fixture")
        self.assertIn("--network=none", arguments)
        self.assertIn("label=type:luma_application.process", arguments)
        self.assertIn("label=level:s0:c1,c2", arguments)
        self.assertIn("/scratch/home:/scratch/home:rw,Z", arguments)
        self.assertEqual(arguments[arguments.index("localhost/new:tag") + 1:], ["fixture", "--flag"])
        text = " ".join(arguments)
        for shared in ("Documents", "pulse", "pipewire", "DBUS_SESSION_BUS_ADDRESS", "at-spi", ".X11-unix"):
            self.assertNotIn(shared, text)

    def test_only_capsules_have_update_state(self):
        self.assertEqual(refresh.summary({"format": "appimage", "root": "/x"}), {})
        self.assertEqual(refresh.kind_of({"format": "portable", "image": "i"}), "rpm")
        self.assertEqual(refresh.kind_of({"format": "portable"}), "")
        self.assertEqual(refresh.summary({"format": "deb", "image": "i"})["status"], "never checked")


class FirstStart(Base):
    """The first real start on a refreshed capsule decides whether it stays."""

    def launch(self, statuses):
        runs = []

        def run(arguments, env=None, capture=True):
            runs.append(arguments[-2])  # the image, just before the command
            return statuses[len(runs) - 1]
        home = Path(self.directory.name)
        with patch("pathlib.Path.home", return_value=home), \
             patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.directory.name}), \
             patch.object(launcher, "_running_capsule", return_value=False), \
             patch.object(launcher, "_start_bus_proxy", return_value=(None, None)), \
             patch.object(launcher, "_person_folders", return_value=[]), \
             patch.object(launcher.launch_guard, "run", side_effect=run), \
             patch.object(launcher.launch_guard, "report") as report, \
             patch.object(launcher.runtime_policy, "record_flag_backoff") as backoff:
            status = launcher._launch_deb(self.record())
        return status, runs, report, backoff

    def test_a_start_that_works_confirms_the_update(self):
        refresh.refresh("fixture", start=self.start())
        new = self.record()["image"]
        status, runs, report, _backoff = self.launch([(0, 600.0, [])])
        self.assertEqual((status, runs), (0, [new]))
        self.assertIs(refresh.state(self.record())["confirmed"], True)
        self.assertEqual(self.record()["image"], new)
        report.assert_not_called()

    def test_a_start_that_fails_goes_back_and_starts_the_application_again(self):
        refresh.refresh("fixture", start=self.start())
        new = self.record()["image"]
        status, runs, report, backoff = self.launch([(127, 0.4, ["error while loading shared libraries"]),
                                                     (0, 600.0, [])])
        self.assertEqual((status, runs), (0, [new, IMAGE]))
        self.assertEqual(self.record()["image"], IMAGE)
        self.assertEqual(refresh.label(self.record()), "Rolled back")
        self.assertIn("first start", refresh.summary(self.record())["detail"])
        # Nobody is told about a failure they did not cause, and Luma's own
        # switches are not blamed for it.
        report.assert_not_called()
        backoff.assert_not_called()
        self.assertNotIn(new, self.engine.images)

    def test_a_waiting_update_is_taken_up_by_the_next_start(self):
        self.engine.running = True
        refresh.refresh("fixture", start=self.start())
        waiting = refresh.state(self.record())["pending_image"]
        self.engine.running = False
        with patch.object(launcher, "_launch_deb", return_value=0) as launch, \
             patch("luma_installer.backends.refresh_capsule_launcher", side_effect=lambda record: record), \
             patch("luma_installer.backends.adopt_own_schemes", side_effect=lambda record: record), \
             patch.object(launcher, "_upgrade_unless_running", side_effect=lambda record: record):
            self.assertEqual(launcher.main(["fixture"]), 0)
        self.assertEqual(launch.call_args.args[0]["image"], waiting)
        self.assertTrue(refresh.unconfirmed(launch.call_args.args[0]))


class Listing(Base):
    def test_the_application_list_carries_the_update_state(self):
        refresh.refresh("fixture", start=self.start())

        class App:
            def get_id(self): return "org.projectluma.Installed.fixture.desktop"
            def get_string(self, key): return None
            def should_show(self): return True
            def get_display_name(self): return "Fixture"
            def get_name(self): return "Fixture"
        listed = depot_inventory.visible_applications([App()], [self.record()])[0]
        self.assertEqual((listed.provider, listed.update_state), ("DEB", "Updated"))
        self.assertEqual(listed.update["packages_updated"], 1)
        self.assertEqual(manager.update_states()["fixture"]["status"], "updated")
        refresh.hold("fixture")
        self.assertEqual(depot_inventory.visible_applications([App()], [self.record()])[0].update_state, "Held")


if __name__ == "__main__":
    unittest.main()
