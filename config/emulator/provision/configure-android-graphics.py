#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Factory configuration for the emulator's unaccelerated virtual GPU.

Uses Waydroid's supported VM software-rendering properties. This is not used
by desktop hardware or FP6 composition. Re-run `waydroid init -f` afterward.
https://docs.waydro.id/faq/get-waydroid-to-work-through-a-vm
"""
import configparser
import hashlib
import os
import sys
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

if os.geteuid() != 0:
    raise SystemExit("Android graphics provisioning requires root")
if subprocess.run(["systemctl", "is-active", "--quiet", "waydroid-container.service"]).returncode == 0:
    raise SystemExit("Stop the Android container before graphics provisioning")

path = Path("/var/lib/waydroid/waydroid.cfg")
config = configparser.ConfigParser(interpolation=None)
with path.open() as source:
    config.read_file(source)
if not config.has_section("properties"):
    config.add_section("properties")
config["properties"]["ro.hardware.gralloc"] = "default"
config["properties"]["ro.hardware.egl"] = "swiftshader"
# Explicit emulator software-video profile. Native VM video is accepted;
# independent handle-validation review and clean-image release gates remain.
# The pinned Android 13 arm64_only vendor advertises the legacy OMX HAL,
# but contains no OMX service binary. Its running Codec2 APEX is the decoder
# implementation. Declare the absent HAL accurately so enumeration cannot wait
# forever, and override Waydroid's legacy Codec2-disabled default. This is a
# static vendor configuration through Waydroid's supported overlay directory;
# no service or login-time replay is added. Other architectures are untouched.
if "--software-video-candidate" in sys.argv and config.get("waydroid", "arch", fallback="") == "arm64_only":
    vendor = Path(config.get("waydroid", "images_path")) / "vendor.img"
    def inspect_vendor(command):
        return subprocess.run(
            ["debugfs", "-R", command, str(vendor)], check=True,
            capture_output=True, text=True, timeout=10,
        ).stdout
    manifest = ET.fromstring(inspect_vendor("cat /etc/vintf/manifest.xml"))
    names = {hal.findtext("name") for hal in manifest.findall("hal")}
    binaries = inspect_vendor("ls -l /bin/hw")
    if "android.hardware.media.omx" not in names or "media.omx" in binaries:
        raise SystemExit("Unrecognized arm64-only codec layout; review the new image")
    allocator = subprocess.run(
        ["debugfs", "-R", "cat /lib64/hw/gralloc.default.so", str(vendor)],
        check=True, capture_output=True, timeout=10,
    ).stdout
    admitted_allocators = {
        # Original standalone software-YUV candidate.
        "2106130c991a6c4c111de31f79154c3d1a58200bb2b70bab85171bb1563c0ef3",
        # Coherent 2026-09-07 ARM App Kit image; actual HAL RGB/YUV and invalid
        # dimensions/handle probe passed before userdata/image replacement.
        "45d6824e13a14bdc1e9bfbff55928117a3053e8ef8a368349ae538e9d027c871",
    }
    if hashlib.sha256(allocator).hexdigest() not in admitted_allocators:
        raise SystemExit("Install the admitted software YUV allocator image before enabling Codec2")
    fragment = Path("/var/lib/waydroid/overlay/vendor/etc/vintf/manifest/luma-codec2.xml")
    fragment.parent.mkdir(parents=True, exist_ok=True)
    fragment.write_text(
        '<manifest version="1.0" type="device">\n'
        '  <hal format="hidl" override="true">\n'
        '    <name>android.hardware.media.omx</name>\n'
        '    <transport>hwbinder</transport>\n'
        '  </hal>\n'
        '</manifest>\n'
    )
    os.chmod(fragment, 0o644)
    subprocess.run(["restorecon", "-RF", str(fragment.parent)], check=True)
    config["properties"]["debug.stagefright.ccodec"] = "4"
    # Software gralloc carries planar video; Waydroid's default SHM multiwindow
    # copier supports RGB only. Let SurfaceFlinger composite to an RGB target.
    # This upstream mode retains app windows, snapshotting inactive Android apps.
    config["properties"]["persist.waydroid.multi_windows"] = "false"
    config["properties"]["persist.waydroid.use_subsurface"] = "false"
    # All runtime entry points must keep the same composition mode, including
    # warm launches and session recovery through the Luma broker.
    runtime_path = Path("/etc/luma/android.conf")
    runtime = configparser.ConfigParser(interpolation=None)
    runtime.read(runtime_path)
    if not runtime.has_section("runtime"):
        runtime.add_section("runtime")
    runtime["runtime"]["multi_window"] = "false"
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_fd, runtime_temporary = tempfile.mkstemp(dir=runtime_path.parent, prefix=".android-")
    try:
        with os.fdopen(runtime_fd, "w") as target:
            runtime.write(target)
            target.flush()
            os.fsync(target.fileno())
            os.fchmod(target.fileno(), 0o644)
        os.replace(runtime_temporary, runtime_path)
    finally:
        if os.path.exists(runtime_temporary):
            os.unlink(runtime_temporary)
    subprocess.run(["restorecon", str(runtime_path)], check=True)


fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".luma-graphics-")
try:
    with os.fdopen(fd, "w") as target:
        config.write(target)
        target.flush()
        os.fsync(target.fileno())
        os.fchmod(target.fileno(), 0o644)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
subprocess.run(["restorecon", str(path)], check=True)
