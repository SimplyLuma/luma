# SPDX-License-Identifier: Apache-2.0
"""Install a package file as an application in the store (ADR-052 step 2).

What Valet does with a .deb, an .rpm, an AppImage or an archive:

1. **Build.** The package's own distribution resolves and installs it into a
   root of its own (``closure``), rootless, in a sandbox.
2. **Check.** The program the launcher starts must find every library it
   links against (``tree_check``).
3. **Commit** the root to the store and compose the checkout it runs from,
   with the device's graphics layer (``device_graphics``).
4. **Hidden start.** The application is started once where nobody sees it
   (``closure_launcher.hidden_start``).
5. **Publish**: launcher, icon, handlers, a command for a console program,
   and the record, which names the tree.

If the build, the library check or the hidden start fails, ``Fallback`` is
raised: the caller installs the package the way it was installed before (a
podman capsule, or the unpacked folder) and writes into its record why.
Nothing of the failed attempt stays in the store.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from . import capsule_identity, closure, closure_launcher, device_graphics
from .app_store import Store
from .desktop import command_name, export_command, iter_records, read_record, safe_id, write_launcher, write_record
from .errors import InstallerError
from .progress import Cancelled, phase

KINDS = {"deb", "rpm", "appimage", "portable"}
STAGES = ("build", "libraries", "launch")
# For the test that proves the fallback: name a stage and the store path fails there.
FAIL_VARIABLE = "LUMA_APP_STORE_FAIL"
SWITCH_VARIABLE = "LUMA_APP_STORE"
SWITCH_FILES = (Path("/etc/luma/app-store"), Path("/usr/share/luma/installer/app-store"))


class Fallback(InstallerError):
    """The store could not hold this application; it is installed the earlier way."""

    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(reason)
        self.stage, self.reason = stage, reason

    def note(self) -> dict[str, object]:
        return {"stage": self.stage, "reason": self.reason[:600], "unix": int(time.time())}


def enabled() -> bool:
    """Whether package files go to the store first on this device.

    On by default. Off with LUMA_APP_STORE=off, or a first line "off" in
    /etc/luma/app-store (an administrator's switch) or the image's own file.
    """
    value = os.environ.get(SWITCH_VARIABLE, "").strip().lower()
    if not value:
        for path in SWITCH_FILES:
            try:
                value = path.read_text().split("\n", 1)[0].strip().lower()
                break
            except OSError:
                continue
    return value not in {"off", "0", "no", "false"}


def missing_tools(family: str) -> list[str]:
    tools = ["ostree", "bwrap", "unshare", *(("debootstrap",) if family == "deb" else ("dnf5", "rpm"))]
    return [tool for tool in tools if not shutil.which(tool)]


def scratch() -> tuple[Path, Path]:
    """(work, download cache): caches, safe to delete at any time."""
    root = Path(os.environ.get("LUMA_APP_SCRATCH") or Path.home() / ".cache/luma/app-closures")
    work, cache = Path(os.environ.get("LUMA_APP_WORK") or root / "work"), root / "cache"
    for folder in (work, cache):
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return work, cache


def log_path(application_id: str) -> Path:
    folder = Path.home() / ".local/state/luma/app-builds"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder / f"{safe_id(application_id)}.log"


def _forced(stage: str) -> None:
    if os.environ.get(FAIL_VARIABLE, "") == stage:
        raise Fallback(stage, f"Stopped at the {stage} stage because {FAIL_VARIABLE} asked for it.")


def build_tree(store: Store, application_id: str, kind: str, *, package_file: Path | None = None,
               payload: Path | None = None, sha256: str = "", package: str = "", command: str = "",
               display_name: str = "", record: dict[str, object] | None = None, ref: str = "", parent: str = "",
               spec: closure.Spec | None = None,
               start: Callable[..., tuple[bool, str]] | None = None) -> dict[str, object]:
    """Build, check, commit, compose and try one application. Returns {"commit", "tree", "why", "graphics"}.

    Raises Fallback with the stage that failed; the store is left as it was.
    """
    family = "deb" if kind == "deb" else "rpm"
    absent = missing_tools(family)
    if absent:
        raise Fallback("build", f"Not installed on this system: {', '.join(absent)}.")
    work, cache = scratch()
    log = log_path(application_id)
    store.ensure()
    ref = ref or store.app_ref(application_id)
    existed = store.resolve(ref)
    built = None
    try:
        _forced("build")
        try:
            if spec is not None:
                candidates = [spec.userland]
            elif kind in {"deb", "rpm"}:
                if package_file is None:
                    raise InstallerError("The package file is no longer kept on this device.")
                spec, candidates = closure.spec_for_file(package_file, application_id)
                spec.package_file = str(store.keep_package(package_file, spec.sha256))
            else:
                if payload is None:
                    raise InstallerError("The application's unpacked folder is missing.")
                candidates = [name for name, userland in closure.USERLANDS.items() if userland.family == "rpm"]
                spec = closure.Spec(app_id=application_id, kind=kind, userland=candidates[0], package=package or application_id,
                                    sha256=sha256, command=command, display_name=display_name)
            built = closure.build_first(spec, candidates, work, cache, store, log, payload)
        except Cancelled:
            raise
        except (InstallerError, OSError) as error:
            raise Fallback("build", str(error).strip().splitlines()[-1][:600] if str(error).strip() else "The build failed.") from error
        tree = built.record
        exports = dict(tree.get("exports") or {})  # type: ignore[arg-type]
        if not exports.get("command"):
            raise Fallback("build", "The package did not publish an application launcher of its own.")
        _forced("libraries")
        if tree.get("unresolved"):
            raise Fallback("libraries", "Its program needs libraries its distribution does not provide: "
                           + ", ".join(tree["unresolved"][:12]))  # type: ignore[index]
        try:
            origin = dict(tree["origin"])  # type: ignore[arg-type]
            commit = store.commit_tree(built.root, ref, tree, subject=f"{application_id} {origin.get('version', '')}".strip(),
                                       parent=parent)
        except (InstallerError, OSError) as error:
            raise Fallback("build", f"The tree could not be stored: {error}") from error
    finally:
        if built is not None:
            closure.remove_tree(built.root.parent)
    try:
        phase("Prepare graphics", 2/3)
        if tree.get("windowed", True):
            plan = device_graphics.ensure_layers(str(tree["userland"]), store, work, cache, log)
        else:
            # A console program draws nothing; no graphics layer is built on its account.
            plan = {"route": "", "reason": "A console program; no graphics layer."}
        closure_launcher.deployment(store, application_id, commit=commit)
        _forced("launch")
        works, why = (start or closure_launcher.hidden_start)(store, application_id, record, commit=commit)
        if not works:
            raise Fallback("launch", f"It did not start in the hidden test: {why}")
    except Fallback:
        discard(store, application_id, ref, existed)
        raise
    except Cancelled:
        discard(store, application_id, ref, existed)
        raise
    except (InstallerError, OSError) as error:
        discard(store, application_id, ref, existed)
        raise Fallback("launch", f"The checkout could not be prepared: {error}") from error
    return {"commit": commit, "tree": tree, "why": why,
            "graphics": {key: plan.get(key) for key in ("route", "fallback", "replaces_glibc", "nvidia", "reason")}}


def discard(store: Store, application_id: str, ref: str, existed: str = "") -> None:
    """Take a failed attempt out of the store again."""
    try:
        if existed and not ref.startswith("pending/"):
            store.promote(ref, existed)
        else:
            store.delete_ref(ref)
        store.collect()
    except (InstallerError, OSError):
        pass


def find_icon(checkout: Path, icon: str, application_id: str, unpacked: bool) -> Path | None:
    """The sharpest copy of the application's icon in its tree."""
    from .backends import _best_icon_path
    if not icon:
        icon = ""
    if icon.startswith("/"):
        direct = checkout / icon.lstrip("/")
        return direct if direct.is_file() else None
    if "/" in icon:
        return None
    found: list[str] = []
    folders = [checkout / "usr/share/icons", checkout / "usr/share/pixmaps", checkout / "usr/local/share/icons"]
    if unpacked:
        folders.insert(0, checkout / "opt" / application_id)
        marker = checkout / "opt" / application_id / ".DirIcon"
        if marker.exists():
            try:
                resolved = marker.resolve(strict=True)
                if resolved.is_relative_to(checkout.resolve()) and resolved.is_file():
                    return resolved
            except OSError:
                pass
    if not icon:
        return None
    for folder in folders:
        if not folder.is_dir():
            continue
        for directory, _folders, files in os.walk(folder):
            for suffix in (".svg", ".png", ".xpm"):
                if icon + suffix in files:
                    found.append(os.path.join(directory, icon + suffix))
            if len(found) > 64:
                break
    best = _best_icon_path(found)
    return Path(best) if best else None


def export_icon(checkout: Path, icon: str, application_id: str, unpacked: bool) -> str:
    from .backends import _install_icon
    source = find_icon(checkout, icon, application_id, unpacked)
    if source is None:
        return icon if icon and "/" not in icon else "application-x-executable"
    with tempfile.TemporaryDirectory() as directory:
        # A copy: the store's file is shared with every tree that contains it.
        copied = Path(directory) / f"icon{source.suffix or '.png'}"
        shutil.copyfile(source, copied)
        destination = _install_icon(copied, application_id)
    try:
        from luma_appkit.icon_assets import prepare_application_icon
        return str(prepare_application_icon(destination))
    except Exception:  # noqa: BLE001 - the plate is decoration; the icon is installed either way
        return application_id


def described(application_id: str, kind: str, built: dict[str, object], sha256: str, *, package: str = "",
              fallback_name: str = "", kept: dict[str, object] | None = None) -> dict[str, object]:
    """The installer record of an application that lives in the store."""
    from .backends import LAUNCHER_METADATA_VERSION
    from . import capsule_runtime
    tree = dict(built["tree"])  # type: ignore[arg-type]
    exports = dict(tree.get("exports") or {})
    origin = dict(tree.get("origin") or {})
    return {
        "format": kind, "name": str(exports.get("name") or fallback_name or application_id), "sha256": sha256,
        "package": package or str(origin.get("package") or ""), "command": str(exports.get("command") or ""),
        "desktop_path": str(exports.get("desktop_path") or ""), "startup_wm_class": str(exports.get("startup_wm_class") or ""),
        "terminal": bool(exports.get("terminal")), "mime_types": list(exports.get("mime_types") or []),
        "chromium": bool(exports.get("chromium")), "bus_names": list(exports.get("bus_names") or []),
        "launcher_metadata_version": LAUNCHER_METADATA_VERSION, "runtime_version": capsule_runtime.RUNTIME_VERSION,
        "store": {"id": application_id, "userland": tree.get("userland"), "architecture": tree.get("architecture"),
                  "commit": built["commit"], "graphics": built.get("graphics"), "installed_unix": int(time.time())},
        "data_home": str(closure_launcher.data_home(application_id)),
        "vendor_repositories": vendor_repositories(tree),
        **(kept or {}),
    }


def vendor_repositories(tree: dict[str, object]) -> list[dict[str, str]]:
    """The software sources the package added for itself: where its maker's updates come from."""
    import base64
    import re
    found: list[dict[str, str]] = []
    for entry in dict(tree.get("origin") or {}).get("added_repositories") or []:  # type: ignore[union-attr]
        path = str(entry.get("path") or "")
        if not path.startswith(("etc/apt/sources.list.d/", "etc/yum.repos.d/")):
            continue
        try:
            text = base64.b64decode(entry.get("base64") or "").decode("utf-8", "replace")
        except ValueError:
            continue
        for uri in re.findall(r"(https?://[^\s\"'\]]+)", text):
            row = {"file": "/" + path, "uri": uri.rstrip("/")}
            if row not in found:
                found.append(row)
    return found[:16]


def publish(application_id: str, record: dict[str, object], checkout: Path, icon: str) -> dict[str, object]:
    """Launcher, icon, handlers and command for a record; returns the record as written."""
    unpacked = record.get("format") in {"appimage", "portable"}
    exported_icon = export_icon(checkout, icon, application_id, unpacked)
    console = bool(record.get("terminal"))
    write_launcher(application_id, str(record["name"]), str(record.get("comment") or ""), exported_icon,
                   str(record.get("startup_wm_class") or ""), console, list(record.get("mime_types") or []))  # type: ignore[arg-type]
    exported_command, refusal = export_command(application_id, command_name(str(record["command"]))) if console else ("", "")
    record = {**record, "icon": exported_icon, "exported_command": exported_command, "command_not_exported": refusal}
    record.pop("comment", None)
    write_record(application_id, record)
    # As it is stored: with the identity the launcher and a watched first start read from it.
    return read_record(application_id)


def application_identity(kind: str, title: str, sha256: str) -> str:
    """The same identity the capsule path gives the same file, so a fallback and a later move keep it."""
    return safe_id(f"{kind}-{title}-{sha256[:12]}")


def install(report, staged: Path, *, start: Callable[..., tuple[bool, str]] | None = None) -> str:
    """Install a reviewed package file from its staged copy. Returns the application's name."""
    from .backends import _kept_identity
    kind = report.kind
    store = Store()
    payload_root: Path | None = None
    command = display_name = ""
    try:
        if kind == "deb":
            application_id = application_identity("deb", report.title, report.sha256)
        elif kind == "rpm":
            application_id = application_identity("rpm", str(report.details.get("Package name", report.title)), report.sha256)
        elif kind == "appimage":
            application_id, payload_root = _unpack_appimage(staged, report)
        elif kind == "portable":
            application_id, payload_root, command, display_name = _unpack_archive(staged, report)
        else:
            raise Fallback("build", "This format has no place in the store.")
        phase("Build application", 1/3)
        kept = _kept_identity(application_id)
        try:
            existing = read_record(application_id)
        except InstallerError:
            existing = {}
        identity = {"application_id": application_id, "sha256": report.sha256, **kept}
        identity = capsule_identity.ensure_level(identity)
        built = build_tree(store, application_id, kind, package_file=staged if kind in {"deb", "rpm"} else None,
                           payload=payload_root, sha256=report.sha256, package=str(report.details.get("Package name", "")),
                           command=command, display_name=display_name or report.title, record=identity, start=start)
    finally:
        if payload_root is not None:
            shutil.rmtree(payload_root.parent, ignore_errors=True)
    phase("Register", 2/3)
    exports = dict(dict(built["tree"]).get("exports") or {})  # type: ignore[arg-type]
    record = described(application_id, kind, built, report.sha256, fallback_name=report.title,
                       kept={key: identity[key] for key in ("mcs_level", "keyring") if key in identity})
    record["comment"] = str(exports.get("comment") or report.summary or "")
    if existing.get("data_home"):
        record["data_home"] = existing["data_home"]
    checkout, _tree = closure_launcher.deployment(store, application_id)
    publish(application_id, record, checkout, str(exports.get("icon") or ""))
    return str(record["name"])


def _unpack_appimage(staged: Path, report) -> tuple[str, Path]:
    from . import appimage_payload
    from .desktop import parse_desktop
    work, _cache = scratch()
    holder = Path(tempfile.mkdtemp(prefix="appimage-", dir=work))
    payload = holder / "AppDir"
    try:
        kind, offset = appimage_payload.locate(staged)
        if kind == appimage_payload.SQUASHFS:
            closure._run(["unsquashfs", "-quiet", "-no-xattrs", "-o", str(offset), "-d", str(payload), str(staged)], timeout=1800)
        else:
            appimage_payload.extract_with_runtime(staged, payload)
        if not (payload / "AppRun").exists():
            raise InstallerError("The AppImage payload does not contain AppRun.")
        name = report.title
        for launcher in sorted(payload.glob("*.desktop")):
            try:
                name = parse_desktop(launcher.read_text(encoding="utf-8"))[0]
                break
            except (OSError, UnicodeError, InstallerError):
                continue
    except (InstallerError, OSError) as error:
        shutil.rmtree(holder, ignore_errors=True)
        raise Fallback("build", f"The AppImage could not be unpacked: {error}") from error
    return safe_id(f"appimage-{name}-{report.sha256[:12]}"), payload


def _unpack_archive(staged: Path, report) -> tuple[str, Path, str, str]:
    from .archive_app import analyse, extract
    work, _cache = scratch()
    holder = Path(tempfile.mkdtemp(prefix="archive-", dir=work))
    payload = holder / "app"
    try:
        layout = analyse(staged)
        extract(staged, payload, layout)
        program = (payload / layout.executable).resolve(strict=True)
        if not program.is_relative_to(payload.resolve()) or not program.is_file():
            raise InstallerError("The application's program points outside its folder.")
        program.chmod(program.stat().st_mode | 0o500)
    except (InstallerError, OSError) as error:
        shutil.rmtree(holder, ignore_errors=True)
        raise Fallback("build", f"The archive could not be unpacked: {error}") from error
    application_id = safe_id(f"portable-{report.title}-{report.sha256[:12]}")
    return application_id, payload, f"/opt/{application_id}/{layout.executable}", report.title


def note_fallback(sha256: str, fallback: Fallback) -> None:
    """Write into the record the earlier path just made why the store was not used."""
    for record in reversed(iter_records()):
        if record.get("sha256") == sha256 and not record.get("store") and record.get("format") in KINDS:
            write_record(str(record["application_id"]), {**record, "store_fallback": fallback.note()})
            return
