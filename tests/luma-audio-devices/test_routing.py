# SPDX-License-Identifier: MPL-2.0
"""One test class per routing rule (ADR-032, amended for 1.luma.3)."""
import unittest

import _paths  # noqa: F401
from luma_audio_devices.classify import Output
from luma_audio_devices.routing import (Decision, RoutingPolicy, SwitchTo, memory_from_dict,
                                        MANUAL_WINDOW, MAX_SETTLE, RESTORE_WINDOW, SETTLE, STARTUP_GRACE)

SPEAKER = Output("alsa_output.speaker", "card:pci:0", "Speaker", "audio-speakers-symbolic", priority=712)
HDMI = Output("alsa_output.hdmi1", "display:pci:LG ULTRAWIDE", "LG ULTRAWIDE", "video-display-symbolic", priority=664)
DOCK = Output("alsa_output.usb-dock", "usb:0x17ef:0xa396", "ThinkPad Dock", "audio-card-symbolic", priority=1500)
WIRED = Output("alsa_output.headphones", "card:pci:0:headphones", "Headphones", "audio-headphones-symbolic",
               personal=True, priority=900)
BUDS = Output("bluez_output.78_C1.1", "bluez:78:C1", "Buds", "audio-headphones-symbolic", personal=True, priority=1010)
BT_SPEAKER = Output("bluez_output.AA_BB.1", "bluez:AA:BB", "Boom", "audio-speakers-symbolic", priority=1010)
USB_HEADSET = Output("alsa_output.usb-headset", "usb:0x1395:0x0025", "Headset", "audio-headset-symbolic",
                     personal=True, priority=1500)
AIRPLAY = Output("luma_airplay.aabb", "airplay:aabb", "Mac", "luma-network-speaker-symbolic", network=True,
                 local=False)

T0 = 1_000_000.0
LATER = T0 + STARTUP_GRACE + 1


def started(**kwargs) -> RoutingPolicy:
    policy = RoutingPolicy(**kwargs)
    policy.start([SPEAKER], SPEAKER.node_name, T0)
    return policy


def run_until(policy: RoutingPolicy, now: float) -> list:
    actions = []
    while (deadline := policy.next_deadline()) is not None and deadline <= now:
        actions += policy.tick(deadline)
    return actions


def switches(actions):
    return [a for a in actions if isinstance(a, SwitchTo)]


def decisions(actions, event=None):
    return [a for a in actions if isinstance(a, Decision) and (event is None or a.event == event)]


class NeverAsks(unittest.TestCase):
    def test_the_prompt_api_is_gone(self):
        policy = started()
        for name in ("answer", "set_prompts_enabled", "set_device_policy", "attention_changed"):
            self.assertFalse(hasattr(policy, name), name)


class PresentAtLogin(unittest.TestCase):
    def test_devices_at_login_change_nothing(self):
        policy = RoutingPolicy()
        policy.start([SPEAKER, HDMI, BUDS], SPEAKER.node_name, T0)
        self.assertIsNone(policy.next_deadline())

    def test_bluetooth_reconnecting_during_login_grace_does_not_switch(self):
        policy = started()
        actions = policy.output_added(BUDS, T0 + 3) + run_until(policy, T0 + 60)
        self.assertEqual(switches(actions), [])
        self.assertEqual(decisions(actions, "kept")[0].reason, "present at login")


class DisplaysAndDocksKeepTheOutput(unittest.TestCase):
    def test_display_with_audio_keeps_current_output(self):
        policy = started()
        policy.output_added(HDMI, LATER)
        actions = run_until(policy, LATER + SETTLE)
        self.assertEqual(switches(actions), [])
        kept = decisions(actions, "kept")
        self.assertEqual((kept[0].key, kept[0].current), (HDMI.key, "Speaker"))

    def test_dock_with_higher_priority_usb_audio_keeps_current_output(self):
        policy = started()
        policy.output_added(DOCK, LATER)
        policy.output_added(HDMI, LATER + 0.5)
        actions = run_until(policy, LATER + MAX_SETTLE)
        self.assertEqual(switches(actions), [])
        self.assertEqual({d.key for d in decisions(actions, "kept")}, {DOCK.key, HDMI.key})

    def test_network_outputs_are_ignored(self):
        policy = started()
        self.assertEqual(policy.output_added(AIRPLAY, LATER), [])
        self.assertIsNone(policy.next_deadline())


