# SPDX-License-Identifier: Apache-2.0

"""Conservative Camera capability discovery.

V4L2 codec endpoints such as Qualcomm Iris are deliberately not accepted as
cameras. The mobile backend is ready only when Linux exposes a media-controller
graph and a libcamera/PipeWire camera node can be enumerated.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class CameraCapability:
    available: bool
    reason: str
    camera_count: int = 0


@dataclass(frozen=True)
class CameraDevice:
    identifier: str
    label: str
    location: str
    rotation: int = 0
    preview_width: int = 1920
    preview_height: int = 1080
    viewfinder_width: int = 1920
    viewfinder_height: int = 1080
    viewfinder_framerate: int = 30
    still_width: int = 1920
    still_height: int = 1080
    focus_position: float | None = None
    preview_saturation: float | None = None
    desktop_uvc: bool = False
    stream_format: str = "video/x-raw"


_CAMERA_LINE = re.compile(r"^\s*\d+:\s+(.+?)\s+\((/.+)\)\s*$")

# libcamera does not promise discovery order. Bind the FP6's product-facing
# names and default cycle to the physical media-graph identities so a reboot
# cannot relabel Main as Wide or make the selfie sensor the startup camera.
_FP6_CAMERA_PRESENTATION = {
    "/base/soc@0/cci@ac15000/i2c-bus@0/camera@1a": ("Main", 0),
    "/base/soc@0/cci@ac15000/i2c-bus@1/camera@36": ("Wide", 1),
    "/base/soc@0/cci@ac16000/i2c-bus@1/camera@3d": ("Front", 2),
}

# OV13B10's smaller 1364x768 mode caps exposure at 8.245 ms.  This request
# selects the 2080x1170 mode (16.553 ms ceiling). Keep this FP6 hardware
# decision below the shared application surface. The shared application then
# presents a screen-oriented 1080p preview instead of a full-resolution sensor
# stream; still capture is a separate quality path.
_PREVIEW_SOURCE_SIZES = {
    # Preserve OV13B10's native processed mode. Scaling it down to 1080p and
    # then back up to fill the tall FP6 panel compounded visible stair-stepping.
    "/base/soc@0/cci@ac15000/i2c-bus@1/camera@36": (2080, 1170),
}

# The FP6 Adreno A810 currently has an unresolved GMU recovery defect. Keep
# sensor acquisition at 30 fps for AE/AWB and the Wide low-light mode, but
# present a smaller 15 fps texture stream to GNOME Shell. This reduces both
# texture bandwidth and compositor submissions while leaving saved stills and
# sensor statistics untouched. External/desktop cameras retain their native
# preview contract.
_VIEWFINDER_PROFILES = {
    "/base/soc@0/cci@ac15000/i2c-bus@1/camera@36": (1600, 900, 15),
    "/base/soc@0/cci@ac15000/i2c-bus@0/camera@1a": (1600, 900, 15),
    "/base/soc@0/cci@ac16000/i2c-bus@1/camera@3d": (1600, 900, 15),
}

_PREVIEW_SATURATION = {
    # The stock-derived Wide CCM is a bring-up baseline; this conservative
    # reduction limits its observed green/over-vivid excursions.
    "/base/soc@0/cci@ac15000/i2c-bus@1/camera@36": 0.85,
}

# A bounded 4.9 MP 4:3 output selects each sensor's native still mode while
# keeping each CPU-ISP ABGR buffer near 20 MB. Native processed maxima require
# roughly 39-52 MB per buffer before conversion/JPEG and are not an acceptable
# interactive path on the current software ISP. Rear focus positions are the
# repeatable physical full-sweep optima used by the bounded startup AF tuning.
_STILL_CAPTURE_PROFILES: dict[str, tuple[int, int, float | None]] = {
    "/base/soc@0/cci@ac15000/i2c-bus@1/camera@36": (2560, 1920, 29.0),
    "/base/soc@0/cci@ac15000/i2c-bus@0/camera@1a": (2560, 1920, 35.0),
    "/base/soc@0/cci@ac16000/i2c-bus@1/camera@3d": (2560, 1920, None),
}


def _preview_source_size(identifier: str) -> tuple[int, int]:
    return _PREVIEW_SOURCE_SIZES.get(identifier, (1920, 1080))


def _viewfinder_profile(
    identifier: str,
    source_width: int = 1920,
    source_height: int = 1080,
) -> tuple[int, int, int]:
    return _VIEWFINDER_PROFILES.get(
        identifier,
        (source_width, source_height, 30),
    )


def _still_capture_profile(identifier: str) -> tuple[int, int, float | None]:
    return _STILL_CAPTURE_PROFILES.get(identifier, (1920, 1080, None))


def _preview_saturation(identifier: str) -> float | None:
    return _PREVIEW_SATURATION.get(identifier)


def still_focus_position(
    preview_position: object,
    fallback_position: float | None,
) -> float | None:
    """Return a valid preview AF result for the still pipeline when available."""

    try:
        position = float(preview_position)
    except (TypeError, ValueError):
        return fallback_position
    if not math.isfinite(position) or not 0.0 <= position <= 100.0:
        return fallback_position
    return position


def image_rotation_from_mount(
    mount_rotation: int,
    display_rotation: int = 0,
    front_facing: bool = False,
) -> int:
    """Return Android-compatible clockwise image rotation in degrees."""

    sensor = mount_rotation % 360
    display = display_rotation % 360
    if front_facing:
        rotation = (sensor + display) % 360
    else:
        rotation = (sensor - display + 360) % 360
    # Physical FP6 acceptance found every mount-derived preview exactly
    # upside down. Keep this correction shared by preview, stills, and taps.
    return (rotation + 180) % 360


def video_direction_for_transform(
    rotation: int,
    mirror_horizontal: bool = False,
) -> int:
    """Return GstVideoOrientationMethod for a rotation and output mirror."""

    normalized = rotation % 360
    if mirror_horizontal:
        return {0: 4, 90: 6, 180: 5, 270: 7}.get(normalized, 4)
    return {0: 0, 90: 1, 180: 2, 270: 3}.get(normalized, 0)


def sensor_point_from_display(
    x: float,
    y: float,
    width: float,
    height: float,
    mount_rotation: int,
    display_rotation: int = 0,
    front_facing: bool = False,
    mirror_horizontal: bool = False,
) -> tuple[int, int]:
    """Map a displayed touch point to normalized pre-rotation sensor space."""

    if width <= 0 or height <= 0:
        return (500, 500)
    display_x = min(1.0, max(0.0, x / width))
    display_y = min(1.0, max(0.0, y / height))
    if mirror_horizontal:
        display_x = 1.0 - display_x
    rotation = image_rotation_from_mount(
        mount_rotation,
        display_rotation,
        front_facing,
    )
    if rotation == 90:
        sensor_x, sensor_y = display_y, 1.0 - display_x
    elif rotation == 180:
        sensor_x, sensor_y = 1.0 - display_x, 1.0 - display_y
    elif rotation == 270:
        sensor_x, sensor_y = 1.0 - display_y, display_x
    else:
        sensor_x, sensor_y = display_x, display_y
    return (round(sensor_x * 1000), round(sensor_y * 1000))


def new_photo_path(
    environment: dict[str, str] | None = None,
    now: datetime | None = None,
    *, suffix: str = ".jpg",
) -> Path:
    """Choose a currently unused Camera path; publication must claim it atomically."""

    if suffix not in (".jpg", ".webm"):
        raise ValueError("Unsupported camera file type")
    env = os.environ if environment is None else environment
    from .user_directories import photos_directory
    pictures = photos_directory(env)
    directory = pictures / "Camera"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    timestamp = (now or datetime.now().astimezone()).strftime("%Y%m%d-%H%M%S")
    candidate = directory / f"Luma-{timestamp}{suffix}"
    sequence = 2
    while candidate.exists():
        candidate = directory / f"Luma-{timestamp}-{sequence}{suffix}"
        sequence += 1
    return candidate


def publish_capture(temporary: Path, requested: Path) -> Path:
    """Publish complete media without replacing a file created during capture.

    The temporary file is in the destination directory. A hard link exposes
    the complete inode atomically and fails if any destination entry exists,
    including a dangling symlink. Concurrent captures retry their own names.
    """
    candidate = requested
    sequence = 2
    while True:
        try:
            os.link(temporary, candidate)
            break
        except FileExistsError:
            candidate = requested.with_name(f"{requested.stem}-{sequence}{requested.suffix}")
            sequence += 1
    temporary.unlink()
    directory = os.open(candidate.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return candidate


def _list_desktop_cameras() -> tuple[CameraDevice, ...]:
    """Discover UVC cameras through the installed libcamera GStreamer provider."""
    try:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
    except (ImportError, ValueError):
        return ()
    Gst.init(None)
    monitor = Gst.DeviceMonitor.new()
    monitor.add_filter("Video/Source", None)
    devices = []
    try:
        if not monitor.start():
            return ()
        for candidate in monitor.get_devices():
            props = candidate.get_properties()
            if props is None or props.get_string("api.libcamera.PipelineHandler") != "uvcvideo":
                continue
            caps = candidate.get_caps()
            modes = []
            for index in range(caps.get_size()):
                structure = caps.get_structure(index)
                if structure.get_name() not in ("video/x-raw", "image/jpeg"):
                    continue
                if structure.get_name() == "image/jpeg" and Gst.ElementFactory.find("jpegdec") is None:
                    continue
                # Infrared authentication sensors are not photographic cameras.
                if structure.get_string("format") in ("GRAY8", "GRAY16_LE", "GRAY16_BE"):
                    continue
                width = structure.get_value("width")
                height = structure.get_value("height")
                if isinstance(width, int) and isinstance(height, int):
                    modes.append((width, height, structure.get_name()))
            if not modes:
                continue
            width, height, stream_format = max(modes, key=lambda mode: mode[0] * mode[1])
            devices.append(CameraDevice(
                identifier=candidate.get_display_name(),
                label=props.get_string("api.libcamera.Model") or "Camera",
                location="front" if props.get_string("api.libcamera.Location") == "CameraLocationFront" else "external",
                preview_width=width, preview_height=height,
                viewfinder_width=width, viewfinder_height=height,
                still_width=width, still_height=height,
                desktop_uvc=True, stream_format=stream_format,
            ))
    finally:
        monitor.stop()
    return tuple(devices)


def list_camera_devices() -> tuple[CameraDevice, ...]:
    """Return libcamera devices without confusing codec V4L2 nodes for cameras."""

    executable = shutil.which("cam")
    if executable is None:
        return _list_desktop_cameras()
    try:
        completed = subprocess.run(
            [executable, "--list"],
            check=True,
            capture_output=True,
            text=True,
            timeout=4,
        )
    except subprocess.SubprocessError:
        return _list_desktop_cameras()

    rotations = _pipewire_camera_rotations()
    devices: list[CameraDevice] = []
    for line in completed.stdout.splitlines():
        match = _CAMERA_LINE.match(line)
        if match is None:
            continue
        name, identifier = match.groups()
        normalized = name.casefold()
        location = "front" if "front" in normalized else "back" if "back" in normalized else "external"
        ordinal = sum(device.location == location for device in devices) + 1
        presentation = _FP6_CAMERA_PRESENTATION.get(identifier)
        if presentation is not None:
            label = presentation[0]
        elif location == "front":
            label = "Front"
        elif location == "back":
            label = "Wide" if ordinal == 1 else "Main" if ordinal == 2 else f"Back {ordinal}"
        else:
            label = name
        preview_width, preview_height = _preview_source_size(identifier)
        viewfinder_width, viewfinder_height, viewfinder_framerate = (
            _viewfinder_profile(identifier, preview_width, preview_height)
        )
        still_width, still_height, focus_position = _still_capture_profile(identifier)
        preview_saturation = _preview_saturation(identifier)
        devices.append(
            CameraDevice(
                identifier=identifier,
                label=label,
                location=location,
                rotation=rotations.get(identifier, 0),
                preview_width=preview_width,
                preview_height=preview_height,
                viewfinder_width=viewfinder_width,
                viewfinder_height=viewfinder_height,
                viewfinder_framerate=viewfinder_framerate,
                still_width=still_width,
                still_height=still_height,
                focus_position=focus_position,
                preview_saturation=preview_saturation,
            )
        )
    if not devices:
        return _list_desktop_cameras()
    return tuple(
        sorted(
            devices,
            key=lambda device: _FP6_CAMERA_PRESENTATION.get(
                device.identifier,
                (device.label, 10 if device.location == "back" else 20),
            )[1],
        )
    )


def _pipewire_camera_rotations() -> dict[str, int]:
    executable = shutil.which("wpctl")
    if executable is None:
        return {}
    try:
        status = subprocess.run(
            [executable, "status"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
    except subprocess.SubprocessError:
        return {}

    video = status.partition("Video")[2]
    sources = video.partition("Sources:")[2].partition("Filters:")[0]
    node_ids = re.findall(r"(?:\*\s*)?(\d+)\.\s+Built-in (?:Back|Front) Camera", sources)
    rotations: dict[str, int] = {}
    for node_id in node_ids:
        try:
            details = subprocess.run(
                [executable, "inspect", node_id],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout
        except subprocess.SubprocessError:
            continue
        path_match = re.search(r'api\.libcamera\.path = "([^"]+)"', details)
        rotation_match = re.search(r'api\.libcamera\.rotation = "(\d+)"', details)
        if path_match is not None and rotation_match is not None:
            rotations[path_match.group(1)] = int(rotation_match.group(1)) % 360
    return rotations


def _pipewire_camera_count() -> int:
    executable = shutil.which("pw-dump")
    if executable is None:
        return _wpctl_camera_count()
    try:
        completed = subprocess.run(
            [executable],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        objects = json.loads(completed.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return _wpctl_camera_count()
    count = 0
    for item in objects if isinstance(objects, list) else ():
        props = item.get("info", {}).get("props", {}) if isinstance(item, dict) else {}
        media_class = str(props.get("media.class", ""))
        if media_class.startswith("Video/Source") or "Camera" in media_class:
            count += 1
    return count or _wpctl_camera_count()


def _wpctl_camera_count() -> int:
    executable = shutil.which("wpctl")
    if executable is None:
        return 0
    try:
        completed = subprocess.run(
            [executable, "status"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except subprocess.SubprocessError:
        return 0
    video = completed.stdout.partition("Video")[2]
    sources = video.partition("Sources:")[2].partition("Filters:")[0]
    return len(re.findall(r"Built-in (?:Back|Front) Camera", sources))


def inspect_camera_capability(
    media_root: Path = Path("/dev"),
) -> CameraCapability:
    media_nodes = tuple(media_root.glob("media*"))
    if not media_nodes:
        return CameraCapability(
            False,
            "No camera media-controller is available on this kernel.",
        )
    if shutil.which("cam") is None and shutil.which("libcamera-hello") is None:
        return CameraCapability(False, "The libcamera runtime is not installed.")
    camera_count = _pipewire_camera_count()
    if camera_count == 0:
        return CameraCapability(False, "No libcamera camera is visible through PipeWire.")
    return CameraCapability(True, "Camera ready", camera_count)
