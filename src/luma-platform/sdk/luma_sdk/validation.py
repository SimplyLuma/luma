# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import configparser
import json
import re
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree

from .background import background_errors, background_project_errors


APP_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+){2,}$")
SOURCE_REVISION = re.compile(r"^[0-9a-f]{40,64}$")
REQUIRED_WIDTHS = (360, 500, 1024)
REQUIRED_INPUTS = ("pointer", "touch", "keyboard")
REQUIRED_PRESENTATIONS = ("windowed", "fullscreen-mobile")
REQUIRED_ARCHITECTURES = ("x86_64", "aarch64")
REQUIRED_EVIDENCE_LANES = (
    "accessibility",
    "adaptive_layout",
    "keyboard",
    "portals",
    "reduced_motion",
    "sandbox",
    "semantics",
    "uninstall",
    "update_and_rollback",
)
KNOWN_PORTALS = {
    "account",
    "background",
    "camera",
    "file-chooser",
    "global-shortcuts",
    "inhibit",
    "location",
    "microphone",
    "network-monitor",
    "notifications",
    "open-uri",
    "printing",
    "realtime",
    "remote-desktop",
    "screencast",
    "secret",
    "settings",
    "sharing",
    "trash",
    "wallpaper",
}
FORBIDDEN_FINISH_ARGS = {
    "--device=all",
    "--filesystem=host",
    "--filesystem=host-os",
    "--filesystem=host-etc",
    "--filesystem=home",
    "--socket=session-bus",
    "--socket=system-bus",
    "--socket=x11",
}


def application_id_errors(app_id: str, schema_version: str = "0.2") -> list[str]:
    errors: list[str] = []
    if not APP_ID.fullmatch(app_id):
        return ["application.id must be a reverse-DNS identifier"]
    if schema_version == "0.2":
        components = app_id.split(".")
        if any(component != component.lower() for component in components[:2]):
            errors.append("application.id domain components must be lowercase")
        if components[-1].lower() == "desktop":
            errors.append("application.id may not end in '.desktop'")
    return errors


def _project_path(root: Path, value: Any, field: str, errors: list[str]) -> Path | None:
    text = str(value or "").strip()
    if not text:
        errors.append(f"{field} is required")
        return None
    candidate = Path(text)
    if candidate.is_absolute() or ".." in candidate.parts:
        errors.append(f"{field} must be a project-relative path without '..'")
        return None
    return root / candidate


