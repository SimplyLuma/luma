# SPDX-License-Identifier: Apache-2.0
"""Prepare the packaged Android image pair through Waydroid's own initializer.

Called only by the system service. No URLs, caller-selected paths, downloads,
forced resets, or application permissions enter this boundary.
"""
from __future__ import annotations

import hashlib
import ctypes
import fcntl
import json
import os
from pathlib import Path
import platform
import stat
import subprocess

from .kernel import is_fp6


IMAGES = Path("/usr/share/waydroid-extra/images")
STATE = Path("/var/lib/waydroid")


def _virtio_has_3d(node: Path) -> bool:
    """Read Linux's virtio GPU capability, without creating a GPU context.

    DRM_IOCTL_VIRTGPU_GETPARAM / VIRTGPU_PARAM_3D_FEATURES from the Linux
    uapi drm/virtgpu_drm.h. Both supported host architectures use this ioctl
    layout: two aligned u64 fields, the second pointing to a u64 result.
    A render node alone does not prove a virtio device supports 3D.
    """
    descriptor = os.open(node, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISCHR(metadata.st_mode) or metadata.st_uid != 0
                or os.major(metadata.st_rdev) != 226 or os.minor(metadata.st_rdev) < 128):
            raise RuntimeError("Android graphics needs a genuine root-owned DRM render device.")
        value = ctypes.c_uint64(0)
        request = (ctypes.c_uint64 * 2)(1, ctypes.addressof(value))
        fcntl.ioctl(descriptor, 0xC0106443, request)
        return value.value != 0
    finally:
        os.close(descriptor)


def _software_graphics_required(dri: Path = Path("/dev/dri"),
                                sys_drm: Path = Path("/sys/class/drm")) -> bool:
    # Match Waydroid 1.6.3 getDriNode ordering and its unsupported nvidia
    # exclusion. Other real GPU drivers retain upstream's normal selection.
    for node in sorted(dri.glob("renderD*")):
        metadata = node.lstat()
        if (not stat.S_ISCHR(metadata.st_mode) or metadata.st_uid != 0
                or os.major(metadata.st_rdev) != 226 or os.minor(metadata.st_rdev) < 128):
            raise RuntimeError("Android graphics needs genuine root-owned DRM render devices.")
        fields = dict(line.split("=", 1) for line in
                      (sys_drm / node.name / "device/uevent").read_text().splitlines()
                      if "=" in line)
        driver = fields.get("DRIVER", "")
        if not driver:
            raise RuntimeError("Android graphics could not identify the render-device driver.")
        if driver == "nvidia":
            continue
        if driver == "virtio-pci":
            # DRM's device link can point at the transport rather than the
            # actual virtio GPU. Read that transport's sole virtio child;
            # a PCI render node by itself does not establish acceleration.
            children = sorted((sys_drm / node.name / "device").glob("virtio[0-9]*"))
            if len(children) != 1:
                raise RuntimeError("Android graphics could not identify the virtio render-device driver.")
            child = children[0]
            metadata = child.lstat()
            if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0
                    or metadata.st_mode & 0o022):
                raise RuntimeError("Android graphics needs a genuine root-owned virtio device.")
            fields = dict(line.split("=", 1) for line in
                          (child / "uevent").read_text().splitlines() if "=" in line)
            driver = fields.get("DRIVER", "")
            if driver != "virtio_gpu" or not fields.get("MODALIAS", "").startswith("virtio:d00000010v"):
                raise RuntimeError("Android graphics could not identify the virtio render-device driver.")
        return driver == "virtio_gpu" and not _virtio_has_3d(node)
    return True


def _seed_initial_graphics(state: Path) -> None:
    # An existing cfg, including interrupted setup or administrator choices,
    # is never rewritten. FP6 retains its separate hardware admission owner.
    config = state / "waydroid.cfg"
    if os.path.lexists(config):
        with _open_packaged(config):
            pass
        return
    if is_fp6() or not _software_graphics_required():
        return
    _trusted_directories(state.parent)
    state.mkdir(mode=0o755, exist_ok=True)
    _trusted_directories(state)
    # Supported Waydroid properties, consumed by its ordinary initial init.
    # https://docs.waydro.id/faq/get-waydroid-to-work-through-a-vm
    descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
    with os.fdopen(descriptor, "w") as target:
        target.write("[waydroid]\n\n[properties]\n"
                     "ro.hardware.gralloc=default\nro.hardware.egl=swiftshader\n")
        target.flush()
        os.fsync(target.fileno())
    directory = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _trusted_directories(path: Path, *, require_root: bool = True) -> None:
    # Waydroid reopens these paths after verification. Protect every ancestor,
    # not just the image leaf, against replacement during that handoff.
    for directory in (path, *path.parents):
        metadata = directory.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or (require_root and metadata.st_uid != 0)
                or metadata.st_mode & 0o022):
            raise RuntimeError("Android support directories must be root-owned and read-only.")


def runtime_complete(state: Path = STATE) -> bool:
    """Authenticate existing state before the privileged initializer uses it."""
    return _runtime_complete(state, require_root=True)


