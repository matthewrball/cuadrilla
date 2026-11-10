"""Pure rules: leases, verification, and what Cuadrilla refuses to drive."""

from __future__ import annotations

import json
from typing import Any

MUTATING = {
    "click",
    "double_click",
    "right_click",
    "type_text",
    "set_value",
    "press_key",
    "hotkey",
    "scroll",
    "drag",
    "move_cursor",
    "invoke_menu",
}
VERIFY = {"type_text", "set_value", "press_key", "hotkey", "click", "double_click", "right_click", "scroll", "drag"}
TEXT_TOOLS = {"type_text", "set_value"}
MEDIA_KEYS = {"screenshot", "image", "image_b64", "png", "bytes", "preview"}


def fail(code: str, message: str, **extra: Any) -> dict[str, Any]:
    body = {"ok": False, "error": code, "message": message}
    body.update(extra)
    return body


def target_of(args: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
    target = args.get("target") if isinstance(args.get("target"), dict) else {}
    pid = args.get("pid", target.get("pid"))
    window_id = args.get("window_id", target.get("window_id"))
    app = args.get("app") or args.get("app_name") or target.get("app") or target.get("app_name")
    try:
        pid = int(pid) if pid is not None else None
    except (TypeError, ValueError):
        pid = None
    try:
        window_id = int(window_id) if window_id is not None else None
    except (TypeError, ValueError):
        window_id = None
    return pid, window_id, str(app) if app else None


def is_desktop(args: dict[str, Any]) -> bool:
    target = args.get("target") if isinstance(args.get("target"), dict) else {}
    kind = str(args.get("kind") or target.get("kind") or "")
    return kind == "desktop" or args.get("display_id") is not None or target.get("display_id") is not None


def lease_conflict(leases: list[dict[str, Any]], session: str, mouse: str, pid: int | None, window_id: int | None, app: str | None, scope: str) -> dict[str, Any] | None:
    for lease in leases:
        if lease.get("session") == session and lease.get("mouse") == mouse:
            continue
        same_window = (
            pid is not None
            and window_id is not None
            and lease.get("pid") == pid
            and lease.get("window_id") == window_id
        )
        their_app = lease.get("app")
        app_held = bool(app) and their_app == app and (lease.get("scope") == "app" or scope == "app")
        if same_window or app_held:
            return lease
    return None


def claim_lease(
    leases: list[dict[str, Any]],
    session: str,
    mouse: str,
    pid: int | None,
    window_id: int | None,
    app: str | None,
    scope: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if scope not in {"app", "window"}:
        return leases, fail("bad_scope", "scope must be app or window")
    if pid is None or window_id is None:
        return leases, fail("target_required", "a lease needs pid and window_id")
    holder = lease_conflict(leases, session, mouse, pid, window_id, app, scope)
    if holder:
        return leases, fail(
            "lease_conflict",
            f"{holder.get('app') or 'that window'} is held by {holder.get('session')} {holder.get('mouse')}",
            holder={"session": holder.get("session"), "mouse": holder.get("mouse"), "app": holder.get("app"), "pid": holder.get("pid"), "window_id": holder.get("window_id")},
        )
    kept = [
        lease
        for lease in leases
        if not (lease.get("session") == session and lease.get("mouse") == mouse and lease.get("pid") == pid and lease.get("window_id") == window_id)
    ]
    kept.append({"session": session, "mouse": mouse, "pid": pid, "window_id": window_id, "app": app, "scope": scope})
    return kept, None


def release_leases(
    leases: list[dict[str, Any]],
    session: str,
    mouse: str | None = None,
    pid: int | None = None,
    window_id: int | None = None,
    app: str | None = None,
) -> list[dict[str, Any]]:
    def keep(lease: dict[str, Any]) -> bool:
        if lease.get("session") != session:
            return True
        if mouse is not None and lease.get("mouse") != mouse:
            return True
        if pid is not None and lease.get("pid") != pid:
            return True
        if window_id is not None and lease.get("window_id") != window_id:
            return True
        if app is not None and lease.get("app") != app:
            return True
        return False

    return [lease for lease in leases if keep(lease)]


def _walk_text(value: Any, out: list[str]) -> None:
    if isinstance(value, str):
        if value:
            out.append(value)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if key in MEDIA_KEYS or key.endswith("_b64"):
                continue
            _walk_text(item, out)
        return
    if isinstance(value, list):
        for item in value:
            _walk_text(item, out)


def flatten_text(value: Any) -> str:
    parts: list[str] = []
    _walk_text(value, parts)
    return "\n".join(parts)


def fingerprint(value: Any) -> str:
    """Stable view of window state, ignoring handles that change every snapshot."""

    def scrub(item: Any) -> Any:
        if isinstance(item, dict):
            dropped = {"element_token", "snapshot_id", "capture_id", "since"} | MEDIA_KEYS
            return {key: scrub(val) for key, val in sorted(item.items()) if key not in dropped and not key.endswith("_b64")}
        if isinstance(item, list):
            return [scrub(val) for val in item]
        return item

    return json.dumps(scrub(value), sort_keys=True, default=str)


def settle(tool: str, args: dict[str, Any], before: Any, after: Any, driver_result: dict[str, Any]) -> dict[str, Any]:
    """Never treat a driver `ok` as success unless the window actually changed.

    cua-driver has reported ok for background typing that sent nothing (#7077).
    """

    effect = driver_result.get("effect")
    if isinstance(driver_result.get("structured"), dict) and not effect:
        effect = driver_result["structured"].get("effect")
    changed = fingerprint(before) != fingerprint(after)
    text = args.get("text") if tool == "type_text" else args.get("value") if tool == "set_value" else None
    landed = isinstance(text, str) and text != "" and text in flatten_text(after)
    if tool in TEXT_TOOLS:
        if landed or (changed and effect == "confirmed"):
            return {"ok": True, "effect": "confirmed", "changed": changed, "driver": driver_result}
        return {
            "ok": False,
            "error": "unconfirmed",
            "effect": "unconfirmed",
            "message": "the window did not show the text. not retrying a false ok",
            "changed": changed,
            "driver": driver_result,
        }
    if effect == "confirmed" or changed:
        return {"ok": True, "effect": "confirmed" if effect == "confirmed" or changed else effect, "changed": changed, "driver": driver_result}
    return {
        "ok": False,
        "error": "unconfirmed",
        "effect": "unconfirmed",
        "message": "the window did not change. the driver result was not treated as success",
        "changed": False,
        "driver": driver_result,
    }


def strip_media(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return None
    if isinstance(value, dict):
        return {
            key: strip_media(item, depth + 1)
            for key, item in value.items()
            if key not in MEDIA_KEYS and not str(key).endswith("_b64")
        }
    if isinstance(value, list):
        return [strip_media(item, depth + 1) for item in value[:160]]
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "…"
    return value



def token_for(state: Any, role: str, label: str) -> str | None:
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            token = node.get("element_token")
            node_role = str(node.get("role") or "")
            node_label = str(node.get("label") or node.get("name") or node.get("title") or "")
            if token and node_role == role and (not label or node_label == label):
                found.append(str(token))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(state)
    return found[0] if found else None


def refusal_code(payload: Any) -> str | None:
    if isinstance(payload, dict):
        refusal = payload.get("refusal")
        if isinstance(refusal, dict) and refusal.get("code"):
            return str(refusal["code"])
        if payload.get("code"):
            return str(payload["code"])
        return None
    text = str(payload)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return refusal_code(json.loads(text[start : end + 1]))
        except json.JSONDecodeError:
            return None
    return None
def window_app(listing: dict[str, Any], pid: int, window_id: int) -> str | None:
    windows = listing.get("windows") if isinstance(listing, dict) else None
    if not isinstance(windows, list):
        return None
    for window in windows:
        if not isinstance(window, dict):
            continue
        try:
            if int(window.get("pid")) == pid and int(window.get("window_id")) == window_id:
                name = window.get("app_name") or window.get("app")
                return str(name) if name else None
        except (TypeError, ValueError):
            continue
    return None
