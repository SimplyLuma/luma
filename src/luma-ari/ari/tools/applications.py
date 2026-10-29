# SPDX-License-Identifier: Apache-2.0
"""Application actions and reviewed package transactions over their owning services.

The broker owns access grants and risk prompts. Valet owns package verification,
privilege escalation, essential-app protection and installation records.
"""
from __future__ import annotations

import dataclasses
import secrets
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ari.mcp import serve

_client = None
_reviews: dict[str, tuple[float, object]] = {}
_software_reviews: dict[str, tuple] = {}


def broker():
    global _client
    if _client is None:
        from luma_sdk.semantic import SemanticClient
        _client = SemanticClient()
    return _client


def app_surfaces(arguments):
    client = broker()
    publication = arguments.get("publication_id")
    data = client.get_surface(publication) if publication else client.list_surfaces()
    return {"ok": True, "summary": "Read the available application actions.", "data": data, "untrusted": True}


def request_app_access(arguments):
    scopes = arguments.get("scopes", ["observe-public", "invoke-low-risk"])
    allowed = {"observe-public", "observe-private", "invoke-low-risk", "invoke-consequential",
               "invoke-destructive", "invoke-security-sensitive"}
    if not isinstance(scopes, list) or not scopes or any(scope not in allowed for scope in scopes):
        return {"ok": False, "summary": "Choose the application access scopes that are needed."}
    result = broker().request_access(arguments["application_id"], scopes, duration=3600)
    return {"ok": result.get("granted") is True,
            "summary": "Application access granted." if result.get("granted") else "Application access was not granted.",
            "data": result}


def app_action(arguments):
    # Action parameters stay structured; no shell, synthetic input or guessed UI.
    result = broker().invoke_action(arguments["publication_id"], arguments["object_id"],
                                    arguments["action_id"], arguments.get("parameter", {}))
    payload = result.get("result")
    ok = result.get("completed") is True and not (isinstance(payload, dict) and payload.get("ok") is False)
    summary = payload.get("summary") if isinstance(payload, dict) else None
    return {"ok": ok, "summary": summary or ("Completed the application action." if ok else "The application action failed."),
            "data": result, "untrusted": True}


def installed_apps(_arguments):
    from luma_installer.desktop import iter_records
    rows = [{key: row.get(key) for key in ("application_id", "name", "format", "version", "source")}
            for row in iter_records()]
    import gi
    gi.require_version('Gio','2.0')
    from gi.repository import Gio
    known = {row['application_id'] for row in rows}
    rows.extend({'application_id':app.get_id(),'name':app.get_display_name(),'format':'desktop'}
                for app in Gio.AppInfo.get_all() if app.should_show() and app.get_id() not in known)
    return {"ok": True, "summary": "Read installed application records.", "untrusted": True, "data": rows[:200]}


def search_software(arguments):
    from luma_installer.depot_catalog import current_catalog
    catalogue = current_catalog()
    words = arguments["query"].casefold().split()
    rows = [row for row in catalogue.applications if all(word in
        (row.name + ' ' + row.identifier + ' ' + row.summary).casefold() for word in words)]
    return {"ok": True, "summary": f"Found {len(rows)} matching catalogue entries.", "untrusted": True,
            "data": [{"id":row.id, "name":row.name, "application_id":row.identifier,
                      "summary":row.summary, "publisher":row.publisher, "backend":row.backend,
                      "direct_install":row.backend=='flatpak'} for row in rows[:20]]}


def review_software(arguments):
    from luma_installer.depot_catalog import local_catalog
    from luma_installer import depot_flatpak as flatpak
    from luma_installer.inspectors import host_architecture
    from luma_installer.depot_permissions import from_metadata
    entry = local_catalog().find(arguments["catalogue_id"])
    if entry is None or entry.backend != 'flatpak':
        return {"ok": False, "summary": "This catalogue entry cannot be installed directly by Ari. Open it in Depot to review its owning source."}
    installation, remote = flatpak.installation_for(entry.source_id)
    source = flatpak.resolve(installation, entry.source_id, host_architecture(), remote=remote, branch=entry.branch)
    import gi
    gi.require_version('Flatpak','1.0')
    from gi.repository import Flatpak
    ref = installation.fetch_remote_ref_sync(remote,Flatpak.RefKind.APP,source.app_id,source.architecture,source.branch,None)
    if ref.get_commit() != source.commit:
        return {"ok": False, "summary": "This application changed while it was being reviewed. Review it again."}
    metadata = flatpak.metadata_text(ref.get_metadata())
    permissions = [{**dataclasses.asdict(value),'title':value.title,'detail':value.detail}
                   for value in from_metadata(metadata, source.app_id, strict=True)]
    moment = time.monotonic()
    for key, reviewed in list(_software_reviews.items()):
        if moment-reviewed[0]>1800:
            del _software_reviews[key]
    if len(_software_reviews)>=32:
        del _software_reviews[next(iter(_software_reviews))]
    token = secrets.token_urlsafe(24)
    _software_reviews[token] = (moment,entry,installation,source,permissions)
    return {"ok": True, "summary": f"Reviewed {entry.name} from {remote}.", "untrusted": True,
            "data": {"review_id":token,"name":entry.name,"publisher":entry.publisher,"remote":remote,
                     "commit":source.commit,"permissions":permissions}}


