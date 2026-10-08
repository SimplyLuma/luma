# SPDX-License-Identifier: Apache-2.0
"""LumaUI core: the API level, style and icon installation, motion and width.

LumaUI is Luma's application toolkit (Python: `luma_appkit`, to be renamed
`lumaui` with a compatibility alias). Applications say *what* (a count that
needs attention, a toast for something deleted, a question about something
that can't come back); the kit owns *how* it looks, moves and adapts. Every
part reads its colours, metrics and motion from config/shared/design-tokens.json
through the generated sheets and `lumaui_tokens`, so a design change reaches
every application with no application edits.

The kit runs on stock GTK 4 and libadwaita. Nothing here depends on Luma's
GTK or libadwaita patches, or on a Luma-only system path: stylesheets and
icons resolve beside the package first, with the system locations as a
fallback, so the same package can ship inside a Luma runtime on another
distribution.

API level and compatibility (docs/developer/kit/lumaui-principles.md):
`LUMAUI_API_LEVEL` rises by one whenever a public name or a keyword is added.
Within a level nothing is removed or renamed. A name that is going away is
first marked deprecated (it keeps working and warns once) for at least one
level and one monthly release before it is removed.
"""
from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from . import lumaui_tokens as tokens  # noqa: E402

#: The public LumaUI API level. See the module docstring for the policy.
# 2: the F2, F3 and F0 parts; hues (hue_class, person_hue); Card(recessed=); PersonAvatar(hue=).
LUMAUI_API_LEVEL = 2

_PACKAGE = Path(__file__).resolve().parent
_portable_installed: set[int] = set()
_warned: set[str] = set()


def deprecated(name: str, instead: str) -> None:
    """Warn once that `name` is going away, and what to use instead."""
    if name not in _warned:
        _warned.add(name)
        warnings.warn(f"{name} is deprecated in LumaUI; use {instead}", DeprecationWarning, stacklevel=3)


