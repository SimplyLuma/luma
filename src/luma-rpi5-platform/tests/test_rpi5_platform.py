# SPDX-License-Identifier: Apache-2.0
"""Static and runnable checks for luma-rpi5-platform and its SD card sources."""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
PI5 = ("raspberrypi,5-model-b", "raspberrypi,5-model-b-d0")


def render(text, volid="Luma-Test-1", args="clk_ignore_unused pd_ignore_unused"):
    return (text.replace("@ISO_VOLID@", volid).replace("@INSTALLER_ARGS@", args)
            .replace("@ISO_NAME@", "test.iso"))


class Platform(unittest.TestCase):
    def test_v3d_rule_is_scoped_to_the_bcm2712_gpu_after_bind(self):
        rule = (HERE / "70-luma-rpi5-v3d.rules").read_text()
        active = [line for line in rule.splitlines() if line and not line.startswith("#")]
        self.assertEqual(len(active), 1)
        for needle in ('ACTION=="bind"', 'DRIVER=="v3d"', 'ENV{OF_COMPATIBLE_0}=="brcm,2712-v3d"',
                       'ATTR{power/autosuspend_delay_ms}="2000"'):
            self.assertIn(needle, active[0])

    def test_no_shell_defaults_on_the_pi(self):
        # Backdrop blur costs ~15-20% on the Pi 5 since the partial-redraw and
        # V3D blit work, so the Pi keeps the stock Shell defaults.
        self.assertEqual(sorted(p.name for p in HERE.glob("*.gschema.override")), [])

    def test_update_stream_is_the_pi5_target(self):
        import configparser
        conf = configparser.ConfigParser()
        conf.read(HERE / "update.conf")
        update = conf["update"]
        self.assertEqual(update["hardware_target"], "raspberrypi,rpi5")
        self.assertEqual(update["ref_template"], "luma/1/{arch}/raspberrypi-rpi5/{channel}")
        self.assertTrue(update["graph_url"].endswith("/os/graph/aarch64/raspberrypi-rpi5/{channel}.json"))

    def test_system_sleep_is_off(self):
        import configparser
        conf = configparser.ConfigParser()
        conf.optionxform = str
        conf.read(HERE / "50-luma-rpi5-no-sleep.conf")
        self.assertEqual(dict(conf["Sleep"]), {"AllowSuspend": "no", "AllowHibernation": "no",
                                               "AllowHybridSleep": "no", "AllowSuspendThenHibernate": "no"})
        spec = HERE.parents[1] / "packaging/rpm/luma-rpi5-platform.spec"
        if spec.is_file():  # not in the RPM's own %check
            self.assertIn("%{_prefix}/lib/systemd/sleep.conf.d/50-luma-rpi5-no-sleep.conf", spec.read_text())

    def test_firmware_sync_runs_only_on_pi5_boards(self):
        unit = (HERE / "luma-rpi5-firmware-sync.service").read_text()
        conditions = re.findall(r"^ConditionFirmware=\|device-tree-compatible\((.*)\)$", unit, re.M)
        self.assertTrue(set(PI5) <= set(conditions), conditions)

    def test_gsk_generator_picks_gl_only_on_a_pi5(self):
        gen = str(HERE / "63-luma-rpi5-gsk-renderer")
        cases = {b"raspberrypi,5-model-b\0brcm,bcm2712\0": "GSK_RENDERER=gl\n",
                 b"raspberrypi,5-model-b-d0\0brcm,bcm2712\0": "GSK_RENDERER=gl\n",
                 b"raspberrypi,4-model-b\0brcm,bcm2711\0": "",
                 b"qcom,sm8650\0": ""}
        with tempfile.TemporaryDirectory() as tmp:
            compat = Path(tmp) / "compatible"
            for blob, want in cases.items():
                compat.write_bytes(blob)
                env = {"PATH": "/usr/bin:/bin", "LUMA_RPI5_DT_COMPATIBLE": str(compat)}
                out = subprocess.run([gen], capture_output=True, text=True, check=True, env=env).stdout
                self.assertEqual(out, want, blob)
            env = {"PATH": "/usr/bin:/bin", "LUMA_RPI5_DT_COMPATIBLE": str(compat), "GSK_RENDERER": "vulkan"}
            compat.write_bytes(b"raspberrypi,5-model-b\0")
            self.assertEqual(subprocess.run([gen], capture_output=True, text=True, check=True, env=env).stdout, "")
            env = {"PATH": "/usr/bin:/bin", "LUMA_RPI5_DT_COMPATIBLE": str(Path(tmp) / "missing")}
            self.assertEqual(subprocess.run([gen], capture_output=True, text=True, check=True, env=env).stdout, "")

    def test_boot_next_refuses_unknown_entries_before_touching_anything(self):
        for argv in ([], ["installed-debug"], ["install", "extra"]):
            result = subprocess.run([str(HERE / "luma-rpi5-boot-next"), *argv], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, argv)


