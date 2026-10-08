# SPDX-License-Identifier: GPL-3.0-only
"""Desktop PiP surface for Chromium's PiP session on its private display.

Reuse AppKit's window and the existing video-only NativeMedia paintable. The
original Chromium PiP session retains playback, page API state and audio.
"""
from gi.repository import Gtk
from luma_appkit import AppWindow, Command, CommandGroup, CommandRegistry


class NativePip(AppWindow):
    def __init__(self, host, tab_id):
        self.host, self.tab_id = host, tab_id
        application = host.get_application()
        menu = application.get_menubar()
        commands = CommandRegistry((CommandGroup(None, (
            Command('window.close', 'Close Picture in Picture', self.close,
                    shortcut=('Ctrl', 'Shift', 'W')),
            Command('browser.new-window', 'New window', host._new_window, shortcut=('Ctrl', 'N')),
            Command('browser.close-tab', 'Close tab', host._close_tab, shortcut=('Ctrl', 'W')),
            Command('browser.restore-tab', 'Reopen closed tab', host._restore_tab,
                    shortcut=('Ctrl', 'Shift', 'T')),
        )),))
        super().__init__(application=application, app_id=application.get_application_id(),
                         title='Picture in Picture', icon_name='com.rhyme.viola', commands=commands,
                         geometry_scope='picture-in-picture',
                         default_width=480, default_height=320,
                         minimum_width=240, minimum_height=160)
        # Creating a utility window must not replace the browser identity menu.
        if menu is not None:
            application.set_menubar(menu)
        self.set_transient_for(host)
        self.set_modal(False)
        self.picture = Gtk.Picture(hexpand=True, vexpand=True, can_shrink=True,
                                   content_fit=Gtk.ContentFit.CONTAIN)
        self.body.append(self.picture)
        self.connect('close-request', self.close_requested)

    def update(self, paintable, tab_id):
        if tab_id == self.tab_id:
            self.picture.set_paintable(paintable)

    def close_requested(self, *_):
        if self.host.pip_window is self:
            self.host.pip_window = None
            self.host.send('media:pip')
        self.picture.set_paintable(None)
        return False

    def dismiss(self):
        if self.host.pip_window is self:
            self.host.pip_window = None
        self.picture.set_paintable(None)
        self.destroy()
