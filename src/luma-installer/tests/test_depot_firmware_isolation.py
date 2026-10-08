# SPDX-License-Identifier: Apache-2.0
"""Firmware work runs in a process of its own and can never close Depot.

The probe is replaced by small Python programs that answer, crash, hang or talk
nonsense the way fwupd and libfwupd can. The real-daemon counterpart is
tests/depot/fwupd-container.sh.
"""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import textwrap
import time
import unittest

try:
    from gi.repository import GLib
    from luma_depot import system_updates as su
except (ImportError, ValueError) as error:  # pragma: no cover - PyGObject is a BuildRequires
    raise unittest.SkipTest(f"PyGObject unavailable: {error}")

UPDATE = {
    "device_id": "fixture", "device": "System Firmware", "vendor": "Fixture", "current_version": "1.0",
    "version": "1.1", "summary": "Fixes", "notes": "", "urgency": "high", "needs_reboot": True,
    "requires_ac": True, "details_url": "", "protocols": ["org.uefi.capsule"], "icons": [],
    "plugin": "uefi_capsule", "guids": ["230c8b18-8d9b-53ec-838b-6cfc0383493a"], "security_release": False,
}


def pump(until, seconds=10.0):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while not until() and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.01)
    return until()


class Probe:
    """A stand-in firmware probe: ``body`` is Python run with the probe's arguments in ``args``."""

    def __init__(self, directory: Path, body: str) -> None:
        self.log = directory / "calls.log"
        self.script = directory / "probe.py"
        self.script.write_text(textwrap.dedent(f"""\
            import json, os, sys, time
            args = sys.argv[1:]
            with open({str(self.log)!r}, "a") as log:
                log.write(" ".join(args) + "\\n")
            """) + textwrap.dedent(body))

    @property
    def command(self):
        return [sys.executable, str(self.script)]

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []


class ProbeCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.changes = 0

    def tearDown(self):
        self._tmp.cleanup()

    def firmware(self, body, prelude="", **kwargs):
        self.probe = Probe(self.dir, prelude + body)
        kwargs.setdefault("attempts_path", self.dir / "attempts.json")
        return su.Firmware(self._changed, command=self.probe.command, **kwargs)

    def _changed(self):
        self.changes += 1


class Checking(ProbeCase):
    def test_updates_are_read_from_the_probe(self):
        firmware = self.firmware(f"print(json.dumps({{'ok': True, 'updates': [{UPDATE!r}], "
                                 f"'on_battery': True}}))\n")
        firmware.load()
        self.assertTrue(firmware.checking)
        self.assertTrue(pump(lambda: firmware.loaded))
        self.assertTrue(firmware.available)
        self.assertEqual(firmware.problem, "")
        self.assertTrue(firmware.on_battery)
        (update,) = firmware.updates
        self.assertEqual(update.guids, ("230c8b18-8d9b-53ec-838b-6cfc0383493a",))
        self.assertEqual(update.protocols, ("org.uefi.capsule",))
        self.assertEqual(self.probe.calls(), ["list"])
        self.assertFalse(firmware.checking)
        self.assertNotIn("gi.repository.Fwupd", sys.modules)

    def test_a_crashing_probe_leaves_a_calm_problem(self):
        firmware = self.firmware("print('{\"progress\"'); sys.stdout.flush(); os.abort()\n")
        firmware.updates = (su._firmware_update(UPDATE),)
        firmware.load()
        self.assertTrue(pump(lambda: firmware.loaded))
        self.assertEqual(firmware.problem, su.FIRMWARE_CHECK_FAILED)
        self.assertTrue(firmware.available)
        self.assertEqual(firmware.updates, ())
        self.assertGreater(self.changes, 0)

    def test_a_hung_fwupd_is_given_up_on(self):
        firmware = self.firmware("time.sleep(60)\n", check_seconds=1)
        started = time.monotonic()
        firmware.load()
        self.assertTrue(pump(lambda: firmware.loaded, seconds=15))
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(firmware.problem, su.FIRMWARE_CHECK_FAILED)

    def test_unreachable_fwupd_is_a_problem_not_a_missing_service(self):
        firmware = self.firmware("print(json.dumps({'ok': False, 'code': 'unavailable', "
                                 "'hint': 'The name is not activatable'}))\n")
        firmware.load()
        self.assertTrue(pump(lambda: firmware.loaded))
        self.assertTrue(firmware.available)
        self.assertEqual(firmware.problem, su.FIRMWARE_CHECK_FAILED)

    def test_missing_fwupd(self):
        firmware = self.firmware("print(json.dumps({'ok': False, 'code': 'missing', 'hint': ''}))\n")
        firmware.load()
        self.assertTrue(pump(lambda: firmware.loaded))
        self.assertIs(firmware.available, False)
        self.assertEqual(firmware.problem, "")

    def test_nonsense_and_malformed_updates(self):
        for body in ("print('not json at all')\n",
                     "print(json.dumps({'ok': True, 'updates': [{'device_id': 'x'}]}))\n"):
            with self.subTest(body=body):
                firmware = self.firmware(body)
                firmware.load()
                self.assertTrue(pump(lambda: firmware.loaded))
                self.assertEqual(firmware.problem, su.FIRMWARE_CHECK_FAILED)
                self.assertEqual(firmware.updates, ())

    def test_a_missing_interpreter_is_a_problem(self):
        firmware = su.Firmware(self._changed, command=[str(self.dir / "no-such-python")])
        firmware.load()
        self.assertTrue(pump(lambda: firmware.loaded))
        self.assertEqual(firmware.problem, su.FIRMWARE_CHECK_FAILED)

    def test_a_success_after_a_problem_clears_it(self):
        flag = self.dir / "second"
        firmware = self.firmware(f"import pathlib\nflag = pathlib.Path({str(flag)!r})\n"
                                 "if not flag.exists():\n    flag.touch(); os.abort()\n"
                                 "print(json.dumps({'ok': True, 'updates': []}))\n")
        firmware.load()
        self.assertTrue(pump(lambda: firmware.loaded))
        self.assertTrue(firmware.problem)
        firmware.loaded = False
        firmware.load()
        self.assertTrue(pump(lambda: firmware.loaded))
        self.assertEqual(firmware.problem, "")

    def test_checks_asked_for_while_one_runs_become_one_more_check(self):
        firmware = self.firmware("time.sleep(0.3)\nprint(json.dumps({'ok': True, 'updates': []}))\n")
        firmware.load()
        firmware.load()
        firmware.load()
        self.assertTrue(pump(lambda: len(self.probe.calls()) == 2 and not firmware.checking))
        pump(lambda: False, seconds=0.5)
        self.assertEqual(self.probe.calls(), ["list", "list"])


