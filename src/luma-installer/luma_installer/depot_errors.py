# SPDX-License-Identifier: Apache-2.0
"""Say what went wrong with an app install, update or removal in plain words.

libflatpak reports failures as GLib errors whose text reads like
``flatpak-error-quark: Aborted due to failure (Can't update to a specific
commit without root permissions) (4)``. Nobody using Depot should have to read
that. :func:`explain` turns an error into one sentence a person can act on,
keeps the original text as the detail behind "Details", and writes it to the
journal with a stable MESSAGE_ID so Luma Vitals can show it.

Classification looks at the flatpak error code when there is one and then at
the text, because libflatpak often wraps the real cause (no space, no network,
no authorization) inside a generic "Aborted due to failure".
"""
from __future__ import annotations

from dataclasses import dataclass
import sys
import os

_lifecycle_client = None

#: journalctl MESSAGE_ID=... finds every Depot app failure; Luma Vitals reads it.
MESSAGE_ID = "c4d1a8e27b3f4f6a9e05d6b2a1f7c389"

#: Flatpak.Error codes (libflatpak flatpak-error.h), so this module needs no GI.
_FLATPAK_DOMAIN = 'flatpak-error-quark'
_OUT_OF_SPACE = 18
_PERMISSION_DENIED = 22
_AUTHENTICATION_FAILED = 23
_NOT_AUTHORIZED = 24
_UNTRUSTED = 12
_NEED_NEW_FLATPAK = 6

_VERBS = {
    'install': ("couldn't be installed", 'install'),
    'update': ("couldn't update", 'update'),
    'remove': ("couldn't be removed", 'remove'),
    'revert': ("couldn't go back to its previous version", 'change'),
}

#: journalctl MESSAGE_ID=... finds every app change Depot made on its own or was asked
#: to make (updated, went back, paused, resumed, skipped); Luma Vitals reads it.
ACTIVITY_MESSAGE_ID = "3f9b6d2a8c1e4f07b5a2d9e6c4f1a803"

#: journalctl MESSAGE_ID=... follows every Depot start: asked for, window shown,
#: slow to show, or a start that failed. Luma Vitals reads it, and it is how a
#: launch that never put a window on screen is found afterwards.
LIFECYCLE_MESSAGE_ID = "9d41c6b70f2e4a58bd3e7c19a06f5b24"

#: Mutter ends a startup sequence after 15 s; a window not shown by then is late.
LIFECYCLE_SLOW_SECONDS = 12



@dataclass(frozen=True)
class Explanation:
    message: str   # one plain sentence for the window
    detail: str    # the original error, behind "Details" and in the journal
    kind: str      # space | network | authorization | verification | flatpak | source | cancelled | other


def _parts(error) -> tuple[str, int | None, str]:
    domain = getattr(error, 'domain', None)
    code = getattr(error, 'code', None)
    message = getattr(error, 'message', None)
    text = message if isinstance(message, str) and message else str(error)
    return (domain if isinstance(domain, str) else ''), (code if isinstance(code, int) else None), text


def classify(error) -> str:
    domain, code, text = _parts(error)
    lower = text.lower()
    if domain == 'g-io-error-quark' and code == 19 or 'operation was cancelled' in lower or 'cancelled' == lower:
        return 'cancelled'
    if domain == _FLATPAK_DOMAIN and code == _OUT_OF_SPACE or any(
            words in lower for words in ('no space left', 'min-free-space', 'not enough space', 'out of space')):
        return 'space'
    if domain == _FLATPAK_DOMAIN and code in (_NOT_AUTHORIZED, _PERMISSION_DENIED, _AUTHENTICATION_FAILED) or any(
            words in lower for words in ('not authorized', 'not allowed for user', 'authorization', 'root permissions', 'permission denied',
                                         'polkit')):
        return 'authorization'
    if domain == _FLATPAK_DOMAIN and code == _UNTRUSTED or any(
            words in lower for words in ('gpg', 'signature', 'untrusted')):
        return 'verification'
    if any(words in lower for words in ('could not resolve', 'couldn’t resolve', "couldn't resolve", 'timeout',
                                        'timed out', 'network', 'connection', 'while fetching', 'server returned',
                                        'unable to connect')):
        return 'network'
    if domain == _FLATPAK_DOMAIN and code == _NEED_NEW_FLATPAK:
        return 'flatpak'
    if any(words in lower for words in ('commit-unavailable', 'no such metadata object', 'no such commit',
                                        'not found in remote', 'couldn\'t find ref', 'has been pruned')):
        return 'gone'
    if type(error).__name__ == 'SourceUnavailable':
        return 'source'
    return 'other'


def explain(error, *, name: str, action: str) -> Explanation:
    """A sentence for the window and the raw detail, for install, update or remove."""
    failed, verb = _VERBS.get(action, _VERBS['update'])
    name = name.strip() or 'This app'
    kind = classify(error)
    detail = _parts(error)[2]
    if kind == 'cancelled':
        message = 'Cancelled.'
    elif kind == 'space':
        message = f'{name} {failed} because this computer is out of space. Free some space and try again.'
    elif kind == 'authorization':
        message = f'{name} {failed} because Luma did not get permission to {verb} it. Try again, or see details.'
    elif kind == 'verification':
        message = f'{name} {failed} because its download could not be verified. Try again later, or see details.'
    elif kind == 'network':
        message = f'{name} {failed} because its app source could not be reached. Check your connection and try again.'
    elif kind == 'flatpak':
        message = f'{name} {failed} because it needs a newer version of Luma. Update Luma, then try again.'
    elif kind == 'gone':
        message = (f'{name} {failed} because its app source no longer offers that version. '
                   'It stays on the version it has now.')
    elif kind == 'source':
        # Depot's own checks already speak plainly.
        message = f'{name} {failed}. {detail}'
    else:
        message = f'{name} {failed}. Try again, or see details.'
    return Explanation(message, detail, kind)


