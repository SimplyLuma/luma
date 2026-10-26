"""Runtime presentation and input capabilities for converged Luma apps."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
from typing import Mapping

_session_context = None


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
        if environment is None and _session_context is not None:
            return _session_context
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
            or (not raw_presentation and device_class in {"handheld", "tablet"})
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


def context_from_session(base: AppContext, mode: str,
                         capabilities: Mapping[str, bool]) -> AppContext:
    """Project a resolved service choice; never resolve hardware policy here."""
    touch = capabilities.get("touchscreen")
    input_mode = base.input_mode if type(touch) is not bool else (
        InputMode.TOUCH if touch else InputMode.POINTER)
    known = mode in {"desktop", "tablet", "mobile"}
    presentation = (PresentationMode.WINDOWED if mode == "desktop" else PresentationMode.FULLSCREEN) if known else base.presentation
    device = ("handheld" if mode == "mobile" else mode) if known else base.device_class
    return AppContext(presentation, input_mode, device)


def _set_session_context(context: AppContext) -> None:
    global _session_context
    _session_context = context
