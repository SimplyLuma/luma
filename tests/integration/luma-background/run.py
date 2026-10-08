#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
"""End-to-end check of ADR-033 in a Fedora 44 container with a real systemd user session.

Run as root inside the container after install.sh. Every check prints PASS or
FAIL with the evidence it read, and the whole record is written as JSON.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

UID = 1000
RUNTIME = f"/run/user/{UID}"
CONTROL = Path(RUNTIME) / "bga-control"
HERE = Path(__file__).resolve().parent
ENV = {"XDG_RUNTIME_DIR": RUNTIME, "DBUS_SESSION_BUS_ADDRESS": f"unix:path={RUNTIME}/bus",
       "HOME": "/home/luma", "PATH": "/usr/bin:/bin"}
RESULTS: list[dict] = []


def as_user(*command: str, check: bool = True, timeout: int = 60) -> str:
    completed = subprocess.run(["runuser", "-u", "luma", "--", "env", *[f"{k}={v}" for k, v in ENV.items()], *command],
                               capture_output=True, text=True, timeout=timeout)
    if check and completed.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} failed: {completed.stderr.strip()}")
    return completed.stdout


def record(name: str, ok: bool, evidence) -> bool:
    RESULTS.append({"check": name, "pass": bool(ok), "evidence": evidence})
    print(f"{'PASS' if ok else 'FAIL'}  {name}\n      {json.dumps(evidence, default=str)[:600]}", flush=True)
    return ok


def wait(predicate, seconds: float, step: float = 0.5):
    deadline = time.monotonic() + seconds
    value = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
        except Exception as error:  # noqa: BLE001 - keep polling
            value = None
            last = error
        if value:
            return value
        time.sleep(step)
    return value


def busctl(*args: str) -> dict:
    return json.loads(as_user("busctl", "--user", "--timeout=5", "--json=short", *args))


def agent(app: str) -> dict:
    reply = busctl("call", "org.projectluma.Background1", "/org/projectluma/Background1",
                   "org.projectluma.Background1", "GetAgent", "s", app)
    return {key: value["data"] for key, value in reply["data"][0].items()}


def values(app: str) -> dict:
    reply = busctl("get-property", f"{app}.Agent", "/org/projectluma/BackgroundAgent1",
                   "org.projectluma.BackgroundAgent1", "Values")
    return {key: value["data"] for key, value in reply["data"].items()}


def unit_property(unit: str, prop: str) -> str:
    return as_user("systemctl", "--user", "show", "-P", prop, unit).strip()


def cgroup_file(unit: str, name: str) -> str:
    group = unit_property(unit, "ControlGroup")
    return Path(f"/sys/fs/cgroup{group}/{name}").read_text()


def shell(*args: str) -> dict:
    output = as_user("/usr/bin/gnome-shell", str(HERE / "fakes/shell_call.py"), *args, check=False)
    return json.loads(output.strip().splitlines()[-1]) if output.strip() else {"error": "no output"}


def fake_system(method: str, signature: str, value: str) -> None:
    as_user("busctl", f"--address=unix:path={RUNTIME}/bga-system-bus", "call", "org.example.BgaTest",
            "/org/example/BgaTest", "org.example.BgaTest", method, signature, value)


def journal(*matches: str) -> list[str]:
    return as_user("journalctl", "--user", "--no-pager", "-o", "cat", *matches, check=False).splitlines()


def login() -> None:
    """Restart the person's user manager: the session starts over, as at login."""
    subprocess.run(["systemctl", "restart", f"user@{UID}.service"], check=True, timeout=120)
    wait(lambda: Path(f"{RUNTIME}/bus").exists(), 30)
    wait(lambda: as_user("busctl", "--user", "status", "org.projectluma.Background1", check=False), 60, 1)


