# SPDX-License-Identifier: Apache-2.0

"""The two independent axes that drive converged Prairie applications.

Available width is deliberately absent here. Layout class belongs to
AdwBreakpoint in each window. Presentation and input are capabilities supplied
by Luma Shell through the activation environment and must never be inferred
from the width of a window.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

from luma_appkit.context import InputMode, PresentationMode
from pathlib import Path
from typing import Mapping


DEVICE_CLASS_MARKER = Path("/etc/luma-device-class")
DEVICE_CLASSES = frozenset({"desktop", "tablet", "handheld"})






@dataclass(frozen=True)
class PrairieContext:
    presentation: PresentationMode
    input_mode: InputMode

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "PrairieContext":
        env = os.environ if environment is None else environment
        device_class = _device_class(env)

        presentation_value = env.get("LUMA_PRESENTATION_MODE", "").strip().lower()
        if presentation_value:
            presentation = PresentationMode(presentation_value)
        elif device_class == "handheld":
            presentation = PresentationMode.FULLSCREEN
        else:
            presentation = PresentationMode.WINDOWED

        input_value = env.get("LUMA_INPUT_MODE", "").strip().lower()
        if input_value:
            input_mode = InputMode(input_value)
        elif device_class == "handheld":
            input_mode = InputMode.TOUCH
        else:
            input_mode = InputMode.POINTER

        return cls(presentation=presentation, input_mode=input_mode)


def _device_class(environment: Mapping[str, str]) -> str:
    explicit = environment.get("LUMA_DEVICE_CLASS", "").strip().lower()
    if explicit in DEVICE_CLASSES:
        return explicit

    try:
        marker = DEVICE_CLASS_MARKER.read_text(encoding="utf-8").strip().lower()
    except (FileNotFoundError, OSError, UnicodeError):
        marker = ""
    if marker in DEVICE_CLASSES:
        return marker
    return "desktop"
