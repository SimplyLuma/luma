# SPDX-License-Identifier: Apache-2.0
"""The update agent's behavior, independent of D-Bus and of the real system.

``Engine`` is given an rpm-ostree backend, system probes (network, power), an
HTTP client and a clock. The daemon passes the real ones; the test suite passes
fakes, so the state machine, policy and reporting are exercised without a VM.

Operations are synchronous and serialized: one of check, download, apply,
rollback, channel change or enrollment runs at a time. The daemon runs them on
a worker thread and forwards ``on_change`` to the main loop.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import platform
import re
import threading
import time

from . import graph as graphmod
from . import kargs as kargsmod
from . import minisign, names, preview, redact, reporting, rpmver, versions
from .config import CHANNELS, PREVIEW_CHANNELS, Paths, Settings
from .origin import ChannelOrigin, parse_origin, refspec_for
from .state import StateStore, atomic_write, barrier_retry_due, load_wariness
from .status import (AVAILABLE, BARRIER_BLOCKED, CHECKING, DOWNLOADING, ERROR, IDLE, RESTART_REQUIRED, STAGED,
                     Status)

__all__ = ("Engine", "UpdateError", "Unmanaged", "Busy", "NothingToDo", "NotOurs", "automatic_download_allowed",
           "classify_error")

log = logging.getLogger("luma-update")
redact.install(log)

DECISION_MAX_AGE = 3600
PERSON_WAIT_SECONDS = 20 * 60
GREENBOOT_UNIT = "/usr/lib/systemd/system/greenboot-healthcheck.service"


class UpdateError(Exception):
    error_class = "unknown"

    def __init__(self, message: str, error_class: str | None = None) -> None:
        super().__init__(message)
        if error_class:
            self.error_class = error_class


class Unmanaged(UpdateError):
    error_class = "unmanaged"


class Busy(UpdateError):
    error_class = "busy"


class NothingToDo(UpdateError):
    error_class = ""


class NotOurs(UpdateError):
    """What waits for a restart was not prepared by this agent."""

    error_class = "not-ours"


FRIENDLY = {
    "network": "Luma couldn't reach its update server. It will try again later.",
    "added-package-newer": ("This computer has a newer copy of a package this update includes, so Luma kept yours "
                            "and is waiting. It updates once Luma ships that version, or after you remove your copy."),
    "signature": "The update information wasn't signed by Luma, so it was ignored.",
    "graph": "The update information from Luma couldn't be read.",
    "stale-graph": "The update information from Luma is out of date. Check this computer's date and time.",
    "busy": "Another software change is in progress. Luma will try again when it finishes.",
    "transaction": "The update couldn't be prepared.",
    "disk-space": "There isn't enough free space to prepare the update.",
    "added-packages-unreachable": "Software you added with dnf couldn't be refreshed because its package "
                                  "servers can't be reached. Luma will try again later.",
    "unmanaged": "This computer doesn't follow a Luma update channel.",
    "preview": "Early updates couldn't be set up.",
    "not-entitled": "This Luma account can't get early updates on that channel.",
    "sign-in-required": "Luma Connect on this computer is signed out. Sign in again to get early updates.",
    "access-denied": "Luma's update server refused this computer. If it got early updates, that access was "
                     "turned off: leave early updates, or turn them on again.",
    "not-authorized": "You aren't allowed to make that change.",
    "finalize": "The update couldn't be finished while restarting.",
}


def classify_error(error: BaseException, *, credential_installed: bool = False) -> str:
    error_class = getattr(error, "error_class", None)
    text = str(error)
    # The rpm-ostree client wraps every failed transaction as "transaction" with only
    # libostree's message (the Finished signal carries no GLib domain or code), so the
    # message decides here too. A more specific class (busy, network, ...) is kept.
    wrapped = not error_class or error_class == "transaction"
    if wrapped and "No valid mirrors were found in mirrorlist" in text:
        # libostree reports every failure of the mirror list's only URL this way, hiding the
        # HTTP status: with a preview credential that is almost always a refused credential;
        # without one, the public repository could not be reached.
        return "access-denied" if credential_installed else "network"
    # libostree's min-free-space check: "min-free-space-percent '3%' would be exceeded[, at least
    # 1.2 GB requested]" or "min-free-space-size 500MB would be exceeded" (ostree-repo-commit.c, 2026.4).
    if "No space left" in text or "ENOSPC" in text or ("min-free-space" in text and "would be exceeded" in text):
        return "disk-space"
    if isinstance(error, minisign.SignatureError):
        return "signature"
    if isinstance(error, versions.InvalidVersion):
        return "unmanaged"
    if wrapped and re.search(r"\b(?:HTTP 40[13]|40[13] (?:Forbidden|Unauthorized))\b", text):
        # The download server refused the pull: a revoked or unknown preview credential.
        return "access-denied"
    if error_class:
        return error_class
    return "unknown"


def automatic_download_allowed(settings: Settings, network, power, enabled: bool | None = None) -> tuple[bool, str]:
    """Whether a download may start (or continue) without a person asking.

    ``enabled`` is the person's own choice (Depot's "Download updates
    automatically"); without one, the package or administrator default in
    ``update.conf`` decides.

    Unknown is never permissive: a connection NetworkManager does not describe
    counts as metered, and power UPower does not describe counts as a battery
    of unknown charge."""
    if not (settings.automatic_download if enabled is None else enabled):
        return False, "automatic downloads are off"
    if not network.available:
        return False, "metered (NetworkManager did not answer)"
    if network.metered is not False:
        return False, "metered"
    if not power.available:
        return False, "battery (UPower did not answer)"
    if power.on_battery:
        if power.percentage is None or power.percentage <= settings.minimum_battery_percent:
            return False, "battery"
    return True, ""


def connection_counts_as_metered(network) -> bool:
    return not network.available or network.metered is not False


def _older_timestamp_refusal(error: BaseException, commit: str) -> bool:
    """libostree's timestamp check refusing ``commit`` (ostree-core.c, 2026.4):
    "Upgrade target revision '<commit>' with timestamp '…' is chronologically
    older than current revision '…' …; use --allow-downgrade to permit"."""
    text = str(error)
    return "chronologically older" in text and f"'{commit}'" in text


#: rpm-ostree's words when it could not refresh the package repositories that
#: packages added with ``sudo dnf install`` come from (libdnf, librepo, curl).
_PACKAGE_REPOSITORY_FAILURES = ("Cannot download repomd.xml", "Failed to download metadata",
                                "Curl error", "Cannot prepare internal mirrorlist", "Status code: 404 for",
                                "Updating rpm-md repo", "cannot update repo", "Librepo error",
                                "Could not resolve host", "All mirrors were tried")


#: libsolv's words when a package added to this computer and a package the new
#: base ships have the same name (rpm-ostree depsolves added packages against the
#: base's rpmdb, which libsolv calls @System):
#: "cannot install both viola-1-2.x86_64 from @System and viola-1-1.x86_64 from @commandline".
_BOTH = re.compile(r"cannot install both (\S+) from (@?[\w.:-]+) and (\S+) from (@?[\w.:-]+)")
_LOCAL_KEY = re.compile(r"^[0-9a-f]{64}:")


def _nevra_name(nevra: str) -> str:
    parts = nevra.rsplit("-", 2)
    return parts[0] if len(parts) == 3 else ""


def _request_name(request: str) -> str:
    """The package name a request names: a local package's NEVRA, or a repository
    request (a bare name; one with a version is left to the NEVRA parse)."""
    request = _LOCAL_KEY.sub("", request)
    return _nevra_name(request) if re.search(r"-\d[^-]*-[^-]+$", request) else request


def added_packages_the_base_provides(error: BaseException, deployments) -> list[str]:
    """The requests of packages added to this computer (``rpm-ostree install``,
    from a repository or a local file) that ``error`` says the new base now
    ships under the same name.

    A package a release adds to its base conflicts with the copy a person (or an
    older first-boot app install) layered from a file, and rpm-ostree then
    refuses the whole update. The base's copy is the one Luma maintains, so the
    added request is what goes. Only a name libsolv reports on the @System side
    of a "cannot install both" pair and that is also requested here is returned:
    anything else is not this conflict and is left alone."""
    provided = set()
    for first, first_repo, second, second_repo in _BOTH.findall(str(error)):
        if first_repo == "@System" and second_repo != "@System":
            provided.add(_nevra_name(first))
        elif second_repo == "@System" and first_repo != "@System":
            provided.add(_nevra_name(second))
    provided.discard("")
    if not provided:
        return []
    found = []
    for request in _requests(deployments):
        if _request_name(request) in provided and request not in found:
            found.append(request)
    return found


#: rpm-ostree's words when a package added from a file is byte-for-byte the
#: version the new base ships (rpmostree-core: "Package '%s' is already in the
#: base"). Nothing depsolves; the whole update is refused until the added copy goes.
_ALREADY_IN_BASE = re.compile(r"Package '([^']+)' is already in the base")