def journal(explanation: Explanation, *, app_id: str, name: str, action: str, error=None) -> None:
    """One structured entry per failure, for Luma Vitals."""
    domain, code, _text = _parts(error) if error is not None else ('', None, '')
    line = f'Depot: {name or app_id} {action} failed ({explanation.kind}): {explanation.detail}'
    fields = {
        'MESSAGE_ID': MESSAGE_ID,
        'PRIORITY': '4' if explanation.kind == 'cancelled' else '3',
        'SYSLOG_IDENTIFIER': 'luma-depot',
        'LUMA_APPLICATION_ID': app_id,
        'LUMA_APPLICATION_NAME': name,
        'LUMA_DEPOT_ACTION': action,
        'LUMA_DEPOT_FAILURE': explanation.kind,
        'LUMA_ERROR_DOMAIN': domain,
        'LUMA_ERROR_CODE': '' if code is None else str(code),
        'LUMA_ERROR': explanation.detail,
    }
    try:
        from systemd import journal as systemd_journal
        systemd_journal.send(line, **fields)
    except Exception:  # noqa: BLE001 - python3-systemd missing or no journal socket
        print(line, file=sys.stderr)


def activity(event: str, *, app_id: str, name: str, automatic: bool, detail: str = '', **extra) -> None:
    """One structured journal entry for an app change, for Luma Vitals.

    ``event`` is one of updated, reverted, paused, resumed, skipped, held."""
    line = f'Depot: {name or app_id} {event}' + (' automatically' if automatic else '') + (f': {detail}' if detail else '')
    fields = {
        'MESSAGE_ID': ACTIVITY_MESSAGE_ID,
        'PRIORITY': '6',
        'SYSLOG_IDENTIFIER': 'luma-depot',
        'LUMA_APPLICATION_ID': app_id,
        'LUMA_APPLICATION_NAME': name,
        'LUMA_DEPOT_EVENT': event,
        'LUMA_DEPOT_AUTOMATIC': '1' if automatic else '0',
    }
    fields.update({f'LUMA_DEPOT_{key.upper()}': str(value) for key, value in extra.items()})
    try:
        from systemd import journal as systemd_journal
        systemd_journal.send(line, **fields)
    except Exception:  # noqa: BLE001 - python3-systemd missing or no journal socket
        print(line, file=sys.stderr)


def lifecycle(event: str, *, detail: str = '', seconds: float | None = None, **extra) -> None:
    """One structured journal entry per Depot start, for Luma Vitals.

    ``event`` is one of asked (a launch arrived), shown (a window is on screen),
    slow (no window yet), degraded (a service Depot shows is unreachable) or
    failed (the window could not be built).
    """
    if os.path.isfile('/.flatpak-info'):
        # The installed, signed Depot already has this authenticated transport.
        # Keep journald ownership on the host; never expose its socket to apps.
        global _lifecycle_client
        try:
            from luma_depot.host_client import Client
            if _lifecycle_client is None:
                _lifecycle_client = Client()
            if set(extra) - {'window'}:
                raise ValueError('Unsupported Depot lifecycle fields.')
            values = {'event':event, 'detail':detail,
                      'seconds':0.0 if seconds is None else float(seconds),
                      'window':extra.get('window', '')}
            def finished(result):
                if not result.ok:
                    print(f'Depot lifecycle could not reach its host: {result.error}', file=sys.stderr)
            _lifecycle_client.call('Lifecycle', values, finished)
        except Exception as error:
            print(f'Depot lifecycle could not reach its host: {error}', file=sys.stderr)
        return
    words = {
        'asked': 'Depot was asked to open',
        'shown': 'Depot put its window on screen',
        'slow': 'Depot has no window on screen yet',
        'failed': 'Depot could not open its window',
        'degraded': 'Depot opened without one of its services',
    }
    line = f"{words.get(event, f'Depot: {event}')}"
    if seconds is not None:
        line += f' after {seconds:.1f}s'
    if detail:
        line += f': {detail}'
    fields = {
        'MESSAGE_ID': LIFECYCLE_MESSAGE_ID,
        'PRIORITY': {'failed': '3', 'slow': '4', 'degraded': '4'}.get(event, '6'),
        'SYSLOG_IDENTIFIER': 'luma-depot',
        'LUMA_DEPOT_LIFECYCLE': event,
        'LUMA_DEPOT_DETAIL': detail,
        'LUMA_DEPOT_SECONDS': '' if seconds is None else f'{seconds:.3f}',
    }
    fields.update({f'LUMA_DEPOT_{key.upper()}': str(value) for key, value in extra.items()})
    try:
        from systemd import journal as systemd_journal
        systemd_journal.send(line, **fields)
    except Exception:  # noqa: BLE001 - python3-systemd missing or no journal socket
        print(line, file=sys.stderr)
