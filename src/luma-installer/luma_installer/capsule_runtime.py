"""What every application capsule needs to behave like a native application.

A Debian or Fedora package installed into a capsule was written for a full
desktop system, not a minimal container image. The first capsules showed the
same failures across unrelated applications, so the fixes live here, applied to
every capsule rather than to whichever application exposed them:

* **Graphics.** A base image has no OpenGL, EGL, GBM or Vulkan loader and no
  Mesa drivers. Anything that draws with the GPU — every Electron and Chromium
  application, most games, many GTK 4 and Qt 6 applications — fails to create a
  context, and Chromium gives up after its GPU process dies repeatedly. A
  capsule with a window always gets the platform graphics stack.
* **Shared memory.** Podman gives a container 64 MiB of /dev/shm. Chromium's
  renderer and compositor exchange frames there, so a larger window draws
  nothing: a white or black window that never paints. Capsules get room.
* **Wayland.** Electron still starts on X11 unless told otherwise, and many
  packaged launch scripts force X11 outright. Under Xwayland inside a capsule
  the GPU process crashes or every resize stutters. Chromium-based payloads are
  detected once and started with Ozone on Wayland, overriding the wrapper.
* **Undeclared libraries.** Third-party packages often bundle their own
  Electron, CEF or Qt and declare none of what it links against, relying on a
  full desktop system to already have NSS, GTK, ALSA and the X libraries. In a
  capsule the loader stops and the application exits with 127 before drawing.
  After installation every program and library the package ships is checked
  with the dynamic loader, and whatever provides the missing libraries is
  installed from the capsule's own distribution.
* **Handing a link to the running window.** Electron up to 41 corrupts the
  message a second instance sends the first when its argument count lands on an
  ASCII whitespace byte (9-13 or 32 arguments; electron/electron#52020): the
  first instance rejects it, the second decides the first is hung and SIGKILLs
  it. That is how a browser sign-in handing figma:// back closed Figma. A
  handoff never carries the capsule's own flags when they would land it on such
  a count (the running instance already has them), and is padded with an inert
  switch when the application's own arguments land there anyway.
* **The keyring.** Applications keep sign-ins encrypted through the Secret
  Service; without it they store secrets in plain text or forget them on every
  restart. The capsule's session-bus boundary lets the Secret Service through,
  as it is for the same application installed natively, and the desktop's
  identity is passed in: Chromium chooses its keyring from XDG_CURRENT_DESKTOP
  and, with none, silently keeps nothing.

Capsules installed before these rules are brought up to date once, the first
time they are opened (`RUNTIME_VERSION`).
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

RUNTIME_VERSION = 4

# Which capsule kinds each runtime version changed, so a capsule is only
# rebuilt when something it would receive is new.
RUNTIME_CHANGES = {1: {"deb", "rpm"}, 2: {"deb", "rpm"}, 3: {"rpm", "portable"}, 4: {"deb"}}


def upgrade_needed(kind: str, version: int) -> bool:
    return any(changed > version and kind in kinds for changed, kinds in RUNTIME_CHANGES.items())

GRAPHICS_PACKAGES = {
    "deb": ("libgl1", "libegl1", "libgles2", "libgbm1", "libgl1-mesa-dri", "libvulkan1", "mesa-vulkan-drivers",
            "libsecret-1-0", "fonts-dejavu-core"),
    # libGLESv2 is opened at run time by Flutter, Chromium's ANGLE and SDL, so no
    # loader check ever reports it missing.
    "rpm": ("mesa-libGL", "mesa-libEGL", "mesa-libgbm", "mesa-dri-drivers", "mesa-vulkan-drivers", "vulkan-loader",
            "libglvnd-gles", "libsecret", "dejavu-sans-fonts"),
}

# Files only a Chromium/Electron/CEF payload ships.
# An application only gets Luma's Chromium switches if it is recognised as
# Chromium, and the first five markers missed at least one Electron
# application in the field: it fell back to no switches at all and therefore
# to Electron's X11 default. These later markers are files that only a
# Chromium or Electron bundle ships.
CHROMIUM_MARKERS = re.compile(r"(/resources/app\.asar$|/chrome-sandbox$|/chrome_100_percent\.pak$|/libcef\.so$|"
                              r"/v8_context_snapshot\.bin$|/chrome_200_percent\.pak$|/icudtl\.dat$|"
                              r"/snapshot_blob\.bin$|/libffmpeg\.so$|/chrome_crashpad_handler$|"
                              r"/vk_swiftshader_icd\.json$|/libvk_swiftshader\.so$)")

SHM_SIZE = "2g"

# Appended after everything the package's own launcher passes: Chromium keeps
# the last value of a repeated switch, and packaged wrapper scripts commonly
# force --ozone-platform=x11, which under Xwayland in a capsule stutters on
# every resize (or crashes the GPU process outright).
#
# Accelerated video decode is off by default in Chromium on Linux, so a laptop
# with a media engine, a VA-API driver and a kernel driver for all of it still
# decodes every frame on the processor. Measured on a Lunar Lake machine on
# 2026-09-20: the browser's GPU client had mapped the Intel VA-API driver and
# the decode engine's own counter read drm-cycles-vcs 0 -- not a small number,
# zero -- while two renderers spent about half a processor on one video
# stream. Chromium ignores a feature name it does not know, and falls back to
# software decode on its own when the driver cannot handle a codec, so naming
# the variants across milestones is safe in both directions: the failure mode
# is no change rather than no video.
#
# Repeated switches keep their last value, so everything Luma enables has to
# live in this one --enable-features, not in a second copy of the switch.
CHROMIUM_FEATURES = (
    "WaylandWindowDecorations",
    "AcceleratedVideoDecodeLinuxGL",
    "AcceleratedVideoDecodeLinuxZeroCopyGL",
    "VaapiVideoDecodeLinuxGL",
    "VaapiIgnoreDriverChecks",
)

CHROMIUM_FLAGS = ("--ozone-platform=wayland", "--enable-features=" + ",".join(CHROMIUM_FEATURES),
                  "--password-store=gnome-libsecret")

DESKTOP_IDENTITY = ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION", "XDG_SESSION_TYPE")


def login_environment(user: str) -> list[tuple[str, str]]:
    """What a login session gives every process, and a container does not.

    Terminals, SSH clients, editors and Git tools start the person's shell or
    read it to learn their environment; Termius stopped before drawing a window
    when SHELL was unset. The shell is the capsule's own bash.
    """
    values = [("SHELL", "/bin/bash")]
    if user and all(c.isalnum() or c in "._-" for c in user):
        values += [("USER", user), ("LOGNAME", user)]
    return values

SECRET_SERVICE = "--talk=org.freedesktop.secrets"

HANDOFF_UNSAFE_COUNTS = frozenset((9, 10, 11, 12, 13, 32))
HANDOFF_PADDING = "--luma-handoff"

# Prints how many arguments the running browser process was started with up to
# and including the capsule's own Chromium switches (anything after them was
# handed over, not part of how the application starts). Renderer, GPU and
# utility children, the capsule's init and the shell script that started it
# are skipped.
PRIMARY_ARGUMENTS_SCRIPT = r"""
for p in /proc/[0-9]*; do
  [ -r "$p/cmdline" ] || continue
  args=$(tr '\0' '\n' < "$p/cmdline") || continue
  case "$args" in *--type=*) continue ;; esac
  first=$(printf '%s\n' "$args" | head -n 1)
  case "${first##*/}" in sh|bash|dash|env|podman|podman-init|catatonit) continue ;; esac
  line=$(printf '%s\n' "$args" | grep -n -x -F -- "$1" | tail -n 1 | cut -d: -f1)
  [ -n "$line" ] || continue
  echo "$line"
  exit 0
