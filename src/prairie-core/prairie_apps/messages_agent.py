# SPDX-License-Identifier: Apache-2.0

"""Messages' background agent (ADR-033): accounts stay connected with the window closed.

``prairie-messages --agent`` owns every network account's bridge helper
(ADR-023): it keeps each connected across sleep and network changes, stores
what arrives, downloads pictures on the provider's media queue, and announces
new messages with Reply, a quick reaction and Mark as Read. It publishes the
unread count for live extensions and the dock.

It has no windows and loads no GTK. When the Messages window is open it is a
client of this agent (``RemoteAccountProvider``): sends, reactions, retries and
"I'm looking at this conversation" go over D-Bus to the one process that holds
each helper, so an account is never connected twice. If the agent is turned
off, the window runs the helpers itself while it is open, as before.

This device's own SMS and MMS keep their existing service
(prairie-messages-daemon) on handsets; this agent counts their unread messages
but does not own the modem.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from gi.repository import Gio, GLib

from .background_agent import Agent, AgentInfo, Notifier, configure_logging, ensure_agent, launch_app
from .messages_accounts import (
    Account,
    AccountProvider,
    Accounts,
    AccountStore,
    KeyringLocked,
    helper_directory,
    network,
    remove_account,
)
from .messages_reply import ReplyEndpoint, ReplyError, validate_reply
from .messages_preferences import ConversationPreferences

LOG = logging.getLogger("prairie.messages.agent")

APP_ID = "org.projectluma.Messages"
INFO = AgentInfo(
    APP_ID, "Messages", "communication", ("prairie-messages", "--agent"),
    wake=("login", "network", "resume"),
    publishes=("unread-count", "unread-conversations", "accounts", "badge", "live-extension:org.projectluma.Messages.Unread"),
    purpose="Get messages when Messages is closed",
)
AGENT_NAME = INFO.agent_id
API = "org.projectluma.Messages.Agent1"
API_PATH = "/org/projectluma/Messages/Agent"
QUICK_REACTION = "👍"
# Long bridge calls (a picture upload) outlive D-Bus's 25 s default.
CALL_TIMEOUT_MS = 330_000
IDLE_EXIT_SECONDS = 60

API_XML = f"""<node><interface name="{API}">
  <method name="Accounts"><arg name="state" type="s" direction="out"/></method>
  <method name="Send"><arg name="account" type="s" direction="in"/><arg name="uid" type="s" direction="in"/>
    <arg name="user_token" type="s" direction="in"/><arg name="result" type="s" direction="out"/></method>
  <method name="React"><arg name="account" type="s" direction="in"/><arg name="uid" type="s" direction="in"/>
    <arg name="emoji" type="s" direction="in"/><arg name="result" type="s" direction="out"/></method>
  <method name="ConversationOpened"><arg name="account" type="s" direction="in"/><arg name="address" type="s" direction="in"/></method>
  <method name="ConversationSeen"><arg name="account" type="s" direction="in"/><arg name="address" type="s" direction="in"/></method>
  <method name="SetViewing"><arg name="account" type="s" direction="in"/><arg name="address" type="s" direction="in"/></method>
  <method name="RetryMedia"><arg name="account" type="s" direction="in"/><arg name="uid" type="s" direction="in"/>
    <arg name="part" type="s" direction="in"/></method>
  <method name="Reconnect"><arg name="account" type="s" direction="in"/></method>
  <method name="Reload"/>
  <method name="Luma"><arg name="account" type="s" direction="in"/><arg name="command" type="s" direction="in"/>
    <arg name="args" type="s" direction="in"/><arg name="result" type="s" direction="out"/></method>
  <method name="RemoveAccount"><arg name="account" type="s" direction="in"/><arg name="sign_out" type="b" direction="in"/></method>
  <signal name="AccountChanged"><arg name="account" type="s"/><arg name="state" type="s"/></signal>
