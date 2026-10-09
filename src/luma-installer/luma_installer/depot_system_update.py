# SPDX-License-Identifier: Apache-2.0
"""What Depot reads from Luma's update agent (ADR-030, section 6).

``luma-updated`` owns the operating system update: checking the signed graph,
downloading, staging, restarting when asked, rolling back. Depot never does any
of that itself. It reads the agent's state -- over D-Bus
(``org.projectluma.Update1`` at ``/org/projectluma/Update1``), or from
``luma-update status --json`` when the bus is not reachable -- and asks it to
act when a person presses a button.

This module is the reading only, with no GLib, so it can be tested without a
bus. Property names are the D-Bus contract; the JSON form uses the same names
in snake_case. A property the agent does not publish reads as unknown, never as
a made-up value.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time

BUS_NAME = 'org.projectluma.Update1'
OBJECT_PATH = '/org/projectluma/Update1'
INTERFACE = 'org.projectluma.Update1'
CHANNELS = ('stable', 'beta', 'nightly')

#: Agent states Depot draws distinctly. Anything else is shown by its fields.
IDLE, CHECKING, AVAILABLE, DOWNLOADING, STAGED, RESTART_REQUIRED, ERROR, BARRIER_BLOCKED = (
    'idle', 'checking', 'available', 'downloading', 'staged', 'restart-required', 'error', 'barrier-blocked')
#: Published by the agent when the bus cannot be reached (docs/os/luma-update.md).
STATUS_FILE = '/run/luma-update/status.json'

PROPERTIES = ('State', 'Channel', 'BootedVersion', 'BootedCommit', 'StagedVersion',
              'AvailableVersion', 'AvailableSummary', 'NotesUrl', 'Importance', 'DownloadBytes',
              'Progress', 'LastCheck', 'LastError', 'Metered', 'RollbackAvailable', 'PreviewEnrolled',
              'AvailableChannels', 'StagedCommit', 'AvailableCommit', 'LastErrorClass', 'Managed',
              'RolledBackVersion', 'RolledBackAt', 'BootedDeadendReason', 'WaitingVersion',
              'AutomaticDownload', 'IgnoredVersion', 'RepositoryUrl', 'GraphUrl', 'SignatureVerified',
              'SigningKeyId', 'LastCheckReason', 'LastCheckAttempt', 'Adoptable', 'UnmanagedReason',
              'PreviewSource', 'BootedName', 'StagedName', 'AvailableName', 'WaitingName',
              'RolledBackName', 'IgnoredName', 'RemovedPackages', 'KeptPackages')

#: What each channel is called and is for, in the one place both Depot and the
#: installer read it. "Official" is the name a person sees; "stable" is the
#: agent's and the graph's word for the same thing, and never appears in the UI.
CHANNEL_NAMES = {'stable': 'Official', 'beta': 'Beta', 'nightly': 'Nightly'}
CHANNEL_DETAILS = {
    'stable': 'The version Luma recommends for everyone.',
    'beta': 'The next release, a few weeks early. Mostly finished, occasionally rough.',
    'nightly': 'Every build that passes its checks. For people working on Luma itself.',
}
#: How often each channel has something new, and how much testing it has had.
#: A person choosing a channel is choosing a pace, so say the pace.
CHANNEL_PACE = {
    'stable': ('New every few weeks', 'tested longest'),
    'beta': ('New every week or two', 'tested but not finished'),
    'nightly': ('New most days', 'automatic checks only'),
}
#: The same promise on every channel, and the reason a person can afford to try
#: one: a release that does not start properly puts itself back.
ROLLBACK_PROMISE = ('If an update does not start properly, Luma goes back to the version you had '
                    'before it, by itself.')
#: What a channel needs: nothing. Every channel is public (ADR-030 section 4,
#: 2026-09-16); kept as a table so a future requirement has one place to go.
CHANNEL_REQUIREMENT = {'stable': '', 'beta': '', 'nightly': ''}

#: Who set up early updates, as PreviewSource says, in plain words.
PREVIEW_SOURCE_TEXT = {
    'hub': 'Set up with the Luma account signed in to Luma Connect on this computer.',
    'staff-media': 'Set up by the Luma team when this computer was installed.',
    'unknown': 'Set up on this computer before Luma recorded how.',
}


def preview_source_text(source: str) -> str:
    return PREVIEW_SOURCE_TEXT.get(source or '', '')


#: What Depot says while the agent works on a channel change it asked for.
def channel_progress_text(method: str, channel: str) -> str:
    name = channel_name(channel)
    return {
        'EnrollPreview': f'Turning on {name} updates\u2026',
        'SetChannel': f'Switching to {name}\u2026 Luma then checks for its newest release.',
        'SetChannelNow': f'Switching to {name} now\u2026 Luma is preparing its newest release.',
        'AdoptChannel': f'Starting to follow {name}\u2026 Luma is preparing its newest release. This can take a while.',
        'LeavePreview': 'Leaving early updates\u2026',
    }.get(method, '')


def channel_consequence(channel: str, current: str, *, adopting: bool = False) -> str:
    """What choosing this channel would do, before it happens.

    Never a surprise and never a threat: the same four facts every time --
    what this computer would follow, that nothing of the person's changes,
    when it takes effect, and how to undo it.
    """
    name = channel_name(channel)
    if adopting:
        return (f'Luma would move this computer onto Luma {name}: it prepares the newest {name} release '
                f'as this computer\u2019s system. Packages you replaced in the current system are replaced '
                f'by {name}\u2019s own versions; packages you added stay. Your files, apps and settings stay '
                f'as they are. Nothing changes until you restart, and the system you are running now is '
                f'kept, so you can go back to it from the boot menu or from here.')
    if channel == current:
        return 'This computer already follows this channel.'
    if current and channel == 'stable':
        return (f'This computer would go back to Luma Official. It waits until Official has something '
                f'newer than you have, unless you ask to switch straight away. Your files, apps and '
                f'settings stay as they are, and the version you are running now is kept.')
    return (f'This computer would start following Luma {name} and move to it with the next update. '
            f'Your files, apps and settings stay as they are. Nothing changes until you restart, and '
            f'the version you are running now is kept, so you can go back to it.')


#: Why a computer follows no Luma channel, said plainly, with what it would
#: take. Never a bare "unavailable": each of these is one line of what is true
#: and one line of what would change it.
UNMANAGED_REASONS = {
    'other-origin': (
        'This computer\u2019s system was installed from somewhere other than a Luma update channel, '
        'so Luma has not been updating it.',
        'It can start following one now.'),
    'no-remote': (
        'This computer does not have Luma\u2019s update source set up, so it cannot follow a channel yet.',
        'Updating luma-update brings it (the luma-os-remote package); then any channel is one choice away.'),
    'no-key': (
        'This computer has Luma\u2019s update source but not the key Luma signs releases with, so '
        'nothing from it could be verified.',
        'Updating luma-update brings the key (the luma-os-remote package). Luma installs nothing it '
        'cannot verify.'),
    'no-image-system': (
        'This computer\u2019s system is not one Luma can replace as a whole, so Luma updates apps '
        'here but not the system itself.',
        'A computer installed from a Luma image updates its system here too.'),
    'busy': (
        'This computer does not follow a Luma update channel yet, and another change is already '
        'waiting for a restart.',
        'Restart to finish that change, then it can start following a channel.'),
}


def unmanaged_text(reason: str) -> tuple[str, str]:
    return UNMANAGED_REASONS.get(reason or '', UNMANAGED_REASONS['no-remote'])


def channel_name(channel: str) -> str:
    return CHANNEL_NAMES.get(channel, channel.capitalize() if channel else '')


def host_of(url: str) -> str:
    """The host a URL names, for saying where an update is hosted.

    Nothing is hard-coded: the agent reads the real remote from the image, so
    moving the downloads to another host changes this line by itself."""
    text = _text(url, 2048)
    if '://' not in text:
        return ''
    host = text.split('://', 1)[1].split('/', 1)[0].split('@')[-1]
    return host if host and ' ' not in host else ''


def snake(name: str) -> str:
    return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()


@dataclass(frozen=True)
class SystemUpdate:
    service: bool
    state: str = ''
    channel: str = ''
    booted_version: str = ''
    booted_commit: str = ''
    staged_version: str = ''
    available_version: str = ''
    available_summary: str = ''
    notes_url: str = ''
    importance: str = 'normal'
    download_bytes: int = 0
    progress: float = 0.0
    last_check: int = 0
    last_error: str = ''
    metered: bool = False
    rollback_available: bool = False
    preview_enrolled: bool = False
    available_channels: tuple[str, ...] = ()
    #: False when the booted system does not follow a Luma channel; the agent changes nothing then.
    managed: bool = True
    rolled_back_version: str = ''
    booted_deadend_reason: str = ''
    waiting_version: str = ''
    #: The person's own choices, and what the agent will admit to (luma-update 1.0.0-1.luma.6).
    automatic_download: bool = True
    ignored_version: str = ''
    repository_url: str = ''
    graph_url: str = ''
    signature_verified: bool = False
    signing_key_id: str = ''
    last_check_reason: str = ''
    last_check_attempt: int = 0
    adoptable: bool = False
    unmanaged_reason: str = ''
    #: Who set up early updates: hub, staff, staff-media, unknown, or '' (luma-update 1.0.0-1.luma.10).
    preview_source: str = ''
    #: What people call each version above ("Luma (Prairie, Beta 1)"). Published by
    #: luma-update 1.0.0-1.luma.12; for an older agent Depot names them by the same rules.
    #: The *_version fields stay the machine versions and are what Depot compares.
    booted_name: str = ''
    staged_name: str = ''
    available_name: str = ''
    waiting_name: str = ''
    rolled_back_name: str = ''
    ignored_name: str = ''
    #: Packages this computer had added that the staged release now ships itself
    #: (the update removes the added copy), and ones newer than the release's own
    #: copy (kept; the update waits). Published by luma-update 1.luma.17.
    removed_packages: tuple[str, ...] = ()
    kept_packages: tuple[str, ...] = ()
    #: How the state was read: dbus, file, cli, or '' when there is no agent.
    source: str = ''

    @property
    def staged(self) -> bool:
        # State is authoritative. Older agents also publish StagedVersion for
        # live-applied package overlays while idle: those are already running,
        # so a residual version must not turn into an OS update/restart card.
        return self.state == STAGED

    @property
    def downloading(self) -> bool:
        return self.state == DOWNLOADING

    @property
    def available(self) -> bool:
        return bool(self.available_version) and not self.staged

    @property
    def restart_required(self) -> bool:
        return self.state == RESTART_REQUIRED

    @property
    def barrier_blocked(self) -> bool:
        """A required stepping-stone release (``waiting_version``) did not install last
        time and the agent waits before trying it again. Never "up to date"."""
        return self.state == BARRIER_BLOCKED

    @property
    def update_ready(self) -> bool:
        """Whether the Updates badge should count the system.

        A version the person ignored stops counting: they said no to it, and a
        badge that keeps insisting is the nagging Luma promised not to do. It is
        still shown in Depot's Updates, with a way back."""
        if self.ignored and not self.restart_required:
            return False
        return self.service and self.managed and (self.staged or self.available or self.restart_required)

    @property
    def security(self) -> bool:
        return self.importance == 'security'

    @property
    def offered_version(self) -> str:
        """The one version the person is being offered right now, if any."""
        return self.staged_version or self.available_version

    @property
    def offered_name(self) -> str:
        """What the version in ``offered_version`` is called."""
        return self.staged_name if self.staged_version else self.available_name

    @property
    def ignored(self) -> bool:
        """Whether what is offered now is the version the person told Luma to be quiet about."""
        return bool(self.ignored_version) and self.ignored_version == self.offered_version

    @property
    def host(self) -> str:
        return host_of(self.repository_url)

    @property
    def nightly_allowed(self) -> bool:
        """Whether this computer may follow nightly without enrolling again."""
        return 'nightly' in self.available_channels or self.channel == 'nightly'

    @property
    def enrolled_by_staff(self) -> bool:
        return self.preview_enrolled and self.preview_source == 'staff-media'

    def checked_ago(self, now: float | None = None) -> str:
        if not self.last_check:
            return ''
        seconds = max(0, int((now if now is not None else time.time()) - self.last_check))
        if seconds < 90:
            return 'just now'
        if seconds < 3600:
            return f'{seconds // 60} minutes ago'
        if seconds < 2 * 3600:
            return 'an hour ago'
        if seconds < 86400:
            return f'{seconds // 3600} hours ago'
        days = seconds // 86400
        return 'yesterday' if days == 1 else f'{days} days ago'


