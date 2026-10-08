# SPDX-License-Identifier: Apache-2.0
"""Described choices with one selection and a stable semantic radio group."""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk
from .content_type import TypeLabel


class ChoiceList(Gtk.Box):
    """Choose one (key, title, detail) row. Programmatic selection stays quiet.

    `size='panel'` uses the larger inset-panel rows. `on_choose(key)` runs
    after a user activates a different choice; focus remains in the list.
    """

    __gtype_name__ = "LumaUIChoiceList"

    def __init__(self, choices, *, selected=None, on_choose=None, size="regular"):
        if size not in ("regular", "panel"):
            raise ValueError("choice list size is regular or panel")
        super().__init__(orientation=Gtk.Orientation.VERTICAL,
                         accessible_role=Gtk.AccessibleRole.RADIO_GROUP)
        self.add_css_class("lumaui-choice-list")
        if size == "panel":
            self.add_css_class("panel")
        self.buttons = {}
        self._selected = None
        self.on_choose = on_choose
        for key, title, detail in choices:
            if key in self.buttons:
                raise ValueError("choice keys must be unique")
            button = Gtk.Button(accessible_role=Gtk.AccessibleRole.RADIO)
            button.add_css_class("lumaui-choice-row")
            button.set_focusable(not self.buttons)
            line = Gtk.Box()
            line.add_css_class("lumaui-choice-content")
            indicator = Gtk.Box(valign=Gtk.Align.START)
            indicator.add_css_class("lumaui-choice-indicator")
            line.append(indicator)
            copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            copy.append(TypeLabel(title, role="choice_title"))
            if detail:
                label = TypeLabel(detail, role="choice_panel_detail" if size == "panel" else "choice_detail", wrap=True)
                label.label.set_hexpand(True)
                copy.append(label)
            line.append(copy)
            button.set_child(line)
            button.update_property([Gtk.AccessibleProperty.LABEL], [title])
            if detail:
                button.update_property([Gtk.AccessibleProperty.DESCRIPTION], [detail])
            button.update_state([Gtk.AccessibleState.CHECKED], [Gtk.AccessibleTristate.FALSE])
            button.connect("clicked", lambda _button, key=key: self.set_selected(key, notify=True))
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", lambda _controller, code, _scan, _state, key=key: self._key(key, code))
            button.add_controller(keys)
            self.buttons[key] = button
            self.append(button)
        if selected is not None:
            self.set_selected(selected)

    @property
    def selected(self):
        return self._selected

    def _key(self, key, code):
        order = list(self.buttons)
        index = order.index(key)
        if code in (Gdk.KEY_Left, Gdk.KEY_Up):
            index = (index - 1) % len(order)
        elif code in (Gdk.KEY_Right, Gdk.KEY_Down):
            index = (index + 1) % len(order)
        elif code == Gdk.KEY_Home:
            index = 0
        elif code == Gdk.KEY_End:
            index = len(order) - 1
        else:
            return False
        self.set_selected(order[index], notify=True)
        self.buttons[order[index]].grab_focus()
        return True

    def set_selected(self, key, *, notify=False):
        if key not in self.buttons:
            raise ValueError("selected key is not in the choice list")
        changed = self._selected != key
        self._selected = key
        for value, button in self.buttons.items():
            active = value == key
            button.set_focusable(active)
            (button.add_css_class if active else button.remove_css_class)("on")
            button.update_state([Gtk.AccessibleState.CHECKED],
                                [Gtk.AccessibleTristate.TRUE if active else Gtk.AccessibleTristate.FALSE])
        if changed and notify and self.on_choose is not None:
            self.on_choose(key)
