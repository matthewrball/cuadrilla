"""Sidebar pill and quiet notifications. Never a desktop banner by default."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable


Runner = Callable[[list[str]], None]


def cmux_bin() -> str | None:
    found = shutil.which("cmux")
    if found:
        return found
    candidates = [
        Path("/Applications/cmux NIGHTLY.app/Contents/Resources/bin/cmux"),
        Path("/Applications/cmux.app/Contents/Resources/bin/cmux"),
        Path.home() / "Applications/cmux NIGHTLY.app/Contents/Resources/bin/cmux",
        Path.home() / "Applications/cmux.app/Contents/Resources/bin/cmux",
    ]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def _run(argv: list[str]) -> None:
    binary = cmux_bin()
    if argv[0] == "cmux":
        if not binary:
            return
        argv = [binary, *argv[1:]]
    subprocess.run(argv, check=False, capture_output=True, text=True, timeout=5)


class Cmux:
    def __init__(self, runner: Runner | None = None) -> None:
        self.runner = runner
        self.socket = ""
        self.workspace = ""

    def bind(self, socket: str = "", workspace: str = "") -> None:
        if socket:
            self.socket = socket
        if workspace:
            self.workspace = workspace

    def _invoke(self, argv: list[str]) -> None:
        if self.workspace and "--workspace" not in argv:
            argv = [*argv, "--workspace", self.workspace]
        if self.runner is not None:
            self.runner(argv)
            return
        binary = cmux_bin()
        if argv[0] == "cmux":
            if not binary:
                return
            argv = [binary, *argv[1:]]
        env = os.environ.copy()
        if self.socket:
            env["CMUX_SOCKET_PATH"] = self.socket
            env.pop("CMUX_SOCKET", None)
        subprocess.run(argv, check=False, capture_output=True, text=True, timeout=5, env=env)

    def publish(self, state: dict[str, Any]) -> None:
        self._invoke(["cmux", "set-status", "cuadrilla", pill_text(state), "--icon", "sparkle", "--color", "#7C6AF7", "--priority", "40"])

    def conflict(self, message: str) -> None:
        self._invoke(["cmux", "notify", "--title", "Cuadrilla", "--body", message[:180], "--desktop", "false"])

    def clear(self) -> None:
        self._invoke(["cmux", "clear-status", "cuadrilla"])


def pill_text(state: dict[str, Any]) -> str:
    sessions = state.get("sessions") or {}
    leases = state.get("leases") or []
    parts = []
    seen = set()
    for lease in leases:
        key = (lease.get("session"), lease.get("mouse"), lease.get("app"))
        if key in seen:
            continue
        seen.add(key)
        mice = ((sessions.get(lease.get("session")) or {}).get("mice") or {})
        ink = ((mice.get(lease.get("mouse")) or {}).get("ink") or {}).get("name") or lease.get("mouse")
        parts.append(f"{ink} {lease.get('app') or 'window'}")
    if parts:
        return " · ".join(parts[:4])
    mice = sum(len((record.get("mice") or {})) for record in sessions.values())
    if not sessions:
        return "idle"
    return f"{len(sessions)} crews · {mice} mice · idle"


def mirror(state: dict[str, Any] | None = None, conflict: str = "") -> None:
    """Publish from this process. A LaunchAgent cannot connect to cmux."""
    socket = os.environ.get("CMUX_SOCKET_PATH") or os.environ.get("CMUX_SOCKET") or ""
    if not socket:
        return
    cmux = Cmux()
    cmux.bind(socket, os.environ.get("CMUX_WORKSPACE_ID") or "")
    if conflict:
        cmux.conflict(conflict)
    if state is not None and "sessions" in state:
        cmux.publish(state)
