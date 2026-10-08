# SPDX-License-Identifier: Apache-2.0
"""LumaUI content: contact actions and the mini cards.

ContactActions — Contacts owns them: Message, Call, Video, Email, in that
order, as stacked buttons; anywhere a person appears embeds them (v70
`lContactActs`). An action that can't do its job is disabled rather than
failing when pressed. The hosting app may handle an action in place
(Messages opens the thread, Phone places the call) by passing `handler`;
otherwise the kit hands the desktop an `sms:`, `tel:` or `mailto:` link.
Promoted from Contacts' card actions.

    ContactActions(Person("Priya Raman", phone="+1 555 0101", email="priya@…", username="priya"),
                   handler=lambda action, person: action == "message" and open_thread(person))

Mini cards — when an app shows something another app owns, it shows that
app's own card, through one component (v70 `lContactCard`, `lEventCard`,
`lSongCard`, `lPlaceCard`; files are FileCard):

    ContactCard(person)                                      # Contacts
    EventCard("Launch rehearsal", start, where="Studio", on_add=add)   # Calendar
    SongCard("God Only Knows", "The Beach Boys", "Pet Sounds", artwork=texture, on_play=play)  # Tide
    PlaceCard("Duende", "468 19th St", eta="9 min", icon="utensils", on_directions=go)          # Maps

StatusPill — how someone or something is, as a small well pill: a dot in
the status colour (green and glowing for good, amber for busy, red for bad,
none when off) and a word (v70 `.onl2`). Kinds are named, so new states are
a kit change: `StatusPill("on-luma")`, `StatusPill("not-on-luma")`,
`StatusPill("syncing", "Syncing 214 songs")`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from urllib.parse import quote

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import icons  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402
from .action_stack import StackedButton  # noqa: E402
from .content_cards import PersonAvatar  # noqa: E402

__all__ = ["Person", "ContactActions", "CONTACT_ACTIONS", "contact_uri", "StatusPill", "STATUS_KINDS",
           "MiniCard", "ContactCard", "EventCard", "SongCard", "PlaceCard"]

#: (action, Lucide glyph, label), in Contacts' order.
CONTACT_ACTIONS = (("message", "message-square", "Message"), ("call", "phone", "Call"),
                   ("video", "video", "Video"), ("email", "mail", "Email"))

EVENT_TONES = tokens.CATEGORY_ORDER

#: A status kind → (tone, default words). Tones: good, busy, bad, off.
STATUS_KINDS = {
    "on-luma": ("good", "On Luma"),
    "not-on-luma": ("off", "Not on Luma"),
    "online": ("good", "Online"),
    "offline": ("off", "Offline"),
    "syncing": ("busy", "Syncing"),
    "error": ("bad", "Needs attention"),
}


@dataclass(frozen=True)
class Person:
    """Who a contact card or action row is about. `username` is their Luma @username."""

    name: str
    phone: str = ""
    email: str = ""
    username: str = ""
    online: bool = False
    picture: Gdk.Paintable | None = None
    #: Their hue (0–360): the face and the light behind their card wear it; None takes it from the name.
    hue: float | None = None

    @property
    def on_luma(self) -> bool:
        return bool(self.username)


def contact_uri(action: str, person: Person) -> str:
    """The link the desktop opens for `action` when the app doesn't handle it; "" when there is none."""
    phone = "".join(ch for ch in person.phone if ch.isdigit() or ch == "+")
    if action == "message":
        return f"sms:{phone}" if phone else ""
    if action == "call":
        return f"tel:{phone}" if phone else ""
    if action == "email":
        return f"mailto:{quote(person.email, safe='@')}" if person.email else ""
    if action == "video":
        return ""  # a video call needs Luma's network or the hosting app; no desktop link places one
    raise ValueError(f"unknown contact action {action!r}")


def _possible(action: str, person: Person, handled: bool) -> bool:
    if contact_uri(action, person):
        return True
    if not handled:
        return False
    # The hosting app can do it in place when the person is reachable that way.
    return {"message": person.on_luma, "call": person.on_luma, "video": person.on_luma or person.online,
            "email": bool(person.email)}[action]


