"""Shared responsive GTK/libadwaita components for Prairie applications.

GTK-backed exports are lazy so the capability contract remains testable on a
non-GTK build controller. Application processes resolve the same public names
after GTK is available.
"""

from .context import InputMode, PrairieContext, PresentationMode

__all__ = [
    "InputMode",
    "PrairieActionBar",
    "PrairieAvatar",
    "PrairieContext",
    "PrairieEmptyState",
    "PrairieIconButton",
    "PrairieMobileHeader",
    "PrairieTwoLineRow",
    "PresentationMode",
    "install_theme",
]


def __getattr__(name: str):
    if name == "install_theme":
        from .theme import install_theme

        return install_theme
    if name in {
        "PrairieActionBar",
        "PrairieAvatar",
        "PrairieEmptyState",
        "PrairieIconButton",
        "PrairieMobileHeader",
        "PrairieTwoLineRow",
    }:
        from . import widgets

        return getattr(widgets, name)
    raise AttributeError(name)
