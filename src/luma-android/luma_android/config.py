from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONFIG = Path("/etc/luma/android.conf")
DEFAULT_PROFILE_ROOT = Path("/usr/share/luma/android/profiles")


@dataclass(frozen=True)
class RuntimeConfig:
    engine: str = "waydroid"
    multi_window: bool = True
    start_when_apps_installed: bool = True
    idle_freeze: bool = False
    apk_max_bytes: int = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class DeviceProfile:
    keep_warm: bool = True


def load_runtime_config(path: Path | None = None) -> RuntimeConfig:
    parser = configparser.ConfigParser()
    parser.read(path or Path(os.environ.get("LUMA_ANDROID_CONFIG", DEFAULT_CONFIG)))
    section = parser["runtime"] if parser.has_section("runtime") else {}
    return RuntimeConfig(
        engine=section.get("engine", "waydroid"),
        multi_window=str(section.get("multi_window", "true")).lower() == "true",
        start_when_apps_installed=str(
            section.get("start_when_apps_installed", "true")
        ).lower()
        == "true",
        idle_freeze=str(section.get("idle_freeze", "false")).lower() == "true",
        apk_max_bytes=int(section.get("apk_max_bytes", 2 * 1024 * 1024 * 1024)),
    )


def device_class() -> str:
    explicit = os.environ.get("LUMA_DEVICE_CLASS", "").strip().lower()
    if explicit in {"desktop", "tablet", "handheld"}:
        return explicit
    marker = Path("/etc/luma-device-class")
    if marker.is_file():
        value = marker.read_text(encoding="utf-8").strip().lower()
        if value in {"desktop", "tablet", "handheld"}:
            return value
    mobile_release = Path("/etc/luma-mobile-release")
    if mobile_release.is_file():
        return "handheld"
    return "desktop"


def load_device_profile(root: Path | None = None) -> DeviceProfile:
    profile = (root or Path(os.environ.get(
        "LUMA_ANDROID_PROFILE_ROOT", DEFAULT_PROFILE_ROOT
    ))) / f"{device_class()}.conf"
    values: dict[str, str] = {}
    try:
        lines = profile.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return DeviceProfile()
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    keep_warm = values.get("KEEP_WARM", "true").lower()
    if keep_warm not in {"true", "false"}:
        raise ValueError(f"{profile}: KEEP_WARM must be true or false")
    return DeviceProfile(keep_warm=keep_warm == "true")
