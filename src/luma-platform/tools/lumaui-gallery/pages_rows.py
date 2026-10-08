# SPDX-License-Identifier: Apache-2.0
"""LumaUI Gallery: the rows family's pages (KB-A).

The gallery takes `PAGES` and `BUILDERS` from here. Each page is a scenario
state measured against v70 (the sidebars against `?app=messages`, `phone`,
`tasks`, `monitor`, `viewer`, `notes`); run it alone with

    python3 lumaui_gallery.py --page rows-people [--appearance dark] [--phone]
"""
from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

HERE = Path(__file__).resolve().parent
if str(HERE.parents[1] / "appkit") not in sys.path:
    sys.path.insert(0, str(HERE.parents[1] / "appkit"))

from luma_appkit import (  # noqa: E402
    AvatarStack, Favourite, FavouritesStrip, GroupFace, Mark, MarkButton, MarkValue,
    PresenceFace, ProgressLine, RowLead, SidebarRow, TypeLabel, apply_sidebar_variant, apply_type, type_font,
)
from luma_appkit.rows_navigation import append_section  # noqa: E402

try:
    from luma_appkit import NavigationSidebar  # noqa: E402
    NavigationSidebar.__init__  # widgets.py needs the Luma typelibs
    from luma_appkit import widgets as _widgets  # noqa: F401,E402
except (ImportError, ValueError):
    class NavigationSidebar(Gtk.Box):  # type: ignore[no-redef]
        """Where widgets.py cannot load (a Mac without the Luma typelibs): the same classes and list."""

        def __init__(self, *, variant: str | None = None, width: str | None = None) -> None:
            super().__init__(orientation=Gtk.Orientation.VERTICAL)
            for name in ("luma-navigation-sidebar", "luma-sidebar-surface"):
                self.add_css_class(name)
            self.variant = variant
            self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
            self.list.add_css_class("luma-navigation-list")
            self.append(self.list)
            if variant or width:
                apply_sidebar_variant(self, variant or "people", width=width)

        def append_row(self, row: Gtk.Widget) -> None:
            self.list.append(row)

        def append_section(self, label: str, *, action=None) -> None:
            append_section(self, label, action=action)

# (key, title, Lucide glyph, family, module) in the gallery's PAGES shape.
PAGES = [
    ("rows-people", "Sidebar: people", "users", "rows", None),
    ("rows-destinations", "Sidebar: destinations", "list-checks", "rows", None),
    ("rows-resources", "Sidebar: resources", "gauge", "rows", None),
    ("rows-files", "Sidebar: files", "image", "rows", None),
    ("rows-tree", "Sidebar: tree", "folder", "rows", None),
    ("rows-type", "Type roles for app surfaces", "type", "rows", None),
    ("rows-progress", "Progress line", "activity", "rows", None),
    ("rows-stack", "Avatar stack and presence", "users", "rows", None),
    ("rows-favourites", "Favourites strip", "star", "rows", None),
    ("rows-mark", "Mark and picker", "tag", "rows", None),
    ("rows-menu", "Rich menu rows", "list", "rows", None),
    ("rows-identity", "App icon, note card, lit glow", "badge-check", "rows", None),
]


def _label(text: str, *css: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0, wrap=True)
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


def _frame(sidebar: Gtk.Widget, name: str) -> Gtk.Widget:
    """The sidebar on the window's frame, beside an empty island, as v70 draws it."""
    frame = Gtk.Box(height_request=460)
    frame.add_css_class("gallery-frame")
    frame.set_name(name)
    sidebar.set_name(f"{name}-side")
    frame.append(sidebar)
    island = Gtk.Box(hexpand=True)
    island.add_css_class("gallery-island")
    island.add_css_class("gallery-isle")
    frame.append(island)
    return frame


def _sparkline(values: list[float]) -> Gtk.Widget:
    """Monitor's 58x22 sparkline, drawn for the gallery (DT1 builds the real one)."""
    area = Gtk.DrawingArea(content_width=58, content_height=22)

    def draw(_area, cr, width, height):
        colour = area.get_color()
        cr.set_source_rgba(colour.red, colour.green, colour.blue, 0.7)
        cr.set_line_width(1.5)
        for i, v in enumerate(values):
            x, y = i * width / (len(values) - 1), height - v * height
            cr.line_to(x, y) if i else cr.move_to(x, y)
        cr.stroke()
    area.set_draw_func(draw)
    return area


# ── SB1: the five variants ─────────────────────────────────────────────────

