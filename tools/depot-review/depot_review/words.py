# SPDX-License-Identifier: Apache-2.0
"""Depot's eleven permission words, and what a package can reach in them.

A developer declares permissions in the eleven words people see in Depot
(Depot handoff 8.11). This module turns what a package can actually reach into
the same words, so review can compare the two: a package that reaches more
than its listing declares fails checks.

Flatpak apps are read through scripts/depot/flatpak-permissions.py, the same
code that computes the permissions shown for Luma's own apps. Each key it
emits maps to one or more words here. Universal packages (.rpm/.deb, ADR-050)
run in a capsule that sees the person's home folder and the network, so those
two are always required, plus whatever the package installs into the system.
"""
from __future__ import annotations

import configparser
import importlib.util
from pathlib import Path

WORDS = ("Camera", "Microphone", "Location", "Files", "Internet", "Notifications",
         "Screen", "Other apps", "Passwords", "Devices", "System")

_REPO = Path(__file__).resolve().parents[3]

# Session bus names that hold secrets or keys.
_PASSWORD_NAMES = ("org.freedesktop.secrets", "org.gnome.keyring", "org.kde.kwalletd",
                   "org.kde.kwalletd5", "org.kde.kwalletd6", "org.freedesktop.impl.secrets")
# Session bus names that can see or record the screen.
_SCREEN_NAMES = ("org.gnome.Shell.Screenshot", "org.gnome.Shell.Screencast",
                 "org.gnome.Mutter.ScreenCast", "org.gnome.Mutter.RemoteDesktop",
                 "org.kde.KWin.ScreenShot2")
# System bus names that only report state and change nothing.
_HARMLESS_SYSTEM = ("org.freedesktop.UPower", "org.freedesktop.timedate1",
                    "org.freedesktop.hostname1", "org.freedesktop.locale1")

KEY_WORDS = {
    "network": ("Internet",),
    "files.documents": ("Files",), "files.pictures": ("Files",), "files.music": ("Files",),
    "files.videos": ("Files",), "files.downloads": ("Files",), "files.other": ("Files",),
    "files.removable": ("Files",), "files.home": ("Files",), "files.host": ("Files", "System"),
    "devices.camera": ("Camera",),
    "devices.microphone": ("Microphone",),
    "devices.all": ("Devices", "Camera"),
    "devices.input": ("Devices",), "devices.usb": ("Devices",),
    "devices.bluetooth": ("Devices",), "devices.smartcard": ("Devices",),
    "devices.kvm": ("Devices",),
    "notifications": ("Notifications",),
    "location": ("Location",),
    "display.x11": ("Screen",),
    "system.ssh-agent": ("Passwords",), "system.gpg-agent": ("Passwords",),
    "sandbox.escape": ("System",), "sandbox.devel": ("System",),
    "system.bus": ("System",),
    # Granted to every app or not one of the words: never listed.
    "files.portal": (), "printing": (), "background": (),
}

# What each word means, for messages to developers.
PLAIN = {
    "Camera": "use the camera",
    "Microphone": "record sound from the microphone",
    "Location": "see where the device is",
    "Files": "read or change files outside its own folder",
    "Internet": "use the internet",
    "Notifications": "show notifications",
    "Screen": "see or record the screen and other windows",
    "Other apps": "talk to other apps directly",
    "Passwords": "read saved passwords and keys",
    "Devices": "use connected devices directly",
    "System": "change the system outside its sandbox",
}


