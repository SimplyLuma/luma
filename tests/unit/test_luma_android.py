#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/luma-android"))

from luma_android.apk import (  # noqa: E402
    inspect_apk,
    local_apk_from_uri,
    native_abis_for_apk,
    processor_summary,
    stage_apk,
    stage_android_package,
    staged_package_root,
    supports_content_type,
)
from luma_android.config import (  # noqa: E402
    device_class,
    load_device_profile,
    load_runtime_config,
)
from luma_android.cli import _application_name, _report_launch_failure  # noqa: E402
from luma_android.engine import (  # noqa: E402
    AndroidApplication,
    AndroidDeviceProfile,
    CommandResult,
    WaydroidEngine,
    _fp6_software_lab_authorized,
    _fp6_software_viewer_environment,
    _fp6_software_viewer_arguments,
    _monitor_fp6_software_viewer,
    _host_booted_in_charger_mode,
    _prepare_interactive_windowing,
    _validate_package_name,
    select_compatible_splits,
)
from luma_android.errors import (  # noqa: E402
    ApkValidationError,
    LumaAndroidError,
    RuntimeUnavailableError,
)
from luma_android.kernel import (  # noqa: E402
    KernelAdmission,
    fp6_kernel_admission,
    fp6_software_presentation_active,
    fp6_software_service_admitted,
)
from luma_android.receipts import write_install_receipt  # noqa: E402


def apk(path: Path, payload: bytes = b"payload", signature: bool = True) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"\x03\x00manifest")
        archive.writestr("classes.dex", payload)
        if signature:
            archive.writestr("META-INF/LUMA.RSA", b"signature")


def native_apk(path: Path, abi: str) -> None:
    apk(path)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr(f"lib/{abi}/libluma.so", b"native")


