"""Stdio MCP. Agents that speak MCP get a crew instead of a desktop takeover."""

from __future__ import annotations

import json
import sys
from typing import Any

from cuadrilla import __version__
from cuadrilla.daemon import request

TOOLS = [
    {
        "name": "session_current",
        "description": "Reuse this cmux surface's crew, or open one. One session can own several mice. Call this before acting.",
        "inputSchema": {"type": "object", "properties": {"mice": {"type": "integer", "minimum": 1, "maximum": 8}, "name": {"type": "string"}}, "additionalProperties": False},
    },
    {
        "name": "session_open",
        "description": "Open a new crew. Pass fresh=true to ignore the cmux surface binding. Each mouse gets its own cursor.",
        "inputSchema": {"type": "object", "properties": {"mice": {"type": "integer"}, "name": {"type": "string"}, "fresh": {"type": "boolean"}}, "additionalProperties": False},
    },
    {
        "name": "session_close",
        "description": "End a crew, its mice, and its window leases.",
        "inputSchema": {"type": "object", "properties": {"session": {"type": "string"}}, "required": ["session"], "additionalProperties": False},
    },
    {
        "name": "mice_add",
        "description": "Give an existing session more mice. Mice in one session still cannot share a window.",
        "inputSchema": {"type": "object", "properties": {"session": {"type": "string"}, "count": {"type": "integer"}}, "required": ["session"], "additionalProperties": False},
    },
    {
        "name": "status",
        "description": "List crews, mice, and which apps they hold.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_windows",
        "description": "List windows. No screenshot.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "observe",
        "description": "Read one window's accessibility tree. No screenshot. Does not take a lease.",
        "inputSchema": {
            "type": "object",
            "properties": {"session": {"type": "string"}, "mouse": {"type": "string"}, "pid": {"type": "integer"}, "window_id": {"type": "integer"}},
            "required": ["session", "mouse", "pid", "window_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lease",
        "description": "Claim a window for one mouse. Default scope is the whole app, so another mouse cannot enter it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string"},
                "mouse": {"type": "string"},
                "pid": {"type": "integer"},
                "window_id": {"type": "integer"},
                "app": {"type": "string"},
                "scope": {"type": "string", "enum": ["app", "window"]},
            },
            "required": ["session", "mouse", "pid", "window_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "release",
        "description": "Drop leases for a session, mouse, window, or app.",
        "inputSchema": {
            "type": "object",
            "properties": {"session": {"type": "string"}, "mouse": {"type": "string"}, "pid": {"type": "integer"}, "window_id": {"type": "integer"}, "app": {"type": "string"}},
            "required": ["session"],
            "additionalProperties": False,
        },
    },
    {
        "name": "act",
        "description": "Act in a leased window with one mouse. Refuses the desktop and focus-stealing. Verifies typing against the window instead of trusting ok. Tools: click, double_click, right_click, type_text, set_value, press_key, hotkey, scroll, drag, move_cursor.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string"},
                "mouse": {"type": "string"},
                "tool": {"type": "string"},
                "arguments": {"type": "object"},
                "delivery": {"type": "string", "enum": ["background", "when_idle"]},
            },
            "required": ["session", "mouse", "tool", "arguments"],
            "additionalProperties": False,
        },
    },
]


def serve_stdio() -> int:
    raw = sys.stdin.buffer
    while True:
        message = _read(raw)
        if message is None:
            return 0
        response = _handle(message)
        if response is not None:
            _write(response)
    return 0


def _handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    msg_id = message.get("id")
    if method == "notifications/initialized" or method == "initialized":
        return None
    if method == "initialize":
        return _result(msg_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "cuadrilla", "version": __version__}})
    if method == "tools/list":
        return _result(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        try:
            body = request(name, arguments)
        except Exception as exc:  # noqa: BLE001
            body = {"ok": False, "error": "daemon", "message": str(exc)}
        if name in {"session_open", "session_current", "session_close", "mice_add", "lease", "release", "act", "status"}:
            from cuadrilla.cmux import mirror

            state = body if isinstance(body, dict) and "sessions" in body else None
            if state is None:
                try:
                    state = request("status")
                except Exception:
                    state = None
            mirror(state, conflict=body.get("message", "") if isinstance(body, dict) and body.get("error") == "lease_conflict" else "")
        text = json.dumps(body, indent=2)
        return _result(msg_id, {"content": [{"type": "text", "text": text}], "isError": not body.get("ok", False)})
    if method == "ping":
        return _result(msg_id, {})
    if msg_id is None:
        return None
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"unknown method {method}"}}


def _result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _read(raw) -> dict[str, Any] | None:
    first = raw.read(1)
    if not first:
        return None
    if first == b"{":
        buf = bytearray(first)
        while not buf.endswith(b"\n"):
            chunk = raw.read(1)
            if not chunk:
                return None
            buf += chunk
        return json.loads(buf.decode())
    header = bytearray(first)
    while b"\r\n\r\n" not in header:
        chunk = raw.read(1)
        if not chunk:
            return None
        header += chunk
        if len(header) > 65536:
            raise ValueError("MCP header too large")
    length = 0
    for line in bytes(header).split(b"\r\n\r\n", 1)[0].split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1].strip())
    if length <= 0:
        return None
    body = bytearray()
    while len(body) < length:
        chunk = raw.read(length - len(body))
        if not chunk:
            return None
        body += chunk
    return json.loads(body.decode())


def _write(message: dict[str, Any]) -> None:
    body = json.dumps(message).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    sys.stdout.buffer.flush()
