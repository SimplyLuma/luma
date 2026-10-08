# SPDX-License-Identifier: Apache-2.0
import json
import os
import stat
import unittest

import fakes
from fakes import NOW, commit, graph_doc, release
from luma_update import minisign
from luma_update.engine import Busy, Unmanaged, UpdateError
from luma_update.status import JSON_SCHEMA_VERSION, STATES
from luma_update.errors import TransactionError


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        self.engine = self.rig.engine()

    def tearDown(self):
        self.rig.close()

    def state(self):
        return json.loads(self.rig.paths.state_file.read_text())

    def events(self):
        return [p for (_m, url, p, _h) in self.rig.http.posts if url.endswith("/events") and "result" in (p or {})]


class Checking(EngineCase):
    def test_check_finds_update_and_publishes_status(self):
        decision = self.engine.check()
        self.assertEqual(str(decision.release.version), "1.0.1")
        status = self.engine.status()
        self.assertEqual((status.state, status.available_version, status.channel, status.booted_version),
                         ("available", "1.0.1", "stable", "1.0.0"))
        self.assertEqual(status.notes_url, "https://simplyluma.com/releases/1.0.1")
        published = json.loads(self.rig.paths.status_file.read_text())
        self.assertEqual(published["state"], "available")
        self.assertEqual(published["schema_version"], JSON_SCHEMA_VERSION)
        self.assertEqual(self.state()["last_check"], int(NOW))

    def test_bad_signature_is_an_error_and_nothing_is_offered(self):
        url = self.rig.settings.graph_url.format(channel="stable")
        doc = graph_doc([release("1.0.0", 1), release("9.9.9", 99)])
        data = json.dumps(doc).encode()
        _, signature = minisign.sign_for_tests(bytes(32), fakes.KEY_ID, data, "evil")
        self.rig.http.files[url] = data
        self.rig.http.files[url + ".minisig"] = signature.encode()
        self.assertIsNone(self.engine.check())
        status = self.engine.status()
        self.assertEqual((status.state, status.last_error_class, status.available_version), ("error", "signature", ""))

    def test_replayed_older_graph_is_refused(self):
        self.engine.check()
        self.rig.publish(graph_doc([release("1.0.0", 1)], generated=NOW - 7200))
        self.assertIsNone(self.engine.check())
        self.assertEqual(self.engine.status().last_error_class, "stale-graph")

    def test_network_failure(self):
        self.rig.http.offline = True
        self.assertIsNone(self.engine.check())
        self.assertEqual(self.engine.status().last_error_class, "network")

    def test_unmanaged_origin_is_never_touched(self):
        self.rig.backend = fakes.FakeRpmOstree(origin="fedora:fedora/44/x86_64/silverblue")
        engine = self.rig.engine()
        self.assertIsNone(engine.check())
        self.assertEqual(engine.status().last_error_class, "unmanaged")
        self.assertFalse(engine.download(user_initiated=True))
        self.assertEqual(self.rig.backend.calls, [])

    def test_automatic_spacing(self):
        self.engine.automatic()
        posts = len(self.rig.http.posts)
        calls = len(self.rig.backend.calls)
        self.rig.now += 600
        self.engine.automatic()
        self.assertEqual(len(self.rig.backend.calls), calls)
        self.assertEqual(len(self.rig.http.posts), posts)