class HeadphonesAndBluetoothTakeTheSound(unittest.TestCase):
    def test_wired_headphones_switch_after_settling(self):
        policy = started()
        policy.output_added(WIRED, LATER)
        self.assertEqual(run_until(policy, LATER + SETTLE - 0.1), [])
        actions = run_until(policy, LATER + SETTLE)
        self.assertEqual(switches(actions), [SwitchTo(WIRED.key, WIRED.node_name, "headphones or headset connected")])
        self.assertEqual(decisions(actions, "switched")[0].current, "Speaker")

    def test_bluetooth_earbuds_switch(self):
        policy = started()
        policy.output_added(BUDS, LATER)
        self.assertEqual([s.node_name for s in switches(run_until(policy, LATER + SETTLE))], [BUDS.node_name])

    def test_bluetooth_speaker_the_person_connects_switches(self):
        policy = started()
        policy.output_added(BT_SPEAKER, LATER)
        actions = run_until(policy, LATER + SETTLE)
        self.assertEqual(switches(actions), [SwitchTo(BT_SPEAKER.key, BT_SPEAKER.node_name, "Bluetooth audio connected")])

    def test_dock_with_a_headset_switches_once_to_the_headset(self):
        policy = started()
        policy.output_added(DOCK, LATER)
        policy.output_added(USB_HEADSET, LATER + 1)
        actions = run_until(policy, LATER + MAX_SETTLE)
        self.assertEqual([s.node_name for s in switches(actions)], [USB_HEADSET.node_name])
        self.assertEqual([d.key for d in decisions(actions, "kept")], [DOCK.key])

    def test_setting_off_keeps_the_output(self):
        policy = started(switch_to_personal=False)
        policy.output_added(BUDS, LATER)
        self.assertEqual(switches(run_until(policy, LATER + SETTLE)), [])

    def test_already_the_output_does_not_switch_again(self):
        policy = started()
        policy.output_added(BUDS, LATER)
        policy.default_changed(BUDS.node_name, LATER + 0.5)   # WirePlumber restored it itself
        self.assertEqual(switches(run_until(policy, LATER + SETTLE)), [])

    def test_two_profile_nodes_of_one_device_switch_once(self):
        policy = started()
        headset_profile = Output("bluez_output.78_C1.2", BUDS.key, "Buds", BUDS.icon, personal=True, priority=900)
        policy.output_added(BUDS, LATER)
        policy.output_added(headset_profile, LATER + 0.2)
        actions = run_until(policy, LATER + MAX_SETTLE)
        self.assertEqual([s.node_name for s in switches(actions)], [BUDS.node_name])


class BackWhenItLeaves(unittest.TestCase):
    def test_unplugging_is_not_a_manual_choice(self):
        policy = started()
        policy.output_added(BUDS, LATER)
        run_until(policy, LATER + SETTLE)
        policy.default_changed(BUDS.node_name, LATER + SETTLE + 0.1)   # our switch
        # WirePlumber moves the default back before pw-dump reports the removal.
        policy.default_changed(SPEAKER.node_name, LATER + 30)
        policy.output_removed(BUDS.node_name, LATER + 30.4, configured=BUDS.node_name)
        self.assertEqual(decisions(run_until(policy, LATER + 60), "remembered-choice"), [])
        self.assertTrue(policy.state.devices[BUDS.key].auto_switch)
        # Next time they connect they take the sound again.
        policy.output_added(BUDS, LATER + 100)
        self.assertEqual(len(switches(run_until(policy, LATER + 100 + SETTLE))), 1)


