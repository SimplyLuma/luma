# SPDX-License-Identifier: MPL-2.0
"""``apt`` and ``apt-get`` on Luma: Debian's words, Fedora's packages.

Guides for Ubuntu and Debian say ``sudo apt install firefox``. On Luma that
works: the command is translated to Luma's dnf, which says so in one line and
runs it::

    Luma uses dnf; running: sudo dnf install firefox

Debian package names that differ on Fedora are translated (a table of known
exceptions and the ``-dev`` -> ``-devel`` pattern); a name Fedora does not have
is answered with the Fedora packages that provide or match it. Nothing here
emulates dpkg: ``.deb`` files open in Depot's installer, which unpacks them
the supported way.

Exit codes follow apt: 0 on success, 100 on failure.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import debian_names, depot_catalog, depot_install

DNF = os.environ.get("LUMA_APT_DNF", "/usr/bin/dnf")
DNF5 = os.environ.get("LUMA_DNF5", "/usr/bin/dnf5")
INSTALLER = os.environ.get("LUMA_APT_INSTALLER", "/usr/bin/luma-installer")

APT_FAILURE = 100

#: apt's value-less options Luma understands.
YES = {"-y", "--yes", "--assume-yes"}
QUIET = {"-q", "-qq", "--quiet"}


@dataclass
class Request:
    command: str = ""
    packages: list[str] = field(default_factory=list)
    yes: bool = False
    quiet: bool = False
    no_recommends: bool = False
    installed: bool = False
    upgradable: bool = False
    purge: bool = False
    unknown: list[str] = field(default_factory=list)


def parse(argv: list[str]) -> Request:
    request = Request()
    positional: list[str] = []
    skip = False
    for index, item in enumerate(argv):
        if skip:
            skip = False
            continue
        if item in YES or (item.startswith("-") and not item.startswith("--") and "y" in item[1:]
                           and set(item[1:]) <= set("yqV")):
            request.yes = True
            if "q" in item:
                request.quiet = True
        elif item in QUIET:
            request.quiet = True
        elif item == "--no-install-recommends":
            request.no_recommends = True
        elif item == "--installed":
            request.installed = True
        elif item == "--upgradable":
            request.upgradable = True
        elif item == "--purge":
            request.purge = True
        elif item in ("-o", "-c", "-t", "--target-release", "--option", "--config-file"):
            skip = True
            request.unknown.append(item)
        elif item.startswith("-"):
            request.unknown.append(item)
        else:
            positional.append(item)
    if positional:
        request.command = positional[0]
        request.packages = positional[1:]
    return request


@dataclass
class Resolution:
    fedora: list[str] = field(default_factory=list)      # names to hand to dnf
    renamed: dict[str, list[str]] = field(default_factory=dict)   # debian name -> fedora names, when different
    unresolved: dict[str, list[str]] = field(default_factory=dict)  # debian name -> suggestions
    debs: list[str] = field(default_factory=list)
    not_needed: list[str] = field(default_factory=list)


def _exists(name: str, runner) -> bool:
    """Fedora's repositories (or this computer) have a package or group called ``name``."""
    if name.startswith("@"):
        result = runner([DNF5, "group", "info", "--quiet", name[1:]], stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, text=True)
        return result.returncode == 0 and bool((result.stdout or "").strip())
    result = runner([DNF5, "repoquery", "--quiet", "--latest-limit=1", "--qf", "%{name}\n", name],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    return result.returncode == 0 and name in (result.stdout or "").split()


def _suggest(name: str, runner) -> list[str]:
    """Fedora packages that provide ``name`` or match it by name."""
    found: list[str] = []
    for query in ([DNF5, "repoquery", "--quiet", "--latest-limit=1", "--qf", "%{name}\n", "--whatprovides", name],
                  [DNF5, "repoquery", "--quiet", "--latest-limit=1", "--qf", "%{name}\n", f"*{name}*"]):
        result = runner(query, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        for item in (result.stdout or "").split():
            if item not in found:
                found.append(item)
        if found:
            break
    return found[:8]


def resolve(packages: list[str], runner=subprocess.run) -> Resolution:
    resolution = Resolution()
    for package in packages:
        if package.endswith(".deb"):
            resolution.debs.append(package)
            continue
        name, _, _version = package.partition("=")    # apt's name=version
        name = name.split(":", 1)[0]                   # apt's name:arch
        name = name.lower()                             # package names are conventionally lowercase
        if debian_names.is_known(name):
            mapped = list(debian_names.candidates(name))
            if not mapped:
                resolution.not_needed.append(name)
                continue
            resolution.fedora += [item for item in mapped if item not in resolution.fedora]
            if mapped != [name]:
                resolution.renamed[name] = mapped
            continue
        chosen = next((item for item in debian_names.candidates(name) if _exists(item, runner)), None)
        if chosen:
            if chosen not in resolution.fedora:
                resolution.fedora.append(chosen)
            if chosen != name:
                resolution.renamed[name] = [chosen]
        else:
            resolution.unresolved[name] = _suggest(name, runner)
    return resolution


def translate(request: Request) -> list[str] | None:
    """The dnf command for an apt request that needs no package lookup, or None."""
    yes = ["-y"] if request.yes else []
    quiet = ["-q"] if request.quiet else []
    command = request.command
    if command == "update":
        return ["makecache", "--refresh", *quiet]
    if command in ("upgrade", "full-upgrade", "dist-upgrade"):
        return ["upgrade", *yes, *quiet]
    if command == "search":
        return ["search", *request.packages]
    if command in ("show", "showpkg", "policy"):
        return ["info", *request.packages]
    if command == "list":
        if request.upgradable:
            return ["check-upgrade"]
        return ["list", "--installed" if request.installed else "--available", *request.packages] \
            if (request.installed or request.packages) else ["list", "--installed"]
    if command == "autoremove":
        return ["autoremove", *yes]
    if command in ("clean", "autoclean"):
        return ["clean", "all"]
    if command == "depends":
        return ["repoquery", "--requires", *request.packages]
    if command == "rdepends":
        return ["repoquery", "--whatrequires", *request.packages]
    return None


def say(text: str, stream=None) -> None:
    print(text, file=stream or sys.stderr, flush=True)


def run_dnf(arguments: list[str], request: Request, *, sudo: bool, runner) -> int:
    shown = ("sudo " if sudo else "") + "dnf " + " ".join(shlex.quote(item) for item in arguments)
    say(f"Luma uses dnf; running: {shown}")
    result = runner([DNF, *arguments])
    return 0 if result.returncode == 0 else APT_FAILURE


def main(argv: list[str] | None = None, *, runner=subprocess.run, euid: int | None = None,
         program: str = "apt") -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    euid = os.geteuid() if euid is None else euid
    request = parse(argv)
    sudo = euid == 0
    if not request.command or request.command in ("help", "--help", "-h"):
        say(f"Luma uses dnf. {program} understands: install, remove, purge, search, show, update, upgrade, "
            "list [--installed|--upgradable], autoremove. Example: sudo apt install firefox", sys.stdout)
        return 0
    command = request.command
    if command == "get":
        # "apt get PACKAGE" is a common slip (apt-get's own verb, typed after
        # apt): understood as install, said once, plainly.
        say(f'{program} has no "get" command; running {program} install instead.')
        command = request.command = "install"

    simple = translate(request)
    if simple is not None:
        return run_dnf(simple, request, sudo=sudo, runner=runner)

    if command in ("install", "reinstall", "remove", "purge"):
        if not request.packages:
            say(f"E: No packages given. Example: sudo {program} {command} firefox")
            return APT_FAILURE
        if command == "install" and any(item.endswith(".deb") for item in request.packages):
            debs = [item for item in request.packages if item.endswith(".deb")]
            say(f"Luma installs .deb files with its app installer; opening {', '.join(debs)} there.")
            for deb in debs:
                path = str(Path(deb).expanduser().resolve())
                user = os.environ.get("SUDO_USER")
                open_command = [INSTALLER, path]
                if sudo and user:
                    open_command = ["/usr/sbin/runuser", "-u", user, "--", *open_command]
                if runner(open_command).returncode != 0:
                    return APT_FAILURE
            request.packages = [item for item in request.packages if not item.endswith(".deb")]
            if not request.packages:
                return 0
        if command in ("remove", "purge"):
            names = []
            for package in request.packages:
                name = package.split("=", 1)[0].split(":", 1)[0]
                mapped = list(debian_names.candidates(name)) if debian_names.is_known(name) else [name]
                names += [item for item in mapped if not item.startswith("@")]
            if not names:
                say("Nothing to do.")
                return 0
            arguments = ["remove", *(["-y"] if request.yes else []), *names]
            return run_dnf(arguments, request, sudo=sudo, runner=runner)

        resolution = resolve(request.packages, runner)
        for name in resolution.not_needed:
            say(f"{name}: Fedora needs nothing for this; skipping.")
        for name, mapped in resolution.renamed.items():
            say(f"{name} is called {' '.join(mapped)} on Fedora.")

        # A name Fedora's own repositories don't have may still be an
        # application Depot knows how to get, usually from Flathub.
        flatpak_installs: list = []
        still_unresolved: dict[str, list[str]] = {}
        for name, suggestions in resolution.unresolved.items():
            matches = depot_catalog.find(name)
            if not matches:
                still_unresolved[name] = suggestions
                continue
            try:
                interactive = sys.stdin.isatty()
            except (AttributeError, ValueError, OSError):
                interactive = False
            match = depot_install.choose(matches, say, interactive=interactive, read_line=sys.stdin.readline)
            if match is None:
                return APT_FAILURE
            if match.backend == "flatpak":
                flatpak_installs.append(match)
            elif _exists(match.source_id, runner):
                resolution.fedora.append(match.source_id)
                resolution.renamed[name] = [match.source_id]
            else:
                say(f"{match.name} is in Depot's catalogue but needs its own software source, which "
                    f"isn't set up on this computer yet. Install it from Depot instead.")
                return APT_FAILURE
        resolution.unresolved = still_unresolved

        if resolution.unresolved:
            for name, suggestions in resolution.unresolved.items():
                if suggestions:
                    say(f"E: Fedora has no package named {name}. These Fedora packages provide or match it: "
                        + ", ".join(suggestions))
                else:
                    say(f"E: Unable to locate package {name}, and no Fedora package provides or matches it. "
                        f"Try: dnf search {name}")
            return APT_FAILURE

        result = 0
        if resolution.fedora:
            arguments = ["install", *(["-y"] if request.yes else [])]
            if request.no_recommends:
                arguments.append("--setopt=install_weak_deps=False")
            result = run_dnf(arguments + resolution.fedora, request, sudo=sudo, runner=runner)
        for match in flatpak_installs:
            code = depot_install.install_flatpak(match, say, runner)
            result = code if code != 0 else result
        if not resolution.fedora and not flatpak_installs:
            say("Nothing to do.")
            return 0
        return result

    # Not a known verb at all: "apt-get Vivaldi" or "apt Vivaldi" with the
    # package typed where the command goes. Treat it as install when the
    # word resolves to something installable (Fedora's own repositories,
    # Debian's name table, or Depot's catalogue); otherwise say so plainly.
    probe = resolve([command], runner)
    resolvable = bool(probe.fedora or probe.not_needed or probe.renamed) or \
        (command.lower() in probe.unresolved and bool(depot_catalog.find(command)))
    if resolvable:
        say(f"Installing {command}…")
        return main(["install", command, *request.packages], runner=runner, euid=euid, program=program)
    say(f'E: {program}: "{command}" is not a Luma command. To install a package: sudo {program} install '
        f"{command}. Depot (the app store) can find and install it too.")
    return APT_FAILURE


def entry() -> None:
    raise SystemExit(main(program=Path(sys.argv[0]).name))
