# SPDX-License-Identifier: Apache-2.0
"""LumaUI Gallery: the November parts (Depot's redesign is their first user).

Buttons by purpose, the inset and two-column inset, the pop menu with checkbox
and radio rows, the checkbox, the confirmation in both forms, the tooltip, the
disclosure, the edge-fade scroller, permissions, the changelog, the framed
picture and the bar that grows.

    python3 lumaui_gallery.py --page parts-buttons [--appearance dark] [--phone]
"""
from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
if str(HERE.parents[1] / "appkit") not in sys.path:
    sys.path.insert(0, str(HERE.parents[1] / "appkit"))

from luma_appkit import (  # noqa: E402
    ActionCenter, BarAction, Button, ChangeLine, Changelog, Checkbox, CheckMenuItem, Confirm, Disclosure,
    EdgeFadeScroller, FramedPicture, IconOnlyButton, Inset, PermissionList, RadioMenuItem, Toast, ToastHost,
    Tooltip, TwoColumnInset, from_keys, from_reach, icons,
)
from luma_appkit.menus import bar_menu  # noqa: E402

FIXTURES = ROOT / "tests/fixtures/lumaui-parts"
#: Live handles for --demo states: page → callables.
_LIVE: dict[str, dict] = {}
WALLPAPER = ROOT / "assets/wallpapers/prism.png"
ICONS = ROOT / "assets/icon-theme/Prairie/scalable/apps"

PAGES = [
    ("parts-buttons", "Buttons by purpose", "square", "action", None),
    ("parts-inset", "Inset and two columns", "rows-3", "structure", None),
    ("parts-menu", "Pop menu: checkbox and radio rows", "list-filter", "action", None),
    ("parts-checkbox", "Checkbox", "square-check", "content", None),
    ("parts-confirm", "Confirmation, both forms", "circle-check", "action", None),
    ("parts-tooltip", "Tooltip for small targets", "message-square", "content", None),
    ("parts-disclosure", "Disclosure", "chevron-down", "structure", None),
    ("parts-edgefade", "Edge-fade scroller", "gallery-horizontal", "structure", None),
    ("parts-permissions", "Permissions", "shield", "content", None),
    ("parts-changelog", "Changelog", "list", "content", None),
    ("parts-framed", "Framed picture", "image", "media", None),
    ("parts-grow", "The bar grows", "panel-bottom", "action", None),
]


