#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Provenance, release manifests and status records for Luma OS builds.

Subcommands:
  build     write <build>/provenance.json after an image build
  manifest  write a signed-release manifest for a channel commit
  status    write the pipeline status record Hub and Switchboard ingest

Every document is canonical JSON (sorted keys, two-space indent, trailing
newline) so its digest is stable. None of them contains a host name, address,
user name or credential.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

SHA256 = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, payload):
    temporary = path + ".partial"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def read_json(path):
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def build(args):
    luma = []
    with open(args.luma_packages, encoding="utf-8") as stream:
        for line in stream:
            nevra, sha, header = line.split()
            luma.append({"nevra": nevra, "sha256": sha, "header_sha256": header})
    installed_count = 0
    with open(args.installed_packages, encoding="utf-8") as stream:
        installed_count = sum(1 for line in stream if line.strip() and not line.startswith("gpg-pubkey\t"))
    sbom_generator = ""
    if os.path.exists(args.sbom + ".generator"):
        with open(args.sbom + ".generator", encoding="utf-8") as stream:
            sbom_generator = stream.read().strip()
    payload = {
        "schema": "org.projectluma.os-build-provenance/v1",
        "build_id": args.build_id,
        "version": args.version,
        "channel": args.channel,
        "source": {"revision": args.source_revision, "dirty": args.source_dirty == "true"},
        "recipe": {"path": "image/luma-desktop/Containerfile", "digest": args.recipe_digest},
        "base_image": {
            "repository": args.base_repository,
            "tag": args.base_tag,
            "digest": args.base_digest,
            "ostree_commit": json.loads(args.base_labels or "{}").get("ostree.commit"),
            "version": json.loads(args.base_labels or "{}").get("org.opencontainers.image.version"),
        },
        "luma_packages": {
            "count": len(luma),
            "digest": hashlib.sha256("".join(f"{p['nevra']} {p['sha256']}\n" for p in luma).encode()).hexdigest(),
            "packages": luma,
        },
        "installed_packages": {
            "count": installed_count,
            "inventory_sha256": sha256_file(args.installed_packages),
        },
        "image": {"id": args.image_id, "size_bytes": int(args.image_size)},
        "build_log_sha256": sha256_file(args.build_log),
        "sbom": {"sha256": sha256_file(args.sbom), "format": "spdx-json", "generator": sbom_generator},
        "started_utc": args.started,
        "completed_utc": args.completed,
        "build_seconds": int(args.build_seconds),
        "tools": {"podman": args.podman_version},
    }
    if not SHA256.match(args.recipe_digest) or not re.fullmatch(r"[0-9a-f]{40}", args.source_revision):
        raise SystemExit("error: invalid recipe digest or source revision")
    if args.source_snapshot_sha256 or args.release_checks_sha256:
        if args.source_dirty != 'true' or not SHA256.fullmatch(args.source_snapshot_sha256) or not SHA256.fullmatch(args.release_checks_sha256):
            raise SystemExit('error: source snapshots are private dirty builds with both complete SHA256 bindings')
        payload['source'].update({'private': True,
            'snapshot_sha256': args.source_snapshot_sha256,
            'release_checks_sha256': args.release_checks_sha256})
    if args.app_baseline_input:
        app_input = read_json(args.app_baseline_input)
        if app_input.get('schema') != 'org.projectluma.os-app-input/v1' or not SHA256.fullmatch(app_input.get('manifest_sha256', '')):
            raise SystemExit('error: invalid application baseline provenance')
        payload['application_baseline'] = app_input
    write_json(args.output, payload)


def manifest(args):
    provenance = read_json(args.provenance)
    gate = read_json(args.gate) if args.gate else None
    payload = {
        "schema": "org.projectluma.os-release/v1",
        "version": args.version,
        "channel": args.channel,
        "ref": args.ref,
        "commit": args.commit,
        "parent": args.parent or None,
        "root_dirtree": args.root_dirtree,
        "build_id": provenance["build_id"],
        "build_version": provenance["version"],
        "source_revision": provenance["source"]["revision"],
        "recipe_digest": provenance["recipe"]["digest"],
        "base_image_digest": provenance["base_image"]["digest"],
        "luma_package_set_digest": provenance["luma_packages"]["digest"],
        "provenance_sha256": sha256_file(args.provenance),
        "sbom_sha256": provenance["sbom"]["sha256"],
        "gate": None if gate is None else {
            "result": gate.get("result"),
            "sha256": sha256_file(args.gate),
            "completed_utc": gate.get("completed_utc"),
        },
        "promoted_from": None if not args.promoted_from else {
            "channel": args.promoted_from_channel,
            "commit": args.promoted_from,
            "version": args.promoted_from_version,
        },
        "deltas": json.loads(args.deltas or "[]"),
        "published_utc": args.published,
    }
    for key in ("commit", "root_dirtree"):
        if not SHA256.match(payload[key]):
            raise SystemExit(f"error: invalid {key}")
    write_json(args.output, payload)


def status(args):
    payload = read_json(args.output) if os.path.exists(args.output) and args.merge else {}
    payload.update({
        "schema": "org.projectluma.os-pipeline-status/v1",
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    for item in args.set or []:
        key, _, value = item.partition("=")
        try:
            payload[key] = json.loads(value)
        except ValueError:
            payload[key] = value
    write_json(args.output, payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build")
    for name in ("output", "build-id", "version", "channel", "source-revision", "source-dirty",
                 "recipe-digest", "base-repository", "base-tag", "base-digest", "base-labels",
                 "luma-packages", "installed-packages", "image-id", "image-size", "build-log",
                 "sbom", "started", "completed", "build-seconds", "podman-version"):
        b.add_argument("--" + name, required=True)
    b.set_defaults(func=build)
    b.add_argument('--app-baseline-input')
    b.add_argument('--source-snapshot-sha256', default='')
    b.add_argument('--release-checks-sha256', default='')

    m = sub.add_parser("manifest")
    for name in ("output", "version", "channel", "ref", "commit", "root-dirtree", "provenance", "published"):
        m.add_argument("--" + name, required=True)
    for name in ("parent", "gate", "deltas", "promoted-from", "promoted-from-channel", "promoted-from-version"):
        m.add_argument("--" + name, default="")
    m.set_defaults(func=manifest)

    s = sub.add_parser("status")
    s.add_argument("--output", required=True)
    s.add_argument("--merge", action="store_true")
    s.add_argument("--set", action="append", help="KEY=JSON-or-string")
    s.set_defaults(func=status)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