def _requests(deployments):
    for deployment in deployments:
        if deployment is None:
            continue
        for request in (tuple(getattr(deployment, "requested_local_packages", ()) or ())
                        + tuple(getattr(deployment, "requested_packages", ()) or ())):
            yield _LOCAL_KEY.sub("", str(request))


def added_packages_in_conflict(error: BaseException, deployments) -> tuple[list[str], list[tuple[str, str]]]:
    """What to do about packages this computer added that ``error`` says the new
    base now ships: (drop, keep).

    ``drop`` are requests whose added copy is the same as, or older than, the
    base's: the base's copy is the one Luma maintains, so the added request goes.
    ``keep`` are (request, base package name) pairs whose added copy is NEWER
    than the base's: removing it would downgrade what the person runs. rpm-ostree
    cannot layer a package whose name the base carries, not even after
    ``override remove`` ("Base packages not marked to be removed", checked on
    rpm-ostree 2026.2), and turning a layer into an override needs the RPM file,
    which the updater does not have; so the update waits and says why. A
    repository request (a bare name) has no version here and is always dropped,
    as before."""
    base: dict[str, str] = {}
    for first, first_repo, second, second_repo in _BOTH.findall(str(error)):
        if first_repo == "@System" and second_repo != "@System":
            base.setdefault(_nevra_name(first), first)
        elif second_repo == "@System" and first_repo != "@System":
            base.setdefault(_nevra_name(second), second)
    for nevra in _ALREADY_IN_BASE.findall(str(error)):
        base.setdefault(_nevra_name(nevra), nevra)
    base.pop("", None)
    drop: list[str] = []
    keep: list[tuple[str, str]] = []
    if not base:
        return drop, keep
    for request in _requests(deployments):
        name = _request_name(request)
        if name not in base or request in drop or any(request == k for k, _ in keep):
            continue
        order = rpmver.compare_nevra(request, base[name]) if request != name else None
        if order is not None and order > 0:
            keep.append((request, name))
        else:
            drop.append(request)
    return drop, keep


def package_repository_unreachable(error: BaseException) -> bool:
    """rpm-ostree failed because the repositories of added packages (Fedora's
    mirrors, a COPR) could not be reached, not because of Luma's own update."""
    text = str(error)
    return any(marker in text for marker in _PACKAGE_REPOSITORY_FAILURES)


class CancelToken:
    """Cancellation for a download when the backend offers no cancellable of its own."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


#: While an automatic download runs, how often progress re-checks the download policy.
POLICY_RECHECK_SECONDS = 15.0


@dataclass(frozen=True)
class SystemView:
    deployments: list
    booted: object
    staged: object | None
    origin: ChannelOrigin | None
    staged_origin: ChannelOrigin | None
    rollback: object | None
    default_is_rollback: bool


def _boot_id() -> str:
    """This boot's identity: the kernel boot id, or the boot moment if that is hidden."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        pass
    try:
        # Rounded to ten minutes so clock slew does not change it within a boot.
        booted_at = time.time() - time.clock_gettime(time.CLOCK_BOOTTIME)
        return f"boot-time:{int(booted_at // 600)}"
    except (AttributeError, OSError):
        return ""


def _kept_packages(data: dict) -> list[str]:
    record = data.get("kept_packages")
    packages = record.get("packages") if isinstance(record, dict) else None
    return [str(item) for item in packages if isinstance(item, str)] if isinstance(packages, list) else []


def _removed_packages(data: dict) -> list[str]:
    record = data.get("removed_packages")
    packages = record.get("packages") if isinstance(record, dict) else None
    return [str(item) for item in packages if isinstance(item, str)] if isinstance(packages, list) else []


def _installed_credentials(paths: Paths) -> tuple[str, ...]:
    credential = preview.read_credential(paths)
    return (credential["credential"],) if credential else ()


#: Why a computer is not following a Luma channel, and whether that can be
#: changed from here. A person is never told only that something is
#: unavailable; these are the exact answers Depot turns into plain words.
UNMANAGED_OTHER_ORIGIN = "other-origin"        # everything is in place; it just came from elsewhere
UNMANAGED_NO_REMOTE = "no-remote"              # the Luma remote is not configured here
UNMANAGED_NO_KEY = "no-key"                    # no update-graph signing key is installed
UNMANAGED_NO_IMAGE_SYSTEM = "no-image-system"  # not an rpm-ostree system at all
UNMANAGED_BUSY = "busy"                        # something else is staged or a transaction runs


def adoption_state(paths: Paths, settings: Settings, view) -> tuple[bool, str]:
    """Whether an unmanaged computer could start following a Luma channel.

    Adoption rebases this computer onto a Luma channel, so it is offered only
    where the trust is already installed: the image's own ``luma`` OSTree
    remote (which carries the release-signing configuration) and at least one
    update-graph key. A computer without them is not refused silently; the
    reason it cannot is published so Depot can say exactly what is missing.
    """
    if view is None or not getattr(view, "deployments", None):
        return False, UNMANAGED_NO_IMAGE_SYSTEM
    try:
        url = preview.remote_url(paths, settings)
    except Exception:
        url = None
    if not url:
        return False, UNMANAGED_NO_REMOTE
    if not minisign.load_keys(paths.graph_key_dirs):
        return False, UNMANAGED_NO_KEY
    if view.staged is not None or view.default_is_rollback:
        return False, UNMANAGED_BUSY
    return True, UNMANAGED_OTHER_ORIGIN


def repository_url(paths: Paths, settings: Settings) -> str:
    """Where this computer actually pulls the operating system from, as a
    person may be shown it.

    Read from the image's own OSTree remote, never assumed, so moving the
    downloads to another host changes what Depot says without changing Depot.
    A preview repository URL carries the device's credential, so the answer is
    redacted before it is published in the world-readable status file.
    """
    try:
        url = preview.remote_url(paths, settings)
    except Exception:  # no remote, unreadable remotes.d: say nothing rather than guess
        return ""
    if not url:
        return ""
    if url.startswith("mirrorlist=file://"):
        try:
            lines = Path(url[len("mirrorlist=file://"):]).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return ""
        url = next((line.strip() for line in lines if line.strip() and not line.strip().startswith("#")), "")
    return redact.redact(url)[:2048]


