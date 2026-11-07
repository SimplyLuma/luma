# SPDX-License-Identifier: Apache-2.0
"""depot-review: run the Depot submission pipeline.

  depot-review serve                       claim work from the Hub until stopped
  depot-review check FILE [--arch A] [--format F] [--app-id ID] [--declare WORD ...] [--no-vm]
                                           check one package locally and print the report
  depot-review publish --id ID --app-id ID BUNDLE:ARCH ...
                                           publish reviewed bundles to the live remote
  depot-review withdraw --id ID --app-id ID --arch A [--target COMMIT] [--reason TEXT]
  depot-review catalogue [--force]         sign and publish the Hub's catalogue now
  depot-review keygen                      create the Depot catalogue minisign key (once)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from . import catalogue, publish, service
from .config import Config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="depot-review", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    c = sub.add_parser("check")
    c.add_argument("file", type=Path)
    c.add_argument("--arch", default="x86_64")
    c.add_argument("--format", default="flatpak")
    c.add_argument("--app-id", default="")
    c.add_argument("--declare", nargs="*", default=[])
    c.add_argument("--no-vm", action="store_true")
    p = sub.add_parser("publish")
    p.add_argument("--id", required=True)
    p.add_argument("--app-id", required=True)
    p.add_argument("bundles", nargs="+")
    w = sub.add_parser("withdraw")
    w.add_argument("--id", required=True)
    w.add_argument("--app-id", required=True)
    w.add_argument("--arch", default="x86_64")
    w.add_argument("--target")
    w.add_argument("--current")
    w.add_argument("--reason", default="Withdrawn")
    k = sub.add_parser("catalogue")
    k.add_argument("--force", action="store_true")
    sub.add_parser("keygen")
    args = parser.parse_args(argv)
    config = Config.load(args.config)

    if args.command == "serve":
        service.serve(config)
        return 0
    if args.command == "check":
        job = config.work / "jobs" / f"local-{time.strftime('%Y%m%dT%H%M%S')}"
        job.mkdir(parents=True)
        if args.no_vm:
            config.vm_arches = []
        submission = {"id": "local", "kind": "app", "app_id": args.app_id,
                      "permissions": [{"word": w, "reason": "declared for a local check"} for w in args.declare]}
        sha = publish.sha256_file(args.file)
        report = service.review(config, None, submission, job,
                                [{"arch": args.arch, "format": args.format, "path": str(args.file.resolve()),
                                  "sha256": sha}])
        print(json.dumps(report, indent=1))
        return 0 if report["status"] == "passed" else 1
    if args.command == "publish":
        bundles = []
        for spec in args.bundles:
            path, _, arch = spec.rpartition(":")
            bundles.append({"path": path, "arch": arch, "sha256": publish.sha256_file(Path(path))})
        print(json.dumps(publish.publish(config, args.id, args.app_id, bundles), indent=1))
        return 0
    if args.command == "withdraw":
        print(json.dumps(publish.withdraw(config, args.id, args.app_id, args.arch, args.target, args.reason,
                                          args.current), indent=1))
        return 0
    if args.command == "catalogue":
        from .hub import Hub
        from .r2 import R2
        hub = Hub(config.hub, config.token_file, config.paths)
        r2 = R2(config.r2_account, config.r2_bucket, config.r2_credentials)
        result = catalogue.publish(config, hub, r2, force=args.force)
        print(json.dumps(result or {"unchanged": True}, indent=1))
        return 0
    if args.command == "keygen":
        if config.catalogue_key.exists():
            print(f"{config.catalogue_key} already exists", file=sys.stderr)
            return 1
        config.catalogue_key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        subprocess.run(["minisign", "-G", "-W", "-p", str(config.catalogue_pub), "-s", str(config.catalogue_key),
                        "-c", "Luma Depot catalogue v1"], check=True)
        config.catalogue_key.chmod(0o600)
        print(config.catalogue_pub.read_text())
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
