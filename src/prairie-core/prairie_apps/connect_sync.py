# SPDX-License-Identifier: Apache-2.0

"""Device-side exporter that pushes Contacts and Notes to the Luma Connect Hub.

The exporter is read-only against the device's own sources of truth: contacts
come from Evolution Data Server through :mod:`prairie_apps.eds_backend` and
notes from the durable :class:`prairie_apps.notes_backend.NotesStore`. Nothing
here reimplements either store, and nothing here prints personal content.

Transport is a full snapshot per service. The hub tombstones anything it holds
for this device that is absent from the snapshot, so a note whose ``deleted_at``
is set is simply left out.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
from dataclasses import dataclass
try:
    import fcntl
except ImportError:  # not on Linux; the lock is best effort
    fcntl = None
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import uuid

from .eds_backend import inspect_eds_inventory, load_contacts
from .app_installs import NOTES, app_environment
from .notes_backend import NotesStore, notes_data_directory

USER_AGENT = "ProjectLuma-Connect/1"
REQUEST_TIMEOUT = 20.0
MAX_ITEMS = 5000
MAX_NOTE_BODY_BYTES = 256 * 1024
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})

# Payload shapes are fixed by the Connect sync contract (v1). They are also the
# only thing --dry-run is allowed to name; values never reach the terminal.
CONTACT_FIELDS = ("uid", "name", "phone", "email", "organization", "role")
NOTE_FIELDS = (
    "id", "title", "body", "folder_id", "folder_name",
    "favorite", "created_at", "modified_at",
)
FOLDER_FIELDS = ("id", "name", "favorite", "sort_order", "expanded")


class ConnectError(RuntimeError):
    """An actionable failure the operator can fix without reading a traceback."""


class AuthorisationError(ConnectError):
    """The hub no longer accepts this device. Retrying cannot fix it."""


class PartialSyncError(ConnectError):
    """A service failed after an authenticated event boundary was observed.

    Keep the failure visible without turning healthy live services into an
    exponential retry loop. This is an observation cursor, not a declaration
    that every service completed its pull.
    """
    def __init__(self, problem, observed_revision):
        super().__init__(str(problem))
        self.observed_revision = observed_revision


# Exit statuses the systemd unit relies on: 1 is worth retrying (network,
# rate limit, hub trouble); 3 is not (the device must be enrolled again).
EXIT_RETRY = 1
EXIT_REENROL = 3
MAX_SETTLE_ROUNDS = 5
# How long the watcher lets a burst of changes on the hub gather before it syncs.
WATCH_COALESCE_SECONDS = 0.25


class HubResponseError(ConnectError):
    def __init__(self, status: int, reason: str, detail: str = "", retry_after: float = 0.0,
                 code: str = "") -> None:
        super().__init__(f"The hub answered {status} ({reason}).")
        self.status = int(status)
        self.reason = reason
        # What the hub said went wrong, in words meant for a person, when it
        # said anything. Bounded and never containing what was sent.
        self.detail = detail
        # How long the hub asked to be left alone (429, 503), in seconds.
        self.retry_after = retry_after
        # The hub's stable name for the refusal ("note_storage_full"), if any.
        self.code = code


def _retry_after(error: HTTPError) -> float:
    try:
        value = float((error.headers or {}).get("Retry-After", "") or 0)
    except (TypeError, ValueError):
        value = 0.0
    return max(0.0, min(value, 3600.0))


def _error_body(error: HTTPError) -> tuple[str, str]:
    """The hub's own {"error": "...", "code": "..."} for a refused request:
    the explanation for a person, and the stable code a client acts on."""
    try:
        text = error.read(4096).decode("utf-8", "replace")
        decoded = json.loads(text)
        message, code = decoded.get("error", ""), decoded.get("code", "")
    except (OSError, ValueError, AttributeError):
        return "", ""
    message = " ".join(message.split())[:300] if isinstance(message, str) else ""
    code = code if isinstance(code, str) and code.replace("_", "").isalnum() and len(code) <= 64 else ""
    return message, code


def _error_detail(error: HTTPError) -> str:
    """The hub's own explanation of a refused request, if any."""
    return _error_body(error)[0]


def _response_error(error: HTTPError) -> "HubResponseError":
    try:
        message, code = _error_body(error)
        return HubResponseError(error.code, error.reason or "no reason given", message, _retry_after(error), code)
    finally:
        error.close()


@dataclass(frozen=True)
class DeviceIdentity:
    device_id: str
    token: str
    hub: str
    name: str
    registered_at: str


def connect_data_directory(environment: dict[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    default = Path(env.get("HOME", str(Path.home()))) / ".local" / "share"
    return Path(env.get("XDG_DATA_HOME", default)) / "luma" / "connect"


def device_file(environment: dict[str, str] | None = None) -> Path:
    return connect_data_directory(environment) / "device.json"


def hub_url(value: str) -> str:
    """Normalise a hub URL and refuse plain http to anywhere but this machine."""
    text = (value or "").strip().rstrip("/")
    if not text:
        raise ConnectError("A hub URL is required, for example --hub https://hub.example.")
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConnectError(f"{text} is not an http(s) hub URL, for example https://hub.example.")
    if parsed.username or parsed.password:
        raise ConnectError("Credentials embedded in the hub URL are not supported; enrol instead.")
    if parsed.scheme == "http" and parsed.hostname not in LOCAL_HOSTS:
        raise ConnectError(
            f"Refusing to send contacts and notes over plain http to {parsed.hostname}. "
            "Use https://, or http://127.0.0.1 (or http://localhost) for local development."
        )
    return text


def _verify_private(path: Path) -> None:
    """Fail loudly rather than leave a bearer token readable by anyone else."""
    directory_mode = path.parent.stat().st_mode & 0o777
    if directory_mode & 0o077:
        raise ConnectError(
            f"{path.parent} is mode {directory_mode:04o}; it must not be group- or "
            f"world-accessible. Run: chmod 700 {path.parent}"
        )
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise ConnectError(
            f"{path} holds a device token and is mode {mode:04o}; it must not be group- or "
            f"world-readable. Run: chmod 600 {path}"
        )


def save_identity(identity: DeviceIdentity, environment: dict[str, str] | None = None) -> Path:
    path = device_file(environment)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".device-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "device_id": identity.device_id,
                    "token": identity.token,
                    "hub": identity.hub,
                    "name": identity.name,
                    "registered_at": identity.registered_at,
                },
                stream,
            )
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.chmod(path, 0o600)
    _verify_private(path)
    return path


