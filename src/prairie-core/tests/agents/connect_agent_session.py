#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Luma Connect's daemon as an ADR-033 communication agent in a real user session.

    connect_agent_session.py --evidence DIR

Signed out and unpaired (no hub or phone is needed for what this proves): the
daemon starts at login as the agent (luma-background's unit, or the autostart
entry without the service) and owns org.projectluma.Connect1 and
org.projectluma.Connect.Agent with its values published; without the agent,
D-Bus activation of Connect1 runs the daemon in luma-connect.service
(SystemdService=), and the agent takes the name over from it; one daemon
however it is started; on resume an enrolled device's hub sync starts at once;
the sync timer is wall-clock and persistent; memory at rest is recorded.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session import Failure, Session, mib  # noqa: E402

BUS = "org.projectluma.Connect1"
APP = "org.projectluma.Connect"
AGENT = "org.projectluma.Connect.Agent"
ON_DEMAND = "luma-connect.service"


def daemon_pids(session: Session) -> list[int]:
    return [pid for pid in session.processes("luma-connect-daemon") if "python" in session.cmdline(pid)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    options = parser.parse_args()
    session = Session(options.evidence)
    extra: dict = {"managed": session.managed}
    unit = session.agent_unit(APP)
    enrolled = Path.home() / ".local/share/luma/connect/device.json"
    try:
        session.systemctl("stop", unit, ON_DEMAND, check=False)
        enrolled.unlink(missing_ok=True)

        # 1. Login.
        session.login()
        session.wait(lambda: session.has_owner(BUS) and session.has_owner(AGENT), "Connect daemon and agent name after login", 60)
        pids = daemon_pids(session)
        values = session.wait(lambda: session.values(AGENT) or None, "published values", 15)
        session.step("starts at login as the agent and owns both names",
                     len(pids) == 1 and pids[0] in session.unit_pids(unit) and "--agent" in session.cmdline(pids[0]),
                     pids=pids, unit=unit, values=values)
        session.limits_step(unit, "communication")
        session.step("loads no GTK", not session.loads_gtk(pids[0]))

        # 2. Without the agent, D-Bus activation starts the on-demand daemon under
        # systemd (SystemdService=); the agent takes the name over when it starts.
        if session.managed:
            session.systemctl("stop", unit)
        else:
            session.run("pkill", "-f", "[l]uma-connect-daemon --agent", check=False)
        session.wait(lambda: not session.has_owner(BUS), "daemon stopped", 30)
        session.run("gdbus", "call", "--session", "--dest", BUS, "--object-path", "/org/projectluma/Connect",
                    "--method", "org.projectluma.Connect1.GetQuickState", timeout=60)
        on_demand = daemon_pids(session)
        session.step("D-Bus activation runs the daemon in luma-connect.service",
                     session.unit_active(ON_DEMAND) and len(on_demand) == 1 and on_demand[0] in session.unit_pids(ON_DEMAND),
                     pids=on_demand)
        if session.managed:
            session.systemctl("start", unit)
        else:
            session.run("systemd-run", "--user", "--quiet", "--collect", "--unit=luma-test-connect-agent",
                        "luma-connect-daemon", "--agent")
            unit = "luma-test-connect-agent.service"
        session.wait(lambda: session.has_owner(AGENT) and not session.unit_active(ON_DEMAND), "agent takes Connect1 over", 40)
        session.step("the agent takes the name over and the on-demand daemon exits cleanly",
                     len(daemon_pids(session)) == 1 and session.unit_property(ON_DEMAND, "Result") == "success",
                     result=session.unit_property(ON_DEMAND, "Result"))

        # 3. Single instance.
        pid = daemon_pids(session)[0]
        for arguments in (("--agent",), ("--agent", "--autostart")):
            for _ in range(2):
                session.run("luma-connect-daemon", *arguments, timeout=30, check=False)
        session.run("gdbus", "call", "--session", "--dest", BUS, "--object-path", "/org/projectluma/Connect",
                    "--method", "org.projectluma.Connect1.GetQuickState", timeout=60)
        session.step("single instance", daemon_pids(session) == [pid] and not session.unit_active(ON_DEMAND))

        # 4. Resume starts hub sync at once for an enrolled device.
        enrolled.parent.mkdir(parents=True, exist_ok=True)
        enrolled.write_text(json.dumps({"test": "enrolled marker; no hub is reachable"}))
        session.systemctl("stop", "luma-connect-sync.service", check=False)
        before = session.unit_property("luma-connect-sync.service", "InactiveExitTimestampMonotonic")
        session.suspend()
        session.resume()
        started = session.wait(lambda: session.unit_property("luma-connect-sync.service", "InactiveExitTimestampMonotonic")
                               != before, "sync started after resume", 30)
        session.step("resume starts Luma Hub sync at once", bool(started),
                     sync_state=session.unit_property("luma-connect-sync.service", "ActiveState"),
                     journal=session.journal_grep(unit, "woke: resume"))
        session.restore_logind()
        timer = Path("/usr/lib/systemd/user/luma-connect-sync.timer").read_text()
        session.step("sync timer is wall-clock and persistent", "OnCalendar=" in timer and "Persistent=true" in timer)

        # 5. Memory at rest.
        time.sleep(10)
        memory = session.memory(unit)
        extra["smaps"] = session.smaps(pid)
        extra["memory"] = {"daemon_rss_mib": mib(session.rss(pid)), "unit_current_mib": mib(memory["current"]),
                           "unit_peak_mib": mib(memory["peak"])}
        session.step("memory at rest within the communication limit", (memory["peak"] or 0) < 128 * 1048576,
                     **extra["memory"])
    except Exception as failure:
        if not isinstance(failure, Failure) or not session.results or session.results[-1]["ok"]:
            session.failed(failure)
    finally:
        session.restore_logind()
        enrolled.unlink(missing_ok=True)
        session.systemctl("stop", "luma-connect-sync.service", "luma-connect-sync-watch.service",
                          "luma-test-connect-agent", check=False)
    summary = session.summary("connect", extra)
    print(json.dumps({"passed": summary["passed"], **extra}))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