class ManualChoicesAreRespected(unittest.TestCase):
    def connect_buds(self, policy, at):
        policy.output_added(BUDS, at)
        run_until(policy, at + SETTLE)
        policy.default_changed(BUDS.node_name, at + SETTLE + 0.1)

    def test_moving_sound_away_while_connected_is_remembered_for_that_device(self):
        policy = started()
        self.connect_buds(policy, LATER)
        policy.default_changed(SPEAKER.node_name, LATER + 60)          # person picks Speaker in Quick Settings
        actions = run_until(policy, LATER + 60 + MANUAL_WINDOW)
        self.assertEqual([d.key for d in decisions(actions, "remembered-choice")], [BUDS.key])
        self.assertFalse(policy.state.devices[BUDS.key].auto_switch)
        policy.output_removed(BUDS.node_name, LATER + 120)
        policy.output_added(BUDS, LATER + 200)
        actions = run_until(policy, LATER + 200 + SETTLE)
        self.assertEqual(switches(actions), [])
        self.assertIn("moved the sound away", decisions(actions, "kept")[0].reason)
        # Other headphones still switch.
        policy.output_added(WIRED, LATER + 300)
        self.assertEqual(len(switches(run_until(policy, LATER + 300 + SETTLE))), 1)

    def test_choosing_the_device_again_restores_switching(self):
        policy = started()
        self.connect_buds(policy, LATER)
        policy.default_changed(SPEAKER.node_name, LATER + 60)
        run_until(policy, LATER + 60 + MANUAL_WINDOW)
        actions = policy.default_changed(BUDS.node_name, LATER + 90)
        self.assertEqual([d.key for d in decisions(actions, "remembered-choice")], [BUDS.key])
        self.assertTrue(policy.state.devices[BUDS.key].auto_switch)

    def test_choosing_the_device_soon_after_it_connects_restores_switching(self):
        policy = started()
        self.connect_buds(policy, LATER)
        policy.default_changed(SPEAKER.node_name, LATER + 60)
        run_until(policy, LATER + 60 + MANUAL_WINDOW)
        policy.output_removed(BUDS.node_name, LATER + 70)
        policy.output_added(BUDS, LATER + 80)
        run_until(policy, LATER + 80 + SETTLE)                 # kept on the speaker
        policy.default_changed(BUDS.node_name, LATER + 83)     # picked in Quick Settings three seconds later
        self.assertTrue(policy.state.devices[BUDS.key].auto_switch)

    def test_restore_reported_before_the_device_is_not_a_manual_choice(self):
        policy = started()
        self.connect_buds(policy, LATER)
        policy.default_changed(HDMI.node_name, LATER + 60)     # metadata first...
        policy.output_added(HDMI, LATER + 60.1)                # ...then the device
        run_until(policy, LATER + 70)
        self.assertTrue(policy.state.devices[BUDS.key].auto_switch)

    def test_a_choice_reported_late_is_still_remembered_when_the_device_leaves(self):
        policy = started()
        self.connect_buds(policy, LATER)
        # The default change never arrived; the configured default at removal tells.
        actions = policy.output_removed(BUDS.node_name, LATER + 60, configured=SPEAKER.node_name)
        self.assertEqual([d.key for d in decisions(actions, "remembered-choice")], [BUDS.key])
        self.assertFalse(policy.state.devices[BUDS.key].auto_switch)

    def test_choosing_it_again_reported_late_restores_switching_when_it_leaves(self):
        policy = started()
        policy.state.devices[BUDS.key] = memory_from_dict({"auto_switch": False})
        policy.output_added(BUDS, LATER)
        run_until(policy, LATER + SETTLE)                       # kept
        policy.default_changed(BUDS.node_name, LATER + 0.5 * RESTORE_WINDOW)  # looks like a restore...
        actions = policy.output_removed(BUDS.node_name, LATER + 60, configured=BUDS.node_name)
        self.assertEqual([d.key for d in decisions(actions, "remembered-choice")], [BUDS.key])
        self.assertTrue(policy.state.devices[BUDS.key].auto_switch)

    def test_unplugging_with_the_device_still_configured_is_not_a_choice(self):
        policy = started()
        self.connect_buds(policy, LATER)
        self.assertEqual(policy.output_removed(BUDS.node_name, LATER + 60, configured=BUDS.node_name), [])
        self.assertTrue(policy.state.devices[BUDS.key].auto_switch)

    def test_a_device_that_never_had_the_sound_is_not_marked_on_removal(self):
        policy = started(switch_to_personal=False)
        policy.output_added(BUDS, LATER)
        run_until(policy, LATER + SETTLE)
        policy.output_removed(BUDS.node_name, LATER + 60, configured=SPEAKER.node_name)
        self.assertTrue(policy.state.devices[BUDS.key].auto_switch)

    def test_our_own_switch_is_not_a_manual_choice(self):
        policy = started()
        policy.state.devices[BUDS.key] = memory_from_dict({"auto_switch": True})
        self.connect_buds(policy, LATER)
        self.assertEqual(decisions(run_until(policy, LATER + 60), "remembered-choice"), [])

    def test_wireplumber_restoring_a_newly_connected_display_is_not_a_manual_choice(self):
        policy = started()
        self.connect_buds(policy, LATER)
        policy.output_added(HDMI, LATER + 60)
        policy.default_changed(HDMI.node_name, LATER + 60.2)   # the person chose the LG last time
        actions = run_until(policy, LATER + 70)
        self.assertEqual(decisions(actions, "remembered-choice"), [])
        self.assertTrue(policy.state.devices[BUDS.key].auto_switch)

    def test_displays_and_docks_are_never_marked(self):
        policy = started()
        policy.output_added(HDMI, LATER)
        run_until(policy, LATER + SETTLE)
        policy.default_changed(HDMI.node_name, LATER + 60)
        policy.default_changed(SPEAKER.node_name, LATER + 90)
        self.assertEqual(decisions(run_until(policy, LATER + 100), "remembered-choice"), [])


class LegacyState(unittest.TestCase):
    def test_prompt_answers_from_earlier_versions_are_dropped(self):
        memory = memory_from_dict({"policy": "never", "snoozed_until": 5.0, "name": "Dock", "last_seen": 3.0})
        self.assertTrue(memory.auto_switch)
        self.assertEqual((memory.name, memory.last_seen), ("Dock", 3.0))
        self.assertFalse(hasattr(memory, "policy"))
        self.assertFalse(hasattr(memory, "snoozed_until"))


if __name__ == "__main__":
    unittest.main()
