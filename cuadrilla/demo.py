"""A two-mouse pass on Calculator and a scratch TextEdit file. No focus steal."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from cuadrilla.brand import ink_for

SCRATCH = Path("/tmp/cuadrilla-demo.txt")
SKIP_TITLES = {"open", "save", "save panel accessory view"}
Request = Callable[[str, dict[str, Any]], dict[str, Any]]


def run_demo(request: Request, keep: bool = False, launch: bool = True) -> dict[str, Any]:
    if launch:
        SCRATCH.write_text("Cuadrilla\n")
        _open_background("Calculator")
        _open_background("TextEdit", SCRATCH)
        time.sleep(0.6)
    listing = request("list_windows", {})
    if not listing.get("ok", True):
        return listing
    calc = _window(listing, "Calculator")
    note = _window(listing, "TextEdit", prefer="cuadrilla-demo")
    if not calc or not note:
        return {
            "ok": False,
            "error": "demo_windows",
            "message": "need a Calculator window and cuadrilla-demo.txt in TextEdit",
            "found": {"calculator": bool(calc), "textedit": bool(note)},
        }
    opened = request("session_open", {"mice": 2, "name": "demo", "fresh": True})
    if not opened.get("ok"):
        return opened
    session = opened["session"]
    mark = "cuad" + str(int(time.time()) % 100000)
    steps = [
        _press_digit(request, session, "m2", calc),
        _type_mark(request, session, "m1", note, mark),
    ]
    if not keep:
        request("session_close", {"session": session})
    return {"ok": all(step.get("ok") for step in steps), "session": session, "steps": steps, "kept": keep}


def _press_digit(request: Request, session: str, mouse: str, window: dict[str, Any]) -> dict[str, Any]:
    observed = request("observe", {"session": session, "mouse": mouse, "pid": window["pid"], "window_id": window["window_id"]})
    token = _token(observed.get("window") or observed, role="AXButton", label="7")
    if not token:
        return _step(mouse, "Calculator", False, "no button labeled 7")
    acted = request(
        "act",
        {
            "session": session,
            "mouse": mouse,
            "tool": "click",
            "arguments": {
                "pid": window["pid"],
                "window_id": window["window_id"],
                "element_token": token,
                "lease_scope": "app",
            },
        },
    )
    delivered = bool(acted.get("ok")) or acted.get("effect") == "unverifiable"
    detail = "pressed 7" if acted.get("ok") else "pressed 7; the display is not in the accessibility tree"
    if acted.get("error") and acted.get("effect") != "unverifiable":
        delivered = False
        detail = str(acted.get("message") or acted.get("error"))[:180]
    return _step(mouse, "Calculator", delivered, detail)


def _type_mark(request: Request, session: str, mouse: str, window: dict[str, Any], mark: str) -> dict[str, Any]:
    observed = request("observe", {"session": session, "mouse": mouse, "pid": window["pid"], "window_id": window["window_id"]})
    token = _first_token(observed.get("window") or observed, "AXTextArea")
    if not token:
        return _step(mouse, "TextEdit", False, "no text area")
    acted = request(
        "act",
        {
            "session": session,
            "mouse": mouse,
            "tool": "type_text",
            "arguments": {
                "pid": window["pid"],
                "window_id": window["window_id"],
                "element_token": token,
                "text": mark,
                "lease_scope": "app",
            },
        },
    )
    detail = str(acted.get("effect") or acted.get("message") or acted.get("error") or mark)[:180]
    return _step(mouse, "TextEdit", bool(acted.get("ok")), f"{mark} · {detail}")


def _step(mouse: str, app: str, ok: bool, detail: str) -> dict[str, Any]:
    ink = ink_for(mouse)
    return {"mouse": mouse, "ink": ink["name"], "app": app, "ok": ok, "detail": detail, "error": None if ok else "unconfirmed"}


def _window(listing: dict[str, Any], app: str, prefer: str = "") -> dict[str, Any] | None:
    found = []
    for window in listing.get("windows") or []:
        if window.get("app_name") != app:
            continue
        title = str(window.get("title") or "")
        if title.lower() in SKIP_TITLES:
            continue
        found.append(window)
    if prefer:
        for window in found:
            if prefer.lower() in str(window.get("title") or "").lower():
                return window
    return found[0] if found else None


def _token(state: Any, role: str, label: str) -> str | None:
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            node_role = str(node.get("role") or "")
            node_label = str(node.get("label") or node.get("name") or node.get("title") or "")
            token = node.get("element_token")
            if token and node_role == role and node_label == label:
                found.append(str(token))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(state)
    return found[0] if found else None


def _first_token(state: Any, role: str) -> str | None:
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            token = node.get("element_token")
            if token and str(node.get("role") or "") == role:
                found.append(str(token))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(state)
    return found[0] if found else None


def _open_background(app: str, document: Path | None = None) -> None:
    argv = ["open", "-g", "-a", app]
    if document is not None:
        argv.append(str(document))
    subprocess.run(argv, check=False, capture_output=True, text=True)


def dumps(body: dict[str, Any]) -> str:
    return json.dumps(body, indent=2)