def load_identity(environment: dict[str, str] | None = None) -> DeviceIdentity | None:
    env = os.environ if environment is None else environment
    if env.get('FLATPAK_ID'):
        from .collaboration_transport import identity
        return identity()
    path = device_file(environment)
    if not path.exists():
        return None
    _verify_private(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConnectError(f"{path} could not be read ({error}). Re-enrol this device.") from None
    if not isinstance(data, dict) or not data.get("device_id") or not data.get("token"):
        raise ConnectError(f"{path} is not a usable device registration. Re-enrol this device.")
    return DeviceIdentity(
        str(data["device_id"]),
        str(data["token"]),
        str(data.get("hub", "")),
        str(data.get("name", "")),
        str(data.get("registered_at", "")),
    )


def send_json(url: str, payload: dict[str, object], *, token: str = "",
              timeout: float = REQUEST_TIMEOUT) -> dict[str, object]:
    """POST JSON and return the decoded reply. Never logs the payload."""
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", "replace")
    except HTTPError as error:
        raise _response_error(error) from None
    except (URLError, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise ConnectError(
            f"The hub at {url} could not be reached ({reason}). Check the address, "
            "the network, and that the hub is running."
        ) from None
    if not text.strip():
        return {}
    try:
        decoded = json.loads(text)
    except ValueError:
        raise ConnectError(f"The hub at {url} did not return JSON.") from None
    return decoded if isinstance(decoded, dict) else {"result": decoded}


class HubClient:
    """GET, POST, and streamed PUT/GET of files against a hub. Never logs bodies."""

    def __init__(self, timeout: float = REQUEST_TIMEOUT) -> None:
        self.timeout = timeout

    def _open(self, request: Request, timeout: float | None):
        try:
            return urlopen(request, timeout=timeout or self.timeout)
        except HTTPError as error:
            raise _response_error(error) from None
        except (URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise ConnectError(f"The hub at {request.full_url} could not be reached ({reason}).") from None

    @staticmethod
    def _headers(token: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT, **(extra or {})}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def get_json(self, url: str, *, token: str = "", timeout: float | None = None) -> dict:
        with self._open(Request(url, headers=self._headers(token)), timeout) as response:
            text = response.read().decode("utf-8", "replace")
        try:
            decoded = json.loads(text)
        except ValueError:
            raise ConnectError(f"The hub at {url} did not return JSON.") from None
        return decoded if isinstance(decoded, dict) else {}

    def post_json(self, url: str, payload: dict, *, token: str = "") -> dict:
        return send_json(url, payload, token=token, timeout=self.timeout)

    def patch_json(self, url: str, payload: dict, *, token: str = "") -> dict:
        return self.put_json(url, payload, token=token, method="PATCH")

    def put_json(self, url: str, payload: dict, *, token: str = "", method: str = "PUT") -> dict:
        request = Request(url, data=json.dumps(payload).encode("utf-8"), method=method,
                          headers=self._headers(token, {"Content-Type": "application/json"}))
        with self._open(request, None) as response:
            text = response.read().decode("utf-8", "replace")
        try:
            decoded = json.loads(text) if text.strip() else {}
        except ValueError:
            raise ConnectError(f"The hub at {url} did not return JSON.") from None
        return decoded if isinstance(decoded, dict) else {}

    def put_file(self, url: str, path: Path, size: int, *, token: str = "") -> dict:
        with open(path, "rb") as stream:
            request = Request(url, data=stream, method="PUT", headers=self._headers(
                token, {"Content-Type": "application/octet-stream", "Content-Length": str(size)}))
            with self._open(request, max(self.timeout, 300)) as response:
                response.read()
        return {}

    def put_bytes(self, url: str, data: bytes, content_type: str, *, token: str = "") -> dict:
        """PUT a small file whole (a note's picture), with its own media type."""
        request = Request(url, data=data, method="PUT", headers=self._headers(
            token, {"Content-Type": content_type, "Content-Length": str(len(data))}))
        with self._open(request, max(self.timeout, 120)) as response:
            text = response.read().decode("utf-8", "replace")
        try:
            decoded = json.loads(text) if text.strip() else {}
        except ValueError:
            decoded = {}
        return decoded if isinstance(decoded, dict) else {}

    def download(self, url: str, destination: Path, *, token: str = "") -> None:
        with self._open(Request(url, headers=self._headers(token)), max(self.timeout, 300)) as response:
            with open(destination, "wb") as stream:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    stream.write(chunk)


@contextlib.contextmanager
def _sync_lock(environment: dict[str, str] | None):
    """One sync at a time per user, whether started by the timer, a file change or a hub event."""
    directory = connect_data_directory(environment)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(directory / ".sync.lock", "a") as handle:
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _observed_at(now: datetime | None = None) -> int:
    return int((now or datetime.now(timezone.utc)).timestamp())


def contacts_snapshot(device_id: str, *, observed_at: int | None = None, loader=None,
                      inventory_probe=None) -> dict[str, object]:
    """Build the /api/hub/sync/contacts body from EDS, ordered as EDS returns it."""
    records = (loader or load_contacts)()
    items = [{field: str(getattr(record, field, "") or "") for field in CONTACT_FIELDS}
             for record in records]
    _check_item_count("contacts", items)
    # load_contacts() answers an unreachable address book and a genuinely empty
    # one identically, with (). A push is a full snapshot, so sending that empty
    # result would tombstone every contact the hub holds for this device. Only
    # an address book that is actually readable may report "no contacts".
    if not items and (inventory := (inventory_probe or inspect_eds_inventory)()) and not inventory.available:
        raise ConnectError(
            "Refusing to sync contacts: the address book is unavailable "
            f"({inventory.reason or 'no reason given'}). Sending an empty snapshot "
            "would delete the contacts already stored for this device.")
    return {
        "device_id": device_id,
        "observed_at": _observed_at() if observed_at is None else int(observed_at),
        "items": items,
    }


def notes_snapshot(device_id: str, *, observed_at: int | None = None,
                   path: Path | None = None,
                   environment: dict[str, str] | None = None) -> dict[str, object]:
    """Build the /api/hub/sync/notes body. Deleted notes are simply absent.

    The store path is always passed explicitly so opening it here can never run
    the NotesStore legacy-Markdown import: the exporter only ever reads.
    """
    store_path = path or notes_store_path(environment)
    if not store_path.exists():
        return {
            "device_id": device_id,
            "observed_at": _observed_at() if observed_at is None else int(observed_at),
            "items": [],
            "folders": [],
        }
    store = NotesStore(store_path)
    try:
        folders = store.list_folders()
        names = {folder.id: folder.name for folder in folders}
        items = []
        for note in store.list_notes():
            if note.deleted_at is not None:  # defensive; list_notes already excludes them
                continue
            items.append({
                "id": note.id,
                "title": note.title,
                "body": note.body,
                "folder_id": note.folder_id,
                "folder_name": names.get(note.folder_id or "", ""),
                "favorite": bool(note.favorite),
                "created_at": note.created_at,
                "modified_at": note.modified_at,
            })
        folder_items = [{
            "id": folder.id,
            "name": folder.name,
            "favorite": bool(folder.favorite),
            "sort_order": int(folder.sort_order),
            "expanded": bool(folder.expanded),
        } for folder in folders]
    finally:
        store.close()
    _check_item_count("notes", items)
    for item in items:
        size = len(str(item["body"]).encode("utf-8"))
        if size > MAX_NOTE_BODY_BYTES:
            raise ConnectError(
                f"Note {item['id']} has a {size} byte body; the hub accepts at most "
                f"{MAX_NOTE_BODY_BYTES}. Shorten it or move its attachments out of the body."
            )
    return {
        "device_id": device_id,
        "observed_at": _observed_at() if observed_at is None else int(observed_at),
        "items": items,
        "folders": folder_items,
    }


def notes_store_path(environment: dict[str, str] | None = None) -> Path:
    """The Notes library of the Notes install in use: the system app's, or the Flatpak's."""
    return notes_data_directory(app_environment(NOTES, environment)) / "notes.sqlite3"


def _check_item_count(service: str, items: list) -> None:
    if len(items) > MAX_ITEMS:
        raise ConnectError(
            f"This device has {len(items)} {service}; the hub accepts at most {MAX_ITEMS} "
            "per snapshot. Sync is refused rather than truncating your data."
        )


def enrol(*, code: str, hub: str, name: str = "", environment: dict[str, str] | None = None,
          transport=send_json, force: bool = False, out=None) -> DeviceIdentity:
    """Register this device once and store the returned bearer token privately."""
    stream = sys.stdout if out is None else out
    code = (code or "").strip()
    if not code:
        raise ConnectError("An enrolment code is required. Generate one in the hub's Connect page.")
    address = hub_url(hub)
    existing = load_identity(environment)
    if existing is not None and not force:
        print(f"This device is already enrolled as {existing.device_id} with {existing.hub}.",
              file=stream)
        print("Nothing was sent. Pass --force to replace the registration.", file=stream)
        return existing
    device_id = str(uuid.uuid4())
    reply = transport(f"{address}/api/hub/connect/devices",
                      {"code": code, "name": name or socket.gethostname(), "device_id": device_id})
    token = str(reply.get("token", ""))
    if not token:
        raise ConnectError("The hub accepted enrolment but returned no device token. Try again.")
    identity = DeviceIdentity(
        str(reply.get("device_id") or device_id),
        token,
        address,
        name or socket.gethostname(),
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    path = save_identity(identity, environment)
    print(f"Enrolled as {identity.device_id} with {address}.", file=stream)
    for unit in ("luma-connect-sync.path", "luma-connect-sync.timer", "luma-connect-sync-watch.service"):
        try:
            subprocess.run(["systemctl", "--user", "start", unit], capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass
    print(f"The device token is stored in {path} (mode 0600).", file=stream)
    return identity


def _summarise(payload: dict[str, object], service: str, fields: tuple[str, ...], out) -> None:
    items = payload.get("items") or []
    print(f"  {service}: {len(items)} record(s); fields: {', '.join(fields)}", file=out)
    if service == "notes":
        folders = payload.get("folders") or []
        print(f"  folders: {len(folders)}; fields: {', '.join(FOLDER_FIELDS)}", file=out)


def state_file(environment: dict[str, str] | None = None) -> Path:
    return connect_data_directory(environment) / "sync-state.json"


def _load_state(environment: dict[str, str] | None) -> dict[str, dict[str, object]]:
    try:
        data = json.loads(state_file(environment).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(state: dict[str, dict[str, object]], environment: dict[str, str] | None) -> None:
    path = state_file(environment)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".sync-state-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(state, stream, sort_keys=True)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _fingerprint(payload: dict[str, object]) -> str:
    """Content identity of a snapshot, ignoring when it was observed."""
    content = {key: value for key, value in payload.items() if key != "observed_at"}
    encoded = json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_directories(environment: dict[str, str] | None = None) -> tuple[Path, ...]:
    env = os.environ if environment is None else environment
    data = Path(env.get("XDG_DATA_HOME") or Path(env.get("HOME", str(Path.home()))) / ".local" / "share")
    from .connect_collections import source_directories as list_directories
    from .connect_photos import library_directory
    return (data / "evolution" / "addressbook" / "system", notes_store_path(environment).parent,
            *list_directories(environment), library_directory(environment))


def source_stamp(environment: dict[str, str] | None = None) -> int:
    """Newest modification among the files the snapshots are read from.

    SQLite keeps its WAL open, so this is the only cheap, reliable way to tell
    that something was written while a push was already under way.
    """
    newest = 0
    for directory in source_directories(environment):
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                newest = max(newest, entry.stat().st_mtime_ns)
            except OSError:
                continue
    return newest


def push(*, hub: str = "", dry_run: bool = False, environment: dict[str, str] | None = None,
         transport=send_json, contacts_loader=None, notes_path: Path | None = None,
         out=None, force: bool = False, settle: float = 0.0, sleep=time.sleep,
         stamp=None, http=None, observed_revision=None, collaboration_only=False) -> int:
    """Push Contacts and Notes snapshots that changed since the last success.

    ``settle`` waits for a burst of edits to finish before reading. If the
    sources are written again while a push is under way, the push repeats
    (a bounded number of times) so the last edit is never left behind until
    the next timer. Prints counts and field names only.
    """
    stamp = stamp or (lambda: source_stamp(environment))
    # Shared lists and photos need more than a JSON POST; they run with the
    # real client, or with one a test supplies, never against a stub transport.
    if http is None and transport is send_json:
        http = HubClient()
    if observed_revision is not None:
        observed_revision = _event_revision({'revision': observed_revision})
    if collaboration_only and (dry_run or http is None or transport is not send_json):
        raise ConnectError('Live collaboration requires the authenticated Hub transport.')
    for _round in range(MAX_SETTLE_ROUNDS):
        if settle > 0:
            sleep(settle)
        before = stamp()
        with _sync_lock(environment):
            _push_once(hub=hub, dry_run=dry_run, environment=environment, transport=transport,
                       contacts_loader=contacts_loader, notes_path=notes_path, out=out, force=force,
                       http=http, observed_revision=observed_revision,
                       collaboration_only=collaboration_only)
        if dry_run or settle <= 0 or stamp() == before:
            return 0
    return 0


def _push_once(*, hub: str, dry_run: bool, environment: dict[str, str] | None, transport,
               contacts_loader, notes_path: Path | None, out, force: bool, http=None,
               observed_revision=None, collaboration_only=False) -> None:
    stream = sys.stdout if out is None else out
    identity = load_identity(environment)
    if identity is None:
        raise ConnectError(
            "This device is not enrolled with a Connect hub. Run: "
            "luma-connect-sync enrol --code CODE --hub https://your-hub"
        )
    address = hub_url(hub or identity.hub)
    observed_at = _observed_at()
    state = _load_state(environment)
    scope = f"{address}|{identity.device_id}"
    notes_store = notes_path or notes_store_path(environment)
    waiting = float(state.get(f"{scope}|backoff-until", 0) or 0) - time.time()
    if waiting > 0 and not dry_run and not force:
        # The hub asked for quiet (429 or 503 with Retry-After). Nothing is
        # sent until then; the edits wait on disk and go in the next sync.
        raise ConnectError(f"The hub asked this device to wait; syncing again in {int(waiting) + 1}s.")
    # Each service stands alone: an unreachable address book must not hold
    # back notes, and the first problem is still reported once both are tried.
    snapshots, problems = [], []
    # Capture BEFORE any collaboration pull. An event arriving between that
    # pull and the old late revision read must remain pending for the next wake.
    if http is not None and not dry_run and observed_revision is None:
        try:
            observed_revision = _event_revision(http.get_json(
                f"{address}/api/hub/sync/events?after=0&wait=0", token=identity.token))
        except HubResponseError as error:
            if error.status == 401:
                raise _reauthorise(address) from None
            _note_backoff(state, scope, error, environment)
            raise
        except ConnectError as error:
            problems.append(error)
    from .connect_services import enabled_services
    enabled = enabled_services(connect_data_directory(environment))
    from .collaboration_ownership import profile_ready
    if not profile_ready(NOTES, environment):
        enabled['notes'] = False
        print('Notes: open the app to finish the native profile handoff before syncing.', file=stream)
    # Collaboration uses the same live-change owner and local file invalidation
    # as personal sync; memberships are rechecked on every operation.
    if http is not None and not dry_run and enabled.get("notes", True) and transport is send_json:
        from .collaboration import sync_notes_collaboration
        try:
            result = sync_notes_collaboration(environment=environment, http=http, store_path=notes_store)
            print(f"collaboration: {result['updated']} updated; {result['conflicts']} unsent copies retained", file=stream)
        except HubResponseError as error:
            if error.status == 401:
                problems.append(_collaboration_refusal(http, identity, address, 'Shared Notes'))
                # The Hub still accepts this registration, but refused this
                # sharing service. Never accept its rejected data or stop the
                # separately authorized personal services from syncing.
            else:
                _note_backoff(state, scope, error, environment)
                if error.status in (429, 503):
                    raise
                problems.append(error)
        except ConnectError as error:
            problems.append(error)
    if http is not None and not dry_run and transport is send_json:
        from .tasks_collaboration import sync_tasks_collaboration
        try:
            count = sync_tasks_collaboration(environment=environment, http=http)
            print(f"shared tasks: {count} documents refreshed", file=stream)
        except HubResponseError as error:
            if error.status == 401:
                problems.append(_collaboration_refusal(http, identity, address, 'Shared Tasks'))
            else:
                _note_backoff(state, scope, error, environment)
                if error.status in (429, 503):
                    raise
                problems.append(error)
        except (ConnectError, ValueError) as error:
            problems.append(ConnectError(str(error)))
    if collaboration_only:
        if dry_run or http is None or transport is not send_json:
            raise ConnectError('Live collaboration requires the authenticated Hub transport.')
        if problems:
            raise PartialSyncError(problems[0], observed_revision)
        # This checkpoint says nothing about personal Notes, Contacts or DAV.
        state[f"{scope}|collaboration-revision"] = observed_revision
        _save_state(state, environment)
        return
    # Notes go change by change to a host that can take them that way; the
    # whole-library snapshot is for hosts that cannot, and for stub transports.
    notes_delta = None
    if http is not None and not dry_run and enabled.get("notes", True) and transport is send_json:
        from .connect_notes import NotesDeltaSync
        candidate = NotesDeltaSync(address=address, token=identity.token, device_id=identity.device_id,
                                   http=http, state=state, scope=scope, store_path=notes_store,
                                   observed_at=observed_at)
        try:
            if candidate.supported():
                notes_delta = candidate
        except HubResponseError as error:
            if error.status == 401:
                raise _reauthorise(address) from None
            _note_backoff(state, scope, error, environment)
            problems.append(ConnectError(f"The hub could not say what it supports: {error}"))
        except ConnectError as error:
            problems.append(error)
    for service, fields, build in (
        ("contacts", CONTACT_FIELDS,
         lambda: contacts_snapshot(identity.device_id, observed_at=observed_at,
                                   loader=contacts_loader)),
        ("notes", NOTE_FIELDS,
         lambda: notes_snapshot(identity.device_id, observed_at=observed_at, path=notes_path,
                                environment=environment)),
    ):
        if not enabled.get(service, True):
            continue
        if service == "notes" and notes_delta is not None:
            snapshots.append((service, fields, None))
            continue
        # Contacts travel over CardDAV once the Luma address book exists; a
        # snapshot as well would count every synced card as this device's own.
        if service == "contacts" and state.get(f"{scope}|contacts-dav", {}).get("source_uid"):
            continue
        try:
            snapshots.append((service, fields, build()))
        except ConnectError as error:
            problems.append(error)
    # A Notes library that vanished is not the same as one that was emptied:
    # sending an empty snapshot would tombstone every note on the hub.
    previous_notes = state.get(f"{scope}|notes", {})
    previous_count = int(previous_notes.get("count", 0) or 0)
    previous_store = previous_notes.get("path") or str(notes_data_directory(environment) / "notes.sqlite3")
    if not notes_store.exists() and previous_count > 0:
        snapshots = [entry for entry in snapshots if entry[0] != "notes"]
        problems.append(ConnectError(
            f"Refusing to sync notes: the Notes library at {notes_store} is missing, but "
            f"{previous_count} note(s) were synced before. Notes were not sent."))
    elif previous_count > 0 and notes_path is None and previous_store != str(notes_store) and not force:
        # Notes moved between the system app and its Flatpak. The snapshot is
        # this device's whole library, so sending the other install's would
        # delete every note synced from the first one, everywhere.
        snapshots = [entry for entry in snapshots if entry[0] != "notes"]
        problems.append(ConnectError(
            f"Refusing to sync notes: this device's notes were synced from {previous_store}, but the "
            f"Notes app in use now keeps its library at {notes_store}. Sending it would replace the "
            f"{previous_count} note(s) synced before. Move your notes into the app you use, or run "
            "luma-connect-sync push --force to send that library as this device's notes."))
    if dry_run:
        print(f"Dry run against {address}; nothing was sent.", file=stream)
        print(f"  device: {identity.device_id}", file=stream)
        for service, fields, payload in snapshots:
            if payload is not None:
                _summarise(payload, service, fields, stream)
        return 0
    own_bumps = 0
    for service, _fields, payload in snapshots:
        key = f"{scope}|{service}"
        if service == "notes" and notes_delta is not None:
            before = notes_delta.revision
            try:
                print(notes_delta.run(), file=stream)
            except HubResponseError as error:
                if error.status == 401:
                    raise _reauthorise(address) from None
                _note_backoff(state, scope, error, environment)
                problems.append(ConnectError(f"The hub refused the note changes: {error}"))
                _save_state(state, environment)
                continue
            except ConnectError as error:
                problems.append(error)
                _save_state(state, environment)
                continue
            if notes_delta.revision is not None and notes_delta.revision != before:
                own_bumps += 1
            count = len(notes_delta.record["notes"])
            state[key] = {"fingerprint": state.get(key, {}).get("fingerprint", ""), "count": count,
                          "pushed_at": observed_at, "path": str(notes_store)}
            _save_state(state, environment)
            continue
        fingerprint = _fingerprint(payload)
        if not force and state.get(key, {}).get("fingerprint") == fingerprint:
            print(f"{service}: unchanged", file=stream)
            continue
        url = f"{address}/api/hub/sync/{service}"
        try:
            reply = transport(url, payload, token=identity.token)
        except HubResponseError as error:
            if error.status == 401:
                raise AuthorisationError(
                    "The hub rejected this device's token (401). It was revoked or replaced. "
                    f"Re-enrol: luma-connect-sync enrol --code CODE --hub {address} --force"
                ) from None
            _note_backoff(state, scope, error, environment)
            problems.append(ConnectError(f"The hub refused the {service} snapshot: {error}"))
            continue
        except ConnectError as error:
            problems.append(error)
            continue
        print(
            f"{service}: accepted {reply.get('accepted', len(payload['items']))}, "
            f"tombstoned {reply.get('deleted', 0)}, "
            f"last success {reply.get('last_success_at', 'unreported')}",
            file=stream,
        )
        own_bumps += 1
        state[key] = {"fingerprint": fingerprint, "count": len(payload["items"]),
                      "pushed_at": observed_at}
        if service == "notes":
            state[key]["path"] = str(notes_store)
        _save_state(state, environment)
    if http is not None:
        _sync_shared(identity=identity, address=address, http=http, state=state, scope=scope,
                     environment=environment, stream=stream, problems=problems, own_bumps=own_bumps,
                     observed_revision=observed_revision)
    if problems:
        if observed_revision is not None:
            raise PartialSyncError(problems[0], observed_revision)
        raise problems[0]


def _reauthorise(address: str) -> AuthorisationError:
    return AuthorisationError(
        "The hub rejected this device's token (401). It was revoked or replaced. "
        f"Re-enrol: luma-connect-sync enrol --code CODE --hub {address} --force")


def _collaboration_refusal(http, identity: DeviceIdentity, address: str, label: str) -> ConnectError:
    """A sharing denial is not proof that the entire registration was revoked.

    Recheck the authoritative account endpoint with the same saved bearer.
    Its 401 still requires reconnection, and an unavailable account check
    remains a retryable failure. A successful check grants no sharing access:
    the refused service stays incomplete while personal services authorize
    their own requests independently.
    """
    _account(http, identity, address)
    return ConnectError(
        f'{label} couldn’t sync with Luma Connect. '
        'This computer is still connected.')


FULL_PULL_SECONDS = 15 * 60
_UNOBSERVED_REVISION = object()


def _event_revision(reply):
    value = reply.get('revision')
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ConnectError('The Hub returned an invalid event revision.')
    return value


def _note_backoff(state: dict, scope: str, error: "HubResponseError", environment) -> None:
    """Remember a hub's request for quiet, so no process asks again before then."""
    if error.status in (429, 503):
        pause = error.retry_after or (60.0 if error.status == 429 else 30.0)
        state[f"{scope}|backoff-until"] = time.time() + min(max(pause, 5.0), 900.0)
        _save_state(state, environment)


def _remote_changed(state: dict, scope: str, revision: int, own_bumps: int) -> bool:
    """Whether someone else changed the account since this device last pulled.

    The account revision also moves for this device's own pushes; those are
    counted out, so pushing a note does not make every other service pull.
    A full pull still happens at least every quarter of an hour.
    """
    previous = state.get(f"{scope}|revision")
    if not isinstance(previous, int):
        return True
    if time.time() - float(state.get(f"{scope}|pulled-at", 0) or 0) > FULL_PULL_SECONDS:
        return True
    return revision not in (previous, previous + own_bumps)


def _sync_shared(*, identity: DeviceIdentity, address: str, http, state: dict, scope: str,
                 environment: dict[str, str] | None, stream, problems: list, own_bumps: int = 0,
                 observed_revision=_UNOBSERVED_REVISION) -> None:
    """World clocks, Weather places and Photos. Each stands alone."""
    from .connect_collections import COLLECTIONS, sync_collection
    from .connect_photos import PhotoSyncError, sync_photos
    try:
        revision = (_event_revision(http.get_json(f"{address}/api/hub/sync/events?after=0&wait=0",
                                                 token=identity.token))
                    if observed_revision is _UNOBSERVED_REVISION else observed_revision)
    except HubResponseError as error:
        if error.status == 401:
            raise _reauthorise(address) from None
        problems.append(ConnectError(f"The hub could not report changes: {error}"))
        return
    except ConnectError as error:
        problems.append(error)
        return
    remote_changed = revision is None or _remote_changed(state, scope, revision, own_bumps)
    complete = revision is not None and not problems
    from .connect_services import enabled_services
    enabled = enabled_services(connect_data_directory(environment))
    tasks = [(collection.name, lambda c=collection: sync_collection(
        c, address=address, token=identity.token, http=http, state=state, scope=scope,
        environment=environment, remote_changed=remote_changed)) for collection in COLLECTIONS]
    from .connect_calendar import CalendarSyncError, sync_calendar
    tasks.append(("calendar", lambda: sync_calendar(
        address=address, token=identity.token, http=http, state=state, scope=scope,
        environment=environment, remote_changed=remote_changed,
        data_directory=connect_data_directory(environment))))
    # Messages and call history: prairie_apps.connect_messages provides
    # sync_messages() and sync_calls() with the same keyword arguments as the
    # other services. Absent module or switched off (the default): skipped.
    try:
        from . import connect_messages
    except ImportError:
        connect_messages = None
    if connect_messages is not None:
        for name in ("messages", "calls"):
            function = getattr(connect_messages, f"sync_{name}", None)
            if function is not None:
                tasks.append((name, lambda run=function: run(
                    address=address, token=identity.token, http=http, state=state, scope=scope,
                    environment=environment, remote_changed=remote_changed)))
    from .connect_contacts import ContactsSyncError, sync_contacts
    tasks.append(("contacts", lambda: sync_contacts(
        address=address, token=identity.token, http=http, state=state, scope=scope,
        environment=environment, remote_changed=remote_changed,
        data_directory=connect_data_directory(environment))))
    from .connect_tide import TideSyncError, sync_tide
    tasks.append(("tide-sources", lambda: sync_tide(
        address=address, token=identity.token, http=http, state=state, scope=scope,
        environment=environment, remote_changed=remote_changed)))
    tasks.append(("photos", lambda: sync_photos(
        address=address, token=identity.token, device_id=identity.device_id, http=http, state=state,
        scope=scope, environment=environment, remote_changed=remote_changed)))
    for label, task in tasks:
        if not enabled.get(label, True):
            continue
        try:
            print(task(), file=stream)
        except HubResponseError as error:
            if error.status == 401:
                raise _reauthorise(address) from None
            _note_backoff(state, scope, error, environment)
            complete = False
            problems.append(ConnectError(f"The hub refused {label}: {error}"))
        except (ConnectError, PhotoSyncError, CalendarSyncError, ContactsSyncError, TideSyncError, OSError) as error:
            complete = False
            problems.append(ConnectError(f"{label}: {error}"))
        except Exception as error:  # one service failing must not stop the others
            complete = False
            problems.append(ConnectError(f"{label}: unexpected {type(error).__name__}: {getattr(error, 'message', error)}"))
        _save_state(state, environment)
    if complete:
        # The revision read before pulling, so a change made meanwhile is
        # still seen as new next time.
        state[f"{scope}|revision"] = revision
        if remote_changed:
            state[f"{scope}|pulled-at"] = time.time()
        _save_state(state, environment)


def wake_aware_sleep(seconds: float, *, clock=time.time, monotonic=time.monotonic, step=time.sleep) -> bool:
    """Sleep, but stop early if the machine was suspended meanwhile. True if it was.

    time.sleep is monotonic and does not count time asleep: a retry pending at
    suspend would still wait its whole delay after waking, while the wall clock
    shows the machine already missed that much. Waking is when a sync is due.
    """
    wall, mono = clock(), monotonic()
    while True:
        elapsed = monotonic() - mono
        if clock() - wall - elapsed > 10:
            return True
        if elapsed >= seconds:
            return False
        step(min(2.0, seconds - elapsed))


def watch(*, environment: dict[str, str] | None = None, http=None, out=None, sleep=wake_aware_sleep,
          rounds: int | None = None) -> int:
    """Wait on the hub for account changes and sync as soon as one lands."""
    stream = sys.stdout if out is None else out
    http = http or HubClient()
    identity = load_identity(environment)
    if identity is None:
        raise ConnectError("This device is not enrolled with a Connect hub.")
    address = hub_url(identity.hub)
    scope = f"{address}|{identity.device_id}"
    after = -1
    delay = 5.0
    retry_pending = False
    while rounds is None or rounds > 0:
        if rounds is not None:
            rounds -= 1
        try:
            if after < 0:
                revision = -2
                collaboration_only = False
            else:
                event = http.get_json(f"{address}/api/hub/sync/events?after={after}",
                                      token=identity.token, timeout=80)
                revision = _event_revision(event)
                # Existing Hub protocol identifies cross-account document
                # changes. They do not need a personal-library or DAV pass.
                collaboration_only = event.get('services') == ['collaboration']
            if revision != after or retry_pending:
                if after >= 0:
                    # Changes come in bursts (another device typing, or this
                    # device's own push still finishing); let them gather.
                    sleep(WATCH_COALESCE_SECONDS)
                    known = _load_state(environment).get(f"{scope}|revision")
                    if not retry_pending and not collaboration_only and isinstance(known, int) and known >= revision:
                        # The change was this device's own, or the push service
                        # already brought this device up to it: nothing to fetch.
                        after = known
                        delay = 5.0
                        continue
                push(environment=environment, http=http, out=stream,
                     observed_revision=revision if revision >= 0 else None,
                     collaboration_only=collaboration_only)
                after = max(after, revision if collaboration_only else
                            int(_load_state(environment).get(f"{scope}|revision", max(revision, 0))))
                if not collaboration_only:
                    retry_pending = False
            delay = 5.0
        except AuthorisationError:
            raise
        except PartialSyncError as error:
            if error.observed_revision is None:
                print(f"luma-connect-sync: {error}; retrying in {int(delay)}s", file=sys.stderr)
                delay = 5.0 if sleep(delay) else min(delay * 2, 300.0)
                continue
            after = max(after, error.observed_revision)
            retry_pending = True
            delay = 5.0
            print(f"luma-connect-sync: {error}; other services remain live; retry on the next event or idle poll.", file=sys.stderr)
            waiting = float(_load_state(environment).get(f"{scope}|backoff-until", 0) or 0) - time.time()
            if waiting > 0:
                sleep(waiting)  # Preserve actual Hub-wide Retry-After quiet.
        except HubResponseError as error:
            if error.status == 401:
                raise _reauthorise(address) from None
            state = _load_state(environment)
            _note_backoff(state, scope, error, environment)
            quiet = max(delay, float(state.get(f"{scope}|backoff-until", 0) or 0) - time.time())
            print(f"luma-connect-sync: {error}; retrying in {int(quiet)}s", file=sys.stderr)
            delay = 5.0 if sleep(quiet) else min(delay * 2, 300.0)
        except ConnectError as error:
            print(f"luma-connect-sync: {error}; retrying in {int(delay)}s", file=sys.stderr)
            delay = 5.0 if sleep(delay) else min(delay * 2, 300.0)
    return 0


def _account(http, identity: DeviceIdentity, address: str) -> dict:
    try:
        return http.get_json(f"{address}/api/hub/sync/account", token=identity.token)
    except HubResponseError as error:
        if error.status == 401:
            raise _reauthorise(address) from None
        raise ConnectError(f"The hub could not report this account: {error}") from None


def status(*, environment: dict[str, str] | None = None, http=None) -> dict:
    """Everything a settings screen shows, as data. Works offline, with less."""
    from .connect_services import SERVICES, enabled_services
    identity = load_identity(environment)
    enabled = enabled_services(connect_data_directory(environment))
    result: dict[str, object] = {"signed_in": identity is not None, "hub": None, "account": None,
                                 "profile": None, "device": None, "reachable": False, "devices": [],
                                 "problem": ""}
    services = []
    remote_row: dict = {}
    if identity is not None:
        address = hub_url(identity.hub)
        result.update(hub=address, device={"id": identity.device_id, "name": identity.name,
                                           "registered_at": identity.registered_at})
        try:
            account = _account(http or HubClient(), identity, address)
            result.update(reachable=True, account=account.get("account"),
                          profile=account.get("profile") if isinstance(account.get("profile"), dict) else None,
                          devices=account.get("overview", {}).get("devices", []))
            remote_row = next((row for row in result["devices"] if row.get("id") == identity.device_id), {})
        except AuthorisationError as error:
            result.update(signed_in=False, problem=str(error))
        except ConnectError as error:
            result["problem"] = str(error)
    reported = {item.get("id"): item for item in remote_row.get("services", [])}
    for service in SERVICES:
        remote = reported.get(service.id, {})
        services.append({"id": service.id, "name": service.name, "enabled": enabled[service.id],
                         "last_success_at": remote.get("last_success_at"), "item_count": remote.get("item_count"),
                         "on": service.on, "off": service.off})
    result["services"] = services
    return result


# The account profile. The hub validates and normalizes every field (a phone
# number becomes E.164) and is the only judge of what is verified; nothing here
# ever reports an email address or a number as verified on its own.
PROFILE_FIELDS = ("name", "email", "phone", "discoverable_by_phone")


def format_phone(value: str | None) -> str:
    """+14155550199 reads as +1 415-555-0199; other countries are shown as stored."""
    text = value or ""
    digits = text[2:]
    if text.startswith("+1") and len(digits) == 10 and digits.isdigit():
        return f"+1 {digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return text


def profile_lines(profile: dict) -> list[str]:
    """The profile as a person reads it in a terminal, verification stated plainly."""
    provider = ((profile.get("sign_in") or {}).get("name")) or "your sign-in provider"
    lines = [f"Name: {profile.get('name') or '(none)'}"
             + (f" (from {provider})" if profile.get("name_source") == "sign_in" else "")]
    email = profile.get("email")
    if email:
        state = f"verified by {profile.get('email_verified_by')}" if profile.get("email_verified") else "not verified"
        lines.append(f"Email: {email} ({state})")
    else:
        lines.append("Email: (none)")
    phone = profile.get("phone")
    lines.append(f"Phone number: {format_phone(phone)} ({'verified' if profile.get('phone_verified') else 'not verified'})"
                 if phone else "Phone number: (none)")
    lookup = profile.get("phone_lookup")
    lines.append("Findable by phone number: " + ("on" if lookup == "on" else
                 "on, once the number is verified" if lookup == "waiting_for_verification" else "off"))
    manage = (profile.get("sign_in") or {}).get("manage_url")
    lines.append(f"Sign-in: {provider}" + (f"; change your password at {manage}" if manage else ""))
    return lines


def profile_changes(*, name: str | None = None, reset_name: bool = False, email: str | None = None,
                    reset_email: bool = False, phone: str | None = None, remove_phone: bool = False,
                    discoverable: str | None = None, stdin_text: str | None = None) -> dict:
    """The fields a `profile set` asks to change. null restores the default."""
    changes: dict[str, object] = {}
    if stdin_text is not None:
        try:
            data = json.loads(stdin_text)
        except ValueError:
            raise ConnectError("Standard input must be a JSON object such as {\"name\": \"Ada\"}.") from None
        if not isinstance(data, dict):
            raise ConnectError("Standard input must be a JSON object such as {\"name\": \"Ada\"}.")
        changes.update(data)
    for field, value, reset in (("name", name, reset_name), ("email", email, reset_email), ("phone", phone, remove_phone)):
        if value is not None and reset:
            raise ConnectError(f"Give a new {field} or reset it, not both.")
        if value is not None:
            changes[field] = value
        elif reset:
            changes[field] = None
    if discoverable is not None:
        changes["discoverable_by_phone"] = discoverable == "on"
    unknown = sorted(set(changes) - set(PROFILE_FIELDS))
    if unknown:
        raise ConnectError(f"Unknown profile field {unknown[0]!r}. Choose from: {', '.join(PROFILE_FIELDS)}.")
    for field in ("name", "email", "phone"):
        if field in changes and changes[field] is not None and not isinstance(changes[field], str):
            raise ConnectError(f"The {field} must be text, or null to reset it.")
    if "discoverable_by_phone" in changes and not isinstance(changes["discoverable_by_phone"], bool):
        raise ConnectError("discoverable_by_phone must be true or false.")
    if not changes:
        raise ConnectError("Nothing to change. Pass --name, --email, --phone or --discoverable.")
    return changes


def _profile_call(method: str, *, changes: dict | None, environment: dict[str, str] | None, http) -> dict:
    identity = load_identity(environment)
    if identity is None:
        raise ConnectError("This device is not signed in to Luma Connect.")
    http = http or HubClient()
    address = hub_url(identity.hub)
    url = f"{address}/api/hub/sync/profile"
    try:
        reply = http.get_json(url, token=identity.token) if method == "GET" else \
            http.patch_json(url, changes or {}, token=identity.token)
    except HubResponseError as error:
        if error.status == 401:
            raise _reauthorise(address) from None
        if error.status == 404 and method == "GET":
            raise ConnectError("This hub does not keep account profiles yet.") from None
        verb = "read" if method == "GET" else "change"
        raise ConnectError(error.detail or f"The hub could not {verb} your profile: {error}") from None
    profile = reply.get("profile")
    if not isinstance(profile, dict):
        raise ConnectError("The hub answered without a profile.")
    return profile


def get_profile(*, environment: dict[str, str] | None = None, http=None) -> dict:
    """This account's profile, as the hub keeps it."""
    return _profile_call("GET", changes=None, environment=environment, http=http)


def set_profile(changes: dict, *, environment: dict[str, str] | None = None, http=None) -> dict:
    """Change some of name, email, phone and discoverable_by_phone; returns the new profile."""
    unknown = sorted(set(changes) - set(PROFILE_FIELDS))
    if unknown or not changes:
        raise ConnectError(f"Unknown profile field {unknown[0]!r}." if unknown else "Nothing to change.")
    return _profile_call("PATCH", changes=changes, environment=environment, http=http)


def set_service(service: str, on: bool, *, environment: dict[str, str] | None = None, http=None,
                out=None) -> str:
    """Turn one service on or off for this device, doing exactly what its text says."""
    from .connect_services import SERVICE_IDS, disconnect_calendar, enabled_services, forget_state, save_enabled
    stream = sys.stdout if out is None else out
    if service not in SERVICE_IDS:
        raise ConnectError(f"Unknown service {service!r}. Choose one of: {', '.join(SERVICE_IDS)}.")
    identity = load_identity(environment)
    if identity is None:
        raise ConnectError("This device is not signed in to Luma Connect.")
    http = http or HubClient()
    address = hub_url(identity.hub)
    scope = f"{address}|{identity.device_id}"
    data_directory = connect_data_directory(environment)
    enabled = enabled_services(data_directory)
    detail = ""
    with _sync_lock(environment):
        state = _load_state(environment)
        if not on and service == "calendar":
            detail = disconnect_calendar(state, scope)
        if not on and service == "contacts":
            from .connect_contacts import disconnect_contacts
            detail = disconnect_contacts(state, scope)
        try:
            http.post_json(f"{address}/api/hub/sync/services/{service}", {"enabled": on}, token=identity.token)
        except HubResponseError as error:
            if error.status == 401:
                raise _reauthorise(address) from None
            raise ConnectError(f"The hub could not change {service}: {error}") from None
        if not on:
            forget_state(state, scope, service)
            _save_state(state, environment)
            if service in ("messages", "calls"):
                # Only the local cache of other devices' history goes; nothing
                # in Luma Cloud or in this device's own Messages/Phone stores.
                from .connect_messages import forget_cloud_history
                forget_cloud_history(service, environment)
        enabled[service] = on
        save_enabled(data_directory, enabled)
    message = f"{service}: {'on' if on else 'off'}" + (f" ({detail})" if detail else "")
    print(message, file=stream)
    if on:
        push(environment=environment, http=http, out=stream)
    return message


def sign_out(*, device_id: str = "", environment: dict[str, str] | None = None, http=None, out=None) -> str:
    """Sign a device out of Luma Connect. For this device, keep every local file."""
    from .connect_services import disconnect_calendar, stop_units
    stream = sys.stdout if out is None else out
    identity = load_identity(environment)
    if identity is None:
        raise ConnectError("This device is not signed in to Luma Connect.")
    http = http or HubClient()
    address = hub_url(identity.hub)
    target = device_id or identity.device_id
    if target == identity.device_id:
        with _sync_lock(environment):
            state = _load_state(environment)
            detail = disconnect_calendar(state, f"{address}|{identity.device_id}")
            try:
                http.post_json(f"{address}/api/hub/sync/devices/{target}/revoke", {}, token=identity.token)
            except HubResponseError as error:
                if error.status not in (401, 404):
                    raise ConnectError(f"The hub could not sign this device out: {error}") from None
            stop_units()
            for path in (device_file(environment), state_file(environment)):
                path.unlink(missing_ok=True)
            # The cache of other devices' history is cloud data, not this device's.
            from .connect_messages import forget_cloud_history
            forget_cloud_history("all", environment)
        message = f"Signed out. Everything on this device stays; {detail}."
    else:
        try:
            http.post_json(f"{address}/api/hub/sync/devices/{target}/revoke", {}, token=identity.token)
        except HubResponseError as error:
            if error.status == 401:
                raise _reauthorise(address) from None
            raise ConnectError(f"The hub could not sign that device out: {error}") from None
        message = "Signed that device out. It stops syncing; nothing on it is deleted."
    print(message, file=stream)
    return message


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="luma-connect-sync",
        description="Push this device's Contacts and Notes to a Luma Connect Hub.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    enrolment = commands.add_parser("enrol", help="register this device with a hub, once")
    enrolment.add_argument("--code", required=True, help="enrolment code shown by the hub")
    enrolment.add_argument("--hub", required=True, help="hub base URL, https:// unless local")
    enrolment.add_argument("--name", default="", help="device name to show in the hub")
    enrolment.add_argument("--force", action="store_true",
                           help="replace an existing registration")
    pushing = commands.add_parser("push", help="push full Contacts and Notes snapshots")
    pushing.add_argument("--hub", default="", help="override the enrolled hub URL")
    pushing.add_argument("--dry-run", action="store_true",
                         help="report counts and field names without sending anything")
    commands.add_parser("watch", help="stay connected and sync as soon as the hub changes")
    reporting = commands.add_parser("status", help="this device, its account, devices and services")
    reporting.add_argument("--json", action="store_true", help="machine-readable output")
    switching = commands.add_parser("service", help="turn one service on or off on this device")
    switching.add_argument("name", help="calendar, notes, contacts, photos, world-clocks, weather-places, tide-sources, messages or calls")
    switching.add_argument("state", choices=("on", "off"))
    commands.add_parser("invite", help="print a one-time code that connects another device to this account")
    profiling = commands.add_parser("profile", help="your Luma account's name, email and phone number")
    profiling.add_argument("--json", action="store_true", help="machine-readable output")
    profile_commands = profiling.add_subparsers(dest="profile_command")
    changing = profile_commands.add_parser("set", help="change your name, email, phone number or phone discoverability")
    name_choice = changing.add_mutually_exclusive_group()
    name_choice.add_argument("--name", help="the name people see, 1 to 60 characters")
    name_choice.add_argument("--reset-name", action="store_true", help="go back to the name from your sign-in")
    email_choice = changing.add_mutually_exclusive_group()
    email_choice.add_argument("--email", help="an email address; it shows as not verified")
    email_choice.add_argument("--reset-email", action="store_true", help="go back to the email from your sign-in")
    phone_choice = changing.add_mutually_exclusive_group()
    phone_choice.add_argument("--phone", help="a phone number with its country code; it shows as not verified")
    phone_choice.add_argument("--remove-phone", action="store_true", help="remove your phone number")
    changing.add_argument("--discoverable", choices=("on", "off"),
                          help="whether people who have your number may find you, once it is verified")
    changing.add_argument("--stdin", action="store_true",
                          help="read the changes as a JSON object from standard input, keeping them out of the process list")
    changing.add_argument("--json", action="store_true", dest="set_json", help="machine-readable output")
    leaving = commands.add_parser("sign-out", help="sign this device (or another) out of Luma Connect")
    leaving.add_argument("--device", default="", help="another device's id; default is this device")
    pushing.add_argument("--force", action="store_true",
                         help="send every snapshot even if it has not changed")
    pushing.add_argument("--settle", type=float, default=0.0, metavar="SECONDS",
                         help="wait for edits to settle, and repeat if they continue")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if arguments.command == "enrol":
            enrol(code=arguments.code, hub=arguments.hub, name=arguments.name,
                  force=arguments.force)
            return 0
        if arguments.command == "watch":
            return watch()
        if arguments.command == "status":
            report = status()
            if arguments.json:
                print(json.dumps(report, indent=2))
            else:
                who = (report.get("account") or {}).get("name") or "unknown account"
                print(f"{'Signed in as ' + who if report['signed_in'] else 'Not signed in'}"
                      f"{' · ' + report['hub'] if report['hub'] else ''}{'' if report['reachable'] else ' · hub unreachable'}")
                if report.get("profile"):
                    for line in profile_lines(report["profile"]):
                        print(f"  {line}")
                for item in report["services"]:
                    print(f"  {item['name']}: {'on' if item['enabled'] else 'off'}")
                for device in report["devices"]:
                    print(f"  device {device['name']} ({device['id']}){' · signed out' if device.get('revoked') else ''}")
            return 0
        if arguments.command == "service":
            set_service(arguments.name, arguments.state == "on")
            return 0
        if arguments.command == "invite":
            identity = load_identity()
            if identity is None:
                raise ConnectError("This device is not signed in to Luma Connect.")
            address = hub_url(identity.hub)
            try:
                reply = HubClient().post_json(f"{address}/api/hub/sync/invite", {}, token=identity.token)
            except HubResponseError as error:
                if error.status == 401:
                    raise _reauthorise(address) from None
                raise ConnectError(f"The hub could not issue a code: {error}") from None
            print(f"{reply['code']}  (single use, expires in 10 minutes)")
            print(f"On the other device: luma-connect-sync enrol --hub {address} --code {reply['code']}")
            return 0
        if arguments.command == "profile":
            if arguments.profile_command == "set":
                changes = profile_changes(
                    name=arguments.name, reset_name=arguments.reset_name, email=arguments.email,
                    reset_email=arguments.reset_email, phone=arguments.phone, remove_phone=arguments.remove_phone,
                    discoverable=arguments.discoverable,
                    stdin_text=sys.stdin.read(16384) if arguments.stdin else None)
                profile = set_profile(changes)
            else:
                profile = get_profile()
            if arguments.json or getattr(arguments, "set_json", False):
                print(json.dumps(profile, indent=2))
            else:
                print("\n".join(profile_lines(profile)))
            return 0
        if arguments.command == "sign-out":
            sign_out(device_id=arguments.device)
            return 0
        return push(hub=arguments.hub, dry_run=arguments.dry_run, force=arguments.force,
                    settle=max(0.0, arguments.settle))
    except AuthorisationError as error:
        print(f"luma-connect-sync: {error}", file=sys.stderr)
        return EXIT_REENROL
    except ConnectError as error:
        print(f"luma-connect-sync: {error}", file=sys.stderr)
        return EXIT_RETRY
    except KeyboardInterrupt:
        return 130
