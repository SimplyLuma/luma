# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import tempfile

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gst, Gtk  # noqa: E402

# CameraWindow is also instantiated directly by package/runtime composition
# tests rather than only through CameraApplication.do_startup(). GStreamer
# initialization is process-global and idempotent, so establish the native
# media boundary at import time before any window can construct a pipeline.
Gst.init(None)

from luma_appkit import (  # noqa: E402
    add_style_sheet,
    AppWindow,
    Command,
    CommandGroup,
    CommandRegistry,
    EmptyState,
    InputMode,
    Island,
    PresentationMode,
    Toast,
    install_appkit,
    install_lumaui,
)

from .camera_backend import (
    CameraDevice,
    image_rotation_from_mount,
    list_camera_devices,
    new_photo_path,
    publish_capture,
    sensor_point_from_display,
    still_focus_position,
    video_direction_for_transform,
)
from .camera_state import CameraState, SessionShot, crop_edges, output_dimensions
from .camera_media import ensure_catalog_backup
from .camera_fixture import fixture_from_environment
from .camera_video import VideoRecording
from .phone_system import TransientShellSurface
from .photos_backend import PhotoLibrary, PhotoRecord, default_database_path


# Match the conventional phone-camera preview ceiling: a screen-oriented
# stream no larger than 1080p, with high-resolution capture remaining a
# separate path. On the FP6 CPU ISP this is a deliberate quality/latency
# compromise; requesting the maximum sensor resolution for the viewfinder is much
# slower and is not how Android CameraX or AVFoundation structure preview.
STILL_SETTLE_FRAMES = 24
APPLICATION_ID = "org.projectluma.Camera"
# The scratch-preview launcher overrides APP_ID after importing this module.
# Keep the production identity stable for icons and normal launches.
APP_ID = APPLICATION_ID
_camera_style_provider: Gtk.CssProvider | None = None


def _install_camera_theme() -> None:
    global _camera_style_provider
    display = Gdk.Display.get_default()
    if display is None or _camera_style_provider is not None:
        return
    # The kit owns this sheet, so it is reloaded when the surface
    # treatment changes (luma_appkit.add_style_sheet). Loaded through a
    # provider of its own it was not: whatever the palette was when the
    # application started stayed on screen, so switching to Glass left
    # this application painting Light colours next to windows that had
    # followed, which is one of the ways two windows came out different.
    provider = add_style_sheet(
        os.environ.get(
            "LUMA_CAMERA_STYLE_PATH", "/usr/share/prairie-core/camera.css"
        )
    )
    _camera_style_provider = provider


