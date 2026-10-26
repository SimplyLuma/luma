# SPDX-License-Identifier: Apache-2.0
"""Unchanged Weather results retain actual widgets and keyboard focus."""
from dataclasses import replace
from pathlib import Path
import os
import sys
import time
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import GLib, Gtk

source = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(source/'src/prairie-core'))
os.environ['LUMA_WEATHER_FIXTURE'] = str(source/'tests/fixtures/weather-v70.json')
os.environ['LUMA_WEATHER_STYLE_PATH'] = str(source/'src/prairie-core/style/weather.css')
from prairie_apps.weather import WeatherApplication, WeatherWindow
errors=[]
def exception(kind, value, tb):
    errors.append(str(value));sys.__excepthook__(kind, value, tb)
sys.excepthook=exception

def pump(seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        for _ in range(100):
            if not GLib.MainContext.default().pending():break
            GLib.MainContext.default().iteration(False)
        time.sleep(.003)

app=WeatherApplication();assert app.register(None)
window=WeatherWindow(app);window.present();pump(1)
assert window.snapshots
uid=window.selected;snapshot=window.snapshots[uid]
original=window._render_sidebar
count=[]
def render():
    count.append(True);original()
window._render_sidebar=render
row=window.sidebar.list.get_row_at_index(0)
window.foot.entry.grab_focus();pump(.3)
focus=window.get_focus()
for _ in range(30):
    window._refreshed(window.generation,uid,replace(snapshot))
pump(.2)
assert not count, len(count)
assert row is window.sidebar.list.get_row_at_index(0)
assert window.get_focus() is focus
print('PASS 30 identical results retain rows and keyboard focus',flush=True)
window._refreshed(window.generation,uid,replace(snapshot,temperature='76°'))
pump(.3)
assert len(count)==1
assert window.snapshots[uid].temperature=='76°'
print('PASS changed forecast still updates rendered content',flush=True)
window.close();pump(.2)
assert not errors,errors
