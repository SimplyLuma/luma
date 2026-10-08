# SPDX-License-Identifier: MPL-2.0
"""``dnf`` and ``yum`` on Luma: the commands Fedora guides use, working.

Luma's system lives in a read-only image (``/usr``), so dnf5 refuses to change
it. This front end keeps dnf's words and its questions and makes the change
the way an image-based system can:

* ``install`` resolves with dnf5 (same repositories, same table, same
  ``Is this ok [y/N]``), then adds the packages to the system image with
  rpm-ostree and applies them to the running system at once. They stay across
  Luma updates and rollbacks and are updated with the system.
* ``remove`` takes added packages away again, at once. A package that is part
  of Luma's image is removed at the next restart, after saying so.
* ``upgrade`` with no packages hands over to Luma's update service: it shows
  what is available and prepares it only when the person says yes. Nothing
  restarts by itself.
* Everything that only reads (search, info, list, provides, repoquery,
  check-upgrade, repolist, makecache, config-manager, copr, ...) is dnf5
  itself, unchanged.

Outside a booted image (toolbox, distrobox, containers, ``--installroot``) and
for ``dnf5`` itself nothing is intercepted. ``LUMA_DNF_PASSTHROUGH=1`` hands a
command to dnf5 unchanged.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import depot_catalog, depot_install, live, options, system, transaction, vitals

DNF5 = os.environ.get("LUMA_DNF5", system.DNF5)
RPM_OSTREE = os.environ.get("LUMA_RPM_OSTREE", system.RPM_OSTREE)
LUMA_UPDATE = os.environ.get("LUMA_UPDATE_COMMAND", "/usr/bin/luma-update")

ROOT_MESSAGE = "This command has to be run with superuser privileges (under the root user on most systems)."

#: Base packages dnf would never let go of, plus Luma's own foundations.
#: Removing or replacing them breaks the computer, so Luma refuses the way
#: dnf5 refuses to remove itself.
PROTECTED = frozenset({
    "dnf5", "libdnf5", "rpm", "rpm-ostree", "rpm-ostree-libs", "ostree", "ostree-libs", "bootc", "bootupd",
    "systemd", "systemd-libs", "systemd-udev", "glibc", "glibc-common", "sudo", "polkit", "selinux-policy",
    "selinux-policy-targeted", "kernel", "kernel-core", "kernel-modules", "kernel-modules-core", "grub2-efi-x64",
    "shim-x64", "dracut", "greenboot", "gnome-shell", "mutter", "gdm", "gnome-session", "NetworkManager",
    "luma-update", "luma-application-installer", "luma-install-commands", "luma-release", "python3",
})

REPO_WARNING_OPTIONS = ("--disablerepo", "--exclude", "-x", "--setopt")

#: Packages Luma builds itself (gnome-shell, mutter, GTK, ...), one name per
#: line, written at image build. Fedora's or anyone else's version of them must
#: never replace Luma's: they arrive only with Luma's own updates.
LUMA_OWNED = Path(os.environ.get("LUMA_OWNED_PACKAGES", "/usr/share/luma/luma-owned-packages.txt"))


_OWNED_CACHE: dict[str, set[str]] = {}


def owned_packages(ctx: "Context | None" = None) -> set[str]:
    if str(LUMA_OWNED) in _OWNED_CACHE:
        return _OWNED_CACHE[str(LUMA_OWNED)]
    try:
        text = LUMA_OWNED.read_text(encoding="utf-8")
    except OSError:
        _OWNED_CACHE[str(LUMA_OWNED)] = set()
        if ctx is not None:
            ctx.warn(f"Warning: {LUMA_OWNED} is missing, so Luma's own packages are not protected from "
                     "being replaced.")
        return set()
    names = {line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")}
    _OWNED_CACHE[str(LUMA_OWNED)] = names
    return names


def owned_message(names) -> str:
    names = sorted(set(names))
    return (f"Error: {_join(names)} {'is' if len(names) == 1 else 'are'} Luma's own build and "
            f"{'is' if len(names) == 1 else 'are'} updated only by Luma's updates; another version can't "
            "replace it.")


# ── Plumbing ─────────────────────────────────────────────────────────────────

@dataclass
class Context:
    """What a command needs from the outside world; tests replace it."""
    run: callable = subprocess.run
    popen: callable = subprocess.Popen
    out: object = sys.stdout
    err: object = sys.stderr
    stdin: object = sys.stdin
    euid: int = field(default_factory=os.geteuid)
    image_based: bool = field(default_factory=system.image_based)
    exec_: callable = os.execv

    def say(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    def warn(self, text: str) -> None:
        print(text, file=self.err, flush=True)


def passthrough(ctx: Context, argv: list[str]) -> int:
    """Become dnf5 with the same arguments."""
    ctx.exec_(DNF5, [DNF5, *argv])
    return 0  # only reached when exec_ is replaced in tests


def confirm(ctx: Context, parsed: options.Parsed, question: str = "Is this ok [y/N]: ") -> bool:
    """dnf's question, with dnf's answers: -y says yes, -n or no terminal says no."""
    if parsed.assume_yes:
        return True
    if parsed.assume_no:
        ctx.say("Operation aborted by the user.")
        return False
    try:
        if not ctx.stdin.isatty():
            ctx.say(question.rstrip())
            ctx.warn("Error: This command has to be run with --assumeyes when there is no terminal to answer on.")
            return False
    except (AttributeError, ValueError):
        return False
    print(question, end="", file=ctx.out, flush=True)
    answer = ctx.stdin.readline().strip().lower()
    if answer in ("y", "yes"):
        return True
    ctx.say("Operation aborted by the user.")
    return False


def dnf5_options(parsed: options.Parsed, *, drop_yes: bool = True) -> list[str]:
    """The person's options for a dnf5 preview: never let the preview say yes."""
    out: list[str] = []
    skip_next = False
    for index, item in enumerate(parsed.options):
        if skip_next:
            skip_next = False
            continue
        name = item.split("=", 1)[0]
        if drop_yes and name in ("-y", "--assumeyes", "-n", "--assumeno"):
            continue
        if drop_yes and item.startswith("-") and not item.startswith("--") and len(item) > 2 and "y" in item[1:] \
                and item not in options.SHORT_VALUE:
            letters = item[1:].replace("y", "")
            if letters:
                out.append("-" + letters)
            continue
        out.append(item)
    return out


