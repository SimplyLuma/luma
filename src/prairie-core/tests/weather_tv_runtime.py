# SPDX-License-Identifier: Apache-2.0
"""Weather in TV posture: the TV presentation replaces the body, driven by the remote only."""
import os
import time
from pathlib import Path

root = Path(__file__).resolve().parents[3]
os.environ.update(LUMA_WEATHER_FIXTURE=str(root / 'tests/fixtures/weather-v70.json'),
                  LUMA_WEATHER_FIXTURE_MINUTE='640', LUMA_WEATHER_SELECTED='oak',
                  LUMA_WEATHER_STYLE_PATH=str(root / 'src/prairie-core/style/weather.css'),
                  LUMA_POSTURE='tv', LUMA_TV_IDLE_MS='600000', GSETTINGS_BACKEND='memory')
import gi  # noqa: E402
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
from prairie_apps.weather import WeatherApplication, WeatherWindow  # noqa: E402


def settle(seconds=.5):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


def key(w, keyval, state=0):
    w.tv_view.focus._key_pressed(None, keyval, 0, Gdk.ModifierType(state))
    settle(.15)


app = WeatherApplication()
app.set_application_id('org.projectluma.Weather.TvTest')
assert app.register(None)
w = WeatherWindow(app)
w.set_default_size(1920, 1080)
w.present()
settle(1.2)
tv = w.tv_view
assert tv is not None and tv.get_visible() and not w._desktop_body.get_visible(), 'TV posture shows the TV presentation'
assert abs(tv.unit - min(tv.get_width() / 1920, tv.get_height() / 1080)) < 1e-3
assert tv.focus.focused is tv.band, 'the band has the remote first'
assert tv.when.get_label() == 'Oakland · Now'
print('PASS opens on the band, now', flush=True)

key(w, Gdk.KEY_Right); key(w, Gdk.KEY_Right)
assert tv.hour == 2 and tv.when.get_label().startswith('Oakland · ') and 'Now' not in tv.when.get_label()
key(w, Gdk.KEY_Left); key(w, Gdk.KEY_Left); key(w, Gdk.KEY_Left)
assert tv.hour == 0, 'scrubbing stops at now'
print('PASS Left/Right scrub the hours and the screen follows', flush=True)

key(w, Gdk.KEY_Down)
assert tv.focus.focused is tv._cards[0], 'Down lands on the chosen day'
key(w, Gdk.KEY_Right); key(w, Gdk.KEY_Right)
assert tv.day == 2 and tv.hour == 12 and tv.band.hours[0].at.hour == 0, 'another day runs from midnight'
assert tv.when.get_label().endswith('12 PM')
key(w, Gdk.KEY_Up)
assert tv.focus.focused is tv.band
print('PASS ten days', flush=True)

key(w, Gdk.KEY_Up)
assert tv.focus.focused is tv._tabs[0], 'Up lands on the open place'
key(w, Gdk.KEY_Right)
assert w.selected == 'lis' and tv.when.get_label() == 'Lisbon · Now', 'moving along the places switches city'
key(w, Gdk.KEY_Return)
assert tv._sheet is not None and tv.focus.in_scope, 'OK on an open added place offers Remove'
key(w, Gdk.KEY_Escape)
assert tv._sheet is None and tv.focus.focused is tv._tabs[1], 'Back closes the sheet where it came from'
key(w, Gdk.KEY_Left)
key(w, Gdk.KEY_Return)
assert tv._sheet is None, 'your location cannot be removed'
print('PASS places', flush=True)

for _ in range(len(w.places)):
    key(w, Gdk.KEY_Right)
assert tv.focus.focused is tv._tabs[-1]
assert tv._tabs[tv._open_index()].has_css_class('tv-chip'), 'the open place steps back while Add has the focus'
key(w, Gdk.KEY_Return)
assert tv._sheet is not None and w.get_focus() is tv._field.text, 'Add opens straight into typing'
tv._field.text.set_text('new')
settle(1.0)
assert len(tv._row_widgets) == 1, 'the list filters as you type'
key(w, Gdk.KEY_Down)
assert tv.focus.focused is tv._row_widgets[0]
count = len(w.places)
key(w, Gdk.KEY_Return)
settle(1.0)
assert len(w.places) == count + 1 and w.places[-1].name == 'New York' and w.selected == w.places[-1].uid
assert tv._sheet is None and getattr(tv, '_tv_toast', None) is not None, 'OK adds the place with a toast'
print('PASS add a place', flush=True)

for _ in range(3):
    if tv.focus.group_of(tv.focus.focused) != 'places':
        key(w, Gdk.KEY_Up)
tv.focus.focus(tv._tabs[len(w.places) - 1])
key(w, Gdk.KEY_Return)
remove = next(item for item in tv.focus._scopes[-1].items if item.label.get_label() == 'Remove')
tv.focus.focus(remove)
key(w, Gdk.KEY_Return)
settle(.5)
assert len(w.places) == count and w.selected == w.places[-1].uid
print('PASS remove a place', flush=True)

key(w, Gdk.KEY_I, Gdk.ModifierType.SHIFT_MASK)
settle(1.5)
assert tv.idle.idle and tv.glance.get_opacity() > .9 and tv.hero.get_opacity() < .1
assert tv.bar.clock.get_mapped() and tv.bar.track.get_opacity() < .1, 'Glance keeps the clock'
labels = [c.get_label() for c in [tv.glance.get_first_child()] if isinstance(c, Gtk.Label)]
assert labels and labels[0] == w.places[-1].name, 'Glance names a place that is not home'
key(w, Gdk.KEY_Down)
settle(1.2)
assert not tv.idle.idle and tv.hero.get_opacity() > .9 and tv.focus.focused is tv.band, 'any key comes back'
print('PASS Glance', flush=True)

w.tv_posture = False
settle()
assert not tv.get_visible() and w._desktop_body.get_visible(), 'leaving TV posture shows the desktop body'
print('PASS TV off returns the desktop presentation', flush=True)
w.close()
settle()
app.quit()
