#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

import json
import re
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} DEPLOYMENT_JSON", file=sys.stderr)
        return 2
    try:
        with open(sys.argv[1], encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        print(f"error: cannot read deployment identity: {error}", file=sys.stderr)
        return 1
    booted = [item for item in data.get("deployments", []) if item.get("booted")]
    if len(booted) != 1:
        print("error: deployment identity must contain exactly one booted deployment", file=sys.stderr)
        return 1
    checksum = booted[0].get("checksum", "")
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        print("error: booted deployment checksum is invalid", file=sys.stderr)
        return 1
    print(checksum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
