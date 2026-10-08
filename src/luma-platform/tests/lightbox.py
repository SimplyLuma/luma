#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""The kit lightbox: geometry, and a real window opening, stepping, zooming and closing it.

Pictures are generated here; nothing is read from a user's files.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gio, GdkPixbuf, GLib, Gtk  # noqa: E402

from luma_appkit import Lightbox, LightboxItem, install_appkit  # noqa: E402
from luma_appkit.lightbox import clamp_pan, fit_rect, neighbours, zoom_about  # noqa: E402

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def settle(seconds: float = 0.2) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.005)


def wait(predicate, what: str, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            failures.append("timed out: " + what)
            return
        settle(0.02)


# ── Pure geometry ──
check(fit_rect(4000, 3000, 800, 600) == (0, 0, 800, 600), "a large picture fits the area")
check(fit_rect(400, 300, 800, 600) == (200, 150, 400, 300), "a small picture is not enlarged past 1:1")
check(fit_rect(1000, 2000, 800, 600) == (250, 0, 300, 600), "a portrait picture is centred")
zoom, pan = zoom_about(1.0, (0.0, 0.0), 2.0, (500.0, 300.0), (400.0, 300.0))
check(zoom == 2.0 and pan == (-100.0, 0.0), f"zooming keeps the point under the pointer: {zoom} {pan}")
check(zoom_about(7.0, (0, 0), 4.0, (0, 0), (0, 0))[0] == 8.0, "zoom is capped")
check(clamp_pan((500.0, -500.0), (1600.0, 1200.0), (800.0, 600.0)) == (400.0, -300.0), "pan is clamped to the picture")
check(clamp_pan((50.0, 50.0), (400.0, 300.0), (800.0, 600.0)) == (0.0, 0.0), "a picture smaller than the area does not pan")
check(neighbours(0, 5) == {0, 1} and neighbours(2, 5) == {1, 2, 3} and neighbours(4, 5) == {3, 4}, "neighbours")

# ── A real window ──
app = Adw.Application(application_id="org.projectluma.LightboxTest", flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
install_appkit()
root = Path(tempfile.mkdtemp(prefix="luma-lightbox-"))


def picture(name: str, width: int, height: int, colour: int) -> Path:
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    pixbuf.fill(colour)
    path = root / name
    pixbuf.savev(str(path), "png", [], [])
    return path


paths = [picture("a.png", 1600, 1200, 0x4878b8ff), picture("b.png", 900, 1600, 0xc0803aff),
         picture("c.png", 1200, 1200, 0x3a944aff), picture("d.png", 2000, 1000, 0x7a52b8ff)]
gif = root / "dot.gif"
# Two 4x4 frames, red then blue, 100 ms each, looping (made with ImageMagick).
gif.write_bytes(bytes.fromhex(
    "47494638396104000400f00000ff000000000021ff0b4e45545343415045322e30030100000021f904000a0000002c00"
    "00000004000400000204848f09050021f904000a0000002c0000000004000400800000ff0000000204848f0905003b"))

window = Adw.ApplicationWindow(application=app, default_width=900, default_height=640)
content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
thumbs = []
for index, path in enumerate(paths):
    button = Gtk.Button(label=f"Picture {index}")
    content.append(button)
    thumbs.append(button)
outside = Gtk.Button(label="Something else")
content.append(outside)
window.set_content(content)
window.present()
settle(0.4)

lightbox = Lightbox.for_window(window)
check(Lightbox.for_window(window) is lightbox, "one lightbox per window")
check(isinstance(window.get_content(), Gtk.Overlay) and window.get_content().get_child() is content,
      "the lightbox lies over the window's own content, not a new window")
items = [LightboxItem(path, "image/png", name=path.name, title="Alex", subtitle="Yesterday 9:41 PM", source=thumbs[i])
         for i, path in enumerate(paths)]
items.append(LightboxItem(None, "image/heic", name="IMG_0001.heic", title="Alex", state="unavailable",
                          message="Google Messages no longer has this photo.", retry=lambda: retried.append(True)))
retried: list[bool] = []
closed: list[bool] = []
lightbox.connect("closed", lambda *_: closed.append(True))
thumbs[1].grab_focus()
lightbox.open(items, 1)
settle(0.1)
check(len(Gtk.Window.list_toplevels()) == 1, "no second window opened")
check(lightbox.get_visible() and lightbox.is_open, "open")
check(not content.get_can_target() and not content.get_can_focus(), "the content behind is inert while open")
check(lightbox.close_button.has_focus(), "focus moves into the lightbox")
wait(lambda: lightbox.current_paintable() is not None and lightbox.progress == 1.0, "the picture decodes and grows")
check(lightbox.natural_size() == (900, 1600), f"natural size {lightbox.natural_size()}")
wait(lambda: lightbox.decoded_indices() == {0, 1, 2}, "the neighbours decode")
texture = lightbox.current_paintable()
area = lightbox.area()
check(texture.get_width() <= area[2] * max(1, window.get_scale_factor()) + 1 and
      texture.get_height() <= area[3] * max(1, window.get_scale_factor()) + 1,
      f"decoded at display size: {texture.get_width()}x{texture.get_height()} for {area}")
check(lightbox.previous_button.get_visible() and lightbox.next_button.get_visible(), "both chevrons in the middle")
check("2 of 5" in lightbox.subtitle_label.get_label(), lightbox.subtitle_label.get_label())
check(lightbox.previous_button.get_icon_name() == "pan-start-symbolic" and
      lightbox.next_button.get_icon_name() == "pan-end-symbolic", "Prairie pan glyphs")


def key(keyval):
    controller = next(c for c in lightbox.observe_controllers() if isinstance(c, Gtk.EventControllerKey))
    return controller.emit("key-pressed", keyval, 0, 0)


from gi.repository import Gdk  # noqa: E402
key(Gdk.KEY_Right)
settle(0.05)
check(lightbox.index == 2, "Right steps forward")
wait(lambda: lightbox.decoded_indices() <= {1, 2, 3} and 3 in lightbox.decoded_indices(), "only ±1 keep pixels")
check(0 not in lightbox.decoded_indices(), "a picture two steps away gives its pixels back")
key(Gdk.KEY_Left)
check(lightbox.index == 1, "Left steps back")

# Zoom: double-click toggles, zoom keeps the picture on screen, Escape unzooms before closing.
wait(lambda: lightbox.current_paintable() is not None, "back to picture 1")
rect = lightbox.picture_rect()
lightbox.toggle_actual_size((rect[0] + rect[2] / 2, rect[1] + rect[3] / 2))
check(lightbox.zoom >= 2.0, f"double-click zooms in: {lightbox.zoom}")
wait(lambda: (1, True) in lightbox._textures, "zooming decodes the shown picture at full size")
lightbox.set_zoom(100.0)
check(lightbox.zoom == 8.0, "zoom is capped at 8x")
key(Gdk.KEY_Right)
check(lightbox.index == 1, "arrow keys pan rather than step while zoomed")
key(Gdk.KEY_Escape)
check(lightbox.zoom == 1.0 and lightbox.is_open, "Escape first returns to the fitted picture")

# The last item is unavailable: its state, and Try Again.
lightbox.step(3)
settle(0.05)
check(lightbox.index == 4 and lightbox.status.get_visible() and "no longer has" in lightbox.status_label.get_label(),
      "an unavailable picture says why")
check(not lightbox.next_button.get_visible() and not lightbox.save_button.get_sensitive(), "no next, no Save As")
lightbox.retry_button.emit("clicked")
check(retried == [True], "Try Again asks the owner")
check(not lightbox.step(1), "no step past the end")

# Close with Escape: pixels released, focus back on the thumbnail it was opened from.
key(Gdk.KEY_Escape)
wait(lambda: not lightbox.get_visible(), "closed")
check(closed == [True], "closed is emitted once")
check(lightbox.decoded_indices() == set() and lightbox.current_paintable() is None, "pixels released on close")
check(content.get_can_target() and content.get_can_focus(), "the content behind works again")
check(thumbs[1].has_focus(), "focus returns to the thumbnail")

# An animated GIF plays; reduced motion opens and closes at once.
Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
gif_item = LightboxItem(gif, "image/gif", name="dot.gif", source=thumbs[0])
lightbox.open([gif_item], 0)
check(lightbox.progress == 1.0, "reduced motion: no zoom animation")
wait(lambda: lightbox._frames is not None and lightbox.current_paintable() is not None, "the GIF animates")
first = lightbox.current_paintable()
wait(lambda: lightbox.current_paintable() is not first, "the GIF advances frames", timeout=2)
check(not lightbox.previous_button.get_visible() and not lightbox.next_button.get_visible(), "one picture, no chevrons")
# A click on the backdrop, outside the picture, closes.
click = next(c for c in lightbox.canvas.observe_controllers() if isinstance(c, Gtk.GestureClick))
click.emit("released", 1, 2.0, 2.0)
check(not lightbox.get_visible() and lightbox._frame_source == 0, "a backdrop click closes and stops the GIF")

# Narrow: controls still fit.
window.set_default_size(360, 700)
settle(0.3)
lightbox.open(items[:2], 0)
settle(0.3)
check(lightbox.bar.get_width() <= window.get_width(), "the bar fits a phone-width window")
lightbox.close()

window.close()
settle(0.1)
if failures:
    raise SystemExit("FAIL: " + "; ".join(failures))
print("ok: lightbox geometry, open, step, zoom, unavailable state, GIF, reduced motion, close and focus return")
