#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""ActionEditor's first field and empty sections, SyncKey and OverrideDot, and contained row thumbnails."""
import time

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, GObject, Graphene, Gtk  # noqa: E402

Gtk.init()

from luma_appkit import (ActionEditor, BarAction, DetailsPane, OverrideDot, RowLead, SyncKey,  # noqa: E402
                         ChatFeed, ChatLine, Sparkline, ToggleChip, install_appkit, rows_tokens)

install_appkit()


def settle(seconds: float = 0.2) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.005)


# ActionEditor: the first field takes focus with its text selected; a hidden body takes no room.
name = Gtk.Entry(text="Camera")
body = Gtk.Box(visible=False)
editor = ActionEditor("Rename", "pencil", fields=[("Name", name), ("Note", "Optional")], body=body,
                      primary=BarAction("check", label="Save"))
window = Gtk.Window(child=editor)
window.present()
settle()
assert editor.has_css_class("bodyless") and not editor.scroller.get_visible()
assert editor.hint.get_label() == "Esc to cancel · Ctrl Return", editor.hint.get_label()
editor.focus_content()
settle()
assert window.get_focus() is not None and window.get_focus().is_ancestor(name) or window.get_focus() is name
bounds = name.get_selection_bounds()  # (start, end) when something is selected
assert tuple(bounds)[-2:] == (0, 6), bounds
body.set_visible(True)
assert not editor.has_css_class("bodyless") and editor.scroller.get_visible()
window.destroy()

# SyncKey: synced (a button that unsyncs), own (Sync with …), and the other side's information.
calls = []
key = SyncKey("Landscape", "Vertical", on_unsync=lambda: calls.append("unsync"), on_sync=lambda: calls.append("sync"))
assert key.words.get_label() == "In sync with Landscape" and key.has_css_class("synced")
assert key.row.get_can_target() and not key.action.get_visible()
key.row.emit("clicked")
assert calls == ["unsync"]
key.set_synced(False, changes=3)
assert key.words.get_label() == "Vertical has its own look" and key.action.get_visible()
assert not key.row.get_can_target()
key.row.emit("clicked")
key.action.emit("clicked")
assert calls == ["unsync", "sync"], calls
info = SyncKey("Landscape", "Vertical", primary=True)
assert info.words.get_label() == "Vertical follows this look" and not info.action.get_visible()
info.set_synced(False, changes=1)
assert info.words.get_label() == "Vertical has its own look (1 change)"
info.set_synced(False, changes=3)
assert info.words.get_label() == "Vertical has its own look (3 changes)"
dot = OverrideDot(tooltip="Differs from Landscape")
assert not dot.has_css_class("active")
dot.set_active(True)
assert dot.has_css_class("active") and dot.get_tooltip_text() == "Differs from Landscape"