def page_people() -> Gtk.Widget:
    page, demo = _page("Sidebar: people", "Conversations and calls: a 46 px face (38 for Phone), the time on line 1, "
                       "the preview with its count or pin on line 2. Unread is bold with an accent count; missed is red.",
                       'NavigationSidebar(variant="people"); SidebarRow(title, lead=RowLead.face(name, online=True), '
                       'meta="9:41", subtitle=…, attention=True, trail=3, pinned=True)')
    sidebar = NavigationSidebar(variant="people")
    sidebar.append_row(SidebarRow("Launch crew", lead=RowLead.group(["Priya Raman", "Sam Ortiz"]), meta="9:41",
                                  subtitle="Sam: I'll bring the banner", attention=True, trail=3))
    first = SidebarRow("Priya Raman", lead=RowLead.face("Priya Raman", online=True), meta="Tue",
                       subtitle="Photos from Saturday are up", pinned=True)
    sidebar.append_row(first)
    sidebar.append_row(SidebarRow("Mum", lead=RowLead.face("Mum"), meta="Yesterday", subtitle="Call me when you land"))
    sidebar.append_row(SidebarRow("Leo Park", lead=RowLead.face("Leo Park"), meta="Mon", subtitle="👍", muted=True))
    sidebar.list.select_row(first)
    phone = NavigationSidebar(variant="people", width="regular")
    phone.append_row(SidebarRow("Sam Ortiz", lead=RowLead.face("Sam Ortiz", size="medium"), subtitle="Voice · missed",
                                subtitle_icon="phone-missed", attention="missed", trail="9:02"))
    phone.append_row(SidebarRow("Priya, Sam", lead=RowLead.group(["Priya Raman", "Sam Ortiz"], size="medium"),
                                subtitle="Video · 12 min", subtitle_icon="phone-outgoing", trail="Tue"))
    row = Gtk.Box(spacing=16)
    row.append(_frame(sidebar, "rows-people"))
    row.append(_frame(phone, "rows-people-phone"))
    demo.append(row)
    return page


def page_destinations() -> Gtk.Widget:
    page, demo = _page("Sidebar: destinations", "Places in the app: 36 px rows, a glyph or a list's mark, a count "
                       "at the end. A heading may carry its action (+ New list).",
                       'NavigationSidebar(variant="destinations"); SidebarRow("Today", lead=RowLead.icon("sun"), '
                       'trail=4, attention=True); sidebar.append_section("Lists", action=("plus", "New list", fn))')
    sidebar = NavigationSidebar(variant="destinations")
    today = SidebarRow("Today", lead=RowLead.icon("sun"), trail=4, attention=True)
    sidebar.append_row(today)
    sidebar.append_row(SidebarRow("Upcoming", lead=RowLead.icon("calendar"), trail=12))
    sidebar.append_row(SidebarRow("Done", lead=RowLead.icon("square-check")))
    sidebar.append_section("Lists", action=("plus", "New list", lambda: None))
    sidebar.append_row(SidebarRow("Launch", lead=RowLead.mark(MarkValue(hue="orange")),
                                  trail=AvatarStack(["Priya Raman", "Sam Ortiz", "Ana Lima"], size="row")))
    sidebar.append_row(SidebarRow("Groceries", lead=RowLead.mark(MarkValue(hue="green")), trail=7))
    sidebar.list.select_row(today)
    demo.append(_frame(sidebar, "rows-destinations"))
    return page


def page_resources() -> Gtk.Widget:
    page, demo = _page("Sidebar: resources", "Things with a live value: a square well (lit when chosen) or a round "
                       "glyph face, two lines, a live trail.",
                       'NavigationSidebar(variant="resources"); SidebarRow("Processor", lead=RowLead.well("cpu"), '
                       'subtitle="12%", trail=sparkline)')
    sidebar = NavigationSidebar(variant="resources")
    cpu = SidebarRow("Processor", lead=RowLead.well("cpu"), subtitle="12% · 8 cores",
                     trail=_sparkline([.2, .3, .25, .5, .4, .35, .6, .3]))
    sidebar.append_row(cpu)
    sidebar.append_row(SidebarRow("Memory", lead=RowLead.well("layers"), subtitle="9.1 of 16 GB",
                                  trail=_sparkline([.5, .52, .55, .54, .56, .57, .56, .58])))
    sidebar.append_row(SidebarRow("My location", lead=RowLead.glyph("navigation", accent=True),
                                  subtitle="Luma Studio, 2nd St"))
    sidebar.append_row(SidebarRow("Duende", lead=RowLead.glyph("utensils"), subtitle="468 2nd St"))
    sidebar.list.select_row(cpu)
    demo.append(_frame(sidebar, "rows-resources"))
    return page


def _swatch(r: int, g: int, b: int) -> Gdk.Paintable:
    import struct
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib
    data = GLib.Bytes.new(struct.pack("BBBB", r, g, b, 255) * (42 * 34))
    return Gdk.MemoryTexture.new(42, 34, Gdk.MemoryFormat.R8G8B8A8, data, 42 * 4)


