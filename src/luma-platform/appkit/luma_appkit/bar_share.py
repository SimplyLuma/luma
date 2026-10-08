# SPDX-License-Identifier: Apache-2.0
"""LumaUI: the share sheet, one for every app (v70 `data-share`, `.lshare`).

Any Share button opens it, anchored to the button: a floating sheet on a
computer, a drawer on a phone. There is no key: sharing has no single job, so
every target acts the moment it is tapped.

- **Header**: what is being shared (a thumbnail, its name, a line about it).
- **Send a copy**: recent people (tap to send in Messages), then where it can
  go (Messages, Mail, Nearby, then apps that take it in), an inset list of
  actions (Copy, Save a copy, Print) and who a link works for, with Copy
  link.
- **Work together** (documents people edit together, when the app has a
  collaboration store): add people by name or @username, what each can do,
  and who the link works for.

    corner = CornerPill(share=lambda anchor: ShareSheet.present(
        anchor, document=ShareSubject("Launch walkthrough", "Stage document", icon="org.projectluma.Stage"),
        people=recent_people, on_choice=shared))

    def shared(choice, value):            # the app does the work; a returned line is shown as a toast
        if choice == "send-to":
            messages.send_attachment(value, file)
            return f"Sent to {value.name.split()[0]} in Messages"

Choices: "send-to" (a Person), "target" (a ShareTarget key), "copy", "save",
"print", "copy-link", "code", "copy-card", "link-for" ("people"|"anyone");
Work together adds "invite" (a Collaborator), "role" ((collaborator, role)),
"remove" (a Collaborator) and "link-access" ("off"|"view"|"edit").
With no collaboration store an app passes `choices=("send-copy",)` and the
sheet shows only Send a copy. Presence is the app's to supply; the sheet
never invents people.

CSS: luma-appkit-bar.css, `/* LumaUI: Share sheet */`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import bar_tokens, icons, lumaui  # noqa: E402
from .action_bubble import FloatingMenu, MenuItem, rect_in  # noqa: E402
from .content_cards import PersonAvatar  # noqa: E402
from .content_contact import Person  # noqa: E402
from .structure_layers import LayerHost  # noqa: E402

__all__ = ["SharePanel", "PANEL_TARGETS", "ShareSheet", "ShareSubject", "ShareTarget", "Collaborator", "SHARE_CHOICES", "SHARE_KINDS",
           "SHARE_ROLES", "LINK_ACCESS", "LINK_FOR", "default_targets", "installed_targets", "ShareResult", "share_actions", "Floater"]

SHARE_CHOICES = ("work-together", "send-copy")
#: What is shared: a file, a document people edit together, a link (a place, a page, an event) or a contact.
SHARE_KINDS = ("file", "doc", "link", "contact")
SHARE_ROLES = (("edit", "Can edit"), ("comment", "Can comment"), ("view", "Can view"))
LINK_ACCESS = (("off", "Only people with access", "Nobody else can open it"),
               ("view", "Anyone with the link can view", "No account needed"),
               ("edit", "Anyone with the link can edit", "No account needed"))
LINK_FOR = (("people", "Only people you send it to", "They sign in to open it"),
            ("anyone", "Anyone with the link", "Can view, no account needed"))


@dataclass
class ShareSubject:
    """What is being shared. `icon` is an app icon name (or a Lucide name); `picture` a thumbnail."""

    title: str
    subtitle: str = ""
    kind: str = "file"
    icon: str | None = None
    picture: Gdk.Paintable | None = None
    person: Person | None = None
    image: bool = False
    mime_type: str | None = None
    share_link_available: bool = False

    def __post_init__(self) -> None:
        if self.kind not in SHARE_KINDS:
            raise ValueError(f"a share subject is one of {SHARE_KINDS}: {self.kind!r}")


@dataclass
class ShareTarget:
    """Somewhere it can go: an app (`app_id`, its icon) or a system place (`icon`, a Lucide name)."""

    key: str
    label: str
    app_id: str | None = None
    icon: str | None = None


@dataclass(frozen=True)
class ShareResult:
    """A completed action or a refusal; starting a dialog is not delivery."""
    completed: bool
    message: str = ""


def installed_targets(subject: ShareSubject) -> list[ShareTarget]:
    """Only installed file/URI receivers registered for this content type.

    A launcher being present does not make it an attachment receiver. Nearby
    is offered only once there is an actual transport, never by this discovery.
    """
    if not subject.mime_type:
        return []
    aliases = {'org.projectluma.Messages': 'messages', 'org.projectluma.Charlie': 'mail',
               'org.projectluma.Notes': 'notes', 'org.projectluma.Photos': 'photos',
               'org.projectluma.Canvas': 'canvas', 'org.projectluma.Write': 'write',
               'org.projectluma.Ari': 'ari'}
    targets, seen = [], set()
    from .application_directory import applications
    for app in applications(subject.mime_type):
        identity = app.get_id()
        if not identity or identity in seen or not app.should_show():
            continue
        if not (app.supports_files() or app.supports_uris()):
            continue
        seen.add(identity)
        app_id = identity.removesuffix('.desktop')
        targets.append(ShareTarget(aliases.get(app_id, app_id), app.get_display_name(), app_id))
    return targets


@dataclass
class Collaborator:
    """Someone on a document: `role` is "owner", "edit", "comment" or "view"."""

    person: Person
    role: str = "edit"


def default_targets(subject: ShareSubject) -> list[ShareTarget]:
    """v70 shTargets: Messages, Mail, Nearby, Notes, then Photos and Canvas for a picture or Write, then Ari."""
    targets = [ShareTarget("messages", "Messages", "org.projectluma.Messages"),
               ShareTarget("mail", "Mail", "org.projectluma.Charlie"),
               ShareTarget("nearby", "Nearby", icon="radar"),
               ShareTarget("notes", "Notes", "org.projectluma.Notes")]
    if subject.image:
        targets += [ShareTarget("photos", "Photos", "org.projectluma.Photos"),
                    ShareTarget("canvas", "Canvas", "org.projectluma.Canvas")]
    elif subject.kind != "link":
        targets.append(ShareTarget("write", "Write", "org.projectluma.Write"))
    targets.append(ShareTarget("ari", "Ari", "org.projectluma.Ari"))
    return targets


def share_actions(subject: ShareSubject) -> list[tuple[str, str, str, str]]:
    """(choice, Lucide icon, label, key hint) for the inset list, by what is shared (v70 shHTML)."""
    if subject.kind == "contact":
        rows = [("copy-link", "link-2", "Copy link", "")] if subject.share_link_available else []
        return rows + [("copy-card", "copy", "Copy contact card", ""), ("save", "folder-down", "Save as a file…", "")]
    if subject.kind == "link":
        return [("copy-link", "link-2", "Copy link", "Ctrl C"), ("code", "qr-code", "Show a code to scan", "")] if subject.share_link_available else []
    return [("copy", "copy", "Copy", "Ctrl C"), ("save", "folder-down", "Save a copy…", ""),
            ("print", "printer", "Print…", "Ctrl P")]


def _first(name: str) -> str:
    return name.split()[0] if name else name


def _m(key: str) -> int:
    return int(bar_tokens.metric("share", key))


# ── floating a sheet at its button ──────────────────────────────────────────

class Floater:
    """A card anchored to a control: beside it on a computer, a drawer on a phone (Esc and outside close it)."""

    def __init__(self, card: Gtk.Widget, *, on_closed: Callable[[], None] | None = None) -> None:
        self.card, self.on_closed = card, on_closed
        self.host: LayerHost | None = None
        self.anchor: Gtk.Widget | None = None
        self._catcher: Gtk.Widget | None = None
        self._keys: Gtk.EventControllerKey | None = None
        self._handle = None
        self.phone = False

    def present(self, anchor: Gtk.Widget, *, width: int, css: str) -> "Floater":
        self.width = width
        host = LayerHost.window_host(anchor)
        bar_tokens.install(host.get_display())
        self.host, self.anchor = host, anchor
        self.card.add_css_class(css)
        anchor.add_css_class("lumaui-share-anchor")
        self.phone = lumaui.is_phone_width(host)
        lumaui.set_css_class(self.card, "phone", self.phone)
        if self.phone:
            if hasattr(self.card, "cap_height"):
                self.card.cap_height(max(0, host.get_height() - 2 * _m("edge")))
            self._handle = host.present_modal(self.card, on_cancel=self._cancelled, drawer=True)
            return self
        catcher = Gtk.Box(hexpand=True, vexpand=True)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_a: self.close())
        catcher.add_controller(click)
        self._catcher = catcher
        host.add_overlay(catcher)
        host.add_overlay(self.card)
        self.card.set_size_request(width, -1)
        self.place()
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        root = anchor.get_root()
        if root is not None:
            root.add_controller(keys)
            self._keys = keys
        lumaui.on_next_frame(self.card, lambda: self.card.add_css_class("shown"))
        return self

    def place(self) -> None:
        """Beside the button: over it in the lower half of the window, under it otherwise; kept inside."""
        if self.phone or self.host is None or self.anchor is None:
            return
        host, card = self.host, self.card
        rect = rect_in(host, self.anchor)
        hw, hh = host.get_width(), host.get_height()
        edge, offset = _m("edge"), _m("offset")
        width = min(self.width, max(0, hw - 2 * edge))
        # GTK measurements include margins. A previous anchored position must
        # not become part of the next content-size calculation after redraw.
        card.set_margin_start(0)
        card.set_margin_top(0)
        card.set_size_request(width, -1)
        if hasattr(card, "cap_height"):
            card.cap_height(max(0, hh - 2 * edge))
        height = min(card.measure(Gtk.Orientation.VERTICAL, width)[1], max(0, hh - 2 * edge))
        # v70 shPlace: end-aligned to the button, over it in the lower half of the
        # window and under it otherwise, and moved to stay `edge` inside.
        up = rect.y > hh * 0.5
        x = max(edge, min(hw - width - edge, rect.x + rect.width - width))
        y = max(edge, rect.y - height - offset) if up else min(hh - height - edge, rect.y + rect.height + offset)
        if host.get_direction() == Gtk.TextDirection.RTL:
            x = hw - x - width
        card.set_halign(Gtk.Align.START)
        card.set_valign(Gtk.Align.START)
        card.set_margin_start(max(0, int(x)))
        card.set_margin_top(max(0, int(y)))
        lumaui.set_css_class(card, "up", up)

    @property
    def is_open(self) -> bool:
        return self.host is not None

    def close(self) -> None:
        if self._handle is not None:
            handle, self._handle = self._handle, None
            handle.close()
            self._finish()
            return
        host = self.host
        if host is None:
            return
        root = host.get_root()
        if self._keys is not None and root is not None:
            root.remove_controller(self._keys)
        self._keys = None
        for widget in (self._catcher, self.card):
            if widget is not None and widget.get_parent() is host:
                host.remove_overlay(widget)
        self._catcher = None
        if self.anchor is not None and self.anchor.get_mapped():
            self.anchor.grab_focus()
        self._finish()

    def _finish(self) -> None:
        if self.anchor is not None:
            self.anchor.remove_css_class("lumaui-share-anchor")
        self.host = None
        if self.on_closed is not None:
            self.on_closed()

    def _cancelled(self) -> None:
        self._handle = None
        self._finish()

    def _key(self, _controller, keyval: int, _code: int, _state: object) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False


# ── the sheet ───────────────────────────────────────────────────────────────

class ShareSheet(Gtk.Box):
    """The share sheet. Open it with `ShareSheet.present(anchor, document=…)`; see the module docstring."""

    __gtype_name__ = "LumaUIShareSheet"
    _open: "ShareSheet | None" = None

    def __init__(self, document: ShareSubject, *, people: Sequence[Person] = (),
                 targets: Sequence[ShareTarget] | None = None, choices: Sequence[str] = ("send-copy",),
                 collaborators: Sequence[Collaborator] = (), owner: Person | None = None,
                 link_access: str = "off", link_for: str = "people",
                 suggest: Callable[[str], Sequence[Person]] | None = None,
                 copy_actions: Sequence[str] | None = None, show_link: bool | None = None,
                 manage_access: bool = True, owner_is_you: bool = True,
                 on_choice: Callable[[str, object], str | None] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.DIALOG)
        if not choices or any(c not in SHARE_CHOICES for c in choices):
            raise ValueError(f"share choices are some of {SHARE_CHOICES}: {choices!r}")
        if "work-together" in choices and document.kind != "doc":
            raise ValueError("only a document (kind='doc') is worked on together")
        if link_access not in [k for k, *_ in LINK_ACCESS] or link_for not in [k for k, *_ in LINK_FOR]:
            raise ValueError("unknown link setting")
        self.add_css_class("lumaui-share")
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Share"])
        self.document, self.people, self.choices = document, list(people), tuple(choices)
        available_actions = {choice for choice, *_rest in share_actions(document)}
        if copy_actions is not None and any(choice not in available_actions for choice in copy_actions):
            raise ValueError("copy actions must be supported by this share subject")
        self.copy_actions = tuple(copy_actions) if copy_actions is not None else None
        self.show_link = document.share_link_available if show_link is None else bool(show_link)
        self.manage_access = bool(manage_access)
        self.owner_is_you = bool(owner_is_you)
        self.targets = list(targets) if targets is not None else installed_targets(document)
        self.collaborators = list(collaborators)
        self.owner = owner or Person("You")
        self.link_access, self.link_for, self.suggest, self.on_choice = link_access, link_for, suggest, on_choice
        self.mode = self.choices[0]
        self.sent: set[str] = set()
        self.query = ""
        self._dismissed = False
        self.floater: Floater | None = None

        self.handle_bar = Gtk.Box(halign=Gtk.Align.CENTER)
        self.handle_bar.add_css_class("lumaui-drawer-handle")
        self.handle_bar.add_css_class("lumaui-share-handle")
        self.append(self.handle_bar)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                           propagate_natural_width=False, vexpand=True)
        self.scroller.add_css_class("lumaui-share-scroll")
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.body.add_css_class("lumaui-share-body")
        self.scroller.set_child(self.body)
        self.append(self.scroller)

        self.body.append(self._header())
        self.mode_switch = None
        if len(self.choices) > 1:
            from .structure_placement import ModeSwitch
            self.mode_switch = ModeSwitch([("work-together", "Work together"), ("send-copy", "Send a copy")],
                                          current=self.mode, on_change=self._mode_changed, label="How to share",
                                          fill=True)
            self.mode_switch.add_css_class("lumaui-share-mode")
            self.body.append(self.mode_switch)
        self.page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.body.append(self.page)
        self._draw()
        if document.mime_type:
            from .application_directory import discover
            self._discovery = discover(document.mime_type, self._receivers_ready)

    def _receivers_ready(self, _apps, error):
        if self._dismissed or error:
            return
        supplied = {target.app_id for target in self.targets}
        self.targets.extend(target for target in installed_targets(self.document)
                            if target.app_id not in supplied)
        if self.mode == "send-copy":
            self._draw()

    def set_targets(self, targets):
        """Publish actual asynchronously discovered receivers while open."""
        if not self._dismissed:
            self.targets = list(targets)
            if self.mode == "send-copy":
                self._draw()

    # ── opening ───────────────────────────────────────────────────────────

    @classmethod
    def present(cls, anchor: Gtk.Widget, *, document: ShareSubject, **options) -> "ShareSheet":
        """Open the sheet at `anchor` (a second press of the same button closes it)."""
        current = cls._open
        if current is not None and current.floater is not None and current.floater.is_open:
            same = current.floater.anchor is anchor
            current.close()
            if same:
                return current
        sheet = cls(document, **options)
        sheet.floater = Floater(sheet, on_closed=sheet._closed)
        cls._open = sheet
        sheet.floater.present(anchor, width=_m("width"), css="lumaui-share-floating")
        GLib.idle_add(lambda: (sheet._focus_first(), False)[1])
        return sheet

    def close(self) -> None:
        self._dismissed = True
        if getattr(self, '_discovery', None):
            self._discovery.cancel()
        if self.floater is not None:
            self.floater.close()

    def cap_height(self, height: int) -> None:
        # The card has its own padding. Bound the actual allocation as well as
        # natural measurement so the last access control remains scrollable.
        content_height = max(0, height - 2 * _m("padding"))
        self.scroller.set_max_content_height(content_height)
        width = max(self.body.measure(Gtk.Orientation.HORIZONTAL, -1)[0], self.get_width())
        self.scroller.set_size_request(-1, min(self.body.measure(Gtk.Orientation.VERTICAL, width)[1], content_height))

    def _closed(self) -> None:
        self._dismissed = True
        if getattr(self, '_discovery', None):
            self._discovery.cancel()
        if ShareSheet._open is self:
            ShareSheet._open = None

    def _focus_first(self) -> None:
        if self.invite is not None:
            self.invite.grab_focus()
        else:
            self.page.child_focus(Gtk.DirectionType.TAB_FORWARD)

    # ── reporting ─────────────────────────────────────────────────────────

    def _choose(self, choice: str, value: object = None):
        result = self.on_choice(choice, value) if self.on_choice is not None else ShareResult(False)
        message = result.message if isinstance(result, ShareResult) else result
        if message and self.floater is not None and self.floater.host is not None:
            from .action_toast import Toast
            # A live list can redraw its original Share button while this card
            # remains open. Feedback belongs to the persistent window layer.
            host = self.floater.host
            if host.get_root() is not None:
                Toast.show(host, message)
        return result

    # ── building ──────────────────────────────────────────────────────────

    def _header(self) -> Gtk.Widget:
        doc = self.document
        head = Gtk.Box()
        head.add_css_class("lumaui-share-head")
        thumb = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, overflow=Gtk.Overflow.HIDDEN)
        thumb.add_css_class("lumaui-share-thumb")
        size = _m("thumb")
        if doc.person is not None:
            thumb.append(PersonAvatar(doc.person.name, size, picture=doc.person.picture, hue=doc.person.hue))
            thumb.add_css_class("person")
        elif doc.picture is not None:
            picture = Gtk.Picture(paintable=doc.picture, content_fit=Gtk.ContentFit.COVER, can_shrink=True)
            picture.set_size_request(size, size)
            thumb.append(picture)
        else:
            thumb.append(_icon(doc.icon or "file", size, glyph=_m("thumb_icon")))
        thumb.set_hexpand(False)
        head.append(thumb)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        title = Gtk.Label(label=doc.title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class("lumaui-share-title")
        self.subtitle = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.subtitle.add_css_class("lumaui-share-sub")
        text.append(title)
        text.append(self.subtitle)
        head.append(text)
        return head

    def _mode_changed(self, mode: str) -> None:
        self.mode = mode
        self._draw()

    def _draw(self) -> None:
        _clear(self.page)
        doc = self.document
        together = self.mode == "work-together"
        self.subtitle.set_label(doc.subtitle if together or doc.kind != "doc" else f"A copy · {doc.subtitle}")
        self.page.append(_divider())
        self.invite = None
        if together:
            self._draw_together()
        else:
            self._draw_copy()
        if self.floater is not None:
            GLib.idle_add(lambda: (self.floater.place(), False)[1])

    def _draw_copy(self) -> None:
        doc = self.document
        self.people_row = _tiles("People", [p for p in self.people if doc.person is None or p.name != doc.person.name],
                                 self._person_tile)
        if self.people:
            self.page.append(self.people_row)
            self.page.append(_divider())
        self.page.append(_tiles("Apps", self.targets, self._target_tile))
        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        actions.add_css_class("lumaui-share-actions")
        self.action_buttons: dict[str, Gtk.Button] = {}
        for choice, icon, label, key in share_actions(doc):
            if self.copy_actions is not None and choice not in self.copy_actions:
                continue
            button = Gtk.Button()
            button.add_css_class("lumaui-share-action")
            line = Gtk.Box()
            glyph = icons.image(icon)
            line.append(glyph)
            name = Gtk.Label(label=label, xalign=0, hexpand=True)
            name.add_css_class("lumaui-share-action-label")
            line.append(name)
            if key:
                hint = Gtk.Label(label=key)
                hint.add_css_class("lumaui-share-key")
                line.append(hint)
            button.set_child(line)
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            button.connect("clicked", lambda b, c=choice, g=glyph, n=name, lbl=label: self._acted(c, g, n, lbl))
            actions.append(button)
            self.action_buttons[choice] = button
        self.page.append(actions)
        if doc.kind == "file" and self.show_link:
            self.page.append(self._link_row(LINK_FOR, self.link_for, "link-for"))

    def _draw_together(self) -> None:
        invite = bar_tokens.Well()
        invite.add_css_class("lumaui-share-invite")
        invite.append(icons.image("user-plus"))
        self.invite = Gtk.Text(hexpand=True, placeholder_text="Add people by name or @username")
        self.invite.set_width_chars(1)
        self.invite.set_sensitive(self.manage_access)
        self.invite.update_property([Gtk.AccessibleProperty.LABEL], ["Add people by name or @username"])
        self.invite.set_input_hints(Gtk.InputHints.NO_SPELLCHECK)
        self.invite.set_text(self.query)
        self.invite.connect("changed", self._query_changed)
        self.invite.connect("activate", lambda _t: self._invite_first())
        invite.append(self.invite)
        invite.set_visible(self.manage_access)
        self.page.append(invite)
        self.suggestions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False)
        self.suggestions.add_css_class("lumaui-share-suggest")
        self.page.append(self.suggestions)
        self.none_label = Gtk.Label(xalign=0, visible=False, wrap=True)
        self.none_label.add_css_class("lumaui-share-none")
        self.page.append(self.none_label)
        people = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        people.add_css_class("lumaui-share-list")
        people.append(self._person_row(Collaborator(self.owner, "owner")))
        for collaborator in self.collaborators:
            people.append(self._person_row(collaborator))
        self.page.append(people)
        if self.show_link:
            self.page.append(self._link_row(LINK_ACCESS, self.link_access, "link-access"))
        self._refresh_suggestions()

    def _person_tile(self, person: Person) -> Gtk.Widget:
        button = _tile_button(person.name)
        face = Gtk.Overlay(child=PersonAvatar(person.name, _m("face"), picture=person.picture, hue=person.hue))
        badge = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.END, visible=person.name in self.sent)
        badge.add_css_class("lumaui-share-sent")
        badge.append(icons.image("check"))
        face.add_overlay(badge)
        face.add_css_class("lumaui-share-face")
        lumaui.set_css_class(button, "sent", person.name in self.sent)
        button.get_child().prepend(face)
        _tile_label(button, _first(person.name))
        button.set_tooltip_text(f"Send to {person.name} in Messages")
        button.update_property([Gtk.AccessibleProperty.LABEL], [f"Send to {person.name} in Messages"])

        def send(_b: Gtk.Button) -> None:
            result = self._choose("send-to", person)
            if not (result.completed if isinstance(result, ShareResult) else bool(result)):
                return
            self.sent.add(person.name)
            button.add_css_class("sent")
            badge.set_visible(True)

        button.connect("clicked", send)
        return button

    def _target_tile(self, target: ShareTarget) -> Gtk.Widget:
        button = _tile_button(target.label)
        button.get_child().prepend(_icon(target.app_id or target.icon or "share-2", _m("app_icon"),
                                         glyph=_m("sys_icon"), tile=True))
        _tile_label(button, target.label)

        def go(_b: Gtk.Button) -> None:
            if target.key == "nearby":
                button.add_css_class("on")
                self._choose("target", target.key)
                return
            result = self._choose("target", target.key)
            if not isinstance(result, ShareResult) or result.completed:
                self.close()

        button.connect("clicked", go)
        return button

    def _acted(self, choice: str, glyph: Gtk.Image, name: Gtk.Label, label: str) -> None:
        result = self._choose(choice)
        completed = result.completed if isinstance(result, ShareResult) else bool(result)
        if choice in ("copy", "copy-link", "copy-card") and completed:
            glyph.set_from_icon_name(icons.icon_name("check"))
            name.set_label("Copied")
            GLib.timeout_add(1600, lambda: (glyph.set_from_icon_name(icons.icon_name(
                {"copy-link": "link-2"}.get(choice, "copy"))), name.set_label(label), False)[-1])
        elif choice in ("save", "print", "code") and result is None:
            self.close()

    def _link_row(self, options, current: str, choice: str) -> Gtk.Widget:
        row = Gtk.Box()
        row.add_css_class("lumaui-share-link")
        entry = next(o for o in options if o[0] == current)
        glyph = icons.image("lock" if current in ("off", "people") else "globe")
        glyph.set_valign(Gtk.Align.CENTER)
        glyph.add_css_class("lumaui-share-link-icon")
        row.append(glyph)
        picker = Gtk.Button(hexpand=True)
        picker.add_css_class("lumaui-share-link-picker")
        picker.update_property([Gtk.AccessibleProperty.HAS_POPUP, Gtk.AccessibleProperty.LABEL],
                               [True, f"Who the link works for: {entry[1]}"])
        grid = Gtk.Grid(column_spacing=6)
        title = Gtk.Label(label=entry[1], xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class("lumaui-share-link-title")
        sub = Gtk.Label(label=entry[2], xalign=0, ellipsize=Pango.EllipsizeMode.END)
        sub.add_css_class("lumaui-share-link-sub")
        grid.attach(title, 0, 0, 1, 1)
        grid.attach(sub, 0, 1, 1, 1)
        grid.attach(icons.image("chevrons-up-down"), 1, 0, 1, 2)
        picker.set_child(grid)

        def pick(key: str) -> None:
            result = self._choose(choice, key)
            if isinstance(result, ShareResult) and not result.completed:
                return
            if choice == "link-access":
                self.link_access = key
            else:
                self.link_for = key
            self._draw()

        picker.connect("clicked", lambda b: FloatingMenu(
            [MenuItem(label, note=None, selected=key == current, on_activate=lambda k=key: pick(k))
             for key, label, _sub in options], label="Who the link works for").popup(b))
        row.append(picker)
        chip = Gtk.Button(valign=Gtk.Align.CENTER)
        chip.add_css_class("lumaui-share-chip")
        chip_line = Gtk.Box()
        chip_glyph = icons.image("link-2")
        chip_text = Gtk.Label(label="Copy link")
        chip_line.append(chip_glyph)
        chip_line.append(chip_text)
        chip.set_child(chip_line)

        def copied(_b: Gtk.Button) -> None:
            result = self._choose("copy-link")
            if not (result.completed if isinstance(result, ShareResult) else bool(result)):
                return
            chip.add_css_class("done")
            chip_glyph.set_from_icon_name(icons.icon_name("check"))
            chip_text.set_label("Copied")

            def back() -> bool:
                chip.remove_css_class("done")
                chip_glyph.set_from_icon_name(icons.icon_name("link-2"))
                chip_text.set_label("Copy link")
                return False

            GLib.timeout_add(1600, back)

        chip.connect("clicked", copied)
        row.append(chip)
        return row

    def _person_row(self, collaborator: Collaborator) -> Gtk.Widget:
        person = collaborator.person
        row = Gtk.Box()
        row.add_css_class("lumaui-share-person")
        row.append(PersonAvatar(person.name, _m("person_face"), picture=person.picture, hue=person.hue))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        owner = collaborator.role == "owner"
        name = Gtk.Label(label="You" if owner and self.owner_is_you else person.name, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        name.add_css_class("lumaui-share-person-name")
        text.append(name)
        if person.username:
            handle = Gtk.Label(label=f"@{person.username}", xalign=0, ellipsize=Pango.EllipsizeMode.END)
            handle.add_css_class("lumaui-share-person-sub")
            text.append(handle)
        row.append(text)
        if collaborator.role == "owner":
            owner = Gtk.Label(label="Owner")
            owner.add_css_class("lumaui-share-owner")
            row.append(owner)
            return row
        role_label = dict(SHARE_ROLES)[collaborator.role]
        role = Gtk.Button(valign=Gtk.Align.CENTER)
        role.set_sensitive(self.manage_access)
        role.add_css_class("lumaui-share-role")
        line = Gtk.Box()
        line.append(Gtk.Label(label=role_label))
        line.append(icons.image("chevron-down"))
        role.set_child(line)
        role.update_property([Gtk.AccessibleProperty.HAS_POPUP, Gtk.AccessibleProperty.LABEL],
                             [True, f"{person.name}: {role_label}"])

        def set_role(key: str) -> None:
            result = self._choose("role", (collaborator, key))
            if isinstance(result, ShareResult) and not result.completed:
                return
            collaborator.role = key
            self._draw()

        def remove() -> None:
            result = self._choose("remove", collaborator)
            if isinstance(result, ShareResult) and not result.completed:
                return
            self.collaborators.remove(collaborator)
            self._draw()

        role.connect("clicked", lambda b: FloatingMenu(
            [MenuItem(label, selected=key == collaborator.role, on_activate=lambda k=key: set_role(k))
             for key, label in SHARE_ROLES] + [None, MenuItem("Remove", on_activate=remove)],
            label=f"What {_first(person.name)} can do").popup(b))
        row.append(role)
        return row

    # ── inviting ──────────────────────────────────────────────────────────

    def _query_changed(self, text: Gtk.Text) -> None:
        self.query = text.get_text()
        self._refresh_suggestions()

    def set_suggestions(self, query: str, people: Sequence[Person], status: str = "") -> bool:
        """Apply asynchronous directory results only to the still-current query.

        Call on GTK's main context. Closing/replacing this sheet or typing a
        newer query prevents an old network reply from selecting a person.
        """
        if self._dismissed or not self.manage_access or query.strip() != self.query.strip() or self.invite is None:
            return False
        self._async_suggestions = (query.strip(), list(people), status)
        self._refresh_suggestions()
        return True

    def _matches(self) -> list[Person]:
        if not self.manage_access:
            return []
        query = self.query.strip()
        def identity(person: Person) -> tuple[str, str]:
            # Display names are not unique. Prefer the directory's username so
            # two people with the same name remain independently selectable.
            if person.username:
                return ("username", person.username.lstrip("@").casefold())
            return ("name", person.name.casefold())
        taken = {identity(self.owner), *(identity(c.person) for c in self.collaborators)}
        answer = getattr(self, "_async_suggestions", None)
        people = (self.people if not query else
                  answer[1] if answer is not None and answer[0] == query else
                  self.suggest(query) if self.suggest else [])
        matches = []
        for person in people:
            key = identity(person)
            if key not in taken:
                matches.append(person)
                taken.add(key)
                if len(matches) == 4:
                    break
        return matches

    def _refresh_suggestions(self) -> None:
        if self.invite is None:
            return
        _clear(self.suggestions)
        matches = self._matches()
        for index, person in enumerate(matches):
            button = Gtk.Button()
            button.add_css_class("lumaui-share-suggestion")
            lumaui.set_css_class(button, "first", index == 0)
            line = Gtk.Box()
            line.append(PersonAvatar(person.name, _m("suggest_face"), picture=person.picture, hue=person.hue))
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            name = Gtk.Label(label=person.name, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            name.add_css_class("lumaui-share-person-name")
            text.append(name)
            if person.username:
                sub = Gtk.Label(label=f"@{person.username}", xalign=0, ellipsize=Pango.EllipsizeMode.END)
                sub.add_css_class("lumaui-share-person-sub")
                text.append(sub)
            line.append(text)
            if index == 0:
                hint = Gtk.Label(label="Return to add")
                hint.add_css_class("lumaui-share-key")
                line.append(hint)
            button.set_child(line)
            button.connect("clicked", lambda _b, p=person: self._invite(p))
            self.suggestions.append(button)
        self.suggestions.set_visible(bool(matches))
        query = self.query.strip()
        answer = getattr(self, "_async_suggestions", None)
        status = answer[2] if answer is not None and answer[0] == query else ""
        self.none_label.set_label(status or (f"No one called “{query}” is on Luma yet." if query and not matches else ""))
        self.none_label.set_visible(bool(query) and not matches)

    def _invite_first(self) -> None:
        matches = self._matches()
        if matches:
            self._invite(matches[0])

    def _invite(self, person: Person) -> None:
        collaborator = Collaborator(person, "edit")
        result = self._choose("invite", collaborator)
        if isinstance(result, ShareResult) and not result.completed:
            return
        self.collaborators.append(collaborator)
        self.query = ""
        self._draw()
        if self.invite is not None:
            self.invite.grab_focus()


# ── small pieces ────────────────────────────────────────────────────────────

def _clear(box: Gtk.Box) -> None:
    child = box.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        box.remove(child)
        child = following


def _divider() -> Gtk.Widget:
    rule = Gtk.Box()
    rule.add_css_class("lumaui-share-divider")
    return rule


def _icon(name: str, size: int, *, glyph: int, tile: bool = False) -> Gtk.Widget:
    """An app's own icon when the theme has it; otherwise a Lucide glyph on a raised tile."""
    display = Gdk.Display.get_default()
    theme = Gtk.IconTheme.get_for_display(display) if display is not None else None
    if "." in name and theme is not None and theme.has_icon(name):
        image = Gtk.Image.new_from_icon_name(name)
        image.set_pixel_size(size)
        image.add_css_class("lumaui-share-app-icon")
        return image
    box = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    box.add_css_class("lumaui-share-sys-icon" if tile else "lumaui-share-thumb-icon")
    box.set_size_request(size, size)
    image = icons.image(name if "." not in name else "app-window")
    image.set_pixel_size(glyph)
    image.set_hexpand(True)
    image.set_halign(Gtk.Align.CENTER)
    box.append(image)
    return box


