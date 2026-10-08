#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/prairie-core"))

from prairie_apps.phone_backend import (  # noqa: E402
    CallPhase,
    CallRecord,
    CallSession,
    CallStore,
    FavouriteStore,
    ImsVoiceTransport,
    IncomingCallMonitor,
    ModemVoiceTransport,
    audio_input_muted,
    audio_output_route,
    classify_call_outcome,
    format_call_duration,
    format_phone_number,
    group_recent_calls,
    inspect_phone_capability,
    set_audio_input_muted,
    set_audio_output_route,
)


class PhoneCapabilityTests(unittest.TestCase):
    def test_connected_timer_starts_only_on_native_active_state(self):
        session = CallSession.preparing("913-555-0100", now=100).with_call_id("call-1")
        self.assertEqual(session.phase, CallPhase.DIALLING)
        self.assertEqual(session.elapsed(now=120), 0)
        session = session.native_state("ringing", now=121)
        self.assertEqual(session.phase, CallPhase.RINGING_OUTGOING)
        self.assertEqual(session.elapsed(now=140), 0)
        session = session.native_state("active", "accepted", now=150)
        self.assertEqual(session.phase, CallPhase.ACTIVE)
        self.assertEqual(session.elapsed(now=172), 22)

    def test_repeated_end_is_idempotent(self):
        session = CallSession.preparing("9135550100", now=100).with_call_id("call-1")
        ending = session.ending()
        self.assertIs(ending.ending(), ending)
        self.assertTrue(ending.input_locked)

    def test_local_outgoing_hangup_is_not_a_network_failure(self):
        self.assertEqual(
            classify_call_outcome(
                direction="outgoing",
                connected_at=0,
                reason="local-hangup",
            ),
            "cancelled",
        )
        self.assertEqual(
            classify_call_outcome(
                direction="outgoing",
                connected_at=150,
                reason="local-hangup",
            ),
            "completed",
        )

    def test_real_outgoing_network_failure_remains_failed(self):
        self.assertEqual(
            classify_call_outcome(
                direction="outgoing",
                connected_at=0,
                reason="error",
            ),
            "failed",
        )

    def test_recents_group_only_nearby_missed_calls_from_same_number(self):
        records = (
            CallRecord("a", "+19135550100", "incoming", 5000, 0, "missed"),
            CallRecord("b", "+19135550100", "incoming", 4900, 0, "missed"),
            CallRecord("c", "+19135550101", "incoming", 4800, 0, "missed"),
            CallRecord("d", "+19135550100", "outgoing", 4700, 30, "completed"),
        )
        groups = group_recent_calls(records)
        self.assertEqual(tuple(group.count for group in groups), (2, 1, 1))
        self.assertEqual(groups[0].outcome, "missed")
        self.assertEqual(groups[-1].duration, 30)

    def test_display_formatting_is_conservative(self):
        self.assertEqual(format_phone_number("9135550100"), "(913) 555-0100")
        self.assertEqual(format_phone_number("+19135550100"), "+1 913 555 0100")
        self.assertEqual(format_phone_number("*123#"), "*123#")
        self.assertEqual(format_call_duration(3723), "1:02:03")

    def test_call_history_is_idempotent_by_native_call_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CallStore(Path(temporary) / "calls.db")
            first = store.add(
                "+19135550100",
                direction="outgoing",
                started=100,
                duration=20,
                outcome="completed",
                uid="native-call-1",
            )
            second = store.add(
                "+19135550100",
                direction="outgoing",
                started=100,
                duration=20,
                outcome="completed",
                uid="native-call-1",
            )
            self.assertEqual(first.uid, second.uid)
            self.assertEqual(len(store.list()), 1)
            store.close()

    def test_pipewire_mute_state_is_read_and_written_authoritatively(self):
        calls = []
        self.assertTrue(audio_input_muted(lambda arguments: "Volume: 0.50 [MUTED]"))
        self.assertFalse(audio_input_muted(lambda arguments: "Volume: 0.50"))
        set_audio_input_muted(True, lambda arguments: calls.append(arguments) or "")
        set_audio_input_muted(False, lambda arguments: calls.append(arguments) or "")
        self.assertEqual(
            calls,
            [
                ["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "1"],
                ["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "0"],
            ],
        )

    def test_native_audio_output_route_is_read_and_written(self):
        calls = []
        self.assertEqual(
            audio_output_route(lambda arguments: 's "speaker"\n'), "speaker"
        )
        self.assertEqual(
            audio_output_route(lambda arguments: 's "earpiece"\n'), "earpiece"
        )
        self.assertEqual(
            audio_output_route(lambda arguments: 's "unexpected"\n'), "unknown"
        )
        set_audio_output_route(
            "speaker", lambda arguments: calls.append(arguments) or ""
        )
        self.assertEqual(calls[-1][-3:], ["SetRoute", "s", "speaker"])
        with self.assertRaises(ValueError):
            set_audio_output_route("arbitrary", lambda _arguments: "")

    def test_favourite_order_persists_without_duplicates(self):
        with tempfile.TemporaryDirectory() as root:
            store = FavouriteStore(Path(root) / "favourites.json")
            store.set(("one", "two", "one"))
            self.assertEqual(store.list(), ("one", "two"))
            self.assertFalse(store.toggle("one"))
            self.assertEqual(store.list(), ("two",))

    def test_ims_transport_normalizes_and_uses_dbus_methods(self):
        class FakeIms(ImsVoiceTransport):
            def __init__(self):
                self.calls = []
            def _call(self, method, signature, values):
                self.calls.append((method, signature, values))
                return ("call-7",) if method == "Dial" else ()

        transport = FakeIms()
        call_id = transport.dial("+1 312 555 0102")
        transport.hangup(call_id)
        transport.accept(call_id)
        transport.send_dtmf(call_id, "12#")
        self.assertEqual(call_id, "call-7")
        self.assertEqual(
            transport.calls,
            [
                ("Dial", "(s)", ("+13125550102",)),
                ("HangUp", "(s)", ("call-7",)),
                ("Accept", "(s)", ("call-7",)),
                ("SendDtmf", "(ss)", ("call-7", "12#")),
            ],
        )

    def test_monitor_reports_all_calls_but_presents_only_incoming(self):
        added = []
        incoming = []
        monitor = IncomingCallMonitor(
            on_added=lambda *values: added.append(values),
            on_incoming=lambda *values: incoming.append(values),
        )

        class Parameters:
            def __init__(self, direction):
                self.direction = direction

            def unpack(self):
                return "call-" + self.direction, {"direction": self.direction, "number": "+15550101010"}

        monitor._handle_added(None, None, None, None, None, Parameters("outgoing"))
        monitor._handle_added(None, None, None, None, None, Parameters("incoming"))
        self.assertEqual(len(added), 2)
        self.assertEqual(incoming, [("call-incoming", "+15550101010")])
        # The owner monitor deduplicates repeated signals for one call;
        # two independent calls above must therefore have distinct IDs.
        monitor._handle_added(None, None, None, None, None, Parameters("incoming"))
        self.assertEqual(len(added), 2)
        self.assertEqual(incoming, [("call-incoming", "+15550101010")])

    def test_private_call_history(self):
        with tempfile.TemporaryDirectory() as root:
            store=CallStore(Path(root)/"calls.db"); record=store.add("+1 312 555 0102",direction="outgoing",started=10,duration=22)
            self.assertEqual(store.list()[0],record); self.assertEqual(store.list()[0].address,"+13125550102"); store.close()

    @mock.patch("prairie_apps.phone_backend.inspect_phone_capability")
    def test_voice_adapter_creates_and_starts_call(self,inspect:mock.Mock):
        from prairie_apps.phone_backend import PhoneCapability
        inspect.return_value=PhoneCapability(True,"Ready",1,True); calls=[]
        def runner(args):
            calls.append(args)
            if args==["mmcli","-L"]: return "/Modem/0"
            if any(a.startswith("--voice-create-call=") for a in args): return "/Call/4"
            return "ok"
        path=ModemVoiceTransport(runner).dial("+1 312 555 0102")
        self.assertEqual(path,"/org/freedesktop/ModemManager1/Call/4"); self.assertEqual(calls[-1],["mmcli","-o","4","--start"])
    @mock.patch("prairie_apps.phone_backend.shutil.which", return_value=None)
    def test_missing_modem_tools_is_unavailable(self, _which: mock.Mock) -> None:
        result = inspect_phone_capability()
        self.assertFalse(result.available)
        self.assertTrue(result.no_modem)
        self.assertNotIn("ModemManager", result.reason)
        self.assertIn("Connect", result.reason)

    @mock.patch("prairie_apps.phone_backend.shutil.which", return_value="/usr/bin/tool")
    @mock.patch("prairie_apps.phone_backend._run")
    def test_sim_missing_disables_call(self, run: mock.Mock, _which: mock.Mock) -> None:
        run.side_effect = ["/Modem/0", "modem.generic.state-failed-reason: sim-missing"]
        result = inspect_phone_capability()
        self.assertFalse(result.available)
        self.assertIn("SIM", result.reason)

    @mock.patch("prairie_apps.phone_backend.shutil.which", return_value="/usr/bin/tool")
    @mock.patch("prairie_apps.phone_backend._run")
    def test_audio_route_is_required(self, run: mock.Mock, _which: mock.Mock) -> None:
        run.side_effect = ["/Modem/0", "modem.generic.state: registered\nmodem.3gpp.registration-state: home", "Audio\nSinks:\n │  \nSources:\n │  \n"]
        result = inspect_phone_capability()
        self.assertFalse(result.available)
        self.assertIn("audio", result.reason)

    @mock.patch("prairie_apps.phone_backend.shutil.which", return_value="/usr/bin/tool")
    @mock.patch("prairie_apps.phone_backend._run")
    def test_bidirectional_audio_completes_gate(
        self, run: mock.Mock, _which: mock.Mock
    ) -> None:
        run.side_effect = [
            "/Modem/0",
            "modem.generic.state: connected\nmodem.3gpp.registration-state: home",
            "Audio\nSinks:\n │  * 42. Speaker\nSources:\n │  * 43. Microphone\nFilters:",
        ]
        result = inspect_phone_capability()
        self.assertTrue(result.available)
        self.assertTrue(result.has_audio_route)


if __name__ == "__main__":
    unittest.main()
