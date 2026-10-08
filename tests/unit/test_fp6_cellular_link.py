# SPDX-License-Identifier: Apache-2.0

import importlib.util
import importlib.machinery
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "config/mobile/fp6-physical/overlay/usr/libexec/luma-fp6-cellular-link"

# The device script is imported straight from the overlay, so it needs a `gi`
# to import against.  The stub used to be pushed into sys.modules at module
# scope and left there: unittest discovers alphabetically, so every later test
# in the suite inherited a `gi` with no require_version and failed.  The stub
# now lives only for the lifetime of this module's tests, and any real `gi`
# that was already imported is put back afterwards.
STUBBED_MODULES = ("gi", "gi.repository")
_PREVIOUS_MODULES: dict[str, types.ModuleType | None] = {}
MODULE: types.ModuleType


def _gi_stub() -> tuple[types.ModuleType, types.ModuleType]:
    gi = types.ModuleType("gi")
    repository = types.ModuleType("gi.repository")
    repository.Gio = mock.MagicMock()
    repository.GLib = mock.MagicMock(SOURCE_CONTINUE=True, SOURCE_REMOVE=False)
    gi.repository = repository
    gi.require_version = mock.MagicMock()
    return gi, repository


def setUpModule() -> None:
    global MODULE
    gi, repository = _gi_stub()
    for name, replacement in (("gi", gi), ("gi.repository", repository)):
        _PREVIOUS_MODULES[name] = sys.modules.get(name)
        sys.modules[name] = replacement
    spec = importlib.util.spec_from_loader(
        "luma_fp6_cellular_link",
        importlib.machinery.SourceFileLoader("luma_fp6_cellular_link", str(SCRIPT)),
    )
    module = importlib.util.module_from_spec(spec)
    _PREVIOUS_MODULES[spec.name] = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    MODULE = module


def tearDownModule() -> None:
    for name, previous in _PREVIOUS_MODULES.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    _PREVIOUS_MODULES.clear()