class Downloading(EngineCase):
    def test_automatic_download_stages_exact_commit_without_downgrade(self):
        self.engine.automatic()
        self.assertIn(("update", commit(2), None, False), self.rig.backend.calls)
        status = self.engine.status()
        self.assertEqual((status.state, status.staged_version, status.staged_commit), ("staged", "1.0.1", commit(2)))
        pending = self.state()["pending"]
        self.assertEqual((pending["from_version"], pending["to_version"], pending["kind"]), ("1.0.0", "1.0.1", "update"))
        self.assertEqual(self.events()[-1], {"channel": "stable", "from_version": "1.0.0", "to_version": "1.0.1",
                                             "arch": "x86_64", "result": "staged", "error_class": ""})

    def test_metered_waits_for_person(self):
        self.rig.probes.net.metered = True
        self.engine.automatic()
        self.assertEqual(self.rig.backend.calls, [])
        status = self.engine.status()
        self.assertEqual((status.state, status.metered), ("available", True))
        self.assertTrue(self.engine.download(user_initiated=True))
        self.assertEqual(self.engine.status().state, "staged")

    def test_unknown_metered_state_waits(self):
        self.rig.probes.net.metered = None
        self.engine.automatic()
        self.assertEqual(self.rig.backend.calls, [])
        self.assertTrue(self.engine.status().metered)

    def test_network_manager_not_answering_counts_as_metered(self):
        self.rig.probes.net = fakes.Network(available=False, metered=None, connectivity=0)
        self.engine.automatic()
        self.assertEqual(self.rig.backend.calls, [])
        status = self.engine.status()
        self.assertEqual((status.state, status.metered), ("available", True))
        self.assertTrue(self.engine.download(user_initiated=True))

    def test_upower_not_answering_waits_for_a_person(self):
        self.rig.probes.pwr = fakes.Power(available=False, on_battery=False, percentage=None)
        self.engine.automatic()
        self.assertEqual(self.rig.backend.calls, [])
        self.assertEqual(self.engine.status().state, "available")
        self.rig.probes.pwr = fakes.Power(available=True, on_battery=False, percentage=None)  # a desktop on AC
        self.rig.now += 7200
        self.engine.automatic()
        self.assertEqual(self.engine.status().state, "staged")

    def test_battery_policy(self):
        self.rig.probes.pwr.on_battery = True
        self.rig.probes.pwr.percentage = 30.0
        self.engine.automatic()
        self.assertEqual(self.rig.backend.calls, [])
        self.rig.probes.pwr.percentage = 31.0
        self.rig.now += 7200
        self.engine.automatic()
        self.assertEqual(self.engine.status().state, "staged")

    def test_refuses_while_another_transaction_runs(self):
        self.engine.check()
        self.rig.backend.active = ("Upgrade", ":1.9", "/")
        self.assertFalse(self.engine.download(user_initiated=True))
        self.assertEqual(self.rig.backend.calls, [])
        self.assertEqual(self.engine.status().last_error_class, "busy")

    def test_transaction_failure_reports_failed(self):
        self.engine.check()
        self.rig.backend.fail_with = TransactionError("error: No space left on device")
        self.assertFalse(self.engine.download(user_initiated=True))
        self.assertEqual(self.engine.status().last_error_class, "disk-space")
        self.assertEqual(self.state()["reports"][-1]["result"], "failed")
        self.assertEqual(self.state()["reports"][-1]["error_class"], "disk-space")

    def test_foreign_staged_deployment_is_not_replaced(self):
        self.rig.backend.list.insert(0, fakes.FakeDeployment(commit(77), "44.1", "private:luma/repair/x", staged=True))
        self.engine.check()
        self.assertFalse(self.engine.download(user_initiated=True))
        self.assertEqual(self.rig.backend.calls, [])

    def test_pulled_staged_release_is_removed_before_boot(self):
        self.engine.automatic()
        self.rig.now += 7200
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2, deadend=True)], generated=NOW + 3600))
        self.engine.check()
        self.assertIn(("cleanup",), self.rig.backend.calls)
        self.assertIsNone(self.state()["pending"])
        self.assertEqual(self.engine.status().state, "idle")

    def test_serialized_operations(self):
        self.engine._operation.acquire()
        with self.assertRaises(Busy):
            self.engine.check()
        self.engine._operation.release()

    def test_person_initiated_change_waits_for_running_check(self):
        import threading
        self.engine._operation.acquire()
        threading.Timer(0.2, self.engine._operation.release).start()
        self.engine.set_channel("stable")  # does not raise Busy
        self.assertEqual(self.engine.status().channel, "stable")

    @unittest.skipUnless(hasattr(__import__("time"), "CLOCK_BOOTTIME"), "Linux boot clock")
    def test_boot_identity_without_proc(self):
        from unittest import mock
        from luma_update import engine as enginemod
        with mock.patch.object(enginemod.Path, "read_text", side_effect=PermissionError):
            first = enginemod._boot_id()
            self.assertTrue(first.startswith("boot-time:"))
            self.assertEqual(first, enginemod._boot_id())


