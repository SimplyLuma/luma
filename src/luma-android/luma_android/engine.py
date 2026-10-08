from __future__ import annotations

import json
import logging
import os
import pwd
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .apk import native_abis_for_apk
from .appearance import surface_treatment
from .config import device_class, load_runtime_config
from .errors import LumaAndroidError, RuntimeUnavailableError
from .kernel import (
    FP6_SOFTWARE_PORT,
    FP6_SOFTWARE_SOCKET,
    fp6_kernel_admission,
    fp6_software_presentation_active,
    fp6_software_service_admitted,
    is_fp6,
)


LOGGER = logging.getLogger(__name__)
COLD_LAUNCH_REASSERT_DELAY_SECONDS = 2.0
FP6_SOFTWARE_PRESENTATION_ENV = "LUMA_ANDROID_FP6_SOFTWARE_PRESENTATION"
FP6_HOST_DISPLAY_ENV = "LUMA_ANDROID_HOST_WAYLAND_DISPLAY"
FP6_SOFTWARE_CANVAS = "558x1088"
FP6_SOFTWARE_HOST_PIXELS = "1116x2176"
FP6_SOFTWARE_LAB_MARKER = Path(
    "/run/luma/android/fp6-software-lab-authorized"
)
FP6_SOFTWARE_LAB_TOKEN = b"LUMA_FP6_SOFTWARE_LAB_V1\n"


def _fp6_software_lab_authorized(
    marker: Path = FP6_SOFTWARE_LAB_MARKER,
) -> bool:
    """Require an ephemeral, root-authenticated marker for unsafe FP6 labs.

    The marker is deliberately outside user configuration and below ``/run``
    so it cannot survive a reboot. No package creates it. Exact contents,
    root ownership, regular-file identity, and non-writable group/other mode
    make an ordinary application or session setting insufficient to bypass
    the physical kernel admission gate.
    """
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(marker, flags)
    except (FileNotFoundError, PermissionError, OSError):
        return False
    try:
        metadata = os.fstat(descriptor)
        if (
            metadata.st_uid != 0
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_mode & 0o022
        ):
            return False
        return os.read(descriptor, len(FP6_SOFTWARE_LAB_TOKEN) + 1) == (
            FP6_SOFTWARE_LAB_TOKEN
        )
    except OSError:
        return False
    finally:
        os.close(descriptor)


def _fp6_software_presentation_admitted() -> bool:
    """Authenticate the FP6 CPU presentation from either process context.

    An ordinary session process can perform the exact process, socket, and
    descriptor inspection.  A hardened systemd service with a private mount
    namespace cannot traverse another service's ``/proc/<pid>/exe`` or file
    descriptors even when both services belong to the same user.  In that
    context, authenticate the immutable compositor unit through the user
    manager instead.  The unit identity check rejects user overrides and
    unapproved drop-ins; the packaged unit supplies the loopback and
    ``PrivateDevices`` containment that the process-level path verifies
    directly.
    """
    if fp6_software_presentation_active():
        return True
    try:
        session_user = pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return False
    return fp6_software_service_admitted(session_user=session_user)


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


@dataclass(frozen=True)
class AndroidApplication:
    name: str
    package: str


@dataclass(frozen=True)
class AndroidDeviceProfile:
    abis: tuple[str, ...]
    density: int
    locale: str


