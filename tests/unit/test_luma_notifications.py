# SPDX-License-Identifier: Apache-2.0

import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class NotificationContractTests(unittest.TestCase):
    def test_shared_tokens_and_timeouts(self):
        note = json.loads((ROOT / "config/shared/design-tokens.json").read_text())["notifications"]
        self.assertEqual(note["font_family"], "Figtree")
        self.assertEqual(note["desktop"]["edge_inset_px"], 14)
        self.assertEqual(note["desktop"]["timeout_ms"], 7000)
        self.assertEqual(note["handheld"]["top_inset_px"], 54)
        self.assertEqual(note["handheld"]["timeout_ms"], 8500)

    def test_desktop_native_ownership(self):
        patch = (ROOT / "patches/gnome-shell/0009-luma-notification-system.patch").read_text()
        lifecycle = (
            ROOT / "patches/gnome-shell/0010-luma-notification-lifecycle-stack.patch"
        ).read_text()
        self.assertIn("Clutter.ActorAlign.END", patch)
        self.assertIn("const NOTIFICATION_TIMEOUT = 7000", patch)
        self.assertIn("get_focus_window", patch)
        self.assertIn("const MAX_VISIBLE_NOTIFICATIONS = 3", lifecycle)
        self.assertIn("const MAX_NOTIFICATIONS_IN_QUEUE = 20", lifecycle)
        self.assertIn("NotificationLifecycleState", lifecycle)
        self.assertIn("NotificationLifecycleState.WAITING", lifecycle)
        self.assertIn("NotificationLifecycleState.SEEN", lifecycle)
        self.assertIn("this._additionalBanners", lifecycle)
        self.assertIn("entry.banner.hover", lifecycle)
        self.assertIn("entry.banner.contains(focusedActor)", lifecycle)
        self.assertNotIn("+        this._notification.acknowledged = true;", lifecycle)
        self.assertNotIn(
            "+        Main.messageTray.bannerAlignment = Clutter.ActorAlign.CENTER", patch
        )

    def test_handheld_waiting_and_quick_options(self):
        patch = (ROOT / "patches/phosh/0012-luma-notification-system.patch").read_text()
        fixups = (ROOT / "patches/phosh/0014-luma-notification-quick-options-fixups.patch").read_text()
        lifecycle = (
            ROOT / "patches/phosh/0015-luma-notification-lifecycle.patch"
        ).read_text()
        self.assertIn("LUMA_BANNER_TIMEOUT_MS 8500", patch)
        self.assertIn("priv->notification_banners->len < 3", patch)
        self.assertIn("phosh_notification_get_transient", patch)
        self.assertIn("Nothing waiting. New alerts will collect here.", patch)
        self.assertIn("gtk_container_remove (GTK_CONTAINER (self->box_bottom_half)", fixups)
        self.assertIn("gtk_box_reorder_child (GTK_BOX (self->box_settings)", fixups)
        self.assertIn("luma_banner_top_inset", fixups)
        self.assertIn("atk_object_set_name", fixups)
        self.assertIn("count > 99", fixups)
        self.assertIn("PHOSH_NOTIFICATION_URGENCY_CRITICAL", fixups)
        self.assertIn('-                  <object class="GtkBox" id="luma_actions_row">', fixups)
        self.assertNotIn("TransientSurfaceChanged", patch)
        self.assertNotIn("luma-surface-deep", patch)
        for state in (
            "QUEUED",
            "ARRIVING",
            "WAITING",
            "SEEN",
            "ACTED",
            "DISMISSED",
            "EXPIRED",
            "REPLACED",
        ):
            self.assertIn(f"PHOSH_NOTIFICATION_LIFECYCLE_{state}", lifecycle)
        self.assertIn("phosh_notify_manager_mark_all_seen", lifecycle)
        self.assertIn("PHOSH_NOTIFICATION_LIFECYCLE_WAITING", lifecycle)

    def test_package_releases_are_synchronized(self):
        inputs = (ROOT / "config/desktop/inputs.env").read_text()
        desktop = (ROOT / "config/desktop/packages.txt").read_text()
        shared = (ROOT / "config/shared/application-packages.txt").read_text()
        desktop_composer = (ROOT / "scripts/vm/compose-desktop-image.sh").read_text()
        mobile_composer = (ROOT / "scripts/mobile/compose-fp6-rootfs.sh").read_text()
        phosh = (ROOT / "packaging/rpm/luma-phosh.spec").read_text()
        values = dict(line.split("=", 1) for line in inputs.splitlines()
                      if line and not line.startswith("#") and "=" in line)
        self.assertIn(values["GNOME_SHELL_NEVRA"], desktop)
        self.assertIn(values["GNOME_SHELL_LUMA_RELEASE"], values["GNOME_SHELL_NEVRA"])
        self.assertRegex(phosh, r"(?m)^Release:\s+1\.luma\.\d+%\{\?dist\}$")
        self.assertIn(values["LUMA_PHOSH_RELEASE"], values["LUMA_PHOSH_AARCH64_NEVRA"])
        self.assertIn("Patch11:        0012-luma-notification-system.patch", phosh)
        self.assertIn("Patch13:        0014-luma-notification-quick-options-fixups.patch", phosh)
        self.assertIn("Patch14:        0015-luma-notification-lifecycle.patch", phosh)
        self.assertIn("Patch17:        0018-luma-single-owner-touch-drawers.patch", phosh)
        self.assertIn(values["PRAIRIE_CORE_APPS_RELEASE"], values["PRAIRIE_CORE_APPS_NEVRA"])
        self.assertIn(values["PRAIRIE_CORE_APPS_NEVRA"], desktop)
        self.assertEqual(shared.splitlines().count("prairie-core-apps"), 1)
        self.assertIn("$PRAIRIE_CORE_APPS_NEVRA.rpm", desktop_composer)
        self.assertIn("$PRAIRIE_CORE_APPS_NEVRA.rpm", mobile_composer)

    def test_messages_uses_actionable_notification_protocol(self):
        daemon = (ROOT / "src/prairie-core/bin/prairie-messages-daemon").read_text()
        self.assertIn("org.freedesktop.Notifications", daemon)
        self.assertIn('"desktop-entry": GLib.Variant', daemon)
        self.assertIn('["default", "Open", "reply", "Reply", "mark-read", "Mark read"]', daemon)
        self.assertIn("notification_by_address", daemon)
        self.assertIn("Gio.AppInfo.launch_default_for_uri", daemon)
        self.assertIn("self.store.mark_read(address)", daemon)
        self.assertNotIn("notify-send", daemon)

    def test_deterministic_approval_publishers(self):
        publisher = (ROOT / "tests/integration/luma-notification-publisher.py").read_text()
        self.assertIn("Nora Feld", publisher)
        self.assertIn("Design review", publisher)
        self.assertIn("replaces_id", publisher)
        self.assertIn("org.freedesktop.Notifications", publisher)
        self.assertIn("ActionInvoked", publisher)
        self.assertIn("NotificationClosed", publisher)
        self.assertIn("CloseNotification", publisher)


if __name__ == "__main__":
    unittest.main()
