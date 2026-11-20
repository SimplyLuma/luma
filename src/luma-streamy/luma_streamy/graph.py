# SPDX-License-Identifier: Apache-2.0
"""The pipeline graph, described before it is built.

Every function here is pure: it turns the scene model and the chosen
encoders into element specs (factory, name, properties) and links. The
engine instantiates them; the tests check them without GStreamer.

Shape (one pipeline; branches come and go while it runs):

    picture widget:  source ! … ! glupload ! glcolorconvert ! lumaframe ! queue ! mixer.sink_N
    sound widget:    source ! audioconvert ! audioresample ! volume ! level ! queue ! audiomixer.sink_N
    program:         glvideomixer ! video/x-raw(memory:GLMemory),WxH,fps ! tee
                       ├ queue ! gtk4paintablesink                 (the preview, while shown)
                       ├ queue ! <video encoder> ! parse ! tee      (only while live or recording)
                     audiomixer ! audio/x-raw,48 kHz,stereo ! tee
                       └ queue ! <aac encoder> ! aacparse ! tee     (only while live or recording)
    record:          mux (matroskamux | mp4mux fragmented) ! filesink
    stream:          eflvmux ! rtmp2sink (rtmp:// or rtmps://)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import Effects, Widget

FPS = 30
AUDIO_RATE = 48000


@dataclass
class Element:
    factory: str
    name: str
    props: dict = field(default_factory=dict)


@dataclass
class Chain:
    """Elements linked in order; ``pad`` holds the properties of the request pad it ends on."""

    elements: list[Element]
    target: str | None = None
    pad: dict = field(default_factory=dict)

    @property
    def first(self) -> Element:
        return self.elements[0]

    @property
    def last(self) -> Element:
        return self.elements[-1]

    def element(self, factory: str) -> Element | None:
        return next((e for e in self.elements if e.factory == factory), None)


def argb(rgb: str, alpha: float) -> int:
    """'1F6FE0' and 0.5 -> 0x801F6FE0, lumaframe's colour property."""
    a = max(0, min(255, round(alpha * 255)))
    return (a << 24) | int(rgb, 16)


# ── Pictures ──

def frame_props(widget: Widget) -> dict:
    """lumaframe's properties for a widget's box and effects (canvas pixels)."""
    fx: Effects = widget.effects.clamped()
    return {
        "box-width": float(round(widget.width)),
        "box-height": float(round(widget.height)),
        "margin": fx.margin,
        "corner-radius": fx.corner_radius,
        "stroke-width": fx.stroke_width,
        "stroke-color": argb(fx.stroke_color, 1.0),
        "shadow-blur": fx.shadow_blur,
        "shadow-offset-x": fx.shadow_x,
        "shadow-offset-y": fx.shadow_y,
        "shadow-color": argb("000000", fx.shadow_opacity),
        "opacity": fx.opacity,
        "crop-left": fx.crop_left,
        "crop-top": fx.crop_top,
        "crop-right": fx.crop_right,
        "crop-bottom": fx.crop_bottom,
    }


def mixer_pad_props(widget: Widget, zorder: int) -> dict:
    """Where glvideomixer draws the padded layer: the box grown by the shadow margin."""
    m = widget.effects.clamped().margin
    return {
        "xpos": int(round(widget.x - m)),
        "ypos": int(round(widget.y - m)),
        "width": int(round(widget.width + 2 * m)),
        "height": int(round(widget.height + 2 * m)),
        "zorder": zorder,
    }


def _capture_head(widget: Widget, prefix: str, fake: bool, fd: int | None, node: str | None) -> list[Element]:
    kind = widget.kind
    if fake:
        # A still picture stands in for a capture (STREAMY_FAKE_SOURCES=1).
        path = widget.source.get("fake-image")
        if path:
            return [Element("filesrc", f"{prefix}-src", {"location": path}),
                    Element("decodebin", f"{prefix}-decode"),
                    Element("videoconvert", f"{prefix}-convert"),
                    Element("imagefreeze", f"{prefix}-freeze", {"is-live": True}),
                    Element("capsfilter", f"{prefix}-rate", {"caps": f"video/x-raw,framerate={FPS}/1"})]
        pattern = {"display": "smpte100", "window": "bar", "camera": "ball"}.get(kind, "smpte")
        return [Element("videotestsrc", f"{prefix}-src", {"is-live": True, "pattern": pattern}),
                Element("capsfilter", f"{prefix}-caps",
                        {"caps": f"video/x-raw,width=1280,height=720,framerate={FPS}/1"})]
    if kind == "image":
        return [Element("filesrc", f"{prefix}-src", {"location": widget.source.get("path", "")}),
                Element("decodebin", f"{prefix}-decode"),
                Element("videoconvert", f"{prefix}-convert"),
                Element("imagefreeze", f"{prefix}-freeze", {"is-live": True}),
                Element("capsfilter", f"{prefix}-rate", {"caps": f"video/x-raw,framerate={FPS}/1"})]
    props = {"always-copy": False, "keepalive-time": 1000}
    if fd is not None:
        props["fd"] = fd
    if node is not None:
        props["target-object"] = node
    if kind == "camera":
        # WirePlumber links a camera-portal client's stream only when it says it is a camera.
        props["stream-properties"] = "props,media.role=Camera"
    head = [Element("pipewiresrc", f"{prefix}-src", props)]
    if kind == "camera":
        # Cameras may send MJPEG; decodebin passes raw pictures through.
        head += [Element("decodebin", f"{prefix}-decode"), Element("videoconvert", f"{prefix}-convert")]
    return head


