# SPDX-License-Identifier: Apache-2.0
"""Memo's native media boundary. No microphone is opened at import time."""
from __future__ import annotations

from array import array
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

from .memo_library import atomic_json, read_json, sidecar, validated_cues, reduce_peaks


def gst():
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init(None)
    return Gst


def gio():
    from gi.repository import Gio
    return Gio


@dataclass(frozen=True)
class RecordingCapability:
    available: bool
    reason: str
    source: str = ''


@dataclass(frozen=True)
class Recording:
    path: Path
    modified: datetime
    bytes: int
    duration: float | None = None
    source: str = ''
    transcript: tuple = ()
    transcript_state: str = 'unavailable'

    def matches(self, query: str) -> bool:
        query = query.casefold().strip()
        return query in self.path.stem.casefold() or any(query in c['text'].casefold() for c in self.transcript)


def format_duration(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return '—'
    value = max(0, int(seconds))
    hours, rest = divmod(value, 3600)
    minutes, seconds = divmod(rest, 60)
    return f'{hours}:{minutes:02}:{seconds:02}' if hours else f'{minutes}:{seconds:02}'


def format_size(value: int) -> str:
    return f'{value / 1024:.0f} KB' if value < 1024 * 1024 else f'{value / (1024 * 1024):.1f} MB'


def format_date(value: datetime, now: datetime | None = None) -> str:
    today = (now or datetime.now()).date()
    day = 'Today' if value.date() == today else ('Yesterday' if value.date() == today - timedelta(days=1) else value.strftime('%b %-d'))
    return f'{day}, {value.strftime("%-I:%M %p")}'


def recordings_directory(environment=None) -> Path:
    env = os.environ if environment is None else environment
    music = Path(env['XDG_MUSIC_DIR'].replace('$HOME', env.get('HOME', str(Path.home())))) if env.get('XDG_MUSIC_DIR') else Path(env.get('HOME', str(Path.home()))) / 'Music'
    return music / 'Voice Memos'


def checked(path: Path, root: Path | None = None, *, partial=False) -> Path:
    directory = (recordings_directory() if root is None else Path(root)).resolve()
    path = Path(path)
    if path.is_symlink() or path.parent.resolve() != directory or not path.is_file() or path.suffix != ('.partial' if partial else '.ogg'):
        raise ValueError('Recording is outside the library.')
    return path.absolute()


def peaks_path(path: Path) -> Path:
    return path.with_name(path.name + '.peaks.json')


def list_recordings(root=None) -> tuple[Recording, ...]:
    directory = recordings_directory() if root is None else Path(root)
    result = []
    if not directory.is_dir():
        return ()
    for path in directory.glob('*.ogg'):
        if path.is_symlink() or not path.is_file():
            continue
        info = path.stat()
        meta = read_json(sidecar(path))
        cache = read_json(peaks_path(path))
        duration = cache.get('duration') if cache.get('fingerprint') == [info.st_size, info.st_mtime_ns] else None
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
            duration = None
        result.append(Recording(path, datetime.fromtimestamp(info.st_mtime), info.st_size, duration,
            str(meta.get('source', '')), validated_cues(meta.get('transcript')), str(meta.get('transcript_state', 'unavailable'))))
    return tuple(sorted(result, key=lambda item: (item.modified, item.path.name), reverse=True))


def unique_path(directory: Path, title: str) -> Path:
    candidate = directory / f'{title}.ogg'
    count = 2
    while any(p.exists() or p.is_symlink() for p in (candidate, sidecar(candidate), peaks_path(candidate))):
        candidate = directory / f'{title} {count}.ogg'
        count += 1
    return candidate


def _publish(partial: Path, destination: Path) -> None:
    # Hard-link publication cannot overwrite an existing recording in a race.
    with partial.open('rb') as stream:
        os.fsync(stream.fileno())
    os.link(partial, destination)
    partial.unlink()
    fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def rename_recording(path: Path, name: str, root=None) -> Path:
    target = checked(path, root)
    clean = re.sub(r'[\\/]', '', name).strip().removesuffix('.ogg')
    if not clean or clean in ('.', '..'):
        raise ValueError('Enter a recording name.')
    destination = target.with_name(f'{clean}.ogg')
    if destination == target:
        return target
    pairs = [(target, destination)] + [(f(target), f(destination)) for f in (sidecar, peaks_path)]
    if any(b.exists() or b.is_symlink() for _, b in pairs):
        raise FileExistsError('A recording already has that name.')
    linked = []
    try:
        for source, dest in pairs:
            if source.is_file() and not source.is_symlink():
                os.link(source, dest)
                linked.append((source, dest))
    except OSError:
        for _, dest in linked:
            dest.unlink()
        raise
    for source, _ in linked:
        source.unlink()
    return destination


@dataclass
class TrashedRecording:
    path: Path
    uri: str

    def restore(self) -> Path:
        Gio = gio()
        Gio.File.new_for_uri(self.uri).move(Gio.File.new_for_path(str(self.path)), Gio.FileCopyFlags.NONE, None, None)
        return self.path


def _trash_items(path):
    Gio = gio()
    trash = Gio.File.new_for_uri('trash:///')
    result = {}
    enumerator = trash.enumerate_children('standard::name,trash::orig-path', Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
    try:
        while (info := enumerator.next_file(None)) is not None:
            original = info.get_attribute_byte_string('trash::orig-path')
            if original == str(path):
                result[info.get_name()] = trash.get_child(info.get_name()).get_uri()
    finally:
        enumerator.close(None)
    return result


def delete_recording(path: Path, root=None) -> TrashedRecording:
    target = checked(path, root)
    Gio = gio()
    # Sidecars remain beside the original path so undo retains metadata. They
    # are not audio and are never displayed without the matching recording.
    before = _trash_items(target)
    Gio.File.new_for_path(str(target)).trash(None)
    added = _trash_items(target).keys() - before.keys()
    after = _trash_items(target)
    uri = after[next(iter(added))] if added else ''
    return TrashedRecording(target, uri)


def permanently_delete_recording(path: Path, root=None) -> None:
    """Only the UI's separate, explicit unrecoverable-delete confirmation calls this."""
    checked(path, root).unlink()


def _element(name, label=None):
    value = gst().ElementFactory.make(name, label)
    if value is None:
        raise RuntimeError(f'The {name} audio component is not installed.')
    return value


class RecordingSession:
    def __init__(self, directory: Path, source: str, source_label: str, on_level=None, on_error=None):
        Gst = gst()
        self.directory = directory
        self.partial = directory / f'.{uuid.uuid4().hex}.partial'
        self.source_label = source_label
        self.error = None
        self.stopped = False
        self.paused = False
        self.on_level, self.on_error = on_level, on_error
        # Reserve the private file before filesink opens it.
        fd = os.open(self.partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        self.pipeline = Gst.Pipeline.new(None)
        elements = [_element(name) for name in (source, 'audioconvert', 'audioresample', 'level', 'opusenc', 'oggmux', 'filesink')]
        if source == 'audiotestsrc':
            elements[0].set_property('is-live', True)
        elements[3].set_property('interval', Gst.SECOND // 10)
        elements[3].set_property('post-messages', True)
        elements[4].set_property('bitrate', 32000)
        # Flush container pages regularly, including before a crash.
        elements[5].set_property('max-delay', Gst.SECOND // 10)
        elements[6].set_property('location', str(self.partial))
        elements[6].set_property('buffer-mode', 2)
        for element in elements:
            self.pipeline.add(element)
        for left, right in zip(elements, elements[1:]):
            if not left.link(right):
                raise RuntimeError('The recording pipeline could not be connected.')
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.handler = self.bus.connect('message', self._message)
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self._dispose()
            raise RuntimeError('The microphone could not start. Check its permission and audio route.')

    def _message(self, _bus, message):
        Gst = gst()
        if message.type == Gst.MessageType.ERROR:
            self.error = message.parse_error()[0].message
            self._dispose()
            if self.on_error:
                self.on_error(self.error)
        elif message.type == Gst.MessageType.ELEMENT:
            structure = message.get_structure()
            if structure and structure.get_name() == 'level' and self.on_level:
                values = structure.get_value('peak')
                self.on_level(min(1., 10 ** (max(values) / 20)) if values else 0.)

    def _dispose(self):
        self.pipeline.set_state(gst().State.NULL)
        if self.handler:
            self.bus.disconnect(self.handler)
            self.bus.remove_signal_watch()
            self.handler = 0

    def pause(self) -> None:
        if self.stopped or self.error:
            raise RuntimeError('The recording is no longer active.')
        if not self.paused:
            if self.pipeline.set_state(gst().State.PAUSED) == gst().StateChangeReturn.FAILURE:
                raise RuntimeError('The recording could not pause.')
            self.paused = True

    def resume(self) -> None:
        if self.stopped or self.error:
            raise RuntimeError('The recording is no longer active.')
        if self.paused:
            if self.pipeline.set_state(gst().State.PLAYING) == gst().StateChangeReturn.FAILURE:
                raise RuntimeError('The recording could not resume.')
            self.paused = False

    def stop(self, timeout=8) -> Path:
        Gst = gst()
        if self.stopped:
            raise RuntimeError('This recording has already stopped.')
        self.stopped = True
        # The worker owns EOS while saving; the UI bus watch must not consume it.
        if self.handler:
            self.bus.disconnect(self.handler)
            self.bus.remove_signal_watch()
            self.handler = 0
        try:
            if self.error:
                raise RuntimeError(self.error)
            if self.paused:
                self.pipeline.set_state(Gst.State.PLAYING)
                self.paused = False
            self.pipeline.send_event(Gst.Event.new_eos())
            message = self.bus.timed_pop_filtered(int(timeout * Gst.SECOND), Gst.MessageType.EOS | Gst.MessageType.ERROR)
            if message is None or message.type == Gst.MessageType.ERROR:
                raise RuntimeError('Recording could not finish. Its partial file is preserved for recovery.')
        finally:
            self._dispose()
        if not self.partial.is_file() or self.partial.stat().st_size == 0:
            raise RuntimeError('No audio was captured. The partial file has been preserved.')
        destination = unique_path(self.directory, 'New recording')
        _publish(self.partial, destination)
        atomic_json(sidecar(destination), {'version': 1, 'source': self.source_label})
        return destination


def start_recording(root=None, *, source='pipewiresrc', source_label='', on_level=None, on_error=None):
    directory = recordings_directory() if root is None else Path(root)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    return RecordingSession(directory, source, source_label, on_level, on_error)


class PlaybackSession:
    def __init__(self, path: Path, on_error=None, on_end=None, *, sink=None):
        Gst = gst()
        self.pipeline = _element('playbin')
        self.pipeline.set_property('uri', path.as_uri())
        self.pipeline.set_property('audio-filter', _element('scaletempo'))
        if sink:
            output = _element(sink)
            output.set_property('sync', True)
            self.pipeline.set_property('audio-sink', output)
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.handler = self.bus.connect('message', self._message)
        self.on_error, self.on_end = on_error, on_end
        self.playing = False
        self.rate = 1.
        self.ready = False
        self.pending_position = None
        self.pipeline.set_state(Gst.State.PAUSED)

    def _message(self, _bus, message):
        Gst = gst()
        if message.type == Gst.MessageType.ASYNC_DONE and not self.ready:
            self.ready = True
            if self.pending_position is not None:
                position, self.pending_position = self.pending_position, None
                self.seek(position)
        elif message.type == Gst.MessageType.ERROR:
            self.pause()
            if self.on_error:
                self.on_error(message.parse_error()[0].message)
        elif message.type == Gst.MessageType.EOS:
            self.pause()
            self.seek(0)
            if self.on_end:
                self.on_end()

    @property
    def position(self):
        ok, value = self.pipeline.query_position(gst().Format.TIME)
        return value / gst().SECOND if ok else 0.

    @property
    def duration(self):
        ok, value = self.pipeline.query_duration(gst().Format.TIME)
        return value / gst().SECOND if ok else 0.

    def play(self):
        if self.pipeline.set_state(gst().State.PLAYING) == gst().StateChangeReturn.FAILURE:
            raise RuntimeError('Playback could not start.')
        self.playing = True

    def pause(self):
        self.pipeline.set_state(gst().State.PAUSED)
        self.playing = False

    def seek(self, seconds):
        Gst = gst()
        if not self.ready:
            state = self.pipeline.get_state(0)
            self.ready = state[0] == Gst.StateChangeReturn.SUCCESS
        if not self.ready:
            self.pending_position = max(0., float(seconds))
            return True
        value = int(max(0., min(float(seconds), self.duration)) * Gst.SECOND)
        # Ogg/Opus KEY_UNIT snaps to an earlier page (observed 0.7 -> 0.4935s).
        # Accurate seeks keep cue clicks, the slider and the displayed time equal.
        flags = Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE
        if self.rate == 1:
            return self.pipeline.seek_simple(Gst.Format.TIME, flags, value)
        return self.pipeline.seek(self.rate, Gst.Format.TIME, flags, Gst.SeekType.SET, value, Gst.SeekType.NONE, -1)

    def set_rate(self, rate):
        if rate not in (0.5, 0.75, 1., 1.25, 1.5, 2.):
            raise ValueError('Unsupported playback speed.')
        previous = self.rate
        self.rate = rate
        if not self.ready:
            self.ready = self.pipeline.get_state(0)[0] == gst().StateChangeReturn.SUCCESS
        if not self.ready:
            self.pending_position = self.pending_position or 0.
            return
        # An explicit rate seek is also required when returning to 1×.
        Gst = gst()
        if not self.pipeline.seek(rate, Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
                                  Gst.SeekType.SET, int(self.position * Gst.SECOND), Gst.SeekType.NONE, -1):
            self.rate = previous
            raise RuntimeError('Playback speed could not be changed yet. Try again after the audio loads.')

    def stop(self):
        self.pipeline.set_state(gst().State.NULL)
        self.playing = False
        self.bus.disconnect(self.handler)
        self.bus.remove_signal_watch()


def start_playback(path, root=None, **kwargs):
    return PlaybackSession(checked(path, root), **kwargs)


def decode_peaks(path: Path) -> tuple[tuple[float, ...], float]:
    """Stream mono PCM into bounded envelope bins; never keep whole PCM in RAM."""
    Gst = gst()
    pipeline = Gst.Pipeline.new(None)
    decoder = _element('uridecodebin')
    decoder.set_property('uri', path.absolute().as_uri())
    convert, resample, caps, sink = [_element(n) for n in ('audioconvert', 'audioresample', 'capsfilter', 'appsink')]
    caps.set_property('caps', Gst.Caps.from_string('audio/x-raw,format=F32LE,channels=1,rate=8000'))
    sink.set_property('sync', False)
    sink.set_property('max-buffers', 4)
    for element in (decoder, convert, resample, caps, sink):
        pipeline.add(element)
    for left, right in zip((convert, resample, caps), (resample, caps, sink)):
        if not left.link(right):
            raise RuntimeError('Audio decoding could not be connected.')
    def pad_added(_decoder, pad):
        target = convert.get_static_pad('sink')
        if not target.is_linked() and pad.query_caps(None).to_string().startswith('audio/'):
            pad.link(target)
    decoder.connect('pad-added', pad_added)
    bins, pending, count, stride, samples = [], 0., 0, 800, 0
    bus = pipeline.get_bus()
    deadline = time.monotonic() + 30
    try:
        pipeline.set_state(Gst.State.PLAYING)
        while True:
            sample = sink.emit('try-pull-sample', Gst.SECOND // 4)
            if sample:
                deadline = time.monotonic() + 30
                buffer = sample.get_buffer()
                data = array('f')
                data.frombytes(buffer.extract_dup(0, buffer.get_size()))
                import sys
                if sys.byteorder != 'little':
                    data.byteswap()
                for value in data:
                    pending = max(pending, abs(value))
                    count += 1
                    samples += 1
                    if count == stride:
                        bins.append(pending)
                        pending, count = 0., 0
                        if len(bins) >= 4096:
                            bins = [max(bins[i:i+2]) for i in range(0, len(bins), 2)]
                            stride *= 2
            else:
                error = bus.pop_filtered(Gst.MessageType.ERROR)
                if error:
                    raise RuntimeError(error.parse_error()[0].message)
                if sink.get_property('eos'):
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError('Audio decoding timed out.')
        if count:
            bins.append(pending)
        if not samples:
            raise RuntimeError('This file contains no decodable audio.')
        known, container_duration = pipeline.query_duration(Gst.Format.TIME)
        duration = container_duration / Gst.SECOND if known and container_duration > 0 else samples / 8000
        return reduce_peaks(bins, min(180, len(bins))), duration
    finally:
        pipeline.set_state(Gst.State.NULL)


def load_peaks(path: Path, root=None):
    path = checked(path, root)
    info = path.stat()
    fingerprint = [info.st_size, info.st_mtime_ns]
    cached = read_json(peaks_path(path))
    peaks, duration = cached.get('peaks'), cached.get('duration')
    if (cached.get('version') == 1 and cached.get('fingerprint') == fingerprint and
            isinstance(peaks, list) and 0 < len(peaks) <= 180 and
            all(isinstance(v, (float, int)) and math.isfinite(v) and 0 <= v <= 1 for v in peaks) and
            isinstance(duration, (float, int)) and math.isfinite(duration) and duration > 0):
        return tuple(peaks), duration
    peaks, duration = decode_peaks(path)
    atomic_json(peaks_path(path), {'version': 1, 'fingerprint': fingerprint, 'peaks': peaks, 'duration': duration})
    return peaks, duration


def continue_recording(original: Path, segment: Path, root=None) -> Path:
    """Publish a real combined copy, keeping both inputs recoverable.

    Ogg bytes cannot be appended safely. Decode each part in order, normalize
    segment timestamps, and encode one stream; publish only after validation.
    """
    original, segment = checked(original, root), checked(segment, root)
    if original == segment:
        raise ValueError('Choose two different recording parts.')
    sources = (original, segment)
    fingerprints = [(p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ino) for p in sources]
    durations = [decode_peaks(p)[1] for p in sources]
    Gst = gst()
    pipeline = Gst.Pipeline.new(None)
    concat, continuity, convert, resample, encoder, mux, sink = [
        _element(n) for n in ('concat', 'identity', 'audioconvert', 'audioresample', 'opusenc', 'oggmux', 'filesink')]
    concat.set_property('adjust-base', True)
    continuity.set_property('single-segment', True)
    encoder.set_property('bitrate', 32000)
    temporary = original.parent / f'.continuation-{uuid.uuid4().hex}.partial'
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    sink.set_property('location', str(temporary))
    output = (concat, continuity, convert, resample, encoder, mux, sink)
    for element in output:
        pipeline.add(element)
    try:
        for left, right in zip(output, output[1:]):
            if not left.link(right):
                raise RuntimeError('Recording parts could not be connected.')
        for path in sources:
            decoder, part_convert, part_resample, caps, queue = [
                _element(n) for n in ('uridecodebin', 'audioconvert', 'audioresample', 'capsfilter', 'queue')]
            decoder.set_property('uri', path.as_uri())
            caps.set_property('caps', Gst.Caps.from_string('audio/x-raw,rate=48000,channels=1'))
            for element in (decoder, part_convert, part_resample, caps, queue):
                pipeline.add(element)
            branch = (part_convert, part_resample, caps, queue)
            for left, right in zip(branch, branch[1:]):
                if not left.link(right):
                    raise RuntimeError('A recording part could not be decoded.')
            target = concat.request_pad_simple('sink_%u')
            if target is None or queue.get_static_pad('src').link(target) != Gst.PadLinkReturn.OK:
                raise RuntimeError('A recording part could not be ordered.')
            def pad_added(_decoder, pad, destination=part_convert):
                target_pad = destination.get_static_pad('sink')
                if not target_pad.is_linked() and pad.query_caps(None).to_string().startswith('audio/'):
                    pad.link(target_pad)
            decoder.connect('pad-added', pad_added)
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('Recording continuation could not start.')
        message = pipeline.get_bus().timed_pop_filtered(600 * Gst.SECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if message is None:
            raise RuntimeError('Recording continuation timed out; both parts were kept.')
        if message.type == Gst.MessageType.ERROR:
            raise RuntimeError(message.parse_error()[0].message)
        pipeline.set_state(Gst.State.NULL)
        peaks, duration = decode_peaks(temporary)
        expected = sum(durations)
        if abs(duration - expected) > max(.15, expected * .001):
            raise RuntimeError('The continued audio was incomplete; both parts were kept.')
        if [(p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ino) for p in sources] != fingerprints:
            raise RuntimeError('A recording changed while joining; both parts were kept.')
        destination = unique_path(original.parent, original.stem + ' continued')
        first, second = (read_json(sidecar(p)) for p in sources)
        cues = list(validated_cues(first.get('transcript')))
        cues.extend(dict(cue, start=cue['start'] + durations[0], end=cue['end'] + durations[0])
                    for cue in validated_cues(second.get('transcript')))
        metadata = {'version': 1, 'source': first.get('source', ''),
                    'parts': [p.name for p in sources], 'transcript': cues,
                    'transcript_state': 'complete' if all(m.get('transcript_state') == 'complete' for m in (first, second)) else 'partial'}
        # A sidecar failure cannot consume either input. Audio publication is
        # no-replace and metadata/cache writes remain independently atomic.
        _publish(temporary, destination)
        atomic_json(sidecar(destination), metadata)
        info = destination.stat()
        atomic_json(peaks_path(destination), {'version': 1, 'fingerprint': [info.st_size, info.st_mtime_ns],
                                             'peaks': peaks, 'duration': duration})
        return destination
    finally:
        pipeline.set_state(Gst.State.NULL)
        temporary.unlink(missing_ok=True)


def recover_partials(root=None):
    directory = recordings_directory() if root is None else Path(root)
    recovered, errors = [], []
    for path in directory.glob('*.partial'):
        try:
            checked(path, directory, partial=True)
            if path.stat().st_size == 0:
                continue
            peaks, duration = decode_peaks(path)
            destination = unique_path(directory, 'Recovered recording')
            _publish(path, destination)
            info = destination.stat()
            atomic_json(peaks_path(destination), {'version': 1, 'fingerprint': [info.st_size, info.st_mtime_ns], 'peaks': peaks, 'duration': duration})
            recovered.append(destination)
        except (OSError, ValueError, RuntimeError) as error:
            errors.append(f'{path.name}: {error}')
    return recovered, errors


def inspect_recording_capability() -> RecordingCapability:
    try:
        Gst = gst()
        for name in ('pipewiresrc', 'audioconvert', 'audioresample', 'level', 'opusenc', 'oggmux'):
            if not Gst.ElementFactory.find(name):
                return RecordingCapability(False, f'The {name} audio component is not installed.')
        result = subprocess.run(['wpctl', 'inspect', '@DEFAULT_AUDIO_SOURCE@'], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError, ImportError, ValueError) as error:
        return RecordingCapability(False, f'The audio service is unavailable: {error}')
    if result.returncode:
        return RecordingCapability(False, 'No microphone input is available on this device yet.')
    match = re.search(r'node.description\s*=\s*"([^"\n]+)"', result.stdout)
    return RecordingCapability(True, 'A microphone recording route is available.', match.group(1) if match else 'Default microphone')
