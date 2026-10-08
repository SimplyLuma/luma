# SPDX-License-Identifier: Apache-2.0
"""Hardware updates: checks before Install, failures in plain words, backing off.

Every case is a fake fwupd answer: an error code, its message and how far the
install got, or the facts the probe gathers before Install.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import types
import unittest

from luma_installer import depot_firmware as fw
from luma_installer import depot_firmware_safety as safety

#: The owner's ThinkPad, fwupd 2.1.7: the modem_manager plugin's detach fails
#: without setting an error.
OWNER_MESSAGE = "failed to detach: failed to detach using modem_manager: unspecified error"


@dataclass
class Update:
    device_id: str = "cf7690c47e8c35378ae54fe736409478aaa1d0a3"
    device: str = "RM520N-GL"
    current_version: str = "RM520NGLAAR03A03M4G_04.211.04.211"
    version: str = "RM520NGLAAR03A03M4G_04.220.04.220"
    plugin: str = "modem_manager"
    protocols: tuple = ("com.qualcomm.firehose",)
    vendor_ids: tuple = ("PCI:0x1EAC",)
    guids: tuple = ("595c3b9c-1f4a-541d-a14b-5af13b24c988",)
    requires_ac: bool = True
    needs_reboot: bool = False
    removable: bool = False


CAPSULE = Update(device_id="uefi", device="System Firmware", current_version="1.20", version="1.22",
                 plugin="uefi_capsule", protocols=("org.uefi.capsule",), vendor_ids=("DMI:LENOVO",),
                 guids=("230c8b18-8d9b-53ec-838b-6cfc0383493a",), needs_reboot=True)


class Text(unittest.TestCase):
    def test_control_characters_from_devices_are_stripped(self):
        self.assertEqual(safety.clean("RM520N-GL\r"), "RM520N-GL")
        self.assertEqual(safety.clean("a\x00b\x1fc\n"), "abc")
        self.assertEqual(safety.clean(None), "")

    def test_fwupdmgr_json_with_a_raw_carriage_return_still_parses(self):
        # What `fwupdmgr get-updates --json` wrote on the owner's ThinkPad.
        raw = '{"Devices": [{"InstanceIds": ["PCI\\\\VID_1EAC&PID_1007&NAME_RM520N-GL\r"]}]}'
        with self.assertRaises(ValueError):
            json.loads(raw)
        value = safety.loads_lenient(raw.encode())
        self.assertEqual(safety.clean(value["Devices"][0]["InstanceIds"][0]), "PCI\\VID_1EAC&PID_1007&NAME_RM520N-GL")

    def test_error_names(self):
        self.assertEqual(safety.error_name(12), "AcPowerRequired")
        self.assertEqual(safety.error_name("org.freedesktop.fwupd.Internal"), "Internal")
        self.assertEqual(safety.error_name("TimedOut"), "TimedOut")
        self.assertEqual(safety.error_name(99), "")
        self.assertEqual(safety.error_name(None), "")
        self.assertEqual(safety.error_name(True), "")

    def test_phase_from_fu_engine_prefixes(self):
        self.assertEqual(safety.phase_from_message(OWNER_MESSAGE), "detach")
        self.assertEqual(safety.phase_from_message("failed to write-firmware: timed out"), "write")
        self.assertEqual(safety.phase_from_message("failed to attach: device not found"), "attach")
        self.assertEqual(safety.phase_from_message("checksum mismatch"), "prepare")
        self.assertEqual(safety.phase_from_message("", "write"), "write")


class Failures(unittest.TestCase):
    def test_the_owners_modem_says_nothing_changed_and_never_unspecified(self):
        failure = safety.explain(0, OWNER_MESSAGE, phase="detach", log="fwupd: unset plugin error")
        self.assertEqual(failure.title, "The update couldn’t start")
        self.assertEqual(f"{failure.title}. {failure.body}", safety.COULD_NOT_START)
        self.assertEqual(failure.body, "Nothing was changed on your device.")
        self.assertEqual(failure.stage, "before")
        self.assertTrue(failure.changed_nothing)
        self.assertTrue(failure.retry)
        self.assertFalse(failure.urgent)
        self.assertIn(OWNER_MESSAGE, failure.detail)
        self.assertIn("unset plugin error", failure.detail)

    def test_no_error_at_all(self):
        failure = safety.explain(None, "")
        self.assertEqual(f"{failure.title}. {failure.body}", safety.COULD_NOT_START)

    def test_every_class_before_writing(self):
        cases = [
            (12, "", "Connect your charger", "ac", True),
            (15, "battery level too low", "Charge first", "battery", True),
            (0, "lid is closed", "Open the lid", "lid", True),
            (4, "not authorized", "Permission needed", "authorization", True),
            (17, "", "Permission needed", "authorization", True),
            (11, "signature invalid", "The update couldn’t be verified", "verification", True),
            (7, "checksum mismatch", "The update couldn’t be verified", "verification", True),
            (21, "could not resolve host", "The update couldn’t be downloaded", "network", True),
            (0, "ESP free space was 3 MB but required 40 MB", "Not enough room on the startup partition",
             "esp", True),
            (0, "Secure Boot is enabled and shim is missing", "Secure Boot stopped the update", "secure-boot",
             False),
            (3, "", "Restart to finish the waiting update", "pending", False),
            (20, "device is busy", "The device is busy", "busy", True),
            (2, "", "Already up to date", "current", False),
            (1, "", "Already up to date", "current", False),
            (10, "not supported", "This update can’t be installed here", "unsupported", False),
            (16, "Unplug the device and replug it", "The device needs you first", "user-action", True),
            (14, "", "This computer can’t update firmware right now", "broken-system", False),
            (19, "failed to detach: timed out", "The device isn’t responding", "unresponsive", True),
            (0, "failed to detach: suitable port not found", "The device isn’t responding", "unresponsive",
             True),
        ]
        for code, message, title, kind, retry in cases:
            with self.subTest(code=code, message=message):
                failure = safety.explain(code, message)
                self.assertEqual(failure.title, title)
                self.assertEqual(failure.kind, kind)
                self.assertEqual(failure.retry, retry)
                self.assertEqual(failure.stage, "before")
                self.assertTrue(failure.body.endswith(safety.NOTHING_CHANGED))
                self.assertNotIn("unspecified", (failure.title + failure.body).lower())

    def test_device_gone_before_writing(self):
        self.assertEqual(safety.explain(8, "", removable=True).body,
                         "Reconnect it and try again. Nothing was changed on your device.")
        self.assertIn("Restart", safety.explain(8, "").body)
        self.assertEqual(safety.explain(19, "", removable=True).body,
                         "Unplug it, plug it back in and try again. Nothing was changed on your device.")
        self.assertEqual(safety.explain(19, "").action, "restart")

    def test_stopping_while_writing_is_urgent_and_never_says_nothing_changed(self):
        for message, phase, after in (("failed to write-firmware: timed out", "", "present"),
                                      ("anything", "write", "present"),
                                      (OWNER_MESSAGE, "detach", "bootloader"),
                                      ("failed to write-firmware: gone", "write", "missing")):
            with self.subTest(message=message, after=after):
                failure = safety.explain(0, message, phase=phase, device_after=after,
                                         device_name="your mobile broadband modem")
                self.assertEqual(failure.stage, "during")
                self.assertTrue(failure.urgent)
                self.assertTrue(failure.retry)
                self.assertEqual(failure.title, "The update stopped while it was being written")
                self.assertNotIn(safety.NOTHING_CHANGED, failure.body)
                self.assertIn("on power", failure.body)
        self.assertIn("Your mobile broadband modem is waiting in its update mode",
                      safety.explain(0, "x", phase="write", device_after="bootloader",
                                     device_name="your mobile broadband modem").body)

    def test_after_writing_asks_for_a_restart(self):
        failure = safety.explain(0, "failed to attach: no device", phase="attach")
        self.assertEqual(failure.stage, "after")
        self.assertEqual(failure.action, "restart")
        self.assertEqual(failure.title, "Restart to finish the update")

    def test_lost_track(self):
        failure = safety.explain(None, "exited with status 1", phase="unknown")
        self.assertEqual(failure.stage, "unknown")
        self.assertNotIn(safety.NOTHING_CHANGED, failure.body)


class Requirements(unittest.TestCase):
    def keys(self, update, facts, **kwargs):
        return [(r.key, r.blocking) for r in safety.requirements(update, facts, **kwargs)]

    def test_everything_met(self):
        self.assertEqual(safety.requirements(Update(), {"daemon_ok": True, "on_battery": False,
                                                        "metadata_age_days": 2}), [])

    def test_charger(self):
        # The charger line covers a low battery too; it is not said twice.
        self.assertEqual([r.key for r in safety.requirements(Update(), {"on_battery": True, "battery_level": 5})],
                         ["ac"])
        found = safety.requirements(Update(), {"on_battery": True, "battery_level": 80})
        self.assertEqual([r.key for r in found], ["ac"])
        self.assertEqual(found[0].text, "Connect your charger. This update needs power the whole time.")
        self.assertTrue(safety.blocking(found))

    def test_battery_level_against_fwupds_threshold(self):
        update = Update(requires_ac=False)
        found = safety.requirements(update, {"on_battery": True, "battery_level": 22, "battery_threshold": 30})
        self.assertEqual(found[0].text, "Charge this computer to at least 30% first (it’s at 22%), or connect "
                                        "your charger.")
        self.assertEqual(safety.requirements(update, {"on_battery": False, "battery_level": 5}), [])
        self.assertEqual(self.keys(update, {"on_battery": True, "battery_level": 40}), [])

    def test_fwupd_device_problems(self):
        facts = {"problems": ["lid-is-closed", "unreachable", "update-pending", "in-use", "something-new"]}
        found = safety.requirements(Update(), facts, device_name="your mouse")
        self.assertEqual([r.key for r in found], ["lid", "unreachable", "pending", "in-use"])
        self.assertIn("Turn on your mouse", found[1].text)

    def test_device_battery(self):
        found = safety.requirements(Update(), {"device_battery_level": 12, "device_battery_threshold": 30},
                                    device_name="your keyboard")
        self.assertEqual(found[0].text, "Charge your keyboard to at least 30% first (it’s at 12%).")

    def test_uefi_capsule_space_secure_boot_and_mode(self):
        facts = {"esp_free": 10 * 1024 * 1024, "esp_needed": 42 * 1024 * 1024, "secure_boot": True,
                 "signed_loader": False, "uefi": False}
        found = {r.key: r for r in safety.requirements(CAPSULE, facts)}
        self.assertEqual(set(found), {"esp", "secure-boot", "uefi"})
        self.assertEqual(found["esp"].text, "Free 32 MB on the startup (EFI) partition. It has 10 MB free and "
                                            "this update needs 42 MB.")
        # Not a capsule: the startup partition is not its business.
        self.assertEqual(safety.requirements(Update(), facts), [])
        self.assertEqual(safety.requirements(CAPSULE, {"esp_free": 400 << 20, "esp_needed": 40 << 20,
                                                       "secure_boot": True, "signed_loader": True,
                                                       "uefi": True}), [])

    def test_a_connected_modem_is_said_but_does_not_block(self):
        found = safety.requirements(Update(), {"modem_connected": True}, kind="modem")
        self.assertEqual(self.keys(Update(), {"modem_connected": True}, kind="modem"),
                         [("modem-connected", False)])
        self.assertFalse(safety.blocking(found))
        self.assertIn("Mobile data disconnects", found[0].text)

    def test_stale_metadata_offers_refresh(self):
        (found,) = safety.requirements(Update(), {"metadata_age_days": 45})
        self.assertEqual((found.key, found.action, found.action_label), ("metadata", "refresh", "Refresh"))
        self.assertIn("45 days old", found.text)

    def test_unhealthy_daemon(self):
        (found,) = safety.requirements(Update(), {"daemon_ok": False})
        self.assertEqual(found.text, "The firmware service isn’t responding. Try again after restarting this "
                                     "computer.")


class BackingOff(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "state/attempts.json"
        self.key = safety.attempt_key("guid", "modem_manager", "1", "2", "2.1.7-1.fc44")
        self.t0 = datetime(2026, 9, 18, 21, 39, tzinfo=timezone.utc)

    def fail(self, attempts, stage="before", kind="unspecified", minutes=0):
        attempts.record(self.key, "failed", stage=stage, kind=kind, when=self.t0 + timedelta(minutes=minutes))

    def test_one_failure_can_be_tried_again_two_wait_a_day_three_pause(self):
        attempts = safety.Attempts.load(self.path)
        self.fail(attempts)
        self.assertIsNone(safety.hold(attempts, self.key, now=self.t0))
        self.fail(attempts, minutes=1)
        held = safety.hold(attempts, self.key, now=self.t0 + timedelta(hours=1))
        self.assertEqual(held.state, "held")
        self.assertEqual(held.until, self.t0 + timedelta(minutes=1, hours=24))
        self.assertIsNone(safety.hold(attempts, self.key, now=self.t0 + timedelta(hours=25)))
        self.fail(attempts, minutes=60 * 26)
        paused = safety.hold(attempts, self.key, now=self.t0 + timedelta(days=30))
        self.assertEqual(paused.state, "paused")
        self.assertEqual(paused.failures, 3)
        # Kept on disk, read back by the next Depot.
        self.assertEqual(safety.hold(safety.Attempts.load(self.path), self.key,
                                     now=self.t0 + timedelta(days=30)).state, "paused")

    def test_a_new_fwupd_build_or_release_is_a_new_offer(self):
        fixed = safety.attempt_key("guid", "modem_manager", "1", "2", "2.1.7-1.luma.1.fc44")
        newer = safety.attempt_key("guid", "modem_manager", "1", "3", "2.1.7-1.fc44")
        self.assertNotEqual(fixed, self.key)
        self.assertNotEqual(newer, self.key)
        attempts = safety.Attempts.load(self.path)
        for minute in range(3):
            self.fail(attempts, minutes=minute)
        self.assertIsNone(safety.hold(attempts, fixed))
        self.assertIsNone(safety.hold(attempts, newer))

    def test_failures_while_writing_never_hold_an_offer(self):
        attempts = safety.Attempts.load(self.path)
        for minute in range(4):
            self.fail(attempts, stage="during", kind="interrupted", minutes=minute)
        self.assertIsNone(safety.hold(attempts, self.key, now=self.t0))

    def test_a_success_clears_the_run(self):
        attempts = safety.Attempts.load(self.path)
        self.fail(attempts)
        self.fail(attempts, minutes=1)
        attempts.record(self.key, "succeeded", when=self.t0 + timedelta(minutes=2))
        self.assertIsNone(safety.hold(attempts, self.key, now=self.t0 + timedelta(minutes=3)))

    def test_unsupported_pauses_at_once(self):
        attempts = safety.Attempts.load(self.path)
        self.fail(attempts, kind="unsupported")
        self.assertEqual(safety.hold(attempts, self.key).state, "paused")

    def test_a_damaged_record_is_an_empty_one(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json")
        self.assertEqual(safety.Attempts.load(self.path).entries, {})
        self.path.write_text('{"attempts": ["x"]}')
        self.assertEqual(safety.Attempts.load(self.path).entries, {})

    def test_state_path(self):
        self.assertEqual(safety.state_path({"XDG_STATE_HOME": "/s"}), Path("/s/luma/depot/firmware-attempts.json"))
        self.assertEqual(safety.state_path({"HOME": "/h"}), Path("/h/.local/state/luma/depot/firmware-attempts.json"))


class KnownIssues(unittest.TestCase):
    def test_fwupd_2_1_7_pcie_firehose_modems_are_paused_until_the_fix(self):
        self.assertIsNotNone(safety.known_issue(Update(), "2.1.7-1.fc44"))
        self.assertIsNone(safety.known_issue(Update(), "2.1.7-1.luma.1.fc44"))
        self.assertIsNone(safety.known_issue(Update(), "2.1.8-1.fc44"))
        self.assertIsNone(safety.known_issue(Update(vendor_ids=("USB:0x2C7C",)), "2.1.7-1.fc44"))
        self.assertIsNone(safety.known_issue(CAPSULE, "2.1.7-1.fc44"))


class SignedListRules(unittest.TestCase):
    def test_plugin_and_fwupd_build_entries(self):
        entry = fw.BlockEntry(plugin="modem_manager", fwupd=("2.1.7-1.fc44",), reason="r")
        blocklist = fw.BlockList("2026-09-18T00:00:00Z", (entry,))
        self.assertIs(blocklist.blocks(("a",), "2", "modem_manager", "2.1.7-1.fc44"), entry)
        self.assertIsNone(blocklist.blocks(("a",), "2", "modem_manager", "2.1.7-1.luma.1.fc44"))
        self.assertIsNone(blocklist.blocks(("a",), "2", "uefi_capsule", "2.1.7-1.fc44"))
        self.assertIsNone(fw.BlockList("", (fw.BlockEntry(),)).blocks(("a",), "2"))


class Journal(unittest.TestCase):
    def test_structured_fields_for_vitals(self):
        sent = []
        journal = types.SimpleNamespace(send=lambda line, **fields: sent.append((line, fields)))
        systemd = types.ModuleType("systemd")
        systemd.journal = journal
        saved = sys.modules.get("systemd"), sys.modules.get("systemd.journal")
        sys.modules["systemd"], sys.modules["systemd.journal"] = systemd, journal
        try:
            failure = safety.explain(0, OWNER_MESSAGE, phase="detach")
            safety.journal("failed", Update(), fwupd_build="2.1.7-1.fc44", failure=failure, attempts=2)
        finally:
            for name, module in zip(("systemd", "systemd.journal"), saved):
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
        ((line, fields),) = sent
        self.assertEqual(fields["MESSAGE_ID"], safety.MESSAGE_ID)
        self.assertEqual(fields["PRIORITY"], "3")
        self.assertEqual(fields["LUMA_FIRMWARE_GUID"], "595c3b9c-1f4a-541d-a14b-5af13b24c988")
        self.assertEqual(fields["LUMA_FIRMWARE_PLUGIN"], "modem_manager")
        self.assertEqual(fields["LUMA_FIRMWARE_FWUPD"], "2.1.7-1.fc44")
        self.assertEqual(fields["LUMA_FIRMWARE_PHASE"], "detach")
        self.assertEqual(fields["LUMA_FIRMWARE_STAGE"], "before")
        self.assertEqual(fields["LUMA_FIRMWARE_ERROR_CODE"], "Internal")
        self.assertEqual(fields["LUMA_FIRMWARE_ATTEMPTS"], "2")
        self.assertIn("RM520NGLAAR03A03M4G_04.220.04.220", fields["LUMA_FIRMWARE_VERSION"])
        self.assertIn("failed for RM520N-GL", line)


if __name__ == "__main__":
    unittest.main()
