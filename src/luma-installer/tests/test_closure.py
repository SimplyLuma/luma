# SPDX-License-Identifier: Apache-2.0
"""Per-application userlands (ADR-052): selection, the build sandbox, the lockfile."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from luma_installer import closure
from luma_installer.closure import PackageFacts


class UserlandSelection(unittest.TestCase):
    def test_a_package_needing_a_new_c_library_rules_old_releases_out(self):
        facts = PackageFacts("deb", "app", "1", "amd64", ("libc6 (>= 2.38)", "libgtk-3-0 (>= 3.9.10) | libgtk-4-1"))
        self.assertEqual(closure.candidate_userlands(facts), ["debian-13", "ubuntu-24.04"])

    def test_newest_release_first_when_nothing_rules_one_out(self):
        facts = PackageFacts("deb", "app", "1", "amd64", ("libc6 (>= 2.17)",))
        self.assertEqual(closure.candidate_userlands(facts), ["debian-13", "ubuntu-24.04", "debian-12", "ubuntu-22.04"])

    def test_renamed_library_packages_need_a_release_that_has_them(self):
        facts = PackageFacts("deb", "app", "1", "amd64", ("libasound2t64 (>= 1.0.17)", "libc6 (>= 2.17)"))
        self.assertEqual(closure.candidate_userlands(facts), ["debian-13", "ubuntu-24.04"])
        either = PackageFacts("deb", "app", "1", "amd64", ("libasound2 (>= 1.0.17) | libasound2t64",))
        self.assertIn("debian-12", closure.candidate_userlands(either))

    def test_a_preference_reorders_and_never_adds(self):
        facts = PackageFacts("deb", "app", "1", "amd64", ("libc6 (>= 2.38)",))
        self.assertEqual(closure.candidate_userlands(facts, ("ubuntu-24.04", "debian-12")), ["ubuntu-24.04", "debian-13"])

    def test_rpm_and_appimage_go_to_fedora_newest_first(self):
        rpm = PackageFacts("rpm", "app", "1", "x86_64", ("libc.so.6(GLIBC_2.34)(64bit)", "rpmlib(PayloadIsZstd) <= 5.4.18-1"))
        self.assertEqual(closure.candidate_userlands(rpm), ["fedora-45", "fedora-44"])
        self.assertEqual(closure.candidate_userlands(PackageFacts("appimage", "app", "", "x86_64", ()))[0], "fedora-45")

    def test_every_deb_userland_names_a_keyring_and_signed_sources(self):
        for userland in closure.USERLANDS.values():
            if userland.family != "deb":
                continue
            self.assertTrue(userland.keyring.endswith(".gpg"))
            self.assertEqual(len(userland.sources), 3)
            self.assertNotIn("trusted=yes", " ".join(userland.sources))

    def test_a_stamp_points_every_source_at_the_archive_snapshot(self):
        sources = closure.USERLANDS["debian-12"].sources_at("20260901T000000Z")
        self.assertTrue(all("snapshot.debian.org" in line and "20260901T000000Z" in line for line in sources))
        self.assertTrue(all("check-valid-until=no" in line for line in sources))


class BuildSandbox(unittest.TestCase):
    def test_package_code_sees_the_new_root_and_nothing_of_the_host(self):
        with patch.object(closure, "userns_prefix", return_value=[]), patch.object(closure, "_resolver", return_value=[]):
            arguments = closure.sandbox_in_tree(Path("/work/root"), ["apt-get", "update"])
        self.assertEqual(arguments[0], "bwrap")
        self.assertNotIn("podman", arguments)
        self.assertEqual(arguments[arguments.index("--bind") + 1:arguments.index("--bind") + 3], ["/work/root", "/"])
        for flag in ("--unshare-pid", "--unshare-ipc", "--unshare-uts", "--clearenv", "--die-with-parent"):
            self.assertIn(flag, arguments)
        bound = [arguments[i + 1] for i, value in enumerate(arguments) if value in {"--bind", "--ro-bind"}]
        self.assertEqual(bound, ["/work/root"])

    def test_a_step_that_needs_no_network_gets_none(self):
        with patch.object(closure, "userns_prefix", return_value=[]):
            self.assertIn("--unshare-net", closure.sandbox_in_tree(Path("/r"), ["true"], network=False))

    def test_without_root_the_sandbox_maps_subordinate_ids(self):
        with patch("os.geteuid", return_value=1000):
            self.assertEqual(closure.userns_prefix()[:4], ["unshare", "--user", "--map-root-user", "--map-auto"])
        with patch("os.geteuid", return_value=0):
            self.assertEqual(closure.userns_prefix(), [])

    def test_the_host_tool_sandbox_has_system_folders_read_only_and_no_home(self):
        with tempfile.TemporaryDirectory() as folder:
            host = Path(folder)
            for name in ("usr/bin", "etc", "var", "home/person", "root"):
                (host / name).mkdir(parents=True)
            (host / "bin").symlink_to("usr/bin")
            with patch.object(closure, "userns_prefix", return_value=[]), patch.object(closure, "_resolver", return_value=[]):
                arguments = closure.sandbox_on_host(["dnf5"], binds=[("/w/root", "/target", True)], target="/target", host=host)
        read_only = [(arguments[i + 1], arguments[i + 2]) for i, value in enumerate(arguments) if value == "--ro-bind"]
        self.assertEqual(read_only, [(f"{host}/usr", "/usr"), (f"{host}/etc", "/etc"), (f"{host}/var", "/var")])
        self.assertIn(("usr/bin", "/bin"), [(arguments[i + 1], arguments[i + 2]) for i, value in enumerate(arguments) if value == "--symlink"])
        self.assertEqual([(arguments[i + 1], arguments[i + 2]) for i, value in enumerate(arguments) if value == "--bind"],
                         [("/w/root", "/target")])
        self.assertEqual(arguments[arguments.index("--dev", arguments.index("/target")) + 1], "/target/dev")
        self.assertNotIn("/home", " ".join(arguments).replace(str(host), ""))

    def test_bootstrap_verifies_the_archive_signature(self):
        with tempfile.TemporaryDirectory() as keys:
            (Path(keys) / "debian-archive-keyring.gpg").write_bytes(b"key")
            with patch.dict("os.environ", {"LUMA_USERLAND_KEYS": keys}):
                command = closure.debootstrap_command(closure.USERLANDS["debian-12"], Path("/r"), Path("/c"), "x86_64")
        self.assertIn("--force-check-gpg", command)
        self.assertIn(f"--keyring={keys}/debian-archive-keyring.gpg", command)
        self.assertIn("--variant=minbase", command)
        self.assertEqual(command[-3:], ["bookworm", "/r", "http://deb.debian.org/debian"])

    def test_a_missing_keyring_stops_the_build(self):
        with tempfile.TemporaryDirectory() as empty, patch.object(closure, "KEYRING_DIRECTORIES", (empty,)), \
                patch.dict("os.environ", {"LUMA_USERLAND_KEYS": ""}):
            with self.assertRaises(closure.InstallerError):
                closure.find_keyring("debian-archive-keyring.gpg")

    def test_dnf_checks_signatures_and_installs_into_the_target_only(self):
        command = closure.dnf_command(closure.USERLANDS["fedora-45"], closure.host_architecture(), ["install", "x"])
        self.assertIn("--installroot=/target", command)
        self.assertIn("--setopt=gpgcheck=1", command)
        self.assertIn("--releasever=45", command)
        self.assertIn("gpgcheck=1", closure.FEDORA_REPOSITORIES)


class Lockfile(unittest.TestCase):
    def test_only_installed_packages_are_locked(self):
        text = "libc6\t2.36-9+deb12u13\tamd64\tii \nold\t1\tamd64\trc \nadduser\t3.134\tall\tii \n"
        self.assertEqual(closure.parse_lock(text), [["adduser", "3.134", "all"], ["libc6", "2.36-9+deb12u13", "amd64"]])

    def test_rpm_rows_have_no_status_column(self):
        self.assertEqual(closure.parse_lock("bash\t0:5.3.0-2.fc45\tx86_64\n"), [["bash", "0:5.3.0-2.fc45", "x86_64"]])

    def test_a_simulation_lists_what_would_change(self):
        text = ("Inst libssl3 [3.0.16-1~deb12u1] (3.0.18-1~deb12u2 Debian-Security:12/stable-security [amd64])\n"
                "Inst newpkg (1.0-1 Debian:12.13/stable [amd64])\nConf libssl3 (3.0.18-1~deb12u2 Debian [amd64])\n")
        self.assertEqual(closure.parse_simulation(text), [["libssl3", "3.0.16-1~deb12u1", "3.0.18-1~deb12u2"],
                                                         ["newpkg", "", "1.0-1"]])

    def test_lock_difference(self):
        old = [["a", "1", "amd64"], ["b", "1", "amd64"], ["c", "1", "all"]]
        new = [["a", "2", "amd64"], ["c", "1", "all"], ["d", "1", "amd64"]]
        difference = closure.lock_difference(old, new)
        self.assertEqual(difference["changed"], [["a", "amd64", "1", "2"]])
        self.assertEqual(difference["added"], [["d", "amd64", "1"]])
        self.assertEqual(difference["removed"], [["b", "amd64", "1"]])


class AddedRepositories(unittest.TestCase):
    def test_a_source_the_package_added_is_recorded_and_can_be_put_back(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "root"
            (root / "etc/apt/sources.list.d").mkdir(parents=True)
            (root / "usr/share/keyrings").mkdir(parents=True)
            (root / "usr/share/keyrings/debian-archive-keyring.gpg").write_bytes(b"distribution")
            before = closure.snapshot_files(root, closure.DEB_SOURCE_PATHS)
            (root / "etc/apt/sources.list.d/vendor.list").write_text("deb [arch=amd64] https://vendor.example/apt stable main\n")
            (root / "usr/share/keyrings/vendor.gpg").write_bytes(b"\x99key")
            added = closure.added_repositories(root, before, closure.DEB_SOURCE_PATHS)
            self.assertEqual(sorted(entry["path"] for entry in added),
                             ["etc/apt/sources.list.d/vendor.list", "usr/share/keyrings/vendor.gpg"])
            again = Path(folder) / "again"
            closure.restore_repositories(added + [{"path": "../escape", "sha256": "", "base64": "eA=="}], again)
            self.assertEqual((again / "usr/share/keyrings/vendor.gpg").read_bytes(), b"\x99key")
            self.assertFalse((Path(folder) / "escape").exists())

    def test_a_recorded_source_becomes_the_update_source(self):
        record = {"id": "vendor-app", "format": "deb", "userland": "debian-12", "architecture": "x86_64",
                  "origin": {"package": "vendor-app", "sha256": "ab", "file": "vendor-app.deb",
                             "added_repositories": [{"path": "etc/apt/sources.list.d/vendor.list", "sha256": "", "base64": ""}]}}
        self.assertTrue(closure.spec_from_record(record, "/kept.deb").from_repository)
        record["origin"]["added_repositories"] = []
        spec = closure.spec_from_record(record, "/kept.deb")
        self.assertFalse(spec.from_repository)
        self.assertEqual(spec.package_file, "/kept.deb")


class Cleaning(unittest.TestCase):
    def test_build_leftovers_do_not_reach_the_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for relative in ("usr/sbin/policy-rc.d", "etc/resolv.conf", "etc/machine-id", "tmp/x", "run/lock/y",
                             "var/lib/apt/lists/deb.debian.org_dists", "var/cache/apt/archives/a.deb", "usr/bin/app",
                             closure.DPKG_BUILD_PATH, "var/lib/dpkg/status"):
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_text("x")
            closure._clean_tree(root)
            left = sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())
            self.assertEqual(left, ["usr/bin/app", "var/lib/dpkg/status"])
            self.assertTrue((root / "tmp").is_dir() and (root / "run").is_dir())


if __name__ == "__main__":
    unittest.main()
