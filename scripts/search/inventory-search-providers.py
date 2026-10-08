#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Inventory the standard providers consumed by Luma Search."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/luma-search"))

from luma_search.contract import KIND_ORDER  # noqa: E402
from luma_search.providers import discover_providers  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-kind", action="append", default=[])
    args = parser.parse_args()

    providers = discover_providers()
    records = [
        {
            "id": provider.id,
            "desktop_id": provider.desktop_id,
            "bus_name": provider.bus_name,
            "object_path": provider.object_path,
            "kind": provider.kind,
            "default_disabled": provider.default_disabled,
        }
        for provider in providers
    ]
    if args.json:
        print(json.dumps(records, indent=2, sort_keys=True))
    else:
        print("App\tbuilt-in\tGAppInfo live catalog")
        for record in records:
            print(
                f"{record['kind'].title()}\t{record['desktop_id']}\t"
                f"{record['bus_name']}{record['object_path']}"
            )

    invalid = sorted(set(args.require_kind) - set(KIND_ORDER))
    if invalid:
        parser.error(f"unknown result kind(s): {', '.join(invalid)}")
    available = {"app", *(provider.kind for provider in providers)}
    missing = sorted(set(args.require_kind) - available)
    if missing:
        print(f"missing required provider kinds: {', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
