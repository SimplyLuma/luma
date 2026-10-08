# SPDX-License-Identifier: Apache-2.0
"""Kernel arguments a release declares in /usr/lib/bootc/kargs.d reach the staged deployment.

Found by the OS release gate (nightly 20260915.8): after the agent updated a
VM from .6 to .8, /proc/cmdline lacked efi_pstore.pstore_disable=0, which .8
declares in kargs.d. rpm-ostree applies kargs.d only for container deploys,
not when it deploys an OSTree commit.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import fakes
from fakes import commit, graph_doc, release
from luma_update import kargs
from luma_update.errors import TransactionError

PSTORE = 'kargs = ["efi_pstore.pstore_disable=0"]\n'


class Declared(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dir = self.root / "usr/lib/bootc/kargs.d"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / name).write_text(text)

    def test_no_directory_declares_nothing(self):
        self.assertEqual(kargs.declared(self.root, "x86_64"), [])

    def test_files_in_name_order_without_repeats_and_architecture_matching(self):
        self.write("60-luma-crash-evidence.toml", PSTORE)
        self.write("10-luma-graphical-boot.toml", 'kargs = ["rhgb", "quiet"]\n')
        self.write("20-arm.toml", 'kargs = ["arm64.nosve"]\nmatch-architectures = ["aarch64"]\n')
        self.write("30-both.toml", 'kargs = ["quiet", "mitigations=auto"]\nmatch-architectures = ["x86_64", "aarch64"]\n')
        self.write("README", "not a toml file")
        self.assertEqual(kargs.declared(self.root, "x86_64"),
                         ["rhgb", "quiet", "mitigations=auto", "efi_pstore.pstore_disable=0"])
        self.assertEqual(kargs.declared(self.root, "aarch64"),
                         ["rhgb", "quiet", "arm64.nosve", "mitigations=auto", "efi_pstore.pstore_disable=0"])

    def test_unusable_files_refuse_the_whole_release(self):
        for text in ('kargs = ["a"', 'kargs = "rhgb"\n', 'kargs = ["two words"]\n', 'kargs = [""]\n',
                     'kargs = [1]\n', 'kargs = ["a"]\nmatch-architectures = "x86_64"\n'):
            self.write("50-bad.toml", text)
            with self.assertRaises(kargs.KargsError, msg=text):
                kargs.declared(self.root, "x86_64")


class Staging(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.backend = self.rig.backend
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))

    def tearDown(self):
        self.rig.close()

    def state(self):
        return json.loads(self.rig.paths.state_file.read_text())

    def staged(self):
        return next((d for d in self.backend.list if d.staged), None)

    def events(self):
        return [p for (_m, url, p, _h) in self.rig.http.posts if url.endswith("/events") and "result" in (p or {})]

    def test_declared_arguments_are_added_to_the_staged_deployment(self):
        self.backend.declare_kargs(commit(2), "10-luma-graphical-boot.toml", 'kargs = ["rhgb", "quiet"]\n')
        self.backend.declare_kargs(commit(2), "60-luma-crash-evidence.toml", PSTORE)
        engine = self.rig.engine()
        engine.automatic()
        # rhgb and quiet were already there (from install); only the new one is added.
        self.assertIn(("kargs", ("efi_pstore.pstore_disable=0",), ()), self.backend.calls)
        self.assertEqual(self.staged().kargs, "root=UUID=rig rw rhgb quiet efi_pstore.pstore_disable=0")
        self.assertEqual(self.staged().base_commit, commit(2))
        status = engine.status()
        self.assertEqual((status.state, status.staged_commit), ("staged", commit(2)))
        self.assertEqual(self.state()["pending"]["kargs_added"], ["efi_pstore.pstore_disable=0"])
        self.assertEqual([e["result"] for e in self.events()], ["staged"])
        # The booted report makes them this system's agent-added arguments.
        self.backend.reboot_into(commit(2))
        self.assertEqual(self.rig.engine(boot="boot-2").reconcile_boot("green"), "booted")
        self.assertEqual(self.state()["kargs_added"], ["efi_pstore.pstore_disable=0"])

    def test_nothing_to_add_means_no_extra_transaction(self):
        self.backend.declare_kargs(commit(2), "10-luma-graphical-boot.toml", 'kargs = ["rhgb", "quiet"]\n')
        self.rig.engine().automatic()
        self.assertFalse([c for c in self.backend.calls if c[0] == "kargs"])
        self.assertEqual(self.state()["pending"]["kargs_added"], [])

    def test_only_arguments_the_agent_added_are_removed_when_a_release_drops_them(self):
        self.backend.declare_kargs(commit(2), "60-luma-crash-evidence.toml", PSTORE)
        self.backend.declare_kargs(commit(2), "70-extra.toml", 'kargs = ["luma.extra=1"]\n')
        engine = self.rig.engine()
        engine.automatic()
        self.backend.reboot_into(commit(2))
        self.assertEqual(self.rig.engine(boot="boot-2").reconcile_boot("green"), "booted")
        # 1.0.2 drops both the agent-added luma.extra=1 and the installer's rhgb/quiet file.
        self.rig.now += 7200
        self.rig.publish(graph_doc([release("1.0.1", 2), release("1.0.2", 3)], generated=self.rig.now - 60))
        self.backend.declare_kargs(commit(3), "60-luma-crash-evidence.toml", PSTORE)
        engine = self.rig.engine(boot="boot-2")
        engine.automatic()
        self.assertEqual(self.backend.calls[-1], ("kargs", (), ("luma.extra=1",)))
        self.assertEqual(self.staged().kargs, "root=UUID=rig rw rhgb quiet efi_pstore.pstore_disable=0")
        self.assertEqual(self.state()["pending"]["kargs_added"], ["efi_pstore.pstore_disable=0"])

    def test_a_person_added_argument_is_never_removed(self):
        self.backend.list[0].kargs += " luma.extra=1"  # set by hand, not by the agent
        self.backend.declare_kargs(commit(2), "60-luma-crash-evidence.toml", PSTORE)
        self.rig.engine().automatic()
        self.assertEqual(self.backend.calls[-1], ("kargs", ("efi_pstore.pstore_disable=0",), ()))
        self.assertIn("luma.extra=1", self.staged().kargs.split())

    def test_arguments_for_another_architecture_are_ignored(self):
        self.backend.declare_kargs(commit(2), "20-arm.toml", 'kargs = ["arm64.nosve"]\nmatch-architectures = ["aarch64"]\n')
        engine = self.rig.engine()
        engine.automatic()
        self.assertFalse([c for c in self.backend.calls if c[0] == "kargs"])
        self.assertEqual(engine.status().state, "staged")

    def assert_not_staged_and_failed(self, engine):
        self.assertIn(("cleanup",), self.backend.calls)
        self.assertIsNone(self.staged())
        self.assertIsNone(self.state()["pending"])
        status = engine.status()
        self.assertEqual((status.state, status.last_error_class, status.staged_version),
                         ("available", "transaction", ""))
        reports = self.events() + self.state()["reports"]
        self.assertEqual([(e["result"], e["error_class"]) for e in reports], [("failed", "transaction")])

    def test_unreadable_kargs_file_stops_the_update_from_staging(self):
        self.backend.declare_kargs(commit(2), "60-bad.toml", 'kargs = ["unterminated"\n')
        engine = self.rig.engine()
        with self.assertLogs("luma-update", level="ERROR") as logs:
            engine.automatic()
        self.assertTrue(any("60-bad.toml" in line for line in logs.output))
        self.assert_not_staged_and_failed(engine)

    def test_rpm_ostree_refusing_the_arguments_stops_the_update_from_staging(self):
        self.backend.declare_kargs(commit(2), "60-luma-crash-evidence.toml", PSTORE)
        self.backend.kargs_fail_with = TransactionError("error: KernelArgs failed")
        engine = self.rig.engine()
        engine.automatic()
        self.assert_not_staged_and_failed(engine)

    def test_arguments_that_did_not_stick_stop_the_update_from_staging(self):
        self.backend.declare_kargs(commit(2), "60-luma-crash-evidence.toml", PSTORE)
        self.backend.kernel_args = mock.Mock()  # rpm-ostree reports success but changes nothing
        engine = self.rig.engine()
        engine.automatic()
        self.assert_not_staged_and_failed(engine)

    def test_a_late_cancel_does_not_hide_a_kargs_failure(self):
        self.backend.declare_kargs(commit(2), "60-bad.toml", 'kargs = 7\n')
        engine = self.rig.engine()
        engine.check()
        original = self.backend.update_deployment

        def finish_then_cancel(**kwargs):
            original(**kwargs)
            kwargs["cancellable"].cancel()
        self.backend.update_deployment = finish_then_cancel
        self.assertFalse(engine.download(user_initiated=True))
        self.assert_not_staged_and_failed(engine)

    def test_a_signed_rollback_target_gets_its_own_arguments(self):
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2, deadend=True,
                                                                  rollback_to={"version": "1.0.0", "commit": commit(1)})]))
        self.backend.list = [fakes.FakeDeployment(commit(2), "1.0.1", "luma:luma/1/x86_64/stable", booted=True)]
        self.backend.declare_kargs(commit(1), "60-luma-crash-evidence.toml", PSTORE)
        engine = self.rig.engine()
        engine.automatic()
        self.assertIn(("update", commit(1), None, True), self.backend.calls)
        self.assertIn("efi_pstore.pstore_disable=0", self.staged().kargs.split())

    def test_a_rolled_back_update_leaves_the_booted_systems_record_alone(self):
        self.backend.declare_kargs(commit(2), "60-luma-crash-evidence.toml", PSTORE)
        engine = self.rig.engine()
        engine.automatic()
        self.backend.reboot_into(commit(2))
        self.rig.engine(boot="boot-2").reconcile_boot("red")
        self.backend.reboot_into(commit(1))
        self.assertEqual(self.rig.engine(boot="boot-3").reconcile_boot(None), "rolled_back")
        self.assertEqual(self.state()["kargs_added"], [])


try:
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib
    from luma_update import rpmostree
except (ImportError, ValueError):  # no GObject introspection here
    rpmostree = None


@unittest.skipIf(rpmostree is None, "PyGObject is not available")
class RpmOstreeClient(unittest.TestCase):
    def test_kernel_args_call_and_boot_config(self):
        client = rpmostree.RpmOstree(mock.Mock())
        with mock.patch.object(client, "_run") as run:
            client.kernel_args(existing="root=UUID=x rw", append_if_missing=["efi_pstore.pstore_disable=0"],
                               delete_if_present=["luma.extra=1"])
        method, args = run.call_args.args[:2]
        self.assertEqual(method, "KernelArgs")
        self.assertEqual(args.get_type_string(), "(sasasasa{sv})")
        existing, added, replaced, removed, options = args.unpack()
        self.assertEqual((existing, added, replaced, removed), ("root=UUID=x rw", [], [], []))
        self.assertEqual((options["append-if-missing"], options["delete-if-present"], options["reboot"]),
                         (["efi_pstore.pstore_disable=0"], ["luma.extra=1"], False))
        with mock.patch.object(client, "_run") as run:
            client.kernel_args(existing="rw", append_if_missing=["a=1"])
        self.assertNotIn("delete-if-present", run.call_args.args[1].unpack()[4])
        with mock.patch.object(client, "register"), mock.patch.object(client, "os_path", return_value="/os"), \
                mock.patch.object(client, "_call", return_value=({"options": GLib.Variant("s", "rw quiet"),
                                                                 "staged": GLib.Variant("b", True)},)) as call:
            self.assertEqual(client.boot_config(pending=True), {"options": "rw quiet", "staged": True})
        self.assertEqual(call.call_args.args[2:4], ("GetDeploymentBootConfig", GLib.Variant("(sb)", ("", True))))

    def test_deployment_root_uses_osname_checksum_and_serial(self):
        deployment = rpmostree.parse_deployment({"osname": GLib.Variant("s", "fedora"), "checksum": GLib.Variant("s", "ab" * 32),
                                                 "serial": GLib.Variant("i", 2)})
        self.assertEqual(str(rpmostree.RpmOstree(mock.Mock()).deployment_root(deployment)),
                         "/ostree/deploy/fedora/deploy/" + "ab" * 32 + ".2")


if __name__ == "__main__":
    unittest.main()
