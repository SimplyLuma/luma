"""Backend-owned Depot Flatpak identities; discovery cannot grant authority.

Two remotes are known, and both are compiled in here with their addresses:
Flathub, which must already be configured and signature-checking, and the Luma
remote (ADR-028, section 12), which Depot adds from the ``.flatpakrepo`` shipped
in this package -- carrying its own signing key -- the first time a person
installs something from it. Depot never changes keys, never edits an existing
remote, never adds any other remote, and never substitutes a similarly named
source. Release qualification and the UI admission switch stay separate.
"""
from dataclasses import dataclass
import configparser
import base64
import binascii
import os
import re
from pathlib import Path

# Maintained source mappings, not values accepted from downloadable metadata.
#: The applications this was written around, kept as the answer when no
#: catalogue can be read at all.
_BUILTIN_SOURCES = {
    'discord': 'com.discordapp.Discord', 'firefox': 'org.mozilla.firefox',
    'vlc': 'org.videolan.VLC', 'thunderbird': 'org.mozilla.thunderbird',
    'audacity': 'org.audacityteam.Audacity', 'libreoffice': 'org.libreoffice.LibreOffice',
    'gimp': 'org.gimp.GIMP', 'obsidian': 'md.obsidian.Obsidian',
    'steam': 'com.valvesoftware.Steam', 'obs': 'com.obsproject.Studio',
}

DATA_DIRECTORY = Path(os.environ.get('LUMA_DEPOT_DATA_DIRECTORY', '/usr/share/luma/installer'))
LUMA_FLATPAKREPO = Path(os.environ.get('LUMA_DEPOT_FLATPAKREPO', str(DATA_DIRECTORY / 'luma.flatpakrepo')))
BRANCHES = frozenset({'stable', 'beta', 'nightly'})


@dataclass(frozen=True)
class RemotePolicy:
    name: str
    title: str
    urls: frozenset
    #: A remote Depot may add itself, from this shipped file. None: never.
    repo_file: Path | None = None


REMOTES = {
    'flathub': RemotePolicy('flathub', 'Flathub',
                            frozenset({'https://dl.flathub.org/repo/', 'https://dl.flathub.org/repo'})),
    'luma': RemotePolicy('luma', 'Luma',
                         frozenset({'https://dl.simplyluma.com/repo/', 'https://dl.simplyluma.com/repo'}),
                         LUMA_FLATPAKREPO),
}
#: Kept for callers that only ever knew Flathub.
REMOTE_URLS = REMOTES['flathub'].urls


def _catalogued():
    """Flatpak applications the catalogue on this device lists, with their remote.

    Widening this to the catalogue does not widen what is trusted. A catalogue
    may only ever name flathub or luma for a flatpak -- the repositories are
    compiled into depot_catalog and no served document can add one, and only a
    signature-verified schema 4 catalogue may name luma -- and resolve() below
    still proves every claim independently: it checks the remote really is the
    expected address with signature checks on, fetches the ref, refuses
    anything whose format does not match exactly or that carries no commit,
    and transaction() re-resolves immediately before preparing so a repository
    that changed under a browse cannot substitute another build. The catalogue
    says what is on offer; libflatpak decides what is real and what is signed.
    """

    from .depot_catalog import CatalogError, local_catalog
    try:
        catalog = local_catalog()
    except CatalogError:
        return {identity: (source, 'flathub', 'stable') for identity, source in _BUILTIN_SOURCES.items()}
    return {entry.id: (entry.source_id, entry.repository, entry.branch)
            for entry in catalog.applications if entry.backend == 'flatpak'}


def _refresh():
    global APPLICATION_SOURCES, APPLICATIONS, SOURCE_REMOTES
    catalogued = _catalogued()
    APPLICATION_SOURCES = {identity: value[0] for identity, value in catalogued.items()}
    SOURCE_REMOTES = {value[0]: (value[1], value[2]) for value in catalogued.values()}
    APPLICATIONS = frozenset(APPLICATION_SOURCES.values())


