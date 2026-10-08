# SPDX-License-Identifier: Apache-2.0
"""LumaUI rows: people together. AvatarStack, GroupFace and a face with presence.

    AvatarStack(["Priya Raman", "Sam Ortiz", "Ana Lima"], size="row")     # Tasks' list, a note's sharers
    AvatarStack(people, size="header", max=4, live={"Priya Raman"})       # a document's header, CornerPill(people=)
    GroupFace(["Priya Raman", "Sam Ortiz"], size=46)                       # a group conversation's face
    PresenceFace("Priya Raman", size=46, online=True)                      # a face with its green dot

v70: `.nfaces` (16, overlapping 5, ringed 1.5), `.tkstk` (18, 6, 2), `.pnwho
.pnstk` and `.sutopr` (28, 8, 2): faces left to right, each ringed in the
ground it stands on so the overlap reads as a cut. More people than `max` end
in a "+N" face. `live` names the people here now: their ring turns green.
`.gav`/`.pnstk` (GroupFace): two faces 0.7 of the lead, corners opposite.
`.crow .onl` (PresenceFace): a 12 px green dot on the face's lower right,
ringed 2.5 in the ground.

A person is a name, a `(name, picture)` pair, or anything with `.name` (and
optionally `.picture`), such as `Person`. Sizes are named, never pixels.
"""
from __future__ import annotations

from typing import Iterable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from . import rows_tokens  # noqa: E402

__all__ = ["AvatarStack", "GroupFace", "PresenceFace", "STACK_SIZES", "stack_label"]

#: The sizes an AvatarStack comes in: small (a tree row), row (a sidebar row), header.
STACK_SIZES = ("small", "row", "header", "document")


def _person(entry: object) -> tuple[str, Gdk.Paintable | None]:
    if isinstance(entry, str):
        return entry, None
    if isinstance(entry, tuple):
        return str(entry[0]), (entry[1] if len(entry) > 1 else None)
    return str(getattr(entry, "name", "") or ""), getattr(entry, "picture", None)


def stack_label(names: Sequence[str]) -> str:
    """How a stack is read aloud: "Priya", "Priya and Sam", "Priya, Sam and 2 others"."""
    names = [n.split()[0] if n.strip() else "Someone" for n in names]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    if len(names) == 3:
        return f"{names[0]}, {names[1]} and {names[2]}"
    rest = len(names) - 2
    return f"{names[0]}, {names[1]} and {rest} others"


def _face(name: str, size: int, picture: Gdk.Paintable | None, hue: float | None = None, *,
          mark: bool = False, colour: str | None = None, stranger: bool = False) -> Gtk.Widget:
    from .content_cards import PersonAvatar
    return PersonAvatar(name, size, picture=picture, hue=hue, mark=mark, colour=colour, stranger=stranger)


class _Overlap(Gtk.Widget):
    """Children laid left to right, each `overlap` px over the one before (GTK margins cannot be negative)."""

    def __init__(self, overlap: int) -> None:
        super().__init__()
        self._overlap = overlap

    def _children(self) -> list[Gtk.Widget]:
        out, child = [], self.get_first_child()
        while child is not None:
            if child.get_visible():
                out.append(child)
            child = child.get_next_sibling()
        return out

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        children = self._children()
        sizes = [c.measure(orientation, -1)[1] for c in children]
        if not sizes:
            return 0, 0, -1, -1
        if orientation == Gtk.Orientation.HORIZONTAL:
            total = sum(sizes) - self._overlap * (len(sizes) - 1)
        else:
            total = max(sizes)
        return total, total, -1, -1

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        x = 0
        for child in self._children():
            w = child.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
            h = child.measure(Gtk.Orientation.VERTICAL, -1)[1]
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = x, (height - h) // 2, w, h
            child.size_allocate(rect, -1)
            x += w - self._overlap

    def do_snapshot(self, snapshot) -> None:
        # Later faces over earlier ones, as v70's DOM order paints them.
        for child in self._children():
            self.snapshot_child(child, snapshot)

    def do_dispose(self) -> None:
        child = self.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            child.unparent()
            child = following


class AvatarStack(_Overlap):
    """People together: overlapping faces, "+N" past `max`, the ones here now ringed green."""

    __gtype_name__ = "LumaUIAvatarStack"

    def __init__(self, people: Iterable[object], *, size: str = "row", max: int = 3,  # noqa: A002 - the API's word
                 live: Iterable[str] | None = None, face: int | None = None, hues: Sequence[float | None] = (),
                 overlap: int | None = None) -> None:
        """`face`: faces of that many pixels overlapping by a third of one, over the named size's (v71
        Charlie's subject: 36 by 12; its phone island: 30 by 10). `hues`: each face's hue, in order.
        `overlap`: how far each face sits over the one before (v71's .crstk: two 36s in a 38 box, 34)."""
        if size not in STACK_SIZES:
            raise ValueError(f"AvatarStack size must be one of {', '.join(STACK_SIZES)}, not {size!r}")
        if max < 1:
            raise ValueError("AvatarStack shows at least one face")
        tokens = rows_tokens.group("avatar_stack")
        super().__init__(overlap if overlap is not None else
                         round(face / 3) if face else int(rows_tokens.value("avatar_stack", f"{size}_overlap")))
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.CENTER)
        self.add_css_class("lumaui-avatar-stack")
        self.add_css_class(size)
        self.size = size
        self._face_size = int(face) if face else int(tokens.get(size, rows_tokens.value("avatar_stack", size)))
        self._max = max
        self._hues = list(hues)
        self.set_people(people, live=live)

    def set_people(self, people: Iterable[object], *, live: Iterable[str] | None = None) -> None:
        """Replace who is shown, keeping the stack in place."""
        child = self.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            child.unparent()
            child = following
        entries = [_person(p) for p in people]
        here = set(live or ())
        self.names = [name for name, _ in entries]
        shown = entries if len(entries) <= self._max else entries[: self._max - 1]
        for index, (name, picture) in enumerate(shown):
            hue = self._hues[index] if index < len(self._hues) else None
            face = _face(name, self._face_size, picture, hue)
            face.add_css_class("lumaui-stack-face")
            if name in here:
                face.add_css_class("live")
            face.set_parent(self)
        extra = len(entries) - len(shown)
        if extra > 0:
            more = Gtk.Label(label=f"+{extra}", width_request=self._face_size, height_request=self._face_size,
                             accessible_role=Gtk.AccessibleRole.PRESENTATION)
            more.add_css_class("lumaui-stack-face")
            more.add_css_class("lumaui-stack-more")
            more.set_parent(self)
        self.set_visible(bool(entries))
        spoken = stack_label(self.names)
        if here & set(self.names):
            spoken += f", {len(here & set(self.names))} here now"
        self.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
        self.set_tooltip_text(", ".join(self.names) or None)
        self.queue_resize()


