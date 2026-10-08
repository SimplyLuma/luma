#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""LumaUI Gallery: every LumaUI part, live, in light, dark, high contrast and at phone width.

A developer tool, never shipped: nothing in packaging installs it. It mirrors
luma-next-70.html?app=lumaui, one page per part. Parts F2 and F3 have not
built yet show a "coming" page with the API their stub module plans.

    python3 src/luma-platform/tools/lumaui-gallery/lumaui_gallery.py [--page toast]
        [--appearance light|dark|high-contrast] [--phone] [--screenshot out.png]

Run from a checkout it finds the kit beside it; in the ThinkPad preview the
run script puts the kit on PYTHONPATH.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for candidate in (HERE.parent.parent / "appkit", HERE):
    if (candidate / "luma_appkit").is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gtk, Pango  # noqa: E402

from luma_appkit import (  # noqa: E402
    CategoryPill, CountBadge, DestructiveDialog, LayerHost, StackedButton, StackedButtons,
    Toast, ToastHost, TypeLabel, apply_type, install_lumaui, lumaui_icon, CATEGORIES,
)
from luma_appkit import (  # noqa: E402  (structure, F2)
    AddRow, Column, CornerPill, DetailsItem, DetailsPane, DetailsRow, ModeSwitch, NavigationTrailBar,
    Selection, SidebarFoot, SidebarToggle, TableHeader,
)
from luma_appkit import lumaui  # noqa: E402
from luma_appkit import (  # noqa: E402
    SEPARATOR, SPACER, ContentLitHeader, HeroTitleField, PersonAvatar, StatusPill, AccountCard, ActionCenter, ActionEditor, BarAction, BarChip, BarContext, BarPrompt, Card,
    ContactActions, ContactCard, ContentLitCard, EventCard, FileCard, MessageBubble, OpenButton, OpenInMenu, Person,
    PlaceCard, PlaceResult, PlaceSearch, SelectionBubble, SongCard, TextField, rank_places,
)

APP_ID = "org.projectluma.LumaUIGallery"
PHONE_WIDTH = 375

# (key, title, Lucide glyph, family, module for a part still to come) in v70's order.
PAGES = [
    ("type", "Type scale", "type", "content", None),
    ("count", "Count badge", "hash", "content", None),
    ("details", "Details pane", "panel-right", "structure", "structure_details"),
    ("stack", "Stacked button", "square-stack", "action", None),
    ("parity", "Contact actions and mini cards", "users", "content", None),
    ("bubble", "Selection bubble", "highlighter", "action", None),
    ("ac", "Action center", "panel-bottom", "action", None),
    ("place", "Placement and modes", "layout-template", "structure", "structure_placement"),
    ("open", "Open in and file card", "file-text", "content", None),
    ("confirm", "Destructive dialog", "triangle-alert", "action", None),
    ("toast", "Toast", "check", "action", None),
    ("cat", "Category pill", "tag", "content", None),
    ("table", "Table header and selection", "table", "structure", "structure_table"),
    ("foot", "Sidebar foot and toggle", "list-filter", "structure", "structure_sidebar"),
    ("psearch", "Place search", "map-pin", "content", None),
    ("acct", "Account and lit cards", "user", "content", None),
    ("drawer", "Menu drawer", "panel-bottom", "structure", "structure_drawer"),
    ("trail", "Navigation trail", "chevron-left", "structure", "structure_trail"),
    ("field", "Field", "text-cursor-input", "content", None),
    ("bubblemsg", "Message bubble", "message-square", "content", None),
    ("hero", "Lit header, hero title and status", "user", "content", None),
]
APPEARANCES = ("light", "dark", "high-contrast")

GALLERY_CSS = """
.gallery-framed { font-family: Figtree, Inter, Cantarell, sans-serif; font-size: 13px; color: @luma_ink; }
.gallery-framed .gallery-nav { background: none; }
.gallery-framed .gallery-bar { border-bottom: 1px solid @luma_line; }
.gallery-framed .gallery-stage-frame { background: none; }
.gallery-root { background: @luma_window; color: @luma_ink; font-family: Figtree, Inter, Cantarell, sans-serif; font-size: 13px; }
.gallery-bar { padding: 8px 12px; border-spacing: 8px; border-bottom: 1px solid @luma_line; }
.gallery-bar label.gallery-name { font-weight: 650; }
.gallery-nav { background: @luma_window; }
.gallery-nav row { padding: 6px 10px; border-radius: 10px; margin: 1px 8px; color: @luma_ink; background: none; }
.gallery-nav row:hover { background: @luma_hover; }
.gallery-nav row:selected { background: @luma_selected; color: @luma_ink; }
.gallery-nav row.coming label { color: @luma_muted; }
.gallery-stage-frame { background: @luma_content; }
.gallery-stage-frame.phone { border-left: 1px solid @luma_line; border-right: 1px solid @luma_line; }
.gallery-page { padding: 28px; border-spacing: 16px; }
.gallery-lede { color: @luma_ink_secondary; }
.gallery-code { font-family: monospace; font-size: 11px; color: @luma_muted; }
.gallery-row { padding: 8px 12px; border-radius: 10px; border-spacing: 10px; min-width: 240px; }
.gallery-row.on { background: @luma_selected; }
.gallery-demo { border-spacing: 10px; }
button.gallery-button { min-height: 32px; padding: 0 12px; border-radius: 10px; border: none; box-shadow: none; background: @luma_control_fill; color: @luma_ink; }
button.gallery-button:hover { background: @luma_control_fill_hover; }
button.gallery-button:focus-visible { outline: 2px solid @luma_focus_ring; outline-offset: 2px; }
togglebutton.gallery-toggle, button.gallery-toggle { min-height: 28px; padding: 0 10px; border-radius: 9px; border: none; box-shadow: none; background: none; color: @luma_ink_secondary; }
togglebutton.gallery-toggle:checked, button.gallery-toggle:checked { background: @luma_chip; color: @luma_ink; box-shadow: 0 0 0 1px @luma_chip_ring; }
.gallery-coming { padding: 20px; border-radius: 16px; background: @luma_fill; border-spacing: 8px; }
.gallery-coming label.gallery-owner { font-weight: 650; color: @luma_accent_ink; }
.gallery-isle { min-height: 400px; border-radius: 16px; background: @luma_island; box-shadow: 0 0 0 1px @luma_line; }
.gallery-isle-content { padding: 22px 24px; border-spacing: 4px; }
.gallery-side { padding: 10px; border-radius: 16px; background: @luma_window; box-shadow: 0 0 0 1px @luma_line; border-spacing: 2px; }
.gallery-sky { padding: 14px; border-radius: 18px; border-spacing: 10px; background-image: linear-gradient(to bottom, #9ec9f0, #cfe6f7); }
.gallery-sky.dusk { background-image: linear-gradient(to bottom, #f3c68f, #f7e2c4); }
.gallery-textview, .gallery-textview > text { background: none; font-size: 15px; }
.gallery-frame { background: @luma_window; border-radius: 16px; padding: 8px 0 0 8px; box-shadow: inset 0 0 0 1px @luma_line; }
.gallery-frame .gallery-island { margin: 0 8px 8px 0; padding: 22px 24px; border-spacing: 4px; }
.gallery-tools { margin-bottom: 20px; padding: 8px 14px; border-radius: 16px; background: @luma_bar_surface; box-shadow: 0 0 0 1px @luma_bar_ring; }
.gallery-cell { font-weight: 500; }
.gallery-cell-dim { color: @luma_muted; }
list.gallery-table, list.gallery-nav-mini, .gallery-footside list { background: none; color: @luma_ink; }
list.gallery-table > row { min-height: 36px; border-radius: 10px; }
list.gallery-table > row > box { padding: 0 12px; border-spacing: 8px; }
.gallery-song { min-height: 38px; border-radius: 10px; border-spacing: 8px; }
.gallery-song:hover { background: @luma_hover; }
.gallery-columns > .gallery-column { min-width: 100px; padding: 2px 8px; border-right: 1px solid @luma_line; border-spacing: 2px; }
.gallery-columns > .gallery-column:last-child { border-right: none; }
button.gallery-step { min-height: 32px; padding: 0 10px; border-radius: 10px; border: none; color: @luma_ink_secondary; }
button.gallery-step:not(.lumaui-selected):not(.lumaui-trail) { background: none; box-shadow: none; }
.gallery-footside { min-width: 240px; min-height: 310px; padding: 10px; border-radius: 16px; background: @luma_window; box-shadow: inset 0 0 0 1px @luma_line; }
.gallery-footside list > row, list.gallery-nav-mini > row { min-height: 34px; padding: 0 10px; border-radius: 10px; margin-bottom: 1px; }
.gallery-memo { padding: 6px 0; }
.gallery-titlerow { min-height: 44px; padding: 0 14px; }
.gallery-footsidebar { min-width: 176px; padding-right: 8px; }
label.gallery-avatar { min-width: 32px; min-height: 32px; border-radius: 16px; background: @luma_control_fill; font-weight: 650; }
"""


