#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Camera fixture composition test; run only on a private headless display."""
import hashlib
import os
from pathlib import Path
import time
from unittest.mock import patch

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk
Gtk.init()
Adw.init()
if os.environ.get('LUMA_CAMERA_TEST_HIGH_CONTRAST') == '1':
    assert os.environ.get('GSETTINGS_BACKEND') == 'memory', 'contrast test needs a private settings backend'
    Gio.Settings.new('org.gnome.desktop.a11y.interface').set_boolean('high-contrast', True)
    Gtk.Settings.get_default().set_property('gtk-theme-name', 'HighContrast')
from prairie_apps import camera

ROOT = Path(__file__).resolve().parents[2]
fixture = ROOT / 'tests/fixtures/camera-v70.json'
os.environ['LUMA_CAMERA_FIXTURE'] = str(fixture)
os.environ['LUMA_CAMERA_STYLE_PATH'] = str(ROOT / 'src/prairie-core/style/camera.css')


def settle(milliseconds=350):
    end = time.monotonic() + milliseconds / 1000
    context = GLib.MainContext.default()
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(.005)


def node(widget):
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(widget).snapshot(snapshot, widget.get_width(), widget.get_height())
    return snapshot.to_node()


before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in fixture.parent.joinpath('camera-v70').iterdir()}
if os.environ.get('LUMA_CAMERA_TEST_HIGH_CONTRAST') == '1':
    settle()
    assert Adw.StyleManager.get_default().get_high_contrast(), 'high contrast was not applied'
