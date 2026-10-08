# SPDX-License-Identifier: Apache-2.0
"""Official publisher channels: the catalogue fields and the root helper.

Nothing here talks to snapd, rpm-ostree, gpg or the network: each is replaced
by a recording stand-in, so the tests pin what the helper refuses, what it
runs and in which order.
"""

from copy import deepcopy
import contextlib
import io
import unittest
from pathlib import Path
import tempfile
from unittest import mock

from luma_installer import depot_channels
from luma_installer.depot_catalog import (CatalogError, RpmRepositorySource, SnapSource,
                                          validate_catalog)
from luma_installer.system_overrides import Refused

NORDVPN = {
    "id": "nordvpn", "name": "NordVPN", "tier": "listed", "visibility": "public",
    "backend": "snap", "repository": "snap-store", "source_id": "nordvpn",
    "distribution": "publisher", "architectures": ["x86_64", "aarch64"],
    "reference_url": "https://snapcraft.io/nordvpn", "qualification": "pending",
    "qualification_reason": "NordVPN's own snap.",
    "sources": {"snap": {"name": "nordvpn", "publisher": "nordvpn", "channel": "stable"}},
    "channel": {"kind": "snap", "title": "NordVPN’s snap", "publisher": "NordVPN", "publisher_verified": True},
    "sandbox": "snap-strict",
}
MEGA = {
    "id": "mega", "name": "MEGA", "tier": "listed", "visibility": "public",
    "backend": "rpm", "repository": "mega", "source_id": "megasync",
    "distribution": "publisher", "architectures": ["x86_64", "aarch64"],
    "reference_url": "https://mega.io/desktop", "qualification": "pending",
    "qualification_reason": "MEGA's own repository.",
    "sources": {"rpm_repository": {
        "id": "mega", "name": "MEGA", "package": "megasync",
        "baseurl": "https://mega.nz/linux/repo/Fedora_$releasever/",
        "gpgkey": "https://mega.nz/linux/repo/Fedora_$releasever/repodata/repomd.xml.key",
        "fingerprints": ["B01C811880480C854C73EC7E1A664B787094A482"]}},
    "channel": {"kind": "rpm-repository", "title": "MEGA’s software repository"},
}


def catalog(*entries, generated="2026-09-18T00:00:00Z"):
    return validate_catalog({"schema_version": 4, "generated_at": generated, "applications": list(entries)})


class CatalogueFields(unittest.TestCase):
    def test_snap_source_and_channel(self):
        entry = catalog(NORDVPN).applications[0]
        self.assertEqual(entry.snap, SnapSource("nordvpn", "nordvpn", "stable"))
        self.assertEqual(entry.channel.kind, "snap")
        self.assertTrue(entry.channel.publisher_verified)
        self.assertEqual(entry.sandbox, "snap-strict")

    def test_repository_source(self):
        entry = catalog(MEGA).applications[0]
        self.assertEqual(entry.rpm_repository.package, "megasync")
        self.assertEqual(entry.rpm_repository.fingerprints, ("B01C811880480C854C73EC7E1A664B787094A482",))

    def test_a_snap_that_disagrees_with_its_entry_is_left_out(self):
        entry = deepcopy(NORDVPN)
        entry["sources"]["snap"]["name"] = "evilvpn"
        self.assertEqual(catalog(entry).applications, ())

    def test_malformed_snap_source_refuses_the_catalogue(self):
        for mutate in (lambda e: e["sources"]["snap"].__setitem__("name", "Bad Name"),
                       lambda e: e["sources"]["snap"].__setitem__("publisher", ""),
                       lambda e: e["sources"]["snap"].__setitem__("channel", "latest/../edge")):
            entry = deepcopy(NORDVPN)
            mutate(entry)
            with self.assertRaises(CatalogError):
                catalog(entry)

    def test_repository_needs_https_and_a_fingerprint(self):
        for mutate in (lambda e: e["sources"]["rpm_repository"].__setitem__("baseurl", "http://mega.nz/repo/"),
                       lambda e: e["sources"]["rpm_repository"].__setitem__("fingerprints", []),
                       lambda e: e["sources"]["rpm_repository"].__setitem__("fingerprints", ["abc"]),
                       lambda e: e["sources"]["rpm_repository"].__setitem__("gpgkey", "https://x/$HOME")):
            entry = deepcopy(MEGA)
            mutate(entry)
            with self.assertRaises(CatalogError):
                catalog(entry)

    def test_a_repository_not_compiled_in_is_left_out(self):
        entry = deepcopy(MEGA)
        entry["repository"] = "strangers"
        entry["sources"]["rpm_repository"]["id"] = "strangers"
        self.assertEqual(catalog(entry).applications, ())

    def test_fedora_packages_name_no_address(self):
        entry = deepcopy(MEGA)
        entry.update(repository="fedora", source_id="ollama")
        entry["sources"]["rpm_repository"] = {"id": "fedora", "package": "ollama"}
        self.assertEqual(catalog(entry).applications[0].rpm_repository,
                         RpmRepositorySource("fedora", "Fedora", "ollama"))
        entry["sources"]["rpm_repository"]["baseurl"] = "https://elsewhere.example/"
        with self.assertRaises(CatalogError):
            catalog(entry)


