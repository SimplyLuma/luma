"""Runtime presentation and input capabilities for converged Luma apps."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
from typing import Mapping


class PresentationMode(str, Enum):
    WINDOWED = "windowed"
    FULLSCREEN = "fullscreen-mobile"


class InputMode(str, Enum):
    POINTER = "pointer"
    TOUCH = "touch"


@dataclass(frozen=True)
class AppContext:
    presentation: PresentationMode
    input_mode: InputMode
    device_class: str

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "AppContext":
        env = os.environ if environment is None else environment
        device_class = env.get("LUMA_DEVICE_CLASS", "").strip().lower()
        if device_class not in {"desktop", "tablet", "handheld"}:
            marker = Path("/etc/luma-device-class")
            try:
                device_class = marker.read_text(encoding="utf-8").strip().lower()
            except (FileNotFoundError, OSError, UnicodeError):
                device_class = "desktop"
        if device_class not in {"desktop", "tablet", "handheld"}:
            device_class = "desktop"

        raw_presentation = env.get("LUMA_PRESENTATION_MODE", "").strip().lower()
        presentation = (
            PresentationMode.FULLSCREEN
            if raw_presentation == PresentationMode.FULLSCREEN.value
            or (not raw_presentation and device_class == "handheld")
            else PresentationMode.WINDOWED
        )
        raw_input = env.get("LUMA_INPUT_MODE", "").strip().lower()
        input_mode = (
            InputMode.TOUCH
            if raw_input == InputMode.TOUCH.value
            or (not raw_input and device_class in {"tablet", "handheld"})
            else InputMode.POINTER
        )
        return cls(presentation, input_mode, device_class)