class Card(unittest.TestCase):
    def setUp(self):
        self.grub = (HERE / "card" / "grub.cfg.in").read_text()

    def test_config_txt_boots_u_boot_on_pi5(self):
        lines = (HERE / "card" / "config.txt").read_text().splitlines()
        section = lines[lines.index("[pi5]"):lines.index("[all]")]
        for line in ("kernel=u-boot.bin", "os_check=0", "arm_64bit=1"):
            self.assertIn(line, section)

    def test_firmware_layout_config_carries_kms_cma_and_pcie(self):
        # kernel-luma-rpi runs on the firmware's tree: these reach Linux.
        lines = (HERE / "card" / "config.txt").read_text().splitlines()
        section = lines[lines.index("[pi5]"):lines.index("[all]")]
        self.assertIn("dtoverlay=vc4-kms-v3d-pi5,cma-256", section)
        self.assertIn("dtparam=pciex1", section)
        self.assertEqual(lines[-1].lstrip("# ").split(":")[0], "for example")

    def test_uboot_layout_config_has_no_firmware_overlays(self):
        # Fedora's kernel boots with the tree U-Boot loads from dtb/broadcom.
        text = (HERE / "card" / "config-uboot.txt").read_text()
        active = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
        self.assertIn("kernel=u-boot.bin", active)
        self.assertFalse([line for line in active if line.startswith(("dtoverlay", "dtparam"))])

    def _sync(self, esp, dtb, config="kernel=u-boot.bin\n"):
        if config is not None:
            (esp / "config.txt").write_text(config)
        env = {"LUMA_RPI5_ESP": str(esp), "LUMA_RPI5_DTB": str(dtb), "PATH": "/usr/bin:/bin"}
        return subprocess.run([str(HERE / "luma-rpi5-firmware-sync")], env=env,
                              capture_output=True, text=True, check=True).stdout

    def _kernel_dtbs(self, root):
        (root / "broadcom").mkdir(parents=True)
        (root / "overlays").mkdir()
        (root / "broadcom" / "bcm2712d0-rpi-5-b.dtb").write_bytes(b"d0")
        (root / "broadcom" / "bcm2712-rpi-5-b.dtb").write_bytes(b"c1")
        (root / "broadcom" / "other-vendor.dtb").write_bytes(b"x")
        (root / "overlays" / "overlay_map.dtb").write_bytes(b"map")
        (root / "overlays" / "vc4-kms-v3d-pi5.dtbo").write_bytes(b"kms")

    def test_firmware_sync_copies_trees_and_overlays_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            esp, dtb = Path(tmp, "esp"), Path(tmp, "dtb")
            esp.mkdir(); self._kernel_dtbs(dtb)
            self.assertIn("4 files updated", self._sync(esp, dtb))
            self.assertEqual((esp / "bcm2712d0-rpi-5-b.dtb").read_bytes(), b"d0")
            self.assertEqual((esp / "overlays" / "vc4-kms-v3d-pi5.dtbo").read_bytes(), b"kms")
            self.assertFalse((esp / "other-vendor.dtb").exists())
            self.assertEqual((esp / "config.txt").read_text(), "kernel=u-boot.bin\n")
            self.assertIn("0 files updated", self._sync(esp, dtb, config=None))
            self.assertFalse(list(esp.rglob("*.luma-new")))

    def test_firmware_sync_leaves_uboot_layout_and_fedora_kernels_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            esp, dtb = Path(tmp, "esp"), Path(tmp, "dtb")
            esp.mkdir(); self._kernel_dtbs(dtb)
            (esp / "dtb").mkdir()
            self.assertIn("not syncing", self._sync(esp, dtb))
            self.assertFalse((esp / "bcm2712d0-rpi-5-b.dtb").exists())
            (esp / "dtb").rmdir()
            (dtb / "overlays" / "overlay_map.dtb").unlink()
            self.assertIn("not a Raspberry Pi kernel", self._sync(esp, dtb))
            (dtb / "overlays" / "overlay_map.dtb").write_bytes(b"map")
            self.assertIn("not a Luma Pi boot partition", self._sync(esp, dtb, config=""))

    def test_firmware_sync_unit_runs_after_a_good_boot_on_pi5_boards(self):
        unit = (HERE / "luma-rpi5-firmware-sync.service").read_text()
        self.assertIn("After=boot-complete.target", unit)
        self.assertIn("RequiresMountsFor=/boot/efi", unit)
        self.assertIn("ConditionFirmware=|device-tree-compatible(raspberrypi,5-model-b)", unit)

    def test_template_has_only_the_known_placeholders(self):
        self.assertEqual(set(re.findall(r"@[A-Z_]+@", self.grub)), {"@ISO_VOLID@", "@INSTALLER_ARGS@", "@ISO_NAME@"})

    def test_installer_entries_never_pass_console(self):
        active = "\n".join(line for line in self.grub.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("console=", active)
        self.assertNotIn("inst.sshd", active)

    def test_installed_entry_hands_off_without_naming_a_deployment(self):
        entry = self.grub[self.grub.index("--id installed"):self.grub.index("--id install {")]
        self.assertIn("source ($luma_esp)/EFI/fedora/bootuuid.cfg", entry)
        self.assertIn("search --no-floppy --fs-uuid --set=boot $BOOT_UUID", entry)
        self.assertIn("set prefix=($boot)/grub2", entry)
        self.assertIn("configfile ($boot)/grub2/grub.cfg", entry)
        for forbidden in ("/ostree/", "vmlinuz", "chainloader", "linux "):
            self.assertNotIn(forbidden, entry)
        self.assertNotRegex(self.grub, r"[0-9a-f]{8}-[0-9a-f]{4}-")

    def test_one_shot_and_fallback(self):
        self.assertIn('set default="installed"\n  set fallback="install"', self.grub)
        self.assertIn("load_env -f ($luma_card)/EFI/BOOT/grubenv next_entry", self.grub)
        self.assertIn('set fallback="$default"\n  set default="$next_entry"\n  set next_entry=\n'
                      "  save_env -f ($luma_card)/EFI/BOOT/grubenv next_entry", self.grub)
        self.assertIn("terminal_output gfxterm", self.grub)

    @unittest.skipUnless(shutil.which("grub2-script-check"), "grub2-script-check unavailable")
    def test_rendered_menu_is_valid_grub(self):
        with tempfile.NamedTemporaryFile("w", suffix=".cfg") as handle:
            handle.write(render(self.grub))
            handle.flush()
            result = subprocess.run(["grub2-script-check", handle.name], capture_output=True, text=True)
            output = (result.stdout + result.stderr).lower()
            self.assertEqual(result.returncode, 0, output)
            self.assertNotIn("error", output)


if __name__ == "__main__":
    unittest.main()