def _read_json(path: Path, field: str, errors: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        errors.append(f"{field} does not exist: {path}")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        errors.append(f"{field} is not valid JSON: {error}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{field} must contain a JSON object")
        return None
    return value


def _read_flatpak_manifest(path: Path, errors: list[str]) -> dict[str, Any] | None:
    if path.suffix == ".json":
        return _read_json(path, "distribution.flatpak_manifest", errors)
    if path.suffix not in {".yaml", ".yml"}:
        errors.append("distribution.flatpak_manifest must use .json, .yaml, or .yml")
        return None
    if not path.is_file():
        errors.append(f"distribution.flatpak_manifest does not exist: {path}")
        return None
    try:
        import yaml
    except ImportError:
        errors.append("YAML Flatpak manifests require the SDK's PyYAML dependency")
        return None
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        errors.append(f"distribution.flatpak_manifest is not valid YAML: {error}")
        return None
    if not isinstance(value, dict):
        errors.append("distribution.flatpak_manifest must contain an object")
        return None
    return value


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_text(element: ElementTree.Element, name: str) -> str:
    for child in element.iter():
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _iter_sources(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        sources = value.get("sources")
        if isinstance(sources, list):
            for source in sources:
                if isinstance(source, dict):
                    yield source
        for child in value.values():
            yield from _iter_sources(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_sources(child)


def _validate_base(manifest: dict[str, Any], root: Path | None, errors: list[str]) -> None:
    version = manifest.get("schema_version")
    if version not in {"0.1", "0.2"}:
        errors.append("schema_version must be '0.1' or '0.2'")
    app = manifest.get("application", {})
    if not isinstance(app, dict):
        errors.append("application must be a table")
        app = {}
    app_id = str(app.get("id", ""))
    errors.extend(application_id_errors(app_id, str(version)))
    for field in ("name", "version", "entrypoint"):
        if not str(app.get(field, "")).strip():
            errors.append(f"application.{field} is required")
    platform = manifest.get("platform", {})
    if not isinstance(platform, dict):
        errors.append("platform must be a table")
        platform = {}
    if str(platform.get("ui_contract", "")) != "1":
        errors.append("platform.ui_contract must be '1'")
    if str(platform.get("semantics_contract", "")) != "1":
        errors.append("platform.semantics_contract must be '1'")
    responsive = manifest.get("responsive", {})
    if not isinstance(responsive, dict):
        errors.append("responsive must be a table")
        responsive = {}
    if responsive.get("adaptive") is not True:
        errors.append("responsive.adaptive must be true")
    widths = responsive.get("test_widths", [])
    if not isinstance(widths, list):
        errors.append("responsive.test_widths must be an array")
        widths = []
    for width in REQUIRED_WIDTHS:
        if width not in widths:
            errors.append(f"responsive.test_widths must include {width}")
    inputs = responsive.get("input_modes", [])
    if not isinstance(inputs, list):
        errors.append("responsive.input_modes must be an array")
        inputs = []
    for mode in REQUIRED_INPUTS:
        if mode not in inputs:
            errors.append(f"responsive.input_modes must include {mode}")
    presentations = responsive.get("presentation_modes", [])
    if not isinstance(presentations, list):
        errors.append("responsive.presentation_modes must be an array")
        presentations = []
    for mode in REQUIRED_PRESENTATIONS:
        if mode not in presentations:
            errors.append(f"responsive.presentation_modes must include {mode}")
    conformance = manifest.get("conformance", {})
    if not isinstance(conformance, dict):
        errors.append("conformance must be a table")
        conformance = {}
    for field in ("accessibility", "keyboard", "semantics", "reduced_motion"):
        if conformance.get(field) is not True:
            errors.append(f"conformance.{field} must be true for a Native candidate")
    if root and app.get("entrypoint"):
        entrypoint = _project_path(root, app["entrypoint"], "application.entrypoint", errors)
        if entrypoint and not entrypoint.is_file():
            errors.append(f"application.entrypoint does not exist: {app['entrypoint']}")


def _validate_flatpak(
    manifest: dict[str, Any], root: Path, app_id: str, errors: list[str]
) -> dict[str, Any] | None:
    distribution = manifest.get("distribution", {})
    flatpak_path = _project_path(
        root, distribution.get("flatpak_manifest"), "distribution.flatpak_manifest", errors
    )
    if flatpak_path is None:
        return None
    flatpak = _read_flatpak_manifest(flatpak_path, errors)
    if flatpak is None:
        return None
    if flatpak.get("id") and flatpak.get("app-id") and flatpak["id"] != flatpak["app-id"]:
        errors.append("Flatpak id and legacy app-id may not disagree")
    flatpak_id = flatpak.get("id", flatpak.get("app-id"))
    if flatpak_id != app_id:
        errors.append("Flatpak id must match application.id")
    for field in ("runtime", "runtime-version", "sdk", "command"):
        if not str(flatpak.get(field, "")).strip():
            errors.append(f"Flatpak {field} is required")
    if not isinstance(flatpak.get("modules"), list) or not flatpak["modules"]:
        errors.append("Flatpak modules must be a non-empty array")
    finish_args = flatpak.get("finish-args", [])
    if not isinstance(finish_args, list) or not all(isinstance(item, str) for item in finish_args):
        errors.append("Flatpak finish-args must be an array of strings")
        finish_args = []
    for argument in finish_args:
        forbidden = argument in FORBIDDEN_FINISH_ARGS or any(
            argument.startswith(f"{prefix}:") for prefix in FORBIDDEN_FINISH_ARGS
        )
        if argument.startswith("--filesystem="):
            forbidden = True
        if forbidden:
            errors.append(f"Flatpak permission is too broad for a Luma Native candidate: {argument}")
        if argument.startswith(("--talk-name=", "--system-talk-name=")) and "*" in argument:
            errors.append(f"Flatpak D-Bus permission may not contain a wildcard: {argument}")
    return flatpak


def _validate_appstream(
    manifest: dict[str, Any], root: Path, app_id: str, errors: list[str]
) -> None:
    distribution = manifest.get("distribution", {})
    if not isinstance(distribution, dict):
        return
    path = _project_path(
        root, distribution.get("appstream_metadata"), "distribution.appstream_metadata", errors
    )
    if path is None:
        return
    if not path.is_file():
        errors.append(f"distribution.appstream_metadata does not exist: {distribution.get('appstream_metadata')}")
        return
    try:
        component = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError) as error:
        errors.append(f"distribution.appstream_metadata is invalid XML: {error}")
        return
    if _first_text(component, "id") != app_id:
        errors.append("AppStream component id must match application.id")
    launchable = ""
    contract = ""
    for element in component.iter():
        name = _local_name(element.tag)
        if name == "launchable" and element.attrib.get("type") == "desktop-id":
            launchable = (element.text or "").strip()
        if name == "value" and element.attrib.get("key") == "org.projectluma.ApplicationContract":
            contract = (element.text or "").strip()
    if launchable != f"{app_id}.desktop":
        errors.append("AppStream desktop launchable must match application.id")
    if contract != "0.2":
        errors.append("AppStream metadata must declare org.projectluma.ApplicationContract=0.2")


def _validate_desktop_file(
    manifest: dict[str, Any], root: Path, app_id: str, flatpak: dict[str, Any] | None,
    errors: list[str],
) -> None:
    distribution = manifest.get("distribution", {})
    path = _project_path(root, distribution.get("desktop_file"), "distribution.desktop_file", errors)
    if path is None:
        return
    if path.name != f"{app_id}.desktop":
        errors.append("desktop file name must be application.id plus '.desktop'")
    if not path.is_file():
        errors.append(f"distribution.desktop_file does not exist: {distribution.get('desktop_file')}")
        return
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, UnicodeError, configparser.Error) as error:
        errors.append(f"distribution.desktop_file is invalid: {error}")
        return
    if "Desktop Entry" not in parser:
        errors.append("desktop file must contain [Desktop Entry]")
        return
    entry = parser["Desktop Entry"]
    if entry.get("Type") != "Application":
        errors.append("desktop file Type must be Application")
    if not entry.get("Name", "").strip():
        errors.append("desktop file Name is required")
    if entry.get("Icon") != app_id:
        errors.append("desktop file Icon must match application.id")
    try:
        executable = shlex.split(entry.get("Exec", ""))[0]
    except (ValueError, IndexError):
        executable = ""
    if not executable:
        errors.append("desktop file Exec is required")
    elif flatpak and executable != flatpak.get("command"):
        errors.append("desktop file Exec command must match the Flatpak command")


def _validate_distribution(manifest: dict[str, Any], root: Path, errors: list[str]) -> None:
    application = manifest.get("application", {})
    app_id = str(application.get("id", "")) if isinstance(application, dict) else ""
    distribution = manifest.get("distribution", {})
    if not isinstance(distribution, dict):
        errors.append("distribution must be a table")
        return
    if distribution.get("substrate") != "flatpak":
        errors.append("distribution.substrate must be 'flatpak'")
    if distribution.get("portal_first") is not True:
        errors.append("distribution.portal_first must be true")
    portals = distribution.get("portals", [])
    if not isinstance(portals, list) or not all(isinstance(item, str) for item in portals):
        errors.append("distribution.portals must be an array of strings")
    else:
        unknown = sorted(set(portals) - KNOWN_PORTALS)
        for portal in unknown:
            errors.append(f"distribution.portals contains an unknown portal: {portal}")
        if len(portals) != len(set(portals)):
            errors.append("distribution.portals must not contain duplicates")
    flatpak = _validate_flatpak(manifest, root, app_id, errors)
    _validate_appstream(manifest, root, app_id, errors)
    _validate_desktop_file(manifest, root, app_id, flatpak, errors)
    if "background" in manifest:
        desktop_exec = None
        desktop_path = _project_path(root, distribution.get("desktop_file"), "distribution.desktop_file", [])
        if desktop_path is not None and desktop_path.is_file():
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str
            try:
                parser.read(desktop_path, encoding="utf-8")
                desktop_exec = parser.get("Desktop Entry", "Exec", fallback=None)
            except (OSError, UnicodeError, configparser.Error):
                desktop_exec = None
        command = flatpak.get("command") if isinstance(flatpak, dict) else None
        errors.extend(background_project_errors(
            manifest, root, desktop_exec, command if isinstance(command, str) else None))
    icon = _project_path(root, distribution.get("icon"), "distribution.icon", errors)
    if icon and not icon.is_file():
        errors.append(f"distribution.icon does not exist: {distribution.get('icon')}")
    elif icon and icon.stem != app_id:
        errors.append("application icon filename must use application.id")


def _validate_lifecycle(manifest: dict[str, Any], errors: list[str]) -> None:
    lifecycle = manifest.get("lifecycle", {})
    if not isinstance(lifecycle, dict):
        errors.append("lifecycle must be a table")
        return
    if lifecycle.get("app_data") not in {"removable", "retained"}:
        errors.append("lifecycle.app_data must be 'removable' or 'retained'")
    if lifecycle.get("cache") != "removable":
        errors.append("lifecycle.cache must be 'removable'")
    if lifecycle.get("user_documents") != "preserved":
        errors.append("lifecycle.user_documents must be 'preserved'")
    if lifecycle.get("reset_supported") is not True:
        errors.append("lifecycle.reset_supported must be true")
    choices = lifecycle.get("uninstall_choices", [])
    if not isinstance(choices, list):
        errors.append("lifecycle.uninstall_choices must be an array")
        choices = []
    for choice in ("keep-data", "remove-app-data"):
        if choice not in choices:
            errors.append(f"lifecycle.uninstall_choices must include '{choice}'")


def validate_manifest(manifest: dict[str, Any], root: Path | None = None) -> list[str]:
    """Validate the static application contract without awarding a status tier."""
    errors: list[str] = []
    _validate_base(manifest, root, errors)
    errors.extend(background_errors(manifest))
    if manifest.get("schema_version") == "0.2":
        if root is None:
            errors.append("schema 0.2 validation requires the project root")
        else:
            _validate_distribution(manifest, root, errors)
        _validate_lifecycle(manifest, errors)
        conformance = manifest.get("conformance", {})
        evidence = conformance.get("evidence") if isinstance(conformance, dict) else None
        if root is not None:
            path = _project_path(root, evidence, "conformance.evidence", errors)
            if path and not path.is_file():
                errors.append(f"conformance.evidence does not exist: {evidence}")
    return errors


def release_evidence_errors(manifest: dict[str, Any], root: Path) -> list[str]:
    """Return gates that prevent the application from being a Native release."""
    errors = validate_manifest(manifest, root)
    if manifest.get("schema_version") != "0.2":
        errors.append("Native release evidence requires application schema 0.2")
        return errors
    conformance = manifest.get("conformance", {})
    evidence_value = conformance.get("evidence") if isinstance(conformance, dict) else None
    evidence_path = _project_path(root, evidence_value, "conformance.evidence", errors)
    if evidence_path is None:
        return errors
    evidence = _read_json(evidence_path, "conformance.evidence", errors)
    if evidence is None:
        return errors
    application = manifest.get("application", {})
    app_id = application.get("id") if isinstance(application, dict) else None
    if evidence.get("schema_version") != "0.1":
        errors.append("conformance evidence schema_version must be '0.1'")
    if evidence.get("application_id") != app_id:
        errors.append("conformance evidence application_id must match application.id")
    revision = str(evidence.get("source_revision") or "")
    if not SOURCE_REVISION.fullmatch(revision):
        errors.append("conformance evidence source_revision must be a 40-64 character lowercase hex revision")
    timestamp = evidence.get("tested_at")
    try:
        datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        errors.append("conformance evidence tested_at must be an ISO-8601 timestamp")
    architectures = evidence.get("architectures", {})
    if not isinstance(architectures, dict):
        errors.append("conformance evidence architectures must be an object")
        architectures = {}
    for architecture in REQUIRED_ARCHITECTURES:
        if architectures.get(architecture) is not True:
            errors.append(f"conformance evidence architecture has not passed: {architecture}")
    presentations = evidence.get("presentations", {})
    if not isinstance(presentations, dict):
        errors.append("conformance evidence presentations must be an object")
        presentations = {}
    for presentation in ("desktop", "mobile"):
        if presentations.get(presentation) is not True:
            errors.append(f"conformance evidence presentation has not passed: {presentation}")
    lanes = evidence.get("lanes", {})
    if not isinstance(lanes, dict):
        errors.append("conformance evidence lanes must be an object")
        lanes = {}
    for lane in REQUIRED_EVIDENCE_LANES:
        if lanes.get(lane) is not True:
            errors.append(f"conformance evidence lane has not passed: {lane}")
    artifacts = evidence.get("artifacts", {})
    if not isinstance(artifacts, dict):
        errors.append("conformance evidence artifacts must be an object")
        artifacts = {}
    for artifact in ("sbom", "test_report"):
        artifact_path = _project_path(root, artifacts.get(artifact), f"conformance.artifacts.{artifact}", errors)
        if artifact_path and not artifact_path.is_file():
            errors.append(f"conformance artifact does not exist: {artifacts.get(artifact)}")

    distribution = manifest.get("distribution", {})
    if not isinstance(distribution, dict):
        return list(dict.fromkeys(errors))
    flatpak_path = _project_path(
        root, distribution.get("flatpak_manifest"), "distribution.flatpak_manifest", errors
    )
    flatpak = _read_flatpak_manifest(flatpak_path, errors) if flatpak_path else None
    if flatpak:
        for source in _iter_sources(flatpak):
            source_type = source.get("type")
            if source_type == "git" and not SOURCE_REVISION.fullmatch(str(source.get("commit", ""))):
                errors.append(f"Flatpak git source '{source.get('url', '<unknown>')}' must pin an immutable commit")
            if source_type in {"archive", "file"} and source.get("url") and not source.get("sha256"):
                errors.append(f"Flatpak {source_type} source '{source['url']}' must declare sha256")
    return list(dict.fromkeys(errors))


def application_status(manifest: dict[str, Any], root: Path) -> tuple[str, list[str]]:
    """Derive status from evidence. Applications never self-assign a Luma tier."""
    contract_errors = validate_manifest(manifest, root)
    if contract_errors:
        return "Invalid", contract_errors
    evidence_errors = release_evidence_errors(manifest, root)
    if evidence_errors:
        return "Development", evidence_errors
    return "Native candidate", []
