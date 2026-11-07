# SPDX-License-Identifier: Apache-2.0
import errno
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from luma_update import device_layer as dl

BASE = "a" * 64
LAYERED = "b" * 64


def kit_json(**overrides):
    data = {
        "schema": 1, "device": "fairphone/fp6", "name": "Fairphone 6", "version": "20261107.1",
        "compatible": ["fairphone,fp6"],
        "repo": {"id": "luma-fairphone-fp6", "url": "https://dl.simplyluma.com/os/devices/aarch64/fairphone-fp6/"},
        "install": ["kernel-modules-luma-fp6", "luma-fp6-platform"],
        "replace": ["ModemManager"],
        "packages": ["kernel-modules-luma-fp6", "luma-fp6-platform", "ModemManager"],
    }
    data.update(overrides)
    return json.dumps(data)


class FakeSystem:
    """rpm-ostree, gpgv and systemctl as the service sees them."""

    def __init__(self, root: Path):
        self.root = root
        self.calls = []
        self.booted = BASE
        self.staged = None
        self.good_signatures = True
        self.fail_install = False
        self.repos_during_install = None
        self.write_origin(BASE, "")

    def write_origin(self, checksum, extra):
        path = self.root / "sysroot/ostree/deploy/luma/deploy" / f"{checksum}.0.origin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[origin]\nbaserefspec=luma:luma/1/aarch64/nightly\n" + extra)

    def boot_layered(self):
        self.write_origin(LAYERED, "\n[packages]\nrequested=kernel-modules-luma-fp6;luma-fp6-platform;\n\n"
                                   "[overrides]\nreplace=repo=luma-fairphone-fp6,ModemManager;\n")
        self.booted, self.staged = LAYERED, None

    def __call__(self, argv):
        argv = list(argv)
        self.calls.append(argv)
        ok = subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "gpgv":
            return ok if self.good_signatures else subprocess.CompletedProcess(argv, 1, "", "BAD signature")
        if argv[:3] == ["rpm-ostree", "status", "--json"]:
            deployments = [{"osname": "luma", "checksum": self.booted, "serial": 0, "booted": True,
                            "requested-packages": []}]
            if self.staged:
                deployments.insert(0, {"osname": "luma", "checksum": self.staged, "serial": 0, "booted": False,
                                       "staged": True,
                                       "requested-packages": ["kernel-modules-luma-fp6", "luma-fp6-platform"]})
            return subprocess.CompletedProcess(argv, 0, json.dumps({"deployments": deployments}), "")
        if argv[:2] == ["rpm-ostree", "install"]:
            self.repos_during_install = sorted(p.name for p in (self.root / "etc/yum.repos.d").glob("*.repo"))
            if self.fail_install:
                return subprocess.CompletedProcess(argv, 1, "", "error: Packages not found: luma-fp6-platform")
            self.staged = LAYERED
        if argv[0] == "mountpoint":
            return subprocess.CompletedProcess(argv, 1, "", "")
        return ok


class DeviceLayerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "etc/luma").mkdir(parents=True)
        (self.root / "etc/luma/device-kit.conf").write_text("# test\nsource=/kitfs\n")
        repos = self.root / "etc/yum.repos.d"
        repos.mkdir(parents=True)
        (repos / "fedora.repo").write_text("[fedora]\n")
        (repos / "fedora-updates.repo").write_text("[updates]\n")
        (self.root / "etc/pki/ostree").mkdir(parents=True)
        (self.root / "etc/pki/ostree/luma-release.gpg").write_bytes(b"key")
        compatible = self.root / "sys/firmware/devicetree/base/compatible"
        compatible.parent.mkdir(parents=True)
        compatible.write_bytes(b"fairphone,fp6\0qcom,milos\0")
        self.kit = self.root / "kitfs/luma-device-kit"
        (self.kit / "repo/repodata").mkdir(parents=True)
        (self.kit / "repo/repodata/repomd.xml").write_text("<repomd/>")
        (self.kit / "repo/repodata/repomd.xml.asc").write_text("sig")
        (self.kit / "kit.json").write_text(kit_json())
        (self.kit / "kit.json.asc").write_text("sig")
        self.system = FakeSystem(self.root)
        self.layer = dl.DeviceLayer(dl.Paths(root=self.root), run=self.system, sleep=lambda _: None)

    def tearDown(self):
        self.tmp.cleanup()

    def commands(self, prefix):
        return [c for c in self.system.calls if c[:len(prefix)] == prefix]

    def repo_file(self):
        return (self.root / "etc/yum.repos.d/luma-fairphone-fp6.repo").read_text()

    def test_first_boot_layers_offline_and_restarts_once(self):
        self.assertEqual(self.layer.apply(), 0)
        replace = self.commands(["rpm-ostree", "override", "replace"])
        self.assertEqual(replace, [["rpm-ostree", "override", "replace", "--experimental", "--from",
                                    "repo=luma-fairphone-fp6", "ModemManager"]])
        self.assertEqual(self.commands(["rpm-ostree", "install"]),
                         [["rpm-ostree", "install", "--idempotent", "kernel-modules-luma-fp6", "luma-fp6-platform"]])
        # Only the device repository is configured during the transaction ...
        self.assertEqual(self.system.repos_during_install, ["luma-fairphone-fp6.repo"])
        # ... and afterwards every repository is back, the device one online.
        names = sorted(p.name for p in (self.root / "etc/yum.repos.d").glob("*.repo"))
        self.assertEqual(names, ["fedora-updates.repo", "fedora.repo", "luma-fairphone-fp6.repo"])
        text = self.repo_file()
        self.assertIn("baseurl=https://dl.simplyluma.com/os/devices/aarch64/fairphone-fp6/\n", text)
        self.assertIn("repo_gpgcheck=1\n", text)
        self.assertIn("gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-luma-os-release\n", text)
        self.assertIn("includepkgs=ModemManager,kernel-modules-luma-fp6,luma-fp6-platform\n", text)
        self.assertEqual(self.commands(["systemctl"]), [["systemctl", "--no-block", "reboot"]])
        verified = [c[-1] for c in self.commands(["gpgv"])]
        self.assertTrue(verified[0].endswith("luma-device-kit/kit.json"))
        self.assertTrue(verified[1].endswith("var/lib/luma-device-layer/repo/repodata/repomd.xml"))
        self.assertFalse((self.root / "var/lib/luma-device-layer/layered").exists())

    def test_second_boot_marks_the_layer_and_does_nothing_else(self):
        self.layer.apply()
        self.system.boot_layered()
        self.system.calls.clear()
        self.assertEqual(self.layer.apply(), 0)
        self.assertTrue((self.root / "var/lib/luma-device-layer/layered").exists())
        self.assertEqual(self.commands(["rpm-ostree", "install"]), [])
        self.assertEqual(self.commands(["systemctl"]), [])
        self.assertFalse((self.root / "var/lib/luma-device-layer/repo").exists())
        self.assertIn("baseurl=https://", self.repo_file())
        self.system.calls.clear()
        self.assertEqual(self.layer.apply(), 0)
        self.assertEqual(self.system.calls, [])

    def test_rolled_back_layer_is_retried_once_then_never(self):
        self.assertEqual(self.layer.apply(), 0)
        self.system.staged = None  # greenboot rolled back to the generic deployment
        self.assertEqual(self.layer.apply(), 0)
        self.system.staged = None
        self.system.calls.clear()
        self.assertEqual(self.layer.apply(), 1)
        self.assertEqual(self.commands(["rpm-ostree", "install"]), [])
        self.assertEqual(self.commands(["systemctl"]), [])
        self.assertIn("not trying again", self.layer.load_state()["last_error"])
        self.system.calls.clear()
        self.assertEqual(self.layer.apply(retry=True), 0)
        self.assertEqual(len(self.commands(["rpm-ostree", "install"])), 1)

    def test_failed_transaction_restores_repositories_and_does_not_restart(self):
        self.system.fail_install = True
        self.assertEqual(self.layer.apply(), 1)
        self.assertEqual(self.commands(["systemctl"]), [])
        names = sorted(p.name for p in (self.root / "etc/yum.repos.d").glob("*.repo"))
        self.assertEqual(names, ["fedora-updates.repo", "fedora.repo", "luma-fairphone-fp6.repo"])
        self.assertIn("Packages not found", self.layer.load_state()["last_error"])

    def test_interrupted_run_gets_its_repositories_back(self):
        repos = self.root / "etc/yum.repos.d"
        (repos / "fedora.repo").rename(repos / "fedora.repo.luma-aside")
        self.layer.restore_repos()
        self.assertTrue((repos / "fedora.repo").exists())
        self.assertEqual(list(repos.glob("*.luma-aside")), [])

    def test_repositories_never_move_across_mounts(self):
        """/etc and /var are separate mounts: a rename between them fails with EXDEV."""
        real = os.replace

        def replace(src, dst):
            if Path(src).parent != Path(dst).parent and "etc" in {Path(src).parts[-2], Path(dst).parts[-2]}:
                raise OSError(errno.EXDEV, "Invalid cross-device link", str(src))
            return real(src, dst)

        with mock.patch.object(dl.os, "replace", replace):
            self.assertEqual(self.layer.apply(), 0)
        self.assertEqual(self.system.repos_during_install, ["luma-fairphone-fp6.repo"])
        names = sorted(p.name for p in (self.root / "etc/yum.repos.d").iterdir())
        self.assertEqual(names, ["fedora-updates.repo", "fedora.repo", "luma-fairphone-fp6.repo"])

    def test_kit_for_another_device_is_refused(self):
        (self.root / "sys/firmware/devicetree/base/compatible").write_bytes(b"google,tokay\0google,zumapro\0")
        self.assertEqual(self.layer.apply(), 1)
        self.assertEqual(self.commands(["rpm-ostree"]), [])
        self.assertIn("this device is google,tokay", self.layer.load_state()["last_error"])

    def test_unsigned_kit_is_refused(self):
        self.system.good_signatures = False
        self.assertEqual(self.layer.apply(), 1)
        self.assertEqual(self.commands(["rpm-ostree"]), [])
        self.assertIn("not signed by the Luma OS Release key", self.layer.load_state()["last_error"])

    def test_missing_signature_is_refused(self):
        (self.kit / "kit.json.asc").unlink()
        self.assertEqual(self.layer.apply(), 1)
        self.assertIn("has no signature", self.layer.load_state()["last_error"])

    def test_no_kit_is_not_an_error(self):
        (self.kit / "kit.json").unlink()
        self.assertEqual(self.layer.apply(), 0)
        self.assertEqual(self.commands(["rpm-ostree"]), [])
        (self.root / "etc/luma/device-kit.conf").unlink()
        self.assertEqual(self.layer.apply(), 0)

    def test_absent_filesystem_is_not_an_error(self):
        (self.root / "etc/luma/device-kit.conf").write_text("source=UUID=032D-57AD\n")
        self.assertEqual(self.layer.apply(wait=0), 0)
        self.assertEqual(self.commands(["blkid"]), [["blkid", "-U", "032D-57AD"]])

    def test_kit_manifest_rules(self):
        dl.parse_kit(kit_json())
        bad = [
            kit_json(schema=2),
            kit_json(compatible=[]),
            kit_json(repo={"id": "x", "url": "http://insecure/"}),
            kit_json(install=["luma-other"]),
            kit_json(install=[], replace=[]),
            kit_json(install=["bad name"]),
        ]
        for text in bad:
            with self.assertRaises(dl.LayerError, msg=text):
                dl.parse_kit(text)

    def test_origin_parser(self):
        requested, replaced = dl.parse_origin(
            "[origin]\nbaserefspec=luma:luma/1/aarch64/nightly\n\n[packages]\nrequested=a;b;\n\n"
            "[overrides]\nreplace=repo=luma-fairphone-fp6,ModemManager,libqmi;\n")
        self.assertEqual(requested, {"a", "b"})
        self.assertEqual(replaced, {"luma-fairphone-fp6": {"ModemManager", "libqmi"}})


if __name__ == "__main__":
    unittest.main()
