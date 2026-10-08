# SPDX-License-Identifier: Apache-2.0
"""LumaUI content: cards.

Card — the plain card: 16 round, the raised surface and the chip edge (v70
`.lcard`). Use it where colour means nothing.

ContentLitCard — a card on a content-lit background (a light sky, a photo):
a deep blue-black frost at 42% darkening, the least that lets 90% white
text pass AA on the brightest sky; its text is white and secondary text 90%
white (v70 `.lskycard`, tokens `luma_sky_card*`, `--lumaui-sky-card-*`).
GTK has no backdrop blur, so the frost is a flat tint of the same darkness
(docs/developer/kit/lumaui-deviations.md).

AccountCard — the person at the top of a sidebar in its own subtle frame,
a section gap below; it opens the account page (v70 `.lacct`).

ContentLitHeader — the soft light at the top of an island that belongs to
someone or something (Contacts' person, an album): their photo blown up,
blurred and faded out behind the top of the page, with a wash of their hue
over it (v70 `.mlight`, `.mwash`). It is decoration only (it takes no
input), sits behind the content, and keeps text on it AA: the wash is at
most 60% of a category pastel in light and 35% of its dark tone in dark,
and high contrast draws neither.

    header = ContentLitHeader.for_person(person)      # or ContentLitHeader(picture=texture, tone="media")
    overlay.set_child(header); overlay.add_overlay(page)

PersonAvatar — the round face these cards and the mini cards share: the
picture when there is one, otherwise initials on a tone taken from the name,
so a person looks the same everywhere.

    Card(child)                 ContentLitCard(child)
    AccountCard("Nick", caption="Luma account", on_activate=open_account)
"""
from __future__ import annotations

import zlib
from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk, Pango  # noqa: E402

from . import icons, lumaui  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402

__all__ = ["Card", "ContentLitCard", "AccountCard", "PersonAvatar", "ContentLitHeader", "initials", "person_tone"]


def initials(name: str) -> str:
    """One or two letters for a name: "Priya Raman" → "PR"; never a digit or a bracket."""
    words = [w for w in name.replace("@", " ").split() if w[:1].isalpha()]
    if not words:
        return ""
    if len(words) == 1:
        return words[0][:1].upper()
    return (words[0][:1] + words[-1][:1]).upper()


def person_tone(name: str) -> str:
    """The hue a person or thing wears everywhere: one of the category hues, picked by the name."""
    return tokens.CATEGORY_ORDER[zlib.crc32(name.encode()) % len(tokens.CATEGORY_ORDER)]


