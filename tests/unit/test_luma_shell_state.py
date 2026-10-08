# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "src/luma-shell-state/luma_shell_state.py"
SPEC = importlib.util.spec_from_file_location("luma_shell_state", MODULE_PATH)
assert SPEC and SPEC.loader
state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(state)


class ShellStateModelTests(unittest.TestCase):
    def test_status_cluster_contract(self):
        expected = state.normalize_status()
        self.assertEqual(expected["frame"], "none")
        self.assertEqual((expected["time_size"], expected["date_size"]), (21, 11))
        for scope in ["all", "controls", "split", "joined", "flush"]:
            self.assertEqual(state.normalize_status({"frame_scope": scope})["frame_scope"], scope)
        for value in ["#123abc", "#ABCDEF"]:
            self.assertEqual(state.normalize_status({"clock_fill": value})["clock_fill"], value)
        for value in ["red", "#fff", "#123abc; padding: 500px", None, 42]:
            self.assertEqual(state.normalize_status({"clock_fill": value})["clock_fill"], "#7f8892")
        for invalid in [{"frame": "invalid"}, {"fill_strength": "15"}, {"time_size": 22},
                        {"date_size": True}, {"controls_right": 1}]:
            with self.assertRaises(state.InvalidState):
                state.normalize_status(invalid)

    def test_legacy_gtk_theme_follows_shared_appearance(self):
        self.assertEqual(state.gtk_theme_for_appearance("prefer-dark"), "Luma-dark")
        self.assertEqual(state.gtk_theme_for_appearance("prefer-light"), "Luma")
        self.assertEqual(state.gtk_theme_for_appearance("default"), "Luma")

    def test_legacy_gtk_theme_has_one_translucent_stand_in(self):
        """A toolkit that cannot be translucent still gets a defined band.

        GTK 3, and Chromium and the Electron applications that read their
        window colours from the GTK 3 theme, cannot take part in a treatment.
        They used to keep Adwaita's window colour, which is a different colour
        again from every window that can be translucent, and that is one of
        the mismatched title bars on the desktop.
        """
        for treatment in ("frost", "glass"):
            with self.subTest(treatment=treatment):
                self.assertEqual(
                    state.gtk_theme_for_appearance("default", treatment),
                    "Luma-translucent",
                )
                self.assertEqual(
                    state.gtk_theme_for_appearance("prefer-dark", treatment),
                    "Luma-translucent",
                )
        self.assertEqual(state.gtk_theme_for_appearance("default", "light"), "Luma")
        self.assertEqual(state.gtk_theme_for_appearance("default", "dark"), "Luma-dark")

    def test_application_ids_are_ordered_and_deduplicated(self):
        self.assertEqual(
            state.normalize_app_ids(["org.example.One.desktop", "org.example.One.desktop", "two.desktop"]),
            ["org.example.One.desktop", "two.desktop"],
        )

    def test_application_ids_reject_paths_and_non_desktop_names(self):
        for value in ("org.example.App", "../app.desktop", "/tmp/app.desktop", ""):
            with self.subTest(value=value), self.assertRaises(state.InvalidState):
                state.normalize_app_ids([value])

    def test_wallpaper_is_local(self):
        self.assertEqual(
            state.normalize_wallpaper_uri("file:///usr/share/backgrounds/luma/a.jpg"),
            "file:///usr/share/backgrounds/luma/a.jpg",
        )
        with self.assertRaises(state.InvalidState):
            state.normalize_wallpaper_uri("https://example.invalid/a.jpg")

    def test_idle_delay_accepts_off_or_phone_safe_interval(self):
        self.assertEqual(state.normalize_idle_delay(0), 0)
        self.assertEqual(state.normalize_idle_delay(60), 60)
        for value in (1, 14, 86401, True):
            with self.subTest(value=value), self.assertRaises(state.InvalidState):
                state.normalize_idle_delay(value)

    def test_renderer_detection_is_capability_not_resolution(self):
        self.assertEqual(state.detect_renderer({"XDG_CURRENT_DESKTOP": "Phosh"}), "phosh")
        self.assertEqual(state.detect_renderer({"XDG_CURRENT_DESKTOP": "Luma:GNOME"}), "gnome")
        self.assertEqual(state.detect_renderer({}), "unknown")

    def test_empty_home_follows_favorites(self):
        snapshot = state.build_snapshot(
            favorites=["org.example.One.desktop"],
            home_apps=[],
            wallpaper_uri="file:///usr/share/backgrounds/luma/a.jpg",
            wallpaper_uri_dark="file:///usr/share/backgrounds/luma/a.jpg",
            wallpaper_style="zoom",
            appearance="prefer-light",
            idle_delay=60,
            screen_keyboard_enabled=True,
            launcher_columns=4,
            input_method="luma-latin",
            restore_app_state=True,
            renderer="phosh",
            shelf=state.normalize_shelf(
                layout_version=1,
                edge="bottom",
                dock_anchor="center",
                actions_anchor="center",
                group_layout="centered-combined",
                surface_mode="separate",
                edge_mode="floating",
                material="dark",
                monitor_mode="primary",
                overflow_mode="scroll",
                dock_visible=True,
                actions_visible=True,
                reserve_work_area=True,
                auto_hide=False,
                islands=["dock", "actions"],
            ),
        )
        self.assertEqual(snapshot["home_apps"], snapshot["favorites"])
        self.assertEqual(snapshot["renderer"], "phosh")
        self.assertEqual(snapshot["shelf"]["edge"], "bottom")
        self.assertEqual(json.loads(state.encode_snapshot(snapshot)), snapshot)

    def test_shelf_contract_is_typed_and_rejects_unsupported_policy(self):
        shelf = state.normalize_shelf(
            layout_version=1,
            edge="left",
            dock_anchor="start",
            actions_anchor="end",
            group_layout="split-ends",
            surface_mode="connected",
            edge_mode="protruding",
            material="glass",
            monitor_mode="all",
            overflow_mode="scroll",
            dock_visible=True,
            actions_visible=False,
            reserve_work_area=True,
            auto_hide=False,
            islands=["dock", "dock"],
        )
        self.assertEqual(shelf["islands"], ["dock"])
        self.assertFalse(shelf["span_full"])
        self.assertTrue(shelf["float_ends"])
        self.assertEqual(shelf["padding"], 10)
        self.assertEqual(state.normalize_shelf(**{**shelf, "group_layout": "luma"})["group_layout"], "luma")
        for padding in [4, 10, 24]:
            self.assertEqual(state.normalize_shelf(**{**shelf, "padding": padding})["padding"], padding)
        for padding in [3, 25, 10.5, True, "10"]:
            with self.assertRaises(state.InvalidState):
                state.normalize_shelf(**{**shelf, "padding": padding})
        spanning = state.normalize_shelf(**{**shelf, "span_full": True, "float_ends": False})
        self.assertTrue(spanning["span_full"])
        self.assertFalse(spanning["float_ends"])
        self.assertFalse(state.normalize_shelf(**{**spanning, "span_full": False})["float_ends"])
        with self.assertRaises(state.InvalidState):
            state.normalize_shelf(**{**shelf, "auto_hide": True})


    def test_arrangement_normalises_like_the_shell(self):
        groups = [
            {"display": "", "edge": "top", "anchor": "center", "position": 0.5, "islands": ["clock", "bogus"]},
            {"display": "", "edge": "sideways", "anchor": "start", "position": 0.5, "islands": ["media"]},
            {"display": "DP-2|DEL|0x2|X", "edge": "bottom", "anchor": "nowhere", "position": 7,
             "islands": ["dock", "clock", "live"]},
            {"display": 5, "edge": "left", "anchor": "free", "position": "x", "islands": "media"},
            {"edge": "right", "islands": []},
            "not a group",
        ]
        normalized = state.normalize_arrangement(groups)
        self.assertEqual(normalized[0]["islands"], ["clock"])
        self.assertEqual(normalized[1], {"display": "DP-2|DEL|0x2|X", "edge": "bottom", "anchor": "center",
                                         "position": 0.5, "islands": ["dock", "live"]})
        self.assertEqual(normalized[2], {"display": "", "edge": "left", "anchor": "free", "position": 0.5,
                                         "islands": ["media"]})
        self.assertEqual(len(normalized), 3)
        self.assertEqual(state.normalize_arrangement([]), [])
        self.assertEqual(state.normalize_arrangement(None), [])
        self.assertEqual(state.SHELF_PLACEABLE_ISLANDS,
                         ("dock", "live", "media", "well", "quick-options", "clock", "notifications"))

    def test_shelf_snapshot_carries_the_arrangement(self):
        shelf = state.normalize_shelf(
            layout_version=1, edge="bottom", dock_anchor="center", actions_anchor="center",
            group_layout="centered-combined", surface_mode="separate", edge_mode="floating",
            material="dark", monitor_mode="primary", overflow_mode="scroll", dock_visible=True,
            actions_visible=True, reserve_work_area=True, auto_hide=False, islands=["dock"],
            arrangement=[{"edge": "left", "anchor": "center", "islands": ["dock"]}])
        self.assertEqual(shelf["arrangement"], [{"display": "", "edge": "left", "anchor": "center",
                                                 "position": 0.5, "islands": ["dock"]}])
        default = state.normalize_shelf(**{**shelf, "arrangement": None})
        self.assertEqual(default["arrangement"], [])

    def test_schema_declares_the_arrangement_key(self):
        from pathlib import Path
        schema = (Path(__file__).resolve().parents[2] / "src/luma-shell-state/org.project_luma.shell-state.gschema.xml").read_text()
        self.assertIn('<key name="shelf-arrangement" type="aa{sv}">', schema)
        self.assertIn("<default>[]</default>", schema.split('name="shelf-arrangement"')[1].split("</key>")[0])

    def test_free_positions_snap_without_free_placement(self):
        groups = [{"edge": "top", "anchor": "free", "position": 0.1, "islands": ["media"]},
                  {"edge": "left", "anchor": "free", "position": 0.9, "islands": ["dock"]},
                  {"edge": "bottom", "anchor": "free", "position": 0.5, "islands": ["clock"]}]
        snapped = state.normalize_arrangement(groups, free_placement=False)
        self.assertEqual([g["anchor"] for g in snapped], ["start", "end", "center"])
        self.assertTrue(all(g["position"] == 0.5 for g in snapped))
        kept = state.normalize_arrangement(groups, free_placement=True)
        self.assertEqual([g["anchor"] for g in kept], ["free", "free", "free"])
        self.assertEqual(kept[0]["position"], 0.1)

    def test_schema_declares_free_placement_off(self):
        from pathlib import Path
        schema = (Path(__file__).resolve().parents[2] / "src/luma-shell-state/org.project_luma.shell-state.gschema.xml").read_text()
        block = schema.split('name="shelf-free-placement"')[1].split("</key>")[0]
        self.assertIn("<default>false</default>", block)

if __name__ == "__main__":
    unittest.main()
