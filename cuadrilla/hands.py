"""Commands a person can type. They use the open crew's leases."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from cuadrilla.engine import fail, token_for

Request = Callable[[str, dict[str, Any]], dict[str, Any]]
SKIP = {"open", "save", "save panel accessory view"}
SCRATCH = Path("/tmp/cuadrilla-demo.txt")


def stop(request: Request) -> dict[str, Any]:
    status = request("status", {})
    closed = []
    for session in list((status.get("sessions") or {})):
        request("session_close", {"session": session})
        closed.append(session)
    return {"ok": True, "closed": closed, "sessions": {}}


def press(request: Request, label: str, mouse: str = "") -> dict[str, Any]:
    held = _ready(request, mouse, prefer="Calculator")
    if not held.get("ok"):
        return held
    token = _control(request, held, "AXButton", label)
    if not token:
        return fail("no_control", f"no button labeled {label}")
    return request(
        "act",
        {
            "session": held["session"],
            "mouse": held["mouse"],
            "tool": "click",
            "arguments": {
                "pid": held["pid"],
                "window_id": held["window_id"],
                "element_token": token,
                "element_role": "AXButton",
                "element_label": label,
                "lease_scope": "app",
            },
        },
    )


def type_text(request: Request, text: str, mouse: str = "") -> dict[str, Any]:
    held = _ready(request, mouse, prefer="TextEdit")
    if not held.get("ok"):
        return held
    token = _control(request, held, "AXTextArea", "")
    if not token:
        return fail("no_control", "no text area in the leased window")
    return request(
        "act",
        {
            "session": held["session"],
            "mouse": held["mouse"],
            "tool": "type_text",
            "arguments": {
                "pid": held["pid"],
                "window_id": held["window_id"],
                "element_token": token,
                "element_role": "AXTextArea",
                "text": text,
                "lease_scope": "app",
            },
        },
    )


def _control(request: Request, held: dict[str, Any], role: str, label: str) -> str | None:
    for _ in range(2):
        observed = request(
            "observe",
            {"session": held["session"], "mouse": held["mouse"], "pid": held["pid"], "window_id": held["window_id"]},
        )
        token = token_for(observed.get("window") or observed, role, label)
        if token:
            return token
        time.sleep(0.4)
    return None


def _ready(request: Request, mouse: str, prefer: str) -> dict[str, Any]:
    held = _hold(request, mouse, prefer)
    if not held.get("ok"):
        return held
    window = _live_window(request, str(held.get("app") or prefer), held.get("pid"))
    if window is None:
        _open_background(str(held.get("app") or prefer))
        time.sleep(0.6)
        window = _live_window(request, str(held.get("app") or prefer), None)
    if window is None:
        return fail("no_window", f"{held.get('app')} has no window")
    return {
        "ok": True,
        "session": held["session"],
        "mouse": held["mouse"],
        "pid": window["pid"],
        "window_id": window["window_id"],
        "app": window.get("app_name") or held.get("app"),
    }


def _hold(request: Request, mouse: str, prefer: str) -> dict[str, Any]:
    status = request("status", {})
    if not status.get("ok", True):
        return status
    leases = status.get("leases") or []
    if mouse:
        leases = [lease for lease in leases if lease.get("mouse") == mouse]
    if not leases:
        return fail("no_lease", "no crew is holding an app. run cuadrilla demo --keep")
    chosen = next((lease for lease in leases if lease.get("app") == prefer), leases[0])
    return {"ok": True, **chosen}


def _live_window(request: Request, app: str, pid: int | None) -> dict[str, Any] | None:
    listing = request("list_windows", {})
    found = []
    for window in listing.get("windows") or []:
        if window.get("app_name") != app:
            continue
        if str(window.get("title") or "").lower() in SKIP:
            continue
        if pid is not None and window.get("pid") != pid:
            continue
        found.append(window)
    if not found and pid is not None:
        return _live_window(request, app, None)
    if app == "TextEdit":
        for window in found:
            if "cuadrilla-demo" in str(window.get("title") or "").lower():
                return window
    return found[0] if found else None


def _open_background(app: str) -> None:
    argv = ["open", "-g", "-a", app]
    if app == "TextEdit":
        SCRATCH.parent.mkdir(parents=True, exist_ok=True)
        if not SCRATCH.exists():
            SCRATCH.write_text("Cuadrilla\n")
        argv.append(str(SCRATCH))
    subprocess.run(argv, check=False, capture_output=True, text=True)
