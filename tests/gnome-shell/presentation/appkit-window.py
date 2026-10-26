# SPDX-License-Identifier: Apache-2.0
"""Real C and Python AppKit windows keep their content during mode changes."""
import time
import json
import os
import sys
from pathlib import Path
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("LumaUI", "1")
from gi.repository import Adw, Gio, GLib, Gtk, LumaUI
from luma_appkit import AppWindow, CommandRegistry, Island, NavigationSidebar, add_style_sheet, add_style_builder

app = Adw.Application(application_id="org.projectluma.PresentationWindowFixture",
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
record = Path(GLib.get_user_state_dir()) / "luma/windows" / (app.get_application_id() + ".json")
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps({"width": 700, "height": 500, "maximized": False}))
python = AppWindow(application=app, app_id=app.get_application_id(), title="Python document",
    icon_name="application-x-executable", commands=CommandRegistry(()))
source = Path(os.environ["PRESENTATION_SOURCE"])
python.add_css_class("luma-notes")
add_style_sheet(str(source / "src/prairie-core/style/notes.css"))
# Reproduce the legacy Notes rule as well: the shared frame must protect its
# inset even when an app still ships a composition reset at app priority.
add_style_builder(lambda _appearance: '.luma-notes .luma-window-body { padding-top: 0; }')
draft = Gtk.Entry(text="Unsaved draft")
sidebar = NavigationSidebar(variant="destinations")
page = Island()
page.append(draft)
panes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
panes.append(sidebar)
panes.append(page)
python.set_body(panes)
native = LumaUI.ApplicationWindow.new(app, None)
native.add_css_class("messages-window")
native.add_css_class("luma-fullscreen")
add_style_sheet(str(source / "src/prairie-core/style/messages.css"))
native_draft = Gtk.Entry(text="Native unsaved draft")
native_sidebar = NavigationSidebar(variant="destinations")
native_page = Island()
native_page.append(native_draft)
native_panes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
native_panes.append(native_sidebar)
native_panes.append(native_page)
native.set_body(native_panes)
python.set_phone_bleed(True)
native.set_phone_bleed(True)
python.set_status_bar_style("light")
native.set_status_bar_style("light")
assert python.lookup_action("luma-status-ink").get_state().unpack() == "light"
assert native.lookup_action("luma-status-ink").get_state().unpack() == "light"
python.present()
native.present()
proxy = Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
    "org.project_luma.ShellState1", "/org/project_luma/ShellState1",
    "org.project_luma.ShellState1", None)

def settle(predicate):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("AppKit windows did not follow the session context")

for mode in ("tablet", "desktop", "mobile", "desktop"):
    proxy.call_sync("SetPresentation", GLib.Variant("(s)", (mode,)),
                    Gio.DBusCallFlags.NONE, 3000, None)
    decorated = mode == "desktop"
    settle(lambda: python.get_decorated() == decorated and native.get_decorated() == decorated
        and python.title_bar.get_visible() == decorated
        and native.get_title_bar().get_visible() == decorated
        and native.props.context.get_experience() == mode)
    assert python.has_css_class("lumaui-tablet-mode") == (mode == "tablet")
    assert native.has_css_class("lumaui-tablet-mode") == (mode == "tablet")
    assert draft.get_text() == "Unsaved draft"
    assert native_draft.get_text() == "Native unsaved draft"
    assert python.get_visible() and native.get_visible()
    if mode == "tablet":
        def below_status(widget, window):
            ok, bounds = widget.compute_bounds(window)
            # GTK native transforms map surface coordinates into widget
            # coordinates. Compare against the status strip in surface space,
            # including any CSD origin retained during fullscreen configure.
            _x, surface_to_widget_y = window.get_surface_transform()
            return ok and bounds.get_y() - surface_to_widget_y >= 52
        surfaces = ((sidebar, python), (page, python), (native_sidebar, native), (native_page, native))
        try:
            settle(lambda: all(below_status(widget, window) for widget, window in surfaces))
        except AssertionError:
            for widget, window in surfaces:
                ok, bounds = widget.compute_bounds(window)
                print("Safe-area diagnostic:", type(widget).__name__, type(window).__name__,
                      ok, bounds.get_y(), window.get_css_classes(), window.get_surface_transform(),
                      'window/surface', window.get_height(), window.get_surface().get_height(), window.get_surface().get_state(), flush=True)
                ancestor = widget
                while ancestor is not None:
                    context = ancestor.get_style_context()
                    padding, margin, border = context.get_padding(), context.get_margin(), context.get_border()
                    _, area = ancestor.compute_bounds(window)
                    print('Frame ancestor:', type(ancestor).__name__, ancestor.get_css_classes(),
                          area.get_y(), padding.top, margin.top, border.top, flush=True)
                    ancestor = ancestor.get_parent()
            raise
        assert python.body.has_css_class("lumaui-frame-sidebar")
        print("PASS Tablet sidebars and pages start below the transparent status strip")
        python.remember("draft-state", "changed in Tablet")
        saved = json.loads(record.read_text())
        assert (saved["width"], saved["height"]) == (700, 500), saved
        assert saved["draft-state"] == "changed in Tablet"
    print("PASS live C/Python frame and document preservation:", mode)
