# SPDX-License-Identifier: Apache-2.0
"""Tasks-only controls composed from LumaUI type, glyphs and person faces.

The window, navigation, details, action center, menus and toasts belong to the
kit. These checks, task rows and activity lines express the Tasks-only model;
their sheet uses kit tokens, never colours or typography defined in this app.
"""
from pathlib import Path
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk, Pango
from luma_appkit import PersonAvatar, AvatarStack, RowLead, Selection, apply_type, icons
from .tasks_data import day_label


def text(words, role="body", *, muted=None, weight=None, **kwargs):
    return apply_type(Gtk.Label(label=words, xalign=0, **kwargs), role, muted=muted,
                      weight=weight)


def line(spacing=8):
    return Gtk.Box(spacing=spacing, valign=Gtk.Align.CENTER)


def column(spacing=0):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)


def named(widget, name):
    widget.set_name(name)
    return widget


def face(key, people, size=24, fixture_dir=None):
    person = people.get(key, {"n": key})
    picture = None
    path = Path(fixture_dir) / f"face-{key}.jpg" if fixture_dir and key != "me" else None
    if path and path.exists():
        try: picture = Gdk.Texture.new_from_filename(str(path))
        except Exception: pass
    return PersonAvatar("N" if key == "me" else person["n"], size, picture=picture,
                        hue=person.get("h"))


def faces(keys, people, size=24, fixture_dir=None):
    if size == 18:
        entries = []
        for key in keys:
            person = people.get(key, {"n": key})
            path = Path(fixture_dir) / f"face-{key}.jpg" if fixture_dir and key != "me" else None
            picture = Gdk.Texture.new_from_filename(str(path)) if path and path.exists() else None
            entries.append(("N" if key == "me" else person["n"], picture))
        return AvatarStack(entries, size="row", max=3)
    # TODO(kit-request tasks-01-task-row.md): SB3 needs the 24/26px sizes.
    # The approved PersonAvatar stand-in keeps v70's six-pixel overlap through
    # placement only; each shared face retains its own styling.
    entries = [face(key, people, size, fixture_dir) for key in keys[:4]]
    if not entries: return line(0)
    entries[0].set_halign(Gtk.Align.START)
    stack = Gtk.Overlay(child=entries[0], valign=Gtk.Align.CENTER, halign=Gtk.Align.START)
    stride = size - 6
    stack.set_size_request(size + stride * (len(entries) - 1), size)
    for offset, avatar in enumerate(entries[1:], 1):
        avatar.set_halign(Gtk.Align.START); avatar.set_margin_start(offset * stride)
        stack.add_overlay(avatar)
    return stack


def check(done, priority, callback, name, *, step=False, big=False):
    button = named(Gtk.CheckButton(active=done, accessible_role=Gtk.AccessibleRole.BUTTON,
                                         valign=Gtk.Align.CENTER, tooltip_text="Complete"), name)
    button.add_css_class("tk-step-check" if step else "tk-task-check")
    if big:
        button.add_css_class("big")
        button.set_valign(Gtk.Align.START); button.set_margin_top(2)
    button.add_css_class(f"priority-{priority}")
    if done: button.add_css_class("done")
    button.update_property([Gtk.AccessibleProperty.LABEL], ["Complete"])
    button.connect("toggled", lambda *_: callback())
    overlay = Gtk.Overlay(child=button, valign=Gtk.Align.START if big else Gtk.Align.CENTER)
    glyph = icons.image("check", pixel_size=13 if big else 12)
    glyph.set_halign(Gtk.Align.CENTER); glyph.set_valign(Gtk.Align.CENTER)
    glyph.set_can_target(False); glyph.add_css_class("tk-check-glyph")
    if done: glyph.add_css_class("done")
    overlay.add_overlay(glyph)
    return overlay


def list_dot(source):
    return RowLead.dot(source.get("h", 250))


def task_row(task, source, people, today, view, pick, complete, *, selected=False,
             suggested=False, on_today=None, fixture_dir=None):
    row = named(line(12), f'tk-row-{task["id"]}')
    row.add_css_class("tk-task-row")
    Selection.mark(row, selected)
    if suggested: row.add_css_class("suggested")
    if task["done"]: row.add_css_class("completed")
    if suggested:
        sunrise = line(0); sunrise.add_css_class("tk-suggestion-icon")
        sunrise.set_margin_start(3); sunrise.set_margin_end(3)
        sunrise.append(icons.image("sunrise", pixel_size=18)); row.append(sunrise)
    else: row.append(check(task["done"], task["pri"], complete, f'tk-complete-{task["id"]}'))
    words = column(3)
    title_line = line(7)
    title = text(task["t"], "list-title", weight=550, ellipsize=Pango.EllipsizeMode.END)
    title.add_css_class("tk-task-title")
    title_line.append(title)
    if task.get("flag"):
        flag = line(0); flag.add_css_class("tk-task-flag")
        flag.append(icons.image("flag", pixel_size=12)); title_line.append(flag)
    title_line.append(Gtk.Box(hexpand=True))
    words.append(title_line)
    meta = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=12, row_spacing=4,
                       min_children_per_line=1, max_children_per_line=8, halign=Gtk.Align.START)
    meta.add_css_class("tk-task-meta")
    def fact(icon, label, overdue=False):
        part = line(4)
        if icon: part.append(icons.image(icon, pixel_size=12))
        if label: part.append(text(label, "caption", weight=600 if overdue else 400))
        if overdue: part.add_css_class("tk-overdue")
        meta.append(part)
    if view not in (source["id"],):
        part = line(4); part.append(list_dot(source)); part.append(text(source["n"], "caption", weight=400)); meta.append(part)
    due = task.get("due")
    clock = ((task['start_time'] + '–') if task.get('start_time') else '') + task.get('time', '')
    if (due is not None and view != "today") or (due is not None and due < 0) or suggested:
        fact("calendar", day_label(due, today) + (", " + clock if clock else ""), due is not None and due < 0)
    elif clock: fact("clock", clock)
    if not suggested:
        if task["subs"]: fact("list", f'{sum(s[1] for s in task["subs"])}/{len(task["subs"])}')
        comments = sum(a[1] == "c" for a in task["act"])
        if comments: fact("message-square", str(comments))
        if task["notes"]: fact("file-text", "")
    if meta.get_first_child(): words.append(meta)
    target = named(Gtk.Button(hexpand=True), f'tk-pick-{task["id"]}')
    target.add_css_class("tk-task-pick")
    target.set_child(words); target.connect("clicked", lambda *_: pick())
    target.update_property([Gtk.AccessibleProperty.LABEL], [task["t"]])
    row.append(target)
    if suggested:
        from luma_appkit import BarAction
        from luma_appkit.action_center import make_control
        today_button = named(make_control(BarAction("plus", "Today", filled=True, on_activate=on_today), size="bubble"), f'tk-today-{task["id"]}')
        row.append(today_button)
    elif task["who"] and (task["who"] != ["me"]):
        row.append(faces(task["who"][:3], people, fixture_dir=fixture_dir))
    return row
