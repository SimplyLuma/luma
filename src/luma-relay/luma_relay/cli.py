from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_config
from .engine import COMPONENTS, WineEngine
from .errors import RelayError
from .package import inspect_windows_package
from .registry import list_manifests


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="luma-relay")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="inspect the installed Relay backends")
    commands.add_parser("list", help="list installed compatibility applications")
    inspect = commands.add_parser("inspect", help="inspect a Windows package without running it")
    inspect.add_argument("file", type=Path)
    install = commands.add_parser("install", help="install a confirmed Windows package")
    install.add_argument("file", type=Path)
    install.add_argument("--yes", action="store_true", help="confirm the non-interactive transaction")
    launch = commands.add_parser("launch", help="launch one installed application")
    launch.add_argument("app_id")
    network = commands.add_parser("network", help="change an application's network access")
    network.add_argument("app_id")
    network.add_argument("state", choices=("on", "off"))
    notifications = commands.add_parser(
        "notifications", help="change an application's notification access"
    )
    notifications.add_argument("app_id")
    notifications.add_argument("state", choices=("on", "off"))
    grant = commands.add_parser("grant", help="grant one folder to an application")
    grant.add_argument("app_id")
    grant.add_argument("folder", type=Path)
    grant.add_argument("--mode", choices=("read-only", "read-write"), default="read-write")
    revoke = commands.add_parser("revoke", help="revoke one folder from an application")
    revoke.add_argument("app_id")
    revoke.add_argument("folder", type=Path)
    component = commands.add_parser("component", help="install a reviewed Windows component")
    component.add_argument("app_id")
    component.add_argument("name", choices=tuple(COMPONENTS))
    remove = commands.add_parser("remove", help="remove one installed application and its private data")
    remove.add_argument("app_id")
    remove.add_argument("--yes", action="store_true", help="confirm the removal")
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    config = load_config()
    engine = WineEngine(config)
    try:
        if arguments.command == "doctor":
            print(json.dumps(engine.doctor(), indent=2, sort_keys=True))
        elif arguments.command == "list":
            print(json.dumps(list_manifests(), indent=2, sort_keys=True))
        elif arguments.command == "inspect":
            package = inspect_windows_package(arguments.file, config.max_package_bytes)
            print(package.to_json())
        elif arguments.command == "install":
            if not arguments.yes:
                raise RelayError(
                    "Non-interactive installation requires --yes after the package has been inspected."
                )
            package = inspect_windows_package(arguments.file, config.max_package_bytes)
            print(json.dumps(engine.install(package), indent=2, sort_keys=True))
        elif arguments.command == "launch":
            engine.launch(arguments.app_id)
        elif arguments.command == "network":
            engine.set_network(arguments.app_id, arguments.state == "on")
        elif arguments.command == "notifications":
            engine.set_notifications(arguments.app_id, arguments.state == "on")
        elif arguments.command == "grant":
            engine.grant(arguments.app_id, arguments.folder, arguments.mode)
        elif arguments.command == "revoke":
            engine.revoke(arguments.app_id, arguments.folder)
        elif arguments.command == "component":
            engine.install_component(arguments.app_id, arguments.name)
        elif arguments.command == "remove":
            if not arguments.yes:
                raise RelayError("Removal requires --yes because the private Windows data will be deleted.")
            print(engine.remove(arguments.app_id))
        return 0
    except RelayError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
