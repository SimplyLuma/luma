# SPDX-License-Identifier: Apache-2.0
"""Going back and forward inside an application.

A person who opens an album, an artist or a folder expects to return the way
they came: with a back control, a breadcrumb, the mouse's back button, or
Alt+Left. An application that swaps pages in a plain stack gives them none of
those. ``NavigationTrail`` is the history such an application keeps, and
``bind_navigation_input`` connects every conventional back and forward input
to it once, so each application does not rediscover which mouse button is
"back".

A trail is hierarchical at its root and chronological above it. Choosing a
top-level place (a sidebar row, a tab) starts a new trail; opening something
from a page pushes onto it. The breadcrumb shows the trail, back pops it, and
forward re-enters what back left.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Hashable

#: X11 and evdev number the side buttons 8 (back) and 9 (forward); GTK passes
#: the same numbers on Wayland.
MOUSE_BACK = 8
MOUSE_FORWARD = 9


@dataclass(frozen=True)
class Place:
    """One page in a trail: which page, what it shows, and its title."""

    view: str
    title: str
    subject: Hashable = None


class NavigationTrail:
    def __init__(self, root: Place, on_change: Callable[[Place, str], None] | None = None) -> None:
        self._places: list[Place] = [root]
        self._forward: list[Place] = []
        self._on_change = on_change
        self._listeners: list[Callable[[Place, str], None]] = []

    @property
    def places(self) -> tuple[Place, ...]:
        return tuple(self._places)

    @property
    def current(self) -> Place:
        return self._places[-1]

    @property
    def can_go_back(self) -> bool:
        return len(self._places) > 1

    @property
    def can_go_forward(self) -> bool:
        return bool(self._forward)

    def add_listener(self, callback: Callable[[Place, str], None]) -> Callable[[], None]:
        """Also call `callback(place, direction)` on every change (a trail bar).

        Returns a function that removes it again.
        """
        self._listeners.append(callback)
        return lambda: callback in self._listeners and self._listeners.remove(callback)

    def _changed(self, direction: str) -> None:
        if self._on_change is not None:
            self._on_change(self.current, direction)
        for listener in tuple(self._listeners):
            listener(self.current, direction)

    def start(self, root: Place) -> None:
        """Begin again from a top-level place."""
        if self._places == [root]:
            return
        self._places = [root]
        self._forward = []
        self._changed("root")

    def open(self, place: Place) -> None:
        """Open a place from the current page."""
        if place == self.current:
            return
        self._places.append(place)
        self._forward = []
        self._changed("forward")

    def replace(self, place: Place) -> None:
        """Change what the current page shows without adding a step."""
        self._places[-1] = place
        for listener in tuple(self._listeners):
            listener(self.current, "replace")

    def back(self) -> bool:
        if not self.can_go_back:
            return False
        self._forward.append(self._places.pop())
        self._changed("back")
        return True

    def forward(self) -> bool:
        if not self._forward:
            return False
        self._places.append(self._forward.pop())
        self._changed("forward")
        return True

    def go_to(self, index: int) -> bool:
        """Return to an earlier crumb, keeping the steps left for forward."""
        if not 0 <= index < len(self._places) - 1:
            return False
        left = self._places[index + 1:]
        del self._places[index + 1:]
        self._forward.extend(reversed(left))
        self._changed("back")
        return True


def bind_navigation_input(widget, back: Callable[[], bool], forward: Callable[[], bool]) -> None:
    """Route the mouse's side buttons, Alt+Left/Right and the Back/Forward
    keys on ``widget`` (usually the window) to ``back`` and ``forward``.

    Each callback returns whether it moved, so an input with nowhere to go
    reaches the rest of the window as it would have without the binding.
    """
    import gi

    gi.require_version("Gdk", "4.0")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gtk

    clicks = Gtk.GestureClick(button=0)
    # Capture, so a list row or picture under the pointer does not take the
    # side button for an ordinary click first.
    clicks.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

    def pressed(gesture, _n_press: int, _x: float, _y: float) -> None:
        button = gesture.get_current_button()
        action = {MOUSE_BACK: back, MOUSE_FORWARD: forward}.get(button)
        if action is not None and action():
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    clicks.connect("pressed", pressed)
    widget.add_controller(clicks)

    keys = Gtk.EventControllerKey()
    keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

    def key_pressed(_controller, keyval: int, _keycode: int, state) -> bool:
        modifiers = state & Gtk.accelerator_get_default_mod_mask()
        if keyval == Gdk.KEY_Back or (keyval == Gdk.KEY_Left and modifiers == Gdk.ModifierType.ALT_MASK):
            return back()
        if keyval == Gdk.KEY_Forward or (keyval == Gdk.KEY_Right and modifiers == Gdk.ModifierType.ALT_MASK):
            return forward()
        return False

    keys.connect("key-pressed", key_pressed)
    widget.add_controller(keys)