def preview(ctx: Context, parsed: options.Parsed, verb: str, args: list[str]) -> transaction.Preview:
    """Ask dnf5 to resolve ``verb args`` and stop before doing anything."""
    command = [DNF5, verb, "--assumeno", *dnf5_options(parsed), *args]
    env = dict(os.environ, LANG=os.environ.get("LANG", "C.UTF-8"))
    process = ctx.popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    lines: list[str] = []
    # Repository loading shows dnf5's own progress as it happens; the table is
    # kept and shown once the request is understood.
    for line in process.stdout:
        lines.append(line.rstrip("\n"))
        stripped = line.strip()
        if stripped.startswith(("Updating and loading repositories", "Repositories loaded")) or " 100% |" in line:
            if not parsed.quiet:
                ctx.say(line.rstrip("\n"))
    returncode = process.wait()
    body = [line for line in lines
            if not (line.strip().startswith(("Updating and loading repositories", "Repositories loaded"))
                    or " 100% |" in line)]
    return transaction.parse("\n".join(body), returncode)


#: rpm-ostree lines that describe its internals or advise a reboot the
#: front end decides about itself.
RPM_OSTREE_NOISE = ("rpm-md repo ", "Enabled rpm-md repositories", "Changes queued for next boot",
                    'Run "systemctl reboot"', "Freed:", "Checking out tree", "Importing rpm-md",
                    "Applying ", 'Use "rpm-ostree override reset"', "Computing /etc diff", "Writing rpmdb",
                    "Writing OSTree commit", "Running systemd-sysusers", "Processing packages", "Importing packages",
                    "Checking out packages", "Staging deployment", "Updating /usr", "Updating /etc",
                    "Running systemd-tmpfiles", "Successfully updated running filesystem tree", "Inactive requests:",
                    "Following services may need to be restarted", "Deployment unchanged")


