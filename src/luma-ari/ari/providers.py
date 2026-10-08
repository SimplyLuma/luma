# SPDX-License-Identifier: Apache-2.0
"""Providers behind one interface: chat(messages, tools) -> a stream of events.

Events are dicts: {"type": "text", "text"}, {"type": "tool_call", "id",
"name", "arguments"}, {"type": "usage", "tokens_per_second"}, {"type": "cost",
"cost"} (dollars, when the service reports it), {"type": "error", "message"}. Tool calling is normalised here, not in the agent loop.
Phase 1 ships the OpenAI-compatible adapter, which covers llama-server,
Ollama, LM Studio and OpenRouter.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Iterator

# What an HTTP status from a hosted service means for the person.
STATUS_MESSAGES = {
    401: "The model service didn't accept the key. Check it in Ari's Models settings.",
    402: "Your OpenRouter credit has run out, or this key reached its limit. Add credit or raise the key's "
         "limit on OpenRouter.",
    403: "The model service refused this request.",
    404: "That model isn't available any more. Choose another in Ari's Models settings.",
    408: "The model took too long to answer.",
    429: "The model is busy or you've reached its rate limit. Try again in a moment.",
}


class OpenAICompatible:
    def __init__(self, base_url: str, api_key: str, model: str, *, extra: dict | None = None,
                 headers: dict | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.extra = extra or {}
        self.headers = headers or {}

    def chat(self, messages: list[dict], tools: list[dict] | None = None, *, max_tokens: int = 800,
             temperature: float = 0.4, stop: threading.Event | None = None) -> Iterator[dict]:
        body = {"model": self.model, "messages": messages, "stream": True,
                "max_tokens": max_tokens, "temperature": temperature, **self.extra}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        request = urllib.request.Request(
            self.base_url + "/chat/completions", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}", **self.headers})
        started = time.monotonic()
        tokens = 0
        calls: dict[int, dict] = {}
        try:
            response = urllib.request.urlopen(request, timeout=300)
        except urllib.error.HTTPError as error:
            yield {"type": "error", "message": STATUS_MESSAGES.get(
                error.code, f"The model service answered with an error ({error.code}).")}
            return
        except OSError as error:
            yield {"type": "error", "message": f"The model didn't answer: {error}"}
            return
        with response:
            for raw in response:
                if stop is not None and stop.is_set():
                    return
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if isinstance(chunk.get("error"), dict):
                    code = chunk["error"].get("code")
                    yield {"type": "error", "message": STATUS_MESSAGES.get(
                        code if isinstance(code, int) else 0,
                        "The model stopped with an error: " + str(chunk["error"].get("message", ""))[:200])}
                    return
                usage = chunk.get("usage") or {}
                if isinstance(usage.get("cost"), (int, float)):
                    yield {"type": "cost", "cost": float(usage["cost"])}
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        tokens += 1
                        yield {"type": "text", "text": delta["content"]}
                    for call in delta.get("tool_calls") or []:
                        slot = calls.setdefault(call.get("index", 0), {"id": "", "name": "", "arguments": ""})
                        slot["id"] = call.get("id") or slot["id"]
                        function = call.get("function") or {}
                        slot["name"] += function.get("name") or ""
                        slot["arguments"] += function.get("arguments") or ""
                        tokens += 1
                timings = chunk.get("timings") or {}
                if timings.get("predicted_per_second"):
                    yield {"type": "usage", "tokens_per_second": float(timings["predicted_per_second"])}
        for index in sorted(calls):
            call = calls[index]
            try:
                arguments = json.loads(call["arguments"] or "{}")
            except json.JSONDecodeError:
                yield {"type": "error", "message": "The model produced a malformed tool call."}
                continue
            yield {"type": "tool_call", "id": call["id"] or f"call_{index}", "name": call["name"],
                   "arguments": arguments if isinstance(arguments, dict) else {}}
        elapsed = time.monotonic() - started
        if tokens and elapsed > 0:
            yield {"type": "usage", "tokens_per_second": tokens / elapsed, "estimated": True}
