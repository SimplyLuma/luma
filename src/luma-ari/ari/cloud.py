# SPDX-License-Identifier: Apache-2.0
"""Hosted models through OpenRouter (ADR-027).

OpenRouter is one account and one key for hundreds of models, and speaks the
same OpenAI-compatible protocol as Ari's local runtime. This module owns what
is particular to it: the key (kept in the person's keyring, never in a file),
the live catalogue and its prices, the recommendations, and the money:

- every model is shown with what a typical question costs, from OpenRouter's
  live price, and models that cost a lot are marked and must be confirmed;
- a request carries a price ceiling, so OpenRouter cannot route it to a
  provider charging more than the price the person saw;
- Ari keeps a monthly ledger from the cost OpenRouter reports for each reply
  and stops using the key when the person's monthly limit is reached;
- the key's own credit limit on OpenRouter is shown, and a key without one is
  called out, because that limit holds even if Ari's does not.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import paths

API = "https://openrouter.ai/api/v1"
KEYS_PAGE = "https://openrouter.ai/settings/keys"
CATALOGUE_MAX_AGE = 6 * 3600
# A typical Ari question on a hosted model: the rules, context, tool
# descriptions and recent conversation in, a short answer out.
TYPICAL_INPUT_TOKENS = 6000
TYPICAL_OUTPUT_TOKENS = 700
# Dollars for one typical question. At three cents a question an app-building
# task of a few hundred steps is already several dollars.
LOW_COST = 0.01
HIGH_COST = 0.03
# Any model this dear per million input tokens is expensive whatever its output price.
HIGH_INPUT_PER_MILLION = 5.0
# Headroom over the listed price before OpenRouter must refuse a provider.
PRICE_CEILING = 1.25
DEFAULT_MONTHLY_LIMIT = 10.0
HEADERS = {"HTTP-Referer": "https://projectluma.org", "X-Title": "Luma Ari"}


class CloudError(RuntimeError):
    pass


# ── The key ────────────────────────────────────────────────────────────────

def _secret():
    import gi
    gi.require_version("Secret", "1")
    from gi.repository import Secret
    schema = Secret.Schema.new("org.projectluma.Ari.OpenRouter", Secret.SchemaFlags.NONE,
                               {"service": Secret.SchemaAttributeType.STRING})
    return Secret, schema


def load_key() -> str:
    try:
        Secret, schema = _secret()
        return Secret.password_lookup_sync(schema, {"service": "openrouter"}, None) or ""
    except Exception:  # no keyring, or it is locked
        return ""


def store_key(key: str) -> None:
    Secret, schema = _secret()
    if not Secret.password_store_sync(schema, {"service": "openrouter"}, Secret.COLLECTION_DEFAULT,
                                      "OpenRouter key for Ari", key, None):
        raise CloudError("The keyring didn't accept the key.")


def forget_key() -> None:
    try:
        Secret, schema = _secret()
        Secret.password_clear_sync(schema, {"service": "openrouter"}, None)
    except Exception:
        pass


def _get(path: str, key: str = "", timeout: float = 20) -> dict:
    headers = dict(HEADERS)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(API + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise CloudError("OpenRouter didn't accept that key. Check it and try again.") from error
        raise CloudError(f"OpenRouter answered with an error ({error.code}).") from error
    except (OSError, ValueError) as error:
        raise CloudError("OpenRouter couldn't be reached. Check the connection.") from error


def key_status(key: str) -> dict:
    """The key's own limits on OpenRouter, which hold even if Ari's do not."""
    data = _get("/key", key).get("data") or {}
    return {"label": data.get("label") or "", "limit": data.get("limit"),
            "limit_remaining": data.get("limit_remaining"), "limit_reset": data.get("limit_reset"),
            "usage_monthly": data.get("usage_monthly"), "free_tier": bool(data.get("is_free_tier"))}


# ── The catalogue ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CloudModel:
    id: str
    name: str
    context: int
    prompt_price: float      # dollars per token
    completion_price: float  # dollars per token
    knowledge_cutoff: str

    @property
    def typical_cost(self) -> float:
        return TYPICAL_INPUT_TOKENS * self.prompt_price + TYPICAL_OUTPUT_TOKENS * self.completion_price

    @property
    def cost_class(self) -> str:
        cost = self.typical_cost
        if cost >= HIGH_COST or round(self.prompt_price * 1e6, 6) >= HIGH_INPUT_PER_MILLION:
            return "high"
        return "low" if cost <= LOW_COST else "moderate"

    @property
    def free(self) -> bool:
        return self.prompt_price == 0 and self.completion_price == 0

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "context": self.context,
                "input_per_million": round(self.prompt_price * 1e6, 4),
                "output_per_million": round(self.completion_price * 1e6, 4),
                "typical_cost": self.typical_cost, "cost_label": cost_label(self),
                "cost_class": self.cost_class}


def cost_label(model: CloudModel) -> str:
    if model.free:
        return "Free, with daily limits"
    cost = model.typical_cost
    if cost < 0.001:
        return "Under a tenth of a cent a question"
    if cost < 0.01:
        return f"About {cost * 100:.1f}¢ a question"
    if cost < 1:
        return f"About {cost * 100:.0f}¢ a question"
    return f"About ${cost:.2f} a question"


def _usable(row: dict) -> bool:
    architecture = row.get("architecture") or {}
    return ("tools" in (row.get("supported_parameters") or [])
            and "text" in (architecture.get("input_modalities") or ["text"])
            and "text" in (architecture.get("output_modalities") or ["text"])
            and not row.get("id", "").startswith("~")
            and not row.get("id", "").endswith(":batch"))


def parse_catalogue(value: dict) -> list[CloudModel]:
    found = []
    for row in value.get("data") or []:
        if not _usable(row):
            continue
        pricing = row.get("pricing") or {}
        try:
            prompt, completion = float(pricing.get("prompt", "")), float(pricing.get("completion", ""))
        except ValueError:
            continue
        if prompt < 0 or completion < 0:  # "-1" marks a router with variable pricing
            continue
        # "Anthropic: Claude Opus 5" -> "Claude Opus 5"; the ID still names the maker.
        name = (row.get("name") or row["id"]).split(": ", 1)[-1]
        found.append(CloudModel(row["id"], name, int(row.get("context_length") or 0),
                                prompt, completion, row.get("knowledge_cutoff") or ""))
    return found


_catalogue_lock = threading.Lock()


def catalogue(*, refresh: bool = False, cache: Path | None = None) -> list[CloudModel]:
    cache = cache or paths.data_dir() / "openrouter-models.json"
    with _catalogue_lock:
        fresh = cache.is_file() and time.time() - cache.stat().st_mtime < CATALOGUE_MAX_AGE
        if not refresh and fresh:
            try:
                return parse_catalogue(json.loads(cache.read_text()))
            except (OSError, ValueError):
                pass
        try:
            value = _get("/models")
        except CloudError:
            if cache.is_file():
                return parse_catalogue(json.loads(cache.read_text()))
            raise
        cache.write_text(json.dumps(value))
        return parse_catalogue(value)


def find(model_id: str, models: list[CloudModel]) -> CloudModel | None:
    return next((m for m in models if m.id == model_id), None)


def recommendations(models: list[CloudModel], path: Path | None = None) -> list[dict]:
    value = json.loads((path or paths.share_dir() / "cloud-models.json").read_text())
    if value.get("schema_version") != 1:
        return []
    shown = []
    for role in value.get("roles", []):
        model = next((m for m in (find(c, models) for c in role["candidates"]) if m), None)
        if model is not None:
            shown.append({"role": role["id"], "title": role["title"], "why": role["why"], **model.as_dict()})
    return shown


# ── Money ──────────────────────────────────────────────────────────────────

class Ledger:
    """What Ari spent through OpenRouter, by calendar month (UTC, as OpenRouter counts)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.data_dir() / "openrouter-spending.json"
        self.lock = threading.Lock()

    @staticmethod
    def _month() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def _read(self) -> dict:
        try:
            value = json.loads(self.path.read_text())
        except (OSError, ValueError):
            value = {}
        if value.get("month") != self._month():
            value = {"month": self._month(), "spent": 0.0, "replies": 0}
        return value

    def spent(self) -> float:
        with self.lock:
            return float(self._read()["spent"])

    def add(self, cost: float) -> None:
        if not cost or cost < 0:
            return
        with self.lock:
            value = self._read()
            value["spent"] = float(value["spent"]) + float(cost)
            value["replies"] = int(value["replies"]) + 1
            self.path.write_text(json.dumps(value))
            self.path.chmod(0o600)


def request_options(model: CloudModel, *, private: bool) -> dict:
    """Body fields every request to this model carries."""
    provider: dict = {"max_price": {"prompt": model.prompt_price * 1e6 * PRICE_CEILING,
                                    "completion": model.completion_price * 1e6 * PRICE_CEILING},
                      "require_parameters": True}
    if private:
        provider["data_collection"] = "deny"
    return {"provider": provider, "usage": {"include": True}}
