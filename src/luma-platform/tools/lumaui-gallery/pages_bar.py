# SPDX-License-Identifier: Apache-2.0
"""LumaUI Gallery: the bar family's pages (KB-B).

The gallery takes `PAGES`, `BUILDERS` and `DEMOS` from here:

    bin/run gallery --page bar-entry [--demo entry-grow] [--appearance dark] [--phone]
"""
from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "appkit"))

from luma_appkit import ActionCenter, ActionEditor, BarAction, Toast, ToastHost, TypeLabel  # noqa: E402
from luma_appkit import SEPARATOR  # noqa: E402
from luma_appkit import CornerPill, ModeSwitch, Person, SPACER  # noqa: E402
from luma_appkit.bar_entry import BarEntry  # noqa: E402
from luma_appkit import BarChip  # noqa: E402
from luma_appkit.action_bubble import MenuItem  # noqa: E402
from luma_appkit.bar_items import BarMenu, BarReadout, BarSearch, BarThumbnail, SplitAction, ZoomControl  # noqa: E402
from luma_appkit.bar_document import DocumentHeader, ExportChoice, ExportSheet  # noqa: E402
from luma_appkit.bar_share import Collaborator, ShareSheet, ShareSubject, default_targets  # noqa: E402

# (key, title, Lucide glyph, family, module) in the gallery's PAGES shape.
PAGES = [
    ("bar-entry", "Bar entry", "text-cursor-input", "bar", None),
    ("bar-share", "Share sheet", "share-2", "bar", None),
    ("bar-modes", "Modes in the bar", "layout-template", "bar", None),
    ("bar-items", "Search, readouts, split key and zoom", "search", "bar", None),
    ("bar-document", "Document header and export", "clapperboard", "bar", None),
]

#: Live parts for the demos: page key → (center, state(key)).
_LIVE: dict[str, tuple] = {}


def _label(text: str, *css: str, wrap: bool = True) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0, wrap=wrap)
    for name in css:
        label.add_css_class(name)
    return label


def _page(title: str, lede: str, api: str) -> tuple[Gtk.Box, Gtk.Box]:
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    page.add_css_class("gallery-page")
    page.append(TypeLabel(title, role="title-1"))
    page.append(_label(lede, "gallery-lede"))
    page.append(_label(api, "gallery-code"))
    demo = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    demo.add_css_class("gallery-demo")
    page.append(demo)
    return page, demo


def _isle(title: str, caption: str) -> tuple[ToastHost, Gtk.Box]:
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START)
    content.add_css_class("gallery-isle-content")
    content.append(TypeLabel(title, role="title-2"))
    content.append(TypeLabel(caption, role="caption"))
    isle = ToastHost(content)
    isle.add_css_class("gallery-isle")
    isle.set_overflow(Gtk.Overflow.HIDDEN)
    return isle, content


def _switch(options: list[tuple[str, str]], choose) -> tuple[Gtk.Box, dict]:
    buttons: dict[str, Gtk.ToggleButton] = {}
    row = Gtk.Box(spacing=4)
    group = None
    for key, label in options:
        button = Gtk.ToggleButton(label=label, group=group)
        group = group or button
        button.add_css_class("gallery-toggle")
        button.connect("toggled", lambda b, k=key: b.get_active() and choose(k))
        buttons[key] = button
        row.append(button)
    return row, buttons


# ── Bar entry (AC1) ─────────────────────────────────────────────────────────

def _parse_task(text: str) -> list:
    chips, words = [], text.lower()
    if "tomorrow" in words:
        chips.append(("calendar", "Tomorrow"))
    if "today" in words:
        chips.append(("calendar", "Today"))
    for token in words.split():
        if token.rstrip("apm").isdigit() and token.endswith(("am", "pm")):
            chips.append(("clock", token.replace("pm", " pm").replace("am", " am")))
    return chips