class Installing(ProbeCase):
    def install(self, body):
        firmware = self.firmware(
            "if args[0] == 'list':\n    print(json.dumps({'ok': True, 'updates': [UPDATE]}))\n    sys.exit(0)\n"
            + body, prelude=f"UPDATE = {UPDATE!r}\n")
        progress = []
        original = firmware._progress
        firmware._progress = lambda value: (progress.append(value), original(value))[1]
        firmware.install(su._firmware_update(UPDATE))
        self.assertEqual(firmware.installing, "fixture")
        self.assertTrue(pump(lambda: not firmware.installing and firmware.loaded))
        return firmware, progress

    def test_progress_and_success(self):
        firmware, progress = self.install(
            "for p in (0.1, 0.5, 1.0):\n    print(json.dumps({'progress': p}), flush=True)\n"
            "print(json.dumps({'ok': True}))\n")
        self.assertEqual(progress, [0.1, 0.5, 1.0])
        self.assertEqual(firmware.error, "")
        self.assertEqual(self.probe.calls(), ["install fixture", "list"])

    def test_a_refusal_becomes_an_action(self):
        firmware, _ = self.install("print(json.dumps({'ok': False, 'code': 'failed', 'fwupd_code': 12, "
                                   "'message': 'Cannot install update without external power', "
                                   "'phase': 'prepare'}))\n")
        failure = firmware.failures["fixture"]
        self.assertEqual(failure.title, "Connect your charger")
        self.assertEqual(failure.stage, "before")
        self.assertEqual(firmware.error, failure.body)

    def test_a_crash_mid_install_is_reported_calmly(self):
        firmware, progress = self.install("print(json.dumps({'progress': 0.4}), flush=True)\nos.abort()\n")
        self.assertEqual(progress, [0.4])
        self.assertEqual(firmware.error, su.FIRMWARE_INSTALL_STOPPED)
        self.assertEqual(firmware.problem, "")
        # Depot cannot know nothing was written, so it does not say so.
        self.assertEqual(firmware.failures["fixture"].stage, "unknown")


MODEM = dict(UPDATE, device_id="modem", device="RM520N-GL", current_version="04.211", version="04.220",
             needs_reboot=False, protocols=["com.qualcomm.firehose"], icons=["modem"], plugin="modem_manager",
             guids=["595c3b9c-1f4a-541d-a14b-5af13b24c988"], vendor_ids=["PCI:0x1EAC"])