def page_files() -> Gtk.Widget:
    page, demo = _page("Sidebar: files", "Documents: a 42x34 thumbnail over two small lines; a page for a document; "
                       "a dimmed row when the file is gone. Memos' rows have no lead.",
                       'NavigationSidebar(variant="files"); SidebarRow("IMG_2041.jpg", lead=RowLead.thumbnail(texture), '
                       'subtitle="4.2 MB"); SidebarRow(…, lead=RowLead.thumbnail(kind="missing"), missing=True)')
    sidebar = NavigationSidebar(variant="files")
    sidebar.append_section("Today")
    shot = SidebarRow("IMG_2041.jpg", lead=RowLead.thumbnail(_swatch(96, 140, 190)), subtitle="4.2 MB · 2 marks")
    sidebar.append_row(shot)
    sidebar.append_row(SidebarRow("Lease agreement.pdf", lead=RowLead.thumbnail(kind="document"), subtitle="312 KB"))
    sidebar.append_row(SidebarRow("Scan 12.png", lead=RowLead.thumbnail(kind="missing"), subtitle="Moved or deleted",
                                  missing=True))
    sidebar.list.select_row(shot)
    memos = NavigationSidebar(variant="files", width="wide")
    memos.append_row(SidebarRow("Standup notes", title_icon="star", subtitle="Today · 4:12", trail=Gtk.Label(label="▁▃▅▂")))
    memos.append_row(SidebarRow("Idea for the dock", subtitle="Yesterday · 0:48"))
    row = Gtk.Box(spacing=16)
    row.append(_frame(sidebar, "rows-files"))
    row.append(_frame(memos, "rows-files-memos"))
    demo.append(row)
    return page


def page_tree() -> Gtk.Widget:
    page, demo = _page("Sidebar: tree", "Folders and pages: 32 px rows indented 16 per level, a twisty (Right "
                       "opens, Left closes), a mark button, drag to move before, after or inside.",
                       'NavigationSidebar(variant="tree"); SidebarRow("Work", folder=True, expanded=True, '
                       'mark=MarkValue(hue="violet"), trail=12, on_expand=fn, drag="f:work", on_drop=fn)')
    sidebar = NavigationSidebar(variant="tree")
    sidebar.append_row(SidebarRow("Work", folder=True, expanded=True, mark=MarkValue(hue="violet"), trail=12,
                                  drag="f:work", on_drop=lambda *_a: None))
    page_row = SidebarRow("Launch plan", depth=1, expanded=False, mark=MarkValue("icon", "blue", "file-text"),
                          trail=AvatarStack(["Priya Raman", "Sam Ortiz"], size="small"), drag="n:1",
                          on_drop=lambda *_a: None)
    sidebar.append_row(page_row)
    sidebar.append_row(SidebarRow("Press kit", depth=1, expanded=None, mark=MarkValue("emoji", "blue", None, "📣"),
                                  drag="n:2", on_drop=lambda *_a: None))
    sidebar.append_row(SidebarRow("Home", folder=True, expanded=False, mark=MarkValue(hue="green"), trail=4))
    sidebar.list.select_row(page_row)
    demo.append(_frame(sidebar, "rows-tree"))
    return page


# ── TY1, DT3, SB3, SB2, SB4 ───────────────────────────────────────────────

def page_type_roles() -> Gtk.Widget:
    page, demo = _page("Type roles for app surfaces", "Roles for what only one app draws, so no app sets a one-off "
                       "size. Mono has Python access for a terminal.",
                       'apply_type(label, "reading"); TypeLabel("72", role="temperature", unit="°"); '
                       'terminal.set_font(type_font("mono"))')
    for role, text in (("reading", "Your flight lands at 6:40, so there is time for dinner."),
                       ("assistant-title", "Trip to Lisbon"), ("temperature", "72°"), ("place", "Kansas City"),
                       ("condition", "Mostly sunny"), ("album-title", "Blue Hour"), ("section-day", "Tuesday"),
                       ("dial", "816 555 0142"), ("mono", "nick@luma ~ % ls -la")):
        line = Gtk.Box(spacing=16)
        tag = Gtk.Label(label=role, xalign=0, width_chars=16)
        tag.add_css_class("gallery-code")
        line.append(tag)
        label = Gtk.Label(label=text, xalign=0)
        apply_type(label, role)
        line.append(label)
        demo.append(line)
    demo.append(_label(f'type_font("mono") → {type_font("mono").to_string()}', "gallery-code"))
    return page