done
"""


# Debian names a library's package after its soname with a few exceptions.
DEB_LIBRARY_EXCEPTIONS = {
    "libnssutil3.so": "libnss3", "libsmime3.so": "libnss3", "libssl3.so": "libnss3",
    "libatspi.so.0": "libatspi2.0-0t64", "libxcb-dri3.so.0": "libxcb-dri3-0", "libX11-xcb.so.1": "libx11-xcb1",
    "libGL.so.1": "libgl1", "libEGL.so.1": "libegl1", "libgbm.so.1": "libgbm1", "libudev.so.1": "libudev1",
    "libstdc++.so.6": "libstdc++6", "libz.so.1": "zlib1g", "libuuid.so.1": "libuuid1", "libffi.so.8": "libffi8",
}

# Every ELF program or library the package ships, checked with the loader.
MISSING_LIBRARIES_SCRIPT = r"""
while IFS= read -r f; do
  [ -f "$f" ] && [ ! -L "$f" ] || continue
  head -c 4 "$f" 2>/dev/null | grep -q "ELF" || continue
  ldd "$f" 2>/dev/null | awk '/=> not found/ {print $1}'
done | sort -u
"""


def deb_package_candidates(soname: str) -> list[str]:
    """libgtk-3.so.0 -> libgtk-3-0, libasound.so.2 -> libasound2 (and the t64 names Debian 13 uses)."""
    if soname in DEB_LIBRARY_EXCEPTIONS:
        return [DEB_LIBRARY_EXCEPTIONS[soname]]
    match = re.fullmatch(r"(lib[^/]+?)\.so(?:\.(\d+))?(?:\.[\d.]+)?", soname)
    if not match:
        return []
    base, version = match.group(1).lower(), match.group(2) or ""
    name = f"{base}-{version}" if version and base[-1].isdigit() else f"{base}{version}"
    return [f"{name}t64", name]


def missing_libraries(container: str, file_list: str) -> list[str]:
    files = "\n".join(line.strip() for line in file_list.splitlines()
                      if line.strip().startswith("/") and not line.strip().endswith((".png", ".svg", ".js", ".json", ".txt", ".html")))
    if not files:
        return []
    result = subprocess.run(["podman", "exec", "-i", container, "sh", "-c", MISSING_LIBRARIES_SCRIPT],
                            input=files[:4_000_000], capture_output=True, text=True, check=False, timeout=600)
    return [line.strip() for line in result.stdout.splitlines() if re.fullmatch(r"[A-Za-z0-9_.+-]+\.so[\d.]*", line.strip())]


def library_install_command(kind: str, container: str, sonames: list[str]) -> list[str] | None:
    """The command that installs whatever provides these libraries, or None."""
    if not sonames:
        return None
    if kind == "rpm":
        # Fedora packages declare the libraries they provide by soname.
        return ["dnf5", "install", "-y", "--setopt=install_weak_deps=False", "--skip-unavailable",
                *[f"{name}()(64bit)" for name in sonames]]
    packages: list[str] = []
    for soname in sonames:
        for candidate in deb_package_candidates(soname):
            known = subprocess.run(["podman", "exec", container, "apt-cache", "show", "--no-all-versions", candidate],
                                   capture_output=True, text=True, check=False)
            if known.returncode == 0 and known.stdout.strip():
                packages.append(candidate)
                break
    if not packages:
        return None
    return ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "--no-install-recommends",
            *sorted(set(packages))]


def language_pack(kind: str, lang: str | None = None) -> str:
    """The person's language, so GTK does not fall back to the C locale.

    A Fedora base image carries only C.UTF-8; Debian's images carry C.UTF-8 and
    rely on the environment, which is passed through."""
    import os
    value = (lang if lang is not None else os.environ.get("LANG", "")).split(".", 1)[0]
    code = value.split("_", 1)[0]
    if kind != "rpm" or not re.fullmatch(r"[a-z]{2,3}", code):
        return ""
    return f"glibc-langpack-{code}"


# Debian's images ship no locales at all, so the person's LANG names one that
# does not exist: glibc falls back to ASCII, non-English text and file names
# break, and every Perl or shell tool prints a warning. Generating just the
# person's locale costs a few megabytes, where locales-all costs hundreds.
DEBIAN_LOCALE_SCRIPT = (
    'set -e; export DEBIAN_FRONTEND=noninteractive; '
    'apt-get install -y --no-install-recommends locales >/dev/null; '
    'localedef -i "$1" -c -f UTF-8 -A /usr/share/locale/locale.alias "$1.UTF-8"'
)


def locale_command(kind: str, lang: str | None = None) -> list[str] | None:
    """The command that gives a Debian capsule the person's locale, or None."""
    import os
    value = (lang if lang is not None else os.environ.get("LANG", "")).split(".", 1)[0]
    if kind != "deb" or not re.fullmatch(r"[a-z]{2,3}_[A-Z]{2}(@[a-z]+)?", value):
        return None
    return ["sh", "-c", DEBIAN_LOCALE_SCRIPT, "sh", value]