</interface></node>"""
from .messages_app_rpc import XML as APPLICATION_MAILBOX_XML
API_XML = API_XML.replace('</interface>', APPLICATION_MAILBOX_XML + '</interface>')


def conversation_key(account_id: str, address: str) -> str:
    """The key Messages uses for a conversation in a network account (MessageService.key)."""
    return f"account:{account_id}|{address}"


class _Secrets:
    """The login keyring, opened on first use (libsecret costs memory we may not need)."""

    def __init__(self) -> None:
        self._inner = None

    def _get(self):
        if self._inner is None:
            from .messages_accounts import AccountSecrets
            try:
                self._inner = AccountSecrets()
            except (ImportError, ValueError):
                raise KeyringLocked("Secret Service unavailable") from None
        return self._inner

    def get(self, account_id):
        return self._get().get(account_id)

    def set(self, account_id, label, value):
        return self._get().set(account_id, label, value)

    def delete(self, account_id):
        try:
            return self._get().delete(account_id)
        except KeyringLocked:
            return None


class AccountReplies:
    """Inline notification replies to a network account's conversation.

    The shell's reply goes through the same ReplyEndpoint as SMS replies; this
    is the sender behind it. A reply is stored as an outgoing message and sent
    by the account's provider, once per request id even if the shell retries.
    """

    def __init__(self, agent: "MessagesAgent") -> None:
        self.agent = agent

    def send(self, target, token, request_id, text, authorized=lambda: True, retry_of=None):
        validate_reply(token, request_id, text)
        account_id, address = target
        service = self.agent.services.get(account_id)
        if service is None:
            raise ReplyError("NoLongerAvailable")
        if not authorized():
            raise ReplyError("Unauthorized")
        uid = hashlib.sha256(f"inline:{token}:{request_id}".encode()).hexdigest()[:32]
        store = AccountStore(service.store_path)
        try:
            try:
                existing = store.message(uid)
            except KeyError:
                existing = None
            if existing is not None:
                return uid, "sent" if existing.state == "sent" else "unknown"
            record = store.add(address, text.strip(), direction="outgoing", uid=uid)
            store.update_state(record.uid, "sending")
            # The person's reply from the notification is the request; it is sent once.
            user_token = store.authorize_send(record.uid)
            store.mark_read(address)
        finally:
            store.close()
        result = service.send_message(uid, user_token)
        return uid, "sent" if result.get("state") in {"sent", "sending"} else result.get("state", "unknown")


class MessagesAgent:
    """Everything the agent does, around one AccountProvider per account."""

    def __init__(self, *, accounts: Accounts | None = None, secrets_store=None, helper_dir: Path | None = None,
                 native_store: Path | None = None) -> None:
        self.accounts = accounts or Accounts()
        self.secrets = secrets_store or _Secrets()
        self.helper_dir = helper_dir or helper_directory()
        self.native_store = native_store
        self.services: dict[str, AccountProvider] = {}
        self.agent: Agent | None = None
        self.notifier: Notifier | None = None
        self.reply_endpoint: ReplyEndpoint | None = None
        self.replies = AccountReplies(self)
        self.worker = ThreadPoolExecutor(max_workers=2, thread_name_prefix="messages-agent-call")
        self._call_slots = threading.BoundedSemaphore(4)
        self.viewing: dict[str, tuple[str, str]] = {}  # caller unique name -> (account, address)
        self._viewer_watch: dict[str, int] = {}
        self._reply_tokens: dict[str, str] = {}
        self._monitors: list[Gio.FileMonitor] = []
        self._count_source = 0
        self._registration = 0
        self._readers: dict[Path, sqlite3.Connection] = {}
        self._idle_source = 0
        self._application_mailbox = None

    # -- lifecycle -----------------------------------------------------------
    def start(self, agent: Agent) -> None:
        self.agent = agent
        connection = agent.connection
        self.notifier = Notifier(connection, APP_ID, "Messages")
        self.reply_endpoint = ReplyEndpoint(connection, self._native_store_path(), self._reply_target,
                                            self._forget_reply_tokens)
        # Replies to accounts go through the providers, not the modem.
        self.reply_endpoint.sender = self.replies
        node = Gio.DBusNodeInfo.new_for_xml(API_XML)
        self._registration = connection.register_object(API_PATH, node.interfaces[0], self._call, None, None)
        self.reload()
        self._watch_store(self._native_store_path().parent)
        self._count_unread()

    def wake(self, event: str) -> None:
        if event in {"network", "resume"}:
            # A connection held open across sleep is often dead without saying so.
            for provider in list(self.services.values()):
                provider.wake()

    def stop(self) -> None:
        if self._application_mailbox is not None:
            self._application_mailbox.close()
            self._application_mailbox = None
        if self._idle_source:
            GLib.source_remove(self._idle_source)
            self._idle_source = 0
        for monitor in self._monitors:
            monitor.cancel()
        if self._count_source:
            GLib.source_remove(self._count_source)
            self._count_source = 0
        for watch in self._viewer_watch.values():
            Gio.bus_unwatch_name(watch)
        for provider in list(self.services.values()):
            provider.close()
        self.services.clear()
        for connection in self._readers.values():
            connection.close()
        self._readers.clear()
        if self.reply_endpoint is not None:
            self.reply_endpoint.close()
        if self.notifier is not None:
            self.notifier.close()
        if self.agent is not None and self._registration:
            self.agent.connection.unregister_object(self._registration)
        self.worker.shutdown(wait=False)

    def _native_store_path(self) -> Path:
        return self.native_store if self.native_store is not None else _default_native_path()

    # -- accounts --------------------------------------------------------------
    def reload(self) -> None:
        """Start a provider for every account on disk and stop the ones removed."""
        try:
            from .messages_luma import ensure_account
            ensure_account(self.accounts, helper_dir=self.helper_dir)
        except (OSError, ValueError) as error:
            LOG.warning("Luma Messages was not added: %s", type(error).__name__)
        present = {account.id: account for account in self.accounts.list()}
        for account_id in [key for key in self.services if key not in present]:
            self.services.pop(account_id).close()
            self._emit_account(account_id, {"state": "removed"})
        for account in present.values():
            if account.id in self.services:
                continue
            try:
                network(account.network)
            except KeyError:
                continue
            provider = AccountProvider(account, self.accounts, secrets=self.secrets, dispatch=GLib.idle_add,
                                       helper_dir=self.helper_dir)
            provider.on_message = lambda address, name, text, account_id=account.id: self._arrived(
                account_id, address, name, text)
            provider.on_outbound_disabled = self._outbound_disabled
            self.services[account.id] = provider
            try:
                store = AccountStore(provider.store_path)
                store.recover_interrupted()
                store.close()
            except Exception:
                LOG.info("Account %s store could not be checked", _hash(account.id))
            self._watch_store(provider.store_path.parent)
            provider.start(lambda state, account_id=account.id: self._state_changed(account_id, state))
        if self.agent is not None:
            self.agent.publish("accounts", len(self.services))
        self._schedule_count()
        self._schedule_idle_exit()

    def _schedule_idle_exit(self) -> None:
        """No accounts and no modem of its own: nothing to keep, so the agent stops.

        A clean exit is not a crash: luma-background starts it again at its
        next wake, and the window asks for it (RequestBackground) when an
        account is added. On a handset it stays for the unread count of its
        texts.
        """
        if self._idle_source:
            GLib.source_remove(self._idle_source)
            self._idle_source = 0
        if not self.services and not (self._application_mailbox and self._application_mailbox.phones) and not _has_modem() and os.environ.get("LUMA_MESSAGES_AGENT_STAY") != "1":
            self._idle_source = GLib.timeout_add_seconds(IDLE_EXIT_SECONDS, self._idle_exit)

    def _idle_exit(self) -> bool:
        self._idle_source = 0
        if not self.services and not (self._application_mailbox and self._application_mailbox.phones) and self.agent is not None and hasattr(self.agent, "quit"):
            LOG.info("No accounts to keep connected; the agent stops until one is added")
            self.agent.quit()
        return GLib.SOURCE_REMOVE

    def _state_changed(self, account_id: str, state: dict) -> None:
        self._emit_account(account_id, self._account_state(account_id))
        if self.agent is not None:
            states = {provider.status.get("state") for provider in self.services.values()}
            if not self.services:
                self.agent.set_state("idle", "No accounts")
            elif states <= {"ready"}:
                self.agent.set_state("running", "Connected")
            else:
                self.agent.set_state("running", "Connecting")

    def _account_state(self, account_id: str) -> dict:
        provider = self.services.get(account_id)
        if provider is None:
            return {"state": "removed"}
        return {"state": provider.status.get("state", "idle"),
                "network_state": provider.status.get("network_state", "connecting"),
                "detail": provider.status.get("detail", ""),
                "capabilities": provider.capabilities}

    def _emit_account(self, account_id: str, state: dict) -> None:
        if self.agent is None or not self._registration:
            return
        self.agent.connection.emit_signal(None, API_PATH, API, "AccountChanged",
                                          GLib.Variant("(ss)", (account_id, json.dumps(state))))

    # -- arrivals and notifications -------------------------------------------
    def _outbound_disabled(self, reason: str) -> None:
        """Sending was turned off by the kill switch: the person is told, once per reason."""
        LOG.error("Messages sending is turned off: %s", reason)
        if self.notifier is None or getattr(self, "_outbound_notified", None) == reason:
            return
        self._outbound_notified = reason
        self.notifier.notify(
            "outbound-disabled", "Messages stopped sending",
            "Messages turned off sending for your accounts to keep anything from going out twice. "
            "Nothing was lost. Open Accounts to review and turn sending back on.",
            actions=[("default", "Open")], category="im.error", urgency=2,
            public_summary="Messages", public_body="Sending is turned off",
            on_action=lambda _action: None)

    def _arrived(self, account_id: str, address: str, name: str, text: str) -> None:
        provider = self.services.get(account_id)
        if provider is None or self.notifier is None:
            return
        if (account_id, address) in self.viewing.values():
            # The window shows this conversation to the person: no announcement,
            # and the sender sees it was read.
            provider.conversation_seen(address)
            return
        key = conversation_key(account_id, address)
        try:
            muted = ConversationPreferences().flags(key)["muted"]
        except (OSError, ValueError, json.JSONDecodeError):
            muted = False
        if muted:
            self._schedule_count()
            return
        token = secrets.token_hex(16)
        self._reply_tokens[key] = token
        label = network(provider.account.network).name.removesuffix(" (unofficial client)")
        actions = [("default", "Open")]
        if provider.reaction_emoji and QUICK_REACTION in provider.reaction_emoji:
            actions.append(("react", QUICK_REACTION))
        actions += [("reply", "Reply"), ("mark-read", "Mark as Read")]
        self.notifier.notify(
            key, name, text, actions=actions, category="im.received", urgency=1,
            public_summary="Messages", public_body=f"New message · {label}",
            on_action=lambda action, account_id=account_id, address=address: self._action(account_id, address, action),
            hints={"x-luma-inline-reply-path": GLib.Variant("o", ReplyEndpoint.PATH),
                   "x-luma-inline-reply-token": GLib.Variant("s", token),
                   "x-luma-conversation": GLib.Variant("s", key)})
        self._schedule_count()

    def _reply_target(self, notification_id: int, token: str):
        if self.notifier is None:
            return None
        key = self.notifier.key_for(notification_id)
        expected = self._reply_tokens.get(key or "")
        if key is None or expected is None or not hmac.compare_digest(expected, token):
            return None
        account_id, _, address = key.removeprefix("account:").partition("|")
        return (account_id, address)

    def _forget_reply_tokens(self, *_args) -> None:
        self._reply_tokens.clear()

    def _action(self, account_id: str, address: str, action: str) -> None:
        provider = self.services.get(account_id)
        key = conversation_key(account_id, address)
        if action in {"default", "reply"}:
            open_conversation(key)
            if self.notifier is not None:
                self.notifier.withdraw(key)
        elif action == "mark-read" and provider is not None:
            self._mark_read(provider, address)
            if self.notifier is not None:
                self.notifier.withdraw(key)
        elif action == "react" and provider is not None:
            self._submit_call(None, lambda: self._react_latest(provider, address))

    def _mark_read(self, provider: AccountProvider, address: str) -> None:
        store = AccountStore(provider.store_path)
        try:
            store.mark_read(address)
        finally:
            store.close()
        provider.conversation_seen(address)
        self._schedule_count()

    def _react_latest(self, provider: AccountProvider, address: str) -> None:
        store = AccountStore(provider.store_path)
        try:
            row = store._connection.execute(
                "SELECT uid FROM messages WHERE address=? AND direction='incoming' ORDER BY timestamp DESC LIMIT 1",
                (address,)).fetchone()
        finally:
            store.close()
        if row is None:
            return
        result = provider.react(row[0], QUICK_REACTION)
        LOG.info("Quick reaction %s", result.get("state"))

    # -- unread count -----------------------------------------------------------
    def _watch_store(self, directory: Path) -> None:
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            monitor = Gio.File.new_for_path(str(directory)).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        except (OSError, GLib.Error):
            return
        monitor.connect("changed", lambda *_args: self._schedule_count())
        self._monitors.append(monitor)

    def _schedule_count(self) -> None:
        if not self._count_source:
            self._count_source = GLib.timeout_add(700, self._count_unread)

    def _unread(self, path: Path) -> tuple[int, dict[str, int]]:
        """Unread incoming messages in one store, read through a read-only connection.

        Opening a MessageStore writes (schema, WAL, chmod), which the directory
        monitor would report straight back; counting must not write.
        """
        connection = self._readers.get(path)
        if connection is None:
            if not path.exists():
                return 0, {}
            try:
                connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
            except sqlite3.Error:
                return 0, {}
            self._readers[path] = connection
        try:
            rows = connection.execute("SELECT address, COUNT(*) FROM messages WHERE direction='incoming' "
                                      "AND state='received' GROUP BY address").fetchall()
        except sqlite3.Error:
            connection.close()
            self._readers.pop(path, None)
            return 0, {}
        by_address = {str(address): int(count) for address, count in rows}
        return sum(by_address.values()), by_address

    def _count_unread(self) -> bool:
        self._count_source = 0
        messages = conversations = 0
        native, by_native = self._unread(self._native_store_path())
        messages += native
        conversations += len(by_native)
        unread_by_account: dict[str, dict[str, int]] = {}
        for account_id, provider in self.services.items():
            count, by_address = self._unread(provider.store_path)
            messages += count
            conversations += len(by_address)
            unread_by_account[account_id] = by_address
        for path in [path for path in self._readers
                     if path != self._native_store_path()
                     and all(path != provider.store_path for provider in self.services.values())]:
            self._readers.pop(path).close()
        if self.agent is not None:
            self.agent.publish("unread-count", messages)
            # The dock's unread badge: the same count, as the standard value.
            self.agent.publish("badge", GLib.Variant("u", messages))
            self.agent.publish("unread-conversations", conversations)
        # A conversation read in the window no longer needs its notification.
        if self.notifier is not None:
            for key in self.notifier.keys():
                account_id, _, address = key.removeprefix("account:").partition("|")
                if account_id in unread_by_account and not unread_by_account[account_id].get(address):
                    self.notifier.withdraw(key)
        if self.agent is not None:
            self.agent.connection.emit_signal(None, API_PATH, API, 'StoreChanged', None)
        return GLib.SOURCE_REMOVE

    # -- D-Bus API for the Messages window -------------------------------------
    def _call(self, connection, sender, _path, _interface, method, parameters, invocation) -> None:
        try:
            from .messages_app_rpc import authenticate_caller
            authenticate_caller(connection, sender)
        except Exception:
            invocation.return_dbus_error(f'{API}.Error.Refused', 'The Messages request was refused')
            return
        if method.startswith('Application'):
            if self._application_mailbox is None:
                from .messages_app_rpc import ApplicationMailbox
                self._application_mailbox = ApplicationMailbox(self)
            self._application_mailbox.call(connection, sender, method, parameters, invocation)
            return
        arguments = parameters.unpack()
        try:
            from .messages_app_rpc import MAX_INPUT
            if sum(len(value.encode('utf-8')) for value in arguments if isinstance(value, str)) > MAX_INPUT:
                raise ValueError('input too large')
            if method == "Accounts":
                state = [{"id": key, **self._account_state(key)} for key in self.services]
                invocation.return_value(GLib.Variant("(s)", (json.dumps(state),)))
            elif method == "Reload":
                self.reload()
                invocation.return_value(None)
            elif method == "Send":
                provider = self._provider(arguments[0])
                self._async(invocation, lambda: provider.send_message(_uid(arguments[1]), _uid(arguments[2])))
            elif method == "React":
                provider = self._provider(arguments[0])
                emoji = arguments[2] or None
                self._async(invocation, lambda: provider.react(_uid(arguments[1]), emoji))
            elif method == "ConversationOpened":
                provider = self._provider(arguments[0])
                provider.conversation_opened(arguments[1])
                if self.notifier is not None:
                    self.notifier.withdraw(conversation_key(arguments[0], arguments[1]))
                self._schedule_count()
                invocation.return_value(None)
            elif method == "ConversationSeen":
                self._provider(arguments[0]).conversation_seen(arguments[1])
                invocation.return_value(None)
            elif method == "SetViewing":
                self._set_viewing(connection, sender, arguments[0], arguments[1])
                invocation.return_value(None)
            elif method == "RetryMedia":
                self._provider(arguments[0]).retry_media(_uid(arguments[1]), arguments[2])
                invocation.return_value(None)
            elif method == "Luma":
                provider = self._provider(arguments[0])
                args = json.loads(arguments[2] or "{}")
                if not isinstance(args, dict):
                    raise ValueError("args")
                self._luma(invocation, provider, arguments[1], args)
            elif method == "Reconnect":
                self._provider(arguments[0]).retry()
                invocation.return_value(None)
            elif method == "RemoveAccount":
                self._remove(invocation, arguments[0], bool(arguments[1]))
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
        except KeyError:
            invocation.return_dbus_error(f"{API}.Error.NoSuchAccount", "No such account")
        except (ValueError, RecursionError):
            invocation.return_dbus_error(f"{API}.Error.InvalidInput", "Invalid input")

    def _luma(self, invocation, provider: AccountProvider, command: str, args: dict) -> None:
        """A Luma helper command for the window (ADR-051); never a delivery."""
        def run() -> None:
            from .messages_accounts import BridgeError
            try:
                answer = {"ok": True, "result": provider.luma_call(command, args)}
            except BridgeError as error:
                answer = {"ok": False, "error": {"code": error.code, "message": str(error), "retryable": error.retryable}}
            except Exception as error:
                LOG.info("Luma call failed: %s", type(error).__name__)
                answer = {"ok": False, "error": {"code": "error", "message": "Luma didn't answer.", "retryable": True}}
            GLib.idle_add(lambda: (invocation.return_value(GLib.Variant("(s)", (json.dumps(answer),))), False)[1])
        self._submit_call(invocation, run)

    def _provider(self, account_id: str) -> AccountProvider:
        return self.services[account_id]

    def _submit_call(self, invocation, work, *, admitted=False):
        if not admitted and not self._call_slots.acquire(blocking=False):
            if invocation is not None:
                invocation.return_dbus_error(f'{API}.Error.Busy', 'Messages is busy. Try again.')
            return False
        def run():
            try: work()
            finally: self._call_slots.release()
        try: self.worker.submit(run)
        except RuntimeError:
            self._call_slots.release()
            if invocation is not None:
                invocation.return_dbus_error(f'{API}.Error.Unavailable', 'Messages is closing.')
            return False
        return True

    def _async(self, invocation, work) -> None:
        def run() -> None:
            try:
                result = work()
            except Exception as error:  # the provider reports failures as states; this is the unexpected rest
                LOG.info("Agent call failed: %s", type(error).__name__)
                result = {"state": "failed"}
            GLib.idle_add(lambda: (invocation.return_value(GLib.Variant("(s)", (json.dumps(result or {}),))),
                                   self._schedule_count(), False)[2])
        self._submit_call(invocation, run)

    def _set_viewing(self, connection, sender: str, account_id: str, address: str) -> None:
        if not account_id or not address:
            self.viewing.pop(sender, None)
            return
        self.viewing[sender] = (account_id, address)
        if self.notifier is not None:
            self.notifier.withdraw(conversation_key(account_id, address))
        if sender not in self._viewer_watch:
            def vanished(_connection, name, sender=sender):
                self.viewing.pop(sender, None)
                watch = self._viewer_watch.pop(sender, 0)
                if watch:
                    GLib.idle_add(lambda: (Gio.bus_unwatch_name(watch), False)[1])
            self._viewer_watch[sender] = Gio.bus_watch_name_on_connection(
                connection, sender, Gio.BusNameWatcherFlags.NONE, None, vanished)

    def _remove(self, invocation, account_id: str, sign_out: bool) -> None:
        if not self._call_slots.acquire(blocking=False):
            invocation.return_dbus_error(f'{API}.Error.Busy', 'Messages is busy. Try again.')
            return
        provider = None
        try:
            account = next((item for item in self.accounts.list(include_pending=True) if item.id == account_id), None)
            provider = self.services.pop(account_id, None)
        except Exception as error:
            self._call_slots.release()
            LOG.warning('Account removal preparation failed: %s', type(error).__name__)
            invocation.return_dbus_error(f'{API}.Error.Unavailable', 'The account could not be removed. Try again.')
            return

        def run() -> None:
            try:
                if account is not None and sign_out:
                    remove_account(account, self.accounts, secrets=self.secrets, provider=provider,
                                   helper_dir=self.helper_dir)
                elif provider is not None:
                    provider.close(wait=True)
            except Exception as error:
                LOG.warning("Removing an account failed: %s", type(error).__name__)
            GLib.idle_add(lambda: (invocation.return_value(None), self._emit_account(account_id, {"state": "removed"}),
                                   self._schedule_count(), self._schedule_idle_exit(), False)[4])
        if not self._submit_call(invocation, run, admitted=True) and provider is not None:
            self.services[account_id] = provider


def _default_native_path() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return data_home / "prairie/messages/messages.db"


def _has_modem() -> bool:
    """Whether this device has its own ModemManager (a handset's texts)."""
    try:
        system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        from .background_agent import name_has_owner
        if name_has_owner(system, "org.freedesktop.ModemManager1"):
            return True
        names = system.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                 "ListActivatableNames", None, GLib.VariantType("(as)"), Gio.DBusCallFlags.NONE,
                                 2000, None).unpack()[0]
        return "org.freedesktop.ModemManager1" in names
    except GLib.Error:
        return False


