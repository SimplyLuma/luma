# SPDX-License-Identifier: Apache-2.0
"""lumaui-conform fixture for the bar family (a dev tool; never shipped).

Each tools/lumaui-conform/scenarios/bar-*.json runs this module headless. It
opens an undecorated window at the spec window's size and draws only the bar
part under test, placed where the v70 app places it: the action center floats
in a host whose rectangle is the v70 bar's positioning box (its island, the
foot 24 below the bar), given as LUMAUI_BAR_HOST / LUMAUI_BAR_HOST_PHONE
("x,y,w,h" in window pixels). The scenarios exclude everything else in the
v70 window, and neighbours in the bar that belong to the app or to another kit
family keep their room here (Room) and are excluded there, so the comparison
measures the bar family's part and nothing faked.

    LUMAUI_BAR_SURFACE=term LUMAUI_BAR_STATE=command python3 conform_bar.py
    LUMAUI_BAR_SHOT=/tmp/x.png ...   renders the window to a PNG and quits (a local look)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402

HERE = Path(__file__).resolve().parent
if (HERE.parents[1] / "appkit").is_dir():
    sys.path.insert(0, str(HERE.parents[1] / "appkit"))

from luma_appkit.bar_share import default_targets
from luma_appkit import (  # noqa: E402
    SEPARATOR, SPACER, ActionCenter, ActionEditor, BarAction, BarChip, BarEntry, BarMenu, BarReadout, BarSearch, Collaborator,
    DocumentHeader, ExportChoice, ExportSheet, ModeSwitch, Person, ShareSheet, ShareSubject, ZoomControl, lumaui,
)
from luma_appkit.action_bubble import MenuItem  # noqa: E402
from luma_appkit.action_center import register_item  # noqa: E402
from luma_appkit.structure_layers import LayerHost  # noqa: E402

APP_ID = "org.projectluma.LumaUIGallery"
SURFACE = os.environ.get("LUMAUI_BAR_SURFACE", "term")
STATE = os.environ.get("LUMAUI_BAR_STATE") or os.environ.get("LUMAUI_CONFORM_STATE", "")
_REPO = HERE.parents[3] if len(HERE.parents) > 3 else HERE
FACES = os.environ.get("LUMAUI_BAR_FACES", str(_REPO / "tests/fixtures/contacts-v70"))

CSS = """
window.conform-bar, window.conform-bar box.conform-frame { background: @luma_window; }
box.conform-room { min-height: 36px; }
box.conform-title { min-height: 46px; padding: 0 130px 0 14px; }
"""


class Room:
    """Room a neighbour takes in the bar (the app's, or another family's; excluded from the spec)."""

    def __init__(self, width: int, height: int = 36) -> None:
        self.width, self.height = width, height


def _room(item: Room) -> Gtk.Widget:
    box = Gtk.Box(valign=Gtk.Align.CENTER, can_target=False, can_focus=False)
    box.add_css_class("conform-room")
    box.set_size_request(item.width, item.height)
    return box


register_item(Room, lambda item, _size: _room(item))


def _rect(text: str | None) -> tuple[int, int, int, int] | None:
    if not text:
        return None
    x, y, w, h = (int(float(v)) for v in text.split(","))
    return x, y, w, h


def _face(key: str) -> Gdk.Paintable | None:
    path = Path(FACES) / f"face-{key}.jpg"
    try:
        return Gdk.Texture.new_from_filename(str(path)) if path.is_file() else None
    except GLib.Error:
        return None


def person(key: str) -> Person:
    """v70's people (PPL): name, @username, hue and face."""
    name, user, hue = {"PR": ("Priya Raman", "priya", 330), "NF": ("Nora Feld", "nora", 45),
                       "SK": ("Sam Kaur", "samk", 200), "TH": ("Theo Marsh", "theo", 350),
                       "MO": ("Dad", "", 20), "me": ("Nick", "nick", 250)}[key]
    return Person(name, username=user, hue=hue, picture=None if key == "me" else _face(key))


def noop(*_args) -> None:
    return None


# ── surfaces: (fixture, phone) -> None; each shows its part as the v70 app composes it ──

def term(fx: "Fixture", phone: bool) -> None:
    """Terminal: [session chip] [command field] [session keys]; the chip and keys are Terminal's."""
    fx.center.show_bar([Room(106), BarEntry("command", prefix=("~", None), label="Command"),
                        Room(40 if phone else 116)])