def page_progress() -> Gtk.Widget:
    page, demo = _page("Progress line", "How far along: a 4 px line in a row or meter, 3 px on a tile, 8 px in a well "
                       "for a hero; tones accent, good, neutral, danger.",
                       'ProgressLine(0.42); ProgressLine(0.3, size="tile", tone="neutral"); '
                       'ProgressLine(0.5, size="hero", label="Downloading")')
    for size, tone, value in (("row", "accent", 0.42), ("meter", "accent", 0.66), ("row", "good", 0.4),
                              ("tile", "neutral", 0.3), ("hero", "accent", 0.5), ("row", "danger", 0.9)):
        line = Gtk.Box(spacing=16)
        tag = Gtk.Label(label=f"{size} · {tone}", xalign=0, width_chars=16)
        tag.add_css_class("gallery-code")
        line.append(tag)
        bar = ProgressLine(value, size=size, tone=tone, label=f"{size} {tone}")
        bar.set_size_request(240, -1)
        bar.set_hexpand(False)
        line.append(bar)
        demo.append(line)
    return page


def page_stack() -> Gtk.Widget:
    page, demo = _page("Avatar stack and presence", "People together: overlapping faces ringed in their ground, "
                       "+N past max, green for who is here now; a group's face; a face with its presence dot.",
                       'AvatarStack(people, size="small"|"row"|"header", max=3, live={"Priya Raman"}); '
                       'GroupFace(people, size=46); PresenceFace(name, size=46, online=True)')
    people = ["Priya Raman", "Sam Ortiz", "Ana Lima", "Leo Park", "Mia Chen"]
    for size in ("small", "row", "header"):
        line = Gtk.Box(spacing=16)
        tag = Gtk.Label(label=size, xalign=0, width_chars=10)
        tag.add_css_class("gallery-code")
        line.append(tag)
        line.append(AvatarStack(people[:3], size=size))
        line.append(AvatarStack(people, size=size, max=4, live={"Priya Raman"}))
        demo.append(line)
    faces = Gtk.Box(spacing=16)
    faces.append(GroupFace(people[:2], size=46))
    faces.append(GroupFace(people[:2], size=38))
    faces.append(PresenceFace("Priya Raman", size=46, online=True))
    faces.append(PresenceFace("Sam Ortiz", size=38, online=True))
    demo.append(faces)
    return page


def page_favourites() -> Gtk.Widget:
    page, demo = _page("Favourites strip", "Favourites above a list: people's faces (Phone) or places' glyph wells "
                       "with their time (Maps). Arrows move, Enter opens.",
                       'FavouritesStrip([("Priya", "Priya Raman", None), …], on_open=call); '
                       'FavouritesStrip([Favourite("Home", icon="house", sub="12 min")], kind="places")')
    phone = Gtk.Box(width_request=268)
    phone.append(FavouritesStrip([("Priya", "Priya Raman", None), ("Sam", "Sam Ortiz", None), ("Mum", "Mum", None),
                                  ("Leo", "Leo Park", None)]))
    demo.append(phone)
    maps = Gtk.Box(width_request=252)
    maps.append(FavouritesStrip([Favourite("Home", icon="house", sub="12 min"),
                                 Favourite("Work", icon="briefcase", sub="21 min"),
                                 Favourite("Gym", icon="activity", sub="9 min")],
                                kind="places"))
    demo.append(maps)
    return page


def page_mark() -> Gtk.Widget:
    page, demo = _page("Mark and picker", "How anything gets its mark: a coloured dot (the default), an icon or an "
                       "emoji. The button opens the picker beside it; a drawer on a phone.",
                       'Mark(kind="icon", icon="book", hue="green"); MarkButton(MarkValue(hue="orange"), '
                       'on_change=save); MarkPicker.present(anchor, value, on_pick=fn)')
    line = Gtk.Box(spacing=12)
    for value in (MarkValue(), MarkValue(hue="orange"), MarkValue("icon", "green", "book"),
                  MarkValue("emoji", "blue", None, "📚"), MarkValue(hue="grey")):
        line.append(Mark(value=value))
    for value in (MarkValue(hue="violet"), MarkValue("icon", "red", "heart", None)):
        line.append(Mark(value=value, large=True))
    demo.append(line)
    buttons = Gtk.Box(spacing=12)
    buttons.append(MarkButton(MarkValue(hue="orange")))
    buttons.append(MarkButton(MarkValue("icon", "teal", "folder")))
    demo.append(buttons)
    return page


