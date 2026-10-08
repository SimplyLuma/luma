from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import RelayConfig, shared_cache_root
from .errors import RelayError
from . import fex
from .package import INSTALLER_WORDS, WindowsPackage, host_supports, inspect_windows_package
from .registry import (
    applications_directory,
    capsule_root,
    read_manifest,
    windows_apps_root,
    write_manifest,
)
from .sandbox import sandbox_command
from .wine_integration import (
    WINE_RUNTIME_ROOTS,
    native_notification_files_available,
    sync_relay_bridge,
)


COMPONENTS = {
    "dotnet48": "Microsoft .NET Framework 4.8",
    "vcrun2022": "Microsoft Visual C++ 2015–2022 runtime",
    "corefonts": "Microsoft Core Fonts",
    "d3dcompiler_47": "Microsoft Direct3D compiler 47",
    "msxml6": "Microsoft XML Core Services 6",
}
IGNORED_PROGRAMS = {
    "unins000.exe", "uninstall.exe", "uninstaller.exe", "update.exe",
    "updater.exe", "setup.exe", "installer.exe", "crashhandler.exe",
}


def _display_name(filename: str) -> str:
    words = re.sub(r"[-_]+", " ", Path(filename).stem).strip()
    words = re.sub(r"(?i)\b(setup|installer|install|portable)\b", "", words)
    words = re.sub(r"\s+", " ", words).strip()
    return words or "Windows Application"


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (value[:48] or "windows-app").strip("-")


def _app_id(package: WindowsPackage) -> str:
    return f"{_slug(Path(package.filename).stem)}-{package.sha256[:12]}"


def _desktop_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", " ").replace(";", "\\;")


