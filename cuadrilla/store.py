"""Cross-process crew state. One file, one lock, no hidden globals."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from cuadrilla.engine import claim_lease, fail, release_leases


def home() -> Path:
    override = os.environ.get("CUADRILLA_HOME")
    return Path(override) if override else Path.home() / ".cuadrilla"


class Store:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or home()
        self.path = self.root / "state.json"
        self.lock_path = self.root / "state.lock"

    def _empty(self) -> dict[str, Any]:
        return {"version": 1, "sessions": {}, "leases": []}

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "locks").mkdir(exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            state = self._empty()
            if self.path.exists():
                try:
                    loaded = json.loads(self.path.read_text())
                    if isinstance(loaded, dict):
                        state = loaded
                except json.JSONDecodeError:
                    state = self._empty()
            state.setdefault("sessions", {})
            state.setdefault("leases", [])
            yield state
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
            os.replace(tmp, self.path)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def snapshot(self) -> dict[str, Any]:
        with self._locked() as state:
            return json.loads(json.dumps(state))

    def open_session(self, session: str, name: str, mice: list[str], cmux: dict[str, str]) -> dict[str, Any]:
        with self._locked() as state:
            record = {
                "name": name,
                "created": time.time(),
                "cmux": cmux,
                "mice": {mouse: {"driver_session": f"cuadrilla-{session}-{mouse}", "started": False} for mouse in mice},
            }
            state["sessions"][session] = record
            return json.loads(json.dumps(record))

    def session_for_surface(self, surface: str) -> str | None:
        with self._locked() as state:
            for sid, record in state["sessions"].items():
                if (record.get("cmux") or {}).get("surface") == surface:
                    return sid
            return None

    def add_mice(self, session: str, mice: list[str]) -> dict[str, Any] | None:
        with self._locked() as state:
            record = state["sessions"].get(session)
            if not record:
                return None
            for mouse in mice:
                record["mice"].setdefault(mouse, {"driver_session": f"cuadrilla-{session}-{mouse}", "started": False})
            return json.loads(json.dumps(record))

    def mark_started(self, session: str, mouse: str) -> None:
        with self._locked() as state:
            record = state["sessions"].get(session) or {}
            mice = record.get("mice") or {}
            if mouse in mice:
                mice[mouse]["started"] = True

    def driver_session(self, session: str, mouse: str) -> str | None:
        snap = self.snapshot()
        record = snap["sessions"].get(session) or {}
        mice = record.get("mice") or {}
        found = mice.get(mouse)
        if not found:
            return None
        return found["driver_session"]

    def mouse_started(self, session: str, mouse: str) -> bool:
        snap = self.snapshot()
        mice = ((snap["sessions"].get(session) or {}).get("mice") or {})
        return bool((mice.get(mouse) or {}).get("started"))

    def require_mouse(self, session: str, mouse: str) -> dict[str, Any] | None:
        snap = self.snapshot()
        record = snap["sessions"].get(session)
        if not record:
            return fail("no_session", f"no session {session}")
        if mouse not in (record.get("mice") or {}):
            return fail("no_mouse", f"{session} has no mouse {mouse}")
        return None

    def claim(self, session: str, mouse: str, pid: int, window_id: int, app: str | None, scope: str) -> dict[str, Any] | None:
        missing = self.require_mouse(session, mouse)
        if missing:
            return missing
        with self._locked() as state:
            leases, error = claim_lease(state["leases"], session, mouse, pid, window_id, app, scope)
            if error:
                return error
            state["leases"] = leases
            return None

    def release(self, session: str, mouse: str | None = None, pid: int | None = None, window_id: int | None = None, app: str | None = None) -> None:
        with self._locked() as state:
            state["leases"] = release_leases(state["leases"], session, mouse, pid, window_id, app)

    def close_session(self, session: str) -> dict[str, Any] | None:
        with self._locked() as state:
            record = state["sessions"].pop(session, None)
            state["leases"] = [lease for lease in state["leases"] if lease.get("session") != session]
            return record
