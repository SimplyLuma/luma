# SPDX-License-Identifier: GPL-3.0-only
"""Mapped native GTK resizing with real Chromium frames on the private QA profile."""
import time
from gi.repository import GLib, Gtk
from capture_native_widget import capture


class ResponsiveQualification:
    def __init__(self, window, output, complete):
        self.window, self.output, self.complete = window, output, complete
        self.report = {'classification': 'real Chromium and mapped GTK, private profile', 'checks': []}
        self.widths = iter((1180, 720, 500, 360, 1180))
        self.started = time.monotonic()
        self.page = window.page
        # Subscribe before the frame: a newly created WidgetPaintable can be
        # empty until GTK publishes its next render node.
        self.paintable = Gtk.WidgetPaintable.new(window)
        self.saved_collapse = window.sidebar_layout.collapsed
        GLib.timeout_add(200, self.ready)

    def fail(self, error):
        self.report['error'] = str(error)
        self.complete(self.report)
        return GLib.SOURCE_REMOVE

    def ready(self):
        if not self.window.state or self.window.page.texture is None:
            if time.monotonic() - self.started > 20:
                return self.fail('Chromium did not deliver a live page frame')
            return GLib.SOURCE_CONTINUE
        self.advance()
        return GLib.SOURCE_REMOVE

    def advance(self):
        self.width = next(self.widths, None)
        if self.width is None:
            self.report['completed'] = True
            self.complete(self.report)
            return
        self.window.set_default_size(self.width, 874)
        GLib.timeout_add(1800, self.check)

    def check(self):
        try:
            w = self.window
            assert abs(w.get_width() - self.width) <= 1, (self.width, w.get_width())
            assert w.page is self.page and w.page.texture is not None
            phone = self.width < 560
            assert w.phone == phone
            assert not w.toolbar.get_mapped()
            assert not w.toggle.get_mapped(), 'removed title-row sidebar control is mapped'
            assert w.sidebar.workspace_well.has_css_class('recessed')
            assert w.responsive.center.bar.get_mapped()
            assert self.width >= 900 or not w.sidebar_layout.fixed.get_mapped()
            assert phone or w.sidebar_layout.collapsed == self.saved_collapse
            ok, page = w.page.compute_bounds(w)
            assert ok and page.get_width() > 150 and page.get_height() > 300
            if phone:
                ok, bar = w.responsive.center.bar.compute_bounds(w)
                assert ok and bar.get_x() >= 0 and bar.get_x() + bar.get_width() <= w.get_width() + 1
                w.responsive.show_tabs()
                GLib.timeout_add(300, self.check_panel)
            else:
                assert w.address.get_mapped(), 'desktop address is not mapped'
                w.page.grab_focus()
                w.responsive.page_scrolled(0, 40)
                assert not w.responsive.center.bar.get_visible(), 'page bar did not retreat on downward scroll'
                w.responsive.page_scrolled(0, -40)
                assert w.responsive.center.bar.get_visible(), 'upward scroll did not reveal page bar'
                w.responsive.page_scrolled(0, 40)
                w._focus_address()
                assert w.responsive.center.bar.get_visible(), 'Ctrl+L did not reveal page bar'
                ok, bar = w.responsive.center.bar.compute_bounds(w)
                assert ok and bar.get_y() > w.get_height() / 2
                assert bar.get_x() >= page.get_x() and bar.get_x() + bar.get_width() <= page.get_x() + page.get_width() + 1
                w._focus_address()
                GLib.timeout_add(400, self.check_desktop_address)
        except Exception as error:
            return self.fail(error)
        return GLib.SOURCE_REMOVE

    def check_desktop_address(self):
        try:
            w = self.window
            assert w.address_focused() and w.address.get_mapped()
            assert w.address.get_text() == (w.state or {}).get('activeUrl', '')
            w.address_suggestions.close()
            w.page.grab_focus()
            w.responsive.show_downloads()
            GLib.timeout_add(300, self.check_desktop_panel)
        except Exception as error:
            return self.fail(error)
        return GLib.SOURCE_REMOVE

    def check_desktop_panel(self):
        try:
            w = self.window
            assert w.responsive.panel == 'downloads'
            w.responsive.center.fold()
            w.responsive.panel = None
            w.responsive.show_share()
            GLib.timeout_add(350, self.check_share)
        except Exception as error:
            return self.fail(error)
        return GLib.SOURCE_REMOVE

    def check_share(self):
        try:
            w = self.window
            sheet = w.responsive.share_sheet
            assert sheet.get_mapped() and sheet.has_css_class('lumaui-share')
            assert sheet.document.subtitle == w.state['activeUrl']
            capture(w, self.output / f'share-{self.width}.png', paintable=self.paintable)
            sheet.action_buttons['copy-link'].emit('clicked')
            self.share_url = sheet.document.subtitle
            w.get_display().get_clipboard().read_text_async(None, self.clipboard_ready)
        except Exception as error:
            return self.fail(error)
        return GLib.SOURCE_REMOVE

    def clipboard_ready(self, clipboard, result):
        try:
            assert clipboard.read_text_finish(result) == self.share_url, 'Copy link did not copy the page URL'
            self.window.responsive.share_sheet.close()
            if self.width == 1180 and not getattr(self, 'pin_verified', False):
                state = self.window.state
                self.pin_tab = state['activeTabId']
                assert not any(t['id'] == self.pin_tab for t in state.get('favorites', []))
                self.pin_count = len(self.window.responsive.tabs())
                self.window.bookmark.emit('clicked')
                self.pin_deadline = time.monotonic() + 5
                GLib.timeout_add(100, self.check_pin)
            elif self.window.phone:
                self.window.responsive.focus_address()
                GLib.timeout_add(500, self.check_address)
            else:
                GLib.timeout_add(300, self.record)
        except Exception as error:
            self.fail(error)

    def check_pin(self):
        try:
            w = self.window
            if not any(t['id'] == self.pin_tab for t in w.state.get('favorites', [])):
                assert time.monotonic() < self.pin_deadline, 'Pin click did not reach Chromium favorites'
                return GLib.SOURCE_CONTINUE
            assert len(w.responsive.tabs()) == self.pin_count
            assert w.state['activeTabId'] == self.pin_tab
            row = w.sidebar.favorites[self.pin_tab]
            assert row.get_mapped() and row.has_css_class('lumaui-selected')
            assert row.has_css_class('lumaui-card')
            assert w.address_field.has_css_class('lumaui-bar-entry')
            assert row.get_width() < w.sidebar.get_width() / 2
            assert w.bookmark.get_child().get_icon_name() == 'lumaui-pin-symbolic'
            assert w.bookmark.get_tooltip_text() == 'Unpin from top'
            capture(w, self.output / 'pinned-1180.png', paintable=self.paintable)
            w.bookmark.emit('clicked')
            self.pin_deadline = time.monotonic() + 5
            GLib.timeout_add(100, self.check_unpin)
        except Exception as error:
            return self.fail(error)
        return GLib.SOURCE_REMOVE

    def check_unpin(self):
        try:
            w = self.window
            if any(t['id'] == self.pin_tab for t in w.state.get('favorites', [])):
                assert time.monotonic() < self.pin_deadline, 'Second pin click did not unpin'
                return GLib.SOURCE_CONTINUE
            assert len(w.responsive.tabs()) == self.pin_count
            assert w.state['activeTabId'] == self.pin_tab
            assert w.sidebar.rows[self.pin_tab].has_css_class('lumaui-selected')
            self.pin_verified = True
            self.report['pin_toggle_preserves_tab'] = True
            self.report['share_copies_actual_url'] = True
            GLib.timeout_add(300, self.record)
        except Exception as error:
            return self.fail(error)
        return GLib.SOURCE_REMOVE

    def check_panel(self):
        try:
            w = self.window
            ok, bounds = w.responsive.center.bar.compute_bounds(w)
            assert ok and bounds.get_width() <= self.width
            assert w.responsive.panel == 'tabs'
            w.responsive.show_more()
            assert w.responsive.panel == 'more'
            w.responsive.center.fold()
            w.responsive.panel = None
            w.responsive.show_share()
            GLib.timeout_add(350, self.check_share)
        except Exception as error:
            return self.fail(error)
        return GLib.SOURCE_REMOVE

    def check_address(self):
        try:
            w = self.window
            search = w.responsive.search
            field = w.get_focus()
            assert isinstance(field, Gtk.Editable) and field.get_mapped(), 'address has no mapped editable focus'
            assert field.get_text() == (w.state or {}).get('activeUrl', ''), 'address URL differs from engine state'
            capture(w, self.output / f'address-{self.width}.png', paintable=self.paintable)
            # Navigate through the same engine-owned classifier as desktop Return.
            w.responsive.navigate(search.text)
            GLib.timeout_add(500, self.record)
        except Exception as error:
            return self.fail(error)
        return GLib.SOURCE_REMOVE

    def record(self):
        w = self.window
        try:
            capture(w, self.output / f'responsive-{len(self.report["checks"])}-{self.width}.png', paintable=self.paintable)
        except Exception as error:
            return self.fail(error)
        self.report['checks'].append({'width': self.width, 'phone': w.phone,
            'page_width': w.page.get_width(), 'page_height': w.page.get_height(),
            'real_frame': dict(w.page.displayed_frame or {}), 'same_page_owner': w.page is self.page})
        self.advance()