def picture_chain(widget: Widget, zorder: int, *, fake: bool = False, fd: int | None = None,
                  node: str | None = None) -> Chain:
    """A picture widget from its source to its glvideomixer pad."""
    prefix = f"w-{widget.id}"
    elements = _capture_head(widget, prefix, fake, fd, node)
    elements += [
        Element("queue", f"{prefix}-queue", {"max-size-buffers": 2, "leaky": 2}),
        Element("glupload", f"{prefix}-upload"),
        Element("glcolorconvert", f"{prefix}-glconvert"),
        Element("capsfilter", f"{prefix}-rgba", {"caps": "video/x-raw(memory:GLMemory),format=RGBA"}),
        Element("lumaframe", f"{prefix}-frame", frame_props(widget)),
    ]
    return Chain(elements, "mixer", mixer_pad_props(widget, zorder))


# ── Sound ──

def sound_chain(widget: Widget, *, fake: bool = False, fd: int | None = None, node: str | None = None) -> Chain:
    prefix = f"w-{widget.id}"
    if fake:
        wave = 9 if widget.kind == "microphone" else 0  # pink noise, a sine
        head = [Element("audiotestsrc", f"{prefix}-src", {"is-live": True, "wave": wave, "volume": 0.25})]
    elif widget.kind == "desktop-audio":
        head = [Element("pulsesrc", f"{prefix}-src", {"device": widget.source.get("device", "@DEFAULT_MONITOR@"),
                                                       "client-name": "Streamy"})]
    elif widget.kind == "app-audio":
        props = {}
        if fd is not None:
            props["fd"] = fd
        if node is not None:
            props["target-object"] = node
        head = [Element("pipewiresrc", f"{prefix}-src", props)]
    else:
        props = {"client-name": "Streamy"}
        if widget.source.get("device"):
            props["device"] = widget.source["device"]
        head = [Element("pulsesrc", f"{prefix}-src", props)]
    return Chain(head + [
        Element("audioconvert", f"{prefix}-aconvert"),
        Element("audioresample", f"{prefix}-resample"),
        Element("volume", f"{prefix}-volume", volume_props(widget)),
        Element("level", f"{prefix}-level", {"interval": 50_000_000, "post-messages": True}),
        Element("queue", f"{prefix}-aqueue", {"max-size-time": 200_000_000, "leaky": 2}),
    ], "audiomixer", {})


def volume_props(widget: Widget) -> dict:
    return {"volume": 10 ** (widget.volume_db / 20) if widget.volume_db > -60 else 0.0, "mute": widget.muted}


# ── Program ──

def program(canvas: tuple[int, int]) -> list[Element]:
    width, height = canvas
    return [
        Element("glvideomixer", "mixer", {"background": 1, "latency": 66_000_000, "force-live": True,
                                          "start-time-selection": 1}),
        Element("capsfilter", "program-caps", {"caps": f"video/x-raw(memory:GLMemory),width={width},height={height},"
                                                        f"framerate={FPS}/1,format=RGBA"}),
        Element("tee", "program", {"allow-not-linked": True}),
    ]


def sound_program() -> list[Element]:
    return [
        Element("audiomixer", "audiomixer", {"latency": 50_000_000, "force-live": True, "start-time-selection": 1}),
        Element("capsfilter", "sound-caps", {"caps": f"audio/x-raw,rate={AUDIO_RATE},channels=2"}),
        Element("tee", "sound", {"allow-not-linked": True}),
    ]


def preview_chain() -> Chain:
    return Chain([Element("queue", "preview-queue", {"max-size-buffers": 1, "leaky": 2}),
                  Element("gtk4paintablesink", "preview", {"sync": False})])


# ── Encoders ──

H264 = ("vah264lpenc", "vah264enc", "openh264enc")
AV1 = ("vaav1lpenc", "vaav1enc")
AAC = ("fdkaacenc", "avenc_aac", "voaacenc")


@dataclass
class EncoderChoice:
    video: str
    audio: str | None
    codec: str  # "h264" | "av1"


def choose(available: set[str], codec: str) -> EncoderChoice | None:
    """The first working encoder for a codec, hardware first; AAC for the sound."""
    order = H264 if codec == "h264" else AV1
    video = next((f for f in order if f in available), None)
    if video is None:
        return None
    audio = next((f for f in AAC if f in available), None)
    return EncoderChoice(video, audio, codec)


