from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .errors import InstallerError

# A console payload is reachable from a shell only through an exported name.
# The name comes from the package's own launcher command, never from free text.
COMMAND_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")
SHIM_MARKER = "# X-Luma-Managed-Shim"


def safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.").lower()
    return cleaned[:80] or "application"


def quote_exec_argument(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def applications_root() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "applications"


def records_root() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "luma/installer/applications"


def commands_root() -> Path:
    return Path.home() / ".local/bin"


def write_record(application_id: str, record: dict[str, object]) -> Path:
    application_id = safe_id(application_id)
    root = records_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = root / f"{application_id}.json"
    record = {
        **record,
        "application_id": application_id,
        "installed_unix": int(record.get("installed_unix", time.time())),
    }
    temporary = destination.with_suffix(".json.partial")
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(destination)
    return destination


def read_record(application_id: str) -> dict[str, object]:
    try:
        return json.loads((records_root() / f"{safe_id(application_id)}.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InstallerError("The installed application's Luma record is unavailable.") from error


def iter_records() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(records_root().glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            value.setdefault("application_id", path.stem)
            records.append(value)
    return records


def remove_record(application_id: str) -> None:
    (records_root() / f"{safe_id(application_id)}.json").unlink(missing_ok=True)


MIME_TYPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*")


def parse_desktop_entry(contents: str) -> dict[str, str]:
    """The [Desktop Entry] keys of a launcher, as written."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read_string(contents)
        return dict(parser["Desktop Entry"])
    except (configparser.Error, KeyError) as error:
        raise InstallerError(f"The launcher metadata is malformed: {error}") from error


def desktop_mime_types(contents: str) -> list[str]:
    """The files and links the application says it opens.

    A sign-in that finishes in the browser returns through a link like
    claude:// or vscode://; if the installed launcher does not claim that
    scheme, the browser has nowhere to send it and the sign-in never completes.
    """
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(contents)
        value = parser["Desktop Entry"].get("MimeType", "")
    except (configparser.Error, KeyError):
        return []
    return [item for item in (part.strip() for part in value.split(";")) if item and MIME_TYPE.fullmatch(item)][:64]


SCHEME_PREFIX = "x-scheme-handler/"

# Links the session itself answers for. An application that declares it can open
# one of these stays a choice the person can pick, but Luma never makes it the
# default: a desktop application declaring x-scheme-handler/https must not become
# the web browser, and one declaring mailto must not become the mail client.
SYSTEM_SCHEMES = frozenset({
    "http", "https", "ftp", "ftps", "file", "mailto", "tel", "sms", "callto",
    "webcal", "about", "unknown", "news", "nntp", "irc", "ircs",
})


def scheme_of(mime: str) -> str:
    """The URL scheme a handler type names, or '' for anything else."""
    return mime[len(SCHEME_PREFIX):] if mime.startswith(SCHEME_PREFIX) else ""


def capsule_declared_schemes(private_home: Path) -> list[str]:
    """The link schemes the application registered for itself after install.

    An Electron application calls setAsDefaultProtocolClient the first time it
    runs, which writes the scheme into ~/.config/mimeapps.list. Inside a capsule
    that home is private, so the registration never reaches the session where
    the browser asks who should open the link. These are schemes the application
    declared about itself, so Luma republishes them against the launcher it
    wrote, under the application's own name.
    """
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string((private_home / ".config/mimeapps.list").read_text(encoding="utf-8", errors="replace"))
    except (OSError, UnicodeError, configparser.Error):
        return []
    found: list[str] = []
    for section in ("Default Applications", "Added Associations"):
        if not parser.has_section(section):
            continue
        for key in parser[section]:
            mime = key.strip()
            if scheme_of(mime) and MIME_TYPE.fullmatch(mime) and mime not in found:
                found.append(mime)
    return found[:64]


def _launcher_installed(desktop_id: str) -> bool:
    """Whether `desktop_id` still names a launcher this session can resolve."""
    if not desktop_id or "/" in desktop_id or not desktop_id.endswith(".desktop"):
        return False
    directories = [applications_root()]
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    directories.extend(Path(value) / "applications" for value in data_dirs.split(":") if value)
    return any((directory / desktop_id).is_file() for directory in directories)


def register_handlers(destination: Path, mime_types: list[str]) -> None:
    """Refresh the handler cache, then point the application's own link schemes
    at the launcher Luma wrote for it.

    The prompt a browser shows before it hands a link to a desktop application
    names that application by reading Name from whichever desktop entry holds
    the scheme; with the scheme unheld it prints the browser's own fallback
    command instead, which reads "xdg-open". Holding the scheme with this
    launcher is what makes that prompt read the application's real name.
    """
    if not mime_types:
        return
    subprocess.run(["update-desktop-database", str(destination.parent)], check=False, capture_output=True)
    for mime in mime_types:
        scheme = scheme_of(mime)
        if not scheme or scheme in SYSTEM_SCHEMES:
            continue
        current = subprocess.run(["xdg-mime", "query", "default", mime],
                                 check=False, capture_output=True, text=True).stdout.strip()
        # Claim the scheme when nothing holds it, when this launcher already
        # holds it (a rewritten entry keeps its links), and when the holder is a
        # stale pointer to a launcher that is no longer installed. An entry that
        # is really there belongs to some application, so it is left alone.
        if current and current != destination.name and _launcher_installed(current):
            continue
        subprocess.run(["xdg-mime", "default", destination.name, mime], check=False, capture_output=True)


def refresh_icon_cache() -> None:
    """Keep the user's icon cache in step with the icons Luma writes.

    GTK and the Shell trust an icon-theme.cache that is newer than the theme
    folder itself, and adding a file to 128x128/apps does not change that
    folder: once any tool has written a cache, every icon installed afterwards
    is invisible and the application shows a generic icon. The cache is
    regenerated whenever one exists.
    """
    root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "icons/hicolor"
    if (root / "icon-theme.cache").exists():
        subprocess.run(["gtk-update-icon-cache", "--force", "--ignore-theme-index", "--quiet", str(root)],
                       check=False, capture_output=True)


def write_launcher(application_id: str, name: str, comment: str, icon: str = "application-x-executable", startup_wm_class: str = "", terminal: bool = False, mime_types: list[str] | None = None) -> Path:
    root = applications_root()
    root.mkdir(mode=0o755, parents=True, exist_ok=True)
    destination = root / f"org.projectluma.Installed.{application_id}.desktop"
    mime_types = list(dict.fromkeys(mime for mime in (mime_types or []) if MIME_TYPE.fullmatch(mime)))
    contents = "\n".join((
        "[Desktop Entry]",
        "Type=Application",
        f"Name={name.replace(chr(10), ' ')}",
        f"Comment={comment.replace(chr(10), ' ')}",
        f"Icon={icon}",
        f"Exec=/usr/bin/luma-capsule-launch {application_id}" + (" %U" if mime_types else ""),
        *([f"MimeType={';'.join(mime_types)};"] if mime_types else []),
        # A console payload asks the desktop for a terminal; claiming otherwise
        # launches it into a closed pipe, where it exits without ever drawing.
        f"Terminal={'true' if terminal else 'false'}",
        f"StartupNotify={'false' if terminal else 'true'}",
        *([f"StartupWMClass={startup_wm_class}"] if startup_wm_class and not any(c in startup_wm_class for c in "\n\r\x00") else []),
        "Categories=Utility;",
        "X-Luma-Managed=true",
        "Actions=Uninstall;",
        "",
        "[Desktop Action Uninstall]",
        "Name=Uninstall",
        f"Exec=/usr/bin/luma-install --remove {quote_exec_argument(application_id)}",
        "",
    ))
    temporary = destination.with_suffix(".desktop.partial")
    temporary.write_text(contents, encoding="utf-8")
    temporary.chmod(0o644)
    temporary.replace(destination)
    refresh_icon_cache()
    register_handlers(destination, mime_types)
    return destination


def command_name(command: str) -> str:
    """The shell name a console payload should answer to, or '' if it has none."""
    head = command.split()[0] if command.split() else ""
    candidate = Path(head).name
    return candidate if COMMAND_NAME.fullmatch(candidate) else ""


def _shim_owner(path: Path) -> bool:
    try:
        return SHIM_MARKER in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def export_command(application_id: str, name: str) -> tuple[str, str]:
    """Publish a console payload under `name` on the user's PATH.

    Returns (exported_name, reason_not_exported). Exactly one is non-empty.
    Refuses to shadow a command the user already has: ~/.local/bin sits ahead of
    /usr/bin, so an unchecked export would silently replace a system tool.
    """
    application_id = safe_id(application_id)
    if not COMMAND_NAME.fullmatch(name):
        return "", "the package did not declare a usable command name"
    root = commands_root()
    destination = root / name
    existing = shutil.which(name)
    if existing and Path(existing).resolve(strict=False) != destination.resolve(strict=False):
        return "", f"the name is already taken by {existing}"
    if destination.exists() and not _shim_owner(destination):
        return "", f"{destination} already exists and is not Luma-managed"
    root.mkdir(mode=0o755, parents=True, exist_ok=True)
    contents = "\n".join((
        "#!/bin/sh",
        SHIM_MARKER,
        f"# Runs the {name} payload inside its Luma application capsule.",
        f"# Remove it with: luma-install --remove {quote_exec_argument(application_id)}",
        f"exec /usr/bin/luma-capsule-launch {quote_exec_argument(application_id)} \"$@\"",
        "",
    ))
    temporary = destination.with_name(f".{name}.luma-partial")
    temporary.write_text(contents, encoding="utf-8")
    temporary.chmod(0o755)
    temporary.replace(destination)
    return name, ""


def remove_command(name: object) -> None:
    if not isinstance(name, str) or not COMMAND_NAME.fullmatch(name):
        return
    destination = commands_root() / name
    if destination.is_file() and _shim_owner(destination):
        destination.unlink(missing_ok=True)


def desktop_is_console(contents: str) -> bool:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(contents)
    except configparser.Error:
        return False
    if "Desktop Entry" not in parser:
        return False
    entry = parser["Desktop Entry"]
    if entry.get("Terminal", "false").strip().lower() == "true":
        return True
    categories = entry.get("Categories", "")
    return "ConsoleOnly" in categories.split(";")


def parse_desktop(contents: str) -> tuple[str, str, str, str]:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(contents)
    except configparser.Error as error:
        raise InstallerError(f"The package's launcher metadata is malformed: {error}") from error
    if "Desktop Entry" not in parser:
        raise InstallerError("The package did not provide a desktop application entry.")
    entry = parser["Desktop Entry"]
    name = entry.get("Name", "Application")
    comment = entry.get("Comment", "Installed application")
    icon = entry.get("Icon", "application-x-executable")
    command = entry.get("Exec", "").strip()
    # Desktop field codes are metadata, not arguments inside the capsule.
    command = re.sub(r"\s+%[fFuUdDnNickvm]", "", command).replace("%%", "%")
    if not command or any(token in command for token in ("\n", "\r", "\x00")):
        raise InstallerError("The package did not declare a safe launcher command.")
    return name, comment, icon, command


def desktop_window_class(contents: str, desktop_path: str) -> str:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(contents)
    entry = parser["Desktop Entry"]
    value = entry.get("StartupWMClass", Path(desktop_path).stem)
    return value if value and not any(c in value for c in "\n\r\x00") else ""