with patch.object(camera, 'list_camera_devices', side_effect=AssertionError('fixture acquired a camera')), \
     patch.object(camera, 'PhotoLibrary', side_effect=AssertionError('fixture opened Photos')), \
     patch.object(camera, 'VideoRecording', side_effect=AssertionError('fixture opened an encoder')):
    app = camera.CameraApplication()
    app.register(None)
    assert set(app.get_accels_for_action('app.capture')) == {'space', 'Return'}
    window = camera.CameraWindow(app)
    assert window._geometry_app_id == app.get_application_id()
    window.present()
    settle()
    surface = window._surface
    assert abs(window._picture.get_width() / window._picture.get_height() - 4 / 3) < .02, (
        'initial camera window letterboxes the 4:3 preview',
        (window._picture.get_width(), window._picture.get_height()))
    zoom = surface.zoom_box.get_first_child()
    assert zoom.has_css_class('camera-zoom-modes')
    assert abs(zoom.indicator.get_height() - zoom.get_height()) <= 1, (
        'selected zoom does not fill its tab height', zoom.indicator.get_height(), zoom.get_height())
    assert surface.ev_scale.get_value() == surface.values['ev'] == 0
    assert not surface.strip.get_visible(), 'empty session strip is visible'
    paintable = window._picture.get_paintable()
    assert paintable is not None, 'fixture viewfinder has no paintable'
    assert paintable.get_intrinsic_width() == 1672
    assert node(window._picture) is not None, 'viewfinder did not paint'
    shutter_snapshot = Gtk.Snapshot()
    window._capture_button.do_snapshot(shutter_snapshot)
    shutter_bounds = shutter_snapshot.to_node().get_bounds()
    window._capture_button.set_state_flags(Gtk.StateFlags.ACTIVE, False)
    settle()
    # WidgetPaintable preserves the allocation clip; measure the painted node.
    pressed_snapshot = Gtk.Snapshot()
    window._capture_button.do_snapshot(pressed_snapshot)
    pressed_bounds = pressed_snapshot.to_node().get_bounds()
    assert abs(pressed_bounds.get_width() - shutter_bounds.get_width() * .94) < .5
    assert abs(pressed_bounds.get_height() - shutter_bounds.get_height() * .94) < .5
    window._capture_button.unset_state_flags(Gtk.StateFlags.ACTIVE)
    settle()
    shutter_states = []
    def draw_shutter(area, cr, width, height):
        shutter_states.append(window.state.recording)
        window._capture_button.draw(area, cr, width, height)
    window._capture_button.get_child().set_draw_func(draw_shutter)
    settle()
    assert all(not button.label_widget.get_layout().is_ellipsized()
               for button in surface.zoom_buttons.values()), ('zoom labels are clipped',
               surface.zoom_box.get_width(), surface.zoom_box.measure(Gtk.Orientation.HORIZONTAL, -1),
               [(button.get_width(), button.label_widget.get_width(),
                 button.measure(Gtk.Orientation.HORIZONTAL, -1)) for button in surface.zoom_buttons.values()])
    surface.cameras()
    next(button for button in surface.menu.buttons if button.get_name() == 'cm-source-phone').emit('clicked')
    settle()
    assert window._fixture.camera.id == 'phone' and surface.values['fmt'] == 'raw'
    assert window.state.keep_raw
    surface.cameras()
    next(button for button in surface.menu.buttons if button.get_name() == 'cm-source-front').emit('clicked')
    settle()
    assert window._fixture.camera.id == 'front' and surface.values['fmt'] == 'jpeg'
    assert not window.state.keep_raw
    surface.cameras()
    next(button for button in surface.menu.buttons if button.get_name() == 'cm-source-usb').emit('clicked')
    settle()
    assert window._fixture.camera.id == 'usb'
    surface.settings()
    settle()
    manual = next(button for button in surface.menu.buttons if button.get_name() == 'cm-manual')
    assert manual.switch is not None and not manual.switch.get_active()
    manual.emit('clicked')
    settle()
    assert surface.values['pro'] and surface.manual.get_visible()
    assert not surface.menu.is_open, 'manual toggle did not dismiss the menu'
    assert manual.switch.get_active()
    surface.menu.close()
    surface.settings()
    selected_format = next(button for button in surface.menu.buttons if button.get_name() == 'cm-format-jpeg')
    selected_format.emit('clicked')
    settle()
    assert surface.values['fmt'] == 'jpeg' and not window.state.keep_raw
    assert surface.menu.is_open, 'format selection dismissed the menu'
    assert next(button for button in surface.menu.buttons if button.get_name() == 'cm-format-jpeg').item.checked
    surface.menu.close()
    surface.aspects()
    aspect = next(button for button in surface.menu.buttons if button.get_name() == 'cm-aspect-16-9')
    aspect.emit('clicked')
    settle()
    assert surface.values['aspect'] == '16:9' and surface.menu.is_open
    assert abs(window._picture.get_width() / window._picture.get_height() - 16 / 9) < .02, (
        'window kept black letterbox around 16:9 photo',
        window.get_width(), window.get_height(),
        window._picture.get_width(), window._picture.get_height())
    surface.menu.close()
    surface.choose_aspect('1:1')
    settle()
    assert abs(window._picture.get_width() / window._picture.get_height() - 1) < .02, (
        'window kept black letterbox around square photo',
        window.get_width(), window.get_height(),
        window._picture.get_width(), window._picture.get_height())
    surface.menu.close()
    surface.choose_aspect('16:9')
    settle()
    surface.menu.close()
    padding = surface.manual.get_style_context().get_padding()
    assert surface.manual.get_width() + padding.left + padding.right == 64, 'manual caption widens its dial'
    for dial_button in (child for child in surface.descendants(surface.manual)
                        if isinstance(child, Gtk.Button) and child.get_name().startswith('cm-dial-')):
        _, dial_bounds = dial_button.compute_bounds(window)
        _, words_bounds = dial_button.get_child().compute_bounds(window)
        assert abs(words_bounds.get_y() + words_bounds.get_height() / 2
                   - dial_bounds.get_y() - dial_bounds.get_height() / 2) <= 2, 'manual dial text is off center'
    surface.dial('wb')
    settle(700)
    dial = next(child for child in surface.descendants(surface.manual) if child.get_name() == 'cm-dial-wb')
    _, menu_bounds = surface.menu.compute_bounds(window)
    _, dial_bounds = dial.compute_bounds(window)
    assert abs(menu_bounds.get_y() + menu_bounds.get_height() / 2
               - dial_bounds.get_y() - dial_bounds.get_height() / 2) <= 2, ('dial menu is off center',
               (menu_bounds.get_y(), menu_bounds.get_height()), (dial_bounds.get_y(), dial_bounds.get_height()),
               (window.get_width(), window.get_height()), (window._picture.get_width(), window._picture.get_height()))
    surface.menu.close()
    surface.set_value('pro', False)
    surface.set_mode('portrait')
    settle()
    assert node(window._picture) is not None, 'portrait mask did not paint'
    surface.set_mode('scan')
    settle()
    assert node(window._grid_overlay) is not None, 'document outline did not paint'
    surface.focus(None, 1, window._picture.get_width() / 2, window._picture.get_height() / 2)
    settle()
    assert window._focus_reticle.get_visible() and surface.ev.get_visible()
    _, picture_bounds = window._picture.compute_bounds(window)
    _, sun_bounds = surface.ev.get_first_child().compute_bounds(window)
    # v70's rotated 96px input sets the exposure grid's implicit column width.
    focus_x = picture_bounds.get_x() + picture_bounds.get_width() / 2
    assert abs(sun_bounds.get_x() + sun_bounds.get_width() / 2 - focus_x - 96) <= 2
    surface.ev_scale.set_value(.5)
    assert surface.values['ev'] == .5
    settle(2600)
    assert abs(window._focus_reticle.get_opacity() - .45) < .01
    surface.focus(None, 1, window._picture.get_width() / 2, window._picture.get_height() / 2)
    assert surface.values['ev'] == 0 and surface.ev_scale.get_value() == 0
    assert window._focus_reticle.get_opacity() == 1
    surface.set_mode('photo')
    window.state.timer = 3
    window._on_capture()
    assert surface.countdown.get_visible() and surface.countdown.get_label() == '3'
    settle(1100)
    assert surface.countdown.get_label() == '2' and not window.state.roll
    settle(2100)
    assert len(window.state.roll) == 1
    assert not surface.countdown.get_visible()
    window.state.timer = 0
    surface.session()
    settle()
    assert surface.strip.get_visible()
    padding = window._last_shot_button.get_style_context().get_padding()
    assert window._last_shot_button.get_width() + padding.left + padding.right == 44
    padding = surface.strip.get_style_context().get_padding()
    assert surface.strip.get_height() + padding.top + padding.bottom == 44
    surface.modes()
    settle()
    assert surface.menu.is_open
    surface.menu.close()
    surface.set_mode('video')
    settle()
    assert surface.shape.get_tooltip_text() == 'Video format'
    assert abs(window._picture.get_width() / window._picture.get_height() - surface.active_frame_ratio()) < .02, (
        'video mode kept the photo crop instead of fitting the active preview',
        window._picture.get_width(), window._picture.get_height(), surface.active_frame_ratio())
    assert shutter_states[-1] is False
    window._on_capture()
    settle(1100)
    assert window.state.recording and window.state.elapsed >= 1
    assert shutter_states[-1] is True, 'recording did not repaint the stop shutter'
    window._on_capture()
    settle()
    assert not window.state.recording and len(window.state.roll) == 2
    assert shutter_states[-1] is False, 'stopping did not repaint the record shutter'
    surface.set_mode('time')
    assert surface.shape.get_tooltip_text() == 'Video format'
    surface.set_mode('photo')
    assert surface.shape.get_tooltip_text() == 'Aspect Ratio'
    window.close()
    settle()
    phone = camera.CameraWindow(app)
    phone.set_default_size(390, 820)
    phone.present()
    settle()
    assert phone._surface.phone, phone.get_width()
    assert not phone._surface.bar.get_visible(), 'desktop action bar remained over phone controls'
    assert phone._surface.shape.get_mapped(), 'phone top row lost its format control'
    assert not phone._grid_button.get_mapped(), 'desktop grid action leaked into phone controls'
    phone._surface.settings()
    settle()
    assert phone._surface.menu.is_open
    assert any(isinstance(child, Gtk.Label) and child.get_label() == 'Photo format'
               and child.get_mapped() for child in phone._surface.descendants(phone)), 'phone menu lost its section heading'
    def phone_action(name):
        matches = [child for child in phone._surface.descendants(phone)
                   if isinstance(child, Gtk.Button) and child.get_mapped() and child.get_name() == name]
        assert len(matches) == 1, (name, len(matches))
        matches[0].emit('clicked')
        settle()
    phone_action('cm-format-jpeg')
    settle()
    assert phone._surface.values['fmt'] == 'jpeg' and phone._surface.menu.is_open
    phone._surface.menu.close()
    settle()
    phone._surface.cameras()
    settle()
    phone_action('cm-source-phone')
    assert phone._fixture.camera.id == 'phone'
    assert set(phone._surface.zoom_buttons) == {.5, 1, 2, 5}
    phone._surface.cameras()
    settle()
    phone_action('cm-source-front')
    assert phone._fixture.camera.id == 'front'
    phone._surface.aspects()
    settle()
    phone_action('cm-aspect-16-9')
    assert phone._surface.values['aspect'] == '16:9' and phone._surface.menu.is_open
    phone._surface.menu.close()
    settle()
    phone.close()
    settle()
    for width in (360, 500, 1024, 1180):
        responsive = camera.CameraWindow(app)
        responsive.set_default_size(width, 820)
        responsive.present()
        settle()
        if width < 640:
            # Native CSD insets are outside the compact layout width.
            inset = width - responsive.get_width()
            if inset:
                responsive.set_default_size(width + inset, 820)
                settle()
            assert abs(responsive.get_width() - width) <= 2, (width, responsive.get_width())
        else:
            assert abs(responsive._picture.get_width() / responsive._picture.get_height() - 4 / 3) < .02, (
                'desktop resize letterboxes the preview', width,
                responsive._picture.get_width(), responsive._picture.get_height())
            if width == 1024:
                before_resize = responsive._picture.get_width()
                responsive.set_default_size(responsive.get_width() + 80, responsive.get_height())
                settle(700)
                assert responsive._picture.get_width() > before_resize + 50, (
                    'manual width resize did not grow the viewfinder',
                    before_resize, responsive._picture.get_width())
                assert abs(responsive._picture.get_width() / responsive._picture.get_height() - 4 / 3) < .02, (
                    'manual width resize left letterboxing',
                    responsive._picture.get_width(), responsive._picture.get_height())
                before_resize = responsive._picture.get_height()
                responsive.set_default_size(responsive.get_width(), responsive.get_height() + 70)
                settle(700)
                assert responsive._picture.get_height() > before_resize + 40, (
                    'manual height resize did not grow the viewfinder',
                    before_resize, responsive._picture.get_height())
                assert abs(responsive._picture.get_width() / responsive._picture.get_height() - 4 / 3) < .02, (
                    'manual height resize left letterboxing',
                    responsive._picture.get_width(), responsive._picture.get_height())
                before_resize = responsive._picture.get_width()
                responsive.set_default_size(responsive.get_width() + 90, responsive.get_height() + 25)
                settle(700)
                assert responsive._picture.get_width() > before_resize + 55, (
                    'manual corner resize did not grow the viewfinder',
                    before_resize, responsive._picture.get_width())
                assert abs(responsive._picture.get_width() / responsive._picture.get_height() - 4 / 3) < .02, (
                    'manual corner resize left letterboxing',
                    responsive._picture.get_width(), responsive._picture.get_height())
        assert responsive._surface.phone is (width < 640)
        assert responsive._surface.shape.get_mapped(), 'format action disappeared while resizing'
        assert responsive._grid_button.get_mapped() is (width >= 640)
        for control in (responsive._capture_button, responsive._settings_button):
            valid, bounds = control.compute_bounds(responsive)
            assert valid and control.get_mapped()
            assert bounds.get_x() >= 0 and bounds.get_x() + bounds.get_width() <= responsive.get_width() + .5
        responsive.close()
        settle()
    # The package empty-state smoke also exercises this native discovery path.
    # Keep the same assertions isolated from other apps' package checks.
    with patch.dict(os.environ), patch.object(camera, 'list_camera_devices', return_value=[]) as discovery:
        os.environ.pop('LUMA_CAMERA_FIXTURE', None)
        empty_window = camera.CameraWindow(app)
        empty_window.present()
        settle()
        def empty_camera():
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                settle(10)
                found = [child for child in surface.descendants(empty_window)
                         if isinstance(child, camera.EmptyState)
                         and child.heading.get_text() == 'No camera found']
                if found:
                    return found[0]
            raise AssertionError('native Camera discovery did not show No camera found')
        empty = empty_camera()
        for action in ('switch-camera', 'refocus', 'grid', 'capture'):
            app.activate_action(action, None)
        empty_window.state.mode = 'video'
        app.activate_action('capture', None)
        assert not empty_window.state.recording
        empty.primary_button.emit('clicked')
        empty_camera()
        assert discovery.call_count == 2, 'native Camera retry did not rediscover'
        empty_window.close()
        settle()
    app.quit()
assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}
print('Camera v70 fixture runtime: PASS', flush=True)