def page_menu() -> Gtk.Widget:
    page, demo = _page("Rich menu rows", "Menu rows with more to say: a switch that stays open, a value with its "
                       "chevron, a subtitle, trailing actions on hover, an inline rename (F2), busy; custom content "
                       "in a MenuSection. The same rows in the phone drawer.",
                       'FloatingMenu([RichMenuItem("Grid", toggle=True, on_toggle=fn), RichMenuItem("Timer", '
                       'value="3 s"), RichMenuItem("zsh", subtitle=…, trail=[RowAction(…)], rename=True, '
                       'on_rename=fn), MenuSection(widget)]).popup(anchor)')
    from luma_appkit import MenuSection, RichMenuItem, RowAction
    from luma_appkit.action_bubble import FloatingMenu

    def rows():
        return [RichMenuItem("Grid", icon="grid-3x3", toggle=True),
                RichMenuItem("Timer", icon="timer", value="3 s"),
                None,
                RichMenuItem("zsh", icon="terminal", subtitle="~/Projects/luma", selected=True, rename=True,
                             on_rename=lambda _n: None, trail=[RowAction("x", "Close session", lambda: None)]),
                RichMenuItem("Importing 12 photos", icon="download", busy=True),
                MenuSection(Gtk.Label(label="Drop photos here to import", xalign=0), label="Import")]

    button = Gtk.Button(label="Open the menu", halign=Gtk.Align.START)
    button.add_css_class("gallery-button")
    button.connect("clicked", lambda b: FloatingMenu(rows(), label="Camera settings").popup(b))
    demo.append(button)
    shown = FloatingMenu(rows(), label="Camera settings")  # the card as it opens, for measuring
    shown.add_css_class("shown")
    shown.set_halign(Gtk.Align.START)
    shown.set_size_request(300, -1)
    demo.append(shown)
    return page


def page_identity() -> Gtk.Widget:
    page, demo = _page("App icon, note card, lit glow", "An app as its icon (installed, a picture, or its monogram "
                       "in its category's hue); a note's mini card; a lit header from hues or the Luma tone.",
                       'AppIcon("org.projectluma.Notes", size=48); AppIcon(name="Kiln", size=56, category="create"); '
                       'NoteCard(title, preview, on_activate=fn); ContentLitHeader(hues=(330, 250)); '
                       'ContentLitHeader(tone="luma")')
    from luma_appkit import AppIcon, ContentLitHeader, NoteCard
    icons_row = Gtk.Box(spacing=14)
    for size, name, category in ((28, "Kiln", "create"), (40, "Ledger", "work"), (48, "Tide", "media"),
                                 (56, "Quest", "play"), (72, "Wrench", "tools"), (108, "Kiln", "create")):
        icons_row.append(AppIcon(name=name, size=size, category=category))
    icons_row.append(AppIcon("org.projectluma.Notes", size=56))
    demo.append(icons_row)
    note = NoteCard("Launch plan", "Freeze strings on the 10th, then the press kit.")
    note.set_size_request(360, -1)
    note.set_halign(Gtk.Align.START)
    demo.append(note)
    glows = Gtk.Box(spacing=14)
    for header in (ContentLitHeader(hues=(330, 250)), ContentLitHeader(tone="luma"), ContentLitHeader(tone="media")):
        frame = Gtk.Box(width_request=260, height_request=180, overflow=Gtk.Overflow.HIDDEN)
        frame.add_css_class("gallery-isle")
        header.set_vexpand(False)
        overlay = Gtk.Overlay(child=Gtk.Box(hexpand=True))
        overlay.add_overlay(header)
        frame.append(overlay)
        overlay.set_hexpand(True)
        glows.append(frame)
    demo.append(glows)
    return page


BUILDERS = {"rows-people": page_people, "rows-destinations": page_destinations, "rows-resources": page_resources,
            "rows-files": page_files, "rows-tree": page_tree, "rows-type": page_type_roles,
            "rows-progress": page_progress, "rows-stack": page_stack, "rows-favourites": page_favourites,
            "rows-mark": page_mark, "rows-menu": page_menu,
            "rows-identity": page_identity}


# ── conform surfaces ───────────────────────────────────────────────────────
#
# tools/lumaui-conform/scenarios/rows-*.json run this module (`bin/run --module
# pages_rows`) and compare one surface, alone in its window, with v70's own
# (the scenario's spec.window). LUMAUI_ROWS_SURFACE names the surface and
# LUMAUI_ROWS_PHOTOS the folder of v70's sample photos (tests/fixtures/contacts-v70).

import os  # noqa: E402

_CONFORM_CSS = """
/* The spec's window is the part's own box, without the margin it keeps from its neighbours. */
window.lumaui-rows-conform #rows-surface { margin: 0; }
window.lumaui-rows-conform { padding: 0; border-radius: 0; box-shadow: none;
  background-image: linear-gradient(to bottom, @luma_window_top, @luma_window_bottom); }
"""


def _photo(name: str) -> Gdk.Paintable | None:
    folder = os.environ.get("LUMAUI_ROWS_PHOTOS", "")
    path = os.path.join(folder, f"{name}.jpg")
    if not folder or not os.path.isfile(path):
        return None
    return Gdk.Texture.new_from_filename(path)


