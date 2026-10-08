# SPDX-License-Identifier: Apache-2.0
"""An update whose base now ships a package this computer had added.

Found on a Dell installed from nightly 20260915.9: Viola had been layered from a
file by the first-boot app install; a later nightly shipped Viola in its base and
rpm-ostree refused every update with "cannot install both ... conflicting
requests". The added copy has to go with the update, and only that copy."""

import importlib.util
import json
import unittest

import fakes
from fakes import commit, graph_doc, release
from luma_update.engine import added_packages_in_conflict, added_packages_the_base_provides
from luma_update.errors import TransactionError

VIOLA_LOCAL = "viola-browser-stable-151.0.7922.72-1.x86_64"


def both(base_nevra, added_nevra, added_from="@commandline", system_first=True):
    pair = (f"{base_nevra} from @System and {added_nevra} from {added_from}" if system_first
            else f"{added_nevra} from {added_from} and {base_nevra} from @System")
    return TransactionError("Could not depsolve transaction; 1 problem detected:\n Problem: conflicting requests\n"
                            f"  - cannot install both {pair}")


class BaseShipsAddedPackage(fakes.FakeRpmOstree):
    """rpm-ostree whose target base ships ``provided`` (name -> base NEVRA)."""

    def __init__(self, provided, *, local=(), repo=()):
        super().__init__()
        self.provided = dict(provided)
        self.list[0].requested_local_packages = tuple(local)
        self.list[0].requested_packages = tuple(repo)
        self.list[0].layered = bool(local or repo)

    def update_deployment(self, *, revision, refspec, allow_downgrade, uninstall=(), **kwargs):
        for request in self.list[0].requested_local_packages + self.list[0].requested_packages:
            name = request.rsplit("-", 2)[0] if request.count("-") >= 2 and request[-1] != "-" \
                and request.split("-")[-2][:1].isdigit() else request
            if name in self.provided and request not in uninstall:
                self.calls.append(("update", revision, refspec, allow_downgrade)
                                  + (("uninstall",) + tuple(uninstall) if uninstall else ()))
                if request == self.provided[name]:
                    # rpm-ostree's own refusal for an identical NEVRA (no depsolve at all).
                    raise TransactionError(f"Package '{request}' is already in the base")
                raise both(self.provided[name], request if request != name else f"{name}-0.9-1.x86_64",
                           "@commandline" if request in self.list[0].requested_local_packages else "fedora")
        super().update_deployment(revision=revision, refspec=refspec, allow_downgrade=allow_downgrade,
                                  uninstall=uninstall, **kwargs)


class AddedPackageTheBaseShips(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()

    def tearDown(self):
        self.rig.close()

    def stage(self, backend):
        self.rig.backend = backend
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        engine = self.rig.engine()
        with self.assertLogs("luma-update", level="INFO") as logs:
            engine.automatic()
        return engine, "\n".join(logs.output)

    def test_the_dell_case_stages_without_the_added_copy_and_says_so(self):
        backend = BaseShipsAddedPackage({"viola-browser-stable": "viola-browser-stable-151.0.7922.72-2.x86_64"},
                                        local=(VIOLA_LOCAL, "htop-3.3.0-1.fc44.x86_64"), repo=("git",))
        engine, logs = self.stage(backend)
        self.assertEqual(backend.calls, [("update", commit(2), None, False),
                                         ("update", commit(2), None, False, "uninstall", VIOLA_LOCAL)])
        status = engine.status()
        self.assertEqual((status.state, status.staged_commit, status.last_error), ("staged", commit(2), ""))
        self.assertEqual(status.removed_packages, [VIOLA_LOCAL])
        self.assertIn(f"includes {VIOLA_LOCAL}", logs)
        state = json.loads(self.rig.paths.state_file.read_text())
        self.assertEqual(state["pending"]["removed_packages"], [VIOLA_LOCAL])
        self.assertEqual(json.loads(engine.status().to_json())["removed_packages"], [VIOLA_LOCAL])

    def test_a_repository_request_the_base_ships_goes_by_its_request(self):
        backend = BaseShipsAddedPackage({"htop": "htop-3.4.0-1.fc44.x86_64"}, repo=("htop", "git"))
        engine, _ = self.stage(backend)
        self.assertEqual(backend.calls[-1], ("update", commit(2), None, False, "uninstall", "htop"))
        self.assertEqual(engine.status().removed_packages, ["htop"])

    def test_two_packages_are_removed_one_conflict_at_a_time(self):
        backend = BaseShipsAddedPackage({"viola-browser-stable": "viola-browser-stable-151.0.7922.72-2.x86_64",
                                         "htop": "htop-3.4.0-1.fc44.x86_64"},
                                        local=(VIOLA_LOCAL, "htop-3.3.0-1.fc44.x86_64"))
        engine, _ = self.stage(backend)
        self.assertEqual(engine.status().state, "staged")
        self.assertEqual(sorted(engine.status().removed_packages), sorted([VIOLA_LOCAL, "htop-3.3.0-1.fc44.x86_64"]))
        self.assertEqual(len(backend.calls), 3)

    def test_a_conflict_with_nothing_added_here_is_not_touched(self):
        backend = fakes.FakeRpmOstree()
        backend.list[0].requested_local_packages = ("htop-3.3.0-1.fc44.x86_64",)
        backend.fail_with = both("viola-browser-stable-151.0.7922.72-2.x86_64", VIOLA_LOCAL)
        self.rig.backend = backend
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        engine = self.rig.engine()
        engine.automatic()
        self.assertEqual(backend.calls, [("update", commit(2), None, False)])
        self.assertEqual((engine.status().last_error_class, engine.status().staged_commit), ("transaction", ""))
        self.assertEqual(engine.status().removed_packages, [])

    def test_a_next_update_with_nothing_to_remove_clears_the_list(self):
        backend = BaseShipsAddedPackage({"viola-browser-stable": "viola-browser-stable-151.0.7922.72-2.x86_64"},
                                        local=(VIOLA_LOCAL,))
        engine, _ = self.stage(backend)
        self.assertEqual(engine.status().removed_packages, [VIOLA_LOCAL])
        backend.list = [d for d in backend.list if not d.staged]
        backend.list[0].requested_local_packages = ()
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2), release("1.0.2", 3)]))
        engine.download(user_initiated=True)
        self.assertEqual(engine.status().removed_packages, [])