class GroupFace(Gtk.Widget):
    """A group's face: its first two people, 0.7 of the lead each, corners opposite (v70 .gav, .pnstk)."""

    __gtype_name__ = "LumaUIGroupFace"

    def __init__(self, people: Sequence[object], *, size: int = 46, decorative: bool = False,
                 mark: bool = False, hues: Sequence[float | None] = (), colours: Sequence[str | None] = (),
                 strangers: Sequence[bool] = ()) -> None:
        # `decorative`: inside a row that already names the group, the face says nothing of its own.
        # `mark`: senders that are not people (v71 Charlie's Updates bundle, `.crstk.sq`): PersonAvatar
        # marks, 26 on 38. `hues`: each face's own hue, in order (None takes the name's).
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION if decorative else Gtk.AccessibleRole.IMG)
        self.add_css_class("lumaui-group-face")
        self.size = size
        scale = rows_tokens.value("row_lead", "group_scale")
        # v70 .gav: 32.2 on 46 (0.7); .pnstk: 28 on 38 (Phone rounds to its 28 px face).
        self._inner = round(size * scale) if size != 38 else (26 if mark else 28)
        entries = [_person(p) for p in people][:2] or [("", None)]
        self.names = [name for name, _ in entries]
        for index, (name, picture) in enumerate(entries):
            hue = hues[index] if index < len(hues) else None
            colour = colours[index] if index < len(colours) else None
            stranger = bool(strangers[index]) if index < len(strangers) else False
            face = _face(name, self._inner, picture, hue, mark=mark, colour=colour, stranger=stranger)
            face.add_css_class("lumaui-group-member")
            face.add_css_class("first" if index == 0 else "second")
            face.set_parent(self)
        if not decorative:
            self.update_property([Gtk.AccessibleProperty.LABEL], [stack_label([_person(p)[0] for p in people])])

    def do_measure(self, _orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        return self.size, self.size, -1, -1

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        child, index = self.get_first_child(), 0
        while child is not None:
            rect = Gdk.Rectangle()
            rect.width = rect.height = self._inner
            rect.x = 0 if index == 0 else width - self._inner
            rect.y = 0 if index == 0 else height - self._inner
            child.size_allocate(rect, -1)
            child, index = child.get_next_sibling(), index + 1

    def do_dispose(self) -> None:
        child = self.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            child.unparent()
            child = following


class PresenceFace(Gtk.Widget):
    """A face with presence: the green dot on its lower right when the person is online (v70 .crow .onl)."""

    __gtype_name__ = "LumaUIPresenceFace"

    def __init__(self, name: str, *, size: int = 46, picture: Gdk.Paintable | None = None,
                 online: bool = False, face: Gtk.Widget | None = None, decorative: bool = False,
                 hue: float | None = None) -> None:
        """`hue` (0-360) is the person's own colour for an initials face (v71 av(k): oklch 0.62 0.12 h)."""
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION if decorative else Gtk.AccessibleRole.IMG)
        self._decorative = decorative
        self.add_css_class("lumaui-presence-face")
        self.size = size
        self.face = face if face is not None else _face(name, size, picture, hue)
        self.face.set_parent(self)
        self.dot = Gtk.Box(accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.dot.add_css_class("lumaui-presence")
        self.dot.set_parent(self)
        self._name = name
        self.set_online(online)

    @property
    def online(self) -> bool:
        return self.dot.get_visible()

    def set_online(self, online: bool) -> None:
        self.dot.set_visible(bool(online))
        if not self._decorative:
            self.update_property([Gtk.AccessibleProperty.LABEL],
                                 [f"{self._name}, online" if online else self._name])

    def do_measure(self, _orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        return self.size, self.size, -1, -1

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        self.face.allocate(width, height, baseline, None)
        dot = int(rows_tokens.value("row_lead", "presence"))
        rect = Gdk.Rectangle()
        # v70: right -1, bottom -1 of the face.
        rect.x, rect.y, rect.width, rect.height = width - dot + 1, height - dot + 1, dot, dot
        self.dot.size_allocate(rect, -1)

    def do_dispose(self) -> None:
        for widget in (self.face, self.dot):
            if widget.get_parent() is self:
                widget.unparent()