def package_set(path: Path, members: dict[str, Path]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, source in members.items():
            archive.write(source, name)


class ApkTests(unittest.TestCase):
    def test_inspects_and_hashes_regular_apk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "safe.apk"
            apk(path)
            result = inspect_apk(path, 1024 * 1024)
            self.assertEqual(result.display_name, "safe.apk")
            self.assertTrue(result.has_manifest)
            self.assertTrue(result.has_v1_signature_files)
            self.assertEqual(len(result.sha256), 64)

    def test_native_abi_inventory_is_bounded_to_android_library_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.apk"
            native_apk(path, "arm64-v8a")
            with zipfile.ZipFile(path, "a") as archive:
                archive.writestr("assets/lib/x86_64/not-native.so", b"asset")
            self.assertEqual(native_abis_for_apk(path), ("arm64-v8a",))

    def test_inspection_records_native_abis_for_split_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.apk"
            arm = root / "arm.apk"
            bundle = root / "native.apkm"
            apk(base)
            native_apk(arm, "arm64-v8a")
            package_set(
                bundle,
                {"base.apk": base, "split_config.arm64_v8a.apk": arm},
            )
            inspection = inspect_apk(bundle, 1024 * 1024)
            self.assertEqual(inspection.native_abis, ("arm64-v8a",))

    def test_processor_summary_explains_native_and_universal_packages(self) -> None:
        with mock.patch("luma_android.apk.platform.machine", return_value="aarch64"):
            self.assertIn("Native match", processor_summary(("arm64-v8a",)))
            self.assertIn("Universal", processor_summary(()))

    def test_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            real = Path(directory) / "real.apk"
            link = Path(directory) / "link.apk"
            apk(real)
            link.symlink_to(real)
            with self.assertRaises(ApkValidationError):
                inspect_apk(link, 1024 * 1024)

    def test_rejects_archive_without_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "not-an-app.apk"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("classes.dex", b"dex")
            with self.assertRaisesRegex(ApkValidationError, "manifest"):
                inspect_apk(path, 1024 * 1024)

    def test_rejects_oversize_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.apk"
            apk(path, os.urandom(2048))
            with self.assertRaisesRegex(ApkValidationError, "larger"):
                inspect_apk(path, 32)

    def test_staging_is_private_and_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "safe.apk"
            staging = root / "staging"
            apk(source)
            inspection = inspect_apk(source, 1024 * 1024)
            with mock.patch.dict(
                os.environ, {"LUMA_ANDROID_STAGING_ROOT": str(staging)}
            ):
                staged = stage_apk(source, inspection)
            self.assertEqual(staged.read_bytes(), source.read_bytes())
            self.assertEqual(stat.S_IMODE(staged.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(staged.parent.stat().st_mode), 0o700)

    def test_staging_uses_disk_backed_state_instead_of_runtime_tmpfs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "safe.apk"
            state = root / "state"
            runtime = root / "runtime"
            runtime.mkdir()
            apk(source)
            inspection = inspect_apk(source, 1024 * 1024)
            environment = {
                "XDG_STATE_HOME": str(state),
                "XDG_RUNTIME_DIR": str(runtime),
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                os.environ.pop("LUMA_ANDROID_STAGING_ROOT", None)
                staged = stage_apk(source, inspection)
            self.assertTrue(staged.is_relative_to(state))
            self.assertFalse(staged.is_relative_to(runtime))

    def test_split_package_is_inspected_and_staged_in_base_first_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base-source.apk"
            split = root / "split-source.apk"
            bundle = root / "safe.apks"
            staging = root / "staging"
            apk(base, b"base")
            apk(split, b"split")
            package_set(bundle, {"splits/config.en.apk": split, "base.apk": base})

            inspection = inspect_apk(bundle, 1024 * 1024)
            self.assertEqual(inspection.package_format, "apks")
            self.assertEqual(inspection.apk_entries, ("base.apk", "splits/config.en.apk"))
            with mock.patch.dict(
                os.environ, {"LUMA_ANDROID_STAGING_ROOT": str(staging)}
            ):
                payloads = stage_android_package(bundle, inspection)
            self.assertEqual(len(payloads), 2)
            self.assertEqual(payloads[0].read_bytes(), base.read_bytes())
            self.assertEqual(payloads[1].read_bytes(), split.read_bytes())
            self.assertEqual(staged_package_root(payloads), payloads[0].parent.parent)

    def test_compressed_bundle_can_be_smaller_than_its_base_apk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base-source.apk"
            bundle = root / "compressed.apkm"
            staging = root / "staging"
            apk(base, b"A" * (1024 * 1024))
            package_set(bundle, {"base.apk": base})
            self.assertLess(bundle.stat().st_size, base.stat().st_size)

            inspection = inspect_apk(bundle, 2 * 1024 * 1024)
            with mock.patch.dict(
                os.environ, {"LUMA_ANDROID_STAGING_ROOT": str(staging)}
            ):
                payloads = stage_android_package(bundle, inspection)
            self.assertEqual(payloads[0].read_bytes(), base.read_bytes())

    def test_xapk_accepts_one_unambiguous_non_config_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "main.apk"
            split = root / "config.apk"
            bundle = root / "safe.xapk"
            apk(base, b"base")
            apk(split, b"split")
            package_set(bundle, {"com.example.app.apk": base, "config.arm64_v8a.apk": split})
            inspection = inspect_apk(bundle, 1024 * 1024)
            self.assertEqual(inspection.apk_entries[0], "com.example.app.apk")

    def test_rejects_ambiguous_package_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.apk"
            second = root / "second.apk"
            bundle = root / "ambiguous.apkm"
            apk(first, b"first")
            apk(second, b"second")
            package_set(bundle, {"one.apk": first, "two.apk": second})
            with self.assertRaisesRegex(ApkValidationError, "base APK"):
                inspect_apk(bundle, 1024 * 1024)


class PolicyTests(unittest.TestCase):
    def test_config_defaults_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = load_runtime_config(Path(directory) / "missing")
        self.assertFalse(config.idle_freeze)
        self.assertEqual(config.engine, "waydroid")

    def test_explicit_handheld_device_class(self) -> None:
        with mock.patch.dict(os.environ, {"LUMA_DEVICE_CLASS": "handheld"}):
            self.assertEqual(device_class(), "handheld")

    def test_handheld_profile_does_not_warm_at_login(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "LUMA_DEVICE_CLASS": "handheld",
                "LUMA_ANDROID_PROFILE_ROOT": str(
                    REPO_ROOT / "config/android/profiles"
                ),
            },
        ):
            self.assertFalse(load_device_profile().keep_warm)

    def test_policy_generator_emits_bounded_systemd_dropin(self) -> None:
        generator = REPO_ROOT / "src/luma-android/bin/luma-android-policy-generator"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "generated"
            environment = os.environ.copy()
            environment["LUMA_ANDROID_PROFILE_ROOT"] = str(REPO_ROOT / "config/android/profiles")
            subprocess.run([sys.executable, generator, output], check=True, env=environment)
            dropin = output / "waydroid-container.service.d/40-luma-resource-policy.conf"
            text = dropin.read_text(encoding="utf-8")
            self.assertIn("CPUWeight=180", text)
            self.assertIn("CPUQuota=1000%", text)
            self.assertIn("MemoryHigh=60%", text)
            self.assertIn("MemoryMax=75%", text)
            # systemd 259 warns that CPUAccounting= was removed on every reload.
            self.assertNotIn("CPUAccounting=", text)

    def test_policy_generator_emits_strict_handheld_ceiling(self) -> None:
        generator = REPO_ROOT / "src/luma-android/bin/luma-android-policy-generator"
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ,
            {
                "LUMA_ANDROID_PROFILE_ROOT": str(
                    REPO_ROOT / "config/android/profiles"
                ),
                "LUMA_DEVICE_CLASS": "handheld",
            },
        ):
            output = Path(directory) / "generated"
            subprocess.run([sys.executable, generator, output], check=True)
            text = (
                output / "waydroid-container.service.d/40-luma-resource-policy.conf"
            ).read_text(encoding="utf-8")
            self.assertIn("CPUWeight=55", text)
            self.assertIn("CPUQuota=300%", text)
            self.assertIn("MemoryHigh=28%", text)
            self.assertIn("MemoryMax=40%", text)
            self.assertIn("TasksMax=1536", text)

    def test_policy_generator_preserves_builtin_binder_without_binderfs(self) -> None:
        generator = REPO_ROOT / "src/luma-android/bin/luma-android-policy-generator"
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ,
            {
                "LUMA_ANDROID_PROFILE_ROOT": str(REPO_ROOT / "config/android/profiles"),
                "LUMA_DEVICE_CLASS": "handheld",
                "LUMA_BINDER_COMPAT": "force",
            },
        ):
            output = Path(directory) / "generated"
            subprocess.run([sys.executable, generator, output], check=True)
            text = (
                output / "waydroid-container.service.d/40-luma-resource-policy.conf"
            ).read_text(encoding="utf-8")
            self.assertIn("ExecStartPre=\n", text)
            self.assertIn("ExecStartPre=/usr/libexec/luma-waydroid-binder-compat", text)
            self.assertEqual((output / "dev-binderfs.mount").readlink(), Path("/dev/null"))

    def test_policy_generator_selects_scoped_fp6_graphics_fallback(self) -> None:
        generator = REPO_ROOT / "src/luma-android/bin/luma-android-policy-generator"
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ,
            {
                "LUMA_ANDROID_PROFILE_ROOT": str(REPO_ROOT / "config/android/profiles"),
                "LUMA_DEVICE_CLASS": "handheld",
                "LUMA_BINDER_COMPAT": "off",
                "LUMA_FP6_GRAPHICS_COMPAT": "force",
            },
        ):
            output = Path(directory) / "generated"
            subprocess.run([sys.executable, generator, output], check=True)
            text = (
                output / "waydroid-container.service.d/40-luma-resource-policy.conf"
            ).read_text(encoding="utf-8")
            self.assertNotIn("luma-waydroid-binder-compat", text)
            self.assertIn("luma-waydroid-fp6-graphics-compat", text)
            self.assertIn("luma-waydroid-fp6-gpu-watchdog --check", text)
            self.assertIn("BindsTo=luma-waydroid-fp6-gpu-watchdog.service", text)

    def test_fp6_graphics_helper_preserves_explicit_overrides(self) -> None:
        helper = REPO_ROOT / "src/luma-android/bin/luma-waydroid-fp6-graphics-compat"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compatible = root / "compatible"
            compatible.write_bytes(b"fairphone,fp6\0qcom,milos\0")
            config = root / "waydroid.cfg"
            config.write_text(
                "[waydroid]\narch = arm64_only\n\n[properties]\n"
                "ro.hardware.egl = custom\n",
                encoding="utf-8",
            )
            config.chmod(0o600)
            subprocess.run(
                [sys.executable, helper],
                check=True,
                env={
                    **os.environ,
                    "LUMA_DEVICE_TREE_COMPATIBLE": str(compatible),
                    "LUMA_WAYDROID_CONFIG": str(config),
                    "LUMA_WAYDROID_EXECUTABLE": "/usr/bin/true",
                },
            )
            text = config.read_text(encoding="utf-8")
            self.assertIn("ro.hardware.egl = custom", text)
            self.assertIn("ro.hardware.gralloc = default", text)
            self.assertIn("ro.hardware.vulkan = pastel", text)
            self.assertIn("persist.waydroid.width = 558", text)
            self.assertIn("persist.waydroid.height = 1088", text)
            self.assertIn(
                "persist.luma.fp6_software_canvas = 558x1088-sdl-2x-v1", text
            )
            self.assertIn("persist.waydroid.no_background_subsurface = true", text)
            self.assertIn("persist.waydroid.use_subsurface = false", text)
            self.assertIn("persist.waydroid.cursor_on_subsurface = false", text)
            self.assertEqual(config.stat().st_mode & 0o777, 0o644)

    def test_fp6_graphics_helper_migrates_stale_explicit_canvas_once(self) -> None:
        helper = REPO_ROOT / "src/luma-android/bin/luma-waydroid-fp6-graphics-compat"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compatible = root / "compatible"
            compatible.write_bytes(b"fairphone,fp6\0qcom,milos\0")
            config = root / "waydroid.cfg"
            config.write_text(
                "[properties]\n"
                "persist.waydroid.width = 558\n"
                "persist.waydroid.height = 1242\n",
                encoding="utf-8",
            )
            subprocess.run(
                [sys.executable, helper],
                check=True,
                env={
                    **os.environ,
                    "LUMA_DEVICE_TREE_COMPATIBLE": str(compatible),
                    "LUMA_WAYDROID_CONFIG": str(config),
                    "LUMA_WAYDROID_EXECUTABLE": "/usr/bin/true",
                },
            )
            text = config.read_text(encoding="utf-8")
            self.assertIn("persist.waydroid.width = 558", text)
            self.assertIn("persist.waydroid.height = 1088", text)
            self.assertIn(
                "persist.luma.fp6_software_canvas = 558x1088-sdl-2x-v1", text
            )

    def test_broker_owns_private_runtime_staging_directory(self) -> None:
        unit = (REPO_ROOT / "src/luma-android/data/luma-android.service").read_text()
        self.assertIn("RuntimeDirectory=luma-android", unit)
        self.assertIn("RuntimeDirectoryMode=0700", unit)
        self.assertIn("-%h/.local/state/luma-android", unit)
        self.assertIn("StateDirectory=luma-android", unit)
        self.assertIn("NoNewPrivileges=yes", unit)
        session_unit = (
            REPO_ROOT / "src/luma-android/data/luma-android-session.service"
        ).read_text()
        self.assertIn("NoNewPrivileges=yes", session_unit)
        tmpfiles = (
            REPO_ROOT / "src/luma-android/data/luma-android-user.conf"
        ).read_text()
        self.assertIn("%h/.local/share/waydroid 0700", tmpfiles)

    def test_user_units_never_start_in_the_greeter(self) -> None:
        # GDM's greeter is a dynamic UID (e.g. 60578), so !@system alone does
        # not exclude it; its imported session class does.
        for name in (
            "luma-android.service",
            "luma-android-session.service",
            "luma-android-fp6-software-compositor.service",
        ):
            unit = (REPO_ROOT / "src/luma-android/data" / name).read_text()
            unit_section = unit.split("[Service]", 1)[0]
            lines = unit_section.splitlines()
            self.assertIn("ConditionUser=!@system", lines, name)
            self.assertIn("ConditionEnvironment=XDG_SESSION_CLASS=user", lines, name)

    def test_broker_can_open_dconf_runtime_file_under_strict_sandbox(self) -> None:
        unit = (REPO_ROOT / "src/luma-android/data/luma-android.service").read_text()
        self.assertIn("ProtectSystem=strict", unit)
        read_write = next(
            line for line in unit.splitlines() if line.startswith("ReadWritePaths=")
        )
        self.assertIn("-%t/dconf", read_write.split("=", 1)[1].split())
        self.assertIn("ExecStartPre=+/usr/bin/mkdir -p -m 0700 %t/dconf", unit)
        # Only the dconf directory is granted, never the whole runtime dir.
        self.assertNotIn("-%t ", read_write + " ")

    def test_fp6_watchdog_treats_safe_containment_stop_as_success(self) -> None:
        unit = (
            REPO_ROOT
            / "src/luma-android/data/luma-waydroid-fp6-gpu-watchdog.service"
        ).read_text()
        self.assertIn("SuccessExitStatus=71", unit)
        self.assertNotIn("SuccessExitStatus=70", unit)