def main() -> int:
    CONTROL.mkdir(parents=True, exist_ok=True)
    os.chown(CONTROL, UID, UID)
    for name in ("notifications.jsonl", "flatpak-argv", "counter-app.log", "counter.order", "chatter.order"):
        (CONTROL / name).unlink(missing_ok=True)
    (CONTROL / "notification-answer").write_text("allow")
    as_user("rm", "-rf", "/home/luma/.config/luma-background", "/home/luma/.local/state/luma-background",
            "/home/luma/.local/share/flatpak/db", "/home/luma/.config/autostart", check=False)

    # -- Login ---------------------------------------------------------------
    login()
    # Read the moment the service answers on the bus, which is when systemd lets
    # the session's autostart run: the mask must already be loaded.
    fallback = "app-org.example.Chatter.Agent@autostart.service"
    load_state = unit_property(fallback, "LoadState")
    link = Path(f"{RUNTIME}/systemd/user/{fallback}")
    record("the app's own autostart entry naming its agent is masked before the service is ready",
           load_state == "masked" and link.is_symlink() and os.readlink(link) == "/dev/null",
           {"LoadState": load_state, "link": os.readlink(link) if link.is_symlink() else None})
    chatter = wait(lambda: agent("org.example.Chatter")["state"] == "running" and agent("org.example.Chatter"), 60)
    record("essential agent is running after login", bool(chatter), chatter and {
        k: chatter[k] for k in ("state", "essential", "default", "category", "explanation", "unit")})
    wakes = wait(lambda: "login::0" in values("org.example.Chatter")["wakes"] and values("org.example.Chatter")["wakes"], 30)
    record("the agent received the login wake", bool(wakes), wakes)
    counter = agent("org.example.Counter")
    record("a third-party agent is off until asked", counter["state"] == "off" and counter["default"] == "ask",
           {k: counter[k] for k in ("state", "default", "decision")})
    unit_text = Path(f"{RUNTIME}/systemd/user/app-org.example.Chatter-agent.service").read_text()
    limits = {prop: unit_property("app-org.example.Chatter-agent.service", prop)
              for prop in ("MemoryHigh", "MemoryMax", "CPUWeight", "IOWeight", "Slice", "Restart", "Type", "BusName")}
    record("generated unit carries the category's limits", limits["MemoryMax"] == "134217728"
           and limits["MemoryHigh"] == "67108864" and limits["CPUWeight"] == "20"
           and limits["Slice"] == "luma-background-essential.slice", limits)
    record("unit runs the desktop entry's program", 'ExecStart="/usr/bin/luma-chatter" "--agent"' in unit_text,
           [line for line in unit_text.splitlines() if line.startswith("ExecStart")])

    # -- First request, from the app, while its window is open -------------------
    app_log = CONTROL / "counter-app.log"
    app = subprocess.Popen(["runuser", "-u", "luma", "--", "env", *[f"{k}={v}" for k, v in ENV.items()],
                            "systemd-run", "--user", "--scope", "--quiet", "--unit=app-gnome-org.example.Counter-4242.scope",
                            "sh", "-c", f"exec luma-counter >{app_log} 2>&1"])
    answered = wait(lambda: "request_background:" in app_log.read_text() and app_log.read_text(), 40)
    prompts = [json.loads(line) for line in (CONTROL / "notifications.jsonl").read_text().splitlines()] \
        if (CONTROL / "notifications.jsonl").exists() else []
    record("first request shows the one-time prompt", any(p["summary"] == "Let Counter work in the background?" for p in prompts),
           prompts)
    record("the app is told it may run in the background", bool(answered) and "allowed=True" in answered, answered)
    running = wait(lambda: agent("org.example.Counter")["state"] == "running", 30)
    counter_values = wait(lambda: "request::0" in values("org.example.Counter")["wakes"] and values("org.example.Counter"), 20)
    record("allowed agent starts with the request wake", bool(running and counter_values), counter_values)

    # -- The window closes; the agent keeps working --------------------------------
    as_user("systemctl", "--user", "stop", "app-gnome-org.example.Counter-4242.scope", check=False)
    app.wait(timeout=30)
    before = values("org.example.Counter")["count"]
    time.sleep(4)
    after = values("org.example.Counter")["count"]
    record("agent survives its app's window closing", after > before and
           unit_property("app-org.example.Counter-agent.service", "ActiveState") == "active",
           {"count_before": before, "count_after": after})

    # -- A live extension fed by the agent, read by the Shell --------------------------
    published = wait(lambda: values("org.example.Counter").get("live-extension"), 30)
    listing = shell("ListLiveExtensions")
    extensions = [item for item in listing.get("value", []) if item.get("application_id") == "org.example.Counter"
                  and item.get("extension", {}).get("subtitle", "").endswith("so far")]
    record("agent's Live Extension is accepted by the broker under its app's identity",
           bool(published) and bool(extensions), {"published": published, "listing": extensions or listing})
    reader = as_user("python3", "-c", """
import sys
sys.path.insert(0, '/usr/lib/python3.14/site-packages')
from gi.repository import GLib
from luma_appkit.background import AgentValue
import gi
from gi.repository import Gio
binding = AgentValue('org.example.Counter.Agent', 'count', connection=Gio.bus_get_sync(Gio.BusType.SESSION, None))
seen = []
binding.connect(lambda value: seen.append(value.value))
loop = GLib.MainLoop()
GLib.timeout_add(3500, loop.quit)
loop.run()
print(seen)
""", timeout=30)
    seen = json.loads(reader.strip().splitlines()[-1])
    record("from_agent binding in another process follows the published value",
           len(seen) >= 2 and seen == sorted(seen), seen)

    # -- Login again: decisions persist, agents return --------------------------------
    login()
    back = wait(lambda: all(agent(app)["state"] == "running" for app in ("org.example.Counter", "org.example.Chatter")), 60)
    counter_wakes = wait(lambda: "login::0" in values("org.example.Counter")["wakes"] and values("org.example.Counter")["wakes"], 30)
    record("after the next login both agents start again, with the login wake", bool(back and counter_wakes),
           {"counter": agent("org.example.Counter")["decision"], "wakes": counter_wakes})

    # -- Network and resume ----------------------------------------------------------
    fake_system("SetNetwork", "u", "20")
    time.sleep(1)
    fake_system("SetNetwork", "u", "70")
    network = wait(lambda: all("network::0" in values(app)["wakes"] for app in ("org.example.Counter", "org.example.Chatter")), 20)
    record("network-online wakes the agents", bool(network),
           {app: values(app)["wakes"] for app in ("org.example.Counter", "org.example.Chatter")})
    fake_system("Sleep", "b", "true")
    fake_system("Sleep", "b", "false")
    resume = wait(lambda: all("resume::0" in values(app)["wakes"] for app in ("org.example.Counter", "org.example.Chatter")), 20)
    record("resume wakes the agents", bool(resume), {app: values(app)["wakes"] for app in ("org.example.Counter", "org.example.Chatter")})

    # -- A schedule owned by a systemd timer -------------------------------------------
    timers = as_user("systemctl", "--user", "list-timers", "--all", "--no-pager", check=False)
    scheduled = wait(lambda: "schedule:soon:0" in values("org.example.Chatter")["wakes"], 60, 1)
    record("a scheduled wake is delivered by the agent's own timer", bool(scheduled),
           {"timers": [line for line in timers.splitlines() if "agent-soon" in line],
            "wakes": values("org.example.Chatter")["wakes"]})

    # -- Power Saver --------------------------------------------------------------------
    fake_system("SetProfile", "s", "power-saver")
    frozen = wait(lambda: "frozen 1" in cgroup_file("app-org.example.Counter-agent.service", "cgroup.events"), 15)
    c1, h1 = values_safe("org.example.Counter"), values("org.example.Chatter")["count"]
    time.sleep(3)
    c2, h2 = values_safe("org.example.Counter"), values("org.example.Chatter")["count"]
    counter_described = agent("org.example.Counter")
    record("Power Saver freezes the non-exempt agent", bool(frozen) and counter_described["state"] == "paused",
           {"cgroup.events": cgroup_file("app-org.example.Counter-agent.service", "cgroup.events").split(),
            "state": counter_described["state"],
            "FreezerState": unit_property("app-org.example.Counter-agent.service", "FreezerState")})
    record("the communication agent keeps running in Power Saver",
           "frozen 0" in cgroup_file("app-org.example.Chatter-agent.service", "cgroup.events") and h2 > h1,
           {"chatter_count": [h1, h2], "state": agent("org.example.Chatter")["state"]})
    fake_system("SetProfile", "s", "balanced")
    thawed = wait(lambda: "frozen 0" in cgroup_file("app-org.example.Counter-agent.service", "cgroup.events"), 15)
    time.sleep(2)
    c3 = values("org.example.Counter")["count"]
    record("leaving Power Saver resumes it where it was", bool(thawed) and c3 > (c2 or 0),
           {"frozen_counts": [c1, c2], "after": c3})

    # -- Crash and restart ------------------------------------------------------------
    old_pid = values("org.example.Chatter")["pid"]
    (CONTROL / "chatter.order").write_text("crash")
    restarted = wait(lambda: unit_property("app-org.example.Chatter-agent.service", "NRestarts") not in {"", "0"}
                     and values("org.example.Chatter")["pid"] != old_pid, 40)
    crash_journal = journal("LUMA_APP_ID=org.example.Chatter", "LUMA_BACKGROUND_EVENT=crashed")
    record("a crashed agent is restarted with backoff and logged with its app ID",
           bool(restarted) and bool(crash_journal),
           {"NRestarts": unit_property("app-org.example.Chatter-agent.service", "NRestarts"),
            "journal": crash_journal[-2:], "last-exit": agent("org.example.Chatter")["last-exit"]})

    # -- Memory limit ------------------------------------------------------------------
    counter_unit = "app-org.example.Counter-agent.service"
    kernel_limits = {name: cgroup_file(counter_unit, name).strip() for name in ("memory.high", "memory.max", "memory.swap.max")}
    record("the kernel enforces the agent's memory limits on its cgroup",
           kernel_limits == {"memory.high": "67108864", "memory.max": "134217728", "memory.swap.max": "67108864"},
           kernel_limits)
    # Reaching memory.max is exercised with memory.high lifted for this one
    # unit. Exceeding memory.high throttles the agent for as long as it keeps
    # allocating, and that stall is memory pressure on every slice above it:
    # on a shared machine the host's systemd-oomd then kills whatever is
    # largest in the person's session. A single allocation straight past
    # memory.max is killed within the agent's own cgroup at once.
    as_user("systemctl", "--user", "set-property", "--runtime", counter_unit, "MemoryHigh=infinity")
    restarts_before = int(unit_property(counter_unit, "NRestarts") or 0)
    (CONTROL / "counter.order").write_text("hog")
    oom = wait(lambda: int(unit_property(counter_unit, "NRestarts") or 0) > restarts_before, 30)
    result = unit_property(counter_unit, "Result")
    back = wait(lambda: unit_property(counter_unit, "ActiveState") == "active"
                and values_safe("org.example.Counter") is not None, 60)
    as_user("systemctl", "--user", "revert", counter_unit, check=False)
    memory_journal = [line for line in journal("-u", counter_unit) if "oom" in line.lower() or "memory" in line.lower()]
    record("an agent past its memory limit is stopped by the kernel and restarted",
           bool(oom) and bool(back) and bool(memory_journal),
           {"NRestarts": unit_property(counter_unit, "NRestarts"), "journal": memory_journal[-3:],
            "last-exit": agent("org.example.Counter")["last-exit"], "result-before-restart": result})

    # -- Flatpak through the portal's permission table and autostart -------------------
    (CONTROL / "notification-answer").write_text("allow")
    as_user("busctl", "--user", "call", "org.freedesktop.impl.portal.PermissionStore",
            "/org/freedesktop/impl/portal/PermissionStore", "org.freedesktop.impl.portal.PermissionStore",
            "SetPermission", "sbssas", "background", "true", "background", "org.example.Flatchat", "1", "yes")
    flatchat = wait(lambda: agent("org.example.Flatchat")["decision"] == "allow" and agent("org.example.Flatchat"), 30)
    record("a sandboxed app's first portal request brings the Luma prompt and its answer",
           bool(flatchat) and any("Flatchat" in json.loads(line)["summary"]
                                  for line in (CONTROL / "notifications.jsonl").read_text().splitlines()),
           flatchat and {k: flatchat[k] for k in ("origin", "decision", "state")})
    autostart = Path("/home/luma/.config/autostart")
    as_user("mkdir", "-p", str(autostart))
    as_user("sh", "-c", f"printf '%s\\n' '[Desktop Entry]' 'Type=Application' 'Name=Flatchat' "
            f"'X-XDP-Autostart=org.example.Flatchat' 'X-Flatpak=org.example.Flatchat' "
            f"'Exec=flatpak run --command=flatchat org.example.Flatchat --background' >{autostart}/org.example.Flatchat.desktop")
    started = wait(lambda: (CONTROL / "flatpak-argv").exists() and (CONTROL / "flatpak-argv").read_text(), 30)
    mask = Path(f"{RUNTIME}/systemd/user/app-org.example.Flatchat@autostart.service")
    record("the portal's autostart entry runs once, as a sandboxed agent under the limits",
           bool(started) and "run --die-with-parent --command=flatchat org.example.Flatchat --background" in started
           and mask.is_symlink() and os.readlink(mask) == "/dev/null",
           {"flatpak argv": started, "autostart unit masked": mask.is_symlink(),
            "slice": unit_property("app-org.example.Flatchat-agent.service", "Slice")})
    (CONTROL / "notification-answer").write_text("deny")
    notify = busctl("call", "org.freedesktop.impl.portal.desktop.luma.background", "/org/freedesktop/portal/desktop",
                    "org.freedesktop.impl.portal.Background", "NotifyBackground", "oss",
                    "/org/freedesktop/portal/desktop/notify/background1", "org.example.Flatquiet", "Flatquiet")
    stored = busctl("call", "org.freedesktop.impl.portal.PermissionStore", "/org/freedesktop/impl/portal/PermissionStore",
                    "org.freedesktop.impl.portal.PermissionStore", "GetPermission", "sss", "background", "background",
                    "org.example.Flatquiet")
    record("NotifyBackground answers from the person's decision and mirrors it to the portal table",
           notify["data"][1]["result"]["data"] == 0 and stored["data"][0] == ["no"],
           {"reply": notify["data"], "permission store": stored["data"]})
    app_state = subprocess.run(["runuser", "-u", "luma", "--", "env", *[f"{k}={v}" for k, v in ENV.items()], "busctl", "--user",
                                "call", "org.freedesktop.impl.portal.desktop.luma.background", "/org/freedesktop/portal/desktop",
                                "org.freedesktop.impl.portal.Background", "GetAppState"], capture_output=True, text=True)
    record("GetAppState refuses to guess when the window list is unavailable",
           app_state.returncode != 0 and "window state is unavailable" in app_state.stderr, app_state.stderr.strip())
    denied = shell("SetAllowed", "org.example.Flatchat", "false")
    stored = busctl("call", "org.freedesktop.impl.portal.PermissionStore", "/org/freedesktop/impl/portal/PermissionStore",
                    "org.freedesktop.impl.portal.PermissionStore", "GetPermission", "sss", "background", "background",
                    "org.example.Flatchat")
    gone = wait(lambda: unit_property("app-org.example.Flatchat-agent.service", "LoadState") == "not-found", 20)
    record("turning a sandboxed app off in the dock stops it and updates the portal table",
           denied.get("value") is True and stored["data"][0] == ["no"] and bool(gone),
           {"shell": denied, "permission store": stored["data"]})

    # -- Who may flip the switch -------------------------------------------------------
    refused = subprocess.run(["runuser", "-u", "luma", "--", "env", *[f"{k}={v}" for k, v in ENV.items()], "busctl", "--user",
                              "call", "org.projectluma.Background1", "/org/projectluma/Background1",
                              "org.projectluma.Background1", "SetAllowed", "sb", "org.example.Flatquiet", "true"],
                             capture_output=True, text=True)
    record("an ordinary process cannot allow an app", refused.returncode != 0 and "NotAuthorized" in refused.stderr
           or "only Settings" in refused.stderr, refused.stderr.strip())
    stopped = shell("StopNow", "org.example.Counter")
    idle = wait(lambda: agent("org.example.Counter")["state"] == "idle" and agent("org.example.Counter"), 20)
    record("Stop Now from the dock stops the agent until its next wake", bool(idle) and idle["last-exit"] == "stopped",
           idle and {k: idle[k] for k in ("state", "last-exit", "last-run")})
    listing = busctl("call", "org.projectluma.Background1", "/org/projectluma/Background1",
                     "org.projectluma.Background1", "ListAgents")
    summary = [{k: item[k]["data"] for k in ("app-id", "state", "decision", "memory", "cpu-percent", "last-run")}
               for item in listing["data"][0]]
    record("ListAgents reports every agent with state, decision and resource use", len(summary) >= 4, summary)

    passed = sum(item["pass"] for item in RESULTS)
    output = Path("/tmp/bga-integration-results.json")
    output.write_text(json.dumps({"passed": passed, "total": len(RESULTS), "checks": RESULTS}, indent=2, default=str))
    print(f"\n{passed}/{len(RESULTS)} checks passed; record in {output}")
    return 0 if passed == len(RESULTS) else 1


def values_safe(app: str):
    try:
        return values(app)["count"]
    except Exception:  # noqa: BLE001 - a frozen agent cannot answer
        return None


def cgroup_events_history(unit: str) -> list[str]:
    try:
        return cgroup_file(unit, "memory.events").splitlines()
    except OSError:
        return []


if __name__ == "__main__":
    raise SystemExit(main())