class AfterRestart(EngineCase):
    def stage(self):
        self.engine.automatic()
        self.assertEqual(self.engine.status().state, "staged")

    def test_booted_after_green(self):
        self.stage()
        self.rig.backend.reboot_into(commit(2))
        engine = self.rig.engine(boot="boot-2")
        self.assertEqual(engine.reconcile_boot(None), "pending")
        self.assertEqual(engine.reconcile_boot("green"), "booted")
        self.assertIsNone(self.state()["pending"])
        self.assertEqual(self.state()["reports"][-1]["result"], "booted")

    def test_booted_without_greenboot(self):
        self.stage()
        self.rig.backend.reboot_into(commit(2))
        engine = self.rig.engine(boot="boot-2", greenboot=False)
        self.assertEqual(engine.reconcile_boot(None), "booted")

    def test_greenboot_rollback_is_recorded_not_retried_and_noticed(self):
        self.stage()
        # The new version booted and failed a required health check (greenboot's red.d) ...
        self.rig.backend.reboot_into(commit(2))
        self.assertEqual(self.rig.engine(boot="boot-2").reconcile_boot("red"), "pending")
        # ... then greenboot fell back: the previous deployment booted, the failed one remains.
        self.rig.backend.reboot_into(commit(1))
        engine = self.rig.engine(boot="boot-3")
        self.assertEqual(engine.reconcile_boot(None), "rolled_back")
        state = self.state()
        self.assertIn(commit(2), [item["commit"] for item in state["do_not_retry"]])
        self.assertEqual(state["do_not_retry"][-1]["expires_at"], 0)
        self.assertEqual(state["reports"][-1], {"channel": "stable", "from_version": "1.0.0", "to_version": "1.0.1",
                                                "arch": "x86_64", "result": "rolled_back",
                                                "error_class": "health-check"})
        status = engine.status()
        self.assertEqual(status.rolled_back_version, "1.0.1")
        self.assertGreater(status.rolled_back_at, 0)
        # The failed release is not offered again; a newer one is.
        self.rig.now += 7200
        self.assertEqual(engine.check().action, "none")
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2), release("1.0.2", 3)],
                                   generated=NOW + 3600))
        self.rig.now += 7200
        self.assertEqual(str(engine.check().release.version), "1.0.2")
        engine.acknowledge_rollback_notice()
        self.assertEqual(engine.status().rolled_back_version, "")

    def test_finalization_failure(self):
        self.stage()
        self.rig.backend.list = [d for d in self.rig.backend.list if not d.staged]
        engine = self.rig.engine(boot="boot-4")
        self.assertEqual(engine.reconcile_boot(None), "failed")
        self.assertEqual(self.state()["reports"][-1]["error_class"], "finalize")
        self.assertNotIn(commit(2), [i["commit"] for i in self.state()["do_not_retry"]])
        # The same commit failing to finalize a second time is not retried again.
        self.rig.now += 7200
        engine.automatic()
        self.assertEqual(engine.status().state, "staged")
        self.rig.backend.list = [d for d in self.rig.backend.list if not d.staged]
        self.assertEqual(self.rig.engine(boot="boot-5").reconcile_boot(None), "failed")
        self.assertIn(commit(2), [i["commit"] for i in self.state()["do_not_retry"]])

    def test_same_boot_staged_removed_by_someone(self):
        self.stage()
        self.rig.backend.list = [d for d in self.rig.backend.list if not d.staged]
        self.assertEqual(self.engine.reconcile_boot(None), "cleared")

    def test_staged_deployment_removed_by_someone_clears_pending(self):
        self.stage()
        self.rig.backend.list = [d for d in self.rig.backend.list if not d.staged]
        self.engine.refresh()
        self.assertIsNone(self.state()["pending"])
        self.assertEqual(self.engine.status().state, "idle")

    def _added_package(self):
        booted = next(d for d in self.rig.backend.list if d.booted)
        booted.layered = True

    def test_unreachable_package_repositories_still_stage_luma_from_cache(self):
        # ADR-038: htop was added with dnf and Fedora's mirrors are down.
        self._added_package()
        self.rig.backend.fail_with = TransactionError(
            "error: Updating rpm-md repo 'updates': Cannot download repomd.xml: Curl error (28): Timeout was reached")
        self.rig.backend.cache_only_works = True
        self.engine.check()
        self.assertTrue(self.engine.download(user_initiated=True))
        self.assertEqual(self.engine.status().state, "staged")
        self.assertEqual(self.rig.backend.calls[-1][-1], "cache-only")

    def test_unreachable_repositories_without_cache_leave_system_unchanged(self):
        self._added_package()
        self.rig.backend.fail_with = TransactionError("error: Cannot download repomd.xml: Curl error (6)")
        self.engine.check()
        self.assertFalse(self.engine.download(user_initiated=True))
        status = self.engine.status()
        self.assertEqual(status.last_error_class, "added-packages-unreachable")
        self.assertFalse(any(d.staged for d in self.rig.backend.list))

    def test_repository_error_without_added_packages_is_not_retried(self):
        self.rig.backend.fail_with = TransactionError("error: Cannot download repomd.xml: Curl error (6)")
        self.engine.check()
        self.assertFalse(self.engine.download(user_initiated=True))
        self.assertFalse(any(call[-1] == "cache-only" for call in self.rig.backend.calls))

    def test_busy_download_keeps_available_state(self):
        self.engine.check()
        self.rig.backend.active = ("RefreshMd", ":1.2", "/")
        self.assertFalse(self.engine.download(user_initiated=True))
        status = self.engine.status()
        self.assertEqual((status.state, status.available_version, status.last_error_class), ("available", "1.0.1", "busy"))

    def test_package_added_live_does_not_ask_for_a_restart(self):
        # sudo dnf install htop: rpm-ostree stages the booted release with the
        # package added and applies it to the running system at once.
        booted = self.rig.backend.list[0]
        layered = fakes.FakeDeployment("f" * 64, booted.version, booted.origin, staged=True,
                                       base_checksum=booted.checksum, layered=True)
        booted.live_replaced = layered.checksum
        self.rig.backend.list.insert(0, layered)
        self.engine.refresh()
        self.assertNotEqual(self.engine.status().state, "restart-required")
        booted.live_replaced = ""
        self.engine.refresh()
        self.assertEqual(self.engine.status().state, "restart-required")

    def test_manual_rollback_marks_booted_as_unwanted(self):
        self.rig.backend.list.append(fakes.FakeDeployment(commit(0), "0.9.0", "luma:luma/1/x86_64/stable"))
        self.engine.rollback()
        self.assertEqual(self.engine.status().state, "restart-required")
        self.assertIn(commit(1), [i["commit"] for i in self.state()["do_not_retry"]])