APPLICATION_SOURCES = {}
SOURCE_REMOTES = {}
APPLICATIONS = frozenset()
_refresh()


class SourceUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedSource:
    app_id: str
    architecture: str
    ref: str
    commit: str
    remote: str = 'flathub'
    branch: str = 'stable'


def _policy(name):
    policy = REMOTES.get(name)
    if policy is None:
        raise SourceUnavailable('This application source is not supported.')
    return policy


def validate_remote(remote, name=None):
    policy = _policy(name or (remote.get_name() if hasattr(remote, 'get_name') else 'flathub'))
    if remote.get_url() not in policy.urls:
        raise SourceUnavailable(f'{policy.title} has an unexpected repository address.')
    if remote.get_disabled():
        raise SourceUnavailable(f'{policy.title} is disabled.')
    if not remote.get_gpg_verify():
        raise SourceUnavailable(f'{policy.title} signature verification must be enabled.')


def _installations(cancellable=None):
    import gi
    gi.require_version('Flatpak', '1.0')
    from gi.repository import Flatpak
    return (Flatpak.Installation.new_user(cancellable), Flatpak.Installation.new_system(cancellable))


def configured_installation(cancellable=None, remote='flathub'):
    return select_installation(_installations(cancellable), cancellable, remote)


def select_installation(installations, cancellable=None, remote='flathub'):
    policy = _policy(remote)
    failures = []
    for installation in installations:
        remotes = installation.list_remotes(cancellable)
        found = next((r for r in remotes if r.get_name() == policy.name), None)
        if found is not None:
            try:
                validate_remote(found, policy.name)
                return installation
            except SourceUnavailable as error:
                failures.append(str(error))
    raise SourceUnavailable(failures[0] if failures else f'{policy.title} is not configured on this device.')


def remote_exists(installations, name, cancellable=None):
    return any(r.get_name() == name for installation in installations
               for r in installation.list_remotes(cancellable))


def read_repo_file(path=None):
    """Check a shipped ``.flatpakrepo`` before libflatpak is given it.

    The file must name the Luma remote's exact address and carry a signing
    key; anything else is refused rather than configured.
    """
    path = Path(path or REMOTES['luma'].repo_file)
    try:
        with path.open('rb') as stream:
            data = stream.read(65537)
    except OSError as error:
        raise SourceUnavailable('The Luma app source is not set up on this computer yet.') from error
    if len(data) > 65536:
        raise SourceUnavailable('The Luma app source file is too large.')
    parser = configparser.RawConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read_string(data.decode('utf-8'))
        section = parser['Flatpak Repo']
    except (UnicodeError, configparser.Error, KeyError) as error:
        raise SourceUnavailable('The Luma app source file cannot be read.') from error
    if section.get('Url', '').strip() not in REMOTES['luma'].urls:
        raise SourceUnavailable('The Luma app source file names an unexpected address.')
    key = section.get('GPGKey', '').strip()
    try:
        if len(base64.b64decode(key, validate=True)) < 32:
            raise ValueError()
    except (binascii.Error, ValueError) as error:
        raise SourceUnavailable('The Luma app source file carries no signing key.') from error
    if section.get('NoGPGVerify', 'false').strip().lower() in ('true', '1', 'yes'):
        raise SourceUnavailable('The Luma app source file turns signature checks off.')
    return data


