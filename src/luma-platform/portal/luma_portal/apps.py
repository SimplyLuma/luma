# SPDX-License-Identifier: Apache-2.0
"""Which applications can open this, and which one already does.

Two lists, not one. An alphabetical heap of everything installed answers
neither question a person actually has — what is this meant to open in, and
what else could — so the applications that claim the content type are kept
apart from the ones that merely could take a file.
"""

from __future__ import annotations

import locale
from dataclasses import dataclass

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402


@dataclass(frozen=True)
class Application:
    """One row: what it is called, what it says it is, and how to run it."""

    desktop_id: str
    name: str
    description: str
    icon: Gio.Icon | None
    is_default: bool = False
    #: "Flatpak", "Snap", or empty when it came with the system.
    origin: str = ""

    @property
    def accessible_name(self) -> str:
        """What a screen reader reads. The chip is never the only signal."""
        parts = [self.name]
        if self.description:
            parts.append(self.description)
        if self.is_default:
            parts.append("default")
        return ", ".join(parts)


def _describe(info: Gio.AppInfo) -> str:
    """The Comment, then GenericName, then nothing.

    Never the Exec line: a command is not a description, and printing one tells
    a person nothing they can use while making the row look like a debug view.
    """
    for value in (info.get_description(), getattr(info, "get_generic_name", lambda: None)()):
        if value and value.strip():
            return " ".join(value.split())
    return ""


def _usable(info: Gio.AppInfo) -> bool:
    """Anything that would not actually appear or run is not an option."""
    # A row with no name is a row a person cannot read, whatever else it has.
    if not info.get_id() or not (info.get_name() or "").strip():
        return False
    if isinstance(info, Gio.DesktopAppInfo) and info.get_nodisplay():
        return False
    return bool(info.get_commandline() or info.get_executable())


#: Where a desktop file lives says how the application was installed. This is
#: the only thing that separates two packagings of the same application, and it
#: is a fact about the file rather than something invented for the row.
ORIGINS = (
    ("/snapd/", "Snap"),
    ("/flatpak/", "Flatpak"),
)


def origin(info) -> str:
    """"Flatpak", "Snap", or empty for something installed in the system."""
    path = getattr(info, "get_filename", lambda: None)() or ""
    for marker, label in ORIGINS:
        if marker in path:
            return label
    return ""


def disambiguate(rows):
    """Guarantee no two rows read the same.

    Two applications can share a display name honestly: Luma's Calendar and a
    Flatpak of GNOME's, or the same Spotify installed as both a Snap and a
    Flatpak. Left alone the last pair is two identical rows — same name, and
    neither carries a Comment — which asks a person to choose between two
    things that look exactly alike.

    Where names collide, the packaging is named. The copy that came with the
    system keeps the plain name, because that is the one a person means when
    they say Calendar; anything sandboxed says so. If two survivors still
    match, their desktop ids break the tie, which is ugly but never ambiguous.
    """
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(row.name.strip().casefold(), []).append(row)

    resolved = []
    for members in groups.values():
        if len(members) == 1:
            resolved.extend(members)
            continue
        seen: dict[str, int] = {}
        for row in members:
            label = row.origin
            name = f"{row.name} ({label})" if label else row.name
            count = seen.get(name.casefold(), 0)
            seen[name.casefold()] = count + 1
            if count:
                # Two of the same packaging, or two system copies: nothing left
                # to distinguish them but the id they are installed under.
                stem = row.desktop_id.removesuffix(".desktop")
                name = f"{row.name} ({stem})"
            resolved.append(_renamed(row, name))
    return resolved


def _renamed(row, name: str):
    return Application(
        desktop_id=row.desktop_id, name=name, description=row.description,
        icon=row.icon, is_default=row.is_default, origin=row.origin,
    )


