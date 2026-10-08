# SPDX-License-Identifier: Apache-2.0
"""The window LumaUI draws itself: frame, title row, identity and islands (ADR-052).

A LumaUI window used to be dressed by Luma's patched libadwaita: it saw the
`luma-app-window` class, drew the frame and the 42 px title row, built the
identity pill, styled the window controls, and gave the islands their gutter,
radius and shadow. LumaUI owns all of that now, so a LumaUI application looks
the same on stock GTK 4 and libadwaita as on Luma's patched ones:

- `lumaui-palette.css` and `lumaui-toolkit.css` carry the design the patches
  used to add to libadwaita's stylesheet: libadwaita's colour names, the frame,
  the title row, the identity, the connected window controls, the gutter, the
  islands, sidebars, menus, command rows, dialogs and the Frost and Glass
  materials. They load at THEME + 1: above whatever libadwaita the system
  has, below the kit's components and every application sheet. On a patched
  system both say the same thing, and the kit's copy decides, so nothing is
  drawn twice and a stock system draws the same pixels.
- `WindowIdentity` is the identity pill: the application's name and icon from
  its desktop entry, and the kit's own menu. The patched libadwaita builds its
  own identity only when a header bar has none, so a window carrying this one
  never gets a second.
- `WindowControls` are v70's window controls (.wctl): Lucide minus,
  maximize-2 and x in 30 px squares, drawn by the kit on any libadwaita, in
  the order the desktop's decoration layout gives, reachable from the
  keyboard. The header bar's own title buttons are turned off, so a patched
  or a stock libadwaita never draws a second set.

This module never imports `widgets.py`, so the portable install (no Luma
typelib) loads the same toolkit sheets.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

#: The toolkit sheets, in load order: the palette first, so the toolkit's
#: colour names resolve.
TOOLKIT_SHEETS = ("lumaui-palette.css", "lumaui-toolkit.css")

#: Above libadwaita (THEME), below the kit's components (APPLICATION) and the
#: sheets applications add (APPLICATION + 1).
TOOLKIT_PRIORITY = Gtk.STYLE_PROVIDER_PRIORITY_THEME + 1

#: Classes the patched libadwaita used to add to a Luma window at run time.
#: The kit adds them itself so the toolkit sheet sees the same window on any
#: libadwaita. The window carries the first two, the work surface the last.
NATIVE_WINDOW_CLASSES = ("luma-native-window", "luma-adw-native-window")
WORK_SURFACE_CLASS = "luma-native-work-surface"

IDENTITY_ICON_SIZE = 22
IDENTITY_CHEVRON_SIZE = 14
IDENTITY_NAME_CHARS = 24
#: v70 .tbar: 9 px between the identity's icon, name and chevron.
IDENTITY_GAP = 9
#: v70 lMenu: 8 below its anchor, 8 inside the window; .crpop pads its rows 6.
MENU_DROP = 8
MENU_INSET = 8
MENU_PADDING = 6
#: Stable widget names: tests, lumaui-conform and accessibility tooling find the frame by them.
TITLE_ROW_NAME = "lumaui-title-row"
IDENTITY_NAME = "lumaui-identity"
IDENTITY_ICON_NAME = "lumaui-identity-icon"
IDENTITY_MENU_NAME = "lumaui-identity-menu"
CONTROLS_NAME = "lumaui-window-controls"

_toolkit_providers: dict[int, list[tuple[Gtk.CssProvider, str]]] = {}
_material_providers: dict[int, Gtk.CssProvider] = {}

#: The kit colours a Frost or Glass window restates (names in lumaui-palette.css).
MATERIAL_ROLES = ("window", "content", "island", "chrome", "chrome_secondary", "menu",
                  "ink", "muted", "faint", "line", "fill", "hover", "selected")


def _find(filename: str) -> str | None:
    # Local import: lumaui imports nothing of ours at module level either.
    from .lumaui import find_asset
    # LUMAUI_PALETTE_PATH, LUMAUI_TOOLKIT_PATH point a check at a source tree.
    return find_asset(filename, filename.split(".")[0].upper().replace("-", "_") + "_PATH")


def _style_manager():
    try:
        gi.require_version("Adw", "1")
        from gi.repository import Adw
    except (ImportError, ValueError):
        return None
    return Adw.StyleManager.get_default()


def _follow_scheme(providers: list[Gtk.CssProvider]) -> None:
    """Evaluate the sheets' dark and high-contrast media as libadwaita does.

    GTK 4.20 lets each provider say which colour scheme and contrast its
    `@media` queries see, and libadwaita sets its own from the style manager
    rather than from GtkSettings; a provider left alone would keep reading
    light while every libadwaita rule around it had gone dark.
    """
    manager = _style_manager()
    if manager is None or not providers or not hasattr(Gtk, "InterfaceColorScheme"):
        return

    def sync(*_args) -> None:
        scheme = Gtk.InterfaceColorScheme.DARK if manager.get_dark() else Gtk.InterfaceColorScheme.LIGHT
        contrast = (Gtk.InterfaceContrast.MORE if manager.get_high_contrast()
                    else Gtk.InterfaceContrast.NO_PREFERENCE)
        for provider in providers:
            provider.set_property("prefers-color-scheme", scheme)
            provider.set_property("prefers-contrast", contrast)

    sync()
    manager.connect("notify::dark", sync)
    manager.connect("notify::high-contrast", sync)


#: libadwaita's default accent, blue, in Luma's shade. libadwaita computes the
#: accent in C, so the palette alone cannot restate it; this names it only while
#: the accent is the default, so a person's own accent choice still wins.
DEFAULT_ACCENT_SHEET = "@define-color accent_bg_color @lumaui_accent_blue;"


def _follow_default_accent(display: Gdk.Display) -> None:
    manager = _style_manager()
    if manager is None or not hasattr(manager, "get_accent_color"):
        return
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    provider = Gtk.CssProvider()
    Gtk.StyleContext.add_provider_for_display(display, provider, TOOLKIT_PRIORITY)

    def sync(*_args) -> None:
        default = manager.get_accent_color() == Adw.AccentColor.BLUE
        provider.load_from_string(DEFAULT_ACCENT_SHEET if default else "")

    sync()
    manager.connect("notify::accent-color", sync)


def install_toolkit(display: Gdk.Display | None = None) -> None:
    """Load the toolkit sheets for `display`, once."""
    display = display or Gdk.Display.get_default()
    if display is None or hash(display) in _toolkit_providers:
        return
    loaded = []
    for filename in TOOLKIT_SHEETS:
        path = _find(filename)
        if path is None:
            continue
        provider = Gtk.CssProvider()
        provider.load_from_path(path)
        Gtk.StyleContext.add_provider_for_display(display, provider, TOOLKIT_PRIORITY)
        loaded.append((provider, path))
    _toolkit_providers[hash(display)] = loaded
    _follow_scheme([provider for provider, _path in loaded])
    _follow_default_accent(display)


TREATMENTS = ("light", "dark", "frost", "glass")
_treatment: dict[str, str] = {}
_watching_toplevels: list[Gio.ListModel] = []


def _apply_treatment(*_args) -> None:
    """Give every toplevel the one treatment class (`luma-treatment-glass`)."""
    current = _treatment.get("name")
    if current is None:
        return
    toplevels = Gtk.Window.get_toplevels()
    for index in range(toplevels.get_n_items()):
        window = toplevels.get_item(index)
        for name in TREATMENTS:
            if name == current:
                window.add_css_class(f"luma-treatment-{name}")
            else:
                window.remove_css_class(f"luma-treatment-{name}")


def set_treatment(display: Gdk.Display | None, appearance: str, dark: bool = False) -> None:
    """Paint every window in one treatment: its class, and its material.

    What colour a window is painted is one decision for the whole process, and
    every toplevel carries it, kit window or not (it used to be Luma's patched
    libadwaita that made it, patch 0042). High contrast paints the plain light
    or dark treatment. Whether a surface also gets a live blur region is the
    backdrop's business, per window, and never changes the colour.
    """
    treatment = appearance if appearance in TREATMENTS else ("dark" if dark else "light")
    _treatment["name"] = treatment
    if not _watching_toplevels:
        toplevels = Gtk.Window.get_toplevels()
        toplevels.connect("items-changed", _apply_treatment)
        _watching_toplevels.append(toplevels)
    _apply_treatment()
    set_material(display, treatment)


def set_material(display: Gdk.Display | None, treatment: str) -> None:
    """Name the Frost or Glass material as the kit's colours, or nothing.

    Above the theme (SETTINGS), where Luma's patched libadwaita used to restate
    them, so a translucent window is one material whatever built it.
    """
    display = display or Gdk.Display.get_default()
    if display is None:
        return
    provider = _material_providers.get(hash(display))
    if provider is None:
        provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(display, provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_SETTINGS)
        _material_providers[hash(display)] = provider
    sheet = ""
    if treatment in ("frost", "glass"):
        sheet = "".join(f"@define-color luma_{role} @lumaui_{treatment}_{role};"
                        for role in MATERIAL_ROLES)
    provider.load_from_string(sheet)


def reload_toolkit(display: Gdk.Display | None = None) -> None:
    """Parse the toolkit sheets again after the kit's tokens changed.

    The toolkit names the kit's colours (`@luma_content`, `@luma_line`), and a
    sheet takes a colour's value when it is parsed.
    """
    display = display or Gdk.Display.get_default()
    for provider, path in _toolkit_providers.get(hash(display), ()):
        provider.load_from_path(path)


@lru_cache(maxsize=1)
def toolkit_carries_luma_patches() -> bool:
    """Whether the system libadwaita still carries Luma's visual patches.

    For diagnostics and the stock-versus-patched checks only: the kit draws the
    same window either way and never branches on this.
    """
    try:
        data = Gio.resources_lookup_data("/org/gnome/Adwaita/styles/gtk.css",
                                         Gio.ResourceLookupFlags.NONE)
    except GLib.Error:
        return False
    return b"luma-window-body" in data.get_data()


# ── Identity ───────────────────────────────────────────────────────────────

def _desktop_info(application: Gio.Application | None):
    app_id = application.get_application_id() if application is not None else None
    if not app_id:
        return None
    for namespace in ("GioUnix", "Gio"):
        try:
            if namespace == "GioUnix":
                gi.require_version("GioUnix", "2.0")
                from gi.repository import GioUnix as module
            else:
                module = Gio
            return module.DesktopAppInfo.new(f"{app_id}.desktop")
        except (ImportError, ValueError, AttributeError, TypeError):
            continue
    return None


def identity_name(application: Gio.Application | None, window: Gtk.Window) -> str:
    """The name the shell shows for this application, which the corner repeats."""
    info = _desktop_info(application)
    # GLib answers the program name ("python3") when no name was set, which is
    # never what the corner should say while the window has a title.
    named = GLib.get_application_name()
    for candidate in (
        info.get_display_name() if info is not None else None,
        named if named != GLib.get_prgname() else None,
        window.get_title(),
        GLib.get_prgname(),
    ):
        if candidate:
            return candidate
    return "Application"


def identity_icon(application: Gio.Application | None, window: Gtk.Window,
                  fallback: str | None = None) -> Gtk.Image:
    """The application's registered icon, in the order the shell resolves it.

    `fallback` is the icon the application names itself (AppWindow's
    `icon_name`), used when neither the window nor a desktop entry names one:
    a preview or a test run has no installed desktop entry, and the generic
    executable glyph is never what the corner should show.
    """
    theme = Gtk.IconTheme.get_for_display(window.get_display())

    def themed(name: str | None) -> Gtk.Image | None:
        return Gtk.Image.new_from_icon_name(name) if name and theme.has_icon(name) else None

    image = themed(window.get_icon_name()) or themed(Gtk.Window.get_default_icon_name())
    info = _desktop_info(application)
    if image is None and info is not None:
        gicon = info.get_icon()
        if isinstance(gicon, Gio.ThemedIcon):
            for name in gicon.get_names():
                image = themed(name)
                if image is not None:
                    break
        elif gicon is not None:
            image = Gtk.Image.new_from_gicon(gicon)
    if image is None and application is not None:
        image = themed(application.get_application_id())
    if image is None and fallback:
        image = themed(fallback)
    image = image or Gtk.Image.new_from_icon_name("application-x-executable")
    image.set_pixel_size(IDENTITY_ICON_SIZE)
    return image


class WindowIdentity(Gtk.MenuButton):
    """The identity pill in a window's top-left corner: icon, name, menu.

    The name and icon are the desktop entry's, so the corner never disagrees
    with the dock; the menu is the kit's own (`menus.Menu`). Two pointer and
    focus guards come with it: a tiling resize that ends without a crossing
    event leaves no stuck highlight, and the silent focus GTK gives a freshly
    mapped window's first control never lands on the pill.
    """

    def __init__(self, window: Gtk.Window, menu: Gtk.Popover, *, icon_name: str | None = None,
                 icon: bool = True) -> None:
        """`icon=False`: the name and chevron alone (v71 Charlie's title row: "Charlie ⌄", no icon)."""
        super().__init__(valign=Gtk.Align.CENTER)
        self.set_name(IDENTITY_NAME)
        application = window.get_application()
        self.name = identity_name(application, window)
        # The class the patched libadwaita looks for before building its own
        # identity: with it, that library stands aside.
        self.add_css_class("luma-identity-button")
        self.add_css_class("luma-automatic-identity")
        if getattr(menu, "variant", None) == "compact":
            self.add_css_class("luma-identity-compact")
        content = Gtk.Box(spacing=IDENTITY_GAP, valign=Gtk.Align.CENTER)
        content.add_css_class("luma-identity-content")
        tile = Gtk.Box(valign=Gtk.Align.CENTER, overflow=Gtk.Overflow.HIDDEN)
        tile.set_name(IDENTITY_ICON_NAME)
        tile.add_css_class("luma-identity-icon")
        tile.set_size_request(IDENTITY_ICON_SIZE, IDENTITY_ICON_SIZE)
        tile.append(identity_icon(application, window, icon_name))
        if icon:
            content.append(tile)
        else:
            self.add_css_class("no-icon")
        self.label = Gtk.Label(label=self.name, ellipsize=Pango.EllipsizeMode.END, max_width_chars=IDENTITY_NAME_CHARS)
        self.label.add_css_class("luma-identity-label")
        content.append(self.label)
        from . import icons
        chevron = icons.image("chevron-down")
        chevron.set_pixel_size(IDENTITY_CHEVRON_SIZE)
        chevron.add_css_class("luma-identity-chevron")
        content.append(chevron)
        self.set_child(content)
        self.set_popover(menu)
        # v70 lMenu: the menu hangs 8 px below the identity with its left edge
        # 8 px in from the window's (its right edge would sit at the
        # identity's right, but a menu is wider than the pill).
        menu.set_name(IDENTITY_MENU_NAME)
        menu.set_has_arrow(False)
        menu.set_position(Gtk.PositionType.BOTTOM)
        menu.set_offset(0, MENU_DROP)
        # GtkMenuButton calls this before mapping the popup, for both pointer
        # and keyboard activation. A map-time retry relocates a visible card.
        self.set_create_popup_func(lambda *_args: self._place_menu(menu))
        self.set_tooltip_text(self.name)
        # What it is for a screen reader: the application's menu, by name (KB7).
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"{self.name} menu"])
        self._guard_pointer_state()
        self._window = window
        self._had_input = False
        window.connect("notify::focus-widget", self._release_silent_focus)
        watch = Gtk.EventControllerLegacy(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        watch.connect("event", self._note_input)
        window.add_controller(watch)

    def _place_menu(self, menu: Gtk.Popover) -> bool:
        root = self.get_root()
        child = menu.get_child()
        width = (child.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
                 if child is not None else 0) + 2 * MENU_PADDING
        if root is None:
            return GLib.SOURCE_REMOVE
        if width <= 2 * MENU_PADDING:
            return GLib.SOURCE_REMOVE
        ok, bounds = self.compute_bounds(root)
        if not ok:
            return
        # A compact identity menu follows the leading edge of its title pill;
        # ordinary identity menus retain the shared trailing alignment.
        compact = getattr(menu, "variant", None) == "compact"
        if compact:
            from .structure_adapt import is_phone
            from .lumaui_tokens import WINDOW
            phone = is_phone(self)
            inset = WINDOW["identity_compact_inset"]
            left = max(2 if phone else inset, bounds.get_x() - 6)
        else:
            left = max(MENU_INSET, bounds.get_x() + bounds.get_width() - width)
        rect = Gdk.Rectangle()
        rect.x = int(round(left - bounds.get_x() + width / 2))
        # v70 hangs it from the name and chevron's box (14 tall, centred in
        # the row), not the pill's taller hit area.
        rect.y, rect.width = 0, 1
        rect.height = (int(round(bounds.get_height())) if compact else
                       int(round(bounds.get_height() / 2 + IDENTITY_CHEVRON_SIZE / 2)))
        if compact:
            drop = WINDOW["identity_compact_menu_drop"]
            menu.set_offset(6, drop) if phone else menu.set_offset(0, drop)
        current = menu.get_pointing_to()
        if not (current[0] and current[1].x == rect.x and current[1].height == rect.height):
            menu.set_pointing_to(rect)
        return GLib.SOURCE_REMOVE

    def _clear_pointer_state(self, *_args) -> None:
        flags = Gtk.StateFlags.PRELIGHT | Gtk.StateFlags.ACTIVE
        self.unset_state_flags(flags)
        inner = self.get_first_child()
        if inner is not None:
            inner.unset_state_flags(flags)

    def _guard_pointer_state(self) -> None:
        motion = Gtk.EventControllerMotion()
        motion.connect("leave", self._clear_pointer_state)
        self.add_controller(motion)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", self._clear_pointer_state)
        self.add_controller(focus)
        legacy = Gtk.EventControllerLegacy(propagation_phase=Gtk.PropagationPhase.CAPTURE)

        def event(controller, _event) -> bool:
            # PyGObject cannot hand a GdkEvent to a signal; ask the controller.
            ev = controller.get_current_event()
            if ev is not None and ev.get_event_type() in (Gdk.EventType.GRAB_BROKEN,
                                                          Gdk.EventType.BUTTON_RELEASE):
                self._clear_pointer_state()
            return False

        legacy.connect("event", event)
        self.add_controller(legacy)

    def _note_input(self, controller, _event) -> bool:
        event = controller.get_current_event()
        if event is not None and event.get_event_type() in (
                Gdk.EventType.KEY_PRESS, Gdk.EventType.BUTTON_PRESS, Gdk.EventType.TOUCH_BEGIN):
            self._had_input = True
        return False

    def _release_silent_focus(self, window: Gtk.Window, _pspec) -> None:
        if self._had_input:
            return
        # The whole title row, not only the pill: the window controls take
        # keyboard focus too, and a window must never open with a ring on Close.
        row = getattr(self, "_title_row", None) or self
        focus = window.get_focus()
        if focus is not None and (focus is row or focus.is_ancestor(row)):
            window.set_focus(None)


class WindowControls(Gtk.Box):
    """v70's window controls (.wctl): Minimize, Zoom and Close.

    Three 30 px squares 2 apart with Lucide glyphs (minus, maximize-2, x),
    drawn by the kit so they are the same on a stock and a patched
    libadwaita. They follow the desktop's decoration layout (the part after
    the colon; GTK's own names), run GTK's own window actions (so Zoom is
    unavailable on a window that cannot be resized), and take keyboard focus
    like any button. Close is always there: on a desktop the title row is the
    window's close control (ADR-042).
    """

    #: layout name -> (Lucide glyph, accessible name, GTK window action)
    BUTTONS = {"minimize": ("minus", "Minimize", "window.minimize"),
               "maximize": ("maximize-2", "Zoom", "window.toggle-maximized"),
               "close": ("x", "Close", "window.close")}

    def __init__(self, window: Gtk.Window) -> None:
        from .lumaui_tokens import WINDOW
        # The 2 px between the squares is the sheet's border-spacing, which a
        # GtkBox adds to its own spacing: the property stays 0.
        super().__init__(spacing=0, valign=Gtk.Align.CENTER)
        self.set_name(CONTROLS_NAME)
        self.add_css_class("lumaui-window-controls")
        self._window = window
        self._icon_size = WINDOW["control_icon"]
        settings = Gtk.Settings.get_for_display(window.get_display())
        settings.connect("notify::gtk-decoration-layout", lambda *_a: self._build())
        self._settings = settings
        self._build()

    def layout(self) -> list[str]:
        """The buttons the desktop asks for, in its order, with Close kept."""
        text = self._settings.get_property("gtk-decoration-layout") or ":minimize,maximize,close"
        end = text.split(":", 1)[1] if ":" in text else text
        names = [name for name in (part.strip() for part in end.split(",")) if name in self.BUTTONS]
        names = list(dict.fromkeys(names))
        if "close" not in names:
            names.append("close")
        return names

    def _build(self) -> None:
        from . import icons
        while (child := self.get_first_child()) is not None:
            self.remove(child)
        for name in self.layout():
            glyph, label, action = self.BUTTONS[name]
            # Reachable with Tab, but a click leaves the focus where it was (as GTK's own controls do).
            button = Gtk.Button(action_name=action, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER,
                                focus_on_click=False)
            button.set_name(f"lumaui-window-{name}")
            button.add_css_class("lumaui-window-control")
            button.add_css_class(name)
            button.set_child(icons.image(glyph, pixel_size=self._icon_size))
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            self.append(button)

    def button(self, name: str) -> Gtk.Button | None:
        child = self.get_first_child()
        while child is not None:
            if child.get_name() == f"lumaui-window-{name}":
                return child
            child = child.get_next_sibling()
        return None


def own_window_frame(window: Gtk.Window, title_bar: Gtk.Widget, work_surface: Gtk.Widget,
                     identity: Gtk.Widget | None) -> None:
    """Dress a window the way the toolkit used to: classes, then the identity.

    `title_bar` is the window's `AdwHeaderBar`; the identity goes first in its
    start box, which is where the patched libadwaita put its own.
    """
    install_toolkit(window.get_display())
    window.add_css_class("luma-app-window")
    for name in NATIVE_WINDOW_CLASSES:
        window.add_css_class(name)
    title_bar.add_css_class("luma-titlebar")
    if identity is not None and identity.has_css_class("luma-identity-compact"):
        title_bar.add_css_class("luma-compact-identity")
    title_bar.set_name(TITLE_ROW_NAME)
    work_surface.add_css_class(WORK_SURFACE_CLASS)
    # The work surface owns its rounded clip, so no child (nor an island's
    # shadow) paints over the title row or past the surface's corners.
    work_surface.set_overflow(Gtk.Overflow.HIDDEN)
    if identity is not None:
        title_bar.pack_start(identity)
        identity._title_row = title_bar
    # The kit's own controls replace the header bar's (stock or patched), at the end.
    if hasattr(title_bar, "set_show_end_title_buttons"):
        title_bar.set_show_start_title_buttons(False)
        title_bar.set_show_end_title_buttons(False)
        controls = WindowControls(window)
        title_bar.pack_end(controls)
        window._lumaui_window_controls = controls


__all__ = [
    "NATIVE_WINDOW_CLASSES", "TOOLKIT_PRIORITY", "TOOLKIT_SHEETS", "WORK_SURFACE_CLASS",
    "WindowControls", "WindowIdentity", "identity_icon", "identity_name", "install_toolkit", "own_window_frame",
    "reload_toolkit", "set_material", "set_treatment", "toolkit_carries_luma_patches",
]