def install_software(arguments):
    reviewed = _software_reviews.get(arguments['review_id'])
    if reviewed is None or time.monotonic()-reviewed[0]>1800:
        return {"ok": False, "summary": "Review this catalogue application again before installing it."}
    _moment,entry,installation,source,permissions = reviewed
    if arguments.get('preview'):
        return {"ok": True,"preview":f'Install {entry.name}',
                "detail":'\n'.join([f'Source: {source.remote}',f'Publisher: {entry.publisher or "Not listed"}',
                                      *[row['title']+': '+row['detail'] for row in permissions]])}
    from luma_installer import depot_flatpak as flatpak
    existing = flatpak.installed_ref(installation,source)
    if existing is not None:
        return {"ok": True,"unchanged":True,"summary":f'{entry.name} is already installed.'}
    transaction = flatpak.transaction(installation,source)
    if transaction is not None:
        transaction.run(None)
    installation.drop_caches(None)
    actual = flatpak.installed_ref(installation,source)
    if actual is None or actual.get_commit()!=source.commit or actual.get_origin()!=source.remote:
        return {"ok": False,"summary":"The application has not completed installation at the reviewed version. Check Depot before retrying."}
    # Keep ownership with Flatpak. resolve_record can subsequently adopt the
    # owning desktop identity for normal Valet removal; no invented receipt.
    del _software_reviews[arguments['review_id']]
    return {"ok": True,"summary":f'Installed {entry.name}.',"data":{"application_id":source.app_id+'.desktop',"commit":source.commit}}


def inspect_package(arguments):
    from luma_installer.inspectors import inspect_package as inspect, architecture_is_compatible
    report = inspect(Path(arguments["path"]))
    if not architecture_is_compatible(report):
        return {"ok": False, "summary": "This package is for a different processor architecture."}
    # A short-lived, process-owned review is required. The model cannot invent
    # a SHA or substitute a different package after approval.
    moment = time.monotonic()
    for key, (created, _) in list(_reviews.items()):
        if moment - created > 1800:
            del _reviews[key]
    if len(_reviews) >= 32:
        del _reviews[next(iter(_reviews))]
    token = secrets.token_urlsafe(24)
    _reviews[token] = (moment, report)
    data = dataclasses.asdict(report)
    data["path"] = str(report.path)
    data["review_id"] = token
    return {"ok": True, "summary": f"Reviewed {report.title} for installation.", "data": data, "untrusted": True}


def install_package(arguments):
    reviewed = _reviews.get(arguments["review_id"])
    if reviewed is None or time.monotonic() - reviewed[0] > 1800:
        return {"ok": False, "summary": "Review this package again before installing it."}
    report = reviewed[1]
    if arguments.get("preview"):
        return {"ok": True, "preview": f"Install {report.title}",
                "detail": "\n".join([str(report.path), report.identity, *report.permissions, *report.warnings,
                                      "System change required." if report.requires_system_change else "",
                                      "Restart required." if report.requires_restart else ""]).strip()}
    from luma_installer.workflow import install
    record = install(report)
    # The workflow verifies both unchanged package bytes and an actual published
    # owning record. A launched installer window is never a successful install.
    del _reviews[arguments["review_id"]]
    name = record.get('name') or report.title
    restart = bool(record.get("restart_required"))
    return {"ok": True, "summary": f"Installation of {name} is staged. Restart to finish." if restart else f"Installed {name}.",
            "data": {key: record.get(key) for key in ("application_id", "name", "format", "version")}}


def remove_application(arguments):
    from luma_installer.workflow import resolve_record, remove
    from luma_installer.removal import refuse_protected
    record = resolve_record(arguments["application_id"])
    if not record:
        return {"ok": False, "summary": "That application is not installed."}
    if record.get('format') == 'system':
        return system_application_change(arguments,'override-remove')
    refuse_protected(record)
    name = record.get("name") or arguments["application_id"]
    if arguments.get("preview"):
        return {"ok": True, "preview": f"Uninstall {name}", "detail": "Keep its settings and personal data."}
    outcome = remove(record, keep_data=True)
    # Owning backends check removal before unregistering. Data deletion is not
    # exposed here; it is a separate choice in the application manager.
    pending = isinstance(outcome, str) and ('staged' in outcome.casefold() or 'restart' in outcome.casefold())
    return {"ok": True, "summary": f"Removal of {name} is staged. Restart to finish. Personal data kept." if pending
            else f"Uninstalled {name}. Settings and personal data kept."}