NOT_INSTALLED = SystemUpdate(service=False)


def _text(value, maximum=512):
    return value[:maximum].strip() if isinstance(value, str) else ''


def _https(value):
    text = _text(value, 2048)
    return text if text.startswith('https://') and not any(c.isspace() for c in text) else ''


def from_values(values: dict, source: str) -> SystemUpdate:
    """Build the state from property values keyed by D-Bus name or snake_case."""
    def get(name):
        if name in values:
            return values[name]
        return values.get(snake(name))

    progress = get('Progress')
    if isinstance(progress, bool) or not isinstance(progress, (int, float)):
        progress = 0.0
    progress = float(progress)
    if progress > 1.0:
        # An agent reporting percent rather than a fraction still draws right.
        progress = progress / 100.0 if progress <= 100 else 0.0
    last_check = get('LastCheck')
    if isinstance(last_check, str):
        try:
            from datetime import datetime
            last_check = int(datetime.fromisoformat(last_check.replace('Z', '+00:00')).timestamp())
        except ValueError:
            last_check = 0
    if isinstance(last_check, bool) or not isinstance(last_check, int) or last_check < 0:
        last_check = 0
    download = get('DownloadBytes')
    if isinstance(download, bool) or not isinstance(download, int) or download < 0:
        download = 0
    attempt = get('LastCheckAttempt')
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
        attempt = 0
    automatic = get('AutomaticDownload')
    channel = _text(get('Channel'), 32)
    managed = get('Managed')
    channels = get('AvailableChannels')
    enrolled = get('PreviewEnrolled')
    if isinstance(enrolled, str):
        enrolled = enrolled in CHANNELS[1:]
    importance = _text(get('Importance'), 16)
    booted_version = _text(get('BootedVersion'), 64)
    staged_version = _text(get('StagedVersion'), 64)
    available_version = _text(get('AvailableVersion'), 64)
    waiting_version = _text(get('WaitingVersion'), 64)
    rolled_back_version = _text(get('RolledBackVersion'), 64)
    ignored_version = _text(get('IgnoredVersion'), 64)

    def named(prop: str, version: str) -> str:
        # An agent too old to publish names: the same fallbacks, from the booted os-release.
        if not version:
            return ''
        return clean_name(get(prop)) or display_name(version)

    return SystemUpdate(
        service=True, state=_text(get('State'), 32).lower(), channel=channel,
        booted_version=booted_version, booted_commit=_text(get('BootedCommit'), 128),
        staged_version=staged_version, available_version=available_version,
        available_summary=_text(get('AvailableSummary'), 1024), notes_url=_https(get('NotesUrl')),
        importance='security' if importance == 'security' else 'normal', download_bytes=download,
        progress=max(0.0, min(1.0, progress)), last_check=last_check,
        last_error=_text(get('LastError'), 512), metered=get('Metered') is True,
        rollback_available=get('RollbackAvailable') is True,
        preview_enrolled=enrolled is True,
        available_channels=tuple(c for c in channels if c in CHANNELS) if isinstance(channels, (list, tuple)) else (),
        managed=managed is not False,
        rolled_back_version=rolled_back_version,
        booted_deadend_reason=_text(get('BootedDeadendReason'), 512),
        waiting_version=waiting_version,
        # An agent too old to publish these reads as the safe answer: automatic
        # download on (the package default), nothing ignored, nothing claimed
        # about a signature, and no host named rather than a guessed one.
        automatic_download=automatic is not False,
        ignored_version=ignored_version,
        repository_url=_https(get('RepositoryUrl')),
        graph_url=_https(get('GraphUrl')),
        signature_verified=get('SignatureVerified') is True,
        signing_key_id=_text(get('SigningKeyId'), 64),
        last_check_reason=_text(get('LastCheckReason'), 64),
        last_check_attempt=attempt,
        adoptable=get('Adoptable') is True,
        unmanaged_reason=_text(get('UnmanagedReason'), 64),
        preview_source=_text(get('PreviewSource'), 32) if enrolled is True else '',
        booted_name=clean_name(get('BootedName')) or booted_display_name(booted_version),
        staged_name=named('StagedName', staged_version),
        available_name=named('AvailableName', available_version),
        waiting_name=named('WaitingName', waiting_version),
        rolled_back_name=named('RolledBackName', rolled_back_version),
        ignored_name=named('IgnoredName', ignored_version),
        removed_packages=_packages(get('RemovedPackages')),
        kept_packages=_packages(get('KeptPackages')),
        source=source)