def page_bar_entry() -> Gtk.Widget:
    page, demo = _page(
        "Bar entry",
        "A field you type into right in the resting bar, in the same well as Charlie’s prompt. Compose writes several "
        "lines (Return sends, Shift Return is a new line) and grows into the editor with its draft; quick adds a thing "
        "in words and shows what it understood; command runs a line with a ghost suggestion that Tab takes.",
        "BarEntry(kind=\"compose\"|\"quick\"|\"command\", placeholder=, text=, icon=, chips=, prefix=(cwd, branch), "
        "suggestion=, busy=, tools=, voice=, span=, close_label=, on_change=, on_submit=, on_close=, on_stop=, "
        "on_accept_suggestion=) · set_text · set_chips · "
        "set_suggestion · set_busy · clear")
    isle, _content = _isle("Priya Raman", "Messages · today")

    def sent() -> None:
        center.editor.clear()
        center.fold()
        compose.clear()
        Toast.show(isle, "Sent", kind="sent")

    editor = ActionEditor(
        "Message", "message-square", summary="to {}", summary_emphasis="Priya Raman",
        tools=[BarAction("bold", tooltip="Bold"), BarAction("italic", tooltip="Italic"),
               BarAction("strikethrough", tooltip="Strikethrough"), SEPARATOR,
               BarAction("list", tooltip="Bulleted list"), BarAction("link", tooltip="Link")],
        placeholder="Message Priya", primary=BarAction("send-horizontal", "Send", on_activate=sent))
    center = ActionCenter(editor).attach(isle)

    def send(_text: str) -> None:
        compose.clear()
        Toast.show(isle, "Sent", kind="sent")

    compose = BarEntry("compose", placeholder="Message Priya", on_submit=send,
                       voice=lambda: Toast.show(isle, "Recording", kind="done"),
                       tools=[BarAction("smile", tooltip="Emoji")])
    attach = BarAction("plus", tooltip="Photo, file or location")
    task = BarEntry("quick", icon="plus", placeholder="Add a task, like “Call Theo tomorrow 3pm”",
                    close_label="Done", on_close=lambda: state("compose"), on_change=lambda t: task.set_chips(_parse_task(t)),
                    on_submit=lambda t: (task.clear(), task.set_chips([]), Toast.show(isle, f"Added “{t}”", kind="done")))
    history = ["git status", "git pull --rebase", "ls -la", "make check"]
    command = BarEntry("command", prefix=("~/Projects/luma", "main"),
                       on_change=lambda t: command.set_suggestion(next((h for h in history if t and h.startswith(t)
                                                                        and h != t), None)),
                       on_submit=lambda t: (command.clear(), Toast.show(isle, f"Ran {t}", kind="done")))

    def state(key: str) -> None:
        if key == "compose":
            center.show_bar([attach, compose])
        elif key == "draft":
            compose.set_text("See you at the launch review — I’ll bring the deck")
            center.show_bar([attach, compose])
        elif key == "quick":
            task.set_text("Call Theo tomorrow 3pm")
            task.set_chips(_parse_task(task.text))
            center.show_bar([task])
        elif key == "command":
            command.set_text("git")
            command.set_suggestion("git status")
            center.show_bar([command])
        elif key == "grow":
            compose.set_text("See you at the launch review")
            center.show_bar([attach, compose])
            center.grow()

    switch, buttons = _switch([("compose", "Compose"), ("draft", "With text"), ("quick", "Quick"),
                               ("command", "Command"), ("grow", "Grown")], state)
    demo.append(switch)
    demo.append(isle)
    buttons["compose"].set_active(True)
    _LIVE["bar-entry"] = (center, state)
    return page


# ── Share sheet (SH1) ───────────────────────────────────────────────────────

PEOPLE = [Person("Priya Raman", username="priya"), Person("Nora Feld", username="nora"),
          Person("Theo Hart", username="theo"), Person("Sam Kline", username="sam"),
          Person("Alex Rivera", username="alex"), Person("Ana Costa", username="ana"),
          Person("Jon Kim", username="jon"), Person("Zoe Marsh", username="zoe")]