def tap_video() -> list[Element]:
    """Program pictures for the encoders: converted to NV12 on the GPU, read back once."""
    return [Element("queue", "tap-vqueue", {"max-size-buffers": 3, "leaky": 2}),
            Element("glcolorconvert", "tap-glconvert"),
            Element("capsfilter", "tap-glnv12", {"caps": "video/x-raw(memory:GLMemory),format=NV12"}),
            Element("gldownload", "tap-download"),
            Element("capsfilter", "tap-nv12", {"caps": "video/x-raw,format=NV12"}),
            Element("appsink", "tap-video", {"emit-signals": True, "drop": True, "max-buffers": 4,
                                             "sync": False, "async": False})]


def tap_sound() -> list[Element]:
    return [Element("queue", "tap-aqueue", {"max-size-time": 500_000_000, "leaky": 2}),
            Element("audioconvert", "tap-aconvert"),
            Element("capsfilter", "tap-s16", {"caps": f"audio/x-raw,format=S16LE,rate={AUDIO_RATE},channels=2"}),
            Element("appsink", "tap-sound", {"emit-signals": True, "drop": True, "max-buffers": 16,
                                             "sync": False, "async": False})]


def video_encoder_chain(choice: EncoderChoice, bitrate_kbps: int, prefix: str) -> list[Element]:
    """NV12 pictures to an encoded stream: CBR, a key frame every 2 s, no B-frames."""
    gop = 2 * FPS
    f = choice.video
    if f.startswith("va"):
        head = []  # VA encoders take NV12 from system memory and upload it themselves
        props = {"rate-control": "cbr", "bitrate": bitrate_kbps, "key-int-max": gop}
        if "h26" in f:
            props["b-frames"] = 0
    else:
        head = [Element("videoconvert", f"{prefix}-convert"),
                Element("capsfilter", f"{prefix}-i420", {"caps": "video/x-raw,format=I420"})]
        props = {"bitrate": bitrate_kbps * 1000, "rate-control": "bitrate", "gop-size": gop,
                 "complexity": "low", "usage-type": "screen"}
    parse = "h264parse" if choice.codec == "h264" else "av1parse"
    parse_props = {"config-interval": -1} if parse == "h264parse" else {}
    return [Element("queue", f"{prefix}-vqueue", {"max-size-time": 2_000_000_000, "max-size-buffers": 0,
                                                    "max-size-bytes": 0, "leaky": 2})] + head + \
        [Element(f, f"{prefix}-encoder", props), Element(parse, f"{prefix}-parse", parse_props)]


def audio_encoder_chain(choice: EncoderChoice, bitrate_kbps: int, prefix: str) -> list[Element]:
    if choice.audio is None:
        return []
    props = {"bitrate": bitrate_kbps * 1000}
    return [Element("queue", f"{prefix}-aqueue", {"max-size-time": 2_000_000_000, "max-size-buffers": 0,
                                                    "max-size-bytes": 0, "leaky": 2}),
            Element("audioconvert", f"{prefix}-aconvert"),
            Element(choice.audio, f"{prefix}-aencoder", props),
            Element("aacparse", f"{prefix}-aparse")]


def record_sink(container: str, path: str) -> list[Element]:
    if container == "mp4":
        # Fragmented, so a crash leaves a playable file.
        mux = Element("mp4mux", "record-mux", {"fragment-duration": 2000, "streamable": True})
    else:
        mux = Element("matroskamux", "record-mux", {"writing-app": "Streamy"})
    return [mux, Element("filesink", "record-sink", {"location": path, "async": False})]


def stream_sink(url: str, *, enhanced: bool) -> list[Element]:
    mux = Element("eflvmux" if enhanced else "flvmux", "stream-mux", {"streamable": True})
    return [mux, Element("rtmp2sink", "stream-sink", {"location": url, "async-connect": True, "sync": False})]


# ── Destinations ──

DESTINATIONS = {
    # key: (label, ingest URL, codec, video kbps, audio kbps)
    "twitch": ("Twitch", "rtmps://live.twitch.tv/app", "h264", 6000, 160),
    "youtube": ("YouTube", "rtmps://a.rtmps.youtube.com/live2", "h264", 8000, 160),
    "custom": ("Custom RTMP", "", "h264", 6000, 160),
}


def stream_url(server: str, key: str) -> str:
    server = server.rstrip("/")
    if not server.startswith(("rtmp://", "rtmps://")):
        raise ValueError("a server address starts with rtmp:// or rtmps://")
    return f"{server}/{key}" if key else server


def backoff(attempt: int) -> float:
    """Seconds before reconnect attempt ``attempt`` (1-based): 2 s, ×1.5, at most 15 min."""
    return min(2.0 * 1.5 ** max(0, attempt - 1), 900.0)
