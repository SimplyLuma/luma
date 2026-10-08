# SPDX-License-Identifier: Apache-2.0
"""Mapped mini-player geometry/actions and ordinary head padding restoration."""
import time
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk
from luma_appkit import ActionCenter, BarAction, MediaMiniPlayer, install_appkit, install_lumaui


def settle():
    end = time.monotonic() + .3
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


def main():
    app = Adw.Application(application_id='org.projectluma.MediaMiniTest', flags=Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    install_appkit(); install_lumaui()
    window = Gtk.ApplicationWindow(application=app, default_width=360, default_height=500)
    window.add_css_class('luma-app-window')
    calls = []
    mini = MediaMiniPlayer(on_open=lambda: calls.append('open'), on_play=lambda: calls.append('play'),
                           on_next=lambda: calls.append('next'))
    mini.set_artwork(Gtk.Box(width_request=44, height_request=44))
    mini.update('An exceptionally long song title that must ellipsize', 'A long artist name', fraction=.25)
    center = ActionCenter()
    window.set_child(center)
    center.show_bar([BarAction('search', tooltip='Search')], head=mini, head_inset=False, fill=True)
    window.present(); settle()
    for width in (360, 402, 720, 1180):
        window.set_default_size(width, 500); settle()
        assert window.get_width() == width
        assert mini.get_height() == 60, mini.get_height()
        assert mini.open.get_height() == 52, mini.open.get_height()
        assert mini.progress.get_height() == 2, mini.progress.get_height()
        for control in (mini.open, mini.play, mini.next, mini.progress):
            ok, bounds = control.compute_bounds(window)
            assert ok and bounds.get_x() >= 0 and bounds.get_x() + bounds.get_width() <= width
        center.grow('test', Gtk.Label(label='Output choices')); settle()
        assert mini.get_mapped() and mini.progress.get_height() == 2
        center.fold(); settle()
    for control in (mini.open, mini.play, mini.next):
        control.emit('clicked')
    assert calls == ['open', 'play', 'next'], calls
    mini.update('Other song', 'Other artist', playing=True, fraction=.75); settle()
    assert mini.play.get_tooltip_text() == 'Pause' and mini.progress.fraction == .75
    ordinary = Gtk.Box(height_request=20)
    center.show_bar([BarAction('search', tooltip='Search')], head=ordinary); settle()
    assert not center.head_row.has_css_class('self-padded')
    assert center.head_row.get_style_context().get_padding().bottom == 8
    window.close()
    print('MediaMiniPlayer geometry/actions, grown panels and default head reset PASS')


if __name__ == '__main__':
    main()
