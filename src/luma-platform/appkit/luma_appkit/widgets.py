"""GTK implementation of the stable Luma Application Kit components."""

from __future__ import annotations

import json
import operator
import ast
import os
import pathlib
import re
import unicodedata
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("LumaAppearance", "1")
gi.require_version("LumaUI", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango, LumaAppearance, LumaUI  # noqa: E402

from . import window_frame, window_policy
from .commands import CommandRegistry
# The menu is one component, defined once, in menus.py.
from .menus import Menu, command_popover
from .context import AppContext, InputMode, PresentationMode


_appkit_providers: dict[Gdk.Display, Gtk.CssProvider] = {}
_structure_providers: dict[Gdk.Display, Gtk.CssProvider] = {}
_appearance_policies: dict[Gdk.Display, LumaAppearance.SurfacePolicy] = {}

# Sheets applications register through add_style_sheet(). A sheet that names an
# AppKit colour takes the value current when it is parsed, so each is parsed
# again whenever the treatment changes; otherwise a sheet loaded before the
# desktop's preference arrived keeps light colours inside a dark window.
_style_sheets: list[tuple[Gtk.CssProvider, str]] = []

# Kit components whose style depends on more than a token name (the save
# sheet's per-appearance ink) register a builder here; each is rebuilt from
# `appearance` whenever the treatment changes, like the sheets above.
_style_builders: list[tuple[Gtk.CssProvider, Callable[[dict], str]]] = []

#: The treatment the kit last loaded: its name ("light", "dark", "frost",
#: "glass" or "high-contrast") and whether its ink is light, which is what a
#: colour measured per appearance needs to know.
appearance: dict = {"name": "light", "light_ink": False}


def _reload_style_sheets() -> None:
    for provider, path in _style_sheets:
        provider.load_from_path(path)
    for provider, build in _style_builders:
        provider.load_from_string(build(dict(appearance)))


def add_style_builder(build: Callable[[dict], str]) -> Gtk.CssProvider | None:
    """Style a kit component from the current treatment, and keep it current."""
    install_appkit()
    display = Gdk.Display.get_default()
    if display is None:
        return None
    provider = Gtk.CssProvider()
    provider.load_from_string(build(dict(appearance)))
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
    )
    _style_builders.append((provider, build))
    return provider


