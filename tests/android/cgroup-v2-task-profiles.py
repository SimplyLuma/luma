#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check the vendor task profiles for a cgroup v2-only host.

Waydroid on Luma gives Android one cgroup2 hierarchy. The v1 cpuset,
schedtune, blkio, cpu and memory controllers in Android's cgroups.json cannot
be mounted there, so every profile that joins one of them or sets one of their
attributes fails and logs, on each service start and process state change.
patches/android-device-waydroid/0006 installs /vendor/etc/task_profiles.json,
which libprocessgroup reads last, with those profiles emptied. Timer slack and
the cgroup2 freezer keep their upstream profiles.

Without arguments the patch is checked on its own. With --source-root (an
Android tree) every upstream profile file is compared: each profile that
touches a v1-only controller must be overridden, and no other profile may be.
"""
import argparse
import json
from pathlib import Path
import sys

V1_ONLY = {"blkio", "cpu", "cpuset", "memory", "schedtune"}
UPSTREAM = ["task_profiles.json", "task_profiles_28.json", "task_profiles_29.json",
            "task_profiles_30.json"]

repo = Path(__file__).resolve().parents[2]
patch = repo / "patches/android-device-waydroid/0006-luma-cgroup-v2-task-profiles.patch"


def added_file(text: str, name: str) -> str:
    lines, inside = [], False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            inside = False
        elif line == f"+++ b/{name}":
            inside = True
        elif inside and line.startswith("+"):
            lines.append(line[1:])
    return "\n".join(lines)


def touches_v1(action: dict, attributes: dict) -> bool:
    params = action.get("Params", {})
    if action["Name"] == "SetClamps":
        return True  # libprocessgroup does not implement it; it only logs.
    if action["Name"] == "JoinCgroup":
        return params["Controller"] in V1_ONLY
    if action["Name"] == "SetAttribute":
        return attributes.get(params["Name"]) in V1_ONLY
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source-root", type=Path)
    options = parser.parse_args()

    text = patch.read_text()
    assert "$(LOCAL_PATH)/configs/task_profiles.json:$(TARGET_COPY_OUT_VENDOR)/etc/task_profiles.json" \
        in text, "device.mk must install the profiles as /vendor/etc/task_profiles.json"
    vendor = json.loads(added_file(text, "configs/task_profiles.json"))
    assert set(vendor) == {"Profiles"}, "only profiles are overridden"
    names = [profile["Name"] for profile in vendor["Profiles"]]
    assert len(names) == len(set(names)), "duplicate profile"
    for profile in vendor["Profiles"]:
        for action in profile["Actions"]:
            assert not touches_v1(action, {}) and action["Name"] != "JoinCgroup", \
                f"{profile['Name']} still uses {action}"

    if options.source_root:
        root = options.source_root / "system/core/libprocessgroup/profiles"
        attributes, upstream = {}, {}
        for name in UPSTREAM:
            data = json.loads((root / name).read_text())
            for attribute in data.get("Attributes", []):
                attributes[attribute["Name"]] = attribute["Controller"]
            for profile in data.get("Profiles", []):
                upstream.setdefault(profile["Name"], []).append(profile["Actions"])
        expected = {name for name, variants in upstream.items()
                    if any(touches_v1(a, attributes) for actions in variants for a in actions)}
        assert set(names) == expected, (
            f"missing {sorted(expected - set(names))}, extra {sorted(set(names) - expected)}")
        for profile in vendor["Profiles"]:
            for actions in upstream[profile["Name"]]:
                kept = [a for a in actions if not touches_v1(a, attributes)]
                assert kept == profile["Actions"], f"{profile['Name']} lost {kept}"

    print(f"PASS {len(names)} v1-only task profiles emptied"
          + (" (checked against the Android tree)" if options.source_root else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
