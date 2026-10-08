# SPDX-License-Identifier: GPL-3.0-only
"""Compact presentation over the existing Chromium page and Luma widgets."""
import math
import time
from gi.repository import GLib, Gtk
from native_sidebar import reconcile


class NativeMini:
    def __init__(self, window):
        self.window = window
        self.idle = MiniIdle(window)
        window.add_css_class('viola-mini')
        # AppKit owns the titlebar, identity, real window controls and shadows.
        # A separate geometry scope keeps authentication sizing out of browsing.
        self.expand = window.responsive.expand
        for widget in (window.sidebar_bin, window.sidebar, window.toggle,
                       window.bookmark, window.new_tab,
                       window.sidebar_layout.handle, window.sidebar_layout.edge,
                       window.sidebar_layout.reveal):
            widget.set_visible(False)
        window.address.set_placeholder_text('Search or enter address')
        # Permissions are contextual, not a permanent notification/sidebar area.
        self.permission = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.permission.set_visible(False)
        window.island.insert_child_after(self.permission, window.toolbar)

    def render(self, state):
        window = self.window
        self.idle.update(state)
        title = 'Developer tools' if getattr(window, 'developer_tools', False) else state.get('activeTitle') or 'Mini Viola'
        window.set_identity_subtitle(title[:160])
        window.sidebar.footer.state = state
        prompt = state.get('permissionPrompt')
        reconcile(self.permission, [window.sidebar.footer.card(prompt)] if prompt else [])
        self.permission.set_visible(bool(prompt))


class MiniIdle:
    """Honor the existing retention preference without closing a focused flow."""
    def __init__(self, window):
        self.window = window
        self.hours = 6
        self.last_activity = time.monotonic()
        self.url = None
        self.timer = GLib.timeout_add_seconds(60, self.tick)
        window.connect('notify::is-active', self.touch)

    def touch(self, *_):
        self.last_activity = time.monotonic()

    def update(self, state):
        hours = (state.get('settings') or {}).get('miniViolaAutoCloseHours', 6)
        self.hours = max(0,min(168,hours)) if isinstance(hours,(int,float)) and not isinstance(hours,bool) and math.isfinite(hours) else 6
        url = state.get('activeUrl')
        if url != self.url:
            self.url = url
            self.touch()

    def tick(self):
        if self.window.is_active() or self.window.get_visible_dialog() is not None:
            self.touch()
        elif self.hours and time.monotonic()-self.last_activity >= self.hours*3600:
            self.timer = 0
            self.window.close()
            return False
        return True

    def close(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