def _page(title: str, lede: str, api: str) -> tuple[Gtk.Box, Gtk.Box]:
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    page.add_css_class("gallery-page")
    heading = Gtk.Label(label=title, xalign=0)
    heading.add_css_class("title-1")
    page.append(heading)
    for text, css in ((lede, "gallery-lede"), (api, "gallery-code")):
        label = Gtk.Label(label=text, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        label.add_css_class(css)
        page.append(label)
    demo = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    demo.add_css_class("gallery-demo")
    page.append(demo)
    return page, demo


def _row(*children: Gtk.Widget, spacing: int = 8) -> Gtk.Box:
    box = Gtk.Box(spacing=spacing, valign=Gtk.Align.CENTER)
    for child in children:
        box.append(child)
    return box


def _icon(name: str, size: int = 46) -> Gtk.Widget:
    path = ICONS / f"{name}.svg"
    image = Gtk.Image.new_from_file(str(path)) if path.exists() else icons.image("package")
    image.set_pixel_size(size)
    return image


def _rank_row(rank: int | None, icon: str, name: str, line: str, action: str) -> Gtk.Widget:
    row = Gtk.Box(spacing=12)
    row.add_css_class("gallery-rank")
    if rank is not None:
        number = Gtk.Label(label=str(rank), width_chars=2)
        number.add_css_class("dim-label")
        row.append(number)
    row.append(_icon(icon))
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
    title = Gtk.Label(label=name, xalign=0)
    title.add_css_class("heading")
    sub = Gtk.Label(label=line, xalign=0)
    sub.add_css_class("dim-label")
    text.append(title)
    text.append(sub)
    row.append(text)
    row.append(Button(action, variant=Button.PLAIN if action == "Open" else Button.FILL, size=Button.SM))
    return row


# ── pages ──────────────────────────────────────────────────────────────────

def page_buttons() -> Gtk.Widget:
    page, demo = _page("Buttons by purpose",
                       "Key is the screen's one main action; Fill is a secondary action or Install in a row; Plain is "
                       "Open in a row; Danger only confirms an uninstall. Never a pill, never two keys side by side. "
                       "The icon goes first; a trailing chevron means it opens a menu.",
                       'Button("Install", variant=Button.FILL, size=Button.SM) · Button("All apps", menu=True)')
    for size in (Button.SM, Button.MD, Button.LG):
        demo.append(_row(Button("Open", variant=Button.KEY, size=size),
                         Button("Install on…", variant=Button.FILL, size=size, icon="monitor-smartphone"),
                         Button("Open", variant=Button.PLAIN, size=size),
                         Button("Uninstall", variant=Button.DANGER, size=size, icon="trash-2")))
    chosen = Button("Filters · 2", icon="list-filter", menu=True)
    chosen.set_chosen(True)
    demo.append(_row(Button("All apps", icon="layout-grid", menu=True), chosen,
                     Button("Update", variant=Button.KEY, icon="refresh-cw", tooltip="1.0.158 → 1.0.159 · 96 MB")))
    return page


def page_inset() -> Gtk.Widget:
    page, demo = _page("Inset and two columns",
                       "Lists sit in insets: recessed wells do the dividing, so rows carry no rules. A two-column inset "
                       "draws a hairline down the middle; one column on a phone, and the hairline goes.",
                       "Inset() · Inset(padding=\"card\") · TwoColumnInset().set_columns(1)")
    rows = [("blender", "Blender", "Create · 4.8 ★", "Install"), ("obsidian", "Obsidian", "Work · 4.7 ★", "Install"),
            ("spotify", "Spotify", "Media · 4.4 ★", "Open"), ("signal", "Signal", "Work · 4.6 ★", "Install")]
    two = TwoColumnInset()
    for index, (icon, name, line, action) in enumerate(rows, 1):
        two.append(_rank_row(index, icon, name, line, action))
    demo.append(two)
    one = Inset()
    for icon, name, line, action in rows[:2]:
        one.append(_rank_row(None, icon, name, line, action))
    demo.append(one)
    return page


def page_menu() -> Gtk.Widget:
    page, demo = _page("Pop menu: checkbox and radio rows",
                       "Ticking a checkbox row updates its box, the button and the page in place and the menu stays "
                       "open; there is no Clear row, you untick. A radio row moves the check in place, then closes.",
                       'bar_menu(button, ["Also works on", CheckMenuItem("Phone", icon="smartphone", checked=True, '
                       'on_toggle=fn), None, "Made by", …])')
    state = {"works": {"phone"}, "seals": set(), "cat": "All apps"}
    filters = Button("Filters · 1", icon="list-filter", menu=True)
    filters.set_chosen(True)
    category = Button("All apps", icon="layout-grid", menu=True)

    def label() -> None:
        count = len(state["works"]) + len(state["seals"])
        filters.set_label_text(f"Filters · {count}" if count else "Filters")
        filters.set_chosen(bool(count))

    def tick(group: str, key: str):
        def toggled(on: bool) -> None:
            (state[group].add if on else state[group].discard)(key)
            label()
        return toggled

    def open_filters() -> None:
        forms = [("desktop", "Desktop", "monitor"), ("tablet", "Tablet", "tablet"), ("phone", "Phone", "smartphone"),
                 ("tv", "TV", "tv"), ("home", "Home", "house")]
        seals = [("luma", "Luma"), ("verified", "Verified developers"), ("community", "Community")]
        bar_menu(filters, ["Also works on"]
                 + [CheckMenuItem(name, icon=glyph, checked=key in state["works"], on_toggle=tick("works", key))
                    for key, name, glyph in forms]
                 + [None, "Made by"]
                 + [CheckMenuItem(name, checked=key in state["seals"], on_toggle=tick("seals", key)) for key, name in seals],
                 label="Filters")

    def pick(name: str):
        return lambda: (state.update(cat=name), category.set_label_text(name))

    def open_categories() -> None:
        bar_menu(category, ["Category"] + [RadioMenuItem(name, group="category", checked=state["cat"] == name,
                                                         on_activate=pick(name))
                                           for name in ("All apps", "Create", "Work", "Media", "Play", "Tools")],
                 label="Category")

    filters.connect("clicked", lambda _b: open_filters())
    category.connect("clicked", lambda _b: open_categories())
    _LIVE["menu"] = {"filters": open_filters, "category": open_categories}
    demo.append(_row(category, filters))
    return page


def page_checkbox() -> Gtk.Widget:
    page, demo = _page("Checkbox", "16 square, 5 round, in a well; on, the accent with a white check. In a menu or a "
                       "device row the row is the control; alone, it takes clicks, Space and Enter.",
                       "Checkbox(checked=True) · Checkbox(label=\"Keep matched\", on_toggle=fn)")
    demo.append(_row(Checkbox(checked=False), Checkbox(checked=True),
                     Checkbox(checked=False, label="Standalone", on_toggle=lambda _on: None),
                     Checkbox(checked=True, sensitive=False)))
    return page


def page_confirm() -> Gtk.Widget:
    page, demo = _page("Confirmation, both forms",
                       "An icon chip, the question, one sentence, Cancel and the action. Destructive: red action and "
                       "icon. Not destructive (switching a release channel): the key and a neutral icon.",
                       'Confirm.ask(w, title="Join Beta?", body=…, action="Join Beta", icon="git-branch", destructive=False)')
    join = Button("Join Beta…", icon="git-branch")
    join.connect("clicked", lambda b: Confirm.ask(
        b, title="Join Beta?", icon="git-branch", action="Join Beta", destructive=False,
        body="This computer will get Beta builds about once a week, before they reach Stable.",
        on_confirm=lambda _c: Toast.show(b, "You joined Beta", kind="done")))
    leave = Button("Switch to Stable…", icon="git-branch")
    leave.connect("clicked", lambda b: Confirm.ask(
        b, title="Leave Nightly?", icon="git-branch", action="Switch to Stable", destructive=False,
        body="This computer stays on nightly October 22 until Stable catches up, then gets Stable releases only.",
        on_confirm=lambda _c: Toast.show(b, "This computer now gets Stable updates", kind="done")))
    _LIVE["confirm"] = {"key": lambda: join.emit("clicked")}
    remove = Button("Delete…", icon="trash-2")
    remove.connect("clicked", lambda b: Confirm.ask(b, title="Delete this conversation?", action="Delete",
                                                    body="It's removed from this computer. Others keep their copy."))
    demo.append(_row(join, leave, remove))
    return page


def page_tooltip() -> Gtk.Widget:
    page, demo = _page("Tooltip for small targets",
                       "Each device icon is its own 24 px target; hovering brightens it and names it in a small light "
                       "label above. Icon-only buttons get one too. It never inherits a dimmed parent's opacity.",
                       'Tooltip.attach(icon, "TV")')
    runs = Gtk.Box(spacing=0)
    runs.add_css_class("gallery-runs")
    for glyph, name in (("monitor", "Desktop"), ("tablet", "Tablet"), ("smartphone", "Phone"), ("tv", "TV"),
                        ("house", "Home")):
        target = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, focusable=True)
        target.add_css_class("gallery-runs-target")
        image = icons.image(glyph)
        image.set_hexpand(True)
        target.append(image)
        tip = Tooltip.attach(target, name)
        if name == "TV":
            _LIVE["tooltip"] = {"tv": tip._show}
        runs.append(target)
    dim = Gtk.Box(opacity=0.45)
    share = IconOnlyButton("share-2", "Share")
    Tooltip.attach(share, "Share")
    dim.append(share)
    demo.append(_row(runs, dim))
    return page


