"""Command-line interface for Mod inspection and bounded lifecycle work."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .catalog_update import TufCatalogClient
from .errors import LumaModsError
from .manifest import inspect_manifest
from .inventory import collect_inventory
from .profile import PreferenceProfile
from .resolver import Catalog, HostContext, assess_mod, resolve_mod
from .runtime import UserRuntime, authorization_for_plan
from .trust import TrustPolicy, verify_inspection
from .update_policy import evaluate_update


def _inspection_dict(
    path: Path, policy: TrustPolicy | None = None
) -> dict[str, Any]:
    inspection = inspect_manifest(path)
    mod = inspection.mod
    assessment = assess_mod(inspection)
    verification = verify_inspection(inspection, policy)
    return {
        "id": mod.identity.id,
        "version": mod.identity.version,
        "name": mod.identity.name,
        "summary": mod.identity.summary,
        "kind": mod.identity.kind,
        "publisher": {
            "id": mod.identity.publisher.id,
            "name": mod.identity.publisher.name,
        },
        "licenses": list(mod.identity.licenses),
        "impact": assessment.impact,
        "activation": assessment.activation,
        "reasons": list(assessment.reasons),
        "dependencies": [
            {"id": item.id, "version": item.version, "reason": item.reason}
            for item in mod.dependencies
        ],
        "effects": {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in mod.effects.populated().items()
        },
        "source_sha256": inspection.source_sha256,
        "canonical_sha256": inspection.canonical_sha256,
        "signatures_present": len(mod.signatures),
        "verification": verification.as_dict(),
        "mutated_host": False,
    }


def _human_inspection(value: dict[str, Any]) -> str:
    lines = [
        f"{value['name']} {value['version']}",
        value["summary"],
        "",
        f"Publisher: {value['publisher']['name']} ({value['verification']['label']})",
        f"Impact: {value['impact']}",
        f"Activation: {value['activation']}",
    ]
    dependencies = value["dependencies"]
    if dependencies:
        lines.append("Dependencies:")
        for dependency in dependencies:
            lines.append(
                f"  - {dependency['id']} {dependency['version']}: {dependency['reason']}"
            )
    else:
        lines.append("Dependencies: none")
    effects = value["effects"]
    if effects:
        lines.append("Changes:")
        for name, items in effects.items():
            rendered = ", ".join(str(item) for item in items) if isinstance(items, list) else str(items)
            lines.append(f"  - {name.replace('_', ' ')}: {rendered}")
    else:
        lines.append("Changes: none declared")
    lines.extend(("", "Inspection only — no system changes were made."))
    return "\n".join(lines)


def _human_plan(value: dict[str, Any]) -> str:
    lines = [
        f"Mod plan: {value['target_id']}",
        f"Overall impact: {value['impact']}",
        f"Activation: {value['activation']}",
        "",
        "Mods included:",
    ]
    for item in value["mods"]:
        lines.append(
            f"  - {item['name']} {item['version']} by {item['publisher']} "
            f"[{item['verification']['label']}; {item['impact']}; {item['activation']}]"
        )
    if value["dependencies"]:
        lines.extend(("", "Required dependencies:"))
        for item in value["dependencies"]:
            state = "already installed" if item["already_installed"] else "will be included"
            lines.append(
                f"  - {item['name']} by {item['publisher']} ({state})\n"
                f"    {item['id']} {item['selected_version']} "
                f"(requires {item['version']})\n"
                f"    Trust: {item['verification']['label']}\n"
                f"    Required by {item['required_by']}: {item['reason']}"
            )
    if value["provider_transitions"]:
        lines.extend(("", "Provider changes on activation:"))
        for transition in value["provider_transitions"]:
            lines.append(
                f"  - {transition['capability']}: "
                f"{transition['from']['provider']} {transition['from']['version']} → "
                f"{transition['to']['provider']} {transition['to']['version']} "
                f"[{transition['ownership']}]\n"
                f"    {transition['reason']}"
            )
    lines.extend(
        (
            "",
            f"Resulting composition: sha256:{value['composition_sha256']}",
            "Plan only — no system changes were made.",
        )
    )
    return "\n".join(lines)


def _host(path: Path) -> HostContext:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LumaModsError(f"cannot read host context: {error}") from error
    if not isinstance(value, dict):
        raise LumaModsError("host context must be a JSON object")
    return HostContext.from_dict(value)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="luma-mod",
        description="Inspect and resolve Luma Mods without changing the system.",
    )
    commands = root.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="inspect one Mod manifest")
    inspect.add_argument("manifest", type=Path)
    inspect.add_argument("--trust-policy", type=Path)
    inspect.add_argument("--json", action="store_true", dest="as_json")
    plan = commands.add_parser("plan", help="resolve a Mod and all declared dependencies")
    plan.add_argument("mod_id")
    plan.add_argument("--catalog", type=Path, required=True)
    plan.add_argument("--host", type=Path, required=True)
    plan.add_argument("--trust-policy", type=Path)
    plan.add_argument("--json", action="store_true", dest="as_json")
    inventory = commands.add_parser(
        "host-scan", help="report observable managed and unmanaged host state"
    )
    inventory.add_argument("--json", action="store_true", dest="as_json")
    update_check = commands.add_parser(
        "update-check",
        help="classify a candidate against the previously reviewed update envelope",
    )
    update_check.add_argument("previous", type=Path)
    update_check.add_argument("candidate", type=Path)
    update_check.add_argument("--trust-policy", type=Path)
    update_check.add_argument("--json", action="store_true", dest="as_json")
    state = commands.add_parser("state", help="show installed Mods and transaction state")
    state.add_argument("--json", action="store_true", dest="as_json")
    for operation in ("install", "update"):
        command = commands.add_parser(
            operation,
            help=f"{operation} one declarative preference Mod transaction",
        )
        command.add_argument("mod_id")
        command.add_argument("--catalog", type=Path, required=True)
        command.add_argument("--host", type=Path, required=True)
        command.add_argument("--profile", type=Path, required=True)
        command.add_argument("--trust-policy", type=Path)
        command.add_argument(
            "--confirm-unverified",
            action="store_true",
            help="confirm the distinct Local / Unverified warning for this transaction",
        )
    for operation in ("catalog-plan", "catalog-install"):
        command = commands.add_parser(
            operation,
            help=(
                "resolve from a live TUF catalog without changing the host"
                if operation == "catalog-plan"
                else "install one declarative preference Mod from a live TUF catalog"
            ),
        )
        command.add_argument("mod_id")
        command.add_argument("--bootstrap-root", type=Path, required=True)
        command.add_argument("--cache", type=Path, required=True)
        command.add_argument("--metadata-url", required=True)
        command.add_argument("--targets-url", required=True)
        command.add_argument("--host", type=Path, required=True)
        command.add_argument("--trust-policy", type=Path, required=True)
        if operation == "catalog-plan":
            command.add_argument("--json", action="store_true", dest="as_json")
        else:
            command.add_argument(
                "--profile",
                type=Path,
                help=(
                    "use these exact reviewed bytes instead of acquiring the "
                    "digest-bound profile from the trusted catalog"
                ),
            )
            command.add_argument(
                "--confirm-unverified",
                action="store_true",
                help="confirm the distinct Local / Unverified warning for this transaction",
            )
    for operation in ("enable", "disable", "remove"):
        command = commands.add_parser(operation, help=f"{operation} an installed Mod")
        command.add_argument("mod_id")
    commands.add_parser("recover", help="recover an interrupted preference transaction")
    return root


def _state_summary(value: dict[str, Any]) -> str:
    lines = [
        f"Luma Mods generation {value['generation']}",
        f"Composition: sha256:{value['composition_sha256']}",
    ]
    if not value["installed"]:
        lines.append("Installed Mods: none")
    else:
        lines.append("Installed Mods:")
        for identifier, record in sorted(value["installed"].items()):
            status = "enabled" if record["enabled"] else "disabled"
            lines.append(
                f"  - {identifier} {record['version']} [{status}; {record['trust_level']}]"
            )
    if value["pending_transaction"] is not None:
        pending = value["pending_transaction"]
        lines.append(
            f"Pending: {pending['operation']} {pending['target_id']} ({pending['phase']})"
        )
    return "\n".join(lines)


def main(arguments: list[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    try:
        trust_policy = getattr(args, "trust_policy", None)
        policy = TrustPolicy.from_file(trust_policy) if trust_policy else None
        if args.command == "host-scan":
            value = collect_inventory()
            if args.as_json:
                output = json.dumps(value, indent=2, sort_keys=True)
            else:
                lines = ["Luma Mod host inventory (read only)"]
                for item in value["observations"]:
                    lines.append(
                        f"  - {item['kind']}: {item['identity']} [{item['provenance']}]"
                    )
                for failure in value["probe_failures"]:
                    lines.append(f"  ! {failure['probe']}: {failure['error']}")
                lines.append("No host changes were made.")
                output = "\n".join(lines)
        elif args.command == "update-check":
            previous = inspect_manifest(args.previous)
            candidate = inspect_manifest(args.candidate)
            decision = evaluate_update(
                previous,
                candidate,
                verify_inspection(previous, policy),
                verify_inspection(candidate, policy),
            )
            value = decision.as_dict()
            if args.as_json:
                output = json.dumps(value, indent=2, sort_keys=True)
            else:
                heading = "Automatic update" if decision.automatic else "Review required"
                output = "\n".join((heading, *(f"  - {reason}" for reason in decision.reasons)))
        elif args.command == "state":
            runtime = UserRuntime.current()
            value = runtime.store.read()
            output = (
                json.dumps(value, indent=2, sort_keys=True)
                if args.as_json
                else _state_summary(value)
            )
        elif args.command in {"install", "update"}:
            policy = TrustPolicy.from_file(args.trust_policy) if args.trust_policy else None
            plan = resolve_mod(
                args.mod_id,
                Catalog.from_directory(args.catalog),
                _host(args.host),
            )
            profile = PreferenceProfile.from_file(args.profile)
            runtime = UserRuntime.current()
            generation = runtime.store.read()["generation"]
            authorization = authorization_for_plan(
                plan,
                policy,
                confirmed_unverified=args.confirm_unverified,
            )
            if args.command == "install":
                value = runtime.lifecycle.install(
                    plan,
                    profile,
                    authorization,
                    expected_generation=generation,
                )
            else:
                value = runtime.lifecycle.update(
                    plan,
                    profile,
                    authorization,
                    expected_generation=generation,
                )
            output = _state_summary(value)
        elif args.command in {"catalog-plan", "catalog-install"}:
            client = TufCatalogClient(
                bootstrap_root=args.bootstrap_root,
                cache_root=args.cache,
                metadata_base_url=args.metadata_url,
                target_base_url=args.targets_url,
            )
            snapshot = client.refresh()
            trusted = client.resolve_plan(
                snapshot,
                args.mod_id,
                _host(args.host),
                policy,
            )
            if args.command == "catalog-plan":
                value = trusted.plan.as_dict()
                value["catalog_snapshot_id"] = trusted.snapshot_id
                for item in value["mods"]:
                    identifier = item["id"]
                    item["verification"] = trusted.verifications[identifier].as_dict()
                    item["catalog_role"] = trusted.trust_roles[identifier]
                for item in value["dependencies"]:
                    identifier = item["id"]
                    item["verification"] = trusted.verifications[identifier].as_dict()
                    item["catalog_role"] = trusted.trust_roles[identifier]
                output = (
                    json.dumps(value, indent=2, sort_keys=True)
                    if args.as_json
                    else _human_plan(value)
                    + f"\nSigned catalog: {trusted.snapshot_id}"
                )
            else:
                runtime = UserRuntime.current()
                generation = runtime.store.read()["generation"]
                profile = (
                    PreferenceProfile.from_file(args.profile)
                    if args.profile is not None
                    else client.acquire_preference_profile(snapshot, args.mod_id)
                )
                value = runtime.lifecycle.install(
                    trusted.plan,
                    profile,
                    trusted.authorization(
                        confirmed_unverified=args.confirm_unverified
                    ),
                    expected_generation=generation,
                )
                output = _state_summary(value)
        elif args.command in {"enable", "disable", "remove"}:
            runtime = UserRuntime.current()
            generation = runtime.store.read()["generation"]
            if args.command == "remove":
                value = runtime.lifecycle.remove(args.mod_id, expected_generation=generation)
            else:
                value = runtime.lifecycle.set_enabled(
                    args.mod_id,
                    args.command == "enable",
                    expected_generation=generation,
                )
            output = _state_summary(value)
        elif args.command == "recover":
            value = UserRuntime.current().lifecycle.recover()
            output = _state_summary(value)
        elif args.command == "inspect":
            value = _inspection_dict(args.manifest, policy)
            output = json.dumps(value, indent=2, sort_keys=True) if args.as_json else _human_inspection(value)
        else:
            catalog = Catalog.from_directory(args.catalog)
            plan = resolve_mod(args.mod_id, catalog, _host(args.host))
            value = plan.as_dict()
            if len(value["mods"]) != len(plan.mods):
                raise LumaModsError("internal plan serialization mismatch")
            for item, planned in zip(value["mods"], plan.mods):
                item["verification"] = verify_inspection(
                    planned.inspection, policy
                ).as_dict()
            if len(value["dependencies"]) != len(plan.dependencies):
                raise LumaModsError("internal dependency serialization mismatch")
            for item, dependency in zip(value["dependencies"], plan.dependencies):
                item["verification"] = verify_inspection(
                    catalog.require(dependency.dependency_id), policy
                ).as_dict()
            output = json.dumps(value, indent=2, sort_keys=True) if args.as_json else _human_plan(value)
        print(output)
        return 0
    except LumaModsError as error:
        print(f"luma-mod: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