def ensure_luma_remote(cancellable=None, installations=None):
    """The installation that carries a trustworthy Luma remote, adding it if absent.

    An existing remote named ``luma`` is used only if it passes the same checks
    as Flathub; one that does not is reported, never repaired or replaced,
    because a person or an administrator put it there. When there is none
    (Luma's image ships it system-wide, so only on other distributions), the
    shipped file is added to the installation that has Flathub, else the
    per-user one.
    """
    installations = installations or _installations(cancellable)
    try:
        return select_installation(installations, cancellable, 'luma')
    except SourceUnavailable:
        if remote_exists(installations, 'luma', cancellable):
            raise
    data = read_repo_file()
    import gi
    gi.require_version('Flatpak', '1.0')
    from gi.repository import Flatpak, GLib
    # Beside Flathub: Luma's apps may take their runtimes from Flathub, and a
    # transaction resolves dependencies within one installation. That is the
    # system installation wherever Flathub is set up system-wide (Luma, and
    # most distributions); adding a system remote asks for permission once.
    installation = next((candidate for candidate in installations
                         if any(r.get_name() == 'flathub' for r in candidate.list_remotes(cancellable))),
                        installations[0])
    try:
        remote = Flatpak.Remote.new_from_file('luma', GLib.Bytes.new(data))
        remote.set_gpg_verify(True)
        remote.set_disabled(False)
        installation.add_remote(remote, False, cancellable)
        installation.drop_caches(cancellable)
    except GLib.Error as error:
        raise SourceUnavailable('The Luma app source could not be added.') from error
    return select_installation(installations, cancellable, 'luma')


def luma_remote_available(cancellable=None, installations=None):
    """Whether installing from the Luma remote can work here, without changing anything."""
    try:
        installations = installations or _installations(cancellable)
        select_installation(installations, cancellable, 'luma')
        return True
    except SourceUnavailable:
        if remote_exists(installations, 'luma', cancellable):
            return False
    except Exception:
        return False
    try:
        read_repo_file()
        return True
    except SourceUnavailable:
        return False


def installation_for(app_id, cancellable=None):
    """The installation and remote a catalogued application installs from."""
    _refresh()
    remote, _branch = SOURCE_REMOTES.get(app_id, ('', ''))
    if remote == 'luma':
        return ensure_luma_remote(cancellable), 'luma'
    if remote == 'flathub':
        return configured_installation(cancellable, 'flathub'), 'flathub'
    raise SourceUnavailable('This application source is not supported.')


def resolve(installation, app_id, architecture, cancellable=None, remote=None, branch=None):
    if app_id not in APPLICATIONS:
        _refresh()
    catalogued_remote, catalogued_branch = SOURCE_REMOTES.get(app_id, ('', ''))
    remote = remote or catalogued_remote or 'flathub'
    branch = branch or catalogued_branch or 'stable'
    if (app_id not in APPLICATIONS or catalogued_remote != remote
            or architecture not in {'x86_64', 'aarch64'} or branch not in BRANCHES):
        raise SourceUnavailable('This application source is not supported.')
    import gi
    gi.require_version('Flatpak', '1.0')
    from gi.repository import Flatpak
    validate_remote(installation.get_remote_by_name(remote, cancellable), remote)
    ref = installation.fetch_remote_ref_sync(remote, Flatpak.RefKind.APP,
                                            app_id, architecture, branch, cancellable)
    expected = f'app/{app_id}/{architecture}/{branch}'
    if ref.format_ref() != expected or not ref.get_commit():
        raise SourceUnavailable('The repository returned an unexpected application.')
    if remote == 'luma':
        from .app_host_compatibility import require_host_compatibility
        require_host_compatibility(metadata_text(ref.get_metadata()))
    return ResolvedSource(app_id, architecture, expected, ref.get_commit(), remote, branch)


#: Remotes an app's runtimes and runtime extensions may also come from.
#: Apps on the Luma remote may build on Flathub's runtimes (org.freedesktop.*,
#: org.gnome.*, org.kde.*): Luma does not redistribute those, and Flathub is a
#: configured, signature-checked remote on Luma. Applications themselves never
#: cross remotes, and Flathub apps never take anything from the Luma remote.
DEPENDENCY_REMOTES = {'luma': ('flathub',)}


def operation_remotes(source, operations):
    """Every remote a prepared transaction would use, for validation."""
    return {op.get_remote() for op in operations} | {source.remote}