def page_disclosure() -> Gtk.Widget:
    page, demo = _page("Disclosure",
                       "A summary with a chevron that turns when open. The line form is quiet and opens into an inset "
                       "card (Technical info); the card form is an inset with a header row (Automatic updates).",
                       'Disclosure("Technical info", content=w) · Disclosure("Automatic updates", icon=…, form="card")')
    facts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    for key, value in (("Format", "Luma app"), ("Version", "1.0.158"), ("Licence", "GPL-3.0")):
        line = Gtk.Box()
        line.append(Gtk.Label(label=key, xalign=0, hexpand=True))
        line.append(Gtk.Label(label=value))
        facts.append(line)
    demo.append(Disclosure("Technical info", content=facts, expanded=True))
    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    rows.append(Gtk.Label(label="Update apps overnight", xalign=0))
    demo.append(Disclosure("Automatic updates", icon="settings-2", content=rows, form="card"))
    return page


def page_edgefade() -> Gtk.Widget:
    page, demo = _page("Edge-fade scroller",
                       "A sideways strip fades at an edge only while there is more past it, easing over 0.25 s; it "
                       "re-measures when images load. Arrows over the fades; none on a phone, you swipe.",
                       "EdgeFadeScroller(cards, fade=56, arrows=True)")
    cards = Gtk.Box(spacing=10)
    for index in range(8):
        card = Inset(padding="card")
        card.set_size_request(236, 150)
        card.append(Gtk.Label(label=f"Review {index + 1}", xalign=0))
        cards.append(card)
    demo.append(EdgeFadeScroller(cards, fade=56, arrows=True))
    return page


