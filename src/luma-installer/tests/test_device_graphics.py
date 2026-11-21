# SPDX-License-Identifier: Apache-2.0
"""Graphics drivers come from the device (ADR-052): which library wins, and when the C library moves."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from closure_fixtures import write_elf
from luma_installer import device_graphics as graphics
from luma_installer.errors import InstallerError


class Versions(unittest.TestCase):
    def test_debian_version_order(self):
        compare = graphics.deb_version_compare
        self.assertLess(compare("22.3.6-1+deb12u1", "25.0.7-2~bpo12+1"), 0)
        self.assertLess(compare("1.0~rc1", "1.0"), 0)
        self.assertGreater(compare("1:1.0", "2.0"), 0)
        self.assertGreater(compare("1.10", "1.9"), 0)
        self.assertLess(compare("1.0-1", "1.0-1+b1"), 0)
        self.assertEqual(compare("2.4.124-2", "2.4.124-2"), 0)

    def test_elf_dynamic_reads_soname_and_needed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_elf(Path(folder) / "libEGL_mesa.so.0", "libEGL_mesa.so.0", ("libgbm.so.1", "libc.so.6"))
            described = graphics.elf_dynamic(path)
            self.assertEqual(described["soname"], "libEGL_mesa.so.0")
            self.assertEqual(described["needed"], ["libgbm.so.1", "libc.so.6"])
            self.assertEqual((described["bits"], described["machine"]), (64, 62))
            (Path(folder) / "text").write_text("not a library")
            self.assertIsNone(graphics.elf_dynamic(Path(folder) / "text"))

    def test_the_c_library_version_is_read_from_its_version_definitions(self):
        with tempfile.TemporaryDirectory() as folder:
            new = write_elf(Path(folder) / "a/libc.so.6", "libc.so.6", versions=("GLIBC_2.2.5", "GLIBC_2.43", "GLIBC_2.9"))
            self.assertEqual(graphics.glibc_version(new), (2, 43))
            self.assertEqual(graphics.elf_exports(new)[1], frozenset({"GLIBC_2.2.5", "GLIBC_2.43", "GLIBC_2.9"}))
            self.assertEqual(graphics.glibc_version(Path(folder) / "missing"), ())

    def test_a_tree_library_stays_only_when_it_is_strictly_newer(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            cases = {
                # same file name, as libdrm ships: only the symbols tell them apart
                "more-symbols": (("drmOpen", "drmClose", "drmSyncobjEventfd"), ("drmOpen", "drmClose"), False),
                "private-names-do-not-count": (("drmOpen",), ("drmOpen", "_edata", "__bss_start"), False),
                "fewer-symbols": (("drmOpen",), ("drmOpen", "drmClose"), True),
                "same": (("drmOpen",), ("drmOpen",), False),
            }
            for name, (device_symbols, tree_symbols, expected) in cases.items():
                device = write_elf(base / name / "device/libdrm.so.2", "libdrm.so.2", symbols=device_symbols)
                tree = write_elf(base / name / "tree/libdrm.so.2", "libdrm.so.2", symbols=tree_symbols)
                self.assertEqual(graphics.tree_is_newer(device, tree, "libdrm.so.2"), expected, name)

    def test_symbol_versions_decide_before_symbols(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            device = write_elf(base / "d/libstdc++.so.6", "libstdc++.so.6", versions=("GLIBCXX_3.4", "GLIBCXX_3.4.34"), symbols=("a",))
            tree = write_elf(base / "t/libstdc++.so.6", "libstdc++.so.6", versions=("GLIBCXX_3.4", "GLIBCXX_3.4.30"), symbols=("a", "b"))
            self.assertFalse(graphics.tree_is_newer(device, tree, "libstdc++.so.6"))
            self.assertTrue(graphics.tree_is_newer(tree, device, "libstdc++.so.6"))

    def test_the_file_name_version_is_the_last_resort(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for name, version in (("device", "1.9.1"), ("tree", "1.9.9")):
                write_elf(base / name / f"libexpat.so.{version}", "libexpat.so.1")
                os.symlink(f"libexpat.so.{version}", base / name / "libexpat.so.1")
            self.assertTrue(graphics.tree_is_newer(base / "device/libexpat.so.1", base / "tree/libexpat.so.1", "libexpat.so.1"))
            self.assertFalse(graphics.tree_is_newer(base / "tree/libexpat.so.1", base / "device/libexpat.so.1", "libexpat.so.1"))


def device_root(base: Path, glibc: str = "GLIBC_2.43") -> Path:
    """A Fedora-shaped device with Mesa, a newer libdrm and its C library."""
    lib = base / "usr/lib64"
    write_elf(lib / "libc.so.6", "libc.so.6", versions=("GLIBC_2.2.5", glibc))
    write_elf(lib / "libm.so.6", "libm.so.6", ("libc.so.6",))
    write_elf(lib / "ld-linux-x86-64.so.2", "ld-linux-x86-64.so.2")
    write_elf(lib / "libdrm.so.2.4.131", "libdrm.so.2", ("libc.so.6",))
    os.symlink("libdrm.so.2.4.131", lib / "libdrm.so.2")
    write_elf(lib / "libexpat.so.1.9.1", "libexpat.so.1", ("libc.so.6",))
    os.symlink("libexpat.so.1.9.1", lib / "libexpat.so.1")
    write_elf(lib / "libgallium-26.2.3.so", "libgallium-26.2.3.so", ("libdrm.so.2", "libexpat.so.1", "libLLVM.so.22.1", "libc.so.6"))
    write_elf(lib / "libLLVM.so.22.1", "libLLVM.so.22.1", ("libm.so.6", "libc.so.6"))
    write_elf(lib / "libgbm.so.1.0.0", "libgbm.so.1", ("libdrm.so.2", "libc.so.6"))
    os.symlink("libgbm.so.1.0.0", lib / "libgbm.so.1")
    write_elf(lib / "libEGL_mesa.so.0.0.0", "libEGL_mesa.so.0", ("libgbm.so.1", "libgallium-26.2.3.so", "libc.so.6"))
    os.symlink("libEGL_mesa.so.0.0.0", lib / "libEGL_mesa.so.0")
    write_elf(lib / "libEGL.so.1.1.0", "libEGL.so.1", ("libc.so.6",))
    os.symlink("libEGL.so.1.1.0", lib / "libEGL.so.1")
    write_elf(lib / "dri/libdril_dri.so", "libdril_dri.so", ("libgallium-26.2.3.so",))
    os.symlink("libdril_dri.so", lib / "dri/iris_dri.so")
    write_elf(lib / "libvulkan_intel.so", "libvulkan_intel.so", ("libdrm.so.2", "libc.so.6"))
    (lib / "gconv").mkdir()
    (lib / "gconv/gconv-modules").write_text("modules")
    (base / "usr/sbin").mkdir(parents=True)
    (base / "usr/sbin/ldconfig").write_text("ldconfig")
    for folder, name, text in (("usr/share/glvnd/egl_vendor.d", "50_mesa.json", '{"ICD": {"library_path": "libEGL_mesa.so.0"}}'),
                               ("usr/share/vulkan/icd.d", "intel_icd.x86_64.json",
                                '{"ICD": {"library_path": "/usr/lib64/libvulkan_intel.so"}}')):
        (base / folder).mkdir(parents=True)
        (base / folder / name).write_text(text)
    return base


def debian_tree(base: Path, glibc: str = "GLIBC_2.36") -> Path:
    lib = base / "usr/lib/x86_64-linux-gnu"
    write_elf(lib / "libc.so.6", "libc.so.6", versions=("GLIBC_2.2.5", glibc))
    write_elf(lib / "libm.so.6", "libm.so.6", ("libc.so.6",))
    write_elf(lib / "ld-linux-x86-64.so.2", "ld-linux-x86-64.so.2")
    (base / "usr/lib64").mkdir(parents=True)
    os.symlink("../lib/x86_64-linux-gnu/ld-linux-x86-64.so.2", base / "usr/lib64/ld-linux-x86-64.so.2")
    write_elf(lib / "libdrm.so.2.4.114", "libdrm.so.2", ("libc.so.6",))
    os.symlink("libdrm.so.2.4.114", lib / "libdrm.so.2")
    write_elf(lib / "libexpat.so.1.9.9", "libexpat.so.1", ("libc.so.6",))
    os.symlink("libexpat.so.1.9.9", lib / "libexpat.so.1")
    write_elf(lib / "libgbm.so.1.0.0", "libgbm.so.1", ("libdrm.so.2",))
    os.symlink("libgbm.so.1.0.0", lib / "libgbm.so.1")
    (base / "usr/share/vulkan/icd.d").mkdir(parents=True)
    (base / "usr/share/vulkan/icd.d/lvp_icd.x86_64.json").write_text("{}")
    return base


class HostRoute(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.base = Path(self.folder.name)
        self.device = device_root(self.base / "device")
        self.layer = self.base / "layer"
        self.record = graphics.stage_host_layer(self.device, self.layer)

    def test_the_layer_is_the_driver_closure_and_the_c_library_apart(self):
        libraries = set(self.record["libraries"])
        for expected in ("usr/lib64/libEGL_mesa.so.0", "usr/lib64/libgallium-26.2.3.so", "usr/lib64/libLLVM.so.22.1",
                         "usr/lib64/libdrm.so.2", "usr/lib64/dri/iris_dri.so", "usr/lib64/libvulkan_intel.so"):
            self.assertIn(expected, libraries)
        self.assertIn("usr/lib64/libc.so.6", self.record["glibc"])
        self.assertIn("usr/lib64/ld-linux-x86-64.so.2", self.record["glibc"])
        self.assertNotIn("usr/lib64/libc.so.6", libraries)
        self.assertEqual(self.record["glibc_version"], [2, 43])
        self.assertEqual(self.record["file_versions"]["usr/lib64/libdrm.so.2"], [4, 131])
        self.assertTrue((self.layer / "usr/lib64/libdrm.so.2").is_file())
        self.assertFalse((self.layer / "usr/lib64/libdrm.so.2").is_symlink())

    def test_an_older_tree_takes_the_devices_c_library_whole(self):
        tree = debian_tree(self.base / "tree")
        decision = graphics.apply_host_layer(self.layer, self.record, tree)
        self.assertTrue(decision["glibc_replaced"])
        self.assertEqual(decision["library_dirs"], ["/usr/lib64"])
        device_libc = (self.layer / "usr/lib64/libc.so.6").read_bytes()
        self.assertEqual((tree / "usr/lib/x86_64-linux-gnu/libc.so.6").read_bytes(), device_libc)
        self.assertEqual((tree / "usr/lib64/libc.so.6").read_bytes(), device_libc)
        interpreter = tree / "usr/lib64/ld-linux-x86-64.so.2"
        self.assertFalse(interpreter.is_symlink())
        self.assertEqual(interpreter.read_bytes(), (self.layer / "usr/lib64/ld-linux-x86-64.so.2").read_bytes())
        self.assertTrue((tree / "usr/lib64/gconv/gconv-modules").is_file())
        self.assertEqual(decision["ldconfig"], "/usr/sbin/ldconfig.luma-device")

    def test_newer_wins_library_by_library_and_mesa_moves_as_a_set(self):
        tree = debian_tree(self.base / "tree")
        decision = graphics.apply_host_layer(self.layer, self.record, tree)
        multiarch = tree / "usr/lib/x86_64-linux-gnu"
        # libdrm: the device's is newer, and the tree's own copy gives way to it.
        self.assertEqual((multiarch / "libdrm.so.2").read_bytes(), (self.layer / "usr/lib64/libdrm.so.2").read_bytes())
        # libexpat: the tree's is newer and stays.
        self.assertIn("libexpat.so.1", decision["kept"])
        self.assertFalse((tree / "usr/lib64/libexpat.so.1").exists())
        # libgbm has the same file version in both; it is Mesa's, so the device's is used.
        self.assertEqual((multiarch / "libgbm.so.1").read_bytes(), (self.layer / "usr/lib64/libgbm.so.1").read_bytes())
        self.assertTrue((tree / "usr/lib64/dri/iris_dri.so").is_file())
        self.assertEqual(sorted(path.name for path in (tree / "usr/share/vulkan/icd.d").iterdir()), ["intel_icd.x86_64.json"])

    def test_a_tree_with_the_newer_c_library_keeps_its_own(self):
        tree = debian_tree(self.base / "tree", "GLIBC_2.44")
        before = (tree / "usr/lib/x86_64-linux-gnu/libc.so.6").read_bytes()
        decision = graphics.apply_host_layer(self.layer, self.record, tree)
        self.assertFalse(decision["glibc_replaced"])
        self.assertEqual((tree / "usr/lib/x86_64-linux-gnu/libc.so.6").read_bytes(), before)
        self.assertTrue((tree / "usr/lib64/ld-linux-x86-64.so.2").is_symlink())
        self.assertFalse((tree / "usr/lib64/gconv").exists())

    def test_placing_never_writes_through_a_link_into_the_store(self):
        tree = debian_tree(self.base / "tree")
        original = self.base / "object"
        original.write_bytes(b"store object")
        target = tree / "usr/lib/x86_64-linux-gnu/libdrm.so.2.4.114"
        target.unlink()
        os.link(original, target)
        (tree / "usr/lib/x86_64-linux-gnu/libdrm.so.2").unlink()
        os.link(original, tree / "usr/lib/x86_64-linux-gnu/libdrm.so.2")
        graphics.apply_host_layer(self.layer, self.record, tree)
        self.assertEqual(original.read_bytes(), b"store object")


class MesaRoute(unittest.TestCase):
    def test_a_package_is_never_replaced_by_an_older_one(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            layer, root = base / "layer", base / "root"
            for package, relative, text in (("libgbm1", "usr/lib/x86_64-linux-gnu/libgbm.so.1.0.0", "gbm 25"),
                                            ("libdrm2", "usr/lib/x86_64-linux-gnu/libdrm.so.2.4.0", "drm 124"),
                                            ("libexpat1", "usr/lib/x86_64-linux-gnu/libexpat.so.1.8.10", "expat old")):
                (layer / "pkgs" / package / relative).parent.mkdir(parents=True, exist_ok=True)
                (layer / "pkgs" / package / relative).write_text(text)
            os.symlink("libgbm.so.1.0.0", layer / "pkgs/libgbm1/usr/lib/x86_64-linux-gnu/libgbm.so.1")
            (root / "usr/lib/x86_64-linux-gnu").mkdir(parents=True)
            (root / "usr/lib/x86_64-linux-gnu/libgbm.so.1.0.0").write_text("gbm 22")
            (root / "usr/lib/x86_64-linux-gnu/libexpat.so.1.8.10").write_text("expat new")
            record = {"mesa": "25.0.7-2~bpo12+1", "packages": [["libgbm1", "25.0.7-2~bpo12+1", "amd64"],
                                                              ["libdrm2", "2.4.124-2~bpo12+1", "amd64"],
                                                              ["libexpat1", "2.5.0-1+deb12u1", "amd64"]]}
            tree = {"packages": [["libgbm1", "22.3.6-1+deb12u1", "amd64"], ["libexpat1", "2.5.0-1+deb12u2", "amd64"]]}
            decision = graphics.apply_mesa_layer(layer, record, tree, root)
            self.assertEqual(decision["taken"], ["libgbm1", "libdrm2"])
            self.assertEqual(decision["kept"], ["libexpat1"])
            self.assertFalse(decision["glibc_replaced"])
            lib = root / "usr/lib/x86_64-linux-gnu"
            self.assertEqual((lib / "libgbm.so.1.0.0").read_text(), "gbm 25")
            self.assertEqual(os.readlink(lib / "libgbm.so.1"), "libgbm.so.1.0.0")
            self.assertEqual((lib / "libexpat.so.1.8.10").read_text(), "expat new")
            self.assertEqual(os.stat(lib / "libdrm.so.2.4.0").st_ino, os.stat(layer / "pkgs/libdrm2" / "usr/lib/x86_64-linux-gnu/libdrm.so.2.4.0").st_ino)


class NvidiaRoute(unittest.TestCase):
    def device(self, base: Path, version: str) -> Path:
        lib = base / "usr/lib64"
        for name in (f"libnvidia-glcore.so.{version}", f"libGLX_nvidia.so.{version}", f"libEGL_nvidia.so.{version}",
                     f"libnvidia-egl-gbm.so.1.1.2"):
            write_elf(lib / name, name.split(".so.")[0] + ".so." + ("0" if "nvidia.so" in name else version))
        os.symlink(f"libGLX_nvidia.so.{version}", lib / "libGLX_nvidia.so.0")
        write_elf(lib / "gbm/nvidia-drm_gbm.so", "libnvidia-allocator.so.1")
        write_elf(lib / "libgbm.so.1", "libgbm.so.1")
        (base / "usr/share/vulkan/icd.d").mkdir(parents=True)
        (base / "usr/share/vulkan/icd.d/nvidia_icd.json").write_text('{"ICD": {"library_path": "libGLX_nvidia.so.0"}}')
        return base

    def test_libraries_from_another_driver_release_are_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            device = self.device(Path(folder), "580.95.05")
            with self.assertRaisesRegex(InstallerError, "580.95.05.*590.44.01"):
                graphics.nvidia_files(device, "590.44.01")
            with self.assertRaisesRegex(InstallerError, "not loaded"):
                graphics.nvidia_files(device, "")

    def test_the_matching_userspace_is_taken_and_only_it(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            device = self.device(base / "device", "590.44.01")
            record = graphics.stage_nvidia_layer(device, base / "layer", "590.44.01")
            names = {Path(relative).name for relative in record["libraries"]}
            self.assertIn("libGLX_nvidia.so.0", names)
            self.assertIn("nvidia-drm_gbm.so", names)
            self.assertNotIn("libgbm.so.1", names)
            self.assertEqual(record["version"], "590.44.01")
            tree = debian_tree(base / "tree")
            decision = graphics.apply_nvidia_layer(base / "layer", record, tree)
            self.assertFalse(decision["glibc_replaced"])
            self.assertTrue((tree / "usr/lib64/libGLX_nvidia.so.0").is_file())
            self.assertTrue((tree / "usr/lib/x86_64-linux-gnu/gbm/nvidia-drm_gbm.so").is_file())
            self.assertTrue((tree / "usr/share/vulkan/icd.d/nvidia_icd.json").is_file())
            self.assertEqual(decision["library_dirs"], ["/usr/lib64"])


class Device(unittest.TestCase):
    def test_kernel_drivers_and_module_version_are_read_from_sysfs(self):
        with tempfile.TemporaryDirectory() as folder:
            sysfs = Path(folder)
            for card, driver in (("card0", "i915"), ("card1", "nvidia")):
                (sysfs / "class/drm" / card / "device").mkdir(parents=True)
                os.symlink(f"../../../bus/pci/drivers/{driver}", sysfs / "class/drm" / card / "device/driver")
            (sysfs / "class/drm/card0-eDP-1").mkdir()
            (sysfs / "module/nvidia").mkdir(parents=True)
            (sysfs / "module/nvidia/version").write_text("590.44.01\n")
            self.assertEqual(graphics.gpu_drivers(sysfs), ["i915", "nvidia"])
            self.assertEqual(graphics.nvidia_module_version(sysfs), "590.44.01")
            self.assertEqual(graphics.nvidia_module_version(sysfs / "missing"), "")


if __name__ == "__main__":
    unittest.main()
