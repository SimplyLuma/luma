#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""FloatingMenu: opened at a point (a context menu), flipped at the window's edges, and cascading
submenus by keyboard and by hover (with the grace triangle), drilled in place on a phone."""
import time

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

Gtk.init()

from luma_appkit import install_appkit  # noqa: E402
from luma_appkit.action_bubble import SUBMENU_GRACE_MS, FloatingMenu, MenuItem  # noqa: E402
from luma_appkit.structure_layers import LayerHost  # noqa: E402

install_appkit()


def settle(seconds: float = 0.3) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.005)


def bounds(widget: Gtk.Widget, host: Gtk.Widget):
    ok, rect = widget.compute_bounds(host)
    assert ok
    return rect.get_x(), rect.get_y(), rect.get_width(), rect.get_height()


def controller(widget: Gtk.Widget, kind: type):
    model = widget.observe_controllers()
    for i in range(model.get_n_items()):
        found = model.get_item(i)
        if isinstance(found, kind):
            return found
    raise AssertionError(f"no {kind.__name__} on {widget}")


def key(menu: FloatingMenu, keyval: int) -> bool:
    return menu._keys.emit("key-pressed", keyval, 0, 0)


def labelled(menu: FloatingMenu, words: str) -> Gtk.Button:
    for button in menu.buttons:
        line = button.get_child()
        child = line.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Label) and child.get_label() == words:
                return button
            child = child.get_next_sibling()
    raise AssertionError(f"no row {words!r}")


canvas = Gtk.Box(hexpand=True, vexpand=True)  # a big anchor, as an ArrangeView
host = LayerHost(canvas, name="window")
window = Gtk.Window(default_width=800, default_height=600)
window.set_child(host)
window.present()
settle()
W, H = host.get_width(), host.get_height()
rows = ["Order", MenuItem("Bring to front", shortcut="Ctrl+Shift+]"), MenuItem("Send to back"), None,
        MenuItem("Size", icon="ratio", submenu=[MenuItem("Fit"), MenuItem("Fill"), MenuItem("Stretch")]),
        MenuItem("Delete", danger=True)]

# A window whose host is made by the first menu (no size yet) still gets the whole card, unclipped.
fresh = Gtk.Window(default_width=700, default_height=500)
fresh_body = Gtk.Box(hexpand=True, vexpand=True)
fresh.set_child(fresh_body)
fresh.present()
settle()
menu = FloatingMenu(rows).popup(fresh_body, x=200, y=60)
settle()
assert menu._scroll is None, "a short menu doesn't scroll (it was wrapped to 96 px when the host had no size)"
menu.close()
fresh.destroy()

# At a point: the menu's corner on the click, not at the big anchor's edge.
menu = FloatingMenu(rows).popup(canvas, x=120, y=90)
settle()
x, y, w, h = bounds(menu, host)
assert (round(x), round(y)) == (120, 90), (x, y)
menu.close()
assert not menu.is_open and menu.get_parent() is None
# Near the right edge it opens to the left of the point; near the bottom, above it; never moved away.
menu = FloatingMenu(rows).popup(canvas, x=W - 20, y=90)
settle()
x, y, w, h = bounds(menu, host)
assert round(x + w) == W - 20 and round(y) == 90, (x, y, w, h, W)
menu.close()
menu = FloatingMenu(rows).popup(canvas, x=W - 20, y=H - 10)
settle()
x, y, w, h = bounds(menu, host)
assert round(x + w) == W - 20 and round(y + h) == H - 10 and not menu.has_css_class("below"), (x, y, w, h)
menu.close()
# A point in a corner where neither side fits keeps the card inside the window.
tall = FloatingMenu([MenuItem(f"Row {i}") for i in range(12)]).popup(canvas, x=200, y=H / 2)
settle()
x, y, w, h = bounds(tall, host)
assert y >= 8 and y + h <= H - 8 and round(x) == 200, (x, y, w, h, H)
tall.close()
# Without a point it still opens from its anchor (the Menu key, Shift+F10).
button = Gtk.Button(label="More", halign=Gtk.Align.START, valign=Gtk.Align.START)
canvas.append(button)
settle()
menu = FloatingMenu(rows).popup(button)
settle()
assert menu.has_css_class("below") and bounds(menu, host)[1] >= bounds(button, host)[1] + bounds(button, host)[3]
menu.close()
for bad in (lambda: FloatingMenu(rows).popup(canvas, x=3),
            lambda: MenuItem("Size", submenu=[], on_activate=lambda: None)):
    try:
        bad()
    except ValueError:
        pass
    else:
        raise AssertionError("refused")
canvas.remove(button)

# A leaf's shortcut shows as keycaps; a submenu row ends in a chevron.
menu = FloatingMenu(rows).popup(canvas, x=120, y=90)
settle()
front = labelled(menu, "Bring to front")
caps = [c.get_label() for c in [front.get_child().get_last_child().get_first_child()]]
assert front.get_child().get_last_child().has_css_class("lumaui-menu-shortcut") and caps[0] in ("Ctrl", "⌘")
size = labelled(menu, "Size")
assert size.has_css_class("submenu")

# Keyboard: → opens the submenu beside the menu and focuses its first row; ← and Esc close one level.
size.grab_focus()
assert key(menu, Gdk.KEY_Right)
settle()
child = menu.submenu
assert child is not None and child.is_open and child.parent_menu is menu
assert window.get_focus() is child.buttons[0]
mx, my, mw, mh = bounds(menu, host)
cx, cy, cw, ch = bounds(child, host)
assert cx >= mx + mw and abs(cy - bounds(size, host)[1]) <= 8, (mx, mw, cx, cy)
assert key(menu, Gdk.KEY_Down) and window.get_focus() is child.buttons[1]
assert key(menu, Gdk.KEY_Left)
assert menu.submenu is None and not child.is_open and window.get_focus() is size and menu.is_open
assert key(menu, Gdk.KEY_Right) and menu.submenu is not None
assert key(menu, Gdk.KEY_Escape)
assert menu.submenu is None and menu.is_open
assert key(menu, Gdk.KEY_Escape) and not menu.is_open

# A leaf in a submenu runs its action and closes every level.
picked = []
rows2 = [MenuItem("Size", submenu=[MenuItem("Fit", on_activate=lambda: picked.append("fit"))]), MenuItem("Delete")]
menu = FloatingMenu(rows2).popup(canvas, x=120, y=90)
settle()
labelled(menu, "Size").emit("clicked")
settle()
menu.submenu.buttons[0].emit("clicked")
assert picked == ["fit"] and not menu.is_open and menu.submenu is None

# Hover: onto a submenu row opens it; onto another row closes it, unless the pointer is heading for it.
menu = FloatingMenu(rows).popup(canvas, x=120, y=90)
settle()
size, delete = labelled(menu, "Size"), labelled(menu, "Delete")
controller(size, Gtk.EventControllerMotion).emit("enter", 10, 10)
settle(0.05)
assert menu.submenu is not None
cx, cy, cw, ch = bounds(menu.submenu, host)
sx, sy, sw, sh = bounds(size, host)
dx, dy, dw, dh = bounds(delete, host)
# Moving right and down from the Size row towards the submenu's near edge, crossing Delete.
menu._pointer = [(sx + sw - 40, sy + sh / 2), (sx + sw - 20, dy + 2)]
controller(delete, Gtk.EventControllerMotion).emit("enter", 10, 2)
settle(SUBMENU_GRACE_MS / 2000)
assert menu.submenu is not None, "the grace triangle keeps the submenu while the pointer heads for it"
settle(SUBMENU_GRACE_MS / 1000 + 0.1)
assert menu.submenu is None, "and closes it once the pointer stays on another row"
controller(size, Gtk.EventControllerMotion).emit("enter", 10, 10)
settle(0.05)
assert menu.submenu is not None
# Moving away (left), onto Delete: it closes at once.
menu._pointer = [(sx + 40, sy + sh / 2), (sx + 20, dy + 2)]
controller(delete, Gtk.EventControllerMotion).emit("enter", 10, 2)
settle(0.02)
assert menu.submenu is None
menu.close()

# A submenu near the right edge opens on the menu's other side.
menu = FloatingMenu(rows).popup(canvas, x=W - 20, y=90)
settle()
menu.open_submenu(labelled(menu, "Size"))
settle()
mx, my, mw, mh = bounds(menu, host)
cx, cy, cw, ch = bounds(menu.submenu, host)
assert cx + cw <= mx and menu.submenu.has_css_class("flipped"), (mx, cx, cw)
menu.close()
assert menu.submenu is None
window.destroy()

# A phone: the menu rises in the bar's frame and a submenu drills in place, with Back.
phone_body = Gtk.Box(hexpand=True, vexpand=True)
phone = Gtk.Window(default_width=390, default_height=780)
phone.set_child(phone_body)
phone.present()
settle()
menu = FloatingMenu(rows, title="Widget").popup(phone_body, x=100, y=200)
settle()
frame = menu._drawer
assert frame is not None
page = frame.scroller.get_child()
if isinstance(page, Gtk.Viewport):
    page = page.get_child()


def row_named(root, words):
    if getattr(root, "label", None) == words and isinstance(root, Gtk.Button):
        return root
    child = root.get_first_child()
    while child is not None:
        found = row_named(child, words)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


first = row_named(page, "Size")
assert first is not None, page
first.emit("clicked")
settle()
drilled = frame.scroller.get_child()
drilled = drilled.get_child() if isinstance(drilled, Gtk.Viewport) else drilled
assert drilled is not page and row_named(drilled, "Back") is not None and row_named(drilled, "Fit") is not None
row_named(drilled, "Back").emit("clicked")
settle()
shown = frame.scroller.get_child()
assert (shown.get_child() if isinstance(shown, Gtk.Viewport) else shown) is page
menu.close()
phone.destroy()
print("ok")
