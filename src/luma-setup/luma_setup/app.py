# SPDX-License-Identifier: Apache-2.0
"""luma-setup: the application.

    luma-setup              first boot, in GDM's initial-setup session
    luma-setup --welcome    the steps after the first sign-in, once (welcome.py)
    luma-setup --cover      the setup's last frame, drawn again as the first session's
                            first frame; the Shell lifts it off the desktop (gnome-shell 0321)
    luma-setup --fixture F  the same with LUMA_SETUP_FIXTURE=F (captures, reviews)

The pages the installer answered are left out (system.installer_answers).
"""
from __future__ import annotations

import logging
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import catalog, system  # noqa: E402
from .model import Setup  # noqa: E402

__all__ = ("SetupApplication", "main", "APP_ID")

APP_ID = "org.projectluma.Setup"
log = logging.getLogger("luma-setup")
STARTED = __import__("time").monotonic()


def initial_setup() -> Setup:
    """The answers to start from: the installer's, else English (US) and the system's time zone."""
    setup = Setup(locale=catalog.DEFAULT_LOCALE, layout=catalog.DEFAULT_LAYOUT, timezone=system.current_timezone())
    answers = system.installer_answers()
    answered = set()
    if answers.get("locale"):
        setup.locale = answers["locale"]
        setup.layout = answers.get("layout") or catalog.keyboards(setup.locale)[0].layout
        if "hello" in answers.get("answered", ()):
            answered.add("hello")
    fixture = system.fixture() or {}
    for key in ("locale", "layout", "name", "timezone"):
        if fixture.get(key):
            setattr(setup, key, fixture[key])
    if fixture.get("name"):
        setup.set_name(fixture["name"])
    for key in ("password", "again"):
        if fixture.get(key):
            setattr(setup, key, fixture[key])
    if fixture.get("timezone_automatic"):
        setup.timezone_automatic = True
    answered.update(fixture.get("answered", ()))
    setup.answered = frozenset(answered)
    return setup


def _process_age() -> float:
    try:
        ticks = int(open("/proc/self/stat").read().rsplit(")", 1)[1].split()[19])
        uptime = float(open("/proc/uptime").read().split()[0])
        return uptime - ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return -1.0


class SetupApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.window = None
        self.welcome = None   # set for --welcome: the post-login steps
        self.frame = None     # set for --cover: the setup's last frame, as data

    def do_startup(self) -> None:
        from .sheet import trace
        trace("startup")
        Adw.Application.do_startup(self)
        trace("startup: adw")
        try:
            from luma_appkit import install_lumaui
            install_lumaui()
        except Exception as error:  # noqa: BLE001 - the sheet draws without the kit's sheets
            log.info("LumaUI not installed: %s", error)
        trace("startup: lumaui")
        from .sheet import load_style
        load_style()
        trace("startup: style")
        # A field keeps what is in it when it takes the focus; nothing is selected for the person.
        Gtk.Settings.get_default().props.gtk_entry_select_on_focus = False

    def _shown(self, *_args) -> None:
        log.info("on screen %.1f s after the process started (%.1f s in this module)",
                 _process_age(), __import__("time").monotonic() - STARTED)

    def do_activate(self) -> None:
        if self.window is not None:
            self.window.present()
            return
        from .sheet import Posture, SetupWindow, posture

        if self.frame is not None:
            self._cover()
            return
        if self.welcome is not None:
            from .welcome import WelcomeWindow, carry_accessibility
            carry_accessibility(self.welcome)
            if posture() == Posture.TV:
                # TV: no desk to style, Connect and apps have their own TV places; nothing to ask here.
                from .welcome import mark_done
                mark_done("tv")
                return
            self.window = WelcomeWindow(self, self.welcome, phone=posture() == Posture.PHONE)
            if os.environ.get("LUMA_SETUP_WINDOWED"):
                width, _, height = os.environ["LUMA_SETUP_WINDOWED"].partition("x")
                self.window.set_default_size(int(width) if height else 1440, int(height) if height else 900)
            elif not os.environ.get("LUMAUI_CONFORM_DUMP"):
                self.window.fullscreen()
            self.window.connect("map", self._shown)
            self.window.present()
            return
        setup = initial_setup()
        form = posture()
        if form == Posture.TV:
            from .sheet_tv import SetupTVWindow
            self.window = SetupTVWindow(self, setup)
        else:
            self.window = SetupWindow(self, setup, form)
        data = system.fixture() or {}
        page = data.get("page")
        if page and page in self.window.pages:
            self.window.index = self.window.pages.index(page)
            if data.get("open_network"):
                self.window.open_network = data["open_network"]
            self.window.draw()
        windowed = os.environ.get("LUMA_SETUP_WINDOWED", "")
        if windowed:
            width, _, height = windowed.partition("x")
            self.window.set_default_size(int(width) if height else 1440, int(height) if height else 900)
        elif not os.environ.get("LUMAUI_CONFORM_DUMP"):
            self.window.fullscreen()
        self.window.present()


    def _cover(self) -> None:
        """The setup's last frame again, in the person's first session: the same code, the same
        picture, the same text settings. The Shell shows its first frame once this one is
        drawn (the title says so) and lifts it off the desktop; it closes this window."""
        from .sheet import Posture, SetupWindow

        frame = self.frame
        settings = Gtk.Settings.get_default()
        for name, value in (frame.get("settings") or {}).items():
            try:
                settings.set_property(name, value)
            except (TypeError, ValueError) as error:
                log.info("cover: setting %s: %s", name, error)
        if frame.get("scale"):
            os.environ["LUMA_SETUP_SCALE"] = str(frame["scale"])
        from .sheet import trace
        trace("cover: window")
        setup = Setup(locale=catalog.DEFAULT_LOCALE, layout=catalog.DEFAULT_LAYOUT, timezone="UTC")
        form = Posture(frame.get("posture") or "desktop")
        if form == Posture.TV:
            from .sheet_tv import SetupTVWindow
            self.window = SetupTVWindow(self, setup, frame)
        else:
            self.window = SetupWindow(self, setup, form, frame=frame)
        trace("cover: built")
        self.window.set_title(COVER_TITLE)
        self.window.fullscreen()
        # Nothing has the focus in the setup's last frame; nothing has it here either (no ring).
        self.window.set_focus_visible(False)
        self.window.connect("map", lambda window: window.set_focus(None))
        self.window.connect("map", self._shown)
        self.window.connect("realize", self._cover_watch)
        self.window.present()
        # Never outstays the lift: the Shell closes it, else it goes by itself (the Shell
        # gives up waiting for it after 15 s).
        GLib.timeout_add_seconds(45, lambda: (self.quit(), False)[1])

    def _cover_watch(self, window: Gtk.Window) -> None:
        clock = window.get_frame_clock()

        def painted(_clock) -> None:
            # The first frame at the full screen's size is the whole picture (everything is built
            # before it); no second frame comes while the Shell holds its own first frame back.
            if not window.is_fullscreen() or window.get_width() < 2 or window.get_height() < 2:
                return
            clock.disconnect(handler)
            window.set_title(COVER_TITLE + COVER_READY)
            from .sheet import trace
            trace("cover: ready")
        handler = clock.connect("after-paint", painted)


