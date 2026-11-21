# SPDX-License-Identifier: Apache-2.0
"""Graphics drivers come from the device (ADR-052): which library wins, and when the C library moves."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for "-m unittest tests.test_device_graphics" as well as discover

from closure_fixtures import write_elf, write_elf32  # noqa: E402
from luma_installer import closure  # noqa: E402
from luma_installer import device_graphics as graphics  # noqa: E402
from luma_installer.errors import InstallerError  # noqa: E402

DATA_FILE = Path(__file__).resolve().parent.parent / "data/graphics-layers.json"


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


def fedora_tree(base: Path) -> Path:
    lib = base / "usr/lib64"
    write_elf(lib / "libc.so.6", "libc.so.6", versions=("GLIBC_2.2.5", "GLIBC_2.43"))
    write_elf(lib / "libgbm.so.1", "libgbm.so.1")
    (base / "usr/lib").mkdir()
    (base / "usr/share/vulkan/icd.d").mkdir(parents=True)
    return base


NVIDIA = "590.44.01"


def nvidia_device(base: Path, module: str = NVIDIA, userspace: str = "") -> Path:
    """A Fedora-shaped device with NVIDIA's driver: the module in sysfs, its 64-bit
    userspace in usr/lib64, the 32-bit one in usr/lib, and the manifests that name them."""
    version = userspace or module
    (base / "sys/module/nvidia").mkdir(parents=True)
    (base / "sys/module/nvidia/version").write_text(module + "\n")
    (base / "sys/class/drm/card0/device").mkdir(parents=True)
    os.symlink("../../../bus/pci/drivers/nvidia", base / "sys/class/drm/card0/device/driver")
    (base / "proc/driver/nvidia").mkdir(parents=True)  # not read: the module's version comes from sysfs
    (base / "proc/driver/nvidia/version").write_text(f"NVRM version: NVIDIA UNIX x86_64 Kernel Module  {module}\n")
    lib, lib32 = base / "usr/lib64", base / "usr/lib"
    write_elf(lib / "libc.so.6", "libc.so.6", versions=("GLIBC_2.2.5", "GLIBC_2.43"))
    write_elf(lib / "libgbm.so.1", "libgbm.so.1")
    write_elf(lib / "libEGL_mesa.so.0", "libEGL_mesa.so.0")
    for name, link in (("libnvidia-glcore", ""), ("libnvidia-eglcore", ""), ("libnvidia-tls", ""), ("libnvidia-gpucomp", ""),
                       ("libGLX_nvidia", "0"), ("libEGL_nvidia", "0"), ("libGLESv2_nvidia", "2"), ("libnvidia-allocator", "1"),
                       ("libnvidia-ml", "1")):
        write_elf(lib / f"{name}.so.{version}", f"{name}.so.{link or version}", ("libc.so.6",))
        if link:
            os.symlink(f"{name}.so.{version}", lib / f"{name}.so.{link}")
    write_elf(lib / "libnvidia-egl-gbm.so.1.1.2", "libnvidia-egl-gbm.so.1")
    os.symlink("libnvidia-egl-gbm.so.1.1.2", lib / "libnvidia-egl-gbm.so.1")
    (lib / "gbm").mkdir()
    os.symlink("../libnvidia-allocator.so.1", lib / "gbm/nvidia-drm_gbm.so")
    write_elf(lib / f"vdpau/libvdpau_nvidia.so.{version}", "libvdpau_nvidia.so.1")
    os.symlink(f"libvdpau_nvidia.so.{version}", lib / "vdpau/libvdpau_nvidia.so.1")
    write_elf32(lib32 / "libc.so.6", "libc.so.6")
    for name, link in (("libnvidia-glcore", ""), ("libnvidia-tls", ""), ("libGLX_nvidia", "0"), ("libEGL_nvidia", "0")):
        write_elf32(lib32 / f"{name}.so.{version}", f"{name}.so.{link or version}", ("libc.so.6",))
        if link:
            os.symlink(f"{name}.so.{version}", lib32 / f"{name}.so.{link}")
    for relative, text in (("usr/share/glvnd/egl_vendor.d/10_nvidia.json", '{"ICD": {"library_path": "libEGL_nvidia.so.0"}}'),
                           ("usr/share/glvnd/egl_vendor.d/50_mesa.json", '{"ICD": {"library_path": "libEGL_mesa.so.0"}}'),
                           ("usr/share/vulkan/icd.d/nvidia_icd.x86_64.json", '{"ICD": {"library_path": "libGLX_nvidia.so.0"}}'),
                           ("usr/share/vulkan/implicit_layer.d/nvidia_layers.json",
                            '{"layer": {"library_path": "libGLX_nvidia.so.0"}}'),
                           ("usr/share/egl/egl_external_platform.d/15_nvidia_gbm.json",
                            '{"ICD": {"library_path": "libnvidia-egl-gbm.so.1"}}'),
                           ("usr/share/nvidia/nvidia-application-profiles-rc", "{}")):
        (base / relative).parent.mkdir(parents=True, exist_ok=True)
        (base / relative).write_text(text)
    return base


class NvidiaRoute(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.base = Path(self.folder.name)

    def staged(self) -> tuple[Path, dict[str, object]]:
        device = nvidia_device(self.base / "device")
        return self.base / "layer", graphics.stage_nvidia_layer(device, self.base / "layer", NVIDIA)

    def test_versions_are_read_from_the_module_and_the_library_names(self):
        device = nvidia_device(self.base / "device")
        self.assertEqual(graphics.nvidia_module_version(device / "sys"), NVIDIA)
        self.assertEqual(graphics.nvidia_userspace_version(device), NVIDIA)
        self.assertEqual(graphics.gpu_drivers(device / "sys"), ["nvidia"])
        self.assertEqual(graphics.nvidia_userspace_version(fedora_tree(self.base / "plain")), "")

    def test_libraries_from_another_driver_release_are_refused(self):
        device = nvidia_device(self.base / "device", module="590.44.01", userspace="580.95.05")
        module = graphics.nvidia_module_version(device / "sys")
        with self.assertRaisesRegex(InstallerError, "580.95.05.*590.44.01"):
            graphics.nvidia_files(device, module)
        with self.assertRaisesRegex(InstallerError, "580.95.05.*590.44.01"):
            graphics.stage_nvidia_layer(device, self.base / "layer", module)
        self.assertFalse((self.base / "layer").exists())
        with self.assertRaisesRegex(InstallerError, "not loaded"):
            graphics.nvidia_files(device, "")
        with self.assertRaisesRegex(InstallerError, "missing.*590.44.01"):
            graphics.nvidia_files(fedora_tree(self.base / "plain"), module)

    def test_the_layer_is_nvidias_userspace_and_only_it(self):
        layer, record = self.staged()
        self.assertEqual((record["version"], record["route"], record["id"]), (NVIDIA, "nvidia", f"nvidia-{NVIDIA}"))
        libraries, libraries32 = set(record["libraries"]), set(record["libraries32"])
        for expected in (f"libnvidia-glcore.so.{NVIDIA}", f"libGLX_nvidia.so.{NVIDIA}", "libGLX_nvidia.so.0", "libEGL_nvidia.so.0",
                         "libGLESv2_nvidia.so.2", "libnvidia-allocator.so.1", "libnvidia-egl-gbm.so.1", "gbm/nvidia-drm_gbm.so",
                         "vdpau/libvdpau_nvidia.so.1"):
            self.assertIn(f"usr/lib64/{expected}", libraries)
        self.assertEqual(libraries32, {f"usr/lib/{name}" for name in (
            f"libnvidia-glcore.so.{NVIDIA}", f"libnvidia-tls.so.{NVIDIA}", f"libGLX_nvidia.so.{NVIDIA}", "libGLX_nvidia.so.0",
            f"libEGL_nvidia.so.{NVIDIA}", "libEGL_nvidia.so.0")})
        self.assertTrue(all(relative.startswith("usr/lib64/") for relative in libraries))
        for foreign in ("libgbm.so.1", "libc.so.6", "libEGL_mesa.so.0"):
            self.assertNotIn(f"usr/lib64/{foreign}", libraries)
            self.assertFalse((layer / "usr/lib64" / foreign).exists())
        self.assertEqual(record["libraries"]["usr/lib64/libGLX_nvidia.so.0"], "libGLX_nvidia.so.0")
        self.assertEqual(sorted(record["data"]), [
            "usr/share/egl/egl_external_platform.d", "usr/share/glvnd/egl_vendor.d/10_nvidia.json", "usr/share/nvidia",
            "usr/share/vulkan/icd.d/nvidia_icd.x86_64.json", "usr/share/vulkan/implicit_layer.d/nvidia_layers.json"])
        self.assertFalse((layer / "usr/share/glvnd/egl_vendor.d/50_mesa.json").exists())
        # Links are followed: every name the loader may ask for is a file of its own.
        self.assertFalse((layer / "usr/lib64/libGLX_nvidia.so.0").is_symlink())
        self.assertFalse((layer / "usr/lib64/gbm/nvidia-drm_gbm.so").is_symlink())
        self.assertEqual(graphics.elf_dynamic(layer / "usr/lib/libGLX_nvidia.so.0")["bits"], 32)

    def applied(self, tree: Path, lib: str) -> dict[str, object]:
        layer, record = self.staged()
        decision = graphics.apply_nvidia_layer(layer, record, tree)
        for name in (f"libnvidia-glcore.so.{NVIDIA}", "libGLX_nvidia.so.0", "libEGL_nvidia.so.0", "libnvidia-egl-gbm.so.1",
                     "gbm/nvidia-drm_gbm.so", "vdpau/libvdpau_nvidia.so.1"):
            self.assertTrue((tree / lib / name).is_file(), name)
            self.assertEqual(graphics.elf_dynamic(tree / lib / name)["bits"], 64, name)
        self.assertEqual(os.stat(tree / lib / "libGLX_nvidia.so.0").st_ino, os.stat(layer / "usr/lib64/libGLX_nvidia.so.0").st_ino)
        for manifest in ("usr/share/glvnd/egl_vendor.d/10_nvidia.json", "usr/share/vulkan/icd.d/nvidia_icd.x86_64.json",
                         "usr/share/vulkan/implicit_layer.d/nvidia_layers.json",
                         "usr/share/egl/egl_external_platform.d/15_nvidia_gbm.json"):
            self.assertTrue((tree / manifest).is_file(), manifest)
        self.assertEqual((decision["route"], decision["version"], decision["glibc_replaced"]), ("nvidia", NVIDIA, False))
        # Everything is where the tree's loader already looks: no folder to add.
        self.assertEqual(decision["library_dirs"], [])
        return decision

    def test_a_debian_tree_gets_the_libraries_in_its_multiarch_folder(self):
        tree = debian_tree(self.base / "tree")
        decision = self.applied(tree, "usr/lib/x86_64-linux-gnu")
        self.assertFalse((tree / "usr/lib64/libGLX_nvidia.so.0").exists())
        # No 32-bit C library in the tree: the 32-bit set stays out.
        self.assertEqual(decision["libraries32"], 0)
        self.assertFalse((tree / "usr/lib/libGLX_nvidia.so.0").exists())
        self.assertTrue((tree / "usr/share/vulkan/icd.d/lvp_icd.x86_64.json").is_file())

    def test_a_debian_tree_with_32_bit_programs_gets_both_sets(self):
        tree = debian_tree(self.base / "tree")
        write_elf32(tree / "usr/lib/i386-linux-gnu/libc.so.6", "libc.so.6")
        decision = self.applied(tree, "usr/lib/x86_64-linux-gnu")
        self.assertEqual(decision["libraries32"], 6)
        self.assertEqual(graphics.elf_dynamic(tree / "usr/lib/i386-linux-gnu/libGLX_nvidia.so.0")["bits"], 32)
        self.assertEqual(graphics.elf_dynamic(tree / "usr/lib/x86_64-linux-gnu/libGLX_nvidia.so.0")["bits"], 64)

    def test_a_fedora_tree_gets_the_libraries_in_lib64(self):
        tree = fedora_tree(self.base / "tree")
        decision = self.applied(tree, "usr/lib64")
        self.assertEqual(decision["libraries32"], 0)
        write_elf32(tree / "usr/lib/libc.so.6", "libc.so.6")
        layer, record = self.base / "layer", json.loads(json.dumps(graphics.nvidia_files(self.base / "device", NVIDIA)))
        self.assertEqual(graphics.apply_nvidia_layer(layer, record, tree)["libraries32"], 6)
        self.assertEqual(graphics.elf_dynamic(tree / "usr/lib/libEGL_nvidia.so.0")["bits"], 32)
        self.assertEqual(graphics.elf_dynamic(tree / "usr/lib64/libEGL_nvidia.so.0")["bits"], 64)


class LayerSources(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(DATA_FILE.read_text())

    def test_every_userland_has_a_row_and_the_file_is_well_formed(self):
        self.assertEqual(self.data["version"], 1)
        self.assertEqual(set(self.data["userlands"]), set(closure.USERLANDS))
        self.assertEqual(graphics.check_graphics_layers(self.data), [])
        for name, row in self.data["userlands"].items():
            userland = closure.USERLANDS[name]
            self.assertIn(row["source"], graphics.LAYER_SOURCES, name)
            self.assertIsInstance(row["luma_build"], bool, name)
            self.assertTrue(row["note"], name)
            if row["source"] == "backports":
                self.assertEqual(row["suite"], f"{userland.suite}-backports", name)
                self.assertEqual(row["apt"], f"deb {userland.mirror} {row['suite']} main", name)
            if row["source"] in graphics.BUILT_SOURCES:
                self.assertEqual(userland.family, "deb", name)
            if row["luma_build"]:
                self.assertEqual(set(row["build"]), set(graphics.LUMA_BUILD_KEYS), name)
                self.assertEqual(row["build"]["source_package"], "mesa", name)
        self.assertFalse(set(self.data.get("not_yet_userlands", {})) & set(closure.USERLANDS))

    def test_each_release_says_where_current_mesa_comes_from(self):
        table = {name: (row["source"], row.get("suite", ""), row["luma_build"]) for name, row in self.data["userlands"].items()}
        self.assertEqual(table, {
            "debian-13": ("backports", "trixie-backports", False), "debian-12": ("backports", "bookworm-backports", False),
            "ubuntu-24.04": ("updates", "noble-updates", True), "ubuntu-22.04": ("updates", "jammy-updates", True),
            "fedora-45": ("release", "", False), "fedora-44": ("release", "", False)})
        wanted = {name for name in table if graphics.wants_mesa_layer(graphics.mesa_source(name))}
        self.assertEqual(wanted, {"debian-13", "debian-12", "ubuntu-24.04", "ubuntu-22.04"})
        self.assertTrue(graphics.wants_mesa_layer(graphics.mesa_source("slackware-15")))
        self.assertEqual(graphics.mesa_source("slackware-15")["source"], "")

    def test_the_built_in_copy_is_the_file(self):
        self.assertEqual(graphics.BUILTIN_LAYERS, self.data)

    def test_the_file_is_looked_for_in_order(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            changed = json.loads(DATA_FILE.read_text())
            changed["userlands"]["debian-12"]["suite"] = "from-override"
            (base / "override.json").write_text(json.dumps(changed))
            changed["userlands"]["debian-12"]["suite"] = "from-installed"
            (base / "installed.json").write_text(json.dumps(changed))
            (base / "broken.json").write_text('{"version": 2, "userlands": {}}')
            paths = (base / "installed.json", DATA_FILE)
            with mock.patch.object(graphics, "LAYERS_PATHS", paths):
                with mock.patch.dict(os.environ, {graphics.LAYERS_ENVIRONMENT: str(base / "override.json")}):
                    self.assertEqual(graphics.mesa_source("debian-12")["suite"], "from-override")
                for unusable in (base / "broken.json", base / "absent.json"):
                    with mock.patch.dict(os.environ, {graphics.LAYERS_ENVIRONMENT: str(unusable)}):
                        self.assertEqual(graphics.mesa_source("debian-12")["suite"], "from-installed")
            with mock.patch.dict(os.environ):
                os.environ.pop(graphics.LAYERS_ENVIRONMENT, None)
                with mock.patch.object(graphics, "LAYERS_PATHS", (base / "absent.json", DATA_FILE)):
                    self.assertEqual(graphics.mesa_source("debian-12")["suite"], "bookworm-backports")
                with mock.patch.object(graphics, "LAYERS_PATHS", (base / "absent.json",)):
                    self.assertIs(graphics.graphics_layers(), graphics.BUILTIN_LAYERS)
            with self.assertRaisesRegex(InstallerError, "version 1"):
                graphics.load_graphics_layers(base / "broken.json")

    def test_a_malformed_table_is_described(self):
        check = graphics.check_graphics_layers
        self.assertTrue(check([]) and check({"version": 1}) and check({"version": 2, "userlands": {}}))
        row = {"source": "backports", "luma_build": False, "note": "n"}
        self.assertRegex(check({"version": 1, "userlands": {"x": row}})[0], "x: .*suite")
        self.assertRegex(check({"version": 1, "userlands": {"x": {**row, "source": "ppa"}}})[0], "x: source")
        self.assertRegex(check({"version": 1, "userlands": {"x": {"source": "release"}}})[0], "x: luma_build and note")
        self.assertRegex(check({"version": 1, "userlands": {"x": {"source": "updates", "luma_build": True, "note": "n"}}})[0],
                         "x: a Luma build says what is built")
        self.assertEqual(check({"version": 1, "userlands": {"x": {"source": "release", "luma_build": False, "note": "n"}},
                                "not_yet_userlands": {"y": {"source": "release", "luma_build": True, "note": "n"}}}), [])


ARCHITECTURE = closure.host_architecture()


class StubStore:
    """The store as the route code sees it: refs and their records."""

    def __init__(self, *names: str, records: dict[str, dict[str, object]] | None = None, architecture: str = ""):
        self.records = {self.graphics_ref(name, architecture): {} for name in names}
        self.records.update({self.graphics_ref(name, architecture): record for name, record in (records or {}).items()})
        self.committed: dict[str, list[str]] = {}

    @staticmethod
    def graphics_ref(name: str, architecture: str = "") -> str:
        return f"graphics/{name}/{architecture or ARCHITECTURE}"

    def refs(self, prefix: str = "") -> list[str]:
        return sorted(ref for ref in self.records if ref.startswith(prefix))

    def has_ref(self, ref: str) -> bool:
        return ref in self.records

    def read_record(self, ref: str) -> dict[str, object]:
        return self.records[ref]

    def commit_tree(self, tree: Path, ref: str, record: dict[str, object], subject: str = "") -> str:
        self.records[ref] = record
        self.committed[ref] = sorted(str(path.relative_to(tree)) for path in Path(tree).rglob("*") if path.is_file())
        return "0" * 64


class MesaBuild(unittest.TestCase):
    def build(self, userland: str) -> list[list[str]]:
        """Run build_mesa_layer with every command recorded instead of run."""
        commands: list[list[str]] = []

        class Log:
            def __init__(self, path=None):
                pass

            def run(self, step, arguments, **options):
                commands.append([step, *arguments])
                return mock.Mock(stdout="libgl1-mesa-dri\t25.0.7-2~bpo12+1\tamd64\tii \n" if step == "lock" else "")

        store = StubStore()
        store.export = lambda ref, destination: Path(destination).mkdir(parents=True)  # type: ignore[attr-defined]
        store.records["userland"] = {"packages": []}
        with tempfile.TemporaryDirectory() as folder, mock.patch.multiple(
                closure, BuildLog=Log, ensure_userland=lambda *a, **k: "userland", _write_in_tree=lambda *a, **k: None,
                sandbox_in_tree=lambda root, command, **k: list(command), _run=lambda *a, **k: None, userns_prefix=lambda: [],
                remove_tree=lambda path: shutil.rmtree(path, ignore_errors=True)):
            ref = graphics.build_mesa_layer(userland, store, Path(folder) / "work", Path(folder) / "cache")
        self.assertEqual(ref, store.graphics_ref(f"mesa-{userland}"))
        self.record = store.records[ref]
        return commands

    def test_the_suite_and_source_come_from_the_data(self):
        install = next(command for command in self.build("debian-12") if command[0] == "install mesa")
        self.assertIn("LUMA_MESA_SUITE=bookworm-backports", install)
        self.assertIn("LUMA_MESA_SOURCE=deb http://deb.debian.org/debian bookworm-backports main", install)
        self.assertEqual((self.record["suite"], self.record["mesa"]), ("bookworm-backports", "25.0.7-2~bpo12+1"))
        with tempfile.TemporaryDirectory() as folder:
            changed = json.loads(DATA_FILE.read_text())
            changed["userlands"]["ubuntu-24.04"].update(source="luma", suite="noble-luma",
                                                        apt="deb https://depot.invalid/mesa noble-luma main")
            (Path(folder) / "layers.json").write_text(json.dumps(changed))
            with mock.patch.dict(os.environ, {graphics.LAYERS_ENVIRONMENT: str(Path(folder) / "layers.json")}):
                install = next(command for command in self.build("ubuntu-24.04") if command[0] == "install mesa")
        self.assertIn("LUMA_MESA_SUITE=noble-luma", install)
        self.assertIn("LUMA_MESA_SOURCE=deb https://depot.invalid/mesa noble-luma main", install)

    def test_nothing_is_built_where_the_data_has_no_source_to_build_from(self):
        for userland, expected in (("fedora-45", "needs no Mesa layer"), ("ubuntu-24.04", "Luma has to build it"),
                                   ("ubuntu-22.04", "Luma has to build it"), ("slackware-15", "No Mesa source is defined")):
            with self.assertRaisesRegex(InstallerError, expected):
                graphics.build_mesa_layer(userland, StubStore(), Path("/nonexistent"), Path("/nonexistent"))


class RoutePlan(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.sys = Path(self.folder.name) / "sys"
        (self.sys / "module/nvidia").mkdir(parents=True)
        (self.sys / "module/nvidia/version").write_text(NVIDIA + "\n")

    def plan(self, userland: str, store: StubStore, drivers: tuple[str, ...] = ("i915",), architecture: str = "") -> dict[str, object]:
        plan = graphics.route_plan(userland, store, list(drivers), architecture, sys_root=self.sys)
        self.assertEqual(set(plan), {"refs", "route", "fallback", "replaces_glibc", "nvidia", "reason"})
        self.assertTrue(plan["reason"])
        return plan

    def test_mesa_built_for_the_userland_is_the_default(self):
        store = StubStore("mesa-debian-12", "mesa-debian-13", "host")
        plan = self.plan("debian-12", store)
        self.assertEqual(plan["refs"], [store.graphics_ref("mesa-debian-12")])
        self.assertEqual((plan["route"], plan["fallback"], plan["replaces_glibc"], plan["nvidia"]), ("mesa", False, False, ""))
        self.assertIn("bookworm-backports", plan["reason"])

    def test_a_release_with_current_mesa_gets_no_layer_and_that_is_not_a_fallback(self):
        store = StubStore("host", "mesa-fedora-45", records={"host": {"glibc_version": [2, 43]}})
        for userland in ("fedora-45", "fedora-44"):
            plan = self.plan(userland, store)
            self.assertEqual((plan["refs"], plan["route"], plan["fallback"], plan["replaces_glibc"]), ([], "release", False, False))
            self.assertIn("own updates", plan["reason"])

    def test_the_devices_drivers_are_the_fallback_and_say_what_they_cost(self):
        store = StubStore("mesa-debian-13", records={"host": {"glibc_version": [2, 43]}})
        plan = self.plan("debian-12", store)
        self.assertEqual(plan["refs"], [store.graphics_ref("host")])
        self.assertEqual((plan["route"], plan["fallback"], plan["replaces_glibc"]), ("host", True, True))
        self.assertRegex(plan["reason"], "has not been built.*C library is replaced")
        self.assertRegex(self.plan("ubuntu-24.04", store)["reason"], "Luma has to build it.*C library is replaced")
        self.assertTrue(self.plan("debian-12", StubStore("host"))["replaces_glibc"])
        older = self.plan("debian-13", StubStore(records={"host": {"glibc_version": [2, 41]}}))
        self.assertEqual((older["route"], older["fallback"], older["replaces_glibc"]), ("host", True, False))

    def test_with_no_layer_at_all_the_tree_runs_on_its_own_mesa(self):
        for userland, expected in (("debian-12", "has not been built"), ("ubuntu-22.04", "Luma has to build it"),
                                   ("slackware-15", "No Mesa source is defined")):
            plan = self.plan(userland, StubStore("mesa-debian-13"))
            self.assertEqual((plan["refs"], plan["route"], plan["fallback"], plan["replaces_glibc"]), ([], "none", True, False))
            self.assertRegex(plan["reason"], expected + ".*its own Mesa")

    def test_nvidia_is_added_only_for_the_nvidia_driver_and_only_the_modules_release(self):
        # 595.10 sorts last and 580.95.05 first; the loaded module is 590.44.01.
        store = StubStore("mesa-debian-12", "nvidia-580.95.05", f"nvidia-{NVIDIA}", "nvidia-595.10")
        self.assertEqual(self.plan("debian-12", store, ("i915",))["refs"], [store.graphics_ref("mesa-debian-12")])
        self.assertEqual(self.plan("debian-12", store, ("nouveau",))["nvidia"], "")
        plan = self.plan("debian-12", store, ("i915", "nvidia"))
        self.assertEqual(plan["refs"], [store.graphics_ref("mesa-debian-12"), store.graphics_ref(f"nvidia-{NVIDIA}")])
        self.assertEqual((plan["route"], plan["nvidia"], plan["fallback"]), ("mesa", NVIDIA, False))
        fedora = self.plan("fedora-45", store, ("nvidia",))
        self.assertEqual((fedora["refs"], fedora["route"], fedora["nvidia"]),
                         ([store.graphics_ref(f"nvidia-{NVIDIA}")], "release", NVIDIA))

    def test_a_layer_of_another_release_is_not_used(self):
        store = StubStore("mesa-debian-12", "nvidia-580.95.05", "nvidia-595.10")
        plan = self.plan("debian-12", store, ("nvidia",))
        self.assertEqual((plan["refs"], plan["nvidia"]), ([store.graphics_ref("mesa-debian-12")], ""))
        self.assertRegex(plan["reason"], "590.44.01.*no layer for it.*580.95.05, 595.10")
        (self.sys / "module/nvidia/version").unlink()
        self.assertRegex(self.plan("debian-12", store, ("nvidia",))["reason"], "version cannot be read")

    def test_layers_are_per_architecture_and_the_device_is_read_when_not_given(self):
        other = "aarch64" if ARCHITECTURE == "x86_64" else "x86_64"
        store = StubStore("mesa-debian-12", f"nvidia-{NVIDIA}", architecture=other)
        self.assertEqual(self.plan("debian-12", store, ("nvidia",))["refs"], [])
        self.assertEqual(self.plan("debian-12", store, ("nvidia",), other)["refs"],
                         [store.graphics_ref("mesa-debian-12", other), store.graphics_ref(f"nvidia-{NVIDIA}", other)])
        (self.sys / "class/drm/card0/device").mkdir(parents=True)
        os.symlink("../../../bus/pci/drivers/nvidia", self.sys / "class/drm/card0/device/driver")
        self.assertEqual(graphics.route_plan("debian-12", store, None, other, sys_root=self.sys)["nvidia"], NVIDIA)

    def test_routes_for_is_the_plans_refs(self):
        store = StubStore("mesa-debian-12", "host")
        with mock.patch.object(graphics, "nvidia_module_version", return_value=""):
            self.assertEqual(graphics.routes_for("debian-12", store, ["amdgpu"]), [store.graphics_ref("mesa-debian-12")])
            self.assertEqual(graphics.routes_for("ubuntu-24.04", store, ["amdgpu"], ARCHITECTURE), [store.graphics_ref("host")])
            self.assertEqual(graphics.routes_for("fedora-45", store, drivers=["amdgpu"], architecture=""), [])


class EnsureLayers(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.base = Path(self.folder.name)

    def ensure(self, userland: str, store: StubStore, build=None, drivers: tuple[str, ...] = ("amdgpu",), device: Path | None = None):
        device = device or self.base / "none"
        with mock.patch.object(graphics, "build_mesa_layer", side_effect=build) as builder:
            plan = graphics.ensure_layers(userland, store, self.base / "work", self.base / "cache", self.base / "log",
                                          drivers=list(drivers), device=device, sys_root=device / "sys")
        return plan, builder

    def test_the_mesa_layer_is_built_when_the_data_calls_for_one_and_it_is_absent(self):
        store = StubStore()

        def build(userland, target, work, cache, log_path=None):
            target.records[target.graphics_ref(f"mesa-{userland}")] = {"route": "mesa"}
            return target.graphics_ref(f"mesa-{userland}")

        plan, builder = self.ensure("debian-13", store, build)
        builder.assert_called_once_with("debian-13", store, self.base / "work", self.base / "cache", self.base / "log")
        self.assertEqual((plan["refs"], plan["route"], plan["fallback"]), ([store.graphics_ref("mesa-debian-13")], "mesa", False))
        plan, builder = self.ensure("debian-13", store, build)
        builder.assert_not_called()
        self.assertEqual(plan["route"], "mesa")

    def test_nothing_is_built_where_no_layer_is_wanted_or_none_can_be_built(self):
        for userland, route in (("fedora-45", "release"), ("ubuntu-24.04", "none")):
            plan, builder = self.ensure(userland, StubStore())
            builder.assert_not_called()
            self.assertEqual((plan["refs"], plan["route"]), ([], route))

    def test_a_failed_build_is_reported_and_never_raised(self):
        for error in (InstallerError("install mesa failed (100): E: Unable to locate package"), OSError("bwrap is not installed"),
                      RuntimeError("")):
            plan, builder = self.ensure("debian-12", StubStore(), error)
            builder.assert_called_once()
            self.assertEqual((plan["refs"], plan["route"], plan["fallback"]), ([], "none", True))
            self.assertRegex(plan["reason"],
                             "its own Mesa.*Building the Mesa layer failed: " + re.escape(str(error) or "RuntimeError"))
        store = StubStore(records={"host": {"glibc_version": [2, 43]}})
        plan, _builder = self.ensure("debian-12", store, InstallerError("no network"))
        self.assertEqual((plan["refs"], plan["route"], plan["replaces_glibc"]), ([store.graphics_ref("host")], "host", True))

    def test_nvidias_userspace_is_staged_for_the_loaded_module(self):
        device = nvidia_device(self.base / "device")
        store = StubStore("mesa-debian-12", "nvidia-580.95.05")
        plan, _builder = self.ensure("debian-12", store, drivers=("nvidia",), device=device)
        ref = store.graphics_ref(f"nvidia-{NVIDIA}")
        self.assertEqual((plan["refs"], plan["nvidia"]), ([store.graphics_ref("mesa-debian-12"), ref], NVIDIA))
        self.assertEqual((store.records[ref]["route"], store.records[ref]["version"]), ("nvidia", NVIDIA))
        for relative in ("usr/lib64/libGLX_nvidia.so.0", "usr/lib/libGLX_nvidia.so.0",
                         "usr/share/vulkan/icd.d/nvidia_icd.x86_64.json"):
            self.assertIn(relative, store.committed[ref])
        self.assertEqual(list((self.base / "work").iterdir()), [])
        # Present for the loaded module: not staged again. Another driver: not looked at.
        store.committed.clear()
        self.ensure("debian-12", store, drivers=("nvidia",), device=device)
        self.ensure("debian-12", StubStore("mesa-debian-12"), drivers=("i915",), device=device)
        self.assertEqual(store.committed, {})

    def test_mismatched_nvidia_libraries_leave_the_plan_without_them_and_say_why(self):
        device = nvidia_device(self.base / "device", module="590.44.01", userspace="580.95.05")
        store = StubStore("mesa-debian-12", "nvidia-580.95.05")
        plan, _builder = self.ensure("debian-12", store, drivers=("nvidia",), device=device)
        self.assertEqual((plan["refs"], plan["route"], plan["nvidia"]), ([store.graphics_ref("mesa-debian-12")], "mesa", ""))
        self.assertRegex(plan["reason"], "no layer for it.*libraries on this device are 580.95.05; the loaded module is 590.44.01")
        self.assertEqual(store.committed, {})


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
