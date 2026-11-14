# SPDX-License-Identifier: Apache-2.0
"""Device-package updates: a device repository release with no new OS commit."""
import gzip
import hashlib
import json
import subprocess
import unittest

import fakes
from fakes import NOW, commit, graph_doc, release
from luma_update import device_layer
from luma_update.device_repo import DeviceRepoError, DeviceUpdates, evr_compare

BASEURL = "https://dl.simplyluma.com/os/devices/aarch64/fairphone-fp6/"
LAYERED = "a" * 64


def primary_xml(packages) -> bytes:
    items = "".join(
        f'<package type="rpm"><name>{name}</name><arch>{arch}</arch>'
        f'<version epoch="{epoch}" ver="{ver}" rel="{rel}"/><size package="{size}"/></package>'
        for name, arch, epoch, ver, rel, size in packages)
    return (f'<?xml version="1.0"?><metadata xmlns="http://linux.duke.edu/metadata/common" '
            f'packages="{len(packages)}">{items}</metadata>').encode()


class DeviceCase(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.rig.publish(graph_doc([release("1.0.0", 1)]))
        self.installed = {"luma-fp6-platform": "0:16-1.luma.4", "luma-fp6-firmware": "0:1-1.luma.4"}
        self.rpm_calls = []
        self.device = DeviceUpdates(self.rig.paths, self.rig.http, "x86_64", run=self.fake_rpm)

    def tearDown(self):
        self.rig.close()

    def fake_rpm(self, argv):
        self.rpm_calls.append(list(argv))
        names = argv[4:]
        out = "".join(f"{n} {self.installed[n]}\n" for n in names if n in self.installed)
        missing = "".join(f"package {n} is not installed\n" for n in names if n not in self.installed)
        return subprocess.CompletedProcess(argv, 1 if missing else 0, out + missing, "")

    def layer(self):
        kit = device_layer.Kit(device="fairphone-fp6", name="Fairphone 6", compatible=["fairphone,fp6"],
                               repo_id="fairphone-fp6", repo_url=BASEURL,
                               install=["luma-fp6-platform", "luma-fp6-firmware"], replace=[],
                               packages=["luma-fp6-platform", "luma-fp6-firmware", "kernel-modules-extra"],
                               version="20261111.1")
        repos = self.rig.root / "etc/yum.repos.d"
        repos.mkdir(parents=True)
        (repos / "fairphone-fp6.repo").write_text(
            device_layer.repo_file_text(kit, BASEURL, "/etc/pki/rpm-gpg/RPM-GPG-KEY-luma-os-release"))
        (repos / "other.repo").write_text("[other]\nbaseurl=https://example.org/repo/\nincludepkgs=luma-fp6-platform\n")
        marker = self.rig.root / "var/lib/luma-device-layer/layered"
        marker.parent.mkdir(parents=True)
        marker.write_text("fairphone-fp6 20261111.1 " + commit(1) + "\n")

    def publish_repo(self, packages, *, tamper=False):
        data = gzip.compress(primary_xml(packages))
        digest = hashlib.sha256(data).hexdigest()
        href = f"repodata/{digest}-primary.xml.gz"
        repomd = (f'<?xml version="1.0"?><repomd xmlns="http://linux.duke.edu/metadata/repo">'
                  f'<revision>1</revision><data type="primary"><checksum type="sha256">{digest}</checksum>'
                  f'<location href="{href}"/></data></repomd>').encode()
        self.rig.http.files[BASEURL + "repodata/repomd.xml"] = repomd
        self.rig.http.files[BASEURL + href] = gzip.compress(primary_xml(packages * 2)) if tamper else data

    def newer_platform(self, rel="1.luma.5"):
        self.publish_repo([("luma-fp6-platform", "x86_64", 0, "16", rel, 4000),
                           ("luma-fp6-platform", "x86_64", 0, "16", "1.luma.4", 4000),
                           ("luma-fp6-firmware", "noarch", 0, "1", "1.luma.4", 9000),
                           ("luma-fp6-platform", "aarch64", 0, "99", "1", 1),
                           ("kernel-modules-extra", "x86_64", 0, "6.18", "1", 1)])

    def engine(self, **kwargs):
        return self.rig.engine(device=self.device, **kwargs)

    def state(self):
        return json.loads(self.rig.paths.state_file.read_text())


class Repository(DeviceCase):
    def test_no_layered_device_reads_nothing(self):
        self.assertIsNone(self.device.repo())
        self.assertEqual(self.device.newer(), [])
        self.assertEqual((self.rpm_calls, self.rig.http.gets), ([], []))

    def test_device_layer_repo_file_is_found(self):
        self.layer()
        repo = self.device.repo()
        self.assertEqual((repo.id, repo.baseurl), ("fairphone-fp6", BASEURL))
        self.assertEqual(set(repo.packages), {"luma-fp6-platform", "luma-fp6-firmware", "kernel-modules-extra"})

    def test_newer_lists_installed_packages_with_newer_builds_of_this_arch(self):
        self.layer()
        self.newer_platform()
        newer = self.device.newer()
        self.assertEqual([(n.name, n.installed, n.available, n.size) for n in newer],
                         [("luma-fp6-platform", "0:16-1.luma.4", "0:16-1.luma.5", 4000)])
        self.assertEqual(newer[0].describe(), "luma-fp6-platform 16-1.luma.5")

    def test_primary_not_matching_repomd_is_refused(self):
        self.layer()
        self.publish_repo([("luma-fp6-platform", "x86_64", 0, "16", "1.luma.5", 1)], tamper=True)
        with self.assertRaises(DeviceRepoError):
            self.device.newer()

    def test_evr_order(self):
        self.assertGreater(evr_compare("0:16-1.luma.10", "0:16-1.luma.9"), 0)
        self.assertGreater(evr_compare("1:1-1", "0:9-9"), 0)
        self.assertEqual(evr_compare("0:1.0-1", "0:1.0-1"), 0)


class Engine(DeviceCase):
    def test_without_device_repo_behaviour_is_unchanged(self):
        engine = self.engine()
        self.assertEqual(engine.check().action, "none")
        self.assertEqual(engine.status().state, "idle")
        self.assertFalse(engine.download(user_initiated=True))
        self.assertEqual(self.rig.backend.transactions(), [])
        self.assertEqual(self.rpm_calls, [])

    def test_device_release_is_offered_staged_and_booted(self):
        self.layer()
        self.newer_platform()
        engine = self.engine()
        decision = engine.check()
        self.assertEqual((decision.action, decision.reason), ("device", "device-update"))
        status = engine.status()
        self.assertEqual((status.state, status.available_version, status.available_commit, status.booted_version),
                         ("available", "1.0.0", commit(1), "1.0.0"))
        self.assertEqual(status.available_summary, "Device update: luma-fp6-platform 16-1.luma.5")
        self.assertEqual(status.download_bytes, 4000)

        self.rig.backend.layered_checksums[commit(1)] = LAYERED
        self.assertTrue(engine.download(user_initiated=True))
        self.assertEqual(self.rig.backend.transactions(),
                         [("refresh-md", True), ("update", commit(1), None, False)])
        status = engine.status()
        self.assertEqual((status.state, status.staged_version, status.staged_commit), ("staged", "1.0.0", commit(1)))
        pending = self.state()["pending"]
        self.assertEqual((pending["kind"], pending["from_checksum"], pending["to_checksum"],
                          pending["device_packages"]),
                         ("device", commit(1), LAYERED, ["luma-fp6-platform-0:16-1.luma.5"]))
        # Staged: not offered again, and the same boot is still pending.
        self.assertEqual(engine.check().action, "none")
        self.assertEqual(engine.status().state, "staged")
        self.assertEqual(engine.reconcile_boot(None), "pending")
        self.assertEqual(engine.apply(lambda: None), "update")

        self.rig.backend.reboot_into(LAYERED)
        self.installed["luma-fp6-platform"] = "0:16-1.luma.5"
        engine = self.engine(boot="boot-2")
        self.assertEqual(engine.reconcile_boot("green"), "booted")
        self.assertIsNone(self.state()["pending"])
        self.assertEqual(self.state()["reports"][-1]["result"], "booted")
        self.assertEqual(engine.check().action, "none")
        self.assertEqual(engine.status().state, "idle")

    def test_greenboot_rollback_skips_that_device_release_only(self):
        self.layer()
        self.newer_platform()
        self.rig.backend.layered_checksums[commit(1)] = LAYERED
        engine = self.engine()
        engine.automatic()
        self.assertEqual(engine.status().state, "staged")
        self.rig.backend.reboot_into(LAYERED)
        self.assertEqual(self.engine(boot="boot-2").reconcile_boot("red"), "pending")
        self.rig.backend.reboot_into(commit(1))
        engine = self.engine(boot="boot-3")
        self.assertEqual(engine.reconcile_boot(None), "rolled_back")
        state = self.state()
        self.assertEqual(state["do_not_retry"], [])  # the base commit is not marked
        self.assertEqual(state["device_skip"]["reason"], "rolled-back-after-failed-boot")
        self.assertEqual(engine.check().action, "none")
        self.newer_platform("1.luma.6")
        self.assertEqual(engine.check().action, "device")

    def test_os_release_takes_precedence_and_refreshes_device_metadata(self):
        self.layer()
        self.newer_platform()
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)], generated=NOW))
        engine = self.engine()
        self.assertEqual(engine.check().action, "update")
        self.assertTrue(engine.download(user_initiated=True))
        self.assertEqual(self.rig.backend.transactions(),
                         [("refresh-md", True), ("update", commit(2), None, False)])
        self.assertEqual(self.state()["pending"]["kind"], "update")

    def test_no_change_is_not_offered_again(self):
        self.layer()
        self.newer_platform()
        engine = self.engine()
        self.assertEqual(engine.check().action, "device")
        self.assertFalse(engine.download(user_initiated=True))
        self.assertEqual(engine.status().state, "idle")
        self.assertEqual(self.state()["device_skip"]["reason"], "no-change")
        self.assertEqual(engine.check().action, "none")

    def test_unreadable_device_repo_leaves_the_os_check_alone(self):
        self.layer()
        engine = self.engine()
        self.assertEqual(engine.check().action, "none")
        status = engine.status()
        self.assertEqual((status.state, status.last_error_class), ("idle", ""))


if __name__ == "__main__":
    unittest.main()
