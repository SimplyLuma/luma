# SPDX-License-Identifier: Apache-2.0
"""A Luma window opens by the shared rule and never loses its close control.

Real windows on a real display: the opening size the kit gives a window with
nothing remembered for it, and the title row it refuses to give up on a
desktop however narrow the window is — the Phone and Messages bug, where a
window opened narrow enough to trip its own phone layout and left no way to
close it. ADR-042.
"""
import json
import os
from pathlib import Path
import tempfile
import time
import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gio, GLib
from luma_appkit import AppWindow, CommandRegistry
from luma_appkit import window_policy

# The kit's own record is the fallback store; this test is about the kit, not
# about Tiling Shell's window memory, so the kit keeps its geometry here.
os.environ['LUMA_WINDOW_MEMORY'] = 'app'

#: Wide enough to hide a title row at, in the style of the real Phone window.
NARROW = 959


def settle(predicate=lambda: True):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('The window did not settle')


def screen_of(window):
    monitors = window.get_display().get_monitors()
    geometry = monitors.get_item(0).get_geometry()
    return window_policy.Screen(geometry.width, geometry.height, 'bottom')


with tempfile.TemporaryDirectory() as root:
    os.environ['XDG_STATE_HOME'] = root
    app = Adw.Application(application_id='org.projectluma.OpeningTest',
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    record = Path(root) / 'luma/windows/org.projectluma.OpeningTest.json'
    record.parent.mkdir(parents=True)

    def window(narrow_layout=True, **overrides):
        arguments = dict(
            application=app, app_id='org.projectluma.OpeningTest', title='Opening',
            icon_name='application-x-executable', commands=CommandRegistry(()),
            default_width=860, default_height=690, minimum_width=360, minimum_height=480,
        )
        arguments.update(overrides)
        built = AppWindow(**arguments)
        built.sidebar = Adw.Bin(visible=True)
        built.set_body(built.sidebar)
        if narrow_layout:
            # A phone layout, exactly as the core applications declare one: it
            # folds the sidebar away and hides the title row.
            compact = Adw.Breakpoint.new(
                Adw.BreakpointCondition.parse(f'max-width: {NARROW}px'))
            compact.add_setter(built.sidebar, 'visible', False)
            compact.add_setter(built.title_bar, 'visible', False)
            built.add_breakpoint(compact)
        return built

    # 1. Nothing remembered: the shared rule decides, the window opens wider
    #    than its own phone layout, and the title row is there.
    first = window()
    assert first.narrow_width == NARROW + 1, first.narrow_width
    expected = window_policy.opening_size(
        screen=screen_of(first), default=(860, 690), minimum=(360, 480),
        narrow=NARROW + 1, declared='auto')
    first.present()
    settle(lambda: first.get_allocated_width() > 0)
    assert first.get_default_size() == expected, (first.get_default_size(), expected)
    assert expected[0] > NARROW, expected
    assert first.title_bar.get_visible(), 'the window opened with no way to close it'
    assert first.sidebar.get_visible(), 'the window opened in its phone layout'
    first.destroy()
    settle()

    # 2. A size the person left behind, narrow enough to trip the phone
    #    layout: the layout folds, and the title row stays.
    record.write_text(json.dumps({'width': 520, 'height': 620, 'maximized': False}))
    narrow = window()
    assert narrow.get_default_size() == (520, 620), narrow.get_default_size()
    narrow.present()
    settle(lambda: narrow.get_allocated_width() > 0)
    assert narrow.get_allocated_width() <= NARROW, narrow.get_allocated_width()
    settle(lambda: not narrow.sidebar.get_visible())
    assert not narrow.sidebar.get_visible(), 'the phone layout never applied'
    assert narrow.title_bar.get_visible(), 'a remembered narrow size hid the close control'
    assert narrow.get_decorated()
    narrow.destroy()
    settle()

    # 3. Whatever hides the row — a breakpoint, a page change, a mode switch —
    #    a framed desktop window keeps it.
    record.unlink()
    stubborn = window()
    stubborn.present()
    settle(lambda: stubborn.get_allocated_width() > 0)
    stubborn.title_bar.set_visible(False)
    assert stubborn.title_bar.get_visible(), 'the title row could be hidden outright'
    assert stubborn._chrome_corrections == 1, stubborn._chrome_corrections
    stubborn.destroy()
    settle()

    # 4. A utility surface keeps the size it asked for; the rule is about
    #    documents and views, not about a note or an assistant.
    note = window(narrow_layout=False, default_width=408, default_height=372,
                  minimum_width=396, minimum_height=300)
    note.present()
    settle(lambda: note.get_allocated_width() > 0)
    assert note.get_default_size() == (408, 372), note.get_default_size()
    note.destroy()
    settle()

    # 5. A window that gives up its frame answers for its own close control,
    #    and the guarantee leaves it alone.
    own = window()
    own.set_decorated(False)
    own.title_bar.set_visible(False)
    assert not own.title_bar.get_visible()
    own.destroy()
    settle()

    # 6. On a handheld the shell draws the surface chrome, so the phone layout
    #    is right to hide the row and the guarantee stays out of the way.
    os.environ['LUMA_PRESENTATION_MODE'] = 'fullscreen-mobile'
    try:
        handheld = window()
        assert not handheld.get_decorated()
        assert not handheld.title_bar.get_visible()
        handheld.destroy()
        settle()
    finally:
        del os.environ['LUMA_PRESENTATION_MODE']

print('PASS: opening size from the shared rule, wider than the window’s own '
      'phone layout; remembered narrow size, an outright hide and a resize all '
      'keep the close control; utility size, own-frame and handheld surfaces '
      'left alone')
