# SPDX-License-Identifier: MPL-2.0
"""Command mapping for dnf, yum and apt on Luma, without touching a system."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from luma_install_commands import apt, debian_names, depot_catalog, dnf, live, options, transaction  # noqa: E402

FIXTURES = HERE / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


#: Depot's real catalogue entry for Vivaldi (data/depot-catalog-4.json), used
#: instead of reading actual catalogue files from a system that isn't there.
VIVALDI = depot_catalog.Match("vivaldi", "Vivaldi", "flatpak", "com.vivaldi.Vivaldi", "flathub")


def patch_catalog(test, module, applications=(VIVALDI,)):
    """Answer ``module.depot_catalog.find`` from ``applications`` instead of disk,
    with the same exact/partial matching ``find`` itself does."""
    def fake_find(name, **_kwargs):
        target = name.strip().lower()
        exact = [app for app in applications if target in (app.id.lower(), app.name.lower())]
        if exact:
            return exact
        return [app for app in applications
                if depot_catalog._related(target, app.id.lower()) or depot_catalog._related(target, app.name.lower())]

    original = module.depot_catalog.find
    module.depot_catalog.find = fake_find
    test.addCleanup(lambda: setattr(module.depot_catalog, "find", original))


class FakeProcess:
    def __init__(self, text: str, code: int):
        self.stdout = io.StringIO(text)
        self._code = code

    def wait(self):
        return self._code


class World:
    """A pretend Luma computer: rpm-ostree status, the base image, dnf5 previews."""

    def __init__(self, *, previews=None, base=("firefox", "firefox-langpacks", "wget2-wget", "glibc"),
                 layered=(), removals=(), staged_base=None, rpm_ostree_codes=None, update_status=None):
        self.previews = previews or {}
        self.base = set(base)
        self.layered = list(layered)
        self.removals = list(removals)
        self.staged_base = staged_base
        self.rpm_ostree_codes = rpm_ostree_codes or {}
        self.update_status = update_status or {"state": "idle", "managed": True}
        self.calls: list[list[str]] = []
        self.execed: list[list[str]] = []
        self.out = io.StringIO()
        self.err = io.StringIO()

    # subprocess.run
    def run(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        text, code = "", 0
        if argv[:2] == [dnf.RPM_OSTREE, "status"]:
            booted = {"booted": True, "staged": False, "checksum": "base1", "base-checksum": "base1",
                      "requested-packages": self.layered, "packages": self.layered,
                      "requested-base-removals": self.removals, "base-removals": self.removals}
            deployments = [booted]
            if self.staged_base:
                deployments.insert(0, dict(booted, booted=False, staged=True, checksum="x",
                                           **{"base-checksum": self.staged_base}))
            text = json.dumps({"deployments": deployments, "transaction": None})
        elif argv[0] == "/usr/bin/rpm" and "--dbpath" in argv:
            text = "\n".join(sorted(self.base))
        elif argv[0] == "/usr/bin/rpm" and "-q" in argv:
            name = argv[-1]
            code = 0 if name in ("glibc",) else 1
            text = "Fedora Project|8.fc44|Fedora\n" if code == 0 else ""
        elif argv[:2] == [dnf.DNF5, "repo"]:
            text = "repo id  repo name\nfedora  Fedora\nupdates  Updates\n"
        elif argv[:2] == [dnf.LUMA_UPDATE, "status"]:
            text = json.dumps(self.update_status)
        elif argv[0] == "/usr/bin/logger":
            text = ""
        return subprocess.CompletedProcess(argv, code, text, "")

    # subprocess.Popen
    def popen(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] == dnf.DNF5:
            key = " ".join(item for item in argv[1:] if item != "--assumeno")
            text = self.previews.get(key, "")
            return FakeProcess(text, 1)
        if argv[0] == dnf.RPM_OSTREE:
            return FakeProcess("Resolving dependencies...done\n", self.rpm_ostree_codes.get(argv[1], 0))
        raise AssertionError(argv)

    def context(self, *, euid=0, image_based=True, answer=None):
        stdin = io.StringIO(answer or "")
        stdin.isatty = lambda: answer is not None
        return dnf.Context(run=self.run, popen=self.popen, out=self.out, err=self.err, stdin=stdin,
                           euid=euid, image_based=image_based,
                           exec_=lambda path, args: self.execed.append(list(args)))

    def rpm_ostree_calls(self):
        return [call[1:] for call in self.calls if call[0] == dnf.RPM_OSTREE and call[1] != "status"]


class NoLive(unittest.TestCase):
    def setUp(self):
        self._snapshot, self._refresh = live.snapshot, live.refresh
        live.snapshot = lambda root=None: {}
        live.refresh = lambda before, runner=None: {}

    def tearDown(self):
        live.snapshot, live.refresh = self._snapshot, self._refresh


class OptionsTest(unittest.TestCase):
    def test_options_before_and_after_command(self):
        parsed = options.parse(["-y", "--refresh", "install", "htop", "--repo", "fedora", "--setopt=a=b"])
        self.assertEqual(parsed.command, "install")
        self.assertEqual(parsed.args, ["htop"])
        self.assertTrue(parsed.assume_yes and parsed.refresh)
        self.assertEqual(parsed.repos, ["fedora"])
        self.assertEqual(parsed.setopts, ["a=b"])

    def test_aliases(self):
        for word, command in (("in", "install"), ("erase", "remove"), ("rm", "remove"), ("update", "upgrade"),
                              ("up", "upgrade"), ("check-update", "check-upgrade"), ("localinstall", "install")):
            self.assertEqual(options.parse([word, "x"]).command, command, word)

    def test_bundled_short_flags(self):
        parsed = options.parse(["install", "-yq", "htop"])
        self.assertTrue(parsed.assume_yes and parsed.quiet)
        self.assertEqual(parsed.args, ["htop"])


class TransactionTest(unittest.TestCase):
    def test_install_sections(self):
        preview = transaction.parse(fixture("install-htop.txt"), 1)
        self.assertTrue(preview.ok)
        self.assertEqual(preview.adds, ["htop"])
        self.assertEqual(preview.names("install-dep"), ["hwloc-libs"])
        self.assertFalse(preview.changes_installed)
        self.assertNotIn("Operation aborted", preview.text)

    def test_already_installed_is_nothing_to_do(self):
        preview = transaction.parse(fixture("install-firefox-installed.txt"), 1)
        self.assertTrue(preview.ok and preview.nothing_to_do)
        self.assertEqual(preview.already_installed, ["firefox-155.0-1.fc44.x86_64"])

    def test_no_match_fails(self):
        self.assertFalse(transaction.parse(fixture("install-nomatch.txt"), 1).ok)

    def test_group_adds_group_packages_not_dependencies(self):
        preview = transaction.parse(fixture("install-group.txt"), 1)
        self.assertEqual(preview.adds, ["diffstat", "git"])

    def test_allowerasing_removal(self):
        preview = transaction.parse(fixture("install-allowerasing.txt"), 1)
        self.assertEqual(preview.names("remove-dep"), ["wget2-wget"])
        self.assertEqual(preview.adds, ["wget1-wget"])


class DnfTest(NoLive):
    def test_read_only_commands_are_dnf5(self):
        for argv in (["search", "htop"], ["info", "htop"], ["list", "--installed"], ["provides", "/usr/bin/rg"],
                     ["repoquery", "htop"], ["check-update"], ["repolist"], ["makecache"],
                     ["copr", "enable", "atim/lazygit"], ["config-manager", "setopt", "x.enabled=1"], ["--version"]):
            world = World()
            dnf.main(argv, world.context())
            self.assertEqual(world.execed, [[dnf.DNF5, *argv]], argv)

    def test_outside_an_image_nothing_is_intercepted(self):
        world = World()
        dnf.main(["install", "-y", "htop"], world.context(image_based=False))
        self.assertEqual(world.execed, [[dnf.DNF5, "install", "-y", "htop"]])

    def test_transient_and_installroot_are_dnf5(self):
        for extra in (["--transient"], ["--installroot", "/tmp/root"], ["--downloadonly"]):
            world = World()
            dnf.main(["install", *extra, "htop"], world.context())
            self.assertTrue(world.execed, extra)

    def test_install_needs_root_like_dnf(self):
        world = World()
        self.assertEqual(dnf.main(["install", "htop"], world.context(euid=1000)), 1)
        self.assertIn("superuser privileges", world.err.getvalue())
        self.assertEqual(world.rpm_ostree_calls(), [])

    def test_install_layers_and_applies_live(self):
        world = World(previews={"install htop": fixture("install-htop.txt")})
        self.assertEqual(dnf.main(["install", "-y", "htop"], world.context()), 0)
        self.assertEqual(world.rpm_ostree_calls(),
                         [["install", "--idempotent", "--assumeyes", "htop"], ["apply-live"]])
        self.assertIn("ready to use", world.out.getvalue())
        self.assertIn("Installing:", world.out.getvalue())

    def test_install_asks_like_dnf_and_no_means_no(self):
        world = World(previews={"install htop": fixture("install-htop.txt")})
        self.assertEqual(dnf.main(["install", "htop"], world.context(answer="n\n")), 1)
        self.assertIn("Is this ok [y/N]: ", world.out.getvalue())
        self.assertIn("Operation aborted by the user.", world.out.getvalue())
        self.assertEqual(world.rpm_ostree_calls(), [])

    def test_install_yes_answer(self):
        world = World(previews={"install htop": fixture("install-htop.txt")})
        self.assertEqual(dnf.main(["install", "htop"], world.context(answer="y\n")), 0)
        self.assertEqual(world.rpm_ostree_calls()[0][0], "install")

    def test_assumeno(self):
        world = World(previews={"install htop": fixture("install-htop.txt")})
        self.assertEqual(dnf.main(["install", "--assumeno", "htop"], world.context()), 1)
        self.assertEqual(world.rpm_ostree_calls(), [])

    def test_already_installed_says_nothing_to_do(self):
        world = World(previews={"install firefox": fixture("install-firefox-installed.txt")})
        self.assertEqual(dnf.main(["install", "-y", "firefox"], world.context()), 0)
        self.assertIn('Package "firefox-155.0-1.fc44.x86_64" is already installed.', world.out.getvalue())
        self.assertIn("Nothing to do.", world.out.getvalue())
        self.assertEqual(world.rpm_ostree_calls(), [])

    def test_unknown_package_fails_like_dnf(self):
        world = World(previews={"install nosuchpkg": fixture("install-nomatch.txt")})
        self.assertEqual(dnf.main(["install", "-y", "nosuchpkg"], world.context()), 1)
        self.assertIn("No match for argument: nosuchpkg", world.out.getvalue())

    def test_install_vivaldi_stable_falls_back_to_depot_flatpak(self):
        # sudo dnf install vivaldi-stable: Fedora has no such package, but
        # Depot's catalogue knows Vivaldi as a Flatpak from Flathub.
        patch_catalog(self, dnf)
        world = World(previews={"install vivaldi-stable":
                                fixture("install-nomatch.txt").replace("nosuchpkg", "vivaldi-stable")})
        self.assertEqual(dnf.main(["install", "-y", "vivaldi-stable"], world.context()), 0)
        text = world.out.getvalue()
        self.assertIn("installing it from Flathub instead", text)
        self.assertIn("ready to use", text)
        self.assertIn("No restart needed.", text)
        self.assertIn(["/usr/bin/flatpak", "install", "--system", "--noninteractive", "-y", "flathub",
                      "com.vivaldi.Vivaldi"], world.calls)
        self.assertEqual(world.rpm_ostree_calls(), [])
        self.assertNotIn("No match for argument", text)

    def test_install_case_insensitive_name_resolves(self):
        # A package Fedora does have, typed in the wrong case, resolves the
        # way apt and dnf's own package names are conventionally lowercase.
        world = World(previews={"install HTOP": fixture("install-nomatch.txt").replace("nosuchpkg", "HTOP"),
                                "install htop": fixture("install-htop.txt")})
        self.assertEqual(dnf.main(["install", "-y", "HTOP"], world.context()), 0)
        self.assertIn("ready to use", world.out.getvalue())

    def test_bare_word_with_no_verb_matching_depot_catalogue_is_install(self):
        # dnf vivaldi (no "install": just the name)
        patch_catalog(self, dnf)
        world = World(previews={"install vivaldi":
                                fixture("install-nomatch.txt").replace("nosuchpkg", "vivaldi")})
        self.assertEqual(dnf.main(["vivaldi"], world.context()), 0)
        text = world.out.getvalue()
        self.assertIn("Installing vivaldi", text)
        self.assertIn("installing it from Flathub instead", text)
        self.assertIn(["/usr/bin/flatpak", "install", "--system", "--noninteractive", "-y", "flathub",
                      "com.vivaldi.Vivaldi"], world.calls)

    def test_removed_base_package_is_restored_live(self):
        world = World(removals=["firefox"])
        self.assertEqual(dnf.main(["install", "-y", "firefox"], world.context()), 0)
        self.assertEqual(world.rpm_ostree_calls(),
                         [["override", "reset", "firefox"], ["apply-live", "--allow-replacement"]])

    def test_conflict_with_base_package_is_staged_override(self):
        world = World(previews={"install --allowerasing wget1-wget": fixture("install-allowerasing.txt")})
        self.assertEqual(dnf.main(["install", "-y", "--allowerasing", "wget1-wget"], world.context()), 0)
        calls = world.rpm_ostree_calls()
        self.assertEqual(calls, [["override", "remove", "wget2-wget", "--install=wget1-wget"]])
        self.assertIn("after you restart", world.out.getvalue())

    def test_replacing_protected_package_is_refused(self):
        text = fixture("install-htop.txt").replace("Installing dependencies:\n hwloc-libs x86_64 0:2.14.0-1.fc44 updates      2.9 MiB",
                                                   "Upgrading:\n glibc      x86_64 0:2.44-1.fc44   updates      2.9 MiB")
        world = World(previews={"install newthing": text})
        self.assertEqual(dnf.main(["install", "-y", "newthing"], world.context()), 1)
        self.assertIn("part of Luma itself", world.err.getvalue())
        self.assertEqual(world.rpm_ostree_calls(), [])

    def test_install_while_update_staged_waits_for_restart(self):
        world = World(previews={"install htop": fixture("install-htop.txt")}, staged_base="base2")
        self.assertEqual(dnf.main(["install", "-y", "htop"], world.context()), 0)
        self.assertEqual(world.rpm_ostree_calls(), [["install", "--idempotent", "--assumeyes", "htop"]])
        self.assertIn("ready after you restart", world.out.getvalue())

    def test_nogpgcheck_refused(self):
        world = World()
        self.assertEqual(dnf.main(["install", "--nogpgcheck", "htop"], world.context()), 1)
        self.assertIn("signatures", world.err.getvalue())

    def test_remove_layered_live(self):
        text = fixture("install-htop.txt").replace("Installing:", "Removing:").replace(
            "Installing dependencies:", "Removing unused dependencies:")
        world = World(previews={"remove htop": text}, layered=["htop"])
        self.assertEqual(dnf.main(["remove", "-y", "htop"], world.context()), 0)
        self.assertEqual(world.rpm_ostree_calls(),
                         [["uninstall", "--idempotent", "htop"], ["apply-live", "--allow-replacement"]])

    def test_remove_base_package_explains_and_overrides(self):
        world = World(previews={"remove firefox": fixture("remove-firefox.txt")})
        self.assertEqual(dnf.main(["remove", "-y", "firefox"], world.context()), 0)
        self.assertIn("part of Luma's system image", world.out.getvalue())
        self.assertEqual(world.rpm_ostree_calls()[0], ["override", "remove", "firefox", "firefox-langpacks"])

    def test_remove_protected_refused(self):
        text = fixture("remove-firefox.txt").replace("firefox ", "glibc   ").replace("firefox-langpacks", "glibc-common")
        world = World(previews={"remove glibc": text}, base=("glibc", "glibc-common"))
        self.assertEqual(dnf.main(["remove", "-y", "glibc"], world.context()), 1)
        self.assertIn("part of Luma itself", world.err.getvalue())

    def test_erase_is_remove(self):
        text = fixture("install-htop.txt").replace("Installing:", "Removing:")
        world = World(previews={"remove htop": text}, layered=["htop"])
        self.assertEqual(dnf.main(["erase", "-y", "htop"], world.context()), 0)

    def test_upgrade_prompts_and_never_restarts(self):
        world = World(update_status={"state": "available", "managed": True, "available_version": "1.0.1",
                                     "available_summary": "Fixes.", "download_bytes": 1048576})
        self.assertEqual(dnf.main(["upgrade"], world.context(answer="n\n")), 1)
        self.assertIn("Luma 1.0.1 is available.", world.out.getvalue())
        self.assertNotIn([dnf.LUMA_UPDATE, "download"], world.calls)
        self.assertFalse(any("apply" in call or "reboot" in " ".join(call) for call in world.calls))

    def test_upgrade_yes_downloads_only(self):
        world = World(update_status={"state": "available", "managed": True, "available_version": "1.0.1"})
        self.assertEqual(dnf.main(["upgrade", "-y"], world.context()), 0)
        self.assertIn([dnf.LUMA_UPDATE, "download"], world.calls)
        self.assertNotIn([dnf.LUMA_UPDATE, "apply"], world.calls)
        self.assertIn("installed when you restart", world.out.getvalue())

    def test_live_install_staged_deployment_is_not_an_update(self):
        world = World(update_status={"state": "idle", "managed": True, "staged_version": "1.0.0"})
        self.assertEqual(dnf.main(["upgrade", "-y"], world.context()), 0)
        self.assertNotIn("is ready", world.out.getvalue())
        self.assertIn("Nothing to do.", world.out.getvalue())

    def test_update_alias_and_nothing_to_do(self):
        world = World()
        self.assertEqual(dnf.main(["update", "-y"], world.context()), 0)
        self.assertIn("Nothing to do.", world.out.getvalue())

    def test_yum_is_the_same(self):
        world = World(previews={"install htop": fixture("install-htop.txt")})
        self.assertEqual(dnf.main(["install", "-y", "htop"], world.context(), program="yum"), 0)

    def test_passthrough_escape_hatch(self):
        import os
        os.environ["LUMA_DNF_PASSTHROUGH"] = "1"
        try:
            world = World()
            dnf.main(["install", "-y", "htop"], world.context())
            self.assertEqual(world.execed, [[dnf.DNF5, "install", "-y", "htop"]])
        finally:
            del os.environ["LUMA_DNF_PASSTHROUGH"]

    def test_group_install_is_install_of_group(self):
        world = World(previews={"install @development-tools": fixture("install-group.txt")})
        self.assertEqual(dnf.main(["group", "install", "-y", "development-tools"], world.context()), 0)
        self.assertEqual(world.rpm_ostree_calls()[0], ["install", "--idempotent", "--assumeyes", "diffstat", "git"])


class OwnedTest(NoLive):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "owned.txt"
        path.write_text("# Luma builds\ngtk4\ngnome-shell\n")
        self._owned = dnf.LUMA_OWNED
        dnf.LUMA_OWNED = path
        dnf._OWNED_CACHE.clear()

    def tearDown(self):
        dnf.LUMA_OWNED = self._owned
        dnf._OWNED_CACHE.clear()
        self.tmp.cleanup()
        super().tearDown()

    def test_upgrade_of_owned_refused(self):
        world = World(base=("gnome-shell",))
        self.assertEqual(dnf.main(["upgrade", "-y", "gnome-shell"], world.context()), 1)
        self.assertIn("Luma's own build", world.err.getvalue())
        self.assertEqual(world.rpm_ostree_calls(), [])
        self.assertNotIn([dnf.LUMA_UPDATE, "check"], world.calls)

    def test_dependency_replacing_owned_refused(self):
        text = fixture("install-htop.txt").replace(
            "Installing dependencies:\n hwloc-libs x86_64 0:2.14.0-1.fc44 updates      2.9 MiB",
            "Upgrading:\n gtk4       x86_64 0:4.22.1-1.fc44  copr_x      2.9 MiB")
        world = World(previews={"install coolapp": text}, base=("gtk4",))
        self.assertEqual(dnf.main(["install", "-y", "coolapp"], world.context()), 1)
        self.assertIn("gtk4 is Luma's own build", world.err.getvalue())
        self.assertEqual(world.rpm_ostree_calls(), [])

    def test_install_of_installed_owned_changes_nothing(self):
        text = fixture("install-firefox-installed.txt").replace("firefox", "gtk4")
        world = World(previews={"install gtk4": text}, base=("gtk4",))
        self.assertEqual(dnf.main(["install", "-y", "gtk4"], world.context()), 0)
        self.assertIn("Luma's own build", world.out.getvalue())
        self.assertEqual(world.rpm_ostree_calls(), [])

    def test_reinstall_downgrade_swap_refused(self):
        for argv in (["reinstall", "-y", "gtk4"], ["downgrade", "-y", "gtk4"], ["swap", "-y", "gtk4", "gtk4-x"]):
            world = World(base=("gtk4",))
            self.assertEqual(dnf.main(argv, world.context()), 1, argv)

    def test_missing_list_protects_nothing_and_warns(self):
        dnf.LUMA_OWNED = Path(self.tmp.name) / "missing.txt"
        dnf._OWNED_CACHE.clear()
        world = World(previews={"install htop": fixture("install-htop.txt")})
        self.assertEqual(dnf.main(["install", "-y", "htop"], world.context()), 0)
        self.assertIn("not protected", world.err.getvalue())


class RpmWrapperTest(unittest.TestCase):
    def run_wrapper(self, *args):
        script = HERE.parent / "bin" / "rpm"
        text = script.read_text()
        with tempfile.TemporaryDirectory() as tmp:
            booted = Path(tmp) / "ostree-booted"
            booted.write_text("")
            log = Path(tmp) / "log"
            fake = Path(tmp) / "fake"
            fake.write_text(f"#!/bin/sh\necho \"$0 $*\" >> {log}\n")
            fake.chmod(0o755)
            body = (text.replace("/run/ostree-booted", str(booted)).replace("/usr/bin/rpm", str(fake) + "-rpm")
                    .replace("/usr/libexec/luma-install-commands/dnf", str(fake) + "-dnf"))
            (Path(str(fake) + "-rpm")).write_text(fake.read_text()); Path(str(fake) + "-rpm").chmod(0o755)
            (Path(str(fake) + "-dnf")).write_text(fake.read_text()); Path(str(fake) + "-dnf").chmod(0o755)
            wrapper = Path(tmp) / "rpm"
            wrapper.write_text(body)
            wrapper.chmod(0o755)
            result = subprocess.run([str(wrapper), *args], capture_output=True, text=True)
            return result, (log.read_text() if log.exists() else "")

    def test_install_goes_to_dnf(self):
        for flags in (["-ivh"], ["-Uvh"], ["-i"], ["--install"], ["-U"]):
            result, log = self.run_wrapper(*flags, "./foo-1.0-1.x86_64.rpm")
            self.assertIn("-dnf install -y ./foo-1.0-1.x86_64.rpm", log, flags)
            self.assertIn("Luma installs package files", result.stderr)

    def test_queries_are_rpm(self):
        for args in (["-qa"], ["-qi", "htop"], ["-qpl", "x.rpm"], ["-V", "htop"], ["--root", "/mnt", "-i", "x.rpm"],
                     ["-e", "htop"], ["--eval", "%{fedora}"], ["-i", "--test", "x.rpm"]):
            result, log = self.run_wrapper(*args)
            self.assertIn("-rpm " + " ".join(args), log, args)

    def test_unsupported_options_explain(self):
        result, log = self.run_wrapper("-ivh", "--nodeps", "x.rpm")
        self.assertEqual(result.returncode, 1)
        self.assertIn("/opt or /usr/local", result.stderr)
        self.assertEqual(log, "")


class DepotCatalogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "depot-catalog-4.json"
        self.path.write_text(json.dumps({"applications": [
            {"id": "vivaldi", "name": "Vivaldi", "backend": "flatpak", "source_id": "com.vivaldi.Vivaldi",
             "repository": "flathub", "architectures": ["x86_64", "aarch64"]},
            {"id": "google-chrome", "name": "Google Chrome", "backend": "rpm", "source_id": "google-chrome-stable",
             "repository": "google-chrome"},
            {"id": "expressvpn", "name": "ExpressVPN", "backend": "download", "source_id": "expressvpn"},
        ]}))

    def test_exact_id_is_case_insensitive(self):
        self.assertEqual([m.source_id for m in depot_catalog.find("VIVALDI", paths=(self.path,))],
                         ["com.vivaldi.Vivaldi"])

    def test_vendor_suffix_matches_the_base_id(self):
        self.assertEqual([m.id for m in depot_catalog.find("vivaldi-stable", paths=(self.path,))], ["vivaldi"])

    def test_unrelated_name_does_not_match(self):
        self.assertEqual(depot_catalog.find("chromium", paths=(self.path,)), [])

    def test_download_only_backend_is_not_offered(self):
        self.assertEqual(depot_catalog.find("expressvpn", paths=(self.path,)), [])

    def test_rpm_backend_is_returned_for_the_caller_to_check_dnf(self):
        self.assertEqual([m.source_id for m in depot_catalog.find("google-chrome", paths=(self.path,))],
                         ["google-chrome-stable"])

    def test_missing_catalogue_is_not_an_error(self):
        self.assertEqual(depot_catalog.find("vivaldi", paths=(Path("/no/such/file.json"),)), [])


class DebianNamesTest(unittest.TestCase):
    def test_known_exceptions(self):
        self.assertEqual(debian_names.candidates("build-essential"), ("@development-tools", "gcc-c++"))
        self.assertEqual(debian_names.candidates("libssl-dev"), ("openssl-devel",))
        self.assertEqual(debian_names.candidates("openjdk-21-jdk"), ("java-21-openjdk-devel",))
        self.assertEqual(debian_names.candidates("linux-headers-6.8.0-45-generic"), ("kernel-devel",))

    def test_same_names_come_first(self):
        self.assertEqual(debian_names.candidates("python3-pip")[0], "python3-pip")
        self.assertEqual(debian_names.candidates("htop"), ("htop",))

    def test_dev_pattern(self):
        self.assertEqual(debian_names.candidates("libxml2-dev")[:3], ("libxml2-dev", "libxml2-devel", "xml2-devel"))
        self.assertIn("libfoo-devel", debian_names.candidates("libfoo1.2-dev"))


class AptTest(unittest.TestCase):
    def runner_for(self, available=(), provides=None):
        calls = []

        def runner(argv, **kwargs):
            argv = list(argv)
            calls.append(argv)
            out = ""
            if argv[:2] == [apt.DNF5, "repoquery"]:
                name = argv[-1]
                if "--whatprovides" in argv:
                    out = "\n".join((provides or {}).get(name, []))
                elif name in available:
                    out = name
            elif argv[:2] == [apt.DNF5, "group"]:
                out = "Id: development-tools" if argv[-1] == "development-tools" else ""
            return subprocess.CompletedProcess(argv, 0, out, "")
        return runner, calls

    def capture(self, argv, **kwargs):
        stderr = io.StringIO()
        old = sys.stderr
        sys.stderr = stderr
        try:
            code = apt.main(argv, **kwargs)
        finally:
            sys.stderr = old
        return code, stderr.getvalue()

    def test_install_maps_and_announces(self):
        runner, calls = self.runner_for(available=("firefox",))
        code, text = self.capture(["install", "-y", "firefox"], runner=runner, euid=0)
        self.assertEqual(code, 0)
        self.assertIn("Luma uses dnf; running: sudo dnf install -y firefox", text)
        self.assertIn([apt.DNF, "install", "-y", "firefox"], calls)

    def test_dev_package_renamed(self):
        runner, calls = self.runner_for(available=("libxml2-devel",))
        code, text = self.capture(["install", "libxml2-dev"], runner=runner, euid=0)
        self.assertIn("libxml2-dev is called libxml2-devel on Fedora.", text)
        self.assertIn([apt.DNF, "install", "libxml2-devel"], calls)

    def test_build_essential(self):
        runner, calls = self.runner_for()
        self.capture(["install", "-y", "build-essential"], runner=runner, euid=0)
        self.assertIn([apt.DNF, "install", "-y", "@development-tools", "gcc-c++"], calls)

    def test_unresolved_names_suggest(self):
        runner, calls = self.runner_for(provides={"libfoo-bar": ["foo-bar-libs"]})
        code, text = self.capture(["install", "libfoo-bar"], runner=runner, euid=0)
        self.assertEqual(code, apt.APT_FAILURE)
        self.assertIn("foo-bar-libs", text)

    def test_update_is_metadata_only(self):
        runner, calls = self.runner_for()
        self.capture(["update"], runner=runner, euid=0)
        self.assertIn([apt.DNF, "makecache", "--refresh"], calls)

    def test_upgrade_goes_to_dnf_upgrade(self):
        runner, calls = self.runner_for()
        self.capture(["upgrade", "-y"], runner=runner, euid=0)
        self.assertIn([apt.DNF, "upgrade", "-y"], calls)

    def test_remove_and_purge(self):
        for verb in ("remove", "purge"):
            runner, calls = self.runner_for()
            self.capture([verb, "-y", "htop"], runner=runner, euid=0)
            self.assertIn([apt.DNF, "remove", "-y", "htop"], calls)

    def test_search_show_list(self):
        for argv, expected in ((["search", "htop"], ["search", "htop"]), (["show", "htop"], ["info", "htop"]),
                               (["list", "--installed"], ["list", "--installed"]),
                               (["list", "--upgradable"], ["check-upgrade"])):
            runner, calls = self.runner_for()
            self.capture(argv, runner=runner, euid=1000)
            self.assertIn([apt.DNF, *expected], calls)

    def test_failure_exit_code_is_100(self):
        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, "", "")
        code, _ = self.capture(["search", "x"], runner=runner, euid=0)
        self.assertEqual(code, 100)

    def test_deb_goes_to_depot_installer(self):
        runner, calls = self.runner_for()
        with tempfile.NamedTemporaryFile(suffix=".deb") as deb:
            code, text = self.capture(["install", deb.name], runner=runner, euid=1000)
        self.assertEqual(code, 0)
        self.assertEqual(calls[0][0], apt.INSTALLER)
        self.assertFalse(any(call[0] == apt.DNF for call in calls))

    def test_get_typo_is_understood_as_install(self):
        # sudo apt get Vivaldi
        patch_catalog(self, apt)
        runner, calls = self.runner_for()
        code, text = self.capture(["get", "Vivaldi"], runner=runner, euid=0)
        self.assertEqual(code, 0)
        self.assertIn('apt has no "get" command; running apt install instead.', text)
        self.assertIn("installing it from Flathub instead", text)
        self.assertIn("ready to use", text)
        self.assertIn(["/usr/bin/flatpak", "install", "--system", "--noninteractive", "-y", "flathub",
                      "com.vivaldi.Vivaldi"], calls)
        self.assertFalse(any(call[0] == apt.DNF for call in calls))

    def test_install_vivaldi_resolves_from_depot_catalogue(self):
        # sudo apt install vivaldi
        patch_catalog(self, apt)
        runner, calls = self.runner_for()
        code, text = self.capture(["install", "-y", "vivaldi"], runner=runner, euid=0)
        self.assertEqual(code, 0)
        self.assertIn("installing it from Flathub instead", text)
        self.assertIn("No restart needed.", text)
        self.assertNotIn("Unable to locate package", text)

    def test_install_vivaldi_stable_matches_the_catalogue_id(self):
        # apt install vivaldi-stable (also covers running without sudo: the
        # name still resolves and Depot's own install is attempted)
        patch_catalog(self, apt)
        runner, calls = self.runner_for()
        code, text = self.capture(["install", "vivaldi-stable"], runner=runner, euid=1000)
        self.assertEqual(code, 0)
        self.assertIn("com.vivaldi.Vivaldi", text)

    def test_apt_get_install_vivaldi(self):
        # sudo apt-get install vivaldi (apt-get is the same program as apt)
        patch_catalog(self, apt)
        runner, calls = self.runner_for()
        code, text = self.capture(["install", "-y", "vivaldi"], runner=runner, euid=0, program="apt-get")
        self.assertEqual(code, 0)
        self.assertIn("installing it from Flathub instead", text)

    def test_ambiguous_catalogue_name_offers_a_numbered_choice(self):
        other = depot_catalog.Match("vivaldi-snapshot", "Vivaldi Snapshot", "flatpak",
                                    "com.vivaldi.Vivaldi.Snapshot", "flathub")
        original = apt.depot_catalog.find
        apt.depot_catalog.find = lambda name, **_: [VIVALDI, other]
        self.addCleanup(lambda: setattr(apt.depot_catalog, "find", original))
        runner, calls = self.runner_for()
        code, text = self.capture(["install", "-y", "vivaldi"], runner=runner, euid=0)
        self.assertEqual(code, apt.APT_FAILURE)
        self.assertIn("1)", text)
        self.assertIn("2)", text)
        self.assertFalse(any("flatpak" in call[0] for call in calls))

    def test_unknown_name_not_in_catalogue_still_fails_plainly(self):
        patch_catalog(self, apt, applications=())
        runner, calls = self.runner_for()
        code, text = self.capture(["install", "-y", "nosuchthing"], runner=runner, euid=0)
        self.assertEqual(code, apt.APT_FAILURE)
        self.assertIn("Unable to locate package nosuchthing", text)

    def test_apt_get_with_no_verb_at_all_is_understood_as_install(self):
        # sudo apt-get Vivaldi (no "install", no "get": just the name)
        patch_catalog(self, apt)
        runner, calls = self.runner_for()
        code, text = self.capture(["Vivaldi"], runner=runner, euid=0, program="apt-get")
        self.assertEqual(code, 0)
        self.assertIn("Installing Vivaldi", text)
        self.assertIn("installing it from Flathub instead", text)
        self.assertIn(["/usr/bin/flatpak", "install", "--system", "--noninteractive", "-y", "flathub",
                      "com.vivaldi.Vivaldi"], calls)

    def test_apt_with_no_verb_at_all_is_understood_as_install(self):
        # apt Vivaldi
        patch_catalog(self, apt)
        runner, calls = self.runner_for()
        code, text = self.capture(["Vivaldi"], runner=runner, euid=0)
        self.assertEqual(code, 0)
        self.assertIn("Installing Vivaldi", text)

    def test_unknown_bare_word_suggests_install_and_depot_no_dnf_jargon(self):
        # sudo apt-get foo-nonexistent
        patch_catalog(self, apt, applications=())
        runner, calls = self.runner_for()
        code, text = self.capture(["foo-nonexistent"], runner=runner, euid=0, program="apt-get")
        self.assertEqual(code, apt.APT_FAILURE)
        self.assertIn("sudo apt-get install foo-nonexistent", text)
        self.assertIn("Depot", text)
        self.assertNotIn("Luma uses dnf", text)
        self.assertNotIn("dnf --help", text)


class LiveTest(unittest.TestCase):
    def test_new_launcher_is_exported_and_removed_one_hidden(self):
        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as exports_dir:
            root, exports = Path(root_dir), Path(exports_dir)
            apps = root / "usr/share/applications"
            apps.mkdir(parents=True)
            (apps / "old.desktop").write_text("[Desktop Entry]\nName=Old\n")
            before = live.snapshot(root)
            (apps / "old.desktop").unlink()
            (apps / "org.gnome.Sudoku.desktop").write_text("[Desktop Entry]\nName=Sudoku\n")
            icon = root / "usr/share/icons/hicolor/scalable/apps/org.gnome.Sudoku.svg"
            icon.parent.mkdir(parents=True)
            icon.write_text("<svg/>")
            changes = live.diff(before, live.snapshot(root))
            live.export(changes, root, exports)
            self.assertIn("Name=Sudoku", (exports / "applications/org.gnome.Sudoku.desktop").read_text())
            self.assertIn("Hidden=true", (exports / "applications/old.desktop").read_text())
            self.assertTrue((exports / "icons/hicolor/scalable/apps/org.gnome.Sudoku.svg").exists())


if __name__ == "__main__":
    unittest.main()
