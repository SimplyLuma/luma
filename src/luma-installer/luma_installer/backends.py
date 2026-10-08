from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import appimage_payload, capsule_runtime, network
from .desktop import (applications_root, capsule_declared_schemes, command_name, desktop_is_console,
                      desktop_mime_types, desktop_window_class, export_command, parse_desktop,
                      parse_desktop_entry, register_handlers, safe_id, write_launcher, write_record)
from .errors import InstallerError
from .progress import phase
from .model import PackageReport
from .safety import fingerprint, require_free_space, stage_user_copy, transaction_lock

DEBIAN_IMAGES = {
    "x86_64": "docker.io/library/debian@sha256:abc9cb88a5587630d7f915f47b23b0668fe250fbfc6457aa4d52b534c1bbf73f",
    "aarch64": "docker.io/library/debian@sha256:7215f78f35ffe58fe13f244fac9c4f21326d55187271fbb3e1a8aa5cc7e387ab",
}
FEDORA_IMAGES = {
    "x86_64": "registry.fedoraproject.org/fedora@sha256:69b1219730fe52d6cbb0874cbc1a36dd43b722354a603f6cdf73acbff8dd7c59",
    "aarch64": "registry.fedoraproject.org/fedora@sha256:62f199d1eb34170a7bb2277485676d89c0e91aae4086151c4043062cce51c77c",
}
TOOL_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# The shape of the launcher metadata a record was written with. A record behind
# this is rewritten from the package the next time the application is opened;
# 7 added the link schemes an application declares for itself.
LAUNCHER_METADATA_VERSION = 7


