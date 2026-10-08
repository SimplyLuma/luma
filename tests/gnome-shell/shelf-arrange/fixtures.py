#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Oracle fixtures for movable islands: a stand-in Semantic Broker publishing a
timer Live Extension (the live island) and a playing MPRIS player with art
(the media island). Adapted from tests/gnome-shell/live-modes/broker.py and
now-playing-island/chrome-mpris.py."""
import datetime, math, os, struct, zlib
from gi.repository import Gio, GLib
bus = Gio.bus_get_sync(Gio.BusType.SESSION)
S = lambda v: GLib.Variant('s', v)
B = lambda v: GLib.Variant('b', v)
now = datetime.datetime.now(datetime.timezone.utc)
iso = lambda d: d.isoformat()

BROKER = """<node><interface name="org.projectluma.SemanticBroker1">
<method name="ListLiveExtensions"><arg type="aa{sv}" direction="out"/></method>
<signal name="LiveExtensionsChanged"/></interface></node>"""
ext = {'id': S('timer.tea'), 'app_id': S('org.projectluma.Clock'), 'category': S('timer'),
       'title': S('Tea'), 'subtitle': S('4:12 left'), 'schema_version': S('0.1'), 'privacy': S('private'),
       'progress': GLib.Variant('d', -1.0), 'starts_at': S(iso(now - datetime.timedelta(minutes=1))),
       'expires_at': S(iso(now + datetime.timedelta(hours=2)))}
item = {'application_id': S('org.projectluma.Clock'), 'publication_id': S('p-timer.tea'),
        'extension': GLib.Variant('a{sv}', ext)}
if os.environ.get('SA_LIVE', '1') == '1':
    bus.register_object('/org/projectluma/SemanticBroker1', Gio.DBusNodeInfo.new_for_xml(BROKER).interfaces[0],
                        lambda c, s, p, i, m, params, inv: inv.return_value(GLib.Variant('(aa{sv})', ([item],))), None, None)
    Gio.bus_own_name_on_connection(bus, 'org.projectluma.SemanticBroker1', 0, None, None)
    # Say so, for a Shell that is already running.
    GLib.timeout_add(800, lambda: bus.emit_signal(None, '/org/projectluma/SemanticBroker1',
        'org.projectluma.SemanticBroker1', 'LiveExtensionsChanged', None) and False)

def png(path, w, h):
    rows = b''
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            t, u = x / w, y / h
            r, g, b = int(40 + 180 * t), int(60 + 90 * (1 - u)), int(150 + 80 * math.sin(3 * t + u))
            if (x - w * 0.5) ** 2 + (y - h * 0.55) ** 2 < (h * 0.28) ** 2:
                r, g, b = 245, 200, 90
            row += bytes([r, g, max(0, min(255, b))])
        rows += bytes(row)
    chunk = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    open(path, 'wb').write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
                           chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))

if os.environ.get('SA_MEDIA', '1') == '1':
    art = os.path.join(os.environ['HOME'], 'art.png')
    png(art, 256, 256)
    MPRIS = """<node><interface name="org.mpris.MediaPlayer2"><property name="Identity" type="s" access="read"/>
<property name="DesktopEntry" type="s" access="read"/><property name="CanRaise" type="b" access="read"/></interface>
<interface name="org.mpris.MediaPlayer2.Player"><property name="PlaybackStatus" type="s" access="read"/>
<property name="Metadata" type="a{sv}" access="read"/><property name="CanGoNext" type="b" access="read"/>
<property name="CanGoPrevious" type="b" access="read"/><property name="CanPlay" type="b" access="read"/>
<property name="CanPause" type="b" access="read"/><property name="CanControl" type="b" access="read"/>
<property name="Position" type="x" access="read"/>
<method name="PlayPause"/><method name="Next"/><method name="Previous"/></interface></node>"""
    values = {'Identity': S('Tide'), 'DesktopEntry': S('org.projectluma.Tide'), 'CanRaise': B(True),
              'PlaybackStatus': S('Playing'), 'CanGoNext': B(True), 'CanGoPrevious': B(True),
              'CanPlay': B(True), 'CanPause': B(True), 'CanControl': B(True), 'Position': GLib.Variant('x', 0),
              'Metadata': GLib.Variant('a{sv}', {'mpris:trackid': GLib.Variant('o', '/t/1'), 'xesam:title': S('Harvest Moon'),
                  'xesam:artist': GLib.Variant('as', ['Neil Young']), 'mpris:artUrl': S('file://' + art)})}
    for iface in Gio.DBusNodeInfo.new_for_xml(MPRIS).interfaces:
        bus.register_object('/org/mpris/MediaPlayer2', iface, lambda *a: a[-1].return_value(None),
                            lambda conn, sender, path, iface, name: values.get(name), None)
    Gio.bus_own_name_on_connection(bus, 'org.mpris.MediaPlayer2.tide', 0, None, None)
print('fixtures up', flush=True)
GLib.MainLoop().run()