def operations_match(source, operations):
    targets = [op for op in operations if op.get_ref() == source.ref]
    if len(targets) != 1 or targets[0].get_commit() != source.commit:
        return False
    allowed = DEPENDENCY_REMOTES.get(source.remote, ())
    for op in operations:
        remote = op.get_remote()
        if remote == source.remote:
            continue
        if op is targets[0] or not op.get_ref().startswith('runtime/') or remote not in allowed:
            return False
    return True


def installed_ref(installation, source, cancellable=None):
    return next((ref for ref in installation.list_installed_refs(cancellable)
                 if ref.format_ref() == source.ref), None)


def require_reviewed_update(source: ResolvedSource, expected_commit: str) -> None:
    """The UI/background permission decision must name the commit being installed."""
    if (not isinstance(expected_commit, str)
            or not re.fullmatch(r'[0-9a-f]{64}', expected_commit)
            or source.commit != expected_commit):
        raise SourceUnavailable('This update changed or has not been checked. Refresh Updates before installing it.')


def require_update_baseline(installed, expected_commit: str) -> None:
    if (not isinstance(expected_commit, str)
            or not re.fullmatch(r'[0-9a-f]{64}', expected_commit)
            or installed is None or installed.get_commit() != expected_commit):
        raise SourceUnavailable('The installed app changed. Refresh Updates before installing it.')


def transaction(installation, source, cancellable=None, *, update=False, expected_installed_commit=None):
    """Prepare supported libflatpak work; caller owns ready/progress/run UI.

    Re-resolve immediately before preparing: repository changes since browsing
    must not silently change a reviewed application commit. libflatpak retains
    responsibility for package signatures and dependency resolution.
    """
    current = resolve(installation, source.app_id, source.architecture, cancellable,
                      source.remote, source.branch)
    if current != source:
        raise SourceUnavailable('This application changed. Refresh it before installing.')
    from gi.repository import Flatpak, GLib
    installed = installed_ref(installation, source, cancellable)
    if update and expected_installed_commit is not None:
        require_update_baseline(installed, expected_installed_commit)
    if installed is not None:
        if installed.get_origin() != source.remote:
            raise SourceUnavailable('This application is installed from another source.')
        if not update or installed.get_commit() == source.commit:
            return None  # Already installed; refresh inventory without a fake job.
    elif update:
        raise SourceUnavailable('This application is no longer installed.')
    tx = Flatpak.Transaction.new_for_installation(installation, cancellable)
    tx.connect('add-new-remote', lambda *_args: False)
    def ready(prepared):
        # Resolve-to-run races must not substitute another commit. Dependencies
        # come from the app's own remote or, for runtimes, a remote it may build
        # on (DEPENDENCY_REMOTES); every remote used must pass the same checks.
        operations = prepared.get_operations()
        if update and expected_installed_commit is not None:
            try:
                require_update_baseline(installed_ref(installation, source, cancellable), expected_installed_commit)
            except SourceUnavailable:
                return False
        if not operations_match(source, operations):
            return False
        try:
            if source.remote == 'luma':
                from .app_host_compatibility import require_host_compatibility
                own = next(op for op in operations if op.get_ref() == source.ref)
                payload = own.get_metadata()
                if payload is None: return False
                require_host_compatibility(payload.to_data()[0])
            for remote in operation_remotes(source, operations):
                validate_remote(installation.get_remote_by_name(remote, cancellable), remote)
        except (SourceUnavailable, GLib.Error):
            return False
        return True
    tx.connect('ready', ready)
    if update:
        # Update to the newest commit the remote offers, not to a named one.
        # flatpak's system helper refuses a pinned commit from anyone but root
        # ("Can't update to a specific commit without root permissions"), and
        # pinning is a downgrade tool. The ready check above still refuses to
        # run unless the transaction's target is the commit just resolved, so a
        # remote that moved in between cannot substitute another build.
        tx.add_update(source.ref, None, None)
    else:
        tx.add_install(source.remote, source.ref, None)
    return tx


