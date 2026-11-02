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


#: The posture that selects an app's TV presentation (luma-shell-state `posture`).
TV_POSTURE = "tv"


def system_posture(environment: Mapping[str, str] | None = None) -> str:
    """The posture as the system states it, without the session bus.

    `LUMA_POSTURE` overrides (tests, reviews, a capture harness). Otherwise
    the shell-state `posture` key, then a device that declares itself a
    television (/etc/luma-device-class). Returns "tv" or "". A system without
    the shell-state schema (another distribution, a sandbox) is never TV.
    """
    env = os.environ if environment is None else environment
    value = env.get("LUMA_POSTURE", "").strip().lower()
    if value:
        return TV_POSTURE if value == TV_POSTURE else ""
    if environment is not None:
        return ""
    selected = "automatic"
    try:
        from gi.repository import Gio
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup("org.project_luma.shell-state", True) if source else None
        if schema is not None and schema.has_key("posture"):
            selected = Gio.Settings.new("org.project_luma.shell-state").get_string("posture")
    except (ImportError, ValueError, TypeError):
        pass
    if selected == TV_POSTURE:
        return TV_POSTURE
    try:
        device = Path("/etc/luma-device-class").read_text(encoding="utf-8").strip().lower()
    except (OSError, UnicodeError):
        device = ""
    return TV_POSTURE if selected == "automatic" and device == TV_POSTURE else ""


@dataclass(frozen=True)
class AppContext:
    presentation: PresentationMode
    input_mode: InputMode
    device_class: str
    #: "tv" in TV posture, else "". Independent of presentation: a TV is a
    #: desktop presentation underneath, so presentation checks are unchanged.
    posture: str = ""

    @property
    def tv(self) -> bool:
        return self.posture == TV_POSTURE

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
        return cls(presentation, input_mode, device_class, system_posture(environment))


def context_from_session(base: AppContext, mode: str,
                         capabilities: Mapping[str, bool],
                         posture: str | None = None) -> AppContext:
    """Project a resolved service choice; never resolve hardware policy here.

    `posture` is the service's effective posture (desktop, tablet, mobile or
    tv); None keeps the base posture (an older service without the property).
    """
    touch = capabilities.get("touchscreen")
    input_mode = base.input_mode if type(touch) is not bool else (
        InputMode.TOUCH if touch else InputMode.POINTER)
    known = mode in {"desktop", "tablet", "mobile"}
    presentation = (PresentationMode.WINDOWED if mode == "desktop" else PresentationMode.FULLSCREEN) if known else base.presentation
    device = ("handheld" if mode == "mobile" else mode) if known else base.device_class
    # LUMA_POSTURE forces the posture (tests, reviews, capture harnesses), over the service too.
    forced = os.environ.get("LUMA_POSTURE", "").strip()
    resolved = base.posture if posture is None or forced else (TV_POSTURE if posture == TV_POSTURE else "")
    return AppContext(presentation, input_mode, device, resolved)


def _set_session_context(context: AppContext) -> None:
    global _session_context
    _session_context = context
