# SPDX-License-Identifier: Apache-2.0
"""Subscription Responses transport behind Ari's existing model interface.

Tool calls are released only after response.completed. Ari still executes them
through its policy engine. Encrypted reasoning/output items stay in memory for
this turn's tool loop because this route has no server-side conversation store.
"""
from __future__ import annotations

import copy
import json
import threading
import time
from urllib.parse import urlsplit

from .chatgpt import API, ChatGPTError, NetworkError, error_message


def _input(messages):
    result = []
    for message in messages:
        role = message["role"]
        if role == "system":
            continue
        if role == "tool":
            result.append({"type": "function_call_output", "call_id": message["tool_call_id"],
                           "output": message["content"]})
            continue
        if message.get("content"):
            result.append({"role": role, "content": message["content"]})
        for call in message.get("tool_calls", []):
            result.append({"type": "function_call", "call_id": call["id"], "namespace": "ari",
                           "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
    return result


def _events(response, stop):
    lines, size = [], 0
    started = time.monotonic()
    while stop is None or not stop.is_set():
        raw = response.readline(1024 * 1024 + 1)
        if not raw:
            if lines:
                yield json.loads("\n".join(lines))
            return
        size += len(raw)
        if size > 8 * 1024 * 1024 or len(raw) > 1024 * 1024 or time.monotonic() - started > 300:
            raise ChatGPTError("ChatGPT's answer exceeded Ari's response limit.")
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if lines:
                data = "\n".join(lines)
                lines = []
                if data == "[DONE]":
                    return
                yield json.loads(data)
        elif line.startswith("data:"):
            lines.append(line[5:].lstrip(" "))


class ChatGPTProvider:
    hosted_search = True

    def __init__(self, manager, profile, model, *, reasoning=None, allow_search=True):
        self.manager, self.profile, self.model = manager, profile, model
        self.reasoning = reasoning
        self.allow_search = allow_search
        self._messages, self._history = [], []

    def chat(self, messages, tools=None, *, max_tokens=800, temperature=.4, stop: threading.Event | None = None):
        # max_tokens and temperature belong to the shared interface but are not
        # supported request fields on the ChatGPT plan route.
        if stop is not None and stop.is_set():
            return
        instructions = "\n\n".join(item["content"] for item in messages if item["role"] == "system")
        if self._messages and messages[:len(self._messages)] == self._messages:
            suffix = messages[len(self._messages):]
            # Ari appends an assistant item and tool outputs after each model
            # call. The actual Responses output already includes that item.
            if not suffix or suffix[0]["role"] != "assistant":
                yield {"type": "error", "message": "Ari could not continue this ChatGPT answer. Try again."}
                return
            input_items = self._history + _input(suffix[1:])
        else:
            input_items = _input(messages)
        body = {"model": self.model, "input": input_items, "instructions": instructions,
                "store": False, "stream": True, "include": ["reasoning.encrypted_content"]}
        tools = [tool for tool in tools or [] if tool["function"]["name"] not in ("web_search", "fetch_page", "wikipedia")]
        if tools:
            body["tools"] = [{"type": "namespace", "name": "ari", "description": "Ari's permitted local tools",
                              "tools": [{"type": "function", "name": tool["function"]["name"],
                                         "description": tool["function"].get("description", ""),
                                         "parameters": tool["function"]["parameters"], "strict": False}
                                        for tool in tools]}]
        if self.allow_search:
            body.setdefault("tools", []).append({"type": "web_search"})
        if self.reasoning:
            body["reasoning"] = {"effort": self.reasoning}
        completed, output, streamed, token = False, [], False, ""
        try:
            token = self.manager.access(self.profile)
            with self.manager.transport.open(API + "/responses", token=token, body=body, timeout=90) as response:
                for event in _events(response, stop):
                    if stop is not None and stop.is_set():
                        return
                    kind = event.get("type")
                    if kind in ("response.web_search_call.in_progress", "response.web_search_call.searching"):
                        yield {"type": "working", "label": "Searching the web…"}
                    elif kind == "response.output_item.added" and event.get("item", {}).get("type") == "web_search_call":
                        yield {"type": "working", "label": "Searching the web…"}
                    elif kind == "response.output_text.delta":
                        if not isinstance(event.get("delta"), str):
                            raise ValueError()
                        streamed = True
                        yield {"type": "text", "text": event["delta"]}
                    elif kind in ("response.failed", "error"):
                        error = event.get("response", {}).get("error") or event.get("error") or event
                        yield {"type": "error", "message": error_message(0, error.get("code", ""))}
                        return
                    elif kind == "response.incomplete":
                        raise ChatGPTError("ChatGPT's answer was incomplete. Try again or choose another model.")
                    elif kind == "response.completed":
                        result = event["response"]
                        if result.get("status") != "completed" or not isinstance(result.get("output"), list):
                            raise ValueError()
                        output = result["output"]
                        completed = True
                        break
            if stop is not None and stop.is_set():
                return
            if not completed:
                raise ChatGPTError("The ChatGPT connection ended before the answer completed. Try again.")
            calls, call_ids, final_text, sources = [], set(), [], []
            searched = False
            for item in output:
                if item.get("type") == "function_call":
                    arguments = json.loads(item["arguments"])
                    if (not isinstance(arguments, dict) or not isinstance(item.get("call_id"), str)
                            or not item["call_id"] or item["call_id"] in call_ids
                            or not isinstance(item.get("name"), str) or not item["name"]
                            or item.get("namespace") not in (None, "ari") or len(calls) >= 32):
                        raise ValueError()
                    call_ids.add(item["call_id"])
                    calls.append({"type": "tool_call", "id": item["call_id"], "name": item["name"], "arguments": arguments})
                elif item.get("type") == "message":
                    for part in item.get("content", []):
                        if part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                            final_text.append(part["refusal"])
                            continue
                        if part.get("type") != "output_text":
                            continue
                        value, citations = _cited_text(part)
                        final_text.append(value)
                        sources.extend(citations)
                elif item.get("type") == "web_search_call":
                    searched = True
            if not calls and not final_text and not streamed:
                raise ChatGPTError("ChatGPT finished without returning a reply or an application action. Try again.")
            self._history = input_items + output
            self._messages = copy.deepcopy(messages)
            if not streamed:
                for piece in final_text:
                    yield {"type": "text", "text": piece}
            elif sources:
                yield {"type": "final_text", "text": "".join(final_text)}
            if searched:
                yield {"type": "untrusted"}
            if sources:
                yield {"type": "sources", "sources": sources[:100]}
            yield from calls
        except NetworkError as error:
            if error.status == 401 and token:
                try:
                    self.manager.require_signin(self.profile, token)
                except ChatGPTError:
                    pass
            yield {"type": "error", "message": str(error)}
        except ChatGPTError as error:
            yield {"type": "error", "message": str(error)}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            yield {"type": "error", "message": "ChatGPT's answer could not be completed safely. Try again."}


def _cited_text(part):
    """Use completed annotations only; keep remote URLs inert until UI validation."""
    text, sources, ranges = part["text"], [], []
    for citation in part.get("annotations", [])[:100]:
        if citation.get("type") != "url_citation":
            continue
        url = citation.get("url", "")
        parsed = urlsplit(url)
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password or len(url) > 4096:
            continue
        title = str(citation.get("title") or parsed.hostname)[:300]
        sources.append({"title": title, "url": url})
        start, end = citation.get("start_index"), citation.get("end_index")
        if type(start) is int and type(end) is int and 0 <= start <= end <= len(text):
            # Labels and URLs are escaped by the Markdown renderer, never HTML.
            label = title.replace("[", "(").replace("]", ")")
            target = url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")
            ranges.append((start, end, f"[{label}]({target})"))
    boundary = len(text) + 1
    for start, end, link in sorted(ranges, reverse=True):
        if end <= boundary:
            text = text[:start] + link + text[end:]
            boundary = start
    return text, sources
