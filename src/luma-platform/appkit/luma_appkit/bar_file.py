# SPDX-License-Identifier: Apache-2.0
"""FileSummaryRow: the current file's accessible disclosure in a headed bar."""
from __future__ import annotations
from typing import Callable
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import GObject, Gtk, Gdk, Pango
from . import icons, lumaui_tokens as tokens
from .rows_navigation import RowLead


class _ThumbnailLayout(Gtk.LayoutManager):
    def do_measure(self,widget,orientation,for_size):
        side=int(tokens.BAR['file_summary']['thumbnail'])
        return side,side,-1,-1

    def do_allocate(self,widget,width,height,baseline):
        rectangle=Gdk.Rectangle();rectangle.x=rectangle.y=0
        rectangle.width=width;rectangle.height=height
        widget.get_first_child().size_allocate(rectangle,baseline)


class _Thumbnail(Gtk.Box):
    """Constrain paintable natural dimensions without changing shared thumbnail paint."""
    def __init__(self,child):
        super().__init__(valign=Gtk.Align.CENTER,overflow=Gtk.Overflow.HIDDEN)
        self.set_layout_manager(_ThumbnailLayout())
        self.append(child)


class FileSummaryRow(Gtk.Button):
    """Thumbnail, ellipsized name and size, and disclosure (v71 Viewer .vwfile2).

    The app owns the file and the expanded panel. This reusable button owns its
    visual hierarchy and accessible state; pass it in an ActionCenter head.
    """
    __gtype_name__ = 'LumaUIFileSummaryRow'

    def __init__(self, title: str, subtitle: str = '', *, picture: Gdk.Paintable | None = None,
                 kind: str = 'image', expanded: bool = False,
                 on_open: Callable[[], None] | None = None):
        super().__init__(hexpand=True, valign=Gtk.Align.CENTER)
        self.add_css_class('lumaui-file-summary')
        self.line = Gtk.Box()
        self.thumbnail = _Thumbnail(RowLead.thumbnail(picture, kind=kind))
        self.line.append(self.thumbnail)
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self.title_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.title_label.add_css_class('lumaui-file-summary-title')
        self.subtitle_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.subtitle_label.add_css_class('lumaui-file-summary-subtitle')
        words.append(self.title_label); words.append(self.subtitle_label)
        self.line.append(words)
        disclosure = icons.image('chevron-down')
        disclosure.add_css_class('lumaui-file-summary-disclosure')
        self.line.append(disclosure)
        self.set_child(self.line)
        self.set_title(title, subtitle)
        self.set_expanded(expanded)
        if on_open is not None:
            self.connect('clicked', lambda _b: on_open())

    def set_title(self, title: str, subtitle: str = '') -> None:
        self.title_label.set_label(title)
        self.subtitle_label.set_label(subtitle)
        self.subtitle_label.set_visible(bool(subtitle))
        self.set_tooltip_text(title)
        self.update_property([Gtk.AccessibleProperty.LABEL], [title + (', ' + subtitle if subtitle else '')])

    def set_expanded(self, expanded: bool) -> None:
        (self.add_css_class if expanded else self.remove_css_class)('on')
        self.update_state([Gtk.AccessibleState.EXPANDED], [GObject.Value(GObject.TYPE_INT, int(expanded))])

class SubjectAction(FileSummaryRow):
    """Two-line activating subject and disclosure, without a file thumbnail."""
    __gtype_name__ = 'LumaUISubjectAction'

    def __init__(self, title: str, subtitle: str = '', *, on_activate=None):
        super().__init__(title, subtitle, on_open=on_activate)
        self.thumbnail.set_visible(False)
        self.bar_flexible = True
        self.bar_phone_wide = True
        self.primary = True
        self.title_label.set_max_width_chars(24)
        self.subtitle_label.set_max_width_chars(24)


from .action_center import register_item
register_item(SubjectAction, lambda item, _size: item)
