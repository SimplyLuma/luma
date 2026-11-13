# SPDX-License-Identifier: Apache-2.0
"""Fairphone 6 cellular link: the data bearer becomes a NetworkManager link for
whichever IP families the network granted, and a failed command says which
mmcli option failed and why."""
import importlib.machinery
import importlib.util
import io
import json
import subprocess
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HELPER = ROOT / "src/luma-fp6-platform/root/usr/libexec/luma-fp6-cellular-link"


def load_helper():
    gi = types.ModuleType("gi")
    repository = types.ModuleType("gi.repository")
    repository.Gio = types.SimpleNamespace()
    repository.GLib = types.SimpleNamespace(SOURCE_REMOVE=False, SOURCE_CONTINUE=True)
    gi.repository = repository
    saved = {name: sys.modules.get(name) for name in ("gi", "gi.repository")}
    sys.modules.update({"gi": gi, "gi.repository": repository})
    try:
        loader = importlib.machinery.SourceFileLoader("luma_fp6_cellular_link_bearer", str(HELPER))
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

IPV4 = [
    "bearer.ipv4-config.method : static",
    "bearer.ipv4-config.address : 192.0.0.2",
    "bearer.ipv4-config.prefix : 27",
    "bearer.ipv4-config.gateway : 192.0.0.1",
    "bearer.ipv4-config.dns.length : 2",
    "bearer.ipv4-config.dns.value[1] : 10.177.0.34",
    "bearer.ipv4-config.dns.value[2] : 10.177.0.210",
    "bearer.ipv4-config.mtu : 1500",
]
IPV6 = [
    "bearer.ipv6-config.method : static",
    "bearer.ipv6-config.address : 2607:fb90:1:2::3",
    "bearer.ipv6-config.prefix : 64",
    "bearer.ipv6-config.gateway : 2607:fb90:1:2::4",
    "bearer.ipv6-config.dns.length : 2",
    "bearer.ipv6-config.dns.value[1] : fd00:976a::9",
    "bearer.ipv6-config.dns.value[2] : fd00:976a::10",
    "bearer.ipv6-config.mtu : 1500",
]
NO_IPV4 = [
    "bearer.ipv4-config.method : unknown",
    "bearer.ipv4-config.address : --",
    "bearer.ipv4-config.prefix : 0",
    "bearer.ipv4-config.gateway : --",
]
NO_IPV6 = [
    "bearer.ipv6-config.method : unknown",
    "bearer.ipv6-config.address : --",
    "bearer.ipv6-config.prefix : 0",
    "bearer.ipv6-config.gateway : --",
]


class FakeSystem:
    """A registered FP6: IMS bearer on qmapmux0.0, the data bearer on
    qmapmux0.1 with whatever IP configuration a test gives it."""

    def __init__(self, data_ip):
        self.data_ip = data_ip
        self.calls = []
        self.profile = None  # (uuid, interface) once added
        self.connect_error = None

    def run(self, arguments, *, check=True):
        command = tuple(arguments)
        self.calls.append(command)
        if command == ("mmcli", "-L"):
            return "    /org/freedesktop/ModemManager1/Modem/0 [QUALCOMM INCORPORATED] 0\n"
        if command == ("mmcli", "-m", "0", "--output-keyvalue"):
            return "\n".join([
                "modem.generic.state : connected",
                "modem.generic.sim : /org/freedesktop/ModemManager1/SIM/0",
                "modem.generic.bearers.value[1] : /org/freedesktop/ModemManager1/Bearer/0",
                "modem.generic.bearers.value[2] : /org/freedesktop/ModemManager1/Bearer/1",
                "modem.generic.bearers.value[3] : /org/freedesktop/ModemManager1/Bearer/2",
                "modem.3gpp.registration-state : home",
            ])
        if command == ("mmcli", "-i", "0", "--output-keyvalue"):
            return "sim.properties.iccid : test-sim\n"
        if command == ("mmcli", "-b", "0", "--output-keyvalue"):
            return "bearer.type : default-attach\nbearer.properties.apn : fast.t-mobile.com\n"
        if command == ("mmcli", "-b", "1", "--output-keyvalue"):
            return "\n".join([
                "bearer.type : default",
                "bearer.properties.apn : ims",
                "bearer.status.connected : yes",
                "bearer.status.interface : qmapmux0.0",
                *NO_IPV4, *IPV6,
            ])
        if command == ("mmcli", "-b", "2", "--output-keyvalue"):
            return "\n".join([
                "bearer.type : default",
                "bearer.properties.apn : fast.t-mobile.com",
                "bearer.status.connected : yes",
                "bearer.status.interface : qmapmux0.1",
                *self.data_ip,
            ])
        if command[:3] == ("mmcli", "-m", "0") and command[3].startswith("--simple-connect="):
            if self.connect_error:
                raise subprocess.CalledProcessError(1, list(arguments), "", self.connect_error)
            return ""
        if command == ("nmcli", "--terse", "--fields", "WWAN", "radio"):
            return "enabled\n"
        if command == ("nmcli", "--terse", "--escape", "no", "--fields", "UUID,TYPE", "connection", "show"):
            return ""
        if command == ("nmcli", "--terse", "--escape", "no", "--fields", "TYPE,STATE",
                       "connection", "show", "--active"):
            return "loopback:activated\n"
        if command == ("nmcli", "--terse", "--escape", "no", "--fields", "NAME,UUID", "connection", "show"):
            return f"{helper.CONNECTION_NAME}:{self.profile[0]}\n" if self.profile else ""
        if command == ("nmcli", "--terse", "--fields", "UUID", "connection", "show"):
            return f"{self.profile[0]}\n" if self.profile else ""
        if command[:3] == ("nmcli", "connection", "add"):
            self.profile = (command[command.index("connection.uuid") + 1], command[command.index("ifname") + 1])
            return ""
        if command[:3] == ("nmcli", "connection", "up"):
            return ""
        if command == ("nmcli", "--terse", "--escape", "no", "--fields", "NAME,UUID,DEVICE",
                       "connection", "show", "--active"):
            return f"{helper.CONNECTION_NAME}:{self.profile[0]}:{self.profile[1]}\n" if self.profile else ""
        if command[:5] == ("ip", "-j", "link", "show", "dev"):
            return json.dumps([{"ifname": command[5], "ifindex": 9, "flags": ["UP"], "mtu": 1500}])
        if command[:3] in {("ip", "link", "set"), ("nmcli", "device", "set")}:
            return ""
        raise AssertionError(f"unexpected command {arguments}")

    def added(self):
        return next(c for c in self.calls if c[:3] == ("nmcli", "connection", "add"))


