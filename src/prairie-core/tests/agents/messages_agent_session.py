#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Messages' background agent in a real user session (ADR-033).

Needs: prairie-core-apps installed (the agent unit, D-Bus service and autostart
entry), notification_stub.py and an unlocked Secret Service running in the
session, sudo for fake_login1.py, xvfb-run for the window. Run as the session
user:

    messages_agent_session.py --evidence DIR

Proves, with the fake luma-messages-bridge/1 helper standing in for a network:
starts at login from the autostart entry inside its systemd unit and slice;
loads no GTK; one agent however often it is started; announces a message with
the window closed (Reply, reaction, Mark as Read, desktop-entry, inline reply);
keeps details off a locked screen and restores them on unlock; Mark as Read and
the reaction reach the network; the unread count is published; the window runs
as a client without a second helper and quits without stopping the agent; a
message that arrives during a simulated suspend is announced after resume; a
crash is restarted; memory stays within the unit's limits.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session import Failure, Session, mib  # noqa: E402

from gi.repository import GLib  # noqa: E402

APP = "org.projectluma.Messages"
AGENT = "org.projectluma.Messages.Agent"
API = "org.projectluma.Messages.Agent1"
API_PATH = "/org/projectluma/Messages/Agent"
FAKE = Path(__file__).resolve().parents[1] / "fake_messages_bridge.py"


def seed_account(helpers: Path) -> tuple[str, Path]:
    from prairie_apps.messages_accounts import Account, Accounts, AccountSecrets

    helpers.mkdir(parents=True, exist_ok=True)
    helper = helpers / "gmessages"
    helper.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE} \"$@\"\n")
    helper.chmod(0o755)
    accounts = Accounts()
    for existing in accounts.list(include_pending=True):
        accounts.remove(existing.id)
    account = accounts.create("gmessages")
    accounts.save(Account(account.id, "gmessages", "Fake Person", "+15550100", account.created, False))
    AccountSecrets().set(account.id, "Fake Person", "fake-session-token")
    return account.id, accounts.directory(account.id)


