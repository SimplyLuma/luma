# SPDX-License-Identifier: Apache-2.0
"""The review service: claim work from the Hub, do it, report back. One job at a time."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import traceback
from pathlib import Path

from . import catalogue, checks, images, publish, vm
from .config import Config
from .hub import Hub, HubError

EXTENSIONS = {"flatpak": ".flatpak", "rpm": ".rpm", "deb": ".deb", "appimage": ".AppImage"}


def log(message: str) -> None:
    print(time.strftime("%H:%M:%S"), message, flush=True)


def unwrap(reply, *keys):
    if isinstance(reply, dict):
        for key in keys:
            if isinstance(reply.get(key), dict):
                return reply[key]
    return reply


def podman_env(config: Config) -> dict:
    return dict(os.environ, CONTAINERS_STORAGE_CONF=str(config.storage_conf))


def refresh_signatures(config: Config) -> None:
    config.clamav_db.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["podman", "run", "--rm", "--security-opt", "label=disable", "--volume", f"{config.clamav_db}:/var/lib/clamav",
         config.review_image, "freshclam", "--stdout", "--datadir=/var/lib/clamav", "--foreground",
         "--user=root", "--no-warnings"],
        env=podman_env(config), capture_output=True, text=True, timeout=1800)
    log("freshclam: " + (result.stdout.strip().splitlines() or ["(no output)"])[-1])


def signatures_fresh(config: Config, hours: int = 26) -> bool:
    files = list(config.clamav_db.glob("daily.c[lv]d"))
    return bool(files) and time.time() - max(f.stat().st_mtime for f in files) < hours * 3600


def inspect_upload(config: Config, package: Path, fmt: str, work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    command = ["podman", "run", "--rm", "--network", "none", "--cap-drop", "ALL", "--security-opt", "label=disable",
               "--security-opt", "no-new-privileges", "--read-only", "--tmpfs", "/tmp:size=2g",
               "--pids-limit", "1024", "--memory", "6g", "--cpus", "2", "--env", "HOME=/tmp",
               "--volume", f"{config.source}:/src:ro", "--volume", f"{package}:/in/package:ro",
               "--volume", f"{work}:/work:rw", "--volume", f"{config.clamav_db}:/clamav:ro",
               "--workdir", "/src/tools/depot-review", config.review_image,
               "python3", "-B", "-m", "depot_review.examine", "--upload", "/in/package", "--format", fmt,
               "--work", "/work/x", "--clamav-db", "/clamav"]
    result = subprocess.run(command, env=podman_env(config), capture_output=True, text=True, timeout=5400)
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("inspection failed: " + result.stderr[-2000:])
    return json.loads(result.stdout)


def media_facts(config: Config, hub: Hub, listing: dict, job: Path) -> dict:
    out = {"screenshots": []}
    folder = job / "media"
    folder.mkdir(exist_ok=True)

    def fetch(url: str, name: str) -> dict:
        try:
            path = folder / name
            hub.download(url, path, limit=images.MAX_BYTES)
            kind, width, height = images.identify(path.read_bytes())
            return {"format": kind, "width": width, "height": height}
        except images.ImageError as error:
            return {"error": str(error)}
        except HubError as error:
            return {"error": f"couldn't be downloaded ({error})"}

    icon = listing.get("icon")
    icon_url = icon.get("url") if isinstance(icon, dict) else icon
    if icon_url:
        out["icon"] = fetch(icon_url, "icon")
    for index, shot in enumerate(listing.get("screenshots") or [], 1):
        url = shot.get("url") if isinstance(shot, dict) else shot
        form = (shot.get("form") if isinstance(shot, dict) else None) or "desktop"
        if url:
            out["screenshots"].append({"form": form, **fetch(url, f"shot-{index}")})
    return out


def luma_ids(config: Config) -> set[str]:
    ids = set(json.loads((config.source / "packaging/flatpak/apps/titles.json").read_text()))
    repo = config.depot_root / "repo"
    if (repo / "config").exists():
        refs = subprocess.run(["ostree", "refs", f"--repo={repo}"], capture_output=True, text=True).stdout.split()
        held = {r.split("/")[1] for r in refs if r.startswith("app/")}
        ids |= {i for i in held if not (config.depot_root / "releases" / i / "THIRD-PARTY").exists()}
    return ids


def owners(config: Config) -> dict:
    path = config.state / "owners.json"
    return json.loads(path.read_text()) if path.exists() else {}


def developer_of(submission: dict) -> str:
    developer = submission.get("developer") or {}
    return str(developer.get("id") or "") if isinstance(developer, dict) else str(developer)


def claim_owner(config: Config, app_id: str, developer: str) -> None:
    """The first developer to publish an id keeps it."""
    table = owners(config)
    holder = table.get(app_id)
    if holder and developer and holder != developer:
        raise publish.PublishError(f"{app_id} belongs to developer {holder}")
    if developer and not holder:
        table[app_id] = developer
        config.state.mkdir(parents=True, exist_ok=True)
        tmp = config.state / "owners.json.new"
        tmp.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n")
        os.replace(tmp, config.state / "owners.json")


def review(config: Config, hub: Hub | None, submission: dict, job: Path, packages: list[dict]) -> dict:
    """packages: [{arch, format, path, sha256}] already on disk. Returns the report."""
    policy = checks.load_policy()
    listing = submission.get("listing") or {}
    kind = submission.get("kind", "app")
    report_checks: list[dict] = []
    all_facts = []
    for package in packages:
        facts = inspect_upload(config, Path(package["path"]), package.get("format", ""), job / f"work-{package['arch']}")
        (job / f"facts-{package['arch']}.json").write_text(json.dumps(facts, indent=1))
        all_facts.append(facts)
        report_checks += checks.package_checks(facts, package, submission, policy, luma_ids(config))
    holder = owners(config).get(submission.get("app_id") or "")
    if holder and developer_of(submission) and holder != developer_of(submission):
        report_checks.append(checks.check("identity", False, "This app id belongs to another developer's app. "
                                          "Use a reverse-DNS id for a domain you own."))
    if not any(p["arch"] == "x86_64" for p in packages) and kind == "app":
        report_checks.append(checks.check("architecture", False, "Send an x86_64 build too. ARM builds are welcome "
                                                                 "alongside it."))
    perm_checks, perm_summary = checks.permission_checks(
        [f for f in all_facts if not f.get("refused")], submission.get("permissions"))
    report_checks += perm_checks
    if hub is not None:
        report_checks += checks.media_checks(media_facts(config, hub, listing, job), listing, kind)
    screenshots = {}
    vm_runs = {}
    static_ok = all(c["ok"] for c in report_checks)
    if static_ok and kind == "app" and not submission.get("listing_only"):
        for package, facts in zip(packages, all_facts):
            arch = package["arch"]
            if arch not in config.vm_arches:
                report_checks.append(checks.check(f"launch ({arch})", True,
                                                  f"Launch test on {arch} runs on ARM hardware; it's coming soon.",
                                                  skipped=True))
                continue
            run = vm.run_app(config.vm_base, config.vm_key, Path(package["path"]), facts["app_id"],
                             facts["format"], job / f"vm-{arch}", arch)
            vm_runs[arch] = run
            if run.get("screenshot"):
                screenshots[arch] = run["screenshot"]
            for step in run["steps"]:
                report_checks.append(checks.check(f"vm:{step['step']} ({arch})", step["ok"], step["message"],
                                                  detail=step.get("detail", ""), internal=step.get("internal", False)))
    status = checks.outcome(report_checks)
    report = {
        "status": status,
        "checks": report_checks,
        "permissions": perm_summary,
        "packages": [{k: f.get(k) for k in ("arch", "format", "app_id", "runtime", "branch", "sha256", "bytes",
                                            "installed_size", "version", "commit")} for f in all_facts],
        "notes": perm_summary.get("notes", []),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (job / "report.json").write_text(json.dumps(report, indent=1))
    report["_screenshots"] = screenshots
    return report


def download_packages(config: Config, hub: Hub, submission: dict, job: Path) -> list[dict]:
    packages = []
    folder = job / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    for upload in submission.get("uploads") or []:
        arch = upload.get("arch") or "x86_64"
        fmt = upload.get("format") or "flatpak"
        path = folder / f"{arch}{EXTENSIONS.get(fmt, '.bin')}"
        digest = hub.download(upload["url"], path, limit=config.max_upload_bytes, sha256=upload.get("sha256"))
        packages.append({"arch": arch, "format": fmt, "path": str(path), "sha256": digest})
    return packages


def handle_check(config: Config, hub: Hub, submission: dict) -> None:
    sid = str(submission["id"])
    job = config.work / "jobs" / f"{sid}-{time.strftime('%Y%m%dT%H%M%S')}"
    job.mkdir(parents=True)
    (job / "submission.json").write_text(json.dumps(submission, indent=1))
    log(f"checking submission {sid} ({submission.get('app_id')})")
    try:
        packages = download_packages(config, hub, submission, job)
        if not packages and not submission.get("listing_only"):
            raise RuntimeError("the submission has no uploads")
        report = review(config, hub, submission, job, packages)
    except Exception as error:  # report every failure; never leave a submission claimed
        traceback.print_exc()
        report = {"status": "attention", "checks": [checks.check("pipeline", False,
                  "Depot couldn't finish checking this. Luma will look at it; you don't need to do anything.",
                  detail=str(error)[-1500:], internal=True)], "_screenshots": {}}
    shots = report.pop("_screenshots", {})
    urls = {}
    for arch, path in shots.items():
        try:
            reply = hub.upload_file("check_screenshot", Path(path), id=sid, field="file")
            urls[arch] = (reply or {}).get("url", "")
        except HubError as error:
            log(f"screenshot upload failed: {error}")
    report["screenshots"] = urls
    hub.call("POST", "report_check", report, id=sid)
    log(f"reported {sid}: {report['status']}")
    prune_jobs(config)


def handle_publish(config: Config, hub: Hub, item: dict) -> None:
    sid = str(item["id"])
    app_id = item["app_id"]
    log(f"publishing {sid} ({app_id})")
    job = config.work / "jobs" / f"publish-{sid}-{time.strftime('%Y%m%dT%H%M%S')}"
    job.mkdir(parents=True)
    try:
        packages = download_packages(config, hub, item, job)
        flatpaks = [p for p in packages if p["format"] == "flatpak"]
        if not flatpaks or len(flatpaks) != len(packages):
            raise publish.PublishError("only Flatpak bundles publish to Depot tonight; universal packages are "
                                       "coming soon")
        claim_owner(config, app_id, developer_of(item))
        results = publish.publish(config, sid, app_id, flatpaks)
        hub.call("POST", "report_publish", {"status": "published", "releases": results,
                                            "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, id=sid)
        catalogue_due[0] = 0
    except Exception as error:
        traceback.print_exc()
        hub.call("POST", "report_publish", {"status": "failed", "message": str(error)[-1500:]}, id=sid)
    finally:
        shutil.rmtree(job, ignore_errors=True)


def handle_withdraw(config: Config, hub: Hub, item: dict) -> None:
    rid = str(item["id"])
    log(f"withdrawing {rid} ({item.get('app_id')})")
    results, errors = {}, []
    arches = item.get("arches") or [item.get("arch") or "x86_64"]
    for arch in arches:
        try:
            current = (item.get("commits") or {}).get(arch) or item.get("commit")
            results[arch] = publish.withdraw(config, rid, item["app_id"], arch, item.get("target"),
                                             item.get("reason", ""), current)
        except Exception as error:
            traceback.print_exc()
            errors.append(f"{arch}: {error}")
    body = {"status": "withdrawn" if not errors else "failed", "results": results}
    if errors:
        body["message"] = "\n".join(errors)[-1500:]
    hub.call("POST", "report_withdraw", body, id=rid)
    catalogue_due[0] = 0


def prune_jobs(config: Config, keep_days: int = 14) -> None:
    for job in (config.work / "jobs").glob("*"):
        if time.time() - job.stat().st_mtime > keep_days * 86400:
            shutil.rmtree(job, ignore_errors=True)
        else:
            for upload in (job / "uploads").glob("*"):
                if time.time() - upload.stat().st_mtime > 2 * 86400:
                    upload.unlink(missing_ok=True)


catalogue_due = [0.0]


def serve(config: Config, once: bool = False) -> None:
    from .r2 import R2
    hub = Hub(config.hub, config.token_file, config.paths)
    r2 = R2(config.r2_account, config.r2_bucket, config.r2_credentials)
    for path in (config.work, config.state, config.work / "jobs"):
        path.mkdir(parents=True, exist_ok=True)
    last_fresh = 0.0
    log("depot review service started")
    while True:
        try:
            if time.time() - last_fresh > 3 * 3600 or not signatures_fresh(config):
                refresh_signatures(config)
                last_fresh = time.time()
            busy = False
            for name, handler, keys in (("claim_withdraw", handle_withdraw, ("withdrawal", "item")),
                                        ("claim_publish", handle_publish, ("submission", "item")),
                                        ("claim_check", handle_check, ("submission", "item"))):
                item = unwrap(hub.call("POST", name), *keys)
                if item:
                    if name == "claim_check" and not signatures_fresh(config):
                        refresh_signatures(config)
                    handler(config, hub, item)
                    busy = True
                    break
            if config.publish_catalogue and time.time() >= catalogue_due[0]:
                try:
                    catalogue.publish(config, hub, r2)
                except Exception as error:  # the next round retries
                    log(f"catalogue: {error}")
                catalogue_due[0] = time.time() + config.catalogue_seconds
            if not busy:
                if once:
                    return
                time.sleep(config.poll_seconds)
        except HubError as error:
            log(f"hub: {error}")
            if once:
                raise
            time.sleep(config.poll_seconds * 3)
        except KeyboardInterrupt:
            return
        except Exception:
            traceback.print_exc()
            time.sleep(config.poll_seconds * 3)