def _flatpak_permissions_module():
    path = _REPO / "scripts/depot/flatpak-permissions.py"
    spec = importlib.util.spec_from_file_location("flatpak_permissions", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_metadata(text: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read_string(text)
    return parser


def _bus_words(names: list[str]) -> set[str]:
    words: set[str] = set()
    for name in names:
        if name == "*":
            words |= {"Other apps", "System", "Passwords", "Screen"}
        elif name.startswith(_PASSWORD_NAMES):
            words.add("Passwords")
        elif name.startswith(_SCREEN_NAMES):
            words.add("Screen")
        else:
            words.add("Other apps")
    return words


def flatpak_reach(metadata_text: str) -> dict:
    """What a Flatpak's metadata lets it reach.

    Returns {"words": {word: [why, ...]}, "notes": [str], "keys": [entry]}.
    `why` names the grant in the package, for the developer's message.
    """
    module = _flatpak_permissions_module()
    metadata = read_metadata(metadata_text)
    entries = module.compute(metadata)
    context = metadata["Context"] if metadata.has_section("Context") else {}
    sockets = [s for s in (context.get("sockets") or "").split(";") if s]
    filesystems = [s for s in (context.get("filesystems") or "").split(";") if s]
    words: dict[str, list[str]] = {}
    notes: list[str] = []

    def add(word: str, why: str) -> None:
        words.setdefault(word, [])
        if why not in words[word]:
            words[word].append(why)

    for entry in entries:
        key = entry["key"]
        names = entry.get("names") or []
        if key == "devices.microphone":
            # The sound socket alone is how apps play sound. It can also record,
            # which review sees, but playing sound is not a permission.
            if "xdg-run/pipewire-0" in [f.split(":", 1)[0] for f in filesystems] or "devices.all" in {
                    e["key"] for e in entries}:
                add("Microphone", "access to the PipeWire audio socket")
            elif "pulseaudio" in sockets:
                notes.append("Plays sound through the PulseAudio socket, which can also record. "
                             "Not listed as Microphone; prompts at first use will cover recording.")
            continue
        if key == "session.bus":
            for word in sorted(_bus_words(names)):
                add(word, "session bus: " + ", ".join(names))
            continue
        if key == "system.bus":
            relevant = [n for n in names if n not in _HARMLESS_SYSTEM]
            if relevant:
                add("System", "system bus: " + ", ".join(relevant))
            continue
        mapped = KEY_WORDS.get(key)
        if mapped is None:
            # A key this mapper has never seen is treated as the widest word,
            # so an unknown grant can never slip through as nothing.
            add("System", "unrecognised grant " + key)
            continue
        why = key
        if names:
            why += " (" + ", ".join(names) + ")"
        for word in mapped:
            add(word, why)
    return {"words": {w: words[w] for w in WORDS if w in words}, "notes": notes, "keys": entries}


def package_reach(files: list[str], scripts: dict[str, str]) -> dict:
    """What a universal package (.rpm/.deb) reaches when installed as a capsule.

    `files` are the paths the package installs; `scripts` maps maintainer
    script names to their text.
    """
    words = {"Files": ["capsules see your home folder"], "Internet": ["capsules share the network"]}
    notes: list[str] = []
    refusals: list[str] = []

    def add(word: str, why: str) -> None:
        words.setdefault(word, [])
        if why not in words[word]:
            words[word].append(why)

    for path in files:
        if path.startswith(("/lib/modules/", "/usr/lib/modules/", "/usr/src/")) or path.endswith(".ko") \
                or path.endswith((".ko.xz", ".ko.zst")) or "/dkms" in path:
            refusals.append(f"installs a kernel module ({path}); Luma doesn't accept kernel modules")
        elif path.startswith(("/usr/lib/systemd/system/", "/lib/systemd/system/", "/etc/systemd/system/")):
            add("System", "installs a system service " + Path(path).name)
        elif path.startswith(("/usr/lib/udev/rules.d/", "/lib/udev/rules.d/", "/etc/udev/rules.d/")):
            add("Devices", "installs device rules " + Path(path).name)
        elif path.startswith(("/etc/sudoers", "/usr/lib/polkit-1/", "/usr/share/polkit-1/actions/",
                              "/etc/polkit-1/")):
            add("System", "installs an administrator rule " + Path(path).name)
        elif path.startswith("/usr/share/dbus-1/system"):
            add("System", "installs a system bus service " + Path(path).name)
    for name, text in scripts.items():
        if text.strip():
            add("System", f"runs a {name} script during install")
    if not any(p.endswith(".desktop") and "/applications/" in p for p in files):
        notes.append("Installs no launcher (.desktop file) in share/applications.")
    return {"words": {w: words[w] for w in WORDS if w in words}, "notes": notes, "refusals": refusals}


def compare(declared: list[str], detected: dict[str, list[str]]) -> list[dict]:
    """Words the package reaches that the listing does not declare."""
    missing = []
    for word in WORDS:
        if word in detected and word not in declared:
            missing.append({
                "word": word,
                "message": (f"Your app can {PLAIN[word]}, but your listing doesn't include "
                            f"{word}. Add {word} with a short reason, or remove it from the app."),
                "detail": "; ".join(detected[word]),
            })
    return missing


def normalise_declared(declared) -> list[str]:
    """Accept ["Files", ...] or [{"word": "Files", "reason": "..."}]."""
    out = []
    for item in declared or []:
        word = item.get("word") if isinstance(item, dict) else item
        if word in WORDS and word not in out:
            out.append(word)
    return out