def install_command(kind: str) -> list[str]:
    """The command that adds the graphics stack inside a capsule of this kind."""
    packages = list(GRAPHICS_PACKAGES[kind])
    language = language_pack(kind)
    if language:
        packages.append(language)
    if kind == "deb":
        return ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "--no-install-recommends", *packages]
    return ["dnf5", "install", "-y", "--setopt=install_weak_deps=False", *packages]


def is_chromium(file_list: str) -> bool:
    return any(CHROMIUM_MARKERS.search(line.strip()) for line in file_list.splitlines())


def package_files(container: str, package: str, kind: str) -> str:
    query = ["dpkg-query", "-L", package] if kind == "deb" else ["rpm", "-ql", package]
    result = subprocess.run(["podman", "exec", container, *query], capture_output=True, text=True, check=False)
    return result.stdout if result.returncode == 0 else ""


def launch_arguments(record: dict[str, object]) -> list[str]:
    """podman run options every capsule gets.

    `--init` puts a minimal init (catatonit) at PID 1 in the capsule's PID
    namespace, with the application as its child. Without it the application
    itself is PID 1, and every process orphaned inside the capsule is handed to
    it to reap. Electron never does: ChatGPT left 1,537 zombies behind in one
    day, filled the capsule's 2,048-process limit, and nothing in the capsule
    could start another process until it was restarted. catatonit reaps
    orphans, forwards signals to the application and exits with its status.

    Podman's default rootless parent is user.slice, outside the app.slice
    monitored by Luma's swap memory guard. The container payload must remain
    an application candidate too: otherwise a capsule can exhaust memory
    while the guard stops an unrelated browser. Select the supported systemd
    parent explicitly; joining an existing capsule does not move its cgroup.
    """
    return ["--init", f"--shm-size={SHM_SIZE}", "--cgroup-parent=app.slice"]


