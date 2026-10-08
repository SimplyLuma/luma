"""Resolve Tide's resources beside its source or installed Python package."""

from pathlib import Path


def stylesheet_path(package_file=__file__) -> Path:
    package_parent = Path(package_file).resolve().parents[1]
    source = package_parent / "data/tide.css"
    if source.is_file():
        return source
    # Follow the package's own prefix, including a private DESTDIR or Flatpak.
    # A staged check must never silently pick up the installed app's stylesheet.
    for parent in package_parent.parents:
        installed = parent / "share/luma-tide/tide.css"
        if installed.is_file():
            return installed
    return Path("/usr/share/luma-tide/tide.css")