def _tile_button(name: str) -> Gtk.Button:
    button = Gtk.Button()
    button.add_css_class("lumaui-share-tile")
    button.set_child(Gtk.Box(orientation=Gtk.Orientation.VERTICAL))
    button.update_property([Gtk.AccessibleProperty.LABEL], [name])
    return button


def _tile_label(button: Gtk.Button, text: str) -> None:
    label = Gtk.Label(label=text, ellipsize=Pango.EllipsizeMode.END, max_width_chars=9)
    label.add_css_class("lumaui-share-tile-label")
    button.get_child().append(label)


def _tiles(name: str, items: Sequence[object], make: Callable[[object], Gtk.Widget]) -> Gtk.Widget:
    """A row of tiles that scrolls sideways, as v70's .shscroll (the far edge fades)."""
    row = Gtk.Box(accessible_role=Gtk.AccessibleRole.LIST)
    row.add_css_class("lumaui-share-tiles")
    row.update_property([Gtk.AccessibleProperty.LABEL], [name])
    for item in items:
        row.append(make(item))
    scroller = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                  child=row)
    scroller.add_css_class("lumaui-share-strip")
    # v70 masks the strip's last 34 px (mask-image); GTK has no mask, so the sheet's own
    # surface fades in over that edge, which reads the same on the solid sheet.
    strip = Gtk.Overlay(child=scroller)
    fade = Gtk.Box(halign=Gtk.Align.END, can_target=False)
    fade.add_css_class("lumaui-share-strip-fade")
    strip.add_overlay(fade)
    return strip


