# SPDX-License-Identifier: Apache-2.0
"""lumaui-conform's in-process GTK capture (a dev tool; never shipped).

Loaded by the sitecustomize.py next to it when LUMAUI_CONFORM_DUMP=<dir> is
set. It runs inside the real app, started from a scratch copy of the LumaUI
preview in a headless compositor, and:

1. pins the appearance in an in-memory GSettings backend (surface treatment,
   colour scheme, font), so the user's own settings never leak in;
2. sizes the first application window to LUMAUI_CONFORM_SIZE (WxH);
3. performs LUMAUI_CONFORM_ACTIONS ([{"activate": "<widget name>"}, ...]);
4. dumps the widget tree (bounds relative to the window, CSS name, classes,
   widget name, accessible role, label text, font, colour, icon) and every
   paint operation in the window's render node (fills, gradients, borders,
   radii, shadows, text runs) to <dir>/gtk-<state>.json;
5. renders the window, with any open popover composited in place, to
   <dir>/gtk-<state>.png at scale 1, then quits the app.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback

OUT = os.environ.get("LUMAUI_CONFORM_DUMP", "")
STATE = os.environ.get("LUMAUI_CONFORM_STATE", "card")
SIZE = os.environ.get("LUMAUI_CONFORM_SIZE", "1180x740")
THEME = os.environ.get("LUMAUI_CONFORM_THEME", "dark")
FONT = os.environ.get("LUMAUI_CONFORM_FONT", "Figtree 11")
ACTIONS = json.loads(os.environ.get("LUMAUI_CONFORM_ACTIONS", "[]") or "[]")
# A state may paint another treatment than the theme's own (Frost over dark or light).
TREATMENT = os.environ.get("LUMAUI_CONFORM_TREATMENT", "") or THEME
# A maximized state lets the compositor size the window (the monitor is the spec's desk).
MAXIMIZE = any(a.get("maximize") for a in ACTIONS)
SETTLE_MS = int(os.environ.get("LUMAUI_CONFORM_SETTLE_MS", "1500"))
PT_TO_PX = 96.0 / 72.0


def _pin_settings() -> None:
    from gi.repository import Gio

    source = Gio.SettingsSchemaSource.get_default()

    def put(schema_id: str, key: str, value: str) -> None:
        schema = source and source.lookup(schema_id, True)
        if schema is not None and schema.has_key(key):
            Gio.Settings.new(schema_id).set_string(key, value)

    put("org.project_luma.shell-state", "surface-treatment", TREATMENT)
    put("org.gnome.desktop.interface", "color-scheme", "prefer-dark" if THEME == "dark" else "prefer-light")
    put("org.gnome.desktop.interface", "font-name", FONT)
    # Luma's icon theme, as on the Luma machine: the window's identity shows the app's own icon.
    put("org.gnome.desktop.interface", "icon-theme", os.environ.get("LUMAUI_CONFORM_ICON_THEME", "Prairie"))
    # Static captures: no animation caught mid-way (the overlay scroll indicator's fade was).
    schema = source and source.lookup("org.gnome.desktop.interface", True)
    if schema is not None and schema.has_key("enable-animations"):
        Gio.Settings.new("org.gnome.desktop.interface").set_boolean("enable-animations", False)


def install() -> None:
    if not OUT:
        return
    os.makedirs(OUT, exist_ok=True)
    try:
        _pin_settings()
    except Exception:  # noqa: BLE001 - a missing schema is not fatal
        traceback.print_exc()
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gsk", "4.0")
    from gi.repository import GLib

    _add_icon_dirs()
    _record_accessible_labels()
    Capture(GLib).start()


def _record_accessible_labels() -> None:
    """GTK has no getter for an accessible label: remember the ones Python sets, so a
    control is named as the spec's aria-label names it."""
    from gi.repository import Gtk

    original = Gtk.Accessible.update_property

    def update_property(self, properties, values):
        try:
            for prop, value in zip(properties, values):
                if prop == Gtk.AccessibleProperty.LABEL and isinstance(value, str):
                    self._conform_label = value
        except Exception:  # noqa: BLE001 - recording never breaks the app
            pass
        return original(self, properties, values)
    Gtk.Accessible.update_property = update_property