def _wait_for_wayland(seconds: float) -> str | None:
    """The session's Wayland display, once its compositor has made the socket."""
    import stat
    import time
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            for name in sorted(os.listdir(runtime)):
                if name.startswith("wayland-") and not name.endswith(".lock") and \
                        stat.S_ISSOCK(os.stat(os.path.join(runtime, name)).st_mode):
                    return name
        except OSError:
            pass
        time.sleep(0.05)
    return None


COVER_TITLE = "luma-setup-cover"
COVER_READY = ":ready"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="luma-setup: %(message)s")
    if "--fixture" in argv:
        at = argv.index("--fixture")
        os.environ["LUMA_SETUP_FIXTURE"] = argv[at + 1] if at + 1 < len(argv) else "1"
        del argv[at:at + 2]
    GLib.set_prgname(APP_ID)
    application = SetupApplication()
    if "--first-session" in argv:
        from .welcome import first_session
        at = argv.index("--first-session")
        return first_session(argv[at + 1] if at + 1 < len(argv) else "settings")
    if "--cover" in argv:
        from .welcome import pending
        welcome = pending() or {}
        frame = welcome.get("last_frame") if not system.fixture() else system.fixture().get("last_frame")
        if not isinstance(frame, dict) or not frame.get("rows"):
            log.info("cover: no last frame to draw")
            return 1
        # Started with the Shell (luma-setup-cover.service): the slow part first, then the display.
        from . import sheet
        sheet.trace("cover: modules")
        sheet.softened_wallpaper(bool(frame.get("light")), frame.get("wallpaper") or None)
        sheet.trace("cover: softened")
        if not os.environ.get("WAYLAND_DISPLAY"):
            display = _wait_for_wayland(30.0)
            if display is None:
                log.info("cover: no display")
                return 1
            os.environ["WAYLAND_DISPLAY"] = display
        # GTK was imported before the display existed: initialise it now (PyGObject keeps
        # its own note of whether that worked, and refuses windows without it).
        from gi.overrides import Gtk as gtk_override
        from gi.repository import Gdk
        if Gdk.Display.get_default() is None:
            opened = Gdk.Display.open(os.environ["WAYLAND_DISPLAY"])
            if opened is None or not Gtk.init_check():
                log.info("cover: the display would not open")
                return 1
            Gdk.DisplayManager.get().set_default_display(opened)
        gtk_override.initialized = True
        sheet.trace("cover: display", os.environ["WAYLAND_DISPLAY"])
        application.frame = frame
        return application.run(argv[:1])
    if "--welcome" in argv:
        from .welcome import pending
        welcome = pending() if not system.fixture() else dict(system.fixture().get("welcome", {"full_name": "Nick McMillan", "lift": False}))
        if welcome is None:
            return 0   # done, or never asked for
        application.welcome = welcome
    return application.run(argv[:1])