def _sorted(infos, default_id: str | None):
    """Alphabetical by display name, in the user's own collation."""
    seen: dict[str, Gio.AppInfo] = {}
    for info in infos:
        if _usable(info) and info.get_id() not in seen:
            seen[info.get_id()] = info
    rows = [
        Application(
            desktop_id=info.get_id(),
            name=" ".join(info.get_name().split()),
            description=_describe(info),
            icon=info.get_icon(),
            is_default=info.get_id() == default_id,
            origin=origin(info),
        )
        for info in seen.values()
    ]
    rows.sort(key=lambda row: locale.strxfrm(row.name.casefold()))
    return tuple(rows)


def desktop_id_for(app_id: str) -> str:
    """A portal app id as the desktop file name GIO looks up.

    org.freedesktop.impl.portal.AppChooser deals in app ids -- the desktop
    file's name without its suffix -- both in the choices it hands over and in
    the choice it expects back. Treating one as the other broke the feature at
    both ends: Gio.DesktopAppInfo.new() returned NULL for every id the caller
    suggested, which PyGObject raises as a TypeError, and the choice we
    answered with carried a suffix the frontend then appended to again, so
    whatever was picked could not be found and nothing opened.
    """
    return app_id if app_id.endswith(".desktop") else f"{app_id}.desktop"


def app_id_for(desktop_id: str) -> str:
    """The inverse: what the portal wants back. See desktop_id_for()."""
    suffix = ".desktop"
    return desktop_id[:-len(suffix)] if desktop_id.endswith(suffix) else desktop_id


def for_content_type(content_type: str, *, extra_ids=()):
    """The two sections, and the desktop id of the current default.

    `extra_ids` are the choices the portal caller supplied. They are trusted to
    be relevant and are merged into the recommended section, because the caller
    knows something about its own request that the MIME database does not.
    """
    default = None
    if content_type:
        info = Gio.AppInfo.get_default_for_type(content_type, False)
        default = info.get_id() if info is not None else None

    recommended_infos = list(
        Gio.AppInfo.get_all_for_type(content_type) if content_type else []
    )
    for desktop_id in extra_ids:
        # PyGObject raises TypeError when a GObject constructor returns NULL,
        # so an id naming no installed desktop file arrives as an exception
        # rather than as the None this used to test for -- and the exception
        # escaped ChooseApplication, so the chooser never appeared and the
        # caller was never answered. "Open with" simply did nothing. These ids
        # come from the requesting application by way of the portal and are not
        # ours to trust.
        try:
            info = Gio.DesktopAppInfo.new(desktop_id_for(desktop_id))
        except TypeError:
            # Still possible: an id naming nothing installed. A caller may
            # suggest an application this machine does not have.
            continue
        if info is not None:
            recommended_infos.append(info)

    recommended = _sorted(recommended_infos, default)
    claimed = {row.desktop_id for row in recommended}
    others = _sorted(
        [info for info in Gio.AppInfo.get_all() if info.get_id() not in claimed],
        default,
    )
    # Across both sections, not within each. The collision that matters is two
    # rows in the same dialog reading alike, and the pair is usually split —
    # the application that claims the type sits above the one that does not.
    resolved = {row.desktop_id: row for row in disambiguate(list(recommended) + list(others))}
    return (
        tuple(resolved[row.desktop_id] for row in recommended),
        tuple(resolved[row.desktop_id] for row in others),
        default,
    )


def set_default(desktop_id: str, content_type: str) -> bool:
    """Make this the handler for the kind. Only ever on an explicit 'always'."""
    info = Gio.DesktopAppInfo.new(desktop_id)
    if info is None or not content_type:
        return False
    try:
        info.set_as_default_for_type(content_type)
    except Exception:  # noqa: BLE001 - a read-only mimeapps.list is not a crash
        return False
    return True


def from_commandline(command: str):
    """The Browse… escape hatch: a command the user named themselves."""
    if not command.strip():
        return None
    try:
        return Gio.AppInfo.create_from_commandline(
            command, None, Gio.AppInfoCreateFlags.NONE,
        )
    except Exception:  # noqa: BLE001
        return None
