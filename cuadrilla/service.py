"""Session, mouse, lease, and verified action service."""

from __future__ import annotations

import os
import secrets
import threading
import time
from typing import Any, Callable, Optional

from cuadrilla.cmux import Cmux
from cuadrilla.driver import Driver, DriverError, parallel_lanes_built_in
from cuadrilla.engine import MUTATING, VERIFY, fail, flatten_text, is_desktop, settle, strip_media, target_of, window_app
from cuadrilla.store import Store

IdleFn = Callable[[], Optional[float]]


def hid_idle_seconds() -> float | None:
    import re
    import subprocess

    try:
        completed = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "2"], capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', completed.stdout)
    if not match:
        return None
    return int(match.group(1)) / 1_000_000_000


class Service:
    def __init__(self, store: Store, driver: Driver, cmux: Cmux | None = None, idle_fn: IdleFn | None = None, idle_min: float = 1.5, idle_wait: float = 8.0) -> None:
        self.store = store
        self.driver = driver
        self.cmux = cmux or Cmux()
        self.idle_fn = idle_fn or hid_idle_seconds
        self.idle_min = idle_min
        self.idle_wait = idle_wait
        self._pid_locks: dict[int, threading.Lock] = {}
        self._pid_guard = threading.Lock()
        self._foreground = threading.Lock()

    def doctor(self) -> dict[str, Any]:
        binary = self.driver.binary
        try:
            version = self.driver.call("list_apps", {}).get("apps")
            reachable = isinstance(version, list)
        except DriverError as exc:
            return {"ok": False, "error": "driver_down", "message": str(exc), "binary": binary}
        return {
            "ok": True,
            "binary": binary,
            "driver_reachable": reachable,
            "parallel_hid": parallel_lanes_built_in(binary),
            "note": "scheduling overlaps different apps here. overlapping HID needs a driver built with CUA_DRIVER_PARALLEL_BACKGROUND.",
        }

    def _pid_lock(self, pid: int) -> threading.Lock:
        with self._pid_guard:
            lock = self._pid_locks.get(pid)
            if lock is None:
                lock = threading.Lock()
                self._pid_locks[pid] = lock
            return lock

    def _cmux_from_env(self, params: dict[str, Any]) -> dict[str, str]:
        surface = str(params.get("surface") or os.environ.get("CMUX_SURFACE_ID") or "")
        workspace = str(params.get("workspace") or os.environ.get("CMUX_WORKSPACE_ID") or "")
        return {key: value for key, value in {"surface": surface, "workspace": workspace}.items() if value}

    def session_open(self, mice: int = 1, name: str = "", surface: str = "", workspace: str = "", fresh: bool = False) -> dict[str, Any]:
        count = max(1, min(int(mice), 8))
        cmux = self._cmux_from_env({"surface": surface, "workspace": workspace})
        if cmux.get("surface") and not fresh:
            existing = self.store.session_for_surface(cmux["surface"])
            if existing:
                record = (self.store.snapshot()["sessions"] or {}).get(existing) or {}
                return {"ok": True, "session": existing, "reused": True, "mice": list((record.get("mice") or {})), "name": record.get("name")}
        session = "cw-" + secrets.token_hex(3)
        mouse_ids = [f"m{i}" for i in range(1, count + 1)]
        record = self.store.open_session(session, name or session, mouse_ids, cmux)
        started = []
        for mouse in mouse_ids:
            error = self._ensure_cursor(session, mouse)
            if error:
                return error
            started.append(record["mice"][mouse]["driver_session"])
        self.cmux.publish(self.store.snapshot())
        return {"ok": True, "session": session, "mice": mouse_ids, "cursors": started, "name": name or session, "reused": False}

    def session_current(self, mice: int = 1, name: str = "") -> dict[str, Any]:
        return self.session_open(mice=mice, name=name, fresh=False)

    def session_close(self, session: str) -> dict[str, Any]:
        record = self.store.close_session(session)
        if not record:
            return fail("no_session", f"no session {session}")
        errors = []
        for mouse, info in (record.get("mice") or {}).items():
            if not info.get("started"):
                continue
            try:
                self.driver.call("end_session", {"session": info["driver_session"]})
            except DriverError as exc:
                errors.append(f"{mouse}: {exc}")
        self.cmux.publish(self.store.snapshot())
        return {"ok": not errors, "session": session, "errors": errors}

    def mice_add(self, session: str, count: int = 1) -> dict[str, Any]:
        snap = self.store.snapshot()
        record = snap["sessions"].get(session)
        if not record:
            return fail("no_session", f"no session {session}")
        have = list(record.get("mice") or {})
        start = len(have) + 1
        adding = [f"m{i}" for i in range(start, start + max(1, min(int(count), 8)))]
        if len(have) + len(adding) > 8:
            return fail("too_many_mice", "a session holds at most 8 mice")
        self.store.add_mice(session, adding)
        cursors = []
        for mouse in adding:
            error = self._ensure_cursor(session, mouse)
            if error:
                return error
            cursors.append(f"cuadrilla-{session}-{mouse}")
        self.cmux.publish(self.store.snapshot())
        mice = list((self.store.snapshot()["sessions"][session].get("mice") or {}))
        return {"ok": True, "session": session, "added": adding, "mice": mice, "cursors": cursors}

    def _ensure_cursor(self, session: str, mouse: str) -> dict[str, Any] | None:
        label = self.store.driver_session(session, mouse)
        if not label:
            return fail("no_mouse", f"{session} has no mouse {mouse}")
        if self.store.mouse_started(session, mouse):
            return None
        try:
            self.driver.call("start_session", {"session": label})
        except DriverError as exc:
            return fail("driver", str(exc))
        self.store.mark_started(session, mouse)
        return None

    def status(self) -> dict[str, Any]:
        snap = self.store.snapshot()
        self.cmux.publish(snap)
        return {"ok": True, **snap, "parallel_hid": parallel_lanes_built_in(self.driver.binary)}

    def list_windows(self) -> dict[str, Any]:
        try:
            listing = self.driver.call("list_windows", {})
        except DriverError as exc:
            return fail("driver", str(exc))
        return {"ok": True, **strip_media(listing)}

    def observe(self, session: str, mouse: str, pid: int, window_id: int) -> dict[str, Any]:
        missing = self.store.require_mouse(session, mouse)
        if missing:
            return missing
        error = self._ensure_cursor(session, mouse)
        if error:
            return error
        label = self.store.driver_session(session, mouse)
        try:
            state = self.driver.call(
                "get_window_state",
                {"pid": pid, "window_id": window_id, "include_screenshot": False, "tree_format": "elements", "session": label},
            )
        except DriverError as exc:
            return fail("driver", str(exc))
        return {"ok": True, "session": session, "mouse": mouse, "window": strip_media(state)}

    def lease(self, session: str, mouse: str, pid: int, window_id: int, app: str | None = None, scope: str = "app") -> dict[str, Any]:
        resolved = app or self._app_name(pid, window_id)
        error = self.store.claim(session, mouse, pid, window_id, resolved, scope)
        if error:
            self.cmux.conflict(error["message"])
            return error
        self.cmux.publish(self.store.snapshot())
        return {"ok": True, "session": session, "mouse": mouse, "pid": pid, "window_id": window_id, "app": resolved, "scope": scope}

    def release(self, session: str, mouse: str | None = None, pid: int | None = None, window_id: int | None = None, app: str | None = None) -> dict[str, Any]:
        self.store.release(session, mouse, pid, window_id, app)
        self.cmux.publish(self.store.snapshot())
        return {"ok": True}

    def _app_name(self, pid: int, window_id: int) -> str | None:
        try:
            listing = self.driver.call("list_windows", {})
        except DriverError:
            return None
        return window_app(listing, pid, window_id)

    def _wait_idle(self) -> dict[str, Any] | None:
        deadline = time.time() + self.idle_wait
        while True:
            idle = self.idle_fn()
            if idle is None:
                return fail("user_active", "could not read keyboard idle time, so foreground input was refused")
            if idle >= self.idle_min:
                return None
            if time.time() >= deadline:
                return fail("user_active", "you are at the machine. foreground input waits until you are idle")
            time.sleep(0.05)

    def act(self, session: str, mouse: str, tool: str, arguments: dict[str, Any] | None = None, delivery: str = "background") -> dict[str, Any]:
        args = dict(arguments or {})
        args.pop("session", None)
        if tool not in MUTATING and tool not in {"get_window_state", "list_windows", "list_apps"}:
            return fail("tool_refused", f"{tool} is not a Cuadrilla action. use observe for reads")
        if is_desktop(args):
            return fail("desktop_refused", "Cuadrilla will not take the desktop pointer. target one window")
        if delivery not in {"background", "when_idle"}:
            return fail("foreground_refused", "delivery is background or when_idle. Cuadrilla does not steal focus")
        pid, window_id, app = target_of(args)
        if tool in MUTATING and (pid is None or window_id is None):
            return fail("target_required", "pass pid and window_id for the window this mouse owns")
        scope = str(args.pop("lease_scope", "app"))
        if tool in MUTATING:
            resolved = app or self._app_name(pid or 0, window_id or 0)
            error = self.store.claim(session, mouse, pid or 0, window_id or 0, resolved, scope)
            if error:
                self.cmux.conflict(error["message"])
                return error
            app = resolved
        cursor = self._ensure_cursor(session, mouse)
        if cursor:
            return cursor
        label = self.store.driver_session(session, mouse)
        args["session"] = label
        if delivery == "when_idle":
            blocked = self._wait_idle()
            if blocked:
                return blocked
        before = None
        # A fresh get_window_state invalidates element_token. Read before only
        # when the action does not already carry a token from the last observe.
        if tool in VERIFY and pid is not None and window_id is not None and "element_token" not in args:
            try:
                before = self.driver.call(
                    "get_window_state",
                    {"pid": pid, "window_id": window_id, "include_screenshot": False, "tree_format": "elements", "session": label},
                )
            except DriverError as exc:
                return fail("driver", str(exc))
        lock = self._pid_lock(pid or 0)
        try:
            if delivery == "when_idle":
                with self._foreground:
                    with lock:
                        result = self.driver.call(tool, args)
            else:
                with lock:
                    result = self.driver.call(tool, args)
        except DriverError as exc:
            return fail("driver", str(exc))
        self.cmux.publish(self.store.snapshot())
        if tool in {"type_text", "set_value"} and before is None and pid is not None and window_id is not None:
            try:
                after = self.driver.call(
                    "get_window_state",
                    {"pid": pid, "window_id": window_id, "include_screenshot": False, "tree_format": "elements", "session": label},
                )
            except DriverError as exc:
                return fail("driver", f"action ran but verification failed: {exc}")
            text = str(args.get("text") or args.get("value") or "")
            if text and text in flatten_text(after):
                return {"ok": True, "effect": "confirmed", "session": session, "mouse": mouse, "app": app}
            return fail("unconfirmed", "the window did not show the text. not retrying a false ok")
        if tool not in VERIFY or before is None:
            effect = result.get("effect")
            return {"ok": effect in {None, "confirmed"}, "effect": effect or "unverifiable", "driver": strip_media(result), "session": session, "mouse": mouse, "app": app}
        try:
            after = self.driver.call(
                "get_window_state",
                {"pid": pid, "window_id": window_id, "include_screenshot": False, "tree_format": "elements", "session": label},
            )
        except DriverError as exc:
            return fail("driver", f"action ran but verification failed: {exc}")
        settled = settle(tool, args, before, after, result)
        settled["session"] = session
        settled["mouse"] = mouse
        settled["app"] = app
        settled["driver"] = strip_media(settled.get("driver"))
        return settled

