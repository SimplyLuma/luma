#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise actual native-HIDL guards in an already running disposable VM.

This changes only the synthetic fixture's activity and clipboard token. It does
not install images, qualify focus policy, test task mutations or establish full
runtime isolation. Stage native-clipboard-control.py alongside this file.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

PACKAGE = "org.projectluma.androidtest"
EVENTS = {"native-clipboard-before", "native-clipboard-write",
          "native-clipboard-after", "native-task-name"}


def assess(records, run_id, uid, pid, controls):
    """Unavailable controls, stale receipts and unknown origin cannot pass."""
    for phase in ("before", "after"):
        for service, expected in (("clipboard", "android-fixture"), ("task", "fixture-label")):
            control = controls.get(phase, {}).get(service, {})
            if phase == "after" and service == "clipboard" and control.get("state") == "forbidden-write-fixture":
                expected = "forbidden-write-fixture"
            if (control.get("uid"), control.get("state"), control.get("service")) != (1000, expected, service):
                return "inconclusive"
    selected = [row for row in records if row.get("guard_run") == run_id
                and row.get("event") in EVENTS]
    if len(selected) != len(EVENTS) or {row["event"] for row in selected} != EVENTS:
        return "inconclusive"
    if any(row.get("uid") != uid or row.get("pid") != pid for row in selected):
        return "inconclusive"
    values = {row["event"]: row.get("value") for row in selected}
    if any(not isinstance(value, str) or value.startswith("unavailable") for value in values.values()):
        return "inconclusive"
    return "passed" if controls["after"]["clipboard"]["state"] == "android-fixture" and values == {
        "native-clipboard-before": "empty", "native-clipboard-write": "request-returned",
        "native-clipboard-after": "empty", "native-task-name": "empty",
    } else "failed"


def command(*args, timeout=20):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=timeout).strip()


def android(*args):
    return command("lxc-attach", "-P", "/var/lib/waydroid/lxc", "-n", "waydroid",
                   "--clear-env", "--", "/system/bin/" + args[0], *args[1:])


