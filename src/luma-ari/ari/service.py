# SPDX-License-Identifier: Apache-2.0
"""ari-daemon: org.projectluma.Ari1 on the session bus (brief §4).

One daemon owns conversations, models, policy and the activity log; the overlay,
the dock popover and the `ari` CLI are clients. Replies stream as Event signals
carrying JSON, keyed by the request they belong to.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from pathlib import Path
from collections import deque

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from . import BUS_NAME, OBJECT_PATH, audit, cloud, hardware, models, paths
from .agent import Agent, Tools, Turn
from .policy import APPROVAL_MODES, ENABLED_TIERS, TIER_NAMES, Policy
from .providers import OpenAICompatible
from .persistence import update_json
from .runtime import LocalRuntime, RuntimeError_
from .store import Store
from .host_identity import authenticate, Refused
from .host_limits import Admission, Owners, Busy, MAX_INPUT, MAX_OUTPUT

IDLE_UNLOAD_MINUTES = 1
# Ari takes nothing while nobody is using her (ADR-024): the model lets go a
# minute after the last answer, at once when the last window closes or memory
# runs short, and the daemon itself exits after two quiet minutes. D-Bus
# starts it again with the next question.
IDLE_EXIT_SECONDS = 120
LOW_MEMORY_BYTES = 1_500_000_000
TOOL_SERVERS = ("answers", "desktop", "system", "preferences")
SETTINGS_SCHEMA = "org.projectluma.Ari"

INTERFACE = """
<node>
  <interface name="org.projectluma.Ari1">
    <method name="Ask"><arg name="conversation" type="s" direction="in"/><arg name="text" type="s" direction="in"/>
      <arg name="conversation_out" type="s" direction="out"/><arg name="request" type="s" direction="out"/></method>
    <method name="Stop"><arg name="request" type="s" direction="in"/></method>
    <method name="Undo"><arg name="step" type="s" direction="in"/><arg name="ok" type="b" direction="out"/></method>
    <method name="KeepStep"><arg name="step" type="s" direction="in"/><arg name="ok" type="b" direction="out"/></method>
    <method name="NewConversation"><arg name="conversation" type="s" direction="out"/></method>
    <method name="ListConversations"><arg name="json" type="s" direction="out"/></method>
    <method name="GetConversation"><arg name="conversation" type="s" direction="in"/><arg name="json" type="s" direction="out"/></method>
    <method name="DeleteConversation"><arg name="conversation" type="s" direction="in"/></method>
    <method name="Models"><arg name="json" type="s" direction="out"/></method>
    <method name="InstallModel"><arg name="model" type="s" direction="in"/><arg name="request" type="s" direction="out"/></method>
    <method name="SetActiveModel"><arg name="model" type="s" direction="in"/><arg name="ok" type="b" direction="out"/></method>
    <method name="RemoveModel"><arg name="model" type="s" direction="in"/><arg name="ok" type="b" direction="out"/></method>
    <method name="Settings"><arg name="json" type="s" direction="out"/></method>
    <method name="SetEnabled"><arg type="b" direction="in"/><arg type="b" direction="out"/></method>
    <method name="SetApprovalMode"><arg type="s" direction="in"/><arg type="b" direction="out"/></method>
    <method name="Cloud"><arg name="refresh" type="b" direction="in"/><arg name="json" type="s" direction="out"/></method>
    <method name="SetCloudKey"><arg name="key" type="s" direction="in"/>
      <arg name="ok" type="b" direction="out"/><arg name="message" type="s" direction="out"/></method>
    <method name="ForgetCloudKey"/>
    <method name="SetCloudModel"><arg name="model" type="s" direction="in"/><arg name="accept_cost" type="b" direction="in"/>
      <arg name="ok" type="b" direction="out"/><arg name="message" type="s" direction="out"/></method>
    <method name="SetBrain"><arg name="brain" type="s" direction="in"/>
      <arg name="ok" type="b" direction="out"/><arg name="message" type="s" direction="out"/></method>
    <method name="SetCloudOptions"><arg name="json" type="s" direction="in"/><arg name="ok" type="b" direction="out"/></method>
    <method name="SetPermission"><arg name="tier" type="u" direction="in"/><arg name="allowed" type="b" direction="in"/>
      <arg name="ok" type="b" direction="out"/></method>
    <method name="Approve"><arg name="approval" type="s" direction="in"/><arg name="approved" type="b" direction="in"/>
      <arg name="ok" type="b" direction="out"/></method>
    <method name="Release"/>
    <method name="Activity"><arg name="limit" type="u" direction="in"/><arg name="json" type="s" direction="out"/></method>
    <signal name="Event"><arg name="request" type="s"/><arg name="json" type="s"/></signal>
  </interface>
