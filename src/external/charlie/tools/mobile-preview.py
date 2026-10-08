#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run the normally installed Charlie with the existing sealed mobile toolkit.

Developer-only profile; does not install a toolkit or change its manifest.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

KIT_COMMIT = "c4f01891403a82915efc8de348c14d09be4c31f4"
EXECUTABLE = "/usr/bin/org.projectluma.Charlie"


def preview_environment(bundle: Path, inherited: dict[str, str]) -> dict[str, str]:
    bundle = bundle.resolve(strict=True)
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest.get("kit_commit") != KIT_COMMIT or manifest.get("architecture") != "aarch64":
        raise ValueError("Charlie requires the authorized c4f018914 ARM mobile preview toolkit")
    sdk = bundle / "shared/src/luma-platform/appkit"
    kit = bundle / "kit/usr"
    if not (sdk / "luma_appkit/__init__.py").is_file() or not (kit / "lib64").is_dir():
        raise ValueError("Existing sealed toolkit payload is incomplete")
    # These are the shared environment entries of the existing bundle's run.py.
    # Charlie itself is imported exclusively from its normal installed package.
    environment = dict(inherited)
    environment.update(
        PYTHONPATH=str(sdk),
        LD_LIBRARY_PATH=str(kit / "lib64"),
        GI_TYPELIB_PATH=str(kit / "lib64/girepository-1.0"),
        LUMA_APPKIT_ICON_PATH=":".join(map(str, (
            sdk / "icons", bundle / "shared/assets/icon-theme/Prairie/scalable/apps",
            bundle / "shared/assets/icon-theme/Prairie/symbolic/actions"))),
        XDG_DATA_DIRS=f"{kit / 'share'}:/usr/local/share:/usr/share",
    )
    return environment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--describe", action="store_true")
    arguments, app_arguments = parser.parse_known_args()
    environment = preview_environment(arguments.bundle, dict(os.environ))
    if arguments.describe:
        keys = ("PYTHONPATH", "LD_LIBRARY_PATH", "GI_TYPELIB_PATH",
                "LUMA_APPKIT_ICON_PATH", "XDG_DATA_DIRS")
        print(json.dumps({"executable": EXECUTABLE, "kit_commit": KIT_COMMIT,
                          "environment": {key: environment[key] for key in keys}}, indent=2))
        return
    os.execve(EXECUTABLE, [EXECUTABLE, *app_arguments], environment)


if __name__ == "__main__":
    main()
