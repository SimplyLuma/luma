"""Visible application inventory shared with the desktop's GAppInfo semantics.

Provider hints are display metadata, not authority. Valet independently resolves
ownership and retains its removal checks before any mutation.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class InstalledApp:
    desktop_id: str
    name: str
    provider: str
    management: str
    can_review_removal: bool
    app_info: object = field(compare=False, repr=False)


def visible_applications(app_infos, records=()):
    result, seen = [], set()
    records = tuple(records)
    for app in app_infos:
        identity = app.get_id()
        if not identity or identity in seen or not app.should_show():
            continue
        seen.add(identity)
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
