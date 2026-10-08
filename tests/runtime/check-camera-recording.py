#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Bounded synthetic recording test. Never opens a camera or microphone."""
import tempfile
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
gi.require_version("GstPbutils", "1.0")
from gi.repository import Gtk, Gst, GLib, GstPbutils
from prairie_apps.camera_backend import CameraDevice
from prairie_apps.camera_video import VideoRecording
Gst.init(None)
Gtk.init()
original_make = Gst.ElementFactory.make

def synthetic(factory, name):
    if factory == "libcamerasrc":
        element = original_make("videotestsrc", name)
        element.set_property("is-live", True)
        if globals().get("device") is not None and device.stream_format == "image/jpeg":
            container = Gst.Bin.new(name)
            encoder = original_make("jpegenc", name + "-jpeg")
            container.add(element)
            container.add(encoder)
            assert element.link(encoder)
            container.add_pad(Gst.GhostPad.new("src", encoder.get_static_pad("src")))
            element = container
        setter = element.set_property
        element.set_property = lambda key, value: None if key == "camera-name" else setter(key, value)
        return element
    if factory == "autoaudiosrc":
        element = original_make("audiotestsrc", name)
        element.set_property("is-live", True)
        element.set_property("volume", .1)
        return element
    return original_make(factory, name)

loop = GLib.MainLoop()
errors = []
ended = []
paused_elapsed = []
still_samples = []

def message(_bus, msg, recorder):
    if msg.type == Gst.MessageType.ERROR:
        errors.append(msg.parse_error()[0].message)
        loop.quit()
    elif msg.type == Gst.MessageType.EOS:
        ended.append(True)
        loop.quit()

with tempfile.TemporaryDirectory(prefix="luma-camera-video-test-") as directory:
    output = Path(directory) / "take.webm"
    device = CameraDevice("synthetic", "Test", "external", preview_width=320, preview_height=240, desktop_uvc=True)
    with patch.object(Gst.ElementFactory, "make", side_effect=synthetic):
        recorder = VideoRecording(device, 2, output, message)
    recorder.start()
    def pause():
        data = recorder.grab_jpeg()
        assert data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9"), "still grab must encode a complete JPEG"
        still_samples.append(data)
        recorder.set_paused(True)
        paused_elapsed.append(recorder.elapsed)
        return False
    def resume():
        assert abs(recorder.elapsed - paused_elapsed[0]) < .05, "pause must freeze elapsed time"
        recorder.set_paused(False)
        return False
    GLib.timeout_add(1500, pause)
    GLib.timeout_add(2500, resume)
    GLib.timeout_add(4000, lambda: (recorder.stop(), False)[1])
    watchdog = GLib.timeout_add_seconds(12, lambda: (errors.append("timed out"), loop.quit(), False)[2])
    loop.run()
    if ended:
        GLib.source_remove(watchdog)
    duration = recorder.elapsed
    recorder.close()
    assert not errors, errors
    assert ended, "stop must receive EOS before publishing the take"
    info = GstPbutils.Discoverer.new(5 * Gst.SECOND).discover_uri(output.as_uri())
    assert info.get_result() == GstPbutils.DiscovererResult.OK
    assert info.get_video_streams() and info.get_audio_streams()
    seconds = info.get_duration() / Gst.SECOND
    assert 2 < seconds < 4, seconds
    print(f"PASS: playable WebM, audio + video, pause excluded, still grab, EOS; duration={seconds:.2f}s")