OWNER_FAILURE = {"ok": False, "code": "failed", "fwupd_code": 0, "domain": "fwupd-error-quark",
                 "message": "failed to detach: failed to detach using modem_manager: unspecified error",
                 "hint": "failed to detach: failed to detach using modem_manager: unspecified error",
                 "phase": "detach", "device_after": "present",
                 "log": "FuPlugin unset plugin error in modem_manager(detach)"}


def listing(updates, host=None, facts=None):
    values = [dict(update, facts=(facts or {}).get(update["device_id"], {})) for update in updates]
    return json.dumps({"ok": True, "updates": values, "on_battery": False,
                       "host": host or {"fwupd_build": "2.1.7-1.luma.1.fc44", "daemon_ok": True}})


class Safety(ProbeCase):
    """The owner's modem, and every state the hardware card can be in."""

    def modem(self, install_body, host=None, facts=None):
        firmware = self.firmware(
            "if args[0] == 'list':\n"
            f"    print({listing([MODEM], host, facts)!r})\n    sys.exit(0)\n" + install_body)
        firmware.load()
        self.assertTrue(pump(lambda: firmware.loaded))
        (update,) = firmware.updates
        return firmware, update

    def run_install(self, firmware, update):
        firmware.loaded = False
        firmware.install(update)
        self.assertTrue(pump(lambda: not firmware.installing and firmware.loaded and not firmware.checking))

    def test_the_owners_modem_failure_backs_off_then_pauses(self):
        firmware, update = self.modem(f"print(json.dumps({OWNER_FAILURE!r}))\n")
        self.assertEqual(firmware.offer(update).state, "ready")
        self.run_install(firmware, update)
        failure = firmware.failures["modem"]
        self.assertEqual(f"{failure.title}. {failure.body}", "The update couldn’t start. Nothing was changed on your device.")
        self.assertIn("unset plugin error", failure.detail)
        self.assertNotIn("unspecified", firmware.error)
        self.assertEqual(firmware.offer(update).state, "ready")       # Try Again
        self.run_install(firmware, update)
        self.assertEqual(firmware.offer(update).state, "held")        # twice: tomorrow
        self.assertEqual(firmware.attention(), (update,))              # the failure still needs reading
        calls = len(self.probe.calls())
        firmware.install(update)                                       # a stale click does nothing
        self.assertEqual(len(self.probe.calls()), calls)
        # The record survives Depot restarting.
        again = su.Firmware(self._changed, command=self.probe.command, attempts_path=self.dir / "attempts.json")
        again.host = firmware.host
        self.assertEqual(again.offer(update).state, "held")

    def test_three_failures_pause_until_fwupd_changes(self):
        firmware, update = self.modem(f"print(json.dumps({OWNER_FAILURE!r}))\n")
        from luma_installer import depot_firmware_safety as safety
        for minute in range(3):
            firmware.attempts.record(firmware.key(update), "failed", stage="before", kind="unspecified")
        self.assertEqual(firmware.offer(update).state, "paused")
        self.assertNotIn(update, firmware.attention())
        firmware.host = dict(firmware.host, fwupd_build="2.1.8-1.fc44")
        self.assertEqual(firmware.offer(update).state, "ready")
        self.assertIsNone(safety.hold(firmware.attempts, firmware.key(update)))

    def test_the_known_fwupd_bug_pauses_the_offer_before_anyone_tries(self):
        firmware, update = self.modem("print(json.dumps({'ok': True}))\n",
                                      host={"fwupd_build": "2.1.7-1.fc44", "daemon_ok": True})
        offer = firmware.offer(update)
        self.assertEqual(offer.state, "paused")
        self.assertIn("known problem", offer.reason)
        firmware.install(update)
        self.assertEqual(self.probe.calls(), ["list"])

    def test_a_signed_block_list_entry_pauses_with_its_reason(self):
        firmware, update = self.modem("", facts={})
        blocked = su._firmware_update(dict(MODEM, blocked_reason="Bricks this model"))
        self.assertEqual(firmware.offer(blocked).state, "paused")
        self.assertEqual(firmware.offer(blocked).reason, "Bricks this model")

    def test_preflight_blocks_install_before_anything_runs(self):
        firmware, update = self.modem("print(json.dumps({'ok': True}))\n",
                                      host={"fwupd_build": "2.1.7-1.luma.1.fc44", "daemon_ok": True,
                                            "on_battery": True, "battery_level": 64})
        offer = firmware.offer(update)
        self.assertEqual(offer.state, "unmet")
        self.assertEqual([r.key for r in offer.requirements], ["ac"])
        firmware.install(update)
        self.assertEqual(self.probe.calls(), ["list"])

    def test_a_connected_modem_is_a_note_not_a_block(self):
        firmware, update = self.modem("", facts={"modem": {"modem_connected": True}})
        offer = firmware.offer(update)
        self.assertEqual(offer.state, "ready")
        self.assertEqual([r.key for r in offer.requirements], ["modem-connected"])

    def test_stopping_while_writing_is_urgent_and_never_held(self):
        body = ("print(json.dumps({'phase': 'write'}), flush=True)\n"
                "print(json.dumps({'ok': False, 'code': 'failed', 'fwupd_code': 19, "
                "'message': 'failed to write-firmware: timed out', 'phase': 'write', "
                "'device_after': 'bootloader'}))\n")
        firmware, update = self.modem(body)
        for _ in range(3):
            self.run_install(firmware, update)
            failure = firmware.failures["modem"]
            self.assertTrue(failure.urgent)
            self.assertEqual(failure.stage, "during")
            self.assertEqual(firmware.offer(update).state, "ready")

    def test_an_urgent_failure_outlives_its_device_leaving_the_list(self):
        body = ("print(json.dumps({'ok': False, 'code': 'failed', 'fwupd_code': 6, "
                "'message': 'failed to write-firmware: gone', 'phase': 'write', 'device_after': 'missing'}))\n")
        flag = self.dir / "installed"
        firmware = self.firmware(
            "import pathlib\n"
            f"flag = pathlib.Path({str(flag)!r})\n"
            "if args[0] == 'list':\n"
            f"    print({listing([MODEM])!r} if not flag.exists() else json.dumps({{'ok': True, 'updates': []}}))\n"
            "    sys.exit(0)\n"
            "flag.touch()\n" + body)
        firmware.load()
        self.assertTrue(pump(lambda: firmware.loaded))
        (update,) = firmware.updates
        firmware.loaded = False
        firmware.install(update)
        self.assertTrue(pump(lambda: not firmware.installing and firmware.loaded and not firmware.checking))
        self.assertEqual(firmware.updates, ())
        self.assertTrue(firmware.failures["modem"].urgent)

    def test_success_is_recorded(self):
        firmware, update = self.modem("print(json.dumps({'phase': 'write'}), flush=True)\n"
                                      "print(json.dumps({'ok': True, 'needs_reboot': False}))\n")
        self.run_install(firmware, update)
        self.assertEqual(firmware.failures, {})
        (result,) = firmware.attempts.history(firmware.key(update))
        self.assertEqual(result["outcome"], "succeeded")

    def test_refresh(self):
        firmware, update = self.modem("print(json.dumps({'ok': True, 'refreshed': 1}))\n",
                                      facts={"modem": {"metadata_age_days": 60}})
        self.assertEqual(firmware.offer(update).requirements[0].action, "refresh")
        firmware.loaded = False
        firmware.refresh()
        self.assertTrue(firmware.refreshing)
        self.assertTrue(pump(lambda: not firmware.refreshing and firmware.loaded and not firmware.checking))
        self.assertEqual(self.probe.calls()[-2:], ["refresh", "list"])
        self.assertEqual(firmware.refresh_error, "")

    def test_a_failed_refresh_says_so(self):
        firmware, update = self.modem("print(json.dumps({'ok': False, 'code': 'failed', 'hint': 'offline'}))\n")
        firmware.refresh()
        self.assertTrue(pump(lambda: not firmware.refreshing))
        self.assertIn("couldn’t refresh", firmware.refresh_error)

    def test_control_characters_from_the_device_survive_the_probe(self):
        noisy = dict(MODEM, device="RM520N-GL\r")
        firmware = self.firmware(f"print({listing([noisy])!r})\n")
        firmware.load()
        self.assertTrue(pump(lambda: firmware.loaded))
        self.assertEqual(firmware.problem, "")
        self.assertEqual(len(firmware.updates), 1)


class ProbeOutput(unittest.TestCase):
    def test_last_result_line_wins_and_noise_is_ignored(self):
        text = "\n".join(["garbage", json.dumps({"progress": 0.2}), json.dumps({"ok": False}),
                          json.dumps({"ok": True, "updates": []}), ""])
        self.assertEqual(su._probe_result(text), {"ok": True, "updates": []})
        self.assertIsNone(su._probe_result(""))
        self.assertIsNone(su._probe_result(None))

    def test_probe_rejects_unknown_commands(self):
        from luma_depot import firmware_probe
        self.assertEqual(firmware_probe.main(["flash-everything"]), 2)


if __name__ == "__main__":
    unittest.main()
