"""Leases, false-ok rejection, multi-mouse sessions, overlap scheduling."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from cuadrilla.cmux import Cmux
from cuadrilla.driver import DriverError
from cuadrilla.engine import claim_lease, lease_conflict, settle
from cuadrilla.install import add_claude, add_codex, remove_claude, remove_codex
from cuadrilla.mcp_server import _handle
from cuadrilla.service import Service
from cuadrilla.store import Store


class FakeDriver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.value = ""
        self.delay = 0.0
        self.binary = "cua-driver"
        self.lie = False

    def call(self, tool: str, args: dict | None = None, timeout: float | None = None) -> dict:
        args = args or {}
        self.calls.append((tool, args))
        if self.delay:
            time.sleep(self.delay)
        if tool == "start_session":
            return {"ok": True, "session": args.get("session")}
        if tool == "end_session":
            return {"ok": True}
        if tool == "list_windows":
            return {"windows": [{"pid": 10, "window_id": 1, "app_name": "TextEdit", "title": "A"}, {"pid": 11, "window_id": 2, "app_name": "Calculator", "title": "B"}, {"pid": 10, "window_id": 3, "app_name": "TextEdit", "title": "C"}]}
        if tool == "get_window_state":
            return {"elements": [{"role": "text", "value": self.value}]}
        if tool == "type_text":
            if not self.lie:
                self.value += args.get("text") or ""
            return {"ok": True, "effect": "confirmed"}
        if tool == "click":
            if not self.lie:
                self.value = "clicked"
            return {"ok": True, "effect": "unverifiable"}
        return {"ok": True}


class LeaseTests(unittest.TestCase):
    def test_second_mouse_cannot_take_the_app(self) -> None:
        leases, err = claim_lease([], "cw-a", "m1", 10, 1, "TextEdit", "app")
        self.assertIsNone(err)
        _leases, err = claim_lease(leases, "cw-a", "m2", 10, 3, "TextEdit", "app")
        self.assertEqual(err["error"], "lease_conflict")

    def test_other_session_cannot_take_the_window(self) -> None:
        leases, _err = claim_lease([], "cw-a", "m1", 10, 1, "TextEdit", "window")
        holder = lease_conflict(leases, "cw-b", "m1", 10, 1, "TextEdit", "window")
        self.assertEqual(holder["session"], "cw-a")

    def test_window_scope_allows_two_windows_of_one_app(self) -> None:
        leases, err = claim_lease([], "cw-a", "m1", 10, 1, "TextEdit", "window")
        self.assertIsNone(err)
        _leases, err = claim_lease(leases, "cw-a", "m2", 10, 3, "TextEdit", "window")
        self.assertIsNone(err)

    def test_false_ok_is_not_success(self) -> None:
        before = {"elements": [{"value": ""}]}
        after = {"elements": [{"value": ""}]}
        settled = settle("type_text", {"text": "hello"}, before, after, {"ok": True, "effect": "confirmed"})
        self.assertFalse(settled["ok"])
        self.assertEqual(settled["error"], "unconfirmed")

    def test_landed_text_is_success(self) -> None:
        settled = settle("type_text", {"text": "hello"}, {"elements": [{"value": ""}]}, {"elements": [{"value": "hello"}]}, {"ok": True})
        self.assertTrue(settled["ok"])


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.driver = FakeDriver()
        self.notes: list[list[str]] = []
        self.service = Service(Store(Path(self.tmp.name)), self.driver, Cmux(self.notes.append), idle_fn=lambda: 0.0, idle_wait=0.0)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_one_session_opens_several_mice(self) -> None:
        opened = self.service.session_open(mice=3, name="crew")
        self.assertTrue(opened["ok"])
        self.assertEqual(opened["mice"], ["m1", "m2", "m3"])
        starts = [args["session"] for tool, args in self.driver.calls if tool == "start_session"]
        self.assertEqual(len(starts), 3)
        self.assertEqual(len(set(starts)), 3)

    def test_surface_reuses_the_crew(self) -> None:
        first = self.service.session_open(mice=1, surface="surf-1")
        second = self.service.session_open(mice=1, surface="surf-1")
        self.assertEqual(first["session"], second["session"])
        self.assertTrue(second["reused"])

    def test_act_leases_and_rejects_the_other_mouse(self) -> None:
        opened = self.service.session_open(mice=2)
        sid = opened["session"]
        ok = self.service.act(sid, "m1", "type_text", {"pid": 10, "window_id": 1, "text": "hi"})
        self.assertTrue(ok["ok"], ok)
        blocked = self.service.act(sid, "m2", "click", {"pid": 10, "window_id": 3, "x": 1, "y": 1})
        self.assertEqual(blocked["error"], "lease_conflict")
        self.assertTrue(any("notify" in call for call in self.notes))

    def test_desktop_and_focus_steal_are_refused(self) -> None:
        opened = self.service.session_open(mice=1)
        sid = opened["session"]
        desktop = self.service.act(sid, "m1", "click", {"target": {"kind": "desktop", "display_id": "primary"}, "x": 1, "y": 1})
        self.assertEqual(desktop["error"], "desktop_refused")
        steal = self.service.act(sid, "m1", "click", {"pid": 11, "window_id": 2}, delivery="foreground")
        self.assertEqual(steal["error"], "foreground_refused")

    def test_when_idle_refuses_while_the_user_is_active(self) -> None:
        opened = self.service.session_open(mice=1)
        blocked = self.service.act(opened["session"], "m1", "click", {"pid": 11, "window_id": 2, "x": 1, "y": 1}, delivery="when_idle")
        self.assertEqual(blocked["error"], "user_active")

    def test_lie_from_the_driver_does_not_count(self) -> None:
        self.driver.lie = True
        opened = self.service.session_open(mice=1)
        result = self.service.act(opened["session"], "m1", "type_text", {"pid": 10, "window_id": 1, "text": "nope"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "unconfirmed")

    def test_different_apps_overlap_same_app_does_not(self) -> None:
        self.driver.delay = 0.15
        opened = self.service.session_open(mice=2)
        sid = opened["session"]
        spans: list[tuple[int, float, float]] = []
        lock = threading.Lock()
        real_call = self.driver.call

        def timed(tool, args=None, timeout=None):
            if tool != "click":
                return real_call(tool, args, timeout)
            start = time.time()
            out = real_call(tool, args, timeout)
            with lock:
                spans.append((args["pid"], start, time.time()))
            return out

        self.driver.call = timed
        # window scope so two mice may enter TextEdit windows; pid lock is the overlap rule
        threads = [
            threading.Thread(target=lambda: self.service.act(sid, "m1", "click", {"pid": 10, "window_id": 1, "lease_scope": "window", "x": 1, "y": 1})),
            threading.Thread(target=lambda: self.service.act(sid, "m1", "click", {"pid": 10, "window_id": 1, "lease_scope": "window", "x": 2, "y": 2})),
            threading.Thread(target=lambda: self.service.act(sid, "m2", "click", {"pid": 11, "window_id": 2, "lease_scope": "window", "x": 3, "y": 3})),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
        same = [span for span in spans if span[0] == 10]
        other = [span for span in spans if span[0] == 11]
        self.assertEqual(len(same), 2)
        self.assertEqual(len(other), 1)
        same.sort()
        self.assertLessEqual(same[0][2], same[1][1] + 0.02)
        self.assertTrue(same[0][1] < other[0][2] and other[0][1] < same[0][2] or same[1][1] < other[0][2] and other[0][1] < same[1][2])

    def test_a_click_token_is_not_refreshed_away(self) -> None:
        opened = self.service.session_open(mice=1)
        self.driver.calls.clear()
        self.service.act(opened["session"], "m1", "click", {"pid": 11, "window_id": 2, "element_token": "tok", "lease_scope": "window", "x": 1, "y": 1})
        tools = [tool for tool, _args in self.driver.calls]
        self.assertIn("click", tools)
        if "get_window_state" in tools:
            self.assertLess(tools.index("click"), tools.index("get_window_state"))


class InstallTests(unittest.TestCase):

    def test_content_length_frame(self) -> None:
        from io import BytesIO
        from cuadrilla.mcp_server import _read

        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}).encode()
        raw = BytesIO(b"Content-Length: %d\r\n\r\n" % len(payload) + payload)
        self.assertEqual(_read(raw)["method"], "initialize")
    def test_mcp_registration_is_idempotent_and_removable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude = root / "claude.json"
            claude.write_text(json.dumps({"mcpServers": {"cua-driver": {"command": "cua-driver"}}}))
            codex = root / "config.toml"
            codex.write_text("[mcp_servers.cua-driver]\ncommand = \"cua-driver\"\n")
            launcher = root / "cuadrilla"
            self.assertIn("claude", add_claude(claude, launcher))
            add_claude(claude, launcher)
            data = json.loads(claude.read_text())
            self.assertEqual(data["mcpServers"]["cua-driver"]["command"], "cua-driver")
            self.assertEqual(data["mcpServers"]["cuadrilla"]["args"], ["mcp"])
            remove_claude(claude)
            self.assertNotIn("cuadrilla", json.loads(claude.read_text())["mcpServers"])
            add_codex(codex, launcher)
            add_codex(codex, launcher)
            self.assertEqual(codex.read_text().count("[mcp_servers.cuadrilla]"), 1)
            remove_codex(codex)
            self.assertNotIn("cuadrilla", codex.read_text())
            self.assertIn("cua-driver", codex.read_text())


class McpTests(unittest.TestCase):
    def test_initialize_and_tools(self) -> None:
        init = _handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(init["result"]["serverInfo"]["name"], "cuadrilla")
        tools = _handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [tool["name"] for tool in tools["result"]["tools"]]
        self.assertIn("session_current", names)
        self.assertIn("act", names)
        self.assertIn("mice_add", names)


class BrandTests(unittest.TestCase):
    def test_mice_keep_stable_inks(self) -> None:
        from cuadrilla.brand import ink_for

        self.assertEqual(ink_for("m1")["name"], "Violet")
        self.assertEqual(ink_for("m2")["name"], "Amber")
        self.assertEqual(ink_for("m1")["hex"], ink_for("m1")["hex"])
        self.assertNotEqual(ink_for("m1")["hex"], ink_for("m2")["hex"])

    def test_status_names_the_ink_and_the_app(self) -> None:
        from cuadrilla.present import render

        text = render("status", {
            "ok": True,
            "sessions": {"cw-1": {"name": "demo", "mice": {"m1": {"ink": {"name": "Violet"}}}}},
            "leases": [{"session": "cw-1", "mouse": "m1", "app": "TextEdit"}],
        })
        self.assertIn("Violet", text)
        self.assertIn("TextEdit", text)
        self.assertIn("demo", text)

    def test_open_panels_are_not_demo_targets(self) -> None:
        from cuadrilla.demo import _token, _window

        listing = {"windows": [
            {"app_name": "TextEdit", "title": "Open", "pid": 1, "window_id": 2},
            {"app_name": "TextEdit", "title": "cuadrilla-demo.txt", "pid": 1, "window_id": 3},
        ]}
        self.assertEqual(_window(listing, "TextEdit", prefer="cuadrilla-demo")["window_id"], 3)
        token = _token({"elements": [{"role": "AXButton", "label": "7", "element_token": "s:5"}]}, "AXButton", "7")
        self.assertEqual(token, "s:5")


class HandsTests(unittest.TestCase):
    def test_stale_token_is_refreshed_once(self) -> None:
        from cuadrilla.cmux import Cmux
        from cuadrilla.driver import DriverError
        from cuadrilla.service import Service
        from cuadrilla.store import Store

        class Stale(FakeDriver):
            def call(self, tool, args=None, timeout=None):
                args = args or {}
                self.calls.append((tool, args))
                if tool == "start_session":
                    return {"ok": True}
                if tool == "list_windows":
                    return {"windows": [{"pid": 11, "window_id": 2, "app_name": "Calculator"}]}
                if tool == "get_window_state":
                    return {"elements": [{"role": "AXButton", "label": "8", "element_token": "new"}]}
                if tool == "click" and args.get("element_token") == "old":
                    raise DriverError('{"refusal": {"code": "stale_element_token"}}')
                if tool == "click":
                    return {"ok": True, "effect": "confirmed"}
                return {"ok": True}

        driver = Stale()
        service = Service(Store(Path(tempfile.mkdtemp())), driver, Cmux(lambda *_: None), idle_fn=lambda: 0.0, idle_wait=0.0)
        opened = service.session_open(mice=1)
        result = service.act(
            opened["session"],
            "m1",
            "click",
            {"pid": 11, "window_id": 2, "element_token": "old", "element_role": "AXButton", "element_label": "8", "lease_scope": "window"},
        )
        self.assertTrue(result["ok"], result)
        clicks = [tool for tool, _args in driver.calls if tool == "click"]
        self.assertEqual(len(clicks), 2)

    def test_pill_names_the_ink(self) -> None:
        from cuadrilla.cmux import pill_text

        self.assertEqual(
            pill_text({
                "sessions": {"cw": {"mice": {"m1": {"ink": {"name": "Violet"}}}}},
                "leases": [{"session": "cw", "mouse": "m1", "app": "TextEdit"}],
            }),
            "Violet TextEdit",
        )


if __name__ == "__main__":
    unittest.main()