def page_bar_share() -> Gtk.Widget:
    page, demo = _page(
        "Share sheet",
        "One sheet for every app, anchored to its Share button; a drawer on a phone. There is no key: every target "
        "acts the moment it is tapped. Documents people edit together open on Work together (only when the app has "
        "a collaboration store); otherwise the sheet is Send a copy.",
        "ShareSheet.present(anchor, document=ShareSubject(title, subtitle, kind=, icon=|picture=|person=), people=, "
        "targets=, choices=(\"work-together\", \"send-copy\"), collaborators=, link_access=, link_for=, suggest=, "
        "on_choice=(choice, value) → toast line) · CornerPill(share=…)")
    isle, _content = _isle("Launch walkthrough", "Stage · Saved")

    def chose(choice: str, value: object) -> str | None:
        if choice == "send-to":
            return f"Sent to {value.name.split()[0]} in Messages"
        if choice == "target":
            return {"messages": "A new message with Launch walkthrough attached", "notes": "Added to a new note"}.get(value)
        return None

    file_doc = ShareSubject("Launch walkthrough.pdf", "PDF document · 2.4 MB", icon="file-text")
    stage_doc = ShareSubject("Launch walkthrough", "Stage · you and 2 others", kind="doc", icon="org.projectluma.Stage")
    collaborators = [Collaborator(PEOPLE[0], "edit"), Collaborator(PEOPLE[1], "comment")]

    def suggest(query: str):
        q = query.lower().lstrip("@")
        return [p for p in PEOPLE if q in p.name.lower() or q in p.username]

    state = {"doc": file_doc}

    def share(anchor: Gtk.Widget) -> None:
        together = state["doc"] is stage_doc
        ShareSheet.present(anchor, document=state["doc"], people=PEOPLE, targets=default_targets(state["doc"]), show_link=True,
                           choices=("work-together", "send-copy") if together else ("send-copy",),
                           collaborators=collaborators if together else (), owner=Person("Nick Miller", username="nick"),
                           suggest=suggest, on_choice=chose)

    corner = CornerPill(share=share, labelled=True, more=None, actions=[("pencil", "Edit", lambda: None)])
    corner.set_halign(Gtk.Align.END)
    corner.set_valign(Gtk.Align.START)
    corner.set_margin_top(16)
    corner.set_margin_end(16)
    isle.add_overlay(corner)

    def choose(key: str) -> None:
        state["doc"] = stage_doc if key == "doc" else file_doc

    switch, buttons = _switch([("file", "A file"), ("doc", "A document together")], choose)
    demo.append(switch)
    demo.append(isle)
    buttons["file"].set_active(True)

    def open_sheet(kind: str) -> None:
        buttons[kind].set_active(True)
        share(corner.controls["share"])

    _LIVE["bar-share"] = (corner, open_sheet)
    return page


# ── Modes in the bar (AC3) ──────────────────────────────────────────────────

def page_bar_modes() -> Gtk.Widget:
    page, demo = _page(
        "Modes in the bar",
        "An app's places or modes sit at the start of its bar (Clock, Phone, Photos); on a phone only the current one "
        "keeps its label. A text-only segment (labels_only) picks among words: aspect, zoom level, priority, theme.",
        "center.show_bar(items, modes=ModeSwitch([(key, label, icon), …])) · ModeSwitch([(key, label), …], "
        "labels_only=True, fill=)")
    isle, _content = _isle("World clock", "Clock")
    center = ActionCenter().attach(isle)

    def state(key: str) -> None:
        if key == "places":
            places = ModeSwitch([("world", "World", "globe"), ("alarms", "Alarms", "alarm-clock"),
                                 ("stopwatch", "Stopwatch", "timer"), ("timer", "Timer", "hourglass")], label="Clock")
            center.show_bar([SEPARATOR, BarAction("plus", "Add city")], modes=places)
        else:
            aspect = ModeSwitch([("free", "Free"), ("square", "Square"), ("4:3", "4:3"), ("16:9", "16:9")],
                                label="Aspect", current="4:3")
            center.show_bar([aspect, SPACER, BarAction("rotate-ccw", tooltip="Rotate"),
                             BarAction("check", "Done", primary=True)])

    switch, buttons = _switch([("places", "Places"), ("segment", "Text segment")], state)
    demo.append(switch)
    demo.append(isle)
    buttons["places"].set_active(True)
    _LIVE["bar-modes"] = (center, state)
    return page