def _label(text: str, *css: str, wrap: bool = False, xalign: float = 0) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=xalign, wrap=wrap)
    if wrap:
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    for name in css:
        label.add_css_class(name)
    return label


def _button(text: str, icon: str | None, callback) -> Gtk.Button:
    button = Gtk.Button()
    button.add_css_class("gallery-button")
    box = Gtk.Box(spacing=8)
    if icon:
        box.append(Gtk.Image(icon_name=lumaui_icon(icon)))
    box.append(Gtk.Label(label=text))
    button.set_child(box)
    button.connect("clicked", lambda _b: callback(button))
    return button


def _page(title: str, lede: str, api: str) -> tuple[Gtk.Box, Gtk.Box]:
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    page.add_css_class("gallery-page")
    heading = TypeLabel(title, role="title-1")
    page.append(heading)
    page.append(_label(lede, "gallery-lede", wrap=True))
    page.append(_label(api, "gallery-code", wrap=True))
    demo = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    demo.add_css_class("gallery-demo")
    page.append(demo)
    return page, demo


def _flow(*children: Gtk.Widget) -> Gtk.FlowBox:
    flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=10, row_spacing=10,
                       max_children_per_line=6, homogeneous=False)
    for child in children:
        flow.append(child)
    return flow


# ── pages ───────────────────────────────────────────────────────────────────

def page_type() -> Gtk.Widget:
    page, demo = _page("Type scale", "Figtree, five roles and a label. Numbers that change are tabular.",
                       'apply_type(label, "caption") · TypeLabel("9:41", role="display", unit=":07 AM")')
    for code, text, role, unit in (
        ("display 72/600", "9:41", "display", ":07 AM"),
        ("title 1 · 24/650", "Launch walkthrough", "title-1", None),
        ("title 2 · 15/650", "Lisbon", "title-2", None),
        ("body · 13/400", "Light rain from around 6 PM, gone by 9 PM.", "body", None),
        ("numeric · 30/500", "1,248", "numeric", "mph"),
        ("caption · 11.5/500", "Today · +3 h", "caption", None),
        ("label · 11.5/600", "People", "label", None),
    ):
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        row.append(_label(code, "gallery-code"))
        row.append(TypeLabel(text, role=role, unit=unit, wrap=True))
        demo.append(row)
    return page


def page_count() -> Gtk.Widget:
    page, demo = _page("Count badge", "A pill with tabular numbers: neutral for totals, accent for unread or "
                       "needs attention; never 0.", "CountBadge(1248) · CountBadge(3, attention=True)")
    for glyph, name, count, attention, on in (("inbox", "Inbox", 3, True, True), ("file-text", "Drafts", 1, False, False),
                                              ("download", "Updates", 12, True, False), ("archive", "Archive", 1248, False, False),
                                              ("bell", "Unread", 120, True, False), ("trash-2", "Trash", 0, False, False)):
        row = Gtk.Box(spacing=10)
        row.add_css_class("gallery-row")
        if on:
            row.add_css_class("on")
        row.append(Gtk.Image(icon_name=lumaui_icon(glyph)))
        row.append(_label(name))
        spacer = Gtk.Box(hexpand=True)
        row.append(spacer)
        row.append(CountBadge(count, attention=attention))
        row.set_halign(Gtk.Align.START)
        row.set_size_request(260, -1)
        demo.append(row)
    return page


def page_stack() -> Gtk.Widget:
    page, demo = _page("Stacked button", "Icon on top, label below, on the raised chip. Equal pairs or trios; "
                       "small for tight panes; danger is red and still confirms.",
                       'StackedButtons([StackedButton("log-out", "Leave"), StackedButton("trash-2", "Delete", danger=True)])')
    pair = StackedButtons([
        StackedButton("log-out", "Leave", on_click=lambda: ask_leave(pair)),
        StackedButton("trash-2", "Delete", danger=True, on_click=lambda: ask_delete(pair)),
    ])
    pair.set_size_request(300, -1)
    pair.set_halign(Gtk.Align.START)
    trio = StackedButtons([
        StackedButton("refresh-cw", "Sync now", on_click=lambda: Toast.show(trio, "Synced just now")),
        StackedButton("pencil", "Edit"),
        StackedButton("log-out", "Sign out", sensitive=False),
    ], small=True)
    trio.set_size_request(300, -1)
    trio.set_halign(Gtk.Align.START)
    demo.append(pair)
    demo.append(trio)
    return page


def ask_delete(where: Gtk.Widget) -> None:
    DestructiveDialog.ask(where, title="Delete this conversation?",
                          body="It’s removed from this computer. Others in the conversation keep their copy.",
                          action="Delete", icon="trash-2",
                          on_confirm=lambda _o: Toast.show(where, "Conversation deleted", kind="deleted"))


def ask_leave(where: Gtk.Widget) -> None:
    DestructiveDialog.ask(where, title="Leave Launch crew?", body="You won’t get new messages. Someone can add you back.",
                          action="Leave", icon="log-out",
                          on_confirm=lambda _o: Toast.show(where, "You left Launch crew", kind="signed-out"))


def ask_uninstall(where: Gtk.Widget) -> None:
    DestructiveDialog.ask(where, title="Uninstall Kiln?", body="Kiln is removed from this computer. Your files stay where they are.",
                          action="Uninstall", icon="package", option="Also remove its settings",
                          on_confirm=lambda also: Toast.show(where, "Kiln uninstalled" + (" with its settings" if also else ""),
                                                             kind="deleted"))


def page_confirm() -> Gtk.Widget:
    page, demo = _page("Destructive dialog", "For what can’t come back. Centred on the asking window (a drawer at phone "
                       "width): icon, the question, one line of consequence, Cancel and the red action as equal buttons. "
                       "Cancel has focus, Esc cancels, Tab stays inside. Reversible things get a toast with Undo instead.",
                       'DestructiveDialog.ask(widget, title=…, body=…, action="Delete", option=…, on_confirm=fn)')
    demo.append(_flow(_button("Delete a conversation", "trash-2", ask_delete),
                      _button("Uninstall an app", "package", ask_uninstall),
                      _button("Leave a group", "log-out", ask_leave)))
    return page


def page_toast() -> Gtk.Widget:
    page, demo = _page("Toast", "One place (centred on the island, above its bar), a plain line, the icon for what "
                       "happened, Undo for 4 s when it can be undone. Announced to screen readers.",
                       'Toast.show(widget, "Copied", kind="copied") · Toast.show(widget, msg, kind="deleted", undo=fn)')

    def undoable(message: str, kind: str):
        return lambda b: Toast.show(b, message, kind=kind, undo=lambda: Toast.show(b, "Undone", kind="undone"))

    busy_holder: dict[str, Toast] = {}

    def busy(b: Gtk.Widget) -> None:
        busy_holder["t"] = Toast.show(b, "Preparing export…", busy=True)
        GLib.timeout_add(2500, lambda: (busy_holder["t"].dismiss(), Toast.show(b, "Exported", kind="done"), False)[2])

    demo.append(_flow(
        _button("Added to Favourites", "heart", lambda b: Toast.show(b, "Added to Favourites", kind="favourite")),
        _button("Removed from Favourites", "heart", undoable("Removed from Favourites", "unfavourite")),
        _button("Added Kansas City, MO", "map-pin", lambda b: Toast.show(b, "Added Kansas City, MO", kind="place")),
        _button("Copied", "copy", lambda b: Toast.show(b, "Copied", kind="copied")),
        _button("Conversation deleted", "trash-2", undoable("Conversation deleted", "deleted")),
        _button("Busy, then done", "download", busy),
        _button("Couldn’t connect", "circle-alert",
                lambda b: Toast.show(b, "Couldn’t connect. Messages will send when you’re back online.", kind="error")),
    ))
    return page


def page_cat() -> Gtk.Widget:
    page, demo = _page("Category pill", "Depot’s category colours wherever a category shows: one map, light and dark, "
                       "AA text (6.2–8.2:1).", 'CategoryPill("media")')
    demo.append(_flow(*(CategoryPill(key) for key in CATEGORIES)))
    return page


