"""Read package facts without executing package content.

Values describe their evidence source. Missing inspection support is distinct
from a package explicitly declaring no permission, signature, or updater.
"""
from dataclasses import replace
import configparser
from pathlib import Path
import re
import shutil
import subprocess
import selectors
import os
import time

from .errors import InstallerError


def rpm_desktop(path, filename):
    """Read one desktop file to stdout; never extract archive paths to disk."""
    if not re.fullmatch(r"/(?:usr/share|usr/local/share)/applications/[A-Za-z0-9._+-]+\.desktop", filename):
        raise InstallerError("Invalid packaged desktop-entry path.")
    producer = subprocess.Popen(["rpm2cpio", str(path)], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    consumer = None
    try:
        consumer = subprocess.Popen(["cpio", "-i", "--to-stdout", "--quiet", "."+filename, filename.lstrip("/")],
                                    stdin=producer.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        producer.stdout.close()
        output = bytearray()
        deadline = time.monotonic() + 20
        with selectors.DefaultSelector() as selector:
            selector.register(consumer.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise InstallerError("The package metadata read timed out.")
                if not selector.select(remaining): continue
                block = os.read(consumer.stdout.fileno(), 65536)
                if not block: break
                output.extend(block)
                if len(output) > 1024 * 1024:
                    raise InstallerError("The packaged application metadata is too large.")
        consumer.wait(timeout=5)
        if len(output) > 1024 * 1024 or consumer.returncode:
            raise InstallerError("The packaged application metadata could not be read.")
        producer.wait(timeout=5)
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.read_string(output.decode("utf-8"))
        return dict(parser["Desktop Entry"]) if parser.has_section("Desktop Entry") else {}
    except (OSError, subprocess.TimeoutExpired, ValueError, UnicodeError, configparser.Error) as error:
        raise InstallerError(f"The packaged application metadata could not be read: {error}") from error
    finally:
        for process in (consumer, producer):
            if process and process.poll() is None:
                process.kill(); process.wait()


def rpm_file_sizes(path):
    from .inspectors import _run
    result = _run(["rpm", "-qp", "--qf", "[%{FILENAMES}\\t%{FILESIZES}\\n]", str(path)])
    if result.returncode:
        raise InstallerError("The package file metadata could not be read.")
    result_map = {}
    for line in result.stdout.splitlines():
        name, separator, size = line.rpartition("\t")
        if separator and size.isdecimal():
            result_map[name] = int(size)
    return result_map


def rpm_identity(report, desktop):
    import base64
    import hashlib
    import json
    import sys
    import tempfile
    from .safety import _validate_fingerprint, fingerprint
    _validate_fingerprint(report.sha256)
    # Reopening the exact installed package need not decompress its payload
    # again. This is presentation data only; the normal package digest and
    # signature inspection have already run before enrichment.
    from .desktop import iter_records
    for record in iter_records():
        if (record.get('format') == 'rpm' and record.get('sha256') == report.sha256
                and record.get('launcher_metadata_version', 0) >= 3
                and record.get('name') and Path(str(record.get('icon', ''))).is_file()):
            return {'name': str(record['name'])}, str(record['icon'])
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "luma/installer/artwork"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    cache = root / (report.sha256 + "-v2.json")
    try:
        result = json.loads(cache.read_text())
    except (OSError, ValueError):
        try:
            worker = subprocess.run([sys.executable, "-m", "luma_installer.rpm_metadata", str(report.path), json.dumps(desktop)],
                                    capture_output=True, text=True, timeout=25)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise InstallerError("The packaged application metadata read failed.") from error
        if worker.returncode:
            raise InstallerError(worker.stderr.strip() or "The packaged application metadata could not be read.")
        result = json.loads(worker.stdout)
        if fingerprint(report.path)[1] != report.sha256:
            raise InstallerError("The package changed while its metadata was being read.")
        with tempfile.NamedTemporaryFile(mode="w", dir=root, delete=False) as stream:
            json.dump(result, stream)
            temporary = Path(stream.name)
        temporary.replace(cache)
    entry = result["entry"]
    icon = report.icon
    if result.get("artwork") and result.get("suffix") in {".png", ".svg", ".xpm"}:
        raw = base64.b64decode(result["artwork"], validate=True)
        if len(raw) > 16 * 1024 * 1024:
            raise InstallerError("The packaged artwork exceeds its size limit.")
        destination = root / (hashlib.sha256(raw).hexdigest() + result["suffix"])
        if not destination.exists():
            with tempfile.NamedTemporaryFile(dir=root, delete=False) as stream:
                stream.write(raw)
                temporary = Path(stream.name)
            temporary.replace(destination)
        icon = str(destination)
    return entry, icon


def flatpak_metadata(data):
    if isinstance(data, bytes): data = data.decode("utf-8")
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(data)
    details = {}
    if parser.has_section("Application"):
        for key, label in (("name", "Application ID"), ("runtime", "Runtime")):
            value = parser.get("Application", key, fallback="")
            if value: details[label] = value
    for section in ("Context", "Session Bus Policy", "System Bus Policy"):
        if parser.has_section(section):
            for key, value in parser.items(section):
                details[f"{section} · {key}"] = value or "Empty declaration"
    return details


def flatpak_bundle(path, size, digest):
    import gi
    gi.require_version("Flatpak", "1.0")
    from gi.repository import Flatpak, Gio, GLib
    from .model import PackageReport
    try:
        bundle = Flatpak.BundleRef.new(Gio.File.new_for_path(str(path)))
        raw = bundle.get_metadata()
        details = flatpak_metadata(raw.get_data() if raw else "")
        details["Reference"] = bundle.format_ref()
        origin = bundle.get_origin()
        if origin: details["Update origin"] = origin
        details["Metadata source"] = "Flatpak bundle metadata"
        return PackageReport(path, "flatpak", bundle.get_name(), "Flatpak application",
            size, digest, bundle.get_arch(), "Signature verification occurs in the Flatpak transaction",
            "Per-user Flatpak installation", details=details)
    except (GLib.Error, ValueError, configparser.Error) as error:
        raise InstallerError(f"The Flatpak bundle could not be read: {error}") from error


def enrich(report):
    from .inspectors import _run
    details = dict(report.details)
    version, publisher, title, icon = report.version, report.publisher, report.title, report.icon
    if report.kind == "rpm":
        result = _run(["rpm", "-qp", "--qf", "%{VERSION}\\n%{VENDOR}\\n", str(report.path)])
        if result.returncode == 0:
            fields = result.stdout.splitlines()
            version = fields[0] if fields else ""
            publisher = fields[1] if len(fields) > 1 and fields[1] != "(none)" else ""
        for args, label in ((["--requires"], "Dependencies"), (["--scripts"], "Maintainer scripts")):
            value = _run(["rpm", "-qp", *args, str(report.path)])
            if value.returncode == 0:
                details[label] = value.stdout.strip() or "None declared"
        files = rpm_file_sizes(report.path)
        if files:
            paths = list(files)
            details["Packaged files"] = str(len(paths))
            details["System services in package"] = "\n".join(p for p in paths if p.endswith(".service")) or "None"
            launchers = [p for p in paths if p.startswith("/usr/share/applications/") and p.endswith(".desktop")]
            if launchers:
                entry, icon = rpm_identity(report, launchers)
                title = entry.get("name", title)
                details["Embedded launchers"] = "\n".join(launchers)
        details["Metadata source"] = "RPM headers, file list and signature check"
    elif report.kind == "deb":
        value = _run(["dpkg-deb", "--field", str(report.path), "Version"])
        if value.returncode == 0: version = value.stdout.strip()
        publisher = details.get("Maintainer", "")
        details["Metadata source"] = "Debian control file"
    elif report.kind == "snap":
        value = _run(["unsquashfs", "-cat", str(report.path), "meta/snap.yaml"])
        if value.returncode == 0:
            import yaml
            parsed = yaml.safe_load(value.stdout)
            if not isinstance(parsed, dict): raise InstallerError("The Snap manifest is malformed.")
            version = str(parsed.get("version", ""))
            if parsed.get("title"): title = str(parsed["title"])
            for key, label in (("base", "Base runtime"), ("confinement", "Confinement"), ("plugs", "Requested interfaces")):
                if key in parsed: details[label] = str(parsed[key])
            apps = parsed.get("apps", {})
            if isinstance(apps, dict):
                daemons = [f"{name}: {item['daemon']}" for name, item in apps.items()
                           if isinstance(item, dict) and item.get("daemon")]
                details["Declared daemons"] = "\n".join(daemons) or "None"
            details["Metadata source"] = "meta/snap.yaml inside the package"
    elif report.kind in {"flatpak", "flatpakref"}:
        runtime = details.get("Runtime")
        if runtime:
            value = _run(["flatpak", "list", "--app", "--columns=application,runtime"])
            if value.returncode == 0:
                users = [line.split("\t", 1)[0] for line in value.stdout.splitlines()
                         if "\t" in line and line.split("\t", 1)[1].strip() == runtime]
                details["Installed apps sharing runtime"] = ", ".join(users) or "None"
        details.setdefault("Metadata source", "Flatpak reference file; remote permissions have not been fetched")
    elif report.kind == "appimage":
        from . import appimage_payload
        try:
            kind, offset = appimage_payload.locate(report.path)
        except (InstallerError, OSError):
            kind, offset = "", 0
        entries = []
        if kind == appimage_payload.SQUASHFS:
            listing = _run(["unsquashfs", "-ll", "-o", str(offset), str(report.path)])
            entries = re.findall(r"squashfs-root/([^\n]+\.desktop)$", listing.stdout, re.M)
        # Only an embedded top-level launcher, bounded static extraction.
        entry = next((p for p in entries if "/" not in p), None)
        if entry:
            value = _run(["unsquashfs", "-o", str(offset), "-cat", str(report.path), entry])
            if value.returncode == 0:
                parser = configparser.ConfigParser(interpolation=None)
                parser.read_string(value.stdout)
                if parser.has_section("Desktop Entry"):
                    title = parser.get("Desktop Entry", "Name", fallback=title)
                    version = parser.get("Desktop Entry", "X-AppImage-Version", fallback="")
                    details["Embedded launcher"] = entry
        details["Metadata source"] = "ELF header and embedded desktop entry; package content was not executed"
        details["Publisher signature"] = "Not verified by the installed inspector"
        details["Embedded update information"] = "Not read by the installed inspector"
    return replace(report, title=title, version=version, publisher=publisher, icon=icon, details=details)


def compatibility_report(path, kind, size, digest):
    from .model import PackageReport
    try:
        if kind == "android":
            from luma_android.apk import inspect_android_package
            from luma_android.config import load_runtime_config
            value = inspect_android_package(path, load_runtime_config().apk_max_bytes)
            details = {"Metadata source": "Luma Android archive inspector", "Package kind": value.package_format,
                       "Native processors": ", ".join(value.native_abis) or "No native libraries declared",
                       "Signature": "Android verifies the signature during installation",
                       "Permissions": "Manifest permission details are unavailable from this inspector"}
            title, icon = value.display_name, "application-x-executable"
            if value.package_format == "apk":
                from .apk_metadata import identity_for_package
                try:
                    name, artwork = identity_for_package(path, digest)
                    title, icon = name or title, artwork or icon
                except (OSError, ValueError, subprocess.SubprocessError):
                    # Identification is optional; never prevent a validated install.
                    pass
            return PackageReport(path, kind, title, "Android application", size, digest,
                                 "runtime-selected", destination="Android application runtime", icon=icon, details=details)
        from luma_relay.package import inspect_windows_package, host_supports
        from luma_relay.config import load_config
        from luma_relay import fex
        import platform
        value = inspect_windows_package(path, load_config().max_package_bytes)
        supported, message = host_supports(value, platform.machine().lower(), fex_available=fex.available())
        if not supported: raise InstallerError(message)
        details = {"Metadata source": "Luma Relay PE/MSI inspector", "Processor": value.processor,
                   "Signature": "Certificate table present; signer not verified" if value.authenticode_present
                                else "No certificate table detected by the inspector",
                   "Permissions": "Not declared — Windows programs do not say",
                   "Updates": "Not declared — the program may have its own updater"}
        return PackageReport(path, kind, path.stem, "Windows application", size, digest,
                             "runtime-selected", destination="Private Relay application environment", details=details)
    except ImportError as error:
        raise InstallerError(f"The {kind.title()} runtime is not installed.") from error
