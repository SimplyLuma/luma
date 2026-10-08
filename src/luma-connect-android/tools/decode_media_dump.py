#!/usr/bin/env python3
"""Decodes a recorded Luma Connect media stream with the desktop's own pipeline plan.

Runs on a Linux desktop with GStreamer. The plan from companion_media.pipeline_plan is
used unchanged except that the final PipeWire or GTK sink becomes a counting fakesink,
so nothing appears on screen and no camera node is created.

    PYTHONPATH=src/luma-continuity python3 decode_media_dump.py camera-vp8.lmd
"""
import json
import struct
import sys
import threading

import gi
gi.require_version('Gst', '1.0')
from gi.repository import GLib, Gst

from luma_continuity import companion_media


def main(path):
    Gst.init(None)
    with open(path, 'rb') as source:
        size, = struct.unpack('>I', source.read(4))
        header = json.loads(source.read(size))
        packets = []
        while True:
            head = source.read(13)
            if len(head) < 13: break
            kind, pts, length = struct.unpack('>BqI', head)
            packets.append((kind, pts, source.read(length)))
    available = lambda factory: Gst.ElementFactory.find(factory) is not None
    kind = header['kind']
    plan = companion_media.pipeline_plan(kind, dict(header, audio=None), device_name='Dump', peer='d' * 64, available=available)
    steps = [step for step in plan['video'] if step[0] not in ('pipewiresink', 'gtk4paintablesink', 'v4l2sink')]
    steps.append(('fakesink', {'name': 'out', 'sync': False, 'signal-handoffs': True}))
    session = type('Session', (), {'kind': kind, 'peer': 'd' * 64, 'header': header})()
    sink = companion_media.GStreamerSink(session, device_name='Dump')
    sink._Gst = Gst
    pipeline = Gst.Pipeline.new('dump')
    elements = sink._chain(Gst, pipeline, steps)
    sink._video = elements[0]
    decoded = {'frames': 0, 'caps': None}
    done = threading.Event()
    def handoff(_element, buffer, pad):
        decoded['frames'] += 1
        decoded['caps'] = pad.get_current_caps().to_string() if pad.get_current_caps() else None
    elements[-1].connect('handoff', handoff)
    errors = []
    bus = pipeline.get_bus()
    pipeline.set_state(Gst.State.PLAYING)
    for kind_code, pts, data in packets:
        if kind_code == 1: sink.codec_config(data)
        else: sink.video(pts, data, kind_code == 3)
    sink._video.emit('end-of-stream')
    message = bus.timed_pop_filtered(20 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    if message and message.type == Gst.MessageType.ERROR:
        errors.append(message.parse_error()[0].message)
    decoder = next((e.get_factory().get_name() for e in pipeline.iterate_recurse() if 'dec' in e.get_factory().get_name()), None) if False else None
    names = []
    iterator = pipeline.iterate_recurse()
    while True:
        result, element = iterator.next()
        if result != Gst.IteratorResult.OK: break
        names.append(element.get_factory().get_name())
    pipeline.set_state(Gst.State.NULL)
    print(json.dumps({'file': path, 'codec': header['codec'], 'packets': len(packets), 'decoded_frames': decoded['frames'],
                      'decoder_elements': [n for n in names if n.endswith('dec') or 'dec' in n], 'caps': decoded['caps'], 'errors': errors}))
    return 0 if decoded['frames'] > 0 and not errors else 1


if __name__ == '__main__':
    sys.exit(max(main(path) for path in sys.argv[1:]))
