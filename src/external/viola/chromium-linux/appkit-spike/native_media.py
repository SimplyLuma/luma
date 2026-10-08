# SPDX-License-Identifier: GPL-3.0-only
"""Native GTK receiver for Viola's existing video-only WebRTC preview."""
from collections import deque
from media_paintable import MediaPaintable, cadence
import json
import time
import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstSdp', '1.0')
gi.require_version('GstWebRTC', '1.0')
from gi.repository import GLib, Gst, GstSdp, GstWebRTC


class NativeMedia:
    def __init__(self, services, submit, preview, error):
        Gst.init(None)
        self.services, self.submit, self.preview, self.error = services, submit, preview, error
        self.pipeline = self.receiver = self.sink = None
        self.paintable = None
        self.frame_samples = deque(maxlen=600)
        self.generation = None
        self.tab_id = None
        self.frames = 0
        self.last_frame = 0
        self.answer_sent = False
        self.closed = False
        self.metrics = {'offers': 0, 'answers': 0, 'frames': 0, 'errors': []}
        self.timer = GLib.timeout_add(500, self.health)

    def event(self, payload):
        if self.closed:
            return False
        if payload.get('kind') == 'stop':
            self.stop()
        elif payload.get('kind') == 'offer':
            try:
                self.offer(payload)
            except Exception as error:
                self.failed(str(error))
        return False

    def offer(self, payload):
        self.stop()
        generation = payload['generation']
        description = json.loads(payload['offer'])
        if (not isinstance(generation, int) or description.get('type') != 'offer'
                or not isinstance(description.get('sdp'), str) or len(description['sdp']) > 262144):
            raise ValueError('Invalid retained media offer')
        self.generation, self.tab_id = generation, payload.get('tabId')
        self.frames, self.last_frame = 0, 0
        self.frame_samples.clear()
        self.answer_sent = False
        self.metrics['offers'] += 1
        pipeline = self.pipeline = Gst.Pipeline.new('viola-native-media')
        self.receiver = Gst.ElementFactory.make('webrtcbin', 'retained-preview')
        decode = Gst.ElementFactory.make('decodebin')
        queue = Gst.ElementFactory.make('queue')
        self.sink = Gst.ElementFactory.make('gtk4paintablesink')
        if not all((pipeline, self.receiver, decode, queue, self.sink)):
            raise RuntimeError('Installed native media elements are unavailable')
        self.receiver.set_property('bundle-policy', GstWebRTC.WebRTCBundlePolicy.MAX_BUNDLE)
        # The transport stays on this machine; avoid the default 200 ms network buffer.
        self.receiver.set_property('latency', 20)
        self.sink.set_property('sync', False)
        queue.set_property('max-size-buffers', 2)
        queue.set_property('max-size-bytes', 0)
        queue.set_property('max-size-time', 0)
        queue.set_property('leaky', 2)
        # gtk4paintablesink accepts decoder DMA-BUFs and raw software frames.
        # Let caps negotiation preserve zero-copy instead of forcing downloads
        # through pixel analysis or conversion stages.
        for element in (self.receiver, decode, queue, self.sink):
            pipeline.add(element)
        for first, second in ((queue, self.sink),):
            if not first.link(second):
                raise RuntimeError('Native preview video elements could not link')
        self.receiver.connect('pad-added', lambda _element, pad: pad.link(decode.get_static_pad('sink')))
        decode.connect('pad-added', lambda _element, pad: pad.link(queue.get_static_pad('sink')))
        self.sink.get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER, self.buffer, generation)
        self.receiver.connect('notify::ice-gathering-state', lambda *_: GLib.idle_add(self.gathered, generation))
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message', self.message, generation)
        pipeline.set_state(Gst.State.PLAYING)
        _, message = GstSdp.SDPMessage.new()
        if GstSdp.sdp_message_parse_buffer(description['sdp'].encode(), message) != GstSdp.SDPResult.OK:
            raise ValueError('Retained SDP did not parse')
        offer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.OFFER, message)
        promise = Gst.Promise.new_with_change_func(lambda *_: GLib.idle_add(self.create_answer, generation), None)
        self.receiver.emit('set-remote-description', offer, promise)
        GLib.timeout_add_seconds(7, self.answer_timeout, generation)

    def create_answer(self, generation):
        if generation != self.generation or not self.receiver:
            return False
        def created(promise, *_):
            reply = promise.get_reply()
            answer = reply.get_value('answer') if reply else None
            GLib.idle_add(self.local_answer, generation, answer.copy() if answer else None)
        self.receiver.emit('create-answer', None, Gst.Promise.new_with_change_func(created, None))
        return False

    def local_answer(self, generation, answer):
        if generation != self.generation or not self.receiver:
            return False
        if answer is None:
            self.failed('Native WebRTC did not create an answer')
            return False
        promise = Gst.Promise.new()
        self.receiver.emit('set-local-description', answer, promise)
        promise.interrupt()
        return False

    def gathered(self, generation):
        if generation != self.generation or not self.receiver or self.answer_sent:
            return False
        if self.receiver.get_property('ice-gathering-state') != GstWebRTC.WebRTCICEGatheringState.COMPLETE:
            return False
        description = self.receiver.get_property('local-description')
        if description:
            self.answer_sent = True
            self.metrics['answers'] += 1
            answer = json.dumps({'type': 'answer', 'sdp': description.sdp.as_text()})
            self.submit(lambda: self.services.media_answer(generation, answer))
        return False

    def answer_timeout(self, generation):
        if generation == self.generation and not self.answer_sent:
            self.failed('Native media answer timed out')
        return False

    def buffer(self, _pad, _info, generation):
        if generation == self.generation:
            self.frames += 1
            self.metrics['frames'] += 1
            self.last_frame = time.monotonic()
            self.frame_samples.append((self.last_frame, self.frames))
            if self.frames == 1:
                GLib.idle_add(self.show_preview, generation)
        return Gst.PadProbeReturn.OK

    def show_preview(self, generation):
        if generation == self.generation and self.sink:
            self.paintable = MediaPaintable(self.sink.get_property('paintable'))
            self.preview(self.paintable, self.tab_id)
        return False

    def message(self, _bus, message, generation):
        if generation != self.generation:
            return
        if message.type == Gst.MessageType.ERROR:
            error, detail = message.parse_error()
            self.failed(str(error) + ': ' + str(detail))

    def update_metrics(self):
        now = time.monotonic()
        self.metrics['decoded_cadence'] = cadence(list(self.frame_samples), now)
        if self.paintable:
            self.metrics['presented_cadence'] = cadence(self.paintable.presentations, now)
            self.metrics['preview_size'] = [self.paintable.get_intrinsic_width(),
                                            self.paintable.get_intrinsic_height()]

    def health(self):
        if self.closed:
            return False
        if self.generation is not None and self.frames:
            self.update_metrics()
            now = time.monotonic()
            state = {'generation': self.generation, 'frames': self.frames,
                     'lastFrameAt': time.time() * 1000 - (now - self.last_frame) * 1000,
                     # Compatibility with the retained health contract. Valid
                     # dark video is not a transport failure. Frame arrival and
                     # pipeline errors determine health without scanning pixels.
                     'blackMs': 0}
            self.submit(lambda: self.services.media_health(state))
        return True

    def failed(self, message):
        self.metrics['errors'].append(message)
        generation = self.generation
        if generation is not None and not self.answer_sent:
            self.submit(lambda: self.services.media_answer(generation, None))
        self.stop()

    def stop(self):
        if self.paintable:
            self.update_metrics()
            self.paintable.close()
            self.paintable = None
        self.generation = None
        if self.pipeline:
            self.pipeline.get_bus().remove_signal_watch()
            self.pipeline.set_state(Gst.State.NULL)
        self.pipeline = self.receiver = self.sink = None
        self.preview(None, self.tab_id)
        self.tab_id = None

    def close(self):
        if self.closed:
            return
        self.closed = True
        GLib.source_remove(self.timer)
        self.stop()