def generation():
    init = command("lxc-info", "-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "-pH")
    system = android("pidof", "system_server")
    if not init.isdecimal() or not system.isdecimal():
        raise RuntimeError("Runtime generation unavailable")
    host_stat = Path("/proc") / init / "stat"
    return {"init_pid": int(init), "init_start": host_stat.read_text().rsplit(")", 1)[1].split()[19],
            "system_pid": int(system),
            "system_start": android("cat", "/proc/" + system + "/stat").rsplit(")", 1)[1].split()[19]}


def trusted(service, seed=False, observe_after=False):
    args = ["python3", str(Path(__file__).with_name("native-clipboard-control.py")),
            "--service", service, "--expect", "android-fixture" if service == "clipboard" else "fixture-label"]
    if seed:
        args.append("--seed-fixture")
    result = subprocess.run(args, text=True, capture_output=True, timeout=60)
    record = json.loads(result.stdout)
    if record.get("uid") != 1000 or record.get("service") != service or record.get("state") not in {
            "empty", "host-fixture", "android-fixture", "forbidden-write-fixture", "other-content", "fixture-label", "other-label", "unavailable"}:
        raise RuntimeError("Trusted synthetic control identity unavailable")
    record["returncode"] = result.returncode
    return record


def installed_apk_path(output):
    paths = output.splitlines()
    if len(paths) != 1 or not re.fullmatch(r"package:/data/app/[A-Za-z0-9_=/+.~\-]+\.apk", paths[0]):
        raise RuntimeError("Expected one installed fixture APK")
    apk = paths[0].removeprefix("package:")
    if ".." in Path(apk).parts or "." in apk.split("/"):
        raise RuntimeError("Installed fixture APK path traversal rejected")
    return apk


def run(expected_apk, report):
    report["phase"] = "installed-apk-path"
    apk = installed_apk_path(android("pm", "path", PACKAGE))
    report["phase"] = "installed-apk-hash"
    if android("sha256sum", apk).split()[0] != expected_apk:
        raise RuntimeError("Installed fixture APK hash mismatch")
    report["phase"] = "ordinary-package-uid"
    package = android("dumpsys", "package", PACKAGE)
    matches = re.findall(r"\buserId=(\d+)\b", package)
    if len(matches) != 1 or not 10000 <= int(matches[0]) < 100000:
        raise RuntimeError("Ordinary fixture application UID unavailable")
    uid = int(matches[0])
    report["phase"] = "runtime-generation"
    start = generation()
    report.update(android_uid=uid, apk_sha256=expected_apk, runtime_generation=start,
                  android_selinux=android("getenforce"),
                  runtime_package=command("rpm", "-q", "luma-android-runtime"))
    android("am", "force-stop", PACKAGE)
    report["phase"] = "focused-fixture-before-controls"
    android("am", "start", "-W", "-n", PACKAGE + "/.MainActivity",
            "--ez", "probe_native_clipboard", "true", "--ez", "probe_native_task", "true",
            "--ez", "native_guard_wait", "true", "--es", "guard_run", report["run_id"])
    ready_deadline = time.monotonic() + 30
    while time.monotonic() < ready_deadline:
        raw = android("head", "-c", "2097153", "/data/user/0/" + PACKAGE + "/files/events.jsonl")
        if len(raw.encode()) > 2097152:
            raise RuntimeError("Synthetic receipt file exceeded its limit")
        try:
            ready = [json.loads(line) for line in raw.splitlines()]
        except json.JSONDecodeError:
            time.sleep(.1)
            continue
        if any(row.get("guard_run") == report["run_id"] and row.get("event") == "native-guard-ready"
               and row.get("value") == "focused-before-controls" and row.get("uid") == uid for row in ready):
            break
        time.sleep(.1)
    else:
        raise RuntimeError("Focused fixture did not reach its private probe gate")
    report["phase"] = "trusted-before-controls"
    controls = {"before": {}}
    report["controls"] = controls
    controls["before"]["clipboard"] = trusted("clipboard", seed=True)
    controls["before"]["task"] = trusted("task")
    if controls["before"]["clipboard"].get("state") != "android-fixture" or controls["before"]["task"].get("state") != "fixture-label":
        raise RuntimeError("Nonempty trusted before-controls unavailable")
    report["phase"] = "ordinary-apk-probes"
    android("run-as", PACKAGE, "/system/bin/touch", "files/native-guard-" + report["run_id"] + ".trigger")
    deadline = time.monotonic() + 30
    records = []
    while time.monotonic() < deadline:
        # Bounded synthetic file; never copy arbitrary user clipboard contents.
        raw = android("head", "-c", "2097153", "/data/user/0/" + PACKAGE + "/files/events.jsonl")
        if len(raw.encode()) > 2097152:
            raise RuntimeError("Synthetic receipt file exceeded its limit")
        try:
            records = [json.loads(line) for line in raw.splitlines()]
        except json.JSONDecodeError:
            time.sleep(.25)  # The APK may still be appending the last record.
            continue
        selected = [row for row in records if row.get("guard_run") == report["run_id"]
                    and row.get("event") in EVENTS]
        if len(selected) >= len(EVENTS):
            break
        time.sleep(.25)
    pid = android("pidof", PACKAGE)
    if not pid.isdecimal():
        raise RuntimeError("Fixture process identity unavailable")
    kernel_uid = re.search(r"^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$",
                           android("cat", "/proc/" + pid + "/status"), re.M)
    if kernel_uid is None or any(int(value) != uid for value in kernel_uid.groups()):
        raise RuntimeError("Fixture kernel identity mismatch")
    report["phase"] = "trusted-after-controls"
    report["probe_receipts"] = [row for row in records if row.get("guard_run") == report["run_id"]
                                and row.get("event") in EVENTS]
    report["android_pid"] = int(pid)
    controls["after"] = {}
    controls["after"]["clipboard"] = trusted("clipboard", observe_after=True)
    controls["after"]["task"] = trusted("task")
    if generation() != start:
        raise RuntimeError("Runtime generation changed during qualification")
    report.update(android_pid=int(pid), status=assess(records, report["run_id"], uid, int(pid), controls))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0 or not re.fullmatch("[0-9a-f]{64}", args.apk_sha256):
        parser.error("Requires the disposable VM administrator and exact fixture APK SHA256")
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    report = {"schema": 1, "run_id": str(uuid.uuid4()), "status": "inconclusive",
              "scope": "actual native clipboard read/write and task-name UID admission only",
              "production_qualified": False, "matched_image_identity_established": False,
              "focus_task_mutations_and_other_boundaries_qualified": False}
    try:
        run(args.apk_sha256, report)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        # No raw command output, private process environment or clipboard text.
        report.update(status="inconclusive", error_type=type(error).__name__)
        if isinstance(error, RuntimeError):
            report["error"] = str(error)
    finally:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    print(json.dumps({"status": report["status"], "run_id": report["run_id"], "scope": report["scope"]}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
