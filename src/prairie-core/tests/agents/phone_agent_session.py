#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Phone's background agent for a phone paired through Luma Connect, in a real user session (ADR-033).

    phone_agent_session.py --evidence DIR

With fake_connect_calls.py standing in for the Connect daemon's relay: at login
the agent starts and, with no phone chosen for calls, loads nothing of Luma
Connect; choosing one makes it listen; an incoming call rings as a call notification with
Answer and Decline; Answer reaches the phone and opens Phone in its own scope;
a call that stops ringing unanswered becomes a Missed Call with Call Back and
Message; a call that starts ringing during a simulated suspend rings on resume;
one agent however often it is started; forgetting the phone stops the
listening; memory within limits. Handset (IMS) services are not involved.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session import Failure, Session, mib  # noqa: E402

from gi.repository import GLib  # noqa: E402

APP = "org.projectluma.Phone"
AGENT = "org.projectluma.Phone.Agent"
UNIT = "app-org.projectluma.Phone-agent.service"  # replaced in main() for the unmanaged fallback
HERE = Path(__file__).resolve().parent


def row(call_id: str, phase: str, number: str = "+15550101011") -> dict:
    return {"id": call_id, "generation": call_id, "address": number, "direction": "incoming", "phase": phase,
            "started_at": int(time.time()), "answered_at": 0}


