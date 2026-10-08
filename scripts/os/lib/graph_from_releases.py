#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Render an unsigned update graph from a channel's published releases.

  graph_from_releases.py REPO CHANNEL ARCH > graph.json

Hub renders channel graphs from Switchboard's release state (ADR-030 section
5). Until the signing job has Hub's service token, the nightly job renders the
nightly graph from what it published itself: every release manifest in
REPO/luma/releases whose commit the repository still has, oldest first, each
released to everyone at once (nightlies have no staged rollout) and none
paused, dead-ended or a barrier. sign-update-graph.sh validates and signs it
like a graph from Hub.
"""

import datetime
import glob
import json
import os
import sys


NOTES_BASE = "https://dl.simplyluma.com/media"


def notes_of(repo, manifest):
    """The release's signed notes (luma/releases/<version>/notes.json), if any."""
    path = os.path.join(repo, "luma", "releases", manifest["version"], "notes.json")
    if not os.path.exists(path):
        return None, ""
    with open(path, encoding="utf-8") as stream:
        notes = json.load(stream)
    return f"{NOTES_BASE}/{manifest['channel']}/notes/{manifest['build_id']}.json", " ".join(notes.get("summary", "").split())[:500]


def display_name_of(repo, manifest):
    """The release's name as people see it (ADR-040): its os-release PRETTY_NAME.

    Nightlies published before release names existed say "Luma 1.0" there;
    every one of them was a Prairie Beta 0 nightly, named for its nightly date
    (from its notes, or its build id's day).
    """
    release_dir = os.path.join(repo, "luma", "releases", manifest["version"])
    fields = {}
    try:
        with open(os.path.join(release_dir, "os-release"), encoding="utf-8") as stream:
            for line in stream:
                key, _, value = line.rstrip("\n").partition("=")
                fields[key] = value.strip('"')
    except OSError:
        pass
    if fields.get("VERSION_CODENAME") and fields.get("PRETTY_NAME", "").startswith("Luma ("):
        return fields["PRETTY_NAME"]
    if manifest.get("channel") != "nightly":
        return None
    day = manifest.get("build_id", "")[:8]
    try:
        with open(os.path.join(release_dir, "notes.json"), encoding="utf-8") as stream:
            day = json.load(stream).get("nightly_date", "").replace("-", "") or day
    except (OSError, ValueError):
        pass
    return f"Luma (Prairie, Beta 0, Nightly {day})" if len(day) == 8 and day.isdigit() else None


def superseded_summary(repo, channel, releases, manifest):
    """Releases published before notes existed point at the newest noted one."""
    for newer in sorted(releases, key=lambda m: m["published_utc"], reverse=True):
        path = os.path.join(repo, "luma", "releases", newer["version"], "notes.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as stream:
                day = json.load(stream).get("nightly_date", "")
            try:
                label = datetime.date.fromisoformat(day).strftime("%b %-d").replace("Sep ", "Sept ")
            except ValueError:
                label = day
            return f"Superseded; see the {label} {channel}"
    return f"Luma {channel} build {manifest['build_id']}"


def main(argv):
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    repo, channel, arch = argv[1:]
    releases = []
    for path in glob.glob(os.path.join(repo, "luma", "releases", "*", "manifest.json")):
        with open(path, encoding="utf-8") as stream:
            manifest = json.load(stream)
        commit = manifest["commit"]
        if manifest.get("channel") != channel:
            continue
        if not os.path.exists(os.path.join(repo, "objects", commit[:2], commit[2:] + ".commit")):
            continue
        releases.append(manifest)
    releases.sort(key=lambda manifest: manifest["published_utc"])
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    graph = {
        "schema_version": 1,
        "channel": channel,
        "arch": arch,
        "generated_at": now,
        "releases": [
            {
                "version": manifest["version"],
                "commit": manifest["commit"],
                "released_at": manifest["published_utc"],
                "rollout": {"start_at": manifest["published_utc"], "start_percentage": 1.0, "duration_minutes": 0},
                "paused": False,
                "deadend": False,
                "deadend_reason": None,
                "barrier": False,
                "importance": "normal",
                "notes_url": notes_of(repo, manifest)[0],
                "summary": notes_of(repo, manifest)[1] or superseded_summary(repo, channel, releases, manifest),
                "download_bytes_estimate": 0,
                **({"display_name": name} if (name := display_name_of(repo, manifest)) else {}),
            }
            for manifest in releases
        ],
    }
    if not graph["releases"]:
        print(f"error: no published {channel} release in {repo}", file=sys.stderr)
        return 1
    json.dump(graph, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