class EngineTests(unittest.TestCase):
    def test_window_chrome_follows_posture_in_both_composition_modes(self):
        engine = WaydroidEngine("waydroid")
        for posture in ("desktop", "tablet", "handheld"):
            for multi_window in (False, True):
                with self.subTest(posture=posture, multi_window=multi_window), \
                     mock.patch("luma_android.engine.device_class", return_value=posture), \
                     mock.patch.object(engine, "status", return_value={"session": "RUNNING"}), \
                     mock.patch.object(engine, "wait_for_boot_completion"), \
                     mock.patch.object(engine, "wait_for_application_service"), \
                     mock.patch.object(engine, "_run_as_android_package_manager"), \
                     mock.patch.object(engine, "_run") as run:
                    engine.ensure_ready(multi_window=multi_window, detached=True)
                    run.assert_any_call("prop", "set", "persist.waydroid.multi_windows",
                                        "true" if multi_window else "false", timeout=30)
                    run.assert_any_call("prop", "set", "persist.luma.host_window_chrome",
                                        "false" if posture == "handheld" else "true", timeout=30)

    def test_readiness_uses_configured_composition_unless_explicitly_overridden(self):
        engine = WaydroidEngine("waydroid")
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "android.conf"
            config.write_text("[runtime]\nmulti_window=false\n")
            with mock.patch.dict(os.environ, {"LUMA_ANDROID_CONFIG": str(config)}), \
                 mock.patch.object(engine, "status", return_value={"session": "RUNNING"}), \
                 mock.patch.object(engine, "wait_for_boot_completion"), \
                 mock.patch.object(engine, "wait_for_application_service"), \
                 mock.patch.object(engine, "_run_as_android_package_manager"), \
                 mock.patch.object(engine, "_run") as run:
                for explicit, expected in ((None, "false"), (True, "true")):
                    run.reset_mock()
                    engine.ensure_ready(multi_window=explicit, detached=True)
                    run.assert_any_call("prop", "set", "persist.waydroid.multi_windows", expected, timeout=30)

    def test_failed_launch_uses_launcher_name_and_records_visible_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"XDG_DATA_HOME": directory}
        ):
            application_root = Path(directory) / "applications"
            application_root.mkdir()
            (application_root / "waydroid.com.example.video.desktop").write_text(
                "[Desktop Entry]\nName=Example Video\n",
                encoding="utf-8",
            )
            self.assertEqual(_application_name("com.example.video"), "Example Video")
            with mock.patch(
                "luma_android.cli.shutil.which",
                side_effect=lambda name: f"/usr/bin/{name}",
            ), mock.patch("luma_android.cli.subprocess.run") as run:
                _report_launch_failure(
                    "com.example.video", RuntimeUnavailableError("hardware gate")
                )
        self.assertEqual(run.call_count, 2)
        logger, notifier = (call.args[0] for call in run.call_args_list)
        self.assertEqual(logger[0], "/usr/bin/logger")
        self.assertIn("package=com.example.video", logger[-1])
        self.assertEqual(notifier[0], "/usr/bin/notify-send")
        self.assertIn("--urgency=critical", notifier)
        self.assertIn("Example Video couldn’t open", notifier)

    def test_package_names_are_bounded(self) -> None:
        _validate_package_name("org.fdroid.fdroid")
        for invalid in ("", "no-dot", "com.example;rm", "../../etc/passwd", "com.example app"):
            with self.assertRaises(LumaAndroidError):
                _validate_package_name(invalid)

    def test_fp6_interactive_windowing_is_fail_closed_on_unknown_kernel(self) -> None:
        with mock.patch("luma_android.engine.is_fp6", return_value=True), mock.patch(
            "luma_android.engine.fp6_kernel_admission",
            return_value=KernelAdmission(
                False, "a" * 64, "kernel-not-admitted"
            ),
        ):
            with self.assertRaisesRegex(LumaAndroidError, "has not passed"):
                _prepare_interactive_windowing()

    def test_fp6_interactive_windowing_admits_verified_ifpc_kernel(self) -> None:
        with mock.patch("luma_android.engine.is_fp6", return_value=True), mock.patch(
            "luma_android.engine.fp6_kernel_admission",
            return_value=KernelAdmission(True, "b" * 64, "verified-kernel"),
        ):
            _prepare_interactive_windowing()

    def test_fp6_interactive_windowing_admits_only_verified_software_path(self) -> None:
        with mock.patch("luma_android.engine.is_fp6", return_value=True), mock.patch(
            "luma_android.engine.fp6_kernel_admission",
            return_value=KernelAdmission(False, "b" * 64, "kernel-not-admitted"),
        ), mock.patch(
            "luma_android.engine.fp6_software_presentation_active", return_value=True
        ), mock.patch(
            "luma_android.engine._fp6_software_lab_authorized", return_value=True
        ), mock.patch.dict(
            os.environ, {"LUMA_ANDROID_FP6_SOFTWARE_PRESENTATION": "1"}
        ):
            _prepare_interactive_windowing()

    def test_fp6_interactive_windowing_uses_service_identity_in_sandbox(self) -> None:
        with mock.patch("luma_android.engine.is_fp6", return_value=True), mock.patch(
            "luma_android.engine.fp6_kernel_admission",
            return_value=KernelAdmission(False, "b" * 64, "kernel-not-admitted"),
        ), mock.patch(
            "luma_android.engine.fp6_software_presentation_active",
            return_value=False,
        ), mock.patch(
            "luma_android.engine.fp6_software_service_admitted",
            return_value=True,
        ) as service_admitted, mock.patch(
            "luma_android.engine._fp6_software_lab_authorized", return_value=True
        ), mock.patch.dict(
            os.environ, {"LUMA_ANDROID_FP6_SOFTWARE_PRESENTATION": "1"}
        ):
            _prepare_interactive_windowing()
        service_admitted.assert_called_once_with(session_user=mock.ANY)

    def test_fp6_software_lab_gate_requires_exact_root_owned_marker(self) -> None:
        metadata = mock.Mock(st_uid=0, st_mode=stat.S_IFREG | 0o600)
        with mock.patch("luma_android.engine.os.open", return_value=9), mock.patch(
            "luma_android.engine.os.fstat", return_value=metadata
        ), mock.patch(
            "luma_android.engine.os.read",
            return_value=b"LUMA_FP6_SOFTWARE_LAB_V1\n",
        ), mock.patch("luma_android.engine.os.close") as close:
            self.assertTrue(_fp6_software_lab_authorized())
        close.assert_called_once_with(9)

        metadata.st_mode = stat.S_IFREG | 0o622
        with mock.patch("luma_android.engine.os.open", return_value=10), mock.patch(
            "luma_android.engine.os.fstat", return_value=metadata
        ), mock.patch("luma_android.engine.os.close"):
            self.assertFalse(_fp6_software_lab_authorized())

    def test_fp6_software_presentation_requires_exact_cpu_loopback_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_users = root / "run-user"
            proc = root / "proc"
            uid = os.getuid()
            user_runtime = runtime_users / str(uid)
            runtime = user_runtime / "luma-android"
            process = proc / "4242"
            (process / "fd").mkdir(parents=True)
            (process / "net").mkdir()
            runtime.mkdir(parents=True, mode=0o700)
            os.chmod(runtime, 0o700)
            (runtime / "rdp-weston.pid").write_text("4242\n", encoding="ascii")
            os.chmod(runtime / "rdp-weston.pid", 0o600)
            for name in ("rdp.crt", "rdp.key"):
                (runtime / name).write_text("test\n", encoding="ascii")
                os.chmod(runtime / name, 0o600)
            (runtime / "luma-android-rdp").touch(mode=0o600)
            (process / "exe").symlink_to("/usr/bin/weston")
            arguments = [
                "/usr/bin/weston", "--backend=rdp", "--renderer=pixman",
                "--shell=kiosk",
                "--config=/usr/share/luma/android/fp6-software-weston.ini",
                "--socket=luma-android-rdp", "--address=127.0.0.1",
                "--port=33990", "--width=558", "--height=1088",
                "--no-resizeable",
                f"--rdp-tls-cert={runtime / 'rdp.crt'}",
                f"--rdp-tls-key={runtime / 'rdp.key'}",
                f"--log={runtime / 'fp6-software-weston.log'}",
            ]
            (process / "cmdline").write_bytes(b"\0".join(a.encode() for a in arguments) + b"\0")
            (process / "environ").write_bytes(
                f"XDG_RUNTIME_DIR={runtime}\0".encode("ascii")
            )
            (process / "fd" / "9").symlink_to("socket:[12345]")
            (process / "net" / "tcp").write_text(
                "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
                "   0: 0100007F:84C6 00000000:0000 0A 00000000:00000000 00:00000000 00000000 "
                f"{uid} 0 12345\n",
                encoding="ascii",
            )
            with mock.patch("luma_android.kernel.stat.S_ISSOCK", return_value=True):
                self.assertTrue(
                    fp6_software_presentation_active(
                        runtime_users_root=runtime_users, proc_root=proc
                    )
                )
            (process / "cmdline").write_bytes(
                (b"\0".join(a.encode() for a in arguments)).replace(
                    b"--renderer=pixman", b"--renderer=gl"
                ) + b"\0"
            )
            with mock.patch("luma_android.kernel.stat.S_ISSOCK", return_value=True):
                self.assertFalse(
                    fp6_software_presentation_active(
                        runtime_users_root=runtime_users, proc_root=proc
                    )
                )

    def test_fp6_root_gate_authenticates_immutable_user_systemd_unit(self) -> None:
        output = "\n".join(
            (
                "ActiveState=active",
                "SubState=running",
                "FragmentPath=/usr/lib/systemd/user/luma-android-fp6-software-compositor.service",
                "DropInPaths=/usr/lib/systemd/user/service.d/10-timeout-abort.conf",
                "MainPID=4242",
                "ExecStart={ path=/usr/libexec/luma-waydroid-fp6-software-compositor ; "
                "argv[]=/usr/libexec/luma-waydroid-fp6-software-compositor ; "
                "ignore_errors=no ; pid=4242 ; status=0/0 }",
            )
        )
        completed = subprocess.CompletedProcess([], 0, stdout=output, stderr="")
        with mock.patch("luma_android.kernel.subprocess.run", return_value=completed) as run:
            self.assertTrue(fp6_software_service_admitted())
        self.assertIn("--machine=luma@.host", run.call_args.args[0])
        overridden = completed.__class__(
            [],
            0,
            stdout=output.replace(
                "/usr/lib/systemd/user/luma-android-fp6-software-compositor.service",
                "/home/luma/.config/systemd/user/luma-android-fp6-software-compositor.service",
                1,
            ),
            stderr="",
        )
        with mock.patch("luma_android.kernel.subprocess.run", return_value=overridden):
            self.assertFalse(fp6_software_service_admitted())

    def test_fp6_kernel_admission_uses_notes_digest_and_latches_faults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compatible = root / "compatible"
            compatible.write_bytes(b"fairphone,fp6\0qcom,milos\0")
            notes = root / "notes"
            notes.write_bytes(b"verified kernel notes")
            import hashlib

            digest = hashlib.sha256(notes.read_bytes()).hexdigest()
            allowlist = root / "allowlist"
            allowlist.write_text(f"{digest}  verified\n", encoding="utf-8")
            marker = root / "fault"
            admission = fp6_kernel_admission(
                compatible_path=compatible,
                notes_path=notes,
                allowlist_path=allowlist,
                fault_marker=marker,
            )
            self.assertTrue(admission.allowed)
            self.assertEqual(admission.notes_sha256, digest)
            marker.write_text("hangcheck detected gpu lockup\n", encoding="utf-8")
            admission = fp6_kernel_admission(
                compatible_path=compatible,
                notes_path=notes,
                allowlist_path=allowlist,
                fault_marker=marker,
            )
            self.assertFalse(admission.allowed)
            self.assertEqual(admission.reason, "gpu-fault-latched")

    def test_non_fp6_windowing_keeps_direct_display(self) -> None:
        with mock.patch("subprocess.run") as run, mock.patch(
            "luma_android.engine.is_fp6", return_value=False
        ), mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-7"}):
                _prepare_interactive_windowing()
                self.assertEqual(os.environ["WAYLAND_DISPLAY"], "wayland-7")
        run.assert_not_called()

    def test_engine_never_invokes_a_shell(self) -> None:
        engine = WaydroidEngine("waydroid")
        completed = subprocess.CompletedProcess(
            ["waydroid", "app", "list"], 0, stdout="Name: test\n", stderr=""
        )
        with mock.patch("shutil.which", return_value="/usr/bin/waydroid"), mock.patch(
            "subprocess.run", return_value=completed
        ) as run:
            self.assertIn("Name: test", engine.list_apps())
        self.assertIsInstance(run.call_args.args[0], list)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_application_listing_is_structured(self) -> None:
        engine = WaydroidEngine("waydroid")
        listing = "Name: F-Droid\npackageName: org.fdroid.fdroid\ncategories:\n"
        with mock.patch.object(engine, "list_apps", return_value=listing):
            applications = engine.applications()
        self.assertEqual(applications[0].name, "F-Droid")
        self.assertEqual(applications[0].package, "org.fdroid.fdroid")

    def test_stopped_runtime_lists_apps_from_first_class_launchers(self) -> None:
        engine = WaydroidEngine("waydroid")
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"XDG_DATA_HOME": directory}
        ), mock.patch.object(
            engine, "list_apps", side_effect=RuntimeUnavailableError("stopped")
        ):
            application_root = Path(directory) / "applications"
            application_root.mkdir()
            (application_root / "waydroid.com.example.notes.desktop").write_text(
                "[Desktop Entry]\nName=Example Notes\n",
                encoding="utf-8",
            )
            applications = engine.applications()
        self.assertEqual(
            applications,
            [AndroidApplication("Example Notes", "com.example.notes")],
        )

    def test_waydroid_launchers_use_supervised_luma_lifecycle(self) -> None:
        engine = WaydroidEngine("waydroid")
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"XDG_DATA_HOME": directory}
        ):
            application_root = Path(directory) / "applications"
            application_root.mkdir()
            launcher = application_root / "waydroid.com.example.app.desktop"
            launcher.write_text(
                "[Desktop Entry]\n"
                "Name=Example\n"
                "Exec=waydroid app launch com.example.app\n"
                "[Desktop Action AppSettings]\n"
                "Exec=waydroid app intent android.settings.APPLICATION_DETAILS_SETTINGS "
                "package:com.example.app\n",
                encoding="utf-8",
            )
            self.assertEqual(engine.synchronize_launchers(), 1)
            text = launcher.read_text(encoding="utf-8")
        self.assertIn("Exec=/usr/bin/luma-android launch com.example.app", text)
        self.assertIn("Exec=/usr/bin/luma-android permissions com.example.app", text)
        self.assertIn("X-Luma-Android=true", text)
        self.assertIn("StartupNotify=true", text)

    def test_permissions_open_without_a_shell(self) -> None:
        engine = WaydroidEngine("waydroid")
        with mock.patch.object(engine, "ensure_ready"), mock.patch.object(
            engine, "_run", return_value=None
        ) as run:
            engine.open_permissions("org.fdroid.fdroid")
        self.assertEqual(run.call_args.args[-1], "package:org.fdroid.fdroid")

    def test_warm_launch_dispatches_once(self) -> None:
        engine = WaydroidEngine("waydroid")
        with mock.patch.object(
            engine, "ensure_ready", return_value=False
        ), mock.patch.object(engine, "_run") as run:
            engine.launch("com.example.app")
        run.assert_called_once_with("app", "launch", "com.example.app", timeout=90)

    def test_cold_launch_reasserts_after_android_launcher_settles(self) -> None:
        engine = WaydroidEngine("waydroid")
        with mock.patch.object(
            engine, "ensure_ready", return_value=True
        ), mock.patch.object(engine, "_run") as run, mock.patch(
            "time.sleep"
        ) as sleep:
            engine.launch("com.example.app")
        self.assertEqual(
            run.call_args_list,
            [
                mock.call("app", "launch", "com.example.app", timeout=90),
                mock.call("app", "launch", "com.example.app", timeout=90),
            ],
        )
        sleep.assert_called_once_with(2.0)

    def test_remove_warms_runtime_and_preserves_data_when_requested(self) -> None:
        engine = WaydroidEngine("waydroid")
        with mock.patch.object(engine, "ensure_ready") as ready, mock.patch.object(
            engine, "_run_as_android_package_manager", return_value=None
        ) as run, mock.patch.object(engine, "wait_for_application_registry_stable") as stable, \
             mock.patch.object(engine, "installed_packages", return_value=set()), \
             mock.patch.object(engine, "synchronize_launchers"),              mock.patch("luma_android.activation.request_existing_restore") as close:
            engine.remove("org.fdroid.fdroid", keep_data=True)
        ready.assert_called_once_with(detached=True)
        close.assert_called_once_with("org.fdroid.fdroid", close=True)
        run.assert_called_once_with(["remove-package", "org.fdroid.fdroid", "keep-data"], timeout=200)
        stable.assert_called_once_with(timeout=30, stable_polls=3)

    def test_session_start_is_detached_then_polled(self) -> None:
        engine = WaydroidEngine("waydroid")
        states = iter(({"session": "STOPPED"}, {"session": "RUNNING"}))
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"XDG_STATE_HOME": directory}
        ), mock.patch.object(engine, "available", return_value=True), mock.patch.object(
            engine, "status", side_effect=lambda: next(states)
        ), mock.patch("subprocess.Popen") as popen, mock.patch(
            "time.sleep"
        ), mock.patch.object(engine, "wait_for_boot_completion") as boot, mock.patch.object(
            engine, "wait_for_application_service"
        ) as wait, mock.patch.object(
            engine, "_run"
        ) as run, mock.patch.object(
            engine, "_run_as_android_package_manager"
        ) as policy:
            engine.ensure_ready(multi_window=True, timeout=2, detached=True)
        popen.assert_called_once()
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        boot.assert_called_once()
        wait.assert_called_once()
        self.assertEqual(
            run.call_args_list,
            [
                mock.call(
                    "prop", "set", "persist.luma.device_class", "desktop", timeout=30
                ),
                mock.call(
                    "prop", "set", "persist.waydroid.multi_windows", "true", timeout=30
                ),
                mock.call(
                    "prop",
                    "set",
                    "persist.luma.host_window_chrome",
                    "true",
                    timeout=30,
                ),
                mock.call("prop", "set", "persist.luma.color_scheme", "light",
                          timeout=5, check=False),
                mock.call("prop", "set", "persist.luma.surface_treatment", "light",
                          timeout=5, check=False),
            ],
        )
        policy.assert_called_once_with(["input-policy", "desktop"], timeout=45)

    def test_desktop_input_policy_failure_does_not_block_ready_runtime(self) -> None:
        engine = WaydroidEngine("waydroid")
        with mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}), mock.patch.object(
            engine,
            "status",
            return_value={"session": "RUNNING", "wayland_display": "wayland-0"},
        ), mock.patch.object(
            engine, "wait_for_boot_completion"
        ), mock.patch.object(
            engine, "wait_for_application_service"
        ), mock.patch.object(
            engine, "_run"
        ), mock.patch.object(
            engine,
            "_run_as_android_package_manager",
            side_effect=RuntimeUnavailableError("settings service is restarting"),
        ) as policy:
            with self.assertLogs("luma_android.engine", level="WARNING"):
                engine.ensure_ready(multi_window=True, detached=True)

        policy.assert_called_once_with(["input-policy", "desktop"], timeout=45)

    def test_warm_frozen_runtime_is_thawed_before_readiness_probes(self) -> None:
        engine = WaydroidEngine("waydroid")
        with mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}), mock.patch.object(
            engine,
            "status",
            return_value={
                "session": "RUNNING",
                "container": "FROZEN",
                "wayland_display": "wayland-0",
            },
        ), mock.patch.object(
            engine, "wait_for_boot_completion"
        ), mock.patch.object(
            engine, "wait_for_application_service"
        ), mock.patch.object(
            engine, "_run"
        ), mock.patch("luma_android.engine.subprocess.run") as thaw, mock.patch.object(
            engine, "_run_as_android_package_manager"
        ):
            cold_start = engine.ensure_ready(multi_window=True, detached=True)

        self.assertFalse(cold_start)
        thaw.assert_called_once_with(
            ["gdbus", "call", "--system", "--dest", "id.waydro.Container",
             "--object-path", "/ContainerManager", "--method",
             "id.waydro.ContainerManager.Unfreeze"],
            check=True, capture_output=True, text=True, timeout=30,
        )

    def test_device_profile_retries_a_transient_compatibility_failure(self) -> None:
        engine = WaydroidEngine("waydroid")
        response = CommandResult(
            '{"abis":["x86_64","x86"],"density":420,"locale":"en-US"}',
            "",
            0,
        )
        with mock.patch.object(
            engine,
            "_android_device_profile_from_properties",
            side_effect=RuntimeUnavailableError("platform properties are starting"),
        ) as public_query, mock.patch.object(
            engine,
            "_run_as_android_package_manager",
            side_effect=(RuntimeUnavailableError("Android is still starting"), response),
        ) as query, mock.patch("time.sleep"):
            profile = engine._android_device_profile()

        self.assertEqual(
            profile, AndroidDeviceProfile(("x86_64", "x86"), 420, "en-US")
        )
        self.assertEqual(public_query.call_count, 3)
        self.assertEqual(
            query.call_args_list,
            [
                mock.call(["device-profile"], timeout=60),
                mock.call(["device-profile"], timeout=60),
            ],
        )

    def test_device_profile_prefers_public_waydroid_properties(self) -> None:
        engine = WaydroidEngine("waydroid")
        properties = {
            "ro.product.cpu.abilist": "x86_64,x86",
            "ro.sf.lcd_density": "420",
            "persist.sys.locale": "en-US",
        }

        def query(*arguments: str, **_kwargs: object) -> CommandResult:
            self.assertEqual(arguments[:2], ("prop", "get"))
            return CommandResult(properties[arguments[2]], "", 0)

        with mock.patch.object(engine, "_run", side_effect=query), mock.patch.object(
            engine, "_run_as_android_package_manager"
        ) as privileged:
            profile = engine._android_device_profile()

        self.assertEqual(
            profile, AndroidDeviceProfile(("x86_64", "x86"), 420, "en-US")
        )
        privileged.assert_not_called()

    def test_device_profile_uses_product_locale_fallback(self) -> None:
        engine = WaydroidEngine("waydroid")
        properties = {
            "ro.product.cpu.abilist": "arm64-v8a",
            "ro.sf.lcd_density": "420",
            "persist.sys.locale": "",
            "ro.product.locale": "en-US",
        }

        with mock.patch.object(
            engine,
            "_run",
            side_effect=lambda *arguments, **_kwargs: CommandResult(
                properties[arguments[2]], "", 0
            ),
        ):
            profile = engine._android_device_profile_from_properties()

        self.assertEqual(
            profile, AndroidDeviceProfile(("arm64-v8a",), 420, "en-US")
        )

    def test_handheld_profile_reaches_android_before_window_policy(self) -> None:
        engine = WaydroidEngine("waydroid")
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ,
            {
                "LUMA_DEVICE_CLASS": "handheld",
                "XDG_STATE_HOME": directory,
            },
        ), mock.patch.object(
            engine,
            "status",
            return_value={"session": "RUNNING", "wayland_display": "wayland-0"},
        ), mock.patch.object(
            engine, "wait_for_boot_completion"
        ), mock.patch.object(
            engine, "wait_for_application_service"
        ), mock.patch.object(
            engine, "_run"
        ) as run, mock.patch.object(
            engine, "_run_as_android_package_manager"
        ) as policy:
            engine.ensure_ready(multi_window=True, detached=True)

        self.assertEqual(
            run.call_args_list[0],
            mock.call(
                "prop", "set", "persist.luma.device_class", "handheld", timeout=30
            ),
        )
        self.assertNotIn(
            mock.call(
                "prop", "set", "persist.luma.device_class", "desktop", timeout=30
            ),
            run.call_args_list,
        )
        policy.assert_not_called()

    def test_charger_boot_promotes_android_before_application_wait(self) -> None:
        engine = WaydroidEngine("waydroid")
        with tempfile.TemporaryDirectory() as directory:
            command_line = Path(directory) / "cmdline"
            command_line.write_text(
                "quiet androidboot.mode=charger rootwait\n", encoding="utf-8"
            )
            environment = {
                "LUMA_DEVICE_CLASS": "handheld",
                "LUMA_PROC_CMDLINE": str(command_line),
            }
            with mock.patch.dict(os.environ, environment), mock.patch.object(
                engine,
                "status",
                return_value={"session": "RUNNING", "wayland_display": "wayland-0"},
            ), mock.patch.object(
                engine, "wait_for_boot_completion"
            ) as boot, mock.patch.object(
                engine, "wait_for_application_service"
            ) as wait, mock.patch.object(
                engine, "_run"
            ), mock.patch.object(
                engine, "_run_as_android_package_manager"
            ) as policy:
                engine.ensure_ready(detached=True)
        policy.assert_called_once_with(["boot-from-charger-mode"], timeout=30)
        boot.assert_called_once()
        wait.assert_called_once()

    def test_charger_boot_detection_requires_exact_kernel_argument(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command_line = Path(directory) / "cmdline"
            command_line.write_text(
                "quiet androidboot.mode=chargerish\n", encoding="utf-8"
            )
            with mock.patch.dict(
                os.environ, {"LUMA_PROC_CMDLINE": str(command_line)}
            ):
                self.assertFalse(_host_booted_in_charger_mode())

    def test_readiness_waits_for_android_package_manager(self) -> None:
        engine = WaydroidEngine("waydroid")
        responses = iter(
            (
                mock.Mock(returncode=0, stdout=""),
                mock.Mock(returncode=0, stdout="Name: Files\npackageName: com.android.documentsui\n"),
            )
        )
        with mock.patch.object(
            engine, "_run", side_effect=lambda *_args, **_kwargs: next(responses)
        ) as run, mock.patch("time.sleep"):
            engine.wait_for_application_service(__import__("time").monotonic() + 5)
        self.assertEqual(run.call_count, 2)

    def test_readiness_waits_for_android_boot_completion(self) -> None:
        engine = WaydroidEngine("waydroid")
        responses = iter(
            (
                CommandResult("0\n", "", 0),
                CommandResult("1\n", "", 0),
            )
        )
        with mock.patch.object(
            engine, "_run", side_effect=lambda *_args, **_kwargs: next(responses)
        ) as run, mock.patch("time.sleep"):
            engine.wait_for_boot_completion(__import__("time").monotonic() + 5)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args.args[:3], ("prop", "get", "sys.boot_completed")
        )

    def test_readiness_rebinds_a_stale_wayland_session(self) -> None:
        engine = WaydroidEngine("waydroid")
        states = iter(
            (
                {"session": "RUNNING", "wayland_display": "wayland-old"},
                {"session": "STOPPED"},
                {"session": "RUNNING", "wayland_display": "wayland-new"},
            )
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ,
            {"WAYLAND_DISPLAY": "wayland-new", "XDG_STATE_HOME": directory},
        ), mock.patch.object(engine, "status", side_effect=lambda: next(states)), mock.patch.object(
            engine, "stop_session"
        ) as stop, mock.patch.object(engine, "available", return_value=True), mock.patch(
            "subprocess.Popen"
        ), mock.patch("time.sleep"), mock.patch.object(
            engine, "wait_for_boot_completion"
        ), mock.patch.object(
            engine, "wait_for_application_service"
        ), mock.patch.object(engine, "_run"), mock.patch.object(
            engine, "_run_as_android_package_manager"
        ):
            engine.ensure_ready(timeout=2, detached=True)
        stop.assert_called_once()

    def test_single_apk_uses_verified_package_manager_transaction(self) -> None:
        engine = WaydroidEngine("waydroid")
        transaction = [Path("/tmp/luma-one.apk")]
        with mock.patch.object(
            engine, "_waydroid_package_transaction", return_value=transaction
        ), mock.patch.object(
            engine, "_run_as_android_package_manager"
        ) as run, mock.patch.object(
            engine, "wait_for_application_registry_stable"
        ) as stable, mock.patch.object(
            engine, "synchronize_launchers"
        ) as synchronize, mock.patch.object(
            Path, "unlink"
        ), mock.patch(
            "luma_android.engine.native_abis_for_apk", return_value=()
        ):
            engine.install(Path("/tmp/app.apk"))
        run.assert_called_once_with([
            "pm", "install", "--user", "0", "-r",
            "/data/waydroid_tmp/luma-one.apk",
        ], timeout=600)
        stable.assert_called_once_with(timeout=45)
        synchronize.assert_called_once_with()

    def test_single_apk_rejects_incompatible_native_abi_before_install(self) -> None:
        engine = WaydroidEngine("waydroid")
        profile = AndroidDeviceProfile(("x86_64", "x86"), 420, "en-US")
        with mock.patch(
            "luma_android.engine.native_abis_for_apk", return_value=("arm64-v8a",)
        ), mock.patch.object(
            engine, "_android_device_profile", return_value=profile
        ), mock.patch.object(
            engine, "_run_as_android_package_manager"
        ) as run:
            with self.assertRaisesRegex(RuntimeUnavailableError, "arm64-v8a"):
                engine.install_package([Path("/tmp/arm.apk")])
        run.assert_not_called()

    def test_split_install_is_one_atomic_package_manager_transaction(self) -> None:
        engine = WaydroidEngine("waydroid")
        transaction = [Path("/tmp/luma-one.apk"), Path("/tmp/luma-two.apk")]
        with mock.patch.object(
            engine, "_waydroid_package_transaction", return_value=transaction
        ), mock.patch.object(engine, "_run_as_android_package_manager") as run, mock.patch.object(
            engine, "wait_for_application_registry_stable"
        ) as stable, mock.patch.object(
            engine, "synchronize_launchers"
        ) as synchronize, mock.patch.object(Path, "unlink"):
            engine.install_package([Path("/tmp/base.apk"), Path("/tmp/config.apk")])
        run.assert_called_once_with(
            [
                "pm", "install", "--user", "0", "-r",
                "/data/waydroid_tmp/luma-one.apk",
                "/data/waydroid_tmp/luma-two.apk",
            ],
            timeout=600,
        )
        stable.assert_called_once_with(timeout=45)
        synchronize.assert_called_once_with()

    def test_package_set_selects_one_compatible_resource_configuration(self) -> None:
        members = (
            "base.apk",
            "split_config.arm64_v8a.apk",
            "split_config.x86_64.apk",
            "split_config.en.apk",
            "split_config.fr.apk",
            "split_config.xhdpi.apk",
            "split_config.xxhdpi.apk",
        )
        selected = select_compatible_splits(
            members,
            AndroidDeviceProfile(("x86_64", "x86"), 420, "en-US"),
        )
        self.assertEqual(
            selected,
            (
                "base.apk",
                "split_config.x86_64.apk",
                "split_config.xxhdpi.apk",
                "split_config.en.apk",
            ),
        )

    def test_package_set_falls_back_to_english_without_overselecting_abis(self) -> None:
        members = (
            "base.apk",
            "split_config.arm64_v8a.apk",
            "split_config.x86_64.apk",
            "split_config.en.apk",
            "split_config.fr.apk",
        )
        selected = select_compatible_splits(
            members,
            AndroidDeviceProfile(("arm64-v8a",), 420, "es-MX"),
        )
        self.assertEqual(
            selected,
            ("base.apk", "split_config.arm64_v8a.apk", "split_config.en.apk"),
        )

    def test_package_set_selects_feature_specific_abi_splits(self) -> None:
        members = (
            "base.apk",
            "split_config.arm64_v8a.apk",
            "split_config.armeabi_v7a.apk",
            "split_config.en.apk",
            "split_df_camera.apk",
            "split_df_camera.config.arm64_v8a.apk",
            "split_df_camera.config.armeabi_v7a.apk",
            "split_df_search.apk",
            "split_df_search.config.arm64_v8a.apk",
        )
        selected = select_compatible_splits(
            members,
            AndroidDeviceProfile(("arm64-v8a", "armeabi-v7a"), 420, "en-US"),
        )
        self.assertEqual(
            selected,
            (
                "base.apk",
                "split_df_camera.apk",
                "split_df_search.apk",
                "split_config.arm64_v8a.apk",
                "split_config.en.apk",
                "split_df_camera.config.arm64_v8a.apk",
                "split_df_search.config.arm64_v8a.apk",
            ),
        )

    def test_package_set_rejects_incompatible_feature_abi(self) -> None:
        members = (
            "base.apk",
            "split_df_camera.apk",
            "split_df_camera.config.x86_64.apk",
        )
        with self.assertRaisesRegex(
            RuntimeUnavailableError, "feature split_df_camera has no split"
        ):
            select_compatible_splits(
                members,
                AndroidDeviceProfile(("arm64-v8a",), 420, "en-US"),
            )

    def test_registry_stability_requires_repeated_identical_results(self) -> None:
        engine = WaydroidEngine("waydroid")
        listing = "Name: F-Droid\npackageName: org.fdroid.fdroid\n"
        responses = iter(
            (
                mock.Mock(returncode=0, stdout="Name: Settings\npackageName: com.android.settings\n"),
                mock.Mock(returncode=0, stdout=listing),
                mock.Mock(returncode=0, stdout=listing),
                mock.Mock(returncode=0, stdout=listing),
            )
        )
        with mock.patch.object(
            engine, "_run", side_effect=lambda *_args, **_kwargs: next(responses)
        ), mock.patch("time.sleep"):
            engine.wait_for_application_registry_stable(timeout=5, stable_polls=2)

    def test_fp6_software_viewer_uses_exact_2x_canvas_and_native_touch(self) -> None:
        arguments = _fp6_software_viewer_arguments("luma")

        self.assertEqual(arguments[0], "/usr/bin/sdl-freerdp")
        self.assertIn("+workarea", arguments)
        self.assertIn("/size:1116x2176", arguments)
        self.assertIn("/smart-sizing:558x1088", arguments)
        self.assertIn("/gdi:sw", arguments)
        self.assertIn("-gfx", arguments)
        self.assertIn("+multitouch", arguments)
        self.assertIn("/clipboard:direction-to:off,files-to:off", arguments)
        self.assertNotIn("-clipboard", arguments)
        self.assertIn("-decorations", arguments)
        self.assertIn("-grab-mouse", arguments)
        self.assertIn("-mouse-motion", arguments)
        self.assertNotIn("/f", arguments)
        self.assertNotIn("+dynamic-resolution", arguments)
        self.assertEqual(arguments[-1], "/network:lan")

    def test_fp6_software_viewer_defers_touch_hint_policy_to_sdl(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "WAYLAND_DISPLAY": "luma-android-rdp",
                "LUMA_ANDROID_HOST_WAYLAND_DISPLAY": "wayland-9",
                "SDL_TOUCH_MOUSE_EVENTS": "1",
                "SDL_MOUSE_TOUCH_EVENTS": "1",
            },
            clear=True,
        ):
            environment = _fp6_software_viewer_environment("wayland-9")

        self.assertEqual(environment["WAYLAND_DISPLAY"], "wayland-9")
        self.assertEqual(environment["SDL_VIDEO_DRIVER"], "wayland")
        self.assertEqual(environment["SDL_VIDEODRIVER"], "wayland")
        self.assertEqual(environment["SDL_RENDER_DRIVER"], "software")
        self.assertNotIn("SDL_TOUCH_MOUSE_EVENTS", environment)
        self.assertNotIn("SDL_MOUSE_TOUCH_EVENTS", environment)
        self.assertNotIn("LUMA_ANDROID_HOST_WAYLAND_DISPLAY", environment)

    def test_fp6_viewer_exit_removes_pid_and_stops_hidden_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "rdp-viewer.pid"
            pid_file.write_text("4321\n", encoding="ascii")
            viewer = mock.Mock(pid=4321)
            viewer.wait.return_value = 11
            with mock.patch("subprocess.run") as run:
                _monitor_fp6_software_viewer(viewer, pid_file)

        self.assertFalse(pid_file.exists())
        self.assertEqual(run.call_count, 2)
        self.assertIn("luma-android-fp6-software-compositor.service", run.call_args_list[0].args[0])
        self.assertEqual(run.call_args_list[1].args[0], ["/usr/bin/waydroid", "session", "stop"])