class CameraWindow(AppWindow):
    def __init__(self, application: Adw.Application) -> None:
        self._fixture = fixture_from_environment()
        def activate(action: str):
            return lambda: application.activate_action(action, None)

        commands = CommandRegistry(
            (
                CommandGroup(
                    None,
                    (
                        Command("camera.capture", "Take photo", activate("capture"), "camera", shortcut=("space",)),
                        Command("camera.switch", "Switch camera", activate("switch-camera"), "refresh-cw"),
                        Command("camera.grid", "Grid", activate("grid"), "grid-2x2"),
                        Command("camera.quit", "Quit Camera", self.close, "log-out", shortcut=("Ctrl", "Q")),
                    ),
                ),
            )
        )
        super().__init__(
            application=application,
            app_id=application.get_application_id(),
            title="Camera",
            icon_name=APPLICATION_ID,
            commands=commands,
            default_width=1180,
            default_height=740,
            minimum_width=360,
            minimum_height=480,
        )
        self.add_css_class("prairie-camera")
        self.shell_surface = None if self._fixture else TransientShellSurface(
            "org.projectluma.Camera.desktop"
        )
        if self.context.presentation is PresentationMode.FULLSCREEN:
            self.add_css_class("mobile")
            # Preserve the shell-owned status and gesture safe areas. Camera
            # is immersive inside that work area, not a compositor-fullscreen
            # surface that hides critical system state.
            self.maximize()
            self.title_bar.set_visible(False)
            if self.shell_surface is not None:
                self.shell_surface.set("deep")
        # v71: on a phone Camera is full black and runs under the clock; the kit's
        # phone frame (AppWindow on a phone device) takes the title row and frame away,
        # and Camera's own rows keep clear of the clock and the gesture chin.
        self.set_phone_bleed(True)
        if self.context.input_mode is InputMode.TOUCH:
            self.add_css_class("luma-touch")

        self.state = self._fixture.state if self._fixture else CameraState()
        self._desktop = self.context.presentation is PresentationMode.WINDOWED
        self._crop = None
        self._recorder = None
        self._video_clock_source = 0
        self._close_after_video = False
        self._pipeline: Gst.Pipeline | None = None
        self._source: Gst.Element | None = None
        self._bus: Gst.Bus | None = None
        self._stopping_pipeline: Gst.Pipeline | None = None
        self._pending_camera_index: int | None = None
        self._switch_wait_count = 0
        self._capture_pipeline: Gst.Pipeline | None = None
        self._capture_bus: Gst.Bus | None = None
        self._capture_device: CameraDevice | None = None
        self._capture_focus_position: float | None = None
        self._capture_frame_count = 0
        self._capture_release_count = 0
        self._capture_sample_received = False
        self._devices = ()
        self._timer_source = 0
        self._last_capture_path: Path | None = None
        self._last_capture_record: PhotoRecord | None = None
        self._closed = False
        self._discovery_generation = 0
        self._library_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="luma-camera-library"
        )
        # Handhelds release the CPU ISP when backgrounded. Desktop capture
        # remains live across focus changes, including during a recording.
        self._preview_suspended = True
        self.connect("notify::is-active", self._on_window_activity_changed)

        self._refresh_camera_availability()

    def _refresh_camera_availability(self) -> None:
        # Native discovery may invoke libcamera/V4L2; never block the window.
        self._discovery_generation += 1
        generation = self._discovery_generation
        if self._fixture:
            self._camera_devices_loaded(generation, self._fixture.devices, "")
            return
        self.set_body(EmptyState("Loading cameras…", "", "camera"))
        future = self._library_executor.submit(list_camera_devices)
        def finished(result):
            try:
                devices, error = result.result(), ""
            except Exception as failure:
                devices, error = (), str(failure)
            GLib.idle_add(self._camera_devices_loaded, generation, devices, error)
        future.add_done_callback(finished)

    def _camera_devices_loaded(self, generation, devices, error):
        if self._closed or generation != self._discovery_generation:
            return GLib.SOURCE_REMOVE
        self._devices = devices
        if self.state.source >= len(self._devices):
            self.state.select_source(0)
        if not self._devices:
            empty = EmptyState("No camera found",
                error or "Connect one, or check that another app isn’t using it.",
                "camera-off", primary=("Try again", self._refresh_camera_availability))
            island = Island()
            island.append(empty)
            self.set_body(island)
        else:
            self.set_body(self._build_camera_surface())
            self._on_window_activity_changed(self, None)
        return GLib.SOURCE_REMOVE

    def _show_fixture_camera(self, index: int) -> None:
        """Static fixture texture: never acquire a device or start GStreamer."""
        camera = self._fixture.cameras[index]
        self.state.source = index
        self._picture.set_paintable(Gdk.Texture.new_from_filename(str(camera.image)))
        self._source_label.set_label(camera.name)
        self._camera_label.set_label(camera.name)
        self._status.set_visible(False)
        self._grid_overlay.set_visible(self.state.grid)
        for source_index, check in self._source_checks.items():
            check.set_visible(source_index == index)
        self._capture_button.set_sensitive(True)
        self._switch_button.set_sensitive(len(self._devices) > 1)
        self._preview_suspended = False
        if hasattr(self, "_surface"):
            self._surface.refresh()

    def _build_camera_surface(self) -> Gtk.Widget:
        self._camera_stack = Gtk.Stack(hexpand=True, vexpand=True)
        self._camera_stack.add_named(self._build_viewfinder(), "camera")
        self._review_note = Gtk.Label()
        return self._camera_stack

    def _build_viewfinder(self) -> Gtk.Widget:
        from .camera_surface import CameraSurface
        self._surface = CameraSurface(self)
        return self._surface.host


    def _start_video(self):
        if getattr(self, "_fixture", None):
            self._fixture.require_live_io()
        if not self._devices or self._pipeline is None:
            return
        if not self._devices[self.state.source].desktop_uvc:
            self._show_pipeline_error("Video recording is not available for this camera.")
            return
        if self._pipeline is None or self._stopping_pipeline is not None or self._capture_pipeline is not None:
            return
        self._stop_camera()
        self._video_path = new_photo_path(suffix=".webm")
        descriptor, filename = tempfile.mkstemp(prefix=".luma-recording-", suffix=".webm", dir=self._video_path.parent)
        os.close(descriptor)
        self._video_temporary = Path(filename)
        try:
            recorder = VideoRecording(self._devices[self.state.source], self.state.zoom,
                                      self._video_temporary, self._video_message, audio=self.state.audio, mirror=self.state.mirror)
            self._recorder = recorder
            self._picture.set_paintable(recorder.sink.get_property("paintable"))
            recorder.start()
        except (RuntimeError, GLib.Error) as error:
            self._finish_video(str(error))
            return
        self.add_css_class("camera-recording")
        self.state.recording = True
        self.state.paused = False
        self._source_picker.set_sensitive(False)
        self._mode_picker.set_sensitive(False)
        self._settings_button.set_sensitive(False)
        if hasattr(self, "_zoom_picker"):
            self._zoom_picker.set_sensitive(False)
        self._capture_button.add_css_class("recording")
        self._picture.set_paintable(recorder.sink.get_property("paintable"))
        self._capture_button.update_property([Gtk.AccessibleProperty.LABEL], ["Stop and keep"])
        self._capture_button.set_tooltip_text("Stop and keep")
        self._status.set_visible(False)
        self._pause_button.set_icon_name("media-playback-pause-symbolic")
        self._pause_button.set_tooltip_text("Pause recording")
        self._video_controls.set_visible(True)
        self._audio_meter.set_visible(self.state.audio)
        self._video_clock_source = GLib.timeout_add(200, self._tick_video)
        self._surface.refresh()
        self._tick_video()

    def _tick_video(self):
        if self._recorder is None or not self.state.recording:
            self._video_clock_source = 0
            return GLib.SOURCE_REMOVE
        self.state.elapsed = self._recorder.elapsed
        self._surface.show_record_time()
        return GLib.SOURCE_CONTINUE

    def _pause_video(self, _button):
        if self._recorder is None:
            return
        self._recorder.set_paused(not self.state.paused)
        self.state.paused = self._recorder.paused
        label = "Resume recording" if self.state.paused else "Pause recording"
        self._pause_button.set_tooltip_text(label)
        self._pause_button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self._pause_button.set_icon_name("media-playback-start-symbolic" if self.state.paused else "media-playback-pause-symbolic")
        self._tick_video()

    def _stop_video(self):
        if self._recorder is None or self._recorder.stopping:
            return
        self._capture_button.set_sensitive(False)
        self._video_controls.set_sensitive(False)
        self._status.set_label("Keeping the take…")
        self._status.set_visible(True)
        try:
            self._recorder.stop()
        except RuntimeError as error:
            self._finish_video(str(error))
            return
        GLib.timeout_add_seconds(10, self._video_stop_timeout, self._recorder)

    def _video_stop_timeout(self, recorder):
        if recorder is self._recorder:
            self._finish_video("Finalizing the take timed out.")
        return GLib.SOURCE_REMOVE

    def _video_message(self, _bus, message, recorder):
        if recorder is not self._recorder:
            return
        if message.type == Gst.MessageType.EOS:
            self._finish_video()
        elif message.type == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            self._finish_video(error.message)
        elif message.type == Gst.MessageType.ELEMENT:
            structure = message.get_structure()
            if structure and structure.get_name() == "level":
                peaks = structure.get_value("peak")
                self._audio_meter.set_value(max(0.0, min(1.0, 10 ** (max(peaks) / 20))))

    def _finish_video(self, error=None):
        if self._video_clock_source:
            GLib.source_remove(self._video_clock_source)
            self._video_clock_source = 0
        recorder = self._recorder
        duration = recorder.elapsed if recorder else 0.0
        device = recorder.device if recorder else self._devices[self.state.source]
        thumbnail = None
        if recorder:
            paintable = self._picture.get_paintable()
            if paintable:
                thumbnail = paintable.get_current_image()
            recorder.close()
        self._recorder = None
        self.remove_css_class("camera-recording")
        self.state.recording = False
        self.state.paused = False
        self._capture_button.remove_css_class("recording")
        self._capture_button.set_sensitive(True)
        self._capture_button.update_property([Gtk.AccessibleProperty.LABEL], ["Start recording"])
        self._capture_button.set_tooltip_text("Start recording")
        self._video_controls.set_visible(False)
        self._video_controls.set_sensitive(True)
        self._audio_meter.set_visible(False)
        self._source_picker.set_sensitive(True)
        self._mode_picker.set_sensitive(True)
        self._settings_button.set_sensitive(True)
        if hasattr(self, "_zoom_picker"):
            self._zoom_picker.set_sensitive(True)
        if error is None:
            try:
                with self._video_temporary.open("rb") as stream:
                    os.fsync(stream.fileno())
                if self._video_temporary.stat().st_size == 0:
                    raise OSError("The encoder produced an empty take")
                self._video_path = publish_capture(self._video_temporary, self._video_path)
            except OSError as failure:
                error = str(failure)
        if error:
            if (self._desktop or self.is_active()) and not self._close_after_video:
                self._start_camera(self.state.source)
            dialog = Adw.AlertDialog(heading="The take could not be finalized", body=f"{error} The unfinished take is kept at {self._video_temporary}.")
            dialog.add_response("close", "Close")
            dialog.present(self)
        else:
            path = self._video_path
            if thumbnail is not None:
                self._surface.thumbnail_cache[path] = thumbnail
            shot = SessionShot(path, device.preview_width, device.preview_height, duration=duration)
            self.state.roll.insert(0, shot)
            self._last_capture_path = path
            self._last_capture_record = None
            self._last_shot_button.set_sensitive(True)
            self._refresh_session_tray()
            self._update_storage_label()
            future = self._library_executor.submit(self._index_capture, path)
            future.add_done_callback(lambda item: self._capture_indexed(path, item))
            if not self._close_after_video:
                Toast.show(self._surface.host, "Saved to Pictures › Camera")
                if self._desktop or self.is_active():
                    self._start_camera(self.state.source)
        self._surface.refresh()
        if self._close_after_video:
            self.close()

    def _grab_video_still(self, _button):
        if self._recorder is None:
            return
        self._video_still_button.set_sensitive(False)
        future = self._library_executor.submit(self._recorder.grab_jpeg)
        future.add_done_callback(lambda result: GLib.idle_add(self._video_still_ready, result))

    def _video_still_ready(self, future):
        self._video_still_button.set_sensitive(True)
        if self._closed:
            return GLib.SOURCE_REMOVE
        try:
            data = future.result()
        except (RuntimeError, GLib.Error) as error:
            self._show_pipeline_error(str(error))
            return GLib.SOURCE_REMOVE
        self._save_still_sample(None, data, recording_still=True)
        return GLib.SOURCE_REMOVE

    def _update_format_label(self):
        device = self._devices[self.state.source]
        megapixels = device.still_width * device.still_height / 1_000_000
        self._format_label.set_label(f"WebM · {device.preview_width} × {device.preview_height}" if self.state.mode == "video" else f"JPEG · {megapixels:.1f} MP")

    @staticmethod
    def _source_description(device: CameraDevice) -> str:
        label = device.label
        if "integrated camera" in label.casefold() or "built-in" in label.casefold():
            return "Built-in Camera"
        return label


    def _configure_crop(self, crop, width, height, aspect=None):
        horizontal, vertical = crop_edges(width, height, self.state.zoom, aspect)
        for name, value in (("left", horizontal), ("right", horizontal), ("top", vertical), ("bottom", vertical)):
            crop.set_property(name, value)


    def _refresh_session_tray(self):
        if hasattr(self, "_surface"):
            self._surface.update_session()


    def _end_capture_feedback(self):
        self._finder_island.remove_css_class("camera-shot-taken")
        return GLib.SOURCE_REMOVE


    def _update_storage_label(self) -> None:
        if self._fixture:
            self._storage_label.set_label("Pictures › Camera")
            return
        try:
            from .user_directories import photos_directory
            pictures = photos_directory()
            free = shutil.disk_usage(pictures if pictures.exists() else Path.home()).free
            label = f"Saves to Photos · this device · {free / (1024 ** 3):.0f} GB free"
        except OSError:
            label = "Saves to Photos · this device"
        self._storage_label.set_label(label)


    def _open_review(self, _button: Gtk.Button | None = None) -> None:
        # v70 keeps the session in the viewfinder instead of a review page.
        if hasattr(self, "_surface"):
            self._surface.update_session()
            self._surface.strip.set_visible(bool(self.state.roll))

    def _back_to_camera(self, _button: Gtk.Button | None = None) -> None:
        self.state.screen = "capture"
        self._camera_stack.set_visible_child_name("camera")
        self._preview_suspended = False
        if (self._desktop or self.is_active()) and self._pipeline is None:
            self._start_camera(self.state.source)

    def _review_favorite_toggled(self, button: Gtk.ToggleButton) -> None:
        if getattr(self, "_fixture", None):
            self._fixture.require_live_io()
        record = self._last_capture_record
        if record is None:
            button.set_active(False)
            return
        try:
            library = self._photo_library()
            library.set_favorite(record.id, button.get_active())
            self._last_capture_record = library.asset(record.id)
            for shot in self.state.roll:
                if shot.path == self._last_capture_path:
                    shot.record = self._last_capture_record
        except (KeyError, OSError):
            button.set_active(False)
            self._show_pipeline_error("Photos could not update the favourite state.")

    def _request_delete_review(self, _button: Gtk.Button) -> None:
        if self._fixture:
            return
        path = self._last_capture_path
        if path is None:
            return
        dialog = Adw.AlertDialog(
            heading="Move this photo to Trash?",
            body="The selected copy remains recoverable in Recently Deleted.",
        )
        dialog.add_response("cancel", "Keep")
        dialog.add_response("trash", "Move to Trash")
        dialog.set_response_appearance("trash", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._delete_review_response, path)
        dialog.present(self)

    def _delete_review_response(
        self, _dialog: Adw.AlertDialog, response: str, path: Path
    ) -> None:
        if getattr(self, "_fixture", None):
            self._fixture.require_live_io()
        if response != "trash":
            return
        record = self._last_capture_record
        try:
            if record is not None and record.copies:
                library = self._photo_library()
                current = library.asset(record.id)
                selected = next((copy for copy in current.copies if copy.path == path.resolve()), None)
                if selected is None:
                    raise KeyError("The selected capture is no longer in Photos")
                library.trash_copy(selected.id)
            else:
                Gio.File.new_for_path(str(path)).trash(None)
        except (GLib.Error, KeyError, OSError, ValueError) as error:
            self._show_pipeline_error(f"The photo was not moved to Trash: {error}")
            return
        self.state.roll[:] = [shot for shot in self.state.roll if shot.path != path]
        remaining = self.state.roll[0] if self.state.roll else None
        self._last_capture_path = remaining.path if remaining else None
        self._last_capture_record = remaining.record if remaining else None
        self._refresh_session_tray()
        self._back_to_camera()

    def _photo_library(self) -> PhotoLibrary:
        if getattr(self, "_fixture", None):
            self._fixture.require_live_io()
        database = default_database_path()
        state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
        ensure_catalog_backup(database, state / "prairie-core/camera/backups")
        return PhotoLibrary(database)

    def _index_capture(self, path: Path) -> PhotoRecord | None:
        if getattr(self, "_fixture", None):
            self._fixture.require_live_io()
        library = self._photo_library()
        source = library.ensure_default_source()
        library.scan_source(source.id)
        resolved = path.resolve()
        return next(
            (record for record in library.assets()
             if record.path == resolved or any(copy.path == resolved for copy in record.copies)),
            None,
        )

    def _capture_indexed(self, path: Path, future: Future[PhotoRecord | None]) -> None:
        try:
            record = future.result()
        except BaseException:
            record = None
        GLib.idle_add(self._apply_indexed_capture, path, record)

    def _apply_indexed_capture(
        self, path: Path, record: PhotoRecord | None
    ) -> bool:
        if self._closed:
            return GLib.SOURCE_REMOVE
        for shot in self.state.roll:
            if shot.path == path:
                shot.record = record
        if self._last_capture_path != path:
            return GLib.SOURCE_REMOVE
        self._last_capture_record = record
        self._review_note.set_label(
            "In Photos under Today · deleting removes the only known copy"
            if record is not None
            else "Saved on this device · Photos is still indexing this item"
        )
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _set_if_supported(element: Gst.Element, name: str, value: object) -> None:
        if element.find_property(name) is not None:
            element.set_property(name, value)

    def _disconnect_camera(self) -> Gst.Pipeline | None:
        if self._bus is not None:
            self._bus.remove_signal_watch()
            self._bus = None
        pipeline = self._pipeline
        self._pipeline = None
        self._source = None
        self._crop = None
        return pipeline

    def _stop_camera(self) -> None:
        pipeline = self._disconnect_camera()
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)
            pipeline.get_state(Gst.SECOND)

    def _start_camera(self, index: int) -> None:
        if getattr(self, "_fixture", None):
            self._show_fixture_camera(index)
            return
        self._stop_camera()
        self.state.source = index % len(self._devices)
        for lens_index, button in getattr(self, "_lens_buttons", {}).items():
            button.set_active(lens_index == self.state.source)
        device = self._devices[self.state.source]
        self._camera_label.set_label(device.label)
        self._source_label.set_label(self._source_description(device))
        for index, check in self._source_checks.items():
            check.set_visible(index == self.state.source)
        self._update_format_label()
        self._status.set_label(f"Starting {device.label.casefold()} camera…")
        self._status.set_visible(True)

        pipeline = Gst.Pipeline.new("prairie-camera-preview")
        source = Gst.ElementFactory.make("libcamerasrc", "camera-source")
        source_caps = Gst.ElementFactory.make("capsfilter", "sensor-mode-caps")
        decoder = Gst.ElementFactory.make("jpegdec" if device.stream_format == "image/jpeg" else "identity", "preview-decoder")
        rate = Gst.ElementFactory.make("videorate", "viewfinder-rate")
        crop = Gst.ElementFactory.make("videocrop", "preview-crop")
        scale = Gst.ElementFactory.make("videoscale", "preview-scale")
        preview_caps = Gst.ElementFactory.make("capsfilter", "preview-caps")
        flip = Gst.ElementFactory.make("videoflip", "mount-rotation")
        convert = Gst.ElementFactory.make("videoconvert", "preview-convert")
        queue = Gst.ElementFactory.make("queue", "preview-queue")
        sink = Gst.ElementFactory.make("gtk4paintablesink", "viewfinder-sink")
        if None in (
            pipeline,
            source,
            source_caps,
            decoder,
            rate,
            crop,
            scale,
            preview_caps,
            flip,
            queue,
            convert,
            sink,
        ):
            self._show_pipeline_error("The low-latency camera preview components are missing.")
            return

        source.set_property("camera-name", device.identifier)
        source_pad = source.get_static_pad("src")
        if source_pad is not None and source_pad.find_property("stream-role") is not None:
            # GstLibcameraStreamRole::Viewfinder. Preview and still capture are
            # intentionally distinct streams even though they use one sensor
            # at a time on the current Simple pipeline.
            source_pad.set_property("stream-role", 3)
        self._set_if_supported(source, "ae-enable", True)
        self._set_if_supported(source, "awb-enable", True)
        self._set_if_supported(source, "af-mode", 2)
        if device.preview_saturation is not None:
            self._set_if_supported(source, "saturation", device.preview_saturation)
        requested_caps = (
            f"{device.stream_format},"
            f"width={device.preview_width},height={device.preview_height},"
            "framerate=30/1"
        )
        if device.desktop_uvc:
            requested_caps = f"{device.stream_format},width={device.preview_width},height={device.preview_height}"
        source_caps.set_property("caps", Gst.Caps.from_string(requested_caps))
        rate.set_property("drop-only", True)
        self._set_if_supported(rate, "max-rate", device.viewfinder_framerate)
        # Bilinear is materially cleaner than nearest-neighbour when GTK must
        # fit the portrait preview to a high-density phone panel, while the
        # physical FP6 benchmarks keep it within the interactive budget.
        scale.set_property("method", 1)
        viewfinder_caps = (
            "video/x-raw,"
            f"width={device.viewfinder_width},height={device.viewfinder_height},"
            f"framerate={device.viewfinder_framerate}/1"
        )
        if device.desktop_uvc:
            viewfinder_caps = f"video/x-raw,width={device.viewfinder_width},height={device.viewfinder_height}"
        preview_caps.set_property("caps", Gst.Caps.from_string(viewfinder_caps))
        rotation = image_rotation_from_mount(
            device.rotation,
            front_facing=device.location == "front",
        )
        if device.desktop_uvc:
            rotation = 0
        flip.set_property(
            "video-direction",
            video_direction_for_transform(
                rotation,
                mirror_horizontal=device.location == "front" and self.state.mirror,
            ),
        )
        queue.set_property("max-size-buffers", 2)
        queue.set_property("max-size-bytes", 0)
        queue.set_property("max-size-time", 0)
        queue.set_property("leaky", 2)
        sink.set_property("sync", False)

        pipeline.add(source)
        pipeline.add(source_caps)
        pipeline.add(decoder)
        pipeline.add(rate)
        self._configure_crop(crop, device.preview_width, device.preview_height)
        self._crop = crop
        pipeline.add(crop)
        pipeline.add(scale)
        pipeline.add(preview_caps)
        pipeline.add(flip)
        pipeline.add(queue)
        pipeline.add(convert)
        pipeline.add(sink)
        if (
            not source.link(source_caps)
            or not source_caps.link(decoder)
            or not decoder.link(rate)
            or not rate.link(crop)
            or not crop.link(scale)
            or not scale.link(preview_caps)
            or not preview_caps.link(flip)
            or not flip.link(queue)
            or not queue.link(convert)
            or not convert.link(sink)
        ):
            pipeline.set_state(Gst.State.NULL)
            self._show_pipeline_error("The camera preview pipeline could not be connected.")
            return

        # A live camera pipeline is not required to emit ASYNC_DONE at the
        # point where its first usable buffer reaches the viewfinder.  Key the
        # visible ready state to that buffer instead; otherwise a healthy
        # sensor can remain covered by "Starting camera…" indefinitely.
        sink_pad = sink.get_static_pad("sink")
        if sink_pad is not None:
            sink_pad.add_probe(
                Gst.PadProbeType.BUFFER,
                self._on_first_frame,
                pipeline,
            )

        self._picture.set_paintable(sink.get_property("paintable"))
        self._pipeline = pipeline
        self._source = source
        self._bus = pipeline.get_bus()
        self._bus.add_signal_watch()
        self._bus.connect("message", self._on_bus_message)
        result = pipeline.set_state(Gst.State.PLAYING)
        if result is Gst.StateChangeReturn.FAILURE:
            self._show_pipeline_error("The camera could not start.")

    def _show_pipeline_error(self, message: str) -> None:
        self._status.set_label(message)
        self._status.set_visible(True)

    def _on_window_activity_changed(
        self,
        _window: Adw.ApplicationWindow,
        _parameter: GObject.ParamSpec,
    ) -> None:
        if self.state.recording:
            if not self._desktop and not self.is_active():
                self._stop_video()
            return
        if self.state.screen == "review" or not self._devices or not hasattr(self, "_picture"):
            return
        if not self._desktop and not self.is_active():
            self._preview_suspended = True
            # A switch or still capture may have disconnected the ordinary
            # preview already. Cancel every in-flight acquisition path so its
            # delayed release callback cannot start a new CPU-ISP pipeline
            # after Camera has moved to the background.
            pending_index = self._pending_camera_index
            if pending_index is not None:
                self.state.source = pending_index % len(self._devices)
            self._pending_camera_index = None
            stopping = self._stopping_pipeline
            self._stopping_pipeline = None
            if stopping is not None:
                stopping.set_state(Gst.State.NULL)
            self._cancel_background_capture()
            self._stop_camera()
            self._picture.set_paintable(None)
            self._status.set_label("Camera paused")
            self._status.set_visible(True)
            self._capture_button.set_sensitive(True)
            self._switch_button.set_sensitive(len(self._devices) > 1)
            return

        if not self._preview_suspended:
            return
        self._preview_suspended = False
        if (
            self._devices
            and self._pipeline is None
            and self._capture_pipeline is None
            and self._stopping_pipeline is None
        ):
            self._start_camera(self.state.source)

    def _cancel_background_capture(self) -> None:
        self._source_picker.set_sensitive(True)
        self._settings_button.set_sensitive(True)
        if hasattr(self, "_zoom_picker"):
            self._zoom_picker.set_sensitive(True)
        pipeline = self._capture_pipeline
        if self._capture_bus is not None:
            self._capture_bus.remove_signal_watch()
            self._capture_bus = None
        self._capture_pipeline = None
        self._capture_device = None
        self._capture_focus_position = None
        self._capture_sample_received = False
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)

    def _on_first_frame(
        self,
        _pad: Gst.Pad,
        _info: Gst.PadProbeInfo,
        pipeline: Gst.Pipeline,
    ) -> Gst.PadProbeReturn:
        GLib.idle_add(self._mark_camera_ready, pipeline)
        return Gst.PadProbeReturn.REMOVE

    def _mark_camera_ready(self, pipeline: Gst.Pipeline) -> bool:
        # Ignore a queued callback from a camera that was switched away before
        # GTK's main loop handled its first frame.
        if pipeline is self._pipeline:
            self._status.set_visible(False)
        return GLib.SOURCE_REMOVE

    def _on_bus_message(self, _bus: Gst.Bus, message: Gst.Message) -> None:
        if message.type is Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            self._show_pipeline_error(error.message)

    def _on_switch_camera(self, _button: Gtk.Button | None = None) -> None:
        if not self._devices:
            return
        self._switch_to_camera((self.state.source + 1) % len(self._devices))

    def _switch_to_camera(self, next_index: int) -> None:
        if getattr(self, "_fixture", None):
            self._fixture.select_camera(next_index)
            self._show_fixture_camera(next_index)
            return
        if self.state.recording or self._capture_pipeline is not None or self._timer_source:
            return
        if self._stopping_pipeline is not None:
            return
        if next_index == self.state.source:
            return
        next_device = self._devices[next_index]
        self.state.select_source(self.state.source)
        if hasattr(self, "_zoom_scale"):
            self._zoom_scale.set_value(1.0)
        self._pending_camera_index = next_index
        self._camera_label.set_label(next_device.label)
        self._status.set_label(f"Starting {next_device.label.casefold()} camera…")
        self._status.set_visible(True)
        self._switch_button.set_sensitive(False)
        self._picture.set_paintable(None)

        self._stopping_pipeline = self._disconnect_camera()
        self._switch_wait_count = 0
        if self._stopping_pipeline is None:
            self._finish_camera_switch()
            return
        self._stopping_pipeline.set_state(Gst.State.NULL)
        GLib.timeout_add(50, self._wait_for_camera_release)

    def _wait_for_camera_release(self) -> bool:
        pipeline = self._stopping_pipeline
        if pipeline is None:
            return GLib.SOURCE_REMOVE
        _result, current, _pending = pipeline.get_state(0)
        self._switch_wait_count += 1
        if current != Gst.State.NULL and self._switch_wait_count < 40:
            return GLib.SOURCE_CONTINUE
        self._stopping_pipeline = None
        self._finish_camera_switch()
        return GLib.SOURCE_REMOVE

    def _finish_camera_switch(self) -> None:
        index = self._pending_camera_index
        self._pending_camera_index = None
        if index is not None:
            self.state.source = index % len(self._devices)
            if not self._preview_suspended and (self._desktop or self.is_active()):
                self._start_camera(self.state.source)
        self._switch_button.set_sensitive(len(self._devices) > 1)

    def _on_capture(self, _button: Gtk.Button | None = None) -> None:
        if getattr(self, "_fixture", None):
            self._surface.capture_fixture()
            return
        if self._timer_source:
            return
        if self.state.mode == "video":
            if self.state.recording:
                self._stop_video()
            else:
                self._start_video()
            return
        if self.state.screen != "capture" or self._pipeline is None or self._capture_pipeline is not None or self._stopping_pipeline is not None:
            return
        if self.state.timer and self._timer_source == 0:
            self._capture_button.set_sensitive(False)
            self._timer_remaining = self.state.timer
            self._surface.countdown.set_label(str(self._timer_remaining))
            self._surface.countdown.set_visible(True)
            self._timer_source = GLib.timeout_add(1000, self._timer_tick)
            return
        if (
            self._pipeline is None
            or self._capture_pipeline is not None
            or self._stopping_pipeline is not None
        ):
            return

        self._source_picker.set_sensitive(False)
        self._settings_button.set_sensitive(False)
        if hasattr(self, "_zoom_picker"):
            self._zoom_picker.set_sensitive(False)
        self._capture_device = self._devices[self.state.source]
        self._capture_focus_position = self._capture_device.focus_position
        if (
            self._capture_focus_position is not None
            and self._source is not None
            and self._source.find_property("lens-position") is not None
        ):
            try:
                preview_position = self._source.get_property("lens-position")
            except (TypeError, ValueError):
                preview_position = None
            self._capture_focus_position = still_focus_position(
                preview_position,
                self._capture_focus_position,
            )
        print(
            "Prairie Camera still focus handoff: "
            f"{self._capture_focus_position}",
            flush=True,
        )
        self._capture_button.set_sensitive(False)
        self._switch_button.set_sensitive(False)
        paintable = self._picture.get_paintable()
        if paintable is not None:
            self._picture.set_paintable(paintable.get_current_image())

        self._stopping_pipeline = self._disconnect_camera()
        self._switch_wait_count = 0
        if self._stopping_pipeline is None:
            self._start_still_capture()
            return
        self._stopping_pipeline.set_state(Gst.State.NULL)
        GLib.timeout_add(50, self._wait_for_preview_release_for_capture)

    def _timer_tick(self) -> bool:
        self._timer_remaining -= 1
        if self._timer_remaining > 0:
            self._surface.countdown.set_label(str(self._timer_remaining))
            return GLib.SOURCE_CONTINUE
        self._timer_source = 0
        self._capture_button.set_sensitive(True)
        self._surface.countdown.set_visible(False)
        # Bypass the timer branch for this committed countdown only.
        saved = self.state.timer
        self.state.timer = 0
        try:
            self._on_capture()
        finally:
            self.state.timer = saved
        return GLib.SOURCE_REMOVE

    def _wait_for_preview_release_for_capture(self) -> bool:
        pipeline = self._stopping_pipeline
        if pipeline is None:
            return GLib.SOURCE_REMOVE
        _result, current, _pending = pipeline.get_state(0)
        self._switch_wait_count += 1
        if current != Gst.State.NULL and self._switch_wait_count < 40:
            return GLib.SOURCE_CONTINUE
        self._stopping_pipeline = None
        self._start_still_capture()
        return GLib.SOURCE_REMOVE

    def _start_still_capture(self) -> None:
        if getattr(self, "_fixture", None):
            self._fixture.require_live_io()
        device = self._capture_device
        if device is None:
            self._finish_capture("The camera selection was lost.")
            return

        pipeline = Gst.Pipeline.new("prairie-camera-still")
        source = Gst.ElementFactory.make("libcamerasrc", "still-camera-source")
        caps = Gst.ElementFactory.make("capsfilter", "still-caps")
        crop = Gst.ElementFactory.make("videocrop", "still-crop")
        scale = Gst.ElementFactory.make("videoscale", "still-scale")
        output_caps = Gst.ElementFactory.make("capsfilter", "still-output-caps")
        decoder = Gst.ElementFactory.make("jpegdec" if device.stream_format == "image/jpeg" else "identity", "still-decoder")
        counter = Gst.ElementFactory.make("identity", "still-settle-counter")
        valve = Gst.ElementFactory.make("valve", "still-valve")
        flip = Gst.ElementFactory.make("videoflip", "still-mount-rotation")
        convert = Gst.ElementFactory.make("videoconvert", "still-convert")
        encoder = Gst.ElementFactory.make("jpegenc", "still-jpeg")
        sink = Gst.ElementFactory.make("appsink", "still-sink")
        if None in (pipeline, source, caps, decoder, crop, scale, output_caps, counter, valve, flip, convert, encoder, sink):
            self._finish_capture("The high-resolution capture components are missing.")
            return

        source.set_property("camera-name", device.identifier)
        source_pad = source.get_static_pad("src")
        if source_pad is not None and source_pad.find_property("stream-role") is not None:
            source_pad.set_property("stream-role", 1)
        self._set_if_supported(source, "ae-enable", True)
        self._set_if_supported(source, "awb-enable", True)
        if self._capture_focus_position is not None:
            self._set_if_supported(source, "af-mode", 0)
            self._set_if_supported(
                source,
                "lens-position",
                self._capture_focus_position,
            )
        caps.set_property(
            "caps",
            Gst.Caps.from_string(
                f"{device.stream_format},"
                f"width={device.still_width},height={device.still_height}"
            ),
        )
        aspect = self._surface.capture_aspect(device)
        self._configure_crop(crop, device.still_width, device.still_height, aspect)
        self._capture_dimensions = output_dimensions(device.still_width, device.still_height, aspect)
        output_caps.set_property("caps", Gst.Caps.from_string(
            f"video/x-raw,width={self._capture_dimensions[0]},height={self._capture_dimensions[1]}"))
        valve.set_property("drop", True)
        rotation = image_rotation_from_mount(
            device.rotation,
            front_facing=device.location == "front",
        )
        if device.desktop_uvc:
            rotation = 0
        flip.set_property(
            "video-direction",
            video_direction_for_transform(rotation, mirror_horizontal=device.location == "front" and self.state.mirror),
        )
        encoder.set_property("quality", 95)
        sink.set_property("emit-signals", True)
        sink.set_property("sync", False)
        sink.set_property("max-buffers", 1)
        sink.set_property("drop", True)

        for element in (source, caps, decoder, counter, valve, crop, scale, output_caps, flip, convert, encoder, sink):
            pipeline.add(element)
        if not all(
            (
                source.link(caps),
                caps.link(decoder),
                decoder.link(counter),
                counter.link(valve),
                valve.link(crop),
                crop.link(scale),
                scale.link(output_caps),
                output_caps.link(flip),
                flip.link(convert),
                convert.link(encoder),
                encoder.link(sink),
            )
        ):
            pipeline.set_state(Gst.State.NULL)
            self._finish_capture("The high-resolution capture pipeline could not connect.")
            return

        counter_pad = counter.get_static_pad("src")
        if counter_pad is not None:
            counter_pad.add_probe(
                Gst.PadProbeType.BUFFER,
                self._on_still_settle_frame,
                pipeline,
            )
        sink.connect("new-sample", self._on_still_sample, pipeline)

        self._capture_pipeline = pipeline
        self._capture_bus = pipeline.get_bus()
        self._capture_bus.add_signal_watch()
        self._capture_bus.connect("message", self._on_still_bus_message, pipeline)
        self._capture_frame_count = 0
        self._capture_sample_received = False
        result = pipeline.set_state(Gst.State.PLAYING)
        if result is Gst.StateChangeReturn.FAILURE:
            self._finish_capture("The high-resolution camera could not start.")
            return
        GLib.timeout_add_seconds(20, self._on_capture_timeout, pipeline)

    def _on_still_settle_frame(
        self,
        _pad: Gst.Pad,
        _info: Gst.PadProbeInfo,
        pipeline: Gst.Pipeline,
    ) -> Gst.PadProbeReturn:
        self._capture_frame_count += 1
        if self._capture_frame_count < STILL_SETTLE_FRAMES:
            return Gst.PadProbeReturn.OK
        GLib.idle_add(self._open_still_valve, pipeline)
        return Gst.PadProbeReturn.REMOVE

    def _open_still_valve(self, pipeline: Gst.Pipeline) -> bool:
        if pipeline is self._capture_pipeline:
            valve = pipeline.get_by_name("still-valve")
            if valve is not None:
                valve.set_property("drop", False)
        return GLib.SOURCE_REMOVE

    def _on_still_sample(
        self,
        sink: Gst.Element,
        pipeline: Gst.Pipeline,
    ) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        if (
            sample is None
            or pipeline is not self._capture_pipeline
            or self._capture_sample_received
        ):
            return Gst.FlowReturn.OK
        buffer = sample.get_buffer()
        if buffer is None:
            return Gst.FlowReturn.ERROR
        self._capture_sample_received = True
        data = buffer.extract_dup(0, buffer.get_size())
        GLib.idle_add(self._save_still_sample, pipeline, data)
        return Gst.FlowReturn.EOS

    def _save_still_sample(self, pipeline: Gst.Pipeline | None, data: bytes, recording_still: bool = False) -> bool:
        if getattr(self, "_fixture", None):
            self._fixture.require_live_io()
        if not recording_still and pipeline is not self._capture_pipeline:
            return GLib.SOURCE_REMOVE
        temporary: Path | None = None
        try:
            path = new_photo_path()
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".luma-camera-", suffix=".partial", dir=path.parent
            )
            temporary = Path(temporary_name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            path = publish_capture(temporary, path)
        except OSError as error:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if recording_still:
                self._show_pipeline_error(f"The photo was not saved: {error.strerror}")
            else:
                self._finish_capture(f"The photo was not saved: {error.strerror}")
            return GLib.SOURCE_REMOVE
        device = self._devices[self.state.source] if recording_still else self._capture_device
        if device is not None:
            width, height = ((device.preview_width, device.preview_height) if recording_still
                             else self._capture_dimensions)
            self.state.roll.insert(0, SessionShot(path, width, height))
            self._refresh_session_tray()
        self._last_capture_path = path
        self._last_capture_record = None
        self._last_shot_button.set_sensitive(True)
        self._update_storage_label()
        future = self._library_executor.submit(self._index_capture, path)
        future.add_done_callback(lambda item: self._capture_indexed(path, item))
        if not recording_still:
            self._finish_capture(f"Saved {path.name}")
        self._surface.flash_capture()
        self._finder_island.add_css_class("camera-shot-taken")
        GLib.timeout_add(60, self._end_capture_feedback)
        return GLib.SOURCE_REMOVE


    def _on_still_bus_message(
        self,
        _bus: Gst.Bus,
        message: Gst.Message,
        pipeline: Gst.Pipeline,
    ) -> None:
        if pipeline is not self._capture_pipeline:
            return
        if message.type is Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            print(
                f"Prairie Camera still-capture error: {error.message}; {debug}",
                flush=True,
            )
            self._finish_capture(error.message)

    def _on_capture_timeout(self, pipeline: Gst.Pipeline) -> bool:
        if pipeline is self._capture_pipeline and not self._capture_sample_received:
            self._finish_capture("The high-resolution capture timed out.")
        return GLib.SOURCE_REMOVE

    def _finish_capture(self, message: str) -> None:
        print(f"Prairie Camera capture result: {message}", flush=True)
        pipeline = self._capture_pipeline
        if self._capture_bus is not None:
            self._capture_bus.remove_signal_watch()
            self._capture_bus = None
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)
        self._capture_release_count = 0
        self._status.set_label(message)
        self._status.set_visible(True)
        if pipeline is None:
            self._resume_preview_after_capture()
        else:
            GLib.timeout_add(50, self._wait_for_still_release, pipeline)

    def _wait_for_still_release(self, pipeline: Gst.Pipeline) -> bool:
        _result, current, _pending = pipeline.get_state(0)
        self._capture_release_count += 1
        if current != Gst.State.NULL and self._capture_release_count < 40:
            return GLib.SOURCE_CONTINUE
        if pipeline is self._capture_pipeline:
            self._capture_pipeline = None
        self._resume_preview_after_capture()
        return GLib.SOURCE_REMOVE

    def _resume_preview_after_capture(self) -> None:
        self._settings_button.set_sensitive(True)
        self._source_picker.set_sensitive(True)
        if hasattr(self, "_zoom_picker"):
            self._zoom_picker.set_sensitive(True)
        self._capture_pipeline = None
        self._capture_device = None
        self._capture_focus_position = None
        if not self._preview_suspended and (self._desktop or self.is_active()):
            self._start_camera(self.state.source)
        self._capture_button.set_sensitive(True)
        self._switch_button.set_sensitive(len(self._devices) > 1)


    def _set_mirror(self, enabled: bool) -> None:
        self.state.mirror = enabled
        if self._fixture or self._pipeline is None:
            return
        flip = self._pipeline.get_by_name("mount-rotation")
        if flip is None:
            return
        device = self._devices[self.state.source]
        rotation = 0 if device.desktop_uvc else image_rotation_from_mount(
            device.rotation, front_facing=device.location == "front")
        flip.set_property("video-direction", video_direction_for_transform(
            rotation, mirror_horizontal=device.location == "front" and enabled))

    def _request_refocus(self, x: float, y: float) -> None:
        if self._source is None or self._source.find_property("focus-point-x") is None or self._devices[self.state.source].desktop_uvc:
            return
        x = min(self._picture.get_width() * .92, max(self._picture.get_width() * .08, x))
        y = min(self._picture.get_height() * .92, max(self._picture.get_height() * .08, y))
        self.state.reticle = (x / self._picture.get_width(), y / self._picture.get_height())
        self._focus_reticle.set_margin_start(max(0, round(x - 36)))
        self._focus_reticle.set_margin_top(max(0, round(y - 36)))
        self._focus_reticle.set_visible(True)
        source = self._source
        if source is not None:
            device = self._devices[self.state.source]
            sensor_x, sensor_y = sensor_point_from_display(
                x,
                y,
                self._picture.get_width(),
                self._picture.get_height(),
                device.rotation,
                front_facing=device.location == "front",
                mirror_horizontal=device.location == "front" and self.state.mirror,
            )
            self._set_if_supported(source, "focus-point-x", sensor_x)
            self._set_if_supported(source, "focus-point-y", sensor_y)
        if source is not None and source.find_property("af-mode") is not None:
            source.set_property("af-mode", 0)
            # Keep Manual visible for several sensor frames. Shorter toggles
            # can be coalesced by libcamerasrc before a request reaches IPA.
            GLib.timeout_add(350, self._resume_continuous_focus)
            GLib.timeout_add(1800, self._resume_center_weighted_metering, source)
        GLib.timeout_add(900, self._hide_focus_reticle)

    def _resume_continuous_focus(self) -> bool:
        if self._source is not None:
            self._source.set_property("af-mode", 2)
        return GLib.SOURCE_REMOVE

    def _resume_center_weighted_metering(self, source: Gst.Element) -> bool:
        if source is self._source:
            self._set_if_supported(source, "ae-metering-mode", 0)
        return GLib.SOURCE_REMOVE

    def _hide_focus_reticle(self) -> bool:
        self._focus_reticle.set_visible(False)
        return GLib.SOURCE_REMOVE

    def do_close_request(self) -> bool:
        if self._recorder is not None:
            self._close_after_video = True
            self._stop_video()
            return True
        self._closed = True
        if hasattr(self, "_surface"):
            self._surface.dispose()
        if self.shell_surface is not None:
            self.shell_surface.clear()
        if self._timer_source:
            GLib.source_remove(self._timer_source)
            self._timer_source = 0
        self._stop_camera()
        if self._capture_pipeline is not None:
            self._capture_pipeline.set_state(Gst.State.NULL)
        self._library_executor.shutdown(wait=False, cancel_futures=True)
        return False


