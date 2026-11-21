# SPDX-License-Identifier: Apache-2.0
"""The output graph, described before it is built (pure Python, like graph.py).

One encode per (canvas, codec, video kbps, audio kbps), teed to every destination row that shares
it. Each row is a pipeline of its own fed by appsrc, so a row that drops its connection reconnects
alone and the encode and the other rows keep going:

    encode:  appsrc (NV12) ! queue ! <video encoder> ! parse ! capsfilter ! appsink
             appsrc (S16)  ! queue ! audioconvert ! <aac encoder> ! aacparse ! appsink
    rtmp:    appsrc ! queue ! flvmux | eflvmux ! rtmp2sink           (rtmp://, rtmps://)
    srt:     appsrc ! queue ! h264parse (byte-stream) ! mpegtsmux ! srtsink    (srt://)
    whip:    appsrc ! queue ! whipclientsink                          (http(s)://, if installed)
    file:    appsrc ! queue ! matroskamux | mp4mux ! filesink         (EOS finalises the file)
"""
from __future__ import annotations

from dataclasses import dataclass

from .graph import AAC, AV1, FPS, H264, Element, EncoderChoice, audio_encoder_chain, video_encoder_chain

#: How a row's address sends: by scheme.
KINDS = {"rtmp://": "rtmp", "rtmps://": "rtmp", "srt://": "srt", "http://": "whip", "https://": "whip"}

#: A recording's encoder, best first: hardware AV1, hardware H.264, then software AV1 and H.264.
#: Software AV1 is behind hardware H.264 so a recording beside a stream never takes the CPU.
RECORD_ORDER = (("av1", ("vaav1lpenc", "vaav1enc")), ("h264", ("vah264lpenc", "vah264enc")),
                ("av1", ("svtav1enc",)), ("h264", ("openh264enc",)))


def kind_of(url: str) -> str:
    for scheme, kind in KINDS.items():
        if url.startswith(scheme):
            return kind
    raise ValueError("A server address starts with rtmp://, rtmps://, srt:// or https://")


@dataclass(frozen=True)
class Preset:
    """What one encode makes; rows with an equal preset share it."""

    canvas: str
    codec: str  # "h264" | "av1"
    video_kbps: int
    audio_kbps: int

    @property
    def key(self) -> str:
        return f"{self.canvas}-{self.codec}-{self.video_kbps}-{self.audio_kbps}"


def choose(working: set[str], codec: str) -> EncoderChoice | None:
    """A stream's encoder for ``codec`` (hardware first) from the encoders that work."""
    order = H264 if codec == "h264" else AV1
    video = next((f for f in order if f in working), None)
    if video is None:
        return None
    return EncoderChoice(video, next((f for f in AAC if f in working), None), codec)


def choose_record(working: set[str], codec: str = "") -> EncoderChoice | None:
    """A recording's encoder: ``codec`` when asked for, else RECORD_ORDER."""
    if codec:
        return choose(working, codec)
    audio = next((f for f in AAC if f in working), None)
    for name, factories in RECORD_ORDER:
        video = next((f for f in factories if f in working), None)
        if video is not None:
            return EncoderChoice(video, audio, name)
    return None


def encode_video(choice: EncoderChoice, preset: Preset) -> list[Element]:
    """The encode's video half after its appsrc: graph.py's encoder chain, then the caps every muxer
    takes (AVC with codec_data for H.264, temporal units for AV1)."""
    prefix = f"enc-{preset.key}"
    chain = video_encoder_chain(choice, preset.video_kbps, prefix)
    parse = chain[-1]
    if parse.factory == "h264parse":
        parse.props = {}  # SPS/PPS travel in codec_data; an SRT row puts them in-band itself
        caps = "video/x-h264,stream-format=avc,alignment=au"
    else:
        caps = "video/x-av1,stream-format=obu-stream,alignment=tu"
    return chain + [Element("capsfilter", f"{prefix}-vcaps", {"caps": caps}),
                    Element("appsink", f"{prefix}-vout", _APPSINK)]


def encode_sound(choice: EncoderChoice, preset: Preset) -> list[Element]:
    prefix = f"enc-{preset.key}"
    chain = audio_encoder_chain(choice, preset.audio_kbps, prefix)
    if not chain:
        return []
    return chain + [Element("capsfilter", f"{prefix}-acaps", {"caps": "audio/mpeg,mpegversion=4,stream-format=raw"}),
                    Element("appsink", f"{prefix}-aout", _APPSINK)]


