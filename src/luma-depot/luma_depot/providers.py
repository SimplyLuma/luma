# SPDX-License-Identifier: Apache-2.0

"""What Depot asks for, and what it never asks how.

There is no repository yet. The whole point of this layer is that standing one
up later is a swap of the two implementations below, not a rewrite of the
window: nothing above this file knows what a Flatpak is, where bytes come from,
or whether an answer took a millisecond or a minute.

Three rules hold for every provider, including the stubs:

* **Everything is asynchronous.** A stub that answers instantly teaches the
  interface above it to assume that, and the first real network call then breaks
  every screen. So even the static catalogue answers on a worker thread.
* **Everything is cancellable.** A `Gio.Cancellable` reaches the worker and is
  honoured; a cancelled request delivers nothing.
* **Everything can fail.** Failure is a state the interface has to draw, so it
  has to be reachable now — see `FaultInjector`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import os
import threading
from typing import Callable

from gi.repository import Gio, GLib  # noqa: E402

from luma_installer import depot_icons


# ── What flows across the seam ───────────────────────────────────────────

@dataclass(frozen=True)
class Screenshot:
    url: str
    caption: str = ""
    #: The digest the catalogue published. A downloaded image is drawn only
    #: when its bytes match; a local file:// fixture carries none.
    sha256: str = ""
    width: int = 0
    height: int = 0


@dataclass(frozen=True)
class Permission:
    """One line of "what it can reach", in the vocabulary the installer uses."""

    key: str
    title: str
    detail: str
    notable: bool = False
    #: ADR-028 section 7: standard, sensitive or high.
    level: str = "standard"
    #: "" for an unchanged grant; added, widened, narrowed or removed on update.
    change: str = ""


@dataclass(frozen=True)
class Release:
    version: str
    date: str = ""
    description: str = ""


def monogram(name: str) -> str:
    """The letters somebody would use to pick an application out of a shelf.

    Drawn when an application has no artwork to show: everything published as
    an rpm or a snap carries no cached AppStream icon, and neither does a
    flatpak whose metadata has not been fetched. The alternative was the same
    anonymous cog on every one of them, which is worse than a blank -- a shelf
    of identical tiles cannot be read at all.

    The rule lives with the rest of the icon policy, in
    `luma_installer.depot_icons`, so the window and the tests that describe a
    first boot cannot disagree about what a tile falls back to.
    """

    return depot_icons.monogram(name)


@dataclass(frozen=True)
class App:
    app_id: str
    name: str
    summary: str
    description: str = ""
    developer: str = ""
    categories: tuple[str, ...] = ()
    icon_name: str = "application-x-executable-symbolic"
    tone: str = "blue"
    download_bytes: int = 0
    installed_bytes: int = 0
    licence: str = ""
    age_rating: str = ""
    rating: float = 0.0
    rating_count: int = 0
    screenshots: tuple[Screenshot, ...] = ()
    releases: tuple[Release, ...] = ()
    permissions: tuple[Permission, ...] = ()
    featured: bool = False
    banner: str = ""
    tagline: str = ""
    installable: bool = True
    removable: bool = True
    availability: str = ""
    # Where the publisher offers an application Depot cannot install itself.
    homepage: str = ""
    # ADR-028 schema 4. Empty when the catalogue does not say.
    slug: str = ""              # the catalogue id: web page and reviews
    flatpak_id: str = ""        # the application id links and events use
    tier: str = ""              # luma | verified | listed
    developer_verified: str = ""  # organization | individual | ""
    sign_in: str = ""           # none | luma | third-party | required-third-party
    icon_url: str = ""
    icon_sha256: str = ""
    support_url: str = ""
    privacy_url: str = ""
    source_url: str = ""
    web_url: str = ""
    source_title: str = ""      # "Luma", "Flathub", or the publisher
    installs_total: int = 0
    permission_changes: tuple[Permission, ...] = ()
    #: Permissions were computed from sandbox metadata rather than stated.
    permissions_computed: bool = False
    unlisted: bool = False
    # ADR-031: an app Luma's image ships. ``system_state`` is one of
    # luma_installer.depot_system_apps' states on a Luma computer, "" otherwise.
    system_state: str = ""
    system_package: str = ""
    #: Depot may offer Remove: the catalogue allows it and nothing on this
    #: computer stands in the way (see ``system_note`` when it does not).
    system_removable: bool = False
    system_note: str = ""
    #: Other apps the same package provides; they go and come back together.
    system_siblings: tuple[str, ...] = ()
    #: rpm-ostree is busy with another change.
    system_busy: bool = False
    #: Not on Luma, and there is no Flatpak: "Available on Luma".
    luma_only: bool = False
    #: The official channel Depot installs this from when it is not a
    #: Flatpak: "snap" (the publisher's verified snap) or "repository" (the
    #: publisher's signed RPM repository, or Fedora's). Installs through the
    #: system helper, so it asks for permission and cannot be cancelled.
    channel_kind: str = ""
    #: How the source reads on a listing: "From Flathub · Verified",
    #: "From NordVPN’s snap", "From MEGA’s software repository".
    source_label: str = ""
    #: The source confirmed who publishes it (Luma, Flathub or the Snap Store).
    source_verified: bool = False
    #: Who confirmed it, for the tooltip: "Luma", "Flathub", "the Snap Store".
    source_verifier: str = ""
    #: Only a publisher's website offers it, and why Depot cannot install it.
    publisher_reason: str = ""
    #: The publisher link opens a web app rather than a download page.
    web_app: bool = False
    #: flatpak | snap-strict | snap-classic | "" (outside a sandbox).
    sandbox: str = ""
    #: Said before installing when the app needs a large runtime this
    #: computer does not have yet (the first app from the Luma remote).
    runtime_note: str = ""
    #: Where updates come from, as a Details fact: "Automatic, from the Snap Store".
    update_note: str = ""

    @property
    def version(self) -> str:
        return self.releases[0].version if self.releases else ""

    @property
    def updated(self) -> str:
        return self.releases[0].date if self.releases else ""


@dataclass(frozen=True)
class Category:
    key: str
    name: str
    icon_name: str


@dataclass(frozen=True)
class Collection:
    """A named row of apps on Home, and a page a link can open."""

    key: str
    name: str
    summary: str
    app_ids: tuple[str, ...]


@dataclass(frozen=True)
class Catalogue:
    apps: tuple[App, ...]
    categories: tuple[Category, ...]
    featured: App | None = None
    collections: tuple[Collection, ...] = ()

    def by_category(self, key: str) -> tuple[App, ...]:
        return tuple(app for app in self.apps if key in app.categories and not app.unlisted)

    def find(self, app_id: str) -> App | None:
        return next((app for app in self.apps if app.app_id == app_id), None)

    def find_link(self, identifier: str) -> App | None:
        """An app named by a link: its application id, Flatpak id or catalogue id."""
        for app in self.apps:
            if identifier and identifier in (app.app_id, app.flatpak_id, app.slug,
                                             app.app_id.removeprefix("catalog:")):
                return app
        folded = identifier.casefold()
        return next((app for app in self.apps
                     if app.flatpak_id and app.flatpak_id.casefold() == folded), None)

    def collection(self, key: str) -> Collection | None:
        return next((item for item in self.collections if item.key == key), None)

    def collection_apps(self, collection: Collection) -> tuple[App, ...]:
        found = (self.find(app_id) for app_id in collection.app_ids)
        return tuple(app for app in found if app is not None)


@dataclass(frozen=True)
class InstalledApp:
    app_id: str
    version: str
    installed_bytes: int
    update_version: str = ""
    update_bytes: int = 0
    update_summary: str = ""
    app: App | None = None
    #: What the update would change about what the app can reach.
    permission_changes: tuple[Permission, ...] = ()
    #: Computed from this installation's own sandbox metadata.
    permissions: tuple[Permission, ...] = ()
    #: Depot installed this from a remote it manages, so it can update and
    #: remove it itself; anything else is reviewed in Valet.
    managed: bool = False
    #: The exact installed build and the build the update would install
    #: (Flatpak commits), for history and going back; empty when unknown.
    commit: str = ""
    update_commit: str = ""
    #: Actual installed Luma ref branch; empty for native/other-origin apps.
    update_channel: str = ""

    @property
    def has_update(self) -> bool:
        return bool(self.update_version)


@dataclass(frozen=True)
class Progress:
    """A real fraction and real byte counts, never a spinner."""

    app_id: str
    fraction: float
    transferred_bytes: int
    total_bytes: int
    stage: str = "Downloading"

    @property
    def finished(self) -> bool:
        return self.fraction >= 1.0


class ProviderError(RuntimeError):
    """Anything the interface has to explain to a person.

    `hint` is the sentence shown under the failure. It exists so a provider can
    say something specific — "the repository could not be reached" — instead of
    the window guessing from an exception type.
    """

    def __init__(self, message: str, *, hint: str = "", recoverable: bool = True, detail: str = "") -> None:
        super().__init__(message)
        self.hint = hint
        self.recoverable = recoverable
        #: The original error text, shown only behind "Details" (and logged for Vitals).
        self.detail = detail


@dataclass
class Result:
    """What a worker delivers: a value, or an error. Never both."""

    value: object = None
    error: ProviderError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


# ── Running work off the main thread ─────────────────────────────────────

def run_async(work: Callable[[], object], callback: Callable[[Result], None],
              cancellable: Gio.Cancellable | None = None) -> None:
    """Run `work` on a thread and deliver its result on the main loop.

    Every provider call goes through here, the stubs included, so the window is
    written against real asynchrony from the first day rather than acquiring it
    when a server appears.
    """

    def worker() -> None:
        try:
            value = work()
            result = Result(value=value)
        except ProviderError as error:
            result = Result(error=error)
        except Exception as error:  # pragma: no cover - defensive
            result = Result(error=ProviderError(str(error),
                                                hint="Something went wrong inside Depot."))

        def deliver() -> bool:
            if (cancellable is not None and cancellable.is_cancelled()
                    and not getattr(callback, '_deliver_cancelled', False)):
                return GLib.SOURCE_REMOVE
            callback(result)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(deliver)

    threading.Thread(target=worker, daemon=True).start()


class FaultInjector:
    """Makes every failure state reachable before there is anything to fail.

    Set `LUMA_DEPOT_FAULTS` to a comma-separated list, or toggle one from the
    application menu. Without this the offline, error and install-failed screens
    get written blind and are discovered to be wrong when the real backend
    arrives.
    """

    KNOWN = {
        "offline": "The repository cannot be reached.",
        "catalogue-error": "The catalogue could not be read.",
        "install-fail": "Installing fails part of the way through.",
        "disk-full": "There is not enough room to install.",
        "slow": "Everything takes noticeably longer.",
        "empty": "The catalogue comes back with nothing in it.",
    }

    def __init__(self) -> None:
        configured = os.environ.get("LUMA_DEPOT_FAULTS", "")
        self.active: set[str] = {
            name.strip() for name in configured.split(",")
            if name.strip() in self.KNOWN
        }

    def enabled(self, name: str) -> bool:
        return name in self.active

    def toggle(self, name: str) -> None:
        if name in self.active:
            self.active.discard(name)
        elif name in self.KNOWN:
            self.active.add(name)

    def check_reachable(self) -> None:
        if self.enabled("offline"):
            raise ProviderError(
                "the repository is unreachable",
                hint="Depot could not reach the app repository. Check the network "
                     "and try again.",
            )
        if self.enabled("catalogue-error"):
            raise ProviderError(
                "the catalogue could not be parsed",
                hint="The catalogue Depot downloaded could not be read. This is a "
                     "problem at the repository, not on this computer.",
            )


# ── The two interfaces a real backend must implement ─────────────────────

class CatalogProvider(ABC):
    """Where apps come from.

    A Flatpak-backed implementation answers these from a remote's AppStream
    branch. Nothing else about this interface changes.
    """

    @abstractmethod
    def load_catalogue(self, callback: Callable[[Result], None],
                       cancellable: Gio.Cancellable | None = None) -> None:
        """Deliver a `Catalogue`."""

    @abstractmethod
    def search(self, query: str, callback: Callable[[Result], None],
               cancellable: Gio.Cancellable | None = None) -> None:
        """Deliver a tuple of `App` matching `query`."""

    @abstractmethod
    def app(self, app_id: str, callback: Callable[[Result], None],
            cancellable: Gio.Cancellable | None = None) -> None:
        """Deliver one `App`, with its screenshots and releases."""


class InstallationProvider(ABC):
    """What is on this machine, and what changes it.

    A libflatpak-backed implementation answers `installed` from the
    installation, and drives `install`/`remove`/`update` through a transaction,
    reporting real progress. Nothing else about this interface changes.
    """

    @abstractmethod
    def installed(self, callback: Callable[[Result], None],
                  cancellable: Gio.Cancellable | None = None) -> None:
        """Deliver a tuple of `InstalledApp`."""

    @abstractmethod
    def free_bytes(self) -> int:
        """Room left where apps are installed. Checked before every install."""

    @abstractmethod
    def install(self, app: App, on_progress: Callable[[Progress], None],
                callback: Callable[[Result], None],
                cancellable: Gio.Cancellable | None = None) -> None:
        """Install `app`, reporting progress, honouring `cancellable`."""

    @abstractmethod
    def remove(self, app_id: str, *, keep_data: bool,
               callback: Callable[[Result], None],
               cancellable: Gio.Cancellable | None = None) -> None:
        """Remove `app_id`. `keep_data` is the user's answer, never assumed."""

    @abstractmethod
    def update(self, app_id: str, on_progress: Callable[[Progress], None],
               callback: Callable[[Result], None],
               cancellable: Gio.Cancellable | None = None, *, expected_commit: str = "",
               expected_installed_commit: str = "") -> None:
        """Update the exact reviewed build of one app. One app — never "everything", which is the OS's word."""