def _run(arguments: list[str], *, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PATH": TOOL_PATH, "LANG": os.environ.get("LANG", "C.UTF-8")},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InstallerError(f"The installer backend could not complete: {error}") from error
    if result.returncode != 0:
        message = next((line.strip() for line in reversed((result.stderr + result.stdout).splitlines()) if line.strip()), "Unknown backend error")
        raise InstallerError(message)
    return result


def _fetch(arguments: list[str], *, timeout: int = 1800, source: str = "the package's software source") -> subprocess.CompletedProcess[str]:
    """Run a step that reaches the network, surviving a name server that blinks.

    Every command routed through here is safe to run again: refreshing an index
    or installing the same package into the same fresh capsule repeats cleanly.
    """
    try:
        return network.retrying(lambda: _run(arguments, timeout=timeout))
    except InstallerError as error:
        actionable = network.friendly(error, source)
        if actionable:
            raise InstallerError(actionable) from error
        raise


def _find_squashfs_offset(path: Path) -> int:
    kind, offset = appimage_payload.locate(path)
    if kind != appimage_payload.SQUASHFS:
        raise InstallerError("The AppImage does not contain a SquashFS application payload.")
    return offset


def _install_appimage(report: PackageReport) -> str:
    root = Path.home() / ".local/share/luma/appimages" / report.sha256
    payload = root / "AppDir"
    partial = root / ".AppDir.partial"
    require_free_space(root, max(report.byte_size * 2, 256 * 1024 * 1024), "extract this AppImage")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    phase("Prepare application", 1/3)
    staged = stage_user_copy(report.path, report.sha256)
    kind, offset = appimage_payload.locate(staged)
    shutil.rmtree(partial, ignore_errors=True)
    try:
        if kind == appimage_payload.SQUASHFS:
            _run(["unsquashfs", "-quiet", "-no-xattrs", "-o", str(offset), "-d", str(partial), str(staged)])
        else:
            appimage_payload.extract_with_runtime(staged, partial)
        app_run = partial / "AppRun"
        if not app_run.exists():
            raise InstallerError("The AppImage payload does not contain AppRun.")
        resolved_app_run = app_run.resolve(strict=True)
        if not resolved_app_run.is_relative_to(partial.resolve()) or not resolved_app_run.is_file():
            raise InstallerError("The AppImage launcher points outside its application payload.")
        resolved_app_run.chmod(resolved_app_run.stat().st_mode | 0o500)
        if payload.exists():
            shutil.rmtree(payload)
        partial.replace(payload)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    desktop_files = sorted(p for p in payload.glob("*.desktop")
                           if p.resolve().is_relative_to(payload.resolve()) and p.is_file())
    name, comment, icon = report.title, report.summary, "application-x-executable"
    mime_types: list[str] = []
    if desktop_files:
        try:
            contents = desktop_files[0].read_text(encoding="utf-8")
            name, comment, icon, _command = parse_desktop(contents)
            mime_types = desktop_mime_types(contents)
        except (OSError, UnicodeError, InstallerError):
            name, comment, icon = report.title, report.summary, "application-x-executable"
    application_id = safe_id(f"appimage-{name}-{report.sha256[:12]}")
    exported_icon = _export_appimage_icon(payload, icon, application_id)
    write_record(application_id, {
        "format": "appimage", "name": name, "sha256": report.sha256,
        "root": str(payload), "command": "/app/AppRun", "icon": exported_icon,
        "mime_types": mime_types, "launcher_metadata_version": LAUNCHER_METADATA_VERSION,
    })
    write_launcher(application_id, name, comment, exported_icon, mime_types=mime_types)
    return name


def _elf_files(payload: Path, prefix: str) -> str:
    """Programs and libraries in an unpacked folder, as paths under `prefix`."""
    found: list[str] = []
    for path in sorted(payload.rglob("*")):
        if len(found) >= 4000:
            break
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size < 64:
                continue
            with path.open("rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    continue
        except OSError:
            continue
        found.append(f"{prefix}/{path.relative_to(payload)}")
    return "\n".join(found)


def _host_missing_libraries(payload: Path) -> list[str]:
    """What the application links against that this system does not have."""
    files = _elf_files(payload, str(payload))
    if not files:
        return []
    result = subprocess.run(["sh", "-c", capsule_runtime.MISSING_LIBRARIES_SCRIPT], input=files,
                            capture_output=True, text=True, check=False, timeout=600)
    # A plugin library often has no search path of its own and finds a library
    # the application ships because the program has already loaded it.
    bundled = _bundled_names(payload)
    return [line.strip() for line in result.stdout.splitlines() if line.strip() and line.strip() not in bundled]


def _bundled_names(payload: Path) -> set[str]:
    return {path.name for path in payload.rglob("*.so*")}


def _portable_capsule(application_id: str, payload: Path, report: PackageReport) -> dict[str, object]:
    """A Fedora capsule with the libraries a downloaded application expects.

    A program built for a general-purpose distribution assumes libraries that
    an image-based system does not carry — tray indicators, older toolkits,
    codecs. Rather than change the system, the application runs from its
    folder inside a Fedora capsule where the missing libraries are installed by
    the soname they provide.
    """
    podman = shutil.which("podman")
    host = {"amd64": "x86_64", "arm64": "aarch64"}.get(platform.machine().lower(), platform.machine().lower())
    image = FEDORA_IMAGES.get(host)
    if not podman or image is None:
        raise InstallerError("This application needs libraries this system does not include, "
                             "and the Fedora capsule engine is not available to provide them.")
    container = f"luma-install-{application_id}"
    output_image = f"localhost/luma-portable-{application_id}:{report.sha256[:12]}"
    require_free_space(Path.home() / ".local/share/containers", 2 * 1024 ** 3, "create this application's capsule")
    phase("Prepare libraries", 2/3)
    _fetch([podman, "pull", image], source="the Fedora base image")
    subprocess.run([podman, "rm", "-f", container], check=False, capture_output=True)
    try:
        _run([podman, "create", "--name", container, "--network=slirp4netns",
              "--volume", f"{payload}:/app:ro,Z", image, "sleep", "infinity"])
        _run([podman, "start", container])
        _fetch([podman, "exec", container, *capsule_runtime.install_command("rpm")], source="the Fedora package index")
        bundled = _bundled_names(payload)
        missing = [name for name in _provide_missing_libraries(container, "rpm", _elf_files(payload, "/app"))
                   if name not in bundled]
        _commit_container(podman, container, output_image)
    finally:
        subprocess.run([podman, "rm", "-f", container], check=False, capture_output=True)
    return {"image": output_image, "missing_libraries": missing, "runtime_version": capsule_runtime.RUNTIME_VERSION}


def _install_portable(report: PackageReport) -> str:
    from .archive_app import analyse, extract
    root = Path.home() / ".local/share/luma/portable" / report.sha256
    payload = root / "app"
    partial = root / ".app.partial"
    phase("Prepare application", 1/3)
    staged = stage_user_copy(report.path, report.sha256)
    # The staged copy is what gets extracted, so it is what gets checked.
    layout = analyse(staged)
    require_free_space(root, sum(member.size for member in layout.members) + report.byte_size,
                       "unpack this application")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.rmtree(partial, ignore_errors=True)
    phase("Unpack application", 2/3)
    try:
        extract(staged, partial, layout)
        program = partial / layout.executable
        resolved = program.resolve(strict=True)
        if not resolved.is_relative_to(partial.resolve()) or not resolved.is_file():
            raise InstallerError("The application's program points outside its folder.")
        resolved.chmod(resolved.stat().st_mode | 0o500)
        if payload.exists():
            shutil.rmtree(payload)
        partial.replace(payload)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    name = report.title
    application_id = safe_id(f"portable-{name}-{report.sha256[:12]}")
    runtime: dict[str, object] = {}
    if _host_missing_libraries(payload):
        runtime = _portable_capsule(application_id, payload, report)
    icon = "application-x-executable"
    if layout.icon:
        source = payload / layout.icon
        try:
            resolved = source.resolve(strict=True)
            if resolved.is_relative_to(payload.resolve()) and resolved.is_file():
                _install_icon(resolved, application_id)
                icon = application_id
        except OSError:
            pass
    window_class = Path(layout.executable).name if layout.executable != "AppRun" else ""
    write_record(application_id, {
        "format": "portable", "name": name, "sha256": report.sha256, "version": report.version,
        "root": str(payload), "command": "/app/" + layout.executable, "icon": icon,
        "toolkit": layout.toolkit, "chromium": layout.toolkit == "electron",
        "source": str(report.path), "source_bytes": report.byte_size, "startup_wm_class": window_class,
        "mime_types": layout.mime_types, "launcher_metadata_version": LAUNCHER_METADATA_VERSION,
        **runtime,
    })
    write_launcher(application_id, name, report.summary, icon, window_class, mime_types=layout.mime_types)
    return name


def _install_flatpak(report: PackageReport) -> str:
    if report.kind == "flatpakrepo":
        remote = safe_id(report.title)
        _run(["flatpak", "remote-add", "--user", "--if-not-exists", remote, str(report.path)])
        write_record(f"flatpakrepo-{remote}", {
            "format": "flatpakrepo", "name": report.title, "remote": remote,
            "sha256": report.sha256, "source": str(report.path),
        })
    else:
        flatpak_id = report.details.get("Application ID", report.title)
        from .flatpak_backend import install as install_flatpak_transaction
        install_flatpak_transaction(report)
        verification = _run(["flatpak", "info", "--user", flatpak_id])
        if not verification.stdout.strip():
            raise InstallerError("Flatpak completed without publishing the reviewed application.")
        write_record(f"flatpak-{flatpak_id}", {
            "format": "flatpak", "name": report.title, "flatpak_id": flatpak_id,
            "sha256": report.sha256, "source": str(report.path),
            "mime_types": _register_flatpak_handlers(flatpak_id),
        })
    return report.title


def _register_flatpak_handlers(flatpak_id: str) -> list[str]:
    """Hold a Flatpak's declared link schemes with the entry Flatpak exported.

    Flatpak publishes the application's own launcher, carrying its own name, so
    there is nothing to rewrite: the schemes only need to be held, the same way
    they are for every other format.
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}", flatpak_id):
        return []
    exported = (Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
                / "flatpak/exports/share/applications" / f"{flatpak_id}.desktop")
    try:
        mime_types = desktop_mime_types(exported.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return []
    register_handlers(exported, mime_types)
    return mime_types


LAUNCHER_DIRECTORIES = {"/usr/share/applications", "/usr/local/share/applications"}


def _launcher_paths(listing: str) -> list[str]:
    return sorted({line.strip() for line in listing.splitlines()
                   if line.strip().endswith(".desktop") and
                   str(Path(line.strip()).parent) in LAUNCHER_DIRECTORIES})


def _identity_words(identity: str) -> set[str]:
    """The words a package name and its launcher could plausibly share."""
    parts = {part for part in re.split(r"[^A-Za-z0-9]+", identity.casefold()) if part}
    # A trailing packaging word is not part of the application's name:
    # "steam-launcher" ships "steam.desktop", "firefox-esr" ships "firefox".
    ignored = {"launcher", "bin", "common", "desktop", "app", "stable", "beta",
               "client", "installer", "release", "esr", "latest"}
    words = {part for part in parts if part not in ignored} or parts
    words.add("".join(parts))
    words.add(identity.casefold())
    return words


def _launcher_score(identity: str, path: str, contents: str) -> int:
    """How strongly a launcher claims to belong to `identity`. 0 means not at all."""
    if not identity:
        return 0
    words = _identity_words(identity)
    stem = Path(path).stem.casefold()
    try:
        name, _comment, _icon, command = parse_desktop(contents)
    except InstallerError:
        name, command = "", ""
    executable = Path(command.split()[0]).name.casefold() if command.split() else ""
    name_words = {part for part in re.split(r"[^A-Za-z0-9]+", name.casefold()) if part}
    score = 0
    if stem in words or stem.replace(".", "") in words:
        score += 4
    if executable and (executable in words or executable.replace("-", "") in words):
        score += 4
    if name_words & words:
        score += 2
    if any(word in stem for word in words if len(word) > 2):
        score += 1
    return score


def _container_desktop(container: str, package: str = "", kind: str = "", identity: str = "") -> tuple[str, str]:
    """The launcher this package published, never a neighbour's.

    The package's own file list is authoritative. When there is no package
    identity to ask with, the shared launcher directory is read instead — but a
    dependency's entry is never adopted just because it sorts first: a candidate
    has to name this application before it is accepted.
    """
    owned = bool(package)
    if package:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.:-]*", package):
            raise InstallerError("Invalid package identity for launcher discovery.")
        query = ["dpkg-query", "-L", package] if kind == "deb" else ["rpm", "-ql", package]
        found = _run(["podman", "exec", container, *query])
    else:
        found = _run([
            "podman", "exec", container, "sh", "-c",
            "find /usr/share/applications /usr/local/share/applications -maxdepth 1 -type f -name '*.desktop' 2>/dev/null | sort",
        ])
    paths = _launcher_paths(found.stdout)
    if not paths:
        if owned:
            raise InstallerError(
                f"{package} installed, but it published no application launcher of its own. "
                "Luma will not adopt another package's launcher for it.")
        raise InstallerError("The package installed, but it did not publish an application launcher.")
    visible: list[tuple[str, str]] = []
    for path in paths:
        contents = _run(["podman", "exec", container, "cat", path]).stdout
        if "NoDisplay=true" in contents or "Hidden=true" in contents:
            continue
        if not re.search(r"(?m)^Type=Application\s*$", contents):
            continue
        visible.append((contents, path))
    if not visible:
        raise InstallerError("The package did not publish a visible application launcher.")
    wanted = package or identity
    if owned and len(visible) == 1:
        return visible[0]
    best = max(visible, key=lambda entry: _launcher_score(wanted, entry[1], entry[0]))
    if _launcher_score(wanted, best[1], best[0]) > 0:
        return best
    if owned:
        # Several launchers, all from this package, none naming it: its own
        # first entry is still its own, which a directory scan cannot promise.
        return visible[0]
    raise InstallerError(
        "Luma could not tell which of the installed launchers belongs to this package, "
        "so it did not guess. Install a package that publishes its own application entry.")


def _prepare_runtime(container: str, package: str, kind: str, console: bool) -> dict[str, object]:
    """Give a capsule what a desktop system would have given the application
    (capsule_runtime.py): the graphics stack for anything with a window, and
    note whether the payload is Chromium-based so it starts on Wayland."""
    if not console:
        phase("Add graphics support", 1/3)
        _fetch(["podman", "exec", container, *capsule_runtime.install_command(kind)],
               source="the capsule's package index")
    _provide_locale(container, kind)
    files = capsule_runtime.package_files(container, package, kind)
    missing = _provide_missing_libraries(container, kind, files)
    return {"chromium": capsule_runtime.is_chromium(files), "missing_libraries": missing,
            "runtime_version": capsule_runtime.RUNTIME_VERSION}


def _provide_locale(container: str, kind: str) -> None:
    """The person's language inside the capsule; without it the app still runs."""
    command = capsule_runtime.locale_command(kind)
    if command:
        try:
            _fetch(["podman", "exec", container, *command], source="the capsule's package index")
        except InstallerError:
            pass


def _provide_missing_libraries(container: str, kind: str, files: str) -> list[str]:
    """Install what the package links against but never declared; return what is still missing."""
    missing = capsule_runtime.missing_libraries(container, files)
    command = capsule_runtime.library_install_command(kind, container, missing)
    if command:
        phase("Add missing libraries", 1/3)
        try:
            _fetch(["podman", "exec", container, *command], source="the capsule's package index")
        except InstallerError:
            pass
        missing = capsule_runtime.missing_libraries(container, files)
    return missing


def upgrade_capsule_runtime(record: dict[str, object]) -> dict[str, object]:
    """Bring a capsule installed before the current runtime rules up to date, once.

    Adding the graphics stack needs the network; if it cannot be reached the
    application still opens as it did, and the upgrade is tried next time.
    """
    version = int(record.get("runtime_version", 0) or 0)
    if version >= capsule_runtime.RUNTIME_VERSION:
        return record
    kind = str(record.get("format", ""))
    package = record.get("package")
    portable = kind == "portable" and bool(record.get("image")) and bool(record.get("root"))
    if not portable and (kind not in {"deb", "rpm"} or not isinstance(package, str) or not package):
        return record
    if not capsule_runtime.upgrade_needed(kind, version):
        updated = {**record, "runtime_version": capsule_runtime.RUNTIME_VERSION}
        write_record(str(record["application_id"]), updated)
        return updated
    image = str(record["image"])
    # The keepalive is the command, not the entrypoint: a committed image keeps
    # its entrypoint, and every later launch would run sleep with the
    # application's arguments.
    mount = ["--volume", f"{record['root']}:/app:ro,Z"] if portable else []
    created = _run(["podman", "create", "--network=host", *mount, image, "sleep", "infinity"])
    container = created.stdout.strip()
    try:
        _run(["podman", "start", container])
        if portable:
            files = _elf_files(Path(str(record["root"])), "/app")
            kind_for_packages = "rpm"
        else:
            files = capsule_runtime.package_files(container, str(package), kind)
            kind_for_packages = kind
        if kind == "deb":
            _fetch(["podman", "exec", container, "apt-get", "update"], source="the capsule's package index")
        if not record.get("terminal"):
            _fetch(["podman", "exec", container, *capsule_runtime.install_command(kind_for_packages)],
                   source="the capsule's package index")
        _provide_locale(container, kind_for_packages)
        missing = _provide_missing_libraries(container, kind_for_packages, files)
        if portable:
            bundled = _bundled_names(Path(str(record["root"])))
            missing = [name for name in missing if name not in bundled]
        runtime = {"chromium": bool(record.get("chromium")) if portable else capsule_runtime.is_chromium(files),
                   "missing_libraries": missing, "runtime_version": capsule_runtime.RUNTIME_VERSION}
        _commit_container("podman", container, image)
        updated = {**record, **runtime}
        write_record(str(record["application_id"]), updated)
        return updated
    finally:
        subprocess.run(["podman", "rm", "-f", "--time=0", container], check=False, capture_output=True)


def _commit_container(podman: str, container: str, output_image: str) -> None:
    # Package installation has finished and the only remaining process is the
    # inert keepalive. Stop first so the filesystem snapshot is stable without
    # relying on cgroup freezer support, which is not available in every
    # rootless/systemd scope.
    _run([podman, "stop", "--time", "5", container])
    _run([podman, "commit", container, output_image])


def _icon_destination(application_id: str, suffix: str) -> Path:
    category = "scalable" if suffix.lower() == ".svg" else "128x128"
    root = Path.home() / ".local/share/icons/hicolor" / category / "apps"
    root.mkdir(mode=0o755, parents=True, exist_ok=True)
    return root / f"{application_id}{suffix.lower()}"


HICOLOR_SIZES = (16, 22, 24, 32, 48, 64, 96, 128, 256, 512)


def _install_icon(source: Path, application_id: str) -> Path:
    """Publish an application's icon at the sizes the icon theme looks for.

    Artwork copied as-is into one folder was drawn from whatever size it came
    in: a 1024px logo filed as 128px was shrunk eightfold by the renderer in a
    single step and looked grainy, and a 64px one filed the same way was
    stretched. A vector is published as a vector; a raster is resampled once,
    carefully, to every standard size up to its own. Returns the largest file.
    """
    root = Path.home() / ".local/share/icons/hicolor"
    for stale in root.glob(f"*/apps/{application_id}.*"):
        stale.unlink(missing_ok=True)
    if source.suffix.lower() == ".svg":
        destination = root / "scalable/apps" / f"{application_id}.svg"
        destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return destination
    # GLib is imported first: naming GLib.Error in the except clause of the
    # same statement that failed to import it raised UnboundLocalError on any
    # system without the GdkPixbuf typelib, and the whole install failed.
    try:
        from gi.repository import GLib
        failures = (ImportError, ValueError, GLib.Error)
    except ImportError:
        failures = (ImportError, ValueError)
    try:
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        artwork = GdkPixbuf.Pixbuf.new_from_file(str(source))
    except failures:
        destination = _icon_destination(application_id, source.suffix or ".png")
        shutil.copy2(source, destination)
        return destination
    width, height = artwork.get_width(), artwork.get_height()
    side = max(width, height)
    if width != height:
        square = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, side, side)
        square.fill(0)
        artwork.composite(square, (side - width) // 2, (side - height) // 2, width, height,
                          (side - width) // 2, (side - height) // 2, 1, 1, GdkPixbuf.InterpType.NEAREST, 255)
        artwork = square
    sizes = [size for size in HICOLOR_SIZES if size <= side] or [min(HICOLOR_SIZES, key=lambda size: abs(size - side))]
    largest = root
    for size in sizes:
        destination = root / f"{size}x{size}/apps" / f"{application_id}.png"
        destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        scaled = artwork if size == side else artwork.scale_simple(size, size, GdkPixbuf.InterpType.HYPER)
        scaled.savev(str(destination), "png", [], [])
        largest = destination
    return largest


def _export_container_icon(container: str, icon: str, application_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,160}", icon):
        return "application-x-executable"
    result = _run([
        "podman", "exec", container, "sh", "-c",
        "find /usr/share/icons /usr/share/pixmaps -type f "
        f"\\( -name '{icon}.svg' -o -name '{icon}.png' -o -name '{icon}.xpm' \\) "
        "2>/dev/null",
    ])
    source = _best_icon_path(result.stdout.splitlines())
    if not source:
        return icon
    suffix = Path(source).suffix
    with tempfile.TemporaryDirectory() as directory:
        copied = Path(directory) / f"icon{suffix}"
        _run(["podman", "cp", f"{container}:{source}", str(copied)])
        destination = _install_icon(copied, application_id)
    from luma_appkit.icon_assets import prepare_application_icon
    return str(prepare_application_icon(destination))


def _best_icon_path(paths: list[str]) -> str:
    """The sharpest artwork among an icon's copies: a vector, else the largest raster.

    Sorting paths by name chose 64x64 over 512x512 -- "6" sorts after "5".
    """
    def rank(path: str) -> tuple[int, int]:
        if path.endswith(".svg"):
            return (2, 0)
        match = re.search(r"/(\d+)x\d+(?:@(\d+))?/", path)
        if match:
            return (1, int(match.group(1)) * int(match.group(2) or 1))
        return (0, 0)
    candidates = [path.strip() for path in paths if path.strip()]
    return max(candidates, key=rank) if candidates else ""


def _export_appimage_icon(payload: Path, icon: str, application_id: str) -> str:
    payload_root = payload.resolve()
    candidates: list[Path] = []
    if icon and "/" not in icon:
        for suffix in (".svg", ".png", ".xpm"):
            candidates.extend(payload.rglob(f"{icon}{suffix}"))
    directory_icon = payload / ".DirIcon"
    if directory_icon.exists():
        candidates.insert(0, directory_icon)
    source: Path | None = None
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_relative_to(payload_root) and resolved.is_file():
            source = resolved
            break
    if source is None:
        return "application-x-executable"
    _install_icon(source, application_id)
    return application_id


def _install_deb(report: PackageReport) -> str:
    podman = shutil.which("podman")
    if not podman:
        raise InstallerError("The Debian capsule engine is not installed.")
    host = {"amd64": "x86_64", "arm64": "aarch64"}.get(platform.machine().lower(), platform.machine().lower())
    image = DEBIAN_IMAGES.get(host)
    if image is None:
        raise InstallerError(f"Debian capsules are not available on {host} yet.")
    phase("Prepare application", 1/3)
    staged = stage_user_copy(report.path, report.sha256)
    application_id = safe_id(f"deb-{report.title}-{report.sha256[:12]}")
    container = f"luma-install-{application_id}"
    output_image = f"localhost/luma-deb-{application_id}:{report.sha256[:12]}"
    require_free_space(Path.home() / ".local/share/containers", max(report.byte_size * 3, 2 * 1024 ** 3), "create this Debian application capsule")
    _fetch([podman, "pull", image], source="the Debian base image")
    image_existed = subprocess.run([podman, "image", "exists", output_image], check=False).returncode == 0
    committed = False
    subprocess.run([podman, "rm", "-f", container], check=False, capture_output=True)
    try:
        _run([
            podman, "create", "--name", container, "--network=slirp4netns",
            "--volume", f"{staged}:/tmp/application.deb:ro,Z", image, "sleep", "infinity",
        ])
        _run([podman, "start", container])
        _fetch([podman, "exec", container, "apt-get", "update"], source="the Debian package index")
        _fetch([
            podman, "exec", container, "env", "DEBIAN_FRONTEND=noninteractive",
            "apt-get", "install", "-y", "--no-install-recommends", "/tmp/application.deb", "libglib2.0-bin",
        ], source="the Debian package index")
        package = _run([podman, "exec", container, "dpkg-deb", "-f", "/tmp/application.deb", "Package"]).stdout.strip()
        desktop_contents, desktop_path = _container_desktop(container, package, "deb", report.title)
        name, comment, icon, command = parse_desktop(desktop_contents)
        window_class = desktop_window_class(desktop_contents, desktop_path)
        exported_icon = _export_container_icon(container, icon, application_id)
        console = desktop_is_console(desktop_contents)
        runtime = _prepare_runtime(container, package, "deb", console)
        _commit_container(podman, container, output_image)
        committed = True
        phase("Register", 2/3)
        mime_types = desktop_mime_types(desktop_contents)
        write_launcher(application_id, name, comment, exported_icon, window_class, console, mime_types)
        exported_command, shim_refusal = export_command(application_id, command_name(command)) if console else ("", "")
        write_record(application_id, {
            "format": "deb", "name": name, "sha256": report.sha256, "package": package,
            "image": output_image, "command": command, "desktop_path": desktop_path, "icon": exported_icon, "startup_wm_class": window_class, "launcher_metadata_version": LAUNCHER_METADATA_VERSION,
            "terminal": console, "exported_command": exported_command, "command_not_exported": shim_refusal,
            "mime_types": mime_types, **runtime,
        })
    except Exception:
        if committed and not image_existed:
            subprocess.run([podman, "image", "rm", "--force", output_image], check=False, capture_output=True)
        raise
    finally:
        subprocess.run([podman, "rm", "-f", container], check=False, capture_output=True)
    return name


def _install_rpm_capsule(report: PackageReport) -> str:
    podman = shutil.which("podman")
    if not podman:
        raise InstallerError("The Fedora capsule engine is not installed.")
    host = {"amd64": "x86_64", "arm64": "aarch64"}.get(platform.machine().lower(), platform.machine().lower())
    image = FEDORA_IMAGES.get(host)
    if image is None:
        raise InstallerError(f"Fedora application capsules are not available on {host} yet.")
    phase("Prepare application", 1/3)
    staged = stage_user_copy(report.path, report.sha256)
    application_id = safe_id(f"rpm-{report.details.get('Package name', report.title)}-{report.sha256[:12]}")
    container = f"luma-install-{application_id}"
    output_image = f"localhost/luma-rpm-{application_id}:{report.sha256[:12]}"
    require_free_space(Path.home() / ".local/share/containers", max(report.byte_size * 3, 2 * 1024 ** 3), "create this Fedora application capsule")
    _fetch([podman, "pull", image], source="the Fedora base image")
    image_existed = subprocess.run([podman, "image", "exists", output_image], check=False).returncode == 0
    committed = False
    subprocess.run([podman, "rm", "-f", container], check=False, capture_output=True)
    try:
        _run([
            podman, "create", "--name", container, "--network=slirp4netns",
            "--volume", f"{staged}:/tmp/application.rpm:ro,Z", image, "sleep", "infinity",
        ])
        _run([podman, "start", container])
        _fetch([podman, "exec", container, "dnf5", "install", "-y", "/tmp/application.rpm", "glib2"],
               source="the Fedora package index")
        package = str(report.details.get("Package name", ""))
        desktop_contents, desktop_path = _container_desktop(container, package, "rpm", report.title)
        name, comment, icon, command = parse_desktop(desktop_contents)
        window_class = desktop_window_class(desktop_contents, desktop_path)
        exported_icon = _export_container_icon(container, icon, application_id)
        console = desktop_is_console(desktop_contents)
        runtime = _prepare_runtime(container, package, "rpm", console)
        _commit_container(podman, container, output_image)
        committed = True
        phase("Register", 2/3)
        mime_types = desktop_mime_types(desktop_contents)
        write_launcher(application_id, name, comment, exported_icon, window_class, console, mime_types)
        exported_command, shim_refusal = export_command(application_id, command_name(command)) if console else ("", "")
        write_record(application_id, {
            "format": "rpm", "name": name, "sha256": report.sha256, "package": package,
            "image": output_image, "command": command, "desktop_path": desktop_path, "icon": exported_icon, "startup_wm_class": window_class, "launcher_metadata_version": LAUNCHER_METADATA_VERSION,
            "terminal": console, "exported_command": exported_command, "command_not_exported": shim_refusal,
            "mime_types": mime_types, **runtime,
        })
    except Exception:
        if committed and not image_existed:
            subprocess.run([podman, "image", "rm", "--force", output_image], check=False, capture_output=True)
        raise
    finally:
        subprocess.run([podman, "rm", "-f", container], check=False, capture_output=True)
    return name


def _install_system(report: PackageReport) -> str:
    phase("Prepare application", 1/3)
    staged = stage_user_copy(report.path, report.sha256)
    helper = "/usr/libexec/luma-installer-system"
    if not Path(helper).is_file():
        raise InstallerError("Luma's privileged installer helper is unavailable.")
    _run(["pkexec", helper, report.kind, str(staged), report.sha256])
    phase("Register", 2/3)
    restart_required = system_restart_required(report.sha256, report.requires_restart)
    write_record(f"{report.kind}-{report.title}-{report.sha256[:12]}", {
        "format": report.kind, "name": report.title, "sha256": report.sha256,
        "package_name": report.details.get("Package name", report.title),
        "source": str(report.path), "system_receipt": report.sha256,
        "restart_required": restart_required,
    })
    return report.title


def system_restart_required(sha256: str, default: bool) -> bool:
    """Whether the privileged helper left this system change for a restart:
    its root-owned receipt says so (ADR-038 applies RPMs live when it can)."""
    try:
        path = Path("/var/lib/luma-installer/receipts") / f"{sha256}.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("restart_required"), bool):
            return value["restart_required"]
    except (OSError, ValueError):
        pass
    return default


def install(report: PackageReport) -> str:
    phase("Check the file", 0, True)
    with transaction_lock(report.sha256):
        if fingerprint(report.path)[1] != report.sha256:
            raise InstallerError("The package changed after review. Reopen it and review the new fingerprint.")
        phase("Install application", 1/3)
        if report.kind == "appimage":
            return _install_appimage(report)
        if report.kind == "portable":
            return _install_portable(report)
        if report.kind in {"flatpak", "flatpakref", "flatpakrepo"}:
            return _install_flatpak(report)
        if report.kind == "deb":
            return _install_deb(report)
        if report.kind == "rpm" and not report.requires_system_change:
            return _install_rpm_capsule(report)
        if report.kind in {"rpm", "snap"}:
            return _install_system(report)
        raise InstallerError("The selected format has no installation backend.")


PACKAGE_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.:-]*")


def _private_home(record: dict[str, object]) -> Path | None:
    sha256 = record.get("sha256")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        return None
    return Path.home() / ".local/share/luma/installer/data" / sha256


def _own_schemes(record: dict[str, object]) -> list[str]:
    """The link schemes the application claimed for itself once it was running."""
    private_home = _private_home(record)
    return capsule_declared_schemes(private_home) if private_home else []


def _stored_mime_types(record: dict[str, object]) -> list[str]:
    value = record.get("mime_types")
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def adopt_own_schemes(record: dict[str, object]) -> dict[str, object]:
    """Republish the link schemes the application registered for itself.

    An application that calls setAsDefaultProtocolClient writes the scheme into
    its own ~/.config/mimeapps.list, which inside a capsule is private: the
    session that opens the browser never sees it, so the browser has no name to
    show and falls back to printing its own opener command. The scheme is the
    application's own declaration, so it is republished against the launcher
    Luma wrote, where it carries the application's name. Nothing is rewritten
    once the launcher already lists them.
    """
    declared = _own_schemes(record)
    if not declared:
        return record
    stored = _stored_mime_types(record)
    merged = list(dict.fromkeys([*stored, *declared]))
    if merged == stored:
        return record
    application_id = safe_id(str(record.get("application_id", "")))
    launcher = applications_root() / f"org.projectluma.Installed.{application_id}.desktop"
    try:
        entry = parse_desktop_entry(launcher.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, InstallerError):
        return record
    write_launcher(application_id, entry.get("Name", str(record.get("name", ""))), entry.get("Comment", ""),
                   entry.get("Icon", str(record.get("icon", ""))), entry.get("StartupWMClass", ""),
                   entry.get("Terminal", "false").strip().lower() == "true", merged)
    updated = {**record, "mime_types": merged}
    write_record(application_id, updated)
    return updated


def _record_package(record: dict[str, object]) -> str:
    """The package name a capsule record stands for, or '' if it has none.

    Records written before the package name was stored still carry the launcher
    the package shipped, so its path names the package well enough to find that
    launcher again inside the capsule. Without this, an older capsule can never
    be brought up to date and keeps whatever its first launcher said.
    """
    for candidate in (
        record.get("package"),
        record.get("package_name"),
        (record.get("reviewed_details") or {}).get("Package name")
        if isinstance(record.get("reviewed_details"), dict) else None,
        Path(str(record.get("desktop_path", ""))).stem or None,
    ):
        if isinstance(candidate, str) and candidate and PACKAGE_IDENTITY.fullmatch(candidate):
            return candidate
    return ""


def refresh_folder_launcher(record: dict[str, object]) -> dict[str, object]:
    """Bring an AppImage or folder launcher up to date from its own payload.

    The payload stays on disk, so the launcher the application shipped can be
    read again. Applications installed before Luma published link schemes keep
    their launcher otherwise unchanged.
    """
    if record.get("launcher_metadata_version", 0) >= LAUNCHER_METADATA_VERSION:
        return record
    application_id = safe_id(str(record.get("application_id", "")))
    root = Path(str(record.get("root", "")))
    if not application_id or not root.is_dir():
        return record
    launchers = sorted(path for path in root.glob("*.desktop")
                       if path.resolve().is_relative_to(root.resolve()) and path.is_file())
    mime_types = _stored_mime_types(record)
    if launchers:
        try:
            mime_types = list(dict.fromkeys([*desktop_mime_types(launchers[0].read_text(encoding="utf-8")),
                                             *mime_types]))
        except (OSError, UnicodeError):
            pass
    installed = applications_root() / f"org.projectluma.Installed.{application_id}.desktop"
    try:
        entry = parse_desktop_entry(installed.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, InstallerError):
        return record
    write_launcher(application_id, entry.get("Name", str(record.get("name", ""))), entry.get("Comment", ""),
                   entry.get("Icon", str(record.get("icon", ""))), entry.get("StartupWMClass", ""),
                   entry.get("Terminal", "false").strip().lower() == "true", mime_types)
    updated = {**record, "mime_types": mime_types, "launcher_metadata_version": LAUNCHER_METADATA_VERSION}
    write_record(application_id, updated)
    return updated


def refresh_capsule_launcher(record: dict[str, object]) -> dict[str, object]:
    """One-time metadata migration for capsules installed before console support."""
    if record.get("launcher_metadata_version", 0) == LAUNCHER_METADATA_VERSION:
        return record
    application_id = str(record["application_id"])
    package = _record_package(record)
    if not package:
        raise InstallerError("Cannot migrate a capsule launcher without an established package identity.")
    kind = str(record.get("format", ""))
    if kind not in {"deb", "rpm"}:
        raise InstallerError("Capsule launcher migration requires a Debian or RPM package identity.")
    container = "luma-metadata-" + application_id
    created = _run(["podman", "create", "--network=none", "--entrypoint=/usr/bin/sleep",
                    str(record["image"]), "infinity"])
    container = created.stdout.strip()
    try:
        _run(["podman", "start", container])
        desktop_contents, desktop_path = _container_desktop(container, package, kind, str(record.get("name", "")))
        name, comment, icon, command = parse_desktop(desktop_contents)
        window_class = desktop_window_class(desktop_contents, desktop_path)
        exported_icon = _export_container_icon(container, icon, application_id)
        console = desktop_is_console(desktop_contents)
        mime_types = list(dict.fromkeys([*desktop_mime_types(desktop_contents), *_own_schemes(record)]))
        write_launcher(application_id, name, comment, exported_icon, window_class, console, mime_types)
        exported_command, shim_refusal = export_command(application_id, command_name(command)) if console else ("", "")
        updated = {**record, "name": name, "icon": exported_icon, "command": command,
                   "desktop_path": desktop_path, "startup_wm_class": window_class,
                   "terminal": console, "exported_command": exported_command,
                   "command_not_exported": shim_refusal, "mime_types": mime_types,
                   "launcher_metadata_version": LAUNCHER_METADATA_VERSION}
        write_record(application_id, updated)
        return updated
    finally:
        subprocess.run(["podman", "rm", "-f", "--time=0", container], check=False, capture_output=True)