class ContactActions(Gtk.Box):
    """Message, Call, Video, Email for one person. `handler(action, person) -> bool` handles one in place."""

    __gtype_name__ = "LumaUIContactActions"

    def __init__(self, person: Person, *, small: bool = False,
                 handler: Callable[[str, Person], bool] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        self.add_css_class("lumaui-stacked-buttons")
        self.add_css_class("lumaui-contact-actions")
        if small:
            self.add_css_class("lumaui-stack-small")  # not "small": Adw.Clamp puts .small on its child at narrow widths
        self.person, self.handler = person, handler
        self.buttons: dict[str, StackedButton] = {}
        for action, glyph, label in CONTACT_ACTIONS:
            button = StackedButton(glyph, label, sensitive=_possible(action, person, handler is not None),
                                   on_click=lambda a=action: self.activate_action(a))
            self.buttons[action] = button
            self.append(button)
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"Contact {person.name}"])

    def activate_action(self, action: str) -> bool:
        """Run `action`: in place when the app handles it, otherwise through the desktop's link."""
        if self.handler is not None and self.handler(action, self.person):
            return True
        uri = contact_uri(action, self.person)
        if not uri:
            return False
        context = self.get_display().get_app_launch_context() if self.get_display() else None
        try:
            Gio.AppInfo.launch_default_for_uri(uri, context)
        except GLib.Error:
            from .action_toast import Toast
            Toast.show(self, "Nothing on this computer can do that yet", kind="error")
            return False
        return True


# ── mini cards ─────────────────────────────────────────────────────────────