class CameraApplication(Adw.Application):
    def __init__(self) -> None:
        app_id = APP_ID + ".Fixture" if "LUMA_CAMERA_FIXTURE" in os.environ else APP_ID
        super().__init__(application_id=app_id)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        install_appkit()
        install_lumaui()
        _install_camera_theme()
        switch_action = Gio.SimpleAction.new("switch-camera", None)
        switch_action.connect("activate", self._on_switch_action)
        self.add_action(switch_action)
        refocus_action = Gio.SimpleAction.new("refocus", None)
        refocus_action.connect("activate", self._on_refocus_action)
        self.add_action(refocus_action)
        capture_action = Gio.SimpleAction.new("capture", None)
        capture_action.connect("activate", self._on_capture_action)
        self.add_action(capture_action)
        grid_action = Gio.SimpleAction.new("grid", None)
        grid_action.connect("activate", self._on_grid_action)
        self.add_action(grid_action)
        open_photos = Gio.SimpleAction.new("open-photos", None)
        open_photos.connect("activate", self._on_open_photos)
        self.add_action(open_photos)
        darkroom = Gio.SimpleAction.new("darkroom", None)
        darkroom.connect("activate", self._on_darkroom)
        self.add_action(darkroom)
        share = Gio.SimpleAction.new("share", None)
        # There is no freedesktop general Share portal in the deployed FP6
        # portal set. Keep the action explicitly disabled rather than silently
        # turning Share into Open With or fabricating success.
        share.set_enabled(False)
        self.add_action(share)
        self.set_accels_for_action("app.capture", ["space", "Return"])

    def _on_switch_action(
        self,
        _action: Gio.SimpleAction,
        _parameter: GLib.Variant | None,
    ) -> None:
        window = self.props.active_window
        if isinstance(window, CameraWindow):
            window._on_switch_camera()

    def _on_refocus_action(
        self,
        _action: Gio.SimpleAction,
        _parameter: GLib.Variant | None,
    ) -> None:
        window = self.props.active_window
        if isinstance(window, CameraWindow) and hasattr(window, "_picture"):
            window._request_refocus(
                window._picture.get_width() / 2,
                window._picture.get_height() / 2,
            )

    def _on_capture_action(
        self,
        _action: Gio.SimpleAction,
        _parameter: GLib.Variant | None,
    ) -> None:
        window = self.props.active_window
        if isinstance(window, CameraWindow):
            window._on_capture()

    def _on_grid_action(
        self,
        _action: Gio.SimpleAction,
        _parameter: GLib.Variant | None,
    ) -> None:
        window = self.props.active_window
        if isinstance(window, CameraWindow) and hasattr(window, "_surface"):
            window._surface.grid()

    def _on_open_photos(
        self,
        _action: Gio.SimpleAction,
        _parameter: GLib.Variant | None,
    ) -> None:
        import os

        window = self.props.active_window
        if getattr(window, "_fixture", None):
            from luma_appkit import Toast
            Toast.show(window._surface.host, "Sample captures stay in this preview", kind="notified")
            return
        if not isinstance(window, CameraWindow):
            return
        if os.environ.get("LUMA_CAMERA_PRIVATE_PREVIEW") == "1":
            from luma_appkit import Toast
            Toast.show(window._surface.host, "Captures stay in private review storage", kind="notified")
            return
        from luma_appkit.application_directory import launch
        files = ([Gio.File.new_for_path(str(window._last_capture_path))]
                 if window._last_capture_path is not None else [])
        launch("org.projectluma.Photos.desktop", files,
               callback=lambda opened, error: None if opened else
               window._show_pipeline_error(f"Photos did not open: {error}"))

    def _on_darkroom(
        self,
        _action: Gio.SimpleAction,
        _parameter: GLib.Variant | None,
    ) -> None:
        import os

        if (getattr(self.props.active_window, "_fixture", None) or
                os.environ.get("LUMA_CAMERA_PRIVATE_PREVIEW") == "1"):
            return
        window = self.props.active_window
        if not isinstance(window, CameraWindow) or window._last_capture_path is None:
            return
        from luma_appkit.application_directory import launch
        def finished(opened, error):
            if opened:
                return
            dialog = Adw.AlertDialog(heading="Darkroom did not open",
                                    body=f"{error}. The original photo remains unchanged in Photos.")
            dialog.add_response("close", "Close")
            dialog.present(window)
        launch("org.projectluma.Darkroom.desktop",
               [Gio.File.new_for_path(str(window._last_capture_path))], callback=finished)

    def do_activate(self) -> None:
        window = self.props.active_window or CameraWindow(self)
        window.present()


def main() -> int:
    return CameraApplication().run([])


if __name__ == "__main__":
    raise SystemExit(main())
