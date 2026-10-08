# SPDX-License-Identifier: Apache-2.0
"""Call producer acceptance: what counts as a call, and what never does.

PipeWire is stubbed throughout.  Every graph here is a captured ``pw-dump``
fixture shaped from a real ThinkPad X1 Gen 10 session, so the qualification
rules are exercised against the property names the daemon will actually meet.
The published payload is asserted against the Semantic Broker's own
``validation.live_extension`` rather than a copy of the schema.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
BROKER = ROOT / "src/luma-platform/broker"
CALLS = ROOT / "src/luma-platform/calls"
FIXTURES = ROOT / "tests/fixtures/calls"
for path in (BROKER, CALLS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from luma_semantic_broker.core import BrokerCore, BrokerError  # noqa: E402
from luma_semantic_broker.model import Identity, IdentityStrength  # noqa: E402
from luma_semantic_broker.policy import RateLimiter  # noqa: E402
from luma_semantic_broker.validation import (  # noqa: E402
    ValidationError,
    live_extension as validate_live_extension,
)

from luma_calls import continuity, control, extension, nodes, qualify  # noqa: E402


NOW = datetime(2026, 9, 11, 17, 30, tzinfo=UTC)

#: The fixtures carry this in ``media.name``/``media.title``.  It stands in for
#: a call title or a participant and must never leave the PipeWire graph.
SENSITIVE = "Priya Raman"


def graph(name: str) -> dict[int, nodes.Node]:
    return nodes.graph_from_dump(
        json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    )


class GraphTests(unittest.TestCase):
    def test_dump_is_reduced_to_nodes_with_their_mute_state(self) -> None:
        current = graph("pw-dump-call.json")
        self.assertIn(121, current)
        self.assertEqual(current[121].media_class, "Stream/Input/Audio")
        self.assertEqual(current[121].binary, "Discord")
        self.assertFalse(current[121].muted)
        self.assertTrue(current[121].mutable)
        self.assertTrue(graph("pw-dump-call-muted.json")[121].muted)

    def test_monitor_frames_add_and_remove_nodes(self) -> None:
        decoder = nodes.FrameDecoder()
        current: dict[int, nodes.Node] = {}
        text = (FIXTURES / "pw-monitor.txt").read_text(encoding="utf-8")
        seen = []
        # Feed the transcript in small chunks: a frame split across two reads
        # must still decode exactly once.
        for start in range(0, len(text), 97):
            for frame in decoder.feed(text[start:start + 97]):
                nodes.apply_frame(current, frame)
                seen.append(121 in current)
        self.assertEqual(seen[0], False)
        self.assertTrue(any(seen))
        self.assertEqual(seen[-1], False)
        self.assertNotIn(121, current)

    def test_a_change_frame_merges_onto_the_known_node(self) -> None:
        current = graph("pw-dump-call.json")
        nodes.apply_frame(
            current,
            [{"id": 121, "info": {"change-mask": ["params"], "params": {
                "Props": [{"mute": True}]}}}],
        )
        self.assertTrue(current[121].muted)
        self.assertEqual(current[121].binary, "Discord")
        self.assertEqual(current[121].media_class, "Stream/Input/Audio")


class QualificationTests(unittest.TestCase):
    def observe(self, name: str, *, seconds: float = 5.0, tracker=None):
        tracker = tracker or qualify.CallTracker()
        current = graph(name)
        tracker.observe(current, NOW)
        return tracker.observe(current, NOW + timedelta(seconds=seconds))

    def test_a_two_way_capture_stream_is_a_call(self) -> None:
        call = self.observe("pw-dump-call.json")
        self.assertIsNotNone(call)
        self.assertEqual(call.audio_node_id, 121)
        self.assertEqual(call.video_node_id, 123)
        self.assertEqual(call.binary, "Discord")
        self.assertTrue(call.camera_mutable)

    def test_an_open_application_with_no_capture_stream_is_not_a_call(self) -> None:
        # The real machine, verified: three Discord PipeWire clients and no
        # capture stream at all, because Discord is open but not in a call.
        self.assertIsNone(self.observe("pw-dump-idle.json"))

    def test_notification_probes_and_plumbing_are_never_calls(self) -> None:
        self.assertIsNone(self.observe("pw-dump-noise.json"))
        current = graph("pw-dump-noise.json")
        # Each exclusion is individually load-bearing.
        self.assertFalse(qualify.is_capture_candidate(current[118]))  # shell meter
        self.assertFalse(qualify.is_capture_candidate(current[120]))  # bluez loopback
        self.assertFalse(qualify.is_capture_candidate(current[130]))  # no app identity
        self.assertFalse(qualify.is_capture_candidate(current[121]))  # role=Notification
        self.assertEqual(qualify.capture_candidates(current), [])

    def test_capture_without_playback_is_a_recorder_not_a_call(self) -> None:
        self.assertIsNone(self.observe("pw-dump-recorder.json"))

    def test_corked_capture_is_not_a_call(self) -> None:
        self.assertIsNone(self.observe("pw-dump-inactive.json"))

    def test_a_short_lived_stream_is_debounced_away(self) -> None:
        tracker = qualify.CallTracker()
        calling = graph("pw-dump-call.json")
        self.assertIsNone(tracker.observe(calling, NOW))
        self.assertIsNone(tracker.observe(calling, NOW + timedelta(seconds=1.5)))
        # The probe closes before it ever qualifies, so nothing is published
        # and the stream's credit is discarded with it.
        self.assertIsNone(
            tracker.observe(graph("pw-dump-idle.json"), NOW + timedelta(seconds=1.9))
        )
        # A real call starting afterwards must serve the full window itself.
        self.assertIsNone(tracker.observe(calling, NOW + timedelta(seconds=2.0)))
        self.assertIsNone(tracker.observe(calling, NOW + timedelta(seconds=3.9)))
        self.assertIsNotNone(tracker.observe(calling, NOW + timedelta(seconds=4.0)))

    def test_a_returning_stream_must_earn_the_debounce_again(self) -> None:
        tracker = qualify.CallTracker()
        calling = graph("pw-dump-call.json")
        tracker.observe(calling, NOW)
        self.assertIsNotNone(tracker.observe(calling, NOW + timedelta(seconds=3)))
        self.assertIsNone(tracker.observe(graph("pw-dump-idle.json"), NOW + timedelta(seconds=4)))
        self.assertIsNone(tracker.observe(calling, NOW + timedelta(seconds=5)))
        self.assertIsNotNone(tracker.observe(calling, NOW + timedelta(seconds=7.5)))

    def test_a_vanished_stream_unpublishes_immediately(self) -> None:
        tracker = qualify.CallTracker()
        calling = graph("pw-dump-call.json")
        tracker.observe(calling, NOW)
        self.assertIsNotNone(tracker.observe(calling, NOW + timedelta(seconds=3)))
        self.assertIsNone(
            tracker.observe(graph("pw-dump-idle.json"), NOW + timedelta(seconds=3.1))
        )


class DurationTests(unittest.TestCase):
    def test_duration_formats_as_a_call_timer(self) -> None:
        self.assertEqual(extension.format_duration(0), "00:00")
        self.assertEqual(extension.format_duration(7.9), "00:07")
        self.assertEqual(extension.format_duration(65), "01:05")
        self.assertEqual(extension.format_duration(599), "09:59")
        self.assertEqual(extension.format_duration(3600), "1:00:00")
        self.assertEqual(extension.format_duration(3723), "1:02:03")
        self.assertEqual(extension.format_duration(-5), "00:00")


class PayloadTests(unittest.TestCase):
    def call(self, name: str = "pw-dump-call.json", *, seconds: float = 125.0):
        tracker = qualify.CallTracker()
        current = graph(name)
        tracker.observe(current, NOW)
        return tracker.observe(current, NOW + timedelta(seconds=seconds))

    def test_payload_passes_the_brokers_own_validator(self) -> None:
        call = self.call()
        payload = extension.build(call, now=NOW + timedelta(seconds=125))
        # The broker validator, not a local copy of the schema.
        normalized = validate_live_extension(payload)
        self.assertEqual(normalized["category"], "call")
        self.assertEqual(normalized["privacy"], "private")
        self.assertEqual(normalized["progress"], -1.0)
        self.assertEqual(normalized["app_id"], "org.projectluma.Calls")
        self.assertEqual(normalized["id"], "calls.active")
        self.assertEqual(normalized["title"], "Discord")
        self.assertEqual(normalized["subtitle"], "Discord · 02:05")
        self.assertIn("starts_at", normalized)
        self.assertEqual(len(normalized["actions"]), 2)

    def test_the_call_title_and_participant_never_enter_the_payload(self) -> None:
        payload = extension.build(self.call(), now=NOW + timedelta(seconds=125))
        self.assertNotIn(SENSITIVE, json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("Call with", json.dumps(payload, ensure_ascii=False))

    def test_a_third_party_call_offers_only_controls_that_work(self) -> None:
        payload = extension.build(self.call(), now=NOW + timedelta(seconds=125))
        actions = {item["id"]: item for item in payload["actions"]}
        # Mute and deafen act on the application's own streams; nothing lets
        # Luma end another application's call, so no end-call is offered and
        # no dead or disabled control is published.
        self.assertEqual(set(actions), {"call.mute", "call.deafen"})
        self.assertTrue(all(action["enabled"] for action in actions.values()))

    def test_a_muted_call_offers_unmute_and_says_the_app_may_not_show_it(self) -> None:
        payload = extension.build(
            self.call("pw-dump-call-muted.json"), now=NOW + timedelta(seconds=125)
        )
        actions = {item["id"]: item for item in payload["actions"]}
        self.assertIn("call.unmute", actions)
        self.assertNotIn("call.mute", actions)
        self.assertEqual(actions["call.unmute"]["label"], "Unmute microphone")
        self.assertIn("may still show you", actions["call.unmute"]["description"])
        validate_live_extension(payload)

    def test_hostile_application_text_cannot_break_the_payload(self) -> None:
        call = self.call()
        spoofed = type(call)(
            **{
                **{field: getattr(call, field) for field in call.__slots__},
                "application_name": "Meet‮exe\nSystem",
                "application_id": "",
                "binary": "",
            }
        )
        payload = extension.build(spoofed, now=NOW + timedelta(seconds=10))
        normalized = validate_live_extension(payload)
        self.assertNotIn("‮", normalized["title"])
        self.assertNotIn("\n", normalized["subtitle"])

    def test_an_oversized_name_is_bounded_before_the_broker_sees_it(self) -> None:
        call = self.call()
        spoofed = type(call)(
            **{
                **{field: getattr(call, field) for field in call.__slots__},
                "application_name": "N" * 4000,
                "application_id": "",
                "binary": "",
            }
        )
        payload = extension.build(spoofed, now=NOW + timedelta(seconds=10))
        validate_live_extension(payload)
        self.assertLessEqual(len(payload["title"].encode("utf-8")), 256)
        self.assertLessEqual(len(payload["subtitle"].encode("utf-8")), 512)

    def test_a_trusted_desktop_name_wins_over_the_stream_client_name(self) -> None:
        call = self.call()
        named = type(call)(
            **{
                **{field: getattr(call, field) for field in call.__slots__},
                "application_id": "com.discordapp.Discord",
            }
        )
        payload = extension.build(
            named,
            now=NOW + timedelta(seconds=10),
            desktop_lookup=lambda app_id: (True, "Discord"),
        )
        self.assertEqual(payload["title"], "Discord")


class MuteActionTests(unittest.TestCase):
    def test_mute_targets_the_stream_node_not_the_microphone_device(self) -> None:
        call = qualify.CallTracker()
        current = graph("pw-dump-call.json")
        call.observe(current, NOW)
        observed = call.observe(current, NOW + timedelta(seconds=3))
        seen: list[list[str]] = []
        self.assertTrue(
            control.set_node_mute(
                observed.audio_node_id, True, runner=lambda argv: seen.append(list(argv)) or 0
            )
        )
        self.assertEqual(
            seen,
            [["pw-cli", "set-param", "121", "Props", '{"mute": true}']],
        )
        # 55 is the shared Audio/Source microphone device; it is never touched.
        self.assertNotIn("55", seen[0])

    def test_the_camera_stream_is_the_mute_target_for_the_camera_action(self) -> None:
        current = graph("pw-dump-call.json")
        tracker = qualify.CallTracker()
        tracker.observe(current, NOW)
        observed = tracker.observe(current, NOW + timedelta(seconds=3))
        seen: list[list[str]] = []
        control.set_node_mute(
            observed.video_node_id, True, runner=lambda argv: seen.append(list(argv)) or 0
        )
        self.assertEqual(seen[0][2], "123")

    def test_unmute_sends_false_and_a_refusal_is_reported(self) -> None:
        self.assertEqual(
            control.mute_command(121, False),
            ["pw-cli", "set-param", "121", "Props", '{"mute": false}'],
        )
        self.assertFalse(control.set_node_mute(121, True, runner=lambda _argv: 1))
        with self.assertRaises(ValueError):
            control.mute_command(-1, True)

    def test_a_pw_cli_error_line_is_a_refusal_even_on_a_zero_exit(self) -> None:
        # Observed on the target: pw-cli prints an error and still exits 0.
        self.assertEqual(
            control.status(0, 'Error: "set-param: unknown global \'999999\'"'), 1
        )
        self.assertEqual(control.status(0, ""), 0)
        self.assertEqual(control.status(2, ""), 2)

    def test_a_failing_mute_never_logs_the_application(self) -> None:
        with self.assertLogs("luma-calls", level="DEBUG") as captured:
            control.set_node_mute(121, True, runner=lambda _argv: 1)
        self.assertNotIn(SENSITIVE, "\n".join(captured.output))
        self.assertIn("121", "\n".join(captured.output))


class LoggingPrivacyTests(unittest.TestCase):
    """No log record may ever carry a title, a participant, or an app payload."""

    def test_no_log_line_contains_call_content(self) -> None:
        recorded: list[str] = []

        class Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                recorded.append(record.getMessage())
                recorded.append(str(record.args))

        logger = logging.getLogger("luma-calls")
        handler = Capture()
        logger.addHandler(handler)
        previous = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            tracker = qualify.CallTracker()
            for offset, name in (
                (0, "pw-dump-idle.json"),
                (1, "pw-dump-call.json"),
                (4, "pw-dump-call.json"),
                (9, "pw-dump-call-muted.json"),
                (12, "pw-dump-idle.json"),
                (13, "pw-dump-noise.json"),
            ):
                observed = tracker.observe(graph(name), NOW + timedelta(seconds=offset))
                if observed is not None:
                    extension.build(observed, now=NOW + timedelta(seconds=offset))
                    control.set_node_mute(
                        observed.audio_node_id, True, runner=lambda _argv: 1
                    )
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)
        blob = "\n".join(recorded)
        for forbidden in (SENSITIVE, "Call with", "WEBRTC VoiceEngine", "media.name"):
            self.assertNotIn(forbidden, blob)


class ContinuityTests(unittest.TestCase):
    """Telephony from the paired phone: the one source with a real hang up."""

    class Record:
        def __init__(self, call_id, phase, *, started_at=0, answered_at=0, address="+15550101"):
            self.call_id = call_id
            self.address = address
            self.direction = "incoming"
            self.phase = phase
            self.started_at = started_at
            self.answered_at = answered_at

    def test_only_live_phases_are_published(self) -> None:
        for phase in ("idle", "preparing", "ending", "ended", "failed"):
            self.assertIsNone(continuity.select([self.Record("a", phase)]), phase)
        for phase in ("incoming", "dialling", "connecting", "active", "held"):
            self.assertIsNotNone(continuity.select([self.Record("a", phase)]), phase)

    def test_an_answered_call_outranks_a_ringing_one(self) -> None:
        chosen = continuity.select([
            self.Record("ringing", "incoming", started_at=200),
            self.Record("live", "active", started_at=100, answered_at=150),
        ])
        self.assertEqual(chosen.call_id, "live")

    def test_hang_up_is_enabled_and_mute_follows_where_the_audio_is(self) -> None:
        bridged = continuity.select(
            [self.Record("live", "active", started_at=1757000000, answered_at=1757000060)],
            audio={"status": "connected", "muted": False},
            mute_supported=True,
        )
        payload = extension.build_continuity(bridged, now=NOW)
        validate_live_extension(payload)
        actions = {item["id"]: item for item in payload["actions"]}
        self.assertTrue(actions["call.hangup"]["enabled"])
        self.assertTrue(actions["call.mute"]["enabled"])
        self.assertNotIn("call.camera", actions)

        on_handset = continuity.select(
            [self.Record("live", "active", started_at=1757000000, answered_at=1757000060)],
            audio={"status": "phone", "muted": False},
            mute_supported=True,
        )
        handset_actions = {
            item["id"]: item
            for item in extension.build_continuity(on_handset, now=NOW)["actions"]
        }
        self.assertFalse(handset_actions["call.mute"]["enabled"])
        self.assertIn("on your phone", handset_actions["call.mute"]["description"])
        self.assertTrue(handset_actions["call.hangup"]["enabled"])

    def test_a_ringing_call_shows_its_state_instead_of_a_timer(self) -> None:
        ringing = continuity.select([self.Record("r", "incoming", started_at=1757000000)])
        payload = extension.build_continuity(ringing, now=NOW)
        validate_live_extension(payload)
        self.assertEqual(payload["subtitle"], "Phone · Incoming call")

    def test_the_source_survives_a_provider_that_raises(self) -> None:
        class Broken:
            def calls(self):
                raise PermissionError("selected phone unavailable")

        source = continuity.ContinuitySource(Broken())
        self.assertIsNone(source.refresh())
        self.assertFalse(source.set_muted(True))
        self.assertFalse(source.hangup("x"))


class BrokerLiveInvocationTests(unittest.TestCase):
    """The broker must route and police actions the Shell renders."""

    def setUp(self) -> None:
        self.locked = False
        self.core = BrokerCore(
            grant_store=_NullGrantStore(),
            audit_store=_NullAuditStore(),
            locked=lambda: self.locked,
            clock=lambda: NOW,
            limiter=RateLimiter(limit=500, window_seconds=60.0),
        )
        self.producer = Identity(
            "native:org.projectluma.Calls",
            "org.projectluma.Calls",
            "Calls",
            ":1.31",
            1000,
            4242,
            IdentityStrength.MANAGED_NATIVE,
        )
        self.shell = Identity(
            "transient:1000::1.7",
            "",
            "GNOME Shell",
            ":1.7",
            1000,
            2547,
            IdentityStrength.TRANSIENT_NATIVE,
        )
        tracker = qualify.CallTracker()
        current = graph("pw-dump-call.json")
        tracker.observe(current, NOW)
        observed = tracker.observe(current, NOW + timedelta(seconds=3))
        self.payload = extension.build(observed, now=NOW + timedelta(seconds=3))
        self.publication = self.core.register_live_extension(
            self.producer,
            extension.APPLICATION_ID,
            extension.EXTENSION_ID,
            self.payload,
            "/org/projectluma/LiveExtensionProvider/org/projectluma/Calls",
        )

    def invoke(self, action_id: str, *, confirmed: bool = False):
        return self.core.authorize_live_invocation(
            self.shell,
            self.publication.publication_id,
            extension.EXTENSION_ID,
            action_id,
            confirmed=confirmed,
        )

    def test_a_declared_enabled_action_is_allowed(self) -> None:
        decision, publication, action = self.invoke("call.mute")
        self.assertTrue(decision.allowed)
        self.assertEqual(action["id"], "call.mute")
        self.assertEqual(publication.application_id, "org.projectluma.Calls")
        self.assertEqual(
            publication.provider_path,
            "/org/projectluma/LiveExtensionProvider/org/projectluma/Calls",
        )

    def test_a_declared_disabled_action_is_refused_by_the_broker(self) -> None:
        # A continuity call whose audio is on the handset declares mute but
        # ships it disabled.
        on_handset = continuity.select(
            [ContinuityTests.Record("live", "active", started_at=1757000000, answered_at=1757000060)],
            audio={"status": "phone", "muted": False},
            mute_supported=True,
        )
        self.core.update_live_extension(
            self.producer,
            self.publication.publication_id,
            extension.build_continuity(on_handset, now=NOW),
        )
        decision, _publication, _action = self.invoke("call.mute")
        self.assertFalse(decision.allowed)
        self.assertIn("disabled", decision.reason)
        self.assertFalse(decision.confirmation_required)

    def test_an_undeclared_action_identifier_is_refused(self) -> None:
        with self.assertRaisesRegex(BrokerError, "unknown semantic action"):
            self.invoke("call.transfer")
        # A third-party call never declares hang-up, so it cannot be invoked.
        with self.assertRaisesRegex(BrokerError, "unknown semantic action"):
            self.invoke("call.hangup")

    def test_an_unknown_object_identifier_is_refused(self) -> None:
        with self.assertRaisesRegex(BrokerError, "unknown semantic object"):
            self.core.authorize_live_invocation(
                self.shell,
                self.publication.publication_id,
                "calls.other",
                "call.mute",
                confirmed=False,
            )

    def test_a_locked_session_refuses_every_live_action(self) -> None:
        self.locked = True
        decision, _publication, _action = self.invoke("call.mute")
        self.assertFalse(decision.allowed)
        self.assertIn("locked", decision.reason)

    def test_the_two_namespaces_never_resolve_each_other(self) -> None:
        with self.assertRaisesRegex(BrokerError, "unknown semantic publication"):
            self.core.authorize_invocation(
                self.shell,
                self.publication.publication_id,
                extension.EXTENSION_ID,
                "call.mute",
                confirmed=False,
            )
        surface = self.core.register_surface(
            self.producer,
            extension.APPLICATION_ID,
            "calls",
            {
                "schema_version": "0.1",
                "id": "calls",
                "kind": "application",
                "name": "Calls",
                "privacy": "private",
                "actions": [],
                "children": [],
            },
            "/org/projectluma/Calls/Semantics",
        )
        with self.assertRaisesRegex(BrokerError, "unknown Live Extension publication"):
            self.core.authorize_live_invocation(
                self.shell,
                surface.publication_id,
                "calls",
                "call.mute",
                confirmed=False,
            )

    def test_the_producer_cannot_claim_another_applications_identity(self) -> None:
        spoofed = dict(self.payload, app_id="com.discordapp.Discord")
        with self.assertRaises((ValidationError, BrokerError)):
            self.core.register_live_extension(
                self.producer,
                extension.APPLICATION_ID,
                extension.EXTENSION_ID,
                spoofed,
                "/org/projectluma/LiveExtensionProvider/org/projectluma/Calls",
            )


class _NullGrantStore:
    def load(self):
        return ()

    def save(self, _grants):
        return None


class _NullAuditStore:
    def __init__(self) -> None:
        self.events = []

    def append(self, event) -> None:
        self.events.append(event)

    def tail(self, _limit):
        return ()


if __name__ == "__main__":
    unittest.main(verbosity=2)