python.close()
native.close()

# Weather deliberately lets its sky reach the bezel. Its controls and places
# remain below the status strip, including when only presentation changes.
source = Path(os.environ["PRESENTATION_SOURCE"])
sys.path.insert(0, str(source / "src/prairie-core"))
os.environ["LUMA_WEATHER_FIXTURE"] = str(source / "tests/fixtures/weather-v70.json")
from luma_appkit import add_style_sheet
from prairie_apps.weather import WeatherWindow
add_style_sheet(str(source / "src/prairie-core/style/weather.css"))
weather = WeatherWindow(app)
weather.present()
for mode in ("tablet", "desktop", "tablet"):
    proxy.call_sync("SetPresentation", GLib.Variant("(s)", (mode,)),
                    Gio.DBusCallFlags.NONE, 3000, None)
    settle(lambda: weather.context.device_class == mode)
    if mode == "tablet":
        settle(lambda: weather.page.get_margin_top() == 56 and weather.sidebar.get_margin_top() == 52)
        settle(lambda: below_status(weather.sidebar, weather) and below_status(weather.page, weather))
        ok, bounds = weather.sky.compute_bounds(weather)
        assert ok and bounds.get_y() <= 2, bounds.get_y()
    else:
        settle(lambda: weather.sidebar.get_margin_top() == 0)
    print("PASS Weather background and foreground safe area:", mode)
weather.close()

# Terminal is a host-installed AppKit client, including its D-Bus launch path.
# Preserve its sessions while the shared mode service changes the frame.
sys.path.insert(0,str(source/'src/luma-terminal'))
from luma_terminal.terminal import TerminalWindow
add_style_sheet(str(source/'src/luma-terminal/style/terminal.css'))
fixture=json.loads((source/'tests/fixtures/term-v70.json').read_text())
terminal=TerminalWindow(app,fixture=fixture)
terminal.present()
original_model=terminal.model
original_panes=dict(terminal.pane_views)
for mode in ('tablet','mobile','desktop','tablet','desktop'):
    proxy.call_sync('SetPresentation',GLib.Variant('(s)',(mode,)),Gio.DBusCallFlags.NONE,3000,None)
    decorated=mode=='desktop'
    settle(lambda:terminal.context.device_class==('handheld' if mode=='mobile' else mode)
        and terminal.title_bar.get_visible()==decorated
        and terminal.get_decorated()==decorated)
    assert terminal.model is original_model
    assert terminal.pane_views==original_panes
    assert terminal.window_controls.get_visible()==decorated
    if mode=='tablet':settle(lambda:below_status(terminal.stage,terminal))
    print('PASS Terminal live frame and session preservation:',mode,flush=True)
terminal.close()