# Exercise the production window's save/session/close transitions with synthetic media.
from prairie_apps import camera
device = replace(device, stream_format="image/jpeg", still_width=320, still_height=240)
camera.CameraWindow._on_window_activity_changed = lambda *args: None
camera.CameraWindow._save_state = lambda *args: None
app = camera.CameraApplication()
app.set_application_id("org.projectluma.Camera.RecordingCheck")
window_errors = []
with tempfile.TemporaryDirectory(prefix="luma-camera-window-test-") as directory, patch.dict(os.environ):
    root = Path(directory)
    os.environ.update({"XDG_PICTURES_DIR": str(root),
                       **{f"XDG_{kind}_HOME": str(root / kind.lower())
                          for kind in ("CONFIG", "DATA", "CACHE", "STATE")}})
    existing = root / "older" / "existing.jpg"
    existing.parent.mkdir()
    existing.write_bytes(still_samples[0])
    library = camera.PhotoLibrary()
    source = library.ensure_default_source()
    library.scan_source(source.id)
    original = next(record for record in library.assets() if record.path == existing)
    library.set_favorite(original.id, True)
    library.set_metadata(original.id, caption="retain original caption")
    windows = []
    serial = [0]
    occupied = {}
    def new_path(*_args, suffix=".jpg", **_kwargs):
        serial[0] += 1
        requested = root / f"capture-{serial[0]}{suffix}"
        # Another save claims the chosen name before Camera publishes its media.
        content = f"existing media {serial[0]}".encode()
        requested.write_bytes(content)
        occupied[requested] = content
        return requested
    def first():
        try:
            w = app.props.active_window
            if w is None or not w._devices:
                GLib.timeout_add(100, first)
                return False
            w._preview_suspended = False
            windows.append(w)
            w._start_camera(0)
            w.state.zoom = 2
            GLib.timeout_add(700, photo)
        except Exception as error:
            window_errors.append(str(error)); app.quit()
        return False
    def photo():
        w = app.props.active_window
        w.state.timer = 3
        w._on_capture()
        assert w._surface.countdown.get_visible() and w._surface.countdown.get_label() == '3'
        GLib.timeout_add(5400, begin_video)
        return False
    def begin_video():
        try:
            w = app.props.active_window
            assert len(w.state.roll) == 1 and w.state.roll[0].path.is_file(), "JPEG camera capture must save a photo"
            assert w.state.roll[0].width == 320
            assert not w._surface.countdown.get_visible()
            w.state.timer = 0
            w.state.mode = "video"
            assert w._pipeline is not None, "photo capture must resume the preview"
            w._start_video()
            assert w.state.recording
            GLib.timeout_add(900, grab)
        except Exception as error:
            window_errors.append(str(error)); app.quit()
        return False
    def grab():
        app.props.active_window._grab_video_still(None)
        GLib.timeout_add(1100, stop)
        return False
    def stop():
        app.props.active_window._stop_video()
        GLib.timeout_add(700, check_session)
        return False
    def check_session():
        try:
            w = app.props.active_window
            if w.state.recording:
                GLib.timeout_add(300, check_session)
                return False
            if w._last_capture_record is None or w._last_capture_record.path != w.state.roll[0].path:
                GLib.timeout_add(300, check_session)
                return False
            assert w.state.screen == "capture"
            assert w._pipeline is not None, "stopping a clip must resume the preview"
            assert not w._surface.strip.get_visible(), "stopping must keep the viewfinder open"
            assert len(w.state.roll) == 3, [(shot.path.name, shot.duration) for shot in w.state.roll]
            assert all(shot.path.is_file() for shot in w.state.roll)
            assert all(shot.path not in occupied for shot in w.state.roll)
            assert all(path.read_bytes() == content for path, content in occupied.items())
            assert w.state.roll[0].duration is not None, "newest session item must be the clip"
            w._review_favorite_toggled(Gtk.ToggleButton(active=True))
            assert w._last_capture_record.favorite, "native favorite must persist through the catalog boundary"
            retained = library.asset(original.id)
            assert retained.favorite and retained.caption == "retain original caption"
            backups = list((root / "state/prairie-core/camera/backups").glob("*.sqlite3"))
            assert len(backups) == 1, "Camera must keep one prior catalog snapshot"
            with closing(sqlite3.connect(backups[0])) as saved:
                assert saved.execute("SELECT favorite,caption FROM assets WHERE id=?", (original.id,)).fetchone() == (1, "retain original caption")
                assert saved.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1, "backup must precede Camera indexing"
            w._surface.session()
            assert w._surface.strip.get_visible()
            w._start_video()
            GLib.timeout_add(1200, close_recording)
        except Exception as error:
            window_errors.append(str(error)); app.quit()
        return False
    def close_recording():
        app.props.active_window.close()
        return False
    GLib.timeout_add(300, first)
    GLib.timeout_add_seconds(15, lambda: (window_errors.append("window test timed out"), app.quit(), False)[2])
    with patch.object(Gst.ElementFactory, "make", side_effect=synthetic), patch.object(camera, "list_camera_devices", return_value=(device,)), patch.object(camera, "new_photo_path", side_effect=new_path):
        app.run([])
    assert not window_errors, window_errors
    assert windows and windows[0]._closed and app.props.active_window is None
    windows[0]._library_executor.shutdown(wait=True)
    assert existing.read_bytes() == still_samples[0]
    clips = [path for path in root.glob("*.webm") if path not in occupied]
    photos = [path for path in root.glob("*.jpg") if path not in occupied]
    assert len(clips) == 2, list(root.iterdir())
    assert len(photos) == 2
    assert len(occupied) == 4
    assert all(path.read_bytes() == content for path, content in occupied.items()), "existing media must survive every save"
    for clip in clips:
        info = GstPbutils.Discoverer.new(5 * Gst.SECOND).discover_uri(clip.as_uri())
        assert info.get_result() == GstPbutils.DiscovererResult.OK
        assert info.get_video_streams() and info.get_audio_streams()
    assert all(photo.read_bytes().startswith(b"\xff\xd8") and photo.read_bytes().endswith(b"\xff\xd9") for photo in photos)
    assert not list(root.glob(".luma-*")), "successful finalization must not leave partial takes"
    print("PASS: MJPEG photo + zoom, window still grab, session tray, close keeps second take; all4 colliding files preserved, both clips playable; disposable catalog favorite/old fields/one-time pre-write backup preserved")