def _ink_is_light(token_sheet: str | None, dark: bool) -> bool:
    """Read the treatment's ink from its token sheet: light ink means a dark surface."""
    try:
        text = pathlib.Path(token_sheet).read_text(encoding="utf-8") if token_sheet else ""
    except OSError:
        text = ""
    match = re.search(r"@define-color\s+luma_ink\s+#([0-9a-fA-F]{6})\b", text)
    if not match:
        return dark
    red, green, blue = (int(match.group(1)[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue > 0.5


def add_style_sheet(
    path: str, priority: int = Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
) -> Gtk.CssProvider:
    """Load an application's style sheet and keep it in step with the treatment.

    The C kit offers the same through luma_ui_add_style_resource(); an
    application written against either kit follows the desktop into dark and
    back without reloading anything itself.
    """
    install_appkit()
    display = Gdk.Display.get_default()
    provider = Gtk.CssProvider()
    provider.load_from_path(path)
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(display, provider, priority)
    _style_sheets.append((provider, path))
    return provider


def _load_with_imports(provider: Gtk.CssProvider, path: str) -> None:
    """Load a sheet whose @import names a file that lives elsewhere in a source tree.

    luma-ui.css imports luma-empty-state-tokens.css from beside itself, which is
    where the installed copy and the gresource put it; in a source checkout the
    generated file is in appkit/, so the import is pointed at wherever
    _find_sheet finds it.
    """
    text = pathlib.Path(path).read_text(encoding="utf-8")
    here = pathlib.Path(path).parent

    def resolve(match):
        name = match.group(1)
        if (here / name).is_file():
            return f'@import url("file://{here / name}");'
        found = _find_sheet("", name)
        return f'@import url("file://{found}");' if found else match.group(0)
    import re as _re
    # A resource import (resource:///org/projectluma/platform/x.css) is the file beside the sheet when it is
    # there: a source tree has no compiled resources until the library is built and loaded.
    text = _re.sub(r'@import url\("resource:///org/projectluma/platform/([^"/]+\.css)"\);',
                   lambda m: f'@import url("{m.group(1)}");' if (here / m.group(1)).is_file() else m.group(0), text)
    provider.load_from_string(_re.sub(r'@import url\("([^"/]+\.css)"\);', resolve, text))


def _find_sheet(override_variable: str, filename: str) -> str | None:
    """Find a kit stylesheet, whether the kit is installed or run from source."""
    package = pathlib.Path(__file__).resolve().parent.parent
    for candidate in (
        os.environ.get(override_variable, ""),
        str(package / filename),
        # The C kit's sheets live beside the kit rather than in it. Without
        # this, luma-ui.css -- the one sheet loaded above an application's own
        # composition -- was quietly not found in a source tree, so an
        # application that paints its own header kept it.
        str(package.parent / "ui" / filename),
        f"/usr/share/luma-appkit/{filename}",
    ):
        if candidate and pathlib.Path(candidate).is_file():
            return candidate
    return None


def _prefers_dark() -> bool:
    """Say whether the desktop is dark, without trusting one source alone.

    Three sources, because each fails somewhere. libadwaita answers only for an
    application that initialised it. A GSettings read comes back with the
    schema default inside a systemd user service that never inherited the
    session's dconf profile — which is how a phone application ended up light
    on a dark phone. The portal is the session's own answer and survives both.
    Falling through to light on a dark desktop is the worst available answer,
    so any source saying dark is taken as dark.
    """
    if Adw.StyleManager.get_default().get_dark():
        return True
    source = Gio.SettingsSchemaSource.get_default()
    schema = source and source.lookup("org.gnome.desktop.interface", True)
    if schema is not None and schema.has_key("color-scheme"):
        if Gio.Settings.new("org.gnome.desktop.interface").get_string(
            "color-scheme"
        ) == "prefer-dark":
            return True
    return _portal_prefers_dark()


def _portal_prefers_dark() -> bool:
    """Ask the desktop portal what colour scheme the session is using."""
    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = connection.call_sync(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.Settings",
            "ReadOne",
            GLib.Variant("(ss)", ("org.freedesktop.appearance", "color-scheme")),
            GLib.VariantType("(v)"),
            Gio.DBusCallFlags.NONE,
            400,
            None,
        )
    except GLib.Error:
        return False
    # 1 means the session prefers dark; 0 is no preference and 2 is light.
    return reply.unpack()[0] == 1


def install_appkit() -> None:
    display = Gdk.Display.get_default()
    if display is None or display in _appkit_providers:
        return
    provider = Gtk.CssProvider()
    override = os.environ.get("LUMA_APPKIT_STYLE_PATH", "")

    # The colours themselves. A Python application that never imports the C
    # library had no source for @luma_ink, @luma_content or any other token,
    # and GTK drops a rule whose colour is undefined without saying so — which
    # is why an application written entirely against Luma's palette came out
    # looking like stock Adwaita.
    tokens = Gtk.CssProvider()
    Gtk.StyleContext.add_provider_for_display(
        display, tokens, Gtk.STYLE_PROVIDER_PRIORITY_THEME
    )

    surface_policy = LumaAppearance.SurfacePolicy.new(LumaAppearance.SurfaceTarget.APPLICATION)
    _appearance_policies[display] = surface_policy

    loading_tokens = False

    def load_for_appearance(*_arguments: object) -> None:
        nonlocal loading_tokens
        if loading_tokens:
            return
        loading_tokens = True
        try:
            manager = Adw.StyleManager.get_default()
            high_contrast = manager.get_high_contrast() or surface_policy.get_locked()
            chosen = surface_policy.get_has_selection()
            # The treatment this surface is *painted* in, which is not the
            # same question as whether this window has a live blur region:
            # painting from the second made one application a different
            # colour from the window beside it whenever its own protocol
            # handshake had not landed.
            effective = surface_policy.get_surface()
            dark = chosen and effective == "dark"
            frost = chosen and effective == "frost"
            glass = chosen and effective == "glass"
            manager.set_color_scheme(Adw.ColorScheme.DEFAULT if not chosen or high_contrast else
                Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
            if not chosen or high_contrast:
                dark = _prefers_dark()
            token_sheet = _find_sheet(
                "LUMA_APPKIT_TOKENS_PATH",
                "luma-appkit-high-contrast-tokens.css" if high_contrast else
                "luma-appkit-frost-tokens.css" if frost else
                "luma-appkit-glass-tokens.css" if glass else
                "luma-appkit-dark-tokens.css" if dark else "luma-appkit-tokens.css",
            )
            if token_sheet:
                tokens.load_from_path(token_sheet)
            appearance["name"] = (
                "high-contrast" if high_contrast else "frost" if frost else
                "glass" if glass else "dark" if dark else "light"
            )
            window_frame.set_treatment(display, appearance["name"], dark)
            from .lumaui import load_family_sheets
            load_family_sheets(display, appearance["name"])
            appearance["light_ink"] = _ink_is_light(token_sheet, dark)
            if override:
                path = override
            else:
                filename = (
                    "luma-appkit-high-contrast.css" if high_contrast else
                    "luma-appkit-frost.css" if frost else
                    "luma-appkit-glass.css" if glass else
                    "luma-appkit-dark.css" if dark else
                    "luma-appkit.css"
                )
                path = _find_sheet("LUMA_APPKIT_STYLE_PATH", filename) or f"/usr/share/luma-appkit/{filename}"
            provider.load_from_path(path)
            window_frame.reload_toolkit(display)
            _reload_style_sheets()

        finally:
            loading_tokens = False

    # Fable's proportions for plain GTK widgets, at APPLICATION priority. At
    # THEME the sheet only tied with Adwaita, whose selectors are the more
    # specific ones, so Adwaita won every disagreement and each application
    # drifted its own way. Application sheets load at APPLICATION + 1 through
    # add_style_sheet() and still have the last word.
    base = Gtk.CssProvider()
    base_sheet = _find_sheet("LUMA_APPKIT_BASE_PATH", "luma-appkit-base.css")
    if base_sheet:
        base.load_from_path(base_sheet)
        Gtk.StyleContext.add_provider_for_display(
            display, base, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    # The window frame and the parts libadwaita used to draw for Luma (the
    # title row, identity, controls, gutter, islands, menus, materials and
    # palette) are LumaUI's own sheets now, above whatever libadwaita the
    # system has (ADR-052). Loaded before the first token pass so that pass
    # parses them against the chosen treatment.
    window_frame.install_toolkit(display)
    # The component sheet joins before the first token pass creates the family
    # sheets' providers: at one priority GTK lets the provider added later win,
    # and a family sheet refines the components, never the other way round.
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    load_for_appearance()

    # Window material is structural, not application decoration.  Load the
    # same sheet used by the C kit above application composition so an old
    # Light fallback cannot repaint Frost or Glass descendants.  The sheet is
    # deliberately narrow: ordinary app controls remain overridable through
    # add_style_sheet().
    structure_sheet = _find_sheet("LUMA_APPKIT_STRUCTURE_PATH", "luma-ui.css")
    if structure_sheet:
        structure = Gtk.CssProvider()
        _load_with_imports(structure, structure_sheet)
        Gtk.StyleContext.add_provider_for_display(
            display,
            structure,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2,
        )
        _structure_providers[display] = structure
    _appkit_providers[display] = provider
    if not override:
        Adw.StyleManager.get_default().connect("notify::dark", load_for_appearance)
        Adw.StyleManager.get_default().connect("notify::high-contrast", load_for_appearance)
        surface_policy.connect("changed", load_for_appearance)


def motion_duration(milliseconds: int) -> int:
    """Respect the toolkit's reduced-motion/animation preference."""
    settings = Gtk.Settings.get_default()
    if settings is not None and not settings.get_property("gtk-enable-animations"):
        return 0
    return max(0, int(milliseconds))


class Island(Gtk.Box):
    """The content island, set into the frame (v70 Solid .isle).

    Its inner edge and shade are drawn over whatever the island holds, as
    v70's `.isle::after` is, so a lit header or a scrolled page slips under the
    edge instead of covering it. The colours are the `luma_island_*` tokens.
    """

    #: (token, dx, dy, spread, blur) for v70's --isle-in, drawn in this order.
    _INNER_EDGE = (("luma_island_edge", 0, 0, 1, 0), ("luma_island_rim", 0, 1, 0, 0),
                   ("luma_island_shade", 0, 3, -2, 8))

    def __init__(self, *, orientation: Gtk.Orientation = Gtk.Orientation.VERTICAL) -> None:
        super().__init__(orientation=orientation)
        self.add_css_class("luma-island")
        # Islands own their rounded clipping boundary.  Without this, an
        # application child with an opaque background can paint square corners
        # over the shared surface (most visibly at the bottom of editor panes).
        self.set_overflow(Gtk.Overflow.HIDDEN)
        from .lumaui import YieldingLayout
        self.set_layout_manager(YieldingLayout(orientation=orientation))

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        Gtk.Box.do_snapshot(self, snapshot)
        root = self.get_root()
        if isinstance(root, Gtk.Widget) and root.has_css_class("lumaui-phone-device"):
            return  # the phone screen has no desktop island rim, including below its status padding
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0 or self.has_css_class("compact") or self.has_css_class("drawer"):
            return
        from . import lumaui_tokens
        gi.require_version("Gsk", "4.0")
        gi.require_version("Graphene", "1.0")
        from gi.repository import Graphene, Gsk

        radius = float(lumaui_tokens.WINDOW["island_radius"])
        corner = Graphene.Size().init(radius, radius)
        outline = Gsk.RoundedRect()
        outline.init(Graphene.Rect().init(0, 0, width, height), corner, corner, corner, corner)
        context = self.get_style_context()
        for token, dx, dy, spread, blur in self._INNER_EDGE:
            found, colour = context.lookup_color(token)
            if found and colour.alpha > 0:
                snapshot.append_inset_shadow(outline, colour, dx, dy, spread, blur)


def _islands_breathe(split_view: Gtk.Widget, pages: tuple[Gtk.Widget | None, ...], collapsed: bool) -> None:
    """Let islands cast their shadow past a page's edge.

    A navigation page and an overlay split view clip to their allocation, which
    is right for a push transition and wrong for an island sitting flush
    against the page: the toolkit's ring and shadow die at the page's edge.
    Side by side, the pages and the split view stop clipping; collapsed, the
    clip returns so the transition stays clean.
    """
    overflow = Gtk.Overflow.HIDDEN if collapsed else Gtk.Overflow.VISIBLE
    split_view.set_overflow(overflow)
    for page in pages:
        if page is not None:
            page.set_overflow(overflow)


def IslandSplitView(**properties: object) -> Adw.NavigationSplitView:
    """A navigation split view whose pages are islands.

    ``AdwNavigationSplitView`` is deliberately final, so AppKit composes the
    real widget instead of introducing a wrapper or an unsupported subclass.
    Its pages clip to their allocation; here they stop clipping while the
    pages sit side by side, so the islands they hold keep the toolkit's ring
    and shadow on every edge.
    """
    split_view = Adw.NavigationSplitView(**properties)
    split_view.add_css_class("luma-island-split")

    def sync(*_args: object) -> None:
        _islands_breathe(split_view, (split_view.get_sidebar(), split_view.get_content()), split_view.get_collapsed())

    for signal in ("notify::sidebar", "notify::content", "notify::collapsed"):
        split_view.connect(signal, sync)
    sync()
    return split_view


def IslandOverlaySplitView(**properties: object) -> Adw.OverlaySplitView:
    """An overlay split view whose sidebar and content are islands.

    The overlay split view clips to its allocation; side by side it stops,
    so the islands keep the toolkit's ring and shadow on every edge, and the
    clip returns when the sidebar overlays the content.
    """
    split_view = Adw.OverlaySplitView(**properties)
    split_view.add_css_class("luma-island-split")

    def sync(*_args: object) -> None:
        _islands_breathe(split_view, (), split_view.get_collapsed())

    split_view.connect("notify::collapsed", sync)
    sync()
    return split_view


def centre_controls(bar: Gtk.Widget) -> None:
    """Stop a bar stretching its controls to its own height.

    A box hands each child its full height unless the child says otherwise, so
    a 28px control inside a 46px bar is drawn 46px tall however the sheet
    states it. Every Luma bar centres its controls instead; call this after
    filling a bar that is not a kit Toolbar.
    """
    # A bar centres across its own direction: a horizontal bar must not
    # stretch a control's height, and a vertical rail must not stretch its
    # width. The design says the same thing as `flex: 0 0 <size>`.
    vertical = (
        isinstance(bar, Gtk.Orientable)
        and bar.get_orientation() == Gtk.Orientation.VERTICAL
    )
    child = bar.get_first_child()
    while child is not None:
        if vertical:
            if child.get_halign() == Gtk.Align.FILL:
                child.set_halign(Gtk.Align.CENTER)
        elif child.get_valign() == Gtk.Align.FILL:
            child.set_valign(Gtk.Align.CENTER)
        child = child.get_next_sibling()


# Measured from the design's own window, not approximated. A control is a 20px
# button in a 64x24 pill padded 2 with nothing between the buttons, and its
# glyph is a Lucide icon painted in the button's colour at a stated size:
# chevron-down at 13, square at 9, x at 11.
# The design's compact control is 20 wide by 18 tall, which is what makes the
# pill 22 rather than 24: 18 of button inside 2px of padding.
_PILL_PADDING = 2
# One box for all three. The design draws each control as a 12px Lucide glyph
# masked out of the control ink, so they share a weight rather than each being
# sized by eye — which is what made the chevron heavier than the square.
# The glyphs sit at 78% of the control ink; full strength reads as a button
# that is already pressed.

# Lucide draws on a 24-unit grid with a 2-unit stroke and round ends, so the
# stroke scales with the glyph rather than being chosen.



class Toolbar(Gtk.Box):
    """Standard island toolbar with semantic control-group spacing."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.add_css_class("luma-toolbar")

    def append(self, child: Gtk.Widget) -> None:
        """Add a control, centred: a toolbar never stretches what it holds."""
        if child.get_valign() == Gtk.Align.FILL:
            child.set_valign(Gtk.Align.CENTER)
        super().append(child)


class SectionLabel(Gtk.Label):
    """Quiet uppercase label shared by navigation and inspector sections."""

    def __init__(self, label: str, *, variant: str = "sidebar") -> None:
        if variant not in {"sidebar", "content"}:
            raise ValueError(f"Unknown section label variant: {variant}")
        super().__init__(label=label.upper(), xalign=0)
        self.add_css_class("luma-section-label")
        if variant == "content":
            self.add_css_class("content")


class EmptyState(Adw.Bin):
    """Centred, token-owned pane state with at most two real actions.

    The original three positional arguments remain supported. Actions are
    (label, callback) pairs pointing at the application's existing command.
    """

    def __init__(self, title: str, description: str, icon_name: str, *,
                 primary: tuple[str, Callable] | None = None,
                 secondary: tuple[str, Callable] | None = None,
                 compact: bool = False) -> None:
        super().__init__(hexpand=True, vexpand=True)
        if secondary is not None and primary is None:
            raise ValueError("A secondary empty-state action needs a primary action")
        self.add_css_class("luma-empty-state")
        if compact:
            self.add_css_class("compact")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                          halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.disc = Adw.Bin(width_request=48 if compact else 84, height_request=48 if compact else 84,
                            halign=Gtk.Align.CENTER, margin_bottom=4)
        self.disc.add_css_class("luma-empty-disc")
        self.icon = Gtk.Image(icon_name=icon_name, pixel_size=24 if compact else 38,
                              halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.icon.add_css_class("luma-empty-icon")
        self.icon.update_state([Gtk.AccessibleState.HIDDEN], [True])
        self.disc.set_child(self.icon)
        content.append(self.disc)
        self.heading = Gtk.Label(justify=Gtk.Justification.CENTER, wrap=True,
                                 wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.heading.add_css_class("luma-empty-title")
        content.append(self.heading)
        self.description = Gtk.Label(justify=Gtk.Justification.CENTER, wrap=True,
                                     wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.description.add_css_class("luma-empty-description")
        attributes = Pango.AttrList()
        attributes.insert(Pango.attr_line_height_new_absolute(round((16.5 if compact else 19.5) * Pango.SCALE)))
        self.description.set_attributes(attributes)
        content.append(self.description)
        self.actions = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
            min_children_per_line=1, max_children_per_line=2,
            column_spacing=8, row_spacing=8, homogeneous=False,
            halign=Gtk.Align.CENTER, margin_top=8)
        self.actions.add_css_class("luma-empty-actions")
        self.primary_button = self._button(primary, primary=True)
        self.secondary_button = self._button(secondary, primary=False)
        self.actions.set_visible(primary is not None)
        content.append(self.actions)
        clamp = Adw.Clamp(maximum_size=260 if compact else 320,
                          tightening_threshold=260 if compact else 320)
        clamp.set_child(content)
        self.set_child(clamp)
        self.set_text(title, description)

    def _button(self, action: tuple[str, Callable] | None, *, primary: bool):
        if action is None:
            return None
        label, callback = action
        button = Gtk.Button(label=label)
        button.add_css_class("luma-button")
        if primary:
            button.add_css_class("primary")
        button.get_child().set_wrap(True)
        button.get_child().set_natural_wrap_mode(Gtk.NaturalWrapMode.NONE)
        button.get_child().set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        button.connect("clicked", lambda *_: callback())
        self.actions.append(button)
        button.get_parent().set_focusable(False)
        return button

    def set_text(self, title: str, description: str) -> None:
        self.heading.set_label(title)
        self.description.set_label(description)


class ListEmptyState(Gtk.Label):
    """A quiet collection hint. Pane-sized glyphs/actions belong to EmptyState."""

    def __init__(self, text: str) -> None:
        super().__init__(label=text, justify=Gtk.Justification.CENTER,
                         wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                         valign=Gtk.Align.START, hexpand=True)
        self.add_css_class("luma-list-empty")


# The four identity tones. A person keeps the same one everywhere they appear —
# a row in Contacts, a thread in Messages, a caller on Phone — so the tone is
# derived from the name rather than stored, and every application derives it the
# same way by asking the kit.
AVATAR_TONES = ("blue", "green", "violet", "amber")


def avatar_tone(name: str) -> str:
    """Return the identity tone for a name, stable across applications."""
    key = "".join(name.split()).casefold()
    if not key:
        return AVATAR_TONES[0]
    return AVATAR_TONES[sum(ord(character) for character in key) % len(AVATAR_TONES)]


# What an avatar shows when it has no photo and no initials worth reading.
# The names are the Prairie icon theme's (Lucide user, users, building-2).
AVATAR_GLYPHS = {"person": "avatar-default-symbolic", "group": "system-users-symbolic",
                 "business": "luma-business-symbolic"}
# Honorifics and suffixes that are not a person's initials: "Dr. Priya Raman" is PR.
_NAME_AFFIXES = frozenset({"mr", "mrs", "ms", "mx", "dr", "prof", "sir", "rev", "jr", "sr", "ii", "iii", "iv", "phd", "md"})
_PHONE_PUNCTUATION = re.compile(r"[\s().\-/]")


def _is_letter(character: str) -> bool:
    return unicodedata.category(character).startswith("L")


def _single_mark_script(character: str) -> bool:
    """Scripts whose names read from one character: Han, kana, Hangul, Thai, Lao, Khmer, Myanmar."""
    name = unicodedata.name(character, "")
    return name.startswith(("CJK", "HIRAGANA", "KATAKANA", "HANGUL", "THAI", "LAO", "KHMER", "MYANMAR", "TIBETAN", "YI "))


def avatar_initials(name: str) -> str:
    """The initials a name is recognised by, or "" when it has none worth showing.

    First letters of the first and last words ("Nick McMillan" is NM, "Nick"
    is N), ignoring punctuation, emoji and honorifics ("(Mom) ❤️" is M, "Dr.
    Priya Raman" is PR). Scripts written without initials show the name's first
    character ("李小龙" is 李). Digits and symbols alone have no initials.
    """
    words = []
    for word in name.split():
        letters = [character for character in word if _is_letter(character)]
        if letters:
            words.append((word, "".join(letters)))
    if len(words) > 1:
        trimmed = [item for item in words if item[1].casefold() not in _NAME_AFFIXES]
        words = trimmed or words
    if not words:
        return ""
    first = words[0][1][0]
    if _single_mark_script(first):
        return first
    upper = first.upper()
    first = upper if len(upper) == 1 else first
    if len(words) == 1:
        return first
    last = words[-1][1][0]
    if _single_mark_script(last):
        return first
    upper = last.upper()
    return first + (upper if len(upper) == 1 else last)


def avatar_kind(name: str, *, group: bool = False) -> str:
    """How an identity is drawn: "initials", or a "person", "business" or "group" glyph.

    A phone number, an unknown or unnamed sender, or a name of emoji alone is a
    person; a short code (3 to 8 digits, no country code) is a business such as
    a bank or a delivery service.
    """
    if group:
        return "group"
    if avatar_initials(name):
        return "initials"
    compact = _PHONE_PUNCTUATION.sub("", name.strip())
    if compact.isdigit() and 3 <= len(compact) <= 8 and not name.strip().startswith("+"):
        return "business"
    return "person"


class Avatar(Gtk.Box):
    """Shared soft-square person identity used across Luma surfaces.

    A photo when there is one; otherwise initials; otherwise a person, business
    or group glyph, never a digit or a bracket taken from the name. The tone is
    derived from the name, so a person looks the same in every application.
    """

    def __init__(
        self,
        name: str,
        *,
        large: bool = False,
        hero: bool = False,
        compact: bool = False,
        medium: bool = False,
        tone: str | None = None,
        group: bool = False,
        paintable: Gdk.Paintable | None = None,
    ) -> None:
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.add_css_class("luma-avatar")
        for enabled, css_class in ((large, "large"), (hero, "hero"), (compact, "compact"), (medium, "medium")):
            if enabled:
                self.add_css_class(css_class)
        self.add_css_class(f"tone-{tone if tone in AVATAR_TONES else avatar_tone(name)}")
        self.kind = "photo" if paintable is not None else avatar_kind(name, group=group)
        self.add_css_class(f"kind-{self.kind}")
        # Whatever fills the tile does so to land in its middle. The expand
        # stops here: GTK propagates a child's expand flag to its parent, and
        # an avatar that expands would stretch the row it sits in.
        if paintable is not None:
            self.set_overflow(Gtk.Overflow.HIDDEN)
            child: Gtk.Widget = Gtk.Picture(paintable=paintable, can_shrink=True, hexpand=True, vexpand=True,
                                            content_fit=Gtk.ContentFit.COVER)
            child.add_css_class("luma-avatar-photo")
        elif self.kind == "initials":
            child = Gtk.Label(label=avatar_initials(name), hexpand=True, vexpand=True,
                              halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            child.add_css_class("luma-avatar-initials")
        else:
            icon = AVATAR_GLYPHS[self.kind]
            display = Gdk.Display.get_default()
            if self.kind == "business" and display is not None and not Gtk.IconTheme.get_for_display(display).has_icon(icon):
                icon = AVATAR_GLYPHS["person"]  # an icon theme older than the business glyph
            child = Gtk.Image(icon_name=icon, hexpand=True, vexpand=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            child.add_css_class("luma-avatar-glyph")
        self.append(child)
        self.set_hexpand(False)
        self.set_vexpand(False)
        spoken = name.strip() or ("Group" if group else "Unknown sender")
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"{spoken} avatar"])


class FieldRow(Gtk.Box):
    """A labelled value: a fixed label column, then the value.

    Contacts reads as a column of these, and so does any inspector that states
    facts about a selection. The label column is fixed so values line up down
    the card — the card is read by scanning values, not labels.
    """

    def __init__(self, label: str, value: str, *, numeric: bool = False) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.add_css_class("luma-field-row")
        name = Gtk.Label(label=label, xalign=0)
        name.add_css_class("luma-field-label")
        self.append(name)
        self.value = Gtk.Label(label=value, xalign=0, hexpand=True, selectable=True)
        self.value.set_ellipsize(Pango.EllipsizeMode.END)
        self.value.add_css_class("luma-field-value")
        if numeric:
            self.value.add_css_class("numeric")
        self.append(self.value)
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"{label}: {value}"])


class NavigationRow(Gtk.ListBoxRow):
    """Canonical sidebar destination/source row with optional secondary state."""

    def __init__(
        self,
        title: str,
        *,
        icon_name: str | None = None,
        icon_widget: Gtk.Widget | None = None,
        trailing: str = "",
        subtitle: str = "",
        status: str | None = None,
        thumbnail: bool = False,
    ) -> None:
        super().__init__()
        self.add_css_class("luma-navigation-row")
        if thumbnail:
            self.add_css_class("thumbnail")
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8 if thumbnail else 9)
        line.add_css_class("luma-navigation-line")
        # An application that draws its own glyphs — Phone's dialler and
        # handset, say — hands the widget over rather than an icon name no
        # theme can resolve, which otherwise renders as a missing-image box.
        # In a v70 sidebar the leading mark is a round face (an avatar, or the
        # icon on a round tile) that the sidebar's sheet sizes to 34px.
        if icon_widget is not None:
            if any(icon_widget.has_css_class(role) for role in ("lumaui-avatar", "lumaui-group-face", "lumaui-presence-face")):
                self.add_css_class("lumaui-person-navigation-row")
            icon_widget.add_css_class("luma-navigation-icon")
            icon_widget.set_valign(Gtk.Align.CENTER)
            line.append(icon_widget)
        elif icon_name:
            icon = Gtk.Image(icon_name=icon_name, pixel_size=14)
            icon.add_css_class("luma-navigation-icon")
            face = Gtk.CenterBox(valign=Gtk.Align.CENTER,
                                 accessible_role=Gtk.AccessibleRole.PRESENTATION)
            face.add_css_class("luma-navigation-face")
            face.set_center_widget(icon)
            line.append(face)
        labels = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=1,
            hexpand=True,
            valign=Gtk.Align.CENTER,
        )
        title_label = Gtk.Label(label=title, xalign=0)
        title_label.set_ellipsize(Pango.EllipsizeMode.END)
        title_label.add_css_class("luma-navigation-title")
        labels.append(title_label)
        if subtitle:
            subtitle_label = Gtk.Label(label=subtitle, xalign=0)
            subtitle_label.set_ellipsize(Pango.EllipsizeMode.END)
            subtitle_label.add_css_class("luma-navigation-subtitle")
            labels.append(subtitle_label)
        line.append(labels)
        # The trailing value is live — an unread count, a number of missed
        # calls — so the label is always built and simply carries nothing when
        # there is nothing to say. A row that has to be rebuilt to change its
        # count loses selection and focus every time the count changes.
        self._title_label = title_label
        self._trailing_label = Gtk.Label(label=trailing)
        self._trailing_label.add_css_class("luma-navigation-trailing")
        self._trailing_label.set_visible(bool(trailing))
        line.append(self._trailing_label)
        if status:
            # A 6px dot, not a bar: without an alignment the box takes the
            # row's full height and the radius turns it into a tall pill.
            dot = Gtk.Box(width_request=6, height_request=6,
                          valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
            dot.add_css_class("luma-status-dot")
            dot.add_css_class(status)
            dot.set_tooltip_text(status.replace("-", " ").title())
            line.append(dot)
        self.set_child(line)
        accessible = title
        if subtitle:
            accessible += f", {subtitle}"
        if trailing:
            accessible += f", {trailing}"
        self.update_property([Gtk.AccessibleProperty.LABEL], [accessible])
        self._accessible_title = title
        self._accessible_subtitle = subtitle

    def set_trailing(self, trailing: str) -> None:
        """Say what the row currently counts, or nothing at all."""
        self._trailing_label.set_label(trailing)
        self._trailing_label.set_visible(bool(trailing))
        spoken = self._accessible_title
        if self._accessible_subtitle:
            spoken += f", {self._accessible_subtitle}"
        if trailing:
            spoken += f", {trailing}"
        self.update_property([Gtk.AccessibleProperty.LABEL], [spoken])


class ScrollView(Gtk.ScrolledWindow):
    """The kit's scrolling container for sidebars, lists and conversations.

    Its scrollbar is an overlay that appears while scrolling or when the pointer
    comes near, drawn over the trailing edge of the content at the same inset
    in every application. Nothing is reserved for it: padding belongs to the
    content inside, never to this container, so a list reaches the island's
    edge and the bar floats over it. Top fades may reserve a transparent header
    band with fade_top_start/end; fade_top_always masks even at scroll position zero.
    """

    def __init__(self, child: Gtk.Widget | None = None, *, horizontal: bool = False, fade_top: bool = True,
                 fade_bottom: bool = False, fade_top_start: int = 0,
                 fade_top_end: int | None = None, fade_top_always: bool = False, **properties: object) -> None:
        properties.setdefault("hexpand", True)
        properties.setdefault("vexpand", True)
        super().__init__(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC if horizontal else Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            has_frame=False,
            **properties,
        )
        self.add_css_class("luma-scroll-view")
        super().set_overlay_scrolling(True)
        # Scroll fades (v70 .is-scrolled, and a list clipped at a card's foot): the
        # content fades out at the edge instead of being cut. A real alpha mask,
        # so it works over any surface.
        self._fade_top, self._fade_bottom = fade_top, fade_bottom
        from . import lumaui_tokens
        end = lumaui_tokens.SCROLL["fade_top"] if fade_top_end is None else fade_top_end
        if fade_top_start < 0 or end <= fade_top_start:
            raise ValueError("top fade requires 0 <= start < end")
        self._fade_top_start, self._fade_top_end = fade_top_start, end
        self._fade_top_always = fade_top_always
        adjustment = self.get_vadjustment()
        adjustment.connect("value-changed", lambda *_: self.queue_draw())
        adjustment.connect("changed", lambda *_: self.queue_draw())
        if child is not None:
            self.set_child(child)

    def set_child(self, child: Gtk.Widget | None) -> None:
        """GTK wraps a plain child in a Viewport whose scroll policy is MINIMUM, which
        allocates the page its minimum height and squeezes every card in it; the
        kit's pages get their natural size."""
        super().set_child(child)
        viewport = self.get_child()
        if isinstance(viewport, Gtk.Viewport):
            viewport.set_vscroll_policy(Gtk.ScrollablePolicy.NATURAL)
            if self.get_policy()[0] != Gtk.PolicyType.NEVER:
                viewport.set_hscroll_policy(Gtk.ScrollablePolicy.NATURAL)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        from . import lumaui_tokens
        width, height = self.get_width(), self.get_height()
        adjustment = self.get_vadjustment()
        value, upper, page = adjustment.get_value(), adjustment.get_upper(), adjustment.get_page_size()
        top = self._fade_top and (self._fade_top_always or value > lumaui_tokens.SCROLL["scrolled_after"])
        bottom = self._fade_bottom and value + page < upper - 1
        if width <= 0 or height <= 0 or not (top or bottom):
            Gtk.ScrolledWindow.do_snapshot(self, snapshot)
            return
        gi.require_version("Gsk", "4.0")
        gi.require_version("Graphene", "1.0")
        from gi.repository import Graphene, Gsk

        def stop(offset: float, alpha: float) -> Gsk.ColorStop:
            colour = Gdk.RGBA()
            colour.red = colour.green = colour.blue = 0.0
            colour.alpha = alpha
            result = Gsk.ColorStop()
            result.offset, result.color = max(0.0, min(1.0, offset)), colour
            return result

        stops = [stop(0.0, 0.0 if top else 1.0)]
        if top:
            if self._fade_top_start:
                stops.append(stop(self._fade_top_start / height, 0.0))
            stops.append(stop(self._fade_top_end / height, 1.0))
        if bottom:
            stops.append(stop(1.0 - lumaui_tokens.SCROLL["fade_bottom"] / height, 1.0))
        stops.append(stop(1.0, 0.0 if bottom else 1.0))
        snapshot.push_mask(Gsk.MaskMode.ALPHA)
        snapshot.append_linear_gradient(Graphene.Rect().init(0, 0, width, height), Graphene.Point().init(0, 0),
                                        Graphene.Point().init(0, height), stops)
        snapshot.pop()
        Gtk.ScrolledWindow.do_snapshot(self, snapshot)
        snapshot.pop()

    def set_overlay_scrolling(self, overlay: bool) -> None:  # noqa: D401 - keeps GTK's signature
        """Kept an overlay: a reserved scrollbar gutter is what this component exists to prevent."""
        super().set_overlay_scrolling(True)


class NavigationSidebar(Gtk.Box):
    """The navigation sidebar: on the window's frame, beside the content island.

    v70 (?app=contacts, ?app=memos): the sidebar is not a boxed island. It
    stands on the frame's own surface, its rows 10px from the window edge and
    from the content island (the window body's 9px gutter plus its own 1px),
    each row 50px with a 34px round face, and each section heading carries a
    hairline beside its label. Every measurement is a `lumaui.sidebar` token.

    `variant` ("people", "destinations", "resources", "files" or "tree") sets
    the width and row metrics of one of SB1's sidebars (rows_navigation), and
    `width` ("narrow", "regular", "wide") picks a named width instead of the
    variant's. `compact=True` is deprecated: it is `variant="files"` (v70
    Viewer: 236 px, on the frame), no longer the pre-v70 178px island.
    """

    def __init__(self, *, compact: bool = False, variant: str | None = None, width: str | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("luma-navigation-sidebar")
        if compact:
            from .lumaui import deprecated
            deprecated("NavigationSidebar(compact=True)", 'NavigationSidebar(variant="files")')
            variant = variant or "files"
        self._compact = False
        self.variant = variant
        from .lumaui_tokens import SIDEBAR
        self.add_css_class("luma-sidebar-surface")
        # v70: the sidebar spans the frame from the window edge to the
        # island (.isle margin 0 8px 8px 0) and pads its own inset, so
        # the window body drops its left gutter while one is shown.
        self.set_size_request(SIDEBAR["width"] - 2 * SIDEBAR["gutter"], -1)
        self.set_margin_end(SIDEBAR["gutter"])
        self.connect("map", lambda _w: self._mark_body(True))
        self.connect("unmap", lambda _w: self._mark_body(False))
        if variant is not None or width is not None:
            from .rows_navigation import apply_sidebar_variant
            apply_sidebar_variant(self, variant or "people", width=width)
        self.set_hexpand(False)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("luma-navigation-list")
        scroll = ScrollView(self.list)
        # v71 (.cside .scr { scrollbar-width: none }): a sidebar scrolls without a bar drawn
        # over its rows, so a row's hover and selection keep their full width.
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        # v70: selecting a row (the app opening on a card further down) or focus
        # landing on it never scrolls the list; only the keyboard moving the
        # cursor brings the row it reaches into view.
        viewport = scroll.get_child()
        if isinstance(viewport, Gtk.Viewport):
            viewport.set_scroll_to_focus(False)
        self._scroll = scroll
        self.list.connect("realize", lambda lb: lb.set_adjustment(None))
        keys = Gtk.EventControllerKey()
        keys.connect("key-released", self._key_moved)
        self.list.add_controller(keys)
        self.append(scroll)
        # Anything that belongs to the sidebar as a whole rather than to one
        # destination — a connection state, an account, a quota — sits below
        # the list and stays put while the list scrolls.
        self.footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.footer.add_css_class("luma-navigation-footer")
        self.footer.set_visible(False)
        self.append(self.footer)

    def _key_moved(self, _controller, keyval: int, _code: int, _state) -> None:
        if keyval not in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Page_Up, Gdk.KEY_Page_Down, Gdk.KEY_Home, Gdk.KEY_End,
                          Gdk.KEY_KP_Up, Gdk.KEY_KP_Down):
            return
        row = self.list.get_focus_child()
        if row is None:
            return
        ok, bounds = row.compute_bounds(self.list)
        if ok:
            self._scroll.get_vadjustment().clamp_page(bounds.get_y(), bounds.get_y() + bounds.get_height())

    def _mark_body(self, shown: bool) -> None:
        body = self.get_parent()
        while body is not None and not body.has_css_class("luma-window-body"):
            body = body.get_parent()
        if body is None:
            return
        count = getattr(body, "_lumaui_frame_sidebars", 0) + (1 if shown else -1)
        body._lumaui_frame_sidebars = max(0, count)
        (body.add_css_class if count > 0 else body.remove_css_class)("lumaui-frame-sidebar")

    def set_phone_title(self, title: str | None) -> None:
        """The large title over the list, drawn only on a phone (under 560): v71 Memos'
        32/750 "Memos" on the 16 gutter (`html[data-phone] #w-memos.phone.phstack.navopen .meside::before`).
        For an app that keeps its IslandSplitView and lets the split push the page. None removes it."""
        label = getattr(self, "phone_title_label", None)
        if label is None:
            label = self.phone_title_label = Gtk.Label(xalign=0, visible=False, ellipsize=Pango.EllipsizeMode.END,
                                                       accessible_role=Gtk.AccessibleRole.HEADING)
            label.add_css_class("lumaui-list-first-title")  # the same 32/750 title as ListFirst's
            self.prepend(label)
            self.connect("notify::root", lambda *_a: self._watch_phone_title())
            self._watch_phone_title()
        label.set_label(title or "")
        self._phone_title = bool(title)
        label.set_visible(bool(title) and getattr(self, "_phone_tier", False))

    def _watch_phone_title(self) -> None:
        root = self.get_root()
        if root is None or getattr(self, "_phone_watch", None) is not None:
            return
        from .structure_adapt import window_tier
        from .structure_layers import LayerHost

        anchor = self.get_parent()
        while anchor is not None and not (isinstance(anchor, LayerHost) and anchor.layer_name == "window"):
            anchor = anchor.get_parent()

        def tiered(tier: str) -> None:
            self._phone_tier = tier == "phone"
            self.phone_title_label.set_visible(self._phone_title and self._phone_tier)

        self._phone_watch = watch = window_tier(anchor or root)
        watch.connect("tier-changed", lambda _w, tier: tiered(tier))
        watch.schedule()
        if watch._tier is not None:
            tiered(watch._tier)

    def append_header(self, widget: Gtk.Widget) -> None:
        """Add pinned content above the scrolling destinations, in call order."""
        if not hasattr(self, "_header"):
            self._header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.prepend(self._header)
        self._header.append(widget)

    def append_footer(self, widget: Gtk.Widget) -> None:
        """Pin a widget to the foot of the sidebar."""
        self.footer.append(widget)
        self.footer.set_visible(True)

    def clear(self) -> None:
        while child := self.list.get_first_child():
            self.list.remove(child)

    def append_section(self, label: str, *, action: tuple | None = None, subtitle: str | None = None,
                       hue: float | None = None, ruled: bool = False) -> None:
        """A heading, as written ("A", "This week"); `action=(icon, label, callback)` gives it its +.

        In a variant sidebar (and whenever it has an action) the heading is
        SB1's SidebarSection; otherwise the label with a hairline beside it."""
        if action is not None or self.variant is not None or subtitle or hue is not None or ruled:
            from .rows_navigation import append_section
            append_section(self, label, action=action, subtitle=subtitle, hue=hue, ruled=ruled)
            return
        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.add_css_class("luma-navigation-section")
        # v70: the label as written ("A", "This week") and a hairline
        # beside it to the sidebar's edge.
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        line.add_css_class("luma-navigation-heading-line")
        heading = Gtk.Label(label=label, xalign=0)
        heading.add_css_class("luma-navigation-heading")
        line.append(heading)
        rule = Gtk.Box(hexpand=True, valign=Gtk.Align.CENTER,
                       accessible_role=Gtk.AccessibleRole.PRESENTATION)
        rule.add_css_class("luma-navigation-heading-rule")
        line.append(rule)
        row.set_child(line)
        row.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self.list.append(row)

    def append_row(self, row: NavigationRow) -> None:
        self.list.append(row)


class StatusBar(Gtk.Box):
    """Compact island footer for totals, ranges, capacity, and sync state."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.add_css_class("luma-status-bar")

    def append_text(self, label: str, *, end: bool = False) -> Gtk.Label:
        value = Gtk.Label(label=label, xalign=0, hexpand=end)
        value.add_css_class("luma-status-text")
        if end:
            value.set_xalign(1)
        self.append(value)
        return value


class ConnectedButtonGroup(Gtk.Box):
    """A single visual control made from related adjacent buttons."""

    def __init__(self, *, tint: str | None = None, compact: bool = False,
                 context: AppContext | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=2 if compact else 0)
        self.add_css_class("luma-connected-buttons")
        if compact:
            self.add_css_class("compact")
            if (context or AppContext.from_environment()).input_mode == InputMode.TOUCH:
                self.add_css_class("touch-targets")
        if tint:
            self.add_css_class(tint)


class ColorSwatch(Gtk.ToggleButton):
    """Named selectable colour; native toggle focus, keyboard and checked state.

    The colour is document data, while the selected ring uses the current
    toolkit foreground. Pointer controls occupy 22px; touch controls use 44px.
    """

    def __init__(self, colour: str, label: str, *, context: AppContext) -> None:
        rgba = Gdk.RGBA()
        if not rgba.parse(colour):
            raise ValueError("Invalid swatch colour")
        if not label.strip():
            raise ValueError("A colour swatch needs an accessible name")
        super().__init__(tooltip_text=label)
        self.add_css_class("luma-color-swatch")
        if context.input_mode == InputMode.TOUCH:
            self.add_css_class("touch-targets")
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self._rgba = rgba
        dot = Gtk.DrawingArea(content_width=22, content_height=22,
                              halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        dot.set_draw_func(self._draw_colour)
        self.set_child(dot)
        self.connect("toggled", lambda *_: dot.queue_draw())

    def _draw_colour(self, widget, cr, width, height):
        cr.arc(width / 2, height / 2, 7, 0, 6.283185307179586)
        c = self._rgba
        cr.set_source_rgba(c.red, c.green, c.blue, c.alpha)
        cr.fill()
        if self.get_active():
            ring = self.get_color()
            cr.arc(width / 2, height / 2, 10, 0, 6.283185307179586)
            cr.set_line_width(2)
            cr.set_source_rgba(ring.red, ring.green, ring.blue, ring.alpha)
            cr.stroke()


class SaveStatus(Gtk.Label):
    """Shared quiet status presentation for autosaving editors."""

    def __init__(self) -> None:
        super().__init__(label="Saved", xalign=1)
        self.add_css_class("luma-save-status")

    def pending(self) -> None:
        self.set_label("Saving…")
        self.remove_css_class("error")

    def saved(self, label: str = "Last saved just now") -> None:
        self.set_label(label)
        self.remove_css_class("error")
        self.set_tooltip_text(None)

    def failed(self, message: str) -> None:
        self.set_label("Couldn’t save · Retrying…")
        self.add_css_class("error")
        self.set_tooltip_text(message)


class InlineRenameField(Gtk.Entry):
    """Reusable, keyboard-complete inline rename editor."""

    def __init__(
        self,
        text: str,
        *,
        commit: Callable[[str], bool],
        cancel: Callable[[], None],
    ) -> None:
        super().__init__(text=text, activates_default=False)
        self._commit = commit
        self._cancel = cancel
        self._finished = False
        self.add_css_class("luma-inline-rename")
        self.connect("activate", lambda *_: self.finish())
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: self.finish())
        self.add_controller(focus)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key_pressed)
        self.add_controller(keys)

    def begin(self) -> None:
        self.grab_focus()
        self.select_region(0, -1)

    def finish(self) -> None:
        if self._finished:
            return
        if self._commit(self.get_text()):
            self._finished = True

    def _key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
    ) -> bool:
        if (Gdk.keyval_name(keyval) or "") != "Escape":
            return False
        self._finished = True
        self._cancel()
        return True


class IconButton(Gtk.Button):
    def __init__(
        self,
        icon_name: str,
        label: str,
        *,
        context: AppContext,
        quiet: bool = False,
    ) -> None:
        super().__init__()
        self.set_child(Gtk.Image(icon_name=icon_name, pixel_size=14))
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self.add_css_class("luma-icon-button")
        self.set_tooltip_text(label)
        if quiet:
            self.add_css_class("quiet")


def evaluate_number(text: str) -> float | None:
    """Read a number, or the arithmetic somebody typed instead of one.

    Design tools accept ``120*2`` where a number is wanted, and refusing it is
    a papercut in every session. Only arithmetic is evaluated: this walks an
    expression tree rather than handing the text to the interpreter.
    """
    operators = {
        ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    }

    def walk(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = walk(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and type(node.op) in operators:
            return operators[type(node.op)](walk(node.left), walk(node.right))
        raise ValueError("not arithmetic")

    cleaned = text.strip().rstrip("%").removesuffix("px").removesuffix("pt").strip()
    try:
        return walk(ast.parse(cleaned, mode="eval"))
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, TypeError):
        return None


class NumericField(Gtk.Box):
    """A number with a unit, behaving the way a design tool's numbers do.

    Four things are table stakes in that category and all four are here:
    dragging the label scrubs the value, the arrow keys step it (Shift steps
    coarsely, Ctrl finely), arithmetic can be typed in, and a field standing
    for several objects that disagree says so rather than showing one of them
    — editing it then applies to all.
    """

    __gsignals__ = {"value-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,))}

    def __init__(self, label: str, *, unit: str = "", step: float = 1.0,
                 minimum: float | None = None, maximum: float | None = None,
                 digits: int = 0) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.add_css_class("luma-numeric")
        self._step, self._minimum, self._maximum = step, minimum, maximum
        self._digits = digits
        self._value = 0.0
        self._mixed = False
        self._drag_origin = 0.0
        self._drag_last = 0.0

        self.label = Gtk.Label(label=label)
        self.label.add_css_class("luma-numeric-label")
        self.label.set_tooltip_text(f"Drag to change {label}")
        self.append(self.label)

        self.entry = Gtk.Entry(hexpand=True, width_chars=3, xalign=0)
        self.entry.add_css_class("luma-numeric-entry")
        self.entry.connect("activate", lambda *_: self._commit())
        leave = Gtk.EventControllerFocus()
        leave.connect("leave", lambda *_: self._commit())
        self.entry.add_controller(leave)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key_pressed)
        self.entry.add_controller(keys)
        self.append(self.entry)

        if unit:
            suffix = Gtk.Label(label=unit)
            suffix.add_css_class("luma-numeric-unit")
            self.append(suffix)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        self.label.add_controller(drag)
        pointer = Gtk.EventControllerMotion()
        pointer.connect("enter", lambda *_: self.label.set_cursor_from_name("ew-resize"))
        pointer.connect("leave", lambda *_: self.label.set_cursor_from_name(None))
        self.label.add_controller(pointer)
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def get_value(self) -> float:
        return self._value

    def set_value(self, value: float) -> None:
        self._mixed = False
        self._value = self._clamp(float(value))
        self.entry.set_text(self._format(self._value))
        self.remove_css_class("mixed")

    def set_mixed(self) -> None:
        """Say the selection disagrees, rather than showing one of its answers."""
        self._mixed = True
        self.entry.set_text("Mixed")
        self.add_css_class("mixed")
        self.entry.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION],
            ["Several values; typing one applies it to everything selected"],
        )

    def _clamp(self, value: float) -> float:
        if self._minimum is not None:
            value = max(self._minimum, value)
        if self._maximum is not None:
            value = min(self._maximum, value)
        return value

    def _format(self, value: float) -> str:
        return f"{value:.{self._digits}f}" if self._digits else f"{value:.0f}"

    def _apply(self, value: float) -> None:
        self._mixed = False
        self.remove_css_class("mixed")
        self._value = self._clamp(value)
        self.entry.set_text(self._format(self._value))
        self.emit("value-changed", self._value)

    def _commit(self) -> None:
        text = self.entry.get_text().strip()
        if not text or text == "Mixed":
            return
        parsed = evaluate_number(text)
        if parsed is None:
            self.entry.set_text("Mixed" if self._mixed else self._format(self._value))
            return
        self._apply(parsed)

    def _key_pressed(self, _controller, keyval, _code, state) -> bool:
        coarse = bool(state & Gdk.ModifierType.SHIFT_MASK)
        fine = bool(state & Gdk.ModifierType.CONTROL_MASK)
        amount = self._step * (10 if coarse else 0.1 if fine else 1)
        if keyval == Gdk.KEY_Up:
            self._apply((0.0 if self._mixed else self._value) + amount)
            return True
        if keyval == Gdk.KEY_Down:
            self._apply((0.0 if self._mixed else self._value) - amount)
            return True
        return False

    def _drag_begin(self, _gesture, _x, _y) -> None:
        self._drag_origin = 0.0 if self._mixed else self._value
        self._drag_last = self._drag_origin

    def _drag_update(self, gesture, offset_x, _offset_y) -> None:
        state = gesture.get_current_event_state()
        coarse = bool(state & Gdk.ModifierType.SHIFT_MASK)
        fine = bool(state & Gdk.ModifierType.CONTROL_MASK)
        scale = self._step * (10 if coarse else 0.1 if fine else 1)
        value = self._clamp(self._drag_origin + round(offset_x / 2.0) * scale)
        if value != self._drag_last:
            self._drag_last = value
            self._apply(value)


class AlignCluster(Gtk.Box):
    """The six alignments and the two distributions, as one control.

    Anything that arranges objects needs exactly these, so they are described
    once here rather than redrawn in each application that needs them.
    """

    __gsignals__ = {"align": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    ACTIONS = (
        ("left", "Align left", "luma-align-left-symbolic"),
        ("center-x", "Align horizontal centres", "luma-align-center-symbolic"),
        ("right", "Align right", "luma-align-right-symbolic"),
        ("top", "Align top", "luma-align-top-symbolic"),
        ("center-y", "Align vertical centres", "luma-align-middle-symbolic"),
        ("bottom", "Align bottom", "luma-align-bottom-symbolic"),
        ("distribute-x", "Distribute horizontally", "luma-distribute-horizontal-symbolic"),
        ("distribute-y", "Distribute vertically", "luma-distribute-vertical-symbolic"),
    )

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.add_css_class("luma-align-cluster")
        self.buttons: dict[str, Gtk.Button] = {}
        for index, (action, label, icon_name) in enumerate(self.ACTIONS):
            if index == 6:
                rule = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
                rule.add_css_class("luma-align-rule")
                self.append(rule)
            button = Gtk.Button()
            button.set_child(Gtk.Image(icon_name=icon_name, pixel_size=14))
            button.add_css_class("luma-icon-button")
            button.set_tooltip_text(label)
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            button.connect("clicked", lambda _b, value=action: self.emit("align", value))
            self.append(button)
            self.buttons[action] = button



def command_menu_model(registry: CommandRegistry, application: Gio.Application) -> Gio.Menu:
    """Build the application menu the toolkit's identity button shows.

    The toolkit draws the identity in the corner of every Luma window and takes
    its menu from the application, so the registry's groups become a GMenuModel
    and each command becomes an `app.` action. Menus, shortcuts and semantics
    then all drive the same command, from the same place.
    """
    menu = Gio.Menu()
    for group in registry.visible_groups():
        section = Gio.Menu()
        for command in group.commands:
            name = _action_name(command.id)
            if application.lookup_action(name) is None:
                action = Gio.SimpleAction.new(name, None)
                def activate(_action, _parameter, cid=command.id):
                    # Application actions must follow the active document window,
                    # not the first window that happened to register the ID.
                    window = application.get_active_window()
                    while window is not None:
                        owner = getattr(window, "commands", None)
                        if owner is not None:
                            try: owner.invoke(cid)
                            except KeyError: pass
                            return
                        window = window.get_transient_for()
                action.connect("activate", activate)
                application.add_action(action)
            if group.in_menu:
                item = Gio.MenuItem.new(command.label, f"app.{name}")
                if command.icon:
                    item.set_attribute_value("icon", GLib.Variant.new_string(command.icon))
                section.append_item(item)
            if command.shortcut:
                keys = "".join(f"<{part}>" if part in {"Ctrl", "Shift", "Alt", "Super"} else part
                               for part in command.shortcut)
                application.set_accels_for_action(f"app.{name}", [keys])
        if section.get_n_items():
            menu.append_section(group.label or None, section)
    return menu


def _action_name(command_id: str) -> str:
    return command_id.replace(".", "-")



class MobileHeader(Gtk.Box):
    """The handheld title row: a leading control, the title, a trailing control.

    On a handheld the shell owns the surface chrome and the application's
    window has no title bar of its own, so a page carries this row instead —
    a back or menu control on the left, what the page is in the middle, one
    action on the right. Both slots are always laid out so the title sits in
    the same place whether or not a control is present.
    """

    def __init__(
        self,
        title: str,
        *,
        context: AppContext,
        leading_icon: str | None = None,
        leading_label: str = "Back",
        trailing_icon: str | None = None,
        trailing_label: str = "More",
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.add_css_class("luma-mobile-header")
        self.leading = self._slot(leading_icon, leading_label, context)
        self.append(self.leading)
        self.title_label = Gtk.Label(label=title, hexpand=True)
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_label.add_css_class("luma-mobile-title")
        self.append(self.title_label)
        self.trailing = self._slot(trailing_icon, trailing_label, context)
        self.append(self.trailing)

    @staticmethod
    def _slot(icon_name: str | None, label: str, context: AppContext) -> Gtk.Widget:
        if icon_name:
            button = IconButton(icon_name, label, context=context, quiet=True)
            button.add_css_class("luma-mobile-slot")
            return button
        spacer = Gtk.Box()
        spacer.add_css_class("luma-mobile-slot")
        return spacer


class _WindowState:
    def __init__(self, app_id: str) -> None:
        state_home = Path(
            os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")
        )
        self.path = state_home / "luma" / "windows" / f"{app_id}.json"

    def load(self) -> dict[str, object]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def save(self, value: dict[str, object]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)


#: Tiling Shell's window memory (tiling-shell patch 0010) remembers where each
#: application's windows live, per monitor setup, and puts them back — tiled,
#: maximized or free. It is the system's one window memory. When it is in
#: charge the kit does not also restore a size, because two memories placing
#: the same window is what makes a window jump on open.
_SHELL_MEMORY_SCHEMA = "org.gnome.shell.extensions.tilingshell"
_SHELL_MEMORY_KEY = "luma-remember-window-positions"


def _shell_remembers_windows() -> bool:
    if os.environ.get("LUMA_WINDOW_MEMORY") == "app":
        return False
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(_SHELL_MEMORY_SCHEMA, True) if source is not None else None
    if schema is None or not schema.has_key(_SHELL_MEMORY_KEY):
        return False
    try:
        return Gio.Settings(schema_id=_SHELL_MEMORY_SCHEMA).get_boolean(_SHELL_MEMORY_KEY)
    except GLib.Error:
        return False


def _current_screen(display: Gdk.Display | None) -> window_policy.Screen:
    """The monitor a window is about to open on, in logical pixels.

    GTK reports monitor geometry already divided by the monitor's scale, which
    is the same unit a window is sized in, so a 1920x1200 panel at 1.25 is a
    1536x960 screen and needs no arithmetic of its own. Before a window has a
    surface there is no monitor to ask for, so the first one stands in; a
    remembered window is placed by the Shell's memory anyway, and Mutter's
    work-area constraint corrects the rest.
    """
    width, height = 1920, 1080
    if display is not None:
        monitors = display.get_monitors()
        monitor = monitors.get_item(0) if monitors.get_n_items() else None
        if monitor is not None:
            geometry = monitor.get_geometry()
            if geometry.width > 0 and geometry.height > 0:
                width, height = geometry.width, geometry.height
    return window_policy.Screen(width, height, _shelf_edge())


def _shelf_edge() -> str:
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup("org.project_luma.shell-state", True) if source is not None else None
    if schema is None or not schema.has_key("shelf-edge"):
        return "bottom"
    try:
        return Gio.Settings(schema_id="org.project_luma.shell-state").get_string("shelf-edge")
    except GLib.Error:
        return "bottom"


class AppWindow(Adw.ApplicationWindow):
    """Canonical Luma window: LumaUI's frame, title row, identity and islands.

    LumaUI draws the whole window (ADR-052): the frame and its shadow, the 42 px
    title row, the identity pill in the top-left corner with the kit's menu,
    the connected window controls, the 9 px gutter and the islands, from its
    own toolkit sheets (`window_frame`). It does not depend on Luma's patched
    libadwaita, and on a patched system nothing is drawn twice: the library
    builds its own identity only for a header bar that has none. The window
    also opens at the size the shared rule gives it.

    Two window promises are kept here for every Luma application (ADR-042):
    the window opens at a size that matches every other Luma window and is
    never folded into its own narrow layout on a desktop, and its title row —
    the close, move and resize control — cannot be taken away while it is a
    framed desktop window. Where the person has moved, resized or tiled a
    window, Tiling Shell's window memory puts it back and the kit stands aside.
    """

    #: Set while the kit is applying the shared opening-size rule, so
    #: `set_default_size` can tell the rule's own call from an application's.
    #: A class attribute because `__init__` sizes the window before any
    #: instance state exists.
    _applying_opening_size = False

    def __init__(
        self,
        *,
        application: Adw.Application,
        app_id: str,
        title: str,
        icon_name: str,
        commands: CommandRegistry,
        subtitle: str = "",
        default_width: int = 820,
        default_height: int = 560,
        minimum_width: int = 460,
        minimum_height: int = 380,
        geometry_scope: str = "",
        size_class: str = "auto",
        narrow_width: int = 0,
        identity_menu_variant: str = "app",
        identity_icon: bool = True,
    ) -> None:
        super().__init__(application=application, title=title)
        # The kit's tokens and toolkit sheets, before anything is styled.
        install_appkit()
        self.context = AppContext.from_environment()
        from .app_updates import refresh_native_action, window_commands
        commands = window_commands(self, commands)
        self.commands = commands
        self._state = _WindowState(app_id)
        self._geometry_app_id = app_id
        self._geometry_scope = geometry_scope
        self._geometry_state = self._scoped_geometry_state(geometry_scope)
        self._geometry_minimum = (minimum_width, minimum_height)
        # What the application asked for is its shape, not its opening size:
        # one shared rule turns shape into pixels for every Luma window
        # (ADR-042), so a desk of windows looks like a set.
        self._geometry_default = (default_width, default_height)
        self._size_class = size_class
        # Widths at which this window folds its own layout away. Declared, or
        # learned from the breakpoints the application adds after construction.
        self._narrow_conditions: list[str] = []
        self._declared_narrow = max(0, int(narrow_width))
        self._opening_size_applied = False
        self._maximize_source = 0
        self._title = title
        self.set_size_request(minimum_width, minimum_height)
        saved = self._state.load()
        # Anything the application asked to be remembered, kept apart from the
        # geometry so saving one does not lose the other.
        self._remembered = {
            key: value for key, value in saved.items()
            if key not in {"width", "height", "maximized"}
        }
        geometry = {} if _shell_remembers_windows() else self._geometry_state.load()
        if "width" in geometry or "height" in geometry:
            width = _bounded_int(geometry.get("width"), minimum_width, 4096, default_width)
            height = _bounded_int(geometry.get("height"), minimum_height, 2160, default_height)
            self.set_default_size(width, height)
            self._opening_size_applied = True
        else:
            # Nothing remembered here: the size is decided at `present`, once
            # the application has added its breakpoints and a monitor is known.
            self.set_default_size(default_width, default_height)
            self._opening_size_applied = False
        # Windowed keeps the frame. Handheld fullscreen has none to keep: the
        # shell owns that surface's chrome, and a title row would be a second.
        decorated = self.context.presentation is PresentationMode.WINDOWED
        self.set_decorated(decorated)
        # One native binding owns the window's compositor backdrop. It follows
        # the same accessibility and capability policy as the C AppKit, and
        # becomes a quiet opaque treatment when the protocol is unavailable.
        self._surface_backdrop = LumaUI.SurfaceBackdrop.new(self)
        appearance_policy = _appearance_policies.get(self.get_display())
        if appearance_policy is not None:
            def update_backdrop_capability(*_args: object) -> None:
                available = self._surface_backdrop.get_available()
                appearance_policy.set_capabilities(available, available, available)
            self._surface_backdrop.connect(
                "notify::available", update_backdrop_capability
            )
            update_backdrop_capability()
        if bool(geometry.get("maximized")):
            self._maximize_source = GLib.idle_add(self._restore_maximized)

        # The identity's name and icon come from the desktop entry, so the
        # corner never disagrees with the dock; `icon_name` is the fallback
        # when no desktop entry is installed (a preview, a test run). The
        # application menu model still carries the commands' `app.` actions
        # and accelerators.
        application.set_menubar(command_menu_model(commands, application))
        refresh_native_action(self)

        view = Adw.ToolbarView()
        view.add_css_class("luma-window-toolbar-view")
        self.title_bar = Adw.HeaderBar()
        self.title_bar.set_show_title(False)
        self.title_bar.set_visible(decorated)
        view.add_top_bar(self.title_bar)
        self.body = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            hexpand=True,
            vexpand=True,
        )
        self.body.add_css_class("luma-window-body")
        # The sheet layer: above the content, below the title bar, so a sheet
        # (the save prompt) hangs from the title bar and dims only this
        # window's content. Empty and untargetable until a sheet is presented.
        self.sheet_layer = Gtk.Overlay(hexpand=True, vexpand=True)
        self.sheet_layer.add_css_class("luma-sheet-layer")
        self.sheet_layer.set_child(self.body)
        # The frame, the title row's classes and the identity: LumaUI's own.
        self.identity = window_frame.WindowIdentity(
            self, Menu(commands, variant=identity_menu_variant, keep_parent=True,
                       phone_placement="popover"), icon_name=icon_name, icon=identity_icon)
        window_frame.own_window_frame(self, self.title_bar, self.sheet_layer, self.identity)
        # v70 folds the window controls away at phone width (.win.phone .wctl);
        # the title row stays, so the window still moves, and closes from its
        # window menu (right-click) and the keyboard.
        # A breakpoint, not a size watch: libadwaita applies it inside the same
        # allocation, so folding them never leaves the window waiting for a
        # second layout. Added past `add_breakpoint`, which learns only the
        # application's own narrow layouts.
        # Only on a phone itself (Nick, 26 Sep): a narrow desktop window, tiled or
        # small like Calculator, always keeps minimise, maximise and close.
        self.window_controls = getattr(self, "_lumaui_window_controls", None)
        from .lumaui import mobile_form_factor
        if self.window_controls is not None and mobile_form_factor():
            from .lumaui_tokens import WINDOW
            fold = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(
                f"max-width: {WINDOW['controls_hide_max_width']}px"))
            fold.add_setter(self.window_controls, "visible", False)
            Adw.ApplicationWindow.add_breakpoint(self, fold)
        self._sheet: Gtk.Widget | None = None
        self._sheet_parked: list[tuple[Gtk.EventController, Gtk.PropagationPhase]] = []
        # The window's layer host sits under the title row: a dialog, a drawer
        # or a toast dims and covers the window below its title bar (v70), and
        # the frame, identity and controls stay as they are.
        from .structure_layers import LayerHost
        self.layer_host = LayerHost(self.sheet_layer, name="window")
        view.set_content(self.layer_host)
        self.set_content(view)
        self.set_identity_subtitle(subtitle)
        self.connect("close-request", self._remember_geometry)
        self.connect("notify::visible-dialog", self._prepare_visible_dialog)
        # v71 on a phone (the device, not a narrow window): no title bar. The app's name,
        # icon and window controls go; the content island is the screen and holds the
        # safe areas (44 under the clock); the title row's own controls float as glass
        # just under the clock (luma-next-71 3194-3215). `set_phone_bleed` lets an app
        # that paints the top (Weather's sky) run under the clock and inset itself.
        self.phone_device = mobile_form_factor()
        if self.phone_device:
            self.add_css_class("lumaui-phone-device")
            view.set_extend_content_to_top_edge(True)
            self.identity.set_visible(False)
            if self.window_controls is not None:
                self.window_controls.set_visible(False)
            # The row floats over the page (v71 .tbar pointer-events: none): taps reach the
            # title island and the page under it. Navigation lives in the island, not here.
            self.title_bar.set_can_target(False)
        # v71 tiers: the window carries lumaui-phone / -compact / -regular, and
        # `tier_watch` ("tier-changed") tells the app when to redraw in the other shape.
        from .structure_adapt import window_tier
        self.tier_watch = window_tier(self)
        # The one guarantee: on a desktop this row is the close control, so
        # nothing — a breakpoint, a page change, a mode switch — may take it
        # away. See `_keep_close_affordance`.
        self._chrome_corrections = 0
        self.title_bar.connect("notify::visible", self._keep_close_affordance)
        self.connect("notify::decorated", self._keep_close_affordance)
        # Everything the window listens to up to here is the toolkit's and the
        # kit's. Controllers added after this line are the application's, and
        # a sheet parks them while it is open so its keys reach nothing else.
        self._kit_controllers = {id(controller) for controller in self._controllers()}
        # The platform owns the sheet layer's behaviour (parking, focus return),
        # the same for C and Python windows; this window only built the layer.
        self._native_sheet_layer = hasattr(LumaUI, "window_set_sheet_layer")
        if self._native_sheet_layer:
            LumaUI.window_set_sheet_layer(self, self.sheet_layer, self.body, self.title_bar)

    def _controllers(self) -> list[Gtk.EventController]:
        model = self.observe_controllers()
        return [model.get_item(index) for index in range(model.get_n_items())]

    @property
    def status_inset(self) -> int:
        """The room the phone's clock takes at the top (44 on a phone, 0 elsewhere): what a
        bleeding page (`set_phone_bleed`) pads itself by."""
        from .lumaui_tokens import PHONE_FRAME
        return PHONE_FRAME["status"] if self.phone_device else 0

    def set_phone_bleed(self, bleed: bool = True) -> None:
        """On a phone, let the content run under the clock (v71 Weather, Photos, Maps paint
        to the bezel); the page insets itself by `status_inset`. No effect on a computer."""
        (self.add_css_class if bleed else self.remove_css_class)("lumaui-bleed")

    @property
    def tier(self) -> str:
        """v71's tier for this window: "phone" (<560), "compact" (560-900) or "regular"."""
        return self.tier_watch.tier

    @property
    def presented_sheet(self) -> Gtk.Widget | None:
        """The sheet hanging from this window's title bar, if one is."""
        if self._native_sheet_layer:
            return LumaUI.window_get_presented_sheet(self)
        return self._sheet

    def present_sheet(self, sheet: Gtk.Widget, keys: Gtk.EventController | None = None) -> None:
        """Hang a sheet from the title bar and make the rest of the window wait.

        Only this window's content dims and stops taking focus and pointer;
        other windows stay usable. The application's own key handling on the
        window, and its accelerators, are parked until the sheet is dismissed,
        so a sheet's keys never reach the document behind it.
        """
        if self._native_sheet_layer:
            LumaUI.window_present_sheet(self, sheet, keys)
            return
        if self._sheet is not None:
            self.dismiss_sheet()
        self._sheet = sheet
        self.sheet_layer.add_overlay(sheet)
        self.sheet_layer.set_measure_overlay(sheet, False)
        self.sheet_layer.set_clip_overlay(sheet, False)
        self.body.set_can_focus(False)
        self.body.set_can_target(False)
        self.body.update_state([Gtk.AccessibleState.HIDDEN], [True])
        self.title_bar.set_can_focus(False)
        for controller in self._controllers():
            name = controller.get_name() or ""
            if id(controller) in self._kit_controllers and name != "gtk-application-shortcuts":
                continue
            if isinstance(controller, (Gtk.EventControllerKey, Gtk.ShortcutController)):
                self._sheet_parked.append((controller, controller.get_propagation_phase()))
                controller.set_propagation_phase(Gtk.PropagationPhase.NONE)
        # The sheet's own keys listen on the window, ahead of everything under
        # it, so they work wherever focus is when the sheet opens.
        self._sheet_keys = keys
        if keys is not None:
            keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            self.add_controller(keys)

    def dismiss_sheet(self) -> None:
        """Take the sheet down, give the window back, and return focus."""
        if self._native_sheet_layer:
            LumaUI.window_dismiss_sheet(self)
            return
        sheet, self._sheet = self._sheet, None
        keys, self._sheet_keys = getattr(self, "_sheet_keys", None), None
        if keys is not None:
            self.remove_controller(keys)
        if sheet is not None and sheet.get_parent() is self.sheet_layer:
            self.sheet_layer.remove_overlay(sheet)
        for controller, phase in self._sheet_parked:
            controller.set_propagation_phase(phase)
        self._sheet_parked = []
        self.body.set_can_focus(True)
        self.body.set_can_target(True)
        self.body.update_state([Gtk.AccessibleState.HIDDEN], [False])
        self.title_bar.set_can_focus(True)

    def _keep_close_affordance(self, *_args) -> None:
        """Put the title row back if something hid it on a desktop.

        An application's narrow layout is right to fold a sidebar away and
        right to hide its title row on a handheld, where the shell draws the
        surface chrome. On a desktop the row carries the window controls, and a
        window that opens or is resized narrow would be left with no way to
        close, move or resize it — the Phone and Messages bug. Enforced here
        because every Luma window is an `AppWindow`, so no application has to
        remember, and none can opt out by accident. A window that gives up its
        frame on purpose and draws its own controls (a sticky note) is not
        decorated and answers for its own close control.
        """
        if not window_policy.close_affordance_required(
            desktop=self.context.presentation is PresentationMode.WINDOWED,
            decorated=self.get_decorated(),
        ):
            return
        if self.title_bar.get_visible():
            return
        self.title_bar.set_visible(True)
        self._chrome_corrections += 1
        if self._chrome_corrections == 1:
            # Once per window, for Luma Vitals: enough to find the application
            # whose breakpoint is wrong, quiet enough to leave on. Never at the
            # cost of the guarantee itself, which is already kept above.
            width = self.get_width() or self.get_default_size()[0]
            try:
                GLib.log_structured(
                    "luma-appkit",
                    GLib.LogLevelFlags.LEVEL_WARNING,
                    {
                        "MESSAGE": (
                            f"{self._geometry_app_id} hid its title row at {width}px on a "
                            "desktop; kept it so the window can still be closed"
                        ),
                        "SYSLOG_IDENTIFIER": "luma-appkit",
                        "LUMA_VITALS_KIND": "window-close-affordance-kept",
                        "LUMA_VITALS_UNIT": self._geometry_app_id,
                        "LUMA_APPKIT_WINDOW_WIDTH": str(width),
                    },
                )
            except (AttributeError, TypeError, GLib.Error):
                pass

    def add_breakpoint(self, breakpoint_: Adw.Breakpoint) -> None:
        """Add a breakpoint, and learn the width at which it applies.

        Applications declare their narrow layouts here; reading the conditions
        as they arrive is what lets the shared rule open a window wider than
        its own narrow layout without every application repeating the number.
        """
        condition = breakpoint_.get_condition()
        if condition is not None:
            self._narrow_conditions.append(condition.to_string())
        super().add_breakpoint(breakpoint_)

    def declare_narrow_width(self, width: int) -> None:
        """Say where this window's narrow layout begins.

        For a window whose breakpoints live on an `AdwBreakpointBin` inside it,
        which `add_breakpoint` never sees.
        """
        self._declared_narrow = max(self._declared_narrow, int(width))

    @property
    def narrow_width(self) -> int:
        """The first width at which this window keeps its full layout."""
        return max(
            self._declared_narrow,
            window_policy.narrow_width(self._narrow_conditions),
        )

    def opening_size(self) -> tuple[int, int]:
        """The size this window opens at with nothing remembered for it."""
        return window_policy.opening_size(
            screen=_current_screen(self.get_display()),
            default=self._geometry_default,
            minimum=self._geometry_minimum,
            narrow=self.narrow_width,
            declared=self._size_class,
            desktop=self.context.presentation is PresentationMode.WINDOWED,
        )

    def present(self) -> None:
        # By now the application has added its breakpoints, so the window knows
        # how narrow it is allowed to be.
        self._apply_opening_size()
        super().present()

    def set_visible(self, visible: bool) -> None:
        if visible:
            self._apply_opening_size()
        super().set_visible(visible)

    def _apply_opening_size(self) -> None:
        if self._opening_size_applied:
            return
        if self.context.presentation is not PresentationMode.WINDOWED:
            # A handheld surface is sized by the shell, which shows it full
            # screen; the desktop opening rule has nothing to say about it.
            self._opening_size_applied = True
            return
        width, height = self.opening_size()
        self._applying_opening_size = True
        try:
            self.set_default_size(width, height)
        finally:
            self._applying_opening_size = False
        self._opening_size_applied = True

    def set_default_size(self, width: int, height: int) -> None:
        """A size an application sets for itself is its own business.

        A window that computes its own size as it goes — an assistant that
        widens when a panel opens — keeps doing so: the shared rule decides
        only the size a window opens at when nothing else has.
        """
        if not self._applying_opening_size:
            self._opening_size_applied = True
        super().set_default_size(width, height)

    def _prepare_visible_dialog(self, *_args) -> None:
        dialog = self.get_visible_dialog()
        if isinstance(dialog, Adw.AlertDialog):
            dialog.add_css_class("luma-controls-quiet")

    def _scoped_geometry_state(self, scope: str) -> _WindowState:
        if scope and (not scope.isascii() or not all(c.isalnum() or c in "-_" for c in scope)):
            raise ValueError("Geometry scope must be an ASCII identifier")
        return _WindowState(self._geometry_app_id + ("." + scope if scope else ""))

    def _restore_maximized(self) -> bool:
        self._maximize_source = 0
        self.maximize()
        return GLib.SOURCE_REMOVE

    def set_geometry_scope(self, scope: str, *, default_width: int, default_height: int) -> None:
        """Restore a separate surface's geometry without overwriting the editor.

        An empty scope is the existing application/editor record. A named scope
        can remember a welcome or utility surface independently in the same
        window. Call when intentionally switching surfaces, not during layout.
        """
        state = self._scoped_geometry_state(scope)
        if scope == self._geometry_scope:
            return
        self._save_state()
        if self._maximize_source:
            GLib.source_remove(self._maximize_source)
            self._maximize_source = 0
        self._geometry_scope = scope
        self._geometry_state = state
        geometry = {} if _shell_remembers_windows() else state.load()
        if self.context.presentation is PresentationMode.WINDOWED:
            self.unmaximize()
        self._geometry_default = (default_width, default_height)
        if "width" in geometry or "height" in geometry:
            self.set_default_size(
                _bounded_int(geometry.get("width"), self._geometry_minimum[0], 4096, default_width),
                _bounded_int(geometry.get("height"), self._geometry_minimum[1], 2160, default_height),
            )
        else:
            # A surface with nothing remembered opens by the shared rule, like
            # any other Luma window.
            self._opening_size_applied = False
            self._apply_opening_size()
        if bool(geometry.get("maximized")):
            self._maximize_source = GLib.idle_add(self._restore_maximized)

    def set_identity_subtitle(self, subtitle: str) -> None:
        """Say what the window currently holds — the open document.

        The corner shows the application's name, as every Luma window does; what
        this window holds is the window title, which is what the shell, the
        switcher and assistive technology read.
        """
        self.set_title(f"{subtitle} — {self._title}" if subtitle else self._title)

    def set_leading(self, widget: Gtk.Widget | None) -> None:
        """Put one control in the title row just after the identity (v70 .tbar).

        What belongs there: the SidebarToggle (v70 lSideBtn, "just after the
        app's name"), or a phone's way back to the list. One slot; a new
        widget replaces the last, None empties it. The row keeps v70's 9 px
        between the identity and it.
        """
        self._swap_title_slot("_title_leading", widget, self.title_bar.pack_start)

    def set_title_content(self, widget: Gtk.Widget | None, *, centred: bool = False) -> None:
        """Give the title row what this window is about (v70's title rows).

        By default it follows the identity and anything leading, left to right,
        as a document's header does in the creative suite (the document's name
        and state, v70 .sutop). `centred=True` centres it on the window, as
        Viola's page title is (v70 #v-top). None empties the slot. The window
        controls always keep their place at the right.
        """
        if getattr(self, "_title_centred", None) is not None:
            self.title_bar.set_title_widget(None)
            self.title_bar.set_show_title(False)
            self._title_centred = None
        if centred:
            self._swap_title_slot("_title_content", None, self.title_bar.pack_start)
            if widget is not None:
                self.title_bar.set_title_widget(widget)
                self.title_bar.set_show_title(True)
                self._title_centred = widget
            return
        self._swap_title_slot("_title_content", widget, self.title_bar.pack_start)

    def set_trailing(self, widget: Gtk.Widget | None) -> None:
        """Put the window's own actions at the right of the title row, before
        the window controls (v70 .sutopr: people, Share, Present, More)."""
        self._swap_title_slot("_title_trailing", widget, self.title_bar.pack_end)

    def _swap_title_slot(self, slot: str, widget: Gtk.Widget | None, pack) -> None:
        old = getattr(self, slot, None)
        if old is not None and old.get_parent() is not None:
            self.title_bar.remove(old)
        setattr(self, slot, widget)
        if widget is None:
            return
        widget.set_valign(Gtk.Align.CENTER)
        # Start-side slots keep their order (identity, leading, content)
        # whatever order the application fills them in.
        if pack == self.title_bar.pack_start:
            for name in ("_title_leading", "_title_content"):
                other = getattr(self, name, None)
                if other is not None and other.get_parent() is not None:
                    self.title_bar.remove(other)
            for name in ("_title_leading", "_title_content"):
                other = getattr(self, name, None)
                if other is not None:
                    self.title_bar.pack_start(other)
        else:
            pack(widget)

    def set_body(self, widget: Gtk.Widget) -> None:
        while child := self.body.get_first_child():
            self.body.remove(child)
        # Application content is the window's flexible region. Requiring this
        # at the shared boundary prevents individual apps from accidentally
        # shrinking to their natural size and leaving unused window space.
        widget.set_hexpand(True)
        widget.set_vexpand(True)
        self.body.append(widget)

    def _remember_geometry(self, _window: Gtk.Window) -> bool:
        self._save_state()
        return False

    def _save_state(self) -> None:
        width, height = self.get_allocated_width(), self.get_allocated_height()
        geometry = {"width": width, "height": height, "maximized": self.is_maximized()}
        record = self._state.load() if self._geometry_scope else {}
        record.update(self._remembered)
        if self._geometry_scope:
            self._geometry_state.save(geometry)
        else:
            record.update(geometry)
        self._state.save(record)

    def remember(self, key: str, value: object) -> None:
        """Keep one small piece of this window's own state until next time.

        The window already remembers its size; this is for what it was showing
        — the open conversation, the last image — so a person returns to the
        window they left rather than to a blank one. It is written beside the
        geometry, so it is one file and one write.
        """
        if self._remembered.get(key) == value:
            return
        self._remembered[key] = value
        self._save_state()

    def recall(self, key: str, default: object = None) -> object:
        """Give back what this window was showing when it was last closed."""
        return self._remembered.get(key, default)


def _bounded_int(value: object, minimum: int, maximum: int, fallback: int) -> int:
    if not isinstance(value, int):
        return fallback
    return max(minimum, min(maximum, value))
