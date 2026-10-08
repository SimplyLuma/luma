#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/prairie-core/prairie_apps/phone.py"
CONTROL = ROOT / "src/prairie-core/prairie_apps/phone_control.py"
BACKEND = ROOT / "src/prairie-core/prairie_apps/phone_backend.py"
CSS = ROOT / "src/prairie-core/style/phone.css"
DESKTOP = ROOT / "src/prairie-core/data/org.projectluma.Phone.desktop"
APPKIT_CSS = ROOT / "src/luma-platform/appkit/luma-appkit.css"
DAEMON = ROOT / "src/prairie-core/bin/prairie-phone-daemon"
SYSTEM = ROOT / "src/prairie-core/prairie_apps/phone_system.py"
AUDIO_SERVICE = (
    ROOT / "config/mobile/fp6-physical/overlay/usr/libexec/"
    "luma-fp6-audio-route-service"
)


class PhoneAppKitContractTests(unittest.TestCase):
    def test_phone_uses_v70_places_and_shared_window(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        places = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "PLACES" for target in node.targets))
        self.assertEqual(tuple(item[0] for item in ast.literal_eval(places)),
                         ("pad", "recents", "contacts", "vm"))
        window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PhoneWindow")
        self.assertEqual([base.id for base in window.bases], ["AppWindow"])
        self.assertNotIn("Cairo", source)
        self.assertNotIn("Simulate incoming", source)

    def test_fixture_is_selected_before_live_service_start(self) -> None:
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PhoneWindow")
        constructor = next(node for node in window.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
        self.assertIn("source_from_environment()", ast.unparse(constructor.body[0]))
        branch = next(node for node in constructor.body if isinstance(node, ast.If)
                      and ast.unparse(node.test) == "self.fixture")
        self.assertNotIn("_start_live", ast.unparse(ast.Module(body=branch.body, type_ignores=[])))
        self.assertIn("_start_live", ast.unparse(ast.Module(body=branch.orelse, type_ignores=[])))

    def test_native_state_drives_connected_timer_and_restart_recovery(self) -> None:
        control = CONTROL.read_text(encoding="utf-8")
        backend = BACKEND.read_text(encoding="utf-8")
        self.assertIn("CallSession.from_native", control)
        self.assertIn("transport.calls()", control)
        self.assertIn('self._call("GetCalls"', backend)
        self.assertIn('self._call("SendDtmf"', backend)
        self.assertNotIn("waydroid", control.casefold())

    def test_phone_css_never_overrides_shared_parts_or_defines_literal_visuals(self) -> None:
        css = re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)
        self.assertNotIn("@define-color", css)
        self.assertNotRegex(css, r"\.(?:luma|lumaui)-")
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}|rgba?\(|oklch\(|[0-9]px")
        self.assertNotRegex(css, r"font-(?:size|weight|family)\s*:")
        self.assertTrue(all(selector.strip().startswith(".pn-")
                            for rule in css.split("}") if "{" in rule
                            for selector in rule.split("{")[0].split(",")))

    def test_phone_declares_bounded_shell_status_surface(self) -> None:
        desktop = DESKTOP.read_text(encoding="utf-8")
        self.assertIn("X-Luma-Status-Surface=deep", desktop)
        self.assertIn("X-Luma-Status-Surfaces=deep;immersive;", desktop)
        self.assertNotIn("X-Luma-Status-Color=", desktop)

    def test_phone_uses_kit_lucide_glyphs(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("luma-phone-", source)
        self.assertNotIn("cairo", source.casefold())
        self.assertIn("icons.image(", source)

    def test_call_surface_leases_immersive_shell_edges(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        system = SYSTEM.read_text(encoding="utf-8")
        daemon = DAEMON.read_text(encoding="utf-8")
        self.assertIn("class TransientShellSurface", system)
        self.assertIn('self.shell_surface.set("immersive")', source)
        self.assertIn("self.shell_surface.clear()", source)
        self.assertIn('self._shell_surface.set("immersive")', daemon)
        self.assertIn("self._shell_surface.clear()", daemon)

    def test_incoming_presenter_reuses_phone_and_appkit_surfaces(self) -> None:
        daemon = DAEMON.read_text(encoding="utf-8")
        self.assertIn("from luma_appkit import Avatar, install_appkit", daemon)
        self.assertIn('add_css_class("phone-call-surface")', daemon)
        self.assertIn('add_css_class("phone-incoming-icon")', daemon)
        self.assertIn('add_css_class("phone-call-control-icon")', daemon)
        self.assertIn('add_css_class("phone-end-icon")', daemon)
        self.assertIn('"Last spoke"', daemon)
        self.assertIn('"Last text"', daemon)
        self.assertIn("Call Back", daemon)
        self.assertIn("Try Again", daemon)
        self.assertIn("Message", daemon)
        self.assertNotIn("Adw.StatusPage", daemon)

    def test_active_call_indicator_uses_shell_owned_structured_notification(self) -> None:
        source = SYSTEM.read_text(encoding="utf-8")
        self.assertIn("org.freedesktop.Notifications", source)
        self.assertIn('"resident": GLib.Variant("b", True)', source)
        self.assertIn('"desktop-entry": GLib.Variant', source)
        self.assertIn('"Return to call"', source)
        self.assertIn('"Mute"', source)
        self.assertIn('"End"', source)
        self.assertNotIn("waydroid", source.casefold())

    def test_unlocked_incoming_call_uses_persistent_shell_actions(self) -> None:
        source = SYSTEM.read_text(encoding="utf-8")
        daemon = DAEMON.read_text(encoding="utf-8")
        self.assertIn("class IncomingCallIndicator", source)
        self.assertIn('"answer", "Answer"', source)
        self.assertIn('"decline", "Decline"', source)
        self.assertIn('"resident": GLib.Variant("b", True)', source)
        self.assertIn("if not self._incoming_locked:", daemon)
        self.assertIn("self._show_incoming_surface()", daemon)
        self.assertIn("lockscreen_identity_visible()", daemon)
        self.assertIn('"Unlock to view"', daemon)

    def test_speaker_control_uses_narrow_native_audio_adapter(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        backend = BACKEND.read_text(encoding="utf-8")
        service = AUDIO_SERVICE.read_text(encoding="utf-8")
        self.assertIn("set_audio_output_route(route)", CONTROL.read_text(encoding="utf-8"))
        self.assertIn('AUDIO_ROUTE_BUS_NAME = "org.projectluma.AudioRoute1"', backend)
        self.assertEqual(service.count('ROUTES = frozenset({"speaker", "earpiece"})'), 1)
        self.assertNotIn("sudo", service)
        self.assertNotIn("shell=True", service)
        self.assertNotIn("waydroid", service.casefold())


if __name__ == "__main__":
    unittest.main()
