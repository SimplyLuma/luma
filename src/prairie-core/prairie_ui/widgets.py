# SPDX-License-Identifier: Apache-2.0

"""Prairie's widgets are the Application Kit's, under their old names.

There was a second kit here — its own action bar, icon button, avatar, row
and empty state — and it was the reason a Prairie application and a Luma
application did not look like the same application. These names are kept so
nothing already written breaks; each is the kit's widget, and a new
application imports luma_appkit directly.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Pango  # noqa: E402

from luma_appkit import (  # noqa: E402
    AppContext, Avatar, EmptyState, IconButton, MobileHeader, NavigationRow, Toolbar,
)

from .context import InputMode


def _context_for(input_mode: InputMode) -> AppContext:
    context = AppContext.from_environment()
    return context if context.input_mode is input_mode else AppContext(
        presentation=context.presentation, input_mode=input_mode, device_class=context.device_class,
    )


class PrairieIconButton(IconButton):
    def __init__(self, icon_name: str, *, label: str, input_mode: InputMode, flat: bool = False) -> None:
        super().__init__(icon_name, label, context=_context_for(input_mode), quiet=flat)


class PrairieActionBar(Toolbar):
    """A toolbar with a title: the kit's toolbar, plus the one thing Prairie's
    added — a title label in the flexible middle."""

    def add_leading(self, widget: Gtk.Widget) -> None:
        self.append(widget)

    def add_title(self, title: str) -> Gtk.Label:
        label = Gtk.Label(label=title, xalign=0, hexpand=True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.add_css_class("luma-action-title")
        self.append(label)
        return label

    def add_trailing(self, widget: Gtk.Widget) -> None:
        self.append(widget)


class PrairieMobileHeader(MobileHeader):
    def __init__(
        self,
        title: str,
        *,
        input_mode: InputMode,
        leading_icon: str | None = None,
        leading_label: str = "Back",
        trailing_icon: str | None = None,
        trailing_label: str = "More",
    ) -> None:
        super().__init__(
            title,
            context=_context_for(input_mode),
            leading_icon=leading_icon,
            leading_label=leading_label,
            trailing_icon=trailing_icon,
            trailing_label=trailing_label,
        )


class PrairieAvatar(Avatar):
    def __init__(self, name: str, size: int = 44) -> None:
        # 44 was Prairie's default row avatar; the kit's sizes are the design's.
        super().__init__(name, large=size >= 64, hero=48 <= size < 64, compact=size < 32)


class PrairieTwoLineRow(NavigationRow):
    def __init__(self, title: str, subtitle: str, *, metadata: str = "",
                 avatar: Avatar | None = None) -> None:
        super().__init__(title, subtitle=subtitle, trailing=metadata, icon_widget=avatar)


class PrairieEmptyState(EmptyState):
    def __init__(self, title: str, description: str, icon_name: str, **kwargs) -> None:
        super().__init__(title, description, icon_name, **kwargs)
