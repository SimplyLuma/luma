# SPDX-License-Identifier: Apache-2.0
import hashlib
import ctypes
import json
import os
from pathlib import Path
import tempfile
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from luma_android import initialize


@unittest.skipUnless(os.geteuid() == 0, "package ownership checks require the RPM builder")
class PackagedImagesTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir="/root")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        records = {}
        for name in ("system.img", "vendor.img"):
            payload = (name + "-test-bytes").encode()
            (self.root / name).write_bytes(payload)
            records[name] = {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        self.manifest = {"architecture": "x86_64", "images": records}
        self.write_manifest()

    def write_manifest(self):
        (self.root / "luma-images.json").write_text(json.dumps(self.manifest))

    def test_complete_pair_and_changed_bytes(self):
        initialize.verify_images(self.root, "x86_64")
        path = self.root / "vendor.img"
        original = path.read_bytes()
        path.write_bytes(b"X" + original[1:])
        with self.assertRaisesRegex(RuntimeError, "verification"):
            initialize.verify_images(self.root, "x86_64")

    def test_wrong_architecture_missing_pair_and_writable_image(self):
        with self.assertRaisesRegex(RuntimeError, "architecture"):
            initialize.verify_images(self.root, "aarch64")
        (self.root / "vendor.img").chmod(0o666)
        with self.assertRaisesRegex(RuntimeError, "read-only"):
            initialize.verify_images(self.root, "x86_64")
        del self.manifest["images"]["vendor.img"]
        self.write_manifest()
        with self.assertRaisesRegex(RuntimeError, "complete"):
            initialize.verify_images(self.root, "x86_64")

    def test_symlink_manifest_and_image_refused(self):
        for name in ("luma-images.json", "system.img"):
            with self.subTest(name=name):
                path = self.root / name
                renamed = self.root / (name + ".original")
                path.rename(renamed)
                path.symlink_to(renamed)
                with self.assertRaises(OSError):
                    initialize.verify_images(self.root, "x86_64")
                path.unlink()
                renamed.rename(path)

    def test_existing_runtime_is_not_reinitialized(self):
        state = self.root / "state"
        state.mkdir()
        (state / "waydroid.cfg").write_text("existing configuration")
        (state / "rootfs").mkdir()
        lxc = state / "lxc/waydroid"
        lxc.mkdir(parents=True)
        for name in ("config", "waydroid.seccomp", "config_nodes", "config_session"):
            (lxc / name).write_text("existing configuration")
        (state / "waydroid_base.prop").write_text("existing properties")
        with patch.object(initialize, "STATE", state), \
                patch.object(initialize, "verify_images") as verify, \
                patch.object(initialize.subprocess, "run") as run:
            initialize.initialize()
        verify.assert_not_called()
        run.assert_not_called()

    def test_partial_cfg_and_rootfs_are_not_completion(self):
        state = self.root / "state"
        state.mkdir()
        (state / "waydroid.cfg").write_text("partial configuration")
        (state / "rootfs").mkdir()

        for name in ("overlay_rw", "overlay_work"):
            (state / name / "system").mkdir(parents=True)
            (state / name / "vendor").mkdir()
        self.assertFalse(initialize.runtime_complete(state))

    def complete_state(self):
        state = self.root / "complete-state"
        (state / "lxc/waydroid").mkdir(parents=True)
        (state / "rootfs").mkdir()
        for name in ("waydroid.cfg", "waydroid_base.prop", "lxc/waydroid/config",
                     "lxc/waydroid/waydroid.seccomp", "lxc/waydroid/config_nodes",
                     "lxc/waydroid/config_session"):
            (state / name).write_text("complete configuration")
        return state

    def test_mapped_root_readiness_does_not_relax_privileged_validation(self):
        state = self.complete_state()
        real_lstat, real_fstat = Path.lstat, os.fstat

        def mapped(metadata):
            values = list(metadata)
            values[4] = 65534
            return os.stat_result(values)

        with patch.object(Path, "lstat", lambda path: mapped(real_lstat(path))), \
                patch.object(os, "fstat", lambda fd: mapped(real_fstat(fd))):
            self.assertTrue(initialize.runtime_metadata_complete(state))
            self.assertFalse(initialize.runtime_complete(state))
        self.assertTrue(initialize.runtime_complete(state))

    def test_readiness_still_rejects_writable_or_symlinked_configuration(self):
        state = self.complete_state()
        config = state / "waydroid.cfg"
        self.assertTrue(initialize.runtime_metadata_complete(state))
        config.chmod(0o666)
        self.assertFalse(initialize.runtime_metadata_complete(state))
        config.chmod(0o644)
        original = state / "original.cfg"
        config.rename(original)
        config.symlink_to(original)
        self.assertFalse(initialize.runtime_metadata_complete(state))

    def test_incomplete_existing_overlay_is_not_erased(self):
        state = self.root / "state"
        (state / "overlay_rw").mkdir(parents=True)
        changed = state / "overlay_rw/application-change"
        changed.write_bytes(b"preserved")
        with patch.object(initialize, "STATE", state), \
                patch.object(initialize.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "existing changes"):
                initialize.initialize()
        self.assertEqual(changed.read_bytes(), b"preserved")
        run.assert_not_called()

    def test_interrupted_empty_setup_can_finish_without_force_or_reset(self):
        state = self.root / "state"
        state.mkdir()
        (state / "waydroid.cfg").write_text("partial configuration")
        (state / "rootfs").mkdir()
        for name in ("overlay_rw", "overlay_work"):
            (state / name / "system").mkdir(parents=True)
            (state / name / "vendor").mkdir()

        def command(arguments, **kwargs):
            if arguments[0] == "/usr/bin/systemctl":
                return SimpleNamespace(returncode=0, stdout="inactive\n")
            self.assertEqual(arguments, ["/usr/bin/waydroid", "init"])
            lxc = state / "lxc/waydroid"
            lxc.mkdir(parents=True)
            for name in ("config", "waydroid.seccomp", "config_nodes", "config_session"):
                (lxc / name).write_text("finished configuration")
            (state / "waydroid_base.prop").write_text("finished properties")
            return SimpleNamespace(returncode=0)

        with patch.object(initialize, "STATE", state), \
                patch.object(initialize, "verify_images") as verify, \
                patch.object(initialize.subprocess, "run", side_effect=command) as run:
            initialize.initialize()
            self.assertTrue(initialize.runtime_complete(state))
            self.assertEqual(run.call_count, 2)
            verify.assert_called_once()
            original = (state / "waydroid.cfg").read_text()
            initialize.initialize()
            self.assertEqual(run.call_count, 2)
            self.assertEqual((state / "waydroid.cfg").read_text(), original)

    def test_active_or_unknown_container_cannot_retry_initialization(self):
        state = self.root / "state"
        state.mkdir()
        for result in ("active", "activating", "deactivating", "failed", "", "inactive\nactive"):
            with self.subTest(result=result), patch.object(initialize, "STATE", state), \
                    patch.object(initialize, "verify_images") as verify, \
                    patch.object(initialize.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=result)) as run:
                with self.assertRaisesRegex(RuntimeError, "active or unknown"):
                    initialize.initialize()
                run.assert_called_once()
                verify.assert_not_called()

    def test_inactive_service_cannot_hide_running_or_unknown_lxc(self):
        state = self.root / "state"
        lxc = state / "lxc/waydroid"
        lxc.mkdir(parents=True)
        (lxc / "config").write_text("partial configuration")
        for result in ("RUNNING", "FROZEN", "", "STOPPED\nRUNNING"):
            with self.subTest(result=result), patch.object(initialize, "STATE", state), \
                    patch.object(initialize, "verify_images") as verify, \
                    patch.object(initialize.subprocess, "run", side_effect=[
                        SimpleNamespace(returncode=0, stdout="inactive"),
                        SimpleNamespace(returncode=0, stdout=result)]) as run:
                with self.assertRaisesRegex(RuntimeError, "LXC container"):
                    initialize.initialize()
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args.args[0], [
                    "/usr/bin/lxc-info", "-P", str(state / "lxc"), "-n", "waydroid", "-s", "-H"])
                verify.assert_not_called()

    def test_writable_or_symlinked_image_ancestry_is_refused(self):
        self.root.chmod(0o777)
        with self.assertRaisesRegex(RuntimeError, "directories"):
            initialize.verify_images(self.root, "x86_64")
        self.root.chmod(0o700)
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "directories"):
            initialize.verify_images(alias, "x86_64")

    def test_fresh_software_config_is_seeded_before_normal_init_without_reset(self):
        state = self.root / "fresh-state"
        def command(arguments, **kwargs):
            self.assertEqual(arguments, ["/usr/bin/waydroid", "init"])
            self.assertEqual((state / "waydroid.cfg").read_text(),
                             "[waydroid]\n\n[properties]\n"
                             "ro.hardware.gralloc=default\nro.hardware.egl=swiftshader\n")
            (state / "rootfs").mkdir()
            lxc = state / "lxc/waydroid"
            lxc.mkdir(parents=True)
            for name in ("config", "waydroid.seccomp", "config_nodes", "config_session"):
                (lxc / name).write_text("finished configuration")
            (state / "waydroid_base.prop").write_text("finished properties")
        with patch.object(initialize, "STATE", state), \
                patch.object(initialize, "verify_images"), \
                patch.object(initialize, "is_fp6", return_value=False), \
                patch.object(initialize, "_software_graphics_required", return_value=True), \
                patch.object(initialize.subprocess, "run", side_effect=command) as run:
            initialize.initialize()
            self.assertTrue(initialize.runtime_complete(state))
            initialize.initialize()
            run.assert_called_once()

    def test_renderer_choices_and_interrupted_cfg_are_byte_preserved(self):
        state = self.root / "renderer-state"
        state.mkdir()
        cfg = state / "waydroid.cfg"
        for payload in (b"[waydroid]\n[properties]\n", b"# User choices\n[properties]\nro.hardware.egl=angle\n"):
            cfg.write_bytes(payload)
            with patch.object(initialize, "_software_graphics_required") as probe:
                initialize._seed_initial_graphics(state)
            self.assertEqual(cfg.read_bytes(), payload)
            probe.assert_not_called()

    def test_untrusted_cfg_and_state_ancestry_cannot_seed_properties(self):
        state = self.root / "graphics-state"
        state.mkdir()
        cfg = state / "waydroid.cfg"
        cfg.symlink_to(self.root / "luma-images.json")
        with self.assertRaises(OSError):
            initialize._seed_initial_graphics(state)
        cfg.unlink()
        state.chmod(0o777)
        with patch.object(initialize, "is_fp6", return_value=False), \
                patch.object(initialize, "_software_graphics_required", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "directories"):
                initialize._seed_initial_graphics(state)
        self.assertFalse(cfg.exists())

    def test_accelerated_gpu_and_fp6_do_not_receive_software_configuration(self):
        for fp6, software in ((False, False), (True, True)):
            state = self.root / f"unchanged-{fp6}"
            with patch.object(initialize, "is_fp6", return_value=fp6), \
                    patch.object(initialize, "_software_graphics_required", return_value=software) as probe:
                initialize._seed_initial_graphics(state)
            self.assertFalse(state.exists())
            if fp6:
                probe.assert_not_called()


class GraphicsCapabilityTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.dri = self.root / "dri"
        self.dri.mkdir()
        self.sys_drm = self.root / "drm"

    def node(self, name, driver):
        path = self.dri / name
        path.touch()
        device = self.sys_drm / name / "device"
        device.mkdir(parents=True)
        (device / "uevent").write_text("DRIVER=" + driver + "\n")
        return path

    def probe(self):
        original = Path.lstat
        def metadata(path):
            if path.parent == self.dri:
                return SimpleNamespace(st_mode=stat.S_IFCHR | 0o660, st_uid=0,
                                       st_rdev=os.makedev(226, int(path.name[7:])))
            return original(path)
        with patch.object(Path, "lstat", metadata):
            return initialize._software_graphics_required(self.dri, self.sys_drm)

    def test_no_render_node_and_only_upstream_unsupported_gpu_use_software(self):
        self.assertTrue(self.probe())
        self.node("renderD128", "nvidia")
        with patch.object(initialize, "_virtio_has_3d") as virtio:
            self.assertTrue(self.probe())
        virtio.assert_not_called()

    def test_real_gpu_keeps_upstream_selection(self):
        self.node("renderD128", "i915")
        with patch.object(initialize, "_virtio_has_3d") as virtio:
            self.assertFalse(self.probe())
        virtio.assert_not_called()

    def test_virtio_3d_control_and_first_node_order_match_upstream(self):
        first = self.node("renderD128", "virtio_gpu")
        self.node("renderD129", "amdgpu")
        for accelerated in (False, True):
            with patch.object(initialize, "_virtio_has_3d", return_value=accelerated) as virtio:
                self.assertEqual(self.probe(), not accelerated)
                virtio.assert_called_once_with(first)

    def test_virtio_transport_resolves_real_gpu_child_and_queries_capability(self):
        node = self.node("renderD128", "virtio-pci")
        device = self.sys_drm / node.name / "device"
        child = device / "virtio0"
        child.mkdir()
        (child / "uevent").write_text("DRIVER=virtio_gpu\nMODALIAS=virtio:d00000010v00001AF4\n")
        # Test runners may be nonroot; this models the kernel-owned sysfs dir.
        original = Path.lstat
        def metadata(path):
            if path == child:
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
            return original(path)
        for accelerated in (False, True):
            with patch.object(Path, "lstat", metadata), \
                    patch.object(initialize, "_virtio_has_3d", return_value=accelerated) as query:
                self.assertEqual(self.probe(), not accelerated)
                query.assert_called_once_with(node)

    def test_missing_ambiguous_and_unrelated_virtio_children_fail_closed(self):
        node = self.node("renderD128", "virtio-pci")
        device = self.sys_drm / node.name / "device"
        with self.assertRaisesRegex(RuntimeError, "identify"):
            self.probe()
        child = device / "virtio0"
        child.mkdir()
        (child / "uevent").write_text("DRIVER=virtio_blk\n")
        with patch.object(initialize, "_virtio_has_3d") as query:
            with self.assertRaisesRegex(RuntimeError, "identify"):
                self.probe()
            (child / "uevent").write_text("DRIVER=virtio_gpu\nMODALIAS=virtio:d00000002v00001AF4\n")
            with self.assertRaisesRegex(RuntimeError, "identify"):
                self.probe()
            (device / "virtio1").mkdir()
            with self.assertRaisesRegex(RuntimeError, "identify"):
                self.probe()
            query.assert_not_called()

    def test_writable_or_symlink_virtio_child_cannot_claim_capability(self):
        node = self.node("renderD128", "virtio-pci")
        child = self.sys_drm / node.name / "device/virtio0"
        child.mkdir()
        (child / "uevent").write_text("DRIVER=virtio_gpu\nMODALIAS=virtio:d00000010v00001AF4\n")
        child.chmod(0o777)
        with self.assertRaisesRegex(RuntimeError, "genuine"):
            self.probe()
        child.chmod(0o755)
        moved = child.with_name("original")
        child.rename(moved)
        child.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "genuine"):
            self.probe()

    def test_unknown_driver_and_ioctl_error_are_not_silent_acceleration_or_fallback(self):
        node = self.node("renderD128", "")
        with self.assertRaisesRegex(RuntimeError, "identify"):
            self.probe()
        (self.sys_drm / node.name / "device/uevent").write_text("DRIVER=virtio_gpu\n")
        with patch.object(initialize, "_virtio_has_3d", side_effect=OSError("query failed")):
            with self.assertRaisesRegex(OSError, "query failed"):
                self.probe()

    def test_fake_regular_render_node_cannot_claim_gpu_capability(self):
        self.node("renderD128", "virtio_gpu")
        with self.assertRaisesRegex(RuntimeError, "genuine"):
            initialize._software_graphics_required(self.dri, self.sys_drm)

    def test_native_ioctl_abi_and_result_pointer(self):
        def ioctl(descriptor, operation, request):
            self.assertEqual(descriptor, 41)
            self.assertEqual(operation, 0xC0106443)
            self.assertEqual(ctypes.sizeof(request), 16)
            self.assertEqual(request[0], 1)
            ctypes.c_uint64.from_address(request[1]).value = 1
        metadata = SimpleNamespace(st_mode=stat.S_IFCHR | 0o660, st_uid=0,
                                   st_rdev=os.makedev(226, 128))
        with patch.object(initialize.os, "open", return_value=41) as opened, \
                patch.object(initialize.os, "fstat", return_value=metadata), \
                patch.object(initialize.os, "close") as closed, \
                patch.object(initialize.fcntl, "ioctl", side_effect=ioctl):
            self.assertTrue(initialize._virtio_has_3d(Path("/dev/dri/renderD128")))
        self.assertTrue(opened.call_args.args[1] & os.O_NOFOLLOW)
        closed.assert_called_once_with(41)


if __name__ == "__main__":
    unittest.main()