class Engine:
    def __init__(self, paths: Paths, settings: Settings, backend, probes, http, *, clock=time.time,
                 arch: str | None = None, on_change=None, boot_id=_boot_id,
                 greenboot_installed=None) -> None:
        self.paths = paths
        self.settings = settings
        self.backend = backend
        self.probes = probes
        self.http = http
        self.clock = clock
        self.arch = arch or platform.machine()
        self.on_change = on_change
        self.boot_id = boot_id
        self._greenboot_installed = greenboot_installed
        redact.install(log, lambda: _installed_credentials(paths))
        self.store = StateStore(paths.state_file)
        self._status = Status()
        self._status_lock = threading.Lock()
        self._status_serial = 0
        self._publish_lock = threading.Lock()
        self._published_serial = 0
        self._operation = threading.Lock()
        self._download_lock = threading.Lock()
        self._download_cancel = None          # the running download's cancellable
        self._download_automatic = False
        self._cancel_reason = ""
        self._policy_checked_at = 0.0
        self.policy_recheck_seconds = POLICY_RECHECK_SECONDS
        self._decision: graphmod.Decision | None = None
        self._decision_at = 0.0
        self._decision_channel = ""

    # ── Published status ─────────────────────────────────────────────────

    def status(self) -> Status:
        with self._status_lock:
            return self._status.copy()

    def _set(self, **changes) -> None:
        with self._status_lock:
            changed = [name for name, value in changes.items() if getattr(self._status, name) != value]
            for name in changed:
                setattr(self._status, name, changes[name])
            snapshot = self._status.copy()
            if changed:
                self._status_serial += 1
            serial = self._status_serial
        if changed:
            self._write_status_file(snapshot, serial)
            if self.on_change:
                self.on_change(changed, snapshot)

    def _write_status_file(self, status: Status, serial: int) -> None:
        # Two threads may publish at once; never let an older snapshot land last.
        with self._publish_lock:
            if serial < self._published_serial:
                return
            try:
                atomic_write(self.paths.status_file, (status.to_json() + "\n").encode("utf-8"), 0o644)
                self._published_serial = serial
            except OSError as error:
                log.warning("could not write %s: %s", self.paths.status_file, error)

    def _fail(self, error: BaseException, keep_state: str | None = None) -> None:
        error_class = classify_error(error, credential_installed=preview.read_credential(self.paths) is not None)
        message = FRIENDLY.get(error_class, "Something went wrong while updating.")
        log.error("%s (%s): %s", message, error_class, error)
        booted = self._booted_commit()
        with self.store.locked():
            if booted:
                self.store.data["attempt_booted"] = booted
            self.store.data["last_error"] = message
            self.store.data["last_error_class"] = error_class
            self.store.data["last_check_reason"] = error_class
            if error_class in ("signature", "graph"):
                # The graph did not verify this time: stop claiming it did.
                self.store.data["graph_signature"] = None
        self._set(state=keep_state or ERROR, last_error=message, last_error_class=error_class, progress=0.0)
        try:
            # The published state follows the system: an update that is still
            # staged or available stays so, with the error alongside it.
            self.refresh(keep_busy=False)
        except Exception:  # the error itself may be that rpm-ostree is unreachable
            pass

    # ── Reading the system ───────────────────────────────────────────────

    def _view(self) -> SystemView:
        deployments = self.backend.deployments()
        booted = next((d for d in deployments if d.booted), None)
        if booted is None:
            raise Unmanaged("rpm-ostree reports no booted deployment")
        staged = next((d for d in deployments if d.staged), None)
        rollback = next((d for d in deployments if not d.booted and not d.staged), None)
        default_is_rollback = bool(deployments) and not deployments[0].booted and not deployments[0].staged
        return SystemView(deployments, booted, staged, parse_origin(booted.origin, self.settings),
                          parse_origin(staged.origin, self.settings) if staged else None,
                          rollback, default_is_rollback)

    def _channel(self, view: SystemView, data: dict | None = None) -> str:
        requested = (self.store.data if data is None else data).get("requested_channel") or ""
        if requested in CHANNELS:
            return requested
        return view.origin.channel if view.origin else ""

    def _ours_staged(self, view: SystemView, data: dict | None = None) -> bool:
        pending = (self.store.data if data is None else data).get("pending")
        return bool(view.staged and isinstance(pending, dict)
                    and view.staged.base_commit == pending.get("to_commit"))

    @staticmethod
    def _live_applied(view: SystemView) -> bool:
        """The staged deployment only adds or removes packages on the booted
        release and is already running (``rpm-ostree apply-live``, as Luma's
        ``dnf install`` does). A restart would change nothing a person can
        see, so it is not a change waiting for a restart."""
        staged, booted = view.staged, view.booted
        return bool(staged is not None and booted is not None
                    and getattr(booted, "live_replaced", "") == staged.checksum
                    and staged.base_commit == booted.base_commit)

    def _ours_finalized(self, view: SystemView, data: dict | None = None) -> bool:
        """Our update finalized and is the default for the next boot, but the
        previous deployment is running: the person picked it in the boot menu."""
        pending = (self.store.data if data is None else data).get("pending")
        first = view.deployments[0] if view.deployments else None
        return bool(view.staged is None and first is not None and not first.booted and isinstance(pending, dict)
                    and pending.get("to_commit") and first.base_commit == pending.get("to_commit")
                    and view.booted.base_commit == pending.get("from_commit"))

    def _ours_waiting(self, view: SystemView, data: dict | None = None) -> str:
        """The commit of our update waiting for a restart (staged or finalized), or ''."""
        if self._ours_staged(view, data):
            return view.staged.base_commit
        if self._ours_finalized(view, data):
            return view.deployments[0].base_commit
        return ""

    def _adopting(self, view: SystemView, data: dict | None = None) -> str:
        """The channel this computer is being moved onto, while it follows none."""
        data = self.store.snapshot() if data is None else data
        if view.origin is not None or not data.get("adopting"):
            return ""
        channel = str(data.get("requested_channel", "") or "")
        return channel if channel in CHANNELS else ""

    def _arch(self, view: SystemView) -> str:
        return view.origin.arch if view.origin is not None else self.arch

    def _booted_version(self, view: SystemView):
        """The booted version for comparison. An unmanaged system may not carry a
        Luma version at all; it is then older than anything the graph offers."""
        try:
            return versions.parse(view.booted.version)
        except Exception:
            return versions.parse("0.0.0")

    def _deployment_name(self, deployment, version: str, graph_name, booted: dict) -> str:
        """A deployment's name: the graph's, else its own os-release on disk, else derived."""
        if not version:
            return ""
        path = names.deployment_os_release(self.paths.root, deployment) if deployment is not None else None
        info = names.read_os_release(path) if path is not None and not names.clean(graph_name) else {}
        return names.display_name(version, graph_name=graph_name, os_release=info, booted=booted)

    def _names(self, view: SystemView, data: dict, *, staged_version: str, rolled_back_version: str) -> dict:
        """What each published version is called (luma_update/names.py). Presentation only."""
        booted = names.read_os_release(self.paths.os_release)
        available = data.get("available") if isinstance(data.get("available"), dict) else {}
        pending = data.get("pending") if isinstance(data.get("pending"), dict) else {}
        notice = data.get("rollback_notice") if isinstance(data.get("rollback_notice"), dict) else {}
        ignored = data.get("ignored") if isinstance(data.get("ignored"), dict) else {}
        booted_graph = available.get("booted_display_name")
        remembered = data.get("booted_display_name")
        if not booted_graph and isinstance(remembered, dict) and remembered.get("commit") == view.booted.base_commit:
            booted_graph = remembered.get("name")
        if not booted_graph and pending.get("to_commit") and pending.get("to_commit") == view.booted.base_commit:
            booted_graph = pending.get("to_display_name")
        staged_graph = None
        if pending.get("to_version") and pending.get("to_version") == staged_version:
            staged_graph = pending.get("to_display_name")
        staged_deployment = view.staged
        if staged_deployment is None and self._ours_finalized(view, data):
            staged_deployment = view.deployments[0]
        ignored_version = str(ignored.get("version", "") or "")
        ignored_graph = ignored.get("display_name")
        if not names.clean(ignored_graph):
            if available.get("version") == ignored_version:
                ignored_graph = available.get("display_name")
            elif pending.get("to_version") == ignored_version:
                ignored_graph = pending.get("to_display_name")
        return dict(
            booted_name=names.booted_name(booted, view.booted.version, graph_name=booted_graph),
            staged_name=self._deployment_name(staged_deployment, staged_version, staged_graph, booted),
            available_name=names.display_name(str(available.get("version", "") or ""),
                                              graph_name=available.get("display_name"), booted=booted),
            waiting_name=names.display_name(str(available.get("waiting_version", "") or ""),
                                            graph_name=available.get("waiting_display_name"), booted=booted),
            rolled_back_name=names.display_name(rolled_back_version, graph_name=notice.get("from_display_name"),
                                                booted=booted),
            ignored_name=names.display_name(ignored_version, graph_name=ignored_graph, booted=booted),
        )

    def _failed_name(self, view: SystemView, pending: dict) -> str:
        """The name of an update that did not start, for the rollback notice."""
        graph_name = names.clean(pending.get("to_display_name"))
        if graph_name:
            return graph_name
        try:
            deployment = next((d for d in view.deployments if d.base_commit == pending.get("to_commit")), None)
            path = names.deployment_os_release(self.paths.root, deployment) if deployment is not None else None
            return names.pretty_name(names.read_os_release(path)) if path is not None else ""
        except Exception:
            return ""

    def refresh(self, keep_busy: bool = True) -> Status:
        """Re-read rpm-ostree and local state into the published status.

        ``keep_busy`` leaves a checking or downloading state alone, for
        refreshes that happen while an operation runs. Reads a private copy of
        the state, so it is safe beside an operation on another thread."""
        try:
            view = self._view()
        except Exception as error:  # rpm-ostree unavailable: publish what is known
            error_class = classify_error(error)
            log.error("could not read deployments: %s", error)
            self._set(state=ERROR, last_error=FRIENDLY.get(error_class, "Luma couldn't read the installed system."),
                      last_error_class=error_class)
            return self.status()
        data = self.store.snapshot()
        pending_now = data.get("pending") if isinstance(data.get("pending"), dict) else None
        if pending_now and pending_now.get("staged_boot_id") and pending_now.get("staged_boot_id") == self.boot_id() \
                and not (view.staged and view.staged.base_commit == pending_now.get("to_commit")):
            # Removed in this boot by someone else (rpm-ostree cleanup): nothing is pending.
            with self.store.locked():
                if self.store.data.get("pending") == pending_now:
                    self.store.data["pending"] = None
            data = self.store.snapshot()
        credential = preview.read_credential(self.paths)
        # Every channel may be chosen: Beta and Nightly are open to anyone who opts in
        # (ADR-030 section 4); choosing one enrolls first when nothing is installed.
        channels = ["stable", "beta", "nightly"]
        notice = data.get("rollback_notice") if isinstance(data.get("rollback_notice"), dict) else {}
        available = data.get("available") if isinstance(data.get("available"), dict) else {}
        signature = data.get("graph_signature") if isinstance(data.get("graph_signature"), dict) else {}
        waiting_commit = self._ours_waiting(view, data)
        staged_ours = bool(waiting_commit)
        pending = data.get("pending") if isinstance(data.get("pending"), dict) else {}
        current = self.status().state
        if keep_busy and current in (CHECKING, DOWNLOADING):
            state = current
        elif staged_ours:
            state = STAGED
        elif view.default_is_rollback or (view.staged and not staged_ours and not self._live_applied(view)):
            state = RESTART_REQUIRED
        elif available.get("version"):
            state = AVAILABLE
        elif available.get("barrier_blocked"):
            state = BARRIER_BLOCKED
        elif data.get("last_error"):
            state = ERROR
        else:
            state = IDLE
        network = self.probes.network()
        if view.origin is not None:
            adoptable, unmanaged_reason = False, ""
            if data.get("adopting"):
                # It arrived: this computer follows a Luma channel now.
                with self.store.locked():
                    self.store.data["adopting"] = False
        else:
            adoptable, unmanaged_reason = adoption_state(self.paths, self.settings, view)
        staged_version = pending.get("to_version", "") if staged_ours else (view.staged.version if view.staged else "")
        try:
            display = self._names(view, data, staged_version=staged_version,
                                  rolled_back_version=notice.get("from_version", ""))
        except Exception as error:  # a name is never worth failing a refresh over
            log.warning("could not name the published versions: %s", error)
            display = {}
        self._set(
            state=state,
            managed=view.origin is not None,
            channel=self._channel(view, data),
            booted_version=view.booted.version,
            booted_commit=view.booted.base_commit,
            staged_version=staged_version,
            staged_commit=waiting_commit or (view.staged.base_commit if view.staged else ""),
            available_version=available.get("version", ""),
            available_commit=available.get("commit", ""),
            available_summary=available.get("summary", "") or (pending.get("summary", "") if staged_ours else ""),
            notes_url=available.get("notes_url", "") or (pending.get("notes_url", "") if staged_ours else ""),
            importance=available.get("importance") or (pending.get("importance", "normal") if staged_ours else "normal"),
            download_bytes=int(available.get("download_bytes", 0) or 0),
            last_check=int(data.get("last_check", 0) or 0),
            last_error=data.get("last_error", ""),
            last_error_class=data.get("last_error_class", ""),
            metered=connection_counts_as_metered(network),
            rollback_available=view.rollback is not None,
            preview_enrolled=credential is not None,
            preview_source=preview.record_source(credential),
            available_channels=channels,
            rolled_back_version=notice.get("from_version", ""),
            rolled_back_at=int(notice.get("at", 0) or 0),
            booted_deadend_reason=available.get("booted_deadend_reason", ""),
            waiting_version=available.get("waiting_version", ""),
            automatic_download=self.automatic_download_enabled(data),
            ignored_version=self.ignored_version(data),
            repository_url=repository_url(self.paths, self.settings),
            graph_url=self.settings.graph_url.format(channel=self._channel(view, data) or "stable"),
            signature_verified=bool(signature.get("key_id")),
            signing_key_id=str(signature.get("key_id", "") or ""),
            last_check_reason=str(data.get("last_check_reason", "") or ""),
            last_check_attempt=int(data.get("last_attempt", 0) or 0),
            adoptable=adoptable,
            unmanaged_reason=unmanaged_reason,
            removed_packages=_removed_packages(data),
            kept_packages=_kept_packages(data),
            **display,
        )
        return self.status()

    # ── The person's own choices ─────────────────────────────────────────

    def automatic_download_enabled(self, data: dict | None = None) -> bool:
        """Download without asking? The person's choice, else update.conf's default."""
        data = self.store.snapshot() if data is None else data
        chosen = data.get("automatic_download")
        return self.settings.automatic_download if chosen is None else bool(chosen)

    def set_automatic_download(self, enabled: bool) -> None:
        """Turn automatic downloading on or off. Off also stops one already running:
        the person just said not to spend their connection on it."""
        with self.store.locked():
            self.store.data["automatic_download"] = bool(enabled)
        if not enabled and self.download_running() and not self._download_user_initiated():
            self.cancel_download("automatic downloads were turned off")
        log.info("automatic download %s", "on" if enabled else "off")
        self.refresh()

    def _download_user_initiated(self) -> bool:
        with self._download_lock:
            return bool(self._download_cancel) and not self._download_automatic

    def ignored_version(self, data: dict | None = None) -> str:
        data = self.store.snapshot() if data is None else data
        ignored = data.get("ignored") if isinstance(data.get("ignored"), dict) else {}
        return str(ignored.get("version", "") or "")

    def is_ignored(self, version: str, commit: str = "") -> bool:
        data = self.store.snapshot()
        ignored = data.get("ignored") if isinstance(data.get("ignored"), dict) else {}
        if not ignored:
            return False
        if commit and ignored.get("commit"):
            return ignored["commit"] == commit
        return bool(version) and ignored.get("version") == version

    def ignore_version(self, version: str) -> None:
        """Stop reminding about one version. It is still shown in Depot, with a
        way back; it is simply never downloaded on its own and never notified
        about again. Nothing about it is sent anywhere."""
        version = (version or "").strip()
        if not version:
            raise UpdateError("no version to ignore", "invalid-argument")
        data = self.store.snapshot()
        available = data.get("available") if isinstance(data.get("available"), dict) else {}
        pending = data.get("pending") if isinstance(data.get("pending"), dict) else {}
        commit = display_name = ""
        if available.get("version") == version:
            commit = str(available.get("commit", "") or "")
            display_name = names.clean(available.get("display_name"))
        elif pending.get("to_version") == version:
            commit = str(pending.get("to_commit", "") or "")
            display_name = names.clean(pending.get("to_display_name"))
        with self.store.locked():
            self.store.data["ignored"] = {"version": version, "commit": commit, "at": int(self.clock()),
                                          "display_name": display_name}
        log.info("ignoring %s until it is asked for", version)
        self.refresh()

    def clear_ignored_version(self) -> None:
        with self.store.locked():
            self.store.data["ignored"] = None
        self.refresh()

    # ── Checking ─────────────────────────────────────────────────────────

    def _begin(self, wait: float = 0.0) -> None:
        """Serialize operations. A person's own change (channel, rollback,
        enrollment) waits for a running check or download to finish."""
        acquired = self._operation.acquire(timeout=wait) if wait > 0 else self._operation.acquire(blocking=False)
        if not acquired:
            raise Busy("an update operation is already running")

    def _end(self) -> None:
        self._operation.release()

    def fetch_graph(self, channel: str, arch: str) -> graphmod.Graph:
        url = self.settings.graph_url.format(channel=channel)
        data = self.http.get(url, graphmod.MAX_GRAPH_BYTES)
        signature = self.http.get(url + ".minisig", minisign.MAX_SIGNATURE_BYTES)
        keys = minisign.load_keys(self.paths.graph_key_dirs)
        if not keys:
            raise minisign.SignatureError("no update-graph signing key is installed")
        verified = minisign.verify(data, signature, keys)
        key_id = next((key.key_id_hex for key in keys if key.key_id == verified.key_id), "")
        graph = graphmod.parse_graph(data)
        graphmod.check_freshness(graph, channel=channel, arch=arch, now=self.clock(),
                                 last_generated_at=self.store.graph_seen(channel))
        with self.store.locked():
            self.store.record_graph(channel, graph.generated_at)
            # What Depot shows as "signed and verified": which key checked out, and when.
            self.store.data["graph_signature"] = {"key_id": key_id, "at": int(self.clock()), "channel": channel}
        return graph

    def _do_not_retry(self, graph: graphmod.Graph, booted_version, now: float) -> frozenset[str]:
        """Marked commits not to offer now. A marked barrier is left out once its
        backoff has passed: it is the only way forward, so it is tried again."""
        active = set(self.store.do_not_retry(now))
        for release in graph.releases:
            if release.barrier and not release.deadend and release.commit in active and booted_version < release.version:
                mark = self.store.retry_mark(release.commit)
                if mark is not None and barrier_retry_due(mark, now):
                    log.info("trying barrier %s again after its backoff", release.version)
                    active.discard(release.commit)
        return frozenset(active)

    def check(self, *, automatic: bool = False) -> graphmod.Decision | None:
        self._begin()
        try:
            return self._check_locked(automatic=automatic)
        finally:
            self._end()

    def _check_locked(self, *, automatic: bool) -> graphmod.Decision | None:
        now = self.clock()
        # A last attempt dated after now was made while the clock ran ahead: the check is due.
        since_attempt = now - float(self.store.data.get("last_attempt", 0) or 0)
        booted_now = self._booted_commit()
        # An attempt made on another system (before the restart into an update, or by an
        # older agent that never said which) does not space out this one's first check.
        if automatic and 0 <= since_attempt < self.settings.minimum_check_spacing_seconds \
                and booted_now and self.store.data.get("attempt_booted") == booted_now:
            log.info("skipping automatic check: the last attempt was less than %ss ago",
                     self.settings.minimum_check_spacing_seconds)
            self.refresh(keep_busy=False)
            return self._decision
        previous_state = self.status().state
        self._set(state=CHECKING, progress=0.0)
        with self.store.locked():
            self.store.data["last_attempt"] = int(now)
            self.store.data["attempt_booted"] = booted_now
        try:
            view = self._view()
            adopting = self._adopting(view)
            if view.origin is None and not adopting:
                raise Unmanaged(f"booted origin {view.booted.origin!r} is not a Luma channel")
            channel = self._channel(view) if view.origin is not None else adopting
            # Every channel is public (ADR-030 section 4, 2026-09-16): a preview channel
            # needs no enrollment. A computer that has a preview credential keeps using it.
            graph = self.fetch_graph(channel, self._arch(view))
            booted_version = self._booted_version(view)
            do_not_retry = self._do_not_retry(graph, booted_version, now)
            wariness = load_wariness(self.paths.wariness_file)
            data = self.store.data

            # A staged release that has since been pulled is removed before booting into it.
            pending = data.get("pending") if isinstance(data.get("pending"), dict) else None
            if self._ours_staged(view):
                staged_release = graph.by_commit(view.staged.base_commit)
                if staged_release is not None and (staged_release.deadend or staged_release.paused):
                    log.warning("staged release %s was pulled or paused; removing it", staged_release.version)
                    self.backend.cleanup_pending()
                    with self.store.locked():
                        self.store.data["pending"] = None
                    view = self._view()
                    pending = None

            if data.get("switch_now") and data.get("requested_channel"):
                decision = graphmod.select_switch_now(graph, wariness=wariness, now=now, do_not_retry=do_not_retry)
                if decision.release is not None and booted_version < decision.release.version:
                    decision = graphmod.Decision("update", decision.release, "switch-now")
                if decision.release is not None and decision.release.commit == view.booted.base_commit:
                    decision = graphmod.Decision("none", None, "switch-now-already-booted")
            else:
                decision = graphmod.select_target(graph, booted_version=booted_version,
                                                  booted_commit=view.booted.base_commit, wariness=wariness,
                                                  now=now, do_not_retry=do_not_retry)
            release = decision.release
            available = None
            if release is not None and self._ours_waiting(view) != release.commit:
                available = {
                    "version": str(release.version), "commit": release.commit, "summary": release.summary,
                    "notes_url": release.notes_url or "", "importance": release.importance,
                    "download_bytes": release.download_bytes_estimate, "action": decision.action,
                    "channel": channel, "display_name": release.display_name,
                }
            booted_release = decision.booted_release
            extras = {
                "booted_deadend_reason": (booted_release.deadend_reason or "pulled")
                if booted_release is not None and booted_release.deadend else "",
                "waiting_version": str(decision.waiting.version) if decision.waiting else "",
                "barrier_blocked": decision.reason == "barrier-blocked",
            }
            # Names ride along with the versions they belong to, never deciding anything.
            names_extras = {
                "waiting_display_name": decision.waiting.display_name if decision.waiting else "",
                "booted_display_name": booted_release.display_name if booted_release is not None else "",
            }
            if available is not None:
                available.update(extras)
                available.update(names_extras)
            elif any(extras.values()):
                available = {**extras, **names_extras}
            with self.store.locked():
                self.store.data["last_check"] = int(now)
                self.store.data["last_error"] = ""
                self.store.data["last_error_class"] = ""
                self.store.data["available"] = available
                # Kept even when nothing is available: an up-to-date computer
                # whose image predates release names is named by the graph.
                if names_extras["booted_display_name"]:
                    self.store.data["booted_display_name"] = {"commit": view.booted.base_commit,
                                                              "name": names_extras["booted_display_name"]}
                self.store.data["last_check_reason"] = str(decision.reason or "")
                if adopting and view.origin is None:
                    # Nothing to reconcile yet: the requested channel stays set
                    # until the adopted deployment boots.
                    self.store.data["requested_channel"] = adopting
                ignored = self.store.data.get("ignored")
                if isinstance(ignored, dict) and release is not None and ignored.get("version") == str(release.version) \
                        and not ignored.get("commit"):
                    # A version ignored before its commit was known: pin it now, so a
                    # different release that reuses the version string is still offered.
                    ignored["commit"] = release.commit
                if data.get("requested_channel") and view.origin is not None \
                        and view.origin.channel == data.get("requested_channel") \
                        and decision.action == "none":
                    self.store.data["requested_channel"] = ""
                    self.store.data["switch_now"] = False
            self._decision, self._decision_at, self._decision_channel = decision, now, channel
            self.refresh(keep_busy=False)
            log.info("check on %s: %s (%s)%s", channel, decision.action, decision.reason,
                     f" -> {release.version}" if release else "")
            return decision
        except Exception as error:
            self._fail(error, keep_state=STAGED if previous_state == STAGED else None)
            return None

    # ── Downloading and staging ──────────────────────────────────────────

    def automatic(self) -> None:
        """The timer's path: check, download if policy allows, then report."""
        decision = self.check(automatic=True)
        if decision is not None and decision.action != "none":
            allowed, reason = automatic_download_allowed(self.settings, self.probes.network(), self.probes.power(),
                                                         self.automatic_download_enabled())
            if allowed:
                self.download(user_initiated=False)
            else:
                log.info("not downloading automatically: %s", reason)
        self.flush_reports()

    def download(self, *, user_initiated: bool) -> bool:
        self._begin()
        try:
            return self._download_locked(user_initiated=user_initiated)
        finally:
            self._end()

    def _download_locked(self, *, user_initiated: bool) -> bool:
        now = self.clock()
        decision = self._decision
        if decision is None or not 0 <= now - self._decision_at <= DECISION_MAX_AGE:
            decision = self._check_locked(automatic=False)
        if decision is None or decision.release is None or decision.action == "none":
            return False
        view = self._view()
        release = decision.release
        if self._ours_waiting(view) == release.commit:
            self.refresh(keep_busy=False)
            return True
        if view.staged is not None and view.staged_origin is None:
            self._fail(UpdateError("a deployment from another source is waiting for a restart", "busy"))
            return False
        if not user_initiated:
            if self.is_ignored(str(release.version), release.commit):
                log.info("not downloading automatically: %s is ignored", release.version)
                self.refresh(keep_busy=False)
                return False
            allowed, reason = automatic_download_allowed(self.settings, self.probes.network(), self.probes.power(),
                                                         self.automatic_download_enabled())
            if not allowed:
                log.info("not downloading automatically: %s", reason)
                self.refresh(keep_busy=False)
                return False
        if user_initiated and self.is_ignored(str(release.version), release.commit):
            # Asking for it is the way back: an ignored version a person downloads
            # is no longer ignored, so the notifier may say it is ready.
            with self.store.locked():
                self.store.data["ignored"] = None
        channel = self._decision_channel or (self._channel(view) if view.origin is not None
                                             else self._adopting(view))
        target_refspec = refspec_for(channel, self._arch(view), self.settings)
        # Adoption always rebases: there is no Luma origin to compare against.
        refspec = None if (view.origin is not None and target_refspec == view.origin.refspec) else target_refspec
        kind = "update" if decision.action == "update" else "rollback"
        if self.backend.active_transaction() is not None:
            self._fail(Busy("another rpm-ostree transaction is in progress"))
            return False
        new_cancellable = getattr(self.backend, "new_cancellable", None)
        token = new_cancellable() if new_cancellable is not None else CancelToken()
        with self._download_lock:
            self._download_cancel, self._download_automatic, self._cancel_reason = token, not user_initiated, ""
            self._policy_checked_at = time.monotonic()
        self._set(state=DOWNLOADING, progress=0.0, last_error="", last_error_class="")

        def progress(fraction, _transferred):
            if fraction is not None:
                self._set(progress=round(max(0.0, min(1.0, fraction)), 3))
            if not user_initiated and time.monotonic() - self._policy_checked_at >= self.policy_recheck_seconds:
                self._policy_checked_at = time.monotonic()
                self.connection_changed()

        added_packages = any(getattr(item, "layered", False) for item in (view.booted, view.staged) if item)

        # Adopting a channel replaces the base: package overrides made against the old
        # base are removed (the channel's versions are used); added packages stay.
        adopting_now = view.origin is None

        # Added packages the new base now ships, removed with this update so it can stage.
        dropping: list[str] = []
        # Added packages NEWER than the copy the new base ships (request, base package
        # name): kept, and the update waits (see added_packages_in_conflict).
        keeping: list[tuple[str, str]] = []

        def stage(allow_downgrade: bool, cache_only: bool = False) -> None:
            options = {"cache_only": True} if cache_only else {}
            if adopting_now:
                options["no_overrides"] = True
            if dropping:
                options["uninstall"] = tuple(dropping)
            try:
                self.backend.update_deployment(revision=release.commit, refspec=refspec,
                                               allow_downgrade=allow_downgrade, progress=progress,
                                               message=lambda text: log.info("rpm-ostree: %s", text),
                                               cancellable=token, **options)
            except Exception as error:
                if token.is_cancelled():
                    raise
                drop, keep = added_packages_in_conflict(error, (view.booted, view.staged))
                drop = [request for request in drop if request not in dropping]
                keep = [pair for pair in keep if pair not in keeping and pair[0] not in dropping]
                if keep:
                    keeping.extend(keep)
                    log.warning("Luma %s includes an older %s than this computer added (%s); keeping the added "
                                "copy and not staging this update", release.version,
                                ", ".join(name for _, name in keep), ", ".join(request for request, _ in keep))
                    with self.store.locked():
                        self.store.data["kept_packages"] = {"to_version": str(release.version),
                                                            "packages": [request for request, _ in keeping]}
                    raise UpdateError("an added package is newer than the release's copy: "
                                      + ", ".join(request for request, _ in keeping),
                                      "added-package-newer") from error
                if not drop:
                    raise
                # Each retry removes at least one more request, so this ends.
                log.warning("Luma %s includes %s, which this computer had added on its own; removing the added "
                            "copy so the update can stage (rpm-ostree: %s)", release.version, ", ".join(drop),
                            str(error).splitlines()[0][:300] if str(error) else "no detail")
                dropping.extend(drop)
                stage(allow_downgrade, cache_only)

        def stage_carrying_added_packages(allow_downgrade: bool) -> None:
            """ADR-038: packages a person added are re-resolved from their
            repositories with every update. When those repositories cannot be
            reached, Luma's update still stages, with the added packages as this
            computer already has them (rpm-ostree's cache); they are refreshed
            with the next update. Luma's own release comes from Luma's server,
            which the first attempt has already pulled."""
            try:
                stage(allow_downgrade)
            except Exception as error:
                if not (added_packages and not token.is_cancelled() and package_repository_unreachable(error)):
                    raise
                log.warning("the package repositories of added packages are unreachable (%s); staging %s with "
                            "the added packages from this computer's cache, to be refreshed with the next update",
                            str(error).splitlines()[0][:300] if str(error) else "no detail", release.version)
                try:
                    stage(allow_downgrade, cache_only=True)
                except Exception as cached_error:
                    log.error("Luma %s could not be staged from the cache either (%s); the running system is "
                              "unchanged and the update is tried again later", release.version, cached_error)
                    raise UpdateError(f"the package repositories of packages added to this computer are "
                                      f"unreachable: {cached_error}", "added-packages-unreachable") from error

        def stage_from_any_repository(allow_downgrade: bool) -> None:
            """Every channel is public (ADR-030 section 4), so a preview
            credential can only ever add a way in, never be the only one. A
            preview repository that refuses or cannot be reached (a revoked or
            stale credential) is retried once from the public repository; the
            credential is set aside only when that works, and put back when it
            does not."""
            try:
                stage_carrying_added_packages(allow_downgrade)
            except Exception as error:
                if token.is_cancelled() or not preview.pulls_from_preview(self.paths, self.settings) \
                        or classify_error(error, credential_installed=True) not in ("access-denied", "network"):
                    raise
                log.warning("the early-updates repository did not serve Luma %s (%s); every channel is public, "
                            "so trying the public repository", release.version,
                            redact.redact(str(error).splitlines()[0][:300]) if str(error) else "no detail")
                saved = preview.set_aside(self.paths, self.settings)
                try:
                    stage_carrying_added_packages(allow_downgrade)
                except Exception:
                    preview.restore(saved)
                    raise
                log.warning("Luma %s came from the public repository; this computer's early-updates credential "
                            "was set aside and is no longer used", release.version)

        # A mirror list still naming a preview repository with no credential record
        # behind it (removed by hand, or from an older agent) is not an enrollment.
        if preview.read_credential(self.paths) is None and preview.pulls_from_preview(self.paths, self.settings):
            log.warning("the Luma remote still pulls from an early-updates repository but no credential is "
                        "installed; using the public repository")
            preview.remove_credential(self.paths, self.settings)

        transferred = False
        kargs_added: list[str] = []
        try:
            try:
                try:
                    stage_from_any_repository(kind == "rollback")
                except Exception as error:
                    if not (kind == "update" and not token.is_cancelled()
                            and _older_timestamp_refusal(error, release.commit)
                            and self._booted_version(view) < release.version):
                        raise
                    # Promotion gives a release a new commit with the promotion time, so a
                    # release that is newer by version can carry an older timestamp than the
                    # booted commit. The signed graph chose this exact commit as newer, and
                    # set-revision pins it, so the timestamp check adds nothing here.
                    log.info("rpm-ostree refused %s as chronologically older than the booted commit; "
                             "retrying the signed, version-newer commit with allow-downgrade", release.version)
                    stage_from_any_repository(True)
            finally:
                with self._download_lock:
                    self._download_cancel = None
            transferred = True
            view_after = self._view()
            if view_after.staged is None or view_after.staged.base_commit != release.commit:
                if view_after.staged is not None and view_after.staged_origin is not None:
                    # Whatever this transaction staged is not what was asked for: never leave it
                    # to be finalized at the next shutdown.
                    try:
                        self.backend.cleanup_pending(message=lambda text: log.info("rpm-ostree: %s", text))
                    except Exception as cleanup_error:
                        log.error("could not remove the wrong staged deployment: %s", cleanup_error)
                raise UpdateError("rpm-ostree did not stage the requested commit", "transaction")
            kargs_added = self._apply_release_kargs(view_after, release)
            self._refresh_boot_menu(view_after, release)
        except Exception as error:
            if token.is_cancelled() and not transferred:
                reason = self._cancel_reason or "cancelled"
                log.info("download of %s cancelled (%s)", release.version, reason)
                self._set(progress=0.0)
                self.refresh(keep_busy=False)
                return False
            error_class = classify_error(error, credential_installed=preview.read_credential(self.paths) is not None)
            with self.store.locked():
                reporting.queue_transition(self.store, self.paths, channel=channel,
                                           from_version=view.booted.version, to_version=str(release.version),
                                           arch=self._arch(view), result="failed", error_class=error_class)
            self._fail(error)
            return False
        with self.store.locked():
            self.store.data["pending"] = {
                "kind": kind, "channel": channel, "from_version": view.booted.version,
                "from_commit": view.booted.base_commit, "to_version": str(release.version),
                "to_commit": release.commit, "importance": release.importance,
                "summary": release.summary, "notes_url": release.notes_url or "",
                "staged_at": int(now), "staged_boot_id": self.boot_id(), "health_failures": 0,
                "arch": self._arch(view), "kargs_added": kargs_added,
                "to_display_name": release.display_name,
                "removed_packages": list(dropping),
            }
            # Kept until the next staging, so Depot can say what the update took over.
            self.store.data["removed_packages"] = {"to_version": str(release.version), "packages": list(dropping)} \
                if dropping else None
            # Nothing is waiting on an added copy any more.
            self.store.data["kept_packages"] = None
            self.store.data["available"] = None
            self.store.data["switch_now"] = False
            reporting.queue_transition(self.store, self.paths, channel=channel,
                                       from_version=view.booted.version, to_version=str(release.version),
                                       arch=self._arch(view), result="staged")
        self._set(progress=1.0)
        self.refresh(keep_busy=False)
        log.info("staged %s (%s) for the next restart", release.version, release.commit)
        return True

    def _refresh_boot_menu(self, view: SystemView, release) -> None:
        """Let the staged release bring GRUB's hidden menu up to date before the
        restart (bootmenu.py). Cosmetic, so it never stops the update: the
        release's boot unit does the same at every start."""
        try:
            said = self.backend.refresh_boot_menu(view.staged)
        except Exception as error:
            log.warning("GRUB menu for %s was not brought up to date; the release retries at boot: %s",
                        release.version, error)
            return
        if said:
            log.info("GRUB menu for %s: %s", release.version, said)

    def _apply_release_kargs(self, view: SystemView, release) -> list[str]:
        """Give the staged deployment the kernel arguments its release declares.

        rpm-ostree does not apply ``/usr/lib/bootc/kargs.d`` when it deploys an
        OSTree commit; bootc does. As bootc would, append every declared argument
        the staged deployment lacks, and delete arguments this agent added for an
        earlier release that the new release no longer declares (never anything
        else). Returns the arguments this agent is now responsible for. On any
        failure the staged deployment is removed: it must not boot without them."""
        staged = view.staged
        try:
            wanted = kargsmod.declared(self.backend.deployment_root(staged), self.arch)
            data = self.store.snapshot()
            previously = [item for item in data.get("kargs_added") or [] if isinstance(item, str)]
            config = self.backend.boot_config(pending=True)
            if not config.get("staged"):
                raise kargsmod.KargsError("rpm-ostree's pending deployment is not the staged update")
            existing = str(config.get("options") or "")
            present = existing.split()
            append = [item for item in wanted if item not in present]
            drop = [item for item in previously if item not in wanted and item in present]
            if append or drop:
                log.info("kernel arguments for %s: adding %s%s", release.version, " ".join(append) or "nothing",
                         f", removing {' '.join(drop)}" if drop else "")
                self.backend.kernel_args(existing=existing, append_if_missing=append, delete_if_present=drop,
                                         message=lambda text: log.info("rpm-ostree: %s", text))
                after = self._view()
                if after.staged is None or after.staged.base_commit != release.commit:
                    raise kargsmod.KargsError("the staged update changed while its kernel arguments were set")
                config = self.backend.boot_config(pending=True)
                now_present = str(config.get("options") or "").split()
                missing = [item for item in wanted if item not in now_present]
                left = [item for item in drop if item in now_present]
                if missing or left or not config.get("staged"):
                    raise kargsmod.KargsError("rpm-ostree did not set the kernel arguments: "
                                              f"missing {' '.join(missing) or '-'}, not removed {' '.join(left) or '-'}")
            return [item for item in previously if item in wanted] + [item for item in append if item not in previously]
        except Exception as error:
            log.error("kernel arguments for %s could not be set; removing the staged update: %s",
                      release.version, error)
            try:
                self.backend.cleanup_pending(message=lambda text: log.info("rpm-ostree: %s", text))
            except Exception as cleanup_error:
                log.error("could not remove the staged update: %s", cleanup_error)
            if isinstance(error, kargsmod.KargsError):
                raise
            raise kargsmod.KargsError(f"kernel arguments could not be set: {error}") from None

    def download_running(self) -> bool:
        with self._download_lock:
            return self._download_cancel is not None

    def cancel_download(self, reason: str = "asked") -> None:
        """Stop the running download. Nothing is staged and nothing is reported."""
        with self._download_lock:
            token = self._download_cancel
            if token is None:
                raise NothingToDo("no download is running")
            self._cancel_reason = reason
        log.info("cancelling the download: %s", reason)
        token.cancel()

    def connection_changed(self) -> bool:
        """Re-check the policy for a running automatic download and cancel it if
        the connection became metered or power low. Returns whether it cancelled."""
        with self._download_lock:
            token, automatic = self._download_cancel, self._download_automatic
        if token is None or not automatic or token.is_cancelled():
            return False
        allowed, reason = automatic_download_allowed(self.settings, self.probes.network(), self.probes.power(),
                                                     self.automatic_download_enabled())
        if allowed:
            return False
        with self._download_lock:
            if self._download_cancel is not token:
                return False
            self._cancel_reason = reason
        log.info("cancelling the automatic download: %s", reason)
        token.cancel()
        return True

    # ── Restart, rollback, channels ──────────────────────────────────────

    def apply(self, restart) -> str:
        """Restart to finish this agent's own staged update or rollback.

        ``restart`` performs the restart (the daemon authorizes the caller and
        asks logind). It runs as an operation, so no check, download or other
        change can start in between, and never while rpm-ostree is busy.
        Returns ``update`` or ``rollback``."""
        self._begin()
        try:
            view = self._view()
            if self.backend.active_transaction() is not None:
                raise Busy("another rpm-ostree transaction is in progress")
            data = self.store.snapshot()
            if self._ours_waiting(view, data):
                kind = "update"
            elif view.staged is not None:
                raise NotOurs("the change waiting for a restart was not made by Luma's update service; "
                              "restart the computer to finish it")
            elif view.default_is_rollback:
                marker = data.get("rollback_restart") if isinstance(data.get("rollback_restart"), dict) else {}
                if not (marker.get("from_commit") == view.booted.base_commit
                        and marker.get("to_commit") == view.deployments[0].base_commit):
                    raise NotOurs("the previous version was chosen for the next start by something other than "
                                  "Luma's update service; restart the computer to finish it")
                kind = "rollback"
            else:
                raise NothingToDo("nothing is waiting for a restart")
            log.info("restarting to finish the %s, as requested", kind)
            restart()
            return kind
        finally:
            self._end()

    def rollback(self) -> None:
        self._begin(wait=PERSON_WAIT_SECONDS)
        try:
            view = self._view()
            if view.rollback is None:
                raise NothingToDo("there is no previous version to go back to")
            if self.backend.active_transaction() is not None:
                raise Busy("another rpm-ostree transaction is in progress")
            self.backend.rollback(message=lambda text: log.info("rpm-ostree: %s", text))
            after = self._view()
            with self.store.locked():
                self.store.data["pending"] = None
                self.store.data["available"] = None
                # Apply() restarts only into a rollback this agent made.
                self.store.data["rollback_restart"] = {
                    "from_commit": view.booted.base_commit, "to_commit": after.deployments[0].base_commit,
                } if after.default_is_rollback else None
                if after.default_is_rollback:
                    # The person chose to leave this version: do not offer it again.
                    self.store.add_do_not_retry(view.booted.base_commit, view.booted.version,
                                                "rolled-back-by-person", self.clock())
                else:
                    # Rolling back a rollback: the booted version is wanted again.
                    self.store.remove_do_not_retry(view.booted.base_commit, "rolled-back-by-person")
            self.refresh()
        finally:
            self._end()

    def set_channel(self, channel: str, *, switch_now: bool = False) -> None:
        self._begin(wait=PERSON_WAIT_SECONDS)
        try:
            self._set_channel_locked(channel, switch_now=switch_now)
        finally:
            self._end()

    def _set_channel_locked(self, channel: str, *, switch_now: bool) -> None:
        if channel not in CHANNELS:
            raise UpdateError(f"unknown channel {channel!r}", "preview")
        view = self._view()
        if view.origin is None:
            raise Unmanaged("this computer does not follow a Luma update channel")
        left = None
        if channel == "stable":
            # Leave locally first: the mirror list goes back to the public repository and
            # the record is removed whatever Hub says. A missing or unreadable record reads
            # as None; its file is removed all the same.
            record = preview.read_credential(self.paths)
            preview.remove_credential(self.paths, self.settings)
            left = record
            try:
                self.backend.reload(config=True)
            except Exception as error:  # a stale remote list is harmless; the refspec changes on staging
                log.warning("rpm-ostree did not reload its configuration: %s", error)
        try:
            staying = channel == view.origin.channel
            if view.staged is not None and self._ours_staged(view):
                pending = self.store.data.get("pending") or {}
                if pending.get("channel") != channel:
                    self.backend.cleanup_pending()
                    with self.store.locked():
                        self.store.data["pending"] = None
            with self.store.locked():
                self.store.data["requested_channel"] = "" if staying else channel
                self.store.data["switch_now"] = bool(switch_now and not staying)
                self.store.data["available"] = None
            self._decision = None
            self.refresh()
        finally:
            if left is not None:
                # Last, after the credential is gone locally, and never raising: only a
                # credential Hub issued to this device alone is revoked. A staff medium's
                # per-batch credential is shared by its batch and only staff revoke it.
                preview.revoke_credential(self.http, self.settings, left)

    def adopt_channel(self, channel: str) -> None:
        """Start following a Luma channel on a computer that follows none.

        This rebases the computer onto ``luma:luma/1/<arch>/<channel>`` and
        stages that channel's newest usable release, which may be older than
        what is booted. It is offered only where the Luma remote and an
        update-graph key are already installed (``adoption_state``), the graph
        still has to verify, and nothing is applied until a person restarts:
        the deployment they are running now stays as the rollback.
        """
        self._begin(wait=PERSON_WAIT_SECONDS)
        try:
            if channel not in CHANNELS:
                raise UpdateError(f"unknown channel {channel!r}", "invalid-argument")
            view = self._view()
            if view.origin is not None:
                raise NothingToDo("this computer already follows a Luma channel")
            self._require_adoptable(view)
            self._adopt_locked(channel)
        finally:
            self._end()

    def _require_adoptable(self, view: SystemView) -> None:
        adoptable, reason = adoption_state(self.paths, self.settings, view)
        if not adoptable:
            raise Unmanaged(f"this computer cannot start following a Luma channel: {reason}")

    def _adopt_locked(self, channel: str) -> None:
        """Rebase an adoptable computer onto ``channel`` and stage its newest usable release."""
        with self.store.locked():
            self.store.data["adopting"] = True
            self.store.data["requested_channel"] = channel
            self.store.data["switch_now"] = True
            self.store.data["available"] = None
            self.store.data["last_error"] = ""
            self.store.data["last_error_class"] = ""
        self._decision = None
        self.refresh()
        decision = self._check_locked(automatic=False)
        if decision is None:
            return  # the failure is already published; the request stands
        if decision.release is None:
            with self.store.locked():
                self.store.data["adopting"] = False
                self.store.data["requested_channel"] = ""
                self.store.data["switch_now"] = False
            raise UpdateError(f"the {channel} channel has no release this computer can use",
                              "no-usable-release")
        # A person asked for this one by name, so it downloads whatever the
        # connection costs -- exactly as Download() does.
        self._download_locked(user_initiated=True)

    def enroll_preview(self, channel: str, connect_token: str) -> None:
        """Exchange a Luma Connect device token for this computer's own preview
        credential at Hub, install it, and follow ``channel``.

        Kept for compatibility: every channel is public since 2026-09-16, so Depot
        and the command line choose Beta and Nightly with SetChannel/AdoptChannel.
        A computer that follows no channel yet but could (``Adoptable``) is
        enrolled and then adopted onto the channel."""
        self._enroll(channel, lambda arch: preview.request_credential(
            self.http, self.settings, channel, arch, connect_token))

    def _enroll(self, channel: str, obtain) -> None:
        if channel not in PREVIEW_CHANNELS:
            raise UpdateError("early updates are the beta and nightly channels", "invalid-argument")
        self._begin(wait=PERSON_WAIT_SECONDS)
        try:
            view = self._view()
            adopting = view.origin is None
            if adopting:
                # Refuse before anything is requested or written.
                self._require_adoptable(view)
            try:
                credential, entitled, credential_id = obtain(self._arch(view))
                preview.install_credential(self.paths, self.settings, credential, channel, entitled,
                                           credential_id)
            except preview.PreviewError as error:
                raise UpdateError(str(error), error.error_class) from None
            log.info("enrolled in early updates (%s) through Luma Hub", channel)
            try:
                self.backend.reload(config=True)
            except Exception as error:  # the refspec changes on staging whatever rpm-ostreed cached
                log.warning("rpm-ostree did not reload its configuration: %s", error)
            if adopting:
                self._adopt_locked(channel)
            else:
                self._set_channel_locked(channel, switch_now=False)
        finally:
            self._end()

    def leave_preview(self) -> None:
        self.set_channel("stable")

    def acknowledge_rollback_notice(self) -> None:
        self._begin(wait=PERSON_WAIT_SECONDS)
        try:
            with self.store.locked():
                self.store.data["rollback_notice"] = None
            self.refresh()
        finally:
            self._end()

    # ── After a restart ──────────────────────────────────────────────────

    def greenboot_installed(self) -> bool:
        if self._greenboot_installed is not None:
            return bool(self._greenboot_installed)
        return Path(GREENBOOT_UNIT).exists()

    def reconcile_boot(self, health: str | None = None) -> str:
        """Decide what became of a staged update. ``health`` is green, red or None.

        Returns booted, rolled_back, booted_previous, failed, pending, cleared or none.
        """
        self._begin(wait=PERSON_WAIT_SECONDS)
        try:
            return self._reconcile_locked(health)
        finally:
            self._end()

    def _booted_commit(self) -> str:
        try:
            return self._view().booted.base_commit
        except Exception:
            return ""

    def _forget_another_systems_error(self, view: SystemView) -> None:
        """An error and a last attempt recorded while another deployment was booted
        (or by an agent too old to say which) describe that system, not this one:
        a person restarting into a fixed update must not keep seeing the old error,
        and the first check here is not spaced out by an attempt made there."""
        booted = view.booted.base_commit
        with self.store.locked():
            data = self.store.data
            if not booted or data.get("attempt_booted") == booted:
                return
            if data.get("last_error") or data.get("last_attempt"):
                log.info("forgetting the last check's result from another system (%s)",
                         data.get("last_error_class") or "no error")
                if data.get("last_check_reason") and data.get("last_check_reason") == data.get("last_error_class"):
                    data["last_check_reason"] = ""
                data["last_error"] = ""
                data["last_error_class"] = ""
                data["last_attempt"] = 0
            data["attempt_booted"] = booted

    def _reconcile_locked(self, health: str | None) -> str:
        view = self._view()
        now = self.clock()
        outcome = "none"
        self._forget_another_systems_error(view)
        with self.store.locked():
            pending = self.store.data.get("pending")
            if not isinstance(pending, dict) or not pending.get("to_commit"):
                return outcome
            booted = view.booted.base_commit
            channel = pending.get("channel", "")
            arch = pending.get("arch") or (view.origin.arch if view.origin else self.arch)
            fields = dict(channel=channel, from_version=pending.get("from_version", ""),
                          to_version=pending.get("to_version", ""), arch=arch)
            if booted == pending["to_commit"]:
                if health == "red":
                    pending["health_failures"] = int(pending.get("health_failures", 0)) + 1
                    self.store.data["pending"] = pending
                    outcome = "pending"
                elif health == "green" or not self.greenboot_installed():
                    reporting.queue_transition(self.store, self.paths, result="booted", **fields)
                    self.store.data["pending"] = None
                    self.store.data["requested_channel"] = ""
                    # The kernel arguments this agent added are now the booted system's.
                    if isinstance(pending.get("kargs_added"), list):
                        self.store.data["kargs_added"] = [item for item in pending["kargs_added"]
                                                          if isinstance(item, str)]
                    # It works here: whatever went wrong on an earlier attempt is forgotten.
                    self.store.remove_do_not_retry(pending["to_commit"])
                    self._forget_finalize_failures(pending["to_commit"])
                    outcome = "booted"
                else:
                    outcome = "pending"
            elif view.staged is not None and view.staged.base_commit == pending["to_commit"]:
                outcome = "pending"
            elif pending.get("staged_boot_id") and pending.get("staged_boot_id") == self.boot_id():
                # Same boot, and the staged deployment is gone: someone removed it.
                self.store.data["pending"] = None
                outcome = "cleared"
            elif booted == pending.get("from_commit") and self._ours_finalized(view):
                # The update finalized and is still the default, but the previous entry was
                # chosen in the boot menu for this one boot. The next restart starts the update.
                outcome = "pending"
            elif booted == pending.get("from_commit"):
                still_there = any(d.base_commit == pending["to_commit"] for d in view.deployments)
                health_failures = int(pending.get("health_failures", 0) or 0)
                if still_there and health_failures > 0:
                    # greenboot recorded a failed health check on the new version, then fell back.
                    self.store.add_do_not_retry(pending["to_commit"], pending.get("to_version", ""),
                                                "rolled-back-after-failed-boot", now)
                    self.store.data["rollback_notice"] = {
                        "from_version": pending.get("to_version", ""), "to_version": view.booted.version,
                        "at": int(now), "id": f"{pending['to_commit'][:16]}-{int(now)}",
                        "from_display_name": self._failed_name(view, pending)}
                    reporting.queue_transition(self.store, self.paths, result="rolled_back",
                                               error_class="health-check", **fields)
                    outcome = "rolled_back"
                elif still_there:
                    # The previous version became the default again with no failed health check
                    # recorded: the new version never got far enough to run its checks, or someone
                    # rolled back by other means. Not proof it is broken: wait a week.
                    self.store.add_do_not_retry(pending["to_commit"], pending.get("to_version", ""),
                                                "booted-previous-deployment", now)
                    reporting.queue_transition(self.store, self.paths, result="rolled_back",
                                               error_class="unknown", **fields)
                    outcome = "booted_previous"
                else:
                    # The staged deployment never finalized: an unclean shutdown (power loss, a
                    # held power button) drops it with /run. Retried at once the first time.
                    counts = self.store.data.get("finalize_failures")
                    counts = {k: v for k, v in counts.items() if isinstance(v, int)} if isinstance(counts, dict) else {}
                    failures = counts.get(pending["to_commit"], 0) + 1
                    counts[pending["to_commit"]] = failures
                    self.store.data["finalize_failures"] = dict(list(counts.items())[-16:])
                    if failures >= 2:
                        self.store.add_do_not_retry(pending["to_commit"], pending.get("to_version", ""),
                                                    "failed-to-finalize", now)
                    reporting.queue_transition(self.store, self.paths, result="failed",
                                               error_class="finalize", **fields)
                    outcome = "failed"
                self.store.data["pending"] = None
            else:
                self.store.data["pending"] = None
                outcome = "cleared"
        log.info("boot reconciliation: %s", outcome)
        self.refresh()
        return outcome

    def _forget_finalize_failures(self, commit: str) -> None:
        counts = self.store.data.get("finalize_failures")
        if isinstance(counts, dict) and commit in counts:
            self.store.data["finalize_failures"] = {k: v for k, v in counts.items() if k != commit}

    # ── Reports ──────────────────────────────────────────────────────────

    def flush_reports(self) -> int:
        """Send the weekly check-in and queued transitions.

        Runs as an operation, so it never overlaps a check, a download or a
        person's change on another thread; if one is running the reports stay
        queued for the next time. The state file lock is not held while posting."""
        try:
            self._begin()
        except Busy:
            log.info("reports stay queued: an update operation is running")
            return 0
        try:
            return self._flush_locked()
        finally:
            self._end()

    def _flush_locked(self) -> int:
        try:
            view = self._view()
        except Exception:
            return 0
        now = self.clock()
        with self.store.locked():
            if not reporting.statistics_enabled(self.paths):
                self.store.data["reports"] = []
                return 0
            countme = None
            if view.origin is not None:
                countme = reporting.countme_payload(self.store, channel=view.origin.channel,
                                                    version=view.booted.version, arch=view.origin.arch, now=now)
            reports = [item for item in self.store.data.get("reports") or [] if isinstance(item, dict)]
        counted = False
        delivered = 0
        sent = 0
        if countme is not None:
            result = reporting.send(self.http, self.settings, countme)
            if result is None:
                return 0
            counted = True
            sent += 1 if result else 0
        for report in reports:
            result = reporting.send(self.http, self.settings, report)
            if result is None:
                break
            delivered += 1
            sent += 1 if result else 0
        with self.store.locked():
            current = [item for item in self.store.data.get("reports") or [] if isinstance(item, dict)]
            if current[:delivered] == reports[:delivered]:
                self.store.data["reports"] = current[delivered:]
            if counted:
                self.store.data["countme"]["counted_window"] = reporting.window_start(now)
        return sent


def ensure_runtime_dir(paths: Paths) -> None:
    try:
        os.makedirs(paths.runtime_dir, mode=0o755, exist_ok=True)
    except OSError:
        pass
