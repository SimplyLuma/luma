# SPDX-License-Identifier: GPL-3.0-only
"""Mapped native GTK resizing with real Chromium frames on the private QA profile."""
import time
from gi.repository import GLib, Gtk
from capture_native_widget import capture


class ResponsiveQualification:
    def __init__(self, window, output, complete):
        self.window, self.output, self.complete = window, output, complete
        self.report = {'classification': 'real native30 Chromium and mapped GTK, private profile', 'checks': []}
        self.widths = iter((1180, 720, 500, 360, 1180))
        self.started = time.monotonic()
        self.page = window.page
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
            assert w.toolbar.get_mapped() != phone
            assert w.responsive.center.bar.get_mapped() == phone
            assert not phone or not w.sidebar_layout.fixed.get_mapped()
            assert phone or w.sidebar_layout.collapsed == self.saved_collapse
            ok, page = w.page.compute_bounds(w)
            assert ok and page.get_width() > 150 and page.get_height() > 300
            if phone:
                ok, bar = w.responsive.center.bar.compute_bounds(w)
                assert ok and bar.get_x() >= 0 and bar.get_x() + bar.get_width() <= w.get_width() + 1
                w.responsive.show_tabs()
                GLib.timeout_add(300, self.check_panel)
            else:
                self.record()
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
            w.responsive.focus_address()
            GLib.timeout_add(500, self.check_address)
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
            capture(w, self.output / f'address-{self.width}.png')
            # Navigate through the same engine-owned classifier as desktop Return.
            w.responsive.navigate(search.text)
            GLib.timeout_add(500, self.record)
        except Exception as error:
            return self.fail(error)
        return GLib.SOURCE_REMOVE

    def record(self):
        w = self.window
        try:
            capture(w, self.output / f'responsive-{len(self.report["checks"])}-{self.width}.png')
        except Exception as error:
            return self.fail(error)
        self.report['checks'].append({'width': self.width, 'phone': w.phone,
            'page_width': w.page.get_width(), 'page_height': w.page.get_height(),
            'real_frame': dict(w.page.displayed_frame or {}), 'same_page_owner': w.page is self.page})
        self.advance()
