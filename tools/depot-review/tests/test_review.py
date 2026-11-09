# SPDX-License-Identifier: Apache-2.0
import struct
import sys
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from depot_review import checks, images, words  # noqa: E402

METADATA = """[Application]
name=com.example.Hello
runtime=org.gnome.Platform/x86_64/49
sdk=org.gnome.Sdk/x86_64/49
command=hello

[Context]
shared=network;ipc;
sockets=wayland;fallback-x11;pulseaudio;
devices=dri;
filesystems=xdg-music:ro;

[Session Bus Policy]
org.freedesktop.Notifications=talk
org.freedesktop.secrets=talk
org.kde.StatusNotifierWatcher=talk
"""


def png(width, height):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    raw = b"".join(b"\0" + b"\0" * width * 3 for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class Words(unittest.TestCase):
    def test_flatpak_reach(self):
        reach = words.flatpak_reach(METADATA)
        self.assertEqual(list(reach["words"]), ["Files", "Internet", "Notifications", "Other apps", "Passwords"])
        self.assertTrue(any("PulseAudio" in n for n in reach["notes"]))

    def test_sandboxed_app_reaches_nothing(self):
        reach = words.flatpak_reach("[Application]\nname=com.example.Calm\nruntime=org.gnome.Platform/x86_64/49\n"
                                    "[Context]\nsockets=wayland;\n")
        self.assertEqual(reach["words"], {})

    def test_x11_is_screen_and_home_is_files(self):
        reach = words.flatpak_reach("[Application]\nname=com.example.Old\n[Context]\nsockets=x11;\n"
                                    "filesystems=home;\ndevices=all;\n")
        self.assertEqual(set(reach["words"]), {"Screen", "Files", "Devices", "Camera"})

    def test_compare_names_missing_words(self):
        missing = words.compare(["Internet"], {"Internet": ["network"], "Camera": ["devices.camera"]})
        self.assertEqual([m["word"] for m in missing], ["Camera"])
        self.assertIn("doesn't include Camera", missing[0]["message"])

    def test_universal_package(self):
        reach = words.package_reach(["/usr/bin/x", "/usr/lib/systemd/system/x.service",
                                     "/usr/share/applications/x.desktop"], {"postinst": "echo"})
        self.assertEqual(set(reach["words"]), {"Files", "Internet", "System"})
        reach = words.package_reach(["/lib/modules/6.1/extra/x.ko"], {})
        self.assertTrue(reach["refusals"])


class Images(unittest.TestCase):
    def test_png(self):
        self.assertEqual(images.identify(png(4, 3)), ("png", 4, 3))

    def test_truncated_png(self):
        with self.assertRaises(images.ImageError):
            images.identify(png(4, 3)[:-6])

    def test_svg(self):
        self.assertEqual(images.identify(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128"/>'),
                         ("svg", 128, 128))
        with self.assertRaises(images.ImageError):
            images.identify(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><script/></svg>')

    def test_webp_lossless(self):
        bits = (99 & 0x3FFF) | ((49 & 0x3FFF) << 14)
        body = b"WEBP" + b"VP8L" + struct.pack("<I", 15) + b"\x2f" + struct.pack("<I", bits) + b"\0" * 10
        data = b"RIFF" + struct.pack("<I", len(body)) + body
        self.assertEqual(images.identify(data), ("webp", 100, 50))


class Checks(unittest.TestCase):
    policy = checks.load_policy()

    def test_reserved_ids(self):
        self.assertIsNotNone(checks.reserved("org.projectluma.Notes", self.policy, set()))
        self.assertIsNotNone(checks.reserved("com.rhyme.viola", self.policy, {"com.rhyme.viola"}))
        self.assertIsNone(checks.reserved("com.example.Notes", self.policy, set()))

    def test_runtime(self):
        self.assertTrue(checks.runtime_allowed("org.gnome.Platform/x86_64/49", self.policy))
        self.assertTrue(checks.runtime_allowed("org.projectluma.Platform/aarch64/44", self.policy))
        self.assertFalse(checks.runtime_allowed("org.gnome.Platform/x86_64/3.38", self.policy))
        self.assertFalse(checks.runtime_allowed("com.evil.Platform/x86_64/1", self.policy))

    def test_media(self):
        out = checks.media_checks({"icon": {"format": "png", "width": 256, "height": 256},
                                   "screenshots": [{"form": "desktop", "format": "png", "width": 1200,
                                                    "height": 700},
                                                   {"form": "phone", "format": "png", "width": 1170,
                                                    "height": 2532}]},
                                  {"summary": "x" * 61}, "app")
        failed = {c["key"] for c in out if not c["ok"]}
        self.assertEqual(failed, {"summary", "icon"})

    def test_missing_desktop_screenshot(self):
        out = checks.media_checks({"icon": {"format": "svg", "width": 64, "height": 64}, "screenshots": []},
                                  {"summary": "Short"}, "app")
        self.assertIn("screenshots", {c["key"] for c in out if not c["ok"]})

    def test_permissions_flow(self):
        facts = {"reach": words.flatpak_reach(METADATA)}
        out, summary = checks.permission_checks([facts], [{"word": "Internet", "reason": "Sync"},
                                                          {"word": "Files", "reason": "Music"}])
        # Reach is reported, never failed: Depot reads it from the sandbox itself.
        self.assertTrue(all(c["ok"] for c in out))
        self.assertEqual(sorted(summary["detected"]), ["Files", "Internet", "Notifications", "Other apps", "Passwords"])
        out, _ = checks.permission_checks([facts], [])
        self.assertTrue(all(c["ok"] for c in out))

    def test_internal_only_failure_is_attention(self):
        self.assertEqual(checks.outcome([checks.check("malware", False, "x", internal=True)]), "attention")


if __name__ == "__main__":
    unittest.main()