APP_ICON = "org.projectluma.Reel"
TASK_HINT = "Add a task, like “Call Theo tomorrow 3pm”"


def tasks(fx: "Fixture", phone: bool) -> None:
    if phone:
        # v71 tkBarPhone: the place picker and Search over a hairline over the always-there add field.
        entry = BarEntry("quick", icon="plus", placeholder="Add a task, like Call Theo tomorrow 3pm", label="Add a task")
        if STATE == "chips":
            entry.set_text("Call Theo tomorrow 3pm")
            entry.set_chips([("calendar", "Tomorrow, 3:00 PM")])
        fx.center.show_bar([BarAction("sun", "Today", dropdown=True, panel=Gtk.Label, tooltip="Today"), SPACER,
                            BarAction("search", tooltip="Search")], entry=entry)
        return
    entry = BarEntry("quick", icon="plus", placeholder=TASK_HINT, label="Add a task", close_label="Done",
                     on_close=noop)
    if STATE == "chips":
        entry.set_text("Call Theo tomorrow 3pm")
        entry.set_chips([("calendar", "Tomorrow, 3:00 PM")])
    fx.center.show_bar([entry])


def messages(fx: "Fixture", phone: bool) -> None:
    if STATE == "subject":
        chip = BarChip("Morning! RC2 finished building overnight", lead=person("SK"), on_dismiss=noop)
        fx.center.show_bar([chip, Room(81), Room(82), Room(81), Room(36)])
        return
    fx.center.set_editor(ActionEditor("Message", "message-square", placeholder="Message Launch crew",
                                      primary=BarAction("send-horizontal", "Send")))
    entry = BarEntry("compose", placeholder="Message Launch crew", label="Message", voice=noop,
                     tools=[BarAction("smile", tooltip="Emoji")])
    fx.center.show_bar([Room(36), entry])


def maps(fx: "Fixture", phone: bool) -> None:
    if STATE == "eta":
        fx.center.show_bar([BarReadout("8 min", "2.1 mi · arrive 10:24 PM"), Room(96)])
        return
    fx.center.show_bar([BarSearch("Search places and addresses", span="wide"), Room(126)])


def photos(fx: "Fixture", phone: bool) -> None:
    if STATE == "pager":
        pager = BarReadout("1 of 22", on_previous=noop, on_next=noop, tone="media")
        fx.center.show_bar([Room(81), pager])
        pager.set_bounds(False, True)  # the first photo: nothing before it
        return
    zoom = ModeSwitch([("years", "Years"), ("months", "Months"), ("days", "Days"), ("all", "All")],
                      current="days", label="Zoom")
    size = ZoomControl(168, noop, kind="range", minimum=96, maximum=300, label="Photo size")
    fx.center.show_bar([BarSearch("Search places, people", span="narrow", label="Search photos"), SEPARATOR, zoom,
                        size, SEPARATOR, Room(44 if phone else 90)])


def viewer(fx: "Fixture", phone: bool) -> None:
    fx.center.show_bar([ZoomControl(0.15 if phone else 0.49, noop, on_fit=noop), SEPARATOR])


def clock(fx: "Fixture", phone: bool) -> None:
    if phone:
        # v71 Clock on a phone (.cktabbar): the places as K-NAV's compact tabs in the bar, as wide as they are.
        from luma_appkit.structure_tabs import TabBar
        tabs = TabBar([("world", "World", "globe"), ("alarms", "Alarms", "alarm-clock"),
                       ("stopwatch", "Stopwatch", "timer"), ("timer", "Timer", "hourglass")],
                      current="world", compact=True)
        tabs.set_count("alarms", 2)
        fx.center.show_bar([tabs])
        return
    places = ModeSwitch([("world", "World", "globe"), ("alarms", "Alarms", "alarm-clock", 2),
                         ("stopwatch", "Stopwatch", "timer"), ("timer", "Timer", "hourglass")],
                        current="world", label="Clock")
    fx.center.show_bar([], modes=places)


def notes(fx: "Fixture", phone: bool) -> None:
    style = BarMenu("Text", [MenuItem("Heading"), MenuItem("Text", selected=True), MenuItem("Quote")],
                    name="Text style")
    fx.center.show_bar([style, Room(228 if phone else 386)])


