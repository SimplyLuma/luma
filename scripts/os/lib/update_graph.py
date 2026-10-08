#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate a Luma update graph before it is signed (ADR-030 section 5).

  update_graph.py validate --graph FILE --channel C --arch A
                  [--releases FILE] [--previous FILE] [--max-age-days N]

--releases is a JSON object {commit: version} of the releases the channel
repository actually publishes; every graph release must be one of them with
the same version, so the graph can never point devices at a commit Luma did
not publish. --previous is the last signed graph for the channel; the new
graph must be newer and must not silently drop a release that is still
published. Exit status 0 means the graph may be signed; problems are printed
one per line.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone

SHA256 = re.compile(r"^[0-9a-f]{64}$")
VERSION = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"

TOP_LEVEL = {"schema_version", "channel", "arch", "generated_at", "releases"}
RELEASE_REQUIRED = {
    "version", "commit", "released_at", "rollout", "paused", "deadend",
    "deadend_reason", "barrier", "importance", "notes_url", "summary",
    "download_bytes_estimate",
}
# display_name (ADR-040): the release's name as people see it. luma-update
# never rejected unknown release keys, so every installed client accepts it.
RELEASE_OPTIONAL = {"rollback_to", "display_name"}
ROLLOUT_REQUIRED = {"start_at", "start_percentage", "duration_minutes"}


def parse_time(value, where, problems):
    if not isinstance(value, str):
        problems.append(f"{where} must be a UTC timestamp string")
        return None
    try:
        return datetime.strptime(value, TIMESTAMP).replace(tzinfo=timezone.utc)
    except ValueError:
        problems.append(f"{where} must look like 2026-10-05T12:00:00Z, got {value!r}")
        return None


def version_key(version):
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    from versions import compare  # noqa: E402
    import functools
    return functools.cmp_to_key(compare)(version)


