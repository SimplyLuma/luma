# SPDX-License-Identifier: Apache-2.0
"""Subscription Responses transport behind Ari's existing model interface.

Tool calls are released only after response.completed. Ari still executes them
through its policy engine. Encrypted reasoning/output items stay in memory for
this turn's tool loop because this route has no server-side conversation store.
"""
from __future__ import annotations

import copy
import json
import logging
import re
import threading
import time
from urllib.parse import urlsplit

from .chatgpt import API, ChatGPTError, NetworkError, error_message


_EVENT_TYPES = frozenset(("response.created", "response.in_progress", "response.output_item.added",
    "response.output_item.done", "response.content_part.added", "response.content_part.done",
    "response.output_text.delta", "response.output_text.done", "response.function_call_arguments.delta",
    "response.function_call_arguments.done", "response.reasoning_summary_text.delta", "response.completed",
    "response.failed", "response.incomplete", "error"))
_ITEM_TYPES = frozenset(("message", "function_call", "reasoning", "web_search_call", "custom_tool_call",
    "tool_search_call", "mcp_call", "computer_call"))
_TRANSIENT_CODES = frozenset(('server_error','internal_error','service_unavailable','server_is_overloaded','overloaded_error','connection_closed'))
_ERROR_CODES = _TRANSIENT_CODES | frozenset(('subscription_sharing_usage_limit_exceeded',
    'subscription_sharing_user_not_eligible','rate_limit_exceeded','invalid_request_error',
    'unsupported_tool','unsupported_tool_type','unsupported_value','unsupported_parameter','unknown_parameter'))


def _failure_event(error, *, remote=False):
    return {'type':'error','message':str(error),
            'retryable':(error.code in _TRANSIENT_CODES or error.status in (500,502,503,504)
                        or (not remote and error.status==0 and not error.code)) and error.retry_after != -1,
            'retry_after':error.retry_after,
            'summary_unavailable':error.status==400 and error.param=='reasoning.summary'
                        and error.code in ('unsupported_value','unsupported_parameter','unknown_parameter','invalid_request_error')}


def _progress_heading(summary):
    # Display only the provider's explicit user-facing summary, never its raw
    # reasoning_text or encrypted_content. One short plain-text line avoids
    # dumping a reasoning transcript into the conversation or activity log.
    first=next((line.strip() for line in summary.splitlines() if line.strip()),'')
    return ' '.join(re.sub(r'^[#*\s]+|[*\s]+$', '', first.replace('\0','')).split())[:160]


def _count_shape(counts, value, allowed):
    # Never log remote strings, arguments, text, identifiers or encrypted data.
    key = value if isinstance(value, str) and value in allowed else "other"
    counts[key] = counts.get(key, 0) + 1