def deliver(directory: Path, message_id: str, text: str, **extra) -> None:
    inbox = directory / "fake-inbox"
    inbox.mkdir(exist_ok=True)
    temporary = inbox / f".{message_id}.tmp"
    temporary.write_text(json.dumps({"id": message_id, "text": text, **extra}))
    temporary.rename(inbox / f"{message_id}.json")


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def agent_pid(session: Session) -> int:
    pids = [pid for pid in session.unit_pids(session.agent_unit(APP)) if "--agent" in session.cmdline(pid)]
    return pids[0] if len(pids) == 1 else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--helpers", type=Path, default=Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "luma-test-helpers")
    options = parser.parse_args()
    session = Session(options.evidence)
    extra: dict = {"managed": session.managed}
    UNIT = session.agent_unit(APP)
    try:
        session.systemctl("stop", UNIT, check=False)
        account_id, directory = seed_account(options.helpers)
        session.systemctl("set-environment", f"LUMA_MESSAGES_HELPER_DIR={options.helpers}")
        session.note(f"seeded one fake Google Messages account in {directory.name}")

        # 1. Login starts the agent from its autostart entry, inside its unit.
        started = time.monotonic()
        session.login()
        session.wait(lambda: session.unit_active(UNIT) and session.has_owner(AGENT), "agent running after login", 30)
        pid = session.wait(lambda: agent_pid(session), "one agent process in the unit")
        autostart = session.systemctl("list-units", "--all", "--no-legend", "app-*Messages.Agent@autostart.service",
                                      check=False)
        session.step("starts at login", True, seconds=round(time.monotonic() - started, 1), unit=UNIT,
                     pid=pid, slice=session.unit_property(UNIT, "Slice"), autostart_unit=autostart,
                     owner=session.owner(AGENT), values=session.values(AGENT))
        session.limits_step(UNIT, "communication")
        session.step("loads no GTK", not session.loads_gtk(pid), cmdline=session.cmdline(pid))

        # 2. The account connects in the agent; there is exactly one helper.
        def connected():
            try:
                state = json.loads(session.call(AGENT, API_PATH, API, "Accounts")[0])
            except Exception:
                return False
            return any(item["id"] == account_id and item["state"] == "ready" for item in state)
        session.wait(connected, "account connected in the agent", 40)
        helpers = session.processes(str(FAKE))
        session.step("account connected with one helper, in the agent's cgroup", len(helpers) == 1
                     and helpers[0] in session.unit_pids(UNIT), helpers=helpers)

        # 3. Single instance, however it is started.
        before = session.unit_pids(UNIT)
        for arguments in (("--agent",), ("--agent", "--autostart")):
            for _ in range(3):
                result = session.run("prairie-messages", *arguments, timeout=30, check=False)
        time.sleep(1)
        session.step("single instance: six more starts add nothing",
                     session.unit_pids(UNIT) == before and agent_pid(session) == pid
                     and len(session.processes(str(FAKE))) == 1, pids=session.unit_pids(UNIT))

        # 4. A message with every window closed is announced.
        mark = session.mark()
        deliver(directory, "live1", "Are you free tonight?")
        record = session.wait_notification(lambda r: r["body"] == "Are you free tonight?", "live message", 20, mark)
        actions = dict(zip(record["actions"][0::2], record["actions"][1::2]))
        hints = record["hints"]
        session.step("announces a message with the window closed",
                     record["summary"] == "Fake Friend" and hints.get("desktop-entry") == "org.projectluma.Messages"
                     and hints.get("category") == "im.received" and {"default", "reply", "mark-read", "react"} <= set(actions)
                     and bool(hints.get("x-luma-inline-reply-token")) and record["sender"] == session.owner(AGENT),
                     summary=record["summary"], actions=actions, hint_keys=sorted(hints))
        unread = session.wait(lambda: session.values(AGENT).get("unread-count"), "unread count published", 10)
        session.step("publishes the unread count", unread >= 1, values=session.values(AGENT))

        # 5. A locked screen shows only that there is a message; unlocking restores it silently.
        session.lock(True)
        mark = session.mark()
        deliver(directory, "live2", "The door code is 4512")
        locked = session.wait_notification(lambda r: r["locked"], "notification while locked", 20, mark)
        session.step("locked screen: no sender or text", locked["summary"] == "Messages"
                     and "4512" not in locked["body"] and "Fake Friend" not in json.dumps(locked)
                     and "x-luma-inline-reply-token" not in locked["hints"], summary=locked["summary"], body=locked["body"])
        mark = session.mark()
        session.lock(False)
        restored = session.wait_notification(lambda r: r["replaces"] == locked["id"], "restored after unlock", 10, mark)
        session.step("unlock restores the real notification without sound", restored["body"] == "The door code is 4512"
                     and restored["hints"].get("suppress-sound") is True, summary=restored["summary"])

        # 6. Notification buttons act on the network.
        session.invoke(record["id"], "react")
        reacted = session.wait(lambda: jsonl(directory / "reacted.jsonl"), "reaction sent", 20)
        session.step("reaction button reacts on the network", reacted[-1].get("emoji") == "👍", sent=reacted[-1])
        read_before = len(jsonl(directory / "read.jsonl"))
        session.invoke(restored["id"], "mark-read")
        session.wait(lambda: len(jsonl(directory / "read.jsonl")) > read_before, "read receipt sent", 20)
        closed = session.wait(lambda: [r for r in session.notification_records()
                                       if r["event"] == "close" and r["id"] == restored["id"]], "notification withdrawn", 10)
        count = session.wait(lambda: (session.values(AGENT).get("unread-count", 99) == 0) or None, "unread count 0", 10)
        session.step("Mark as Read marks it read, tells the network and withdraws it", bool(closed), unread=count)

        # 7. The window is a client: no second helper, and quitting it leaves the agent.
        pid_before = agent_pid(session)
        ui = session.launch_window(APP, "window1", "prairie-messages", env={"GSK_RENDERER": "cairo"})
        session.wait(lambda: session.has_owner("org.projectluma.Messages"), "Messages window running", 60)
        time.sleep(4)
        window_pids = session.unit_pids(ui)
        window_rss = max((session.rss(p) for p in window_pids if "prairie-messages" in session.cmdline(p)), default=0)
        session.step("window opens as a client: same agent, still one helper",
                     agent_pid(session) == pid_before and len(session.processes(str(FAKE))) == 1,
                     window_rss_mib=mib(window_rss), helpers=session.processes(str(FAKE)))
        read_before = len(jsonl(directory / "read.jsonl"))
        session.run("prairie-messages", f"--conversation=account:{account_id}|chat.one", timeout=30)
        session.wait(lambda: len(jsonl(directory / "read.jsonl")) > read_before, "window's open conversation reached the agent", 20)
        session.step("opening a conversation in the window goes through the agent", True)
        session.systemctl("stop", ui, check=False)
        session.wait(lambda: not session.has_owner("org.projectluma.Messages"), "window quit", 20)
        time.sleep(1)
        mark = session.mark()
        deliver(directory, "live3", "Still there?")
        session.wait_notification(lambda r: r["body"] == "Still there?", "message after the window quit", 20, mark)
        session.step("window quit: agent keeps running and announcing", agent_pid(session) == pid_before
                     and len(session.processes(str(FAKE))) == 1)

        # 8. Suspend: a message that arrives while asleep is announced after resume.
        session.suspend()
        session.systemctl("freeze", UNIT)
        backlog = directory / "fake-backlog.json"
        items = json.loads(backlog.read_text()) if backlog.exists() else []
        items.append({"id": "asleep1", "conversation": "chat.one", "sender": {"id": "friend", "name": "Fake Friend"},
                      "outgoing": False, "text": "Sent while you were asleep", "time": int(time.time()) + 5,
                      "state": "received", "attachments": []})
        backlog.write_text(json.dumps(items))
        time.sleep(8)
        mark = session.mark()
        session.systemctl("thaw", UNIT)
        session.resume()
        caught = session.wait_notification(lambda r: r["body"] == "Sent while you were asleep",
                                           "message from while asleep announced after resume", 60, mark)
        session.step("catches up after a simulated suspend", caught["summary"] == "Fake Friend",
                     journal=session.journal_grep(UNIT, "woke: resume"))

        # 9. A crash is restarted (by the unit luma-background generated) and the account reconnects.
        if session.managed:
            crashed = agent_pid(session)
            os.kill(crashed, signal.SIGKILL)
            session.wait(lambda: agent_pid(session) not in (0, crashed) and session.has_owner(AGENT), "restarted after a crash", 40)
            session.wait(connected, "account connected again after the restart", 40)
            session.step("restarted after a crash", len(session.processes(str(FAKE))) == 1, pid=agent_pid(session))

        # 10. Memory at rest.
        time.sleep(10)
        pid = agent_pid(session)
        memory = session.memory(UNIT)
        helper_rss = sum(session.rss(p) for p in session.processes(str(FAKE)))
        extra["smaps"] = session.smaps(pid)
        extra["memory"] = {"agent_rss_mib": mib(session.rss(pid)), "helper_rss_mib": mib(helper_rss),
                           "unit_current_mib": mib(memory["current"]), "unit_peak_mib": mib(memory["peak"])}
        session.step("memory at rest within the unit's limits", (memory["peak"] or 0) < 128 * 1048576
                     and session.rss(pid) < 64 * 1048576, **extra["memory"])
        # 11. Removing the last account signs out; with nothing to keep, the agent stops,
        # and asking it anything starts it again.
        session.call(AGENT, API_PATH, API, "RemoveAccount", GLib.Variant("(sb)", (account_id, True)), timeout=60000)
        session.wait(lambda: not directory.exists(), "account removed", 30)
        signed_out = (directory.parent / "logged-out.json").exists()
        session.wait(lambda: not session.has_owner(AGENT), "agent stops with no accounts", 120)
        result = session.unit_property(UNIT, "Result")
        if session.managed:
            window = session.launch_window(APP, "window2", "prairie-messages", env={"GSK_RENDERER": "cairo"})
            session.wait(lambda: session.has_owner(AGENT), "the window asks luma-background for the agent", 60)
            session.step("with no accounts the agent stops cleanly, and opening Messages asks for it again",
                         signed_out and result == "success" and session.unit_active(UNIT), result=result)
            session.systemctl("stop", window, check=False)
        else:
            session.step("with no accounts the agent stops cleanly", signed_out and result in {"success", ""},
                         result=result)
    except Exception as failure:  # every failure is evidence, including an unexpected exception
        if not isinstance(failure, Failure) or not session.results or session.results[-1]["ok"]:
            session.failed(failure)
    finally:
        session.restore_logind()
        session.systemctl("thaw", UNIT, check=False)
        session.systemctl("stop", f"app-luma-{APP}@window1.service", f"app-luma-{APP}@window2.service", check=False)
    summary = session.summary("messages", extra)
    print(json.dumps({"passed": summary["passed"], **extra}))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
