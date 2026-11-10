"""cuadrilla session open --mice 3"""

from __future__ import annotations

import argparse
import json
import sys

from cuadrilla import __version__
from cuadrilla.daemon import request, serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cuadrilla", description="A crew, not a takeover.")
    parser.add_argument("--version", action="version", version=f"cuadrilla {__version__}")
    parser.add_argument("--json", action="store_true", help="print raw JSON")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="run the local crew daemon")
    sub.add_parser("mcp", help="stdio MCP for agents")
    sub.add_parser("doctor")
    sub.add_parser("status")
    sub.add_parser("install")
    sub.add_parser("uninstall")
    demo = sub.add_parser("demo", help="two mice, Calculator and a scratch TextEdit file, no focus steal")
    demo.add_argument("--keep", action="store_true", help="leave the crew and its cursors open")
    press = sub.add_parser("press", help="press a labeled button in the leased app")
    press.add_argument("label")
    press.add_argument("--mouse", default="")
    typed = sub.add_parser("type", help="type into the leased text area")
    typed.add_argument("text")
    typed.add_argument("--mouse", default="")
    sub.add_parser("stop", help="close every crew and drop its cursors")

    open_p = sub.add_parser("session")
    session_sub = open_p.add_subparsers(dest="session_cmd", required=True)
    op = session_sub.add_parser("open")
    op.add_argument("--mice", type=int, default=1)
    op.add_argument("--name", default="")
    op.add_argument("--fresh", action="store_true")
    session_sub.add_parser("current")
    cl = session_sub.add_parser("close")
    cl.add_argument("session")

    mice = sub.add_parser("mice")
    mice_sub = mice.add_subparsers(dest="mice_cmd", required=True)
    add = mice_sub.add_parser("add")
    add.add_argument("session")
    add.add_argument("--count", type=int, default=1)

    windows = sub.add_parser("windows")
    windows.set_defaults(cmd="windows")

    obs = sub.add_parser("observe")
    obs.add_argument("session")
    obs.add_argument("mouse")
    obs.add_argument("--pid", type=int, required=True)
    obs.add_argument("--window", type=int, required=True)

    lease = sub.add_parser("lease")
    lease.add_argument("session")
    lease.add_argument("mouse")
    lease.add_argument("--pid", type=int, required=True)
    lease.add_argument("--window", type=int, required=True)
    lease.add_argument("--app")
    lease.add_argument("--scope", choices=["app", "window"], default="app")

    release = sub.add_parser("release")
    release.add_argument("session")
    release.add_argument("--mouse")
    release.add_argument("--pid", type=int)
    release.add_argument("--window", type=int)
    release.add_argument("--app")

    act = sub.add_parser("act")
    act.add_argument("session")
    act.add_argument("mouse")
    act.add_argument("tool")
    act.add_argument("arguments", help="JSON object")
    act.add_argument("--delivery", choices=["background", "when_idle"], default="background")

    args = parser.parse_args(argv)
    if args.cmd == "serve":
        return serve()
    if args.cmd == "mcp":
        from cuadrilla.mcp_server import serve_stdio

        return serve_stdio()
    if args.cmd == "install":
        from cuadrilla.install import install

        return install()
    if args.cmd == "uninstall":
        from cuadrilla.install import uninstall

        return uninstall()
    body = _call(args)
    _mirror(args, body)
    if args.json:
        json.dump(body, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        from cuadrilla.present import render

        sys.stdout.write(render(_kind(args), body))
    return 0 if body.get("ok", True) else 1


def _kind(args: argparse.Namespace) -> str:
    if args.cmd == "session" and getattr(args, "session_cmd", "") == "open":
        return "session_open"
    if args.cmd == "session" and getattr(args, "session_cmd", "") == "close":
        return "session_close"
    return args.cmd


def _mirror(args: argparse.Namespace, body: dict) -> None:
    from cuadrilla.cmux import mirror

    if args.cmd not in {"doctor", "status", "windows", "session", "mice", "observe", "lease", "release", "act", "demo", "press", "type", "stop"}:
        return
    state = body if "sessions" in body else None
    if state is None and args.cmd in {"session", "mice", "lease", "release", "act", "status", "demo", "press", "type", "stop"}:
        try:
            state = request("status")
        except Exception:
            state = None
    mirror(state, conflict=body.get("message", "") if body.get("error") == "lease_conflict" else "")


def _call(args: argparse.Namespace) -> dict:
    if args.cmd == "doctor":
        return request("doctor")
    if args.cmd == "demo":
        from cuadrilla.demo import run_demo

        return run_demo(request, keep=args.keep)
    if args.cmd == "press":
        from cuadrilla.hands import press

        return press(request, args.label, args.mouse)
    if args.cmd == "type":
        from cuadrilla.hands import type_text

        return type_text(request, args.text, args.mouse)
    if args.cmd == "stop":
        from cuadrilla.hands import stop

        return stop(request)
    if args.cmd == "status":
        return request("status")
    if args.cmd == "windows":
        return request("list_windows")
    if args.cmd == "session" and args.session_cmd == "open":
        return request("session_open", {"mice": args.mice, "name": args.name, "fresh": args.fresh})
    if args.cmd == "session" and args.session_cmd == "current":
        return request("session_current", {})
    if args.cmd == "session" and args.session_cmd == "close":
        return request("session_close", {"session": args.session})
    if args.cmd == "mice":
        return request("mice_add", {"session": args.session, "count": args.count})
    if args.cmd == "observe":
        return request("observe", {"session": args.session, "mouse": args.mouse, "pid": args.pid, "window_id": args.window})
    if args.cmd == "lease":
        return request("lease", {"session": args.session, "mouse": args.mouse, "pid": args.pid, "window_id": args.window, "app": args.app, "scope": args.scope})
    if args.cmd == "release":
        return request("release", {"session": args.session, "mouse": args.mouse, "pid": args.pid, "window_id": args.window, "app": args.app})
    if args.cmd == "act":
        return request("act", {"session": args.session, "mouse": args.mouse, "tool": args.tool, "arguments": json.loads(args.arguments), "delivery": args.delivery})
    return {"ok": False, "error": "usage", "message": "unknown command"}
