# SPDX-License-Identifier: Apache-2.0
"""LumaUI content: place search.

PlaceSearch — the field is the whole flow. Places open in a list just above
the field as you type (it sits at the foot of a sidebar); Up and Down move,
Enter adds the chosen one (the top one unless you moved), a click adds that
one, Esc closes the list and then clears the field; with nothing to offer
the list says "No places match" (v70 `lFoot({sug})`, `lPlaceSug`,
`lPlaceFind`). Promoted from Weather's Add City search: the 180 ms pause
before searching, names that start with what was typed before names that
contain it, coordinates typed as "37.32, -122.03", places already added left
out, and the index loaded off the main thread.

The data source is the application's: any object with `search(query)`
returning places (anything with `.name` and, optionally, `.subtitle`), and
optionally `load()`, which runs once in a thread before the first search.
A plain function `query -> places` works too. Searches run off the main
thread; an answer that arrives after the text changed is dropped (a
generation token), and nothing is searched until `load()` has finished.

An application whose source is already asynchronous (a network geocoder)
passes `provider=None`, connects to the `search` signal and answers with
`set_results(query, places)`, as the C part does (`::search` +
`luma_ui_place_search_set_results`). An answer for any query but the current
one is ignored. Enter pressed while an answer is on its way adds the top
match when it comes. `SidebarFoot(search=PlaceSearch(...))` hosts the field
at a sidebar's foot.

    PlaceSearch(weather.search, on_pick=add_city, exclude=lambda p: store.contains(p))
    PlaceSearch(lambda q: rank_places(MY_PLACES, q), on_pick=…, placeholder="Add a city or ZIP")

The list floats in the nearest LayerHost.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, GObject, Gtk, Pango  # noqa: E402

from . import icons, lumaui  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402
from .structure_layers import LayerHost  # noqa: E402

__all__ = ["PlaceSearch", "PlaceResult", "rank_places", "parse_coordinates", "NO_PLACES"]

NO_PLACES = "No places match"
SEARCH_ERROR = "Could not search. Check your connection and try again."


@dataclass(frozen=True)
class PlaceResult:
    """A place a provider offers: its name, one line of where it is, and the application's own value."""

    name: str
    subtitle: str = ""
    value: Any = None


def rank_places(places: Iterable[Any], query: str, *, limit: int | None = None,
                key: Callable[[Any], str] = lambda place: f"{place.name} {getattr(place, 'subtitle', '')}") -> list:
    """Places matching `query`: names that start with it first, then names that contain it (Weather's order)."""
    text = (query or "").strip().casefold()
    if not text:
        return []
    items = list(places)
    starts = [p for p in items if p.name.casefold().startswith(text)]
    contains = [p for p in items if p not in starts and text in key(p).casefold()]
    found = starts + contains
    return found[:limit] if limit else found


def parse_coordinates(text: str) -> tuple[float, float] | None:
    """`37.32, -122.03` typed into the field, for when there is no place database to search."""
    parts = [part.strip() for part in (text or "").replace(";", ",").split(",")]
    if len(parts) != 2:
        return None
    try:
        latitude, longitude = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    return latitude, longitude


class _Suggestions(Gtk.Box):
    __gtype_name__ = "LumaUIPlaceSuggestions"

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.LIST_BOX)
        self.add_css_class("lumaui-place-list")
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Places"])