#: Encoded samples are handed on as they come: never synced, never dropped at the encode.
_APPSINK = {"emit-signals": True, "sync": False, "async": False, "drop": False, "max-buffers": 0}


def row_head(row_id: str, media: str) -> list[Element]:
    """A row's appsrc and queue for ``media`` ("video" | "sound"). A full queue (the network is behind)
    drops at the row's appsrc, counted as network drops."""
    return [Element("appsrc", f"row-{row_id}-{media}", {"is-live": True, "format": 3, "do-timestamp": False,
                                                        "block": False, "max-buffers": FPS * 4 if media == "video" else 400}),
            Element("queue", f"row-{row_id}-{media}q", {"max-size-time": 4_000_000_000, "max-size-buffers": 0,
                                                       "max-size-bytes": 0})]


def row_sink(row_id: str, kind: str, target: str, *, codec: str = "h264", container: str = "mkv") -> list[Element]:
    """The mux and sink a row's appsrcs link to; the first element takes both video and sound."""
    name = f"row-{row_id}"
    if kind == "rtmp":
        mux = Element("eflvmux" if codec != "h264" else "flvmux", f"{name}-mux", {"streamable": True})
        return [mux, Element("rtmp2sink", f"{name}-sink", {"location": target, "async-connect": True, "sync": False})]
    if kind == "srt":
        return [Element("mpegtsmux", f"{name}-mux", {"alignment": 7}),
                Element("srtsink", f"{name}-sink", {"uri": target, "sync": False, "wait-for-connection": False})]
    if kind == "whip":
        return [Element("whipclientsink", f"{name}-sink", {"signaller::whip-endpoint": target})]
    if kind == "file":
        if container == "mp4":
            # Finalised on EOS (moov written at the end); fragmented too, so a crash keeps what was written.
            mux = Element("mp4mux", f"{name}-mux", {"fragment-duration": 2000, "streamable": False})
        else:
            mux = Element("matroskamux", f"{name}-mux", {"writing-app": "Streamy"})
        return [mux, Element("filesink", f"{name}-sink", {"location": target, "async": False, "sync": False})]
    raise ValueError(f"unknown output kind {kind!r}")


def video_adapter(row_id: str, kind: str, codec: str) -> list[Element]:
    """What sits between a row's video queue and its mux: MPEG-TS takes H.264 as byte-stream with
    SPS/PPS before every key frame."""
    if kind == "srt" and codec == "h264":
        return [Element("h264parse", f"row-{row_id}-h264", {"config-interval": -1}),
                Element("capsfilter", f"row-{row_id}-bytestream",
                        {"caps": "video/x-h264,stream-format=byte-stream,alignment=au"})]
    return []


@dataclass
class RowStats:
    """One row's live numbers (the Stats panel reads these at most once a second)."""

    state: str = "off"  # off | connecting | live | reconnecting | finishing | error
    words: str = ""
    bytes: int = 0
    bitrate_kbps: float = 0.0
    frames: int = 0
    dropped_network: int = 0
    dropped_encoder: int = 0
    reconnects: int = 0
    rtt_ms: float | None = None
    started: float | None = None
    history: list = None  # bitrate samples, newest last

    def __post_init__(self) -> None:
        if self.history is None:
            self.history = []


HISTORY = 120


def state_words(stats: RowStats) -> str:
    """A row's status in words."""
    if stats.state == "live":
        return f"Live · {format_rate(stats.bitrate_kbps)}"
    if stats.state == "connecting":
        return "Connecting…"
    if stats.state == "reconnecting":
        return f"Reconnecting… {stats.words}".strip()
    if stats.state == "finishing":
        return "Finishing…"
    if stats.state == "error":
        return stats.words or "Couldn't connect"
    return stats.words or "Off"


def format_rate(kbps: float) -> str:
    if kbps >= 1000:
        return f"{kbps / 1000:.1f} Mb/s"
    return f"{kbps:.0f} kb/s"


def error_words(message: str, kind: str) -> str:
    """A GStreamer error as one line a person reads."""
    text = message.lower()
    if "refused" in text or "could not connect" in text or "connection" in text and "fail" in text:
        return "The server refused the connection"
    if "resolve" in text or "name or service" in text:
        return "The server's address wasn't found"
    if "timed out" in text or "timeout" in text:
        return "The server didn't answer"
    if "auth" in text or "unauthor" in text or "forbidden" in text or "denied" in text:
        return "The server didn't accept the stream key"
    if "no space" in text:
        return "The disk is full"
    if "permission" in text and kind == "file":
        return "Streamy can't write to that folder"
    return message[:120]
