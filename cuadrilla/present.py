"""Terminal voice. JSON stays available with --json."""

from __future__ import annotations

from typing import Any

from cuadrilla import __version__
from cuadrilla.brand import MARK, NAME, ink_for


def render(kind: str, body: dict[str, Any]) -> str:
    if not body.get("ok", True):
        return _fail(body)
    if kind == "doctor":
        return _doctor(body)
    if kind == "status":
        return _status(body)
    if kind == "session_open":
        return _opened(body)
    if kind == "demo":
        return _demo(body)
    if kind == "windows":
        return _windows(body)
    return _plain(kind, body)


def _fail(body: dict[str, Any]) -> str:
    code = body.get("error") or "error"
    message = body.get("message") or code
    holder = body.get("holder") or {}
    extra = ""
    if holder:
        extra = f"\n  held by {holder.get('session')} {holder.get('mouse')} · {holder.get('app')}"
    return f"{NAME.lower()}  {code}\n  {message}{extra}"


def _doctor(body: dict[str, Any]) -> str:
    parallel = "hardware overlaps" if body.get("parallel_hid") else "hardware still takes turns"
    reach = "reachable" if body.get("driver_reachable") else "down"
    return (
        f"{MARK}"
        f"  {__version__}\n"
        f"  driver   {body.get('binary') or 'cua-driver'}  {reach}\n"
        f"  input    scheduling overlaps apps; {parallel}\n"
        f"  {body.get('note') or ''}".rstrip()
        + "\n"
    )


def _opened(body: dict[str, Any]) -> str:
    if body.get("reused"):
        mice = ", ".join(body.get("mice") or [])
        return f"{NAME.lower()}  reused {body.get('session')}  {mice}\n"
    lines = [f"{NAME.lower()}  {body.get('name') or body.get('session')}  {body.get('session')}"]
    for mouse in body.get("mice") or []:
        ink = ink_for(mouse)
        lines.append(f"  {mouse}  {ink['name']}")
    return "\n".join(lines) + "\n"


def _status(body: dict[str, Any]) -> str:
    sessions = body.get("sessions") or {}
    leases = body.get("leases") or []
    if not sessions:
        return f"{NAME.lower()}  no crews\n  cuadrilla session open --mice 2\n"
    lines = [f"{NAME.lower()}  {len(sessions)} {'crew' if len(sessions) == 1 else 'crews'}"]
    held = {(lease.get("session"), lease.get("mouse")): lease for lease in leases}
    for sid, record in sessions.items():
        lines.append(f"  {record.get('name') or sid}  {sid}")
        for mouse, info in (record.get("mice") or {}).items():
            ink = (info or {}).get("ink") or ink_for(mouse)
            lease = held.get((sid, mouse))
            app = lease.get("app") if lease else "free"
            lines.append(f"    {mouse}  {ink.get('name', 'ink')}  {app}")
    return "\n".join(lines) + "\n"


def _windows(body: dict[str, Any]) -> str:
    windows = body.get("windows") or []
    lines = [f"{NAME.lower()}  {len(windows)} windows"]
    for window in windows[:24]:
        if not isinstance(window, dict):
            continue
        title = window.get("title") or "untitled"
        lines.append(f"  {window.get('app_name') or '?'}  {title}  pid {window.get('pid')}  window {window.get('window_id')}")
    if len(windows) > 24:
        lines.append(f"  … {len(windows) - 24} more")
    return "\n".join(lines) + "\n"


def _demo(body: dict[str, Any]) -> str:
    lines = [MARK.rstrip(), f"  demo  {body.get('session')}"]
    for step in body.get("steps") or []:
        mark = "ok" if step.get("ok") else step.get("error") or "no"
        lines.append(f"  {step.get('mouse')}  {step.get('ink')}  {step.get('app')}  {mark}")
        if step.get("detail"):
            lines.append(f"       {step['detail']}")
    if body.get("kept"):
        lines.append(f"  still open. close with: cuadrilla session close {body.get('session')}")
    return "\n".join(lines) + "\n"


def _plain(kind: str, body: dict[str, Any]) -> str:
    if kind == "session_close":
        return f"{NAME.lower()}  closed {body.get('session')}\n"
    message = body.get("message")
    if message:
        return f"{NAME.lower()}  {message}\n"
    return f"{NAME.lower()}  {kind} ok\n"