# The media tile (fit="contain"): one fixed 16:9 tile whatever the picture, its size, or the row's height.
class Frames(GObject.Object, Gdk.Paintable):
    """A source whose frames change size after the first (a camera settling, a 9:16 scene arriving)."""

    def __init__(self, width: int, height: int) -> None:
        super().__init__()
        self.width, self.height = width, height

    def do_get_intrinsic_width(self) -> int:
        return self.width

    def do_get_intrinsic_height(self) -> int:
        return self.height

    def do_get_intrinsic_aspect_ratio(self) -> float:
        return self.width / self.height

    def do_snapshot(self, snapshot, width, height) -> None:
        colour = Gdk.RGBA()
        colour.parse("#3070c0")
        snapshot.append_color(colour, Graphene.Rect().init(0, 0, width, height))

    def resize(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.invalidate_size()
        self.invalidate_contents()


tile_w, tile_h = int(rows_tokens.value("nav_files", "media_thumb_width")), int(rows_tokens.value("nav_files", "media_thumb_height"))
source = Frames(1920, 1080)
leads = {"wide": RowLead.thumbnail(source, fit="contain"),
         "tall": RowLead.thumbnail(Gdk.Paintable.new_empty(1080, 1920), fit="contain"),
         "forced": RowLead.thumbnail(Gdk.Paintable.new_empty(10, 10), fit="contain", aspect=9 / 16),
         "empty": RowLead.thumbnail(None, kind="image", fit="contain", aspect=16 / 9)}
column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
for lead in leads.values():
    row = Gtk.Box(vexpand=True)  # a tall row: the tile still centres at its own height
    row.append(lead)
    column.append(row)
window = Gtk.Window(child=column, default_width=320, default_height=640)
window.present()
settle(0.4)


def sizes() -> dict:
    return {key: (lead.get_width(), lead.get_height()) for key, lead in leads.items()}


assert set(sizes().values()) == {(tile_w, tile_h)}, sizes()
for lead in leads.values():
    assert lead.has_css_class("contain") and lead.get_valign() == Gtk.Align.CENTER and not lead.get_vexpand()
    assert lead.get_parent().get_height() > 2 * tile_h  # the row is tall; the tile is not
    assert lead.measure(Gtk.Orientation.VERTICAL, -1)[:2] == (tile_h, tile_h)
assert leads["wide"].picture_rect(tile_w, tile_h) == (0.0, 0.0, tile_w, tile_h)  # 16:9 fills, no margin
x, y, w, h = leads["tall"].picture_rect(tile_w, tile_h)
assert h == tile_h and abs(w - tile_h * 9 / 16) < 1e-6 and abs(x - (tile_w - w) / 2) < 1e-6 and y == 0
assert abs(leads["forced"].picture_rect(tile_w, tile_h)[2] - tile_h * 9 / 16) < 1e-6
assert leads["empty"].glyph.get_visible() and leads["empty"].picture_rect(tile_w, tile_h) is None
assert not leads["wide"].glyph.get_visible()
# Frames of another size, and then another shape, arrive: the tile keeps its size and contains them.
source.resize(3840, 2160)
settle(0.2)
source.resize(1080, 1920)
settle(0.2)
assert sizes()["wide"] == (tile_w, tile_h), sizes()
assert leads["wide"].picture_rect(tile_w, tile_h)[2] < tile_w
leads["empty"].set_paintable(Frames(1920, 1080))
settle(0.2)
assert sizes()["empty"] == (tile_w, tile_h) and not leads["empty"].glyph.get_visible()
window.destroy()
assert RowLead.thumbnail(Gdk.Paintable.new_empty(160, 90)).get_center_widget().get_content_fit() == \
    Gtk.ContentFit.COVER
for bad in (lambda: RowLead.thumbnail(None, fit="fill"), lambda: RowLead.thumbnail(None, fit="contain", aspect=0)):
    try:
        bad()
    except ValueError:
        pass
    else:
        raise AssertionError("refused")
# ValueSlider Mixed: the readout says so until one value is applied by a move, a reset or set_value.
from luma_appkit import ValueSlider  # noqa: E402

moved = []
look = ValueSlider("Opacity", 100, 0, 100, unit="%", default=100, on_change=moved.append)
look.set_mixed(40)
assert look.mixed and look.has_css_class("mixed") and look.value_label.get_label() == "Mixed" and look.value == 40
assert not look.has_css_class("set") and moved == []
look.range.emit("value-changed", 55.0)
assert not look.mixed and not look.has_css_class("mixed") and moved == [55.0]
look.set_mixed()
assert look.value == 100 and look.mixed
look.reset()
assert not look.mixed and moved[-1] == 100, moved
look.set_mixed()
look.set_value(20)
assert not look.mixed and look.value_label.get_label() != "Mixed"
# ToggleChip: its own toggle; a refusal keeps it; the live dot; set_active is the app's state.
asked = []
refuse = {"value": False}
chip = ToggleChip("Vertical", active=True, on_toggle=lambda on: (asked.append(on), not refuse["value"])[1])
other = ToggleChip("Landscape", active=True)
assert chip.get_active() and other.get_active() and not chip.live_dot.get_visible()
refuse["value"] = True
chip.set_active(True)
chip.emit("clicked")
assert chip.get_active() and asked == [False], "refused: the chip stays on"
refuse["value"] = False
chip.emit("clicked")
assert not chip.get_active() and asked == [False, False]
chip.set_active(True)
assert chip.get_active() and asked == [False, False], "the app's own state calls nothing"
chip.set_live(True)
assert chip.live and chip.live_dot.get_visible() and chip.has_css_class("live")

# DetailsPane(raised=True): the sheet carries .raised (a raised island beside the main one).
pane = DetailsPane("Inspector", main=True, raised=True)
assert pane.sheet.has_css_class("raised")
pane.set_raised(False)
assert not pane.sheet.has_css_class("raised")
# Sparkline: a rolling window, tones, marks, a description, and it draws.
spark = Sparkline([1, 2, 3], capacity=4, label="Bitrate")
for v in (4, 5):
    spark.push(v)
assert spark.values == [2.0, 3.0, 4.0, 5.0]
spark.mark()
spark.set_tone("danger")
assert spark.has_css_class("danger") and not spark.has_css_class("accent")
for bad in (lambda: spark.set_tone("red"), lambda: Sparkline(capacity=1)):
    try:
        bad()
    except ValueError:
        pass
    else:
        raise AssertionError("refused")
holder = Gtk.Window(default_width=360, default_height=60)
holder.set_child(spark)
holder.present()
settle()
assert spark.get_height() == 24 and spark.get_width() > 100
paint = Gtk.WidgetPaintable.new(spark)
snap = Gtk.Snapshot()
paint.snapshot(snap, spark.get_width(), spark.get_height())
assert snap.to_node() is not None, "it draws"
holder.destroy()
# ChatLine: readable author colours, badges, kinds; ChatFeed keeps the newest and follows the bottom.
from luma_appkit.content_chat import _contrast, readable_color  # noqa: E402

for color in ("#ffff00", "#000080", "", "#9146FF"):
    for dark in (False, True):
        hexed = readable_color(color, "lumafan", dark=dark)
        rgb = tuple(int(hexed[i:i + 2], 16) / 255 for i in (1, 3, 5))
        surface = (0.118, 0.129, 0.141) if dark else (1.0, 1.0, 1.0)
        assert _contrast(rgb, surface) >= 4.4, (color, dark, hexed)
assert readable_color("", "a") == readable_color("", "a") != readable_color("", "b")
line = ChatLine("lumafan", "hi <there>", color="#9146FF", badges=("moderator", "nobody", "vip"),
                lead=Gtk.Image(icon_name="image-missing", pixel_size=16))
badges = [c for c in [line.get_first_child()] + [] if c is not None]
glyphs, child = [], line.get_first_child()
while child is not None:
    if child.has_css_class("lumaui-chat-badge"):
        glyphs.append(child.get_tooltip_text())
    child = child.get_next_sibling()
assert glyphs == ["Moderator", "VIP"], glyphs
assert "hi &lt;there&gt;" in line.label.get_label() and line.label.get_selectable()
assert "<i>" in ChatLine("a", "waves", kind="action").label.get_label()
notice = ChatLine("", "lumafan raided", kind="notice")
assert notice.label.get_xalign() == 0.5 and notice.label.get_label() == "lumafan raided"
line.set_dimmed(True)
assert line.has_css_class("dimmed")
try:
    ChatLine("a", "b", kind="shout")
except ValueError:
    pass
else:
    raise AssertionError("refused")
feed = ChatFeed(max_lines=5)
chat = Gtk.Window(default_width=360, default_height=90)
chat.set_child(feed)
chat.present()
for i in range(12):
    feed.append(ChatLine(f"user{i}", f"message {i} " * 4))
settle()
assert feed.lines == 5 and feed.list.get_row_at_index(0).get_child().author == "user7"
adj = feed.scroller.get_vadjustment()
assert adj.get_upper() > adj.get_page_size(), (adj.get_upper(), adj.get_page_size())
assert adj.get_value() >= adj.get_upper() - adj.get_page_size() - 4, "follows the newest"
settle()
adj.set_value(0)  # a person scrolls up: the key appears, and new lines don't pull the view down
assert feed.jump.get_visible()
feed.append(ChatLine("late", "one more"))
settle()
assert adj.get_value() == 0 and feed.jump.get_visible()
feed.jump.emit("clicked")
settle()
assert not feed.jump.get_visible()
chat.destroy()
print("ok")
