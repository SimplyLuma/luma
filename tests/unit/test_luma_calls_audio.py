# SPDX-License-Identifier: Apache-2.0
"""Per-application mute and deafen against a simulated PipeWire graph.

The graph here behaves like the real one: a write lands a moment later, a
stream can be replaced mid-call, WirePlumber restores a remembered mute onto
an application's next stream, and someone else can unmute a stream.
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
CALLS = ROOT / "src/luma-platform/calls"
if str(CALLS) not in sys.path:
    sys.path.insert(0, str(CALLS))

from luma_calls import audio, nodes, qualify  # noqa: E402


def stream(
    node_id: int, media_class: str, binary: str = "Discord", *, muted: bool = False, serial: int | None = None
) -> nodes.Node:
    return nodes.Node(
        node_id=node_id,
        media_class=media_class,
        state="running",
        props={
            "application.process.binary": binary,
            "application.name": binary,
            "object.serial": node_id if serial is None else serial,
        },
        params={"Props": [{"mute": muted, "volume": 1.0}]},
    )


class Graph:
    """A node map plus a PipeWire that applies mute writes when told to."""

    def __init__(self) -> None:
        self.nodes: dict[int, nodes.Node] = {}
        self.pending: list[tuple[int, bool]] = []
        self.writes: list[tuple[int, bool]] = []
        #: What WirePlumber's state-stream remembers, per binary and class.
        self.remembered: dict[tuple[str, str], bool] = {}

    def add(self, node_id: int, media_class: str, binary: str = "Discord", *, serial: int | None = None,
            restore: bool = True) -> None:
        restored = restore and self.remembered.get((binary, media_class), False)
        self.nodes[node_id] = stream(node_id, media_class, binary, muted=restored, serial=serial)

    def mute(self, node_id: int, muted: bool) -> bool:
        self.writes.append((node_id, muted))
        self.pending.append((node_id, muted))
        return node_id in self.nodes

    def settle(self) -> None:
        for node_id, muted in self.pending:
            node = self.nodes.get(node_id)
            if node is None:
                continue
            self.set_external(node_id, muted)
        self.pending.clear()

    def set_external(self, node_id: int, muted: bool) -> None:
        node = self.nodes[node_id]
        self.nodes[node_id] = stream(
            node_id, node.media_class, node.binary, muted=muted, serial=node.props["object.serial"]
        )
        self.remembered[(node.binary, node.media_class)] = muted


CAP, PLAY = qualify.CAPTURE_AUDIO, qualify.PLAYBACK_AUDIO


class AudioControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = Graph()
        self.directory = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.directory.name) / "audio-undo.json"
        self.controls = audio.AudioControls(
            self.graph.mute, audio.UndoLedger(self.ledger_path), grace=10.0
        )
        self.graph.add(10, CAP)
        self.graph.add(11, CAP)  # a second capture stream from the same app
        self.graph.add(20, PLAY)
        self.graph.add(30, CAP, "zoom")  # another application entirely
        self.graph.add(31, PLAY, "zoom")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def reconcile(self, now: float, active: str | None = "Discord") -> None:
        self.controls.reconcile(self.graph.nodes, active, now)

    def test_mute_reaches_every_capture_stream_of_that_application_only(self) -> None:
        self.assertTrue(self.controls.set(self.graph.nodes, "Discord", audio.CAPTURE, True))
        self.assertEqual(sorted(self.graph.writes), [(10, True), (11, True)])
        self.graph.settle()
        self.assertTrue(audio.muted(self.graph.nodes, "Discord", audio.CAPTURE))
        self.assertFalse(audio.muted(self.graph.nodes, "Discord", audio.PLAYBACK))
        self.assertFalse(audio.muted(self.graph.nodes, "zoom", audio.CAPTURE))

    def test_state_is_all_streams_not_any(self) -> None:
        self.graph.set_external(10, True)
        self.assertFalse(audio.muted(self.graph.nodes, "Discord", audio.CAPTURE))
        self.assertIsNone(audio.muted(self.graph.nodes, "nobody", audio.CAPTURE))

    def test_a_write_that_has_not_landed_is_not_mistaken_for_someone_else(self) -> None:
        self.controls.set(self.graph.nodes, "Discord", audio.CAPTURE, True)
        self.reconcile(1.0)  # the graph still shows the old state
        self.assertIn(("Discord", audio.CAPTURE), self.controls.intents)

    def test_a_stream_reopened_mid_call_stays_muted(self) -> None:
        self.controls.set(self.graph.nodes, "Discord", audio.CAPTURE, True)
        self.graph.settle()
        self.reconcile(1.0)
        del self.graph.nodes[10]
        self.graph.remembered.clear()  # even without WirePlumber's help
        self.graph.add(12, CAP)
        self.reconcile(2.0)
        self.graph.settle()
        self.assertTrue(audio.muted(self.graph.nodes, "Discord", audio.CAPTURE))

    def test_someone_else_unmuting_releases_the_stream_to_them(self) -> None:
        self.controls.set(self.graph.nodes, "Discord", audio.CAPTURE, True)
        self.graph.settle()
        self.reconcile(1.0)
        self.graph.set_external(11, False)  # the volume panel
        self.reconcile(2.0)
        self.assertNotIn(("Discord", audio.CAPTURE), self.controls.intents)
        self.assertNotIn(("Discord", audio.CAPTURE), self.controls.ledger)
        self.graph.writes.clear()
        self.reconcile(3.0)
        self.assertEqual(self.graph.writes, [])  # Luma does not fight back
        self.assertFalse(audio.muted(self.graph.nodes, "Discord", audio.CAPTURE))

    def test_a_brief_gap_in_the_call_does_not_undo_the_mute(self) -> None:
        self.controls.set(self.graph.nodes, "Discord", audio.CAPTURE, True)
        self.graph.settle()
        self.reconcile(1.0)
        self.reconcile(5.0, active=None)
        self.reconcile(9.0, active="Discord")
        self.reconcile(30.0, active="Discord")
        self.assertIn(("Discord", audio.CAPTURE), self.controls.intents)

    def test_the_call_ending_restores_live_streams_and_defers_the_rest(self) -> None:
        self.controls.deafen(self.graph.nodes, "Discord", True)
        self.graph.settle()
        self.reconcile(1.0)
        # The call ends: capture streams close, the playback stream lingers.
        del self.graph.nodes[10]
        del self.graph.nodes[11]
        self.reconcile(2.0, active=None)
        self.reconcile(13.0, active=None)
        self.graph.settle()
        self.assertFalse(audio.muted(self.graph.nodes, "Discord", audio.PLAYBACK))
        self.assertEqual(self.controls.intents, {})
        # WirePlumber still remembers the capture mute.
        self.assertIn(("Discord", audio.CAPTURE), self.controls.ledger)
        self.graph.add(14, CAP)
        self.assertTrue(self.graph.nodes[14].muted)
        self.reconcile(20.0, active=None)
        self.graph.settle()
        self.assertFalse(self.graph.nodes[14].muted)
        self.reconcile(20.5, active=None)
        self.reconcile(22.5, active=None)
        self.assertNotIn(("Discord", audio.CAPTURE), self.controls.ledger)
        self.assertFalse(self.graph.remembered[("Discord", CAP)])

    def test_a_remembered_mute_that_lands_late_is_still_undone(self) -> None:
        self.controls.set(self.graph.nodes, "Discord", audio.CAPTURE, True)
        self.graph.settle()
        del self.graph.nodes[10]
        del self.graph.nodes[11]
        self.reconcile(1.0, active=None)
        self.reconcile(12.0, active=None)
        # Luma sees the new stream before WirePlumber restores the mute.
        self.graph.add(15, CAP, restore=False)
        self.reconcile(13.0, active=None)
        self.assertIn(("Discord", audio.CAPTURE), self.controls.ledger)
        self.graph.set_external(15, True)  # WirePlumber's restore lands
        self.reconcile(13.2, active=None)
        self.graph.settle()
        self.assertFalse(self.graph.nodes[15].muted)
        self.reconcile(13.5, active=None)
        self.reconcile(15.5, active=None)
        self.assertNotIn(("Discord", audio.CAPTURE), self.controls.ledger)

    def test_a_new_stream_reusing_an_old_id_is_not_someone_else(self) -> None:
        self.controls.set(self.graph.nodes, "Discord", audio.PLAYBACK, True)
        self.graph.settle()
        self.reconcile(1.0)
        # The stream is replaced; PipeWire hands the new one the same id.
        self.graph.add(20, PLAY, serial=99, restore=False)
        self.reconcile(2.0)
        self.assertIn(("Discord", audio.PLAYBACK), self.controls.intents)
        self.graph.settle()
        self.assertTrue(audio.muted(self.graph.nodes, "Discord", audio.PLAYBACK))

    def test_the_undo_ledger_survives_a_restart(self) -> None:
        self.controls.set(self.graph.nodes, "Discord", audio.CAPTURE, True)
        reloaded = audio.UndoLedger(self.ledger_path)
        self.assertIn(("Discord", audio.CAPTURE), reloaded)
        text = self.ledger_path.read_text(encoding="utf-8")
        self.assertNotIn("Priya", text)
        self.assertEqual(audio.UndoLedger(Path(self.directory.name) / "absent.json").entries, set())
        self.ledger_path.write_text("not json", encoding="utf-8")
        self.assertEqual(audio.UndoLedger(self.ledger_path).entries, set())

    def test_deafen_silences_both_and_undeafen_restores_the_microphone(self) -> None:
        self.controls.set(self.graph.nodes, "Discord", audio.CAPTURE, True)
        self.graph.settle()
        self.controls.deafen(self.graph.nodes, "Discord", True)
        self.graph.settle()
        self.assertTrue(audio.muted(self.graph.nodes, "Discord", audio.PLAYBACK))
        self.controls.deafen(self.graph.nodes, "Discord", False)
        self.graph.settle()
        self.assertFalse(audio.muted(self.graph.nodes, "Discord", audio.PLAYBACK))
        # Muted before deafening, so still muted after.
        self.assertTrue(audio.muted(self.graph.nodes, "Discord", audio.CAPTURE))

        self.controls.set(self.graph.nodes, "Discord", audio.CAPTURE, False)
        self.graph.settle()
        self.controls.deafen(self.graph.nodes, "Discord", True)
        self.graph.settle()
        self.assertTrue(audio.muted(self.graph.nodes, "Discord", audio.CAPTURE))
        self.controls.deafen(self.graph.nodes, "Discord", False)
        self.graph.settle()
        self.assertFalse(audio.muted(self.graph.nodes, "Discord", audio.CAPTURE))

    def test_nothing_to_mute_is_a_refusal(self) -> None:
        self.assertFalse(self.controls.set(self.graph.nodes, "nobody", audio.CAPTURE, True))

    def test_a_monitor_restart_does_not_read_as_someone_else_changing_streams(self) -> None:
        self.controls.set(self.graph.nodes, "Discord", audio.CAPTURE, True)
        self.graph.settle()
        self.reconcile(1.0)
        self.controls.forget_graph()
        self.reconcile(2.0)
        self.assertIn(("Discord", audio.CAPTURE), self.controls.intents)


if __name__ == "__main__":
    unittest.main()