def surface_messages_side() -> Gtk.Widget:
    """v70 ?app=messages #m-side: the people variant with Messages' sample conversations."""
    sidebar = NavigationSidebar(variant="people")
    rows = [
        ("Launch crew", RowLead.group([("Launch crew", _photo("life-ari-street")), ("", _photo("life-sync-terrace"))]),
         "10:02 AM", "Sam: simplyluma.com is live on the new server btw", dict(pinned=True)),
        ("Priya Raman", RowLead.face("Priya Raman", picture=_photo("life-ari-street"), online=True), "8:43 AM",
         "Will do 👍", dict(pinned=True)),
        ("Dad", RowLead.face("Dad", picture=_photo("life-hero-home")), "SMS · 11:05 AM",
         "Saw the article about Luma. Proud of you kid. Call me this weekend", dict(attention=True, trail=1)),
        ("Theo Marsh", RowLead.face("Theo Marsh", picture=_photo("take-back")), "6:31 PM",
         "You: Haha yes. Saturday works", {}),
        ("Climbing Saturday", RowLead.group([("Climbing", _photo("take-back")), ("", _photo("life-suite-bg-canvas"))]),
         "4:02 PM", "Lena: Gym at 10, coffee after?", dict(muted=True)),
        ("Alex Ruiz", RowLead.face("Alex Ruiz", picture=_photo("life-professionals-loft")), "Monday",
         "Sent you the invoice, no rush", {}),
        ("Nora Feld", RowLead.face("Nora Feld", picture=_photo("life-sync-terrace"), online=True), "5:44 PM",
         "Anytime. It reads really well now", {}),
    ]
    first = None
    for title, lead, meta, subtitle, extra in rows:
        row = SidebarRow(title, lead=lead, meta=meta, subtitle=subtitle, **extra)
        sidebar.append_row(row)
        first = first or row
    sidebar.list.select_row(first)
    return sidebar


def surface_tasks_side() -> Gtk.Widget:
    """v70 ?app=tasks #tk-side: the destinations variant with Tasks' views and lists."""
    sidebar = NavigationSidebar(variant="destinations")
    today = SidebarRow("Today", lead=RowLead.icon("sun"), trail=4, attention=True)
    sidebar.append_row(today)
    for title, icon, count in (("Upcoming", "calendar", 6), ("Assigned to me", "user", 3), ("Flagged", "flag", 2),
                               ("Done", "check-check", 2)):
        sidebar.append_row(SidebarRow(title, lead=RowLead.icon(icon), trail=count))
    sidebar.append_section("Lists", action=("plus", "New list", lambda: None))
    for title, hue, count, faces in (("Personal", 250, 3, ()),
                                     ("Launch", 45, 6, ("life-ari-street", "life-sync-terrace", "life-suite-bg-write")),
                                     ("Home", 150, 2, ("life-professionals-loft",)),
                                     ("Groceries", 90, 3, ("life-professionals-loft",))):
        people = [(face, _photo(face)) for face in faces]
        sidebar.append_row(SidebarRow(title, lead=RowLead.dot(hue), trail=count, people=people or None))
    sidebar.list.select_row(today)
    return sidebar


def surface_monitor_side() -> Gtk.Widget:
    """v70 ?app=monitor #mn-side: the resources variant (its sparklines are DT1's, left out)."""
    sidebar = NavigationSidebar(variant="resources")
    sidebar.append_section("This computer")
    first = None
    for title, icon, value in (("CPU", "cpu", "54%"), ("Memory", "layers", "7.9 of 16 GB"),
                               ("Disk", "hard-drive", "182.5 MB/s"), ("Network", "wifi", "↓ 5.8 MB/s"),
                               ("Energy", "battery-medium", "74% · 4 h left")):
        row = SidebarRow(title, lead=RowLead.well(icon), subtitle=value)
        sidebar.append_row(row)
        first = first or row
    sidebar.list.select_row(first)
    return sidebar


def surface_phone_favourites() -> Gtk.Widget:
    """v70 ?app=phone #pn-side .pnfavs: Phone's favourites, four faces over their names."""
    strip = FavouritesStrip([Favourite("Priya", person="Priya Raman", picture=_photo("life-ari-street")),
                             Favourite("Nora", person="Nora Feld", picture=_photo("life-sync-terrace")),
                             Favourite("Dad", person="Dad", picture=_photo("life-hero-home")),
                             Favourite("Sam", person="Sam Kaur", picture=_photo("life-suite-bg-write"))])
    strip.set_hexpand(True)
    return strip


def surface_depot_icon() -> Gtk.Widget:
    """v70 ?app=depot: the Au monogram at the head of a stack (52, hue 260)."""
    from luma_appkit import AppIcon
    return AppIcon(name="Audacity", size=52, hue=260)