class NicksThinkPad(AddedPackageTheBaseShips):
    """Found on Nick's ThinkPad, 2026-09-28: nightly 20260926.2 was offered and
    "couldn't be prepared: Package 'luma-keyring-0.1.0-1.luma.1.fc44.noarch' is
    already in the base". Ten of his 15 added packages were now in the base,
    some identical, and one (luma-session) newer than the base's copy."""

    KEYRING = "luma-keyring-0.1.0-1.luma.1.fc44.noarch"
    SESSION = "luma-session-0.1.0-1.luma.17.fc44.x86_64"
    SOUNDS = "luma-sound-theme-0.1.0-2.luma.1.fc44.noarch"

    def test_a_newer_added_copy_holds_the_update_and_says_why(self):
        # rpm-ostree cannot keep a layer whose name the base carries (not even with
        # "override remove": "Base packages not marked to be removed"), so removing
        # luma-session .17 for the base's .16 would downgrade what Nick runs: wait.
        backend = BaseShipsAddedPackage({
            "luma-keyring": self.KEYRING,                                   # same NEVRA: "already in the base"
            "luma-session": "luma-session-0.1.0-1.luma.16.fc44.x86_64",     # base older than the added .17
            "luma-sound-theme": "luma-sound-theme-0.1.0-2.luma.2.fc44.noarch",  # base newer
        }, local=(self.KEYRING, self.SESSION, self.SOUNDS, "sfizz-1.2.3-1.luma.1.fc44.x86_64"))
        engine, logs = self.stage(backend)
        status = engine.status()
        self.assertEqual((status.staged_commit, status.last_error_class), ("", "added-package-newer"))
        self.assertIn("newer copy", status.last_error)
        self.assertEqual(status.kept_packages, [self.SESSION])
        for call in backend.calls:
            self.assertNotIn(self.SESSION, call)            # never asked to remove the newer copy
            self.assertNotIn("override-remove", call)
        self.assertIn("keeping the added copy", logs)
        self.assertEqual(json.loads(engine.status().to_json())["kept_packages"], [self.SESSION])

        # Nick removes his copy (or a release ships .17): the next update stages and clears the note.
        backend.list[0].requested_local_packages = (self.KEYRING, self.SOUNDS, "sfizz-1.2.3-1.luma.1.fc44.x86_64")
        engine.download(user_initiated=True)
        status = engine.status()
        self.assertEqual((status.state, status.staged_commit), ("staged", commit(2)))
        self.assertEqual(sorted(status.removed_packages), sorted([self.KEYRING, self.SOUNDS]))
        self.assertEqual(status.kept_packages, [])

    def test_an_identical_copy_alone_is_dropped(self):
        backend = BaseShipsAddedPackage({"luma-keyring": self.KEYRING}, local=(self.KEYRING,))
        engine, _ = self.stage(backend)
        self.assertEqual(engine.status().state, "staged")
        self.assertEqual(backend.calls[-1], ("update", commit(2), None, False, "uninstall", self.KEYRING))
        self.assertEqual(engine.status().kept_packages, [])


