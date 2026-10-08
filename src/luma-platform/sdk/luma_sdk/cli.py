# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tomllib
from pathlib import Path

from .validation import (
    application_id_errors,
    application_status,
    release_evidence_errors,
    validate_manifest,
)


def _manifest(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _cmd_new(args: argparse.Namespace) -> int:
    identifier_errors = application_id_errors(args.app_id)
    if identifier_errors:
        for error in identifier_errors:
            print(f"error: {error}", file=sys.stderr)
        return 2
    destination = Path(args.destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        print(f"error: destination is not empty: {destination}", file=sys.stderr)
        return 2
    template = Path(__file__).with_name("templates") / "python"
    destination.mkdir(parents=True, exist_ok=True)
    for source in template.rglob("*"):
        if "__pycache__" in source.parts or source.suffix == ".pyc":
            continue
        relative = Path(
            *(part.replace("@APP_ID@", args.app_id).replace("@APP_NAME@", args.name)
              for part in source.relative_to(template).parts)
        )
        target = destination / relative
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        content = source.read_text(encoding="utf-8")
        content = content.replace("@APP_ID@", args.app_id).replace("@APP_NAME@", args.name)
        target.write_text(content, encoding="utf-8")
        if source.name == "run-app":
            target.chmod(0o755)
    print(f"Created {args.name} at {destination}")
    return 0


def _cmd_lint(args: argparse.Namespace) -> int:
    path = Path(args.manifest).resolve()
    errors = validate_manifest(_manifest(path), path.parent)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Luma application contract passed: {path}")
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    path = Path(args.manifest).resolve()
    data = _manifest(path)
    status, gates = application_status(data, path.parent)
    errors = validate_manifest(data, path.parent)
    report = {
        "application": data.get("application", {}),
        "platform": data.get("platform", {}),
        "responsive": data.get("responsive", {}),
        "distribution": data.get("distribution", {}),
        "lifecycle": data.get("lifecycle", {}),
        "status": status,
        "native_candidate": status == "Native candidate",
        "status_gates": gates,
        "errors": errors,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not errors else 1


def _cmd_release_check(args: argparse.Namespace) -> int:
    path = Path(args.manifest).resolve()
    errors = release_evidence_errors(_manifest(path), path.parent)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        print(
            "This project is structurally valid but has not earned Luma Native status.",
            file=sys.stderr,
        )
        return 1
    print(f"Luma Native candidate evidence passed: {path}")
    return 0


def _cmd_test(args: argparse.Namespace) -> int:
    path = Path(args.manifest).resolve()
    data = _manifest(path)
    errors = validate_manifest(data, path.parent)
    widths = data.get("responsive", {}).get("test_widths", [])
    modes = data.get("responsive", {}).get("input_modes", [])
    for width in widths:
        for mode in modes:
            print(f"contract: width={width} input={mode}")
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print("Static adaptive and semantic contracts passed.")
    print("Runtime screenshot, keyboard, screen-reader, and portal lanes require the Luma SDK image.")
    return 0


def _cmd_live_preview(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.activity).read_text(encoding="utf-8"))
    actions = data.get("actions", [])
    errors = []
    if data.get("category") not in {"call", "media", "timer", "navigation", "event", "transfer", "recording", "installation", "generic"}:
        errors.append("unsupported Live Extension category")
    if not data.get("title"):
        errors.append("title is required")
    if len(actions) > 3:
        errors.append("a Live Extension may expose at most three actions")
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    for surface in ("desktop-top-bar", "tablet-action-strip", "phone-compact", "lock-screen"):
        redacted = data.get("privacy") in {"sensitive", "secret"} and surface == "lock-screen"
        title = "Private activity" if redacted else data["title"]
        print(f"{surface}: {title} ({len(actions)} actions)")
    return 0


def _print_json(value) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))


def _semantic_arguments(args: argparse.Namespace) -> list[str]:
    arguments = ["semantic", args.semantic_command]
    if args.semantic_command in {"get"}:
        arguments.append(args.publication_id)
    elif args.semantic_command in {"request", "revoke"}:
        arguments.append(args.application_id)
        if args.semantic_command == "request":
            for scope in args.scope:
                arguments.extend(("--scope", scope))
            arguments.extend(("--duration", str(args.duration)))
    elif args.semantic_command == "invoke":
        arguments.extend(
            (
                args.publication_id,
                args.object_id,
                args.action_id,
                "--parameter",
                args.parameter,
            )
        )
    elif args.semantic_command == "audit":
        arguments.extend(("--limit", str(args.limit)))
    return arguments