# ── Search, subject, readouts, split key, zoom (AC2, AC4–AC7) ──────────────

def page_bar_items() -> Gtk.Widget:
    page, demo = _page(
        "Search, readouts, split key and zoom",
        "The rest of what goes in a bar: a search well (the full span on a phone), the selected subject with a face "
        "or thumbnail, text that reads rather than acts (an ETA, a pager), the key with its other ways, a word that "
        "picks a style, and zoom as a well, a range or a pill.",
        "BarSearch(placeholder, text=, label=, wide=, on_change=, on_activate=) · BarChip(label, lead=Person|BarThumbnail|"
        "icon, meta=, on_dismiss=) · BarThumbnail(paintable, duration=) · BarReadout(primary, secondary=, on_previous=, "
        "on_next=) · SplitAction(label, on_activate, menu) · BarMenu(label, menu) · BarAction(\"\", \"Save\") · "
        "ZoomControl(value, on_zoom, on_fit=, kind=\"well\"|\"range\"|\"pill\")")
    isle, _content = _isle("Launch", "A place, a message, a document")
    center = ActionCenter().attach(isle)
    zoom = {"value": 1.0}

    def zoomed(value: float) -> None:
        zoom["value"] = value
        for control in live_zoom:
            control.set_value(value)

    live_zoom: list = []

    def state(key: str) -> None:
        live_zoom.clear()
        if key == "search":
            center.show_bar([BarSearch("Search places", on_change=lambda t: None), SEPARATOR,
                             BarAction("navigation", tooltip="Where am I"), BarAction("mic", tooltip="Voice")])
        elif key == "eta":
            center.show_bar([BarReadout("12 min", "3.4 mi · arrive 4:12"), SEPARATOR,
                             BarAction("volume-2", tooltip="Voice"), BarAction("", "End", danger=True)])
        elif key == "subject":
            center.show_bar([BarChip("See you at the launch review — I’ll bring the deck", lead=PEOPLE[0],
                                     on_dismiss=lambda: state("search")),
                             BarAction("reply", "Reply"), BarAction("copy", "Copy"),
                             BarAction("ellipsis", tooltip="More")])
        elif key == "process":
            center.show_bar([BarChip("Viola", lead="app-window", meta="PID 3802", on_dismiss=lambda: state("search")),
                             BarAction("", "Quit", primary=True)])
        elif key == "viewer":
            well = ZoomControl(zoom["value"], zoomed, on_fit=lambda: zoomed(1.0))
            live_zoom.append(well)
            center.show_bar([well, SEPARATOR, BarAction("undo-2", tooltip="Undo"),
                             SplitAction("Save", lambda: None, [MenuItem("Save as…", icon="copy-plus"),
                                                                MenuItem("Share…", icon="share-2")])])
        elif key == "photos":
            size = ZoomControl(1.0, zoomed, kind="range", minimum=0.5, maximum=2.0, label="Photo size")
            live_zoom.append(size)
            center.show_bar([BarReadout("3 of 120", on_previous=lambda: None, on_next=lambda: None), SEPARATOR,
                             size, BarAction("", "Import", primary=True)])
        elif key == "notes":
            style = BarMenu("Text", [MenuItem("Heading"), MenuItem("Text", selected=True), MenuItem("Quote")],
                            name="Text style")
            center.show_bar([style, SEPARATOR, BarAction("bold", tooltip="Bold"), BarAction("italic", tooltip="Italic"),
                             BarAction("strikethrough", tooltip="Strikethrough")])

    switch, buttons = _switch([("search", "Search"), ("eta", "ETA"), ("subject", "Subject"), ("process", "App subject"),
                               ("viewer", "Zoom and split"), ("photos", "Pager and range"), ("notes", "Style menu")],
                              state)
    demo.append(switch)
    demo.append(isle)
    pill = ZoomControl(1.0, lambda v: pill.set_value(v), on_fit=lambda: pill.set_value(1.0), kind="pill")
    pill_widget = pill._build()
    pill_widget.set_halign(Gtk.Align.END)
    pill_widget.set_valign(Gtk.Align.END)
    pill_widget.set_margin_end(16)
    pill_widget.set_margin_bottom(16)
    isle.add_overlay(pill_widget)
    buttons["search"].set_active(True)
    _LIVE["bar-items"] = (center, state)
    return page


