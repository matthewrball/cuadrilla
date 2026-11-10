"""Thin client for the forked Cua engine. Cuadrilla does not reimplement input."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any


class DriverError(RuntimeError):
    pass


class Driver:
    def __init__(self, binary: str | None = None, timeout: float = 45.0) -> None:
        self.binary = binary or os.environ.get("CUADRILLA_DRIVER") or shutil.which("cua-driver") or "cua-driver"
        self.timeout = timeout

    def call(self, tool: str, args: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        payload = json.dumps(args or {}, separators=(",", ":"))
        try:
            completed = subprocess.run(
                [self.binary, "call", tool, payload],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
            )
        except FileNotFoundError as exc:
            raise DriverError("cua-driver is not installed") from exc
        except subprocess.TimeoutExpired as exc:
            raise DriverError(f"{tool} timed out") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[:500]
            raise DriverError(detail or f"{tool} failed ({completed.returncode})")
        return _parse_json(completed.stdout)


def _parse_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end < start:
            raise DriverError("cua-driver did not return JSON")
        parsed = json.loads(raw[start : end + 1])
    if isinstance(parsed, dict):
        return parsed
    return {"result": parsed}


def parallel_lanes_built_in(binary: str | None = None) -> bool:
    path = binary or shutil.which("cua-driver")
    if not path:
        return False
    try:
        completed = subprocess.run(
            ["grep", "-a", "-q", "CUA_DRIVER_PARALLEL_BACKGROUND", path],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0
