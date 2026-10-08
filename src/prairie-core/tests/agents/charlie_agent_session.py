#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Charlie's background mail agent in a real user session (ADR-033).

    charlie_agent_session.py --evidence DIR --charlie-tests /path/to/native-charlie-luma/tests

Needs luma-charlie installed. With Charlie's test IMAP server as a session
service and one account whose password is in the session's Secret Service:
starts at login from its autostart entry inside its unit and slice; loads no
GTK; one agent however it is started; new mail notifies with sender and subject
(and only "New mail" on a locked screen); Mark as Read sets \\Seen on the server;
the unread count is published; mail that arrives during a simulated suspend is
announced after resume; memory within the mail category's limits.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session import Failure, Session, mib  # noqa: E402

APP = "org.projectluma.Charlie"
AGENT = "org.projectluma.Charlie.Agent"
UNIT = "app-org.projectluma.Charlie-agent.service"  # replaced in main() for the unmanaged fallback
HERE = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--charlie-tests", type=Path, required=True)
    options = parser.parse_args()
    session = Session(options.evidence)
    extra: dict = {"managed": session.managed}
    global UNIT
    UNIT = session.agent_unit(APP)
    state = options.evidence / "imap"
    account_id = "mail-agent-session"

    def deliver(name: str, sender: str, subject: str) -> None:
        (state / "inbox").mkdir(parents=True, exist_ok=True)
        (state / "inbox" / f".{name}").write_text(json.dumps({"sender": sender, "subject": subject}))
        (state / "inbox" / f".{name}").rename(state / "inbox" / f"{name}.json")

    def flags() -> dict:
        try:
            return json.loads((state / "flags.json").read_text())
        except (OSError, ValueError):
            return {}

    def agent_pid() -> int:
        pids = [pid for pid in session.unit_pids(UNIT) if "--agent" in session.cmdline(pid)]
        return pids[0] if len(pids) == 1 else 0

    try:
        session.systemctl("stop", UNIT, "luma-test-imap", check=False)
        session.run("systemd-run", "--user", "--quiet", "--unit=luma-test-imap", sys.executable,
                    str(HERE / "charlie_imap_service.py"), "--charlie-tests", str(options.charlie_tests),
                    "--state", str(state))
        server = session.wait(lambda: (state / "server.json").exists() and json.loads((state / "server.json").read_text()),
                              "IMAP server", 30)
        session.systemctl("set-environment", f"SSL_CERT_FILE={server['ca']}")
        from charlie_luma.model import Account, ServerConfig
        from charlie_luma.secrets import SecretStore
        from charlie_luma.store import MailStore
        data = Path.home() / ".local/share/charlie"
        data.mkdir(parents=True, exist_ok=True, mode=0o700)
        store = MailStore(data / "mail.db")
        for account in store.accounts():
            store.delete_account(account.id)
        store.upsert_account(Account(account_id, "Reader", server["username"]))
        store.upsert_server_config(account_id, ServerConfig("localhost", server["port"], "localhost", 465, server["username"]))
        store.close()
        SecretStore().store(account_id, "password", server["password"])
        (Path.home() / ".local/state/charlie/agent.json").unlink(missing_ok=True)
        deliver("old", "Old Sender <old@example.test>", "Already here before the agent")
        time.sleep(1)

        # 1. Login.
        session.login()
        session.wait(lambda: session.unit_active(UNIT) and session.has_owner(AGENT), "Charlie agent after login", 40)
        pid = session.wait(agent_pid, "one agent process")
        session.step("starts at login", True, unit=UNIT, pid=pid)
        session.limits_step(UNIT, "mail", memory_max_mib=256)
        session.step("loads no GTK or WebKit", not session.loads_gtk(pid) and "webkit" not in Path(f"/proc/{pid}/maps").read_text())
        session.wait(lambda: session.values(AGENT).get("unread-count") == 1, "baseline unread count", 40)
        time.sleep(3)
        session.step("mail already there at the first start is counted, not announced",
                     not any(r.get("event") == "notify" and "Already here" in r.get("body", "")
                             for r in session.notification_records()), values=session.values(AGENT))

        # 2. Single instance.
        for arguments in (("--agent",), ("--agent", "--autostart")):
            for _ in range(3):
                session.run("org.projectluma.Charlie", *arguments, timeout=40, check=False)
        session.step("single instance", agent_pid() == pid and flags().get("logins", 0) >= 1)

        # 3. New mail notifies.
        mark = session.mark()
        deliver("new1", "Grace Hopper <grace@example.test>", "Compiler notes")
        record = session.wait_notification(lambda r: r["body"] == "Compiler notes", "new mail notification", 60, mark)
        actions = dict(zip(record["actions"][0::2], record["actions"][1::2]))
        session.step("new mail notifies with sender and subject", record["summary"] == "Grace Hopper"
                     and record["hints"].get("desktop-entry") == "org.projectluma.Charlie"
                     and record["hints"].get("category") == "email.arrived" and "mark-read" in actions,
                     summary=record["summary"], actions=actions)
        session.wait(lambda: session.values(AGENT).get("unread-count") == 2, "unread count 2", 20)
        session.step("publishes the unread count", True, values=session.values(AGENT))

        # 4. Locked screen.
        session.lock(True)
        mark = session.mark()
        deliver("new2", "Private Person <private@example.test>", "Medical results")
        locked = session.wait_notification(lambda r: r["locked"], "mail while locked", 60, mark)
        session.step("locked screen shows only that there is mail", "Medical" not in json.dumps(locked)
                     and "Private Person" not in json.dumps(locked), summary=locked["summary"], body=locked["body"])
        session.lock(False)

        # 5. Mark as Read reaches the server.
        session.invoke(record["id"], "mark-read")
        session.wait(lambda: any("\\Seen" in value for value in flags().get("flags", {}).values()), "\\Seen on the server", 30)
        session.step("Mark as Read sets \\Seen on the server and withdraws it",
                     bool(session.wait(lambda: [r for r in session.notification_records()
                                                if r["event"] == "close" and r["id"] == record["id"]], "withdrawn", 15)))

        # 6. Mail during a simulated suspend.
        session.suspend()
        session.systemctl("freeze", UNIT)
        deliver("asleep", "Night Owl <owl@example.test>", "Sent while you slept")
        time.sleep(8)
        mark = session.mark()
        resumed = time.time()
        session.systemctl("thaw", UNIT)
        session.resume()
        session.wait_notification(lambda r: r["body"] == "Sent while you slept", "mail from while asleep", 60, mark)
        session.step("mail that arrived during a simulated suspend is announced after resume", True,
                     seconds_after_resume=round(time.time() - resumed, 1))
        session.restore_logind()

        # 7. Memory.
        time.sleep(10)
        memory = session.memory(UNIT)
        extra["smaps"] = session.smaps(pid)
        extra["memory"] = {"agent_rss_mib": mib(session.rss(pid)), "unit_current_mib": mib(memory["current"]),
                           "unit_peak_mib": mib(memory["peak"])}
        session.step("memory at rest within the mail category's limits", (memory["peak"] or 0) < 256 * 1048576,
                     **extra["memory"])
    except Exception as failure:
        if not isinstance(failure, Failure) or not session.results or session.results[-1]["ok"]:
            session.failed(failure)
    finally:
        session.restore_logind()
        session.systemctl("thaw", UNIT, check=False)
        session.systemctl("stop", UNIT, "luma-test-imap", check=False)
        session.systemctl("unset-environment", "SSL_CERT_FILE", check=False)
    summary = session.summary("charlie", extra)
    print(json.dumps({"passed": summary["passed"], **extra}))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
