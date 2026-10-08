"""One application workflow over the existing owning package/runtime services."""
from dataclasses import replace
from pathlib import Path
import shutil
import os
import subprocess

from . import backends, manager
from .desktop import iter_records, write_record, safe_id
from .errors import InstallerError
from .model import PackageReport
from .progress import current, phase
from .removal import display_name, is_essential, refuse_protected
from .safety import fingerprint, transaction_lock


def install(report, permission_changes=None):
    phase("Check the file", 0, True)
    if fingerprint(report.path)[1] != report.sha256:
        raise InstallerError("The package changed after review. Reopen it to review the new file.")
    if report.kind not in {"android", "windows"}:
        name = backends.install(report)
        matches = [r for r in iter_records() if r.get("sha256") == report.sha256]
        if not matches:
            raise InstallerError("The package finished without publishing an application record.")
        record = matches[-1]
    else:
        with transaction_lock(report.sha256):
            if report.kind == "android":
                from luma_android.apk import inspect_android_package, stage_android_package, staged_package_root
                from luma_android.config import load_runtime_config
                from luma_android.engine import WaydroidEngine
                from luma_android.receipts import write_install_receipt
                config = load_runtime_config()
                inspection = inspect_android_package(report.path, config.apk_max_bytes)
                if inspection.sha256 != report.sha256: raise InstallerError("The package changed after review.")
                payloads = stage_android_package(report.path, inspection)
                try:
                    phase("Prepare Android", 1/3)
                    engine = WaydroidEngine(config.engine)
                    engine.ensure_ready(config.multi_window, timeout=180, detached=True)
                    before = {a.package: a for a in engine.applications()}
                    phase("Install into Android", 1/3)
                    engine.install_package(payloads, inspection.apk_entries)
                    write_install_receipt(inspection, "installed")
                    phase("Register", 2/3)
                    after = {a.package: a for a in engine.applications()}
                    package = report.details.get("Application ID")
                    if package not in after:
                        new = set(after) - set(before)
                        if not new:
                            # A repeated install of the same reviewed file is
                            # still identifiable from its previous owner receipt.
                            new = {str(r["package"]) for r in iter_records()
                                   if r.get("format") == "android" and r.get("sha256") == report.sha256
                                   and r.get("package") in after}
                        if len(new) != 1:
                            raise InstallerError("Android installed the package, but its application identity could not be resolved. Check Applications before retrying.")
                        package = new.pop()
                    name = after[package].name
                    record = {"application_id": safe_id("android-" + package), "format": "android",
                              "package": package, "name": name, "sha256": report.sha256}
                finally:
                    shutil.rmtree(staged_package_root(payloads), ignore_errors=True)
            else:
                from luma_relay.package import inspect_windows_package
                from luma_relay.engine import WineEngine
                from luma_relay.config import load_config
                config = load_config()
                package = inspect_windows_package(report.path, config.max_package_bytes)
                if package.sha256 != report.sha256: raise InstallerError("The package changed after review.")
                phase("Install into Windows", 1/3)
                manifest = WineEngine(config).install(package, launch_portable=False)
                phase("Register", 2/3)
                name = str(manifest["name"])
                record = {"application_id": safe_id("windows-" + manifest["app_id"]), "format": "windows",
                          "relay_id": manifest["app_id"], "name": name, "sha256": report.sha256}
    if permission_changes:
        from .valet_permissions import apply_flatpak_choices
        apply_flatpak_choices(record, permission_changes)
    record = {**record, "version": report.version, "publisher": report.publisher,
              "source": str(report.path), "source_bytes": report.byte_size,
              "icon": record.get("icon", report.icon), "reviewed_details": report.details,
              "destination": report.destination}
    write_record(str(record["application_id"]), record)
    return record