def _uid(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("uid")
    return value


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:8]


def open_conversation(key: str) -> None:
    """Open Messages on one conversation; the agent itself never shows a window."""
    launch_app(APP_ID, ["prairie-messages", f"--conversation={key}"])


# ---------------------------------------------------------------------------
# The window's side
# ---------------------------------------------------------------------------


class AgentClient:
    """The Messages window's connection to the agent, if it is running."""

    def __init__(self, connection: Gio.DBusConnection) -> None:
        self.connection = connection
        self._listeners: dict[str, list] = {}
        self._subscription = connection.signal_subscribe(AGENT_NAME, API, "AccountChanged", API_PATH, None,
                                                         Gio.DBusSignalFlags.NONE, self._account_changed)
        self._seen_owner = False
        self._owner_watch = Gio.bus_watch_name_on_connection(connection, AGENT_NAME, Gio.BusNameWatcherFlags.NONE,
                                                             self._appeared, self._vanished)
        self.on_vanished = lambda: None
        self.on_store_changed = lambda: None
        self.on_mailbox_error = lambda _message: None
        self._store_subscription = connection.signal_subscribe(AGENT_NAME, API, 'StoreChanged', API_PATH, None,
            Gio.DBusSignalFlags.NONE, lambda *_: self.on_store_changed())
        self._error_subscription = connection.signal_subscribe(AGENT_NAME, API, 'MailboxError', API_PATH, None,
            Gio.DBusSignalFlags.NONE, lambda _c, _s, _p, _i, _n, args: self.on_mailbox_error(args.unpack()[0]))

    @classmethod
    def connect(cls, *, start: bool = True) -> "AgentClient | None":
        try:
            connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return None
        from .messages_app_client import sandboxed
        if start and not ensure_agent(APP_ID, AGENT_NAME, reason=INFO.purpose, connection=connection,
                                      foreground=sandboxed()):
            return None
        return cls(connection)

    def call(self, method: str, parameters: GLib.Variant | None = None, *, timeout: int = 10_000):
        try:
            result = self.connection.call_sync(AGENT_NAME, API_PATH, API, method, parameters, None,
                                               Gio.DBusCallFlags.NO_AUTO_START, timeout, None)
        except GLib.Error as error:
            # The agent stopped (no accounts left, or a crash being restarted):
            # ask luma-background for it once, then try again.
            if not error.matches(Gio.dbus_error_quark(), Gio.DBusError.NAME_HAS_NO_OWNER) and \
                    not error.matches(Gio.dbus_error_quark(), Gio.DBusError.SERVICE_UNKNOWN):
                raise
            from .messages_app_client import sandboxed
            if not ensure_agent(APP_ID, AGENT_NAME, reason=INFO.purpose, connection=self.connection,
                                foreground=sandboxed()):
                raise
            result = self.connection.call_sync(AGENT_NAME, API_PATH, API, method, parameters, None,
                                               Gio.DBusCallFlags.NO_AUTO_START, timeout, None)
        return result.unpack() if result is not None else ()

    def call_async(self, method: str, parameters: GLib.Variant | None = None) -> None:
        self.connection.call(AGENT_NAME, API_PATH, API, method, parameters, None, Gio.DBusCallFlags.NO_AUTO_START,
                             10_000, None, None)

    def states(self) -> dict[str, dict]:
        try:
            return {item["id"]: item for item in json.loads(self.call("Accounts")[0])}
        except (GLib.Error, ValueError, KeyError, TypeError):
            return {}

    def listen(self, account_id: str, callback) -> None:
        self._listeners.setdefault(account_id, []).append(callback)

    def unlisten(self, account_id: str, callback) -> None:
        if callback in self._listeners.get(account_id, []):
            self._listeners[account_id].remove(callback)

    def _account_changed(self, _connection, _sender, _path, _interface, _signal, parameters, *_user) -> None:
        account_id, state = parameters.unpack()
        try:
            value = json.loads(state)
        except ValueError:
            return
        for callback in list(self._listeners.get(account_id, [])):
            callback(value)

    def _appeared(self, *_args) -> None:
        # A restarted agent (after a crash) knows nothing of what the window last
        # heard: read every account's state again.
        if not self._seen_owner:
            self._seen_owner = True
            return
        states = self.states()
        for account_id, listeners in list(self._listeners.items()):
            state = states.get(account_id)
            if state is not None:
                for callback in list(listeners):
                    callback(state)

    def _vanished(self, *_args) -> None:
        for listeners in list(self._listeners.values()):
            for callback in list(listeners):
                callback({"state": "offline", "network_state": "connecting", "detail": ""})
        self.on_vanished()

    def close(self) -> None:
        try:
            self.call_async("SetViewing", GLib.Variant("(ss)", ("", "")))
        except GLib.Error:
            pass
        self.connection.signal_unsubscribe(self._store_subscription)
        self.connection.signal_unsubscribe(self._error_subscription)
        self.connection.signal_unsubscribe(self._subscription)
        Gio.bus_unwatch_name(self._owner_watch)
        from .messages_app_client import sandboxed
        if sandboxed():
            from .background_agent import release_foreground
            release_foreground(self.connection)