def page_permissions() -> Gtk.Widget:
    page, demo = _page("Permissions",
                       "Eleven words in a fixed order, one glyph each, the app's reason underneath. Publishers' lines "
                       "map to them (folders become Files, microphone and camera become two). The installer shares it.",
                       "PermissionList(from_reach(app.reach)) · PermissionList(from_keys(grants))")
    grants = from_reach([
        ("mic", "Microphone and camera", "Only during a call you start or answer"),
        ("music-2", "Your Music folder", "Reads it to build your library"),
        ("globe", "The internet", "Plays from your own music server"),
        ("bell", "Notifications", "Says what's playing next"),
        ("volume-2", "Sound", "Plays music"),
    ])
    card = Inset(padding="card")
    card.append(PermissionList(grants))
    demo.append(card)
    changes = Inset(padding="card")
    changes.append(PermissionList(from_keys([type("G", (), {"key": "network", "detail": "Syncs your library",
                                                             "change": "added"})()])))
    demo.append(changes)
    return page


def page_changelog() -> Gtk.Widget:
    page, demo = _page("Changelog",
                       "Plain text lines read as one bulleted list. A line may lead with a bold title. Typed lines group "
                       "under New, Better, Fixed, Security. No pills; lines wrap at about 68 characters.",
                       "Changelog([\"Faster search\", ChangeLine(text, title=…, kind=\"Feature\")])")
    demo.append(Changelog(["Gapless playback between tracks on the same album",
                           "The mini player remembers where it was"]))
    demo.append(Changelog([
        ChangeLine("Set how fast mice and touchpads scroll, in Settings › Mouse and touchpad.",
                   title="Adjustable scroll speed", kind="Feature"),
        ChangeLine("Windows open where you last closed them", kind="Improvement"),
        ChangeLine("The dock no longer flickers on wake", kind="Fix"),
        ChangeLine("Updated the image decoders", kind="Security"),
    ]))
    return page