def find_asset(filename: str, override_variable: str | None = None) -> str | None:
    """A kit file, beside the package first and the system copy last."""
    candidates = [os.environ.get(override_variable, "") if override_variable else ""]
    candidates += [str(_PACKAGE.parent / filename), str(_PACKAGE.parent.parent / "ui" / filename),
                   f"/usr/share/luma-appkit/{filename}"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


# ── family sheets ─────────────────────────────────────────────────────────
# A part family's own stylesheet (luma-appkit-<family>.css, beside the base
# sheet) loads after the base at the same priority, so it can refine base
# rules; a variant (luma-appkit-<family>-dark|frost|glass|high-contrast.css)
# loads after it for that appearance. Families are found on disk, so a new
# family needs no loader change.
_NOT_FAMILIES = {"base", "tokens", "dark", "frost", "glass", "high", "contrast", "ui"}
_VARIANTS = ("dark", "frost", "glass", "high-contrast")
_family_providers: dict[int, dict[str, tuple[Gtk.CssProvider, Gtk.CssProvider]]] = {}


def family_sheets() -> dict[str, dict[str, str]]:
    """{family: {"": base path, "<variant>": path}} for every family sheet beside the base sheet."""
    import re

    base = find_asset("luma-appkit-base.css", "LUMA_APPKIT_BASE_PATH")
    if not base:
        return {}
    found: dict[str, dict[str, str]] = {}
    pattern = re.compile(r"^luma-appkit-([a-z][a-z0-9_]*)(?:-(dark|frost|glass|high-contrast))?\.css$")
    for path in sorted(Path(base).parent.glob("luma-appkit-*.css")):
        match = pattern.match(path.name)
        if not match or match.group(1) in _NOT_FAMILIES or path.name.endswith("-tokens.css"):
            continue
        found.setdefault(match.group(1), {})[match.group(2) or ""] = str(path)
    return {family: paths for family, paths in found.items() if "" in paths}


def load_family_sheets(display: Gdk.Display, appearance: str) -> None:
    """Load (or, on an appearance change, reload the variants of) every family sheet.

    `appearance` is "light", "dark", "frost", "glass" or "high-contrast".
    """
    key = hash(display)
    providers = _family_providers.setdefault(key, {})
    for family, paths in family_sheets().items():
        if family not in providers:
            base, variant = Gtk.CssProvider(), Gtk.CssProvider()
            base.load_from_path(paths[""])
            Gtk.StyleContext.add_provider_for_display(display, base, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            Gtk.StyleContext.add_provider_for_display(display, variant, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            providers[family] = (base, variant)
        variant_path = paths.get(appearance)
        if variant_path:
            providers[family][1].load_from_path(variant_path)
        else:
            providers[family][1].load_from_string("")


def install(display: Gdk.Display | None = None) -> None:
    """Load the kit's stylesheets and icons for `display`, once.

    On a Luma system this is install_appkit(), which also follows the desktop's
    surface treatment. Where the Luma appearance library is absent (stock GTK,
    another distribution, a test machine) the same token and component sheets
    are loaded directly and follow libadwaita's dark and high-contrast state.
    """
    display = display or Gdk.Display.get_default()
    if display is None:
        return
    try:
        from .widgets import install_appkit
    except (ImportError, ValueError):
        _install_portable(display)
    else:
        install_appkit()
    from . import icons
    icons.ensure_icons(display)


#: The name the package exports: `luma_appkit.install_lumaui()`.
install_lumaui = install


def _install_portable(display: Gdk.Display) -> None:
    if hash(display) in _portable_installed:
        return
    _portable_installed.add(hash(display))
    tokens_provider, component_provider, base_provider = Gtk.CssProvider(), Gtk.CssProvider(), Gtk.CssProvider()
    Gtk.StyleContext.add_provider_for_display(display, tokens_provider, Gtk.STYLE_PROVIDER_PRIORITY_THEME)
    Gtk.StyleContext.add_provider_for_display(display, base_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    Gtk.StyleContext.add_provider_for_display(display, component_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    base = find_asset("luma-appkit-base.css", "LUMA_APPKIT_BASE_PATH")
    if base:
        base_provider.load_from_path(base)

    def load(*_args: object) -> None:
        dark, high_contrast = _desktop_appearance()
        suffix = "-high-contrast" if high_contrast else "-dark" if dark else ""
        sheet = find_asset(f"luma-appkit{suffix}-tokens.css", "LUMA_APPKIT_TOKENS_PATH")
        component = find_asset(f"luma-appkit{suffix}.css", "LUMA_APPKIT_STYLE_PATH")
        if sheet:
            tokens_provider.load_from_path(sheet)
        if component:
            component_provider.load_from_path(component)
        load_family_sheets(display, "high-contrast" if high_contrast else "dark" if dark else "light")

    # The window frame and the parts libadwaita used to draw for Luma
    # (ADR-052), above whatever libadwaita this system has.
    from . import window_frame
    window_frame.install_toolkit(display)

    def load_all(*_args: object) -> None:
        load()
        window_frame.reload_toolkit(display)

    load_all()
    try:
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        manager = Adw.StyleManager.get_default()
        manager.connect("notify::dark", load_all)
        manager.connect("notify::high-contrast", load_all)
    except (ImportError, ValueError):
        settings = Gtk.Settings.get_for_display(display)
        settings.connect("notify::gtk-application-prefer-dark-theme", load_all)


def _desktop_appearance() -> tuple[bool, bool]:
    try:
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        manager = Adw.StyleManager.get_default()
        return bool(manager.get_dark()), bool(manager.get_high_contrast())
    except (ImportError, ValueError):
        settings = Gtk.Settings.get_default()
        dark = bool(settings and settings.get_property("gtk-application-prefer-dark-theme"))
        return dark, False


# ── Motion ─────────────────────────────────────────────────────────────────

def reduced_motion() -> bool:
    """Whether movement should become a fade (the desktop's reduced-motion or animations-off)."""
    settings = Gtk.Settings.get_default()
    if settings is None:
        return False
    if not settings.get_property("gtk-enable-animations"):
        return True
    try:
        return settings.get_property("gtk-interface-reduced-motion") == Gtk.ReducedMotion.REDUCE
    except (TypeError, AttributeError):
        return False


def duration(name: str) -> int:
    """A motion token in milliseconds (`"toast_in"`, `"drawer"`, …), honouring reduced motion.

    Timeouts that are not motion (`"toast"`, `"toast_undo"`) are never shortened.
    """
    key = name if name.endswith("_ms") else f"{name}_ms"
    value = int(tokens.MOTION[key])
    if key in ("toast_ms", "toast_undo_ms", "tooltip_delay_ms"):
        return value
    if reduced_motion():
        settings = Gtk.Settings.get_default()
        animations = settings is None or settings.get_property("gtk-enable-animations")
        return min(value, int(tokens.MOTION["reduced_fade_ms"])) if animations else 0
    return value


def on_next_frame(widget: Gtk.Widget, callback: Callable[[], None]) -> None:
    """Run `callback` once the widget has been drawn in its starting state.

    A class added in the same frame as the widget appears has no transition to
    run; adding it on the next frame lets the CSS transition play.
    """
    def tick(_widget: Gtk.Widget, _clock: object) -> bool:
        callback()
        return False

    widget.add_tick_callback(tick)


# ── Width ──────────────────────────────────────────────────────────────────

def is_phone_width(widget: Gtk.Widget) -> bool:
    """Whether the place a part appears in is phone-width (v71: under 560).

    Width adapts layout, never identity: a narrow desktop window gets the same
    drawers a phone does.
    """
    width = widget.get_width()
    if width <= 0:
        root = widget.get_root()
        width = root.get_width() if root is not None else 0
    return 0 < width <= tokens.PHONE_MAX_WIDTH


def set_css_class(widget: Gtk.Widget, name: str, on: bool) -> None:
    (widget.add_css_class if on else widget.remove_css_class)(name)


# ── Hue ────────────────────────────────────────────────────────────────────
#
# v70 gives each person (and some things) a hue: their face, the wash behind
# their card and the icons of what you share with them wear it. The colours
# come from the `lumaui.hue` tokens (OKLCH lightness and chroma per role); the
# kit renders the rules for the hues in use into one provider of its own, and
# re-renders them when the appearance turns light or dark.

_hue_providers: dict[int, tuple[Gtk.CssProvider, set[int]]] = {}


def oklch_rgba(lightness: float, chroma: float, hue: float, alpha: float = 1.0) -> str:
    """An OKLCH colour as a CSS rgba(), clipped into sRGB."""
    import math

    radians = math.radians(hue)
    a, b = chroma * math.cos(radians), chroma * math.sin(radians)
    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    linear = (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
              -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
              -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)

    def encode(value: float) -> int:
        value = max(0.0, min(1.0, value))
        value = 1.055 * value ** (1 / 2.4) - 0.055 if value > 0.0031308 else 12.92 * value
        return round(value * 255)

    red, green, blue = (encode(value) for value in linear)
    return f"rgba({red}, {green}, {blue}, {alpha:g})"


def person_hue(name: str) -> int:
    """The hue a name wears when nothing else gives one: stable, spread round the wheel."""
    import zlib

    return zlib.crc32((name or "").strip().casefold().encode()) % 360


def _hue_css(hues: set[int], dark: bool) -> str:
    roles = tokens.HUE
    wash = roles["wash_dark" if dark else "wash_light"]
    tint = roles["tint_dark" if dark else "tint_light"]
    face = roles["avatar"]
    ring = roles["ring"]
    bubble = roles["bubble_dark" if dark else "bubble_light"]
    rules = []
    for hue in sorted(hues):
        name = f"lumaui-hue-{hue}"
        rules.append(
            f".{name}.lumaui-avatar {{ background: {oklch_rgba(face['lightness'], face['chroma'], hue)}; }}\n"
            f"box.lumaui-lit-wash.{name} {{ background-image: linear-gradient(to bottom, "
            f"{oklch_rgba(wash['lightness'], wash['chroma'], hue, wash['alpha'])}, "
            f"{oklch_rgba(wash['lightness'], wash['chroma'], hue, 0)}); }}\n"
            f".{name} .lumaui-hue-tint {{ color: {oklch_rgba(tint['lightness'], tint['chroma'], hue)}; }}\n"
            f".{name} entry.lumaui-hero-field.editable {{ box-shadow: inset 0 0 0 1px @luma_well_ring, "
            f"inset 0 1px 2px @luma_well_shade, 0 1px 0 @luma_well_lip, "
            f"0 0 0 2px {oklch_rgba(ring['lightness'], ring['chroma'], hue)}; }}\n"
            f".{name} entry.lumaui-fact-input:focus-within {{ box-shadow: 0 0 0 2px {oklch_rgba(ring['lightness'], ring['chroma'], hue)}; }}\n"
            # Messages v26: your bubbles in the conversation's hue; a caption or glyph tinted with it.
            f"box.lumaui-message.mine.{name} {{ background-image: linear-gradient(to bottom, "
            f"{oklch_rgba(bubble['lightness'], bubble['chroma'], hue)}, {oklch_rgba(bubble['lightness_end'], bubble['chroma'], hue)}); }}\n"
            f".lumaui-hue-tint.{name} {{ color: {oklch_rgba(tint['lightness'], tint['chroma'], hue)}; }}\n"
            # v71 Charlie `.crnh i`: an account's dot, oklch(0.68 0.12 h).
            f"box.lumaui-section-dot.{name} {{ background: {oklch_rgba(*(0.68, 0.12), hue)}; }}\n"
            # v71 `.lmini.event .lmth`: the event card's date tile, in a calendar's hue (`--eh`).
            f"box.lumaui-event-tile.{name} {{ background: "
            f"{oklch_rgba(*((0.36, 0.07, hue, 0.75) if dark else (0.92, 0.05, hue)))}; box-shadow: inset 3px 0 0 "
            f"{oklch_rgba(*((0.72, 0.13, hue) if dark else (0.6, 0.14, hue)))}; }}")
    return "\n".join(rules)


def mobile_form_factor() -> bool:
    """Whether this device is a phone (not whether a window is narrow).

    An explicit form-factor override takes precedence over Luma's device-class
    contract (environment, then /etc/luma-device-class). Older installations
    can still identify themselves through CHASSIS=handset in /etc/machine-info.
    A desktop never qualifies merely because its windows are narrow.
    """
    value = os.environ.get("LUMA_FORM_FACTOR", "").strip().lower()
    if value:
        return value in ("phone", "handset", "mobile")
    device_class = os.environ.get("LUMA_DEVICE_CLASS", "").strip().lower()
    if device_class not in ("handheld", "tablet", "desktop"):
        try:
            device_class = Path("/etc/luma-device-class").read_text(encoding="utf-8").strip().lower()
        except (OSError, UnicodeError):
            device_class = ""
    if device_class in ("handheld", "tablet", "desktop"):
        return device_class == "handheld"
    try:
        for line in Path("/etc/machine-info").read_text(encoding="utf-8").splitlines():
            if line.startswith("CHASSIS="):
                return line.split("=", 1)[1].strip().strip('"').lower() == "handset"
    except (OSError, UnicodeError):
        pass
    return False


class YieldingLayout(Gtk.BoxLayout):
    """A box layout that asks for little width and, given less than its content needs, lays the
    content out at that need and lets the island clip it (Nick, 26 Sep: a tiled window narrower
    than an app's content pushed the frame and its controls off screen). The frame, title row and
    controls always fit; only the island's content is cut, inside its rounded edge."""

    FLOOR = 160

    def do_measure(self, widget, orientation, for_size):
        minimum, natural, min_base, nat_base = Gtk.BoxLayout.do_measure(self, widget, orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            return min(minimum, self.FLOOR), natural, min_base, nat_base
        return minimum, natural, min_base, nat_base

    def do_allocate(self, widget, width, height, baseline):
        need = Gtk.BoxLayout.do_measure(self, widget, Gtk.Orientation.HORIZONTAL, -1)[0]
        widget._lumaui_overflow = max(0, need - width)
        Gtk.BoxLayout.do_allocate(self, widget, max(width, need), height, baseline)


def overflowing(root: Gtk.Widget) -> list[str]:
    """What does not fit its window: islands whose content needs more width than they were given
    (the content is clipped, not the frame), and anything reaching past the window's right edge.
    The gallery and conform report these so a part with a large minimum width is found on the
    desk, not on a tiled window (Nick, 26 Sep: Calculator's number, Voice Memos' deck)."""
    found: list[str] = []
    width = root.get_width()

    def name(widget: Gtk.Widget) -> str:
        classes = [c for c in widget.get_css_classes() if c.startswith(("lumaui-", "luma-"))]
        return f"{type(widget).__name__}{'.' + classes[0] if classes else ''}"

    def visit(widget: Gtk.Widget) -> None:
        if not widget.get_mapped():
            return
        short = getattr(widget, "_lumaui_overflow", 0)
        if short:
            found.append(f"{name(widget)} content needs {short}px more than the island gives it")
        ok, bounds = widget.compute_bounds(root)
        if ok and widget is not root and bounds.get_x() + bounds.get_width() > width + 1 and not _clipped_above(widget, root):
            found.append(f"{name(widget)} reaches {round(bounds.get_x() + bounds.get_width() - width)}px past the window")
        child = widget.get_first_child()
        while child is not None:
            visit(child)
            child = child.get_next_sibling()

    visit(root)
    return found


def _clipped_above(widget: Gtk.Widget, root: Gtk.Widget) -> bool:
    parent = widget.get_parent()
    while parent is not None and parent is not root:
        if parent.get_overflow() == Gtk.Overflow.HIDDEN or isinstance(parent, (Gtk.Viewport, Gtk.ScrolledWindow)):
            return True
        parent = parent.get_parent()
    return False


def media_context(widget: Gtk.Widget, on: bool = True) -> Gtk.Widget:
    """Make `widget` an always-dark photographic region (v70 `.cmbody`: Camera's viewfinder,
    a photo or film being viewed), in light, dark and high contrast alike.

    The kit's parts inside it (bars, the corner pill, menus, type roles) take the
    media palette (`@luma_media_*`); menus and drawers opened from inside it take
    it too, though they float in the window's layer host.
    """
    set_css_class(widget, "lumaui-media", on)
    return widget


def hue_tint(widget: Gtk.Widget, hue: float | None) -> Gtk.Widget:
    """Ink `widget` (a sender's name over their messages, a glyph) in a person's hue (v70 .tgi, --ch)."""
    set_css_class(widget, "lumaui-hue-tint", hue is not None)
    hue_class(widget, hue)
    return widget


def in_media_context(widget: Gtk.Widget | None) -> bool:
    """Whether `widget` sits inside a media context."""
    while widget is not None:
        if widget.has_css_class("lumaui-media"):
            return True
        widget = widget.get_parent()
    return False


def hue_class(widget: Gtk.Widget, hue: float | None) -> str:
    """Give `widget` a hue (a person's): the CSS class that carries it, e.g. "lumaui-hue-330".

    Inside the classed widget, a face (PersonAvatar) wears the hue, a lit
    header's wash is tinted with it and any `lumaui-hue-tint` icon takes it.
    `None` removes the hue. Only the kit's own provider holds the colours.
    """
    for name in [c for c in widget.get_css_classes() if c.startswith("lumaui-hue-") and c[11:].isdigit()]:
        widget.remove_css_class(name)
    if hue is None:
        return ""
    value = int(round(hue)) % 360
    display = widget.get_display() or Gdk.Display.get_default()
    if display is not None:
        key = hash(display)
        if key not in _hue_providers:
            provider = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            _hue_providers[key] = (provider, set())
            try:
                gi.require_version("Adw", "1")
                from gi.repository import Adw

                manager = Adw.StyleManager.get_default()
                manager.connect("notify::dark", lambda m, _p, k=key: _render_hues(k, m.get_dark()))
            except (ImportError, ValueError):
                pass
        provider, hues = _hue_providers[key]
        if value not in hues:
            hues.add(value)
            _schedule_hues(key)
    name = f"lumaui-hue-{value}"
    widget.add_css_class(name)
    return name


_paint_providers: dict[int, tuple[Gtk.CssProvider, set[str]]] = {}


def paint_class(widget: Gtk.Widget, colour: str | None) -> str:
    """Give a face its own exact colour (a sender's brand, v71 Charlie CRB.c): the class that carries
    it, e.g. "lumaui-paint-0f5d73". The colour is data (#rrggbb), never a kit literal. None removes it."""
    for name in [c for c in widget.get_css_classes() if c.startswith("lumaui-paint-")]:
        widget.remove_css_class(name)
    if colour is None:
        return ""
    value = colour.strip().lstrip("#").casefold()
    if len(value) != 6 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"a paint is #rrggbb, not {colour!r}")
    display = widget.get_display() or Gdk.Display.get_default()
    if display is not None:
        key = hash(display)
        if key not in _paint_providers:
            provider = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            _paint_providers[key] = (provider, set())
        provider, paints = _paint_providers[key]
        if value not in paints:
            paints.add(value)
            provider.load_from_string("\n".join(
                f".lumaui-paint-{v}.lumaui-avatar {{ background: #{v}; }}" for v in sorted(paints)))
    name = f"lumaui-paint-{value}"
    widget.add_css_class(name)
    return name


def _desktop_dark() -> bool:
    try:
        gi.require_version("Adw", "1")
        from gi.repository import Adw

        return Adw.StyleManager.get_default().get_dark()
    except (ImportError, ValueError):
        return _desktop_appearance()[0]


_hue_pending: set[int] = set()


def _schedule_hues(key: int) -> None:
    """One provider reload per main-loop turn, however many new hues a list brings.

    Reloading per hue stalled a 400-person list for 1.7 s. HIGH_IDLE runs
    before GTK's layout and paint (GDK_PRIORITY_REDRAW), so no frame shows a
    face without its hue.
    """
    if key in _hue_pending:
        return
    _hue_pending.add(key)
    from gi.repository import GLib

    def flush() -> bool:
        _hue_pending.discard(key)
        if key in _hue_providers:
            _render_hues(key, _desktop_dark())
        return False
    GLib.idle_add(flush, priority=GLib.PRIORITY_HIGH_IDLE)


def _render_hues(key: int, dark: bool) -> None:
    provider, hues = _hue_providers[key]
    provider.load_from_string(_hue_css(hues, dark))
