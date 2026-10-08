#!/usr/bin/env python3
"""Private-display Calculator identity menu placement and command contract."""
import gi
import os

gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, GLib, Gtk

from luma_appkit import Command, CommandGroup, CommandRegistry, Menu, install_appkit
from luma_appkit.window_frame import WindowIdentity, own_window_frame


def settle():
    loop = GLib.MainLoop()
    GLib.timeout_add(200, lambda: (loop.quit(), False)[1])
    loop.run()


Adw.init()
install_appkit()
selected = {'basic': True}
registry = CommandRegistry((CommandGroup(None, (
    Command('calc.basic', 'Basic', lambda: None, shortcut=('Ctrl', '1'), checked=lambda: selected['basic']),
    Command('calc.scientific', 'Scientific', lambda: None, shortcut=('Ctrl', '2'), checked=lambda: not selected['basic']),
)), CommandGroup(None, (
    Command('calc.copy', 'Copy result', lambda: None),
    Command('calc.clear', 'Clear tape', lambda: None),
))))

for width in (390, 1180):
    window = Gtk.Window(title='Calculator', default_width=width, default_height=820)
    title = Gtk.Box()
    title.set_valign(Gtk.Align.START)
    title.set_margin_start(8)
    title.set_margin_top(7)
    window.set_child(title)
    menu = Menu(registry, variant='compact', keep_parent=True, phone_placement='popover')
    identity = WindowIdentity(window, menu)
    title.append(identity)
    window.present()
    settle()
    identity.set_active(True)
    settle()
    assert menu.get_mapped(), f'identity menu became a drawer at {width}px'
    assert menu.get_position() == Gtk.PositionType.BOTTOM
    contents = menu.get_first_child()
    assert contents.get_width() == 188, (width, contents.get_width())
    assert menu.get_parent() is identity, 'identity menu lost its anchor'
    ok, bounds = contents.compute_bounds(window)
    assert ok and (bounds.get_x(), bounds.get_y(), bounds.get_width(), bounds.get_height()) == (
        8, 45, 200, 167), (width, bounds.get_x(), bounds.get_y(), bounds.get_width(), bounds.get_height())
    keys = []
    marks = []
    mark_widgets = []
    rows = []
    labels = []
    def visit(widget):
        if widget.has_css_class('luma-menu-row'):
            rows.append(widget)
        if isinstance(widget, Gtk.Label):
            labels.append(widget)
        if widget.has_css_class('luma-menu-keycap'):
            keys.append(widget.get_label())
        if widget.has_css_class('luma-menu-check'):
            marks.append((widget.get_icon_name(), widget.get_visible()))
            mark_widgets.append(widget)
        child = widget.get_first_child()
        while child:
            visit(child)
            child = child.get_next_sibling()
    visit(menu)
    assert keys == ['Ctrl', '1', 'Ctrl', '2'], keys
    assert len(marks) == 2 and marks[0][1] and not marks[1][1], marks
    assert all('check' in name and 'object-select' not in name for name, _visible in marks), marks
    assert [row.has_css_class('luma-menu-selected') for row in rows] == [True, False, False, False]
    basic = next(label for label in labels if label.get_label() == 'Basic')
    scientific = next(label for label in labels if label.get_label() == 'Scientific')
    assert basic.get_layout().get_context().get_font_description().get_weight() == 600
    assert scientific.get_layout().get_context().get_font_description().get_weight() == 400
    if width == 390 and os.environ.get('EXPECTED_MENU_FILL'):
        expected = tuple(float(v) for v in os.environ['EXPECTED_MENU_FILL'].split(','))
        def painted_row(row):
            snapshot = Gtk.Snapshot()
            Gtk.WidgetPaintable.new(row).snapshot(snapshot, row.get_width(), row.get_height())
            def colours(node):
                if node is None:
                    return []
                own = [node.get_color()] if node.get_node_type().value_nick == 'color-node' else []
                return own + [colour for child in node.get_children() for colour in colours(child)]
            return colours(snapshot.to_node())
        def matches(colour):
            return all(abs(actual - want) < 0.006 for actual, want in
                       zip((colour.red, colour.green, colour.blue, colour.alpha), expected))
        assert any(matches(colour) for colour in painted_row(rows[0]))
        assert not any(matches(colour) for colour in painted_row(rows[1]))
    selected['basic'] = False
    menu._refresh_state(menu)
    assert [row.has_css_class('luma-menu-selected') for row in rows] == [False, True, False, False]
    assert [mark.get_visible() for mark in mark_widgets] == [False, True]
    selected['basic'] = True
    menu._refresh_state(menu)
    assert [row.has_css_class('luma-menu-selected') for row in rows] == [True, False, False, False]
    menu.popdown()
    window.destroy()
    print('PASS identity popover', width, '200px, anchored, Ctrl shortcuts')

for width in (390, 1180):
    window = Gtk.Window(title='Calculator', default_width=width, default_height=820)
    view = Adw.ToolbarView()
    header = Adw.HeaderBar()
    view.add_top_bar(header)
    work = Gtk.Box()
    view.set_content(work)
    window.set_child(view)
    menu = Menu(registry, variant='compact', keep_parent=True, phone_placement='popover')
    identity = WindowIdentity(window, menu)
    own_window_frame(window, header, work, identity)
    window.present()
    settle()
    ok, box = identity.compute_bounds(window)
    assert ok and (box.get_x(), box.get_y(), box.get_width(), box.get_height()) == (
        8, 7, 130, 32), (width, box.get_x(), box.get_y(), box.get_width(), box.get_height())
    window.destroy()
    print('PASS identity hitbox', width, 'x8 y7 130x32')
