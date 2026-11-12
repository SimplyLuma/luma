# SPDX-License-Identifier: Apache-2.0
"""Fairphone 6 cellular link: mobile data follows the person's switch in
Settings (NetworkManager's WWAN state and the per-SIM Mobile Data profile),
is on by default, and turning it off leaves calls and texts' bearers alone."""
import importlib.machinery
import importlib.util
import subprocess
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HELPER = ROOT / "src/luma-fp6-platform/root/usr/libexec/luma-fp6-cellular-link"
ICCID = "test-sim-a"
DATA_UUID = "11111111-2222-3333-4444-555555555555"
OTHER_UUID = "66666666-7777-8888-9999-000000000000"


def load_helper():
    # The helper's GLib main loop isn't under test; nothing here touches D-Bus.
    gi = types.ModuleType("gi")
    repository = types.ModuleType("gi.repository")
    repository.Gio = types.SimpleNamespace()
    repository.GLib = types.SimpleNamespace(SOURCE_REMOVE=False, SOURCE_CONTINUE=True)
    gi.repository = repository
    saved = {name: sys.modules.get(name) for name in ("gi", "gi.repository")}
    sys.modules.update({"gi": gi, "gi.repository": repository})
    try:
        loader = importlib.machinery.SourceFileLoader("luma_fp6_cellular_link", str(HELPER))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
    finally:
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
    return module


helper = load_helper()


class FakeSystem:
    """mmcli and nmcli as a registered FP6 with an IMS bearer, a data bearer
    and whatever NetworkManager state a test sets."""

    def __init__(self):
        self.radio = "enabled"
        self.profiles = {}  # uuid -> (sim-id, autoconnect)
        self.data_connected = "yes"
        self.calls = []
        self.fail_nmcli = False

    def run(self, arguments, *, check=True):
        self.calls.append(tuple(arguments))
        command = tuple(arguments)
        if command[0] == "nmcli" and self.fail_nmcli:
            raise subprocess.CalledProcessError(8, list(arguments))
        if command == ("mmcli", "-L"):
            return "    /org/freedesktop/ModemManager1/Modem/0 [QUALCOMM INCORPORATED] 0\n"
        if command == ("mmcli", "-m", "0", "--output-keyvalue"):
            return "\n".join([
                "modem.generic.state : registered",
                "modem.generic.sim : /org/freedesktop/ModemManager1/SIM/0",
                "modem.generic.bearers.value[1] : /org/freedesktop/ModemManager1/Bearer/1",
                "modem.generic.bearers.value[2] : /org/freedesktop/ModemManager1/Bearer/2",
                "modem.3gpp.registration-state : home",
            ])
        if command == ("mmcli", "-i", "0", "--output-keyvalue"):
            return f"sim.properties.iccid : {ICCID}\n"
        if command == ("mmcli", "-b", "1", "--output-keyvalue"):
            return "\n".join([
                "bearer.type : default",
                "bearer.properties.apn : ims",
                "bearer.status.connected : yes",
                "bearer.status.interface : qmapmux0.1",
            ])
        if command == ("mmcli", "-b", "2", "--output-keyvalue"):
            return "\n".join([
                "bearer.type : default",
                "bearer.properties.apn : fast.example",
                f"bearer.status.connected : {self.data_connected}",
                "bearer.status.interface : --",
            ])
        if command[:2] == ("mmcli", "-b") and command[3:] == ("--disconnect",):
            return ""
        if command[:2] == ("mmcli", "-m") and command[3].startswith("--simple-connect="):
            return ""
        if command == ("nmcli", "--terse", "--fields", "WWAN", "radio"):
            return self.radio + "\n"
        if command == ("nmcli", "--terse", "--escape", "no", "--fields", "UUID,TYPE", "connection", "show"):
            rows = [f"{uuid}:gsm" for uuid in self.profiles]
            rows.append("abcdefab-0000-0000-0000-000000000000:802-11-wireless")
            return "\n".join(rows) + "\n"
        if command[:7] == ("nmcli", "--terse", "--escape", "no", "--fields",
                           "connection.autoconnect,gsm.sim-id", "connection"):
            sim_id, autoconnect = self.profiles[command[-1]]
            return f"connection.autoconnect:{autoconnect}\ngsm.sim-id:{sim_id}\n"
        if command[:6] == ("nmcli", "--terse", "--fields", "UUID", "connection", "show"):
            return ""
        raise AssertionError(f"unexpected command {arguments}")


class CellularDataSwitch(unittest.TestCase):
    def setUp(self):
        self.system = FakeSystem()
        self.original_run = helper.run
        helper.run = self.system.run
        self.link = helper.CellularLink()
        self.link.apn = "fast.example"
        self.link.allowed_modes = ""
        self.modem = helper.values(self.system.run(["mmcli", "-m", "0", "--output-keyvalue"]))

    def tearDown(self):
        helper.run = self.original_run

    def disconnected(self):
        return [c for c in self.system.calls if c[-1] == "--disconnect"]

    def connects(self):
        return [c for c in self.system.calls if len(c) > 3 and c[3].startswith("--simple-connect=")]

    def test_on_by_default(self):
        self.assertTrue(self.link.data_enabled(self.modem))
        self.system.data_connected = "no"
        self.link.reconcile()
        self.assertEqual(self.connects(), [("mmcli", "-m", "0", "--simple-connect=apn=fast.example,ip-type=ipv4")])
        self.assertEqual(self.disconnected(), [])

    def test_wwan_off_stops_only_the_data_bearer(self):
        self.system.radio = "disabled"
        self.assertFalse(self.link.data_enabled(self.modem))
        self.link.reconcile()
        self.assertEqual(self.disconnected(), [("mmcli", "-b", "2", "--disconnect")])
        self.assertEqual(self.connects(), [])

    def test_mobile_data_off_for_this_sim(self):
        self.system.profiles = {DATA_UUID: (ICCID, "no")}
        self.assertFalse(self.link.data_enabled(self.modem))
        self.link.reconcile()
        self.assertEqual(self.disconnected(), [("mmcli", "-b", "2", "--disconnect")])
        self.assertEqual(self.connects(), [])

    def test_mobile_data_on_for_this_sim(self):
        self.system.profiles = {DATA_UUID: (ICCID, "no"), OTHER_UUID: (ICCID, "yes")}
        self.assertTrue(self.link.data_enabled(self.modem))

    def test_another_sims_profile_does_not_count(self):
        self.system.profiles = {OTHER_UUID: ("test-sim-b", "no")}
        self.assertTrue(self.link.data_enabled(self.modem))

    def test_unreadable_switch_keeps_data_on(self):
        self.system.fail_nmcli = True
        self.assertTrue(self.link.data_enabled(self.modem))

    def test_already_off_disconnects_nothing(self):
        self.system.radio = "disabled"
        self.system.data_connected = "no"
        self.link.reconcile()
        self.assertEqual(self.disconnected(), [])

    def test_back_on_connects_without_waiting(self):
        self.system.radio = "disabled"
        self.link.last_connect_action = helper.time.monotonic()
        self.link.reconcile()
        self.system.radio = "enabled"
        self.system.data_connected = "no"
        self.link.reconcile()
        self.assertEqual(len(self.connects()), 1)


if __name__ == "__main__":
    unittest.main()