def page_coming(key: str, title: str, family: str, module: str) -> Gtk.Widget:
    owner = "F2" if family == "structure" else "F3"
    doc = (importlib.import_module(f"luma_appkit.{module}").__doc__ or "").strip()
    body = doc.split("\n", 1)[1].strip() if "\n" in doc else doc
    page, demo = _page(title, f"Coming from {owner} ({family} family).", f"luma_appkit/{module}.py")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.add_css_class("gallery-coming")
    box.append(_label(f"{owner} builds this part", "gallery-owner"))
    box.append(_label(body, wrap=True))
    demo.append(box)
    return page


# ── F3: action and content parts ────────────────────────────────────────────

_F3: dict = {}
PRIYA = Person("Priya Raman", phone="+1 555 0101", email="priya@example.com", username="priya", online=True)
NORA = Person("Nora Feld", phone="+1 555 0102")
PLACES = [PlaceResult(n, f"{r}{' · ' + z if z else ''}", z) for n, r, z in (
    ("Kansas City", "MO", "64105"), ("New York", "NY", "10001"), ("Chicago", "IL", "60601"), ("Seattle", "WA", "98101"),
    ("Austin", "TX", "78701"), ("Denver", "CO", "80202"), ("London", "United Kingdom", ""), ("Paris", "France", ""),
    ("Berlin", "Germany", ""), ("Sydney", "Australia", ""), ("Mexico City", "Mexico", ""), ("Toronto", "Canada", ""),
    ("Kansas City", "KS", "66101"), ("San Francisco", "CA", "94103"))]


def _gradient(width: int = 88, height: int = 88) -> Gdk.Texture:
    """A stand-in photo (the gallery ships no pictures): a warm-to-cool gradient."""
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            pixels += bytes((200 - y, 120 + x // 2, 90 + y, 255))
    return Gdk.MemoryTexture.new(width, height, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes(pixels)), width * 4)


def page_parity() -> Gtk.Widget:
    page, demo = _page("Contact actions and mini cards", "The owning app defines the look; the others embed a mini "
                       "version. Contacts owns Message, Call, Video, Email; Calendar the event, Tide the song, Maps the "
                       "place, Filer the file.",
                       "ContactActions(person, handler=) · ContactCard(person) · EventCard(title, start, where=, on_add=) · "
                       "SongCard(title, artist, album, artwork=, on_play=) · PlaceCard(name, address, eta=, on_directions=)")
    from datetime import datetime

    def handled(action: str, person: Person) -> bool:
        Toast.show(demo, f"{action.title()} {person.name.split()[0]} (handled in place)", kind="opening")
        return True

    actions = ContactActions(PRIYA, handler=handled)
    actions.set_halign(Gtk.Align.START)
    actions.set_size_request(376, -1)
    demo.append(actions)
    demo.append(_flow(
        ContactCard(PRIYA, handler=handled),
        ContactCard(NORA),
        EventCard("Launch rehearsal", datetime(2026, 9, 29, 10, 0), where="Studio", tone="play",
                  on_add=lambda: Toast.show(demo, "Added to Calendar", kind="added")),
        SongCard("God Only Knows", "The Beach Boys", "Pet Sounds", artwork=_gradient(),
                 on_play=lambda: Toast.show(demo, "Playing God Only Knows", kind="opening")),
        PlaceCard("Duende", "468 19th St", eta="9 min", icon="utensils",
                  on_directions=lambda: Toast.show(demo, "Directions open in Maps", kind="place")),
        FileCard("/nonexistent/launch-deck.stage", size=18_400_000, kind="Stage presentation",
                 content_type="application/vnd.oasis.opendocument.presentation",
                 on_open=lambda: Toast.show(demo, "Opening launch-deck.stage", kind="opening")),
    ))
    return page


def page_bubble() -> Gtk.Widget:
    page, demo = _page("Selection bubble", "Write’s bubble for every rich-text surface: the common marks just above the "
                       "selection, flipping below near the top; it waits for the drag to finish and hides on typing or "
                       "scrolling. Left and Right move inside it, Esc hides it, Alt+F10 reaches it. Select some words:",
                       'SelectionBubble(text_view, [BarAction("bold", tooltip="Bold", on_activate=fn), SEPARATOR, …])')
    view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD, top_margin=12, bottom_margin=12, hexpand=True)
    view.add_css_class("gallery-textview")
    view.get_buffer().set_text("Freeze strings on the 10th. Launch day is October 1: film at nine, download at ten, "
                               "and press all day. Select any of these words and the marks float just above them.")
    marks: dict[str, bool] = {}

    def toggle(name: str) -> Callable[[], None]:
        def run() -> None:
            marks[name] = not marks.get(name, False)
            bubble.set_active(name, marks[name])
        return run

    bubble = SelectionBubble(view, [
        BarAction("bold", tooltip="Bold", on_activate=toggle("bold")),
        BarAction("italic", tooltip="Italic", on_activate=toggle("italic")),
        BarAction("underline", tooltip="Underline", on_activate=toggle("underline")),
        BarAction("strikethrough", tooltip="Strikethrough", on_activate=toggle("strikethrough")),
        SEPARATOR,
        BarAction("link", tooltip="Link", on_activate=lambda: Toast.show(view, "Link added", kind="done")),
        BarAction("highlighter", tooltip="Highlight", on_activate=toggle("highlighter")),
    ])
    _F3["bubble"] = (view, bubble)
    view.set_size_request(-1, 120)
    frame = Card(view)
    demo.append(frame)
    return page


def _editor(center_holder: dict) -> ActionEditor:
    def send() -> None:
        center = center_holder["center"]
        center.editor.clear()
        center.fold()
        Toast.show(center, "Sent", kind="sent")

    return ActionEditor(
        "Reply all", "reply-all", summary="to {}", summary_emphasis="Priya Raman, Nora Feld",
        modes=[("one", "Reply", "reply"), ("all", "Reply all", "reply-all"), ("fwd", "Forward", "forward")], mode="all",
        tools=[BarAction("bold", tooltip="Bold"), BarAction("italic", tooltip="Italic"),
               BarAction("underline", tooltip="Underline"), SEPARATOR, BarAction("list", tooltip="Bulleted list"),
               BarAction("link", tooltip="Link"), SPACER, BarAction("paperclip", tooltip="Attach")],
        placeholder="Write your reply", primary=BarAction("send-horizontal", "Send", on_activate=send))


def page_ac() -> Gtk.Widget:
    page, demo = _page("Action center", "The floating bar grows in place instead of opening a dialog: bar, double "
                       "height, the full editor (Charlie’s reply, ported), or split in two. It caps at the island and its "
                       "text scrolls; Esc folds it back and keeps what you typed; on a phone it spans the width.",
                       "ActionCenter().attach(host) · show_bar(items, context=) · set_editor(ActionEditor(…)) · grow() · "
                       "fold() · show_split(a, b)")
    holder: dict = {}
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START)
    content.add_css_class("gallery-isle-content")
    content.append(TypeLabel("Launch walkthrough deck", role="title-2"))
    content.append(TypeLabel("Priya, Nora and you · 4 messages", role="caption"))
    isle = ToastHost(content)
    isle.add_css_class("gallery-isle")
    isle.set_overflow(Gtk.Overflow.HIDDEN)
    center = ActionCenter(_editor(holder)).attach(isle)
    holder["center"] = center
    attach = BarAction("paperclip", tooltip="Attach", on_activate=lambda: Toast.show(isle, "Attach a file", kind="done"))

    syncing = {"on": False}

    def state(key: str) -> None:
        if syncing["on"]:
            return
        if key == "bar":
            center.show_bar([BarPrompt("Reply to Priya and Nora…"), attach])
        elif key == "double":
            center.show_bar([BarPrompt("Reply to Priya and Nora…"), attach],
                            context=BarContext("reply", "Replying to {}’s message", emphasis="Priya",
                                               on_dismiss=lambda: state("bar")))
        elif key == "editor":
            center.show_bar([BarPrompt("Reply to Priya and Nora…"), attach])
            center.grow()
        elif key == "split":
            center.show_split([BarChip("3 photos", icon="image", on_dismiss=lambda: state("bar")),
                               BarAction("share-2", "Share")],
                              [BarAction("heart", "Favourite"), BarAction("trash-2", tooltip="Delete", danger=True)])

    buttons: dict[str, Gtk.ToggleButton] = {}
    switch = Gtk.Box(spacing=4)
    group = None
    for key, label in (("bar", "Bar"), ("double", "Double"), ("editor", "Editor"), ("split", "Split")):
        button = Gtk.ToggleButton(label=label, group=group)
        group = group or button
        button.add_css_class("gallery-toggle")
        button.connect("toggled", lambda b, k=key: b.get_active() and state(k))
        buttons[key] = button
        switch.append(button)
    def follow(_center, key: str) -> None:  # Esc or Send folds the bar: light the matching toggle
        if key in buttons and not buttons[key].get_active():
            syncing["on"] = True
            buttons[key].set_active(True)
            syncing["on"] = False

    center.connect("state-changed", follow)
    demo.append(switch)
    demo.append(isle)
    buttons["bar"].set_active(True)
    _F3["ac"] = (center, state)
    return page


