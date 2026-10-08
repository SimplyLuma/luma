#!/usr/bin/env python3
"""Small physical-output tester for the Fairphone 6 audio bring-up."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")

from gi.repository import GLib, Gst, Gtk  # noqa: E402


ROUTE_HELPER = "/usr/libexec/luma-fp6-audio-route"


def format_time(nanoseconds: int) -> str:
    seconds = max(0, nanoseconds // Gst.SECOND)
    return f"{seconds // 60}:{seconds % 60:02d}"


class AudioTester(Gtk.Application):
    def __init__(self, media_path: Path) -> None:
        super().__init__(application_id="org.projectluma.FP6AudioTest")
        self.media_path = media_path
        self.player = Gst.ElementFactory.make("playbin", "player")
        if self.player is None:
            raise RuntimeError("GStreamer playbin is unavailable")
        self.player.set_property("uri", media_path.resolve().as_uri())
        self.player.set_property("volume", 0.70)

    def do_activate(self) -> None:
        window = Gtk.ApplicationWindow(application=self)
        window.set_title("FP6 Audio Test")
        window.set_default_size(420, 760)

        css = Gtk.CssProvider()
        css.load_from_data(
            b"""
            window { background: #f7f8fb; color: #182033; }
            .title { font-size: 28px; font-weight: 700; }
            .track { font-size: 22px; font-weight: 650; }
            .muted { color: #647086; }
            .route { min-height: 64px; font-size: 18px; font-weight: 650;
                     border-radius: 18px; }
            .play { min-height: 68px; font-size: 20px; font-weight: 700;
                    border-radius: 20px; }
            .status { min-height: 24px; }
            """
        )
        Gtk.StyleContext.add_provider_for_display(
            window.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        root.set_margin_top(72)
        root.set_margin_bottom(44)
        root.set_margin_start(26)
        root.set_margin_end(26)

        title = Gtk.Label(label="Audio output test")
        title.add_css_class("title")
        root.append(title)

        track = Gtk.Label(label="Money — Pink Floyd")
        track.add_css_class("track")
        root.append(track)

        hint = Gtk.Label(label="Choose where the music should play")
        hint.add_css_class("muted")
        root.append(hint)

        routes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.speaker_button = Gtk.ToggleButton(label="Speaker")
        self.speaker_button.add_css_class("route")
        self.speaker_button.set_hexpand(True)
        self.earpiece_button = Gtk.ToggleButton(label="Earpiece")
        self.earpiece_button.add_css_class("route")
        self.earpiece_button.set_hexpand(True)
        self.earpiece_button.set_group(self.speaker_button)
        self.speaker_button.connect("toggled", self.on_route, "speaker")
        self.earpiece_button.connect("toggled", self.on_route, "earpiece")
        routes.append(self.speaker_button)
        routes.append(self.earpiece_button)
        root.append(routes)

        self.progress = Gtk.ProgressBar()
        self.progress.set_hexpand(True)
        root.append(self.progress)

        times = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.elapsed = Gtk.Label(label="0:00")
        self.duration = Gtk.Label(label="0:00")
        self.duration.set_hexpand(True)
        self.duration.set_halign(Gtk.Align.END)
        times.append(self.elapsed)
        times.append(self.duration)
        root.append(times)

        self.play_button = Gtk.Button(label="Pause")
        self.play_button.add_css_class("suggested-action")
        self.play_button.add_css_class("play")
        self.play_button.connect("clicked", self.on_play_pause)
        root.append(self.play_button)

        self.status = Gtk.Label(label="Starting on Speaker at a safe volume…")
        self.status.add_css_class("muted")
        self.status.add_css_class("status")
        self.status.set_wrap(True)
        root.append(self.status)

        window.set_child(root)
        window.connect("close-request", self.on_close)
        window.present()
        window.fullscreen()

        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_bus_message)

        self.speaker_button.set_active(True)
        self.player.set_state(Gst.State.PLAYING)
        GLib.timeout_add(250, self.update_progress)

    def set_route(self, route: str) -> bool:
        try:
            result = subprocess.run(
                ["sudo", "-n", ROUTE_HELPER, route],
                check=True,
                capture_output=True,
                text=True,
                timeout=4,
            )
        except (OSError, subprocess.SubprocessError) as error:
            self.status.set_text(f"Could not select {route}: {error}")
            return False
        self.status.set_text(
            "Playing through the top receiver" if route == "earpiece"
            else "Playing through the phone speakers"
        )
        return result.returncode == 0

    def on_route(self, button: Gtk.ToggleButton, route: str) -> None:
        if not button.get_active():
            return
        if not self.set_route(route):
            other = self.earpiece_button if route == "speaker" else self.speaker_button
            other.set_active(True)

    def on_play_pause(self, _button: Gtk.Button) -> None:
        _result, state, _pending = self.player.get_state(0)
        if state == Gst.State.PLAYING:
            self.player.set_state(Gst.State.PAUSED)
            self.play_button.set_label("Play")
            self.status.set_text("Paused")
        else:
            self.player.set_state(Gst.State.PLAYING)
            self.play_button.set_label("Pause")

    def update_progress(self) -> bool:
        ok_position, position = self.player.query_position(Gst.Format.TIME)
        ok_duration, duration = self.player.query_duration(Gst.Format.TIME)
        if ok_position:
            self.elapsed.set_text(format_time(position))
        if ok_duration and duration > 0:
            self.duration.set_text(format_time(duration))
            self.progress.set_fraction(position / duration if ok_position else 0)
        return True

    def on_bus_message(self, _bus: Gst.Bus, message: Gst.Message) -> None:
        if message.type == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            self.player.set_state(Gst.State.NULL)
            self.play_button.set_label("Play")
            self.status.set_text(f"Playback error: {error.message}")
        elif message.type == Gst.MessageType.EOS:
            self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
            self.player.set_state(Gst.State.PLAYING)

    def on_close(self, _window: Gtk.Window) -> bool:
        self.player.set_state(Gst.State.NULL)
        self.set_route("speaker")
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("media", type=Path)
    args = parser.parse_args()
    if not args.media.is_file():
        parser.error(f"media file does not exist: {args.media}")
    Gst.init(None)
    return AudioTester(args.media).run([])


if __name__ == "__main__":
    raise SystemExit(main())
