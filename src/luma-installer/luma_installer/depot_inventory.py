"""Visible application inventory shared with the desktop's GAppInfo semantics.

Provider hints are display metadata, not authority. Valet independently resolves
ownership and retains its removal checks before any mutation.
"""
from dataclasses import dataclass, field
import os
import shlex


@dataclass(frozen=True)
class InstalledApp:
    desktop_id: str
    name: str
    provider: str
    management: str
    can_review_removal: bool
    app_info: object = field(compare=False, repr=False)


#: Programs that open a file, folder or address rather than being an application.
_OPENERS = frozenset({'xdg-open', 'gio', 'gvfs-open', 'gnome-open', 'kde-open', 'kde-open5', 'kioclient',
                      'kioclient5', 'exo-open'})
#: Interpreters a launcher runs a script with.
_SHELLS = frozenset({'sh', 'bash', 'dash', 'zsh', 'fish', 'ksh', 'python', 'python3', 'perl'})
#: File managers; given a folder, the launcher is a shortcut to it.
_FILE_MANAGERS = frozenset({'nautilus', 'org.gnome.Nautilus', 'filer', 'nemo', 'thunar', 'dolphin', 'pcmanfm'})
_SCRIPTS = ('.sh', '.bash', '.py', '.pl')
_TRUE = ('true', '1', 'yes')


def _scratch_dirs():
    home = os.path.expanduser('~')
    return tuple(os.path.join(home, d) + os.sep for d in ('bin', '.local/bin')) + ('/tmp/', '/var/tmp/')


def is_application(app, managed=False):
    """A launcher a person would call an app: not hidden (NoDisplay, Hidden, X-Luma-Depot-Hidden) and,
    unless a package system owns it, not a developer helper that opens a folder or runs a shell script."""
    get = getattr(app, 'get_string', lambda key: None)
    if not app.should_show() or (get('X-Luma-Depot-Hidden') or '').strip().lower() in _TRUE:
        return False
    if managed:
        return True
    try:
        words = shlex.split(get('Exec') or '')
    except ValueError:
        return True
    while words and (words[0] in ('env', '/usr/bin/env') or ('=' in words[0] and not words[0].startswith('/'))):
        words = words[1:]
    if not words:
        return True
    program = os.path.basename(words[0])
    if program in _OPENERS or program in _SHELLS:
        return False
    if program in _FILE_MANAGERS and any(w.startswith(('/', '~', 'file:')) for w in words[1:]):
        return False
    if words[0].endswith(_SCRIPTS):
        terminal = (get('Terminal') or '').strip().lower() in _TRUE
        return not (terminal or words[0].startswith(_scratch_dirs()))
    return True


def visible_applications(app_infos, records=()):
    result, seen = [], set()
    records = tuple(records)
    for app in app_infos:
        identity = app.get_id()
        if not identity or identity in seen:
            continue
        get = getattr(app, 'get_string', lambda key: None)
        flatpak = get('X-Flatpak')
        snap = get('X-SnapInstanceName')
        relay = get('X-Luma-Relay-AppID')
        prefix = 'org.projectluma.Installed.'
        local_id = identity[len(prefix):-8] if identity.startswith(prefix) and identity.endswith('.desktop') else None
        owned = [r for r in records if (local_id and r.get('application_id') == local_id)
                 or (r.get('desktop_id') == identity)
                 or (snap and r.get('format') == 'snap' and r.get('package_name') == snap)]
        provider, message, review = 'System or external package', 'Managed by its package provider', False
        if flatpak:
            provider, message, review = 'Flatpak', 'Review removal in Valet', True
        elif snap:
            review = len(owned) == 1 and bool(owned[0].get('system_receipt'))
            provider = 'Snap'
            message = 'Review removal in Valet' if review else 'Snap ownership must be registered before removal'
        elif relay:
            provider, message, review = 'Windows · Relay', 'Review removal in Valet', True
        elif identity.startswith('waydroid.'):
            provider, message, review = 'Android', 'Review removal in Valet', True
        elif len(owned) == 1 and owned[0].get('format') in {'rpm', 'deb', 'appimage', 'portable'}:
            provider = {'rpm': 'RPM', 'deb': 'DEB', 'appimage': 'AppImage', 'portable': 'App folder'}[owned[0]['format']]
            message, review = 'Review removal in Valet', True
        if not is_application(app, managed=provider != 'System or external package'):
            continue
        seen.add(identity)
        result.append(InstalledApp(identity, app.get_display_name() or app.get_name() or identity,
                                   provider, message, review, app))
    return tuple(sorted(result, key=lambda app: (app.name.casefold(), app.desktop_id)))


def removal_arguments(app):
    # This opens the existing review flow; it never directly removes a package.
    if not app.can_review_removal:
        raise ValueError(app.management)
    if not app.desktop_id.endswith('.desktop') or '/' in app.desktop_id or app.desktop_id.startswith('-'):
        raise ValueError('Invalid desktop application identity')
    return ['luma-installer', '--remove', app.desktop_id]