def rpm_ostree(ctx: Context, arguments: list[str], *, quiet: bool = False) -> tuple[int, str]:
    """Run rpm-ostree, showing its progress indented; returns (code, output)."""
    process = ctx.popen([RPM_OSTREE, *arguments], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    collected: list[str] = []
    in_diff = False
    for line in process.stdout:
        collected.append(line.rstrip("\n"))
        stripped = line.strip()
        # rpm-ostree's closing Added/Removed/Upgraded lists compare with the
        # booted image, so they repeat everything added before; dnf5's table
        # above already said what this change does.
        if stripped in ("Added:", "Removed:", "Upgraded:", "Downgraded:"):
            in_diff = True
            continue
        if in_diff and line.startswith("    "):
            continue
        in_diff = False
        if quiet or not stripped or stripped.startswith(RPM_OSTREE_NOISE):
            continue
        ctx.say("  " + line.rstrip("\n"))
    return process.wait(), "\n".join(collected)


def last_error(output: str) -> str:
    for line in reversed(output.splitlines()):
        if line.strip().startswith("error:"):
            return line.strip()[len("error:"):].strip()
    lines = [line for line in output.splitlines() if line.strip()]
    return lines[-1].strip() if lines else "rpm-ostree did not finish"


def restart_note(ctx: Context) -> None:
    ctx.say("Luma will not restart by itself. Restart when it suits you.")


# ── Commands ─────────────────────────────────────────────────────────────────

def is_local_package(arg: str) -> bool:
    return arg.endswith(".rpm") and (arg.startswith(("http://", "https://", "/", "./", "../", "~"))
                                     or os.path.exists(arg))


def _depot_fallback(ctx: Context, parsed: options.Parsed, rest: list[str],
                    result: transaction.Preview) -> tuple[str, object]:
    """dnf5 found no match for a single requested name: try it lower-cased,
    then Depot's catalogue (usually Flathub) for an application Fedora does
    not package (see ``depot_catalog`` for what that looks up and why it
    needs no trust of its own beyond flatpak's and dnf5's).

    Only handles one requested name at a time, so a multi-package request
    never installs some names while reporting the rest as failed.

    Returns ("retry", (new_result, new_rest)) for the caller to finish a
    rewritten request through the normal rpm-ostree path, ("done", exit_code)
    when a Flatpak install already finished the work, or ("unchanged", None)
    to show dnf5's own message as before.
    """
    if len(rest) != 1 or "No match for argument:" not in result.text:
        return "unchanged", None
    name = rest[0]
    if name.lower() != name:
        retry = preview(ctx, parsed, "install", [name.lower()])
        if retry.ok:
            return "retry", (retry, [name.lower()])
    matches = depot_catalog.find(name)
    if not matches:
        return "unchanged", None
    try:
        interactive = ctx.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        interactive = False
    match = depot_install.choose(matches, ctx.say, interactive=interactive, read_line=ctx.stdin.readline)
    if match is None:
        return "done", 1
    if match.backend == "flatpak":
        return "done", depot_install.install_flatpak(match, ctx.say, ctx.run)
    retry = preview(ctx, parsed, "install", [match.source_id])
    if retry.ok:
        return "retry", (retry, [match.source_id])
    ctx.say(f"{match.name} is in Depot's catalogue but needs its own software source, which isn't "
            "set up on this computer yet. Install it from Depot instead.")
    return "done", 1


def install(ctx: Context, parsed: options.Parsed, args: list[str] | None = None, *, reinstall: bool = False) -> int:
    args = list(parsed.args if args is None else args)
    if not args:
        ctx.warn("Error: Missing positional argument \"specs\" for command \"install\"")
        return 2
    if parsed.nogpgcheck:
        ctx.warn("Error: Luma always checks package signatures, so --nogpgcheck can't be used here.\n"
                 "A package file you trust can be installed directly: sudo dnf install ./package.rpm")
        return 1
    if ctx.euid != 0:
        ctx.warn(ROOT_MESSAGE)
        return 1
    try:
        status = system.status(ctx.run)
    except Exception as error:  # rpm-ostree unreachable
        ctx.warn(f"Error: Luma couldn't read the installed system: {error}")
        return 1
    pending = status.pending
    booted = status.booted
    base = system.base_packages(ctx.run)
    owned = owned_packages(ctx)
    local_names = {path: _local_names(ctx, [path]) for path in args if is_local_package(path)}
    named_owned = [arg for arg in args if arg in owned] + \
        [name for names in local_names.values() for name in names if name in owned]
    if named_owned and (reinstall or local_names or parsed.command in ("swap", "upgrade", "downgrade")
                        or not all(name in base for name in named_owned)):
        ctx.warn(owned_message(named_owned))
        return 1

    # A package in Luma's image that someone removed comes back by undoing
    # the removal, not by adding a second copy.
    removed = set(pending.requested_base_removals if pending else []) | set(booted.base_removals if booted else [])
    restore = [arg for arg in args if arg in base and (arg in removed or arg in _names_of(removed))]
    rest = [arg for arg in args if arg not in restore]

    result = preview(ctx, parsed, "install", rest) if rest else transaction.Preview(nothing_to_do=True)
    if not result.ok and rest and all(not is_local_package(item) for item in rest):
        action, payload = _depot_fallback(ctx, parsed, rest, result)
        if action == "done":
            return payload
        if action == "retry":
            result, rest = payload
    if result.text:
        ctx.say(result.text)
    if not result.ok:
        return 1
    if restore:
        ctx.say("Restoring (part of Luma, removed on this computer):")
        for name in restore:
            ctx.say(f" {name}")
    # Nothing from another repository may replace a package Luma builds.
    touched = result.rows("install", "install-group", "install-dep", "install-weak", "upgrade", "downgrade",
                          "reinstall", "replaced")
    replacing_owned = [row.name for row in touched if row.name in owned and row.repo != "@System"]
    if replacing_owned:
        ctx.warn(owned_message(replacing_owned))
        return 1
    if named_owned and result.nothing_to_do and not restore:
        ctx.say(f"{_join(named_owned)} {'is' if len(named_owned) == 1 else 'are'} Luma's own build, "
                "updated only by Luma's updates.")
    adds = [name for name in result.adds]
    local = [arg for arg in rest if is_local_package(arg)]
    local_by_name = {name: path for path, names in local_names.items() for name in names}
    if local:
        adds = [name for name in adds if name not in local_by_name] + \
            [path for path in local if not (local_names.get(path, set()) & base)]
    if result.nothing_to_do and not restore:
        ctx.say("Nothing to do.")
        return 0

    # Which of the person's options rpm-ostree cannot honor.
    ignored = [item for item in parsed.options if item.split("=", 1)[0] in REPO_WARNING_OPTIONS]
    enable = [repo for repo in parsed.repos if repo not in _enabled_repos(ctx)]

    changes = result.rows("upgrade", "downgrade", "replaced", "remove", "remove-dep", "remove-unused")
    base_changes = [row for row in changes if row.name in base]
    refused = [row.name for row in base_changes if row.name in PROTECTED or ".luma" in row.release
               or _luma_built(ctx, row.name)]
    if refused:
        ctx.warn(f"Error: this would replace or remove {', '.join(sorted(set(refused)))}, which "
                 f"{'is' if len(set(refused)) == 1 else 'are'} part of Luma itself.\n"
                 "A newer version arrives with a Luma update (sudo dnf upgrade). To use a different version "
                 "without changing the system, use a container: toolbox enter")
        return 1

    staged_update = bool(status.staged and booted and status.staged.base_checksum != booted.base_checksum)
    live_possible = not base_changes and not staged_update
    ctx.say("")
    if enable:
        ctx.say(f"Luma keeps {', '.join(enable)} enabled so {_join(adds or restore)} "
                f"{'gets' if len(adds or restore) == 1 else 'get'} updates.")
    if ignored:
        ctx.say(f"Luma resolves this change with the repositories that are enabled; {' '.join(ignored)} "
                "applies to the preview above only.")
    if base_changes:
        ctx.say("This also changes packages that are part of Luma: "
                + ", ".join(f"{row.name}" for row in base_changes) + ".")
        ctx.say("That takes effect after the next restart.")
    elif staged_update:
        ctx.say("A Luma update is waiting for a restart, so this is ready after that restart.")
    else:
        ctx.say("Added to Luma's system image and ready right away. It stays through Luma updates.")
    if not confirm(ctx, parsed):
        return 1

    for repo in enable:
        code = ctx.run([DNF5, "config-manager", "setopt", f"{repo}.enabled=1"]).returncode
        if code != 0:
            ctx.warn(f"Error: could not enable the repository {repo}")
            return 1

    record = {"command": "install", "packages": adds + restore, "live": live_possible,
              "base_changes": [row.name for row in base_changes]}
    before = live.snapshot()
    steps: list[list[str]] = []
    if restore:
        steps.append(["override", "reset", *restore])
    removals = [row.name for row in base_changes if row.name in base and row.name not in
                [r.name for r in result.rows("upgrade", "downgrade")]]
    upgrades = [row for row in base_changes if row.name not in removals]
    if upgrades:
        by_repo: dict[str, list[str]] = {}
        files: list[str] = []
        for row in upgrades:
            if row.name in local_by_name:
                files.append(local_by_name[row.name])
            else:
                by_repo.setdefault(row.repo, []).append(row.name)
        if files:
            steps.append(["override", "replace", *files])
        for repo, names in by_repo.items():
            steps.append(["override", "replace", "--experimental", f"--from=repo={repo}", *names])
    if removals:
        steps.append(["override", "remove", *removals, *[f"--install={name}" for name in adds]])
        adds = []
    if adds:
        # A newer file of a package added earlier from a file replaces it.
        previous = [nevra for nevra in (pending.requested_local if pending else [])
                    if system.nevra_name(nevra) in local_by_name
                    and local_by_name[system.nevra_name(nevra)] in adds]
        steps.append(["install", "--idempotent", "--assumeyes", *[f"--uninstall={n}" for n in previous], *adds])

    ctx.say("Running transaction")
    for step in steps:
        code, output = rpm_ostree(ctx, step, quiet=parsed.quiet)
        if code != 0:
            vitals.log("install", record | {"result": "failed", "error": last_error(output)}, ctx.run)
            ctx.warn(f"Error: {last_error(output)}")
            return 1
    applied_live = False
    if live_possible:
        code, output = rpm_ostree(ctx, ["apply-live"] + (["--allow-replacement"] if restore else []),
                                  quiet=True)
        applied_live = code == 0
        if applied_live:
            live.refresh(before, ctx.run)
        else:
            ctx.say(f"  Couldn't apply this to the running system ({last_error(output)}).")
    vitals.log("install", record | {"result": "live" if applied_live else "staged"}, ctx.run)
    ctx.say("")
    names = _join(record["packages"])
    if applied_live:
        ctx.say(f"Complete! {names} {'is' if len(record['packages']) == 1 else 'are'} ready to use.")
    else:
        ctx.say(f"Complete! {names} will be ready after you restart.")
        restart_note(ctx)
    ctx.say(f"To remove {'it' if len(record['packages']) == 1 else 'them'}: sudo dnf remove {' '.join(_bare(record['packages']))}")
    return 0


def remove(ctx: Context, parsed: options.Parsed) -> int:
    args = list(parsed.args)
    if not args:
        ctx.warn("Error: Missing positional argument \"specs\" for command \"remove\"")
        return 2
    if ctx.euid != 0:
        ctx.warn(ROOT_MESSAGE)
        return 1
    try:
        status = system.status(ctx.run)
    except Exception as error:
        ctx.warn(f"Error: Luma couldn't read the installed system: {error}")
        return 1
    pending = status.pending
    booted = status.booted
    added = set(pending.added if pending else []) | set(booted.added if booted else [])
    base = system.base_packages(ctx.run)

    result = preview(ctx, parsed, "remove", args)
    if result.text:
        ctx.say(result.text)
    if not result.ok:
        return 1
    if result.nothing_to_do:
        ctx.say("Nothing to do.")
        return 0

    removing = result.names("remove", "remove-dep", "remove-unused")
    layered = [name for name in removing if name in added]
    base_removals = [name for name in removing if name in base and name not in layered]
    # Packages pulled in only as dependencies of something added go with it.
    dependencies = [name for name in removing if name not in layered and name not in base_removals]
    orphans = [name for name in dependencies if name in args]
    if orphans:
        ctx.warn(f"Error: {_join(orphans)} came in as a dependency of a package you added. "
                 "Remove that package instead; sudo dnf list --installed shows what was added.")
        return 1
    refused = [name for name in base_removals if name in PROTECTED or _luma_built(ctx, name)]
    if refused:
        ctx.warn(f"Error: {_join(refused)} {'is' if len(refused) == 1 else 'are'} part of Luma itself and "
                 "can't be removed. Apps from Luma can be removed in Depot → My apps.")
        return 1

    staged_update = bool(status.staged and booted and status.staged.base_checksum != booted.base_checksum)
    live_possible = not staged_update
    ctx.say("")
    if base_removals:
        ctx.say(f"{_join(base_removals)} {'is' if len(base_removals) == 1 else 'are'} part of Luma's system image. "
                "Removing takes effect now where possible and fully after the next restart; "
                f"sudo dnf install {' '.join(base_removals)} brings {'it' if len(base_removals) == 1 else 'them'} back.")
    if not confirm(ctx, parsed):
        return 1
    record = {"command": "remove", "packages": layered + base_removals}
    before = live.snapshot()
    steps: list[list[str]] = []
    if layered and base_removals:
        steps.append(["override", "remove", *base_removals, *[f"--uninstall={name}" for name in layered]])
    elif layered:
        steps.append(["uninstall", "--idempotent", *layered])
    elif base_removals:
        steps.append(["override", "remove", *base_removals])
    ctx.say("Running transaction")
    for step in steps:
        code, output = rpm_ostree(ctx, step, quiet=parsed.quiet)
        if code != 0:
            vitals.log("remove", record | {"result": "failed", "error": last_error(output)}, ctx.run)
            ctx.warn(f"Error: {last_error(output)}")
            return 1
    applied_live = False
    if live_possible:
        code, output = rpm_ostree(ctx, ["apply-live", "--allow-replacement"], quiet=True)
        applied_live = code == 0
        if applied_live:
            live.refresh(before, ctx.run)
    vitals.log("remove", record | {"result": "live" if applied_live else "staged"}, ctx.run)
    ctx.say("")
    if applied_live:
        ctx.say(f"Complete! {_join(record['packages'])} removed.")
    else:
        ctx.say(f"Complete! {_join(record['packages'])} will be gone after you restart.")
        restart_note(ctx)
    return 0


def upgrade(ctx: Context, parsed: options.Parsed) -> int:
    """``dnf upgrade``: Luma's updates, prepared only when the person says yes."""
    if ctx.euid != 0:
        ctx.warn(ROOT_MESSAGE)
        return 1
    try:
        status = system.status(ctx.run)
    except Exception as error:
        ctx.warn(f"Error: Luma couldn't read the installed system: {error}")
        return 1
    pending = status.pending
    added = pending.added if pending else []
    named = list(parsed.args)
    base = system.base_packages(ctx.run)
    owned = [name for name in named if name in owned_packages(ctx)]
    if owned:
        ctx.warn(owned_message(owned))
        return 1
    for name in named:
        if name in base and name not in added:
            ctx.say(f"{name} is part of Luma and is updated with Luma's updates.")

    ctx.say("Checking for Luma updates…")
    check = ctx.run([LUMA_UPDATE, "check"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    state = _update_status(ctx)
    if not state:
        ctx.warn("Error: Luma's update service didn't answer. " + (check.stdout or "").strip())
        return 1
    if not state.get("managed", True):
        ctx.say("This computer doesn't follow a Luma update channel, so Luma updates aren't offered here "
                f"({state.get('unmanaged_reason') or 'unmanaged'}).")
    available = state.get("available_version") or ""
    # StagedVersion also names a staged deployment luma-update did not make
    # (a package added live stages the booted version); only an update is news.
    staged = (state.get("staged_version") or "") if state.get("state") == "staged" else ""
    added_updates = _added_updates(ctx, parsed, added) if added else []

    if staged:
        ctx.say(f"Luma {staged} is ready and is installed when you restart.")
        if added:
            ctx.say(f"Packages you added ({_join(added)}) come along, at their newest versions.")
        restart_note(ctx)
        return 0
    if available:
        size = state.get("download_bytes") or 0
        ctx.say(f"Luma {available} is available." + (f" {state.get('available_summary')}" if state.get("available_summary") else ""))
        if size:
            ctx.say(f"Download size: {_size(size)}")
        if added:
            ctx.say(f"Packages you added ({_join(added)}) are updated with it.")
        if not confirm(ctx, parsed, "Download and prepare it now? It is installed when you restart. [y/N]: "):
            return 1
        vitals.log("upgrade", {"command": "upgrade", "version": available}, ctx.run)
        code = ctx.run([LUMA_UPDATE, "download"]).returncode
        if code != 0:
            return 1
        ctx.say("Complete! The update is installed when you restart.")
        restart_note(ctx)
        return 0
    if state.get("state") == "error" and state.get("last_error"):
        ctx.say(f"Luma updates: {state['last_error']}")
    if added_updates:
        ctx.say("Updates for packages you added:")
        for line in added_updates:
            ctx.say(f" {line}")
        ctx.say("They are ready after the next restart.")
        if not confirm(ctx, parsed):
            return 1
        record = {"command": "upgrade", "packages": added}
        # Redeploying the same Luma version resolves the added packages again
        # at their newest versions; the Luma version itself does not move.
        code, output = rpm_ostree(ctx, ["deploy", "--bypass-driver", pending.base_checksum], quiet=parsed.quiet)
        vitals.log("upgrade", record | {"result": "staged" if code == 0 else "failed"}, ctx.run)
        if code != 0:
            ctx.warn(f"Error: {last_error(output)}")
            return 1
        ctx.say("Complete! The newer versions are ready after you restart.")
        restart_note(ctx)
        return 0
    ctx.say("Nothing to do.")
    return 0


def not_on_luma(ctx: Context, parsed: options.Parsed, text: str) -> int:
    ctx.warn(text)
    return 1


def history(ctx: Context, parsed: options.Parsed, argv: list[str]) -> int:
    ctx.say("On Luma every system change is a version you can go back to: sudo rpm-ostree rollback "
            "makes the previous one start next time, and rpm-ostree status lists them.")
    return 0


# ── Helpers ──────────────────────────────────────────────────────────────────

def _names_of(nevras) -> set[str]:
    return {system.nevra_name(item) for item in nevras}


def _local_names(ctx: Context, paths: list[str]) -> set[str]:
    names: set[str] = set()
    for path in paths:
        if path.startswith(("http://", "https://")):
            continue
        result = ctx.run([system.RPM, "-qp", "--qf", "%{NAME}\n", path], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True)
        if result.returncode == 0:
            names.update(result.stdout.split())
    return names


def _enabled_repos(ctx: Context) -> set[str]:
    result = ctx.run([DNF5, "repo", "list", "--enabled", "--quiet"], stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, text=True)
    repos: set[str] = set()
    for line in (result.stdout or "").splitlines()[1:]:
        if line.strip():
            repos.add(line.split()[0])
    return repos


def _luma_built(ctx: Context, name: str) -> bool:
    result = ctx.run([system.RPM, "-q", "--qf", "%{VENDOR}|%{RELEASE}|%{PACKAGER}\n", name],
                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    text = (result.stdout or "").lower()
    return result.returncode == 0 and (".luma" in text or "project luma" in text)


def _update_status(ctx: Context) -> dict:
    result = ctx.run([LUMA_UPDATE, "status", "--json"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        return json.loads(result.stdout or "")
    except ValueError:
        return {}


def _added_updates(ctx: Context, parsed: options.Parsed, names: list[str]) -> list[str]:
    command = [DNF5, "check-upgrade", "--quiet", *dnf5_options(parsed), *names]
    result = ctx.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    if result.returncode != 100:
        return []
    return [line.strip() for line in (result.stdout or "").splitlines()
            if line.strip() and not line.startswith(("Last metadata", "Updating", "Repositories"))]


def _size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def _bare(names) -> list[str]:
    return [Path(name).name.rsplit(".rpm", 1)[0] if name.endswith(".rpm") else name for name in names]


def _join(names) -> str:
    names = _bare(list(names))
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


UNSUPPORTED = {
    "downgrade": "Luma can't downgrade one package of its system image. To go back to the previous version "
                 "of the whole system: sudo rpm-ostree rollback. For an older version of a tool, use a "
                 "container: toolbox enter",
    "distro-sync": None,  # handled as upgrade
    "offline": "Luma installs system updates offline already: sudo dnf upgrade prepares one for the next restart.",
    "system-upgrade": "Luma moves to new releases with its own updates: sudo dnf upgrade.",
    "builddep": "Build dependencies belong in a container on Luma so the system stays clean: "
                "toolbox enter, then sudo dnf builddep …",
}


def main(argv: list[str] | None = None, ctx: Context | None = None, program: str = "dnf") -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ctx = ctx or Context()
    parsed = options.parse(argv)
    if (os.environ.get("LUMA_DNF_PASSTHROUGH") == "1" or not ctx.image_based or parsed.installroot
            or parsed.transient or parsed.downloadonly or parsed.help or parsed.version or not parsed.command
            or parsed.command in options.READ_ONLY):
        return passthrough(ctx, argv)
    command = parsed.command
    if command == "install":
        return install(ctx, parsed)
    if command in ("reinstall", "downgrade", "swap"):
        owned = [name for name in parsed.args if name in owned_packages(ctx)]
        if owned:
            ctx.warn(owned_message(owned))
            return 1
    if command == "reinstall":
        ctx.say("Packages on Luma are checked against their signed versions at every update; "
                "there is nothing to reinstall. Installing anything missing:")
        return install(ctx, parsed, reinstall=True)
    if command == "remove":
        return remove(ctx, parsed)
    if command in ("upgrade", "distro-sync"):
        return upgrade(ctx, parsed)
    if command == "autoremove":
        ctx.say("Nothing to do. On Luma, dependencies leave together with the package that needed them.")
        return 0
    if command == "swap":
        if len(parsed.args) != 2:
            ctx.warn("Error: swap needs two packages: sudo dnf swap OLD NEW")
            return 2
        parsed.allowerasing = True
        if "--allowerasing" not in parsed.options:
            parsed.options.append("--allowerasing")
        return install(ctx, parsed, [parsed.args[1]])
    if command == "group":
        sub = parsed.args[0] if parsed.args else ""
        if sub in ("install", "remove", "upgrade"):
            specs = [f"@{name}" if not name.startswith("@") else name for name in parsed.args[1:]]
            inner = options.parse([*parsed.options, sub, *specs])
            return install(ctx, inner) if sub == "install" else (remove(ctx, inner) if sub == "remove"
                                                                  else upgrade(ctx, inner))
        return passthrough(ctx, argv)
    if command == "history":
        if parsed.args and parsed.args[0] in ("undo", "redo", "rollback"):
            return history(ctx, parsed, argv)
        return passthrough(ctx, argv)
    if command == "mark":
        ctx.say("Nothing to do. Luma records what you added: rpm-ostree status lists it.")
        return 0
    if command in UNSUPPORTED and UNSUPPORTED[command]:
        return not_on_luma(ctx, parsed, UNSUPPORTED[command])
    # Not a known verb at all: "dnf vivaldi" with the package typed where the
    # command goes. Treat it as install when the word resolves to something
    # installable (Fedora's own repositories, or Depot's catalogue);
    # otherwise say so plainly instead of handing dnf5 an unknown command.
    probe = preview(ctx, parsed, "install", [command])
    if probe.ok or ("No match for argument:" in probe.text and depot_catalog.find(command)):
        ctx.say(f"Installing {command}…")
        return install(ctx, parsed, [command])
    ctx.warn(f'{program}: "{command}" is not a Luma command. To install a package: sudo {program} install '
             f"{command}. Depot (the app store) can find and install it too.")
    return 1


def entry() -> None:
    program = Path(sys.argv[0]).name
    raise SystemExit(main(program=program))
