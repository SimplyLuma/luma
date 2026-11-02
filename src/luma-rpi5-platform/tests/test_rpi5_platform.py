# SPDX-License-Identifier: Apache-2.0
"""Static and runnable checks for luma-rpi5-platform and its SD card sources."""
import configparser
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SHELL_SCHEMA = HERE.parent / "luma-shell-state" / "org.project_luma.shell-state.gschema.xml"
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

    def test_shell_default_is_backdrop_blur_not_reduce_transparency(self):
        override = configparser.ConfigParser()
        override.read(HERE / "rpi5.gschema.override")
        self.assertEqual(dict(override["org.project_luma.shell-state"]), {"backdrop-blur": "false"})

    @unittest.skipUnless(SHELL_SCHEMA.is_file() and shutil.which("glib-compile-schemas"),
                         "Shell state schema or glib-compile-schemas unavailable")
    def test_override_compiles_strictly_against_the_shell_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            shutil.copy(SHELL_SCHEMA, directory)
            shutil.copy(HERE / "rpi5.gschema.override", Path(directory) / "90-luma-rpi5.gschema.override")
            subprocess.run(["glib-compile-schemas", "--strict", directory], check=True)
            self.assertTrue((Path(directory) / "gschemas.compiled").is_file())

    def test_service_runs_only_on_pi5_boards(self):
        unit = (HERE / "luma-rpi5-shell-defaults.service").read_text()
        conditions = re.findall(r"^ConditionFirmware=(.*)$", unit, re.M)
        self.assertEqual(conditions, [f"|device-tree-compatible({name})" for name in PI5])

    def test_generator_is_silent_without_the_pi_schemas(self):
        if Path("/run/luma-rpi5/schemas/gschemas.compiled").exists():
            self.skipTest("running on a configured Pi 5")
        result = subprocess.run([str(HERE / "62-luma-rpi5-schemas")], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, "")

    def test_gsk_generator_picks_gl_only_on_a_pi5(self):
        import tempfile
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

    def test_config_txt_has_no_firmware_overlays(self):
        # Linux boots with the tree U-Boot loads from dtb/broadcom; firmware
        # overlays never reach it.
        active = [line for line in (HERE / "card" / "config.txt").read_text().splitlines()
                  if not line.lstrip().startswith("#")]
        self.assertFalse([line for line in active if line.startswith("dtoverlay")])

    def test_hdmi_audio_overlay_matches_the_bcm2712_dma_binding(self):
        dts = (HERE / "card" / "dtb" / "luma-rpi5-hdmi-audio.dtso").read_text()
        self.assertIn('target-path = "/soc@107c000000";', dts)
        self.assertIn('compatible = "brcm,bcm2712-dma";', dts)
        self.assertIn("reg = <0x00010600 0x600>;", dts)
        self.assertIn("brcm,dma-channel-mask = <0x07c0>;", dts)
        # DREQ | burst (bit 30) | wide source (bit 24) | QoS 10 | panic QoS 15
        flags = (1 << 30) | (1 << 24) | (10 << 16) | (15 << 20)
        for target, dreq in (("hdmi0", 10), ("hdmi1", 17)):
            fragment = dts[dts.index(f"target = <&{target}>;"):]
            self.assertIn(f"dmas = <&dma40 {flags | dreq:#010x}>;", fragment.split("};")[0])
            self.assertIn('dma-names = "audio-rx";', fragment.split("};")[0])

    def test_hdmi_audio_d0_fragment_uses_the_d0_request_lines(self):
        # Raspberry Pi's bcm2712d0-rpi-5-b.dts: HDMI0 DREQ 12, HDMI1 DREQ 13.
        dts = (HERE / "card" / "dtb" / "luma-rpi5-hdmi-audio-d0.dtso").read_text()
        flags = (1 << 30) | (1 << 24) | (10 << 16) | (15 << 20)
        for target, dreq in (("hdmi0", 12), ("hdmi1", 13)):
            fragment = dts[dts.index(f"target = <&{target}>;"):]
            self.assertIn(f"dmas = <&dma40 {flags | dreq:#010x}>;", fragment.split("};")[0])

    @unittest.skipUnless(shutil.which("dtc"), "dtc unavailable")
    def test_hdmi_audio_overlay_compiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                ["dtc", "-q", "-@", "-I", "dts", "-O", "dtb", "-o", f"{tmp}/o.dtbo",
                 str(HERE / "card" / "dtb" / "luma-rpi5-hdmi-audio.dtso")],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run(
                ["dtc", "-q", "-@", "-I", "dts", "-O", "dtb", "-o", f"{tmp}/d0.dtbo",
                 str(HERE / "card" / "dtb" / "luma-rpi5-hdmi-audio-d0.dtso")],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

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