def _packages(value) -> tuple[str, ...]:
    """A published package list: at most 64 NEVRAs or names, each cleaned."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(text for text in (_text(item, 200) for item in value[:64] if isinstance(item, str)) if text)


def from_status_json(text: str, source: str = 'cli') -> SystemUpdate:
    """Parse ``luma-update status --json`` (schema 1); an unreadable answer is no agent."""
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return NOT_INSTALLED
    if not isinstance(value, dict) or value.get('schema_version', 1) != 1:
        return NOT_INSTALLED
    return from_values(value, source)


#: org.gnome.SessionManager.Reboot errors that mean there is no GNOME-style session manager.
#: Only then may Depot restart through the agent's Apply() instead.
NO_SESSION_MANAGER = ('org.freedesktop.DBus.Error.ServiceUnknown',
                      'org.freedesktop.DBus.Error.NameHasNoOwner',
                      'org.freedesktop.DBus.Error.UnknownMethod')
#: How GDBus names G_IO_ERROR_CANCELLED on the wire. gnome-session answers Reboot with it
#: when the person presses Cancel in the end-session dialog.
_CANCELLED_SUFFIX = '.Quark._g_2dio_2derror_2dquark.Code19'


def restart_outcome(remote_error: str) -> str:
    """What a failed ``org.gnome.SessionManager.Reboot`` call means.

    ``agent``: there is no session manager, so ask luma-updated's Apply().
    ``cancelled``: the person cancelled the end-session dialog; do nothing.
    ``refused``: the session manager refused (locked down, not running yet, an
    inhibitor). Depot reports it and never restarts past it.
    """
    name = remote_error or ''
    if name in NO_SESSION_MANAGER:
        return 'agent'
    if name.endswith(_CANCELLED_SUFFIX) or name == 'org.freedesktop.DBus.Error.Cancelled':
        return 'cancelled'
    return 'refused'


def counting_privacy_text(preview_enrolled: bool, preview_source: str = 'hub') -> str:
    """What Depot's Counting settings say about what Luma can learn from counts.

    Counts and update reports carry no account, device name or identifier, but
    every one reaches Luma from this computer's network address. On early
    updates the computer also downloads with a credential issued to the Luma
    account, from the same address, so matching address and time could tie a
    count to that account. The copy says so rather than promising that nothing
    can be joined to anyone."""
    text = ("Luma counts installs and active computers without sending who you are: no account, "
            "name or identifier goes with a count. Like any connection, each count reaches Luma "
            "from this computer\u2019s network address.")
    if preview_enrolled and preview_source == 'staff-media':
        text += (" This computer gets early updates with a credential the Luma team issued to it, so Luma "
                 "could connect counts and update reports to that credential by address and time.")
    elif preview_enrolled:
        text += (" This computer gets early updates, which it downloads with a credential tied to your "
                 "Luma account from the same address, so Luma could connect counts and update reports "
                 "to that account by address and time.")
    return text


EARLY_UPDATES_SUBTITLE = ("Try Luma Beta or Nightly before everyone else: choose it on the Updates page. "
                          "No account is needed, and Official is one choice away.")


#: Why the last check ended as it did, in words rather than in the agent's own
#: vocabulary. Anything not listed is not shown at all: a person is never given
#: a machine word to puzzle over.
CHECK_REASONS = {
    'up-to-date': 'Nothing newer was offered for this channel.',
    'newest-eligible': 'The newest release for this channel was chosen.',
    'barrier': 'This release has to be installed before anything newer.',
    'barrier-blocked': 'A release that has to come first did not finish last time. Luma will try it again.',
    'rollout-not-reached': 'A newer release exists and is still rolling out. It reaches this computer soon.',
    'switch-now': 'This computer was switched to another channel.',
    'booted-release-pulled': 'Luma withdrew the version on this computer and moved it on.',
    'booted-release-pulled-rollback': 'Luma withdrew the version on this computer and went back a release.',
    'booted-release-pulled-no-target': 'Luma withdrew the version on this computer and has nothing to move to yet.',
    'no-usable-release': 'That channel has no release this computer can use.',
    'network': 'Luma could not reach the update server.',
    'signature': 'The release information was not signed by a key this computer trusts.',
    'graph': 'The release information could not be read.',
    'stale-graph': 'The release information was too old to trust. Luma will try again.',
    'disk-space': 'There was not enough room to download it.',
    'transaction': 'Preparing the update failed.',
    'unmanaged': 'This computer does not follow a Luma update channel.',
    'preview': 'Early updates are not set up on this computer.',
    'not-entitled': 'This Luma account cannot get early updates yet.',
}


def check_reason_text(reason: str) -> str:
    return CHECK_REASONS.get((reason or '').strip(), '')


def automatic_download_note(metered: bool, automatic: bool) -> str:
    """What "Download updates automatically" actually does, said plainly."""
    if not automatic:
        return ('Luma checks for updates but downloads nothing until you press Download. '
                'Nothing is ever installed until you restart.')
    return ('Luma downloads updates in the background and then tells you. It waits on a metered '
            'connection, and on battery below 30%. Nothing is ever installed until you restart.')


# ── What a release is called ─────────────────────────────────────────────
#
# The same rules as luma-update's luma_update/names.py, which Depot cannot import
# (a separate package). luma-update publishes the names itself; these only name
# versions for an agent too old to do so, and the booted system when there is no
# agent at all. Machine versions are never changed or compared here.

#: The booted system's os-release. Its PRETTY_NAME is what the booted system is called.
OS_RELEASE = '/usr/lib/os-release'
MAX_NAME = 200
_N = r'(0|[1-9]\d{0,8})'
_NIGHTLY_VERSION = re.compile(_N + r'\.' + _N + r'\.' + _N + r'-nightly\.(\d{8})(?:\.\d{1,9})?\Z')
_BETA_VERSION = re.compile(_N + r'\.' + _N + r'\.' + _N + r'-beta\.' + _N + r'(?:\.' + _N + r')?\Z')
_RELEASE_VERSION = re.compile(_N + r'\.' + _N + r'\.' + _N + r'\Z')
_CODENAME = re.compile(r'[a-z0-9][a-z0-9._-]{0,31}\Z')
_STAGE_NUMBER = re.compile(r'(0|[1-9]\d{0,8})(\.(0|[1-9]\d{0,8}))?\Z')
_os_release_cache: dict[str, dict] = {}


def clean_name(value) -> str:
    """A name fit to show: one line, at most 200 characters, else ''."""
    if not isinstance(value, str):
        return ''
    text = value.strip()
    if not text or len(text) > MAX_NAME or any(ord(c) < 32 or ord(c) == 127 for c in text):
        return ''
    return text


def parse_os_release(text: str) -> dict:
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if not re.match(r'[A-Z][A-Z0-9_]{0,63}\Z', key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            quote, value = value[0], value[1:-1]
            if quote == '"':
                value = re.sub(r'\\([\\"$`])', r'\1', value)
        values[key] = value
    return values


def booted_os_release(path: str | None = None) -> dict:
    """The booted os-release, read once: it cannot change until the next boot."""
    path = path or OS_RELEASE
    if path not in _os_release_cache:
        try:
            with open(path, 'rb') as stream:
                _os_release_cache[path] = parse_os_release(stream.read(65536).decode('utf-8', 'replace'))
        except OSError:
            _os_release_cache[path] = {}
    return _os_release_cache[path]


def pretty_name(info: dict) -> str:
    """PRETTY_NAME, unless missing or from a Luma image older than release names ("Luma 1.0")."""
    name = clean_name((info or {}).get('PRETTY_NAME'))
    if name and info.get('ID') == 'luma' and not info.get('VERSION_CODENAME'):
        return ''
    return name


def derive_name(version: str, booted: dict | None = None) -> str:
    """A name from the version alone, with the booted system's codename and stage."""
    text = version.strip() if isinstance(version, str) else ''
    if not text:
        return ''
    if text.lower().startswith('luma'):
        return clean_name(text)
    info = booted or {}
    codename = str(info.get('VERSION_CODENAME') or '')
    if not _CODENAME.match(codename):
        codename = 'prairie'
    codename = ' '.join(part.capitalize() for part in re.split(r'[._-]+', codename) if part)
    match = _NIGHTLY_VERSION.match(text)
    if match:
        if info.get('LUMA_RELEASE_STAGE') == 'final':
            return f'Luma (Version {match.group(1)}, {codename}, Nightly {match.group(4)})'
        number = str(info.get('LUMA_RELEASE_STAGE_NUMBER') or '0')
        return f'Luma ({codename}, Beta {number if _STAGE_NUMBER.match(number) else "0"}, Nightly {match.group(4)})'
    match = _BETA_VERSION.match(text)
    if match:
        return f'Luma ({codename}, Beta {match.group(4)}' + (f'.{match.group(5)})' if match.group(5) else ')')
    match = _RELEASE_VERSION.match(text)
    if match:
        if match.group(2) == '0' and match.group(3) == '0':
            return f'Luma (Version {match.group(1)}, {codename})'
        return f'Luma (Version {text}, {codename})'
    return clean_name(f'Luma {text}')


def display_name(version: str, *, booted: dict | None = None) -> str:
    """The name of a version that is not booted, when the agent did not publish one."""
    return derive_name(version, booted_os_release() if booted is None else booted) if version else ''


def booted_display_name(version: str = '', *, booted: dict | None = None) -> str:
    """The booted system's name: its own PRETTY_NAME, else derived from its version."""
    info = booted_os_release() if booted is None else booted
    return pretty_name(info) or derive_name(version, info)


def display_version(version: str) -> str:
    """A machine version as people read it ("1.0.0-beta.1" is "Luma (Prairie, Beta 1)")."""
    return display_name(version)
