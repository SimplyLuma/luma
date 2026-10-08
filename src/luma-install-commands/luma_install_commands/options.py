# SPDX-License-Identifier: MPL-2.0
"""dnf5's command line, read the way dnf5 reads it.

Only what Luma needs to decide what to do is understood: the command, its
package arguments, and the options that change the answer (assume yes/no,
refresh, repository selection, and the options Luma cannot honor on an image
system). Everything else is kept verbatim and handed to dnf5 or rpm-ostree.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: dnf5 options that take a value, as ``--name value`` or ``--name=value``.
VALUE_OPTIONS = frozenset({
    "--repo", "--repoid", "--enablerepo", "--disablerepo", "--setopt", "--setvar",
    "--config", "-c", "--installroot", "--releasever", "--use-host-config",
    "--exclude", "-x", "--repofrompath", "--from-repo", "--comment", "--debuglevel",
    "-d", "--rpmverbosity", "--forcearch", "--color", "--qf", "--queryformat",
    "--advisories", "--advisory-severities", "--bzs", "--cves", "--whatprovides",
    "--whatrequires", "--whatdepends", "--arch", "--installonly-limit", "--dump-main-config",
    "--dump-repo-config", "--destdir", "--downloaddir", "--output", "--latest-limit",
    "--nevra", "--providers-of", "--srpm", "--what",
})

#: Short options that take a value.
SHORT_VALUE = frozenset({"-c", "-x", "-d", "-R", "-e"})

#: Aliases dnf5 accepts for the commands Luma handles itself.
COMMAND_ALIASES = {
    "in": "install", "install": "install", "localinstall": "install",
    "rm": "remove", "remove": "remove", "erase": "remove",
    "up": "upgrade", "upgrade": "upgrade", "update": "upgrade", "distro-sync": "distro-sync",
    "dsync": "distro-sync", "distrosync": "distro-sync", "upgrade-minimal": "upgrade",
    "update-minimal": "upgrade",
    "reinstall": "reinstall", "rei": "reinstall", "downgrade": "downgrade", "dg": "downgrade",
    "swap": "swap", "autoremove": "autoremove", "group": "group", "groups": "group", "grp": "group",
    "check-update": "check-upgrade", "check-upgrade": "check-upgrade",
    "history": "history", "offline": "offline", "system-upgrade": "system-upgrade",
    "builddep": "builddep", "build-dep": "builddep", "mark": "mark",
}

#: Commands that only read, or write nothing outside dnf's own cache and
#: configuration. dnf5 runs them unchanged, exactly as on Fedora Workstation.
READ_ONLY = frozenset({
    "search", "se", "info", "if", "list", "ls", "provides", "prov", "repoquery", "rq",
    "check-upgrade", "check-update", "repolist", "repoinfo", "repo", "makecache", "mc",
    "clean", "help", "advisory", "updateinfo", "leaves", "environment", "module",
    "config-manager", "copr", "download", "changelog", "versionlock", "needs-restarting",
    "debuginfo-install", "repoclosure", "repomanage", "reposync", "check", "automatic",
})


@dataclass
class Parsed:
    command: str = ""            # canonical command ("install", "remove", ...) or dnf5's word
    word: str = ""               # the word the person typed
    args: list[str] = field(default_factory=list)       # positional arguments after the command
    options: list[str] = field(default_factory=list)    # every option, verbatim, in order
    assume_yes: bool = False
    assume_no: bool = False
    refresh: bool = False
    quiet: bool = False
    transient: bool = False      # dnf5's own bootc overlay mode, asked for explicitly
    installroot: bool = False    # acting on another root, never this system
    downloadonly: bool = False
    nogpgcheck: bool = False
    allowerasing: bool = False
    help: bool = False
    version: bool = False
    offline: bool = False
    repos: list[str] = field(default_factory=list)          # --repo / --enablerepo values
    disabled_repos: list[str] = field(default_factory=list)
    setopts: list[str] = field(default_factory=list)


def parse(argv: list[str]) -> Parsed:
    """Split dnf5 arguments. Options may come before or after the command."""
    parsed = Parsed()
    index = 0
    positional: list[str] = []
    while index < len(argv):
        item = argv[index]
        index += 1
        if item == "--":
            positional += argv[index:]
            break
        if not item.startswith("-") or item == "-":
            positional.append(item)
            continue
        name, has_value, value = item.partition("=")
        takes_value = name in VALUE_OPTIONS or (len(item) == 2 and item in SHORT_VALUE)
        if takes_value and not has_value:
            if index < len(argv):
                value = argv[index]
                parsed.options += [item, value]
                index += 1
            else:
                parsed.options.append(item)
        else:
            parsed.options.append(item)
        if name in ("-y", "--assumeyes"):
            parsed.assume_yes = True
        elif name in ("-n", "--assumeno"):
            parsed.assume_no = True
        elif name == "--refresh":
            parsed.refresh = True
        elif name in ("-q", "--quiet"):
            parsed.quiet = True
        elif name == "--transient":
            parsed.transient = True
        elif name == "--installroot":
            parsed.installroot = True
        elif name == "--downloadonly":
            parsed.downloadonly = True
        elif name in ("--nogpgcheck", "--no-gpgchecks"):
            parsed.nogpgcheck = True
        elif name == "--allowerasing":
            parsed.allowerasing = True
        elif name in ("-h", "--help"):
            parsed.help = True
        elif name == "--version":
            parsed.version = True
        elif name == "--offline":
            parsed.offline = True
        elif name in ("--repo", "--repoid", "--enablerepo"):
            parsed.repos += [v for v in value.split(",") if v]
        elif name == "--disablerepo":
            parsed.disabled_repos += [v for v in value.split(",") if v]
        elif name == "--setopt":
            parsed.setopts.append(value)
        elif item.startswith("-") and not item.startswith("--") and len(item) > 2 and "=" not in item:
            # Bundled short flags such as -yq.
            letters = item[1:]
            if "y" in letters:
                parsed.assume_yes = True
            if "q" in letters:
                parsed.quiet = True
    if positional:
        parsed.word = positional[0]
        parsed.command = COMMAND_ALIASES.get(positional[0], positional[0])
        parsed.args = positional[1:]
    return parsed
