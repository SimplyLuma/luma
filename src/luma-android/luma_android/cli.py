from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from .apk import inspect_android_package, stage_android_package, staged_package_root
from .config import device_class, load_runtime_config
from .engine import WaydroidEngine
from .errors import LumaAndroidError
from .kernel import fp6_kernel_admission, fp6_software_presentation_active, is_fp6
from .receipts import write_install_receipt


def _application_name(package: str) -> str:
    data_root = Path(
        os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))
    )
    launcher = data_root / "applications" / f"waydroid.{package}.desktop"
    try:
        for line in launcher.read_text(encoding="utf-8").splitlines():
            if line.startswith("Name=") and line.removeprefix("Name=").strip():
                return line.removeprefix("Name=").strip()
    except (FileNotFoundError, PermissionError, OSError, UnicodeError):
        pass
    return package


def _report_launch_failure(package: str, error: LumaAndroidError) -> None:
    name = _application_name(package)
    message = str(error)

    # Desktop launch stderr is not reliably retained by every shell. Record a
    # metadata-only event in the user journal so a failed icon tap is
    # diagnosable without collecting application content or Android data.
    logger = shutil.which("logger")
    if logger:
        try:
            subprocess.run(
                [
                    logger,
                    "--tag=luma-android",
                    "--priority=user.warning",
                    "--",
                    f"launch failed: package={package}; reason={message}",
                ],
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    notifier = shutil.which("notify-send")
    if notifier:
        try:
            subprocess.run(
                [
                    notifier,
                    "--app-name=Android Applications",
                    "--urgency=critical",
                    "--expire-time=15000",
                    "--icon=dialog-warning-symbolic",
                    f"{name} couldn’t open",
                    message,
                ],
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            pass


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="luma-android")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("doctor")
    inspect = commands.add_parser("inspect-apk")
    inspect.add_argument("apk", type=Path)
    install = commands.add_parser("install")
    install.add_argument("apk", type=Path)
    install.add_argument("--confirmed", action="store_true")
    launch = commands.add_parser("launch")
    launch.add_argument("package")
    permissions = commands.add_parser("permissions")
    permissions.add_argument("package")
    remove = commands.add_parser("remove")
    remove.add_argument("package")
    commands.add_parser("pause")
    commands.add_parser("resume")
    commands.add_parser("restart")
    commands.add_parser("list")
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    config = load_runtime_config()
    engine = WaydroidEngine(config.engine)
    try:
        if arguments.command == "status":
            print(json.dumps({"device_class": device_class(), **engine.status()}, sort_keys=True))
        elif arguments.command == "doctor":
            binder_devices = all(
                path.exists() and stat.S_ISCHR(path.stat().st_mode)
                for path in (
                    Path("/dev/binder"),
                    Path("/dev/hwbinder"),
                    Path("/dev/vndbinder"),
                )
            )
            checks = {
                "device_class": device_class(),
                "engine": config.engine,
                "engine_available": engine.available(),
                "wayland_display": bool(__import__("os").environ.get("WAYLAND_DISPLAY")),
                "binderfs_supported": Path("/sys/fs/binder").exists()
                or Path("/dev/binderfs").exists()
                or binder_devices
                or Path("/proc/filesystems").read_text(encoding="utf-8").find("binder") >= 0,
                "dri_render_node": bool(list(Path("/dev/dri").glob("renderD*")))
                if Path("/dev/dri").is_dir()
                else False,
            }
            if is_fp6():
                admission = fp6_kernel_admission()
                checks["fp6_kernel_admitted"] = admission.allowed
                checks["fp6_gpu_gate"] = admission.reason
                checks["fp6_kernel_notes_sha256"] = admission.notes_sha256
                checks["fp6_software_presentation"] = fp6_software_presentation_active()
            print(json.dumps(checks, sort_keys=True))
            return 0 if (
                checks["engine_available"]
                and checks["binderfs_supported"]
                and (
                    checks.get("fp6_kernel_admitted", True)
                    or checks.get("fp6_software_presentation", False)
                )
            ) else 1
        elif arguments.command == "inspect-apk":
            print(inspect_android_package(arguments.apk, config.apk_max_bytes).to_json())
        elif arguments.command == "install":
            if not arguments.confirmed:
                raise LumaAndroidError("Installation requires explicit confirmation.")
            inspection = inspect_android_package(arguments.apk, config.apk_max_bytes)
            staged_payloads = stage_android_package(arguments.apk, inspection)
            try:
                engine.ensure_ready(config.multi_window, detached=True)
                engine.install_package(staged_payloads, inspection.apk_entries)
                write_install_receipt(inspection, "installed")
            finally:
                shutil.rmtree(staged_package_root(staged_payloads), ignore_errors=True)
        elif arguments.command == "launch":
            engine.launch(arguments.package)
            from .activation import request_existing_restore
            request_existing_restore(arguments.package)
        elif arguments.command == "permissions":
            engine.open_permissions(arguments.package)
        elif arguments.command == "remove":
            engine.remove(arguments.package)
        elif arguments.command == "pause":
            engine.stop_session()
        elif arguments.command == "resume":
            engine.ensure_ready(config.multi_window, detached=True)
        elif arguments.command == "restart":
            engine.restart_session(config.multi_window)
        elif arguments.command == "list":
            print(engine.list_apps(), end="")
        return 0
    except LumaAndroidError as error:
        if arguments.command in {"launch", "permissions"}:
            _report_launch_failure(arguments.package, error)
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