class ChannelsAndPreview(EngineCase):
    def test_preview_channel_needs_no_enrollment(self):
        self.engine.set_channel("beta")
        self.assertEqual(self.engine.status().channel, "beta")
        self.assertFalse(self.rig.paths.preview_credential.exists())

    def test_enroll_writes_root_only_credential_and_remote(self):
        url = self.rig.settings.preview_credentials_url
        self.rig.http.responses[url] = (201, {"credential": "A" * 32, "channels": ["beta", "nightly"],
                                              "credential_id": "device-credential-1"})
        remotes = self.rig.paths.ostree_remotes_dir
        remotes.mkdir(parents=True)
        (remotes / "luma.conf").write_text('# image comment\n[remote "luma"]\nurl=https://dl.simplyluma.com/os/repo\n'
                                           'gpg-verify=true\ngpg-verify-summary=true\n'
                                           'gpgkeypath=/etc/pki/ostree/luma-release.gpg\ncollection-id=org.projectluma.OS\n')
        self.engine.enroll_preview("beta", "connect-device-token-123")
        method, posted_url, payload, headers = self.rig.http.posts[-1]
        self.assertEqual((method, posted_url, payload), ("POST", url, {"channel": "beta", "arch": "x86_64"}))
        self.assertEqual(headers["Authorization"], "Bearer connect-device-token-123")
        credential = self.rig.paths.preview_credential
        self.assertEqual(stat.S_IMODE(credential.stat().st_mode), 0o600)
        self.assertNotIn("connect-device-token", credential.read_text())
        remote = remotes / "luma.conf"
        # The one luma remote now points at the root-only mirror list; the file stays
        # world-readable, secret-free and otherwise unchanged.
        self.assertEqual(stat.S_IMODE(remote.stat().st_mode), 0o644)
        text = remote.read_text()
        self.assertNotIn("A" * 32, text)
        self.assertIn(f"url=mirrorlist=file://{self.rig.paths.preview_mirrorlist}\n", text)
        self.assertIn("gpg-verify-summary=true\n", text)
        self.assertIn("collection-id=org.projectluma.OS\n", text)
        self.assertIn("# image comment\n", text)
        mirrorlist = self.rig.paths.preview_mirrorlist
        self.assertEqual(stat.S_IMODE(mirrorlist.stat().st_mode), 0o600)
        self.assertEqual(mirrorlist.read_text(), "https://dl.simplyluma.com/os/preview/" + "A" * 32 + "/repo\n")
        status = self.engine.status()
        self.assertEqual((status.channel, status.preview_enrolled), ("beta", True))
        self.assertIn("nightly", status.available_channels)

        # The beta graph is followed and the rebase refspec is the preview remote.
        self.rig.publish(graph_doc([release("1.1.0-beta.1", 5)], channel="beta"), channel="beta")
        self.engine.automatic()
        self.assertIn(("update", commit(5), "luma:luma/1/x86_64/beta", False), self.rig.backend.calls)

        self.engine.leave_preview()
        self.assertFalse(credential.exists())
        self.assertFalse(mirrorlist.exists())
        self.assertIn("url=https://dl.simplyluma.com/os/repo\n", remote.read_text())
        self.assertIn("collection-id=org.projectluma.OS\n", remote.read_text())
        self.assertEqual(self.engine.status().channel, "stable")
        self.assertIn(("cleanup",), self.rig.backend.calls)
        self.assertEqual(self.rig.http.posts[-1][0], "DELETE")

    def test_not_entitled(self):
        self.rig.http.responses[self.rig.settings.preview_credentials_url] = (403, {})
        with self.assertRaises(UpdateError) as caught:
            self.engine.enroll_preview("nightly", "connect-device-token-123")
        self.assertEqual(caught.exception.error_class, "not-entitled")
        self.assertFalse(self.rig.paths.preview_credential.exists())

    def test_less_advanced_channel_waits_switch_now_deploys_with_downgrade(self):
        self.rig.backend = fakes.FakeRpmOstree(booted_version="1.1.0-beta.2", booted_commit=commit(8),
                                               origin="luma:luma/1/x86_64/beta")
        self.rig.paths.preview_credential.parent.mkdir(parents=True, exist_ok=True)
        engine = self.rig.engine()
        engine.set_channel("stable")
        engine.automatic()
        self.assertEqual(self.rig.backend.calls, [])
        self.assertEqual(engine.status().channel, "stable")
        self.rig.now += 7200
        engine.set_channel("stable", switch_now=True)
        engine.automatic()
        self.assertIn(("update", commit(2), "luma:luma/1/x86_64/stable", True), self.rig.backend.calls)