def page_framed() -> Gtk.Widget:
    page, demo = _page("Framed picture",
                       "Flat screenshots, always shown in a Luma setting. Desktop: the window at 64% on the blurred "
                       "wallpaper with the dock. Phone: Luma's reference phone upright on a blurred real place.",
                       'FramedPicture.desktop(shot, backdrop=wall, dock=icons, running=2) · FramedPicture.phone(shot, backdrop=room)')
    dock = [str(ICONS / f"org.projectluma.{name}.svg") for name in ("Viewer", "Maps", "Tide", "Photos", "Messages",
                                                                     "Calendar", "Notes")]
    desktop = FramedPicture.desktop(str(FIXTURES / "tide-desktop-dark.webp"), backdrop=str(WALLPAPER), dock=dock,
                                    running=2, label="Tide on the desktop")
    desktop.set_size_request(560, -1)
    phone = FramedPicture.phone(str(FIXTURES / "tide-phone-dark.webp"), backdrop=str(FIXTURES / "room-terrace.webp"),
                                label="Tide on a phone")
    phone.set_size_request(560, -1)
    thumbs = Gtk.Box(spacing=10)
    for maker in (lambda: FramedPicture.desktop(str(FIXTURES / "tide-desktop-dark.webp"), backdrop=str(WALLPAPER),
                                                dock=dock, running=2, radius=10),
                  lambda: FramedPicture.phone(str(FIXTURES / "tide-phone-dark.webp"),
                                              backdrop=str(FIXTURES / "room-terrace.webp"), radius=10)):
        thumb = maker()
        thumb.set_size_request(96, 54)
        thumbs.append(thumb)
    demo.append(desktop)
    demo.append(phone)
    demo.append(thumbs)
    return page


def page_grow() -> Gtk.Widget:
    page, demo = _page("The bar grows",
                       "Install on… and Uninstall grow the bar upward into a panel; the buttons stay under it. Escape, "
                       "× or the same button folds it. No sheets, no dialogs.",
                       'center.grow("where", panel, anchor=button) · center.fold()')
    host = ToastHost()
    host.set_size_request(-1, 420)
    stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
    host.set_child(stage)
    center = ActionCenter()
    center.attach(host)

    def where_panel() -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for name, note, on in (("ThinkPad X1", "Tablet", True), ("Living room", "TV · off, installs when it wakes", False)):
            row = Gtk.Box(spacing=10)
            row.append(Checkbox(checked=on))
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            text.append(Gtk.Label(label=name, xalign=0))
            sub = Gtk.Label(label=note, xalign=0)
            sub.add_css_class("dim-label")
            text.append(sub)
            row.append(text)
            panel.append(row)
        panel.append(Button("Install on 1 device", variant=Button.KEY, size=Button.LG))
        return panel

    _LIVE["grow"] = {"where": lambda: center.grow("where", where_panel)}
    center.show_bar([BarAction(None, "Open", primary=True),
                     BarAction("monitor-smartphone", "Install on…", on_activate=lambda: center.grow("where", where_panel)),
                     BarAction("share-2", tooltip="Share"),
                     BarAction("trash-2", tooltip="Uninstall")])
    demo.append(host)
    return page


BUILDERS = {
    "parts-buttons": page_buttons, "parts-inset": page_inset, "parts-menu": page_menu,
    "parts-checkbox": page_checkbox, "parts-confirm": page_confirm, "parts-tooltip": page_tooltip,
    "parts-disclosure": page_disclosure, "parts-edgefade": page_edgefade, "parts-permissions": page_permissions,
    "parts-changelog": page_changelog, "parts-framed": page_framed, "parts-grow": page_grow,
}


#: --demo states: name → callable(gallery).
DEMOS = {
    "parts-filters": lambda g: _LIVE["menu"]["filters"](),
    "parts-category": lambda g: _LIVE["menu"]["category"](),
    "parts-confirm-key": lambda g: _LIVE["confirm"]["key"](),
    "parts-tooltip": lambda g: _LIVE["tooltip"]["tv"](),
    "parts-grow": lambda g: _LIVE["grow"]["where"](),
}