class FakeSnapd:
    def __init__(self, publisher="nordvpn", validation="verified", confinement="strict", installed=None):
        self.store = {"name": "nordvpn", "publisher": {"username": publisher, "validation": validation},
                      "confinement": confinement}
        self.installed_snap = installed
        self.calls = []

    def ensure_running(self):
        self.calls.append("ensure")

    def installed(self, name):
        return self.installed_snap

    def store_info(self, name):
        return self.store

    def request(self, method, target, body=None):
        self.calls.append((method, target, body))
        if method == "POST" and body["action"] == "install":
            self.installed_snap = {"publisher": {"username": self.store["publisher"]["username"]}}
        return {"change": "7"}

    def wait(self, change, label):
        self.calls.append(("wait", change, label))

    def connect(self, name, plugs):
        self.calls.append(("connect", name, tuple(plugs)))
        return []


def quiet(function, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


class SnapHelper(unittest.TestCase):
    def setUp(self):
        self.catalogs = [catalog(NORDVPN)]
        patcher = mock.patch.object(depot_channels, "machine", return_value="x86_64")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_installs_the_pinned_verified_strict_snap(self):
        snapd = FakeSnapd()
        self.assertEqual(quiet(depot_channels.snap_install, "nordvpn", 1000, snapd=snapd, catalogs=self.catalogs),
                         "installed")
        self.assertIn(("POST", "/v2/snaps/nordvpn", {"action": "install", "channel": "stable"}), snapd.calls)

    def test_connects_the_reviewed_interfaces(self):
        entry = deepcopy(NORDVPN)
        entry["sources"]["snap"]["connect"] = ["network-control", "firewall-control"]
        snapd = FakeSnapd()
        quiet(depot_channels.snap_install, "nordvpn", 1000, snapd=snapd, catalogs=[catalog(entry)])
        self.assertIn(("connect", "nordvpn", ("network-control", "firewall-control")), snapd.calls)

    def test_a_bad_interface_name_refuses_the_catalogue(self):
        entry = deepcopy(NORDVPN)
        entry["sources"]["snap"]["connect"] = ["network control"]
        with self.assertRaises(CatalogError):
            catalog(entry)

    def test_refuses_another_publisher(self):
        with self.assertRaises(Refused):
            quiet(depot_channels.snap_install, "nordvpn", 1000, snapd=FakeSnapd(publisher="someone"),
                  catalogs=self.catalogs)

    def test_refuses_an_unverified_publisher(self):
        with self.assertRaises(Refused):
            quiet(depot_channels.snap_install, "nordvpn", 1000, snapd=FakeSnapd(validation="unproven"),
                  catalogs=self.catalogs)

    def test_refuses_a_classic_snap(self):
        with self.assertRaises(Refused):
            quiet(depot_channels.snap_install, "nordvpn", 1000, snapd=FakeSnapd(confinement="classic"),
                  catalogs=self.catalogs)

    def test_already_installed_is_unchanged(self):
        snapd = FakeSnapd(installed={"publisher": {"username": "nordvpn"}})
        self.assertEqual(quiet(depot_channels.snap_install, "nordvpn", 1000, snapd=snapd, catalogs=self.catalogs),
                         "unchanged")
        self.assertFalse(any(isinstance(call, tuple) and call[0] == "POST" for call in snapd.calls))

    def test_unlisted_ids_and_other_processors_are_refused(self):
        with self.assertRaises(Refused):
            quiet(depot_channels.snap_install, "Not An Id", 1000, snapd=FakeSnapd(), catalogs=self.catalogs)
        with self.assertRaises(Refused):
            quiet(depot_channels.snap_install, "termius", 1000, snapd=FakeSnapd(), catalogs=self.catalogs)
        with mock.patch.object(depot_channels, "machine", return_value="riscv64"):
            with self.assertRaises(Refused):
                quiet(depot_channels.snap_install, "nordvpn", 1000, snapd=FakeSnapd(), catalogs=self.catalogs)

    def test_newest_catalogue_decides(self):
        older = deepcopy(NORDVPN)
        older["sources"]["snap"]["publisher"] = "someone-else"
        catalogs = [catalog(older, generated="2026-09-01T00:00:00Z"), catalog(NORDVPN)]
        self.assertEqual(quiet(depot_channels.snap_install, "nordvpn", 1000, snapd=FakeSnapd(), catalogs=catalogs),
                         "installed")

    def test_nothing_newer_is_up_to_date(self):
        snapd = FakeSnapd(installed={"publisher": {"username": "nordvpn"}})

        def request(method, target, body=None):
            if body and body.get("action") == "refresh":
                raise RuntimeError("snap has no updates available")
            return {"change": "7"}
        snapd.request = request
        self.assertEqual(quiet(depot_channels.snap_refresh, "nordvpn", 1000, snapd=snapd, catalogs=self.catalogs),
                         "unchanged")

    def test_remove_and_refresh(self):
        snapd = FakeSnapd(installed={"publisher": {"username": "nordvpn"}})
        self.assertEqual(quiet(depot_channels.snap_refresh, "nordvpn", 1000, snapd=snapd, catalogs=self.catalogs),
                         "updated")
        self.assertEqual(quiet(depot_channels.snap_remove, "nordvpn", 1000, snapd=snapd, catalogs=self.catalogs),
                         "removed")


class Run:
    def __init__(self, installed=False, fail=""):
        self.calls = []
        self.present = installed
        self.fail = fail

    def __call__(self, arguments, **_kwargs):
        self.calls.append(arguments)
        if arguments[:2] == [depot_channels.RPM, "-q"]:
            return mock.Mock(returncode=0 if self.present else 1, stdout="", stderr="")
        if self.fail and arguments[1] in ("install", "uninstall"):
            return mock.Mock(returncode=1, stdout="", stderr=self.fail)
        if arguments[1] == "install":
            self.present = True
        return mock.Mock(returncode=0, stdout="", stderr="")


class RepositoryHelper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repos = Path(self.tmp.name, "repos")
        self.keys = Path(self.tmp.name, "keys")
        self.catalogs = [catalog(MEGA)]
        patcher = mock.patch.object(depot_channels, "machine", return_value="x86_64")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def install(self, runner, **kwargs):
        options = dict(runner=runner, catalogs=self.catalogs, fetch_key=lambda url: b"key",
                       check_key=lambda content, pins: b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n",
                       apply=lambda replacement: True, repos=self.repos, keys=self.keys)
        options.update(kwargs)
        return quiet(depot_channels.repo_install, "mega", 1000, **options)

    def test_writes_a_signed_repository_and_adds_the_package(self):
        runner = Run()
        self.assertEqual(self.install(runner), "installed")
        text = (self.repos / "luma-depot-mega.repo").read_text()
        self.assertIn("gpgcheck=1", text)
        self.assertIn("gpgkey=file://", text)
        self.assertTrue((self.keys / "luma-depot-mega.asc").is_file())
        self.assertIn([depot_channels.RPM_OSTREE, "install", "--idempotent", "megasync"], runner.calls)

    def test_a_key_that_does_not_match_installs_nothing(self):
        runner = Run()

        def refuse(content, pins):
            raise Refused("The publisher’s signing key is not the one Luma has on record.")
        with self.assertRaises(Refused):
            self.install(runner, check_key=refuse)
        self.assertFalse(any(call[1] == "install" for call in runner.calls if call[0] == depot_channels.RPM_OSTREE))
        self.assertFalse((self.repos / "luma-depot-mega.repo").exists())

    def test_a_failed_install_forgets_the_repository(self):
        runner = Run(fail="error: Could not depsolve transaction")
        with self.assertRaises(RuntimeError):
            self.install(runner)
        self.assertFalse((self.repos / "luma-depot-mega.repo").exists())
        self.assertFalse((self.keys / "luma-depot-mega.asc").exists())

    def test_installed_already_is_unchanged(self):
        self.assertEqual(self.install(Run(installed=True)), "unchanged")

    def test_remove_uninstalls_and_forgets_the_repository(self):
        self.install(Run())
        runner = Run(installed=True)
        result = quiet(depot_channels.repo_remove, "mega", 1000, runner=runner, catalogs=self.catalogs,
                       apply=lambda replacement: True, repos=self.repos, keys=self.keys)
        self.assertEqual(result, "removed")
        self.assertIn([depot_channels.RPM_OSTREE, "uninstall", "--idempotent", "megasync"], runner.calls)
        self.assertFalse((self.repos / "luma-depot-mega.repo").exists())

    def test_expand_fills_in_the_architecture(self):
        self.assertIn("x86_64", depot_channels.expand("https://x/$basearch/"))


class Keys(unittest.TestCase):
    """pinned_key with a stand-in gpg that reports one primary fingerprint."""

    def fake_gpg(self, fingerprint):
        def run(arguments, **_kwargs):
            if "--import" in arguments:
                return mock.Mock(returncode=0, stdout=b"", stderr=b"")
            if "--list-keys" in arguments:
                return mock.Mock(returncode=0, stdout=f"pub:-:4096:1:X:1:::::::\nfpr:::::::::{fingerprint}:\n",
                                 stderr="")
            if "--export" in arguments:
                return mock.Mock(returncode=0, stdout=b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n", stderr=b"")
            raise AssertionError(arguments)
        return run

    def test_matching_key_is_armored(self):
        with mock.patch.object(depot_channels.subprocess, "run", self.fake_gpg("B01C811880480C854C73EC7E1A664B787094A482")):
            self.assertIn(b"BEGIN PGP", depot_channels.pinned_key(b"key", ["B01C811880480C854C73EC7E1A664B787094A482"]))

    def test_other_key_is_refused(self):
        with mock.patch.object(depot_channels.subprocess, "run", self.fake_gpg("0" * 40)):
            with self.assertRaises(Refused):
                depot_channels.pinned_key(b"key", ["B01C811880480C854C73EC7E1A664B787094A482"])


class Main(unittest.TestCase):
    def test_refusal_and_failure_exit_codes(self):
        @contextlib.contextmanager
        def lock():
            yield
        with mock.patch.dict(depot_channels.COMMANDS, {"snap-install": mock.Mock(side_effect=Refused("No."))}):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(depot_channels.main(["snap-install", "nordvpn"], lock=lock), 3)
            self.assertIn("refused: No.", err.getvalue())
        with mock.patch.dict(depot_channels.COMMANDS, {"repo-install": mock.Mock(side_effect=RuntimeError("Down"))}):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(depot_channels.main(["repo-install", "mega"], lock=lock), 1)
            self.assertIn("error: Down", err.getvalue())
        with mock.patch.dict(depot_channels.COMMANDS, {"snap-remove": mock.Mock(return_value="removed")}):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(depot_channels.main(["snap-remove", "nordvpn"], lock=lock), 0)
            self.assertIn("result: removed", out.getvalue())


if __name__ == "__main__":
    unittest.main()