class CellularLinkTests(unittest.TestCase):
    def setUp(self):
        # CellularLink() reads /etc/luma/fp6-cellular-link.conf. Point it at a
        # path that cannot exist so the tests read the same empty configuration
        # on a build host, a developer laptop and an FP6.
        patcher = mock.patch.object(
            MODULE, "CONFIG", Path("/nonexistent/luma/fp6-cellular-link.conf")
        )
        self.config = patcher.start()
        self.addCleanup(patcher.stop)

    def test_values_and_suffix_parse_mmcli_keyvalue(self):
        parsed = MODULE.values(
            "modem.generic.state : connected\nmodem.3gpp.registration-state : home\n"
        )
        self.assertEqual(MODULE.suffix(parsed, ".generic.state"), "connected")
        self.assertEqual(MODULE.suffix(parsed, ".registration-state"), "home")

    def test_apn_validation_rejects_command_syntax(self):
        with mock.patch.object(MODULE, "CONFIG") as config:
            config.read_text.return_value = "APN=wholesale;reboot\n"
            self.assertEqual(MODULE.read_config(), ("", ""))

    def test_cleanup_removes_only_owned_in_memory_connection(self):
        link = MODULE.CellularLink()
        link.active = ("qmapmux0.0", "192.0.0.2", "27")
        calls = []
        with mock.patch.object(MODULE, "run", side_effect=lambda args, **kwargs: calls.append(args) or ""):
            link.cleanup()
        self.assertEqual(
            calls,
            [
                ["nmcli", "connection", "down", MODULE.CONNECTION_NAME],
                ["nmcli", "connection", "delete", MODULE.CONNECTION_NAME],
                ["nmcli", "device", "set", "qmapmux0.0", "managed", "no"],
            ],
        )
        self.assertIsNone(link.active)

    def test_connected_bearer_ignores_default_attach(self):
        link = MODULE.CellularLink()
        link.apn = "fast.t-mobile.com"
        outputs = {
            ("mmcli", "-b", "0", "--output-keyvalue"):
                "bearer.type: default-attach\nbearer.status.connected: yes\n"
                "bearer.properties.apn: fast.t-mobile.com\n",
            ("mmcli", "-b", "1", "--output-keyvalue"):
                "bearer.type: default\nbearer.status.connected: yes\n"
                "bearer.status.interface: qmapmux0.0\n"
                "bearer.properties.apn: fast.t-mobile.com\n"
                "bearer.ipv4-config.address: 192.0.0.2\n"
                "bearer.ipv4-config.prefix: 27\n"
                "bearer.ipv4-config.gateway: 192.0.0.1\n",
        }
        modem_values = {
            "modem.generic.bearers.value[1]": "/org/freedesktop/ModemManager1/Bearer/0",
            "modem.generic.bearers.value[2]": "/org/freedesktop/ModemManager1/Bearer/1",
        }
        with mock.patch.object(
            MODULE, "run", side_effect=lambda args, **_kwargs: outputs[tuple(args)]
        ):
            bearer = link.connected_bearer("0", modem_values)
        self.assertEqual(bearer[0], "1")

    def test_connected_bearer_ignores_ims_without_ipv4_configuration(self):
        link = MODULE.CellularLink()
        link.apn = "fast.t-mobile.com"
        outputs = {
            ("mmcli", "-b", "2", "--output-keyvalue"):
                "bearer.type: default\nbearer.status.connected: yes\n"
                "bearer.status.interface: qmapmux0.0\n"
                "bearer.properties.apn: ims\n"
                "bearer.ipv4-config.address: --\n"
                "bearer.ipv4-config.prefix: --\n"
                "bearer.ipv4-config.gateway: --\n",
            # Same APN as the data bearer below, so only the missing IPv4
            # configuration can be what rules this one out.
            ("mmcli", "-b", "5", "--output-keyvalue"):
                "bearer.type: default\nbearer.status.connected: yes\n"
                "bearer.status.interface: qmapmux0.2\n"
                "bearer.properties.apn: fast.t-mobile.com\n"
                "bearer.ipv4-config.address: --\n"
                "bearer.ipv4-config.prefix: --\n"
                "bearer.ipv4-config.gateway: --\n",
            ("mmcli", "-b", "8", "--output-keyvalue"):
                "bearer.type: default\nbearer.status.connected: yes\n"
                "bearer.status.interface: qmapmux0.1\n"
                "bearer.properties.apn: fast.t-mobile.com\n"
                "bearer.ipv4-config.address: 192.0.0.2\n"
                "bearer.ipv4-config.prefix: 27\n"
                "bearer.ipv4-config.gateway: 192.0.0.1\n",
        }
        modem_values = {
            "modem.generic.bearers.value[1]": "/org/freedesktop/ModemManager1/Bearer/2",
            "modem.generic.bearers.value[2]": "/org/freedesktop/ModemManager1/Bearer/5",
            "modem.generic.bearers.value[3]": "/org/freedesktop/ModemManager1/Bearer/8",
        }
        with mock.patch.object(
            MODULE, "run", side_effect=lambda args, **_kwargs: outputs[tuple(args)]
        ):
            bearer = link.connected_bearer("0", modem_values)
        self.assertEqual(bearer[0], "8")

    def connect_calls_for_configured_apn(self, apn):
        """Return the mmcli calls made when no usable data bearer exists yet.

        The modem reports a default-attach bearer on fast.t-mobile.com; `apn`
        is what Luma has been configured with, "" meaning nothing configured.
        """
        link = MODULE.CellularLink()
        link.apn = apn
        link.last_connect_action = 0
        calls = []
        outputs = {
            ("mmcli", "-b", "0", "--output-keyvalue"):
                "bearer.type: default-attach\nbearer.status.connected: yes\n"
                "bearer.properties.apn: fast.t-mobile.com\n",
        }
        modem_values = {
            "modem.generic.bearers.value[1]": "/org/freedesktop/ModemManager1/Bearer/0",
        }

        def fake_run(args, **_kwargs):
            calls.append(args)
            return outputs.get(tuple(args), "")

        with mock.patch.object(MODULE, "run", side_effect=fake_run), mock.patch.object(
            MODULE.time, "monotonic", return_value=1000
        ):
            self.assertIsNone(link.connected_bearer("0", modem_values))
        return calls

    def test_attach_apn_is_the_fallback_when_nothing_is_configured(self):
        self.assertIn(
            [
                "mmcli", "-m", "0",
                "--simple-connect=apn=fast.t-mobile.com,ip-type=ipv4",
            ],
            self.connect_calls_for_configured_apn(""),
        )

    def test_configured_apn_overrides_the_modem_reported_attach_apn(self):
        # A carrier can expose a generic parent-network attach APN while the
        # MVNO needs its own for user traffic, so an explicit Luma setting
        # wins.  Asserted here because the reverse precedence once shipped and
        # is not observable from the outside: nothing in the mmcli output says
        # the carrier rejected an APN.
        calls = self.connect_calls_for_configured_apn("wholesale")
        self.assertIn(
            ["mmcli", "-m", "0", "--simple-connect=apn=wholesale,ip-type=ipv4"],
            calls,
        )
        self.assertNotIn(
            [
                "mmcli", "-m", "0",
                "--simple-connect=apn=fast.t-mobile.com,ip-type=ipv4",
            ],
            calls,
        )

    def test_dbus_signal_coalesces_reconcile_work(self):
        link = MODULE.CellularLink()
        with mock.patch.object(MODULE.GLib, "timeout_add", return_value=73) as timeout_add:
            link.changed()
            link.changed()
        timeout_add.assert_called_once_with(MODULE.RECONCILE_DELAY_MS, link.reconcile)


if __name__ == "__main__":
    unittest.main()
