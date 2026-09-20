"""
A self-contained, stdlib-only MCP server (JSON-RPC 2.0 over stdio).

This is the point of "a full loading version inside, not linking other repos": the
package speaks the Model Context Protocol directly, so `pip install .` gives you a
runnable MCP server with **zero third-party dependencies** — no `mcp` SDK, no `nlm`
required to even start. It implements the MCP handshake and the `tools/list` +
`tools/call` methods, which is the whole surface an MCP client needs to discover and
invoke tools.

The official `mcp` SDK remains an optional accelerator (see `server.py`), but nothing
here depends on it. Tools are registered as plain Python callables with a name, a
description, and a JSON-Schema input spec; each returns a string.

Protocol: newline-delimited JSON-RPC on stdin/stdout (the stdio transport MCP clients
like Claude Desktop / Cursor / Copilot speak). Notifications (no `id`) get no response.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Callable

PROTOCOL_VERSION = "2024-11-05"


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., str]

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description,
                "inputSchema": self.input_schema}


@dataclass
class StdioMCPServer:
    name: str
    version: str = "0.1.0"
    tools: dict[str, Tool] = field(default_factory=dict)

    def tool(self, name: str, description: str, input_schema: dict | None = None):
        """Decorator registering a callable as an MCP tool."""
        def deco(fn: Callable[..., str]) -> Callable[..., str]:
            self.tools[name] = Tool(name, description,
                                    input_schema or {"type": "object", "properties": {}}, fn)
            return fn
        return deco

    # --- JSON-RPC plumbing ------------------------------------------------------

    def _result(self, id_, result) -> dict:
        return {"jsonrpc": "2.0", "id": id_, "result": result}

    def _error(self, id_, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}

    def handle(self, msg: dict) -> dict | None:
        """Handle one JSON-RPC message. Returns a response dict, or None for a
        notification (a message with no `id`)."""
        method = msg.get("method")
        id_ = msg.get("id")
        is_notification = "id" not in msg

        if method == "initialize":
            return self._result(id_, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.name, "version": self.version},
            })
        if method in ("notifications/initialized", "initialized"):
            return None
        if method == "ping":
            return self._result(id_, {})
        if method == "tools/list":
            return self._result(id_, {"tools": [t.spec() for t in self.tools.values()]})
        if method == "tools/call":
            params = msg.get("params") or {}
            tname = params.get("name")
            args = params.get("arguments") or {}
            tool = self.tools.get(tname)
            if tool is None:
                return self._error(id_, -32602, f"unknown tool: {tname}")
            try:
                text = tool.handler(**args)
            except TypeError as e:
                return self._error(id_, -32602, f"bad arguments for {tname}: {e}")
            except Exception as e:  # tool errors are returned as content, not transport errors
                return self._result(id_, {
                    "content": [{"type": "text", "text": f"[error] {type(e).__name__}: {e}"}],
                    "isError": True})
            return self._result(id_, {"content": [{"type": "text", "text": str(text)}]})

        if is_notification:
            return None
        return self._error(id_, -32601, f"method not found: {method}")

    def serve_forever(self, stdin=None, stdout=None) -> None:  # pragma: no cover - I/O loop
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                stdout.write(json.dumps(self._error(None, -32700, "parse error")) + "\n")
                stdout.flush()
                continue
            resp = self.handle(msg)
            if resp is not None:
                stdout.write(json.dumps(resp) + "\n")
                stdout.flush()
