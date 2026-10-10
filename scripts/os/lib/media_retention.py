#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Host-side ISO retention ledger; public index entries remain untouched."""
import argparse
import datetime as dt
import json
from pathlib import Path
import re

SCHEMA = "org.projectluma.media-retention/v1"


def utc(value):
    result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Retention times require a timezone")
    return result.astimezone(dt.timezone.utc)


def policy(entries, current, prior, now, days=7, previous=None, confirmations=None):
    if days < 7 or now.tzinfo is None:
        raise ValueError("At least seven days and an aware publication time are required")
    ids = [entry["build_id"] for entry in entries]
    if len(set(ids)) != len(ids) or current not in ids or any(
            not re.fullmatch(r"[0-9]{8}\.[0-9]+", build) for build in ids):
        raise ValueError("Invalid or duplicate installer identity")
    previous = previous or {"schema": SCHEMA, "deadlines": {}}
    if previous.get("schema") != SCHEMA:
        raise ValueError("Wrong retention ledger schema")
    deadlines = {build: utc(value) for build, value in previous["deadlines"].items()}
    minimum = now + dt.timedelta(days=days)
    pending = set(previous.get("unconfirmed_switches", []))
    # First enrollment protects all existing downloads for a full window. A
    # subsequent publication never shortens a previously promised deadline.
    for build in ids:
        if build not in deadlines:
            deadlines[build] = minimum
            if build != current:
                pending.add(build)
    # The ISO being replaced stays available seven days after this switch,
    # even if its original publication was much earlier. Preserve a longer
    # deadline and protect an out-of-band prior selection as well.
    for build in (prior, previous.get("current_build")):
        if build and build != current:
            deadlines[build] = max(deadlines.get(build, minimum), minimum)
            pending.add(build)
    for build, value in (confirmations or {}).items():
        switched = utc(value)
        if build not in ids or build == current or switched > now:
            raise ValueError("Invalid actual website switch confirmation")
        deadlines[build] = max(deadlines[build], switched + dt.timedelta(days=days))
        pending.discard(build)
    # Publication cannot infer when the website actually switched. Missing
    # confirmation keeps the old download indefinitely rather than expiring early.
    eligible = [build for build in ids if build != current and build not in pending and now >= deadlines[build]]
    ledger = {"schema": SCHEMA, "current_build": current,
              "minimum_days": days, "updated_utc": now.isoformat(),
              "unconfirmed_switches": sorted(pending),
              "deadlines": {build: value.isoformat() for build, value in sorted(deadlines.items())}}
    return ledger, eligible


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--current", required=True)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--confirm-replaced-build")
    parser.add_argument("--confirmed-switch-utc")
    args = parser.parse_args()
    if bool(args.confirm_replaced_build) != bool(args.confirmed_switch_utc):
        parser.error("Actual switch confirmation requires both replaced build and UTC time")
    tree, ledger_path = Path(args.tree), Path(args.ledger)
    entries = [json.loads(path.read_text()) for path in tree.glob("luma-*.iso.json")]
    prior_path = tree / "latest.json"
    prior = json.loads(prior_path.read_text())["latest"]["build_id"] if prior_path.exists() else None
    previous = json.loads(ledger_path.read_text()) if ledger_path.exists() else None
    ledger, eligible = policy(entries, args.current, prior, dt.datetime.now(dt.timezone.utc), args.days, previous,
                              {args.confirm_replaced_build: args.confirmed_switch_utc} if args.confirm_replaced_build else None)
    temporary = ledger_path.with_name(ledger_path.name + ".new")
    temporary.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
    temporary.replace(ledger_path)
    print("\n".join(eligible))


if __name__ == "__main__":
    main()