def page_open() -> Gtk.Widget:
    page, demo = _page("Open in and file card", "Open wears the default app’s own icon and says where it opens. Open in "
                       "lists every app that can, the best Luma app first, the default marked, then Other app. One card "
                       "for a file anywhere: the file’s own face (never larger than its square), the name cut in the "
                       "middle so the extension stays, size and kind, and Open.",
                       'OpenButton(file) · OpenInMenu(file).popup(button) · FileCard(file, subtitle=, tone=, extra=, actions=)')
    opener = _button("Open in…", "square-arrow-out-up-right",
                     lambda b: OpenInMenu(content_type="image/png", on_open=lambda a: Toast.show(
                         b, f"Opening in {a.get_name()}", kind="opening")).popup(b))
    song = _button("Open a song in…", "square-arrow-out-up-right",
                   lambda b: OpenInMenu(content_type="audio/mpeg", on_open=lambda a: Toast.show(
                       b, f"Opening in {a.get_name()}", kind="opening")).popup(b))
    _F3["open"] = opener
    demo.append(_flow(OpenButton(content_type="application/pdf", on_open=lambda: None),
                      OpenButton(content_type="image/png", on_open=lambda: None),
                      OpenButton(content_type="text/plain", on_open=lambda: None), opener, song))
    progress = Gtk.ProgressBar(fraction=0.68)
    toast = lambda text, kind="done": (lambda: Toast.show(demo, text, kind=kind))  # noqa: E731
    cards = [
        FileCard("/nonexistent/luma-1.0-nightly-x86_64.iso", size=2_100_000_000, subtitle="38 s left", extra=progress,
                 content_type="application/x-cd-image",
                 actions=[BarAction("pause", tooltip="Pause", on_activate=toast("Paused", "paused")),
                          BarAction("x", tooltip="Cancel", on_activate=toast("Cancelled"))]),
        FileCard("/nonexistent/homepage-hero-final-final.webp", size=1_800_000, subtitle="Just now",
                 content_type="image/webp", thumbnail=_gradient(), on_open=toast("Opening in Viewer", "opening")),
        FileCard("/nonexistent/press-kit.zip", size=48_000_000, content_type="application/zip",
                 subtitle="Failed: the server stopped responding", tone="danger",
                 actions=[BarAction("rotate-cw", "Retry", on_activate=toast("Retrying"))]),
        FileCard("/nonexistent/quarterly-press-release-for-the-october-launch.write", size=24_000,
                 kind="Write document", content_type="text/plain", on_open=toast("Opening", "opening")),
        FileCard("/nonexistent/launch-deck.stage", size=18_400_000, kind="Stage presentation", compact=True,
                 content_type="application/vnd.oasis.opendocument.presentation",
                 actions=[BarAction("x", tooltip="Remove", on_activate=toast("Removed", "deleted"))]),
    ]
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.START)
    for card in cards:
        column.append(card)
    demo.append(column)
    return page


def page_psearch() -> Gtk.Widget:
    page, demo = _page("Place search", "The field is the whole flow: places open above it as you type, Enter or a click "
                       "adds the top or chosen one, no match says so in the list. Promoted from Weather.",
                       "PlaceSearch(provider, on_pick=fn, exclude=fn) · rank_places(places, query)")

    def provider(query: str) -> list:
        text = query.strip()
        by_zip = [p for p in PLACES if p.value and p.value.startswith(text)] if text.isdigit() else []
        return by_zip or rank_places(PLACES, query)

    side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.START)
    side.add_css_class("gallery-footside")
    side.set_size_request(280, 300)
    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
    for index, name in enumerate(("Oakland", "Lisbon", "Tokyo")):
        row = Gtk.Box(spacing=10)
        row.add_css_class("gallery-row")
        if index == 0:
            row.add_css_class("on")
        row.append(Gtk.Image(icon_name=lumaui_icon("map-pin")))
        row.append(_label(name))
        rows.append(row)
    side.append(rows)
    search = PlaceSearch(provider, on_pick=lambda place: Toast.show(
        side, f"Added {place.name}, {place.subtitle.split(' · ')[0]}", kind="place"))
    side.append(search)
    _F3["psearch"] = search
    demo.append(side)
    demo.append(_label("Try “64105”, “Kansas” or “Atlantis”.", "gallery-lede"))
    return page


def page_acct() -> Gtk.Widget:
    page, demo = _page("Account and lit cards", "The person at the top of a sidebar in its own subtle frame. On a light "
                       "sky a card sits darker than the sky so white text passes AA; secondary text is 90% white. Where "
                       "colour means nothing, use a plain card.",
                       'AccountCard("Nick", caption="Luma account", on_activate=fn) · ContentLitCard(child) · Card(child)')
    side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.START)
    side.add_css_class("gallery-footside")
    side.set_size_request(280, -1)
    side.append(AccountCard("Nick", on_activate=lambda: Toast.show(side, "Opening your Luma account", kind="opening")))
    for index, (glyph, name) in enumerate((("wifi", "Wi-Fi"), ("bluetooth", "Bluetooth"))):
        row = Gtk.Box(spacing=10)
        row.add_css_class("gallery-row")
        if index == 0:
            row.add_css_class("on")
        row.append(Gtk.Image(icon_name=lumaui_icon(glyph)))
        row.append(_label(name))
        side.append(row)
    demo.append(side)

    def lit(title: str, figure: str, line: str, plain: bool = False) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(TypeLabel(title, role="label"))
        box.append(TypeLabel(figure, role="numeric"))
        box.append(TypeLabel(line, role="caption"))
        card = Card(box) if plain else ContentLitCard(box)
        card.set_size_request(170, -1)
        return card

    for css, cards in (("", [("Rain", "80%", "Light rain 6 PM to 9 PM."), ("Wind", "11 mph", "From the west"),
                             ("UV index", "5", "Moderate")]),
                       ("dusk", [("Sunset", "7:02 PM", "Sunrise was 7:01 AM"), ("Humidity", "64%", "Dew point 50°")])):
        sky = Gtk.Box(halign=Gtk.Align.START)
        sky.add_css_class("gallery-sky")
        if css:
            sky.add_css_class(css)
        for title, figure, line in cards:
            sky.append(lit(title, figure, line))
        if css:
            sky.append(lit("Plain card", "+3 h", "Lisbon, today", plain=True))
        demo.append(sky)
    return page


def page_field() -> Gtk.Widget:
    page, demo = _page("Field", "A label above a recessed well (the search well), the accent ring on focus, a caption "
                       "hint below. Used in Tide’s Add source.",
                       'TextField("Server address", placeholder="music.example.com", purpose="url") · TextField(label, value=, hint=)')
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.START)
    column.set_size_request(320, -1)
    column.append(TextField("Server address", placeholder="music.example.com", purpose="url"))
    column.append(TextField("Username", value="nick", placeholder="nick",
                            hint="Luma signs in with a token; the password isn’t kept."))
    demo.append(column)
    return page


def page_bubblemsg() -> Gtk.Widget:
    page, demo = _page("Message bubble", "Messages owns the look; every thread uses it (Messages, Charlie). Theirs is the "
                       "raised chip, yours the accent gradient in white; 18 round, 6 where one sender’s run joins.",
                       "MessageBubble(text, mine=, joined_above=, joined_below=, selected=)")
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    column.set_size_request(360, -1)
    column.set_halign(Gtk.Align.START)
    for text, mine, above, below in (("Freeze strings on the 10th?", False, False, True),
                                     ("Then we film at nine.", False, True, False),
                                     ("Works for me.", True, False, True),
                                     ("I’ll book the studio.", True, True, False)):
        bubble = MessageBubble(text, mine=mine, joined_above=above, joined_below=below)
        if not above and text != "Freeze strings on the 10th?":
            bubble.set_margin_top(8)
        column.append(bubble)
    demo.append(column)
    return page


