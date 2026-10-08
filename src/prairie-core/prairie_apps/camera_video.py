# SPDX-License-Identifier: Apache-2.0
"""A native, finite Camera recording session. EOS finalizes the WebM container."""
from __future__ import annotations

import time
from pathlib import Path

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst

from .camera_state import crop_edges
from .camera_backend import image_rotation_from_mount, video_direction_for_transform


class VideoRecording:
    @staticmethod
    def available() -> bool:
        return all(Gst.ElementFactory.find(name) for name in (
            "vp8enc", "webmmux", "opusenc", "autoaudiosrc", "level", "appsink",
        ))

    def __init__(self, device, zoom: float, filename: Path, on_message, *, audio=True, mirror=False):
        self.pipeline = Gst.Pipeline.new("camera-video")
        self.device = device
        self.paused = False
        self.stopping = False
        self.started = None
        self.paused_at = None
        self.pause_duration = 0.0
        self._latest = None
        self.audio = audio

        def make(factory, name):
            element = Gst.ElementFactory.make(factory, name)
            if element is None:
                raise RuntimeError(f"The recording component {factory} is unavailable.")
            self.pipeline.add(element)
            return element

        source = make("libcamerasrc", "video-camera")
        source.set_property("camera-name", device.identifier)
        caps = make("capsfilter", "video-source-caps")
        caps.set_property("caps", Gst.Caps.from_string(
            f"{device.stream_format},width={device.preview_width},height={device.preview_height}"))
        decoder = make("jpegdec" if device.stream_format == "image/jpeg" else "identity", "video-decoder")
        crop = make("videocrop", "video-crop")
        horizontal, vertical = crop_edges(device.preview_width, device.preview_height, zoom)
        for name, value in (("left", horizontal), ("right", horizontal), ("top", vertical), ("bottom", vertical)):
            crop.set_property(name, value)
        scale = make("videoscale", "video-scale")
        output = make("capsfilter", "video-output-caps")
        output.set_property("caps", Gst.Caps.from_string(
            f"video/x-raw,width={device.preview_width},height={device.preview_height}"))
        flip = make("videoflip", "video-mount")
        rotation = 0 if device.desktop_uvc else image_rotation_from_mount(device.rotation, front_facing=device.location == "front")
        flip.set_property("video-direction", video_direction_for_transform(rotation, mirror_horizontal=mirror and device.location == "front"))
        tee = make("tee", "video-tee")
        preview_queue = make("queue", "video-preview-queue")
        preview_queue.set_property("leaky", 2)
        preview_queue.set_property("max-size-buffers", 2)
        convert = make("videoconvert", "video-preview-convert")
        self.sink = make("gtk4paintablesink", "video-preview")
        self.sink.set_property("sync", False)
        encode_queue = make("queue", "video-encode-queue")
        encoder_convert = make("videoconvert", "video-encode-convert")
        encoder = make("vp8enc", "video-encoder")
        encoder.set_property("deadline", 1)
        encoder.set_property("cpu-used", 8)
        encoder.set_property("threads", 2)
        encoder.set_property("target-bitrate", 4_000_000)
        encoder.set_property("keyframe-max-dist", 60)
        mux = make("webmmux", "video-mux")
        file_sink = make("filesink", "video-file")
        file_sink.set_property("location", str(filename))
        still_queue = make("queue", "video-still-queue")
        still_queue.set_property("leaky", 2)
        still_queue.set_property("max-size-buffers", 1)
        self.still_sink = make("appsink", "video-still")
        self.still_sink.set_property("max-buffers", 1)
        self.still_sink.set_property("drop", True)
        self.still_sink.set_property("sync", False)
        self.still_sink.set_property("emit-signals", True)
        self.still_sink.connect("new-sample", self._sample)
        chains = [
            (source, caps, decoder, crop, scale, output, flip, tee),
            (tee, preview_queue, convert, self.sink),
            (tee, encode_queue, encoder_convert, encoder, mux, file_sink),
            (tee, still_queue, self.still_sink),
        ]
        if audio:
            audio_source = make("autoaudiosrc", "video-microphone")
            audio_queue = make("queue", "video-audio-queue")
            audio_convert = make("audioconvert", "video-audio-convert")
            audio_resample = make("audioresample", "video-audio-resample")
            level = make("level", "video-audio-level")
            level.set_property("interval", 100_000_000)
            audio_encoder = make("opusenc", "video-audio-encoder")
            chains.append((audio_source, audio_queue, audio_convert, audio_resample, level, audio_encoder, mux))
        for chain in chains:
            for first, second in zip(chain, chain[1:]):
                if not first.link(second):
                    self.pipeline.set_state(Gst.State.NULL)
                    raise RuntimeError("The recording pipeline could not be connected.")
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect("message", on_message, self)

    def start(self):
        result = self.pipeline.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Recording could not start.")
        self.started = time.monotonic()

    @property
    def elapsed(self):
        if self.started is None:
            return 0.0
        return max(0.0, (self.paused_at or time.monotonic()) - self.started - self.pause_duration)

    def set_paused(self, paused):
        if self.paused == paused or self.stopping:
            return
        now = time.monotonic()
        self.pipeline.set_state(Gst.State.PAUSED if paused else Gst.State.PLAYING)
        if paused:
            self.paused_at = now
        elif self.paused_at is not None:
            self.pause_duration += now - self.paused_at
            self.paused_at = None
        self.paused = paused

    def stop(self):
        if self.stopping:
            return
        self.set_paused(False)
        self.stopping = True
        if not self.pipeline.send_event(Gst.Event.new_eos()):
            raise RuntimeError("The recording could not finish. The unfinished take has been kept.")

    def close(self):
        self.pipeline.set_state(Gst.State.NULL)
        self.bus.remove_signal_watch()
        self._latest = None

    def _sample(self, sink):
        self._latest = sink.emit("pull-sample")
        return Gst.FlowReturn.OK

    def grab_jpeg(self) -> bytes:
        """Encode the last acquired frame without interrupting the recording."""
        sample = self._latest
        if sample is None:
            raise RuntimeError("No recording frame is available yet.")
        pipeline = Gst.parse_launch("appsrc name=input format=time ! videoconvert ! jpegenc quality=95 ! appsink name=output sync=false")
        try:
            source = pipeline.get_by_name("input")
            source.set_property("caps", sample.get_caps())
            pipeline.set_state(Gst.State.PLAYING)
            buffer = sample.get_buffer().copy_deep()
            buffer.pts = 0
            buffer.dts = Gst.CLOCK_TIME_NONE
            source.emit("push-buffer", buffer)
            source.emit("end-of-stream")
            encoded = pipeline.get_by_name("output").emit("try-pull-sample", 3 * Gst.SECOND)
            if encoded is None:
                raise RuntimeError("The still frame could not be encoded.")
            data = encoded.get_buffer()
            return data.extract_dup(0, data.get_size())
        finally:
            pipeline.set_state(Gst.State.NULL)