class ServiceTests(unittest.TestCase):
    def test_only_local_apk_uris_cross_the_bus_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "safe.apk"
            apk(path)
            self.assertEqual(local_apk_from_uri(path.as_uri()), path)
        for uri in (
            "https://example.invalid/app.apk",
            "file://remote-host/tmp/app.apk",
            "file:///tmp/not-an-apk.txt",
        ):
            with self.assertRaises(LumaAndroidError):
                local_apk_from_uri(uri)

    def test_all_declared_android_package_formats_cross_the_bus_boundary(self) -> None:
        self.assertTrue(supports_content_type("application/vnd.android.package-archive"))
        self.assertTrue(supports_content_type("application/vnd.projectluma.android-package-set"))
        for suffix in (".apk", ".apks", ".xapk", ".apkm"):
            path = Path("/tmp") / f"application{suffix}"
            self.assertEqual(local_apk_from_uri(path.as_uri()), path)


class ReceiptTests(unittest.TestCase):
    def test_receipt_is_atomic_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "safe.apk"
            apk(path)
            inspection = inspect_apk(path, 1024 * 1024)
            data = Path(directory) / "data"
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(data)}):
                receipt = write_install_receipt(inspection, "installed")
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(payload["apk"]["sha256"], inspection.sha256)
            self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