def page_hero() -> Gtk.Widget:
    page, demo = _page("Lit header, hero title and status", "The top of a person’s page: a soft light from their photo "
                       "and a wash of their hue fading into the surface, the name in the hero type (edited in place: it "
                       "looks like the title until it has focus), and how they are as a status pill.",
                       "ContentLitHeader.for_person(person) · HeroTitleField(name, on_commit=fn) · StatusPill(\"on-luma\")")
    for person, photo in ((PRIYA, _gradient(120, 120)), (NORA, None)):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER, margin_top=44)
        column.append(PersonAvatar(person.name, 116, picture=photo))
        column.append(HeroTitleField(person.name, on_commit=lambda text: Toast.show(demo, f"Renamed to {text}")))
        sub = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        sub.append(_label(f"@{person.username}" if person.username else person.phone))
        sub.append(StatusPill("on-luma" if person.username else "not-on-luma"))
        column.append(sub)
        overlay = Gtk.Overlay(child=ContentLitHeader(picture=photo, name=person.name))
        overlay.add_overlay(column)
        isle = Gtk.Box()
        isle.add_css_class("gallery-isle")
        isle.set_overflow(Gtk.Overflow.HIDDEN)
        overlay.set_hexpand(True)
        isle.append(overlay)
        isle.set_size_request(-1, 320)
        demo.append(isle)
    demo.append(_flow(StatusPill("online"), StatusPill("syncing", "Syncing 214 songs"), StatusPill("offline"),
                      StatusPill("error", "Can’t reach the server")))
    return page


# Live states for screenshots: `--page ac --demo grow`.
def _demo_bubble(gallery) -> None:
    view, bubble = _F3["bubble"]
    buffer = view.get_buffer()
    start = buffer.get_iter_at_offset(buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).index("Launch day"))
    end = start.copy()
    end.forward_chars(len("Launch day is October 1"))
    buffer.select_range(start, end)
    bubble.set_active("bold", True)
    bubble._settled()


def _demo_grow(gallery) -> None:
    center, state = _F3["ac"]
    state("editor")
    center.editor.text_view.get_buffer().set_text("Looks great. One note on slide 4: the chart needs the new numbers.")


def _demo_fold(gallery) -> None:
    _demo_grow(gallery)
    GLib.timeout_add(600, lambda: (_F3["ac"][0].fold(), False)[1])


def _demo_openin(gallery) -> None:
    button = _F3["open"]
    button.emit("clicked")


def _demo_place(gallery) -> None:
    search = _F3["psearch"]
    search.entry.grab_focus()
    search.set_text("Kansas")
    search.search_now()


def _demo_noplace(gallery) -> None:
    search = _F3["psearch"]
    search.entry.grab_focus()
    search.set_text("Atlantis")
    search.search_now()


F3_BUILDERS = {"parity": page_parity, "bubble": page_bubble, "ac": page_ac, "open": page_open,
               "psearch": page_psearch, "acct": page_acct, "field": page_field, "bubblemsg": page_bubblemsg,
               "hero": page_hero}
F3_DEMOS = {"bubble": _demo_bubble, "grow": _demo_grow, "double": lambda g: _F3["ac"][1]("double"),
            "split": lambda g: _F3["ac"][1]("split"), "fold": _demo_fold, "openin": _demo_openin,
            "place": _demo_place, "noplace": _demo_noplace}




# ── structure pages (F2) ────────────────────────────────────────────────────

def _frame(height: int) -> Gtk.Box:
    """A window's body in miniature: the frame, and whatever sits in it."""
    frame = Gtk.Box(hexpand=True)
    frame.add_css_class("gallery-frame")
    frame.set_size_request(-1, height)
    return frame


def _island(title: str, caption: str) -> Gtk.Box:
    island = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
    island.add_css_class("luma-island")
    island.add_css_class("gallery-island")
    island.append(TypeLabel(title, role="title-1"))
    island.append(_label(caption, "gallery-lede"))
    return island


def _avatar(name: str) -> Gtk.Widget:
    try:
        from luma_appkit import Avatar
        return Avatar(name)
    except Exception:  # noqa: BLE001 - no Luma platform: initials in a circle
        mark = _label("".join(p[0] for p in name.split()[:2]), "gallery-avatar", xalign=0.5)
        return mark


def _swatch(r: int, g: int, b: int) -> Gdk.Paintable:
    """A small two-tone picture standing in for a photo."""
    size = 24
    rows = bytearray()
    for y in range(size):
        k = y / size
        rows += bytes((int(r * (1 - k) + 40 * k), int(g * (1 - k) + 50 * k), int(b * (1 - k) + 70 * k), 255)) * size
    return Gdk.MemoryTexture.new(size, size, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes(rows)), size * 4)


def page_details() -> Gtk.Widget:
    page, demo = _page("Details pane", "One pane for Details and Information: recessed into the frame, header with a "
                       "hairline and close, facts with hairlines, lists with full-row hover, the Add row, stacked buttons "
                       "last. Closed by default, Info opens it, Esc or its subject going away closes it; a drawer on a phone.",
                       'DetailsPane("Details") · pane.show(open=True, subject=thread) · DetailsRow · DetailsItem · AddRow')
    frame = _frame(520)
    island = _island("Launch crew", "The conversation")
    frame.append(island)
    pane = DetailsPane("Details")
    pane.add_hero("Launch crew", "4 people", lead=_avatar("Launch crew"))
    pane.add_section("Conversation")
    pane.add_facts([("Created", "Sep 2"), ("Encryption", "End-to-end"), ("Notifications", "On")])
    pane.add_section("People")
    people = [DetailsRow(name, "@" + user, lead=_avatar(name),
                         on_activate=lambda n=name: Toast.show(pane, f"Opening {n} in Contacts", kind="opening"),
                         actions=[("message-square", "Message " + name.split()[0], lambda: None),
                                  ("phone", "Call", lambda n=name: Toast.show(pane, f"Calling {n.split()[0]}…"))])
              for name, user in (("Priya Raman", "priya"), ("Nora Feld", "nora"), ("Sam Kato", "sam"))]
    pane.add_list(people + [AddRow("Add people", on_activate=lambda: Toast.show(pane, "Type a name or @username"))])
    pane.add_section("Also with these people")
    pane.add_list([DetailsItem("Press kit review", "Priya · Tuesday", icon="mail"),
                   DetailsItem("Launch rehearsal", "Sep 29 · 10:00 AM", icon="calendar")])
    pane.add_section("Photos and files", action=("View all", lambda: Toast.show(pane, "All 48 photos and 6 files")))
    pane.add_photos([_swatch(200, 150, 110), _swatch(90, 140, 200), _swatch(120, 180, 130)])
    pane.add(StackedButtons([StackedButton("log-out", "Leave", on_click=lambda: ask_leave(pane)),
                             StackedButton("trash-2", "Delete", danger=True, on_click=lambda: ask_delete(pane))]))
    frame.append(pane)
    demo.append(frame)
    subject = {"n": 0}

    def reopen(_b):
        subject["n"] += 1
        pane.show(open=True, subject=subject["n"])

    controls = _flow(_button("Information", "info", reopen),
                     _button("The conversation was deleted", "trash-2", lambda _b: pane.show(subject=None)))
    demo.append(controls)
    demo.append(_label("Main view (Calendar’s day): DetailsPane(main=True) stays open, has no close and ignores Esc.",
                       "gallery-code", wrap=True))
    GLib.idle_add(lambda: (reopen(None), False)[1])
    return page


def page_place() -> Gtk.Widget:
    page, demo = _page("Placement and modes", "Top right holds the mode switch and the thing itself (Open in, Share, Edit, "
                       "Info, ···); the bottom bar holds what you do in this mode. Modes are icon plus label; a narrow "
                       "window keeps only the current label.",
                       "CornerPill(modes=ModeSwitch([...]), open_in=fn, share=fn, info=pane, more=registry)")
    frame = _frame(340)
    overlay = Gtk.Overlay(hexpand=True)
    overlay.set_child(_island("Offsite-hero.jpg", "1672 × 941"))
    tools = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.END)
    tools.add_css_class("gallery-tools")
    sets = {"view": ("Zoom out · 100% · Zoom in · Fit"), "markup": "Pen · Highlight · Text · Shapes",
            "adjust": "Crop · Rotate · Auto"}
    tool_label = _label(sets["view"], "gallery-code")
    tools.append(tool_label)
    overlay.add_overlay(tools)
    from luma_appkit.commands import Command, CommandGroup, CommandRegistry
    more = CommandRegistry([CommandGroup(None, (Command("g.rename", "Rename", lambda: None, icon=lumaui_icon("pencil")),
                                                Command("g.copy", "Copy", lambda: None, icon=lumaui_icon("copy"))))])
    modes = ModeSwitch([("view", "View", "eye"), ("markup", "Mark up", "pen-line"), ("adjust", "Adjust", "sliders-horizontal")],
                       on_change=lambda k: tool_label.set_label(sets[k]))
    corner = CornerPill(modes=modes, open_in=lambda b: Toast.show(b, "Open in lists the apps that can", kind="opening"),
                        share=lambda b: Toast.show(b, "Sharing"), info=lambda on: None, more=more)
    overlay.add_overlay(corner)
    frame.append(overlay)
    demo.append(frame)
    return page


