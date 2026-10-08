"""Deterministic authoring helpers for Luma Mod maintainers."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from .author_artifact import identity
from .author_sandbox import reproducible_build
from .errors import LumaModsError
from .manifest import inspect_manifest
from .image_composition import build_image_composition_lock
from .profile import PreferenceProfile
from .pilot import template as pilot_template, validate as validate_pilot
from .resolver import Catalog, HostContext, resolve_mod


def _write_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def _replace_regular(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.is_symlink():
            raise LumaModsError(f"refusing to replace symlink output: {path}")
    except OSError as error:
        raise LumaModsError(f"cannot inspect output safely: {error}") from error
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o644)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _template(identifier: str, name: str, publisher_id: str, publisher_name: str) -> dict:
    return {
        "schema": "org.luma.mod/v0.1",
        "identity": {
            "id": identifier,
            "version": "0.1.0",
            "kind": "appearance",
            "name": name,
            "summary": "Describe the user-visible outcome.",
            "license": ["MIT"],
            "publisher": {"id": publisher_id, "name": publisher_name},
        },
        "compatibility": {
            "luma_base": ">=0.1.0 <1.0.0",
            "architectures": ["x86_64", "aarch64"],
            "presentations": ["desktop"],
            "hardware": [],
            "kernel_releases": [],
            "install_modes": ["running-system", "image-compose"],
        },
        "dependencies": [],
        "capabilities": {"provides": [], "requires": [], "conflicts": [], "replaces": []},
        "effects": {
            "settings": [], "files": [], "packages": [], "services": [],
            "dbus_names": [], "portals": [], "devices": [],
            "configuration_domains": [], "kernel_modules": [], "firmware": [],
            "boot_arguments": [], "user_data": [], "initramfs": False,
            "secure_boot": "none",
        },
        "payloads": [],
        "recovery": {
            "previous_composition_retained": False,
            "health_gate": "none",
            "user_data": "retain",
        },
        "evidence": {
            "source": "https://example.invalid/replace-with-immutable-source",
            "source_digest": f"sha256:{'0' * 64}",
        },
        "signatures": [],
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Create and validate deterministic Luma Mod metadata.")
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create a new non-overwriting manifest skeleton")
    init.add_argument("directory", type=Path)
    init.add_argument("--id", required=True, dest="identifier")
    init.add_argument("--name", required=True)
    init.add_argument("--publisher-id", required=True)
    init.add_argument("--publisher-name", required=True)
    lint = commands.add_parser("lint", help="strictly parse a manifest and optional profile")
    lint.add_argument("manifest", type=Path)
    lint.add_argument("--profile", type=Path)
    plan = commands.add_parser("plan", help="resolve a catalog entry against an explicit host")
    plan.add_argument("mod_id")
    plan.add_argument("--catalog", type=Path, required=True)
    plan.add_argument("--host", type=Path, required=True)
    image_plan = commands.add_parser(
        "image-plan",
        help="write a deterministic review-only Mod lock for an image-compose host",
    )
    image_plan.add_argument("mod_id")
    image_plan.add_argument("--catalog", type=Path, required=True)
    image_plan.add_argument("--host", type=Path, required=True)
    image_plan.add_argument("--output", type=Path, required=True)
    build = commands.add_parser("build", help="write deterministic review evidence, not an executable archive")
    build.add_argument("manifest", type=Path)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--payload", type=Path, action="append", default=[])
    reproducible = commands.add_parser(
        "verify-reproducible",
        help="compare two independently built, bounded artifact trees",
    )
    reproducible.add_argument("first", type=Path)
    reproducible.add_argument("second", type=Path)
    reproducible.add_argument("--output", type=Path, required=True)
    pilot = commands.add_parser("pilot-template", help="create a complete high-impact pilot checklist")
    pilot.add_argument("manifest", type=Path)
    pilot.add_argument("--host", type=Path, required=True)
    pilot.add_argument("--output", type=Path, required=True)
    verify_pilot = commands.add_parser("pilot-verify", help="validate completed pilot evidence")
    verify_pilot.add_argument("evidence", type=Path)
    sandbox = commands.add_parser(
        "sandbox-build",
        help="run two rootless networkless builds and publish only identical output",
    )
    sandbox.add_argument("--source", type=Path, required=True)
    sandbox.add_argument("--output", type=Path, required=True)
    sandbox.add_argument("--image", required=True)
    sandbox.add_argument("argv", nargs=argparse.REMAINDER)
    return root


def main(arguments: list[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    try:
        if args.command == "init":
            manifest = args.directory / f"{args.identifier}.mod.json"
            _write_new(
                manifest,
                _template(
                    args.identifier, args.name, args.publisher_id, args.publisher_name
                ),
            )
            output = str(manifest)
        elif args.command == "lint":
            inspection = inspect_manifest(args.manifest)
            if args.profile:
                profile = PreferenceProfile.from_file(args.profile)
                if (
                    profile.mod_id != inspection.mod.identity.id
                    or profile.version != inspection.mod.identity.version
                    or set(profile.values) != set(inspection.mod.effects.settings)
                ):
                    raise LumaModsError("profile identity, version, or settings do not match the manifest")
            output = json.dumps({
                "id": inspection.mod.identity.id,
                "version": inspection.mod.identity.version,
                "canonical_sha256": inspection.canonical_sha256,
                "signing_sha256": inspection.signing_sha256,
                "valid": True,
            }, indent=2, sort_keys=True)
        elif args.command == "plan":
            host_value = json.loads(args.host.read_text(encoding="utf-8"))
            plan = resolve_mod(
                args.mod_id,
                Catalog.from_directory(args.catalog),
                HostContext.from_dict(host_value),
            )
            output = json.dumps(plan.as_dict(), indent=2, sort_keys=True)
        elif args.command == "image-plan":
            host_value = json.loads(args.host.read_text(encoding="utf-8"))
            image_host = HostContext.from_dict(host_value)
            image_plan = resolve_mod(
                args.mod_id,
                Catalog.from_directory(args.catalog),
                image_host,
            )
            lock = build_image_composition_lock(image_plan, image_host)
            _replace_regular(
                args.output,
                (json.dumps(lock.as_dict(), indent=2, sort_keys=True) + "\n").encode("utf-8"),
            )
            output = str(args.output)
        elif args.command == "build":
            inspection = inspect_manifest(args.manifest)
            payloads = [identity(path) for path in args.payload]
            evidence = {
                "schema": "org.luma.mod-authoring-evidence/v0.1",
                "id": inspection.mod.identity.id,
                "version": inspection.mod.identity.version,
                "source_sha256": inspection.source_sha256,
                "canonical_sha256": inspection.canonical_sha256,
                "signing_sha256": inspection.signing_sha256,
                "effects": inspection.mod.effects.populated(),
                "payloads": [item.__dict__ for item in payloads],
            }
            _replace_regular(
                args.output,
                (json.dumps(evidence, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            )
            output = str(args.output)
        elif args.command == "verify-reproducible":
            first = identity(args.first)
            second = identity(args.second)
            report = {
                "schema": "org.luma.mod-reproducibility/v0.1",
                "first": first.__dict__,
                "second": second.__dict__,
                "reproducible": first == second,
            }
            _replace_regular(
                args.output,
                (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            )
            if first != second:
                print(str(args.output))
                return 3
            output = str(args.output)
        elif args.command == "pilot-template":
            evidence = pilot_template(args.manifest, args.host)
            _replace_regular(
                args.output,
                (json.dumps(evidence, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            )
            output = str(args.output)
        elif args.command == "pilot-verify":
            try:
                evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                raise LumaModsError(f"pilot evidence is invalid JSON: {error}") from error
            validate_pilot(evidence)
            output = json.dumps({
                "mod_id": evidence["mod_id"],
                "release_ready": evidence["release_ready"],
                "valid": True,
            }, indent=2, sort_keys=True)
        else:
            argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
            result = reproducible_build(
                args.source, args.output, args.image, argv
            )
            output = json.dumps({
                "output": str(args.output),
                "sha256": result.sha256,
                "files": result.files,
                "bytes": result.bytes,
                "reproducible": True,
            }, indent=2, sort_keys=True)
    except (LumaModsError, OSError, json.JSONDecodeError) as error:
        print(f"luma-mod-author: {error}", file=os.sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