def runtime_metadata_complete(state: Path = STATE) -> bool:
    """Read-only readiness for the desktop; this grants no setup privilege.

    The protected user service maps host root to an unmapped UID. It cannot
    authenticate host ownership. The system initializer always uses the
    separate, strict runtime_complete check before accepting existing state.
    """
    return _runtime_complete(state, require_root=False)


def _runtime_complete(state: Path, *, require_root: bool) -> bool:
    # Upstream's cfg+rootfs predicate is set before its last initialization
    # steps. These outputs must also exist before we accept that handoff.
    required = ("waydroid.cfg", "lxc/waydroid/config", "lxc/waydroid/waydroid.seccomp",
                "lxc/waydroid/config_nodes", "lxc/waydroid/config_session",
                "waydroid_base.prop")
    try:
        _trusted_directories(state / "lxc/waydroid", require_root=require_root)
        if (state / "rootfs").is_symlink() or not (state / "rootfs").is_dir():
            return False
        for name in required:
            with _open_packaged(state / name, require_root=require_root) as stream:
                if name in ("waydroid.cfg", "lxc/waydroid/config", "waydroid_base.prop"):
                    if not stream.read(1):
                        return False
    except (OSError, RuntimeError):
        return False
    return True


def _empty_overlay(path: Path, depth: int = 0) -> bool:
    # Upstream creates empty system/vendor directories before final config.
    # Files, links, foreign ownership or unusually deep trees are changes.
    metadata = path.lstat()
    if (depth > 16 or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0 or metadata.st_mode & 0o022):
        return False
    return all(_empty_overlay(child, depth + 1) for child in path.iterdir())


def _open_packaged(path: Path, *, require_root: bool = True):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    metadata = os.fstat(descriptor)
    if (not stat.S_ISREG(metadata.st_mode) or (require_root and metadata.st_uid != 0)
            or metadata.st_mode & 0o022):
        os.close(descriptor)
        raise RuntimeError("Android support files must be regular, root-owned and read-only.")
    return os.fdopen(descriptor, "rb")


def verify_images(root: Path = IMAGES, machine: str | None = None) -> None:
    _trusted_directories(root)
    with _open_packaged(root / "luma-images.json") as stream:
        manifest = json.load(stream)
    if not isinstance(manifest, dict):
        raise ValueError("Android support files have an invalid manifest.")
    machine = platform.machine() if machine is None else machine
    if manifest.get("architecture") != machine:
        raise RuntimeError("The Android support images do not match this computer’s architecture.")
    records = manifest.get("images")
    if not isinstance(records, dict) or set(records) != {"system.img", "vendor.img"}:
        raise RuntimeError("Android needs a complete, verified system and vendor image pair.")
    for name, record in records.items():
        with _open_packaged(root / name) as stream:
            metadata = os.fstat(stream.fileno())
            if metadata.st_size != record["size"]:
                raise RuntimeError(f"The packaged Android {name} has an unexpected size.")
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != record["sha256"]:
                raise RuntimeError(f"The packaged Android {name} failed verification.")


def initialize() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("Android setup must run through its system service.")
    # An existing Android installation and its user data stay owned by
    # Waydroid. This is initial setup, never a repair/reset or image migration.
    if runtime_complete(STATE):
        return
    # Upstream prefers /etc over /usr. Refuse to accidentally initialize a
    # different pair instead of the admitted package verified below.
    if os.path.lexists("/etc/waydroid-extra/images"):
        raise RuntimeError("A separately configured Android image pair needs administrator review.")
    if STATE.exists():
        _trusted_directories(STATE)
        # Ordinary upstream init removes overlay directories. Retry only a
        # partial initial setup with no overlay entries and no active runtime;
        # never erase an existing application's changes as a side effect.
        for name in ("overlay_rw", "overlay_work"):
            overlay = STATE / name
            if os.path.lexists(overlay) and not _empty_overlay(overlay):
                raise RuntimeError("Incomplete Android setup has existing changes and needs administrator review.")
        active = subprocess.run(
            ["/usr/bin/systemctl", "show", "--property=ActiveState", "--value", "waydroid-container.service"],
            check=False, capture_output=True, text=True, timeout=15,
        )
        if active.returncode != 0 or active.stdout.strip() != "inactive":
            raise RuntimeError("Android setup cannot retry while its container state is active or unknown.")
        if (STATE / "lxc/waydroid/config").exists():
            container = subprocess.run(
                ["/usr/bin/lxc-info", "-P", str(STATE / "lxc"), "-n", "waydroid", "-s", "-H"],
                check=False, capture_output=True, text=True, timeout=15,
            )
            if container.returncode != 0 or container.stdout.strip() != "STOPPED":
                raise RuntimeError("Android setup cannot retry while its LXC container is active or unknown.")
    verify_images()
    _seed_initial_graphics(STATE)
    subprocess.run(["/usr/bin/waydroid", "init"], check=True, timeout=180)
    if not runtime_complete(STATE):
        raise RuntimeError("Android setup did not produce a usable runtime configuration.")


def main() -> int:
    try:
        initialize()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError,
            subprocess.SubprocessError) as error:
        print(f"Android setup failed: {error}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