class WaydroidEngine:
    def __init__(self, executable: str = "waydroid") -> None:
        self.executable = executable

    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    def installed_packages(self) -> set[str]:
        """Package Manager's user-zero installed state, excluding retained data.

        Waydroid's cached application metadata can continue listing a package
        removed with KEEP_DATA. It is not authoritative installation evidence.
        """
        result = self._run_as_android_package_manager(["installed-packages"], timeout=30)
        packages = set()
        for line in result.stdout.splitlines():
            if line.startswith("package:"):
                package = line[len("package:"):].strip()
                if package != "android":
                    _validate_package_name(package)
                packages.add(package)
        # Every configured runtime has Android system packages. Never erase
        # launchers in response to an empty/starting/unavailable runtime.
        if not packages:
            raise RuntimeUnavailableError("Android did not return its installed package registry.")
        return packages

    def live_launchable_packages(self) -> set[str]:
        """Fresh PackageManager-backed Binder inventory; no privilege or cache."""
        from .live_registry import live_launchable_packages
        return live_launchable_packages(self.executable)

    def _installed_packages_if_available(self) -> set[str] | None:
        if not self.available(): return None
        try: return self.installed_packages()
        except (RuntimeUnavailableError, OSError): return None

    def synchronize_launchers(self) -> int:
        """Route Waydroid launchers through Luma's supervised lifecycle."""
        data_root = Path(
            os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))
        )
        application_root = data_root / "applications"
        if not application_root.is_dir():
            return 0

        changed = 0
        installed = self._installed_packages_if_available()
        launch_pattern = re.compile(
            r"^Exec=(?:/usr/bin/)?waydroid app launch "
            r"(?P<package>[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)$"
        )
        permissions_pattern = re.compile(
            r"^Exec=(?:/usr/bin/)?waydroid app intent "
            r"android\.settings\.APPLICATION_DETAILS_SETTINGS package:"
            r"(?P<package>[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)$"
        )
        for launcher in application_root.glob("waydroid.*.desktop"):
            if launcher.is_symlink() or not launcher.is_file():
                continue
            package = launcher.name.removeprefix("waydroid.").removesuffix(".desktop")
            if installed is not None and package not in installed:
                # Retained Android data is not an installed application. Remove
                # only the generated Waydroid entry, never its icon/data files.
                launcher.unlink()
                changed += 1
                continue
            original = launcher.read_text(encoding="utf-8")
            # Prepare once at the existing import/reconciliation boundary. All
            # desktop consumers resolve the same asset; retain the provider's
            # source path so icon updates and rollback never depend on our PNG.
            from luma_appkit.icon_assets import prepare_application_icon
            main_entry = original.split('\n[Desktop Action', 1)[0].splitlines()
            source_icon = next((line.removeprefix('X-Luma-Original-Icon=')
                                for line in main_entry if line.startswith('X-Luma-Original-Icon=')), None)
            if source_icon is None:
                source_icon = next((line.removeprefix('Icon=') for line in main_entry
                                    if line.startswith('Icon=')), None)
            prepared_icon = prepare_application_icon(Path(source_icon), data_root) if source_icon else None
            in_main_entry = False
            has_luma_marker = "X-Luma-Android=true" in original.splitlines()
            has_startup_notify = any(
                line.startswith("StartupNotify=") for line in original.splitlines()
            )
            lines: list[str] = []
            for line in original.splitlines(keepends=True):
                ending = "\n" if line.endswith("\n") else ""
                body = line[:-1] if ending else line
                if body.startswith('['):
                    in_main_entry = body == '[Desktop Entry]'
                if in_main_entry and body.startswith('Icon=') and prepared_icon is not None:
                    body = 'Icon=' + str(prepared_icon)
                match = launch_pattern.fullmatch(body)
                if match:
                    package = match.group("package")
                    _validate_package_name(package)
                    body = f"Exec=/usr/bin/luma-android launch {package}"
                else:
                    match = permissions_pattern.fullmatch(body)
                    if match:
                        package = match.group("package")
                        _validate_package_name(package)
                        body = f"Exec=/usr/bin/luma-android permissions {package}"
                lines.append(body + ending)
                if body == "[Desktop Entry]":
                    if source_icon and not any(line.startswith('X-Luma-Original-Icon=') for line in main_entry):
                        lines.append('X-Luma-Original-Icon=' + source_icon + '\n')
                    if not has_luma_marker:
                        lines.append("X-Luma-Android=true\n")
                    if not has_startup_notify:
                        lines.append("StartupNotify=true\n")
            updated = "".join(lines)
            if updated == original:
                continue
            descriptor, temporary_name = tempfile.mkstemp(
                dir=application_root, prefix=f".{launcher.name}.", text=True
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(updated)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary_name, launcher.stat().st_mode & 0o777)
                os.replace(temporary_name, launcher)
            finally:
                Path(temporary_name).unlink(missing_ok=True)
            changed += 1
        return changed

    def _run(self, *arguments: str, timeout: int = 120, check: bool = True) -> CommandResult:
        if not self.available():
            raise RuntimeUnavailableError("The Android runtime engine is not installed.")
        environment = os.environ.copy()
        environment.setdefault("PYTHONUNBUFFERED", "1")
        try:
            completed = subprocess.run(
                [self.executable, *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=environment,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeUnavailableError("The Android runtime did not respond in time.") from error
        result = CommandResult(completed.stdout, completed.stderr, completed.returncode)
        if check and completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
            raise RuntimeUnavailableError(detail)
        return result

    def status(self) -> dict[str, str | bool]:
        result = self._run("status", check=False, timeout=15)
        values: dict[str, str | bool] = {"available": self.available()}
        for line in result.stdout.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                values[key.strip().lower().replace(" ", "_")] = value.strip()
        values["healthy"] = result.returncode == 0
        values["initialized"] = "not initialized" not in (result.stdout + result.stderr).lower()
        from .initialize import STATE, runtime_metadata_complete
        if (STATE / "waydroid.cfg").exists() and not runtime_metadata_complete(STATE):
            values["initialized"] = False
        if is_fp6():
            admission = fp6_kernel_admission()
            values["fp6_kernel_admitted"] = admission.allowed
            values["fp6_kernel_notes_sha256"] = admission.notes_sha256
            values["fp6_gpu_gate"] = admission.reason
            values["fp6_software_presentation"] = (
                _fp6_software_presentation_admitted()
            )
            values["fp6_software_lab_authorized"] = (
                _fp6_software_lab_authorized()
            )
        return values

    def ensure_ready(
        self, multi_window: bool | None = None, timeout: int = 120, detached: bool = False
    ) -> bool:
        """Make Android application services ready and report a cold start.

        A true return value means this call started (or rebound) the graphical
        Waydroid session.  Callers can use that signal for cold-start-only
        presentation work without penalizing ordinary warm launches.
        """
        if multi_window is None:
            multi_window = load_runtime_config().multi_window
        _activate_fp6_software_presentation(timeout=min(timeout, 30))
        _prepare_interactive_windowing(timeout=min(timeout, 30))
        if os.environ.get(FP6_SOFTWARE_PRESENTATION_ENV) == "1":
            multi_window = False
        deadline = time.monotonic() + timeout
        started_session = False
        status = self.status()
        if status.get("initialized") is False:
            # Standard systemd authorization owns this root operation. The
            # service accepts no caller paths and initializes only the locally
            # packaged, verified pair; it never downloads an image at launch.
            try:
                subprocess.run(
                    ["/usr/bin/systemctl", "start", "luma-android-initialize.service"],
                    check=True, capture_output=True, text=True, timeout=210,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise RuntimeUnavailableError(
                    "Android setup could not finish or administrator approval was cancelled. "
                    "Please retry setup; if it still fails, check the Android setup service log."
                ) from error
            status = self.status()
            if status.get("initialized") is False:
                raise RuntimeUnavailableError("Android setup did not become ready. Please retry setup.")
            # Initial setup has its own bounded service budget. Session startup
            # still receives the caller's complete timeout after that step.
            deadline = time.monotonic() + timeout
        current_display = os.environ.get("WAYLAND_DISPLAY", "")
        runtime_display = str(status.get("wayland_display", ""))
        if (
            str(status.get("session", "")).upper() == "RUNNING"
            and current_display
            and runtime_display
            and runtime_display != current_display
        ):
            # A compositor crash or interrupted diagnostic can leave Waydroid
            # alive against a dead display. Rebind through a normal session
            # stop/start instead of presenting a misleading RUNNING state.
            self.stop_session()
            status = self.status()
        if (
            str(status.get("session", "")).upper() == "RUNNING"
            and str(status.get("container", "")).upper() == "FROZEN"
        ):
            # Waydroid may freeze an otherwise warm container independently of
            # Luma's idle policy.  Its app-launch command normally thaws in a
            # later layer, but Luma now verifies boot completion before that
            # dispatch.  Use Waydroid's public lifecycle command first so the
            # readiness probe cannot deadlock against a frozen Binder service.
            # The container CLI is root-only. Use the same public D-Bus
            # method as upstream Waydroid's ordinary app launcher.
            try:
                subprocess.run(
                    ["gdbus", "call", "--system", "--dest", "id.waydro.Container",
                     "--object-path", "/ContainerManager", "--method",
                     "id.waydro.ContainerManager.Unfreeze"],
                    check=True, capture_output=True, text=True, timeout=30,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise RuntimeUnavailableError("Android could not resume its container.") from error
        if str(status.get("session", "")).upper() != "RUNNING":
            started_session = True
            if not self.available():
                raise RuntimeUnavailableError("The Android runtime engine is not installed.")
            session_process: subprocess.Popen[bytes] | None = None
            session_attempts = 0

            def start_detached_session() -> subprocess.Popen[bytes]:
                nonlocal session_attempts
                session_attempts += 1
                log_root = Path(
                    os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))
                ) / "luma-android"
                log_root.mkdir(mode=0o700, parents=True, exist_ok=True)
                with (log_root / "session.log").open("ab", buffering=0) as log:
                    return subprocess.Popen(
                        [self.executable, "session", "start"],
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        close_fds=True,
                        start_new_session=True,
                        env=os.environ.copy(),
                    )

            if detached:
                session_process = start_detached_session()
            else:
                os.execvpe(
                    self.executable,
                    [self.executable, "session", "start"],
                    os.environ.copy(),
                )
            while time.monotonic() < deadline:
                time.sleep(0.5)
                status = self.status()
                if str(status.get("session", "")).upper() == "RUNNING":
                    break
                if (
                    detached
                    and session_process is not None
                    and session_process.poll() is not None
                    and session_attempts < 6
                ):
                    # The public session command can lose a race with a cold
                    # container start and exit cleanly before Binder appears.
                    # Retry only that public handoff, at a bounded cadence.
                    time.sleep(1)
                    session_process = start_detached_session()
            else:
                raise RuntimeUnavailableError("The Android session did not become ready in time.")
        if _host_booted_in_charger_mode():
            # A phone powered on by attached USB may inherit the kernel's
            # charger-only boot mode. Waydroid passes that property into the
            # container, where Android deliberately waits before late-init.
            # Use Android's own supported transition before waiting for its
            # application services.
            self._run_as_android_package_manager(
                ["boot-from-charger-mode"], timeout=30
            )
        self.wait_for_boot_completion(deadline)
        self.wait_for_application_service(deadline)
        runtime_device_class = device_class()
        self._run(
            "prop",
            "set",
            "persist.luma.device_class",
            runtime_device_class,
            timeout=30,
        )
        self._run(
            "prop",
            "set",
            "persist.waydroid.multi_windows",
            "true" if multi_window else "false",
            timeout=30,
        )
        self._run(
            "prop",
            "set",
            "persist.luma.host_window_chrome",
            # Presentation is independent of SurfaceFlinger composition.
            "false" if runtime_device_class == "handheld" else "true",
            timeout=30,
        )
        self.sync_host_appearance()
        # Android already exposes its soft keyboard alongside Waydroid's
        # virtual hardware keyboard on touch-first profiles. Only a desktop
        # needs the privileged secure-setting override that suppresses it.
        # Avoiding that unnecessary privilege request lets the handheld broker
        # retain NoNewPrivileges while preserving the expected mobile IME.
        if runtime_device_class == "desktop":
            # This setting suppresses Android's redundant software keyboard on
            # a hardware-keyboard desktop.  It is presentation policy, not an
            # installation prerequisite: a transient Settings service failure
            # must never reject an otherwise valid package transaction.  The
            # policy is attempted again on every normal readiness pass.
            try:
                self._run_as_android_package_manager(
                    ["input-policy", runtime_device_class], timeout=45
                )
            except RuntimeUnavailableError as error:
                LOGGER.warning(
                    "Android input policy is not ready; installation may continue: %s",
                    error,
                )
        return started_session

    def sync_host_appearance(self, appearance: str | None = None) -> None:
        try:
            treatment = appearance or surface_treatment()
            color_scheme = "dark" if treatment == "dark" else "light"
            self._run("prop", "set", "persist.luma.color_scheme",
                      color_scheme, timeout=5, check=False)
            self._run("prop", "set", "persist.luma.surface_treatment",
                      treatment, timeout=5, check=False)
        except RuntimeUnavailableError as error:
            LOGGER.warning("Android appearance sync deferred: %s", error)

    def wait_for_boot_completion(self, deadline: float) -> None:
        """Wait for Android's public boot-completion property.

        Waydroid can publish its application registry well before Android has
        finished BOOT_COMPLETED processing.  Launch requests made in that gap
        may return success yet never reach ActivityTaskManager.  The public
        Waydroid property API exposes the authoritative readiness boundary and
        does not require shell, ADB, or container privilege.
        """
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                result = self._run(
                    "prop",
                    "get",
                    "sys.boot_completed",
                    timeout=max(1, min(10, int(remaining))),
                    check=False,
                )
                if result.returncode == 0 and result.stdout.strip() == "1":
                    return
            except RuntimeUnavailableError:
                pass
            time.sleep(0.5)
        raise RuntimeUnavailableError("Android did not finish booting in time.")

    def wait_for_application_service(self, deadline: float) -> None:
        """Wait beyond Waydroid's early RUNNING state for Package Manager."""
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                result = self._run(
                    "app",
                    "list",
                    timeout=max(1, min(10, int(remaining))),
                    check=False,
                )
                # A zero exit with an empty listing can still occur while
                # Android is bringing Package Manager online. Every initialized
                # image has system applications, so require one structured app
                # record before exposing install/launch operations.
                if result.returncode == 0 and "packageName:" in result.stdout:
                    return
            except RuntimeUnavailableError:
                pass
            time.sleep(0.5)
        raise RuntimeUnavailableError("Android application services did not become ready in time.")

    def list_apps(self) -> str:
        return self._run("app", "list", timeout=30).stdout

    def applications(self) -> list[AndroidApplication]:
        try:
            listing = self.list_apps()
        except RuntimeUnavailableError:
            return self.applications_from_launchers()
        applications: list[AndroidApplication] = []
        name = ""
        package = ""
        for line in listing.splitlines():
            key, separator, value = line.partition(":")
            if not separator:
                continue
            normalized = key.strip().lower().replace("_", "")
            value = value.strip()
            if normalized == "name":
                if package:
                    applications.append(AndroidApplication(name or package, package))
                    package = ""
                name = value
            elif normalized in {"packagename", "package"}:
                package = value
        if package:
            applications.append(AndroidApplication(name or package, package))
        applications = applications or self.applications_from_launchers()
        installed = self._installed_packages_if_available()
        return [app for app in applications if app.package in installed] if installed is not None else applications

    def applications_from_launchers(self) -> list[AndroidApplication]:
        data_root = Path(
            os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))
        )
        applications: list[AndroidApplication] = []
        seen: set[str] = set()
        for launcher in sorted((data_root / "applications").glob("waydroid.*.desktop")):
            if launcher.is_symlink() or not launcher.is_file():
                continue
            prefix = "waydroid."
            package = launcher.name.removeprefix(prefix).removesuffix(".desktop")
            try:
                _validate_package_name(package)
                lines = launcher.read_text(encoding="utf-8").splitlines()
            except (LumaAndroidError, OSError, UnicodeError):
                continue
            if package in seen:
                continue
            name = next(
                (line.removeprefix("Name=").strip() for line in lines
                 if line.startswith("Name=") and line.removeprefix("Name=").strip()),
                package,
            )
            applications.append(AndroidApplication(name, package))
            seen.add(package)
        return applications

    def install(self, apk: Path) -> None:
        self.install_package([apk])

    def install_package(
        self, payloads: list[Path], member_names: tuple[str, ...] = ()
    ) -> None:
        if not payloads:
            raise RuntimeUnavailableError("The Android package has no installable payloads.")
        if member_names:
            if len(member_names) != len(payloads):
                raise RuntimeUnavailableError("The Android split manifest is inconsistent.")
            selected_names = select_compatible_splits(
                member_names, self._android_device_profile()
            )
            by_name = dict(zip(member_names, payloads, strict=True))
            payloads = [by_name[name] for name in selected_names]

        if len(payloads) == 1:
            package_abis = native_abis_for_apk(payloads[0])
            if package_abis:
                runtime_abis = self._android_device_profile().abis
                if not set(package_abis).intersection(runtime_abis):
                    required = ", ".join(package_abis)
                    available = ", ".join(runtime_abis)
                    raise RuntimeUnavailableError(
                        "This Android application requires a different processor "
                        f"architecture ({required}). This device currently supports "
                        f"{available}; the application was not installed."
                    )

        # Always use Android's native PackageInstaller transaction, including
        # for a single APK. Waydroid's public single-APK helper can return zero
        # even when Package Manager rejects the package; the direct transaction
        # preserves the real exit status and diagnostic. Android's `pm install
        # PATH [SPLIT...]` form accepts one or more payloads and atomically
        # verifies package identity, signatures, splits, and native-library
        # compatibility. Some current images omit the legacy
        # `install-multiple` alias while retaining this session-backed form.
        transaction = self._waydroid_package_transaction(payloads)
        try:
            paths = [f"/data/waydroid_tmp/{path.name}" for path in transaction]
            self._run_as_android_package_manager(
                ["pm", "install", "--user", "0", "-r", *paths],
                timeout=600,
            )
            self.wait_for_application_registry_stable(timeout=45)
            self.synchronize_launchers()
        finally:
            for path in transaction:
                path.unlink(missing_ok=True)

    def _waydroid_package_transaction(self, payloads: list[Path]) -> list[Path]:
        root = Path.home() / ".local/share/waydroid/data/waydroid_tmp"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
        transaction_id = uuid.uuid4().hex
        staged: list[Path] = []
        try:
            for index, payload in enumerate(payloads):
                destination = root / f"luma-{transaction_id}-{index:04d}.apk"
                with payload.open("rb") as source, destination.open("xb") as target:
                    os.chmod(destination, 0o600)
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                    target.flush()
                    os.fsync(target.fileno())
                staged.append(destination)
            return staged
        except Exception:
            for path in staged:
                path.unlink(missing_ok=True)
            raise

    def _run_as_android_package_manager(
        self, arguments: list[str], timeout: int
    ) -> CommandResult:
        helper = Path("/usr/libexec/luma-waydroid-package-session")
        if not helper.is_file():
            raise RuntimeUnavailableError(
                "Split-package support is not installed for this Android runtime."
            )
        try:
            completed = subprocess.run(
                ["/usr/bin/pkexec", str(helper), *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired as error:
            if arguments == ["device-profile"]:
                message = "Android did not finish checking package compatibility in time."
            elif arguments[:1] == ["input-policy"]:
                message = "Android did not finish applying desktop input policy in time."
            else:
                message = "Android did not finish the package transaction in time."
            raise RuntimeUnavailableError(message) from error
        result = CommandResult(completed.stdout, completed.stderr, completed.returncode)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
            raise RuntimeUnavailableError(detail)
        return result

    def _android_device_profile(self) -> AndroidDeviceProfile:
        last_error: Exception | None = None

        # The running Waydroid session already exposes Android properties over
        # its public per-user D-Bus platform interface.  Prefer that interface
        # for this read-only compatibility query: it is the same supported
        # path used by `waydroid prop get`, requires no privilege escalation,
        # and becomes available with the session that `ensure_ready()` just
        # verified.  The narrowly privileged LXC helper remains a fallback for
        # images whose platform interface does not expose a complete profile.
        for attempt in range(3):
            try:
                return self._android_device_profile_from_properties()
            except RuntimeUnavailableError as error:
                last_error = error
                if attempt < 2:
                    time.sleep(0.75)

        for attempt in range(3):
            try:
                result = self._run_as_android_package_manager(
                    ["device-profile"], timeout=60
                )
                payload = json.loads(result.stdout)
                abis = tuple(str(value) for value in payload["abis"] if value)
                density = int(payload["density"])
                locale = str(payload["locale"])
                if not abis or density <= 0 or not locale:
                    raise ValueError("incomplete Android device profile")
                return AndroidDeviceProfile(abis, density, locale)
            except (
                KeyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
                RuntimeUnavailableError,
            ) as error:
                last_error = error
                if attempt < 2:
                    time.sleep(0.75)

        if last_error is not None:
            LOGGER.warning("Android compatibility profile is unavailable: %s", last_error)
        raise RuntimeUnavailableError(
            "Android opened, but its package compatibility service did not "
            "become ready. The package was not modified; please try again."
        ) from last_error

    def _android_device_profile_from_properties(self) -> AndroidDeviceProfile:
        def property_value(key: str) -> str:
            result = self._run("prop", "get", key, timeout=15, check=False)
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip() or key
                raise RuntimeUnavailableError(
                    f"Android did not expose compatibility property {detail}."
                )
            return result.stdout.strip()

        abis = tuple(
            value
            for value in property_value("ro.product.cpu.abilist").split(",")
            if value
        )
        density_text = property_value("ro.sf.lcd_density")
        locale = property_value("persist.sys.locale")
        if not locale:
            locale = property_value("ro.product.locale")
        try:
            density = int(density_text)
        except ValueError as error:
            raise RuntimeUnavailableError(
                "Android did not expose a valid display density."
            ) from error
        if not abis or density <= 0 or not locale:
            raise RuntimeUnavailableError(
                "Android did not expose a complete compatibility profile."
            )
        return AndroidDeviceProfile(abis, density, locale)

    def wait_for_application_registry_stable(
        self, timeout: int = 30, stable_polls: int = 8
    ) -> None:
        """Wait for Waydroid's asynchronous package work to settle.

        The public install API returns before Android completes dex optimization
        and clears its transient package freeze. Waydroid exposes no explicit
        completion signal, so Luma requires a stable public application
        registry over several polls before exposing Open/Launch.
        """
        deadline = time.monotonic() + timeout
        previous: str | None = None
        stable = 0
        while time.monotonic() < deadline:
            result = self._run("app", "list", timeout=10, check=False)
            current = result.stdout if result.returncode == 0 else None
            if current and current == previous:
                stable += 1
                if stable >= stable_polls:
                    return
            else:
                stable = 0
            previous = current
            time.sleep(0.5)
        raise RuntimeUnavailableError("Android did not finish registering the application.")

    def launch(self, package: str) -> None:
        _validate_package_name(package)
        _activate_fp6_software_presentation(timeout=30)
        if os.environ.get(FP6_SOFTWARE_PRESENTATION_ENV) == "1":
            _start_fp6_software_viewer()
        cold_start = self.ensure_ready(detached=True)
        self._run("app", "launch", package, timeout=90)
        if cold_start:
            # Android's Quickstep launcher can become the resumed activity at
            # the tail end of a cold Waydroid boot, after the first public app
            # launch request has already succeeded.  Reassert exactly once
            # after that bounded settling interval.  A warm launch remains a
            # single dispatch, and this never starts a visible Android home
            # surface intentionally.
            time.sleep(COLD_LAUNCH_REASSERT_DELAY_SECONDS)
            self._run("app", "launch", package, timeout=90)

    def remove(self, package: str, *, keep_data: bool = False) -> None:
        _validate_package_name(package)
        self.ensure_ready(detached=True)
        # Normal host close retires the native HWC window and its task before
        # PackageManager removes the launcher or the Android application.
        from .activation import request_existing_restore
        request_existing_restore(package, close=True)
        self._run_as_android_package_manager(
            ["remove-package", package, "keep-data" if keep_data else "delete-data"], timeout=200)
        self.wait_for_application_registry_stable(timeout=30, stable_polls=3)
        if package in self.installed_packages():
            raise RuntimeUnavailableError("Android still reports this application as installed.")
        self.synchronize_launchers()

    def open_permissions(self, package: str) -> None:
        _validate_package_name(package)
        self.ensure_ready(detached=True)
        self._run(
            "app",
            "intent",
            "android.settings.APPLICATION_DETAILS_SETTINGS",
            f"package:{package}",
            timeout=60,
        )

    def stop_session(self) -> None:
        self._run("session", "stop", timeout=60, check=False)
        if is_fp6():
            subprocess.run(
                [
                    "/usr/bin/systemctl", "--user", "stop",
                    "luma-android-fp6-software-compositor.service",
                ],
                check=False,
                timeout=15,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def restart_session(self, multi_window: bool | None = None) -> None:
        self.stop_session()
        self.ensure_ready(multi_window, timeout=180, detached=True)


def _host_booted_in_charger_mode() -> bool:
    command_line_path = Path(
        os.environ.get("LUMA_PROC_CMDLINE", "/proc/cmdline")
    )
    try:
        arguments = command_line_path.read_text(encoding="utf-8").split()
    except (FileNotFoundError, PermissionError, OSError):
        return False
    return "androidboot.mode=charger" in arguments


def _prepare_interactive_windowing(timeout: int = 30) -> None:
    """Admit FP6 presentation through a verified kernel or CPU isolation."""
    del timeout
    if not is_fp6():
        return
    admission = fp6_kernel_admission()
    if admission.allowed:
        return
    if (
        admission.reason == "kernel-not-admitted"
        and _fp6_software_lab_authorized()
        and os.environ.get(FP6_SOFTWARE_PRESENTATION_ENV) == "1"
        and _fp6_software_presentation_admitted()
    ):
        return
    if admission.reason == "gpu-fault-latched":
        detail = "a GPU fault was detected during this boot"
    elif admission.reason == "kernel-not-admitted":
        detail = (
            "this running kernel has not passed Luma's physical Android GPU gate "
            f"({admission.notes_sha256})"
        )
    else:
        detail = "Luma could not verify the running kernel identity"
    raise RuntimeUnavailableError(
        "Android windows are disabled on Fairphone 6 because " + detail + ". "
        "The application remains installed; reboot into an admitted kernel to try again."
    )


def _activate_fp6_software_presentation(timeout: int = 30) -> bool:
    """Start and verify the packaged software-only FP6 containment surface."""
    if not is_fp6():
        return False
    admission = fp6_kernel_admission()
    if admission.allowed:
        return False
    if admission.reason != "kernel-not-admitted":
        return False
    if not _fp6_software_lab_authorized():
        return False
    host_display = os.environ.get(FP6_HOST_DISPLAY_ENV) or os.environ.get(
        "WAYLAND_DISPLAY", ""
    )
    if not host_display or host_display == FP6_SOFTWARE_SOCKET:
        raise RuntimeUnavailableError(
            "Luma could not identify the host display for the isolated Android window."
        )
    required = (
        Path("/usr/libexec/luma-waydroid-fp6-software-compositor"),
        Path("/usr/bin/sdl-freerdp"),
    )
    if any(not path.is_file() for path in required):
        raise RuntimeUnavailableError(
            "The FP6 software Android presentation components are not installed."
        )
    try:
        completed = subprocess.run(
            [
                "/usr/bin/systemctl", "--user", "start",
                "luma-android-fp6-software-compositor.service",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeUnavailableError(
            "The isolated Android compositor could not be started."
        ) from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeUnavailableError(
            "The isolated Android compositor could not be started"
            + (f": {detail}" if detail else ".")
        )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _fp6_software_presentation_admitted():
            os.environ[FP6_HOST_DISPLAY_ENV] = host_display
            os.environ[FP6_SOFTWARE_PRESENTATION_ENV] = "1"
            os.environ["WAYLAND_DISPLAY"] = FP6_SOFTWARE_SOCKET
            return True
        time.sleep(0.1)
    raise RuntimeUnavailableError(
        "The isolated Android compositor failed its process and loopback checks."
    )


def _start_fp6_software_viewer() -> None:
    """Present the isolated compositor as one ordinary maximized Luma window."""
    runtime_root = Path(os.environ.get("XDG_RUNTIME_DIR", "")) / "luma-android"
    state_root = Path(
        os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))
    ) / "luma-android"
    host_display = os.environ.get(FP6_HOST_DISPLAY_ENV, "")
    if not host_display or not runtime_root.is_dir():
        raise RuntimeUnavailableError("The isolated Android viewer has no host display.")
    state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    pid_file = runtime_root / "rdp-viewer.pid"
    try:
        old_pid_text = pid_file.read_text(encoding="ascii").strip()
        if old_pid_text.isdecimal():
            old_pid = int(old_pid_text)
            executable = os.path.realpath(f"/proc/{old_pid}/exe")
            command = Path(f"/proc/{old_pid}/cmdline").read_bytes().split(b"\0")
            trusted_viewer = (
                executable == "/usr/bin/sdl-freerdp"
                and f"/v:127.0.0.1:{FP6_SOFTWARE_PORT}".encode() in command
                and b"/cert:ignore" in command
            )
            current_presentation = (
                f"/size:{FP6_SOFTWARE_HOST_PIXELS}".encode() in command
                and f"/smart-sizing:{FP6_SOFTWARE_CANVAS}".encode() in command
                and b"+multitouch" in command
                and b"+workarea" in command
                and b"/clipboard:direction-to:off,files-to:off" in command
                and b"/gdi:sw" in command
                and b"-gfx" in command
                and b"-decorations" in command
                and b"/f" not in command
                and b"+dynamic-resolution" not in command
            )
            if trusted_viewer and current_presentation:
                # Recreating a full-screen FreeRDP surface provoked an A810
                # host-ring hang after an otherwise stable physical soak.
                # The viewer is a persistent presentation surface: reuse it
                # while Android changes the task shown inside the compositor.
                return
            if trusted_viewer:
                raise RuntimeUnavailableError(
                    "The existing Android viewer predates the current display policy. "
                    "Reboot normally to replace it without recreating the surface in-place."
                )
            if Path(f"/proc/{old_pid}").exists():
                raise RuntimeUnavailableError(
                    "The existing Android viewer has an unexpected process identity."
                )
    except (FileNotFoundError, ProcessLookupError):
        pass
    except (OSError, UnicodeError) as error:
        raise RuntimeUnavailableError(
            "The isolated Android viewer identity could not be verified."
        ) from error
    viewer_environment = _fp6_software_viewer_environment(host_display)
    local_username = pwd.getpwuid(os.getuid()).pw_name
    with (state_root / "fp6-software-viewer.log").open("ab", buffering=0) as log:
        try:
            viewer = subprocess.Popen(
                _fp6_software_viewer_arguments(local_username),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                close_fds=True,
                start_new_session=True,
                env=viewer_environment,
            )
        except OSError as error:
            raise RuntimeUnavailableError(
                "The isolated Android viewer could not be started."
            ) from error
    # SDL FreeRDP exits successfully enough for Popen to return even when a
    # conflicting SDL hint makes its command-line initialization fail.  Do not
    # publish a stale PID and report a successful launch in that case.
    for _attempt in range(40):
        exit_code = viewer.poll()
        if exit_code is not None:
            raise RuntimeUnavailableError(
                "The isolated Android viewer exited during startup "
                f"(status {exit_code})."
            )
        time.sleep(0.05)
    temporary_pid = runtime_root / f".rdp-viewer.pid.{os.getpid()}"
    temporary_pid.write_text(f"{viewer.pid}\n", encoding="ascii")
    os.chmod(temporary_pid, 0o600)
    os.replace(temporary_pid, pid_file)
    threading.Thread(
        target=_monitor_fp6_software_viewer,
        args=(viewer, pid_file),
        name="luma-fp6-android-viewer",
        daemon=True,
    ).start()


def _monitor_fp6_software_viewer(
    viewer: subprocess.Popen[bytes], pid_file: Path
) -> None:
    """Reap the presentation child and fail the hidden runtime closed."""
    exit_code = viewer.wait()
    try:
        if pid_file.read_text(encoding="ascii").strip() == str(viewer.pid):
            pid_file.unlink(missing_ok=True)
    except (OSError, UnicodeError):
        pass
    LOGGER.error("FP6 Android viewer exited with status %s", exit_code)
    for command in (
        [
            "/usr/bin/systemctl", "--user", "stop",
            "luma-android-fp6-software-compositor.service",
        ],
        ["/usr/bin/waydroid", "session", "stop"],
    ):
        try:
            subprocess.run(
                command,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            LOGGER.exception("Could not stop hidden FP6 Android runtime")


def _fp6_software_viewer_environment(host_display: str) -> dict[str, str]:
    """Build the host-display environment expected by SDL FreeRDP."""
    viewer_environment = os.environ.copy()
    viewer_environment["WAYLAND_DISPLAY"] = host_display
    # The FP6 A810 bring-up kernel cannot safely host a second accelerated GL
    # renderer beside GNOME Shell.  SDL FreeRDP otherwise selects OpenGL even
    # though the nested Weston server is Pixman-only.  Force SDL's Wayland SHM
    # software renderer so Android pixels cross the containment boundary as an
    # ordinary CPU-backed Luma window and never open another host GPU context.
    viewer_environment["SDL_VIDEO_DRIVER"] = "wayland"
    viewer_environment["SDL_VIDEODRIVER"] = "wayland"
    viewer_environment["SDL_RENDER_DRIVER"] = "software"
    # FreeRDP 3.30's SDL client sets SDL_HINT_TOUCH_MOUSE_EVENTS=0 itself and
    # treats a same-named environment override as a fatal initialization
    # conflict.  Remove inherited overrides and let the client establish its
    # native RDPEI-only touch policy before it opens the host window.
    viewer_environment.pop("SDL_TOUCH_MOUSE_EVENTS", None)
    viewer_environment.pop("SDL_MOUSE_TOUCH_EVENTS", None)
    viewer_environment.pop(FP6_SOFTWARE_PRESENTATION_ENV, None)
    viewer_environment.pop(FP6_HOST_DISPLAY_ENV, None)
    return viewer_environment


def _fp6_software_viewer_arguments(local_username: str) -> list[str]:
    """Build the scale-correct, touch-native FP6 viewer command.

    SDL FreeRDP converts native finger events to renderer pixel coordinates and
    reverses any local scale before RDPEI delivery. Luma's patched Weston RDP
    backend terminates RDPEI as a real Weston touch device, preserving contact
    identity and touch frames instead of translating a finger into a pointer.
    A borderless normal window lets Luma Shell maximize into its panel-to-dock
    work area. Dynamic server owns a scale-safe 1116x2176 pixel buffer while
    Luma Shell places its logical frame at the exact 496x967 panel-to-dock work
    area. A bounded 558x1088 remote canvas scales by exactly 2x, and SDL
    reverses that same transform for touch. This avoids the deprecated client's
    nonuniform visual/input mapping without asking Pixman and Android to render
    five times as many pixels.
    """
    return [
        "/usr/bin/sdl-freerdp",
        f"/v:127.0.0.1:{FP6_SOFTWARE_PORT}",
        "/cert:ignore",
        f"/u:{local_username}",
        "/p:",
        "+workarea",
        f"/size:{FP6_SOFTWARE_HOST_PIXELS}",
        f"/smart-sizing:{FP6_SOFTWARE_CANVAS}",
        "/gdi:sw",
        "-gfx",
        "+multitouch",
        # Android content is an application surface, not a second desktop.
        # Weston 15 and FreeRDP 3.30 require cliprdr to remain negotiated for
        # connection finalization, so retain the protocol channel while
        # disabling every host clipboard and file-transfer direction.
        "/clipboard:direction-to:off,files-to:off",
        "-decorations",
        "-grab-mouse",
        "-mouse-motion",
        "-toggle-fullscreen",
        "/t:Luma Android",
        "/wm-class:org.projectluma.Android",
        "/network:lan",
    ]


def _validate_package_name(package: str) -> None:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._")
    if not package or "." not in package or any(character not in allowed for character in package):
        raise LumaAndroidError("Invalid Android package name.")


_DENSITY_SPLITS = {
    "ldpi": 120,
    "mdpi": 160,
    "tvdpi": 213,
    "hdpi": 240,
    "xhdpi": 320,
    "xxhdpi": 480,
    "xxxhdpi": 640,
}
_KNOWN_ABIS = {
    "armeabi",
    "armeabi_v7a",
    "arm64_v8a",
    "x86",
    "x86_64",
    "mips",
    "mips64",
}


def select_compatible_splits(
    member_names: tuple[str, ...], profile: AndroidDeviceProfile
) -> tuple[str, ...]:
    if not member_names:
        raise RuntimeUnavailableError("The Android package has no declared splits.")

    base = member_names[0]
    normalized_abis = tuple(abi.replace("-", "_").lower() for abi in profile.abis)
    primary_locale = re.split(r"[-_]", profile.locale, maxsplit=1)[0].lower()
    abi_members: dict[str, str] = {}
    density_members: dict[int, str] = {}
    locale_members: dict[str, str] = {}
    feature_abi_members: dict[str, dict[str, str]] = {}
    feature_density_members: dict[str, dict[int, str]] = {}
    feature_locale_members: dict[str, dict[str, str]] = {}
    required = [base]

    for name in member_names[1:]:
        stem = Path(name).stem.lower()
        feature_match = re.fullmatch(r"(?P<feature>.+)\.config\.(?P<qualifier>[^.]+)", stem)
        if feature_match:
            feature = feature_match.group("feature")
            qualifier = feature_match.group("qualifier")
            if qualifier in _KNOWN_ABIS:
                feature_abi_members.setdefault(feature, {})[qualifier] = name
            elif qualifier in _DENSITY_SPLITS:
                feature_density_members.setdefault(feature, {})[
                    _DENSITY_SPLITS[qualifier]
                ] = name
            elif re.fullmatch(r"[a-z]{2,3}(?:[-_]r?[a-z]{2})?", qualifier):
                locale = qualifier.split("-", 1)[0].split("_", 1)[0]
                feature_locale_members.setdefault(feature, {})[locale] = name
            else:
                # Unknown feature configuration qualifiers are not safe to
                # guess. Android may require exactly one mutually exclusive
                # resource for the physical device.
                raise RuntimeUnavailableError(
                    f"The Android feature split {name} uses an unsupported "
                    "device qualifier."
                )
            continue

        qualifier = stem.removeprefix("split_config.").removeprefix("config.")
        if qualifier in _KNOWN_ABIS:
            abi_members[qualifier] = name
        elif qualifier in _DENSITY_SPLITS:
            density_members[_DENSITY_SPLITS[qualifier]] = name
        elif re.fullmatch(r"[a-z]{2,3}(?:[-_]r?[a-z]{2})?", qualifier):
            locale_members[qualifier.split("-", 1)[0].split("_", 1)[0]] = name
        else:
            # Feature/master splits do not represent mutually exclusive device
            # resources. Preserve them; PackageInstaller remains authoritative.
            required.append(name)

    selected_abi = next((abi for abi in normalized_abis if abi in abi_members), None)
    if abi_members and selected_abi is None:
        available = ", ".join(sorted(abi_members))
        supported = ", ".join(normalized_abis)
        raise RuntimeUnavailableError(
            "This Android bundle has no base split for the device processor "
            f"architecture (bundle: {available}; device: {supported})."
        )
    if selected_abi is not None:
        required.append(abi_members[selected_abi])
    if density_members:
        density = min(density_members, key=lambda value: abs(value - profile.density))
        required.append(density_members[density])
    if primary_locale in locale_members:
        required.append(locale_members[primary_locale])
    elif "en" in locale_members:
        required.append(locale_members["en"])

    feature_names = dict.fromkeys(
        (
            *feature_abi_members,
            *feature_density_members,
            *feature_locale_members,
        )
    )
    for feature in feature_names:
        feature_abis = feature_abi_members.get(feature, {})
        selected_feature_abi = next(
            (abi for abi in normalized_abis if abi in feature_abis), None
        )
        if feature_abis and selected_feature_abi is None:
            available = ", ".join(sorted(feature_abis))
            supported = ", ".join(normalized_abis)
            raise RuntimeUnavailableError(
                f"The Android feature {feature} has no split for the device "
                f"processor architecture (bundle: {available}; device: {supported})."
            )
        if selected_feature_abi is not None:
            required.append(feature_abis[selected_feature_abi])

        feature_densities = feature_density_members.get(feature, {})
        if feature_densities:
            density = min(
                feature_densities,
                key=lambda value: abs(value - profile.density),
            )
            required.append(feature_densities[density])

        feature_locales = feature_locale_members.get(feature, {})
        if primary_locale in feature_locales:
            required.append(feature_locales[primary_locale])
        elif "en" in feature_locales:
            required.append(feature_locales["en"])

    return tuple(required)