def revert_transaction(installation, ref, commit, cancellable=None):
    """Go back to ``commit`` of one catalogued application (Depot's "Go back").

    For a system installation only root may name a commit (flatpak's system
    helper refuses anyone else), so Depot runs this in luma-installer-system
    after polkit authorizes it; a per-user installation runs it directly. The
    ready check refuses to run unless the app's own operation targets exactly
    that commit from the app's own, verified remote."""
    import re
    from gi.repository import Flatpak, GLib
    if not re.fullmatch(r'[0-9a-f]{64}', commit or ''):
        raise SourceUnavailable('That is not a version Depot can go back to.')
    if ref.get_origin() not in REMOTES or ref.get_name() not in APPLICATIONS:
        raise SourceUnavailable('Depot does not manage this application.')
    validate_remote(installation.get_remote_by_name(ref.get_origin(), cancellable), ref.get_origin())
    tx = Flatpak.Transaction.new_for_installation(installation, cancellable)
    tx.connect('add-new-remote', lambda *_args: False)
    wanted = ref.format_ref()
    baseline_commit = ref.get_commit()
    before = metadata_text(ref.load_metadata(cancellable)) if ref.get_origin() == 'luma' else None

    def ready(prepared):
        try:
            current = next((item for item in installation.list_installed_refs(cancellable)
                            if item.format_ref() == wanted), None)
            require_update_baseline(current, baseline_commit)
            if current.get_origin() != ref.get_origin():
                return False
            if before is not None:
                from .depot_app_channels import rollback_operations_match
                if not rollback_operations_match(ref, commit, before, prepared.get_operations()):
                    return False
        except (SourceUnavailable, GLib.Error, ValueError, TypeError):
            return False
        own = [op for op in prepared.get_operations() if op.get_ref() == wanted]
        if len(own) != 1 or own[0].get_commit() != commit or own[0].get_remote() != ref.get_origin():
            return False
        try:
            for operation in prepared.get_operations():
                validate_remote(installation.get_remote_by_name(operation.get_remote(), cancellable),
                                operation.get_remote())
        except (SourceUnavailable, GLib.Error):
            return False
        return True
    tx.connect('ready', ready)
    tx.add_update(wanted, None, commit)
    return tx


def removal_transaction(installation, ref, cancellable=None):
    """Uninstall one catalogued application. Its data in ~/.var/app stays."""
    from gi.repository import Flatpak
    if ref.get_origin() not in REMOTES or ref.get_name() not in APPLICATIONS:
        raise SourceUnavailable('Depot does not manage this application.')
    tx = Flatpak.Transaction.new_for_installation(installation, cancellable)
    tx.connect('add-new-remote', lambda *_args: False)
    tx.add_uninstall(ref.format_ref())
    return tx


def pending_updates(installations, cancellable=None):
    """Installed catalogued applications with a newer commit on their own remote."""
    _refresh()
    import gi
    gi.require_version('Flatpak', '1.0')
    from gi.repository import Flatpak, GLib
    found = []
    for installation in installations:
        try:
            refs = installation.list_installed_refs_for_update(cancellable)
        except GLib.Error:
            continue
        for ref in refs:
            if (ref.get_kind() != Flatpak.RefKind.APP or ref.get_origin() not in REMOTES
                    or ref.get_name() not in APPLICATIONS):
                continue
            try:
                validate_remote(installation.get_remote_by_name(ref.get_origin(), cancellable),
                                ref.get_origin())
            except (SourceUnavailable, GLib.Error):
                continue
            found.append((installation, ref))
    return found


def metadata_text(data):
    """A GLib.Bytes or bytes Flatpak metadata keyfile as text, or ''."""
    if data is None:
        return ''
    try:
        raw = data.get_data() if hasattr(data, 'get_data') else data
        return bytes(raw or b'').decode('utf-8', 'replace')
    except (TypeError, ValueError):
        return ''
