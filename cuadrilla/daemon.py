"""One local socket. Agents share it so leases hold across processes."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from cuadrilla.cmux import Cmux
from cuadrilla.driver import Driver
from cuadrilla.service import Service
from cuadrilla.store import Store, home


def socket_path(root: Path | None = None) -> Path:
    return (root or home()) / "cuadrilla.sock"


def serve(root: Path | None = None) -> int:
    root = root or home()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "serve.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return 0
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    path = socket_path(root)
    if path.exists():
        path.unlink()
    service = Service(Store(root), Driver(), Cmux())
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    os.chmod(path, 0o600)
    server.listen(32)
    server.settimeout(1.0)

    def handle(conn: socket.socket) -> None:
        with conn:
            buffer = b""
            while b"\n" not in buffer:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buffer += chunk
            line, _rest = buffer.split(b"\n", 1)
            try:
                request = json.loads(line.decode())
                result = dispatch(service, request.get("method", ""), request.get("params") or {})
                response = {"id": request.get("id"), "ok": bool(result.get("ok", True)), "result": result}
            except Exception as exc:  # noqa: BLE001 — one bad call must not kill the crew
                response = {"id": None, "ok": False, "result": {"ok": False, "error": "internal", "message": str(exc)}}
            conn.sendall(json.dumps(response).encode() + b"\n")

    while True:
        try:
            conn, _addr = server.accept()
        except socket.timeout:
            continue
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


def dispatch(service: Service, method: str, params: dict[str, Any]) -> dict[str, Any]:
    service.cmux.bind(str(params.get("cmux_socket") or ""), str(params.get("cmux_workspace") or params.get("workspace") or ""))
    if method == "doctor":
        return service.doctor()
    if method == "status":
        return service.status()
    if method == "session_open":
        return service.session_open(
            mice=int(params.get("mice") or 1),
            name=str(params.get("name") or ""),
            surface=str(params.get("surface") or ""),
            workspace=str(params.get("workspace") or ""),
            fresh=bool(params.get("fresh")),
        )
    if method == "session_current":
        return service.session_current(mice=int(params.get("mice") or 1), name=str(params.get("name") or ""))
    if method == "session_close":
        return service.session_close(str(params["session"]))
    if method == "mice_add":
        return service.mice_add(str(params["session"]), int(params.get("count") or 1))
    if method == "list_windows":
        return service.list_windows()
    if method == "observe":
        return service.observe(str(params["session"]), str(params["mouse"]), int(params["pid"]), int(params["window_id"]))
    if method == "lease":
        return service.lease(
            str(params["session"]),
            str(params["mouse"]),
            int(params["pid"]),
            int(params["window_id"]),
            params.get("app"),
            str(params.get("scope") or "app"),
        )
    if method == "release":
        return service.release(
            str(params["session"]),
            params.get("mouse"),
            int(params["pid"]) if params.get("pid") is not None else None,
            int(params["window_id"]) if params.get("window_id") is not None else None,
            params.get("app"),
        )
    if method == "act":
        return service.act(
            str(params["session"]),
            str(params["mouse"]),
            str(params["tool"]),
            params.get("arguments") or {},
            str(params.get("delivery") or "background"),
        )
    return {"ok": False, "error": "unknown_method", "message": method}


def request(method: str, params: dict[str, Any] | None = None, root: Path | None = None, timeout: float = 60.0) -> dict[str, Any]:
    ensure(root)
    path = socket_path(root)
    body = dict(params or {})
    body.setdefault("cmux_socket", os.environ.get("CMUX_SOCKET_PATH") or os.environ.get("CMUX_SOCKET") or "")
    body.setdefault("cmux_workspace", os.environ.get("CMUX_WORKSPACE_ID") or "")
    if method in {"session_open", "session_current"}:
        body.setdefault("surface", os.environ.get("CMUX_SURFACE_ID") or "")
        body.setdefault("workspace", os.environ.get("CMUX_WORKSPACE_ID") or "")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(str(path))
    payload = {"id": "1", "method": method, "params": body}
    sock.sendall(json.dumps(payload).encode() + b"\n")
    data = b""
    while b"\n" not in data:
        chunk = sock.recv(1048576)
        if not chunk:
            break
        data += chunk
    sock.close()
    parsed = json.loads(data.decode() or "{}")
    return parsed.get("result") or parsed


def ensure(root: Path | None = None) -> None:
    path = socket_path(root)
    if _alive(path):
        return
    env = os.environ.copy()
    if root is not None:
        env["CUADRILLA_HOME"] = str(root)
    subprocess.Popen(
        [sys.executable, "-m", "cuadrilla", "serve"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        if _alive(path):
            return
        time.sleep(0.05)
    raise RuntimeError("Cuadrilla serve did not start")


def _alive(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(0.3)
        sock.connect(str(path))
        sock.close()
        return True
    except OSError:
        return False