def _completed_output(result, finished_items):
    if result.get("status") != "completed" or not isinstance(result.get("output"), list):
        raise ValueError()
    if result["output"] or not finished_items:
        return result["output"]
    # Some stream summaries omit the items already delivered individually. Only
    # completed items in a contiguous output sequence can replace an empty list.
    # They remain inert until the response itself has completed successfully.
    if sorted(finished_items) != list(range(len(finished_items))):
        raise ValueError()
    return [finished_items[index] for index in range(len(finished_items))]


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
        self._summaries_available = True

    def chat(self, messages, tools=None, *, max_tokens=800, temperature=.4, stop: threading.Event | None = None):
        summary=bool(self.reasoning and self._summaries_available)
        retries=0
        while stop is None or not stop.is_set():
            streamed=False
            failure=None
            for event in self._chat_once(messages,tools,max_tokens=max_tokens,temperature=temperature,
                                         stop=stop,reasoning_summary=summary):
                if event['type']=='error':
                    failure=event
                    break
                streamed = streamed or event['type'] in ('text','final_text','tool_call')
                yield event
            if failure is None:
                return
            if not streamed and summary and failure.get('summary_unavailable'):
                self._summaries_available=summary=False
                yield {'type':'working','label':'Continuing without reasoning summaries'}
                continue
            if not streamed and failure.get('retryable') and retries<2:
                retries+=1
                yield {'type':'working','label':f'Retrying ChatGPT ({retries}/2)'}
                waiter=stop if stop is not None else threading.Event()
                if waiter.wait(max(.5*retries,failure.get('retry_after') or 0)):return
                continue
            yield {'type':'error','message':failure['message']}
            return

    def _chat_once(self, messages, tools=None, *, max_tokens=800, temperature=.4, stop=None, reasoning_summary=False):
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
            if reasoning_summary:body['reasoning']['summary']='auto'
        completed, output, streamed, token = False, [], False, ""
        finished_items, event_counts, item_counts = {}, {}, {}
        terminal_count, usable = 0, False
        failure_code, failure_status = '', 0
        summaries={}
        try:
            token = self.manager.access(self.profile)
            with self.manager.transport.open(API + "/responses", token=token, body=body, timeout=90) as response:
                for event in _events(response, stop):
                    if stop is not None and stop.is_set():
                        return
                    kind = event.get("type")
                    _count_shape(event_counts, kind, _EVENT_TYPES)
                    if kind in ("response.web_search_call.in_progress", "response.web_search_call.searching"):
                        yield {"type": "working", "label": "Searching the web…"}
                    elif kind == "response.output_item.added" and event.get("item", {}).get("type") == "web_search_call":
                        yield {"type": "working", "label": "Searching the web…"}
                    elif kind == "response.output_text.delta":
                        if not isinstance(event.get("delta"), str):
                            raise ValueError()
                        streamed = streamed or bool(event["delta"])
                        yield {"type": "text", "text": event["delta"]}
                    elif kind == 'response.reasoning_summary_text.delta':
                        delta=event.get('delta')
                        index,part=event.get('output_index'),event.get('summary_index')
                        if not isinstance(delta,str) or type(index) is not int or type(part) is not int:
                            continue
                        if not (0<=index<256 and 0<=part<256):continue
                        key=(index,part)
                        summaries[key]=(summaries.get(key,'')+delta)[:2000]
                        heading=_progress_heading(summaries[key])
                        if heading:yield {'type':'working','label':heading,'source':'reasoning_summary'}
                    elif kind == "response.output_item.done":
                        index, item = event.get("output_index"), event.get("item")
                        if (type(index) is not int or not 0 <= index < 256 or not isinstance(item, dict)
                                or item.get("status") not in (None, "completed")
                                or (index in finished_items and finished_items[index] != item)):
                            raise ValueError()
                        finished_items[index] = item
                    elif kind in ("response.failed", "error"):
                        error = event.get("response", {}).get("error") or event.get("error") or event
                        code=error.get('code','') if isinstance(error,dict) else ''
                        failure_code=code if isinstance(code,str) and code in _ERROR_CODES else 'other'
                        failure=NetworkError(0,code)
                        result=_failure_event(failure,remote=True)
                        if failure_code=='other':
                            result['message']='ChatGPT returned an error while continuing this task. The task has not finished.'
                        yield result
                        return
                    elif kind == "response.incomplete":
                        raise ChatGPTError("ChatGPT's answer was incomplete. Try again or choose another model.")
                    elif kind == "response.completed":
                        result = event["response"]
                        output = _completed_output(result, finished_items)
                        terminal_count = len(result["output"])
                        completed = True
                        break
            if stop is not None and stop.is_set():
                return
            if not completed:
                raise NetworkError(0,'connection_closed')
            calls, call_ids, final_text, sources = [], set(), [], []
            searched = False
            for item in output:
                _count_shape(item_counts, item.get("type"), _ITEM_TYPES)
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
                elif item.get('type')=='reasoning' and not summaries:
                    for part in item.get('summary',[])[:30]:
                        if isinstance(part,dict) and part.get('type')=='summary_text' and isinstance(part.get('text'),str):
                            heading=_progress_heading(part['text'])
                            if heading:yield {'type':'working','label':heading,'source':'reasoning_summary'}
            if not calls and not any(piece.strip() for piece in final_text) and not streamed:
                raise ChatGPTError("ChatGPT finished without returning a reply or an application action. Try again.")
            usable = True
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
            failure_code=error.code if error.code in _ERROR_CODES else ('other' if error.code else '')
            failure_status=error.status
            if error.status == 401 and token:
                try:
                    self.manager.require_signin(self.profile, token)
                except ChatGPTError:
                    pass
            yield _failure_event(error)
        except ChatGPTError as error:
            yield {"type": "error", "message": str(error)}
        except OSError:
            failure_code='connection_closed'
            yield _failure_event(NetworkError(0,'connection_closed'))
        except (ValueError, KeyError, TypeError, AttributeError):
            yield {"type": "error", "message": "ChatGPT's answer could not be completed safely. Try again."}
        finally:
            logging.getLogger(__name__).log(logging.DEBUG if usable else logging.WARNING,
                "Ari ChatGPT stream shape: completed=%s terminal_items=%d done_items=%d streamed_text=%s events=%s output=%s failure_code=%s http_status=%d",
                completed, terminal_count, len(finished_items), streamed, event_counts, item_counts, failure_code, failure_status)


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