class WineEngine:
    def __init__(self, config: RelayConfig) -> None:
        self.config = config

    @property
    def available(self) -> bool:
        return bool((fex.available() or shutil.which("wine")) and shutil.which("bwrap"))

    def version(self) -> str:
        if fex.available():
            return "Wine via FEX · runtime version unavailable"
        wine = shutil.which("wine")
        if not wine:
            return "not installed"
        result = subprocess.run(
            (wine, "--version"), check=False, capture_output=True, text=True, timeout=10
        )
        return (result.stdout or result.stderr).strip() or "unknown"

    def doctor(self) -> dict[str, Any]:
        host = platform.machine().lower()
        dxvk_roots = WINE_RUNTIME_ROOTS
        dxvk_available = Path("/usr/share/dxvk").exists() or bool(
            shutil.which("setup_dxvk.sh")
        )
        if not dxvk_available:
            dxvk_available = any(
                root.is_dir() and next(root.rglob("dxvk-d3d11.dll"), None) is not None
                for root in dxvk_roots
            )
        return {
            "backend": "Wine / FEX (ARM preview)" if fex.available() else "Wine",
            "wine": self.version(),
            "wine_available": bool(fex.available() or shutil.which("wine")),
            "translated_windows_processors": ["x86", "x86_64"] if fex.available() else [],
            "sandbox_available": bool(shutil.which("bwrap")),
            "winetricks_available": bool(shutil.which("winetricks")),
            "dxvk_available": dxvk_available,
            "native_notifications_available": native_notification_files_available(),
            "ntsync_device": Path("/dev/ntsync").exists(),
            "host_processor": host,
            "native_windows_processors": (
                ["x86", "x86_64"] if host == "x86_64" else ["arm64"] if host in {"aarch64", "arm64"} else []
            ),
            "idle_service": False,
        }

    def _prepare_capsule(self, app_id: str) -> Path:
        root = capsule_root(app_id)
        for name in ("prefix", "payload", "cache", "run", "logs"):
            (root / name).mkdir(mode=0o700, parents=True, exist_ok=True)
        return root

    def _run(
        self,
        root: Path,
        command: list[str],
        *,
        source: Path | None = None,
        network: bool = True,
        grants: list[dict[str, Any]] | None = None,
        wait: bool,
    ) -> subprocess.CompletedProcess[str] | subprocess.Popen[str]:
        if not self.available:
            raise RelayError("The Relay Windows runtime is not installed on this device.")
        argv, environment = sandbox_command(
            root, command, source=source, network=network, grants=grants
        )
        if wait:
            return subprocess.run(
                argv,
                env=environment,
                check=False,
                text=True,
                capture_output=True,
            )
        return subprocess.Popen(
            argv,
            env=environment,
            text=True,
            start_new_session=True,
            close_fds=True,
        )

    def _initialize(self, root: Path, network: bool) -> None:
        marker = root / "prefix" / ".luma-initialized"
        if marker.exists():
            sync_relay_bridge(root)
            return
        result = self._run(
            root, ["/usr/bin/wineboot", "--init"], network=network, wait=True
        )
        assert isinstance(result, subprocess.CompletedProcess)
        if result.returncode != 0:
            raise RelayError("Relay could not create the private Windows environment.")
        marker.write_text("schema=1\n", encoding="utf-8")
        marker.chmod(0o600)
        sync_relay_bridge(root)

    def _stage(self, package: WindowsPackage, root: Path) -> Path:
        source = Path(package.path)
        destination = root / "payload" / package.filename
        digest = hashlib.sha256()
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            input_fd = os.open(source, flags)
        except OSError as error:
            raise RelayError(f"The selected package changed before installation: {error}.") from error
        try:
            temporary_fd, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.", suffix=".partial", dir=destination.parent
            )
            temporary = Path(temporary_name)
            os.fchmod(temporary_fd, 0o600)
            with os.fdopen(temporary_fd, "wb") as output:
                while True:
                    block = os.read(input_fd, 1024 * 1024)
                    if not block:
                        break
                    digest.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
        except Exception:
            if "temporary" in locals():
                temporary.unlink(missing_ok=True)
            raise
        finally:
            os.close(input_fd)
        if digest.hexdigest() != package.sha256:
            temporary.unlink(missing_ok=True)
            raise RelayError("The selected package changed after it was inspected.")
        os.replace(temporary, destination)
        return destination

    @staticmethod
    def _windows_path(root: Path, executable: Path) -> str:
        relative = executable.relative_to(root / "prefix" / "drive_c")
        return "C:\\" + "\\".join(relative.parts)

    def _find_program(self, root: Path, package: WindowsPackage) -> tuple[str, str]:
        if package.suggested_action == "open":
            return f"/relay/payload/{package.filename}", _display_name(package.filename)
        candidates: list[tuple[int, Path]] = []
        wanted = set(_slug(Path(package.filename).stem).split("-")) - set(INSTALLER_WORDS)
        for base_name in ("Program Files", "Program Files (x86)"):
            base = root / "prefix" / "drive_c" / base_name
            if not base.is_dir():
                continue
            for executable in base.rglob("*.exe"):
                lower = executable.name.lower()
                if lower in IGNORED_PROGRAMS or any(
                    word in lower for word in ("unins", "uninstall", "updater", "crash")
                ):
                    continue
                words = set(_slug(executable.stem).split("-"))
                score = 20 * len(words & wanted) - len(executable.relative_to(base).parts)
                if lower.endswith("launcher.exe"):
                    score += 3
                candidates.append((score, executable))
        if not candidates:
            raise RelayError(
                "The Windows installer finished, but Relay could not identify its main application. "
                "The environment has been kept so it can be completed from Relay Settings."
            )
        executable = max(candidates, key=lambda item: (item[0], -len(str(item[1]))))[1]
        return self._windows_path(root, executable), _display_name(executable.name)

    def _write_launcher(self, manifest: dict[str, Any]) -> Path:
        app_id = manifest["app_id"]
        desktop_id = f"org.projectluma.Relay.Windows.{app_id}.desktop"
        path = applications_directory() / desktop_id
        content = "\n".join(
            (
                "[Desktop Entry]",
                "Type=Application",
                "Version=1.5",
                f"Name={_desktop_escape(manifest['name'])}",
                f"Comment=Windows application managed by Luma Relay",
                f"Exec=/usr/bin/luma-relay launch {app_id}",
                f"Icon={manifest.get('icon', 'application-x-executable')}",
                "Terminal=false",
                "StartupNotify=true",
                "Categories=Utility;",
                f"X-Luma-Relay-AppID={app_id}",
                "X-Luma-Compatibility=Windows",
                "",
            )
        )
        temporary = path.with_name(f".{path.name}.partial")
        temporary.write_text(content, encoding="utf-8")
        temporary.chmod(0o644)
        os.replace(temporary, path)
        return path

    def install(self, package: WindowsPackage, *, launch_portable: bool = True) -> dict[str, Any]:
        supported, explanation = host_supports(package, platform.machine().lower(), fex_available=fex.available())
        if not supported:
            raise RelayError(explanation)
        app_id = _app_id(package)
        root = self._prepare_capsule(app_id)
        staged = self._stage(package, root)
        network = self.config.network_default
        self._initialize(root, network)
        if package.suggested_action == "open":
            executable, name = self._find_program(root, package)
        else:
            if package.package_format == "msi":
                command = ["/usr/bin/wine", "msiexec", "/i", "Z:\\source\\input"]
            else:
                command = [
                    "/usr/bin/wine", "start", "/wait", "Z:\\source\\input"
                ]
            result = self._run(root, command, source=staged, network=network, wait=True)
            assert isinstance(result, subprocess.CompletedProcess)
            if result.returncode != 0:
                log = root / "logs" / "last-install.log"
                log.write_text((result.stdout or "") + (result.stderr or ""), encoding="utf-8")
                raise RelayError(
                    "The Windows installer did not complete. Relay kept its private environment and diagnostic log."
                )
            executable, name = self._find_program(root, package)
        manifest = {
            "app_id": app_id,
            "name": name,
            "platform": "windows",
            "engine": "wine",
            "engine_version": self.version(),
            "processor": package.processor,
            "source_name": package.filename,
            "source_sha256": package.sha256,
            "source_authenticode_present": package.authenticode_present,
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "executable": executable,
            "network": network,
            "notifications": self.config.notifications_default,
            "grants": [],
            "components": ["wine-mono"] if package.dotnet_metadata_present else [],
            "icon": "application-x-executable",
        }
        write_manifest(app_id, manifest)
        self._write_launcher(manifest)
        if package.suggested_action == "open" and launch_portable:
            self.launch(app_id)
        return manifest

    def launch(self, app_id: str) -> subprocess.Popen[str]:
        read_manifest(app_id)
        session = shutil.which("luma-relay-session")
        command = (
            [session, app_id]
            if session
            else [sys.executable, "-m", "luma_relay.session", app_id]
        )
        return subprocess.Popen(
            command,
            text=True,
            start_new_session=True,
            close_fds=True,
        )

    def set_network(self, app_id: str, enabled: bool) -> dict[str, Any]:
        manifest = read_manifest(app_id)
        manifest["network"] = enabled
        write_manifest(app_id, manifest)
        return manifest

    def set_notifications(self, app_id: str, enabled: bool) -> dict[str, Any]:
        manifest = read_manifest(app_id)
        manifest["notifications"] = enabled
        write_manifest(app_id, manifest)
        return manifest

    def grant(self, app_id: str, path: Path, mode: str) -> dict[str, Any]:
        if mode not in {"read-only", "read-write"}:
            raise RelayError("Folder access must be read-only or read-write.")
        path = path.expanduser().absolute()
        if not path.is_dir() or path.is_symlink():
            raise RelayError("Relay can grant access only to an existing local folder.")
        path = path.resolve(strict=True)
        manifest = read_manifest(app_id)
        grants = [item for item in manifest.get("grants", []) if item.get("path") != str(path)]
        grants.append({"path": str(path), "mode": mode})
        if len(grants) > 22:
            raise RelayError("This application has reached Relay's shared-folder limit.")
        self._sync_grant_drives(app_id, grants)
        manifest["grants"] = grants
        write_manifest(app_id, manifest)
        return manifest

    def revoke(self, app_id: str, path: Path) -> dict[str, Any]:
        requested = str(path.expanduser().absolute().resolve(strict=False))
        manifest = read_manifest(app_id)
        grants = [
            item for item in manifest.get("grants", [])
            if item.get("path") != requested
        ]
        self._sync_grant_drives(app_id, grants)
        manifest["grants"] = grants
        write_manifest(app_id, manifest)
        return manifest

    @staticmethod
    def _sync_grant_drives(app_id: str, grants: list[dict[str, Any]]) -> None:
        dosdevices = capsule_root(app_id) / "prefix" / "dosdevices"
        dosdevices.mkdir(mode=0o700, parents=True, exist_ok=True)
        for letter in "defghijklmnopqrstuvwxy":
            candidate = dosdevices / f"{letter}:"
            if candidate.is_symlink() and str(candidate.readlink()).startswith("/shared/"):
                candidate.unlink()
        for index, grant in enumerate(grants, start=1):
            letter = "defghijklmnopqrstuvwxy"[index - 1]
            grant["drive"] = f"{letter.upper()}:"
            (dosdevices / f"{letter}:").symlink_to(f"/shared/{index}")

    def install_component(self, app_id: str, component: str) -> None:
        if component not in COMPONENTS:
            raise RelayError("That Windows component is not in Relay's reviewed allow-list.")
        winetricks = shutil.which("winetricks")
        if not winetricks:
            raise RelayError("Relay's optional Windows component service is not installed.")
        manifest = read_manifest(app_id)
        root = capsule_root(app_id)
        cache = shared_cache_root()
        command, environment = sandbox_command(
            root,
            ["/usr/bin/winetricks", "-q", component],
            network=True,
            grants=[{"path": str(cache), "mode": "read-write"}],
        )
        environment["WINETRICKS_CACHE"] = "/shared/1"
        result = subprocess.run(command, env=environment, check=False)
        if result.returncode != 0:
            raise RelayError(f"{COMPONENTS[component]} could not be installed.")
        components = list(manifest.get("components", []))
        if component not in components:
            components.append(component)
        manifest["components"] = components
        write_manifest(app_id, manifest)

    def remove(self, app_id: str, *, keep_data: bool = False) -> str:
        manifest = read_manifest(app_id)
        root = capsule_root(app_id)
        expected_parent = windows_apps_root().resolve()
        if root.is_symlink() or root.parent.resolve() != expected_parent:
            raise RelayError("Relay refused to remove an application outside its registry.")
        retained = expected_parent.parent / "retained"
        target = retained / app_id
        if keep_data:
            if retained.is_symlink() or target.exists() or target.is_symlink():
                raise RelayError("A retained environment already exists or its storage is unsafe; nothing was overwritten.")
            retained.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            result = self._run(
                root,
                ["/usr/bin/wineserver", "-k"],
                network=False,
                grants=list(manifest.get("grants", [])),
                wait=True,
            )
            assert isinstance(result, subprocess.CompletedProcess)
        except (RelayError, OSError):
            # Removal must remain possible when Wine itself is damaged or absent.
            pass
        launcher = applications_directory() / f"org.projectluma.Relay.Windows.{app_id}.desktop"
        # The owning runtime stops Wine before preserving the environment.
        # Rename on the same filesystem avoids a live, partial copy and retains
        # unknown Windows settings alongside the application's private files.
        if keep_data:
            root.rename(target)
        else:
            shutil.rmtree(root)
        launcher.unlink(missing_ok=True)
        return str(manifest.get("name", "Windows Application"))
