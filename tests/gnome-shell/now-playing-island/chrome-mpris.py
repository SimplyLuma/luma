"""A second MPRIS player shaped like a Chromium browser tab playing media."""
import os
from gi.repository import Gio, GLib
bus = Gio.bus_get_sync(Gio.BusType.SESSION)
S = lambda v: GLib.Variant('s', v)
B = lambda v: GLib.Variant('b', v)
art = os.path.join(os.environ['HOME'], 'art-16x9.png')
MPRIS = """<node><interface name="org.mpris.MediaPlayer2"><property name="Identity" type="s" access="read"/>
<property name="DesktopEntry" type="s" access="read"/><property name="CanRaise" type="b" access="read"/></interface>
<interface name="org.mpris.MediaPlayer2.Player"><property name="PlaybackStatus" type="s" access="read"/>
<property name="Metadata" type="a{sv}" access="read"/><property name="CanGoNext" type="b" access="read"/>
<property name="CanGoPrevious" type="b" access="read"/><property name="CanPlay" type="b" access="read"/>
<property name="CanPause" type="b" access="read"/><property name="CanControl" type="b" access="read"/>
<property name="CanSeek" type="b" access="read"/><property name="Position" type="x" access="read"/>
<method name="PlayPause"/><method name="Next"/><method name="Previous"/></interface></node>"""
values = {'Identity': S(os.environ.get('C_IDENTITY', 'Chromium')), 'DesktopEntry': S(os.environ.get('C_DESKTOP', 'chromium-browser')),
          'CanRaise': B(True), 'PlaybackStatus': S('Playing'), 'CanGoNext': B(False), 'CanGoPrevious': B(False),
          'CanPlay': B(True), 'CanPause': B(True), 'CanControl': B(True), 'CanSeek': B(True), 'Position': GLib.Variant('x', 12000000),
          'Metadata': GLib.Variant('a{sv}', {'mpris:trackid': GLib.Variant('o', '/org/chromium/MediaPlayer2/TrackList/TrackUID1'),
              'xesam:title': S('My Sweet Lord (2014 Remaster)'), 'xesam:artist': GLib.Variant('as', ['George Harrison - Topic']),
              'xesam:album': S(''), 'mpris:length': GLib.Variant('x', 280000000), 'mpris:artUrl': S('file://' + art)})}
info = Gio.DBusNodeInfo.new_for_xml(MPRIS)
for iface in info.interfaces:
    bus.register_object('/org/mpris/MediaPlayer2', iface, lambda *a: a[-1].return_value(None),
                        lambda conn, sender, path, iface, name: values.get(name), None)
NAME = 'org.mpris.MediaPlayer2.chromium.instance4242'
owner = [Gio.bus_own_name_on_connection(bus, NAME, 0, None, None)]
print('chrome player up', flush=True)
flaps = [float(x) for x in os.environ.get('C_FLAPS', '').split(',') if x]
def flap(i=0):
    if i >= len(flaps):
        print('flaps done', flush=True); return False
    Gio.bus_unown_name(owner[0]); print('gone', flaps[i], flush=True)
    def back():
        owner[0] = Gio.bus_own_name_on_connection(bus, NAME, 0, None, None); print('back', flush=True)
        GLib.timeout_add(int(float(os.environ.get('C_FLAP_EVERY', '3')) * 1000), flap, i + 1)
        return False
    GLib.timeout_add(int(flaps[i] * 1000), back)
    return False
if flaps:
    GLib.timeout_add(int(float(os.environ.get('C_FLAP_START', '12')) * 1000), flap)
GLib.MainLoop().run()