def setting(command, name):
    return command[command.index(name) + 1] if name in command else None


class BearerLink(unittest.TestCase):
    def reconcile(self, data_ip, connect_error=None):
        system = FakeSystem(data_ip)
        system.connect_error = connect_error
        original = helper.run
        helper.run = system.run
        self.addCleanup(setattr, helper, "run", original)
        link = helper.CellularLink()
        link.apn = "fast.t-mobile.com"
        link.allowed_modes = ""
        output = io.StringIO()
        with redirect_stdout(output):
            link.reconcile()
        return system, link, output.getvalue()

    def test_dual_stack(self):
        system, link, log = self.reconcile(IPV4 + IPV6)
        self.assertEqual(log, "")
        add = system.added()
        self.assertEqual(setting(add, "ifname"), "qmapmux0.1")
        self.assertEqual(setting(add, "ipv4.method"), "manual")
        self.assertEqual(setting(add, "ipv4.addresses"), "192.0.0.2/27")
        self.assertEqual(setting(add, "ipv4.gateway"), "192.0.0.1")
        self.assertEqual(setting(add, "ipv4.dns"), "10.177.0.210,10.177.0.34")
        self.assertEqual(setting(add, "ipv6.method"), "manual")
        self.assertEqual(setting(add, "ipv6.addresses"), "2607:fb90:1:2::3/64")
        self.assertEqual(setting(add, "ipv6.routes"), "::/0")
        self.assertEqual(setting(add, "ipv6.dns"), "fd00:976a::10,fd00:976a::9")
        self.assertIsNotNone(link.active_uuid)
        # The IMS bearer's link is never touched.
        self.assertFalse(any("qmapmux0.0" in c for c in system.calls))

    def test_ipv6_only(self):
        system, link, log = self.reconcile(NO_IPV4 + IPV6)
        self.assertEqual(log, "")
        add = system.added()
        self.assertEqual(setting(add, "ipv4.method"), "disabled")
        self.assertIsNone(setting(add, "ipv4.addresses"))
        self.assertEqual(setting(add, "ipv6.method"), "manual")
        self.assertEqual(setting(add, "ipv6.addresses"), "2607:fb90:1:2::3/64")

    def test_ipv4_only(self):
        system, link, log = self.reconcile(IPV4 + NO_IPV6)
        add = system.added()
        self.assertEqual(setting(add, "ipv4.addresses"), "192.0.0.2/27")
        self.assertEqual(setting(add, "ipv6.method"), "disabled")

    def test_no_usable_configuration_connects_instead(self):
        system, link, log = self.reconcile(NO_IPV4 + NO_IPV6)
        self.assertFalse(any(c[:3] == ("nmcli", "connection", "add") for c in system.calls))
        self.assertIn(("mmcli", "-m", "0", "--simple-connect=apn=fast.t-mobile.com,ip-type=ipv4v6"), system.calls)

    def test_failed_mmcli_names_option_and_error(self):
        system, link, log = self.reconcile(
            NO_IPV4 + NO_IPV6,
            connect_error="error: couldn't connect the modem: 'GDBus.Error:org.freedesktop.ModemManager1."
                          "Error.MobileEquipment.GprsMissingOrUnknownApn: Missing or unknown APN 310260123456789'\n",
        )
        self.assertIn("command=mmcli action=-m operation=0 option=--simple-connect returncode=1 error=", log)
        self.assertIn("GprsMissingOrUnknownApn", log)
        self.assertNotIn("123456789", log)


if __name__ == "__main__":
    unittest.main()