def surface_notes_side() -> Gtk.Widget:
    """v70 ?app=notes #n-side: the tree variant with Notes' pinned pages, folders and a shared page."""
    sidebar = NavigationSidebar(variant="tree")
    page = MarkValue("icon", "grey", "file-text")
    faces = {"launch": [("Priya Raman", _photo("life-ari-street")), ("Nora Feld", _photo("life-sync-terrace"))],
             "day": [("Priya Raman", _photo("life-ari-street"))], "groceries": [("Dad", _photo("life-hero-home"))],
             "gear": [("Theo Marsh", _photo("take-back"))]}
    sidebar.append_section("Pinned")
    pinned = SidebarRow("Launch checklist", mark=page, pinned=True, people=faces["launch"], drag="p:1",
                        on_drop=lambda *_a: None)
    pinned.add_css_class("lumaui-selected")  # the open page, shown where it is pinned too
    sidebar.append_row(pinned)
    sidebar.append_row(SidebarRow("Groceries", mark=page, pinned=True, people=faces["groceries"]))
    sidebar.append_section("Folders", action=("plus", "New folder", lambda: None))
    rows = [("Launch", 0, True, True, MarkValue(hue=285), 4, None),
            ("Press", 1, True, False, MarkValue(hue=285), 2, None),
            ("Launch checklist", 1, False, True, page, None, faces["launch"]),
            ("Day-of run sheet", 2, False, None, page, None, faces["day"]),
            ("Personal", 0, True, True, MarkValue(hue=150), 5, None),
            ("Recipes", 1, True, False, MarkValue(hue=45), 2, None),
            ("Groceries", 1, False, None, page, None, faces["groceries"]),
            ("Books to read", 1, False, None, page, None, None),
            ("Cabin Wi-Fi", 1, False, None, page, None, None),
            ("Ideas", 0, True, False, MarkValue(hue=200), 1, None)]
    chosen = None
    for title, depth, folder, expanded, mark, count, people in rows:
        row = SidebarRow(title, depth=depth, folder=folder, expanded=expanded, mark=mark, trail=count, people=people)
        sidebar.append_row(row)
        if title == "Launch checklist":
            chosen = row
    sidebar.append_section("Shared with me")
    sidebar.append_row(SidebarRow("Climbing gear", expanded=None, mark=page, people=faces["gear"]))
    sidebar.list.select_row(chosen)
    return sidebar


def surface_leaf_progress() -> Gtk.Widget:
    """v70 ?app=leaf .lfmeta .lfpr: the reading book's progress, 12%, on a tile (3 px, the quiet ink)."""
    line = ProgressLine(0.12, size="tile", tone="neutral", label="Read")
    line.set_valign(Gtk.Align.FILL)
    return line


def surface_camera_menu() -> Gtk.Widget:
    """v70 ?app=camera #cm-pop: Format, shape and settings, as the menu card opens (MN1 rows)."""
    from luma_appkit import RichMenuItem
    from luma_appkit.action_bubble import FloatingMenu
    menu = FloatingMenu([
        "Photo format",
        RichMenuItem("RAW + JPEG", subtitle="About 25 MB a photo", selected=True, checked=True),
        RichMenuItem("JPEG", subtitle="About 4 MB a photo"),
        RichMenuItem("HEIF", subtitle="About 2 MB a photo"),
        None,
        RichMenuItem("Manual controls", icon="aperture", subtitle="ISO, shutter, focus, white balance", toggle=False),
        RichMenuItem("Stabilisation", icon="vibrate", toggle=True),
        RichMenuItem("Mirror the front camera", icon="flip-horizontal-2", toggle=True),
        RichMenuItem("Save where photos are taken", icon="map-pin", toggle=False),
        None,
        RichMenuItem("Photo size", icon="image", value="12 MP"),
        RichMenuItem("Video", icon="video", value="4K · 30 fps"),
        RichMenuItem("Save to", icon="folder", value="Pictures › Camera"),
    ], label="Format, shape and settings")
    menu.add_css_class("shown")
    return menu


def surface_messages_drawer() -> Gtk.Widget:
    """v70 ?app=messages at phone width, conversations open (#m-side as .mwin.navopen's drawer).

    The card is SidebarToggle's own (box.lumaui-sidebar-drawer.full); the spec's window is the
    drawer, so the card fills this window rather than being presented over a phone window."""
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
    card.add_css_class("lumaui-sidebar-drawer")
    card.add_css_class("full")
    sidebar = surface_messages_side()
    sidebar.set_hexpand(True)
    card.append(sidebar)
    return card


def surface_notes_drawer() -> Gtk.Widget:
    """v70 ?app=notes at phone width, sidebar open (#n-side as .nwin.navopen's 264 places drawer)."""
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
    card.add_css_class("lumaui-sidebar-drawer")
    sidebar = surface_notes_side()
    sidebar.set_hexpand(True)
    sidebar.set_size_request(-1, -1)  # the drawer sets the width (SidebarToggle does the same)
    card.append(sidebar)
    return card


