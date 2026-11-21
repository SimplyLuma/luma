# SPDX-License-Identifier: Apache-2.0
"""The pipeline graph, described before it is built.

Every function here is pure: it turns the scene model and the chosen
encoders into element specs (factory, name, properties) and links. The
engine instantiates them; the tests check them without GStreamer.

Shape (one pipeline; branches come and go while it runs):

    picture widget:  source ! … ! glupload ! glcolorconvert ! tee        (captured once)
                       └ per canvas on: queue ! lumaframe ! mixer-<canvas>.sink_N
    sound widget:    source ! audioconvert ! audioresample ! volume ! level ! queue ! audiomixer.sink_N
    program, per canvas (landscape, vertical), only while shown or sent:
                     glvideomixer ! video/x-raw(memory:GLMemory),WxH,fps ! tee
                       ├ queue ! gtk4paintablesink                 (the preview, while shown)
                       ├ queue ! <video encoder> ! parse ! tee      (only while live or recording)
                     audiomixer ! audio/x-raw,48 kHz,stereo ! tee
                       └ queue ! <aac encoder> ! aacparse ! tee     (only while live or recording)
    record:          mux (matroskamux | mp4mux fragmented) ! filesink
    stream:          eflvmux ! rtmp2sink (rtmp:// or rtmps://)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .model import Effects, Widget

FPS = 30
#: The compositors' rate: a drag shows within a screen frame. What is sent is FPS.
PREVIEW_FPS = 60
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


# ── Formats ──

ALPHA_FORMATS = ("BGRA", "RGBA", "ARGB", "ABGR")
ALPHA_FOURCCS = ("AR24", "AB24", "RA24", "BA24")


def prefer_alpha(caps: str) -> str:
    """``caps`` with every format list ordered alpha first, so a window cast with transparent
    (rounded) corners is offered and taken with its alpha rather than as an opaque XR24/BGRx."""
    import re

    def order(found: re.Match) -> str:
        items = [v.strip() for v in found.group(2).split(",")]
        alpha = [v for v in items if v in ALPHA_FORMATS or v.split(":")[0] in ALPHA_FOURCCS]
        return f"{found.group(1)}{{ {', '.join(alpha + [v for v in items if v not in alpha])} }}"

    return re.sub(r"(\b(?:drm-)?format=\(string\))\{([^}]*)\}", order, caps)


def only_alpha(caps: str) -> str:
    """``caps`` cut to the formats with alpha (DMABuf AR24/AB24… with their modifiers, system-memory
    BGRA/RGBA…); "" when none is left. A PipeWire producer picks the first format of its own list that
    the consumer takes, so ordering alone never wins over a producer that lists XR24 first."""
    import re

    kept = []
    for part in caps.split(";"):
        part = part.strip()
        if not part:
            continue
        key = "drm-format" if "drm-format=" in part else "format"
        found = re.search(rf"\b{key}=\(string\)(\{{[^}}]*\}}|[^,;]+)", part)
        if found is None:
            continue
        value = found.group(1)
        items = [v.strip() for v in value.strip("{} ").split(",")] if value.startswith("{") else [value.strip()]
        alpha = [v for v in items if v in ALPHA_FORMATS or v.split(":")[0] in ALPHA_FOURCCS]
        if not alpha:
            continue
        text = alpha[0] if len(alpha) == 1 else "{ " + ", ".join(alpha) + " }"
        kept.append(part[:found.start(1)] + text + part[found.end(1):])
    return "; ".join(kept)



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
        "stroke-position": fx.stroke_position,
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


def hardware_decode() -> bool:
    """VA decoding is there (vajpegdec and vapostproc) and not turned off (STREAMY_DECODE=software)."""
    global _HW
    if _HW is None:
        from gi.repository import Gst

        _HW = os.environ.get("STREAMY_DECODE") != "software" and all(
            Gst.ElementFactory.find(name) is not None for name in ("vajpegdec", "vapostproc"))
    return _HW


_HW: bool | None = None


def _capture_head(widget: Widget, prefix: str, fake: bool, fd: int | None, node: str | None,
                  hw_decode: bool = False) -> list[Element]:
    kind = widget.kind
    if fake:
        if widget.source.get("fake-silent"):
            # A check run's stream that opens and never sends a picture (a window that never redraws).
            return [Element("appsrc", f"{prefix}-src", {"is-live": True, "format": "time", "caps":
                                                         f"video/x-raw,format=RGBA,width=1280,height=720,"
                                                         f"framerate={FPS}/1"})]
        # A still picture stands in for a capture (STREAMY_FAKE_SOURCES=1).
        path = widget.source.get("fake-image")
        if path:
            return [Element("filesrc", f"{prefix}-src", {"location": path}),
                    Element("decodebin", f"{prefix}-decode"),
                    Element("videoconvert", f"{prefix}-convert")]  # a still: frozen once uploaded
        pattern = {"display": "smpte100", "window": "bar", "camera": "ball"}.get(kind, "smpte")
        return [Element("videotestsrc", f"{prefix}-src", {"is-live": True, "pattern": pattern}),
                Element("capsfilter", f"{prefix}-caps",
                        {"caps": f"video/x-raw,width=1280,height=720,framerate={FPS}/1"})]
    if kind == "image":
        return [Element("filesrc", f"{prefix}-src", {"location": widget.source.get("path", "")}),
                Element("decodebin", f"{prefix}-decode"),
                Element("videoconvert", f"{prefix}-convert")]  # a still: frozen once uploaded
    # do-timestamp: PipeWire's own times aren't the pipeline's; stamping on arrival keeps the mixer in step.
    # resend-last + keepalive: a still screen (no damage, no new frames) keeps its picture.
    props = {"always-copy": False, "keepalive-time": 1000, "resend-last": True, "do-timestamp": True}
    if fd is not None:
        props["fd"] = fd
    if node is not None and node.startswith("path:"):
        props["path"] = node[5:]  # an old portal's node id, when no serial could be found
    elif node is not None:
        props["target-object"] = node
    if kind == "camera":
        # WirePlumber links a camera-portal client's stream only when it says it is a camera.
        props["stream-properties"] = "props,media.role=Camera"
    head = [Element("pipewiresrc", f"{prefix}-src", props)]
    if kind == "window":
        # Its caps are set to what downstream takes, alpha formats first (Engine._alpha_first): a
        # window's rounded corners stay transparent.
        head.append(Element("capsfilter", f"{prefix}-alpha", {}))
    if kind in ("display", "window") and os.environ.get("STREAMY_CAPTURE_MEMORY") == "system":
        head.append(Element("capsfilter", f"{prefix}-sysmem", {"caps": "video/x-raw"}))
    if kind == "camera":
        mode = widget.source.get("mode")
        if isinstance(mode, (list, tuple)) and len(mode) == 3:
            # The chosen resolution and frame rate, raw or MJPEG, whichever the camera sends.
            w, h, fps = (int(v) for v in mode)
            head.append(Element("capsfilter", f"{prefix}-mode", {
                "caps": f"video/x-raw,width={w},height={h},framerate={fps}/1;"
                        f"image/jpeg,width={w},height={h},framerate={fps}/1"}))
        # Cameras may send MJPEG; decodebin passes raw pictures through.
        if hw_decode:
            # The GPU decodes (vajpegdec) and vapostproc hands GL a DMABuf: no picture crosses the CPU.
            # A raw camera's frames are uploaded by vapostproc the same way.
            head += [Element("decodebin", f"{prefix}-decode"),
                     Element("vapostproc", f"{prefix}-vapp"),
                     Element("capsfilter", f"{prefix}-dmabuf", {"caps": "video/x-raw(memory:DMABuf)"})]
        else:
            # Software: a VA decoder's VA memory is something videoconvert can't take.
            head += [Element("decodebin", f"{prefix}-decode", {"force-sw-decoders": True}),
                     Element("videoconvert", f"{prefix}-convert")]
    return head


def is_still(widget: Widget, fake: bool) -> bool:
    """A picture decoded once (an image, or a fake source's stand-in picture)."""
    if fake:
        return bool(widget.source.get("fake-image")) and not widget.source.get("fake-silent")
    return widget.kind == "image"


def freeze(prefix: str) -> list[Element]:
    """A still picture repeated at the canvas rate on the GPU: uploaded and converted once, then the
    same texture every frame (the mixer needs a picture per frame from every layer)."""
    return [Element("imagefreeze", f"{prefix}-freeze", {"is-live": True}),
            Element("capsfilter", f"{prefix}-rate", {"caps": f"video/x-raw(memory:GLMemory),format=RGBA,"
                                                              f"framerate={FPS}/1"})]


def flip_method(widget: Widget) -> str:
    """A camera shown as a mirror (the way people see themselves) or as others see it."""
    return "horizontal-flip" if widget.source.get("mirror") else "none"


def branch_prefix(widget: Widget, generation: int) -> str:
    """Element names for one branch of a widget: "w-<id>-g<n>" (a widget may be re-added before
    its old branch has finished stopping)."""
    return f"w-{widget.id}-g{generation}"


_BRANCH_NAME = re.compile(r"^w-(.+?)-g(\d+)(?:-|$)")


def widget_of(element_name: str) -> str | None:
    """The widget id in a branch element's name ("w-<id>-g<n>-<part>"), or None."""
    match = _BRANCH_NAME.match(element_name)
    return match.group(1) if match else None


def generation_of(element_name: str) -> int | None:
    """The branch generation in a branch element's name, or None."""
    match = _BRANCH_NAME.match(element_name)
    return int(match.group(2)) if match else None


def picture_chain(widget: Widget, zorder: int, *, fake: bool = False, fd: int | None = None,
                  node: str | None = None, generation: int = 0, hw_decode: bool = False) -> Chain:
    """A picture widget from its source to its glvideomixer pad (a camera decoded on the GPU with
    ``hw_decode``)."""
    prefix = branch_prefix(widget, generation)
    elements = _capture_head(widget, prefix, fake, fd, node, hw_decode)
    elements += [
        Element("queue", f"{prefix}-queue", {"max-size-buffers": 2, "leaky": 2}),
        Element("glupload", f"{prefix}-upload"),
        Element("glcolorconvert", f"{prefix}-glconvert"),
        Element("capsfilter", f"{prefix}-rgba", {"caps": "video/x-raw(memory:GLMemory),format=RGBA"}),
    ]
    if is_still(widget, fake):
        elements += freeze(prefix)
    scaled = widget.source.get("scaled") if widget.kind == "camera" else None
    if isinstance(scaled, (list, tuple)) and len(scaled) == 2:
        # A size the camera lacks: made on the GPU right after capture, so everything after is smaller.
        elements += [Element("glcolorscale", f"{prefix}-scale"),
                     Element("capsfilter", f"{prefix}-scaled", {"caps": f"video/x-raw(memory:GLMemory),format=RGBA,"
                                                                          f"width={int(scaled[0])},height={int(scaled[1])}"})]
    if widget.kind == "camera":
        elements.append(Element("glvideoflip", f"{prefix}-flip", {"method": flip_method(widget)}))
    # Captured once; each canvas on takes its own layer from this tee (layer_chain).
    elements.append(Element("tee", f"{prefix}-vtee", {"allow-not-linked": True}))
    return Chain(elements)


def layer_chain(widget: Widget, zorder: int, prefix: str, fmt: str) -> Chain:
    """A picture widget drawn on canvas ``fmt``: ``widget`` is placed and styled as that canvas has it.
    The queue lets one canvas's mixer never hold up another's."""
    return Chain([Element("queue", f"{prefix}-{fmt}-lqueue", {"max-size-buffers": 1, "max-size-bytes": 0,
                                                               "max-size-time": 0, "leaky": 2}),
                  Element("lumaframe", f"{prefix}-{fmt}-frame", frame_props(widget))],
                 f"mixer-{fmt}", mixer_pad_props(widget, zorder))


# ── Placeholders ──

#: A frameless picture widget's card: a quiet neutral grey (ARGB for videotestsrc) and its word.
PLACEHOLDER_COLOR = 0xFF3A3E46
PLACEHOLDER_FPS = FPS


def placeholder_chain(widget: Widget, zorder: int, words: str, generation: int = 0) -> Chain:
    """What a picture widget shows while it has no live picture (connecting, failed, nothing chosen):
    a neutral card with one word, drawn through the widget's own lumaframe, so its Look shows live.
    Named "p-…", so its errors never fail the widget."""
    prefix = f"p-{widget.id}-g{generation}"
    width = max(2, int(round(widget.width)) // 2 * 2)
    height = max(2, int(round(widget.height)) // 2 * 2)
    scale = min(1.0, 1280 / max(width, height))  # a card, not a full-size picture: the box scales it
    cw, ch = max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)
    font = max(10, round(min(cw, ch) / 9))
    # Drawn once (one buffer, the text rendered and uploaded once), then frozen on the GPU.
    elements = [
        Element("videotestsrc", f"{prefix}-src", {"num-buffers": 1, "pattern": "solid-color",
                                                   "foreground-color": PLACEHOLDER_COLOR}),
        Element("capsfilter", f"{prefix}-caps", {"caps": f"video/x-raw,format=BGRA,width={cw},height={ch},"
                                                          f"framerate={PLACEHOLDER_FPS}/1"}),
        Element("textoverlay", f"{prefix}-words", {"text": words, "valignment": "center", "halignment": "center",
                                                    "font-desc": f"Figtree Semi-Bold {font}px",
                                                    "shaded-background": False, "draw-shadow": False,
                                                    "draw-outline": False, "color": 0xC8FFFFFF}),
        Element("videoconvert", f"{prefix}-convert"),
        Element("glupload", f"{prefix}-upload"),
        Element("glcolorconvert", f"{prefix}-glconvert"),
        Element("capsfilter", f"{prefix}-rgba", {"caps": "video/x-raw(memory:GLMemory),format=RGBA"}),
        *freeze(prefix),
        Element("tee", f"{prefix}-vtee", {"allow-not-linked": True}),
    ]
    return Chain(elements)


def placeholder_words(widget: Widget, state: str) -> str:
    """The card's word: "Connecting", "No picture", or what to choose."""
    if state == "choose":
        return {"camera": "Choose a camera", "display": "Choose a display", "window": "Choose a window",
                "image": "Choose an image"}.get(widget.kind, "Choose a source")
    return {"connecting": "Connecting", "failed": "No picture", "waiting": "No picture yet"}.get(state, state)


# ── Sound ──

def sound_chain(widget: Widget, *, fake: bool = False, fd: int | None = None, node: str | None = None,
                generation: int = 0) -> Chain:
    prefix = branch_prefix(widget, generation)
    if fake:
        wave = 9 if widget.kind == "microphone" else 0  # pink noise, a sine
        head = [Element("audiotestsrc", f"{prefix}-src", {"is-live": True, "wave": wave, "volume": 0.25})]
    elif widget.kind == "desktop-audio":
        # Its device is an output (a sink's node name); what it plays is that sink's monitor.
        sink = widget.source.get("device")
        head = [Element("pulsesrc", f"{prefix}-src", {"device": f"{sink}.monitor" if sink else "@DEFAULT_MONITOR@",
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
        Element("level", f"{prefix}-level", {"interval": 100_000_000, "post-messages": True}),
        # The monitor (headphones) hangs off this tee only while it is on.
        Element("tee", f"{prefix}-atee", {"allow-not-linked": True}),
        Element("queue", f"{prefix}-aqueue", {"max-size-time": 200_000_000, "leaky": 2}),
    ], "audiomixer", {"mute": widget.monitor == "only"})


#: A sound widget's monitor: off, heard here only (not in what is sent), or heard here and sent.
MONITOR_MODES = ("off", "only", "both")


def monitor_chain(widget: Widget, generation: int = 0) -> list[Element]:
    """Low-latency local playback of a sound widget (after its volume), for headphones."""
    prefix = branch_prefix(widget, generation)
    props = {"client-name": "Streamy monitor", "sync": False, "async": False,
             "buffer-time": 40_000, "latency-time": 10_000}
    if widget.source.get("monitor-device"):
        props["device"] = widget.source["monitor-device"]
    return [Element("queue", f"{prefix}-mqueue", {"max-size-time": 60_000_000, "max-size-buffers": 0,
                                                    "max-size-bytes": 0, "leaky": 2}),
            Element("audioconvert", f"{prefix}-mconvert"),
            Element("audioresample", f"{prefix}-mresample"),
            Element("pulsesink", f"{prefix}-monitor", props)]


def volume_props(widget: Widget) -> dict:
    return {"volume": 10 ** (widget.volume_db / 20) if widget.volume_db > -60 else 0.0, "mute": widget.muted}


# ── Program ──

def program(canvas: tuple[int, int], fmt: str = "landscape") -> list[Element]:
    """One canvas's compositor: mixer-<fmt> ! caps ! tee program-<fmt>."""
    width, height = canvas
    return [
        # No added latency: a move or resize shows on the next program frame; a source that is slower
        # than the canvas has its last picture reused.
        Element("glvideomixerelement", f"mixer-{fmt}", {"background": 1, "latency": 0, "force-live": True,
                                                        "start-time-selection": 1}),
        # Composited at the screen's rate, so a move shows on the next 16 ms frame; outputs take FPS (tap_video).
        Element("capsfilter", f"program-caps-{fmt}", {"caps": f"video/x-raw(memory:GLMemory),width={width},"
                                                               f"height={height},framerate={PREVIEW_FPS}/1,format=RGBA"}),
        Element("tee", f"program-{fmt}", {"allow-not-linked": True}),
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

#: Each codec's encoders, best first: VA hardware (low-power, then full), then software.
H264 = ("vah264lpenc", "vah264enc", "openh264enc")
#: SVT-AV1 is the software AV1, for machines without hardware AV1 encode.
AV1 = ("vaav1lpenc", "vaav1enc", "svtav1enc")

#: SVT-AV1's fastest preset in Fedora's build (it maps 11-13 to 10): real-time 1080p30 on a few cores.
SVTAV1_PRESET = 10
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


def tap_video(fmt: str = "landscape") -> list[Element]:
    """Program pictures for the encoders: converted to NV12 on the GPU, read back once. The valve
    closes while nothing is sent, so an idle tap converts and reads back nothing."""
    return [Element("valve", f"tap-vvalve-{fmt}", {"drop": False}),
            Element("queue", f"tap-vqueue-{fmt}", {"max-size-buffers": 3, "leaky": 2}),
            Element("videorate", f"tap-rate-{fmt}", {"drop-only": True}),
            Element("capsfilter", f"tap-fps-{fmt}", {"caps": f"video/x-raw(memory:GLMemory),framerate={FPS}/1"}),
            Element("glcolorconvert", f"tap-glconvert-{fmt}"),
            Element("capsfilter", f"tap-glnv12-{fmt}", {"caps": "video/x-raw(memory:GLMemory),format=NV12"}),
            Element("gldownload", f"tap-download-{fmt}"),
            Element("capsfilter", f"tap-nv12-{fmt}", {"caps": "video/x-raw,format=NV12"}),
            Element("appsink", f"tap-video-{fmt}", {"emit-signals": True, "drop": True, "max-buffers": 4,
                                             "sync": False, "async": False})]


def tap_sound() -> list[Element]:
    return [Element("valve", "tap-avalve", {"drop": False}),
            Element("queue", "tap-aqueue", {"max-size-time": 500_000_000, "leaky": 2}),
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
    elif f == "svtav1enc":
        head = [Element("videoconvert", f"{prefix}-convert"),
                Element("capsfilter", f"{prefix}-i420", {"caps": "video/x-raw,format=I420"})]
        # Real-time: the fastest preset, rate-controlled to the bitrate (crf off), a key frame every
        # 2 s. SVT-AV1's CBR needs its low-delay structure, which stalls in GStreamer 1.28, so this
        # is its bitrate-targeted VBR, which AV1 destinations (enhanced RTMP, files) take.
        props = {"preset": SVTAV1_PRESET, "target-bitrate": bitrate_kbps, "crf": -1,
                 "intra-period-length": gop - 1}
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