def system_application_change(arguments, action='override-reset'):
    from luma_installer.depot_catalog import local_catalog
    from luma_installer.removal import is_essential
    identity = arguments['application_id']
    catalogue = local_catalog()
    entry = next((row for row in catalogue.applications if row.luma_system and
                  identity in (row.id,row.identifier,row.luma_system.desktop_id)),None)
    if entry is None or (action=='override-remove' and (not entry.luma_system.removable or is_essential(identity=identity))):
        return {"ok":False,"summary":"This system application is not listed as removable. Luma's essential components are protected."}
    verb = 'Remove' if action=='override-remove' else 'Restore'
    if arguments.get('preview'):
        return {"ok":True,"preview":f'{verb} {entry.name}',
                "detail":"Change the system image through Luma's application manager. Restart to finish. Personal documents and settings stay. You can restore removed apps in Depot."}
    # The root-owned helper re-reads its own authenticated catalogue and actual
    # deployment, checks essential packages/dependents, and verifies staging.
    # No command, privilege flag, URL or package name comes from the model.
    outcome = subprocess.run(['/usr/bin/pkexec','/usr/libexec/luma-installer-system',action,entry.luma_system.package],
                             stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=600)
    if outcome.returncode != 0:
        return {"ok":False,"summary":f'{verb} of {entry.name} did not finish. Check the application manager before retrying.'}
    from luma_installer.depot_system_apps import SystemApps
    state = SystemApps.read([entry.luma_system.package]).state(entry.luma_system.package)
    expected = {'removed','removal-pending'} if action=='override-remove' else {'installed','restore-pending'}
    if state not in expected:
        return {"ok":False,"summary":"The system change has not reached the expected state. Check Depot before retrying."}
    pending = state.endswith('pending')
    summary = (f'{verb} of {entry.name} is staged. Restart to finish.' if pending else
               f'{entry.name} is removed from the system image.' if action=='override-remove' else f'{entry.name} is restored.')
    return {"ok":True,"summary":summary,'data':{'state':state}}


def schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


STRING = {"type": "string"}
PREVIEW = {"type": "boolean"}
TOOLS = {
    "restore_system_application": ("Restore a removable Luma application removed from the system image. Use its installed desktop identity or Depot catalogue ID. The owning system helper protects essential components and verifies staging; a restart may be needed.",
                                   schema({'application_id':STRING,'preview':PREVIEW},['application_id']), system_application_change),
    "search_software": ("Find applications in this computer's authenticated or package-shipped Depot catalogue. Direct installation supports known Flatpak sources; URLs are never install authority.",
                        schema({"query":STRING},['query']), search_software),
    "review_software": ("Resolve a catalogue application's actual signature-checked Flatpak source, version commit and sandbox permissions before installation. Use the catalogue ID returned by search_software.",
                        schema({"catalogue_id":STRING},['catalogue_id']), review_software),
    "install_software": ("Install the exact catalogue application reviewed by review_software. Use its review ID; the owning backend rechecks the commit, remote and dependencies. Returns only after verifying the installed ref.",
                         schema({"review_id":STRING,"preview":PREVIEW},['review_id']), install_software),
    "app_surfaces": ("Read published application screens, objects and action IDs. Only granted data is visible. Open the app and request_app_access first if needed.",
                     schema({"publication_id": STRING}), app_surfaces),
    "request_app_access": ("Ask the system broker to grant access to an open application's published actions. The person approves the actual scopes in the system prompt.",
                           schema({"application_id": STRING, "scopes": {"type": "array", "items": STRING}}, ["application_id"]), request_app_access),
    "app_action": ("Perform a real application action using the publication, object and action IDs read from app_surfaces. The broker enforces permissions and any risk confirmation. Never guess IDs or claim unsupported actions.",
                   schema({"publication_id": STRING, "object_id": STRING, "action_id": STRING,
                           "parameter": {}}, ["publication_id", "object_id", "action_id"]), app_action),
    "installed_apps": ("List application identities and installed versions managed by Luma.", schema({}), installed_apps),
    "inspect_package": ("Inspect a local application package or folder before installation. Returns its publisher, permissions, warnings and a short-lived review ID. Never installs.",
                        schema({"path": STRING}, ["path"]), inspect_package),
    "install_package": ("Install the exact package returned by inspect_package, through Luma's owning installer. Requires its review ID and verifies unchanged bytes. Wait for the result before reporting installed.",
                        schema({"review_id": STRING, "preview": PREVIEW}, ["review_id"]), install_package),
    "remove_application": ("Uninstall an application by its installed application_id, keeping settings and personal data. Essential Luma components cannot be removed.",
                           schema({"application_id": STRING, "preview": PREVIEW}, ["application_id"]), remove_application),
}

if __name__ == "__main__":
    serve("ari-applications", TOOLS)
