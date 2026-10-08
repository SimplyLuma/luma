# SPDX-License-Identifier: Apache-2.0
"""GTK-free v70 data and in-memory capture state.

Only source_from_environment reads LUMA_ARI_FIXTURE. This source never imports
the daemon, Store, keyring, model downloader or an OS tool. The live UI uses
its existing daemon independently of this owned capture data.
"""
from __future__ import annotations

import copy
from datetime import datetime
import json
import os
from pathlib import Path


def audit_timestamp(value) -> float | None:
    """Read existing audit ISO dates and legacy epoch values without guessing."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            pass
    return None


def chat_groups(chats: list[dict], query: str = "") -> list[tuple[str, list[dict]]]:
    """v70 arSide: title search, retaining the fixture's day and chat order."""
    query = query.lower()
    matches = [chat for chat in chats if query in chat["t"].lower()]
    days = dict.fromkeys(chat["day"] for chat in matches)
    return [(day, [chat for chat in matches if chat["day"] == day]) for day in days]


def recipient_first_name(receipt: dict) -> str:
    """v71 arChain: who an approval sends to, by first name, from its `ask.to` (never a fixed name).

    `to` is a person's initials; the receipt's own step for that person names them."""
    to = (receipt.get("ask") or {}).get("to", "")
    person = next((node for node in receipt.get("nodes", []) if node.get("k") == "person" and node.get("p") == to), None)
    name = person["n"] if person else to
    return name.split(" ")[0] if name else "them"


def activity_items(activity: dict, category: str = "All") -> list[tuple[str, dict]]:
    """v70 arActivity: filter without reordering or mutating receipts."""
    return [(key, receipt) for key, receipt in activity.items()
            if category == "All" or category in receipt["cats"]]


def pending_count(activity: dict) -> int:
    return sum(node["st"] == "ask" for receipt in activity.values() for node in receipt["nodes"])


def trace_state(receipt: dict) -> tuple[str, str]:
    """v70 arChain: undo, approval, progress, then completed-step count."""
    states = {node["st"] for node in receipt["nodes"]}
    if receipt["undo"] == "undone":
        return "undone", "Undone"
    if "ask" in states:
        return "ask", "Needs your OK"
    if states.intersection(("run", "wait")):
        return "run", "Working"
    return "done", f"{len(receipt['nodes'])} steps"


def model_fit(model: dict, computer: dict) -> tuple[str, str]:
    """Fixture-only arFit thresholds; these are not measured machine speeds."""
    if model["mem"] <= 12:
        return "good", "Runs well"
    if model["mem"] <= computer["mem"] - computer["sys"] - 3:
        return "ok", "Runs, but slower"
    return "no", "Too big for this computer"


def model_catalog(models: list[dict], category: str = "All", query: str = "") -> dict:
    """v70 arModels: installed/downloading, recommendation, then the store."""
    query = query.strip().lower()
    local = [model for model in models if model.get("have") or model.get("dl") is not None]
    recommended = next((model for model in models if model.get("rec") and not model.get("have")), None)
    store = [model for model in models
             if not model.get("have") and model.get("dl") is None and model is not recommended
             and (category == "All" or category in model["good"])
             and query in (model["n"] + model["by"]).lower()]
    return {"local": local, "recommended": recommended if category == "All" and not query else None,
            "store": store}


class FixtureSource:
    """An owned in-memory copy. Simulated approvals never send or write anything."""

    def __init__(self, data: dict):
        for key, kind in (("computer", dict), ("local_models", list), ("providers", list),
                          ("provider_choices", list), ("activity", dict), ("conversations", list), ("state", dict)):
            if not isinstance(data.get(key), kind):
                raise ValueError(f"Ari fixture needs {key} ({kind.__name__})")
        self.data = copy.deepcopy(data)

    @classmethod
    def read(cls, path: str | Path) -> "FixtureSource":
        with Path(path).open(encoding="utf-8") as stream:
            return cls(json.load(stream))

    def answer(self, identity: str, approved: bool) -> None:
        receipt = self.data["activity"][identity]
        node = next((node for node in receipt["nodes"] if node["st"] == "ask"), None)
        if node is None:
            raise ValueError("This receipt has no pending approval")
        node["st"] = "done" if approved else "undone"
        if not approved:
            node["m"] = "Not sent"

    def undo(self, identity: str) -> None:
        receipt = self.data["activity"][identity]
        if receipt["undo"] not in ("can", "part"):
            raise ValueError("This receipt cannot be undone")
        # Match the spec: the initial input and a sent email survive Undo.
        for index, node in enumerate(receipt["nodes"]):
            if index and not (node["k"] == "email" and node["st"] == "done"):
                node["st"] = "undone"
        receipt["undo"] = "undone"

    def advance_download(self, identity: str, amount: float = 5) -> bool:
        """Advance only the owned model copy; true once the fixture download is ready."""
        model = next(model for model in self.data["local_models"] if model["id"] == identity)
        model["dl"] = min(100, model.get("dl", 0) + amount)
        if model["dl"] < 100:
            return False
        model.pop("dl")
        model["have"] = True
        return True


def scripted_reply(source: FixtureSource, chat: dict, value: str, model: str) -> list[dict]:
    """Approved v70 suggestion behavior, restricted to the owned fixture copy."""
    lower = value.lower()
    if "tax" in lower:
        title, answer, seconds, categories = ("Tax documents in one folder",
            "Found 9 tax documents: returns, receipts and two letters from the tax office. Moving them into Documents › Taxes 2026.",
            2.4, ["Files"])
        nodes = [{"k": "stack", "kind": "paper", "n": "9 tax documents", "m": "Downloads and Documents", "st": "done"},
                 {"k": "folder", "v": "Moved into", "n": "Taxes 2026", "m": "Documents", "count": 9, "st": "done"}]
    elif any(word in lower for word in ("quiet", "focus", "disturb")):
        title, answer, seconds, categories = ("Quiet until 18:00",
            "Do Not Disturb is on until 18:00. Priya can still reach you.", .8, ["Settings", "People"])
        nodes = [{"k": "setting", "n": "Do Not Disturb", "m": "On until 18:00", "st": "done"},
                 {"k": "person", "v": "except", "p": "PR", "n": "Priya Raman", "m": "Can still reach you", "st": "done"}]
    else:
        answer = ("For code on this computer, Qwen3 Coder 30B is the strongest that fits, but it answers at about 13 words a second. Qwen3 14B is quicker and still good at code. For the hardest problems, Claude Opus in the cloud is stronger than both."
                  if "code" in lower else
                  "A local model is a file you download that runs entirely on this computer, so nothing you type leaves it. The bigger the file, the smarter it is, and the more memory it needs."
                  if "local" in lower or "model" in lower else
                  "This is a mockup, so only a few requests really run here. Try one of the suggestions in a new chat.")
        return [{"a": answer, "m": model, "s": 1.6, "mem": "local" in lower or "model" in lower}]
    identity = "ch-" + chat["id"] + "-" + str(len(chat["msgs"]))
    source.data["activity"][identity] = {"t": title, "conv": chat["id"], "day": "Today", "at": "", "cats": categories,
                                        "undo": "can", "nodes": nodes}
    return [{"a": answer, "m": model, "s": seconds}, {"ch": identity}]


def source_from_environment() -> FixtureSource | None:
    path = os.environ.get("LUMA_ARI_FIXTURE")
    return FixtureSource.read(path) if path else None