class MiniCard(Gtk.Box):
    """The shared shape: a 44 px lead, a title, one quiet line, and what to do on the right."""

    __gtype_name__ = "LumaUIMiniCard"

    def __init__(self, kind: str, lead: Gtk.Widget, title: str, subtitle: str | Gtk.Widget = "",
                 trailing: Gtk.Widget | None = None) -> None:
        # v70: 300 wide (320 for a person), narrower only when it must be; a pane that wants it
        # full width sets halign FILL.
        super().__init__(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.START)
        self.add_css_class("lumaui-mini-card")
        self.add_css_class(kind)
        self.kind = kind
        from .content_file import cap_width
        # v71 `.lmini` is border-box: 300 (320 a person) is the card, padding (8+10, 12+12) included.
        person = kind == "person"
        cap_width(self, tokens.MINI_CARD["person_width" if person else "width"] - (24 if person else 18))
        row = Gtk.Box()
        row.add_css_class("lumaui-mini-row")
        slot = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        slot.add_css_class("lumaui-mini-lead")
        slot.append(lead)
        row.append(slot)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        text.add_css_class("lumaui-mini-text")
        self.title = Gtk.Label(label=title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.title.add_css_class("lumaui-mini-title")
        text.append(self.title)
        if isinstance(subtitle, Gtk.Widget):
            text.append(subtitle)
        elif subtitle:
            line = Gtk.Label(label=subtitle, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            line.add_css_class("lumaui-mini-subtitle")
            text.append(line)
        row.append(text)
        if trailing is not None:
            row.append(trailing)
        self.append(row)
        self.row = row
        name = title if not isinstance(subtitle, str) or not subtitle else f"{title}, {subtitle}"
        self.update_property([Gtk.AccessibleProperty.LABEL], [name])


def _small_button(label: str, callback: Callable[[], None] | None, tooltip: str | None = None) -> Gtk.Button:
    button = Gtk.Button(label=label, valign=Gtk.Align.CENTER, sensitive=callback is not None)
    button.add_css_class("lumaui-mini-button")
    if tooltip:
        button.set_tooltip_text(tooltip)
    if callback is not None:
        button.connect("clicked", lambda _b: callback())
    return button


class ContactCard(MiniCard):
    """Contacts' card: the face, the name, "@username · On Luma" or the number, and the actions under it."""

    __gtype_name__ = "LumaUIContactCard"

    def __init__(self, person: Person, *, handler: Callable[[str, Person], bool] | None = None) -> None:
        if person.on_luma:
            line = Gtk.Box()
            line.add_css_class("lumaui-mini-subtitle")
            dot = Gtk.Box(valign=Gtk.Align.CENTER)
            dot.add_css_class("lumaui-on-luma-dot")
            line.append(dot)
            line.append(Gtk.Label(label=f"@{person.username} · On Luma", xalign=0,
                                  ellipsize=Pango.EllipsizeMode.END))
            subtitle: str | Gtk.Widget = line
        else:
            subtitle = person.phone or person.email
        super().__init__("person", PersonAvatar(person.name, tokens.MINI_CARD["lead"], picture=person.picture, hue=person.hue),
                         person.name, subtitle)
        self.person = person
        self.actions = ContactActions(person, small=True, handler=handler)
        self.append(self.actions)


class EventCard(MiniCard):
    """Calendar's card: the date tile in the calendar's colour, the title, when and where, Add.

    `hue` (degrees, v71's `--eh`; Messages' card says 285) tints the tile as v71 does, the calendar's hue;
    without one the tile wears the `tone`'s category colour.
    """

    __gtype_name__ = "LumaUIEventCard"

    def __init__(self, title: str, start: datetime, *, where: str | None = None, when: str | None = None,
                 all_day: bool = False, tone: str = "work", hue: float | None = None,
                 on_add: Callable[[], None] | None = None, add_label: str = "Add") -> None:
        if tone not in EVENT_TONES:
            raise ValueError(f"an event's tone is one of {', '.join(EVENT_TONES)}")
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        tile.add_css_class("lumaui-event-tile")
        if hue is None:
            tile.add_css_class(tone)
        else:
            from .lumaui import hue_class
            hue_class(tile, hue)
        # The two lines sit together in the middle of the 44 (v71 `.lmth` centres its content).
        month = Gtk.Label(label=start.strftime("%b").upper(), vexpand=True, valign=Gtk.Align.END)
        month.add_css_class("lumaui-event-month")
        day = Gtk.Label(label=str(start.day), vexpand=True, valign=Gtk.Align.START)
        day.add_css_class("lumaui-event-day")
        tile.append(month)
        tile.append(day)
        if when is None:
            when = "All day" if all_day else start.strftime("%I:%M %p").lstrip("0")
        subtitle = " · ".join(part for part in (when, where) if part)
        trailing = _small_button(add_label, on_add) if on_add is not None else None
        super().__init__("event", tile, title, subtitle, trailing)
        self.start = start


class SongCard(MiniCard):
    """Tide's card: the artwork, the title, artist · album, and Play."""

    __gtype_name__ = "LumaUISongCard"

    def __init__(self, title: str, artist: str, album: str | None = None, *,
                 artwork: Gdk.Paintable | None = None, on_play: Callable[[], None] | None = None) -> None:
        from .content_file import _Face
        metrics = tokens.MINI_CARD
        if artwork is not None:
            lead: Gtk.Widget = _Face(metrics["lead"], metrics["artwork_radius"])
            lead.set_paintable(artwork)
        else:
            lead = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            lead.append(icons.image("music"))
        lead.add_css_class("lumaui-song-artwork")
        play = Gtk.Button(valign=Gtk.Align.CENTER, tooltip_text="Play", sensitive=on_play is not None)
        play.add_css_class("lumaui-mini-play")
        play.set_child(icons.image("play"))
        play.remove_css_class("image-button")  # a LumaUI part, not the legacy icon-button look
        play.update_property([Gtk.AccessibleProperty.LABEL], [f"Play {title}"])
        if on_play is not None:
            play.connect("clicked", lambda _b: on_play())
        subtitle = " · ".join(part for part in (artist, album) if part)
        super().__init__("song", lead, title, subtitle, play)


class PlaceCard(MiniCard):
    """Maps' card: the pin, the name, the address and how far, Directions."""

    __gtype_name__ = "LumaUIPlaceCard"

    def __init__(self, name: str, address: str, *, eta: str | None = None, icon: str = "map-pin",
                 on_directions: Callable[[], None] | None = None) -> None:
        pin = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        pin.add_css_class("lumaui-place-pin")
        glyph = icons.image(icon)
        glyph.set_halign(Gtk.Align.CENTER)
        glyph.set_valign(Gtk.Align.CENTER)
        pin.append(glyph)
        subtitle = " · ".join(part for part in (address, eta) if part)
        trailing = _small_button("Directions", on_directions) if on_directions is not None else None
        super().__init__("place", pin, name, subtitle, trailing)


class StatusPill(Gtk.Box):
    """A status as a pill: a dot in the status colour and the words for it."""

    __gtype_name__ = "LumaUIStatusPill"

    def __init__(self, kind: str, label: str | None = None) -> None:
        super().__init__(valign=Gtk.Align.CENTER, halign=Gtk.Align.START)
        self.add_css_class("lumaui-status-pill")
        self.dot = Gtk.Box(valign=Gtk.Align.CENTER)
        self.dot.add_css_class("lumaui-status-pill-dot")
        self.label = Gtk.Label()
        self.append(self.dot)
        self.append(self.label)
        self.kind, self.tone = "", ""
        self.set_kind(kind, label)

    def set_kind(self, kind: str, label: str | None = None) -> None:
        if kind not in STATUS_KINDS:
            raise ValueError(f"unknown status {kind!r}; use one of {', '.join(STATUS_KINDS)}")
        if self.tone:
            self.remove_css_class(self.tone)
        self.kind = kind
        self.tone, words = STATUS_KINDS[kind]
        self.add_css_class(self.tone)
        self.dot.set_visible(self.tone != "off")
        self.label.set_label(label or words)
        self.update_property([Gtk.AccessibleProperty.LABEL], [label or words])