def _semantic_scope_command(
    args: argparse.Namespace,
    *,
    token: str | None = None,
) -> list[str]:
    suffix = token or secrets.token_hex(8)
    return [
        "/usr/bin/systemd-run",
        "--user",
        "--scope",
        "--quiet",
        f"--unit=app-gnome-org.projectluma.SemanticInspector-{suffix}.scope",
        "/usr/bin/env",
        "LUMA_SEMANTIC_INSPECTOR_MANAGED=1",
        "/usr/bin/luma",
        *_semantic_arguments(args),
    ]


def _cmd_semantic(args: argparse.Namespace) -> int:
    if os.environ.get("LUMA_SEMANTIC_INSPECTOR_MANAGED") != "1":
        try:
            return subprocess.run(
                _semantic_scope_command(args),
                check=False,
            ).returncode
        except OSError as error:
            print(f"error: unable to enter the managed inspector scope: {error}", file=sys.stderr)
            return 2
    # Keep ordinary project generation and static linting usable from a source
    # checkout that has not installed the runtime broker package yet.
    from .semantic import SemanticClient, SemanticClientError, json_parameter

    try:
        client = SemanticClient()
        if args.semantic_command == "list":
            _print_json(client.list_surfaces())
        elif args.semantic_command == "get":
            _print_json(client.get_surface(args.publication_id))
        elif args.semantic_command == "request":
            _print_json(
                client.request_access(
                    args.application_id,
                    args.scope,
                    duration=args.duration,
                )
            )
        elif args.semantic_command == "revoke":
            client.revoke_access(args.application_id)
            _print_json({"revoked": True, "application_id": args.application_id})
        elif args.semantic_command == "invoke":
            _print_json(
                client.invoke_action(
                    args.publication_id,
                    args.object_id,
                    args.action_id,
                    json_parameter(args.parameter),
                )
            )
        elif args.semantic_command == "audit":
            _print_json(client.audit(args.limit))
        else:
            raise SemanticClientError("missing semantic inspector operation")
        return 0
    except SemanticClientError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="luma", description="Luma Developer Platform tools")
    parser.add_argument("--version", action="version", version="luma-sdk 0.1.0")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("new", help="create a responsive GTK application")
    create.add_argument("name")
    create.add_argument("--id", dest="app_id", required=True)
    create.add_argument("--destination", default=".")
    create.set_defaults(func=_cmd_new)
    for name, function, help_text in (
        ("lint", _cmd_lint, "validate a Luma application contract"),
        ("inspect", _cmd_inspect, "inspect platform and conformance metadata"),
        ("test", _cmd_test, "run the local conformance contract matrix"),
        ("release-check", _cmd_release_check, "verify evidence required for Luma Native status"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("manifest", nargs="?", default="luma-app.toml")
        command.set_defaults(func=function)
    live = commands.add_parser("live-preview", help="preview a Live Extension contract")
    live.add_argument("activity")
    live.set_defaults(func=_cmd_live_preview)
    semantic = commands.add_parser(
        "semantic",
        help="inspect and exercise the live Semantic Broker (developer only)",
    )
    semantic_commands = semantic.add_subparsers(
        dest="semantic_command", required=True
    )
    semantic_commands.add_parser("list", help="list currently visible surfaces")
    get = semantic_commands.add_parser("get", help="read one visible surface")
    get.add_argument("publication_id")
    request = semantic_commands.add_parser(
        "request", help="show trusted consent and request a session grant"
    )
    request.add_argument("application_id")
    request.add_argument(
        "--scope",
        action="append",
        required=True,
        choices=(
            "observe-public",
            "observe-private",
            "invoke-low-risk",
            "invoke-consequential",
            "invoke-destructive",
            "invoke-security-sensitive",
        ),
    )
    request.add_argument("--duration", type=int, default=3600)
    revoke = semantic_commands.add_parser("revoke", help="revoke an app grant")
    revoke.add_argument("application_id")
    invoke = semantic_commands.add_parser(
        "invoke", help="invoke an exposed action through broker policy"
    )
    invoke.add_argument("publication_id")
    invoke.add_argument("object_id")
    invoke.add_argument("action_id")
    invoke.add_argument("--parameter", default="{}", help="JSON action parameter")
    audit = semantic_commands.add_parser(
        "audit", help="show this inspector identity's payload-free audit events"
    )
    audit.add_argument("--limit", type=int, default=50)
    semantic.set_defaults(func=_cmd_semantic)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