</node>
"""


def _available_memory() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 1 << 40


def _load_config() -> dict:
    try:
        return json.loads(paths.config_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_config(config: dict, keys=(), *, removed=(), defaults=()) -> None:
    current = update_json(paths.config_path(), {key: config[key] for key in keys},
                          removed=removed, defaults={key: config[key] for key in defaults})
    config.update(current)
    for key in removed:
        config.pop(key, None)


class Daemon:
    def __init__(self) -> None:
        self.admission = Admission()
        self.owners = Owners()
        self._events = deque()
        self._event_source = None
        self._event_lock = threading.Lock()
        self._closed = False
        self._model_lock = threading.RLock()
        self._model_mutation = None
        self._mutation_owner = None
        self.config = _load_config()
        self.hardware = hardware.cached(paths.data_dir() / "hardware.json")
        self.catalogue = models.catalogue()
        self.runtime = LocalRuntime(self.hardware)
        self.store = Store()
        tool_dir = Path(__file__).resolve().parent / "tools"
        self.tools = Tools({name: [sys.executable, str(tool_dir / f"{name}.py")] for name in TOOL_SERVERS})
        self.tools.start()
        self.agent = Agent(self.store, self.tools, Policy(), model_provider=self._model)
        self.agent.policy.enabled -= {int(t) for t in self.config.get("turned_off_tiers", [])}
        self.agent.describe_model = self._model_label
        self.ledger = cloud.Ledger()
        self.agent.on_cost = self._spent
        # The switches people and administrators use live in GSettings, so a
        # managed machine can lock them with dconf (ADR-025, the hard cutoff).
        source = Gio.SettingsSchemaSource.get_default()
        self.settings = Gio.Settings.new(SETTINGS_SCHEMA) if source and source.lookup(SETTINGS_SCHEMA, True) else None
        if self.settings is not None:
            self.settings.connect("changed", lambda *_: self._apply_settings())
        self.requests: dict[str, Turn] = {}
        self.downloads: dict[str, threading.Event] = {}
        self.connection: Gio.DBusConnection | None = None
        self._unload_timer: threading.Timer | None = None
        self._unload_lock = threading.Lock()
        self.last_activity = time.monotonic()
        self._apply_settings()

    # ── Switches ─────────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self.settings.get_boolean("enabled") if self.settings is not None else True

    def _apply_settings(self) -> None:
        if self.settings is not None:
            mode = self.settings.get_string("approval-mode")
            self.agent.policy.approval_mode = mode if mode in APPROVAL_MODES else "system"
        if not self.enabled:
            # Off means off: stop what is running, let the model go, stop the tools.
            for turn in list(self.requests.values()):
                turn.stop.set()
            for waiter in list(self.agent.approvals.values()):
                waiter[0].set()
            self.runtime.stop()
            self.tools.stop()
            self.tools.by_name.clear()
        elif not self.tools.by_name:
            self.tools.start()

    def _model_label(self) -> str:
        if self.brain == "openrouter":
            model = self._cloud_model(offline=True)
            return f"{model.name} through OpenRouter" if model else ""
        model = self.active_model()
        return f"{model.name} {model.quantisation} on this machine" if model else ""

    # ── Hosted models (ADR-027) ──────────────────────────────────────────

    @property
    def brain(self) -> str:
        return "openrouter" if self.config.get("brain") == "openrouter" else "local"

    def _monthly_limit(self) -> float:
        return max(0.0, float(self.config.get("cloud_monthly_limit", cloud.DEFAULT_MONTHLY_LIMIT)))

    def _spent(self, cost: float) -> None:
        self.ledger.add(cost)

    def _cloud_model(self, *, offline: bool = False) -> cloud.CloudModel | None:
        model_id = self.config.get("cloud_model", "")
        if not model_id:
            return None
        try:
            return cloud.find(model_id, cloud.catalogue())
        except (cloud.CloudError, OSError, ValueError):
            if offline:
                return None
            raise

    def _cloud_provider(self):
        key = cloud.load_key()
        if not key:
            raise LookupError("I'm set to think through OpenRouter, but there's no key yet. "
                              "Add one in Ari's Models settings.")
        try:
            model = self._cloud_model()
        except cloud.CloudError as error:
            raise LookupError(str(error)) from error
        if model is None:
            raise LookupError("The OpenRouter model I was set to use isn't available any more. "
                              "Choose another in Ari's Models settings.")
        if model.cost_class == "high" and model.id not in self.config.get("cloud_cost_accepted", []):
            raise LookupError(f"{model.name} costs a lot per question, and that hasn't been confirmed. "
                              "Choose it again in Ari's Models settings to confirm.")
        ledger, limit = self.ledger, self._monthly_limit()

        class Budgeted(OpenAICompatible):
            def chat(self, *args, **kwargs):
                # Checked before every call, including each step of one answer.
                if not model.free and ledger.spent() >= limit:
                    yield {"type": "error", "message": f"I've reached this month's OpenRouter limit of "
                                                       f"${limit:.2f}. Raise it in Ari's Models settings, "
                                                       "or switch me back to this computer."}
                    return
                yield from super().chat(*args, **kwargs)

        private = bool(self.config.get("cloud_private", True))
        provider = Budgeted(cloud.API, key, model.id, extra=cloud.request_options(model, private=private),
                            headers=cloud.HEADERS)
        provider.hardware_summary = self.hardware.summary()
        cutoff = model.knowledge_cutoff or "its training date"
        return provider, model.name, "through OpenRouter", cutoff, "large"

    def cloud_json(self, refresh: bool) -> str:
        key = cloud.load_key()
        value: dict = {"brain": self.brain, "key_set": bool(key), "keys_page": cloud.KEYS_PAGE,
                       "model": self.config.get("cloud_model", ""), "monthly_limit": self._monthly_limit(),
                       "spent": round(self.ledger.spent(), 4), "private": bool(self.config.get("cloud_private", True)),
                       "accepted": self.config.get("cloud_cost_accepted", []), "error": ""}
        try:
            models_now = cloud.catalogue(refresh=refresh)
            value["recommended"] = cloud.recommendations(models_now)
            value["models"] = sorted((m.as_dict() for m in models_now), key=lambda m: m["name"].lower())
        except (cloud.CloudError, OSError, ValueError) as error:
            value["recommended"], value["models"], value["error"] = [], [], str(error)
        if key:
            try:
                value["key"] = cloud.key_status(key)
            except cloud.CloudError as error:
                value["key"], value["error"] = None, value["error"] or str(error)
        return json.dumps(value)

    def set_cloud_key(self, key: str, cancelled=None) -> tuple[bool, str]:
        key = key.strip()
        if not key.startswith("sk-or-"):
            return False, "That doesn't look like an OpenRouter key. They start with sk-or-."
        try:
            status = cloud.key_status(key)
            if cancelled is not None and cancelled.is_set(): return False, "Cancelled."
            cloud.store_key(key)
        except cloud.CloudError as error:
            return False, str(error)
        audit.record({"tool": "set_cloud_key", "tier": 0, "arguments": {"service": "OpenRouter"},
                      "decision": "person changed", "summary": "Connected an OpenRouter key"})
        if status.get("limit") is None:
            return True, ("Connected. This key has no credit limit on OpenRouter; set one there so a mistake "
                          "can never cost more than you choose.")
        return True, "Connected."

    def set_cloud_model(self, model_id: str, accept_cost: bool, cancelled=None) -> tuple[bool, str]:
        try:
            model = cloud.find(model_id.strip(), cloud.catalogue())
        except cloud.CloudError as error:
            return False, str(error)
        if cancelled is not None and cancelled.is_set(): return False, "Cancelled."
        if model is None:
            return False, "OpenRouter doesn't list a model with that ID that can use Ari's tools."
        accepted = set(self.config.get("cloud_cost_accepted", []))
        if model.cost_class == "high":
            if not accept_cost and model.id not in accepted:
                return False, "confirm-cost"
            accepted.add(model.id)
        self.config["cloud_cost_accepted"] = sorted(accepted)
        self.config["cloud_model"] = model.id
        _save_config(self.config, ("cloud_cost_accepted", "cloud_model"))
        audit.record({"tool": "set_cloud_model", "tier": 0, "arguments": {"model": model.id},
                      "decision": "person changed", "summary": f"Ari now thinks with {model.name} through OpenRouter"})
        return True, ""

    def set_brain(self, brain: str) -> tuple[bool, str]:
        if brain not in ("local", "openrouter"):
            return False, "Unknown choice."
        self.config["brain"] = brain
        _save_config(self.config, ("brain",))
        if brain == "openrouter":
            self.runtime.stop()  # the local model isn't needed while Ari thinks elsewhere
        audit.record({"tool": "set_brain", "tier": 0, "arguments": {"brain": brain}, "decision": "person changed",
                      "summary": "Ari thinks through OpenRouter" if brain == "openrouter"
                      else "Ari thinks on this computer"})
        return True, ""

    def set_cloud_options(self, options: dict) -> bool:
        if "monthly_limit" in options:
            self.config["cloud_monthly_limit"] = max(0.0, min(10000.0, float(options["monthly_limit"])))
        if "private" in options:
            self.config["cloud_private"] = bool(options["private"])
        _save_config(self.config, tuple("cloud_" + key for key in ("monthly_limit", "private") if key in options))
        return True

    # ── Models ───────────────────────────────────────────────────────────

    def active_model(self) -> models.CatalogModel | None:
        installed = models.installed(self.catalogue)
        chosen = models.find(self.config.get("active_model", ""), self.catalogue)
        if chosen in installed:
            return chosen
        return installed[0] if installed else None

    def _model(self):
        if self.brain == "openrouter":
            return self._cloud_provider()
        model = self.active_model()
        if model is None:
            recommended = models.default_for_tier(self.hardware.tier, self.catalogue)
            size = f"{recommended.size / 1e9:.1f} GB"
            raise LookupError(f"I need a model on this machine to answer that. {recommended.name} ({size}) "
                              "runs entirely here. Want me to download it?")
        try:
            self.runtime.ensure(model)
        except RuntimeError_ as error:
            raise LookupError(str(error)) from error
        # Qwen3 thinks aloud unless asked not to; Ari's answers don't need it.
        provider = OpenAICompatible(self.runtime.base_url, self.runtime.key, model.id,
                                    extra={"chat_template_kwargs": {"enable_thinking": False}})
        provider.hardware_summary = self.hardware.summary()
        return provider, f"{model.name} {model.quantisation}", "on this machine", model.knowledge_cutoff, model.profile

    def models_json(self) -> str:
        installed = {m.id for m in models.installed(self.catalogue)}
        active = self.active_model()
        recommended = models.default_for_tier(self.hardware.tier, self.catalogue)
        suites = models.suite_results()
        cloud_model = self._cloud_model(offline=True) if self.brain == "openrouter" else None
        return json.dumps({
            "brain": self.brain,
            "cloud_model": {"id": cloud_model.id, "name": cloud_model.name, "cost_label": cloud.cost_label(cloud_model)}
            if cloud_model else None,
            "hardware": self.hardware.as_dict(), "summary": self.hardware.summary(),
            "active": active.id if active else "", "recommended": recommended.id,
            "runtime_installed": paths.runtime_binary() is not None,
            "models": [{"suite": suites.get(m.id), "id": m.id, "name": m.name, "family": m.family, "parameters": m.parameters,
                        "quantisation": m.quantisation, "licence": m.licence, "size": m.size,
                        "source": m.source, "installed": m.id in installed, "where": "This machine",
                        "badge": "Private", "downloading": m.id in self.downloads} for m in self.catalogue],
        })

    # A loaded model holds gigabytes that the rest of the machine shares. Let
    # it go once Ari has been quiet for a while; the next question reloads it.
    def _schedule_unload(self) -> None:
        minutes = float(self.config.get("keep_model_loaded_minutes", IDLE_UNLOAD_MINUTES))
        minutes = min(minutes, IDLE_UNLOAD_MINUTES) if _available_memory() < 2 * LOW_MEMORY_BYTES else minutes
        with self._unload_lock:
            if self._unload_timer:
                self._unload_timer.cancel()
            self._unload_timer = threading.Timer(minutes * 60, self._unload_if_idle)
            self._unload_timer.daemon = True
            self._unload_timer.start()

    def _cancel_unload(self) -> None:
        with self._unload_lock:
            if self._unload_timer:
                self._unload_timer.cancel()
                self._unload_timer = None

    def _unload_if_idle(self) -> None:
        if not self.requests:
            self.runtime.stop()

    def _busy(self) -> bool:
        return bool(self.requests or self.downloads or self.agent.revert_timers or self.agent.approvals)

    def _watch(self, loop: GLib.MainLoop) -> bool:
        """Every few seconds: give memory back when it runs short, and exit when idle."""
        if self.runtime.running() and not self.requests and _available_memory() < LOW_MEMORY_BYTES:
            self.runtime.stop()
        idle = time.monotonic() - self.last_activity
        if not self._busy() and not self.runtime.running() and idle > IDLE_EXIT_SECONDS:
            loop.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def settings_json(self) -> str:
        enabled = self.agent.policy.enabled
        descriptions = {0: "Answer questions, look things up and do sums.",
                        1: "Change your settings, sound, media, displays, time zone and connections, and open apps.",
                        2: "Read your calendar, messages and files to answer.",
                        3: "Send messages and make bookings for you.",
                        4: "Change your files and Luma's design.",
                        5: "Install software and change system settings."}
        return json.dumps({
            "tiers": [{"tier": tier, "name": TIER_NAMES[tier], "description": descriptions[tier],
                       "available": tier in ENABLED_TIERS, "enabled": tier in enabled,
                       "adjustable": tier in ENABLED_TIERS and tier != 0} for tier in sorted(TIER_NAMES)],
            "keep_model_loaded_minutes": float(self.config.get("keep_model_loaded_minutes", IDLE_UNLOAD_MINUTES)),
            "enabled": self.enabled, "approval_mode": self.agent.policy.approval_mode,
            "locked": self.settings is not None and not (self.settings.is_writable("enabled")
                                                         and self.settings.is_writable("approval-mode")),
            "schema": self.settings is not None,
            "enabled_writable": self.settings is not None and self.settings.is_writable("enabled"),
            "approval_mode_writable": self.settings is not None and self.settings.is_writable("approval-mode"),
        })

    def activity_json(self, limit: int) -> str:
        entries = audit.recent(limit or 200)
        for entry in entries:
            if entry.get("step"):
                step = self.store.step(entry["step"])
                entry["step_state"] = step["state"] if step else "gone"
        return json.dumps(entries)

    # ── D-Bus ────────────────────────────────────────────────────────────

    def _bounded_reply(self, value):
        if value is not None and value.get_size() > MAX_OUTPUT:
            raise ValueError("The result is too large. Choose a smaller conversation.")
        return value

    def _name_changed(self, _connection, _sender, _path, _interface, _signal, parameters):
        name, old, new = parameters.unpack()
        if name.startswith(":") and old and not new:
            with self._model_lock:
                if self._mutation_owner == name and self._model_mutation is not None:
                    self._model_mutation.set()
            for request in self.owners.vanished(name):
                turn = self.requests.get(request)
                if turn: turn.stop.set()
                download = self.downloads.get(request)
                if download: download.set()
            with self._event_lock:
                self._events = deque(e for e in self._events if e[0] != name)

    def _drain_events(self):
        with self._event_lock:
            batch = [self._events.popleft() for _ in range(min(32, len(self._events)))]
        for sender, request, encoded in batch:
            if self.connection and not self._closed:
                self.connection.emit_signal(sender, OBJECT_PATH, "org.projectluma.Ari1", "Event",
                                            GLib.Variant("(ss)", (request, encoded)))
        with self._event_lock:
            if self._events: return GLib.SOURCE_CONTINUE
            self._event_source = None
        return GLib.SOURCE_REMOVE

    def emit(self, request: str, event: dict) -> None:
        sender = self.owners.event(request, event)
        if not sender or self._closed: return
        encoded = json.dumps(event)
        if len(encoded.encode("utf-8")) > 65536:
            turn = self.requests.get(request)
            if turn: turn.stop.set()
            encoded = json.dumps({"type": "error", "message": "The answer exceeded Ari's limit."})
        with self._event_lock:
            # Streaming text and download progress are coalesced; no token can
            # create another GLib source or an unbounded pending signal queue.
            if self._events and event.get("type") in ("text", "download"):
                previous_sender, previous_request, previous = self._events[-1]
                decoded = json.loads(previous)
                if (previous_sender == sender and previous_request == request
                        and decoded.get("type") == event.get("type")):
                    if event["type"] == "text":
                        decoded["text"] += event.get("text", "")
                        merged = json.dumps(decoded)
                        if len(merged.encode("utf-8")) <= 65536:
                            self._events[-1] = (sender, request, merged)
                            return
                    else:
                        self._events[-1] = (sender, request, encoded)
                        return
            if len(self._events) >= 128 or sum(len(e[2].encode("utf-8")) for e in self._events) + len(encoded.encode("utf-8")) > 512 * 1024:
                turn = self.requests.get(request)
                if turn: turn.stop.set()
                download = self.downloads.get(request)
                if download: download.set()
                self._events = deque(e for e in self._events if e[1] != request)
                self._events.append((sender, request, json.dumps({"type": "error", "message": "Ari could not deliver this answer. Try again."})))
                return
            self._events.append((sender, request, encoded))
            if self._event_source is None:
                self._event_source = GLib.idle_add(self._drain_events)

    def handle(self, _connection, _sender, _path, _interface, method, parameters, invocation) -> None:
        reserved = False
        mutation = None
        mutation_submitted = False
        try:
            authenticate(_connection, _sender)
            if self._closed: raise Refused("Ari is closing.")
            if parameters.get_size() > MAX_INPUT:
                raise ValueError("This request is too large.")
            args = parameters.unpack()
            if method in ("Ask", "InstallModel", "Cloud", "SetCloudKey", "SetCloudModel"):
                self.admission.reserve()
                reserved = True
            self.last_activity = time.monotonic()
            mutations = ("SetActiveModel", "RemoveModel", "SetBrain", "SetCloudModel", "ForgetCloudKey", "SetCloudKey")
            with self._model_lock:
                if method in (*mutations, "Ask", "InstallModel") and self._model_mutation is not None:
                    raise Busy("A model change is still in progress.")
                if method in mutations and (self.requests or self.downloads):
                    raise Busy("Finish the current answer or download before changing its model.")
                if method in ("SetCloudKey", "SetCloudModel"):
                    mutation = threading.Event()
                    self._model_mutation, self._mutation_owner = mutation, _sender
            if method in ("Ask", "InstallModel") and not self.enabled:
                invocation.return_dbus_error("org.projectluma.Ari1.Error.Disabled",
                                             "Ari is turned off in Settings.")
                return
            if method == "Ask":
                if self.requests or self.downloads: raise Busy("Ari is already answering or downloading a model.")
                conversation, text = args
                if len(text.encode("utf-8")) > 8000 or len(conversation) > 64:
                    raise ValueError("The question is too long.")
                if not conversation or not self.store.exists(conversation):
                    conversation = self.store.new_conversation(model=getattr(self.active_model(), "id", ""))
                request = uuid.uuid4().hex
                self.owners.add(request, _sender)
                turn = Turn(conversation, text, lambda event, r=request: self.emit(r, event))
                self.requests[request] = turn

                def work() -> None:
                    self._cancel_unload()
                    try:
                        self.agent.answer(turn)
                    finally:
                        self.requests.pop(request, None)
                        with self.owners.lock: self.owners.requests.pop(request, None)
                        self.last_activity = time.monotonic()
                        self._schedule_unload()
                reserved = False
                try:
                    self.admission.submit_reserved(work)
                except BaseException:
                    self.requests.pop(request, None)
                    with self.owners.lock: self.owners.requests.pop(request, None)
                    raise
                invocation.return_value(self._bounded_reply(GLib.Variant("(ss)", (conversation, request))))
            elif method == "Stop":
                if not self.owners.owns("requests", args[0], _sender):
                    raise Refused("This request belongs to another connection.")
                turn = self.requests.get(args[0])
                if turn:
                    turn.stop.set()
                download = self.downloads.get(args[0])
                if download:
                    download.set()
                invocation.return_value(None)
            elif method == "Undo":
                with self.owners.lock:
                    owner = self.owners.steps.get(args[0])
                if owner is not None and owner != _sender:
                    raise Refused("This change belongs to another active connection.")
                invocation.return_value(self._bounded_reply(GLib.Variant("(b)", (self.agent.undo(args[0]),))))
            elif method == "KeepStep":
                if not self.owners.owns("steps", args[0], _sender):
                    raise Refused("This change belongs to another connection.")
                invocation.return_value(self._bounded_reply(GLib.Variant("(b)", (self.agent.confirm(args[0]),))))
            elif method == "NewConversation":
                identity = self.store.new_conversation(model=getattr(self.active_model(), "id", ""))
                invocation.return_value(self._bounded_reply(GLib.Variant("(s)", (identity,))))
            elif method == "ListConversations":
                invocation.return_value(self._bounded_reply(GLib.Variant("(s)", (json.dumps(self.store.conversations()),))))
            elif method == "GetConversation":
                value = {"messages": self.store.messages(args[0]), "steps": self.store.steps(args[0])}
                invocation.return_value(self._bounded_reply(GLib.Variant("(s)", (json.dumps(value),))))
            elif method == "DeleteConversation":
                if any(turn.conversation == args[0] for turn in self.requests.values()):
                    raise Busy("Finish the current answer before deleting this conversation.")
                self.store.delete_conversation(args[0])
                invocation.return_value(None)
            elif method == "Models":
                invocation.return_value(self._bounded_reply(GLib.Variant("(s)", (self.models_json(),))))
            elif method == "InstallModel":
                if self.requests: raise Busy("Finish the current answer before downloading a model.")
                reserved = False
                request = self.install(args[0], _sender)
                invocation.return_value(self._bounded_reply(GLib.Variant("(s)", (request,))))
            elif method == "SetActiveModel":
                ok = models.find(args[0], self.catalogue) in models.installed(self.catalogue)
                if ok:
                    self.config["active_model"] = args[0]
                    _save_config(self.config, ("active_model",))
                invocation.return_value(self._bounded_reply(GLib.Variant("(b)", (ok,))))
            elif method == "Activity":
                invocation.return_value(self._bounded_reply(GLib.Variant("(s)", (self.activity_json(min(500, int(args[0]))),))))
            elif method == "Release":
                # The last Ari window closed: nothing is waiting for an answer.
                if not self.requests:
                    self._cancel_unload()
                    self.runtime.stop()
                invocation.return_value(None)
            elif method == "Approve":
                if not self.owners.owns("approvals", args[0], _sender):
                    raise Refused("This approval belongs to another connection.")
                invocation.return_value(self._bounded_reply(GLib.Variant("(b)", (self.agent.approve(args[0], bool(args[1])),))))
            elif method == "SetEnabled":
                if self.settings is None or not self.settings.is_writable("enabled"):
                    raise Refused("Your administrator manages this setting.")
                invocation.return_value(self._bounded_reply(GLib.Variant("(b)", (self.settings.set_boolean("enabled", args[0]),))))
            elif method == "SetApprovalMode":
                if args[0] not in APPROVAL_MODES: raise ValueError("Unknown approval mode.")
                if self.settings is None or not self.settings.is_writable("approval-mode"):
                    raise Refused("Your administrator manages this setting.")
                invocation.return_value(self._bounded_reply(GLib.Variant("(b)", (self.settings.set_string("approval-mode", args[0]),))))
            elif method == "Settings":
                invocation.return_value(self._bounded_reply(GLib.Variant("(s)", (self.settings_json(),))))
            elif method in ("Cloud", "SetCloudKey", "SetCloudModel"):
                # These reach OpenRouter; answer from a worker so the bus stays responsive.
                def work(method=method, args=args) -> None:
                    try:
                        if method == "Cloud":
                            result = GLib.Variant("(s)", (self.cloud_json(bool(args[0])),))
                        elif method == "SetCloudKey":
                            result = GLib.Variant("(bs)", self.set_cloud_key(args[0], mutation))
                        else:
                            result = GLib.Variant("(bs)", self.set_cloud_model(args[0], bool(args[1]), mutation))
                        result = self._bounded_reply(result)
                        GLib.idle_add(lambda result=result: invocation.return_value(result) and False)
                    except Exception as error:  # noqa: BLE001 - reported to the caller
                        GLib.idle_add(lambda message=str(error): invocation.return_dbus_error(
                            "org.projectluma.Ari1.Error", message) and False)
                    finally:
                        if mutation is not None:
                            with self._model_lock:
                                if self._model_mutation is mutation:
                                    self._model_mutation = self._mutation_owner = None
                reserved = False
                self.admission.submit_reserved(work)
                mutation_submitted = True
            elif method == "ForgetCloudKey":
                cloud.forget_key()
                audit.record({"tool": "set_cloud_key", "tier": 0, "arguments": {"service": "OpenRouter"},
                              "decision": "person changed", "summary": "Removed the OpenRouter key"})
                invocation.return_value(None)
            elif method == "SetBrain":
                invocation.return_value(self._bounded_reply(GLib.Variant("(bs)", self.set_brain(args[0]))))
            elif method == "SetCloudOptions":
                invocation.return_value(self._bounded_reply(GLib.Variant("(b)", (self.set_cloud_options(json.loads(args[0])),))))
            elif method == "SetPermission":
                tier, allowed = int(args[0]), bool(args[1])
                ok = tier in ENABLED_TIERS and tier != 0
                if ok:
                    policy = self.agent.policy
                    policy.enabled = policy.enabled | {tier} if allowed else policy.enabled - {tier}
                    self.config["turned_off_tiers"] = sorted(ENABLED_TIERS - policy.enabled)
                    _save_config(self.config, ("turned_off_tiers",))
                    audit.record({"tool": "set_permission", "tier": tier,
                                  "arguments": {"tier": TIER_NAMES[tier], "allowed": allowed},
                                  "decision": "person changed", "summary":
                                  f"{TIER_NAMES[tier]} turned {'on' if allowed else 'off'}"})
                invocation.return_value(self._bounded_reply(GLib.Variant("(b)", (ok,))))
            elif method == "RemoveModel":
                model = models.find(args[0], self.catalogue)
                ok = model is not None and not (self.runtime.model == model and self.requests)
                if ok:
                    if self.runtime.model == model:
                        self.runtime.stop()
                    models.remove(model)
                    if self.config.get("active_model") == model.id:
                        self.config.pop("active_model")
                        _save_config(self.config, removed=("active_model",))
                    audit.record({"tool": "remove_model", "tier": 0, "arguments": {"model": model.id},
                                  "decision": "person agreed", "summary": f"Removed {model.name}"})
                invocation.return_value(self._bounded_reply(GLib.Variant("(b)", (ok,))))
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
        except (Refused, PermissionError, FileNotFoundError) as error:
            invocation.return_dbus_error("org.projectluma.Ari1.Refused", "This application is not authorized for Ari.")
        except Busy as error:
            invocation.return_dbus_error("org.projectluma.Ari1.Busy", str(error))
        except Exception as error:
            invocation.return_dbus_error("org.projectluma.Ari1.Error", str(error))
        finally:
            if reserved: self.admission.slots.release()
            if mutation is not None and not mutation_submitted:
                with self._model_lock:
                    if self._model_mutation is mutation:
                        self._model_mutation = self._mutation_owner = None

    def install(self, model_id: str, sender: str) -> str:
        request = None
        try:
            model = models.find(model_id, self.catalogue)
            if model is None: raise ValueError("Unknown model")
            if self.downloads: raise Busy("A model download is already running.")
            request = uuid.uuid4().hex
            self.owners.add(request, sender)
            cancelled = threading.Event()
            self.downloads[request] = cancelled
            audit.record({"tool": "install_model", "tier": 0, "arguments": {"model": model.id, "source": model.url},
                          "decision": "person agreed"})
        except BaseException:
            if request is not None:
                self.downloads.pop(request, None)
                with self.owners.lock: self.owners.requests.pop(request, None)
            self.admission.slots.release()
            raise

        def work() -> None:
            last = [0.0]

            def progress(done: int, total: int) -> None:
                fraction = done / total
                if fraction - last[0] >= 0.005 or done == total:
                    last[0] = fraction
                    self.emit(request, {"type": "download", "model": model.id, "done": done, "total": total})
            try:
                models.download(model, progress=progress, cancelled=cancelled)
                self.config.setdefault("active_model", model.id)
                _save_config(self.config, defaults=("active_model",))
                self.emit(request, {"type": "installed", "model": model.id})
            except models.DownloadError as error:
                self.emit(request, {"type": "error", "message": str(error)})
            finally:
                self.downloads.pop(request, None)
                with self.owners.lock: self.owners.requests.pop(request, None)
        try:
            self.admission.submit_reserved(work)
        except BaseException:
            self.downloads.pop(request, None)
            with self.owners.lock: self.owners.requests.pop(request, None)
            raise
        return request

    def run(self) -> None:
        node = Gio.DBusNodeInfo.new_for_xml(INTERFACE)
        loop = GLib.MainLoop()

        def acquired(connection, _name) -> None:
            self.connection = connection
            connection.register_object(OBJECT_PATH, node.interfaces[0], self.handle, None, None)
            connection.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                "/org/freedesktop/DBus", None, Gio.DBusSignalFlags.NONE, self._name_changed)

        def lost(_connection, _name) -> None:
            loop.quit()
        Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, acquired, lost)
        GLib.timeout_add_seconds(5, self._watch, loop)
        try:
            loop.run()
        finally:
            self._closed = True
            with self._model_lock:
                if self._model_mutation is not None: self._model_mutation.set()
            for turn in list(self.requests.values()): turn.stop.set()
            for cancelled in list(self.downloads.values()): cancelled.set()
            self.admission.close()
            if self._event_source is not None: GLib.source_remove(self._event_source)
            self.runtime.stop()
            self.tools.stop()


def main() -> int:
    Daemon().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
