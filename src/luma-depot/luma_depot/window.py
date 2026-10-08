# SPDX-License-Identifier: Apache-2.0

"""Depot's window.

Nothing in this file knows what a Flatpak is. It asks the two providers for
things, draws what comes back, and draws the failure when nothing does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import pathlib
import tempfile
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from luma_appkit import (
    AppWindow, Command, CommandGroup, CommandRegistry, EmptyState, IconButton,
    Island, SectionLabel, StatusBar, install_appkit, install_lumaui,
    NavigationSidebar, RowLead, SidebarRow, SidebarFoot, SidebarToggle, CountBadge,
    Toast, ToastHost, DestructiveDialog, add_style_sheet,
)
from luma_appkit.menus import Menu

from luma_installer import depot_counting, depot_errors as errors
from luma_installer.depot_errors import lifecycle
from luma_installer.depot_links import Link, parse as parse_link
from luma_installer.depot_system_update import EARLY_UPDATES_SUBTITLE, counting_privacy_text

from .appstream_catalogue import StaticCatalogProvider
from .media import MediaLoader
from . import icons
from .os_updates import OsState
from .permissions import PermissionList
from .reviews import ReviewsSection, ReviewsStore, ratings_line, star_row
from .updates_page import UpdatesPage  # noqa: E402
from .providers import (
    App, Catalogue, FaultInjector, InstalledApp, Progress, ProviderError, Result, run_async,
)
from .simulated_install import SimulatedInstallationProvider
from . import system_tools
from .sorting import DEFAULT_SORT, SORT_ORDERS, sort_apps


APP_ID = ("org.projectluma.Depot.LumaUIPreview"
          if os.environ.get("LUMA_DEPOT_LUMAUI_PREVIEW") == "1"
          else "org.projectluma.Depot")
ICON_NAME = "org.projectluma.Depot"
SIDEBAR_WIDTH = 178
# .dp-body sets the gap to --f-space-sm; Depot is 8, where Calendar and
# Weather are 9. Taken from the design rather than from habit.
PRIMARY_ISLAND_GAP = 8


def _shot_path(url: str) -> str:
    """The local file a screenshot URL points at, or "" if there is not one.

    A repository serves screenshots over HTTP and they are cached before they
    are drawn; until there is a repository the catalogue points at files on
    disk. Either way the window never draws a frame it has no image for.
    """
    if not url.startswith("file://"):
        return ""
    path = url.removeprefix("file://")
    if GLib.file_test(path, GLib.FileTest.EXISTS):
        return path
    # The packaged path is not where an uninstalled tree keeps them.
    import os

    local = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "screenshots", os.path.basename(path))
    return local if GLib.file_test(local, GLib.FileTest.EXISTS) else ""


def _age_rating(value: str) -> str:
    """OARS "none" everywhere reads as suitable for everyone; anything else is shown as given."""
    if not value:
        return ""
    if value.endswith(":none") or value == "none":
        return "Everyone"
    if value.startswith("age:") and value[4:].isdigit():
        return f"{int(value[4:])}+"
    return ""


def megabytes(value: int) -> str:
    if value <= 0:
        return "—"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f} GB"
    return f"{round(value / 1_000_000)} MB"


#: The screenshot strip's height; each frame's width follows its picture.
SHOT_HEIGHT = 190

#: Jobs that change the system image rather than an app: they cannot be cancelled.
SYSTEM_JOBS = ("override-remove", "override-reset")

TIER_LABELS = {"luma": "Made for Luma", "verified": "Verified", "listed": "Listed"}
TIER_EXPLANATIONS = {
    "luma": "Built with the Luma kit and reviewed by Luma for design, accessibility, privacy and quality.",
    "verified": "Published by a developer whose identity Luma has verified. Hosted and signed by Luma.",
    "listed": "Installs from its publisher. Luma lists it so you can find it, and has not reviewed it.",
}
SIGN_IN = {
    "none": ("No account needed", "It works without signing in to anything.", False),
    "luma": ("Uses your Luma account", "Signing in is optional and uses the account you already have.", False),
    "third-party": ("Can use an account with another service",
                    "Some features need an account with its developer or another company.", False),
    "required-third-party": ("Needs an account with another service",
                             "You have to sign in with its developer or another company to use it.", True),
}


@dataclass
class Job:
    """One install or update in flight."""

    app_id: str
    kind: str
    cancellable: Gio.Cancellable
    progress: Progress | None = None
    failed: str = ""
    #: The original error, shown only behind "Details"
    detail: str = ""
    widgets: list = field(default_factory=list)


class DepotWindow(UpdatesPage, AppWindow):
    def __init__(self, application: Adw.Application) -> None:
        self.faults = FaultInjector()
        self.fixture = os.environ.get("LUMA_DEPOT_FIXTURE", "")
        self.preview = bool(self.fixture) or os.environ.get("LUMA_DEPOT_PREVIEW") == "1"
        # Which channel "Start Following" would use on a computer that follows
        # none. The agent has no opinion until one is chosen.
        self.pending_channel = "beta"
        self.preview_staged = False
        self.preview_checked = "14 minutes ago"
        self.preview_preferences = {"download": True, "overnight": False}
        if self.fixture:
            # Conformance reads only the supplied Studio sample. No catalogue,
            # settings, installation, update agent or user store is consulted.
            from .v70_provider import V70Catalogue, V70Installation, V70OsReader
            self.catalogue_provider = V70Catalogue(self.fixture)
            self.fixture_listings = {item.id: item for item in self.catalogue_provider.listings}
            self.installer = V70Installation(self.catalogue_provider.listings,
                                             self.catalogue_provider._apps)
            self.os_reader = V70OsReader()
            self.system = None
            self.firmware = None
        elif self.preview:
            from .preview import PreviewCatalogue, PreviewOsReader
            self._preview_directory = tempfile.TemporaryDirectory(prefix="luma-depot-preview-")
            self.catalogue_provider = PreviewCatalogue(self.faults)
            self.installer = SimulatedInstallationProvider(
                self.faults, state_file=pathlib.Path(self._preview_directory.name) / "installed.json")
            self.os_reader = PreviewOsReader()
            self.system = None
            self.firmware = None
        else:
            from .native import providers
            from .system_updates import Firmware, SystemUpdates
            self.catalogue_provider, self.installer, self.host_client = providers()
            self.os_reader = None
            # ADR-030: the update agent and fwupd own the work; Depot shows it.
            # Neither may keep the window off the screen: a service that is
            # missing or refuses leaves its own section empty, nothing more.
            try:
                self.system = SystemUpdates(self._updates_changed)
            except Exception as error:  # noqa: BLE001
                self.system = None
                lifecycle("degraded", detail=f"the update agent could not be reached: {error}")
            try:
                if self.host_client is not None:
                    from .host_client import Firmware as HostFirmware
                    self.firmware = HostFirmware(self.host_client, self._updates_changed)
                else:
                    self.firmware = Firmware(self._updates_changed)
                self.firmware.load()
            except Exception as error:  # noqa: BLE001
                self.firmware = None
                lifecycle("degraded", detail=f"the firmware check could not be started: {error}")

        self.catalogue: Catalogue | None = None
        self.catalogue_error: str = ""
        self.installed: dict[str, InstalledApp] = {}
        self._installed_ready = False
        self.system_tools: list[system_tools.SystemTool] = []
        self.system_tools_busy: set[str] = set()
        self.os_state: OsState | None = None
        self.jobs: dict[str, Job] = {}
        #: Listings whose long description the person unfolded with More.
        self.expanded_descriptions: set[str] = set()
        self.fixture_state = os.environ.get("LUMA_DEPOT_FIXTURE_STATE", "discover") if self.fixture else ""
        self.fixture_system_phase = "done" if self.fixture_state == "update-done" else "ready"
        self.show_whats_new = self.fixture_state == "whats-new"
        self.show_all_system_notes = False
        self.system_release_notes = None
        self.system_release_notes_url = ""
        self.system_release_notes_error = ""
        self._fixture_overlay_shown = False
        self._uninstall_buttons: dict[str, Gtk.Button] = {}
        self.view, self.view_argument = self._fixture_destination(self.fixture_state)
        self.history: list[tuple[str, str, str, float]] = []
        self.search_results = ()
        self._setting_search = False
        self._scroll_positions = {}
        # Sort order per category, for this session (like scroll positions).
        self._category_sort: dict[str, str] = {}
        self._render_serial = 0
        self._progress_views = {}
        self._scroll_restore_tick = 0
        self._pending_open = None
        self.loading = True
        self.nav_rows: dict[str, Gtk.Widget] = {}
        self.search_cancellable: Gio.Cancellable | None = None
        self.media = MediaLoader(self._media_ready)
        if getattr(self, 'host_client', None) is not None:
            from .host_client import ReviewsClient as HostReviewsClient
            self.reviews = ReviewsStore(self._reviews_changed, HostReviewsClient(self.host_client, self._reviews_changed))
        else:
            self.reviews = ReviewsStore(self._reviews_changed)
        self.permission_requests: dict[str, object] = {}
        self.pending_link: Link | None = None
        self.install_listeners: list = []
        self.system_error = ""
        self.provisioning = None
        self._settings_ready = True
        if getattr(self, 'host_client', None) is not None:
            # Do not hold window construction behind native service activation.
            # Automatic work waits for the actual host policy, never a default.
            self.settings = depot_counting.Settings(app_updates=False)
            self._settings_ready = False
            self.host_client.call('GetSettings', {}, self._host_settings_loaded)
        else:
            self.settings = depot_counting.Settings() if self.fixture else depot_counting.load_settings()

        super().__init__(
            application=application,
            app_id=APP_ID,
            title="Depot",
            icon_name=ICON_NAME,
            commands=self._application_commands(),
            default_width=1040,
            default_height=760,
            minimum_width=360,
            minimum_height=440,
        )
        self.add_css_class("luma-depot")

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=PRIMARY_ISLAND_GAP)
        self.sidebar = self._build_sidebar()
        if self.view == "search":
            self._set_search_text(self.view_argument)
        self.main = self._build_main()
        body.append(self.sidebar)
        body.append(self.main)
        self.set_body(body)

        self.sidebar_toggle = SidebarToggle(self.sidebar)
        self.sidebar_toggle.set_control_visible(False)
        self.title_bar.pack_start(self.sidebar_toggle)
        phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720px"))
        phone.add_setter(self.content, "margin-start", 0)
        for side in ("end", "top", "bottom"):
            phone.add_setter(self.content, f"margin-{side}", 16)
        phone.add_setter(self.content_clamp, "maximum-size", 366)
        phone.add_setter(self.content_clamp, "tightening-threshold", 366)
        self.add_breakpoint(phone)

        self._inventory_refresh = 0
        self._app_monitor = Gio.AppInfoMonitor.get() if not self.preview else None
        self._app_monitor_handler = (self._app_monitor.connect('changed', self._applications_changed)
                                     if self._app_monitor else 0)
        self._metadata_monitors: list = []
        self._metadata_refresh = 0
        self.connect('close-request', self._close_requested)
        self.connect('unmap', lambda *_args: self._cancel_pending_open())
        self._watch_for_app_metadata()
        self._ask_for_app_metadata()
        self.reload()
        if self.fixture and self.view == "search":
            self.catalogue_provider.search(self.view_argument, self._search_done)

    def _close_requested(self, *_args):
        updates = getattr(self.get_application(), "app_updates", None)
        if (self.provisioning is not None and self.provisioning.active) or (updates is not None and updates.active):
            # The apps chosen at installation are still arriving. Closing the
            # window must not stop them; the notification says when they are.
            self.set_visible(False)
            return True
        return self._stop_inventory_monitor()

    def _updates_changed(self) -> None:
        if self.view in {"updates", "preferences"}:
            self.render()
        else:
            self._refresh_sidebar_counts()

    def _media_ready(self) -> None:
        self.render()

    # ── Metadata arriving after the window is already open ───────────────

    def _watch_for_app_metadata(self) -> None:
        """Redraw when Flathub's metadata lands, without being reopened.

        That cache lives under /var and so cannot ship in the image: a freshly
        installed computer has no application icons or summaries from it until
        `luma-depot-appstream.service` has downloaded it, which on a slow
        connection is minutes after somebody first opens Depot. Watching the
        directory it is checked out into means the shelves fill in by
        themselves instead of only on the next launch.
        """

        if self.preview:
            return
        if getattr(self, 'host_client', None) is not None:
            return  # The host owns AppStream caches; a refresh returns a new DTO.
        from luma_installer import depot_icons
        for monitor in self._metadata_monitors:
            monitor.cancel()
        self._metadata_monitors.clear()
        for root in depot_icons.FLATPAK_APPSTREAM_ROOTS:
            # The directory a remote is checked out into does not exist yet on
            # a computer that has never downloaded any, so watch the deepest
            # part of the path that does; each change re-arms the watchers a
            # level further down as the checkout is created.
            path = pathlib.Path(os.path.expanduser(root))
            while not path.is_dir() and path.parent != path:
                path = path.parent
            try:
                monitor = Gio.File.new_for_path(str(path)).monitor_directory(
                    Gio.FileMonitorFlags.WATCH_MOVES, None)
            except GLib.Error:
                continue
            monitor.connect('changed', self._app_metadata_changed)
            self._metadata_monitors.append(monitor)

    def _ask_for_app_metadata(self) -> None:
        """Nothing has downloaded the metadata yet: ask for it now.

        The timer may not have fired since a network appeared, and somebody
        looking at Depot on a new computer should not have to wait up to six
        hours for artwork. This downloads display data and installs nothing;
        when it lands the shelves are drawn again where they are.
        """

        if self.preview or getattr(self, 'host_client', None) is not None:
            return
        from .native_metadata import cache_directory, request_metadata
        from .native import architecture

        def work():
            arch = architecture()
            if cache_directory(arch) is not None:
                return False
            return request_metadata(arch)

        def arrived(result):
            if result.ok and result.value:
                self._app_metadata_changed()

        run_async(work, arrived)

    def _app_metadata_changed(self, *_args):
        if self._metadata_refresh:
            return
        # A checkout writes many files; wait for it to settle, then read once.
        def refresh():
            self._metadata_refresh = 0
            from .native_metadata import forget_cache
            forget_cache()
            self.media.retry_now()
            self._watch_for_app_metadata()
            self.reload()
            return GLib.SOURCE_REMOVE
        self._metadata_refresh = GLib.timeout_add_seconds(3, refresh)

    def _reviews_changed(self) -> None:
        if self.view == "app":
            self.render()

    def _applications_changed(self, *_args):
        if not self._inventory_refresh:
            def refresh():
                self._inventory_refresh = 0
                self.reload()
                return GLib.SOURCE_REMOVE
            self._inventory_refresh = GLib.timeout_add(150, refresh)

    def _stop_inventory_monitor(self, *_args):
        if self._inventory_refresh:
            GLib.source_remove(self._inventory_refresh)
            self._inventory_refresh = 0
        if self._metadata_refresh:
            GLib.source_remove(self._metadata_refresh)
            self._metadata_refresh = 0
        for monitor in self._metadata_monitors:
            monitor.cancel()
        self._metadata_monitors.clear()
        self.media.close()
        if getattr(self, 'host_client', None) is not None:
            self.host_client.close()
        if self._app_monitor_handler:
            self._app_monitor.disconnect(self._app_monitor_handler)
            self._app_monitor_handler = 0
        return False

    # ── Commands ─────────────────────────────────────────────────────────

    def _application_commands(self) -> CommandRegistry:
        faults = tuple(
            Command(f"depot.fault.{name}", f"Simulate: {description}",
                    lambda name=name: self._toggle_fault(name))
            for name, description in FaultInjector.KNOWN.items()
        )
        return CommandRegistry((
            CommandGroup("", (
                Command("depot.refresh", "Check again", self._check_again,
                        "view-refresh-symbolic", shortcut=("Ctrl", "R")),
            )),
            # Every failure state has to be reachable while there is nothing
            # real to fail, or it gets written blind.
            *( (CommandGroup("Simulate", faults),) if self.preview and not self.fixture else () ),
            CommandGroup("Browse", self._navigation_commands()),
            CommandGroup("", (
                Command("depot.settings", "Settings", lambda: self.go("settings"),
                        "preferences-system-symbolic", shortcut=("Ctrl", "comma")),
                Command("depot.about", "About Depot", self._show_about,
                        "help-about-symbolic"),
                Command("depot.quit", "Quit Depot", self.close, "lumaui-log-out-symbolic",
                        shortcut=("Ctrl", "Q")),
            )),
        ))

    def _check_again(self) -> None:
        if hasattr(self.installer, "force_update_check"):
            self.installer.force_update_check = True
        if self.system is not None:
            self.system.refresh()
        if self.firmware is not None:
            self.firmware.load()
        if hasattr(self.catalogue_provider, "request_refresh"):
            self.catalogue_provider.request_refresh()
        self.reload()

    def _begin_system_update(self) -> None:
        if self.fixture:
            if getattr(self, "fixture_system_phase", "ready") == "done":
                Toast.show(self, "Restarting into Luma 1.0 nightly 0922…")
                return
            if getattr(self, "fixture_system_phase", "ready") == "run":
                return
            self.fixture_system_phase = "run"
            self.fixture_system_progress = 0
            self.render()

            def advance() -> bool:
                if getattr(self, "fixture_system_phase", None) != "run":
                    return GLib.SOURCE_REMOVE
                self.fixture_system_progress = min(100, self.fixture_system_progress + 6)
                if self.fixture_system_progress == 100:
                    self.fixture_system_phase = "done"
                if self.view == "updates":
                    self.render()
                return self.fixture_system_phase == "run"

            GLib.timeout_add(200, advance)
            return
        state = self.system.state if self.system is not None else None
        if state is not None and (state.staged or state.restart_required):
            self._confirm_restart()
        elif state is not None and state.available:
            self.system.call("Download")
        else:
            self._check_again()

    def _toggle_fault(self, name: str) -> None:
        self.faults.toggle(name)
        self.catalogue_provider._cache = None
        self.reload()

    # ── Chrome ───────────────────────────────────────────────────────────

    def _navigation_commands(self):
        return tuple(Command("depot.view." + key.replace(":", "."), title,
                             lambda value=key: self.go(value), icon)
                     for key, title, icon in (
                         ("home", "Discover", "starred-symbolic"),
                         ("category:Create", "Create", "applications-graphics-symbolic"),
                         ("category:Work", "Work", "x-office-document-symbolic"),
                         ("category:Media", "Media", "applications-multimedia-symbolic"),
                         ("category:Tools", "Tools", "applications-utilities-symbolic"),
                         ("mine", "My apps", "drive-harddisk-symbolic"),
                         ("updates", "Updates", "folder-download-symbolic")))

    def _build_sidebar(self):
        sidebar = NavigationSidebar(variant="destinations")
        sidebar.set_name("dp-sidebar")
        sidebar.set_size_request(212, -1)
        sidebar.set_margin_end(0)
        sidebar.list.connect("row-activated", lambda _list, row:
                             self.go(row.destination) if hasattr(row, "destination") else None)
        self.sidebar = sidebar
        self._refresh_categories()
        self.foot = SidebarFoot(search="Search apps", on_search=lambda _text: self._search_changed(self.search))
        self.foot.set_name("dp-foot")
        self.search = self.foot.entry
        sidebar.append_footer(self.foot)
        return sidebar

    def _nav_row(self, key, title, icon_name, *, count=False, badge=False):
        semantic = {"home": "star", "mine": "layout-grid", "updates": "download",
                    "category:Create": "palette", "category:Work": "briefcase",
                    "category:Media": "clapperboard", "category:Play": "gamepad-2",
                    "category:Tools": "wrench"}.get(key, "layout-grid")
        if key.startswith("category:"):
            lead = Gtk.CenterBox(valign=Gtk.Align.CENTER)
            lead.set_center_widget(Gtk.Image(icon_name=f"lumaui-{semantic}-symbolic"))
            lead.add_css_class("depot-nav-category-icon")
            lead.add_css_class(key.split(":", 1)[1].casefold())
        else:
            lead = Gtk.Image(icon_name=f"lumaui-{semantic}-symbolic")
            lead.add_css_class("depot-nav-main-icon")
        row = SidebarRow(title, lead=lead)
        row.add_css_class("depot-nav-category" if key.startswith("category:") else "depot-nav-main")
        row.destination = key
        if badge:
            row.badge = CountBadge(attention=True)
            row.set_trail(row.badge)
        self.nav_rows[key] = row
        return row

    def _build_main(self) -> Gtk.Widget:
        island = Island()
        island.set_name("dp-island")
        island.set_hexpand(True)
        self.navigation_button = Gtk.Box(visible=False)
        self.back_button = IconButton("go-previous-symbolic", "Back",
                                      context=self.context, quiet=True)
        self.back_button.connect("clicked", lambda *_: self.go_back())
        self.back_button.set_visible(False)
        self.title_label = Gtk.Label(label="Discover", xalign=0, hexpand=True)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                               valign=Gtk.Align.START, hexpand=True)
        self.content.set_name("dp-body")
        self.content.set_margin_start(32)
        self.content.set_margin_end(32)
        self.content.set_margin_top(24)
        self.content.set_margin_bottom(24)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_name("dp-scroll")
        self.content_clamp = Adw.Clamp(maximum_size=100000, tightening_threshold=100000)
        self.content_clamp.set_child(self.content)
        scroll.set_child(self.content_clamp)
        self.content_scroll = scroll
        island.append(scroll)

        self.status = StatusBar()
        self.status_left = self.status.append_text("")
        self.status_right = self.status.append_text("", end=True)
        return ToastHost(island)

    # ── Loading ──────────────────────────────────────────────────────────

    def reload(self) -> None:
        self.loading = True
        self.catalogue_error = ""
        self.render()
        self.reviews.pages.clear()
        self.reviews.errors.clear()
        self.catalogue_provider.load_catalogue(self._catalogue_loaded)
        self.installer.installed(self._installed_loaded)
        self._load_system_tools()
        if self.os_reader is not None:
            self.os_reader.state(self._os_loaded)

    def _load_system_tools(self) -> None:
        if self.preview:
            return

        def loaded(result: Result) -> None:
            if result.ok:
                self.system_tools = list(result.value)
                if self.view == "mine":
                    self.render()
        if getattr(self, 'host_client', None) is not None:
            self.host_client.call('SystemTools', {}, loaded)
        else:
            run_async(system_tools.load, loaded)

    def _host_settings_loaded(self, result):
        if result.ok:
            values = result.value
            if (isinstance(values, dict) and set(values) == {'install_events', 'countme', 'app_updates'}
                    and all(type(v) is bool for v in values.values())):
                self.settings = depot_counting.Settings(**values)
                self._settings_ready = True
                if getattr(self, 'main', None) is not None:
                    self.render()

    def _save_settings(self):
        if getattr(self, 'host_client', None) is None:
            depot_counting.save_settings(self.settings)
            return
        from dataclasses import asdict
        def saved(result):
            if not result.ok:
                self._report('Settings were not saved', result.error.hint or str(result.error))
                self._settings_ready = False
                self.host_client.call('GetSettings', {}, self._host_settings_loaded)
        self.host_client.call('SetSettings', asdict(self.settings), saved)

    def _catalogue_loaded(self, result: Result) -> None:
        self.loading = False
        if result.ok:
            self.catalogue = result.value
            self.catalogue_error = ""
            self._refresh_categories()
        else:
            self.catalogue = None
            self.catalogue_error = result.error.hint or str(result.error)
        self.render()
        if self.pending_link is not None and self.catalogue is not None:
            link, self.pending_link = self.pending_link, None
            self.open_link(link)
        for listener in tuple(self.install_listeners):
            if hasattr(listener, "catalogue_loaded"):
                listener.catalogue_loaded(result)

    def _installed_loaded(self, result: Result) -> None:
        if result.ok:
            self.installed = {record.app_id: record for record in result.value}
            self._installed_ready = True
            self._record_finished_updates()
            # Newly exported package icons can replace a cached missing-icon
            # lookup in this long-lived window. Refresh the existing upstream
            # theme paths after inventory changes, without adding private paths.
            theme = Gtk.IconTheme.get_for_display(self.get_display())
            theme.set_search_path(theme.get_search_path())
        self.render()
        if self.fixture and not self._fixture_overlay_shown and self.fixture_state.startswith(("menu:", "uninstall:")):
            self._fixture_overlay_shown = True
            app_id = self.fixture_state.split(":", 1)[1]
            def show_fixture_overlay():
                app = self.catalogue.find(app_id) if self.catalogue else None
                if app is not None:
                    if self.fixture_state.startswith("menu:"):
                        self._show_uninstall_menu(self.fixture_listings[app_id], self._uninstall_buttons.get(app_id))
                    else:
                        self._confirm_remove(app)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(show_fixture_overlay)

    def _os_loaded(self, result: Result) -> None:
        if result.ok:
            self.os_state = result.value
        self.render()

    def _refresh_categories(self):
        self.sidebar.clear()
        self.nav_rows.clear()
        self.sidebar.append_row(self._nav_row("home", "Discover", "starred-symbolic"))
        self.sidebar.append_row(self._nav_row("mine", "Your apps", "view-grid-symbolic"))
        self.sidebar.append_row(self._nav_row("updates", "Updates", "folder-download-symbolic", badge=True))
        self.sidebar.append_section("Categories")
        from .appstream_catalogue import CATEGORIES
        for category in (self.catalogue.categories if self.catalogue else CATEGORIES):
            self.sidebar.append_row(self._nav_row("category:" + category.key,
                category.name, category.icon_name))

    @staticmethod
    def _fixture_destination(state: str) -> tuple[str, str]:
        if state in {"apps", "menu:discord", "uninstall:discord"}:
            return "mine", ""
        if state in {"updates", "whats-new", "update-done"}:
            return "updates", ""
        if state.startswith("category:"):
            return state.replace("category:media", "category:Media"), ""
        if state.startswith("app:"):
            return "app", state.split(":", 1)[1]
        if state.startswith("search:"):
            return "search", state.split(":", 1)[1]
        return "home", ""

    # ── Navigation ───────────────────────────────────────────────────────

    def _set_search_text(self, text):
        self._setting_search = True
        self.search.set_text(text)
        self._setting_search = False

    def go(self, view, argument="", *, remember=True):
        current = (self.view, self.view_argument)
        position = self.content_scroll.get_vadjustment().get_value()
        self._scroll_positions[current] = position
        if self.search_cancellable is not None:
            self.search_cancellable.cancel()
        if remember and current != (view, argument):
            self.history.append((*current, self.search.get_text(), position))
        self.view, self.view_argument = view, argument
        if view != "app":
            self._set_search_text(argument if view == "search" else "")
        self.render(restore=self._scroll_positions.get((view, argument), 0))

    def go_back(self):
        if not self.history:
            self.go("home", remember=False)
            return
        view, argument, query, position = self.history.pop()
        self.view, self.view_argument = view, argument
        self._set_search_text(query)
        self.render(restore=position)

    def _search_changed(self, entry):
        if self._setting_search:
            return
        text = entry.get_text().strip()
        if self.view == "search" and text == self.view_argument:
            return
        if self.search_cancellable is not None:
            self.search_cancellable.cancel()
        if not text:
            if self.view == "search":
                self.go("home", remember=False)
            return
        self.search_cancellable = Gio.Cancellable()
        self.view, self.view_argument = "search", text
        self.catalogue_provider.search(text, self._search_done, self.search_cancellable)

    def _search_done(self, result):
        self.search_results = tuple(result.value) if result.ok else ()
        self.render(restore=0)

    # ── Rendering ────────────────────────────────────────────────────────

    def render(self, *, restore=None) -> None:
        position = self.content_scroll.get_vadjustment().get_value() if restore is None else restore
        self._render_serial += 1
        serial = self._render_serial
        if self._scroll_restore_tick:
            self.content_scroll.remove_tick_callback(self._scroll_restore_tick)
        frames = 0
        def restore_scroll(_widget, _clock):
            nonlocal frames
            frames += 1
            if frames < 2:
                return GLib.SOURCE_CONTINUE
            if serial == self._render_serial:
                self.content_scroll.get_vadjustment().set_value(position)
            self._scroll_restore_tick = 0
            return GLib.SOURCE_REMOVE
        # The first frame allocates the replacement content; only then is the
        # vertical range large enough to restore a nonzero position.
        self._scroll_restore_tick = self.content_scroll.add_tick_callback(restore_scroll)
        self._progress_views.clear()
        while child := self.content.get_first_child():
            self.content.remove(child)
        for cls in self.content.get_css_classes():
            if cls.startswith("depot-view-"):
                self.content.remove_css_class(cls)
        self.content.add_css_class("depot-view-category" if self.view.startswith("category:")
                                   else "depot-view-" + self.view)
        self._uninstall_buttons.clear()
        self.content.set_valign(Gtk.Align.START)
        self.back_button.set_visible(self.view in {"app", "release", "preferences", "settings",
                                                   "missing"} or self.view.startswith("collection"))
        self._refresh_sidebar_counts()
        self._refresh_status()

        if self.catalogue_error and self.view in {"home", "search"} or \
           (self.catalogue_error and self.view.startswith("category")):
            self._render_failure()
            return
        if self.loading and self.catalogue is None and self.view != "settings":
            self._render_loading()
            return

        if self.view in {"home", "mine", "updates", "app", "search"} or self.view.startswith("category:"):
            from . import lumaui_views
            if self.view == "home":
                lumaui_views.render_home(self)
            elif self.view == "mine":
                lumaui_views.render_mine(self)
            elif self.view == "updates":
                lumaui_views.render_updates(self)
            elif self.view == "app":
                lumaui_views.render_app(self)
            elif self.view == "search":
                lumaui_views.render_search(self)
            else:
                lumaui_views.render_category(self, self.view.split(":", 1)[1])
            return

        if self.view == "home":
            self.title_label.set_label("Discover")
            self._render_home()
        elif self.view.startswith("category:"):
            key = self.view.split(":", 1)[1]
            self.title_label.set_label(key)
            order = self._category_sort.get(key, DEFAULT_SORT)
            apps = sort_apps(self.catalogue.by_category(key) if self.catalogue else (),
                             order, is_installed=lambda app: app.app_id in self.installed)
            if apps:
                self.content.append(self._category_heading(key, order))
            self._render_cards(apps)
        elif self.view == "search":
            self.title_label.set_label(f"“{self.view_argument}”")
            self._render_search()
        elif self.view == "app":
            self._render_detail()
        elif self.view == "mine":
            self.title_label.set_label("My apps")
            self._render_mine()
        elif self.view == "updates":
            self.title_label.set_label("Updates")
            self._render_updates()
        elif self.view == "release":
            self.title_label.set_label("What’s included")
            self._render_release()
        elif self.view == "preferences":
            self.title_label.set_label("Update preferences")
            self._render_preferences()
        elif self.view == "settings":
            self.title_label.set_label("Settings")
            self._render_settings()
        elif self.view == "collection":
            self._render_collection()
        elif self.view == "missing":
            self.title_label.set_label("Depot")
            self.content.set_valign(Gtk.Align.FILL)
            self.content.append(EmptyState(
                "Depot cannot find that app",
                f"\u201c{self.view_argument}\u201d is not in the catalogue on this computer. "
                "It may not be published yet, or it may have been withdrawn.",
                "system-search-symbolic", primary=("Browse apps", lambda: self.go("home"))))

    def _render_loading(self) -> None:
        self.content.set_valign(Gtk.Align.FILL)
        spinner = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                      hexpand=True, vexpand=True)
        box.append(spinner)
        label = Gtk.Label(label="Reading the catalogue…")
        label.add_css_class("dp-quiet-line")
        box.append(label)
        self.content.append(box)

    def _render_failure(self) -> None:
        # EmptyState is an Adw.Bin and takes its actions as (label, callback)
        # pairs; appending to it raises AttributeError. That raise happened
        # while rendering the failure itself, so a catalogue that could not be
        # read showed as an empty pane with nothing to explain it -- the one
        # moment the interface most needed to say what had gone wrong.
        self.content.set_valign(Gtk.Align.FILL)
        self.content.append(EmptyState(
            "Depot cannot reach the catalogue",
            self.catalogue_error,
            "network-offline-symbolic",
            primary=("Try again", lambda: self.reload())))

    def _render_home(self) -> None:
        if self.catalogue is None or not self.catalogue.apps:
            self.content.set_valign(Gtk.Align.FILL)
            self.content.append(EmptyState(
                "Nothing to browse yet",
                "The catalogue is empty. When a repository is configured its apps "
                "will appear here.",
                "folder-download-symbolic"))
            return
        self._provisioning_banner()
        featured = self.catalogue.featured
        if featured is not None:
            self.content.append(self._hero(featured))
        for collection in self.catalogue.collections:
            apps = self.catalogue.collection_apps(collection)
            if not apps:
                # Its apps are not public yet, or not for this computer. An
                # empty row would promise something Depot cannot show.
                continue
            self.content.append(self._collection_rail(collection, apps))
        for category in self.catalogue.categories:
            apps = self.catalogue.by_category(category.key)
            if not apps:
                continue
            rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            rail.add_css_class("dp-rail")
            rail.append(SectionLabel(category.name, variant="content"))
            rail.append(self._cards(apps))
            self.content.append(rail)

    def _collection_rail(self, collection, apps) -> Gtk.Widget:
        rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        rail.add_css_class("dp-rail")
        rail.add_css_class("dp-collection")
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        copy.append(SectionLabel(collection.name, variant="content"))
        if collection.summary:
            summary = Gtk.Label(label=collection.summary, xalign=0, wrap=True)
            summary.add_css_class("dp-collection-summary")
            copy.append(summary)
        head.append(copy)
        if len(apps) > 4:
            everything = Gtk.Button(label=f"All {len(apps)}", valign=Gtk.Align.END)
            everything.add_css_class("luma-button")
            everything.add_css_class("small")
            everything.add_css_class("quiet")
            everything.update_property([Gtk.AccessibleProperty.LABEL], [f"Open the {collection.name} collection"])
            everything.connect("clicked", lambda *_: self.go("collection", collection.key))
            head.append(everything)
        rail.append(head)
        rail.append(self._cards(apps))
        return rail

    def _render_collection(self) -> None:
        collection = self.catalogue.collection(self.view_argument) if self.catalogue else None
        apps = self.catalogue.collection_apps(collection) if collection else ()
        self.title_label.set_label(collection.name if collection else "Collection")
        if collection is None or not apps:
            self.content.set_valign(Gtk.Align.FILL)
            self.content.append(EmptyState(
                "This collection is not available",
                "Its apps are not in the catalogue on this computer yet." if collection else
                "Depot cannot find a collection by that name.",
                "view-app-grid-symbolic", primary=("Browse apps", lambda: self.go("home"))))
            return
        intro = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        intro.add_css_class("dp-collection-head")
        name = Gtk.Label(label=collection.name, xalign=0)
        name.add_css_class("dp-head-name")
        intro.append(name)
        if collection.summary:
            summary = Gtk.Label(label=collection.summary, xalign=0, wrap=True)
            summary.add_css_class("dp-section-body")
            intro.append(summary)
        count = Gtk.Label(label=f"{len(apps)} app{'s' if len(apps) != 1 else ''}", xalign=0)
        count.add_css_class("dp-get-note")
        intro.append(count)
        self.content.append(intro)
        self.content.append(self._cards(apps))

    def _provisioning_banner(self) -> None:
        run = self.provisioning
        if run is None or not run.visible:
            return
        banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        banner.add_css_class("dp-banner")
        banner.add_css_class("dp-provision")
        if run.active:
            spinner = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)
            spinner.set_size_request(16, 16)
            spinner.set_valign(Gtk.Align.CENTER)
            banner.append(spinner)
        else:
            banner.append(Gtk.Image(icon_name="emblem-ok-symbolic", pixel_size=16))
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        title = Gtk.Label(label=run.headline(), xalign=0, wrap=True)
        title.add_css_class("dp-banner-title")
        copy.append(title)
        detail = Gtk.Label(label=run.detail(), xalign=0, wrap=True)
        detail.add_css_class("dp-banner-detail")
        copy.append(detail)
        banner.append(copy)
        if not run.active:
            dismiss = Gtk.Button(label="Done", valign=Gtk.Align.CENTER)
            dismiss.add_css_class("luma-button")
            dismiss.add_css_class("small")
            dismiss.add_css_class("quiet")
            dismiss.connect("clicked", lambda *_: (run.dismiss(), self.render()))
            banner.append(dismiss)
        self.content.append(banner)

    def _hero(self, app: App) -> Gtk.Widget:
        hero = Gtk.Button()
        hero.add_css_class("dp-hero")
        hero.add_css_class(f"tone-{app.tone}")
        # The sheet states the height; adding a request here would stack on
        # top of the padding the sheet already accounts for.
        hero.set_vexpand(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      valign=Gtk.Align.END, hexpand=True)
        eyebrow = Gtk.Label(label="WORTH A LOOK", xalign=0)
        eyebrow.add_css_class("dp-hero-eyebrow")
        box.append(eyebrow)
        name = Gtk.Label(label=app.name, xalign=0)
        name.add_css_class("dp-hero-name")
        box.append(name)
        tagline = Gtk.Label(label=app.tagline or app.summary, xalign=0, wrap=True)
        tagline.set_max_width_chars(44)  # the design caps the measure at 44ch
        tagline.add_css_class("dp-hero-tagline")
        box.append(tagline)
        if app.banner and pathlib.Path(app.banner).is_file():
            picture = Gtk.Picture.new_for_filename(app.banner)
            picture.set_can_shrink(True)
            picture.set_content_fit(Gtk.ContentFit.COVER)
            overlay = Gtk.Overlay()
            overlay.set_child(Gtk.Box(height_request=186))
            overlay.add_overlay(picture)
            shade = Gtk.Box()
            shade.add_css_class("dp-hero-shade")
            overlay.add_overlay(shade)
            for setter in (box.set_margin_start, box.set_margin_end, box.set_margin_top, box.set_margin_bottom):
                setter(16)
            overlay.add_overlay(box)
            hero.add_css_class("artwork")
            hero.set_overflow(Gtk.Overflow.HIDDEN)
            hero.set_child(overlay)
        else:
            hero.set_child(box)
        hero.connect("clicked", lambda _b, value=app: self.go("app", value.app_id))
        hero.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"Worth a look: {app.name}. {app.summary}"])
        return hero

    def _cards(self, apps):
        # CSS grid auto-fill retains empty tracks. GTK FlowBox stretches short
        # shelves, so allocate explicit equal grid tracks from available width.
        grid = Gtk.Grid(column_spacing=8, row_spacing=8, column_homogeneous=True)
        grid.add_css_class("dp-cards")
        cards = [self._card(app) for app in apps]
        columns = [0]
        def layout(widget, _clock):
            count = max(1, (widget.get_width() + 8) // 218)
            if count == columns[0]:
                return True
            columns[0] = count
            focused = next((card for card in cards if card.has_focus()), None)
            while child := widget.get_first_child():
                widget.remove(child)
            for index, card in enumerate(cards):
                widget.attach(card, index % count, index // count, 1, 1)
            for index in range(len(cards), count):
                widget.attach(Gtk.Box(), index, 0, 1, 1)
            if focused is not None:
                focused.grab_focus()
            return True
        grid.add_tick_callback(layout)
        return grid

    def _card(self, app: App) -> Gtk.Widget:
        card = Gtk.Button()
        card.add_css_class("dp-card")
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        line.append(self._icon_tile(app, 40))
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        name = Gtk.Label(label=app.name, xalign=0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(14)
        name.set_width_chars(1)
        name.add_css_class("dp-card-name")
        summary = Gtk.Label(label=app.summary, xalign=0)
        summary.set_ellipsize(Pango.EllipsizeMode.END)
        summary.set_max_width_chars(20)
        summary.set_width_chars(1)
        summary.add_css_class("dp-card-summary")
        copy.append(name)
        copy.append(summary)
        line.append(copy)

        job = self.jobs.get(app.app_id)
        end = Gtk.Label(label="")
        end.add_css_class("dp-card-end")
        if job is not None and job.progress is not None:
            end.set_label(f"{round(job.progress.fraction * 100)}%")
            end.add_css_class("working")
        elif app.app_id in self.installed:
            end.set_label("✓")
            end.add_css_class("have")
        elif app.system_state == "removed":
            end.set_label("Removed")
        elif app.luma_only:
            end.set_label("Luma")
        else:
            end.set_label(megabytes(app.download_bytes))
        line.append(end)
        card.set_child(line)
        card.connect("clicked", lambda _b, value=app: self.go("app", value.app_id))
        card.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [f"{app.name}. {app.summary}. "
             f"{'Installed' if app.app_id in self.installed else (app.availability if app.system_state or app.luma_only else megabytes(app.download_bytes))}"],
        )
        return card

    def _fixture_icon(self, name, size, tone="muted"):
        if not self.preview or not name:
            return None
        path = pathlib.Path(os.environ.get("LUMA_DEPOT_PREVIEW_ASSETS", "/nonexistent")) / "icons" / (name + "-" + tone + ".svg")
        if not path.is_file():
            return None
        theme = Gtk.IconTheme.get_for_display(self.get_display())
        if str(path.parent) not in theme.get_search_path():
            theme.add_search_path(str(path.parent))
        image = Gtk.Image(icon_name=name + "-" + tone, pixel_size=size)
        return image

    def _icon_tile(self, app: App, size: int) -> Gtk.Widget:
        tile = Gtk.Box()
        tile.add_css_class("dp-icon")
        tile.add_css_class(f"tone-{app.tone}")
        if size >= 70:
            tile.add_css_class("large")
        tile.set_size_request(size, size)
        tile.set_valign(Gtk.Align.START if size >= 70 else Gtk.Align.CENTER)
        tile.set_halign(Gtk.Align.CENTER)
        names = {"Reel": "clapperboard", "Studio": "audio-waveform", "Inkline": "pencil", "Lattice": "code",
                 "Ledgerbook": "chart-column", "Bellwether": "bell", "Plate": "image", "Folio": "file-text",
                 "Gauge": "gauge", "Keyring": "lock", "Shears": "scissors", "Crate": "archive"}
        icon = self._fixture_icon(names.get(app.name, ""), 38 if size >= 70 else round(size * .52), app.tone)
        if icon is None:
            icon = self._app_glyph(app, size)
            if icon.has_css_class('dp-artwork'):
                tile.add_css_class('artwork')
        icon.set_hexpand(True)
        icon.set_vexpand(True)
        tile.append(icon)
        tile.set_hexpand(False)
        tile.set_vexpand(False)
        return tile

    def _app_glyph(self, app, size):
        """The application's own icon, or its initials in the same tile.

        Everything published as an rpm or a snap has no cached AppStream icon
        to find, and neither does a flatpak on a computer that has not
        downloaded Flathub's metadata yet -- which is every computer on its
        first boot, because that cache lives in /var and cannot ship in the
        image. Those used to draw as an empty tile, or as the same anonymous
        cog as each other, which is worse than a blank: a shelf of identical
        tiles cannot be read at all. Initials need no network, no vendor
        artwork and no licence, and they keep the tone, the corners and the
        proportions every other tile already has.

        Which of the sources answers is `luma_installer.depot_icons.choose`,
        and it never returns a name the icon theme does not have: the missing
        artwork a toolkit draws for one is the empty box this used to show.
        """

        choice = icons.choose_for(self, app, self.media)
        return icons.image(choice, size)

    def _category_heading(self, key: str, order: str) -> Gtk.Widget:
        """The page's heading, with "Sort by" at the end of the same row."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("dp-section-head")
        title = SectionLabel(key, variant="content")
        title.set_hexpand(True)
        title.set_valign(Gtk.Align.CENTER)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        row.append(title)

        labels = dict(SORT_ORDERS)
        face = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        kicker = Gtk.Label(label="Sort by")
        kicker.add_css_class("dp-sort-kicker")
        value = Gtk.Label(label=labels.get(order, labels[DEFAULT_SORT]))
        value.add_css_class("dp-sort-value")
        chevron = Gtk.Image(icon_name="pan-down-symbolic", pixel_size=12, margin_start=2)
        chevron.add_css_class("dp-sort-chevron")
        for part in (kicker, value, chevron):
            face.append(part)
        commands = tuple(
            Command("depot.sort." + choice, label, lambda choice=choice: self._set_sort(key, choice),
                    checked=lambda choice=choice: self._category_sort.get(key, DEFAULT_SORT) == choice)
            for choice, label in SORT_ORDERS)
        button = Gtk.MenuButton(child=face, valign=Gtk.Align.CENTER)
        button.add_css_class("dp-sort")
        inner = button.get_first_child()
        if isinstance(inner, Gtk.Button):
            for name in ("luma-button", "small", "quiet"):
                inner.add_css_class(name)
        button.set_popover(Menu(CommandRegistry((CommandGroup(None, commands),))))
        button.set_tooltip_text("Sort apps by")
        button.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                               ["Sort apps by", value.get_label()])
        self.sort_button = button
        row.append(button)
        return row

    def _set_sort(self, key: str, order: str) -> None:
        if self._category_sort.get(key, DEFAULT_SORT) == order:
            return
        self._category_sort[key] = order
        if self.view != "category:" + key:
            return
        # Rebuild after the menu has closed; the heading, and the button the
        # menu hangs from, are replaced. Keep the keyboard where it was.
        def rebuild():
            self.render(restore=0)
            self.sort_button.grab_focus()
            return False
        GLib.idle_add(rebuild)

    def _render_cards(self, apps: tuple[App, ...]) -> None:
        if not apps:
            self.content.set_valign(Gtk.Align.FILL)
            self.content.append(EmptyState("Nothing here yet",
                                           "No applications in this category.",
                                           "folder-symbolic"))
            return
        self.content.append(self._cards(apps))

    def _render_search(self) -> None:
        results = getattr(self, "search_results", ())
        if not results:
            self.content.set_valign(Gtk.Align.FILL)
            self.content.append(EmptyState(
                f"Nothing called “{self.view_argument}”",
                "Try another name, or browse the shelves.",
                "system-search-symbolic", primary=("Browse", lambda: self.go("home"))))
            return
        self.content.append(self._cards(results))

    # ── Detail ───────────────────────────────────────────────────────────

    def _render_detail(self) -> None:
        app = self.catalogue.find(self.view_argument) if self.catalogue else None
        if app is None and self.view_argument in self.installed:
            app = self.installed[self.view_argument].app
        if app is None:
            self.content.append(EmptyState("That app is gone",
                                           "It is no longer in the catalogue.",
                                           "dialog-question-symbolic"))
            return
        self.title_label.set_label(app.name)
        record = self.installed.get(app.app_id)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        head.add_css_class("dp-head")
        head.append(self._icon_tile(app, 76))
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5,
                       valign=Gtk.Align.CENTER, hexpand=True)
        name_line = Adw.WrapBox(child_spacing=8, line_spacing=4)
        name = Gtk.Label(label=app.name, xalign=0, wrap=True)
        name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        name.add_css_class("dp-head-name")
        name_line.append(name)
        if app.tier in TIER_LABELS:
            name_line.append(self._tier_badge(app.tier))
        copy.append(name_line)
        if app.developer:
            developer_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            developer = Gtk.Label(label=app.developer, xalign=0)
            developer.set_ellipsize(Pango.EllipsizeMode.END)
            developer.add_css_class("dp-head-developer")
            developer_line.append(developer)
            if app.developer_verified:
                mark = Gtk.Image(icon_name="object-select-symbolic", pixel_size=10,
                                 valign=Gtk.Align.CENTER)
                mark.add_css_class("dp-verified")
                spoken = ("Verified organization" if app.developer_verified == "organization"
                          else "Verified developer")
                mark.set_tooltip_text(spoken + ": Luma has confirmed who publishes this app.")
                mark.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
                developer_line.append(mark)
            copy.append(developer_line)
        if app.source_label:
            source_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            source_line.add_css_class("dp-source")
            source = Gtk.Label(label=app.source_label, xalign=0)
            source.set_ellipsize(Pango.EllipsizeMode.END)
            source_line.append(source)
            if app.source_verified and app.source_verifier:
                mark = Gtk.Image(icon_name="object-select-symbolic", pixel_size=9, valign=Gtk.Align.CENTER)
                mark.add_css_class("dp-verified")
                mark.add_css_class("small")
                spoken = f"Verified by {app.source_verifier}"
                mark.set_tooltip_text(f"{app.source_verifier[:1].upper()}{app.source_verifier[1:]} has confirmed "
                                      "who publishes this app.")
                mark.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
                source_line.append(mark)
            copy.append(source_line)
        if app.rating_count >= 3:
            stars = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            stars.add_css_class("dp-stars")
            value = Gtk.Label(label=f"{app.rating:.1f}")
            value.add_css_class("dp-stars-value")
            stars.append(value)
            stars.append(star_row(app.rating, 11))
            stars.append(Gtk.Label(label=f"· {ratings_line(app.rating, app.rating_count)}"))
            copy.append(stars)
        elif app.tier:
            quiet = Gtk.Label(label=ratings_line(app.rating, app.rating_count), xalign=0)
            quiet.add_css_class("dp-stars")
            copy.append(quiet)
        copy.append(self._detail_actions(app))
        head.append(copy)
        self.content.append(head)

        if record is not None and record.has_update and self._asks_for_more(record):
            self.content.append(self._permission_notice(app, record))

        self._screenshots(app)

        description = self._section("What it does", self._description(app))
        description.add_css_class("first")
        self.content.append(description)
        self.content.append(self._section("What it can reach", self._reach_block(app, record)))
        if app.slug and (app.tier or self.preview):
            self.content.append(self._section("Ratings and reviews", ReviewsSection(
                self, app, version=(record.version if record and record.version else app.version),
                installed=record is not None, store=self.reviews,
                preview_page=self._preview_reviews(app))))
        self.content.append(self._section("Details", self._facts(app, record)))
        links = self._links(app)
        if links is not None:
            self.content.append(links)

    #: A description longer than this opens folded, with More, as a store does.
    DESCRIPTION_FOLD = 560

    def _description(self, app: App) -> Gtk.Widget:
        text = app.description or ""
        label = Gtk.Label(label=text, xalign=0, wrap=True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        if len(text) <= self.DESCRIPTION_FOLD or app.app_id in self.expanded_descriptions:
            return label
        cut = text[:self.DESCRIPTION_FOLD]
        # End at a paragraph or sentence where one is near, never mid-word.
        for mark in ("\n\n", ". ", "\n", " "):
            index = cut.rfind(mark)
            if index > self.DESCRIPTION_FOLD // 2:
                cut = cut[:index + (1 if mark == ". " else 0)]
                break
        label.set_label(cut.rstrip() + "\u2026")
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        column.append(label)
        more = Gtk.Button(label="More")
        more.add_css_class("luma-button")
        more.add_css_class("small")
        more.add_css_class("quiet")
        more.set_halign(Gtk.Align.START)
        more.update_property([Gtk.AccessibleProperty.LABEL], [f"Read the rest of {app.name}\u2019s description"])

        def expand(*_args, key=app.app_id):
            self.expanded_descriptions.add(key)
            self.render()  # keeps the scroll position
        more.connect("clicked", expand)
        column.append(more)
        return column

    def _preview_reviews(self, app):
        if not self.preview:
            return None
        from .preview import preview_reviews
        return preview_reviews(app)

    def _tier_badge(self, tier: str) -> Gtk.Widget:
        badge = Gtk.Label(label=TIER_LABELS[tier], valign=Gtk.Align.CENTER)
        badge.add_css_class("dp-tier")
        badge.add_css_class(tier)
        badge.set_tooltip_text(TIER_EXPLANATIONS[tier])
        badge.update_property([Gtk.AccessibleProperty.DESCRIPTION], [TIER_EXPLANATIONS[tier]])
        return badge

    def _screenshots(self, app: App) -> None:
        # Only screenshots that actually resolve are drawn. An empty frame is a
        # claim that there is a picture, which is worse than no strip at all.
        shots = []
        waiting = 0
        for shot in app.screenshots:
            if shot.sha256:
                path = self.media.path(shot.url, shot.sha256)
                if not path and not self.media.failed_for(shot.sha256):
                    waiting += 1
            else:
                path = _shot_path(shot.url)
            if path:
                shots.append((shot, path))
        if not shots and not waiting:
            return
        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for shot, path in shots:
            frame = Gtk.Box()
            frame.add_css_class("dp-shot")
            # Each frame takes its screenshot's own shape at the strip's
            # height, so nothing is cropped away (a store's pictures are
            # often captioned artwork, and half a caption reads as broken).
            aspect = (shot.width / shot.height) if shot.width and shot.height else 1.6
            frame.set_size_request(max(120, min(420, round(SHOT_HEIGHT * aspect))), SHOT_HEIGHT)
            frame.set_overflow(Gtk.Overflow.HIDDEN)
            picture = Gtk.Picture()
            picture.set_can_shrink(True)
            picture.set_content_fit(Gtk.ContentFit.CONTAIN if shot.width else Gtk.ContentFit.COVER)
            from .media import texture
            decoded = texture(path)
            if decoded is not None:
                picture.set_paintable(decoded)
            else:
                picture.set_filename(path)
            picture.set_hexpand(True)
            picture.set_vexpand(True)
            frame.append(picture)
            frame.update_property([Gtk.AccessibleProperty.LABEL],
                                  [shot.caption or f"{app.name} screenshot"])
            strip.append(frame)
        for _ in range(waiting):
            # Downloading: the frame is real, the picture is on its way.
            frame = Gtk.Box()
            frame.add_css_class("dp-shot")
            frame.add_css_class("loading")
            frame.set_size_request(round(SHOT_HEIGHT * 1.6), SHOT_HEIGHT)
            frame.update_property([Gtk.AccessibleProperty.LABEL], ["Screenshot loading"])
            strip.append(frame)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                      vscrollbar_policy=Gtk.PolicyType.NEVER)
        # Preserve the images plus the native 24px scrollbar and the
        # 16px external strip margin; NEVER policy ignores min-content-height.
        scroller.set_size_request(-1, SHOT_HEIGHT + 40)
        scroller.set_overlay_scrolling(False)
        scroller.add_css_class("dp-shots")
        scroller.set_child(strip)
        self.content.append(scroller)

    def _permission_notice(self, app: App, record: InstalledApp) -> Gtk.Widget:
        notice = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        notice.add_css_class("dp-banner")
        notice.add_css_class("dp-permission-notice")
        title = Gtk.Label(label=f"The update to {app.name} asks for more", xalign=0, wrap=True)
        title.add_css_class("dp-banner-title")
        notice.append(title)
        detail = Gtk.Label(
            label="Look at what changes before you update. Nothing changes until you do.",
            xalign=0, wrap=True)
        detail.add_css_class("dp-banner-detail")
        notice.append(detail)
        notice.append(PermissionList(tuple(change for change in record.permission_changes
                                           if change.change != "narrowed")))
        if app.app_id not in self.jobs:
            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            update = Gtk.Button(label="Update")
            update.add_css_class("luma-button")
            update.add_css_class("small")
            update.add_css_class("primary")
            update.connect("clicked", lambda *_: self._update(app.app_id, approve=True, expected_commit=record.update_commit,
                                                                         expected_installed_commit=record.commit))
            actions.append(update)
            note = Gtk.Label(label="Background updates skip this one until you do.", xalign=0, wrap=True,
                             hexpand=True)
            note.add_css_class("dp-get-note")
            actions.append(note)
            notice.append(actions)
        return notice

    def _reach_block(self, app: App, record: InstalledApp | None) -> Gtk.Widget:
        block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        permissions = record.permissions if record is not None and record.permissions else app.permissions
        computed = bool(record is not None and record.permissions) or app.permissions_computed
        note_text = ""
        if not permissions and not self.preview and app.flatpak_id and app.installable:
            state = self.permission_requests.get(app.app_id)
            if state is None:
                self._request_permissions(app)
                state = "loading"
            if isinstance(state, tuple):
                permissions, computed = state, True
            elif state == "loading":
                note_text = "Reading what it can reach from its sandbox\u2026"
            else:
                note_text = str(state)
        if permissions:
            block.append(PermissionList(permissions))
            if self.preview:
                note_text = ("You can change any of this later in Settings, and turning "
                             "something off never removes the app.")
            elif app.sandbox == "snap-strict":
                note_text = ("It runs in a strict snap sandbox. Beyond what every sandboxed snap may do, "
                             "Luma connects what is listed above when it installs it, because it needs "
                             "them to work.")
            elif computed:
                note_text = ("Read from this app\u2019s sandbox on this computer." if record is not None
                             else "Read by Depot from this app\u2019s sandbox settings.")
            else:
                note_text = "Worked out by Luma from this release\u2019s sandbox settings."
        elif not note_text:
            if app.system_state:
                note_text = ("It came with Luma and runs as part of the system, outside a sandbox, "
                             "so it can reach what your account can.")
            elif app.web_app:
                note_text = ("It runs in your web browser, which keeps it to what the browser "
                             "allows and asks before it uses your camera, microphone or location.")
            elif app.sandbox == "snap-strict":
                note_text = ("It runs in a strict snap sandbox: it reaches only what its snap "
                             "interfaces allow, and asks through the system for anything more.")
            elif app.channel_kind == "deb" or app.sandbox == "deb-capsule":
                note_text = ("It runs in a private Debian capsule: it sees your own folders and the "
                             "network, and keeps its data apart from the rest of the system.")
            elif app.channel_kind == "repository" or (app.channel_kind == "" and app.source_label
                                                      and app.source_label.startswith("From Fedora")):
                note_text = ("It installs as part of the system from its publisher\u2019s signed "
                             "repository, outside a sandbox, so it can reach what your account can.")
            elif app.flatpak_id:
                note_text = "Luma has not read what this app can reach yet."
            else:
                note_text = ("It installs from its publisher, outside a sandbox Depot can read, "
                             "so it can reach what your account can.")
        note = Gtk.Label(label=note_text, xalign=0, wrap=True)
        note.add_css_class("dp-acc-note")
        block.append(note)
        if app.sign_in in SIGN_IN:
            heading, body, notable = SIGN_IN[app.sign_in]
            frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            frame.add_css_class("dp-access")
            frame.add_css_class("dp-signin")
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            line.add_css_class("dp-acc")
            mark = Gtk.Box(valign=Gtk.Align.CENTER, hexpand=False, vexpand=False)
            mark.add_css_class("dp-accmark")
            if notable:
                mark.add_css_class("notable")
            icon = Gtk.Image(icon_name="avatar-default-symbolic", pixel_size=13, hexpand=True, vexpand=True)
            mark.append(icon)
            mark.set_hexpand(False)
            line.append(mark)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
            first = Gtk.Label(label=heading, xalign=0, wrap=True)
            first.add_css_class("dp-acc-title")
            second = Gtk.Label(label=body, xalign=0, wrap=True)
            second.add_css_class("dp-acc-detail")
            text.append(first)
            text.append(second)
            line.append(text)
            line.update_property([Gtk.AccessibleProperty.LABEL], [f"{heading}. {body}"])
            frame.append(line)
            block.append(frame)
        return block

    def _request_permissions(self, app: App) -> None:
        provider = getattr(self.catalogue_provider, "permissions", None)
        if provider is None:
            self.permission_requests[app.app_id] = "Luma has not read what this app can reach yet."
            return
        self.permission_requests[app.app_id] = "loading"

        def done(result, key=app.app_id):
            self.permission_requests[key] = (tuple(result.value) if result.ok and result.value
                                             else (result.error.hint if not result.ok else
                                                   "Luma has not read what this app can reach yet."))
            if self.view == "app" and self.view_argument == key:
                self.render()
        provider(app, done)

    def _facts(self, app: App, record: InstalledApp | None) -> Gtk.Widget:
        facts = Adw.WrapBox(child_spacing=16, line_spacing=8)
        facts.add_css_class("dp-facts")
        installs = ""
        if app.installs_total >= 100:
            installs = f"{app.installs_total:,}"
        for label, value in (
            ("Version", record.version if record and record.version else app.version),
            ("Updated", app.updated),
            ("Download", megabytes(app.download_bytes) if app.download_bytes else ""),
            ("On disk", megabytes(record.installed_bytes if record and record.installed_bytes
                                  else app.installed_bytes) if (record and record.installed_bytes) or app.installed_bytes else ""),
            ("Licence", app.licence),
            ("Installs", installs),
            ("From", app.source_title or app.developer),
            ("Updates", app.update_note),
            ("Age rating", _age_rating(app.age_rating)),
        ):
            if not value:
                continue
            fact = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            caption = Gtk.Label(label=label)
            caption.add_css_class("dp-fact-label")
            reading = Gtk.Label(label=str(value))
            reading.add_css_class("dp-fact-value")
            fact.append(caption)
            fact.append(reading)
            facts.append(fact)
        return facts

    def _links(self, app: App) -> Gtk.Widget | None:
        links = [(label, url) for label, url in (
            ("Website", app.homepage if app.installable else ""),
            ("Page on simplyluma.com", app.web_url),
            ("Support", app.support_url),
            ("Privacy policy", app.privacy_url),
            ("Source code", app.source_url),
        ) if url]
        if not links:
            return None
        row = Adw.WrapBox(child_spacing=6, line_spacing=6)
        row.add_css_class("dp-links")
        for label, url in links:
            button = Gtk.Button(label=label)
            button.add_css_class("luma-button")
            button.add_css_class("small")
            button.add_css_class("quiet")
            button.set_tooltip_text(url)
            button.connect("clicked", lambda _b, address=url: Gtk.UriLauncher.new(address).launch(self, None, None, None))
            row.append(button)
        return row

    def _detail_actions(self, app: App) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("dp-get")
        job = self.jobs.get(app.app_id)
        installed = app.app_id in self.installed
        record = self.installed.get(app.app_id)

        if job is not None and job.failed:
            failed = Gtk.Label(label=job.failed, xalign=0, wrap=True)
            failed.add_css_class("dp-failed")
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            column.append(failed)
            if job.detail:
                column.append(self._failure_details(job.detail))
            again = Gtk.Button(label="Try again")
            again.add_css_class("luma-button")
            again.set_halign(Gtk.Align.START)
            if job.kind in SYSTEM_JOBS:
                again.connect("clicked", lambda *_: self._system_change(app, job.kind))
            elif job.kind == "update":
                again.connect("clicked", lambda *_: self._update(app.app_id, approve=False,
                                                                         expected_commit=record.update_commit if record else "",
                                                                         expected_installed_commit=record.commit if record else ""))
            elif job.kind == "channel":
                again.connect("clicked", lambda *_: self._choose_app_channel(app, record.update_channel if record else 'beta'))
            else:
                again.connect("clicked", lambda *_: self._install(app))
            column.append(again)
            return column

        if job is not None:
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            stage = Gtk.Label(label=job.progress.stage if job.progress else "Starting", xalign=0)
            stage.add_css_class("dp-progress-stage")
            line.append(stage)
            self._progress_views.setdefault(app.app_id, []).append(
                lambda update, target=stage: target.set_label(update.stage))
            if job.kind not in SYSTEM_JOBS:
                counts = Gtk.Label(label=(
                    f"{megabytes(job.progress.transferred_bytes)} of {megabytes(job.progress.total_bytes)}"
                    if job.progress else "Starting"))
                counts.add_css_class("dp-progress-counts")
                line.append(counts)
                self._progress_views.setdefault(app.app_id, []).append(
                    lambda update, target=counts: target.set_label(
                        f"{megabytes(update.transferred_bytes)} of {megabytes(update.total_bytes)}"))
            if job.kind != "remove" and job.kind not in SYSTEM_JOBS and not app.channel_kind:
                cancel = Gtk.Button(label="Cancel")
                cancel.add_css_class("luma-button")
                cancel.add_css_class("small")
                cancel.connect("clicked", lambda *_: self._cancel(app.app_id))
                line.append(cancel)
            column.append(line)
            track = Gtk.ProgressBar()
            track.add_css_class("dp-track")
            track.set_fraction(job.progress.fraction if job.progress else 0.0)
            self._progress_views.setdefault(app.app_id, []).append(
                lambda update, target=track: target.set_fraction(update.fraction))
            column.append(track)
            return column

        if app.system_state and app.system_state != "layered":
            return self._system_actions(app, row)

        if app.luma_only:
            get_luma = Gtk.Button(label="Get Luma")
            get_luma.add_css_class("luma-button")
            get_luma.add_css_class("small")
            get_luma.set_tooltip_text(app.homepage)
            get_luma.connect("clicked", lambda *_: Gtk.UriLauncher.new(app.homepage).launch(self, None, None, None))
            row.append(get_luma)
            note = Gtk.Label(label="Available on Luma", xalign=0, hexpand=True)
            note.add_css_class("dp-get-note")
            row.append(note)
            return row

        if installed:
            open_button = Gtk.Button(label="Open")
            open_button.add_css_class("luma-button")
            open_button.add_css_class("small")
            open_button.add_css_class("primary")
            if self.preview:
                open_button.connect("clicked", lambda *_: self._report("Preview only", "No application is launched."))
            else:
                open_button.connect("clicked", lambda *_: self._open_installed(app))
            row.append(open_button)
            managed = bool(self.installed.get(app.app_id) and self.installed[app.app_id].managed)
            remove = Gtk.Button(label="Remove" if (self.preview or managed) else "Review removal")
            remove.set_sensitive(app.removable or managed)
            remove.add_css_class("luma-button")
            remove.add_css_class("small")
            remove.add_css_class("quiet")
            remove.connect("clicked", lambda *_: self._confirm_remove(app))
            row.append(remove)
            if record and record.update_channel and hasattr(self.installer, 'review_channel'):
                channel = Gtk.Button(label="Update channel: " + record.update_channel.title())
                channel.add_css_class("luma-button")
                channel.add_css_class("quiet")
                channel.connect("clicked", lambda *_: self._choose_app_channel(app, record.update_channel))
                row.append(channel)
            return row

        if not app.installable and app.homepage:
            # The rare listing with no official channel Depot can install
            # from (the catalogue says why). Say so plainly and link to the
            # publisher, rather than a dead button.
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            publisher = Gtk.Button(label="Open in Browser" if app.web_app else "Visit Website")
            publisher.add_css_class("luma-button")
            publisher.add_css_class("small")
            publisher.set_halign(Gtk.Align.START)
            publisher.set_tooltip_text(app.homepage)
            publisher.connect("clicked", lambda *_: Gtk.UriLauncher.new(app.homepage).launch(self, None, None, None))
            row.append(publisher)
            if not app.publisher_reason:
                note = Gtk.Label(label=f"Installed from {app.developer}\u2019s website" if app.developer
                                 else "Installed from the publisher\u2019s website", wrap=True)
                note.add_css_class("dp-get-note")
                row.append(note)
                return row
            column.append(row)
            reason = Gtk.Label(label=app.publisher_reason, xalign=0, wrap=True)
            reason.set_max_width_chars(60)
            reason.add_css_class("dp-get-note")
            reason.add_css_class("dp-publisher-reason")
            column.append(reason)
            return column
        get = Gtk.Button(label="Install")
        get.add_css_class("luma-button")
        get.add_css_class("small")
        get.add_css_class("primary")
        get.set_sensitive(app.installable)
        get.set_tooltip_text(app.availability or None)
        get.connect("clicked", lambda *_: self._install(app))
        row.append(get)
        room = Gtk.Label(label=(megabytes(app.download_bytes) if app.download_bytes
                                else ("" if app.source_label else app.availability))
                         if app.installable else (app.availability or "Not available yet"))
        room.add_css_class("dp-get-note")
        row.append(room)
        return row

    def _system_actions(self, app: App, row: Gtk.Box) -> Gtk.Widget:
        """ADR-031: an app Luma's image ships, as it stands on this computer."""
        state = app.system_state

        def button(text, callback, *, primary=False, quiet=False, sensitive=True):
            widget = Gtk.Button(label=text)
            widget.add_css_class("luma-button")
            widget.add_css_class("small")
            if primary:
                widget.add_css_class("primary")
            if quiet:
                widget.add_css_class("quiet")
            widget.set_sensitive(sensitive and not (app.system_busy and callback != self._confirm_system_restart))
            widget.connect("clicked", lambda *_: callback())
            row.append(widget)
            return widget

        def note(text):
            # One line beside the buttons, as long as the page allows; the
            # full sentence stays in the tooltip and the accessible label.
            label = Gtk.Label(label=text, xalign=0, hexpand=True)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_tooltip_text(text)
            label.add_css_class("dp-get-note")
            row.append(label)
            return label

        if state == "installed":
            button("Open", lambda: self._open_installed(app), primary=True,
                   sensitive=app.app_id in self.installed)
            if app.system_removable:
                button("Remove", lambda: self._confirm_system_change(app, "override-remove"), quiet=True)
            note(app.availability)
        elif state == "removal-pending":
            button("Restart to Finish", self._confirm_system_restart, primary=True)
            button("Keep", lambda: self._confirm_system_change(app, "override-reset"), quiet=True)
            note(app.availability)
        elif state == "removed":
            button("Restore", lambda: self._confirm_system_change(app, "override-reset"), primary=True)
            note("Removed \u00b7 Restore brings it back with Luma")
        elif state == "restore-pending":
            button("Restart to Finish", self._confirm_system_restart, primary=True)
            note(app.availability)
        lines = []
        if state == "installed" and app.system_note:
            lines.append((app.system_note, "dp-get-note"))
        if app.system_busy:
            lines.append(("Another change to the system is in progress. Try again when it finishes.", "dp-get-note"))
        if self.system_error and self.view_argument == app.app_id:
            lines.append((self.system_error, "dp-failed"))
        if not lines:
            return row
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        column.append(row)
        for text, style in lines:
            label = Gtk.Label(label=text, xalign=0, wrap=True)
            label.add_css_class(style)
            column.append(label)
        return column

    def _choose_app_channel(self, app, current):
        target = 'beta' if current == 'nightly' else 'nightly'
        heading = f"Use {target.title()} updates for {app.name}?"
        body = ("Nightly builds arrive sooner and may have unfinished changes. " if target == 'nightly' else
                "Beta follows tested preview releases. ")
        body += "This changes only this app. Your OS update channel and documents stay as they are."
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response('cancel', 'Cancel'); dialog.add_response('review', 'Review update')
        dialog.set_close_response('cancel')
        dialog.set_default_response('cancel' if target == 'nightly' else 'review')
        def chosen(_dialog, name):
            if name != 'review': return
            self.installer.review_channel(app.app_id, target,
                lambda result: self._app_channel_reviewed(app, result))
        dialog.connect('response', chosen); dialog.present(self)

    def _app_channel_reviewed(self, app, result):
        if not result.ok:
            self._report("Could not review the update channel", result.error.hint or str(result.error))
            return
        reviewed = result.value
        dialog = Adw.AlertDialog(heading=f"Switch {app.name} to {reviewed['branch'].title()}?",
                                body="The signed application below replaces this app’s current channel. Your documents are retained.")
        changes = reviewed['permissions']
        if changes: dialog.set_extra_child(PermissionList(changes))
        dialog.add_response('cancel', 'Cancel'); dialog.add_response('switch', 'Switch channel')
        dialog.set_close_response('cancel'); dialog.set_default_response('cancel')
        dialog.set_response_appearance('switch', Adw.ResponseAppearance.SUGGESTED)
        def chosen(_dialog, name):
            if name != 'switch' or app.app_id in self.jobs: return
            cancellable = Gio.Cancellable()
            job = self.jobs[app.app_id] = Job(app_id=app.app_id, kind='channel', cancellable=cancellable)
            self.render()
            self.installer.switch_channel(app.app_id, reviewed, lambda p: self._progress_for_job(job,p),
                lambda outcome: self._app_channel_done(app.app_id, outcome, job), cancellable)
        dialog.connect('response', chosen); dialog.present(self)

    def _app_channel_done(self, app_id, result, owned=None):
        if not self._current_transaction(owned,result): return
        if result.ok:
            self.jobs.pop(app_id, None)
            self.installer.installed(self._installed_loaded)
        else:
            job = self.jobs.get(app_id)
            if job:
                job.failed = result.error.hint or str(result.error)
                job.detail = getattr(result.error, 'detail', '')
                job.progress = None
        self.render()

    def _confirm_system_change(self, app: App, action: str) -> None:
        removing = action == "override-remove"
        others = ", ".join(app.system_siblings)
        if removing:
            heading = f"Remove {app.name}?"
            body = (f"{app.name} came with Luma. It is removed when you restart and stays removed "
                    "through updates. You can restore it from Depot at any time. Your documents and "
                    "settings stay on this computer.")
            if others:
                body += f"\n\n{others} come in the same part of Luma and are removed with it."
        else:
            heading = f"Restore {app.name}?"
            body = f"Luma adds {app.name} back when you restart."
            if others:
                body += f" {others} come back with it."
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("cancel", "Cancel")
        verb = "remove" if removing else "restore"
        dialog.add_response(verb, "Remove" if removing else "Restore")
        dialog.set_response_appearance(
            verb, Adw.ResponseAppearance.DESTRUCTIVE if removing else Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel" if removing else verb)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, name: self._system_change(app, action) if name == verb else None)
        dialog.present(self)

    def _system_change(self, app: App, action: str) -> None:
        if app.app_id in self.jobs and not self.jobs[app.app_id].failed: return
        change = getattr(self.installer, "system_change", None)
        if change is None:
            self._report("Preview only", "Nothing on this computer changes.")
            return
        self.system_error = ""
        job = self.jobs[app.app_id] = Job(app_id=app.app_id, kind=action, cancellable=Gio.Cancellable(),
                                    progress=Progress(app.app_id, 0.0, 0, 0,
                                                      "Removing" if action == "override-remove" else "Restoring"))
        self.render()
        change(app, action, lambda p: self._progress_for_job(job,p),
               lambda result, value=app, kind=action: self._system_done(value, kind, result, job), job.cancellable)

    def _system_done(self, app: App, action: str, result: Result, owned=None) -> None:
        if not self._current_transaction(owned,result): return
        job = self.jobs.get(app.app_id)
        if result.ok:
            self.jobs.pop(app.app_id, None)
        elif job is not None:
            job.failed = result.error.hint or str(result.error)
            job.detail = getattr(result.error, "detail", "")
            job.progress = None
        # The staged deployment changed: the listing's state, My apps and the
        # Updates tab's "Restart to finish" all read it again.
        if self.system is not None:
            self.system._read()
        self.reload()

    def _confirm_system_restart(self) -> None:
        dialog = Adw.AlertDialog(
            heading="Restart to finish?",
            body="Save your work first. Luma restarts, finishes the change and starts again.")
        dialog.add_response("cancel", "Later")
        dialog.add_response("restart", "Restart")
        dialog.set_response_appearance("restart", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, name: self._restart_to_finish() if name == "restart" else None)
        dialog.present(self)

    def _restart_to_finish(self) -> None:
        """Every "Restart to finish" in Depot: the Updates tab's and an app page's."""
        from .restart import restart
        state = self.system.state if self.system is not None else None
        update_staged = bool(state is not None and state.service and state.state == "staged")

        def refused(sentence):
            self.system_error = sentence
            if self.system is not None:
                self.system.error = sentence
            self.render()
        restart(update_staged=update_staged, on_refused=refused, system_updates=self.system)

    def _section(self, title: str, child: Gtk.Widget) -> Gtk.Widget:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        section.add_css_class("dp-section")
        section.append(SectionLabel(title, variant="content"))
        child.add_css_class("dp-section-body")
        section.append(child)
        return section

    # ── Installing ───────────────────────────────────────────────────────

    # ── Links ────────────────────────────────────────────────────────────

    def open_uri(self, uri: str) -> bool:
        link = parse_link(uri)
        if link is None:
            return False
        self.open_link(link)
        return True

    def open_link(self, link: Link) -> None:
        """Show what a link names. An install link only ever gets as far as asking."""
        if self.catalogue is None:
            self.pending_link = link
            return
        if link.action == "collection":
            self.go("collection", link.target)
            return
        app = self.catalogue.find_link(link.target)
        if app is None:
            self.go("missing", link.target)
            return
        self.go("app", app.app_id)
        if link.action == "install" and app.app_id not in self.installed and app.app_id not in self.jobs:
            GLib.idle_add(lambda: (self._confirm_install(app), GLib.SOURCE_REMOVE)[1])

    def _confirm_install(self, app: App) -> None:
        if self.fixture:
            self._install(app)
            return
        if not app.installable:
            self._report(f"{app.name} cannot be installed here",
                         app.availability or "Depot cannot install this app on this computer.")
            return
        details = [f"From {app.source_title}" if app.source_title else "",
                   megabytes(app.download_bytes) if app.download_bytes else ""]
        dialog = Adw.AlertDialog(
            heading=f"Install {app.name}?",
            body=" \u00b7 ".join(part for part in details if part) or None)
        notable = tuple(permission for permission in app.permissions
                        if permission.level in ("sensitive", "high"))
        extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        if notable:
            caption = Gtk.Label(label="It will be able to", xalign=0)
            caption.add_css_class("dp-acc-note")
            extra.append(caption)
            extra.append(PermissionList(notable))
        if app.sign_in in SIGN_IN and app.sign_in != "none":
            line = Gtk.Label(label=SIGN_IN[app.sign_in][0], xalign=0, wrap=True)
            line.add_css_class("dp-acc-note")
            extra.append(line)
        if app.runtime_note:
            line = Gtk.Label(label=app.runtime_note, xalign=0, wrap=True)
            line.add_css_class("dp-acc-note")
            extra.append(line)
        if app.channel_kind:
            line = Gtk.Label(label="Luma asks for your password to add it to this computer.",
                             xalign=0, wrap=True)
            line.add_css_class("dp-acc-note")
            extra.append(line)
        if extra.get_first_child() is not None:
            dialog.set_extra_child(extra)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("install", "Install")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("install")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, name: self._install(app) if name == "install" else None)
        dialog.present(self)

    # ── Installing (continued) ───────────────────────────────────────────

    def _install(self, app: App) -> None:
        if app.app_id in self.jobs and not self.jobs[app.app_id].failed: return
        cancellable = Gio.Cancellable()
        job = Job(app_id=app.app_id, kind="install", cancellable=cancellable)
        self.jobs[app.app_id] = job
        self.render()
        self.installer.install(app, lambda p: self._progress_for_job(job,p),
                               lambda result, value=app: self._install_done(value, result, job),
                               cancellable)

    def _update(self, app_id: str, *, approve: bool = False, automatic: bool = False,
                expected_commit: str | None = None, expected_installed_commit: str | None = None) -> bool:
        if app_id in self.jobs and not self.jobs[app_id].failed:
            return False
        from luma_installer import depot_autoupdate
        record = self.installed.get(app_id)
        # Every action binds the snapshot shown to the user or chosen by the
        # background planner. Refreshing inventory cannot silently grant consent.
        if (record is None or not record.has_update or not expected_commit
                or not expected_installed_commit or record.update_commit != expected_commit
                or record.commit != expected_installed_commit):
            if not automatic:
                self._report("This update changed", "Refresh Updates and review the new build before installing it.")
            return False
        pending = depot_autoupdate.Pending(
            app_id, record.app.name if record.app else app_id, record.update_version,
            self._asks_for_more(record), expected_commit, expected_installed_commit)
        if pending.widens:
            if approve and not getattr(self.installer, 'requires_permission_approval', False):
                # Only the Update action below the displayed permission changes
                # supplies approval. Bulk, retry and background actions do not.
                try:
                    depot_autoupdate.approve(pending)
                except (OSError, ValueError):
                    if not automatic:
                        self._report("Could not save permission approval", "Try reviewing this update again.")
                    return False
            elif not approve and depot_autoupdate.is_held(pending,
                    getattr(self.installer, 'permission_state', None) or depot_autoupdate.load()):
                if not automatic:
                    self.go("app", app_id)
                return False
        self._remember_before_update(app_id, automatic)
        cancellable = Gio.Cancellable()
        job = self.jobs[app_id] = Job(app_id=app_id, kind="update", cancellable=cancellable)
        self.render()
        extra = ({'permission_approval': approve} if getattr(self.installer, 'requires_permission_approval', False) else {})
        self.installer.update(app_id, lambda p: self._progress_for_job(job,p),
                              lambda result, value=app_id: self._update_done(value, result, job),
                              cancellable, expected_commit=expected_commit,
                              expected_installed_commit=expected_installed_commit, **extra)
        return True

    def _cancel(self, app_id: str) -> None:
        job = self.jobs.get(app_id)
        if job is None:
            return
        job.cancellable.cancel()
        if getattr(self.installer, 'delivers_cancelled_terminal', False):
            # Native transactions may have committed just before cancellation.
            # Keep this exact job until its real terminal reply; a retry cannot
            # replace it while completion is still unknown.
            self.render()
            return
        self.jobs.pop(app_id, None)
        # A cancelled worker delivers nothing, so anything waiting on this job
        # hears about it here.
        cancelled = Result(error=ProviderError("Cancelled", hint="Cancelled"))
        for listener in tuple(self.install_listeners):
            handler = getattr(listener, "update_finished" if job.kind == "update" else "install_finished", None)
            if handler is not None:
                handler(app_id, cancelled, True)
        self.render()

    def _progress(self, progress: Progress) -> bool:
        job = self.jobs.get(progress.app_id)
        if job is None:
            return GLib.SOURCE_REMOVE
        job.progress = progress
        for update in tuple(self._progress_views.get(progress.app_id, ())):
            update(progress)
        # Progress changes the existing controls, never the page's widget tree,
        # image loaders, measured height, or current scroll position.
        self._refresh_sidebar_counts()
        return GLib.SOURCE_REMOVE

    def _current_transaction(self, job, result=None):
        if job is None or self.jobs.get(job.app_id) is job: return True
        # A late successful commit must still refresh actual installed state,
        # but never alter a later retry or notify its listeners twice.
        if result is not None and result.ok:
            self.installer.installed(self._installed_loaded)
        return False

    def _progress_for_job(self, job, progress):
        if self.jobs.get(job.app_id) is job: return self._progress(progress)
        return GLib.SOURCE_REMOVE

    def _install_done(self, app: App, result: Result, owned=None) -> None:
        if not self._current_transaction(owned,result): return
        job = self.jobs.get(app.app_id)
        for listener in tuple(self.install_listeners):
            handler = getattr(listener, "install_finished", None)
            if handler is not None:
                handler(app.app_id, result, job is not None and job.cancellable.is_cancelled())
        if result.ok:
            self.jobs.pop(app.app_id, None)
            self.installer.installed(self._installed_loaded)
        elif job is not None:
            if job.cancellable.is_cancelled():
                self.jobs.pop(app.app_id, None)
            else:
                job.failed = result.error.hint or str(result.error)
                job.detail = getattr(result.error, "detail", "")
                job.progress = None
        self.render()

    def _update_done(self, app_id: str, result: Result, owned=None) -> None:
        if not self._current_transaction(owned,result): return
        job = self.jobs.get(app_id)
        for listener in tuple(self.install_listeners):
            handler = getattr(listener, "update_finished", None)
            if handler is not None:
                handler(app_id, result, job is not None and job.cancellable.is_cancelled())
        if result.ok:
            self.jobs.pop(app_id, None)
            self.installer.installed(self._installed_loaded)
        elif job is not None and not job.cancellable.is_cancelled():
            job.failed = result.error.hint or str(result.error)
            job.detail = getattr(result.error, "detail", "")
            job.progress = None
        else:
            self.jobs.pop(app_id, None)
        self.render()

    def _open_installed(self, app):
        if self.fixture:
            Toast.show(self, f"Opening {app.name}", kind="opening")
            return
        pending = self._pending_open
        if pending is not None and pending['app_id'] == app.app_id:
            return
        self._cancel_pending_open()
        if not self.get_mapped():
            return
        toast = Toast.show(self, f"Opening {app.name}", kind="opening")
        pending = {'app_id': app.app_id, 'toast': toast, 'tick': 0,
                   'clock': None, 'paint': 0, 'dispatch': 0}
        self._pending_open = pending

        def dispatch():
            pending['dispatch'] = 0
            if self._pending_open is not pending or not self.get_mapped():
                return GLib.SOURCE_REMOVE
            self._pending_open = None
            try:
                self.installer.launch(app.app_id, self.get_display().get_app_launch_context())
            except Exception:
                self._report("Could not open the application", "Refresh My apps and try again.")
            return GLib.SOURCE_REMOVE

        def painted(clock):
            clock.disconnect(pending['paint'])
            pending['paint'] = 0
            # The frame containing the allocated, shown toast has completed.
            # Dispatch outside GTK's paint phase; a synchronous launcher may
            # otherwise prevent the first feedback frame from being rendered.
            if self._pending_open is pending and self.get_mapped():
                pending['dispatch'] = GLib.idle_add(dispatch)

        def ready(_toast, clock):
            if self._pending_open is not pending:
                pending['tick'] = 0
                return GLib.SOURCE_REMOVE
            if not (toast.get_mapped() and toast.get_width() > 0 and
                    toast.get_height() > 0 and toast.has_css_class('shown')):
                return GLib.SOURCE_CONTINUE
            pending['clock'] = clock
            pending['paint'] = clock.connect('after-paint', painted)
            pending['tick'] = 0
            return GLib.SOURCE_REMOVE

        pending['tick'] = toast.add_tick_callback(ready)

    def _cancel_pending_open(self):
        pending, self._pending_open = self._pending_open, None
        if pending is None:
            return
        if pending['tick']:
            pending['toast'].remove_tick_callback(pending['tick'])
        if pending['paint']:
            pending['clock'].disconnect(pending['paint'])
        if pending['dispatch']:
            GLib.source_remove(pending['dispatch'])
        pending['toast'].dismiss()

    def _toggle_whats_new(self) -> None:
        if not self.fixture:
            state = self.system.state if self.system is not None else None
            url = state.notes_url if state is not None else ""
            if not url:
                return
        self.show_whats_new = not self.show_whats_new
        self.show_all_system_notes = False
        if self.show_whats_new and not self.fixture:
            from . import release_notes
            if self.system_release_notes_url != url:
                self.system_release_notes_url = url
                self.system_release_notes = release_notes.CACHE.get(url)
                self.system_release_notes_error = ""
                if self.system_release_notes is None:
                    name = state.offered_name or state.offered_version or "Luma update"
                    def load_notes():
                        try:
                            return release_notes.fetch(url, fallback_name=name)
                        except release_notes.NotesError as error:
                            raise ProviderError(error.message, hint=error.message,
                                                detail=error.detail) from error
                    def loaded(result):
                        if self.system_release_notes_url != url:
                            return
                        if result.ok:
                            self.system_release_notes = result.value
                            release_notes.CACHE.put(url, result.value)
                        else:
                            self.system_release_notes_error = result.error.hint or str(result.error)
                        if self.view == "updates" and self.show_whats_new:
                            self.render()
                    run_async(load_notes, loaded)
        self.render()

    def _show_all_system_notes(self) -> None:
        self.show_all_system_notes = True
        self.render()

    def _show_uninstall_menu(self, listing, anchor=None) -> None:
        app = self.catalogue.find(listing.id) if self.catalogue else None
        if app is None:
            return
        anchor = anchor or self._uninstall_buttons.get(listing.id)
        if anchor is None:
            self._confirm_remove(app)
            return
        groups = []
        if self.view != "app":
            groups.append(CommandGroup("", (
                Command("depot.about-app", "About this app", lambda: self.go("app", app.app_id),
                        "info"),
            )))
        groups.append(CommandGroup("", (
            Command("depot.uninstall", "Uninstall", lambda: self._confirm_remove(app),
                    "trash-2", destructive=True),
        )))
        registry = CommandRegistry(tuple(groups))
        menu = Menu(registry)
        menu.present_for(anchor)

    def _confirm_remove(self, app: App) -> None:
        record = self.installed.get(app.app_id)
        if not self.preview and (record is None or not record.managed):
            try:
                self.installer.review_removal(app.app_id)
            except Exception:
                self._report("Could not review removal", "Refresh My apps and try again.")
            return
        def confirm(also_remove_data: bool) -> None:
            if app.app_id in self.jobs and not self.jobs[app.app_id].failed: return
            job = self.jobs[app.app_id] = Job(app_id=app.app_id, kind="remove",
                                        cancellable=Gio.Cancellable(),
                                        progress=Progress(app.app_id, 0.0, 0, 0, "Removing"))
            self.render()
            def removed(result, key=app.app_id):
                if not self._current_transaction(job,result): return
                self.jobs.pop(key, None)
                self._removed(result, app.name, also_remove_data)
            self.installer.remove(app.app_id, keep_data=not also_remove_data, callback=removed,
                                  cancellable=job.cancellable)
        kind = self.fixture_listings[app.app_id].kind if self.fixture else (
            "layered" if app.system_state else app.channel_kind or "flatpak")
        body = {
            "layered": f"{app.name} comes off the next time you restart. Until then it keeps working.",
            "deb": f"{app.name} and the parts it installed with it are removed from this computer.",
            "rpm": f"{app.name} and the parts it installed with it are removed from this computer.",
        }.get(kind, f"{app.name} is removed from this computer. You can get it again from Depot.")
        DestructiveDialog.ask(self, title=f"Uninstall {app.name}?",
            body=body,
            action="Uninstall", icon="trash-2",
            option="Also remove its data" if not app.channel_kind else None,
            on_confirm=confirm)

    def _removed(self, result: Result, name: str = "App", removed_data: bool = False) -> None:
        if not result.ok:
            self._report("That app was not removed", result.error.hint or str(result.error))
        elif isinstance(result.value, dict) and result.value.get("data_removal_error"):
            self._report(f"{name} was uninstalled, but its data remains",
                         f"Depot kept a backup at {result.value['backup']}. "
                         f"The data could not be removed: {result.value['data_removal_error']}")
        else:
            Toast.show(self, f"{name} uninstalled" + (" with its data" if removed_data else ". Its data is kept"),
                       kind="deleted")
        self.installer.installed(self._installed_loaded)

    # ── My apps ──────────────────────────────────────────────────────────

    def _render_mine(self) -> None:
        self._provisioning_banner()
        if not self.installed and not self.system_tools:
            self.content.set_valign(Gtk.Align.FILL)
            self.content.append(EmptyState(
                "No applications found",
                "Installed desktop applications appear here automatically.",
                "drive-harddisk-symbolic"))
            return
        if not self.installed:
            self._render_system_tools()
            return
        listing = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        listing.add_css_class("dp-rows")
        for record in sorted(self.installed.values(), key=lambda item: item.app_id):
            app = record.app or (self.catalogue.find(record.app_id) if self.catalogue else None)
            row = Gtk.Button()
            row.add_css_class("dp-row")
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            if app is not None:
                line.append(self._icon_tile(app, 34))
            copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
            name = Gtk.Label(label=app.name if app else record.app_id, xalign=0)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.add_css_class("dp-row-name")
            detail = Gtk.Label(
                label=app.summary if app else f"Version {record.version}",
                xalign=0)
            detail.set_ellipsize(Pango.EllipsizeMode.END)
            detail.add_css_class("dp-row-detail")
            copy.append(name)
            copy.append(detail)
            line.append(copy)
            disk = Gtk.Label(label=megabytes(record.installed_bytes))
            disk.add_css_class("dp-row-detail")
            line.append(disk)
            row.set_child(line)
            if app is not None:
                row.connect("clicked", lambda _b, value=app: self.go("app", value.app_id))
            listing.append(row)
        self.content.append(listing)
        self._render_system_tools()

    def _render_system_tools(self) -> None:
        """Packages added with sudo dnf install (or apt): ADR-038."""
        if not self.system_tools:
            return
        listing = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        listing.add_css_class("dp-rows")
        for tool in self.system_tools:
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            line.add_css_class("dp-row")
            copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
            name = Gtk.Label(label=tool.name, xalign=0)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.add_css_class("dp-row-name")
            detail = Gtk.Label(label=tool.detail, xalign=0)
            detail.set_ellipsize(Pango.EllipsizeMode.END)
            detail.add_css_class("dp-row-detail")
            copy.append(name)
            copy.append(detail)
            line.append(copy)
            if tool.state != "removal-pending":
                busy = tool.name in self.system_tools_busy
                remove = Gtk.Button(label="Removing\u2026" if busy else "Remove", valign=Gtk.Align.CENTER)
                remove.set_sensitive(not busy)
                remove.connect("clicked", lambda _b, value=tool: self._confirm_remove_system_tool(value))
                line.append(remove)
            listing.append(line)
        note = Gtk.Label(label="Added with sudo dnf install. They stay through Luma updates.", xalign=0, wrap=True)
        note.add_css_class("dp-get-note")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(listing)
        box.append(note)
        self.content.append(self._section("System tools you installed", box))

    def _confirm_remove_system_tool(self, tool) -> None:
        dialog = Adw.AlertDialog(
            heading=f"Remove {tool.name}?",
            body="It is removed from this computer right away. sudo dnf install brings it back.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def response(_dialog, choice: str) -> None:
            if choice != "remove":
                return
            self.system_tools_busy.add(tool.name)
            self.render()

            def done(result: Result, name=tool.name) -> None:
                self.system_tools_busy.discard(name)
                failure = result.value if result.ok else str(result.error)
                if failure:
                    self._report(f"{name} was not removed", failure)
                self._load_system_tools()
                self.render()
            if getattr(self, 'host_client', None) is not None:
                self.host_client.call('RemoveSystemTool', {'name': tool.name}, done)
            else:
                run_async(lambda: system_tools.remove(tool.name), done)
        dialog.connect("response", response)
        dialog.present(self)

    # ── Updates ──────────────────────────────────────────────────────────

    def _render_updates(self) -> None:
        if not self.preview:
            # Attention first, then history, then this computer, then preferences (updates_page.py).
            self._render_updates_page()
            return
        # Two classes, never one button. The OS applies on restart and keeps a
        # rollback; an app applies immediately and does not.
        self.content.append(self._os_block() if self.preview else self._system_block())
        if not self.preview:
            # Everything about this computer's system updates is on this one
            # page: what it runs, where that came from, which channel it
            # follows, and whether it downloads on its own. Nothing about
            # updates lives anywhere else a person has to go and find.
            facts = self._system_facts_block()
            if facts is not None:
                self.content.append(facts)
            channels = self._channel_block()
            if channels is not None:
                self.content.append(channels)
            preferences = self._download_preference_block()
            if preferences is not None:
                self.content.append(preferences)
        if self.firmware is not None:
            firmware = self._firmware_block()
            if firmware is not None:
                self.content.append(firmware)
        pending = [record for record in self.installed.values() if record.has_update]
        if self.catalogue:
            order = {app.app_id: index for index, app in enumerate(self.catalogue.apps)}
            pending.sort(key=lambda record: order.get(record.app_id, len(order)))
        apps_block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        apps_block.add_css_class("dp-apps")
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        head.add_css_class("dp-appshead")
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        title = Gtk.Label(label="Your apps", xalign=0)
        title.add_css_class("dp-apps-title")
        copy.append(title)
        total = sum(record.update_bytes for record in pending)
        summary = Gtk.Label(
            label=(f"{len(pending)} update{'s' if len(pending) != 1 else ''} · "
                   f"{megabytes(total)} · no restart needed") if pending
                  else self._no_updates_text(),
            xalign=0)
        summary.add_css_class("dp-apps-summary")
        copy.append(summary)
        head.append(copy)
        ready = [record for record in pending if not self._asks_for_more(record)
                 and record.app_id not in self.jobs]
        if len(ready) > 1:
            update_all = Gtk.Button(label="Update all" if len(ready) == len(pending) else f"Update {len(ready)}")
            update_all.add_css_class("luma-button")
            update_all.add_css_class("small")
            update_all.set_valign(Gtk.Align.CENTER)
            if len(ready) != len(pending):
                update_all.set_tooltip_text("Updates that ask for more wait until you look at them.")
            update_all.connect("clicked", lambda *_: [self._update(record.app_id, approve=False, expected_commit=record.update_commit, expected_installed_commit=record.commit) for record in ready])
            head.append(update_all)
        apps_block.append(head)

        if pending:
            listing = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            listing.add_css_class("dp-uplist")
            for index, record in enumerate(pending):
                if index:
                    rule = Gtk.Box()
                    rule.add_css_class("dp-acc-rule")
                    listing.append(rule)
                listing.append(self._update_row(record))
            apps_block.append(listing)
        self.content.append(apps_block)

        quiet = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        quiet.add_css_class("dp-quiet")
        quiet.append(Gtk.Image(icon_name="preferences-system-symbolic", pixel_size=15))
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        heading = Gtk.Label(label="Apps" if not self.preview else "Keep everything up to date on its own",
                            xalign=0, wrap=True)
        heading.add_css_class("dp-quiet-title")
        copy.append(heading)
        state = Gtk.Label(
            label=self._preferences_summary(), xalign=0, wrap=True)
        state.add_css_class("dp-quiet-detail")
        copy.append(state)
        quiet.append(copy)
        change = Gtk.Button(label="Change")
        change.add_css_class("luma-button")
        change.add_css_class("small")
        change.add_css_class("quiet")
        change.connect("clicked", lambda *_: self.go("preferences"))
        quiet.append(change)
        self.content.append(quiet)

    @staticmethod
    def _asks_for_more(record: InstalledApp) -> bool:
        return any(change.change in ("added", "widened") and change.key != "files.portal"
                   for change in record.permission_changes)

    def _no_updates_text(self) -> str:
        if self.preview:
            return "Every app is up to date."
        if any(record.managed for record in self.installed.values()):
            return "Every app Depot installed is up to date."
        if self.installed:
            return ("No app Depot installed needs an update. Apps installed another way update "
                    "wherever they came from.")
        return "Nothing installed from Depot yet. Apps you install here are updated here."

    def _preferences_summary(self):
        if not self.preview:
            # The system's own settings are on this page now; this row is about apps.
            return ("Apps update on their own every six hours, on connections that are not metered."
                    if self.settings.app_updates else "Apps update when you choose.")
        return ("Downloading automatically is " + ("on" if self.preview_preferences["download"] else "off")
                + ". Installing overnight is " + ("on" if self.preview_preferences["overnight"] else "off") + ".")

    def _render_preferences(self):
        if not self.preview:
            self._render_update_preferences()
            return
        group = Adw.PreferencesGroup(title="Automatic updates",
                                     description="Preview only. These choices do not change this computer.")
        for key, title in (("download", "Download automatically"), ("overnight", "Install overnight")):
            row = Adw.SwitchRow(title=title, active=self.preview_preferences[key])
            row.connect("notify::active", lambda widget, _pspec, key=key:
                        self.preview_preferences.__setitem__(key, widget.get_active()))
            group.add(row)
        self.content.append(group)

    def _render_update_preferences(self):

        # Everything about the system's own updates -- version, channel, where
        # it is hosted, whether it downloads on its own -- is on the Updates
        # page, so there is exactly one place to look and one place to change it.
        state = self.system.state if self.system is not None else None
        system = Adw.PreferencesGroup(title="System", margin_top=16)
        row = Adw.ActionRow(
            title="Updates",
            subtitle=(EARLY_UPDATES_SUBTITLE if state is None or not state.service else
                      "Automatic app updates, what was recently updated, the version this computer runs, "
                      "its update channel and where updates come from are all on the Updates page."))
        row.set_subtitle_lines(3)
        open_updates = Gtk.Button(label="Open Updates", valign=Gtk.Align.CENTER)
        open_updates.add_css_class("luma-button")
        open_updates.add_css_class("small")
        open_updates.connect("clicked", lambda *_: self.go("updates"))
        row.add_suffix(open_updates)
        row.set_activatable_widget(open_updates)
        system.add(row)
        self.content.append(system)
        if self.system is not None and self.system.error:
            failed = Gtk.Label(label=self.system.error, xalign=0, wrap=True, margin_top=8)
            failed.add_css_class("dp-failed")
            self.content.append(failed)

    def _confirm_remove_credential(self) -> None:
        """Drop a credential from before channels were public, keeping the channel."""
        from luma_installer.depot_system_update import channel_name
        state = self.system.state
        keep = state.channel if state is not None and state.channel in ("beta", "nightly") else ""
        dialog = Adw.AlertDialog(
            heading="Remove the early-updates credential?",
            body=(f"This computer keeps following Luma {channel_name(keep)}, from the public repository like "
                  f"every other computer. Nothing is installed or restarted." if keep else
                  "Nothing is installed or restarted."))
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_close_response("cancel")

        def response(_dialog, name):
            if name != "remove":
                return
            # LeavePreview removes the credential and asks for Official; the channel is then chosen again.
            then = (lambda: self.system.set_channel(keep)) if keep else None
            self.system.call("LeavePreview", then=then)
        dialog.connect("response", response)
        dialog.present(self)

    def _confirm_leave_preview(self, row) -> None:
        dialog = Adw.AlertDialog(
            heading="Stop getting early updates?",
            body="This computer goes back to Luma Official. It can wait until Official has a newer version "
                 "than the one you have, or switch to the newest Official version now, which may be older. "
                 "Your files, apps and settings stay as they are.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("wait", "Wait for Official")
        dialog.add_response("now", "Switch Now")
        dialog.set_response_appearance("wait", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")

        def response(_dialog, name):
            if name == "wait":
                self.system.call("LeavePreview")
            elif name == "now":
                self.system.call("LeavePreview", then=lambda: self.system.call("SetChannelNow", "stable"))
            elif row is not None:
                row.set_active(True)
            else:
                # The channel list draws itself from the agent; put it back.
                self.render()
        dialog.connect("response", response)
        dialog.present(self)

    def _render_settings(self):
        system_state = self.system.state if self.system is not None else None
        group = Adw.PreferencesGroup(
            title="Counting",
            description=counting_privacy_text(bool(system_state and system_state.preview_enrolled),
                                              system_state.preview_source if system_state else ""))
        events = Adw.SwitchRow(
            title="Count installs",
            subtitle="After an install, update or removal, send the app, its version, the "
                     "processor type and what happened. Nothing else.",
            active=self.settings.install_events)
        weekly = Adw.SwitchRow(
            title="Count this computer once a week",
            subtitle="When Depot reads the catalogue, say roughly how long Luma has been installed "
                     "here: its first week, month, six months, or longer.",
            active=self.settings.countme)

        def changed(*_):
            self.settings = depot_counting.Settings(install_events=events.get_active(),
                                                    countme=weekly.get_active(), app_updates=self.settings.app_updates)
            if not self.preview:
                try:
                    self._save_settings()
                except OSError:
                    self._report("Settings were not saved", "Depot could not write its settings file.")
        events.connect("notify::active", changed)
        weekly.connect("notify::active", changed)
        group.add(events)
        group.add(weekly)
        self.content.append(group)

    def _render_release(self):
        notes = self.os_state.notes if self.os_state else ()
        self.content.append(self._section("Release details", Gtk.Label(
            label="Illustrative preview release notes" if self.preview else "Reported by the update reader",
            xalign=0, wrap=True)))
        for note in notes:
            label = Gtk.Label(label="• " + note, xalign=0, wrap=True)
            label.add_css_class("dp-os-what")
            self.content.append(label)
        if not notes:
            self.content.append(EmptyState("No release notes available", "The update reader supplied no release notes.", "dialog-information-symbolic"))

    def _preview_os_block(self):
        block = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        block.add_css_class("dp-os")
        artwork = pathlib.Path(os.environ.get("LUMA_DEPOT_PREVIEW_ASSETS", "/nonexistent")) / "prism.png"
        art = Gtk.Overlay(width_request=104, height_request=104, valign=Gtk.Align.START, hexpand=False)
        art.set_child(Gtk.Box())
        art.add_css_class("dp-os-art")
        art.set_overflow(Gtk.Overflow.HIDDEN)
        if artwork.is_file():
            picture = Gtk.Picture.new_for_filename(str(artwork))
            picture.set_can_shrink(True)
            picture.set_content_fit(Gtk.ContentFit.COVER)
            picture.set_hexpand(True)
            picture.set_vexpand(True)
            art.add_overlay(picture)
        block.append(art)
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        block.append(copy)
        def label(text, style):
            widget = Gtk.Label(label=text, xalign=0, wrap=True)
            widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            widget.add_css_class(style)
            if style == "dp-os-what":
                # Match the reference reading measure without forcing narrow
                # windows to keep a desktop-sized label.
                def measure_width(label, _clock):
                    margin = max(0, copy.get_width() - 468)
                    if label.get_margin_end() != margin:
                        label.set_margin_end(margin)
                    return True
                widget.add_tick_callback(measure_width)
                copy.append(widget)
            else:
                copy.append(widget)
            return widget
        if self.os_state is None:
            label("Checking this computer…", "dp-os-meta")
            return block
        if not self.os_state.update_ready:
            label("YOUR SYSTEM", "dp-os-kicker")
            label("Fable Prairie 0.2", "dp-os-name")
            label("Up to date", "dp-os-ready")
            return block
        label("SYSTEM UPDATE", "dp-os-kicker")
        label("Fable Prairie 0.2", "dp-os-name")
        label("271 MB · Restarts to finish · You have 0.1", "dp-os-meta")
        label("A new overview for finding your windows and apps at a glance, quieter fans when the machine is working hard, and fixes for the dock and for where new windows open.", "dp-os-what")
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        if self.preview_staged:
            label("Preview: downloaded and ready", "dp-os-ready")
            label("No update was downloaded. This computer will not restart.", "dp-os-meta")
        else:
            update = Gtk.Button(label="Update now")
            update.add_css_class("luma-button")
            update.add_css_class("primary")
            update.update_property([Gtk.AccessibleProperty.LABEL], ["Preview update-ready state"])
            def stage(*_):
                self.preview_staged = True
                self.render()
            update.connect("clicked", stage)
            actions.append(update)
        from .preview import preview_release_notes
        notes = Gtk.Button(label="What’s new")
        notes.add_css_class("luma-button")
        notes.add_css_class("small")
        notes.add_css_class("quiet")
        notes.connect("clicked", lambda *_: self.show_release_notes(
            name="Fable Prairie 0.2", loader=preview_release_notes))
        actions.append(notes)
        copy.append(actions)
        checked = Gtk.Label(xalign=0, use_markup=True)
        checked.set_markup("Checked " + self.preview_checked + ' · <a href="preview:check">Check again</a>')
        checked.add_css_class("dp-get-note")
        def check(*_):
            self.preview_checked = "just now"
            self.render()
            return True
        checked.connect("activate-link", check)
        copy.append(checked)
        def adapt(widget, _clock):
            compact = self.get_width() <= 640
            widget.set_orientation(Gtk.Orientation.VERTICAL if compact else Gtk.Orientation.HORIZONTAL)
            art.set_size_request(76 if compact else 104, 76 if compact else 104)
            return True
        block.add_tick_callback(adapt)
        return block

    def _os_block(self) -> Gtk.Widget:
        block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        block.add_css_class("dp-os")
        state = self.os_state
        if self.preview:
            return self._preview_os_block()
        kicker = Gtk.Label(label="SYSTEM UPDATE", xalign=0)
        kicker.add_css_class("dp-os-kicker")
        block.append(kicker)

        if state is None:
            block.append(Gtk.Label(label="Checking this computer…", xalign=0))
            return block

        if not state.enrolled:
            heading = Gtk.Label(label="Not enrolled in an update channel", xalign=0, wrap=True)
            heading.add_css_class("dp-os-status")
            block.append(heading)
            reason = Gtk.Label(label=state.reason, xalign=0, wrap=True)
            reason.add_css_class("dp-os-what")
            block.append(reason)
            return block

        if not state.update_ready:
            heading = Gtk.Label(label="Update status unavailable" if state.reason or not state.booted_version else "No update staged", xalign=0, wrap=True)
            heading.add_css_class("dp-os-status")
            block.append(heading)
            meta = Gtk.Label(
                label=f"{state.channel or 'recent'} channel"
                      + (f" · you have {state.booted_version}" if state.booted_version else ""),
                xalign=0)
            meta.add_css_class("dp-os-meta")
            block.append(meta)
            if state.reason:
                note = Gtk.Label(label=state.reason, xalign=0, wrap=True)
                note.add_css_class("dp-os-what")
                block.append(note)
            return block

        heading = Gtk.Label(label=state.staged_version, xalign=0)
        heading.add_css_class("dp-os-name")
        block.append(heading)
        meta = Gtk.Label(
            label=f"Restarts to finish · you have {state.booted_version or 'an earlier build'}",
            xalign=0)
        meta.add_css_class("dp-os-meta")
        block.append(meta)
        ready = Gtk.Label(label="Already downloaded and staged. It applies when you restart.",
                          xalign=0, wrap=True)
        ready.add_css_class("dp-os-ready")
        block.append(ready)
        if state.rollback_kept:
            # The most reassuring thing Luma can say about updates, and nobody
            # else says it.
            rollback = Gtk.Label(
                label=f"The version you are on now is kept, so you can go back to it.",
                xalign=0, wrap=True)
            rollback.add_css_class("dp-os-rollback")
            block.append(rollback)
        note = Gtk.Label(
            label="Depot does not restart this computer. The update client staged this; "
                  "restart when it suits you.",
            xalign=0, wrap=True)
        note.add_css_class("dp-os-what")
        block.append(note)
        return block

    def _system_block(self) -> Gtk.Widget:
        from luma_installer.depot_system_update import booted_display_name
        block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        block.add_css_class("dp-os")
        system = self.system
        state = system.state if system is not None else None

        def label(text, style, *, wrap=True):
            widget = Gtk.Label(label=text, xalign=0, wrap=wrap)
            widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            widget.add_css_class(style)
            block.append(widget)
            return widget

        def button(text, callback, *, primary=False, quiet=False):
            widget = Gtk.Button(label=text)
            widget.add_css_class("luma-button")
            widget.add_css_class("small")
            if primary:
                widget.add_css_class("primary")
            if quiet:
                widget.add_css_class("quiet")
            widget.set_sensitive(not system.busy)
            widget.connect("clicked", lambda *_: callback())
            return widget

        if state is not None and state.security and (state.staged or state.available):
            kicker = label("IMPORTANT SECURITY UPDATE", "dp-os-kicker", wrap=False)
            kicker.add_css_class("security")
        else:
            label("SYSTEM UPDATE", "dp-os-kicker", wrap=False)
        if state is None:
            label("Checking this computer\u2026", "dp-os-meta")
            return block
        if not state.service:
            label(state.booted_name or booted_display_name(state.booted_version) or "Luma", "dp-os-name")
            label("System updates are not set up on this computer", "dp-os-status")
            label("Luma\u2019s update service (luma-update) is not installed here, so nothing can check "
                  "for, download or install system updates.", "dp-os-what")
            label("Installing luma-update, or reinstalling from a Luma image, brings system updates back. "
                  "Your apps are updated on this page as usual.", "dp-get-note")
            return block

        # While a channel is being adopted there is something real in flight, so
        # the ordinary progress and "ready" cards apply even though the booted
        # system still follows nothing.
        if not state.managed and not (state.downloading or state.staged or state.available):
            from luma_installer.depot_system_update import unmanaged_text
            what, next_step = unmanaged_text(state.unmanaged_reason)
            label(state.booted_name or booted_display_name(state.booted_version) or "Luma", "dp-os-name")
            label("Not following a Luma update channel", "dp-os-status")
            label(what, "dp-os-what")
            # The choice itself is the group directly below this card, so point
            # at it rather than repeating its button here.
            label(next_step + (" Choose one below." if state.adoptable else ""), "dp-get-note")
            label("Your apps are updated on this page as usual.", "dp-get-note")
            return block

        from luma_installer.depot_system_update import channel_name
        channel = f"{channel_name(state.channel)} channel" if state.channel else ""
        checked = state.checked_ago()
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_top=4)

        if state.rolled_back_version:
            notice = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            notice.add_css_class("dp-banner")
            words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
            heading = Gtk.Label(label="Luma went back to the previous version", xalign=0, wrap=True)
            heading.add_css_class("dp-banner-title")
            words.append(heading)
            again = ("Newer updates need it first, so Luma will try it again later."
                     if state.barrier_blocked and state.waiting_version == state.rolled_back_version
                     else "It will not try that update again.")
            detail = Gtk.Label(label=f"{state.rolled_back_name} did not start properly, so "
                                     f"Luma is using the version before it. {again}",
                               xalign=0, wrap=True)
            detail.add_css_class("dp-banner-detail")
            words.append(detail)
            notice.append(words)
            notice.append(button("OK", lambda: system.call("AcknowledgeRollback"), quiet=True))
            block.append(notice)

        if state.restart_required and not state.staged:
            label(state.booted_name or booted_display_name(state.booted_version) or "Luma", "dp-os-name")
            label("Restart to finish", "dp-os-status")
            label("A change to this computer\u2019s system waits for a restart.", "dp-os-what")
            actions.append(button("Restart now", self._confirm_restart, primary=True))
            block.append(actions)
        elif state.staged:
            label((state.staged_name or state.available_name) + " is ready", "dp-os-name")
            label(" \u00b7 ".join(part for part in (
                "Restarts to finish", f"you have {state.booted_name}" if state.booted_name else "",
                channel) if part), "dp-os-meta")
            if state.available_summary:
                label(state.available_summary, "dp-os-what")
            if state.removed_packages:
                label("Now part of Luma, so your own copy goes with this update: "
                      + ", ".join(state.removed_packages), "dp-os-what")
            actions.append(button("Restart to finish updating", self._confirm_restart, primary=True))
            if state.notes_url:
                actions.append(self._notes_button(state.notes_url, state.staged_name or state.available_name))
            self._not_now(actions, state, button)
            block.append(actions)
            if state.ignored:
                label("Ignored. Luma will not mention it again; it waits here until you restart.",
                      "dp-get-note")
            label("The version you have now is kept, so Luma can go back to it if the update "
                  "does not start properly.", "dp-os-rollback")
        elif state.downloading or (state.available and state.progress > 0):
            label(state.available_name, "dp-os-name")
            size = megabytes(state.download_bytes) if state.download_bytes else ""
            label(" \u00b7 ".join(part for part in (
                f"Downloading {round(state.progress * 100)}%", size, channel) if part), "dp-os-meta")
            track = Gtk.ProgressBar(fraction=state.progress, margin_top=4)
            track.add_css_class("dp-track")
            block.append(track)
            if state.available_summary:
                label(state.available_summary, "dp-os-what")
            if state.downloading:
                actions.append(button("Cancel", lambda: system.call("Cancel"), quiet=True))
                block.append(actions)
            label("You can keep working. Luma asks before it restarts.", "dp-get-note")
        elif state.available:
            label(state.available_name, "dp-os-name")
            size = megabytes(state.download_bytes) if state.download_bytes else ""
            label(" \u00b7 ".join(part for part in (
                size, f"you have {state.booted_name}" if state.booted_name else "", channel) if part),
                  "dp-os-meta")
            if state.available_summary:
                label(state.available_summary, "dp-os-what")
            # The agent waits for a person on a metered or unknown connection, on low or
            # unknown battery, and when automatic download is off. Download is therefore
            # always offered, and it is always the main action: most people do want the
            # update, and the ways of saying no are right beside it.
            actions.append(button("Download", lambda: system.call("Download"), primary=not state.ignored,
                                  quiet=state.ignored))
            if state.notes_url:
                actions.append(self._notes_button(state.notes_url, state.available_name))
            self._not_now(actions, state, button)
            block.append(actions)
            if state.kept_packages:
                note = ("Waiting: your own copy of " + ", ".join(state.kept_packages) + " is newer than the one "
                        "in this update, so it is kept. Luma updates once a release carries that version, "
                        "or after you remove your copy.")
            elif state.ignored:
                note = "Ignored. Luma will not mention it again, and will not download it on its own."
            elif state.metered:
                note = "This connection is metered, so Luma waits for you to download it."
            elif not state.automatic_download:
                note = "Luma downloads updates only when you ask. Nothing is installed until you restart."
            else:
                note = ("Luma downloads it in the background when this computer has enough power, and asks "
                        "before it restarts.")
            label(note, "dp-get-note")
        elif state.barrier_blocked:
            label(state.booted_name or booted_display_name(state.booted_version) or "Luma", "dp-os-name")
            label("Update waiting", "dp-os-status")
            label(" \u00b7 ".join(part for part in (channel, f"Checked {checked}" if checked else "") if part),
                  "dp-os-meta")
            label(f"{state.waiting_name or 'The next update'} has to be installed before "
                  "anything newer, and it did not finish last time. Luma will try it again automatically.",
                  "dp-os-what")
            actions.append(button("Check now", lambda: system.call("Check"), quiet=True))
            block.append(actions)
        elif state.state == "error" or (state.last_error and not state.booted_version):
            label("Luma could not check for updates", "dp-os-status")
            if state.last_error:
                label(state.last_error, "dp-os-what")
            if state.kept_packages:
                label("Kept: " + ", ".join(state.kept_packages), "dp-os-meta")
            actions.append(button("Try again", lambda: system.call("Check")))
            block.append(actions)
        else:
            label(state.booted_name or booted_display_name(state.booted_version) or "Luma", "dp-os-name")
            label("Checking for updates\u2026" if system.busy == "Check" or state.state == "checking"
                  else "Up to date", "dp-os-ready")
            label(" \u00b7 ".join(part for part in (channel, f"Checked {checked}" if checked else "") if part),
                  "dp-os-meta")
            if state.waiting_version:
                label(f"{state.waiting_name} is rolling out and reaches this computer soon.",
                      "dp-os-what")
            if state.booted_deadend_reason:
                label("Luma pulled this version: " + state.booted_deadend_reason, "dp-os-what")
            actions.append(button("Check now", lambda: system.call("Check"), quiet=True))
            block.append(actions)
            if state.last_error:
                label(state.last_error, "dp-get-note")

        if state.rollback_available and not state.staged and not state.downloading:
            back = button("Go back to the previous version\u2026", self._confirm_rollback, quiet=True)
            back.set_halign(Gtk.Align.START)
            back.set_margin_top(4)
            block.append(back)
        if system.error:
            label(system.error, "dp-failed")
        return block

    def _not_now(self, actions: Gtk.Box, state, button) -> None:
        """The way out of an update, always offered and never hidden.

        "Not now" is simply closing the page: nothing happens until a person
        presses Restart, so it needs no button of its own. What does need one
        is "Ignore this version" -- the promise that Luma will stop asking
        about this particular release -- and the way back from it.
        """
        version = state.offered_version
        if not version:
            return
        if state.ignored:
            actions.append(button("Stop ignoring", lambda: self.system.call("ClearIgnoredVersion"), quiet=True))
        else:
            actions.append(button("Ignore this version…", lambda: self._confirm_ignore(version, state.offered_name),
                                  quiet=True))

    # ── The audit: what this computer runs, and where it came from ───────

    def _fact_row(self, label: str, value: str, *, trailing: Gtk.Widget | None = None) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("dp-factrow")
        name = Gtk.Label(label=label, xalign=0, valign=Gtk.Align.BASELINE_CENTER)
        name.add_css_class("dp-fact-key")
        name.set_size_request(148, -1)
        row.append(name)
        text = Gtk.Label(label=value, xalign=0, wrap=True, hexpand=True, selectable=True,
                         valign=Gtk.Align.BASELINE_CENTER)
        text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        text.add_css_class("dp-fact-value")
        row.append(text)
        if trailing is not None:
            trailing.set_valign(Gtk.Align.CENTER)
            row.append(trailing)
        return row

    def _system_facts_block(self) -> Gtk.Widget | None:
        """What Luma knows about this computer's system, said out loud.

        Everything here is read from the agent, never assumed: the host comes
        from the image's own OSTree remote, so moving the downloads somewhere
        else changes this page without changing Depot.
        """
        from luma_installer.depot_system_update import (booted_display_name, channel_name, check_reason_text)
        system = self.system
        state = system.state if system is not None else None
        if state is None:
            return None

        block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        block.add_css_class("dp-apps")
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        head.add_css_class("dp-appshead")
        title = Gtk.Label(label="This computer", xalign=0)
        title.add_css_class("dp-apps-title")
        head.append(title)
        summary = Gtk.Label(label="What Luma is running, and where it came from.", xalign=0, wrap=True)
        summary.add_css_class("dp-apps-summary")
        head.append(summary)
        block.append(head)

        facts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        facts.add_css_class("dp-uplist")
        rows: list[Gtk.Widget] = []
        # Without the update service there is no agent to ask, but rpm-ostree
        # still knows what is booted: say that rather than "unknown".
        booted = state.booted_version or (self.os_state.booted_version if self.os_state else "")
        rows.append(self._fact_row("Version", state.booted_name or booted_display_name(booted) or "Unknown"))
        if booted:
            # The machine version, for anyone comparing builds or reporting a problem.
            rows.append(self._fact_row("Build", booted))
        following = state.service and state.managed
        rows.append(self._fact_row("Channel", channel_name(state.channel)) if following
                    else self._fact_row("Channel", "Not following one yet"))
        newest = state.offered_version or state.waiting_version
        if following:
            if newest and newest != state.booted_version:
                rows.append(self._fact_row("Newest", state.offered_name or state.waiting_name))
            elif state.state not in ("error", "checking"):
                rows.append(self._fact_row("Newest", "You have it"))
        checked = state.checked_ago()
        reason = check_reason_text(state.last_check_reason)
        again = Gtk.Button(label="Check now")
        again.add_css_class("luma-button")
        again.add_css_class("small")
        again.add_css_class("quiet")
        again.set_sensitive(bool(system) and not system.busy and following)
        again.connect("clicked", lambda *_: system.call("Check"))
        if following:
            rows.append(self._fact_row(
                "Last checked",
                " · ".join(part for part in (checked.capitalize() if checked else "Never", reason) if part),
                trailing=again))
        if state.repository_url:
            rows.append(self._fact_row("Updates from", state.host or state.repository_url))
        else:
            rows.append(self._fact_row("Updates from",
                                       "Luma’s update source is not set up on this computer"))
        rows.append(self._fact_row(
            "Signature",
            ("Verified" + (f" · key {state.signing_key_id}" if state.signing_key_id else ""))
            if state.signature_verified else
            "Nothing verified yet. Luma installs nothing it cannot verify."))
        if state.notes_url and state.offered_version:
            # Only ever the notes of the release being offered: a link left over from a
            # release this computer already has would be a lie about what is new.
            notes = self._notes_button(state.notes_url, state.offered_name, text="Show")
            rows.append(self._fact_row("What\u2019s new", state.offered_name or "The offered update",
                                       trailing=notes))
        for index, row in enumerate(rows):
            if index:
                rule = Gtk.Box()
                rule.add_css_class("dp-acc-rule")
                facts.append(rule)
            facts.append(row)
        block.append(facts)
        return block

    # ── Channels ─────────────────────────────────────────────────────────

    def _channel_block(self) -> Gtk.Widget | None:
        """Official, Beta or Nightly, chosen here, enrolled here.

        Always drawn, on every computer the update service is on, whether it
        follows a channel or not. A person is never shown a page that only says
        what it cannot do: the choice is visible, each option says what it is
        and how often it changes, and choosing one says what it would do before
        it happens.

        The agent owns the policy: SetChannel for a channel this computer may
        already follow, AdoptChannel for one that follows none, EnrollPreview
        with the Luma Connect device token the first time a preview channel is
        chosen, LeavePreview on the way out. Depot only asks.
        """
        from luma_installer.depot_system_update import (
            CHANNEL_DETAILS, CHANNEL_NAMES, CHANNEL_PACE, CHANNEL_REQUIREMENT, ROLLBACK_PROMISE,
            channel_consequence, channel_progress_text, preview_source_text)
        system = self.system
        state = system.state if system is not None else None
        if state is None:
            return None
        adopting = not state.service or not state.managed

        group = Adw.PreferencesGroup(
            title="Start following a Luma channel" if adopting else "Update channel", margin_top=24,
            description=("Choose which versions of Luma this computer should follow. Nothing is "
                         "installed until you restart, and the version you are running now is kept."
                         if adopting else
                         "Which versions of Luma this computer is offered. You can change this whenever "
                         "you like; leaving a preview channel waits until Official has something newer, "
                         "unless you ask to switch straight away."))
        # All three, always, and all public (ADR-030 section 4): choosing one
        # needs no account and no credential.
        offered = ["stable", "beta", "nightly"]
        busy = bool(system.busy)
        # A computer that follows no channel can only be moved onto one where the
        # agent says so; the rows are still shown, with the reason they are quiet.
        can_choose = (state.adoptable if adopting else True) and state.service and not busy
        blocked_reason = ""
        if not state.service:
            blocked_reason = ("Luma\u2019s update service (luma-update) is not installed on this computer, "
                              "so no channel can be followed yet. Installing it, or reinstalling from a "
                              "Luma image, makes this choice work.")
        elif adopting and not state.adoptable:
            from luma_installer.depot_system_update import unmanaged_text
            blocked_reason = unmanaged_text(state.unmanaged_reason)[1]
        first: Gtk.CheckButton | None = None
        for channel in offered:
            pace, testing = CHANNEL_PACE[channel]
            detail = f"{CHANNEL_DETAILS[channel]} {pace}, {testing}."
            requirement = CHANNEL_REQUIREMENT[channel]
            if requirement and not state.preview_enrolled:
                detail += f" {requirement}"
            row = Adw.ActionRow(title=CHANNEL_NAMES[channel], subtitle=detail)
            row.set_subtitle_lines(4)
            choice = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            choice.add_css_class("selection-mode")
            if first is None:
                first = choice
            else:
                choice.set_group(first)
            choice.set_active(self._chosen_channel(offered) == channel if adopting
                              else state.channel == channel)
            choice.set_sensitive(can_choose)
            row.add_prefix(choice)
            if can_choose:
                row.set_activatable_widget(choice)
            choice.connect("toggled", self._channel_chosen, channel)
            group.add(row)
        if adopting:
            # What choosing one would do, before it happens, for the one ticked.
            chosen = self._chosen_channel(offered)
            follow = Adw.ActionRow(
                title=f"What choosing {CHANNEL_NAMES[chosen]} would do",
                subtitle=channel_consequence(chosen, state.channel, adopting=True))
            follow.set_subtitle_lines(5)
            start = Gtk.Button(label="Start Following\u2026", valign=Gtk.Align.CENTER)
            start.add_css_class("luma-button")
            start.add_css_class("small")
            start.add_css_class("primary")
            start.set_sensitive(can_choose)
            if not can_choose and blocked_reason:
                start.set_tooltip_text(blocked_reason)
            start.connect("clicked", lambda *_, c=chosen: self._confirm_adopt(c))
            follow.add_suffix(start)
            group.add(follow)
            if blocked_reason:
                note = Adw.ActionRow(title="Why this is not available yet", subtitle=blocked_reason)
                note.set_subtitle_lines(4)
                group.add(note)
        progress = channel_progress_text(system.busy, system.busy_channel or "stable") \
            if system.busy in ("EnrollPreview", "SetChannel", "SetChannelNow", "AdoptChannel", "LeavePreview") else ""
        if progress:
            working = Adw.ActionRow(title=progress)
            working.set_title_lines(3)
            spinner = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)
            working.add_prefix(spinner)
            group.add(working)
        promise = Adw.ActionRow(title="If an update goes wrong", subtitle=ROLLBACK_PROMISE)
        promise.set_subtitle_lines(3)
        group.add(promise)
        if state.preview_enrolled:
            source = preview_source_text(state.preview_source)
            leave = Adw.ActionRow(
                title="Early-updates credential",
                subtitle=((source + " ") if source else "") +
                         "Every channel is public now, so this computer no longer needs it. Removing it keeps "
                         "the channel you follow.")
            leave.set_subtitle_lines(4)
            button = Gtk.Button(label="Remove\u2026", valign=Gtk.Align.CENTER)
            button.add_css_class("luma-button")
            button.add_css_class("small")
            button.set_sensitive(not busy)
            button.connect("clicked", lambda *_: self._confirm_remove_credential())
            leave.add_suffix(button)
            leave.set_activatable_widget(button)
            group.add(leave)
        if system.error:
            failed = Gtk.Label(label=system.error, xalign=0, wrap=True, margin_top=8)
            failed.add_css_class("dp-failed")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            box.append(group)
            box.append(failed)
            return box
        return group

    def _chosen_channel(self, offered) -> str:
        """The channel ticked right now on a computer that follows none.

        The agent has no answer here -- it follows nothing yet -- so Depot
        remembers the tick between renders and defaults to Official."""
        chosen = getattr(self, "pending_channel", "")
        return chosen if chosen in offered else "stable"

    def _confirm_adopt(self, channel: str) -> None:
        from luma_installer.depot_system_update import channel_name, channel_consequence
        dialog = Adw.AlertDialog(
            heading=f"Start following Luma {channel_name(channel)}?",
            body=channel_consequence(channel, "", adopting=True))
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("follow", "Start Following")
        dialog.set_response_appearance("follow", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")
        def response(_dialog, name):
            if name == "follow":
                # Every channel is public: adopting one needs no account and no credential.
                self.system.call("AdoptChannel", channel)
        dialog.connect("response", response)
        dialog.present(self)

    def _channel_chosen(self, button, channel: str) -> None:
        system = self.system
        state = system.state if system is not None else None
        if not button.get_active() or state is None:
            return
        if not state.managed:
            # Nothing is asked of the agent yet: ticking a channel on a computer
            # that follows none only chooses which one "Start Following" means,
            # and redraws what it would do.
            if getattr(self, "pending_channel", "") != channel:
                self.pending_channel = channel
                self.render()
            return
        if state.channel == channel:
            return
        if channel == "stable":
            # Leaving a preview channel removes a credential and asks what to do
            # about a version that may be newer than Official's; it is a question,
            # not a switch.
            if state.preview_enrolled:
                self._confirm_leave_preview(None)
            else:
                system.set_channel("stable")
            return
        self.render()  # the radio follows the agent, not the click, until the agent says so
        self._confirm_channel(channel)

    def _confirm_channel(self, channel: str) -> None:
        """Beta or Nightly: say what it means, then switch. No account, no credential."""
        from luma_installer.depot_system_update import CHANNEL_PACE, channel_consequence, channel_name
        state = self.system.state
        name = channel_name(channel)
        pace, testing = CHANNEL_PACE[channel]
        dialog = Adw.AlertDialog(
            heading=f"Follow Luma {name}?",
            body=f"{pace}, {testing}. " + channel_consequence(channel, state.channel if state else ""))
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("switch", f"Follow {name}")
        dialog.set_response_appearance("switch", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")

        def response(_dialog, answer):
            if answer == "switch":
                self.system.set_channel(channel)
        dialog.connect("response", response)
        dialog.present(self)

    # ── Downloading on its own ───────────────────────────────────────────

    def _download_preference_block(self) -> Gtk.Widget | None:
        """The download preference, always drawn.

        A control that cannot act right now is shown switched to what is
        actually true, turned off, and saying in its own subtitle why and what
        would change it. "Preferences unavailable" is never an answer.
        """
        from luma_installer.depot_system_update import automatic_download_note, unmanaged_text
        system = self.system
        state = system.state if system is not None else None
        group = Adw.PreferencesGroup(title="Downloading", margin_top=24)
        if state is None or not state.service:
            row = Adw.SwitchRow(
                title="Download updates automatically",
                subtitle="Luma\u2019s update service is not on this computer, so it downloads no system "
                         "updates. Installing luma-update, or reinstalling from a Luma image, brings this "
                         "back. Your apps still update below.",
                active=False, sensitive=False)
            row.set_subtitle_lines(4)
            group.add(row)
            return group
        reason = ""
        if not state.managed:
            reason = "Choose a channel above, and Luma will download its updates in the background."
        automatic = Adw.SwitchRow(
            title="Download updates automatically",
            subtitle=reason or automatic_download_note(state.metered, state.automatic_download),
            active=state.automatic_download and state.managed,
            sensitive=state.managed and not system.busy)
        automatic.set_subtitle_lines(4)

        def changed(row, _pspec):
            if row.get_active() != (self.system.state.automatic_download if self.system.state else True):
                self.system.call("SetAutomaticDownload", row.get_active())
        automatic.connect("notify::active", changed)
        group.add(automatic)
        if state.ignored_version and not state.ignored:
            # The way back, for a version that is not the one on the card above;
            # when it is, the card carries "Stop ignoring" itself.
            ignored = Adw.ActionRow(
                title=f"{state.ignored_name} is ignored",
                subtitle="Luma will not mention this version again. Newer versions are offered as usual.")
            ignored.set_subtitle_lines(2)
            back = Gtk.Button(label="Stop ignoring", valign=Gtk.Align.CENTER)
            back.add_css_class("luma-button")
            back.add_css_class("small")
            back.set_sensitive(not system.busy)
            back.connect("clicked", lambda *_: self.system.call("ClearIgnoredVersion"))
            ignored.add_suffix(back)
            ignored.set_activatable_widget(back)
            group.add(ignored)
        return group

    def _confirm_ignore(self, version: str, name: str = "") -> None:
        from luma_installer.depot_system_update import display_name
        dialog = Adw.AlertDialog(
            heading=f"Ignore {name or display_name(version)}?",
            body="Luma stops mentioning this version: no notifications, and it is not downloaded on its own. "
                 "You can still install it from here whenever you like, and a newer version is offered "
                 "as usual.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ignore", "Ignore This Version")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, name:
                       self.system.call("IgnoreVersion", version) if name == "ignore" else None)
        dialog.present(self)

    def _notes_button(self, url: str, name: str, *, text: str = "What\u2019s new") -> Gtk.Widget:
        """Every "What's new" in Depot: the release's notes in Depot's own sheet."""
        widget = Gtk.Button(label=text)
        widget.add_css_class("luma-button")
        widget.add_css_class("small")
        widget.add_css_class("quiet")
        widget.update_property([Gtk.AccessibleProperty.LABEL],
                               [f"What\u2019s new in {name}" if name else "What\u2019s new"])
        widget.connect("clicked", lambda *_: self.show_release_notes(url=url, name=name))
        return widget

    def show_release_notes(self, *, name: str, url: str = "", loader=None) -> None:
        from .release_notes_sheet import ReleaseNotesSheet
        ReleaseNotesSheet(name=name, url=url, loader=loader).present(self)

    def _confirm_restart(self) -> None:
        dialog = Adw.AlertDialog(
            heading="Restart to finish updating?",
            body="Save your work first. Luma restarts, finishes updating and starts again.")
        dialog.add_response("cancel", "Later")
        dialog.add_response("restart", "Restart")
        dialog.set_response_appearance("restart", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, name: self._restart_to_finish() if name == "restart" else None)
        dialog.present(self)

    def _confirm_rollback(self) -> None:
        dialog = Adw.AlertDialog(
            heading="Go back to the previous version?",
            body="Luma restarts into the version you had before the last update. Your files and "
                 "apps stay as they are.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rollback", "Go Back")
        dialog.set_response_appearance("rollback", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, name: self.system.call("Rollback") if name == "rollback" else None)
        dialog.present(self)

    def _firmware_block(self) -> Gtk.Widget | None:
        firmware = self.firmware
        if firmware.available is None:
            return None
        block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        block.add_css_class("dp-apps")
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        head.add_css_class("dp-appshead")
        title = Gtk.Label(label="Firmware", xalign=0)
        title.add_css_class("dp-apps-title")
        head.append(title)
        if not firmware.available:
            text = ("Luma cannot check this computer\u2019s firmware: the firmware service (fwupd) is not "
                    "installed here. Everything else on this page still works. Reinstalling from a Luma "
                    "image brings firmware updates back.")
        elif firmware.problem:
            text = firmware.problem
        elif not firmware.updates:
            text = "Every device\u2019s firmware is up to date."
        else:
            count = len(firmware.updates)
            text = f"{count} update{'s' if count != 1 else ''} \u00b7 installed only when you choose"
        summary = Gtk.Label(label=text, xalign=0, wrap=True)
        summary.add_css_class("dp-apps-summary")
        head.append(summary)
        block.append(head)
        if firmware.problem:
            again = Gtk.Button(label="Checking\u2026" if firmware.checking else "Try Again",
                               halign=Gtk.Align.START, margin_top=8, sensitive=not firmware.checking)
            for style in ("luma-button", "small", "quiet"):
                again.add_css_class(style)
            again.connect("clicked", lambda *_: firmware.load())
            block.append(again)
        if firmware.updates:
            listing = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            listing.add_css_class("dp-uplist")
            for index, update in enumerate(firmware.updates):
                if index:
                    rule = Gtk.Box()
                    rule.add_css_class("dp-acc-rule")
                    listing.append(rule)
                listing.append(self._firmware_row(update))
            block.append(listing)
        if firmware.error:
            failed = Gtk.Label(label=firmware.error, xalign=0, wrap=True, margin_top=6)
            failed.add_css_class("dp-failed")
            block.append(failed)
        return block

    def _firmware_row(self, update) -> Gtk.Widget:
        firmware = self.firmware
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        row.add_css_class("dp-uprow")
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        mark = Gtk.Box(valign=Gtk.Align.START)
        mark.add_css_class("dp-accmark")
        mark.append(Gtk.Image(icon_name="application-x-firmware-symbolic", pixel_size=13, hexpand=True, vexpand=True))
        mark.set_hexpand(False)
        mark.set_vexpand(False)
        line.append(mark)
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        name = Gtk.Label(label=update.device, xalign=0, wrap=True)
        name.add_css_class("dp-up-name")
        copy.append(name)
        versions = " \u2192 ".join(part for part in (update.current_version, update.version) if part)
        detail = Gtk.Label(label=" \u00b7 ".join(part for part in (update.vendor, versions) if part),
                           xalign=0, wrap=True)
        detail.add_css_class("dp-up-detail")
        copy.append(detail)
        if update.summary:
            summary = Gtk.Label(label=update.summary, xalign=0, wrap=True)
            summary.add_css_class("dp-get-note")
            copy.append(summary)
        notes = []
        if update.needs_reboot:
            notes.append("Finishes when you restart")
        if update.requires_ac:
            notes.append("Needs power connected")
        if notes:
            flags = Gtk.Label(label=" \u00b7 ".join(notes), xalign=0)
            flags.add_css_class("dp-get-note")
            copy.append(flags)
        if update.notes:
            expander = Gtk.Expander(label="Release notes")
            expander.add_css_class("dp-notes")
            text = Gtk.Label(label=update.notes, xalign=0, wrap=True, selectable=True)
            text.add_css_class("dp-up-detail")
            expander.set_child(text)
            copy.append(expander)
        line.append(copy)
        end = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, valign=Gtk.Align.CENTER)
        if firmware.installing == update.device_id:
            state = Gtk.Label(label="Installing")
            state.add_css_class("dp-up-state")
            end.append(state)
        else:
            install = Gtk.Button(label="Install")
            install.add_css_class("luma-button")
            install.add_css_class("small")
            blocked = update.requires_ac and firmware.on_battery
            install.set_sensitive(not firmware.installing and not blocked)
            if blocked:
                install.set_tooltip_text("Connect this computer to power to install.")
            install.connect("clicked", lambda *_: self._confirm_firmware(update))
            end.append(install)
        line.append(end)
        row.append(line)
        if firmware.installing == update.device_id:
            track = Gtk.ProgressBar(fraction=firmware.progress)
            track.add_css_class("dp-track")
            row.append(track)
            keep = Gtk.Label(label="Keep this computer on power and the device connected until it finishes.",
                             xalign=0, wrap=True)
            keep.add_css_class("dp-progress-counts")
            row.append(keep)
        return row

    def _confirm_firmware(self, update) -> None:
        body = ("Keep this computer connected to power and the device connected until it finishes. ")
        if update.needs_reboot:
            body += "The update finishes the next time you restart."
        if self.firmware is not None:
            notes = [r.text for r in self.firmware.offer(update).requirements if not r.blocking]
            if notes:
                body += "\n\n" + "\n".join(notes)
        dialog = Adw.AlertDialog(heading=f"Install {update.description.title[:1].lower()}"
                                         f"{update.description.title[1:]}?"
                                 if update.description.title.startswith(("Update", "Security"))
                                 else f"Install firmware for {update.device}?", body=body.strip())
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("install", "Install")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, name: self.firmware.install(update) if name == "install" else None)
        dialog.present(self)

    def _update_row(self, record: InstalledApp) -> Gtk.Widget:
        app = record.app or (self.catalogue.find(record.app_id) if self.catalogue else None)
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        row.add_css_class("dp-uprow")
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        if app is not None:
            line.append(self._icon_tile(app, 40))
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        name = Gtk.Label(label=app.name if app else record.app_id, xalign=0, wrap=True)
        name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        name.add_css_class("dp-up-name")
        copy.append(name)
        detail = Gtk.Label(label=record.update_summary, xalign=0, wrap=True)
        detail.add_css_class("dp-up-detail")
        copy.append(detail)
        widened = [change for change in record.permission_changes if change.change in ("added", "widened")]
        if widened:
            chips = Adw.WrapBox(child_spacing=4, line_spacing=4)
            for change in widened:
                chip = Gtk.Label(label=change.title)
                chip.add_css_class("dp-chip")
                chip.add_css_class("notable")
                chips.append(chip)
            copy.append(chips)
        line.append(copy)

        end = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        end.set_valign(Gtk.Align.CENTER)
        size = Gtk.Label(label=megabytes(record.update_bytes))
        size.add_css_class("dp-up-size")
        size.set_xalign(1)
        end.append(size)
        job = self.jobs.get(record.app_id)
        if job is not None and job.failed:
            again = Gtk.Button(label="Try again")
            again.add_css_class("luma-button")
            again.add_css_class("small")
            again.connect("clicked", lambda _b, value=record: self._update(value.app_id, approve=False, expected_commit=value.update_commit, expected_installed_commit=value.commit))
            end.append(again)
        elif job is not None:
            cancel = Gtk.Button(label="Cancel")
            cancel.add_css_class("luma-button")
            cancel.add_css_class("small")
            cancel.connect("clicked", lambda _b, uid=record.app_id: self._cancel(uid))
            end.append(cancel)
        elif self._asks_for_more(record) and app is not None:
            button = Gtk.Button(label="Review")
            button.add_css_class("luma-button")
            button.add_css_class("small")
            button.set_tooltip_text("This update asks for more. Look at what changes first.")
            button.connect("clicked", lambda _b, uid=record.app_id: self.go("app", uid))
            end.append(button)
        else:
            button = Gtk.Button(label="Update")
            button.add_css_class("luma-button")
            button.add_css_class("small")
            button.connect("clicked", lambda _b, value=record: self._update(value.app_id, approve=False, expected_commit=value.update_commit, expected_installed_commit=value.commit))
            end.append(button)
        line.append(end)
        row.append(line)

        if job is not None and job.progress is not None:
            track = Gtk.ProgressBar()
            track.add_css_class("dp-track")
            track.set_fraction(job.progress.fraction)
            row.append(track)
            counts = Gtk.Label(
                label=f"{job.progress.stage} · {megabytes(job.progress.transferred_bytes)}"
                      f" of {megabytes(job.progress.total_bytes)}",
                xalign=0)
            counts.add_css_class("dp-progress-counts")
            row.append(counts)
        elif job is not None and job.failed:
            note = Gtk.Label(label=job.failed, xalign=0, wrap=True)
            note.add_css_class("dp-failed")
            row.append(note)
            if job.detail:
                row.append(self._failure_details(job.detail))
        return row

    def _failure_details(self, detail: str) -> Gtk.Widget:
        """The original error, for someone who wants it; never the headline."""
        expander = Gtk.Expander(label="Details")
        expander.add_css_class("dp-failure-details")
        text = Gtk.Label(label=detail, xalign=0, wrap=True, selectable=True)
        text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        text.add_css_class("dp-up-detail")
        expander.set_child(text)
        return expander

    # ── Chrome state ─────────────────────────────────────────────────────

    def _refresh_sidebar_counts(self) -> None:
        if self.view not in self.nav_rows:
            self.sidebar.list.unselect_all()
        for key, row in self.nav_rows.items():
            if key == self.view:
                self.sidebar.list.select_row(row)
            badge = getattr(row, "badge", None)
            if badge is None:
                continue
            if key == "updates":
                pending = sum(1 for record in self.installed.values() if record.has_update)
                # One system provider owns production updates. The legacy
                # reader is used only by the explicit preview fixture.
                if (self.system is None and self.os_state is not None and self.os_state.update_ready and
                        not (self.fixture and self.fixture_system_phase == "done")):
                    pending += 1
                if self.system is not None and self._os_needs_attention():
                    pending += 1
                if self.firmware is not None:
                    pending += len(self.firmware.attention()) + len(self._orphan_firmware_failures())
                badge.set_count(pending)

    def _refresh_status(self) -> None:
        if not self.preview:
            count = sum(bool(a.categories) for a in self.catalogue.apps) if self.catalogue else 0
            self.status_left.set_label("Reading the catalogue…" if self.loading else
                                       "Catalogue unavailable" if self.catalogue_error else
                                       f"{count} catalogue apps")
            self.status_right.set_label(f"{len(self.installed)} installed")
            return
        if self.catalogue is None:
            self.status_left.set_label("Reading the catalogue…" if self.loading else "No catalogue")
        else:
            count = len(self.catalogue.apps)
            self.status_left.set_label(f"Preview · {count} app{'s' if count != 1 else ''} to browse")
        used = sum(record.installed_bytes for record in self.installed.values())
        self.status_right.set_label(
            f"{len(self.installed)} installed · {megabytes(used)} used"
            if self.installed else "Nothing installed")

    def _report(self, heading: str, body: str) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("close", "Close")
        dialog.present(self)

    def _show_about(self) -> None:
        about = Adw.AboutDialog(application_name="Depot", application_icon=ICON_NAME,
                                developer_name="Project Luma")
        about.set_comments(
            "Browse application sources and manage installed desktop applications. "
            "Catalogue installation remains unavailable until its sources are qualified.")
        about.present(self)


