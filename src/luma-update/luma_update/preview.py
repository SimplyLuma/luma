# SPDX-License-Identifier: Apache-2.0
"""Preview channel enrollment (ADR-030 sections 4 and 6).

Nightly and beta are served from ``https://dl.simplyluma.com/os/preview/<credential>/repo``.
Beta and nightly are also public at the stable repository URL; a credential is
only kept for computers enrolled before that (Hub or staff media).

Every channel uses the image's one ``luma`` remote (``/etc/ostree/remotes.d/luma.conf``);
enrollment changes where it points rather than adding a second remote. This
module:

* exchanges the Connect device token for a credential (the token is used once
  and never stored by the update agent);
* stores the credential root-only in ``/etc/luma/update-preview-credential``
  (0600);
* points the remote at the preview repository URL, which contains the
  credential, through a root-only mirror list, in one of two layouts:

  - **mirror-list layout** (images from 2026-09 on): the image's remote always
    says ``url=mirrorlist=file:///etc/luma/update-mirrorlist``, and that root-only
    file holds the public repository URL. Enrollment and leaving rewrite only
    that file; the remote file is never touched.
  - **url layout** (the first images): the remote says the public ``url=https://…``.
    Enrollment writes ``/etc/luma/update-preview-mirrorlist`` (0600) and changes
    only the remote's ``url`` line to name it, leaving every other line (signature
    settings, collection id, comments) untouched; leaving restores the public URL.

  The remote file itself holds no secret and stays world-readable in both:
  libostree parses every remote file whenever any process loads the sysroot,
  so a root-only remote file breaks unprivileged ``rpm-ostree status``
  (observed on Fedora 44, ostree 2025.7), and a credential in it would be
  readable by every account;
* on leaving, points back at the public repository and removes the credential,
  then asks Hub to revoke it only when Hub issued it to this device alone (see
  ``revocable``).

Two kinds of enrollment record exist. EnrollPreview writes ``"source": "hub"``
with the ``hub_credential_id`` Hub returned for this device. Staff install
media (Atlas) write ``"source": "staff-media"``: one credential shared by every
computer installed from the same batch, issued and revoked only by staff on the
download server. Revoking a shared credential from one computer would cut off
the whole batch, so the agent revokes only records that name a per-device Hub
credential id and never blocks leaving on the answer.

Since 2026-09-16 every channel is public (ADR-030 section 4): no enrollment is
needed to follow beta or nightly. Enrollment and staff-media records keep
working for computers that have them.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import time

from .config import PREVIEW_CHANNELS, Paths, Settings
from .http import Http, HttpError
from .state import atomic_write

__all__ = ("PreviewError", "request_credential", "install_credential", "remove_credential", "record_source",
           "read_credential", "revocable", "revoke_credential", "set_remote_url", "remote_url",
           "mirrorlist_layout", "pulls_from_preview", "set_aside", "restore")

log = logging.getLogger("luma-update")

SOURCE_HUB = "hub"
SOURCE_STAFF_MEDIA = "staff-media"
#: What ``PreviewSource`` may say. ``unknown`` is a record written before sources existed.
SOURCES = (SOURCE_HUB, SOURCE_STAFF_MEDIA)

_CREDENTIAL = re.compile(r"[A-Za-z0-9_-]{16,256}\Z")
_CREDENTIAL_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_TOKEN = re.compile(r"[\x21-\x7e]{8,4096}\Z")
_SECTION = re.compile(r'\s*\[\s*remote\s+"([^"]+)"\s*\]\s*\Z')
_OTHER_SECTION = re.compile(r"\s*\[.*\]\s*\Z")
_URL = re.compile(r"\s*url\s*=")


class PreviewError(Exception):
    error_class = "preview"


def request_credential(http: Http, settings: Settings, channel: str, arch: str,
                       connect_token: str) -> tuple[str, list[str], str | None]:
    """Returns the credential, the preview channels Hub says this device may follow,
    and Hub's id for this device's credential (None when Hub sent no usable id)."""
    if channel not in PREVIEW_CHANNELS:
        raise PreviewError("early updates are the beta and nightly channels")
    if not isinstance(connect_token, str) or not _TOKEN.match(connect_token):
        raise PreviewError("this computer's Luma Connect enrollment is missing or unreadable")
    try:
        status, reply = http.request_json("POST", settings.preview_credentials_url,
                                          {"channel": channel, "arch": arch},
                                          headers={"Authorization": f"Bearer {connect_token}"})
    except HttpError as error:
        raise PreviewError(f"Luma Hub could not be reached: {error}") from None
    if status == 401:
        # The device token is unknown, expired or signed out at Hub: signing in
        # to Luma Connect again on this computer is the way forward.
        error = PreviewError("Luma Connect on this computer is signed out. Sign in to Luma Connect again, "
                             "then turn on early updates")
        error.error_class = "sign-in-required"
        raise error
    if status == 403:
        error = PreviewError(f"this Luma account can't get early updates on the {channel} channel")
        error.error_class = "not-entitled"
        raise error
    if status == 429:
        raise PreviewError("Luma Hub has had too many requests from this computer. Wait a few minutes "
                           "and try again")
    if status not in (200, 201):
        raise PreviewError(f"Luma Hub could not turn on early updates right now (HTTP {status}). "
                           "Try again later")
    credential = reply.get("credential")
    if not isinstance(credential, str) or not _CREDENTIAL.match(credential):
        raise PreviewError("Luma Hub returned an unusable preview credential")
    channels = reply.get("channels")
    channels = [c for c in channels if c in PREVIEW_CHANNELS] if isinstance(channels, list) else [channel]
    if channel not in channels:
        channels.append(channel)
    credential_id = reply.get("credential_id")
    if not isinstance(credential_id, str) or not _CREDENTIAL_ID.match(credential_id):
        log.warning("Luma Hub sent no credential id with this computer's preview credential; "
                    "leaving the channel will not revoke it at Hub")
        credential_id = None
    return credential, channels, credential_id


def _remote_file(paths: Paths, settings: Settings) -> Path:
    """The remotes.d file that defines the Luma remote."""
    directory = paths.ostree_remotes_dir
    if directory.is_dir():
        for item in sorted(directory.glob("*.conf")):
            try:
                lines = item.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            if any((m := _SECTION.match(line)) and m.group(1) == settings.stable_remote for line in lines):
                return item
    raise PreviewError(f"the image's {settings.stable_remote} OSTree remote is not configured")


def remote_url(paths: Paths, settings: Settings) -> str | None:
    """The Luma remote's url as libostree reads it: GKeyFile merges repeated
    groups and the last value of a key wins."""
    lines = _remote_file(paths, settings).read_text(encoding="utf-8").splitlines()
    inside = False
    found = None
    for line in lines:
        match = _SECTION.match(line)
        if match or _OTHER_SECTION.match(line):
            inside = bool(match and match.group(1) == settings.stable_remote)
            continue
        if inside and _URL.match(line):
            found = line.split("=", 1)[1].strip()
    return found


def mirrorlist_layout(paths: Paths, settings: Settings) -> bool:
    """Whether the image's remote always reads the agent's root-only mirror list."""
    return remote_url(paths, settings) == f"mirrorlist=file://{paths.update_mirrorlist}"


def set_remote_url(paths: Paths, settings: Settings, url: str) -> None:
    """Replace the Luma remote's url line in place; every other line is kept.

    The value may not contain control characters (a carriage return or line
    feed would add keys or groups). The file's line endings are kept. In a file
    that repeats the Luma group, the url is written once, in the first."""
    if not url or any(ord(c) < 32 or ord(c) == 127 for c in url):
        raise PreviewError("invalid remote URL")
    path = _remote_file(paths, settings)
    text = path.read_bytes().decode("utf-8")  # not read_text: universal newlines would hide CRLF
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    out, inside, done = [], False, False
    for line in lines:
        match = _SECTION.match(line)
        if match or _OTHER_SECTION.match(line):
            if inside and not done:
                out.append(f"url={url}")
                done = True
            inside = bool(match and match.group(1) == settings.stable_remote)
            out.append(line)
            continue
        if inside and _URL.match(line):
            if not done:
                out.append(f"url={url}")
                done = True
            continue
        out.append(line)
    if inside and not done:
        out.append(f"url={url}")
    atomic_write(path, (newline.join(out) + newline).encode("utf-8"), 0o644)


def record_source(record: dict | None) -> str:
    """Where an installed record came from: ``hub``, ``staff-media``,
    ``unknown`` (a record from before sources were written), or '' without one."""
    if not isinstance(record, dict):
        return ""
    source = record.get("source")
    return source if source in SOURCES else "unknown"


def install_credential(paths: Paths, settings: Settings, credential: str, channel: str,
                       channels: list[str] | None = None, credential_id: str | None = None) -> None:
    """Install a credential Hub issued to this device (EnrollPreview)."""
    if not _CREDENTIAL.match(credential):
        raise PreviewError("unusable preview credential")
    record = {"credential": credential, "channel": channel, "channels": list(channels or [channel]),
              "issued_at": int(time.time()), "source": SOURCE_HUB}
    if credential_id is not None and _CREDENTIAL_ID.match(credential_id):
        record["hub_credential_id"] = credential_id
    url = settings.preview_repo_url.format(credential=credential)
    mirrorlist = mirrorlist_layout(paths, settings)  # also fails early when there is no Luma remote
    atomic_write(paths.preview_credential, (json.dumps(record) + "\n").encode("utf-8"), 0o600)
    if mirrorlist:
        atomic_write(paths.update_mirrorlist, (url + "\n").encode("utf-8"), 0o600)
        return
    atomic_write(paths.preview_mirrorlist, (url + "\n").encode("utf-8"), 0o600)
    set_remote_url(paths, settings, f"mirrorlist=file://{paths.preview_mirrorlist}")


def read_credential(paths: Paths) -> dict | None:
    try:
        stat = paths.preview_credential.stat()
        if stat.st_mode & 0o077:
            os.chmod(paths.preview_credential, 0o600)
        value = json.loads(paths.preview_credential.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(value, dict) or not isinstance(value.get("credential"), str) \
            or not _CREDENTIAL.match(value["credential"]):
        return None
    return value


def revocable(record: dict | None) -> bool:
    """Whether leaving should ask Hub to revoke this record's credential.

    Only a credential Hub issued to this one device: ``source`` is ``hub`` and the
    record names Hub's per-device credential id. A staff-media record carries a
    per-batch credential that only staff revoke; a record with no source, an
    unknown source or no id is treated the same way, because revoking a
    credential that other computers share would cut them off too."""
    return (isinstance(record, dict) and record.get("source") == SOURCE_HUB
            and isinstance(record.get("hub_credential_id"), str)
            and bool(_CREDENTIAL_ID.match(record["hub_credential_id"]))
            and isinstance(record.get("credential"), str) and bool(_CREDENTIAL.match(record["credential"])))


def revoke_credential(http: Http, settings: Settings, record: dict | None) -> str:
    """Tell Hub a per-device credential is no longer used, after it was removed locally.

    Never raises: the computer has already left the channel, and a Hub that is
    unreachable, not built yet or refusing only means the credential stays valid
    at Hub until it expires. Returns what happened, for logs and tests:
    ``skipped``, ``revoked``, ``not-found``, ``refused`` or ``unreachable``. Nothing logged here
    contains the credential or its id."""
    if not revocable(record):
        source = record.get("source") if isinstance(record, dict) else None
        if source == SOURCE_STAFF_MEDIA:
            log.info("left early updates; the preview credential came with the staff install media and is "
                     "shared by its batch, so it is not revoked at Luma Hub (staff revoke batch credentials)")
        else:
            log.info("left early updates; the preview credential has no per-device Luma Hub id, "
                     "so it is not revoked at Luma Hub")
        return "skipped"
    try:
        status, _reply = http.request_json("DELETE", settings.preview_credentials_url + "/current", None,
                                           headers={"Authorization": f"Bearer {record['credential']}"})
    except HttpError as error:
        log.warning("left early updates, but Luma Hub could not be reached to revoke this computer's "
                    "preview credential: %s", error)
        return "unreachable"
    except Exception as error:  # never let revocation undo or block leaving
        log.warning("left early updates, but revoking this computer's preview credential at Luma Hub "
                    "failed: %s", type(error).__name__)
        return "unreachable"
    if status in (200, 202, 204):
        log.info("left early updates; Luma Hub revoked this computer's preview credential (HTTP %s)", status)
        return "revoked"
    if status in (404, 410):
        # Hub does not know the credential, or does not serve revocation (yet).
        log.warning("left early updates; Luma Hub has nothing to revoke for this computer's preview "
                    "credential (HTTP %s)", status)
        return "not-found"
    log.warning("left early updates, but Luma Hub did not revoke this computer's preview credential "
                "(HTTP %s)", status)
    return "refused"


def pulls_from_preview(paths: Paths, settings: Settings) -> bool:
    """Whether the Luma remote still pulls from a preview repository (a
    credential in the mirror list, or the first images' preview mirror list)."""
    try:
        url = remote_url(paths, settings)
    except PreviewError:
        return False
    if url == f"mirrorlist=file://{paths.preview_mirrorlist}":
        return True
    if url != f"mirrorlist=file://{paths.update_mirrorlist}":
        return False
    prefix = settings.preview_repo_url.split("{credential}", 1)[0]
    try:
        lines = paths.update_mirrorlist.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    return any(line.strip().startswith(prefix) for line in lines if line.strip() and not line.startswith("#"))


def set_aside(paths: Paths, settings: Settings) -> dict:
    """Point the Luma remote at the public repository and remove the local
    record, keeping everything needed to put it back (``restore``). Hub is not
    told: nothing is revoked."""
    saved = {}
    for path in (_remote_file(paths, settings), paths.update_mirrorlist, paths.preview_mirrorlist,
                 paths.preview_credential):
        try:
            saved[path] = (path.read_bytes(), path.stat().st_mode & 0o777)
        except FileNotFoundError:
            saved[path] = None
    remove_credential(paths, settings)
    return saved


def restore(saved: dict) -> None:
    for path, value in saved.items():
        if value is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        else:
            atomic_write(path, value[0], value[1])


def remove_credential(paths: Paths, settings: Settings) -> None:
    try:
        if mirrorlist_layout(paths, settings):
            atomic_write(paths.update_mirrorlist, (settings.stable_repo_url + "\n").encode("utf-8"), 0o600)
        elif remote_url(paths, settings) == f"mirrorlist=file://{paths.preview_mirrorlist}":
            set_remote_url(paths, settings, settings.stable_repo_url)
    except PreviewError:
        pass
    for path in (paths.preview_mirrorlist, paths.preview_credential):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