def _swiped_drawer(offset: float) -> Gtk.Widget:
    """v71 ?app=messages at phone width, the conversations open, Priya's row slid `offset` px (LSW Messages)."""
    from luma_appkit import SwipeAction, SwipeRow

    card = surface_messages_drawer()
    sidebar = card.get_first_child()
    row = sidebar.list.get_row_at_index(1)
    inner = row.get_child()
    row.set_child(None)
    swipe = SwipeRow(inner, start=SwipeAction("pin", "yellow", lambda _r: None, label="Pin"),
                     end=SwipeAction("bell-off", "blue", lambda _r: None, label="Mute"), phone_only=False)
    row.set_child(swipe)
    swipe.drag_to(offset, 0)
    return card


def surface_messages_swipe_pin() -> Gtk.Widget:
    """v71 LumaUI swipe rows: a conversation slid 60 px right shows Pin (yellow) on the left edge."""
    return _swiped_drawer(60)


def surface_messages_swipe_mute() -> Gtk.Widget:
    """v71 LumaUI swipe rows: slid 110 px left, past 90, Mute (blue) on the right edge, ready to commit."""
    return _swiped_drawer(-110)


def _az(touch: bool) -> Gtk.Widget:
    """v71 ?app=contacts at phone width: the A-Z index (lAZ) over Contacts' list, which has everyone but X."""
    from luma_appkit import AZIndex

    listbox = Gtk.ListBox()
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWYZ":
        row = Gtk.ListBoxRow(child=Gtk.Label(label=letter))
        row.letter = letter
        listbox.append(row)
    index = AZIndex(listbox, key=lambda r: r.letter, phone_only=False)
    index.list_holder = listbox   # kept alive; the index is the surface
    index.refresh()
    if touch:
        index.connect("map", lambda w: GLib.timeout_add(50, lambda: (w.jump_at(w.get_height() / 2), False)[1]))
    return index


def surface_contacts_az() -> Gtk.Widget:
    return _az(False)


def surface_contacts_az_touch() -> Gtk.Widget:
    """Touching the middle of the index: N, with the index's fill (the bubble sits outside its box)."""
    return _az(True)


def surface_messages_run() -> Gtk.Widget:
    """v71 ?app=messages, Priya's thread: her question and the event card under it are one run (mShape)."""
    from datetime import datetime

    from luma_appkit import MessageBubble, MessageRun
    from luma_appkit.content_contact import EventCard

    run = MessageRun(mine=False)
    run.append(MessageBubble("Are we still on for the walkthrough at 2?"))
    run.append(EventCard("Launch walkthrough", datetime(2026, 9, 25, 14, 0), where="Studio", hue=285,
                         on_add=lambda: None))
    return run


SURFACES = {"messages-run": surface_messages_run, "messages-swipe-pin": surface_messages_swipe_pin, "messages-swipe-mute": surface_messages_swipe_mute,
            "contacts-az": surface_contacts_az, "contacts-az-touch": surface_contacts_az_touch,
            "notes-drawer": surface_notes_drawer, "messages-drawer": surface_messages_drawer, "camera-menu": surface_camera_menu, "leaf-progress": surface_leaf_progress, "notes-side": surface_notes_side, "depot-icon": surface_depot_icon, "messages-side": surface_messages_side, "tasks-side": surface_tasks_side,
            "monitor-side": surface_monitor_side, "phone-favourites": surface_phone_favourites}


def main() -> int:
    """Show one conform surface alone in a window (see the block comment above)."""
    from luma_appkit import install_lumaui

    name = os.environ.get("LUMAUI_ROWS_SURFACE", "messages-side")
    app = Gtk.Application(application_id="org.projectluma.LumaUIRowsConform")

    def activate(application: Gtk.Application) -> None:
        window = Gtk.ApplicationWindow(application=application, title=name)
        window.add_css_class("lumaui-rows-conform")
        window.set_decorated(False)
        window.add_css_class("luma-no-native-surfaces")
        install_lumaui(window.get_display())
        provider = Gtk.CssProvider()
        provider.load_from_string(_CONFORM_CSS)
        Gtk.StyleContext.add_provider_for_display(window.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        surface = SURFACES[name]()
        surface.set_name("rows-surface")
        filling = isinstance(surface, (FavouritesStrip, ProgressLine)) or name in ("camera-menu", "messages-drawer", "notes-drawer",
                                                                                         "messages-swipe-pin", "messages-swipe-mute",
                                                                                         "contacts-az", "contacts-az-touch", "messages-run")
        surface.set_halign(Gtk.Align.FILL if filling else Gtk.Align.START)
        if name == "depot-icon":
            # v70 .dic has its own drop; the window is its box, so the icon stands on the list it sits in.
            window.remove_css_class("lumaui-rows-conform")
        surface.set_valign(Gtk.Align.FILL)
        window.set_child(surface)
        window.present()

    app.connect("activate", activate)
    return app.run([])


if __name__ == "__main__":
    raise SystemExit(main())
