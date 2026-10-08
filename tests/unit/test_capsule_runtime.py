# SPDX-License-Identifier: Apache-2.0
"""Capsule runtime policy: what every Debian/Fedora application capsule gets."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src/luma-installer"))

from luma_installer import capsule_runtime  # noqa: E402
from luma_installer.desktop import desktop_mime_types  # noqa: E402


class CapsuleRuntime(unittest.TestCase):
    def test_graphics_stack_for_both_capsule_kinds(self):
        deb = capsule_runtime.install_command("deb")
        self.assertIn("libgl1-mesa-dri", deb)
        self.assertIn("--no-install-recommends", deb)
        rpm = capsule_runtime.install_command("rpm")
        self.assertIn("mesa-dri-drivers", rpm)
        self.assertIn("libglvnd-gles", rpm)
        self.assertEqual(capsule_runtime.language_pack("rpm", "en_US.UTF-8"), "glibc-langpack-en")
        self.assertEqual(capsule_runtime.language_pack("rpm", "C.UTF-8"), "")
        self.assertEqual(capsule_runtime.language_pack("deb", "de_DE.UTF-8"), "")

    def test_a_debian_capsule_is_given_the_persons_locale(self):
        command = capsule_runtime.locale_command("deb", "de_DE.UTF-8")
        self.assertEqual(command[:2], ["sh", "-c"])
        self.assertEqual(command[-1], "de_DE")
        self.assertIn("localedef", command[2])
        self.assertIsNone(capsule_runtime.locale_command("deb", "C.UTF-8"))
        self.assertIsNone(capsule_runtime.locale_command("rpm", "en_US.UTF-8"))
        self.assertIsNone(capsule_runtime.locale_command("deb", "en_US; rm -rf /"))
        self.assertTrue(capsule_runtime.upgrade_needed("deb", 3))
        self.assertFalse(capsule_runtime.upgrade_needed("rpm", 3))

    def test_chromium_payloads_are_recognised_by_their_files(self):
        self.assertTrue(capsule_runtime.is_chromium("/usr/lib/claude-desktop/resources/app.asar\n/usr/bin/claude-desktop"))
        self.assertTrue(capsule_runtime.is_chromium("/opt/Slack/chrome-sandbox"))
        self.assertFalse(capsule_runtime.is_chromium("/usr/bin/htop\n/usr/share/doc/htop/copyright"))
        # An Electron bundle that ships none of the first five markers was
        # taken for an ordinary program, so it was started with no switches at
        # all and fell to Electron's X11 default. These files are Chromium's
        # and nothing else's.
        self.assertTrue(capsule_runtime.is_chromium("/usr/lib/chatgpt/libffmpeg.so\n/usr/bin/chatgpt"))
        self.assertTrue(capsule_runtime.is_chromium("/usr/lib/x/icudtl.dat"))
        self.assertTrue(capsule_runtime.is_chromium("/usr/lib/x/chrome_crashpad_handler"))
        self.assertTrue(capsule_runtime.is_chromium("/usr/lib/x/snapshot_blob.bin"))
        # Still nothing that merely mentions chrome in a path.
        self.assertFalse(capsule_runtime.is_chromium("/usr/share/doc/chrome-gnome-shell/README"))

    def test_every_capsule_gets_room_to_draw_and_chromium_gets_wayland_and_the_keyring(self):
        self.assertIn("--shm-size=2g", capsule_runtime.launch_arguments({}))
        flags = capsule_runtime.payload_arguments({"chromium": True}, wayland=True)
        self.assertEqual(flags[0], "--ozone-platform=wayland")
        self.assertIn("--password-store=gnome-libsecret", flags)
        self.assertEqual(capsule_runtime.payload_arguments({"chromium": False}, wayland=True), [])
        self.assertIn("XDG_CURRENT_DESKTOP", capsule_runtime.DESKTOP_IDENTITY)

    def test_a_link_handed_to_a_running_electron_app_never_lands_on_a_corrupting_argument_count(self):
        record = {"chromium": True}
        # Figma: five arguments of its own and three capsule flags; the sign-in
        # link would make nine, which Electron 41 turns into a SIGKILL.
        handoff = capsule_runtime.handoff_arguments(record, True, 8, ["figma://app_auth/redeem"])
        self.assertEqual(handoff, ["figma://app_auth/redeem"])
        # Safe counts keep the flags, in case the application is not single-instance.
        self.assertEqual(capsule_runtime.handoff_arguments(record, True, 20, ["x"])[:-1],
                         list(capsule_runtime.CHROMIUM_FLAGS))
        # Its own arguments already on a bad count: padded past it.
        padded = capsule_runtime.handoff_arguments(record, True, 11, ["x"])
        self.assertEqual(8 + len(padded), 14)
        self.assertEqual(padded[-1], "x")
        self.assertEqual(capsule_runtime.handoff_arguments(record, True, 0, ["x"])[-1], "x")
        self.assertEqual(capsule_runtime.handoff_arguments({"chromium": False}, True, 8, ["x"]), ["x"])

    def test_capsules_get_a_login_environment(self):
        env = dict(capsule_runtime.login_environment("nick"))
        self.assertEqual(env["SHELL"], "/bin/bash")
        self.assertEqual((env["USER"], env["LOGNAME"]), ("nick", "nick"))
        self.assertNotIn("USER", dict(capsule_runtime.login_environment("bad name;")))

    def test_link_schemes_are_exported_from_the_payload_launcher(self):
        entry = "[Desktop Entry]\nName=Claude\nExec=claude-desktop %U\nMimeType=x-scheme-handler/claude;bad value;text/plain;\n"
        self.assertEqual(desktop_mime_types(entry), ["x-scheme-handler/claude", "text/plain"])
        self.assertEqual(desktop_mime_types("[Desktop Entry]\nName=x\n"), [])


if __name__ == "__main__":
    unittest.main()