class RemoteAccountProvider(AccountProvider):
    """A network account whose helper runs in the agent; the window reads the store and asks."""

    def __init__(self, account: Account, accounts: Accounts, client: AgentClient, *, dispatch,
                 helper_dir: Path | None = None) -> None:
        super().__init__(account, accounts, secrets=None, dispatch=dispatch, helper_dir=helper_dir)
        self.client = client
        self._remote_notify = lambda _state: None

    remote = True

    def _reader(self):
        from .messages_app_client import sandboxed, ApplicationStore
        if not sandboxed():
            return super()._reader()
        reader = getattr(self, '_ui_store', None)
        if reader is None:
            reader = self._ui_store = ApplicationStore(self.client, self.account.id)
        return reader

    def start(self, notify) -> None:
        self._remote_notify = notify
        self.client.listen(self.account.id, self._apply)
        state = self.client.states().get(self.account.id)
        if state is None:
            try:
                self.client.call("Reload")
            except GLib.Error:
                pass
            state = self.client.states().get(self.account.id, {"state": "offline", "network_state": "connecting"})
        self._apply(state)

    def _apply(self, state: dict) -> None:
        if state.get("state") == "removed":
            return
        if isinstance(state.get("capabilities"), dict):
            self.capabilities = state["capabilities"]
            self._publish_media()
        self.status = {"state": state.get("state", "offline"), "network_state": state.get("network_state", "connecting"),
                       "detail": state.get("detail", "")}
        status = dict(self.status)
        self.dispatch(lambda: (self._remote_notify(status), False)[1])

    def refresh(self):
        future: Future = Future()
        future.set_result(None)
        return future

    def retry(self) -> None:
        self._ask("Reconnect", GLib.Variant("(s)", (self.account.id,)))

    invalidate = retry

    def wake(self) -> None:
        pass  # the agent hears sleep and network changes itself

    def close(self, *, wait: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        self.client.unlisten(self.account.id, self._apply)
        self._executor.shutdown(wait=False)
        reader, self._ui_store = getattr(self, "_ui_store", None), None
        if reader is not None:
            reader.close()

    def send_message(self, uid: str, user_token: str) -> dict:
        return self._ask_json("Send", GLib.Variant("(sss)", (self.account.id, uid, user_token)))

    def retry_message(self, uid: str, user_token: str) -> dict:
        return self.send_message(uid, user_token)

    def react(self, uid: str, emoji: str | None) -> dict:
        return self._ask_json("React", GLib.Variant("(sss)", (self.account.id, uid, emoji or "")))

    def conversation_seen(self, address: str) -> None:
        self._ask("ConversationSeen", GLib.Variant("(ss)", (self.account.id, address)))

    def conversation_opened(self, address: str) -> None:
        self._ask("ConversationOpened", GLib.Variant("(ss)", (self.account.id, address)))

    def viewing(self, address: str | None) -> None:
        self._ask("SetViewing", GLib.Variant("(ss)", (self.account.id if address else "", address or "")))

    def retry_media(self, uid: str, part: str) -> None:
        self._ask("RetryMedia", GLib.Variant("(sss)", (self.account.id, uid, part)))

    def luma_call(self, command: str, args: dict, *, timeout: float = 60) -> dict:
        from .messages_accounts import BridgeError
        try:
            reply = self.client.call("Luma", GLib.Variant("(sss)", (self.account.id, command, json.dumps(args))),
                                     timeout=int(timeout * 1000) + 5000)
            answer = json.loads(reply[0])
        except (GLib.Error, ValueError, IndexError):
            raise BridgeError("not_connected", "Luma isn't reachable right now.", True) from None
        if answer.get("ok"):
            return answer.get("result") if isinstance(answer.get("result"), dict) else {}
        error = answer.get("error") if isinstance(answer.get("error"), dict) else {}
        raise BridgeError(str(error.get("code") or "error"), str(error.get("message") or ""), bool(error.get("retryable")))

    def remove(self, *, sign_out: bool) -> None:
        try:
            self.client.call("RemoveAccount", GLib.Variant("(sb)", (self.account.id, sign_out)), timeout=60_000)
        except GLib.Error as error:
            LOG.warning("The agent did not remove the account: %s", error.message)
        self.close()

    def outbound_disabled(self):
        from .messages_app_client import sandboxed
        if not sandboxed(): return super().outbound_disabled()
        return json.loads(self.client.call('ApplicationOutbound', GLib.Variant('(sb)', (self.account.id, False)))[0])

    def resume_outbound(self):
        from .messages_app_client import sandboxed
        if not sandboxed(): return super().resume_outbound()
        self.client.call('ApplicationOutbound', GLib.Variant('(sb)', (self.account.id, True)))

    def _ask(self, method: str, parameters: GLib.Variant) -> None:
        try:
            self.client.call_async(method, parameters)
        except GLib.Error:
            pass

    def _ask_json(self, method: str, parameters: GLib.Variant) -> dict:
        try:
            return json.loads(self.client.call(method, parameters, timeout=CALL_TIMEOUT_MS)[0])
        except (GLib.Error, ValueError, IndexError):
            return {"state": "queued"}


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    agent_state = MessagesAgent()
    agent = Agent(INFO, start=agent_state.start, wake=agent_state.wake, stop=agent_state.stop)
    return agent.run()


if __name__ == "__main__":
    raise SystemExit(main())
