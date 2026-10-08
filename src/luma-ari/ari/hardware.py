# SPDX-License-Identifier: Apache-2.0
"""What this machine can run, and which tier that makes it (brief §6.3)."""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

TIERS = ("minimal", "standard", "plus", "workstation")


@dataclass(frozen=True)
class Hardware:
    cpu: str
    cores: int
    ram_gb: float
    gpu_vendor: str       # "intel", "amd", "nvidia" or ""
    gpu_name: str
    vram_gb: float        # dedicated memory; 0 for integrated graphics
    backend: str          # "vulkan" or "cpu"
    tier: str

    def summary(self) -> str:
        gpu = f"{self.gpu_name} ({self.backend})" if self.gpu_name else "no usable GPU"
        return f"{self.cpu}, {self.cores} cores, {self.ram_gb:.0f} GB RAM, {gpu}"

    def as_dict(self) -> dict:
        return asdict(self)


def _cpu() -> tuple[str, int]:
    name = platform.processor() or "CPU"
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                name = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    name = re.sub(r"\s+", " ", name.replace("(R)", "").replace("(TM)", "")).strip()
    return name, os.cpu_count() or 1


def _ram_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return 0.0


def _gpus() -> list[tuple[str, str, float]]:
    """(vendor, name, dedicated GB) for each DRM render device."""
    vendors = {"0x8086": "intel", "0x1002": "amd", "0x10de": "nvidia"}
    found = []
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]")):
        device = card / "device"
        try:
            vendor = vendors.get((device / "vendor").read_text().strip(), "")
        except OSError:
            continue
        if not vendor:
            continue
        vram = 0.0
        for name in ("mem_info_vram_total",):
            try:
                vram = int((device / name).read_text()) / 1024 ** 3
            except (OSError, ValueError):
                pass
        label = vendor.upper() if vendor != "intel" else "Intel graphics"
        found.append((vendor, label, vram))
    return found


def _vulkan_device() -> str:
    tool = shutil.which("vulkaninfo")
    if not tool:
        return ""
    try:
        out = subprocess.run([tool, "--summary"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    names = re.findall(r"deviceName\s*=\s*(.+)", out)
    names = [n.strip() for n in names if "llvmpipe" not in n.lower()]
    return names[0] if names else ""


def tier_for(ram_gb: float, vram_gb: float, has_gpu: bool) -> str:
    if vram_gb >= 20 and ram_gb >= 48:
        return "workstation"
    if vram_gb >= 8:
        return "plus"
    if ram_gb >= 14 and has_gpu:
        return "standard"
    if ram_gb >= 14:
        return "standard"
    return "minimal"


def detect() -> Hardware:
    cpu, cores = _cpu()
    ram = _ram_gb()
    gpus = _gpus()
    vulkan = _vulkan_device()
    vendor, name, vram = max(gpus, key=lambda g: g[2]) if gpus else ("", "", 0.0)
    if vulkan:
        name = re.sub(r"\s*\(.*?\)\s*$", "", vulkan).replace("(R)", "").strip()
    backend = "vulkan" if (vulkan or gpus) else "cpu"
    return Hardware(cpu, cores, round(ram, 1), vendor, name, round(vram, 1), backend,
                    tier_for(ram, vram, bool(gpus)))


def cached(path: Path) -> Hardware:
    """Detection runs once per boot of the daemon; the benchmark is stored separately."""
    hardware = detect()
    try:
        path.write_text(json.dumps(hardware.as_dict(), indent=1))
    except OSError:
        pass
    return hardware