def _add_icon_dirs() -> None:
    """LUMAUI_CONFORM_ICON_DIRS: folders of loose app icons (the scenario's fixtures),
    searched as the Luma machine's installed icon theme would be, so a window's
    identity shows the app's own icon in the capture too."""
    dirs = [d for d in os.environ.get("LUMAUI_CONFORM_ICON_DIRS", "").split(os.pathsep) if d]
    if not dirs:
        return
    from gi.repository import Gdk, Gtk

    def opened(_manager, display) -> None:
        theme = Gtk.IconTheme.get_for_display(display)
        for d in dirs:
            theme.add_search_path(d)

    manager = Gdk.DisplayManager.get()
    manager.connect("display-opened", opened)
    if manager.get_default_display() is not None:
        opened(manager, manager.get_default_display())


def _tight_path_bounds(path):
    """A path's ink geometry without its stroke. gsk_path_get_bounds includes curve control
    points (an arc drawn as cubics grows by ~10%), so evaluate each segment of the path's own
    serialization instead. (PathMeasure.get_point can't be called from PyGObject: its point is
    caller-allocated.)"""
    from gi.repository import Graphene

    nums = re.findall(r"[MLHVCQOZmlhvcqoz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", path.to_string())
    xs, ys = [], []
    cur = start = (0.0, 0.0)
    i, cmd, rel = 0, None, False
    arity = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "Q": 4, "O": 5, "Z": 0}
    while i < len(nums):
        if nums[i].isalpha():
            cmd, rel = nums[i].upper(), nums[i].islower()
            i += 1
            if cmd == "Z":
                cur = start
                continue
        if cmd is None or cmd == "Z" or i + arity[cmd] > len(nums):
            break
        v = [float(x) for x in nums[i:i + arity[cmd]]]
        i += arity[cmd]
        # Absolute points: a relative command's pairs are offsets from the segment's start
        # (a conic's weight is not a coordinate); H/V become lines.
        if cmd == "H":
            cmd_now, v = "L", [v[0] + (cur[0] if rel else 0), cur[1]]
        elif cmd == "V":
            cmd_now, v = "L", [cur[0], v[0] + (cur[1] if rel else 0)]
        else:
            cmd_now = cmd
            if rel:
                pairs = 2 if cmd == "O" else len(v) // 2
                for k in range(pairs):
                    v[2 * k] += cur[0]; v[2 * k + 1] += cur[1]
        if cmd_now == "M":
            cur = start = (v[0], v[1]); xs.append(v[0]); ys.append(v[1])
            cmd = "L"  # further pairs after M are lines
            continue
        steps = 64
        for k in range(1, steps + 1):
            t = k / steps; u = 1 - t
            if cmd_now == "L":
                x = u * cur[0] + t * v[0]; y = u * cur[1] + t * v[1]
            elif cmd_now == "C":
                x = u**3 * cur[0] + 3 * u * u * t * v[0] + 3 * u * t * t * v[2] + t**3 * v[4]
                y = u**3 * cur[1] + 3 * u * u * t * v[1] + 3 * u * t * t * v[3] + t**3 * v[5]
            elif cmd_now == "Q":
                x = u * u * cur[0] + 2 * u * t * v[0] + t * t * v[2]
                y = u * u * cur[1] + 2 * u * t * v[1] + t * t * v[3]
            else:  # conic: control, end, weight
                w = v[4]; den = u * u + 2 * u * t * w + t * t
                x = (u * u * cur[0] + 2 * u * t * w * v[0] + t * t * v[2]) / den
                y = (u * u * cur[1] + 2 * u * t * w * v[1] + t * t * v[3]) / den
            xs.append(x); ys.append(y)
        cur = (v[2], v[3]) if cmd_now == "O" else (v[-2], v[-1])
    if not xs:
        ok, rect = path.get_bounds()
        return rect
    return Graphene.Rect().init(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def _rgba(c) -> list[float] | None:
    if c is None:
        return None
    return [round(c.red * 255, 1), round(c.green * 255, 1), round(c.blue * 255, 1), round(c.alpha, 3)]


def _repeating_gradient(node):
    """Read stops from GSK's own serialization when GI omits the repeat-node getter.

    Calling LinearGradientNode.get_color_stops(node) looks plausible, but GI rejects
    a RepeatingLinearGradientNode before the C accessor can inspect it. GSK's
    serialization preserves the stop offsets, colours and repeat geometry.
    """
    from gi.repository import Gdk

    data = node.serialize().get_data().decode("utf-8")
    match = re.search(r"^\s*stops:\s*(.+);\s*$", data, re.MULTILINE)
    if match is None:
        raise ValueError("repeating gradient serialization has no stops")
    value = match.group(1)
    stop_pattern = re.compile(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s+((?:rgba?|color)\([^)]*\)|#[0-9a-fA-F]+)\s*(?:,|$)")
    stops = []
    position = 0
    while position < len(value):
        stop = stop_pattern.match(value, position)
        if stop is None:
            raise ValueError(f"unrecognized repeating gradient stop: {value[position:]!r}")
        color = Gdk.RGBA()
        if not color.parse(stop.group(2)):
            raise ValueError(f"unrecognized repeating gradient colour: {stop.group(2)!r}")
        stops.append([round(float(stop.group(1)), 3), _rgba(color)])
        position = stop.end()
    if not stops:
        raise ValueError("repeating gradient serialization has empty stops")

    def coordinates(name):
        field = re.search(rf"^\s*{name}:\s*([^;]+);\s*$", data, re.MULTILINE)
        if field is None:
            return None
        try:
            return [float(part) for part in field.group(1).split()]
        except ValueError:
            return None

    start, end = coordinates("start"), coordinates("end")
    period = [round(b - a, 3) for a, b in zip(start, end)] if start and end and len(start) == len(end) else None
    return stops, period


def _isect(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    if x1 - x0 < 0.5 or y1 - y0 < 0.5:
        return None
    return [round(x0, 2), round(y0, 2), round(x1 - x0, 2), round(y1 - y0, 2)]


def _box(r) -> list[float]:
    return [round(r.origin.x, 2), round(r.origin.y, 2), round(r.size.width, 2), round(r.size.height, 2)]


def _font(desc) -> dict:
    size = desc.get_size() / 1024.0
    px = size if desc.get_size_is_absolute() else size * PT_TO_PX
    return {"family": desc.get_family(), "size": round(px, 2), "weight": int(desc.get_weight()),
            "style": desc.get_style().value_nick, "describe": desc.to_string()}


class Capture:
    def __init__(self, GLib) -> None:
        self.GLib = GLib
        self.t0 = time.monotonic()
        self.window = None
        self.sized_at = None
        self.acted_at = None
        self.width, self.height = (int(v) for v in SIZE.lower().split("x"))
        self.notes: list[str] = []

    def start(self) -> None:
        self.GLib.timeout_add(150, self.tick)

    # ── the settle loop ────────────────────────────────────────────────
    def tick(self) -> bool:
        from gi.repository import Gtk

        try:
            elapsed = time.monotonic() - self.t0
            if elapsed > 45:
                self.notes.append("timed out waiting for the window")
                self.finish()
                return False
            if self.window is None:
                wins = [w for w in Gtk.Window.list_toplevels() if w.get_mapped() and w.get_child() is not None]
                if not wins:
                    return True
                self.window = wins[0]
                if MAXIMIZE:
                    self.window.maximize()
                elif self.window.is_maximized():
                    self.window.unmaximize()
                if not MAXIMIZE:
                    self.window.set_default_size(self.width, self.height)
                self.sized_since = time.monotonic()
                return True
            w = self.window
            if (w.get_width(), w.get_height()) != (self.width, self.height):
                if time.monotonic() - self.sized_since < 6:
                    if not MAXIMIZE:
                        w.set_default_size(self.width, self.height)
                    return True
                self.notes.append(f"window is {w.get_width()}x{w.get_height()}, asked for {self.width}x{self.height}")
            if self.sized_at is None:
                self.sized_at = time.monotonic()
                return True
            if time.monotonic() - self.sized_at < SETTLE_MS / 1000:
                return True
            if self.acted_at is None:
                self.act()
                self.acted_at = time.monotonic()
                return True
            if ACTIONS and time.monotonic() - self.acted_at < 1.2:
                return True
            self.finish()
            return False
        except Exception:  # noqa: BLE001
            self.notes.append(traceback.format_exc())
            self.finish()
            return False

    def find(self, root, name):
        # "css:button.close" names the first widget with that CSS name and classes
        # (for parts the toolkit builds and never names, such as GTK's own window controls).
        if name.startswith("css:"):
            node, *classes = name[4:].split(".")
            if root.get_css_name() == node and all(root.has_css_class(c) for c in classes) and root.get_mapped():
                return root
        elif root.get_name() == name:
            return root
        child = root.get_first_child()
        while child is not None:
            hit = self.find(child, name)
            if hit is not None:
                return hit
            child = child.get_next_sibling()
        return None

    STATE_FLAGS = {"prelight": "PRELIGHT", "active": "ACTIVE", "focus-visible": "FOCUS_VISIBLE", "checked": "CHECKED"}

    def act(self) -> None:
        from gi.repository import Gtk

        for action in ACTIONS:
            # Pointer and focus states the headless compositor has no pointer for: the
            # state flags GTK itself would set (hover = PRELIGHT, pressed = ACTIVE).
            if "flags" in action:
                target = self.find(self.window, action.get("widget", ""))
                if target is None:
                    self.notes.append(f"no widget named {action.get('widget')}")
                    continue
                flags = Gtk.StateFlags(0)
                for flag in action["flags"].split("|"):
                    flags |= getattr(Gtk.StateFlags, self.STATE_FLAGS[flag])
                target.set_state_flags(flags, False)
                continue
            if action.get("backdrop"):
                # An inactive window: GTK sets BACKDROP on the toplevel and it propagates.
                self.window.set_state_flags(Gtk.StateFlags.BACKDROP, False)
                continue
            if "focus" in action:
                target = self.find(self.window, action["focus"])
                if target is None:
                    self.notes.append(f"no widget named {action['focus']}")
                    continue
                self.window.set_focus_visible(True)
                target.grab_focus()
                continue
            if action.get("maximize"):
                if not self.window.is_maximized():
                    self.notes.append("the window did not maximize")
                continue
            if "activate" in action:
                target = self.find(self.window, action["activate"])
                if target is None:
                    self.notes.append(f"no widget named {action['activate']}")
                    continue
                from gi.repository import Gtk

                if isinstance(target, Gtk.Button):
                    target.emit("clicked")
                elif not target.activate():
                    self.notes.append(f"{action['activate']} did not activate")

    # ── the dump ───────────────────────────────────────────────────────
    def finish(self) -> None:
        try:
            self.dump()
        except Exception:  # noqa: BLE001
            with open(os.path.join(OUT, f"gtk-{STATE}.error.txt"), "w") as f:
                f.write(traceback.format_exc())
        finally:
            self.quit()

    def quit(self) -> None:
        from gi.repository import Gtk

        app = self.window.get_application() if self.window is not None else None
        if app is not None:
            app.quit()
        self.GLib.timeout_add(1500, lambda: os._exit(0))

    def popovers(self, widget, out):
        from gi.repository import Gtk

        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Popover):
                if child.get_mapped():
                    out.append(child)
            elif child.get_mapped():
                self.popovers(child, out)
            child = child.get_next_sibling()
        return out

    def native_offset(self, native):
        """Where a popover's widget origin sits in window coordinates."""
        wx, wy = self.window.get_surface_transform()
        px, py = native.get_surface_transform()
        surface = native.get_surface()
        return surface.get_position_x() + px - wx, surface.get_position_y() + py - wy

    def dump(self) -> None:
        import gi
        from gi.repository import Graphene, Gtk

        win = self.window
        W, H = win.get_width(), win.get_height()
        widgets: list[dict] = []
        self.walk(win, win, None, widgets, (0.0, 0.0), 1.0)
        pops = self.popovers(win, [])
        pop_offsets = []
        for pop in pops:
            ox, oy = self.native_offset(pop)
            pop_offsets.append((pop, ox, oy))
            self.walk(pop, pop, None, widgets, (ox, oy), 1.0)

        snapshot = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(win).snapshot(snapshot, W, H)
        for pop, ox, oy in pop_offsets:
            snapshot.save()
            snapshot.translate(Graphene.Point().init(ox, oy))
            Gtk.WidgetPaintable.new(pop).snapshot(snapshot, pop.get_width(), pop.get_height())
            snapshot.restore()
        node = snapshot.to_node()
        ops: list[dict] = []
        if node is not None:
            self.paint(node, lambda r: r, 1.0, None, ops)
            texture = win.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, W, H))
            texture.save_to_png(os.path.join(OUT, f"gtk-{STATE}.png"))
        self.attach_text(widgets, ops)
        try:
            from luma_appkit.lumaui import overflowing
            overflow = overflowing(win)
        except Exception:  # an app without the kit, or an older kit
            overflow = []
        doc = {
            "state": STATE, "theme": THEME, "window": {"w": W, "h": H, "asked": [self.width, self.height]},
            "scale": win.get_scale_factor(), "gtk": [Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version()],
            "font_setting": Gtk.Settings.get_default().props.gtk_font_name, "notes": self.notes,
            "widgets": widgets, "ops": ops, "overflow": overflow,
        }
        with open(os.path.join(OUT, f"gtk-{STATE}.json"), "w") as f:
            json.dump(doc, f)

    def walk(self, widget, root, parent, out, offset, opacity, clip=None) -> None:
        from gi.repository import Gtk

        if not widget.get_mapped() or widget.get_opacity() <= 0.01:
            return
        if isinstance(widget, Gtk.Popover) and widget is not root:
            return
        ok, bounds = widget.compute_bounds(root)
        if not ok:
            return
        op = opacity * widget.get_opacity()
        x, y = bounds.origin.x + offset[0], bounds.origin.y + offset[1]
        rec = {
            "id": len(out), "parent": parent, "type": widget.__gtype__.name, "css": widget.get_css_name(),
            "classes": list(widget.get_css_classes()), "name": widget.get_name(),
            "role": widget.get_accessible_role().value_nick,
            "box": [round(x, 2), round(y, 2), round(bounds.size.width, 2), round(bounds.size.height, 2)],
            "opacity": round(op, 3), "sensitive": widget.is_sensitive(),
        }
        box = rec["box"]
        if clip is None:
            clip = [0.0, 0.0, float(self.window.get_width()), float(self.window.get_height())] if root is self.window else list(box)
        vis = _isect(box, clip)
        if vis is None:
            rec["offscreen"] = True
        elif vis != box:
            rec["visible"] = vis
        if widget.get_overflow() == Gtk.Overflow.HIDDEN or isinstance(widget, (Gtk.Viewport, Gtk.ScrolledWindow)):
            clip = vis or [box[0], box[1], 0, 0]
        try:
            rec["color"] = _rgba(widget.get_color())
        except Exception:  # noqa: BLE001 - GTK < 4.10
            pass
        tip = widget.get_tooltip_text()
        if tip:
            rec["tooltip"] = tip
        label = getattr(widget, "_conform_label", None)
        if label:
            rec["a11y_label"] = label
        try:
            rec["font"] = _font(widget.get_pango_context().get_font_description())
            # A label's own size attribute (PersonAvatar's initials: round(size x .36)) is what it draws.
            attrs = widget.get_attributes() if isinstance(widget, Gtk.Label) else None
            for attr in (attrs.get_attributes() if attrs is not None else []):
                sized = attr.as_size() if hasattr(attr, "as_size") else None
                if sized is not None and attr.start_index == 0:
                    px = sized.size / 1024.0
                    rec["font"]["size"] = round(px if sized.absolute else px * PT_TO_PX, 2)
        except Exception:  # noqa: BLE001
            pass
        if isinstance(widget, Gtk.Label):
            layout = widget.get_layout()
            ink, logical = layout.get_pixel_extents()
            rec.update(text=widget.get_text(), xalign=widget.get_xalign(), wrap=widget.get_wrap(),
                       ellipsize=widget.get_ellipsize().value_nick, lines=layout.get_line_count(),
                       layout=[logical.x, logical.y, logical.width, logical.height],
                       ellipsized=layout.is_ellipsized())
            spacing = self.letter_spacing(widget)
            if spacing is not None:
                rec["letter_spacing"] = spacing
        elif isinstance(widget, Gtk.Text):
            rec["text"] = widget.get_text()
            rec["placeholder"] = widget.get_placeholder_text()
            parent_widget = widget.get_parent()
            if parent_widget is not None and parent_widget.has_css_class("lumaui-bar-search"):
                rec["role"] = "searchbox"
        elif isinstance(widget, Gtk.Image):
            rec["icon"] = widget.get_icon_name()
            gicon = widget.get_gicon()
            if not rec["icon"] and gicon is not None and hasattr(gicon, "get_names"):
                rec["icon"] = gicon.get_names()[0]
            rec["pixel_size"] = widget.get_pixel_size()
            rec["storage"] = widget.get_storage_type().value_nick
        elif isinstance(widget, Gtk.Switch):
            rec["active"] = widget.get_active()
        elif isinstance(widget, Gtk.Picture):
            rec["picture"] = True
            # An app's icon drawn as a picture (C LumaApplicationIcon: a normalized .svg icon) is an
            # app icon, as a kit AppIcon is (Settings' Apps pages).
            paintable = widget.get_paintable()
            if paintable is not None and type(paintable).__name__ in ("ApplicationIcon", "LumaApplicationIcon"):
                rec["paintable"] = "LumaApplicationIcon"
                rec.setdefault("icon", "application-icon")
        # A kit part that draws an app's icon itself (ID1 AppIcon) names it by app_id.
        if "icon" not in rec and isinstance(getattr(widget, "app_id", None), str) and widget.get_css_name() != "window":
            rec["icon"] = widget.app_id
        if isinstance(widget, Gtk.ListBoxRow):
            rec["activatable"] = widget.get_activatable()
        if isinstance(widget, (Gtk.Button, Gtk.MenuButton)):
            label = widget.get_label() if hasattr(widget, "get_label") else None
            if label:
                rec["button_label"] = label
            icon = widget.get_icon_name() if hasattr(widget, "get_icon_name") else None
            if icon:
                rec["icon"] = icon
        if isinstance(widget, Gtk.Editable) and not isinstance(widget, Gtk.Text):
            rec["text"] = widget.get_text()
        out.append(rec)
        if isinstance(widget, Gtk.TextView):
            self.text_view_lines(widget, root, rec, out)
        me = rec["id"]
        child = widget.get_first_child()
        while child is not None:
            self.walk(child, root, me, out, offset, op, clip)
            child = child.get_next_sibling()

    def text_view_lines(self, view, root, rec, out) -> None:
        """An editable document's paragraphs as text records (v70's contenteditable paragraphs):
        one per buffer line, boxed by its laid-out lines."""
        from gi.repository import Gtk

        buffer = view.get_buffer()
        x0, y0 = rec["box"][0], rec["box"][1]
        for n in range(buffer.get_line_count()):
            ok, start = buffer.get_iter_at_line(n)
            if not ok:
                continue
            end = start.copy()
            if not end.ends_line():
                end.forward_to_line_end()
            text = buffer.get_text(start, end, False)
            if not text.strip():
                continue
            top, height = view.get_line_yrange(start)
            wx, wy = view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, 0, top)
            out.append({"id": len(out), "parent": rec["id"], "type": "GtkTextViewLine", "css": "label", "classes": [],
                        "name": "", "role": "paragraph", "text": text,
                        "box": [round(x0, 2), round(y0 + wy, 2), rec["box"][2], round(height, 2)],
                        "opacity": rec.get("opacity", 1), "color": rec.get("color"), "font": rec.get("font")})

    @staticmethod
    def letter_spacing(label):
        attrs = label.get_layout().get_attributes()
        if attrs is None:
            return None
        try:
            for attr in attrs.get_attributes():
                if attr.klass.type.value_nick == "letter-spacing":
                    return round(attr.value / 1024.0, 3) if hasattr(attr, "value") else None
        except Exception:  # noqa: BLE001 - older PyGObject
            return None
        return None

    # ── the render node ────────────────────────────────────────────────
    def paint(self, node, mapper, opacity, rclip, ops, clip=None) -> None:
        from gi.repository import Graphene

        kind = node.get_node_type().value_nick.replace("-node", "")
        box = lambda r: _box(mapper(r))  # noqa: E731

        def child(n, m=mapper, o=opacity, rc=rclip, c=clip):
            if n is not None:
                self.paint(n, m, o, rc, ops, c)

        start = len(ops)
        clip_now = clip

        if kind == "container":
            for i in range(node.get_n_children()):
                child(node.get_child(i))
        elif kind == "transform":
            tr = node.get_transform()
            if tr is None:
                child(node.get_child())
            else:
                child(node.get_child(), lambda r, tr=tr: mapper(tr.transform_bounds(r)))
        elif kind == "opacity":
            child(node.get_child(), o=opacity * node.get_opacity())
        elif kind == "rounded-clip":
            rr = node.get_clip()
            radius = [round(c.width, 2) for c in rr.corner]
            ops.append({"k": "rclip", "box": box(rr.bounds), "radius": radius})
            cb = box(rr.bounds)
            child(node.get_child(), rc={"box": cb, "radius": radius}, c=self.narrow(cb, clip_now))
        elif kind == "clip":
            cb = box(node.get_clip())
            child(node.get_child(), rc=None, c=self.narrow(cb, clip_now))
        elif kind == "color":
            ops.append({"k": "fill", "box": box(node.get_bounds()), "color": _rgba(node.get_color()), "op": round(opacity, 3),
                        "radius": self.radius_for(box(node.get_bounds()), rclip)})
        elif kind in ("linear-gradient", "repeating-linear-gradient", "radial-gradient", "repeating-radial-gradient", "conic-gradient"):
            if kind.startswith("repeating-"):
                stops, period = _repeating_gradient(node)
            else:
                stops = [[round(s.offset, 3), _rgba(s.color)] for s in node.get_color_stops()]
                period = None
            op = {"k": "gradient", "type": kind, "box": box(node.get_bounds()), "stops": stops, "op": round(opacity, 3),
                  "radius": self.radius_for(box(node.get_bounds()), rclip)}
            if period is not None:
                op["period"] = period
            ops.append(op)
        elif kind == "border":
            outline = node.get_outline()
            ops.append({"k": "border", "box": box(outline.bounds), "widths": [round(v, 2) for v in node.get_widths()],
                        "colors": [_rgba(c) for c in node.get_colors()], "radius": [round(c.width, 2) for c in outline.corner],
                        "op": round(opacity, 3)})
        elif kind in ("outset-shadow", "inset-shadow"):
            outline = node.get_outline()
            ops.append({"k": "shadow", "inset": kind == "inset-shadow", "box": box(outline.bounds), "color": _rgba(node.get_color()),
                        "dx": node.get_dx(), "dy": node.get_dy(), "blur": node.get_blur_radius(), "spread": node.get_spread(),
                        "radius": [round(c.width, 2) for c in outline.corner], "op": round(opacity, 3)})
        elif kind == "text":
            font = node.get_font()
            desc = font.describe()
            metrics = font.get_metrics(None)
            off = node.get_offset()
            origin = mapper(Graphene.Rect().init(off.x, off.y, 0, 0))
            ops.append({"k": "text", "box": box(node.get_bounds()), "x": round(origin.origin.x, 2), "baseline": round(origin.origin.y, 2),
                        "ascent": round(metrics.get_ascent() / 1024, 2), "descent": round(metrics.get_descent() / 1024, 2),
                        "font": _font(desc), "color": _rgba(node.get_color()), "glyphs": node.get_num_glyphs(), "op": round(opacity, 3)})
        elif kind in ("texture", "texture-scale"):
            ops.append({"k": "texture", "box": box(node.get_bounds()), "op": round(opacity, 3)})
        elif kind == "fill" or kind == "stroke":
            color = None
            inner = node.get_child()
            if inner is not None and inner.get_node_type().value_nick == "color-node":
                color = _rgba(inner.get_color())
            rec = {"k": "glyph", "box": box(node.get_bounds()), "color": color, "op": round(opacity, 3)}
            # A stroke node's bounds are conservative (a full line width on each side); the
            # spec's ink is the path geometry (getBBox), so record that too.
            try:
                rec["geo"] = box(_tight_path_bounds(node.get_path()))
            except Exception:  # noqa: BLE001 - fall back to the path's own bounds, never the stroke's
                try:
                    ok, geo = node.get_path().get_bounds()
                    if ok:
                        rec["geo"] = box(geo)
                except Exception:  # noqa: BLE001
                    pass
            ops.append(rec)
            if color is None:
                child(inner)
        elif kind == "color-matrix":
            # A symbolic icon drawn in black and recoloured (translucent ink): the glyphs'
            # colour is the matrix applied to theirs.
            first = len(ops)
            child(node.get_child())
            try:
                m, off = node.get_color_matrix(), node.get_color_offset()
                for op in ops[first:]:
                    if op["k"] == "glyph" and op.get("color"):
                        r, g, b, a = op["color"]
                        v = m.transform_vec4(Graphene.Vec4().init(r / 255, g / 255, b / 255, a))
                        out = [v.get_x() + off.get_x(), v.get_y() + off.get_y(), v.get_z() + off.get_z(), v.get_w() + off.get_w()]
                        out = [min(1.0, max(0.0, x)) for x in out]
                        op["color"] = [round(out[0] * 255, 1), round(out[1] * 255, 1), round(out[2] * 255, 1), round(out[3], 3)]
            except Exception:  # noqa: BLE001 - older bindings
                pass
        elif kind == "mask":
            # A translucent symbolic icon is its colour masked by its paths: the
            # glyphs are the mask's, their colour the source's.
            source = node.get_source()
            if source is not None and source.get_node_type().value_nick == "color-node":
                first = len(ops)
                child(node.get_mask())
                for op in ops[first:]:
                    if op["k"] == "glyph":
                        op["color"] = _rgba(source.get_color())
            else:
                child(source)
        elif kind == "shadow":
            child(node.get_child())
        elif kind == "cross-fade":
            child(node.get_end_child())
        elif kind == "blend":
            child(node.get_bottom_child())
            child(node.get_top_child())
        elif kind == "cairo":
            ops.append({"k": "cairo", "box": box(node.get_bounds()), "op": round(opacity, 3)})
        else:
            getter = getattr(node, "get_child", None)
            if getter is not None:
                try:
                    child(getter())
                except TypeError:
                    pass
        self.mark(ops, start, clip)

    @staticmethod
    def narrow(cb, clip):
        if clip is None:
            return cb
        return _isect(cb, clip) or [cb[0], cb[1], 0, 0]

    @staticmethod
    def mark(ops, start, clip):
        for op in ops[start:]:
            if "hidden" in op or clip is None:
                continue
            if clip[2] <= 0 or _isect(op["box"], clip) is None:
                op["hidden"] = True

    @staticmethod
    def radius_for(b, rclip):
        if not rclip:
            return None
        cb = rclip["box"]
        if all(abs(b[i] - cb[i]) <= 1.5 for i in range(4)) or (b[0] <= cb[0] + 0.5 and b[1] <= cb[1] + 0.5 and b[0] + b[2] >= cb[0] + cb[2] - 0.5 and b[1] + b[3] >= cb[1] + cb[3] - 0.5):
            return rclip["radius"]
        return None

    @staticmethod
    def attach_text(widgets, ops) -> None:
        """Give each text run to the deepest label or text widget holding it."""
        holders = [w for w in widgets if "text" in w and w["type"] != "GtkEntry"]
        order = {w["id"]: i for i, w in enumerate(widgets)}
        for op in ops:
            if op["k"] != "text":
                continue
            cx = op["x"] + 1
            cy = op["baseline"] - 1
            best = None
            # Inside a widget's box first; the 2 px slack only when none holds it (two labels side by
            # side, "launch" + "-deck.stage", share an edge the slack would blur). Among the holders,
            # one whose text has as many characters as the run has glyphs wins (a placeholder over a
            # list row scrolled beneath it); then the smallest.
            glyphs = op.get("glyphs")

            def rank(w):
                # glyph count, then a holder with text (a placeholder over its empty GtkText), then the
                # smallest box, then the deepest (the later in the walk)
                # A label too short to hold the run is never its holder (a sheet's two-line summary drawn
                # over a page row whose one-line title is behind it: Settings' hidden-network form).
                text = w.get("text") or ""
                return (0 if glyphs is not None and len(text) == glyphs else 1,
                        1 if glyphs is not None and text and len(text) < glyphs * 0.9 else 0,
                        # the one drawn last (later in the walk: a child, or a sheet over the page), then
                        # the smaller box
                        0 if text else 1, -order[w["id"]], w["box"][2] * w["box"][3])
            for slack in (0, 2):
                inside = [w for w in holders
                          if w["box"][0] - slack <= cx <= w["box"][0] + w["box"][2] + slack
                          and w["box"][1] - slack <= cy <= w["box"][1] + w["box"][3] + slack]
                if inside:
                    best = min(inside, key=rank)
                    break
            if best is not None:
                op["widget"] = best["id"]
