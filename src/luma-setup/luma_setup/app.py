# SPDX-License-Identifier: Apache-2.0
"""luma-setup: the application.

    luma-setup              first boot, in GDM's initial-setup session
    luma-setup --welcome    the steps after the first sign-in, once (welcome.py)
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


class SetupApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.window = None
        self.welcome = None   # set for --welcome: the post-login steps

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        try:
            from luma_appkit import install_lumaui
            install_lumaui()
        except Exception as error:  # noqa: BLE001 - the sheet draws without the kit's sheets
            log.info("LumaUI not installed: %s", error)
        from .sheet import load_style
        load_style()

    def do_activate(self) -> None:
        if self.window is not None:
            self.window.present()
            return
        from .sheet import Posture, SetupWindow, posture

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


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="luma-setup: %(message)s")
    if "--fixture" in argv:
        at = argv.index("--fixture")
        os.environ["LUMA_SETUP_FIXTURE"] = argv[at + 1] if at + 1 < len(argv) else "1"
        del argv[at:at + 2]
    GLib.set_prgname(APP_ID)
    application = SetupApplication()
    if "--welcome" in argv:
        from .welcome import pending
        welcome = pending() if not system.fixture() else dict(system.fixture().get("welcome", {"full_name": "Nick McMillan", "lift": False}))
        if welcome is None:
            return 0   # done, or never asked for
        application.welcome = welcome
    return application.run(argv[:1])