def memos(fx: "Fixture", phone: bool) -> None:
    """Voice Memos recording, paused at 0:00 (v70 .merec, then Resume and the red Done key, .mestop).

    v71 on a phone (meBarPhone): the status as an inset well, Resume as an icon, Done the primary key."""
    if phone:
        fx.center.show_bar([BarChip("Paused", meta="0:00", live=True), BarAction("mic", tooltip="Resume"),
                            BarAction("", "Done", primary=True)], fill=True)
        return
    fx.center.show_bar([BarChip("Paused", meta="0:00", live=True), BarAction("mic", "Resume"),
                        BarAction("square", "Done", record=True)])


def stage(fx: "Fixture", phone: bool) -> None:
    fx.center.hide_bar()
    title = Gtk.Box()
    title.add_css_class("conform-title")
    # Stage installs no icon yet; an installed Luma app icon stands in (compared as an app icon box).
    header = DocumentHeader("Launch walkthrough", icon=APP_ICON, subtitle="Stage · Saved",
                            people=[person("PR"), person("NF")], share=noop, primary="Present", on_primary=noop,
                            more=noop)
    header.set_name("doc-header")
    title.append(header)
    fx.top.append(title)
    if STATE == "share":
        def open_sheet() -> bool:
            sheet = ShareSheet.present(
                header.controls["share"],
                document=ShareSubject("Launch walkthrough", "Stage · you and 2 others", kind="doc",
                                      icon=APP_ICON),
                choices=("work-together", "send-copy"), owner=person("me"),
                targets=default_targets(ShareSubject("Launch walkthrough", kind="doc")), show_link=True,
                collaborators=[Collaborator(person("PR"), "edit"), Collaborator(person("NF"), "comment")])
            sheet.set_name("share-sheet")
            return False
        GLib.timeout_add(300, open_sheet)


REEL_CHOICES = [ExportChoice("share", "Share", "1080p · H.264", "about 180 MB"),
                ExportChoice("master", "Master", "4K · ProRes 422", "about 11.2 GB", shape="master"),
                ExportChoice("vertical", "Vertical", "1080 × 1920 · for phones", "about 150 MB", shape="portrait"),
                ExportChoice("audio", "Sound only", "WAV · 48 kHz", "about 8 MB", shape="audio")]


def reel(fx: "Fixture", phone: bool) -> None:
    fx.center.hide_bar()

    def open_sheet() -> bool:
        sheet = ExportSheet.present(fx.center, title="Export “Launch film”", subtitle="44 seconds, to Videos.",
                                    choices=REEL_CHOICES, selected="share", on_export=noop)
        sheet.set_name("export-sheet")
        return False
    GLib.timeout_add(300, open_sheet)


def grow(fx: "Fixture", phone: bool) -> None:
    """v71: Filer's selection bar on a phone, grown (⋯, Share) and holding a file (Move): the kit's grown bar."""
    from luma_appkit import PanelRow, SharePanel, panel_list
    more_rows = lambda: panel_list([PanelRow("Open in…", icon="app-window", submenu=True),  # noqa: E731
                                    PanelRow("Rename", icon="pencil"), PanelRow("Duplicate", icon="copy"),
                                    PanelRow("Compress", icon="file-archive"),
                                    PanelRow("Move to Trash", icon="trash-2", danger=True)], label="More")
    share = lambda: SharePanel(people=[person(k) for k in ("PR", "NF", "SK", "TH", "MO")])  # noqa: E731
    fx.center.show_bar([BarAction("copy", tooltip="Copy"),
                        BarAction("share-2", tooltip="Share", panel=share, key="share"),
                        BarAction("folder-input", tooltip="Move"),
                        BarAction("info", tooltip="Details", panel=Gtk.Label, key="info"),
                        BarAction("ellipsis", tooltip="More", panel=more_rows, key="more"),
                        BarAction("x", tooltip="Clear selection")])
    buttons = {}
    child = fx.center.bar_row.get_first_child()
    while child is not None:
        buttons[getattr(child.bar_item, "tooltip", "")] = child
        child = child.get_next_sibling()
    if STATE == "more":
        fx.center.grow("more", more_rows(), anchor=buttons["More"])
    elif STATE == "share":
        fx.center.grow("share", share(), anchor=buttons["Share"])
    elif STATE == "moving":
        fx.center.hold("press-release.write", None)  # the face is the file's own thumbnail (the app's; excluded)
    fx.center.panel.set_name("bar-panel")