def page_table() -> Gtk.Widget:
    page, demo = _page("Table header and selection", "One header for every list view: a recessed bar, each heading sorts "
                       "(again reverses), the accessible sort state says which. Lists built from rows align to it; a "
                       "ColumnView gets the same bar. One selection look, the raised chip; earlier steps of a path are a "
                       "quieter trail.", "TableHeader(columns, sort=(key, dir), on_sort=fn) · header.align(row) · "
                       "TableHeader.for_column_view(view) · Selection.apply(list) · Selection.trail(step)")
    files = [("budget-q4.grid", "Grid spreadsheet", 96), ("launch-deck.stage", "Stage presentation", 18400),
             ("why-letter.pdf", "PDF document", 2100)]
    rows_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
    rows_box.add_css_class("gallery-table")
    Selection.apply(rows_box)

    def size(kb: int) -> str:
        return f"{kb / 1000:.1f} MB" if kb >= 1000 else f"{kb} KB"

    def fill(key: str, direction: str) -> None:
        while (child := rows_box.get_first_child()) is not None:
            rows_box.remove(child)
        index = {"name": 0, "kind": 1, "size": 2}[key]
        for name, kind, kb in sorted(files, key=lambda f: f[index], reverse=direction == "descending"):
            line = Gtk.Box()
            for text, css in ((name, "gallery-cell"), (kind, "gallery-cell-dim"), (size(kb), "gallery-cell-dim")):
                line.append(_label(text, css))
            header.align(line)
            rows_box.append(line)
        rows_box.select_row(rows_box.get_row_at_index(1))

    header = TableHeader([Column("name", "Name", expand=True), Column("kind", "Kind"),
                          Column("size", "Size", width=70)], sort=("name", "ascending"), on_sort=fill)
    table = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    table.append(header)
    table.append(rows_box)
    fill("name", "ascending")
    demo.append(table)

    songs = [("God Only Knows", "Pet Sounds", 175), ("Wouldn’t It Be Nice", "Pet Sounds", 153), ("Here Today", "Pet Sounds", 172)]
    song_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

    def fill_songs(key: str, direction: str) -> None:
        while (child := song_rows.get_first_child()) is not None:
            song_rows.remove(child)
        index = {"title": 0, "album": 1, "time": 2}[key]
        for n, (title, album, secs) in enumerate(sorted(songs, key=lambda s: s[index], reverse=direction == "descending")):
            line = Gtk.Box()
            line.add_css_class("gallery-song")
            line.append(_label(str(n + 1), "gallery-cell-dim"))
            line.append(_label(title, "gallery-cell"))
            line.append(_label(album, "gallery-cell-dim"))
            line.append(_label(f"{secs // 60}:{secs % 60:02d}", "gallery-cell-dim", xalign=1))
            grid_header.align(line)
            song_rows.append(line)

    grid_header = TableHeader([Column(None, "", width=32), Column("title", "Title", expand=True),
                               Column("album", "Album", expand=True), Column("time", "Time", width=56, end=True)],
                              sort=("title", "ascending"), on_sort=fill_songs)
    songs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    songs_box.append(grid_header)
    songs_box.append(song_rows)
    fill_songs("title", "ascending")
    demo.append(songs_box)

    columns = Gtk.Box()
    columns.add_css_class("gallery-columns")
    for steps, trail_at, chosen in ((("Projects", "Personal site"), 0, None), (("Taxes 2026", "Archive"), 0, None),
                                    (("Return.pdf", "Receipts.grid"), None, 1)):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.add_css_class("gallery-column")
        for index, name in enumerate(steps):
            step = Gtk.Button()
            step.set_child(_label(name, "gallery-step-label"))
            step.add_css_class("gallery-step")
            if index == trail_at:
                Selection.trail(step)
            if index == chosen:
                Selection.mark(step)
            column.append(step)
        columns.append(column)
    demo.append(columns)
    return page