# ── Document header and export (CR5) ───────────────────────────────────────

EXPORTS = [ExportChoice("standard", "Standard", "1080p · H.264, plays anywhere", "about 84 MB"),
           ExportChoice("master", "Master", "4K · ProRes, for more editing", "about 1.2 GB", shape="master"),
           ExportChoice("vertical", "Vertical", "1080 × 1920 · for phones", "about 62 MB", shape="portrait"),
           ExportChoice("audio", "Audio only", "WAV · the mix", "about 7 MB", shape="audio")]


def page_bar_document() -> Gtk.Widget:
    page, demo = _page(
        "Document header and export",
        "The creative suite's title row says which document is open and what can be done with all of it: who is in "
        "it, Share, the one key (Export or Present) and More. At 720 or narrower the line and faces go. Export asks "
        "how in a sheet near the top of the window (a drawer on a phone); the app does the export and says how it went.",
        "DocumentHeader(title, icon=, subtitle=, people=, share=, primary=\"Export\"|\"Present\", on_primary=, more=) · "
        "ExportSheet.present(where, title=, subtitle=, choices=[ExportChoice(key, label, detail, size, shape=)], "
        "selected=, on_export=)")
    isle, _content = _isle("", "")
    holder: dict = {}

    def export(key: str) -> None:
        Toast.show(isle, f"Exporting {key}", kind="done")

    def open_export() -> None:
        ExportSheet.present(holder["header"], title="Export “Launch film”", subtitle="42 seconds, to Videos.",
                            choices=EXPORTS, selected="standard", on_export=export)

    header = DocumentHeader("Launch film", icon="org.projectluma.Reel", subtitle="Reel · Saved",
                            people=[PEOPLE[1], PEOPLE[0]],
                            share=lambda a: ShareSheet.present(a, document=ShareSubject("Launch film", "Reel",
                                                                                        kind="doc"), people=PEOPLE,
                                targets=default_targets(ShareSubject("Launch film", kind="doc")), show_link=True),
                            primary="Export", on_primary=open_export, more=lambda a: None)
    holder["header"] = header
    row = Gtk.Box()
    row.add_css_class("gallery-titlerow")
    row.append(header)
    frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    frame.add_css_class("gallery-frame")
    frame.append(row)
    frame.append(isle)
    demo.append(frame)
    _LIVE["bar-document"] = (header, open_export)
    return page


BUILDERS = {"bar-entry": page_bar_entry, "bar-share": page_bar_share, "bar-modes": page_bar_modes,
            "bar-items": page_bar_items, "bar-document": page_bar_document}

#: --demo states: name → callable(gallery).
DEMOS = {
    "entry-draft": lambda g: _LIVE["bar-entry"][1]("draft"),
    "entry-quick": lambda g: _LIVE["bar-entry"][1]("quick"),
    "entry-command": lambda g: _LIVE["bar-entry"][1]("command"),
    "entry-grow": lambda g: _LIVE["bar-entry"][1]("grow"),
    "share-copy": lambda g: _LIVE["bar-share"][1]("file"),
    "share-together": lambda g: _LIVE["bar-share"][1]("doc"),
    "modes-segment": lambda g: _LIVE["bar-modes"][1]("segment"),
    "export": lambda g: _LIVE["bar-document"][1](),
    **{f"items-{k}": (lambda k: lambda g: _LIVE["bar-items"][1](k))(k)
       for k in ("eta", "subject", "process", "viewer", "photos", "notes")},
}