class Reporting(EngineCase):
    def test_countme_weekly_and_no_identifiers(self):
        self.engine.automatic()
        countme = [p for (_m, _u, p, _h) in self.rig.http.posts if p and "countme_bucket" in p]
        self.assertEqual(countme, [{"channel": "stable", "version": "1.0.0", "arch": "x86_64", "countme_bucket": 1}])
        for _method, _url, payload, headers in self.rig.http.posts:
            if payload is None:
                continue
            self.assertLessEqual(set(payload), {"channel", "version", "arch", "countme_bucket", "from_version",
                                                "to_version", "result", "error_class"})
            self.assertNotIn("Authorization", headers)
        self.rig.now += 3 * 86400
        self.engine.flush_reports()
        self.assertEqual(len([p for (_m, _u, p, _h) in self.rig.http.posts if p and "countme_bucket" in p]),
                         len(countme) + (1 if fakes_week_changed(NOW, self.rig.now) else 0))
        self.assertEqual(self.state()["reports"], [])

    def test_statistics_off_sends_and_queues_nothing(self):
        config = self.rig.paths.statistics_config
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("[statistics]\nenabled = false\n")
        self.engine.automatic()
        self.assertEqual([p for p in self.rig.http.posts if p[1].endswith("/events")], [])
        self.assertEqual(self.state()["reports"], [])

    def test_damaged_statistics_file_means_off(self):
        config = self.rig.paths.statistics_config
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("enabled = false\n")  # no section: unreadable intent
        self.engine.automatic()
        self.assertEqual([p for p in self.rig.http.posts if p[1].endswith("/events")], [])

    def test_offline_reports_stay_queued(self):
        self.engine.check()
        self.engine.download(user_initiated=True)
        self.rig.http.offline = True
        self.engine.flush_reports()
        self.assertEqual(len(self.state()["reports"]), 1)
        self.rig.http.offline = False
        self.engine.flush_reports()
        self.assertEqual(self.state()["reports"], [])


