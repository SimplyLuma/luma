# SPDX-License-Identifier: Apache-2.0
"""The part of the Model Context Protocol Ari needs, over stdio (ADR-024).

Client: start a tool server, `initialize`, `tools/list`, `tools/call`.
Server: `serve(tools)` answers the same three requests on stdin/stdout.
Messages are newline-delimited JSON-RPC 2.0, as the MCP stdio transport says.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Callable

PROTOCOL_VERSION = "2025-06-18"


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    server: str


class ToolError(RuntimeError):
    pass


class Client:
    """One running tool server."""

    def __init__(self, name: str, command: list[str], *, env: dict | None = None) -> None:
        self.name = name
        self.command = command
        self.env = env
        self.process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._next = 0
        self.tools: list[Tool] = []

    def start(self) -> None:
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, bufsize=1, env=self.env)
        self._request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                     "clientInfo": {"name": "ari", "version": "0.1.0"}})
        self._notify("notifications/initialized")
        listed = self._request("tools/list", {})
        self.tools = [Tool(t["name"], t.get("description", ""), t.get("inputSchema", {}), self.name)
                      for t in listed.get("tools", [])]

    def _send(self, message: dict) -> None:
        assert self.process and self.process.stdin
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def _notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def _request(self, method: str, params: dict) -> dict:
        with self._lock:
            if self.process is None or self.process.poll() is not None:
                raise ToolError(f"The {self.name} tools aren't running.")
            self._next += 1
            identity = self._next
            self._send({"jsonrpc": "2.0", "id": identity, "method": method, "params": params})
            assert self.process.stdout
            while True:
                line = self.process.stdout.readline()
                if not line:
                    raise ToolError(f"The {self.name} tools stopped.")
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("id") != identity:
                    continue
                if "error" in message:
                    raise ToolError(message["error"].get("message", "Tool error"))
                return message.get("result", {})

    def call(self, tool: str, arguments: dict) -> dict:
        """Returns the tool's structured result: {"ok", "summary", "undo"?, "data"?}."""
        result = self._request("tools/call", {"name": tool, "arguments": arguments})
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            text = " ".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
            structured = {"ok": not result.get("isError"), "summary": text}
        structured.setdefault("ok", not result.get("isError"))
        return structured

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
        self.process = None


# ── Server side ──────────────────────────────────────────────────────────

Handler = Callable[[dict], dict]


def serve(server_name: str, tools: dict[str, tuple[str, dict, Handler]]) -> None:
    """tools: name -> (description, JSON schema, handler returning a result dict)."""
    for raw in sys.stdin:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue
        identity = message.get("id")
        method = message.get("method")
        if identity is None:
            continue  # notifications need no answer
        if method == "initialize":
            result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                      "serverInfo": {"name": server_name, "version": "0.1.0"}}
        elif method == "tools/list":
            result = {"tools": [{"name": name, "description": description, "inputSchema": schema}
                                for name, (description, schema, _h) in tools.items()]}
        elif method == "tools/call":
            params = message.get("params") or {}
            entry = tools.get(params.get("name"))
            if entry is None:
                reply = {"jsonrpc": "2.0", "id": identity, "error": {"code": -32602, "message": "Unknown tool"}}
                print(json.dumps(reply), flush=True)
                continue
            try:
                outcome = entry[2](params.get("arguments") or {})
            except Exception as error:  # a tool never takes its server down
                outcome = {"ok": False, "summary": f"{error}"}
            result = {"content": [{"type": "text", "text": outcome.get("summary", "")}],
                      "structuredContent": outcome, "isError": not outcome.get("ok", False)}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": identity,
                              "error": {"code": -32601, "message": "Method not found"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": identity, "result": result}), flush=True)