def resolve_record(application_id):
    """Resolve a Filer desktop identity through its actual owning registry."""
    if application_id.startswith("org.projectluma.Installed.") and application_id.endswith(".desktop"):
        application_id = application_id[len("org.projectluma.Installed."):-len(".desktop")]
    try:
        return manager._record(application_id)
    except InstallerError:
        if not application_id.endswith(".desktop") or "/" in application_id:
            raise
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    desktop = Gio.DesktopAppInfo.new(application_id)
    if desktop is None:
        raise InstallerError("This application is no longer registered in Applications.")
    name = desktop.get_display_name() or desktop.get_name() or display_name(application_id)
    if is_essential(identity=application_id):
        # The session needs it, whoever installed it: describe it, never adopt it.
        return _system_record(application_id, desktop, name)
    flatpak_id = desktop.get_string("X-Flatpak")
    if flatpak_id:
        # Query the owning installation; never infer ownership from a filename.
        for location in ("user", "system"):
            result = subprocess.run(["flatpak", "info", "--"+location, "--show-ref", flatpak_id],
                                    capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                record = {"application_id": safe_id("flatpak-"+flatpak_id), "format":"flatpak",
                          "flatpak_id":flatpak_id, "installation":location, "name":name}
                break
        else: raise InstallerError("Flatpak could not locate this installed application.")
    elif desktop.get_string("X-SnapInstanceName"):
        instance = desktop.get_string("X-SnapInstanceName")
        matches = [r for r in iter_records() if r.get("format") == "snap"
                   and r.get("package_name") == instance and r.get("system_receipt")]
        if len(matches) != 1:
            raise InstallerError("This Snap has no Luma installation receipt. Its owner must register it before Valet can safely remove it.")
        return matches[0]
    elif desktop.get_string("X-Luma-Relay-AppID"):
        from luma_relay.registry import read_manifest
        relay_id = desktop.get_string("X-Luma-Relay-AppID")
        manifest = read_manifest(relay_id)
        record = {"application_id":safe_id("windows-"+relay_id), "format":"windows",
                  "relay_id":relay_id, "name":manifest["name"]}
    elif application_id.startswith("waydroid."):
        package = application_id[len("waydroid."):-len(".desktop")]
        from luma_android.engine import WaydroidEngine
        from luma_android.config import load_runtime_config
        apps = WaydroidEngine(load_runtime_config().engine).applications_from_launchers()
        if package not in {a.package for a in apps}:
            raise InstallerError("Android could not locate this installed application.")
        record = {"application_id":safe_id("android-"+package), "format":"android",
                  "package":package, "name":name}
    else:
        from . import layered_apps
        try:
            # A user launcher may shadow a system desktop ID. Never adopt it
            # merely because an identically named system file exists.
            filename = getattr(desktop, 'get_filename', lambda: None)()
            if not filename or Path(filename) != layered_apps.APPLICATIONS / application_id:
                raise ValueError('Not the system-owned launcher')
            package = layered_apps.identity(application_id)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            return _system_record(application_id, desktop, name)
        record = {'application_id': safe_id('layered-rpm-' + package), 'format': 'rpm',
                  'package_name': package, 'name': name, 'external_layered': True,
                  'sha256': layered_apps.receipt_id(application_id, package)}
    record["desktop_id"] = application_id
    icon = desktop.get_icon()
    if icon: record["icon"] = icon.to_string()
    # Adoption records identify an already installed app; no package is run.
    write_record(record["application_id"], record)
    return record


def _system_record(application_id, desktop, name):
    """A display-only record for an app Luma itself provides. It is never written."""
    record = {"application_id": application_id[:-len(".desktop")], "format": "system",
              "desktop_id": application_id, "name": name}
    icon = desktop.get_icon()
    if icon: record["icon"] = icon.to_string()
    return record


def installed_icon(record, applications=None):
    """Use the owner's current launcher artwork, including adopted old apps.

    These desktop entries supply display metadata only; they cannot change
    the record's package identity or authorize removal through another owner.
    """
    if applications is None:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        applications = Gio.AppInfo.get_all()
    kind = record.get("format")
    desktop_id = record.get("desktop_id")
    if kind == "android": desktop_id = "waydroid." + str(record["package"]) + ".desktop"
    elif kind in {"appimage", "portable", "rpm", "deb"} and not record.get("system_receipt") and not record.get('external_layered'):
        desktop_id = "org.projectluma.Installed." + str(record["application_id"]) + ".desktop"
    for app in applications:
        matches = bool(desktop_id and app.get_id() == desktop_id)
        if hasattr(app, "get_string"):
            for owner, key, field in (("flatpak", "X-Flatpak", "flatpak_id"),
                                      ("windows", "X-Luma-Relay-AppID", "relay_id"),
                                      ("snap", "X-SnapInstanceName", "package_name")):
                if kind == owner and record.get(field):
                    matches = app.get_string(key) == record[field]
        if matches and app.get_icon():
            return app.get_icon().to_string()
    return str(record.get("icon") or "application-x-executable")


def installed_name(record, applications=None):
    """The launcher's own name for an installed app, then the record's, never a placeholder."""
    if applications is None:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        applications = Gio.AppInfo.get_all()
    desktop_id = record.get("desktop_id")
    if record.get("format") == "android" and record.get("package"):
        desktop_id = "waydroid." + str(record["package"]) + ".desktop"
    elif not desktop_id and record.get("application_id"):
        desktop_id = "org.projectluma.Installed." + str(record["application_id"]) + ".desktop"
    for app in applications:
        if desktop_id and app.get_id() == desktop_id:
            name = (app.get_display_name() if hasattr(app, "get_display_name") else "") or app.get_name()
            if name: return str(name)
    return display_name(desktop_id, record)


def installed_report(application_id):
    record = resolve_record(application_id)
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    applications = Gio.AppInfo.get_all()
    record = {**record, "icon": installed_icon(record, applications),
              "name": installed_name(record, applications)}
    removable = not (record.get("format") == "system" or is_essential(record, application_id))
    record["removable"] = removable
    details = {"Metadata source": "Luma's installed application record",
               "Application ID": str(record["application_id"]),
               "Owner": "Luma" if record.get("format") == "system" else str(record.get("format", "Unknown"))}
    kind = str(record.get("format", ""))
    if not removable:
        details["Removal"] = "Part of Luma; it can’t be uninstalled"
    elif kind == "flatpak":
        result = manager._run(["flatpak", "info", "--"+str(record.get("installation", "user")), "--show-metadata", str(record["flatpak_id"])])
        from .facts import flatpak_metadata
        details.update(flatpak_metadata(result.stdout))
        runtime = details.get("Runtime")
        if runtime:
            refs = manager._run(["flatpak", "list", "--app", "--columns=application,runtime"])
            others = [line.split("\t")[0] for line in refs.stdout.splitlines()
                      if "\t" in line and line.split("\t", 1)[1].strip() == runtime
                      and line.split("\t")[0] != record["flatpak_id"]]
            details["Other apps using the retained runtime"] = ", ".join(others) or "None"
        details["Application data"] = str(Path.home() / ".var/app" / str(record["flatpak_id"]))
    elif kind == "windows":
        details["Removal"] = "Relay removes the private Windows environment; shared runtime remains"
        details["Keep data"] = "Relay retains the private environment outside Applications to preserve Windows settings"
    elif kind == "android":
        details["Removal"] = "Android removes the package and revokes its permissions"
        details["Keep data"] = "Android retains the app’s private data for a matching signed reinstall when Keep settings and data is enabled"
    elif kind == "snap":
        details["Data retention"] = "snapd manages automatic snapshots according to the system retention policy"
    elif record.get('external_layered'):
        details['Owner'] = 'System package manager'
        details['Package'] = str(record['package_name'])
        details['Removal'] = 'Removes the added RPM; a restart may be required'
        details['Data retention'] = 'Personal settings and files are retained'
    elif kind != "system":
        details["Application data"] = str(Path.home() / ".local/share/luma/installer/data" / str(record.get("sha256", "")))
    return PackageReport(Path(str(record.get("source", ""))), kind, str(record.get("name", application_id)),
                         "Installed application", int(record.get("source_bytes", 0)), str(record.get("sha256", "")),
                         details=details, requires_restart=bool(record.get("restart_required")),
                         version=str(record.get("version", "")), publisher=str(record.get("publisher", "")),
                         icon=str(record.get("icon", "application-x-executable"))), record


def remove(record, keep_data=True):
    refuse_protected(record)
    application_id = str(record["application_id"])
    kind = record.get("format")
    phase("Remove application", 0, cancellable=kind in {"flatpak", "flatpakrepo"})
    if kind == "android":
        from luma_android.engine import WaydroidEngine
        from luma_android.config import load_runtime_config
        engine = WaydroidEngine(load_runtime_config().engine)
        package = str(record["package"])
        engine.remove(package, keep_data=keep_data)
        if package in {app.package for app in engine.applications()}:
            raise InstallerError("Android still reports this application as installed. Its record has been retained so removal can be retried.")
    elif kind == "windows":
        from luma_relay.config import load_config
        from luma_relay.engine import WineEngine
        WineEngine(load_config()).remove(str(record["relay_id"]), keep_data=keep_data)
    else:
        return manager.remove(application_id, delete_data=not keep_data)
    phase("Unregister", 2/3)
    from .desktop import remove_record
    remove_record(application_id)
    return "Removed."


def open_application(record, *, environment=None, errors=None):
    """Start the installed application and hand back the process, if there is one.

    The caller watches it so a slow start can be shown as starting rather than
    as nothing happening at all. ``environment`` adds variables such as the
    activation token that lets the new window come forward, and ``errors``
    receives what the application writes to stderr, so a failed start can say
    why.
    """
    kind = record.get("format")
    if kind == "flatpak":
        args = ["flatpak", "run", str(record["flatpak_id"])]
    elif kind == "snap":
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        for app in Gio.AppInfo.get_all():
            if isinstance(app, Gio.DesktopAppInfo) and app.get_string("X-SnapInstanceName") == record.get("package_name"):
                app.launch([], None)
                return None
        raise InstallerError("Snap has not published a launchable application yet. Check Applications after registration finishes.")
    elif kind == "android":
        args = ["luma-android", "launch", str(record["package"])]
    elif kind == "windows":
        args = ["luma-relay", "launch", str(record["relay_id"])]
    elif record.get('external_layered'):
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import Gio
        desktop = Gio.DesktopAppInfo.new(str(record['desktop_id']))
        if desktop is None:
            raise InstallerError('This application is no longer registered in Applications.')
        desktop.launch([], None)
        return None
    elif kind in {"appimage", "portable", "deb", "rpm"} and not record.get("system_receipt"):
        args = ["luma-capsule-launch", str(record["application_id"])]
    else:
        args = ["gio", "open", "applications:///"]
    extra = {}
    if environment:
        extra["env"] = {**os.environ, **environment}
    if errors is not None:
        extra["stderr"] = errors
    return subprocess.Popen(args, start_new_session=True, close_fds=True, **extra)