def fakes_week_changed(a, b):
    from luma_update.reporting import window_start
    return window_start(a) != window_start(b)


class StatusContract(unittest.TestCase):
    def test_json_keys_are_the_documented_contract(self):
        from luma_update.status import DBUS_TYPES, Status, dbus_name
        data = Status().to_json_dict()
        self.assertEqual(set(data) - {"schema_version"}, set(DBUS_TYPES))
        depot_properties = ('State', 'Channel', 'BootedVersion', 'BootedCommit', 'StagedVersion', 'AvailableVersion',
                            'AvailableSummary', 'NotesUrl', 'Importance', 'DownloadBytes', 'Progress', 'LastCheck',
                            'LastError', 'Metered', 'RollbackAvailable', 'PreviewEnrolled', 'AvailableChannels')
        self.assertTrue(set(depot_properties) <= {dbus_name(k) for k in DBUS_TYPES})
        self.assertIn(Status().state, STATES)

    def test_introspection_xml_is_valid_and_complete(self):
        import xml.etree.ElementTree as ET
        from luma_update.dbus_interface import introspection_xml
        from luma_update.status import DBUS_TYPES, dbus_name
        root = ET.fromstring(introspection_xml().split("\n", 2)[2])
        interface = root.find("interface")
        props = {p.get("name"): p.get("type") for p in interface.findall("property")}
        self.assertEqual(props, {dbus_name(k): v for k, v in DBUS_TYPES.items()})
        methods = {m.get("name") for m in interface.findall("method")}
        self.assertEqual(methods, {"Check", "Download", "Cancel", "Apply", "SetChannel", "SetChannelNow", "Rollback",
                                   "EnrollPreview", "LeavePreview", "AcknowledgeRollback", "SetAutomaticDownload",
                                   "IgnoreVersion", "ClearIgnoredVersion", "AdoptChannel", "Automatic"})


if __name__ == "__main__":
    unittest.main()