# ── v71: the share panel in the grown bar ───────────────────────────────────

#: The panel's ways to send: (glyph, words, choice).
PANEL_TARGETS: tuple[tuple[str, str, str], ...] = (
    ("message-square", "Messages", "messages"), ("mail", "Email", "mail"),
    ("link", "Copy link", "copy-link"), ("radio-tower", "Nearby", "nearby"))


class SharePanel(Gtk.Box):
    """Share as the bar grown (v71 `.fexp.fshare2`): "Send to", recent faces with first names, then tiles.

    `on_choice(choice, value)`: "send-to" with the Person, or a target's choice ("messages", "mail",
    "copy-link", "nearby") with None. A returned line is shown as a toast. The panel folds after a choice.

        center.grow("share", SharePanel(people=recent, on_choice=shared))
        BarAction("share-2", tooltip="Share", panel=lambda: SharePanel(people=recent, on_choice=shared))
    """

    __gtype_name__ = "LumaUISharePanel"

    def __init__(self, *, people: Sequence[Person] = (), on_choice: Callable[[str, object], object] | None = None,
                 targets: Sequence[tuple[str, str, str]] = PANEL_TARGETS, heading: str = "Send to") -> None:
        from .bar_panel import BarTile, BarTiles, PanelHeading
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("lumaui-share-panel")
        self.on_choice = on_choice
        self.append(PanelHeading(heading))
        if people:
            faces = Gtk.Box()
            faces.add_css_class("lumaui-share-panel-people")
            for index, person in enumerate(list(people)[:5]):
                if index:
                    faces.append(Gtk.Box(hexpand=True))  # spread edge to edge (.fshp: space-between)
                button = Gtk.Button()
                button.add_css_class("lumaui-share-panel-person")
                stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER)
                stack.append(PersonAvatar(person.name, 48, picture=person.picture, hue=person.hue))
                stack.append(Gtk.Label(label=_first(person.name)))
                button.set_child(stack)
                button.update_property([Gtk.AccessibleProperty.LABEL], [_first(person.name)])
                button.set_tooltip_text(f"Send to {person.name}")
                button.connect("clicked", lambda _b, p=person: self._chose("send-to", p))
                faces.append(button)
            self.append(faces)
        self.tiles = BarTiles([BarTile(icon, words, lambda c=choice: self._chose(c, None), closes=False)
                               for icon, words, choice in targets], columns=min(4, len(targets)) or 1)
        self.append(self.tiles)

    def _chose(self, choice: str, value: object) -> None:
        from .bar_panel import _fold_from
        _fold_from(self)
        line = self.on_choice(choice, value) if self.on_choice is not None else None
        if isinstance(line, str) and line:
            root = self.get_root()
            if root is not None:
                from .action_toast import Toast
                Toast.show(root.get_child() or root, line, kind="sent" if choice == "send-to" else "done")