def primary_argument_count(container: str) -> int:
    """How many arguments the running browser process was started with, or 0."""
    result = subprocess.run(["podman", "exec", container, "sh", "-c", PRIMARY_ARGUMENTS_SCRIPT, "sh", CHROMIUM_FLAGS[-1]],
                            capture_output=True, text=True, check=False, timeout=20)
    value = result.stdout.strip()
    return int(value) if value.isdigit() else 0


def handoff_arguments(record: dict[str, object], wayland: bool, primary_count: int, extra: list[str]) -> list[str]:
    """Arguments after the command when joining a running capsule.

    `primary_count` is the running browser process's own argument count (0 when
    unknown); the handoff instance's count is the same minus the capsule flags,
    plus whatever is being handed over.
    """
    flags = payload_arguments(record, wayland)
    if not flags or primary_count <= 0:
        return [*flags, *extra]
    own = primary_count - len(flags)
    if own + len(flags) + len(extra) not in HANDOFF_UNSAFE_COUNTS:
        return [*flags, *extra]
    padding: list[str] = []
    while own + len(padding) + len(extra) in HANDOFF_UNSAFE_COUNTS:
        padding.append(HANDOFF_PADDING)
    return [*padding, *extra]


# Luma may start an application with switches its publisher did not choose,
# where the override is plainly better for the person -- native Wayland instead
# of XWayland, video decoded on the chip built for it. That licence comes with
# an escape hatch, in four places, because an override that cannot be undone is
# just a bug with a good intention:
#
#   1. LUMA_ENERGY_FLAGS=off in the environment, for one launch.
#   2. The person's own list, which is also where Luma writes what it learns.
#   3. The machine's list, for whoever administers it.
#   4. Luma's own shipped list of applications we have had to back off from.
#
# A line is an application id on its own -- no Luma switches at all -- or an id
# followed by the groups to drop: "video-decode", "wayland".
FLAG_GROUPS = {
    "video-decode": ("AcceleratedVideoDecodeLinuxGL", "AcceleratedVideoDecodeLinuxZeroCopyGL",
                     "VaapiVideoDecodeLinuxGL", "VaapiIgnoreDriverChecks"),
    "wayland": ("--ozone-platform=wayland",),
}

FLAG_EXCEPTION_FILES = (
    "/usr/share/luma/energy-flag-exceptions.txt",
    "/etc/luma/energy-flag-exceptions.txt",
)