def edit(fx: "Fixture", phone: bool) -> None:
    """v71 Photos' editor (.lac.ped / .phed): the kit's edit layout around Photos' own sliders (Room)."""
    editor = ActionEditor("Edit", "sliders-horizontal",
                          modes=[("looks", "Looks", "palette"), ("light", "Light", "sun"),
                                 ("colour", "Color", "droplet"), ("crop", "Crop", "crop")],
                          mode="light", tools=[BarAction("wand-sparkles", "Auto", tooltip="Let Photos adjust it")],
                          body=_room(Room(300, 330 if phone else 128)),
                          primary=BarAction("", "Done"), on_discard=noop,
                          revert=BarAction("rotate-ccw", "Revert to original", sensitive=False))
    fx.center.set_editor(editor)
    fx.center.show_bar([BarAction("pencil", tooltip="Edit")])
    fx.center.grow()
    fx.center.card.set_name("editor")


SURFACES = {"edit": edit, "grow": grow, "term": term, "tasks": tasks, "messages": messages, "maps": maps, "photos": photos, "viewer": viewer,
            "clock": clock, "notes": notes, "memos": memos, "stage": stage, "reel": reel}
#: Surfaces whose window host sits under a 46 title row (v70's scrims and sheets start below it).
UNDER_TITLE = {"reel"}


class Fixture(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.connect("activate", self._activate)

    def _activate(self, _app: Gtk.Application) -> None:
        display = Gdk.Display.get_default()
        lumaui.install(display)
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
        size = os.environ.get("LUMAUI_CONFORM_SIZE", "1180x740").lower().split("x")
        window = Gtk.ApplicationWindow(application=self, decorated=False, title="Bar")
        window.add_css_class("conform-bar")
        window.add_css_class("luma-no-native-surfaces")  # the patched GTK's own frame would inset the content
        window.set_default_size(int(size[0]), int(size[1]))
        frame = Gtk.Overlay()
        base = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        base.add_css_class("conform-frame")
        self.top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        base.append(self.top)
        frame.set_child(base)
        self.host = LayerHost(Gtk.Box(hexpand=True, vexpand=True))
        self.host.set_name("bar-host")
        frame.add_overlay(self.host)
        frame.connect("get-child-position", self._place)
        window_host = LayerHost(frame, name="window")
        if SURFACE in UNDER_TITLE:
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            title = Gtk.Box()
            title.add_css_class("conform-title")
            column.append(title)
            window_host.set_vexpand(True)
            column.append(window_host)
            window.set_child(column)
        else:
            window.set_child(window_host)
        self.center = ActionCenter().attach(self.host)
        self.center.bar_row.set_name("bar-row")
        self.center.bar.set_name("bar")
        self.frame = frame
        self._built_for: bool | None = None
        window.add_tick_callback(self._tick)
        window.present()

    def _place(self, _overlay, widget: Gtk.Widget, allocation: Gdk.Rectangle) -> bool:
        if widget is not self.host:
            return False
        window = self.frame.get_root()
        phone = 0 < window.get_width() <= 639
        rect = _rect(os.environ.get("LUMAUI_BAR_HOST_PHONE" if phone else "LUMAUI_BAR_HOST"))
        x, y, w, h = rect or (0, 0, self.frame.get_width(), self.frame.get_height())
        allocation.x, allocation.y, allocation.width, allocation.height = x, y, w, h
        return True

    def _tick(self, widget: Gtk.Widget, _clock) -> bool:
        width = widget.get_width()
        if width <= 0:
            return GLib.SOURCE_CONTINUE
        phone = width <= 639
        if phone != self._built_for:  # (re)build once the window has its size: phone or not
            self._built_for = phone
            GLib.idle_add(self._build)
        return GLib.SOURCE_CONTINUE

    def _build(self) -> bool:
        child = self.top.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.top.remove(child)
            child = following
        self.center._apply_width()
        SURFACES[SURFACE](self, bool(self._built_for))
        shot = os.environ.get("LUMAUI_BAR_SHOT")
        if shot:  # a local look without the harness: render the window once it settles, then quit
            GLib.timeout_add(1500, self._shoot, shot)
        return False

    def _shoot(self, path: str) -> bool:
        window = self.frame.get_root()
        width, height = window.get_width(), window.get_height()
        snapshot = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(window).snapshot(snapshot, width, height)
        texture = window.get_renderer().render_texture(snapshot.to_node(), Graphene.Rect().init(0, 0, width, height))
        texture.save_to_png(path)
        self.quit()
        return False


def main(argv: list[str] | None = None) -> int:
    return Fixture().run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
