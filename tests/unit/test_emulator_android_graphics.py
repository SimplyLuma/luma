"""Reject incompatible codec layouts while provisioning the pinned VM profile."""
import configparser
from pathlib import Path
import runpy
import subprocess
import tempfile
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

SCRIPT = Path(__file__).resolve().parents[2] / "config/emulator/provision/configure-android-graphics.py"

class GraphicsProvisionTests(unittest.TestCase):
    def provision(self, arch="arm64_only", omx_binary=False, running=False, allocator=True, candidate=True, full_image=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        cfg = root / "var/lib/waydroid/waydroid.cfg"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(f"[waydroid]\narch={arch}\nimages_path=/images\n[properties]\nuser.setting=keep\n")
        runtime = root / "etc/luma/android.conf"
        runtime.parent.mkdir(parents=True)
        runtime.write_text("[runtime]\nmulti_window=true\nidle_freeze=false\n")
        def local_path(value):
            return root / str(value).lstrip("/")
        def command(args, **kwargs):
            output = ""
            status = 0
            if args[0] == "systemctl": status = 0 if running else 3
            elif args[0] == "debugfs":
                if args[2] == "cat /lib64/hw/gralloc.default.so":
                    output = b"admitted-test-module" if allocator else b"stock-module"
                elif args[2].startswith("cat "):
                    output = '<manifest><hal><name>android.hardware.media.omx</name></hal></manifest>'
                else:
                    output = "android.hardware.media.omx@1.0-service" if omx_binary else "android.hardware.audio.service"
            return subprocess.CompletedProcess(args, status, output, "")
        original_sha256 = hashlib.sha256
        def module_sha256(data):
            if data == b"admitted-test-module":
                return SimpleNamespace(hexdigest=lambda: "45d6824e13a14bdc1e9bfbff55928117a3053e8ef8a368349ae538e9d027c871" if full_image else "2106130c991a6c4c111de31f79154c3d1a58200bb2b70bab85171bb1563c0ef3")
            return original_sha256(data)
        with patch("sys.argv", [str(SCRIPT)] + (["--software-video-candidate"] if candidate else [])), patch("hashlib.sha256", side_effect=module_sha256), patch("os.geteuid", return_value=0), patch("pathlib.Path", side_effect=local_path), patch("subprocess.run", side_effect=command):
            runpy.run_path(str(SCRIPT))
        config = configparser.ConfigParser(); config.read(cfg)
        return root, config

    def test_arm64_only_enumerates_existing_codec2_without_phantom_omx(self):
        root, cfg = self.provision()
        self.assertEqual(cfg["properties"]["debug.stagefright.ccodec"], "4")
        self.assertEqual(cfg["properties"]["persist.waydroid.multi_windows"], "false")
        self.assertEqual(cfg["properties"]["persist.waydroid.use_subsurface"], "false")
        self.assertEqual(cfg["properties"]["user.setting"], "keep")
        runtime = configparser.ConfigParser(); runtime.read(root / "etc/luma/android.conf")
        self.assertEqual(runtime["runtime"]["multi_window"], "false")
        self.assertEqual(runtime["runtime"]["idle_freeze"], "false")
        hal = ET.parse(root / "var/lib/waydroid/overlay/vendor/etc/vintf/manifest/luma-codec2.xml").getroot().find("hal")
        self.assertEqual(hal.attrib["override"], "true")
        self.assertEqual(hal.findtext("transport"), "hwbinder")
        self.assertEqual(hal.findtext("name"), "android.hardware.media.omx")
        self.assertIsNone(hal.find("version"))

    def test_admitted_coherent_image_keeps_composed_video(self):
        root, cfg = self.provision(full_image=True)
        self.assertEqual(cfg["properties"]["debug.stagefright.ccodec"], "4")
        self.assertEqual(cfg["properties"]["persist.waydroid.multi_windows"], "false")
        self.assertEqual(cfg["properties"]["persist.waydroid.use_subsurface"], "false")

    def test_factory_does_not_enable_unaccepted_video_candidate(self):
        root, cfg = self.provision(candidate=False, allocator=False)
        self.assertNotIn("debug.stagefright.ccodec", cfg["properties"])
        self.assertFalse((root / "var/lib/waydroid/overlay").exists())

    def test_other_architecture_keeps_codec_policy(self):
        root, cfg = self.provision("x86_64")
        self.assertNotIn("debug.stagefright.ccodec", cfg["properties"])
        self.assertFalse((root / "var/lib/waydroid/overlay").exists())

    def test_new_image_with_real_omx_requires_review(self):
        with self.assertRaisesRegex(SystemExit, "Unrecognized"):
            self.provision(omx_binary=True)

    def test_stock_allocator_cannot_enable_broken_video_configuration(self):
        with self.assertRaisesRegex(SystemExit, "admitted software YUV"):
            self.provision(allocator=False)

    def test_live_container_is_not_modified(self):
        with self.assertRaisesRegex(SystemExit, "Stop the Android"):
            self.provision(running=True)

if __name__ == "__main__":
    unittest.main()
