# SPDX-License-Identifier: GPL-3.0-only
"""Capture only the QA harness's private Mutter monitor via its ScreenCast API."""
import argparse
import json
import os
from pathlib import Path
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gio, GLib, Gst


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--focus-native-window', action='store_true')
    args = parser.parse_args()
    if (not os.environ.get('WAYLAND_DISPLAY', '').startswith('viola-qa-')
            or not os.environ.get('DBUS_SESSION_BUS_ADDRESS', '').startswith('unix:path=/tmp/viola-qa-bus-')):
        raise RuntimeError('This capture tool requires the owned private QA display and bus')
    device = None
    if args.focus_native_window:
        from private_input import PrivateInput
        device = PrivateInput()
        device.start()
    Gst.init(None)
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    service = 'org.gnome.Mutter.ScreenCast'
    def call(path, interface, method, parameters=None):
        result = connection.call_sync(service, path, interface, method, parameters, None,
                                      Gio.DBusCallFlags.NONE, 5000, None)
        return result.unpack() if result else None
    session = call('/org/gnome/Mutter/ScreenCast', service, 'CreateSession',
                   GLib.Variant('(a{sv})', ({},)))[0]
    stream = call(session, service + '.Session', 'RecordMonitor',
                  GLib.Variant('(sa{sv})', ('Meta-0', {'cursor-mode': GLib.Variant('u', 0)})))[0]
    loop = GLib.MainLoop()
    pipeline = None
    errors = []
    def bus_message(_bus, message):
        if message.type == Gst.MessageType.ERROR:
            error, detail = message.parse_error()
            errors.append(str(error) + ': ' + str(detail))
            loop.quit()
        elif message.type == Gst.MessageType.EOS:
            loop.quit()
    def stream_ready(_connection, _sender, _path, _interface, _signal, parameters):
        nonlocal pipeline
        node_id = parameters.unpack()[0]
        pipeline = Gst.Pipeline.new('viola-private-monitor-capture')
        source = Gst.ElementFactory.make('pipewiresrc')
        convert = Gst.ElementFactory.make('videoconvert')
        encode = Gst.ElementFactory.make('pngenc')
        sink = Gst.ElementFactory.make('filesink')
        if not all((source, convert, encode, sink)):
            errors.append('Installed GStreamer capture elements are unavailable')
            loop.quit()
            return
        source.set_property('path', str(node_id))
        source.set_property('num-buffers', 1)
        source.set_property('do-timestamp', True)
        sink.set_property('location', str(args.output))
        for element in (source, convert, encode, sink):
            pipeline.add(element)
        if not (source.link(convert) and convert.link(encode) and encode.link(sink)):
            errors.append('Could not link native monitor capture pipeline')
            loop.quit()
            return
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message', bus_message)
        pipeline.set_state(Gst.State.PLAYING)
    subscription = connection.signal_subscribe(service, service + '.Stream', 'PipeWireStreamAdded',
        stream, None, Gio.DBusSignalFlags.NONE, stream_ready)
    def timeout():
        errors.append('Private monitor capture timed out')
        loop.quit()
        return False
    timer = GLib.timeout_add_seconds(12, timeout)
    try:
        if device:
            GLib.timeout_add(200, lambda: device.move(800, 110) or False)
            GLib.timeout_add(300, lambda: device.click() or False)
            GLib.timeout_add(550, lambda: call(session, service + '.Session', 'Start') and False)
        else:
            call(session, service + '.Session', 'Start')
        loop.run()
    finally:
        GLib.source_remove(timer)
        if pipeline:
            pipeline.set_state(Gst.State.NULL)
        connection.signal_unsubscribe(subscription)
        call(session, service + '.Session', 'Stop')
        if device:
            device.close()
    if errors:
        raise RuntimeError('; '.join(errors))
    if not args.output.is_file() or args.output.stat().st_size == 0:
        raise RuntimeError('Private display produced no PNG')
    print(json.dumps({'classification': 'owned headless Wayland monitor capture',
                      'output': str(args.output), 'monitor': 'Meta-0'}, indent=2))


if __name__ == '__main__':
    main()
