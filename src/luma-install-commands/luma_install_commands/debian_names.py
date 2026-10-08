# SPDX-License-Identifier: MPL-2.0
"""Debian and Ubuntu package names, in Fedora's words.

Guides written for Debian name packages Debian's way. Most names are the same
on Fedora; the ones that are not follow two patterns (``-dev`` is ``-devel``,
``lib`` prefixes come and go) and a list of known exceptions. ``candidates``
returns the Fedora names to try, most likely first; the caller keeps the first
one Fedora's repositories actually have and otherwise asks dnf what provides
or matches the name.
"""

from __future__ import annotations

import re

#: Names that differ and follow no pattern. A value starting with ``@`` is a
#: Fedora package group; a tuple installs several packages; ``()`` means Fedora
#: needs nothing for it (the feature is built in or not applicable).
EXCEPTIONS: dict[str, tuple[str, ...]] = {
    "build-essential": ("@development-tools", "gcc-c++"),
    "g++": ("gcc-c++",),
    "libc6-dev": ("glibc-devel",),
    "libc-dev": ("glibc-devel",),
    "linux-headers-generic": ("kernel-devel",),
    "linux-libc-dev": ("kernel-headers",),
    "libssl-dev": ("openssl-devel",),
    "zlib1g-dev": ("zlib-ng-compat-devel",),
    "zlib1g": ("zlib-ng-compat",),
    "libbz2-dev": ("bzip2-devel",),
    "liblzma-dev": ("xz-devel",),
    "libncurses-dev": ("ncurses-devel",),
    "libncurses5-dev": ("ncurses-devel",),
    "libncursesw5-dev": ("ncurses-devel",),
    "libreadline-dev": ("readline-devel",),
    "libsqlite3-dev": ("sqlite-devel",),
    "libcurl4-openssl-dev": ("libcurl-devel",),
    "libcurl4-gnutls-dev": ("libcurl-devel",),
    "libgdbm-dev": ("gdbm-devel",),
    "uuid-dev": ("libuuid-devel",),
    "libjpeg-dev": ("libjpeg-turbo-devel",),
    "libjpeg-turbo8-dev": ("libjpeg-turbo-devel",),
    "libglib2.0-dev": ("glib2-devel",),
    "libgtk-3-dev": ("gtk3-devel",),
    "libgtk-4-dev": ("gtk4-devel",),
    "libgl1-mesa-dev": ("mesa-libGL-devel",),
    "libegl1-mesa-dev": ("mesa-libEGL-devel",),
    "libx11-dev": ("libX11-devel",),
    "libxext-dev": ("libXext-devel",),
    "libxrandr-dev": ("libXrandr-devel",),
    "libsdl2-dev": ("SDL2-devel",),
    "libudev-dev": ("systemd-devel",),
    "libsystemd-dev": ("systemd-devel",),
    "libdbus-1-dev": ("dbus-devel",),
    "libusb-1.0-0-dev": ("libusb1-devel",),
    "libpq-dev": ("libpq-devel",),
    "libmysqlclient-dev": ("mariadb-connector-c-devel",),
    "libboost-all-dev": ("boost-devel",),
    "libgmp-dev": ("gmp-devel",),
    "libevent-dev": ("libevent-devel",),
    "libasound2-dev": ("alsa-lib-devel",),
    "libpulse-dev": ("pulseaudio-libs-devel",),
    "libfreetype6-dev": ("freetype-devel",),
    "libfontconfig1-dev": ("fontconfig-devel",),
    "python3-dev": ("python3-devel",),
    "python-dev-is-python3": ("python3-devel",),
    "python3-venv": ("python3",),
    "python-is-python3": ("python-unversioned-command",),
    "pkg-config": ("pkgconf-pkg-config",),
    "golang-go": ("golang",),
    "rustc": ("rust",),
    "default-jdk": ("java-latest-openjdk-devel",),
    "default-jre": ("java-latest-openjdk",),
    "openssh-client": ("openssh-clients",),
    "dnsutils": ("bind-utils",),
    "bind9-dnsutils": ("bind-utils",),
    "iputils-ping": ("iputils",),
    "netcat": ("nmap-ncat",),
    "netcat-openbsd": ("netcat",),
    "netcat-traditional": ("nmap-ncat",),
    "vim": ("vim-enhanced",),
    "gnupg": ("gnupg2",),
    "xz-utils": ("xz",),
    "p7zip-full": ("7zip",),
    "docker.io": ("moby-engine",),
    "chromium-browser": ("chromium",),
    "firefox-esr": ("firefox",),
    "exa": ("eza",),
    "fd-find": ("fd-find",),
    "manpages-dev": ("man-pages",),
    "sqlite3": ("sqlite",),
    "apache2": ("httpd",),
    "apt-transport-https": (),
    "software-properties-common": (),
    "ubuntu-restricted-extras": (),
}

_OPENJDK = re.compile(r"^openjdk-(\d+)-(jdk|jre)(-headless)?$")
_LINUX_HEADERS = re.compile(r"^linux-headers-")
_VERSIONED_LIB_DEV = re.compile(r"^lib(.+?)[\d.]*-dev$")


def candidates(name: str) -> tuple[str, ...]:
    """Fedora names worth trying for the Debian package ``name``, best first.

    A known exception is returned alone (it may name several packages, which
    are all wanted). Otherwise the name itself comes first, since most names
    are shared, then what the ``-dev``/``lib`` patterns suggest.
    """
    if name in EXCEPTIONS:
        return EXCEPTIONS[name]
    match = _OPENJDK.match(name)
    if match:
        version, kind, headless = match.groups()
        suffix = "-devel" if kind == "jdk" else ("-headless" if headless else "")
        return (f"java-{version}-openjdk{suffix}",)
    if _LINUX_HEADERS.match(name):
        return ("kernel-devel",)
    out: list[str] = [name]
    if name.endswith("-dev"):
        stem = name[: -len("-dev")]
        out += [f"{stem}-devel"]
        if stem.startswith("lib"):
            out += [f"{stem[3:]}-devel"]
            versioned = _VERSIONED_LIB_DEV.match(name)
            if versioned:
                out += [f"lib{versioned.group(1)}-devel", f"{versioned.group(1)}-devel"]
    elif name.endswith("-dbg"):
        out += [f"{name[: -len('-dbg')]}-debuginfo"]
    seen: list[str] = []
    for item in out:
        if item not in seen:
            seen.append(item)
    return tuple(seen)


def is_known(name: str) -> bool:
    """True when ``name`` is in the exceptions table (resolved without guessing)."""
    return name in EXCEPTIONS or bool(_OPENJDK.match(name)) or bool(_LINUX_HEADERS.match(name))