class PersonAvatar(Gtk.Widget):
    """A round face: `PersonAvatar("Priya Raman", 44)` or with `picture=` a Gdk.Paintable.

    `mark=True` is a sender that is not a person (a company, a newsletter: v71 Charlie `.crbrand`):
    a rounded square (28%) in its hue (or its own `colour`, hex rrggbb) with its one letter, bold, at
    0.44 of the side.

    `stranger=True`: someone not in Contacts (v71 Charlie's .crmono): the initials at 0.38 of the side, 650.
    Initials are round(side × 0.36) otherwise (v71 av0: 10 on 28, 12 on 34).
    """

    __gtype_name__ = "LumaUIPersonAvatar"

    def __init__(self, name: str, size: int = 40, *, picture: Gdk.Paintable | None = None,
                 hue: float | None = None, mark: bool = False, colour: str | None = None,
                 stranger: bool = False) -> None:
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION, overflow=Gtk.Overflow.HIDDEN)
        self.size, self.name, self.mark, self.stranger = size, name, mark, stranger
        self.add_css_class("lumaui-avatar")
        if mark:
            self.add_css_class("mark")
        # The person's hue (v70 .av: oklch 0.62 0.12 h); taken from the name when none is known,
        # so a person looks the same in every app.
        self.hue = int(round(hue)) % 360 if hue is not None else lumaui.person_hue(name)
        self.tone = lumaui.hue_class(self, self.hue)
        if colour is not None:   # a brand's own colour (`mark=True`), over the hue
            lumaui.paint_class(self, colour)
        if picture is not None:
            from .content_file import _Face
            face = _Face(size, size / 2)
            face.set_paintable(picture)
            face.set_parent(self)
            self.add_css_class("picture")
        else:
            text = name.strip()[:1].upper() if mark else initials(name)
            if text:
                label = Gtk.Label(label=text)
            else:
                label = icons.image("user")
            label.add_css_class("lumaui-avatar-initials")
            if isinstance(label, Gtk.Label):
                # Initials grow with the face: v70 av0 sets round(size × .36): 42 px on 116, 12 on 34.
                attributes = Pango.AttrList()
                if mark:   # v71 .crbrand: font-size round(side × .44), 700
                    attributes.insert(Pango.attr_size_new_absolute(round(size * 0.44) * Pango.SCALE))
                    attributes.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
                elif stranger:   # v71 .crmono: round(side × .38), 650 (between Pango's named weights)
                    attributes = Pango.AttrList.from_string("0 4294967295 weight 650") or attributes
                    attributes.insert(Pango.attr_size_new_absolute(int(size * 0.38 + 0.5) * Pango.SCALE))
                else:            # v71 av0: Math.round(side × .36)
                    attributes.insert(Pango.attr_size_new_absolute(int(size * 0.36 + 0.5) * Pango.SCALE))
                label.set_attributes(attributes)
            label.set_parent(self)
        self.add_css_class("large" if size >= 40 else "small")

    def do_measure(self, _orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        return self.size, self.size, -1, -1

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        child = self.get_first_child()
        if child is not None:
            child.allocate(width, height, baseline, None)

    def do_dispose(self) -> None:
        child = self.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            child.unparent()
            child = following


class Card(Gtk.Box):
    """The plain card: the raised surface, 16 round, the chip edge, 12/14/14 inside."""

    __gtype_name__ = "LumaUICard"

    def __init__(self, child: Gtk.Widget | None = None, *, padded: bool = True,
                 recessed: bool = False, outlined: bool = False, shape: str = "regular") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("lumaui-card")
        self.set_shape(shape)
        if padded:
            self.add_css_class("padded")
        if recessed:
            # A well in the page rather than a chip on it (v70 .ccard: Contacts' cards).
            self.add_css_class("recessed")
        if outlined:
            # An inset outline on the content surface, without the dark well fill.
            self.add_css_class("outlined")
        if child is not None:
            self.append(child)

    def set_shape(self, shape: str) -> None:
        """Use the regular card or a compact statistic well."""
        if shape not in ("regular", "stat"):
            raise ValueError("card shape must be regular or stat")
        (self.add_css_class if shape == "stat" else self.remove_css_class)("stat")


class ContentLitCard(Card):
    """A card over a lit picture (sky, photo): darker than what is behind it, white text that passes AA."""

    __gtype_name__ = "LumaUIContentLitCard"

    GLASS_SHAPES = ("card", "tile", "strip", "list", "map")

    def __init__(self, child: Gtk.Widget | None = None, *, padded: bool = True, glass: bool = False,
                 shape: str = "card", night: bool = False) -> None:
        """`glass=True` is v71's phone card over the live sky (Weather, Maps' sheet): white at 8% with an
        8% edge, in light and dark. `shape`: "card" (22 round, 12/14 inside), "tile" (20, 12), "strip"
        (22, 12/10: the next hours), "list" (22, no padding: `append_row` adds 52 px rows with a hairline
        under each, `current=True` filled) or "map" (24, no padding)."""
        if shape not in self.GLASS_SHAPES:
            raise ValueError(f"a glass card's shape is one of {', '.join(self.GLASS_SHAPES)}")
        super().__init__(child, padded=padded and not (glass and shape in ("list", "map")))
        self.remove_css_class("lumaui-card")
        self.add_css_class("lumaui-lit-card")
        self.glass, self.shape = glass, shape
        self.set_night(night)
        if glass:
            self.add_css_class("glass")
            self.add_css_class(shape)
            if shape in ("list", "map"):
                self.set_overflow(Gtk.Overflow.HIDDEN)

    def set_night(self, night: bool) -> None:
        """Use a light veil over a dark scene, preserving the card's geometry.

        Phone glass already has its own surface and retains that treatment.
        """
        self.night = bool(night)
        if self.night:
            self.add_css_class("night")
        else:
            self.remove_css_class("night")

    def append_row(self, child: Gtk.Widget, *, current: bool = False) -> Gtk.Box:
        """A row of a glass list (v71 .wxhr): 52 tall, 14 inside, a hairline under it; `current` is filled."""
        row = Gtk.Box(valign=Gtk.Align.FILL)
        row.add_css_class("lumaui-glass-row")
        if current:
            row.add_css_class("current")
        child.set_hexpand(True)
        child.set_valign(Gtk.Align.CENTER)
        row.append(child)
        self.append(row)
        return row


class AccountCard(Gtk.Button):
    """The person at the top of a sidebar: avatar, name, a caption, a chevron; opens the account."""

    __gtype_name__ = "LumaUIAccountCard"

    def __init__(self, name: str, *, caption: str = "Luma account", picture: Gdk.Paintable | None = None,
                 on_activate: Callable[[], None] | None = None, selected: bool = False,
                 hue: float | None = None, recessed: bool = False, compact: bool = False) -> None:
        super().__init__()
        self.add_css_class("lumaui-account-card")
        if recessed:
            self.add_css_class("recessed")
        if selected:
            self.add_css_class("on")
        line = Gtk.Box()
        line.add_css_class("lumaui-account-content")
        # compact: v70 Settings' card (.cfme.lacct): a 34 avatar, the name 13.5/600, the caption 11.5/400.
        if compact:
            self.add_css_class("compact")
        size = tokens.ACCOUNT_CARD["compact_avatar" if compact else "recessed_avatar" if recessed else "avatar"]
        line.append(PersonAvatar(name, size, picture=picture, hue=hue))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        title = Gtk.Label(label=name, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class("lumaui-account-name")
        text.append(title)
        sub = Gtk.Label(label=caption, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        sub.add_css_class("lumaui-t-caption")
        text.append(sub)
        line.append(text)
        chevron = icons.image("chevron-right")
        chevron.add_css_class("lumaui-account-chevron")
        line.append(chevron)
        self.set_child(line)
        self._well = None
        if recessed:
            # v70 .cmecard > .ctrow.me: the row is the button, and the well is
            # drawn around it, `recessed_padding` out on every side. The well is
            # a real CSS node allocated past the button's own box, so apps and
            # measurements see the row as the control.
            self._well = Gtk.Box(can_target=False, accessible_role=Gtk.AccessibleRole.PRESENTATION)
            self._well.add_css_class("lumaui-account-well")
            self._well.insert_before(self, line)
            self.set_layout_manager(None)
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"{name}, {caption}"])
        if on_activate is not None:
            self.connect("clicked", lambda _b: on_activate())



    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        return self.get_child().measure(orientation, for_size)

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        child = self.get_child()
        if child is not None:
            child.allocate(width, height, baseline, None)
        if self._well is not None:
            pad = 0 if self.has_css_class("compact") else tokens.ACCOUNT_CARD["recessed_padding"]
            self._well.allocate(width + 2 * pad, height + 2 * pad, -1,
                                Gsk.Transform().translate(Graphene.Point().init(-pad, -pad)))

    def do_dispose(self) -> None:
        if getattr(self, "_well", None) is not None:
            self._well.unparent()
            self._well = None
        Gtk.Button.do_dispose(self)


# ── the content-lit header ─────────────────────────────────────────────────

def _saturation(value: float) -> Graphene.Matrix:
    """CSS saturate() as a Gsk colour matrix (the matrix GTK uses for the filter)."""
    s = value
    return Graphene.Matrix().init_from_float([
        0.213 + 0.787 * s, 0.213 - 0.213 * s, 0.213 - 0.213 * s, 0.0,
        0.715 - 0.715 * s, 0.715 + 0.285 * s, 0.715 - 0.715 * s, 0.0,
        0.072 - 0.072 * s, 0.072 - 0.072 * s, 0.072 + 0.928 * s, 0.0,
        0.0, 0.0, 0.0, 1.0])


def _stops(pairs: Sequence[tuple[float, float]]) -> list:
    stops = []
    for offset, alpha in pairs:
        stop = Gsk.ColorStop()
        stop.offset = offset
        colour = Gdk.RGBA()
        colour.red = colour.green = colour.blue = 0.0
        colour.alpha = alpha
        stop.color = colour
        stops.append(stop)
    return stops


class _LitPicture(Gtk.Widget):
    """The photo, blown up, blurred and saturated, fading out downwards (v70 .mlight)."""

    __gtype_name__ = "LumaUILitPicture"

    def __init__(self) -> None:
        super().__init__(can_target=False, accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.add_css_class("lumaui-lit-picture")
        self.paintable: Gdk.Paintable | None = None
        self.focus = (0.5, 0.5)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        paintable = self.paintable
        width, height = self.get_width(), self.get_height()
        if paintable is None or width <= 0 or height <= 0:
            return
        m = tokens.LIT_HEADER
        top, tall = m["light_top"], m["light_height"]
        left, wide = -0.10 * width, 1.20 * width
        image_w = wide * m["light_zoom_pct"] / 100
        ratio = (paintable.get_intrinsic_height() or 1) / (paintable.get_intrinsic_width() or 1)
        image_h = image_w * ratio
        x = left + (wide - image_w) * self.focus[0]
        y = top + (tall - image_h) * self.focus[1]
        area = Graphene.Rect().init(left, top, wide, tall)
        snapshot.push_clip(Graphene.Rect().init(0, 0, width, height))
        snapshot.push_mask(Gsk.MaskMode.ALPHA)
        snapshot.append_linear_gradient(area, Graphene.Point().init(0, top), Graphene.Point().init(0, top + tall),
                                        _stops([(m["mask_start_pct"] / 100, 1.0), (m["mask_end_pct"] / 100, 0.0)]))
        snapshot.pop()
        snapshot.push_blur(m["blur"])
        snapshot.push_color_matrix(_saturation(m["saturate_scale"]), Graphene.Vec4().init(0, 0, 0, 0))
        snapshot.push_clip(area)
        snapshot.save()
        snapshot.translate(Graphene.Point().init(x, y))
        paintable.snapshot(snapshot, image_w, image_h)
        snapshot.restore()
        snapshot.pop()
        snapshot.pop()
        snapshot.pop()
        snapshot.pop()
        snapshot.pop()


class ContentLitHeader(Gtk.Widget):
    """The light at the top of an island, from a photo and a hue. See the module docstring."""

    __gtype_name__ = "LumaUIContentLitHeader"

    def __init__(self, *, picture: Gdk.Paintable | None = None, tone: str | None = None, name: str | None = None,
                 focus: tuple[float, float] = (0.5, 0.5), hue: float | None = None,
                 hues: tuple[float, ...] | None = None) -> None:
        super().__init__(can_target=False, can_focus=False, hexpand=True, valign=Gtk.Align.START,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.add_css_class("lumaui-lit-header")
        self.picture = _LitPicture()
        self.picture.set_parent(self)
        self.wash = Gtk.Box(can_target=False)
        self.wash.add_css_class("lumaui-lit-wash")
        self.wash.set_parent(self)
        from .rows_lit import HueGlow  # ID2 (rows family): hues and the Luma tone
        self.glow = HueGlow()
        self.glow.set_parent(self)
        self.tone = ""
        self.set_source(picture=picture, tone=tone, name=name, focus=focus, hue=hue, hues=hues)

    @classmethod
    def for_person(cls, person: object) -> "ContentLitHeader":
        """The header for a person (a content_contact.Person or anything with `name` and `picture`)."""
        return cls(picture=getattr(person, "picture", None), name=getattr(person, "name", ""),
                   hue=getattr(person, "hue", None))

    def set_source(self, *, picture: Gdk.Paintable | None = None, tone: str | None = None, name: str | None = None,
                   focus: tuple[float, float] = (0.5, 0.5), hue: float | None = None,
                   hues: tuple[float, ...] | None = None) -> None:
        """Change whose light it is: their photo, and their hue (given, or taken from the name).

        `tone` (a category) is the older way to colour the wash; `hue` (0–360, a
        person's, v70 .mwash) wins when both are given.
        """
        # ID2: `hues` (up to three) or tone="luma" (the system update) light it instead of one wash.
        luma = tone == "luma"
        self.glow.set_source(hues, luma)
        if luma or hues:
            tone = None
            hue = hues[0] if hues else 0
        if tone is not None and tone not in tokens.CATEGORY_ORDER:
            raise ValueError(f"a lit header's tone is one of {', '.join(tokens.CATEGORY_ORDER)} or luma")
        if self.tone and self.tone in tokens.CATEGORY_ORDER:
            self.wash.remove_css_class(self.tone)
        if tone is not None and hue is None:
            lumaui.hue_class(self.wash, None)
            self.tone = tone
            self.wash.add_css_class(tone)
        else:
            self.tone = lumaui.hue_class(self.wash, hue if hue is not None else lumaui.person_hue(name or ""))
        self.wash.set_visible(not (luma or hues))
        if luma:
            self.tone = "luma"
        self.picture.paintable = picture
        self.picture.focus = focus
        self.picture.set_visible(picture is not None)
        self.picture.queue_draw()

    def do_measure(self, orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        if orientation == Gtk.Orientation.VERTICAL:
            height = tokens.LIT_HEADER["wash_height"]
            return height, height, -1, -1
        return 0, 0, -1, -1

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        # The photo's light shows down to where it fades out (v70 .mlight: 520 tall from 160 above);
        # the wash runs the header's full height.
        m = tokens.LIT_HEADER
        self.picture.allocate(width, max(0, min(height, m["light_top"] + m["light_height"])), baseline, None)
        self.wash.allocate(width, height, baseline, None)
        self.glow.allocate(width, height, baseline, None)

    def do_dispose(self) -> None:
        for child in (self.picture, self.wash, self.glow):
            if child.get_parent() is self:
                child.unparent()
