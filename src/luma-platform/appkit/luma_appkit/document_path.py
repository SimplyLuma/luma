# SPDX-License-Identifier: Apache-2.0
"""Document location and clickable ancestor crumbs."""
from gi.repository import Gtk
from .rows_mark import Mark, MarkValue


class FolderPath(Gtk.Button):
    """A document's folder path; activating it invokes the caller's Move action."""
    __gtype_name__ = 'LumaUIFolderPath'

    def __init__(self, path, *, mark=None, on_activate=None):
        super().__init__(valign=Gtk.Align.CENTER)
        self.add_css_class('lumaui-folder-path')
        self.set_tooltip_text('Move')
        self.update_property([Gtk.AccessibleProperty.LABEL], ['Move'])
        line = Gtk.Box()
        line.add_css_class('lumaui-folder-path-content')
        line.append(Mark(value=mark or MarkValue(), density='path'))
        for index, name in enumerate(path):
            if index:
                slash = Gtk.Label(label='/')
                slash.add_css_class('lumaui-path-separator')
                line.append(slash)
            line.append(Gtk.Label(label=str(name)))
        self.set_child(line)
        if on_activate is not None:
            self.connect('clicked', lambda _button: on_activate())


class TrailCrumb(Gtk.Box):
    """An ancestor action with its leading path separator."""
    __gtype_name__ = 'LumaUITrailCrumb'

    def __init__(self, label, *, on_activate=None):
        super().__init__(valign=Gtk.Align.CENTER)
        self.add_css_class('lumaui-trail-crumb')
        slash = Gtk.Label(label='/')
        slash.add_css_class('lumaui-path-separator')
        self.append(slash)
        self.button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
        self.button.add_css_class('lumaui-trail-action')
        if on_activate is not None:
            self.button.connect('clicked', lambda _button: on_activate())
        self.append(self.button)


class PresenceChip(Gtk.Button):
    """A document's collaborators and optional live status, opening sharing."""
    __gtype_name__ = 'LumaUIPresenceChip'

    def __init__(self, people, *, label='People', status=None, on_activate=None):
        super().__init__(valign=Gtk.Align.CENTER)
        from .rows_people import AvatarStack
        self.add_css_class('lumaui-presence-chip')
        self.set_tooltip_text(label)
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        line = Gtk.Box()
        line.add_css_class('lumaui-presence-content')
        self.faces = AvatarStack(people, size='document')
        line.append(self.faces)
        if status:
            live = Gtk.Box(valign=Gtk.Align.CENTER)
            live.add_css_class('lumaui-presence-status')
            dot = Gtk.Box(valign=Gtk.Align.CENTER)
            dot.add_css_class('lumaui-presence-dot')
            live.append(dot)
            live.append(Gtk.Label(label=status))
            line.append(live)
        self.set_child(line)
        if on_activate is not None:
            self.connect('clicked', lambda _button: on_activate(self))