class DepotApplication(Adw.Application):
    """Single instance: opening Depot again focuses the window it already has.

    Links arrive three ways and all end in :meth:`DepotWindow.open_uri`: as
    command-line arguments (``luma-depot luma-depot://app/...``), through
    ``org.freedesktop.Application.Open`` when the desktop activates Depot over
    D-Bus, and from a second invocation forwarded to this one.
    """

    def __init__(self) -> None:
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE
                         | Gio.ApplicationFlags.HANDLES_OPEN)
        self.started = time.monotonic()
        self.provisioning = None
        self.app_updates = None
        self.add_main_option("preview", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Use disposable illustrative design fixtures", None)
        self.add_main_option(
            "view", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
            "Open at a view: home, mine, updates, settings, app:APP_ID or collection:ID", "VIEW")
        self.add_main_option(
            "provision", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Install the apps chosen during installation, without opening a window", None)
        self.add_main_option(
            "update-apps", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Update apps in the background, holding any update that asks for more", None)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        install_appkit()
        install_lumaui()
        _install_depot_style()
        show = Gio.SimpleAction.new("show-view", GLib.VariantType.new("s"))
        show.connect("activate", lambda _action, value: self._show_view(value.get_string()))
        self.add_action(show)
        # The desktop entry's Software Update action and Settings' row.
        updates = Gio.SimpleAction.new("updates", None)
        updates.connect("activate", lambda *_: self._show_view("updates"))
        self.add_action(updates)

    def window(self, *, present: bool = True):
        window = self.props.active_window
        if window is None:
            try:
                window = DepotWindow(self)
            except Exception as error:  # noqa: BLE001 - never leave a person with nothing
                import traceback
                traceback.print_exc()
                lifecycle("failed", detail=f"{type(error).__name__}: {error}",
                          seconds=time.monotonic() - self.started)
                window = FailureWindow(self, f"{type(error).__name__}: {error}")
            self._watch(window)
        if present:
            window.present()
        return window

    def _watch(self, window) -> None:
        """Say in the journal when the window reaches the screen, or has not."""
        shown = {"done": False}

        def mapped(*_):
            if not shown["done"]:
                shown["done"] = True
                lifecycle("shown", seconds=time.monotonic() - self.started,
                          window=type(window).__name__)
            return False

        def late():
            if not shown["done"]:
                lifecycle("slow", seconds=time.monotonic() - self.started,
                          detail="the launch has not put a window on screen")
            return GLib.SOURCE_REMOVE

        window.connect("map", mapped)
        GLib.timeout_add_seconds(errors.LIFECYCLE_SLOW_SECONDS, late)

    def _show_view(self, view: str) -> None:
        window = self.window()
        if not isinstance(window, DepotWindow):   # a start that failed has no views
            return
        if view.startswith("app:"):
            window.go("app", view.split(":", 1)[1], remember=False)
        elif view.startswith("collection:"):
            window.go("collection", view.split(":", 1)[1], remember=False)
        elif view in {"home", "mine", "updates", "settings"}:
            window.go(view, remember=False)

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        options = command_line.get_options_dict()
        view = ""
        if options.contains("preview"):
            os.environ["LUMA_DEPOT_PREVIEW"] = "1"
        if options.contains("view"):
            value = options.lookup_value("view", GLib.VariantType.new("s"))
            view = value.get_string() if value is not None else ""
        links = [argument for argument in command_line.get_arguments()[1:]
                 if parse_link(argument) is not None]
        background = False
        if options.contains("provision"):
            from .provisioning import start_provisioning
            start_provisioning(self)
            background = True
        if options.contains("update-apps"):
            from .autoupdate import start_app_updates
            start_app_updates(self)
            background = True
        if background and not view and not links:
            return 0
        if view:
            self._show_view(view)
        else:
            self.activate()
        window = self.props.active_window
        for link in links:
            if isinstance(window, DepotWindow):
                window.open_uri(link)
        return 0

    def do_open(self, files, _count, _hint) -> None:
        window = self.window()
        if not isinstance(window, DepotWindow):
            return
        for file in files:
            window.open_uri(file.get_uri())

    def do_activate(self) -> None:
        self.window()


class FailureWindow(Adw.ApplicationWindow):
    """What a person sees when Depot cannot build its window.

    Nothing on screen is the one outcome Depot may never have: a start that
    fails says so, in words, with a way to try again, and the reason is in the
    journal under MESSAGE_ID=LIFECYCLE_MESSAGE_ID for Luma Vitals.
    """

    def __init__(self, application: Adw.Application, detail: str) -> None:
        super().__init__(application=application, title="Depot", default_width=520,
                         default_height=300)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=32,
                      margin_bottom=32, margin_start=32, margin_end=32,
                      valign=Gtk.Align.CENTER)
        heading = Gtk.Label(label="Depot could not open", xalign=0, wrap=True)
        heading.add_css_class("title-2")
        box.append(heading)
        box.append(Gtk.Label(
            label="Something went wrong while Depot was starting. Trying again often works; "
                  "restarting this computer will too. The details are in Luma Vitals.",
            xalign=0, wrap=True))
        reason = Gtk.Label(label=detail, xalign=0, wrap=True, selectable=True)
        reason.add_css_class("dim-label")
        reason.add_css_class("caption")
        box.append(reason)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_top=8,
                          halign=Gtk.Align.START)
        again = Gtk.Button(label="Try Again")
        again.add_css_class("suggested-action")
        again.connect("clicked", lambda *_: self._again())
        actions.append(again)
        quit_button = Gtk.Button(label="Quit")
        quit_button.connect("clicked", lambda *_: self.close())
        actions.append(quit_button)
        box.append(actions)
        toolbar = Adw.ToolbarView(content=box)
        toolbar.add_top_bar(Adw.HeaderBar())
        self.set_content(toolbar)

    def _again(self) -> None:
        application = self.props.application
        self.close()
        GLib.idle_add(lambda: (application.window(), False)[1])


def _install_depot_style() -> None:
    import os

    path = os.environ.get("LUMA_DEPOT_STYLE_PATH", "/usr/share/luma-depot/depot.css")
    if not pathlib.Path(path).is_file():
        path = str(pathlib.Path(__file__).resolve().parent.parent / "data/depot.css")
    add_style_sheet(path)


def main(argv: list[str] | None = None) -> int:
    import faulthandler
    import sys

    if not os.environ.get('LUMA_DEPOT_FIXTURE') and not os.environ.get('LUMA_DEPOT_PREVIEW'):
        from luma_installer.native_app_roles import launch_if_owned
        launch_if_owned('org.projectluma.Depot')

    # A native crash (its own, or a library's) must leave a traceback in the
    # journal rather than a process that simply disappeared.
    try:
        faulthandler.enable(all_threads=True)
    except (OSError, ValueError, AttributeError):  # pragma: no cover - no usable stderr
        pass
    fixture_directory = None
    if os.environ.get("LUMA_DEPOT_FIXTURE"):
        # Isolate logs and AppWindow geometry before constructing the app or
        # recording its first lifecycle event.
        fixture_directory = tempfile.TemporaryDirectory(prefix="luma-depot-fixture-")
        for name in ("XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
            os.environ[name] = str(pathlib.Path(fixture_directory.name) / name.lower())
    try:
        GLib.set_application_name("Depot")
        lifecycle("asked", detail=" ".join((sys.argv if argv is None else argv)[1:]))
        return DepotApplication().run(sys.argv if argv is None else argv)
    finally:
        if fixture_directory is not None:
            fixture_directory.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