def validate(graph, channel, arch, releases=None, previous=None, max_age_days=14, now=None):
    now = now or datetime.now(timezone.utc)
    problems = []
    if not isinstance(graph, dict):
        return ["graph must be a JSON object"]
    missing = TOP_LEVEL - graph.keys()
    extra = graph.keys() - TOP_LEVEL
    if missing:
        problems.append(f"graph is missing {sorted(missing)}")
    if extra:
        problems.append(f"graph has unknown keys {sorted(extra)}")
    if graph.get("schema_version") != 1:
        problems.append("schema_version must be 1")
    if graph.get("channel") != channel:
        problems.append(f"channel must be {channel!r}, got {graph.get('channel')!r}")
    if graph.get("arch") != arch:
        problems.append(f"arch must be {arch!r}, got {graph.get('arch')!r}")
    generated = parse_time(graph.get("generated_at"), "generated_at", problems)
    if generated:
        if generated > now + timedelta(minutes=5):
            problems.append("generated_at is in the future")
        if generated < now - timedelta(days=max_age_days):
            problems.append(f"generated_at is older than {max_age_days} days")
    entries = graph.get("releases")
    if not isinstance(entries, list):
        problems.append("releases must be a list")
        entries = []
    if len(entries) > 512:
        problems.append("a graph may list at most 512 releases (luma-update's limit)")
    if len(json.dumps(graph).encode()) > 1024 * 1024:
        problems.append("the graph is larger than 1 MiB (luma-update's limit)")

    seen_versions, seen_commits = set(), set()
    ordered = []
    rollbacks = []
    for index, release in enumerate(entries):
        where = f"releases[{index}]"
        if not isinstance(release, dict):
            problems.append(f"{where} must be an object")
            continue
        missing = RELEASE_REQUIRED - release.keys()
        extra = release.keys() - RELEASE_REQUIRED - RELEASE_OPTIONAL
        if missing:
            problems.append(f"{where} is missing {sorted(missing)}")
        if extra:
            problems.append(f"{where} has unknown keys {sorted(extra)}")
        version = release.get("version")
        commit = release.get("commit")
        if not isinstance(version, str) or not VERSION.match(version):
            problems.append(f"{where}.version is not a Luma OS version")
        elif version in seen_versions:
            problems.append(f"{where}.version {version} appears twice")
        else:
            seen_versions.add(version)
            ordered.append(version)
        if not isinstance(commit, str) or not SHA256.match(commit):
            problems.append(f"{where}.commit is not a SHA-256 commit checksum")
        elif commit in seen_commits:
            problems.append(f"{where}.commit {commit} appears twice")
        else:
            seen_commits.add(commit)
            if releases is not None:
                if commit not in releases:
                    problems.append(f"{where}.commit {commit} is not published on {channel}")
                elif releases[commit] != version:
                    problems.append(f"{where}: {commit} is published as {releases[commit]}, not {version}")
        parse_time(release.get("released_at"), f"{where}.released_at", problems)
        rollout = release.get("rollout")
        if not isinstance(rollout, dict) or set(rollout.keys()) != ROLLOUT_REQUIRED:
            problems.append(f"{where}.rollout must have exactly {sorted(ROLLOUT_REQUIRED)}")
        else:
            parse_time(rollout.get("start_at"), f"{where}.rollout.start_at", problems)
            pct = rollout.get("start_percentage")
            if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 <= pct <= 1:
                problems.append(f"{where}.rollout.start_percentage must be a number in [0, 1]")
            duration = rollout.get("duration_minutes")
            if isinstance(duration, bool) or not isinstance(duration, int) or not 0 <= duration <= 525600:
                problems.append(f"{where}.rollout.duration_minutes must be a whole number of minutes up to a year")
        for flag in ("paused", "deadend", "barrier"):
            if not isinstance(release.get(flag), bool):
                problems.append(f"{where}.{flag} must be true or false")
        reason = release.get("deadend_reason")
        if isinstance(reason, str) and (len(reason) > 500 or any(ord(c) < 32 for c in reason)):
            problems.append(f"{where}.deadend_reason must be one line of at most 500 characters")
        if release.get("deadend") is True and not (isinstance(reason, str) and reason.strip()):
            problems.append(f"{where}.deadend_reason is required when deadend is true")
        if release.get("deadend") is False and reason is not None:
            problems.append(f"{where}.deadend_reason must be null unless deadend is true")
        if release.get("importance") not in ("normal", "security"):
            problems.append(f"{where}.importance must be normal or security")
        notes = release.get("notes_url")
        if notes is not None and not (isinstance(notes, str) and notes.startswith("https://")
                                      and len(notes) <= 2048 and not any(c.isspace() for c in notes)):
            problems.append(f"{where}.notes_url must be an https URL or null")
        summary = release.get("summary")
        if not isinstance(summary, str) or len(summary) > 500 or any(ord(c) < 32 for c in summary):
            problems.append(f"{where}.summary must be one line of at most 500 characters")
        name = release.get("display_name")
        if "display_name" in release and (not isinstance(name, str) or not name.startswith("Luma")
                                          or len(name) > 200 or any(ord(c) < 32 for c in name)):
            problems.append(f"{where}.display_name must be one line naming Luma, at most 200 characters")
        size = release.get("download_bytes_estimate")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            problems.append(f"{where}.download_bytes_estimate must be a non-negative integer")
        rollback = release.get("rollback_to")
        if rollback is not None:
            # {"version", "commit"} naming an older release in this graph
            # (docs/os/luma-update.md, "Graph addition: rollback_to").
            if (not isinstance(rollback, dict) or set(rollback.keys()) != {"version", "commit"}
                    or not isinstance(rollback.get("commit"), str) or not SHA256.match(rollback["commit"])
                    or not isinstance(rollback.get("version"), str) or not VERSION.match(rollback["version"])):
                problems.append(f"{where}.rollback_to must be {{\"version\", \"commit\"}} or null")
            elif release.get("deadend") is not True:
                problems.append(f"{where}.rollback_to is only valid on a deadend release")
            else:
                rollbacks.append((where, version, rollback))

    listed = {r.get("commit"): r.get("version") for r in entries if isinstance(r, dict)}
    for where, version, rollback in rollbacks:
        if listed.get(rollback["commit"]) != rollback["version"]:
            problems.append(f"{where}.rollback_to must name a release listed in this graph")
        elif isinstance(version, str) and VERSION.match(version):
            try:
                if version_key(rollback["version"]) >= version_key(version):
                    problems.append(f"{where}.rollback_to must name an older release")
            except ValueError as error:
                problems.append(str(error))
    try:
        if ordered != sorted(ordered, key=version_key):
            problems.append("releases must be listed oldest version first")
    except ValueError as error:
        problems.append(str(error))

    if previous:
        prev_generated = parse_time(previous.get("generated_at"), "previous generated_at", [])
        if generated and prev_generated and generated <= prev_generated:
            problems.append("generated_at must be newer than the last signed graph")
        prev_commits = {r.get("commit") for r in previous.get("releases", []) if isinstance(r, dict)}
        dropped = prev_commits - seen_commits
        if releases is not None:
            dropped = {c for c in dropped if c in releases}
        if dropped:
            problems.append(f"graph drops published releases {sorted(dropped)}; mark them deadend instead")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--graph", required=True)
    v.add_argument("--channel", required=True)
    v.add_argument("--arch", required=True)
    v.add_argument("--releases")
    v.add_argument("--previous")
    v.add_argument("--max-age-days", type=int, default=14)
    args = parser.parse_args(argv)
    try:
        with open(args.graph, encoding="utf-8") as stream:
            graph = json.load(stream)
    except (OSError, ValueError) as error:
        print(f"graph is not readable JSON: {error}")
        return 1
    releases = json.load(open(args.releases, encoding="utf-8")) if args.releases else None
    previous = json.load(open(args.previous, encoding="utf-8")) if args.previous else None
    problems = validate(graph, args.channel, args.arch, releases, previous, args.max_age_days)
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