def page_foot() -> Gtk.Widget:
    page, demo = _page("Sidebar foot and toggle", "Search at the foot, then a filter picker whose menu opens upward (lit "
                       "when a filter is on, and the list names it), the raised New square, or both. Adding an account is "
                       "a list row. One control hides and shows a sidebar: panel-left after the app’s name, F9; on a "
                       "phone the sidebar is a drawer from the left.",
                       'SidebarFoot(search=…, filters=[(key, label, icon, count)], on_filter=fn, add=(label, icon, fn)) · '
                       'SidebarToggle(sidebar)')
    memos = [("Launch dry run", "Today, 9:52 AM", True), ("Idea: notifications as a lip", "Today, 8:14 AM", False),
             ("Call with Theo", "Yesterday", False), ("Saturday dinner playlist", "Monday", True)]
    listing = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
    Selection.apply(listing)

    def show(key: str) -> None:
        while (child := listing.get_first_child()) is not None:
            listing.remove(child)
        items = [m for m in memos if m[2]] if key == "fav" else [("Old idea", "28 days left", False)] if key == "deleted" else memos
        for title, when, _fav in items:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.add_css_class("gallery-memo")
            box.append(_label(title, "gallery-cell"))
            box.append(TypeLabel(when, role="caption"))
            listing.append(box)
        listing.select_row(listing.get_row_at_index(0))

    foot = SidebarFoot(search="Search what was said", filters=[("all", "All", "layers", 4), ("fav", "Favourites", "star", 2),
                                                               ("deleted", "Recently deleted", "trash-2", 1)],
                       on_filter=show)
    show("all")
    side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    side.add_css_class("gallery-footside")
    side.append(foot.heading)
    side.append(Gtk.ScrolledWindow(child=listing, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
    side.append(foot)

    mail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    mail.add_css_class("gallery-footside")
    boxes = Gtk.ListBox()
    Selection.apply(boxes)
    for glyph, name, count, attention in (("inbox", "Inbox", 2, True), ("file-text", "Drafts", 1, False), ("send-horizontal", "Sent", 0, False)):
        line = Gtk.Box(spacing=10)
        line.add_css_class("gallery-mailrow")
        line.append(Gtk.Image(icon_name=lumaui_icon(glyph)))
        line.append(_label(name, "gallery-cell"))
        line.append(Gtk.Box(hexpand=True))
        line.append(CountBadge(count, attention=attention))
        boxes.append(line)
    boxes.select_row(boxes.get_row_at_index(0))
    mail.append(boxes)
    mail.append(AddRow("Add account", icon="plus", on_activate=lambda: Toast.show(mail, "Account setup opens")))
    mail.append(Gtk.Box(vexpand=True))
    mail.append(SidebarFoot(search="Search mail", add=("New email", "square-pen", lambda: Toast.show(mail, "New email"))))
    demo.append(_flow(side, mail))

    # The sidebar toggle, in a window's title row.
    frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    frame.add_css_class("gallery-frame")
    frame.set_size_request(-1, 280)
    title = Gtk.Box(spacing=8)
    title.add_css_class("gallery-titlerow")
    title.append(Gtk.Image(icon_name=lumaui_icon("folder")))
    title.append(_label("Filer", "gallery-cell"))
    body = Gtk.Box(vexpand=True)
    nav = Gtk.ListBox()
    nav.add_css_class("gallery-nav-mini")
    Selection.apply(nav)
    for glyph, name in (("house", "Home"), ("file-text", "Documents"), ("download", "Downloads"), ("image", "Pictures")):
        line = Gtk.Box(spacing=10)
        line.append(Gtk.Image(icon_name=lumaui_icon(glyph)))
        line.append(_label(name, "gallery-cell"))
        nav.append(line)
    nav.select_row(nav.get_row_at_index(0))
    sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    sidebar.add_css_class("gallery-sidebar")
    sidebar.append(nav)
    body.append(sidebar)
    body.append(_island("Home", "12 folders · 4 files"))
    toggle = SidebarToggle(sidebar)
    title.append(toggle)
    frame.append(title)
    frame.append(body)
    demo.append(frame)
    return page


def page_drawer() -> Gtk.Widget:
    page, demo = _page("Menu drawer", "Every shared menu (Open in, the sidebar-foot filter, ···) is the usual popover on a "
                       "computer and a bottom drawer on a phone: full width, a grab handle, rows sized for a thumb, the "
                       "window dimmed behind it. Nothing to do per app. Try it with Phone width on.",
                       "command_popover(registry).popup() · MenuDrawer.present(widget, registry)")
    demo.append(_flow(_button("Open a menu", "ellipsis", open_menu)))
    return page


def open_menu(button: Gtk.Widget) -> None:
    from luma_appkit.commands import Command, CommandGroup, CommandRegistry
    from luma_appkit.menus import command_popover
    say = lambda text: (lambda: Toast.show(button, text))  # noqa: E731
    registry = CommandRegistry([
        CommandGroup(None, (Command("m.open", "Open in Stage", say("Opening in Stage"), icon=lumaui_icon("square-arrow-out-up-right")),
                            Command("m.share", "Share…", say("Sharing"), icon=lumaui_icon("share-2")),
                            Command("m.copy", "Copy link", say("Copied"), icon=lumaui_icon("link"), shortcut=("Ctrl", "L")))),
        CommandGroup("Sort by", (Command("m.sort", "Sort", lambda: None, icon=lumaui_icon("arrow-up-down"), children=(
            Command("m.name", "Name", say("Sorted by name"), checked=lambda: True),
            Command("m.date", "Date modified", say("Sorted by date"), checked=lambda: False))),)),
        CommandGroup(None, (Command("m.delete", "Delete", lambda: ask_delete(button), icon=lumaui_icon("trash-2"), destructive=True),)),
    ])
    menu = command_popover(registry)
    menu.set_parent(button)
    menu.popup()


def page_trail() -> Gtk.Widget:
    page, demo = _page("Navigation trail", "Where you came from sits inline with where you are, never as a framed Back "
                       "button on a row of its own. Before a title the chevron stands alone; with no title it carries the name.",
                       "NavigationTrailBar(trail, meta=[...]) · NavigationTrailBar(trail, title=False)")
    from luma_appkit.navigation import NavigationTrail, Place
    trail = NavigationTrail(Place("albums", "Albums"))
    trail.open(Place("artist", "The Beach Boys"))
    bar = NavigationTrailBar(trail, meta=["12 albums", "214 songs"])
    demo.append(bar)
    sources = NavigationTrail(Place("sources", "Sources"))
    sources.open(Place("source", "Navidrome"))
    demo.append(NavigationTrailBar(sources, title=False))
    demo.append(_flow(_button("Open an album", "disc", lambda _b: trail.open(Place("album", "Pet Sounds"))),
                      _button("Back", "chevron-left", lambda _b: trail.back())))
    return page


F2_BUILDERS = {"details": page_details, "place": page_place, "table": page_table, "foot": page_foot,
               "drawer": page_drawer, "trail": page_trail}
#: Every page, one table: F1's, then F3's and F2's.
BUILDERS = {"type": page_type, "count": page_count, "stack": page_stack, "confirm": page_confirm,
            "toast": page_toast, "cat": page_cat, **F3_BUILDERS, **F2_BUILDERS}


def _load_family_pages() -> None:
    """Family builders' pages: every pages_<family>.py beside this file.

    A module exports BUILDERS ({key: builder}, like the table above) and may
    export PAGES ([(key, title, glyph, family, module)], like PAGES here) and
    DEMOS ({name: callable(gallery)}, live states for `--demo name`); a key
    with no PAGES entry is listed under its family. A key already taken is
    skipped with a warning, and a module that fails to import is reported and
    left out, so one family never breaks the gallery.
    """
    import importlib.util

    listed = {entry[0] for entry in PAGES}
    for path in sorted(HERE.glob("pages_*.py")):
        family = path.stem.removeprefix("pages_")
        try:
            spec = importlib.util.spec_from_file_location(f"lumaui_gallery_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as error:  # noqa: BLE001 (a family page must not stop the gallery)
            print(f"lumaui-gallery: {path.name} did not load: {error}", file=sys.stderr)
            continue
        titles = {entry[0]: tuple(entry) for entry in getattr(module, "PAGES", ())}
        for key, builder in getattr(module, "BUILDERS", {}).items():
            if key in BUILDERS:
                print(f"lumaui-gallery: {path.name}: page {key!r} is already taken", file=sys.stderr)
                continue
            BUILDERS[key] = builder
            if key not in listed:
                PAGES.append(titles.get(key, (key, key.replace("_", " ").title(), "layout-grid", family, path.stem)))
                listed.add(key)
        for name, demo in getattr(module, "DEMOS", {}).items():
            if name in FAMILY_DEMOS or name in F3_DEMOS or name in F2_DEMOS or name in ("toast", "confirm"):
                print(f"lumaui-gallery: {path.name}: demo {name!r} is already taken", file=sys.stderr)
                continue
            FAMILY_DEMOS[name] = demo


#: Family pages' live states for --demo (pages_<family>.DEMOS).
FAMILY_DEMOS: dict = {}
#: Structure parts' floating states for --demo (menu drawer, sidebar drawer, filter menu).
F2_DEMOS = ("menu", "side", "filter")


_load_family_pages()


# ── window ──────────────────────────────────────────────────────────────────

class Gallery:
    def __init__(self, app: Gtk.Application, options: argparse.Namespace) -> None:
        self.options = options
        self.window = self._make_window(app)
        self.window.set_title("LumaUI Gallery")
        self.window.set_default_size(1180, 780)
        display = self.window.get_display()
        install_lumaui(display)
        # The appearance toggle loads a treatment's token sheet above every
        # other token source (the C kit loads its own at APPLICATION), so the
        # parts show that treatment whatever the desktop is set to.
        self.override = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(display, self.override, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 4)
        gallery_css = Gtk.CssProvider()
        gallery_css.load_from_string(GALLERY_CSS)
        Gtk.StyleContext.add_provider_for_display(display, gallery_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 3)

        # In a LumaUI AppWindow the gallery is an ordinary Luma window: the
        # parts list is the sidebar island and the stage the content island,
        # under LumaUI's own title row, identity and controls (F0, ADR-052).
        framed = hasattr(self.window, "set_body")
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class("gallery-root")
        body = Gtk.Box(vexpand=True, spacing=9 if framed else 0)
        if framed:
            root.remove_css_class("gallery-root")
            root.add_css_class("gallery-framed")
        else:
            root.append(self._bar())
        root.append(body)

        self.nav = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.nav.add_css_class("gallery-nav")
        self.nav.update_property([Gtk.AccessibleProperty.LABEL], ["Parts"])
        nav_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.nav)
        nav_scroll.set_size_request(250, -1)
        nav_scroll.add_css_class("gallery-nav")
        if framed:
            from luma_appkit import Island
            sidebar = Island()
            sidebar.add_css_class("gallery-sidebar")
            sidebar.append(nav_scroll)
            nav_scroll.set_vexpand(True)
            body.append(sidebar)
        else:
            body.append(nav_scroll)

        # Sized by the page showing, so the phone frame is as narrow as that page allows.
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hhomogeneous=False,
                               transition_duration=lumaui.duration("fade"), hexpand=True, vexpand=True)
        for key, title, glyph, family, module in PAGES:
            row = Gtk.ListBoxRow()
            row.key = key
            line = Gtk.Box(spacing=10)
            line.append(Gtk.Image(icon_name=lumaui_icon(glyph)))
            line.append(Gtk.Label(label=title, xalign=0, hexpand=True))
            if key not in BUILDERS:
                row.add_css_class("coming")
                line.append(_label("soon", "gallery-code"))
            row.set_child(line)
            self.nav.append(row)
            content = BUILDERS[key]() if key in BUILDERS else page_coming(key, title, family, module)
            scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=content)
            self.stack.add_named(scroller, key)
        self.nav.connect("row-selected", lambda _l, row: row and self.stack.set_visible_child_name(row.key))

        # The stage stands for an application window: modal cards dim it, and at
        # phone width the parts adapt to its 375 px exactly as on a phone.
        self.island = ToastHost(self.stack)
        self.stage = LayerHost(self.island, name="window")
        self.frame = Gtk.Box(hexpand=True)
        self.frame.add_css_class("gallery-stage-frame")
        self.frame.append(self.stage)
        self.stage.set_hexpand(True)
        if framed:
            from luma_appkit import Island
            content = Island()
            content.set_hexpand(True)
            content.append(self._bar())
            content.append(self.frame)
            self.frame.set_vexpand(True)
            body.append(content)
            self.window.set_body(root)
        else:
            body.append(self.frame)
            self.window.set_child(root) if not hasattr(self.window, "set_content") else self.window.set_content(root)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._keys)
        self.window.add_controller(keys)

        self.set_appearance(options.appearance)
        self.set_phone(options.phone)
        first = next((r for r in self._rows() if r.key == options.page), None) or self.nav.get_row_at_index(0)
        self.nav.select_row(first)

    def _make_window(self, app: Gtk.Application) -> Gtk.Window:
        try:
            from luma_appkit import AppWindow, Command, CommandGroup, CommandRegistry
        except Exception:  # noqa: BLE001 - no Luma platform here: a stock window
            return Gtk.ApplicationWindow(application=app)
        def appearance(key: str) -> Command:
            return Command(f"gallery.{key}", {"high-contrast": "High Contrast"}.get(key, key.title()),
                           lambda: self.set_appearance(key),
                           checked=lambda: self.options.appearance == key)
        commands = CommandRegistry((
            CommandGroup("Appearance", tuple(appearance(k) for k in APPEARANCES)),
            CommandGroup(None, (
                Command("gallery.phone", "Phone Width", lambda: self.set_phone(not self.options.phone),
                        checked=lambda: self.options.phone),
                Command("gallery.quit", "Quit LumaUI Gallery", lambda: self.window.close(),
                        shortcut=("Ctrl", "Q")),
            )),
        ))
        return AppWindow(application=app, app_id=APP_ID, title="LumaUI Gallery",
                         icon_name="luma-v3-designer", commands=commands,
                         default_width=1180, default_height=780)

    def _rows(self):
        index = 0
        while (row := self.nav.get_row_at_index(index)) is not None:
            yield row
            index += 1

    def _bar(self) -> Gtk.Widget:
        bar = Gtk.Box(spacing=8)
        bar.add_css_class("gallery-bar")
        name = _label("LumaUI Gallery", "gallery-name")
        bar.append(name)
        bar.append(_label("preview · every part, live", "gallery-code", xalign=0))
        bar.append(Gtk.Box(hexpand=True))
        self.appearance_buttons: dict[str, Gtk.ToggleButton] = {}
        group: Gtk.ToggleButton | None = None
        for key, glyph, label in (("light", "sun", "Light"), ("dark", "moon", "Dark"), ("high-contrast", "eye", "High contrast")):
            button = Gtk.ToggleButton(group=group)
            group = group or button
            button.add_css_class("gallery-toggle")
            inner = Gtk.Box(spacing=6)
            inner.append(Gtk.Image(icon_name=lumaui_icon(glyph)))
            inner.append(Gtk.Label(label=label))
            button.set_child(inner)
            button.connect("toggled", lambda b, k=key: b.get_active() and self.set_appearance(k))
            self.appearance_buttons[key] = button
            bar.append(button)
        self.phone_button = Gtk.ToggleButton()
        self.phone_button.add_css_class("gallery-toggle")
        inner = Gtk.Box(spacing=6)
        inner.append(Gtk.Image(icon_name=lumaui_icon("smartphone")))
        inner.append(Gtk.Label(label="Phone width"))
        self.phone_button.set_child(inner)
        self.phone_button.connect("toggled", lambda b: self.set_phone(b.get_active()))
        bar.append(self.phone_button)
        return bar

    def set_appearance(self, key: str) -> None:
        self.options.appearance = key
        suffix = {"light": "", "dark": "-dark", "high-contrast": "-high-contrast"}[key]
        sheet = lumaui.find_asset(f"luma-appkit{suffix}-tokens.css")
        if sheet:
            self.override.load_from_path(sheet)
        try:
            gi.require_version("Adw", "1")
            from gi.repository import Adw
            Adw.StyleManager.get_default().set_color_scheme(
                Adw.ColorScheme.FORCE_DARK if key == "dark" else Adw.ColorScheme.FORCE_LIGHT)
        except (ImportError, ValueError):
            Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", key == "dark")
        button = self.appearance_buttons[key]
        if not button.get_active():
            button.set_active(True)

    def set_phone(self, on: bool) -> None:
        self.options.phone = on
        if on:
            self.frame.add_css_class("phone")
            self.stage.set_hexpand(False)
            self.stage.set_size_request(PHONE_WIDTH, -1)
            self.frame.set_halign(Gtk.Align.FILL)
            self.stage.set_halign(Gtk.Align.CENTER)
        else:
            self.frame.remove_css_class("phone")
            self.stage.set_hexpand(True)
            self.stage.set_size_request(-1, -1)
            self.stage.set_halign(Gtk.Align.FILL)
        if self.phone_button.get_active() != on:
            self.phone_button.set_active(on)
        if getattr(self, "_phone", None) is not None and self._phone != on:
            GLib.idle_add(lambda: (self._rebuild(), False)[1])
        self._phone = on

    def demo(self, name: str) -> None:
        """Open a structure part's floating state, for screenshots."""
        page = self.stack.get_visible_child()

        def find(kind, where=lambda _w: True):
            stack = [page]
            while stack:
                node = stack.pop(0)
                if isinstance(node, kind) and where(node):
                    return node
                child = node.get_first_child()
                while child is not None:
                    stack.append(child)
                    child = child.get_next_sibling()
            return None

        if name == "menu":
            open_menu(find(Gtk.Button, lambda b: b.has_css_class("gallery-button")))
        elif name == "side":
            find(SidebarToggle).toggle()
        elif name == "filter":
            find(SidebarFoot, lambda f: f.filter_button is not None).open_filters()

    def _rebuild(self) -> None:
        """Parts measure the window as they appear: rebuild the pages for a new width."""
        current = self.stack.get_visible_child_name()
        for key, title, _glyph, family, module in PAGES:
            old = self.stack.get_child_by_name(key)
            content = BUILDERS[key]() if key in BUILDERS else page_coming(key, title, family, module)
            scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=content)
            self.stack.remove(old)
            self.stack.add_named(scroller, key)
        if current:
            self.stack.set_visible_child_name(current)

    def _keys(self, _c, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if keyval in (Gdk.KEY_q, Gdk.KEY_w) and state & Gdk.ModifierType.CONTROL_MASK:
            self.window.close()
            return True
        return False

    def screenshot(self, path: str) -> None:
        width, height = self.window.get_width(), self.window.get_height()
        paintable = Gtk.WidgetPaintable.new(self.window)
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, width, height)
        node = snapshot.to_node()
        renderer = self.window.get_renderer()
        texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, width, height))
        texture.save_to_png(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LumaUI Gallery (preview)")
    parser.add_argument("--page", default="type", choices=[p[0] for p in PAGES])
    parser.add_argument("--appearance", default="light", choices=APPEARANCES)
    parser.add_argument("--phone", action="store_true")
    parser.add_argument("--screenshot", help="render the window to this PNG after it settles, then quit")
    parser.add_argument("--demo", choices=["toast", "confirm", *F3_DEMOS, *F2_DEMOS, *FAMILY_DEMOS],
                        help="open a toast, a dialog or a part's live state first (for screenshots)")
    options = parser.parse_args(argv)

    GLib.set_application_name("LumaUI Gallery")
    GLib.set_prgname(APP_ID)
    try:
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        app = Adw.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
    except (ImportError, ValueError):
        app = Gtk.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
    state: dict[str, Gallery] = {}

    def activate(application: Gtk.Application) -> None:
        gallery = state.setdefault("g", Gallery(application, options))
        gallery.window.present()
        if options.demo == "toast":
            GLib.timeout_add(400, lambda: (Toast.show(gallery.island, "Conversation deleted", kind="deleted",
                                                      undo=lambda: None), False)[1])
        elif options.demo == "confirm":
            GLib.timeout_add(400, lambda: (ask_uninstall(gallery.island), False)[1])
        elif options.demo in F3_DEMOS:
            GLib.timeout_add(500, lambda: (F3_DEMOS[options.demo](gallery), False)[1])
        elif options.demo in F2_DEMOS:
            GLib.timeout_add(600, lambda: (gallery.demo(options.demo), False)[1])
        elif options.demo in FAMILY_DEMOS:
            GLib.timeout_add(500, lambda: (FAMILY_DEMOS[options.demo](gallery), False)[1])
        if options.screenshot:
            def shoot() -> bool:
                gallery.screenshot(options.screenshot)
                application.quit()
                return False
            GLib.timeout_add(1600, shoot)

    app.connect("activate", activate)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
