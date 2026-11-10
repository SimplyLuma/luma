# SPDX-License-Identifier: Apache-2.0
"""Which paths of an OSTree physical root get labeled, and as which path."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/mobile/common/label-ostree-physical-root.py"
spec = importlib.util.spec_from_file_location("label_ostree_physical_root", SCRIPT)
label = importlib.util.module_from_spec(spec)
spec.loader.exec_module(label)


class Walk(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        r = self.root = Path(self.tmp.name)
        dep = "ostree/deploy/luma/deploy/" + "a" * 64 + ".0"
        for d in ("boot/loader.0/entries", "boot/ostree/luma-" + "b" * 64, "ostree/repo/objects/00",
                  "ostree/repo/tmp/cache", "ostree/repo/refs/heads", dep + "/usr/bin", dep + "/etc",
                  "ostree/deploy/luma/backing/x.0/root-transient/upper",
                  "ostree/deploy/luma/var/home/luma/.config", "ostree/deploy/luma/var/lib/rpm",
                  "ostree/boot.0.0/luma/" + "b" * 64):
            (r / d).mkdir(parents=True)
        for f in ("boot/loader.0/entries/ostree-1.conf", "boot/ostree/luma-" + "b" * 64 + "/vmlinuz",
                  "ostree/repo/objects/00/11.file", "ostree/repo/tmp/cache/x", "ostree/repo/config",
                  dep + "/usr/bin/bash", dep + ".origin", "ostree/deploy/luma/var/home/luma/.bashrc",
                  "ostree/deploy/luma/var/lib/rpm/rpmdb.sqlite"):
            (r / f).write_text("")
        (r / "boot/loader").symlink_to("loader.0")
        (r / "ostree/root.a").symlink_to(dep[len("ostree/"):])
        (r / "ostree/boot.0").symlink_to("boot.0.0")
        self.dep = dep

    def paths(self, var):
        return {logical: path for path, logical, _mode in label.walk(str(self.root), var)}

    def test_labels_the_skeleton_and_named_var_paths_only(self):
        got = self.paths(["home"])
        for logical in ("/", "/boot", "/boot/loader", "/boot/loader.0/entries/ostree-1.conf",
                        "/boot/ostree", "/boot/ostree/luma-" + "b" * 64, "/ostree", "/ostree/repo",
                        "/ostree/repo/config", "/ostree/repo/objects", "/ostree/repo/objects/00",
                        "/ostree/repo/tmp", "/ostree/deploy", "/ostree/deploy/luma",
                        "/ostree/deploy/luma/deploy", "/ostree/deploy/luma/backing", "/ostree/root.a",
                        "/ostree/boot.0", "/var/home", "/var/home/luma", "/var/home/luma/.bashrc",
                        "/var/home/luma/.config"):
            self.assertIn(logical, got)
        self.assertEqual(got["/var/home/luma"], str(self.root / "ostree/deploy/luma/var/home/luma"))
        for logical in ("/ostree/repo/objects/00/11.file", "/ostree/repo/tmp/cache", "/" + self.dep,
                        "/" + self.dep + ".origin", "/" + self.dep + "/usr/bin/bash",
                        "/boot/ostree/luma-" + "b" * 64 + "/vmlinuz", "/ostree/deploy/luma/var",
                        "/ostree/deploy/luma/backing/x.0", "/var/lib", "/var/lib/rpm/rpmdb.sqlite"):
            self.assertNotIn(logical, got)
        self.assertFalse([p for p in got.values() if "/deploy/luma/var/lib" in p])

    def test_var_paths_are_opt_in(self):
        got = self.paths([])
        self.assertFalse([logical for logical in got if logical.startswith("/var")])
        self.assertIn("/var/lib/rpm", self.paths(["lib"]))

    def test_rejects_var_paths_outside_var(self):
        for bad in ("/etc", "../etc", "home/../../etc"):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                label.main(["--file-contexts", "fc", "--var", bad, str(self.root)])


if __name__ == "__main__":
    unittest.main()