def _person_flag_exceptions() -> Path:
    config = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config) / "luma" / "energy-flag-exceptions.txt"


def flag_exceptions(application_id: str) -> set[str]:
    """Which groups of Luma's own switches this application must not be given.

    "all" means none of them. An unreadable or malformed file is ignored: a
    person must never be unable to start an application because a list Luma
    wrote for its own benefit could not be parsed.
    """
    if not application_id:
        return set()
    dropped: set[str] = set()
    for path in (*FLAG_EXCEPTION_FILES, str(_person_flag_exceptions())):
        try:
            text = Path(path).read_text()
        except OSError:
            continue
        for line in text.splitlines():
            line = line.split("#", 1)[0].split()
            if not line or line[0] != application_id:
                continue
            groups = [g for g in line[1:] if g in FLAG_GROUPS]
            dropped.update(groups or ["all"])
    return dropped


def record_flag_backoff(application_id: str, group: str = "all") -> None:
    """Remember not to do that again.

    Called when an application Luma had added switches to failed on the way up.
    Falling back is the rule: better an application that starts as its
    publisher intended than one Luma improved into not starting.
    """
    if not application_id or (group != "all" and group not in FLAG_GROUPS):
        return
    if group in flag_exceptions(application_id) or "all" in flag_exceptions(application_id):
        return
    path = _person_flag_exceptions()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{application_id}{'' if group == 'all' else ' ' + group}\n")
    except OSError:
        pass


#: What each group is, said the way it would be said to a person rather than
#: the way it is spelled on a command line.
FLAG_GROUP_NAMES = {
    "video-decode": ("Video played by the graphics chip",
                     "Video is decoded by the chip built for it instead of the "
                     "processor, which uses much less battery."),
    "wayland": ("Drawn directly by the display system",
                "The window is drawn natively instead of through a "
                "compatibility layer, which is sharper and cheaper."),
}


def flag_overrides(application_id: str) -> list[dict[str, object]]:
    """What Luma is doing to this application, and what it has stopped doing.

    The point of this function is that the answer to "has Luma changed how my
    application starts?" must be answerable. A setting that only exists as a
    file on disk is not something a person has; it is something we have.
    Settings, Depot and `luma-appctl energy` all read this.
    """
    dropped = flag_exceptions(application_id)
    rows: list[dict[str, object]] = []
    for group, (title, explanation) in FLAG_GROUP_NAMES.items():
        off = "all" in dropped or group in dropped
        rows.append({
            "group": group,
            "title": title,
            "explanation": explanation,
            "active": not off,
            "withdrawn_because": _withdrawn_because(application_id) if off else "",
        })
    return rows


def _withdrawn_because(application_id: str) -> str:
    """Why Luma stopped, read back from the journal it wrote at the time."""
    try:
        finished = subprocess.run(
            ("journalctl", "--user", "-r", "-n", "1", "-o", "cat",
             "MESSAGE_ID=9a41d6c05f8e4b37a2c9e18b47d50f63",
             f"LUMA_APPLICATION_ID={application_id}"),
            capture_output=True, text=True, timeout=5, check=False)
        line = finished.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        # No journal to read is not a reason to show a setting as off with no
        # explanation beside it. Say the honest general thing instead.
        line = ""
    if line:
        return line
    # No journal entry means the person, or whoever administers the machine,
    # turned it off themselves rather than Luma backing away from a crash.
    return "Turned off on this computer."


def payload_arguments(record: dict[str, object], wayland: bool) -> list[str]:
    """Arguments added after the application's own command."""
    if not (record.get("chromium") and wayland):
        return []
    if os.environ.get("LUMA_ENERGY_FLAGS", "").strip().lower() == "off":
        return []
    dropped = flag_exceptions(str(record.get("application_id") or ""))
    if "all" in dropped:
        return []
    if not dropped:
        return list(CHROMIUM_FLAGS)
    features = [f for f in CHROMIUM_FEATURES
                if not any(f in FLAG_GROUPS[g] for g in dropped)]
    flags = []
    for flag in CHROMIUM_FLAGS:
        if any(flag in FLAG_GROUPS[g] for g in dropped):
            continue
        flags.append("--enable-features=" + ",".join(features)
                     if flag.startswith("--enable-features=") else flag)
    return [f for f in flags if f != "--enable-features="]