def agent_pid(session: Session) -> int:
    pids = [pid for pid in session.processes("prairie-phone --agent") if "python" in session.cmdline(pid)]
    return pids[0] if len(pids) == 1 else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    options = parser.parse_args()
    session = Session(options.evidence)
    extra: dict = {"managed": session.managed}
    global UNIT
    UNIT = session.agent_unit(APP)
    connect_units = ("app-org.projectluma.Connect-agent.service", "luma-connect.service",
                     "app-org.projectluma.Connect.Agent@autostart.service")
    selection = Path.home() / ".config/luma-connect/calls-phone.json"
    calls_log = options.evidence / "calls.jsonl"
    account, epoch = "acct-test", secrets.token_hex(16)

    def set_calls(*rows):
        session.call("org.projectluma.Connect1", "/org/projectluma/Connect", "org.projectluma.Test.Calls", "SetCalls",
                     GLib.Variant("(s)", (json.dumps(list(rows)),)))

    def controls():
        return [json.loads(line) for line in calls_log.read_text().splitlines()] if calls_log.exists() else []

    try:
        session.systemctl("stop", UNIT, check=False)
        selection.unlink(missing_ok=True)
        # The real Connect daemon is held off; the fake answers for the phone.
        session.systemctl("stop", *connect_units, check=False)
        session.systemctl("mask", "--runtime", *connect_units, check=False)
        session.systemctl("stop", "luma-test-connect", check=False)
        session.run("systemd-run", "--user", "--quiet", "--unit=luma-test-connect", f"--setenv=LUMA_TEST_CALLS_LOG={calls_log}",
                    f"--setenv=LUMA_TEST_ACCOUNT={account}", f"--setenv=LUMA_TEST_EPOCH={epoch}",
                    sys.executable, str(HERE / "fake_connect_calls.py"))
        session.wait(lambda: session.has_owner("org.projectluma.Connect1"), "fake Connect1", 20)

        # 1. Login without a phone chosen for calls: the agent waits, cheaply.
        session.login()
        session.wait(lambda: session.has_owner(AGENT) and agent_pid(session), "agent after login", 40)
        pid = agent_pid(session)
        idle_rss = session.smaps(pid)
        maps = Path(f"/proc/{pid}/maps").read_text()
        session.step("no phone chosen: the agent waits without loading Luma Connect", not session.loads_gtk(pid)
                     and "cryptography" not in maps and session.values(AGENT).get("call-state") == "idle",
                     unit=UNIT, idle=idle_rss)
        session.limits_step(UNIT, "communication")
        extra["idle_without_phone"] = idle_rss

        # 2. Choosing a phone makes it listen.
        selection.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(selection.parent, 0o700)
        selection.write_text(json.dumps({"version": 1, "phone": {
            "peer": secrets.token_hex(32), "epoch": epoch, "account": account, "label": "Pixel 9",
            "address": "192.0.2.10", "port": 7443, "carrier": "relay"}}))
        os.chmod(selection, 0o600)
        session.wait(lambda: session.journal_grep(UNIT, "Listening") or session.run(
            "gdbus", "call", "--session", "--dest", "org.projectluma.Connect1", "--object-path", "/org/projectluma/Connect",
            "--method", "org.freedesktop.DBus.Peer.Ping", check=False).returncode == 0, "fake Connect reachable", 10)
        time.sleep(2)
        session.step("choosing a phone for calls: the same agent listens", agent_pid(session) == pid, pid=pid)

        # 3. An incoming call rings.
        mark = session.mark()
        set_calls(row("a" * 32, "incoming"))
        ringing = session.wait_notification(lambda r: r["hints"].get("category") == "call.incoming", "incoming call", 20, mark)
        actions = dict(zip(ringing["actions"][0::2], ringing["actions"][1::2]))
        session.step("incoming call notification with Answer and Decline",
                     {"answer", "decline", "default"} <= set(actions) and ringing["hints"].get("urgency") == 2
                     and ringing["body"] == "Incoming call on Pixel 9" and ringing["hints"].get("resident") is True,
                     summary=ringing["summary"], actions=actions)
        session.wait(lambda: session.values(AGENT).get("call-state") == "ringing", "CallState ringing", 10)

        # 4. Answer reaches the phone and opens Phone in its own scope.
        session.invoke(ringing["id"], "answer")
        session.wait(lambda: any(item.get("control") == "answer" for item in controls()), "answer sent to the phone", 20)
        session.wait(lambda: session.has_owner("org.projectluma.Phone"), "Phone window", 60)
        window = [p for p in session.processes("prairie-phone") if "--agent" not in session.cmdline(p)]
        cgroup = Path(f"/proc/{window[0]}/cgroup").read_text().strip().rsplit("/", 1)[-1] if window else ""
        session.step("Answer answers on the phone and opens Phone in its own scope",
                     bool(window) and cgroup.startswith("app-luma-org.projectluma.Phone-"), cgroup=cgroup)
        set_calls()
        time.sleep(2)
        session.run("pkill", "-f", "[p]rairie-phone$", check=False)
        session.wait(lambda: not session.has_owner("org.projectluma.Phone"), "Phone window closed", 30)
        session.step("an answered call is not a missed call",
                     not any(r.get("event") == "notify" and r.get("summary") == "Missed call"
                             for r in session.notification_records()[mark:]))

        # 5. Unanswered: missed.
        mark = session.mark()
        set_calls(row("b" * 32, "incoming"))
        session.wait_notification(lambda r: r["hints"].get("category") == "call.incoming", "second call", 20, mark)
        set_calls()
        missed = session.wait_notification(lambda r: r["summary"] == "Missed call", "missed call", 20, mark)
        actions = dict(zip(missed["actions"][0::2], missed["actions"][1::2]))
        session.step("unanswered call becomes a Missed Call with Call Back and Message",
                     {"call-back", "message"} <= set(actions), body=missed["body"],
                     missed=session.values(AGENT).get("missed-calls"))

        # 6. A call that starts ringing while asleep rings on resume.
        session.suspend()
        session.systemctl("freeze", UNIT)
        mark = session.mark()
        set_calls(row("c" * 32, "incoming"))
        time.sleep(5)
        session.systemctl("thaw", UNIT)
        session.resume()
        session.wait_notification(lambda r: r["hints"].get("category") == "call.incoming", "call after resume", 20, mark)
        session.step("a call ringing across a simulated suspend rings on resume", True)
        session.restore_logind()
        set_calls()

        # 7. Single instance.
        for arguments in (("--agent",), ("--agent", "--autostart")):
            for _ in range(3):
                session.run("prairie-phone", *arguments, timeout=30, check=False)
        session.step("single instance", agent_pid(session) == pid)

        # 8. Memory at rest.
        time.sleep(8)
        memory = session.memory(UNIT)
        extra["smaps"] = session.smaps(pid)
        extra["memory"] = {"agent_rss_mib": mib(session.rss(pid)), "unit_current_mib": mib(memory["current"]),
                           "unit_peak_mib": mib(memory["peak"])}
        session.step("memory at rest within limits", (memory["peak"] or 0) < 128 * 1048576
                     and session.rss(pid) < 64 * 1048576, **extra["memory"])

        # 9. Forgetting the phone stops listening.
        selection.unlink()
        time.sleep(2)
        mark = session.mark()
        set_calls(row("d" * 32, "incoming"))
        time.sleep(6)
        session.step("forgetting the phone stops listening", not [r for r in session.notification_records()[mark:]
                                                                  if r.get("event") == "notify"] and agent_pid(session) == pid)
        set_calls()
    except Exception as failure:
        if not isinstance(failure, Failure) or not session.results or session.results[-1]["ok"]:
            session.failed(failure)
    finally:
        session.restore_logind()
        session.systemctl("thaw", UNIT, check=False)
        session.systemctl("stop", "luma-test-connect", check=False)
        session.systemctl("unmask", "--runtime", *connect_units, check=False)
        selection.unlink(missing_ok=True)
    summary = session.summary("phone", extra)
    print(json.dumps({"passed": summary["passed"], **extra}))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
