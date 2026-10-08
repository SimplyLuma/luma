# SPDX-License-Identifier: Apache-2.0
"""v70's Camera session, entirely in memory and independent of GTK/devices.

Fixture media are read from the supplied fixture directory. Captures add
references to those images; they never encode, save, index or launch anything.
Malformed fixtures fail closed instead of falling back to a real camera.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path

from .camera_backend import CameraDevice
from .camera_state import CameraState, SessionShot


@dataclass(frozen=True)
class FixtureCamera:
    id: str
    name: str
    subtitle: str
    raw: bool
    zooms: tuple[float, ...]
    image: Path
    phone: bool = False
    mirror: bool = False


class CameraFixture:
    def __init__(self, path: Path, *, overrides: dict | None = None) -> None:
        path = path.resolve(strict=True)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.cameras = tuple(self._camera(row, path.parent) for row in data["cameras"])
        if not self.cameras or len({c.id for c in self.cameras}) != len(self.cameras):
            raise ValueError("Camera fixture needs distinct camera IDs")
        self.modes = tuple(tuple(row) for row in data["modes"])
        self.formats = deepcopy(data["formats"])
        self.dials = deepcopy(data["dials"])
        self.values = deepcopy(data["initial"])
        if overrides:
            unknown = overrides.keys() - self.values.keys()
            if unknown:
                raise ValueError(f"Unknown Camera fixture state: {sorted(unknown)}")
            self.values.update(deepcopy(overrides))
        self._validate()
        self.state = CameraState(
            source=next(i for i, c in enumerate(self.cameras) if c.id == self.values["cam"]),
            mode=self.values["mode"], zoom=float(self.values["zoom"]),
            timer=self.values["timer"], grid=self.values["grid"],
            keep_raw=self.values["fmt"] == "raw", mirror=self.camera.mirror,
        )
        self.shots: list[dict] = []

    @staticmethod
    def _camera(row: dict, directory: Path) -> FixtureCamera:
        image = (directory / row["image"]).resolve(strict=True)
        if not image.is_relative_to(directory) or not image.is_file():
            raise ValueError("Camera fixture media must stay inside its directory")
        zooms = tuple(float(z) for z in row["zooms"])
        if not zooms or any(not math.isfinite(z) or z <= 0 for z in zooms):
            raise ValueError("Camera fixture zooms must be positive and finite")
        return FixtureCamera(row["id"], row["name"], row["subtitle"], row["raw"],
                             zooms, image, row.get("phone", False), row.get("mirror", False))

    def _validate(self) -> None:
        v = self.values
        if v["cam"] not in {c.id for c in self.cameras}:
            raise ValueError("Unknown fixture camera")
        if v["mode"] not in {key for key, _label in self.modes}:
            raise ValueError("Unknown fixture mode")
        if v["flash"] not in ("auto", "on", "off") or v["timer"] not in (0, 3, 10):
            raise ValueError("Invalid fixture flash or timer")
        if v["fmt"] not in self.formats or v["aspect"] not in ("4:3", "3:2", "16:9", "1:1"):
            raise ValueError("Invalid fixture format or shape")
        if v["zoom"] not in self.camera.zooms:
            raise ValueError("Zoom is not offered by this fixture camera")
        for key, _label, choices in self.dials:
            if v[key] not in choices:
                raise ValueError(f"Invalid fixture {key}")
        if not math.isfinite(float(v["ev"])) or not -2 <= float(v["ev"]) <= 2:
            raise ValueError("Fixture exposure must be between -2 and 2")

    @property
    def camera(self) -> FixtureCamera:
        return next(c for c in self.cameras if c.id == self.values["cam"])

    @property
    def devices(self) -> tuple[CameraDevice, ...]:
        # These identifiers must never be passed to a native pipeline.
        return tuple(CameraDevice(f"fixture:{c.id}", c.name,
                                  "front" if c.mirror else "external",
                                  still_width=8000 if c.phone else 4000,
                                  still_height=6000 if c.phone else 3000,
                                  desktop_uvc=True) for c in self.cameras)

    def select_camera(self, index: int) -> None:
        camera = self.cameras[index]
        self.values.update(cam=camera.id, zoom=1, pop=None)
        self.state.select_source(index)
        self.state.mirror = camera.mirror
        if not camera.raw:
            self.values["pro"] = False
            if self.values["fmt"] == "raw":
                self.values["fmt"] = "jpeg"
        self.state.keep_raw = self.values["fmt"] == "raw"

    def capture(self) -> SessionShot | None:
        """Simulate v70's shutter without opening a store, file or encoder."""
        video = self.state.mode in ("video", "time")
        if video and not self.state.recording:
            self.state.recording = True
            self.state.elapsed = 0
            return None
        duration = self.state.elapsed if video else None
        self.state.recording = False
        camera = self.camera
        device = self.devices[self.state.source]
        shot = SessionShot(camera.image, device.still_width, device.still_height, duration=duration)
        label = ("Document, PDF" if self.state.mode == "scan" else "Portrait"
                 if self.state.mode == "portrait" else self.formats[self.values["fmt"]][0])
        if video:
            label = f"{'Time-lapse' if self.state.mode == 'time' else 'Video'}, {format_time(duration)}"
        self.shots.insert(0, {"image": camera.image, "label": label, "duration": duration})
        self.state.roll.insert(0, shot)
        return shot

    def require_live_io(self) -> None:
        raise RuntimeError("Camera fixture mode cannot access real devices or data")


def format_time(seconds: float) -> str:
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def fixture_from_environment() -> CameraFixture | None:
    path = os.environ.get("LUMA_CAMERA_FIXTURE")
    if path is None:
        return None
    if not path:
        raise ValueError("LUMA_CAMERA_FIXTURE must name a fixture file")
    overrides = json.loads(os.environ.get("LUMA_CAMERA_FIXTURE_STATE", "{}"))
    return CameraFixture(Path(path), overrides=overrides)
