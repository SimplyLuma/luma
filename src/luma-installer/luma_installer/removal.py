"""What Valet may remove, and what its uninstall window shows.

Nothing here imports GTK, so the decisions can be tested without a display.
The window applies :class:`RemovalView` to its AppKit transaction card; the
card itself stays AppKit's.
"""
from __future__ import annotations

from dataclasses import dataclass

from .errors import InstallerError

# Applications the session needs. Valet never removes these, however they were
# installed (base image, Flatpak, or a package opened in Valet).
ESSENTIAL_APPLICATIONS = frozenset({
    "org.gnome.Shell",               # the Shell
    "org.gnome.Settings",            # Settings
    "org.gnome.Nautilus",            # Filer
    "org.gnome.Ptyxis",              # Terminal
    "org.gnome.Console",
    "org.gnome.Terminal",
    "org.projectluma.Depot",         # Depot
    "io.luma.Valet",                 # Valet
    "io.luma.Install",
    "org.projectluma.ApplicationInstaller",
    "org.projectluma.SoftwareUpdate",
    "org.projectluma.Update",
})
ESSENTIAL_PREFIXES = ("org.gnome.Shell.",)
ESSENTIAL_PACKAGES = frozenset({
    "gnome-shell", "gnome-session", "gnome-control-center", "nautilus",
    "ptyxis", "gnome-console", "gnome-terminal", "luma-application-installer",
})

INSTALLED_PREFIX = "org.projectluma.Installed."


def _stem(value) -> str:
    text = str(value or "").strip()
    if text.endswith(".desktop"):
        text = text[:-len(".desktop")]
    if text.startswith(INSTALLED_PREFIX):
        text = text[len(INSTALLED_PREFIX):]
    return text


def is_essential(record=None, identity=None) -> bool:
    """Whether a record or desktop identity names an application the session needs."""
    record = record or {}
    names = {_stem(identity), _stem(record.get("desktop_id")), _stem(record.get("flatpak_id"))}
    names.discard("")
    folded = {name.casefold() for name in names}
    if folded & {name.casefold() for name in ESSENTIAL_APPLICATIONS}:
        return True
    if any(name.startswith(prefix.casefold()) for name in folded
           for prefix in (p.casefold() for p in ESSENTIAL_PREFIXES)):
        return True
    packages = {str(record.get(key) or "") for key in ("package", "package_name")}
    return record.get("format") in {"rpm", "deb"} and bool(packages & ESSENTIAL_PACKAGES)


def is_removable(record) -> bool:
    return bool(record) and record.get("format") != "system" and not is_essential(record)


def refuse_protected(record) -> None:
    """The last check before anything is removed; the window is not trusted to have made it."""
    name = str((record or {}).get("name") or "This application")
    if not is_removable(record):
        raise InstallerError(f"{name} is part of Luma and can’t be uninstalled.")


def display_name(identity, record=None) -> str:
    """A person-facing name that is never the placeholder "Application"."""
    record = record or {}
    name = str(record.get("name") or "").strip()
    if name:
        return name
    stem = _stem(identity or record.get("desktop_id") or record.get("application_id"))
    last = stem.rsplit(".", 1)[-1] if stem else ""
    return last or "This app"


@dataclass(frozen=True)
class RemovalView:
    action: str              # primary button label
    message: str             # one complete sentence, wrapped, never truncated
    show_keep: bool          # the "Keep settings and data" checkbox
    keep: bool               # its state
    destructive: bool        # destructive-action styling on the primary button
    closes: bool             # the primary button only closes the window


KEEP_WARNING = "{name}’s settings and data will be deleted and can’t be recovered."


def removal_view(record, name, keep=True) -> RemovalView:
    """Decide the uninstall window's state for ``record``.

    ``keep`` is the checkbox as the person left it; a fresh window starts at
    True, so deleting data always takes an explicit uncheck.
    """
    if not is_removable(record):
        return RemovalView("Close", f"{name} is part of Luma and can’t be uninstalled.",
                           False, True, False, True)
    if record.get('external_layered'):
        return RemovalView('Uninstall', 'Personal settings and files will be retained.',
                           False, True, True, False)
    if record.get("format") == "snap":
        # snapd keeps its own snapshot; the choice is not Valet's to offer.
        return RemovalView("Uninstall", "", True, True, True, False)
    if keep:
        return RemovalView("Uninstall", "", True, True, True, False)
    return RemovalView("Uninstall and Delete Data", KEEP_WARNING.format(name=name),
                       True, False, True, False)


def unavailable_view(message) -> RemovalView:
    """Removal could not even be reviewed: say why and offer only Close."""
    return RemovalView("Close", message, False, True, False, True)


def apply_view(card, view: RemovalView, *, reset_keep=False) -> None:
    """Show ``view`` on an AppKit TransactionCard (or anything shaped like one)."""
    card.set_phase("ready", view.action, view.message, removing=view.destructive)
    # set_phase styles every non-removal as suggested; Close is neither.
    if view.closes:
        card.primary.remove_css_class("suggested-action")
    card.keep.set_visible(view.show_keep)
    if reset_keep or not view.show_keep:
        card.keep.set_active(view.keep)