class PlaceSearch(Gtk.Box):
    """A place field whose suggestions open above it. See the module docstring."""

    __gtype_name__ = "LumaUIPlaceSearch"
    __gsignals__ = {"search": (GObject.SignalFlags.RUN_LAST, None, (str,))}

    def __init__(self, provider: Any = None, *, on_pick: Callable[[Any], None], placeholder: str = "Add a city or ZIP",
                 exclude: Callable[[Any], bool] | None = None) -> None:
        super().__init__(valign=Gtk.Align.END)
        self.add_css_class("lumaui-place-search")
        self.provider, self.on_pick, self.exclude = provider, on_pick, exclude
        self.results: list = []
        self.selected = 0
        self._error: str | None = None
        self._dismiss_root: Gtk.Widget | None = None
        self._dismiss_controller: Gtk.GestureClick | None = None
        self._debounce = 0
        self._loaded = not hasattr(provider, "load")
        self._generation = 0          # bumped by every search and every cancel; late answers are dropped
        self._pending: str | None = None   # the query an answer is awaited for
        self._pick_when_ready = False
        glyph = icons.image("search")
        glyph.add_css_class("lumaui-place-search-icon")
        self.append(glyph)
        self.entry = Gtk.Text(hexpand=True, placeholder_text=placeholder,
                              accessible_role=Gtk.AccessibleRole.SEARCH_BOX)  # a search field, as v70's .srch
        self.entry.add_css_class("lumaui-place-entry")
        self.entry.update_property([Gtk.AccessibleProperty.LABEL], [placeholder])
        self.append(self.entry)
        self.list = _Suggestions()
        self.entry.update_relation([Gtk.AccessibleRelation.CONTROLS], [Gtk.AccessibleList.new_from_list([self.list])])
        self.entry.connect("changed", self._changed)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.entry.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_a: GLib.timeout_add(150, self._left))
        self.entry.add_controller(focus)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda *_a: self.entry.grab_focus())
        self.add_controller(click)
        if not self._loaded:
            threading.Thread(target=self._load, daemon=True).start()

    # ── public API ────────────────────────────────────────────────────────

    @property
    def text(self) -> str:
        return self.entry.get_text()

    def set_text(self, text: str) -> None:
        self.entry.set_text(text)

    @property
    def list_shown(self) -> bool:
        return self.list.get_parent() is not None and self.list.get_visible()

    def search_now(self) -> None:
        """Search for what is in the field without waiting for the typing pause."""
        self._cancel()
        self._run(self.text)

    def set_results(self, query: str, places: Iterable[Any]) -> bool:
        """The answer to a `search` for `query`; False (and ignored) when the field has moved on."""
        if query != self._pending:
            return False
        self._pending = None
        places = list(places or ())
        if self.exclude is not None:
            places = [p for p in places if not self.exclude(p)]
        self._error = None
        self.results = places[:tokens.PLACE_SEARCH["limit_count"]]
        self.selected = 0
        self._fill()
        if self._pick_when_ready:
            self._pick_when_ready = False
            self.pick()
        return True

    def set_error(self, query: str, message: str = SEARCH_ERROR) -> bool:
        """Show a failed search separately from a successful search with no matches."""
        if query != self._pending:
            return False
        self._pending = None
        self._pick_when_ready = False
        self.results = []
        self._error = message
        self._fill()
        return True

    @property
    def searching(self) -> bool:
        return self._pending is not None

    def pick(self, index: int | None = None) -> bool:
        """Add the chosen place (the selected one by default); False when there is none."""
        index = self.selected if index is None else index
        if not (0 <= index < len(self.results)):
            return False
        place = self.results[index]
        self._cancel()
        self.entry.set_text("")
        self.close_list()
        self.on_pick(place)
        return True

    def close_list(self) -> None:
        self._forget()
        if self._dismiss_root is not None and self._dismiss_controller is not None:
            self._dismiss_root.remove_controller(self._dismiss_controller)
        self._dismiss_root = None
        self._dismiss_controller = None
        if self.list.get_parent() is not None:
            self.list.get_parent().remove_overlay(self.list)

    # ── searching ─────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            self.provider.load()
        finally:
            GLib.idle_add(self._after_load)

    def _after_load(self) -> bool:
        self._loaded = True
        if self.text.strip():
            self._run(self.text)
        return False

    def _forget(self) -> None:
        """Drop any answer on its way."""
        self._generation += 1
        self._pending = None
        self._pick_when_ready = False

    def _changed(self, _entry: Gtk.Text) -> None:
        self._cancel()
        text = self.text
        if not text.strip():
            self.results = []
            self.close_list()
            return
        self._forget()
        self._debounce = GLib.timeout_add(int(tokens.MOTION["search_debounce_ms"]), self._debounced, text)

    def _debounced(self, text: str) -> bool:
        self._debounce = 0
        self._run(text)
        return False

    def _cancel(self) -> None:
        if self._debounce:
            GLib.source_remove(self._debounce)
            self._debounce = 0

    def _run(self, text: str) -> None:
        if not text.strip():
            self.results = []
            self.close_list()
            return
        self._forget()
        self._pending = text
        if not self._loaded:
            return  # _after_load searches for whatever the field holds then
        if self.provider is None:
            self.emit("search", text)
            return
        generation = self._generation
        search = getattr(self.provider, "search", self.provider)

        def work() -> None:
            try:
                places = list(search(text) or ())
            except Exception:  # noqa: BLE001 (do not expose provider internals to the UI)
                GLib.idle_add(self._failed, generation, text)
                return
            GLib.idle_add(self._answer, generation, text, places)
        threading.Thread(target=work, daemon=True).start()

    def _answer(self, generation: int, text: str, places: list) -> bool:
        if generation == self._generation:
            self.set_results(text, places)
        return False

    def _failed(self, generation: int, text: str) -> bool:
        if generation == self._generation:
            self.set_error(text)
        return False

    # ── the list ──────────────────────────────────────────────────────────

    def _fill(self) -> None:
        child = self.list.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.list.remove(child)
            child = following
        if not self.results:
            none = Gtk.Label(label=self._error or NO_PLACES, xalign=0, wrap=True)
            none.add_css_class("lumaui-place-none")
            self.list.append(none)
        for index, place in enumerate(self.results):
            self.list.append(self._row(index, place))
        self._present()

    def _row(self, index: int, place: Any) -> Gtk.Button:
        on = index == self.selected
        row = Gtk.Button(can_focus=False, accessible_role=Gtk.AccessibleRole.OPTION)
        row.add_css_class("lumaui-place-row")
        lumaui.set_css_class(row, "on", on)
        line = Gtk.Box()
        glyph = icons.image("map-pin")
        glyph.add_css_class("lumaui-place-row-icon")
        line.append(glyph)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        name = Gtk.Label(label=place.name, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        name.add_css_class("lumaui-place-name")
        text.append(name)
        subtitle = getattr(place, "subtitle", "") or ""
        if subtitle:
            where = Gtk.Label(label=subtitle, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            where.add_css_class("lumaui-t-caption")
            text.append(where)
        line.append(text)
        hint = Gtk.Label(label="Return", visible=on)
        hint.add_css_class("lumaui-place-key")
        line.append(hint)
        row.set_child(line)
        row.update_property([Gtk.AccessibleProperty.LABEL], [f"{place.name}, {subtitle}" if subtitle else place.name])
        row.update_state([Gtk.AccessibleState.SELECTED], [int(on)])
        row.connect("clicked", lambda _b, i=index: self.pick(i))
        return row

    def _present(self) -> None:
        from .action_bubble import float_at, rect_in
        host = LayerHost.for_widget(self)
        if self.list.get_parent() is not host:
            if self.list.get_parent() is not None:
                self.list.get_parent().remove_overlay(self.list)
            host.add_overlay(self.list)
        root = self.get_root()
        if root is not None and self._dismiss_root is None:
            # Observe the press without claiming it: the same click must still
            # activate another control, even if that control does not take focus.
            self._dismiss_root = root
            self._dismiss_controller = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)
            self._dismiss_controller.connect("pressed", self._outside_pressed)
            root.add_controller(self._dismiss_controller)
        self.list.set_visible(True)
        metrics = tokens.PLACE_SEARCH
        field = rect_in(host, self)
        float_at(host, self.list, field, prefer="above", align="start", offset=metrics["offset"],
                 edge=0, inset=0, width=field.width)

    def _outside_pressed(self, _gesture: Gtk.GestureClick, _count: int, x: float, y: float) -> None:
        from gi.repository import Graphene
        root = self._dismiss_root
        if root is None:
            return
        point = Graphene.Point().init(x, y)
        for widget in (self, self.list):
            ok, bounds = widget.compute_bounds(root)
            if ok and bounds.contains_point(point):
                return
        if _gesture is not None:
            _gesture.set_state(Gtk.EventSequenceState.DENIED)
            controller = self._dismiss_controller
            # Removing the capture controller while GTK dispatches the press
            # cancels the target button's click sequence. Finish dispatch first.
            def dismiss() -> bool:
                if self._dismiss_controller is controller:
                    self.close_list()
                return False
            GLib.idle_add(dismiss)
        else:
            self.close_list()

    def _select(self, index: int) -> None:
        if not self.results:
            return
        self.selected = index % len(self.results)
        self._fill()

    def _key(self, _controller: Gtk.EventControllerKey, keyval: int, _code: int, _state: Gdk.ModifierType) -> bool:
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up) and self.list_shown:
            self._select(self.selected + (1 if keyval == Gdk.KEY_Down else -1))
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if self._debounce:  # typed faster than the pause: search now, then add the top match
                self.search_now()
            if self.searching:  # the answer is on its way: add its top match when it comes
                self._pick_when_ready = True
                return True
            return self.pick()
        if keyval == Gdk.KEY_Escape:
            if self.list_shown:
                self.close_list()
            elif self.text:
                self.entry.set_text("")
            else:
                return False
            return True
        return False

    def _left(self) -> bool:
        if not self.entry.has_focus():
            self.close_list()
        return False