class ReadingTheConflict(unittest.TestCase):
    def deployment(self, local=(), repo=()):
        d = fakes.FakeDeployment(commit(1), "1.0.0", "luma:luma/1/x86_64/stable", booted=True)
        d.requested_local_packages, d.requested_packages = tuple(local), tuple(repo)
        return d

    def test_either_order_epochs_and_a_digest_prefix(self):
        error = both("foo-2:1.0-2.x86_64", "foo-2:1.0-1.x86_64", system_first=False)
        local = ("a" * 64 + ":foo-2:1.0-1.x86_64",)
        self.assertEqual(added_packages_the_base_provides(error, [self.deployment(local)]), ["foo-2:1.0-1.x86_64"])

    def test_two_added_packages_conflicting_with_each_other_is_not_this(self):
        error = TransactionError("cannot install both foo-1-1.x86_64 from @commandline and foo-1-2.x86_64 from fedora")
        self.assertEqual(added_packages_the_base_provides(error, [self.deployment(("foo-1-1.x86_64",))]), [])

    def test_a_name_that_only_starts_the_same_is_not_matched(self):
        error = both("viola-browser-stable-151-2.x86_64", "viola-browser-stable-151-1.x86_64")
        self.assertEqual(added_packages_the_base_provides(
            error, [self.deployment(("viola-browser-stable-extras-1-1.x86_64",), ("viola-browser",))]), [])

    def test_already_in_the_base_is_a_drop(self):
        error = TransactionError("error: Package 'luma-keyring-0.1.0-1.luma.1.fc44.noarch' is already in the base")
        self.assertEqual(added_packages_in_conflict(error, [self.deployment(("luma-keyring-0.1.0-1.luma.1.fc44.noarch",))]),
                         (["luma-keyring-0.1.0-1.luma.1.fc44.noarch"], []))

    def test_a_newer_added_copy_is_kept_and_an_older_one_dropped(self):
        error = TransactionError("cannot install both foo-1.0-2.x86_64 from @System and foo-1.0-3.x86_64 from @commandline\n"
                                 "  - cannot install both bar-2.0-1.x86_64 from @System and bar-1.0-1.x86_64 from @commandline")
        drop, keep = added_packages_in_conflict(error, [self.deployment(("foo-1.0-3.x86_64", "bar-1.0-1.x86_64"))])
        self.assertEqual((drop, keep), (["bar-1.0-1.x86_64"], [("foo-1.0-3.x86_64", "foo")]))

    def test_tilde_releases_order_like_rpm(self):
        error = both("luma-developer-platform-0.1.0-1.luma.78~preview.20260923.1.fc44.x86_64",
                     "luma-developer-platform-0.1.0-1.luma.78~lumaui.20260926.1.fc44.x86_64")
        drop, keep = added_packages_in_conflict(error, [self.deployment(
            ("luma-developer-platform-0.1.0-1.luma.78~lumaui.20260926.1.fc44.x86_64",))])
        self.assertEqual(keep, [])  # "lumaui" < "preview" after the tilde: the added copy is OLDER

    def test_a_repository_request_is_always_dropped(self):
        error = both("htop-3.4.0-1.fc44.x86_64", "htop-9.9.9-1.x86_64", "fedora")
        self.assertEqual(added_packages_in_conflict(error, [self.deployment((), ("htop",))]), (["htop"], []))

    def test_other_errors_find_nothing(self):
        self.assertEqual(added_packages_the_base_provides(TransactionError("Curl error (6)"),
                                                          [self.deployment((VIOLA_LOCAL,))]), [])

    @unittest.skipUnless(importlib.util.find_spec("gi"), "PyGObject is not installed")
    def test_rpm_ostree_deployment_fields_are_read(self):
        from luma_update.rpmostree import parse_deployment
        d = parse_deployment({"id": "x", "checksum": commit(1), "requested-local-packages": [VIOLA_LOCAL],
                              "requested-packages": ["git"]})
        self.assertEqual((d.requested_local_packages, d.requested_packages, d.layered), ((VIOLA_LOCAL,), ("git",), True))


if __name__ == "__main__":
    unittest.main()
