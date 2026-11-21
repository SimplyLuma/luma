# SPDX-License-Identifier: Apache-2.0
"""The Destinations panel: one row per place Go live sends to, and the recording.

A row shows the platform's logo, the account or name, the canvas it takes (a chip that switches
it), an on/off switch and its status in words. Adding and editing happen in the action bar's editor:
Twitch and YouTube sign in (accounts.py); Kick, TikTok and Custom take a server and a stream key;
the recording takes a format, a canvas and a folder. Keys and sign-ins live in the keyring (Secret
schema org.projectluma.Streamy.Destination, attributes account = the row's id, kind).

    panel = DestinationsPanel(store, center=window.center, host=window.host, outputs=outputs)
    DockTile("destinations", "Destinations", "radio-tower", panel, phone="drawer", actions=panel.actions)
    panel.go_live() / panel.end()        # every enabled row; the recording row with record=True
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from luma_appkit import (  # noqa: E402
    ActionCenter, ActionEditor, AppIcon, BarAction, IconOnlyButton, ModeSwitch, NavigationList,
    PanelHeading, PanelRow, RowLead, SidebarRow, Switch, TextButton, Toast, ToggleChip,
)
from luma_appkit.action_bubble import FloatingMenu, MenuItem  # noqa: E402
from luma_appkit.bar_panel import panel_list  # noqa: E402

from . import accounts  # noqa: E402
from . import output_graph as og  # noqa: E402
from .destinations import PLATFORMS, Destination, DestinationStore  # noqa: E402
from .model import FORMATS  # noqa: E402

SECRET_SCHEMA = "org.projectluma.Streamy.Destination"
KEY, SIGN_IN = "stream-key", "oauth"
CANVAS_ICONS = {"landscape": "rectangle-horizontal", "vertical": "smartphone"}
#: What "Add" offers, in order.
ADDABLE = ("twitch", "youtube", "kick", "tiktok", "custom")


def logos_dir() -> Path:
    here = Path(__file__).resolve().parent.parent / "data" / "logos"
    return here if here.is_dir() else Path("/usr/share/luma-streamy/logos")


def logo(platform: str, size: int = 32) -> Gtk.Widget | None:
    """The platform's official logo (data/logos/<platform>.svg) as a LumaUI app icon, or None (Custom,
    the recording)."""
    path = logos_dir() / f"{platform}.svg"
    if not path.is_file():
        return None
    from luma_appkit.svg_icons import SvgIcon

    icon = AppIcon(picture=SvgIcon(str(path), size), name=PLATFORMS[platform][0], size=size)
    icon.update_property([Gtk.AccessibleProperty.LABEL], [PLATFORMS[platform][0]])
    return icon


def lead_for(row: Destination) -> Gtk.Widget:
    return logo(row.platform) or RowLead.well("circle-dot" if row.platform == "record" else "server")


def canvas_label(canvas: str) -> str:
    return FORMATS[canvas][0].split()[0]


def recordings_dir() -> Path:
    videos = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_VIDEOS) or str(Path.home() / "Videos")
    return Path(videos) / "Streamy"


class DestinationsPanel(Gtk.Box):
    """The panel's content (the window puts it in a DockTile with ``actions``)."""

    __gtype_name__ = "StreamyDestinationsPanel"

    def __init__(self, store: DestinationStore, *, center: ActionCenter, host: Gtk.Widget, outputs=None,
                 secrets=None, on_changed: Callable[[], None] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.add_css_class("streamy-destinations")
        self.store, self.center, self.host, self.outputs = store, center, host, outputs
        self._secrets = secrets
        self.on_changed = on_changed
        self.states: dict[str, tuple[str, str]] = {}  # row id -> (state, words) while live
        self._rows: dict[str, SidebarRow] = {}
        self._signing_in = None
        self.nav = NavigationList("files")
        self.list = self.nav.list
        self.list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list.connect("row-activated", lambda _l, r: getattr(r, "row_id", None) and self.edit(r.row_id))
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(self.nav)
        self.append(scroll)
        add = IconOnlyButton("plus", "Add a destination", on_click=self.add_menu)
        self.actions = [add]
        self._narrow = False
        from luma_appkit import WidthWatch

        self._watch = WidthWatch(self, on_tier=self._tier)
        if outputs is not None:
            outputs.connect("row-state", self._row_state)
            outputs.connect("tick", lambda *_a: self._refresh_live())
        self.refresh()

    def _tier(self, tier: str) -> None:
        """On a phone a row's canvas chip shows its glyph alone, so the status keeps its words."""
        self._narrow = tier == "phone"
        for widget in self._rows.values():
            widget.chip.label.set_visible(not self._narrow)

    # ── Keyring ──

    @property
    def secrets(self):
        if self._secrets is None:
            from luma_appkit.platform import secret_store

            self._secrets = secret_store(SECRET_SCHEMA)
        return self._secrets

    def key_for(self, row: Destination) -> str:
        try:
            return self.secrets.lookup(row.id, KEY) or ""
        except (GLib.Error, OSError):
            return ""

    def _store_key(self, row: Destination, key: str) -> bool:
        try:
            if key:
                self.secrets.store(row.id, KEY, key, label=f"Streamy stream key ({row.label})")
            else:
                self.secrets.clear(row.id, KEY)
            return True
        except (GLib.Error, OSError):
            Toast.show(self.host, "The stream key couldn't be saved in the keyring", kind="warning")
            return False

    # ── Rows ──

    def refresh(self) -> None:
        child = self.list.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.list.remove(child)
            child = following
        self._rows.clear()
        # The recording is the first row; destinations added with + follow it in the order they came.
        for row in sorted(self.store.rows, key=lambda r: r.is_stream):
            self.list.append(self._row(row))

    def _row(self, row: Destination) -> SidebarRow:
        chip = ToggleChip(canvas_label(row.canvas), icon=CANVAS_ICONS[row.canvas], active=True,
                          live=self._live(row.id), on_toggle=lambda _on, r=row: self._switch_canvas(r))
        chip.update_property([Gtk.AccessibleProperty.LABEL], [f"Canvas: {canvas_label(row.canvas)}"])
        chip.set_tooltip_text("Switch canvas")
        switch = Switch(active=row.enabled, on_toggle=lambda on, r=row: self._toggle(r, on),
                        label=f"Send to {row.label}" if row.is_stream else "Record when going live")
        trail = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        trail.append(chip)
        trail.append(switch)
        title = row.account or row.label
        widget = SidebarRow(title, lead=lead_for(row), subtitle=self.status(row), trail=trail,
                            muted=not row.enabled)
        widget.row_id = row.id
        widget.set_activatable(True)
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", lambda _g, _n, x, y, r=row, w=widget: self._menu(r, w, x, y))
        widget.add_controller(menu)
        hold = Gtk.GestureLongPress(touch_only=True)
        hold.connect("pressed", lambda _g, x, y, r=row, w=widget: self._menu(r, w, x, y))
        widget.add_controller(hold)
        widget.chip, widget.switch = chip, switch
        chip.label.set_visible(not self._narrow)
        self._rows[row.id] = widget
        return widget

    def status(self, row: Destination) -> str:
        """The row's status in words."""
        if row.id in self.states:
            state, words = self.states[row.id]
            if state == "live" and not row.is_stream:
                stats = self.outputs.stats(row.id) if self.outputs is not None else None
                since = stats.started if stats is not None and stats.started else time.monotonic()
                return f"Recording · {_clock(time.monotonic() - since)}"
            return words
        detail = canvas_label(row.canvas)
        if not row.is_stream:
            return f"{row.container.upper()} · {detail}" + ("" if row.enabled else " · Off")
        if not row.enabled:
            return "Off"
        if row.signs_in and not row.account and not self.key_for(row):
            return f"Sign in to {PLATFORMS[row.platform][0]}"
        if not row.ingest:
            return "Needs a server"
        if not self.key_for(row) and row.ingest.startswith(("rtmp://", "rtmps://")) and row.platform != "custom":
            return "Needs a stream key"
        return "Ready"

    def _live(self, row_id: str) -> bool:
        return self.states.get(row_id, ("off", ""))[0] in ("connecting", "live", "reconnecting", "finishing")

    def _update(self, row_id: str) -> None:
        widget, row = self._rows.get(row_id), self.store.get(row_id)
        if widget is None or row is None:
            return
        widget.set_subtitle(self.status(row))
        widget.chip.set_live(self._live(row_id))

    def _row_state(self, _outputs, row_id: str, state: str, words: str) -> None:
        if state == "off":
            self.states.pop(row_id, None)
        else:
            self.states[row_id] = (state, words)
        self._update(row_id)
        if state == "error":
            row = self.store.get(row_id)
            Toast.show(self.host, f"{row.label if row else 'A destination'}: {words}", kind="warning")

    def _refresh_live(self) -> None:
        for row_id in list(self.states):
            stats = self.outputs.stats(row_id)
            if stats is not None and stats.state == "live":
                self.states[row_id] = ("live", og.state_words(stats))
                self._update(row_id)

    def _toggle(self, row: Destination, on: bool) -> None:
        row.enabled = on
        self.store.update(row)
        widget = self._rows.get(row.id)
        if widget is not None:
            widget.set_css_classes([c for c in widget.get_css_classes() if c != "muted"] + ([] if on else ["muted"]))
        if self.outputs is not None and self.outputs.live() and row.is_stream:
            # While live, the switch starts or ends just this row.
            if on:
                self._start(row)
            else:
                self.outputs.stop(row.id)
        self._update(row.id)
        self._changed()

    def _switch_canvas(self, row: Destination) -> bool:
        if self._live(row.id):
            Toast.show(self.host, "End this destination to change its canvas", kind="warning")
            return False
        row.canvas = "vertical" if row.canvas == "landscape" else "landscape"
        self.store.update(row)
        widget = self._rows.get(row.id)
        if widget is not None:
            widget.chip.label.set_label(canvas_label(row.canvas))
            widget.chip.update_property([Gtk.AccessibleProperty.LABEL], [f"Canvas: {canvas_label(row.canvas)}"])
        self._update(row.id)
        self._changed()
        return False  # the chip keeps its look: it names the canvas, it isn't a switch

    def _menu(self, row: Destination, anchor: Gtk.Widget, x: float, y: float) -> None:
        rows = [MenuItem("Edit…", icon="pencil", on_activate=lambda: self.edit(row.id)),
                MenuItem(f"Use {canvas_label('vertical' if row.canvas == 'landscape' else 'landscape')}",
                         icon=CANVAS_ICONS["vertical" if row.canvas == "landscape" else "landscape"],
                         on_activate=lambda: self._switch_canvas(row))]
        if row.is_stream:
            if row.signs_in and row.account:
                rows.append(MenuItem("Sign out", icon="log-out", on_activate=lambda: self.sign_out(row)))
            rows += [None, MenuItem("Remove", icon="trash-2", danger=True, on_activate=lambda: self.remove(row))]
        else:
            rows.append(MenuItem("Open folder", icon="folder", on_activate=lambda: self._open_folder(row)))
        FloatingMenu(rows, label=row.label, title=row.label).popup(anchor, x=x, y=y)

    def _changed(self) -> None:
        if self.on_changed is not None:
            self.on_changed()

    # ── Add, edit, remove ──

    def add_menu(self) -> None:
        rows = [PanelHeading("Add a destination")]
        for platform in ADDABLE:
            rows.append(PanelRow(PLATFORMS[platform][0], lead=logo(platform) or RowLead.well("server"),
                                 note="Sign in" if platform in ("twitch", "youtube") else
                                 ("RTMP, SRT or WHIP" if platform == "custom" else "Server and key"),
                                 on_activate=lambda p=platform: GLib.idle_add(lambda: self.edit_new(p) and False)))
        self.center.grow("add-destination", panel_list(rows, label="Add a destination"))

    def edit_new(self, platform: str) -> None:
        self.edit(None, platform=platform)

    def edit(self, row_id: str | None, *, platform: str | None = None) -> None:
        """The row's editor in the action bar (a new row when ``row_id`` is None)."""
        row = self.store.get(row_id) if row_id else Destination("", platform or "custom")
        if row is None:
            return
        if not row.is_stream:
            self._edit_recording(row)
            return
        label = PLATFORMS[row.platform][0]
        canvas = ModeSwitch([(k, canvas_label(k), CANVAS_ICONS[k]) for k in FORMATS], current=row.canvas,
                            label="Canvas", compact=True)
        name = Gtk.Entry(text=row.name, hexpand=True, placeholder_text=label)
        name.update_property([Gtk.AccessibleProperty.LABEL], ["Name"])
        server = Gtk.Entry(text=row.server or PLATFORMS[row.platform][1], hexpand=True,
                           placeholder_text="rtmps://… · srt://… · https://… (WHIP)")
        server.update_property([Gtk.AccessibleProperty.LABEL], ["Server"])
        key = Gtk.PasswordEntry(show_peek_icon=True, hexpand=True)
        key.set_text(self.key_for(row) if row.id else "")
        key.update_property([Gtk.AccessibleProperty.LABEL], ["Stream key"])
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=14, margin_end=14,
                       margin_top=8, margin_bottom=8)
        body.add_css_class("streamy-destination-body")
        fields: list = []
        mode = {"value": "signin" if row.signs_in and (row.account or not self.key_for(row)) else "key"}
        sign_in_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        if row.signs_in:
            self._fill_sign_in(sign_in_box, row)
            body.append(sign_in_box)
        if row.platform == "custom":
            fields.append(("Name", name))
        if row.platform in ("custom", "kick", "tiktok") or row.signs_in:
            fields += [("Server", server), ("Stream key", key)]
        fields.append(("Canvas", canvas))

        def save() -> None:
            server_text = server.get_text().strip()
            candidate = Destination(row.id or "", row.platform, name=name.get_text().strip(),
                                    canvas=canvas.current if hasattr(canvas, "current") else row.canvas,
                                    enabled=row.enabled, server=server_text if server_text != PLATFORMS[row.platform][1]
                                    else "", container=row.container, codec=row.codec, video_kbps=row.video_kbps,
                                    audio_kbps=row.audio_kbps, account=row.account)
            if not (row.signs_in and mode["value"] == "signin"):
                try:
                    og.kind_of(candidate.ingest)
                except ValueError as error:
                    Toast.show(self.host, str(error), kind="warning")
                    return
            target = row if row.id else self.store.add(row.platform, candidate.canvas)
            for field in ("name", "canvas", "server"):
                setattr(target, field, getattr(candidate, field))
            self.store.update(target)
            if not (row.signs_in and mode["value"] == "signin"):
                self._store_key(target, key.get_text().strip())
            self.center.fold()
            self.refresh()
            self._changed()
            Toast.show(self.host, f"{target.label} saved", kind="saved")

        modes = [("signin", "Sign in", "user"), ("key", "Stream key", "key-round")] if row.signs_in else []

        def on_mode(value: str) -> None:
            mode["value"] = value
            sign_in_box.set_visible(value == "signin")
            for widget in (server, key):
                widget.get_parent().set_visible(value == "key") if widget.get_parent() else None

        editor = ActionEditor(row.label if row.id else f"Add {label}", "radio-tower", modes=modes,
                              mode=mode["value"] if modes else None, on_mode=on_mode if modes else None,
                              summary="Stream keys are kept in your keyring", fields=fields, body=body,
                              primary=BarAction("check", "Save" if row.id else "Add", on_activate=save))
        self.center.set_editor(editor)
        self.center.grow()
        if modes:
            GLib.idle_add(lambda: on_mode(mode["value"]) and False)
        (name if row.platform == "custom" else server if not row.signs_in else canvas).grab_focus()

    def _fill_sign_in(self, box: Gtk.Box, row: Destination) -> None:
        child = box.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            box.remove(child)
            child = following
        label = PLATFORMS[row.platform][0]
        if row.account:
            line = Gtk.Label(label=f"Signed in as {row.account}", xalign=0, wrap=True)
            box.append(line)
            out = TextButton("Sign out", icon="log-out", on_click=lambda: self.sign_out(row, box))
            out.set_halign(Gtk.Align.START)
            box.append(out)
            return
        words = Gtk.Label(xalign=0, wrap=True, label=f"Sign in to {label} and Streamy finds your stream key itself.")
        box.append(words)
        if not accounts.configured(row.platform):
            note = Gtk.Label(xalign=0, wrap=True, label=f"This build has no {label} app id yet: signing in "
                             f"will be refused. Paste a stream key instead.")
            note.add_css_class("dim-label")
            box.append(note)
        sign_in = TextButton(f"Sign in with {label}", icon="user", style="raised",
                             on_click=lambda: self.sign_in(row, box))
        sign_in.set_halign(Gtk.Align.START)
        box.append(sign_in)

    def sign_in(self, row: Destination, box: Gtk.Box | None = None) -> None:
        """Twitch: show the code and open twitch.tv/activate; YouTube: open Google's page. Then the row
        gets the account's name, server and key."""
        label = PLATFORMS[row.platform][0]
        status = Gtk.Label(xalign=0, wrap=True, label=f"Opening {label}…", selectable=True)
        if box is not None:
            self._fill_sign_in(box, row)
            box.append(status)
        cancelled = {"value": False}
        self._signing_in = cancelled

        def say(text: str) -> None:
            GLib.idle_add(lambda: status.set_label(text) and False)

        def open_page(url: str) -> None:
            GLib.idle_add(lambda: Gtk.UriLauncher.new(url).launch(self.get_root(), None, None) and False)

        def work():
            if row.platform == "twitch":
                flow = accounts.TwitchSignIn()
                code = flow.start()
                say(f"Enter {code.user_code} at {code.verification_uri.removeprefix('https://')}")
                open_page(f"{code.verification_uri}?public=true")
                tokens = flow.wait(code, lambda: cancelled["value"])
            else:
                flow = accounts.YouTubeSignIn()
                open_page(flow.begin())
                say("Finish signing in in your browser")
                tokens = flow.wait(lambda: cancelled["value"])
            return tokens, flow.account(tokens)

        def done(result, error) -> None:
            GLib.idle_add(lambda: self._signed_in(row, box, status, result, error) and False)

        accounts.run(work, done)

    def _signed_in(self, row: Destination, box, status: Gtk.Label, result, error: str | None) -> None:
        if error is not None:
            status.set_label(error)
            return
        tokens, account = result
        target = row if row.id else self.store.add(row.platform, row.canvas)
        target.account, target.server = account.name, account.server
        self.store.update(target)
        try:
            self.secrets.store(target.id, SIGN_IN, tokens.to_json(), label=f"Streamy sign-in ({target.label})")
        except (GLib.Error, OSError):
            pass
        self._store_key(target, account.key)
        if box is not None:
            self._fill_sign_in(box, target)
        self.refresh()
        self._changed()
        Toast.show(self.host, f"Signed in as {account.name}", kind="done")

    def sign_out(self, row: Destination, box: Gtk.Box | None = None) -> None:
        for kind in (SIGN_IN, KEY):
            try:
                self.secrets.clear(row.id, kind)
            except (GLib.Error, OSError):
                pass
        row.account, row.server = "", ""
        self.store.update(row)
        if box is not None:
            self._fill_sign_in(box, row)
        self.refresh()
        self._changed()

    def _edit_recording(self, row: Destination) -> None:
        formats = ModeSwitch([("mkv", "MKV", "film"), ("mp4", "MP4", "film")], current=row.container,
                             label="Format", compact=True)
        canvas = ModeSwitch([(k, canvas_label(k), CANVAS_ICONS[k]) for k in FORMATS], current=row.canvas,
                            label="Canvas", compact=True)
        folder = {"path": row.folder or str(recordings_dir())}
        pick = TextButton(Path(folder["path"]).name or folder["path"], icon="folder")

        def choose() -> None:
            dialog = Gtk.FileDialog(title="Record to", modal=True)
            dialog.set_initial_folder(Gio.File.new_for_path(folder["path"]))

            def picked(d, result) -> None:
                try:
                    chosen = d.select_folder_finish(result)
                except GLib.Error:
                    return
                folder["path"] = chosen.get_path()
                pick.set_label(Path(folder["path"]).name) if hasattr(pick, "set_label") else None

            dialog.select_folder(self.get_root(), None, picked)

        pick.connect("clicked", lambda *_a: choose())

        def save() -> None:
            row.container = formats.current if hasattr(formats, "current") else row.container
            row.canvas = canvas.current if hasattr(canvas, "current") else row.canvas
            row.folder = "" if folder["path"] == str(recordings_dir()) else folder["path"]
            self.store.update(row)
            self.center.fold()
            self.refresh()
            self._changed()

        editor = ActionEditor("Recording", "circle-dot", summary="MKV survives a crash; MP4 plays everywhere",
                              fields=[("Format", formats), ("Canvas", canvas), ("Folder", pick)],
                              body=Gtk.Box(visible=False), primary=BarAction("check", "Save", on_activate=save))
        self.center.set_editor(editor)
        self.center.grow()

    def remove(self, row: Destination) -> None:
        if self._live(row.id) and self.outputs is not None:
            self.outputs.stop(row.id)
        for kind in (KEY, SIGN_IN):
            try:
                self.secrets.clear(row.id, kind)
            except (GLib.Error, OSError):
                pass
        index = self.store.rows.index(row)
        self.store.remove(row.id)
        self.refresh()
        self._changed()

        def undo() -> None:
            self.store.rows.insert(index, row)
            self.store.save()
            self.refresh()
            self._changed()

        Toast.show(self.host, f"{row.label} removed", kind="done", undo=undo)

    def _open_folder(self, row: Destination) -> None:
        path = Path(row.folder or recordings_dir())
        path.mkdir(parents=True, exist_ok=True)
        Gtk.FileLauncher.new(Gio.File.new_for_path(str(path))).launch(self.get_root(), None, None)

    # ── Going live ──

    def target_for(self, row: Destination) -> str:
        if not row.is_stream:
            folder = Path(row.folder or recordings_dir())
            folder.mkdir(parents=True, exist_ok=True)
            return str(folder / time.strftime(f"Streamy %Y-%m-%d %H.%M.%S.{row.container}"))
        return row.url(self.key_for(row))

    def _start(self, row: Destination) -> str | None:
        """Start one row; the problem in words, or None."""
        from .outputs import OutputError
        from .output_graph import Preset

        try:
            if row.is_stream and row.signs_in and not self.key_for(row):
                return f"{row.label}: sign in first"
            self.outputs.start(row.id, Preset(*row.preset()), self.target_for(row), record=not row.is_stream,
                               container=row.container)
        except (OutputError, ValueError, OSError) as error:
            self.states[row.id] = ("error", str(error))
            self._update(row.id)
            return f"{row.label}: {error}"
        return None

    def go_live(self, *, record: bool | None = None) -> list[str]:
        """Start every enabled stream row (and the recording row when it is on, or ``record``)."""
        problems = []
        for row in self.store.rows:
            wanted = row.enabled if row.is_stream else (row.enabled if record is None else record)
            if wanted and not self._live(row.id):
                problem = self._start(row)
                if problem:
                    problems.append(problem)
        if problems:
            Toast.show(self.host, problems[0] if len(problems) == 1 else f"{len(problems)} destinations didn't start",
                       kind="warning")
        return problems

    def record(self, on: bool) -> str | None:
        row = next((r for r in self.store.rows if not r.is_stream), None)
        if row is None:
            return None
        if on:
            return self._start(row)
        self.outputs.stop(row.id)
        return None

    def end(self, *, recording: bool = False) -> None:
        """End every live stream row (and the recording with ``recording``)."""
        for row in self.store.rows:
            if self._live(row.id) and (row.is_stream or recording):
                self.outputs.stop(row.id)

    def live_canvases(self) -> set[str]:
        """Canvases some live row takes (their chips carry the red dot)."""
        return {r.canvas for r in self.store.rows if self._live(r.id)}


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
